from __future__ import annotations

"""检索阶段缓存: 复用上一次运行的文献检索结果, 跳过检索直接进入撰写/审稿循环。

场景: 测试时主题/关键词/分主题固定, 初始文献资料几乎一致。
首次运行会在 outline_generation 结束后把检索产物写入 data/pipeline_cache/{topic}.json;
之后用 --skip-retrieval 启动时直接加载, 跳过 literature_review / pdf_ingestion /
citation_precheck / outline_generation 四个阶段, 从 paper_writing 开始。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "pipeline_cache"

# 判定"缓存可用"的最小已验证参考文献数 (低于此值回退完整检索)
MIN_VERIFIED_REFS = 20


def _cache_path(topic: str) -> Path:
    from src.utils.file_utils import sanitize_filename

    return CACHE_DIR / f"{sanitize_filename(topic)}.json"


def resolve_cache_topic(topic: str) -> str | None:
    """返回与给定主题最匹配的缓存主题 (精确匹配优先, 否则按相似度模糊匹配)。

    自然语言入口下 Planner 提取的主题 (如「射频指纹识别研究综述」) 未必与
    缓存文件名 (如「射频指纹识别研究」) 完全一致, 需模糊匹配兜底。
    无任何匹配返回 None。
    """
    if _cache_path(topic).exists():
        return topic
    if not CACHE_DIR.exists():
        return None
    try:
        from difflib import SequenceMatcher
    except ImportError:
        return None

    best_name, best_score = None, 0.0
    for f in CACHE_DIR.glob("*.json"):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        stored = (payload.get("topic") or "").strip() or f.stem
        if not stored:
            continue
        score = SequenceMatcher(None, topic, stored).ratio()
        if topic and stored and (topic in stored or stored in topic):
            score = max(score, 0.85)
        if score > best_score:
            best_score, best_name = score, stored

    if best_name is not None and best_score >= 0.5:
        return best_name
    return None


def latest_cache_topic() -> str | None:
    """返回最近修改的缓存主题 (用于"已有数据/报告"但未指明主题时的兜底)。"""
    if not CACHE_DIR.exists():
        return None
    files = [f for f in CACHE_DIR.glob("*.json") if f.is_file()]
    if not files:
        return None
    latest = max(files, key=lambda f: f.stat().st_mtime)
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
        return (payload.get("topic") or "").strip() or latest.stem
    except Exception:
        return latest.stem


def save_retrieval_cache(
    topic: str,
    literature_review_notes: str,
    verified_references: list,
    paper_outline: str = "",
    retrieved_papers: list | None = None,
    unfiltered_papers: list | None = None,
    keywords: list | None = None,
    sub_topics: list | None = None,
    time_range: str = "",
    stages: list | None = None,
) -> str:
    """把检索产物写入 data/pipeline_cache/{topic}.json, 供 --skip-retrieval 复用"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "topic": topic,
        "keywords": list(keywords or []),
        "sub_topics": list(sub_topics or []),
        "time_range": time_range,
        "stages": list(stages or []),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "literature_review_notes": literature_review_notes,
        "verified_references": verified_references,
        "paper_outline": paper_outline,
        "retrieved_papers": retrieved_papers or [],
        "unfiltered_papers": unfiltered_papers or [],
    }
    path = _cache_path(topic)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info(f"检索缓存已保存: {path} ({len(verified_references)} 篇已验证引用)")
    return str(path)


def list_contexts() -> list[dict]:
    """列出所有缓存上下文 (主题 + 进度 + 产物信息), 用于多上下文切换。

    每个缓存文件对应一个研究上下文; 返回按 saved_at 倒序的列表。
    """
    if not CACHE_DIR.exists():
        return []
    contexts = []
    for f in CACHE_DIR.glob("*.json"):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        topic = (payload.get("topic") or "").strip() or f.stem
        contexts.append({
            "topic": topic,
            "stages": list(payload.get("stages") or []),
            "saved_at": payload.get("saved_at", ""),
            "refs": len(payload.get("verified_references", []) or []),
            "notes_chars": len(payload.get("literature_review_notes", "") or ""),
            "has_outline": bool(payload.get("paper_outline")),
        })
    contexts.sort(key=lambda c: c.get("saved_at", ""), reverse=True)
    return contexts


def update_retrieval_cache(topic: str, **fields) -> str:
    """增量更新检索缓存: 只覆盖传入的字段, 其余保留已有值。

    供模块化流水线在三大模块间传递中间产物:
    - 模块1(检索/入库) 写入 retrieved_papers / unfiltered_papers / verified_references
    - 模块2(分析) 追加 literature_review_notes / paper_outline
    传 None 表示不更新该字段。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(topic)
    payload: dict = {"topic": topic}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"topic": topic}
    payload["topic"] = topic
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info(f"检索缓存已更新: {path} (字段: {list(fields.keys())})")
    return str(path)


def load_retrieval_cache(topic: str) -> dict | None:
    """加载检索缓存; 缓存不存在或引用数不足时返回 None (回退完整检索)

    加载时会对 cached verified_references 再做一次预印本过滤:
    历史缓存可能由旧过滤规则写入 (漏掉 TechRxiv/SSRN 等预印本 DOI),
    重新过滤确保"不得出现预印本"的硬性要求始终满足。
    """
    path = _cache_path(topic)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"检索缓存解析失败: {e}")
        return None
    refs = payload.get("verified_references", [])
    if not payload.get("literature_review_notes") or len(refs) < MIN_VERIFIED_REFS:
        logger.info(
            f"检索缓存不满足条件 (已验证引用 {len(refs)} < {MIN_VERIFIED_REFS}), 回退完整检索"
        )
        return None

    # 预印本兜底过滤 (历史缓存可能含旧规则漏掉的预印本)
    try:
        from src.rag.reference_formatter import is_published_ref

        before = len(refs)
        refs = [r for r in refs if is_published_ref(r)]
        if len(refs) < before:
            logger.info(f"检索缓存预印本过滤: {before} → {len(refs)} 篇")
        payload["verified_references"] = refs
    except Exception:
        pass

    # 跨域论文兜底过滤 (历史缓存可能含音频/多媒体等异域论文,
    # 如 "End-to-end Recording Device Identification", 被引用后审稿人
    # 连续判 Critical 主题错配): 与 rule_filter 同一判定标准
    try:
        from src.rag.relevance_filter import has_off_domain_signal

        before = len(refs)
        refs = [
            r for r in refs
            if not has_off_domain_signal(r.get("title", "") or "")
            or (r.get("api_source") or "") == "人工导入"
        ]
        if len(refs) < before:
            logger.info(f"检索缓存跨域论文过滤: {before} → {len(refs)} 篇")
        payload["verified_references"] = refs
    except Exception:
        pass

    return payload


def load_retrieval_cache_raw(topic: str) -> dict | None:
    """无门槛加载检索缓存 (模块化流水线用)。

    不同于 load_retrieval_cache (要求 notes 非空 + ≥MIN_VERIFIED_REFS 引用),
    本函数原样返回 payload —— 模块1 刚保存时尚未生成 notes, 模块2 需要能
    加载到 retrieved_papers; 是否满足下游条件由各模块自行判断。
    """
    path = _cache_path(topic)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"检索缓存解析失败: {e}")
        return None
