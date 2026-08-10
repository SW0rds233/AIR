from __future__ import annotations

"""检索结果相关性过滤

问题背景: OpenAlex 模糊检索(尤其中文)会返回大量语义无关论文
(如检索"射频指纹"返回岩石油页岩/微生物/岩石学论文)。

两层过滤:
1. 规则过滤 (零成本): 标题必须包含主题关键词
2. LLM 打分过滤 (可选): 对候选做相关性打分 0-10, 过滤低分
"""

import logging
import re

logger = logging.getLogger(__name__)

RULE_THRESHOLD = 0.3       # 规则命中率低于该值剔除
LLM_SCORE_THRESHOLD = 5.0  # LLM 打分低于该值剔除


def _normalize_keyword(kw: str) -> str:
    """规范化关键词: 小写 + 去下划线/连字符 + 去空格

    借鉴 gpt-researcher 的查询规范化:
    "RF_fingerprinting" / "RF-Fingerprinting" / "rf fingerprinting"
    统一为 "rffingerprinting", 避免下划线/连字符/空格导致的匹配失败
    """
    return re.sub(r"[\s_\-]+", "", kw.lower())


def extract_keywords(topic: str, user_keywords: str = "") -> list[str]:
    """从主题和用户关键词中提取检索关键词（中英文, 去重 + 规范化变体）"""
    kws = []
    for k in [topic, user_keywords]:
        if not k:
            continue
        for part in re.split(r"[,，;；\s]+", k):
            part = part.strip()
            if part and len(part) >= 2 and part not in kws:
                kws.append(part)
    return kws


def rule_filter(papers: list[dict], topic: str, user_keywords: str = "") -> list[dict]:
    """规则过滤: 标题/摘要必须命中主题关键词 (归一化匹配)

    命中的关键词越多越相关。命中率为 0 的论文剔除。
    归一化: 关键词与文本都转小写去分隔符, 解决
    "RF_fingerprinting" vs "RF fingerprinting" 的匹配失败 (借鉴 gpt-researcher 查询规范化)
    """
    kws = extract_keywords(topic, user_keywords)
    if not kws:
        return papers
    norm_kws = [_normalize_keyword(kw) for kw in kws]
    # 去重后保留有区分度的关键词
    norm_kws = list(dict.fromkeys(norm_kws))

    kept = []
    for p in papers:
        title = (p.get("title", "") or "").lower()
        abstract = (p.get("abstract", "") or "").lower()
        text = _normalize_keyword(f"{title} {abstract}")

        hits = sum(1 for kw in norm_kws if kw in text)
        if hits > 0:
            kept.append(p)
        else:
            logger.debug(f"规则过滤剔除 (0 关键词命中): {p.get('title', '')[:60]}")

    logger.info(f"规则过滤: {len(papers)} -> {len(kept)} (关键词: {kws[:5]})")
    return kept


def llm_score_filter(
    papers: list[dict],
    topic: str,
    llm,
    threshold: float = LLM_SCORE_THRESHOLD,
    max_batch: int = 40,
) -> list[dict]:
    """LLM 相关性打分过滤

    对候选论文批量打分 (0-10), 剔除低于阈值的。
    返回带 relevance_score 字段的论文列表。
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    if not papers:
        return []

    candidates = papers[:max_batch]
    scored = []

    system = """你是文献相关性评估专家。对每篇候选论文判断其与给定研究主题的相关性，
输出 0-10 的分数（0=完全不相关, 10=高度相关）。只输出分数列表，每行一个数字。"""

    items_text = "\n".join(
        f"{i+1}. {p.get('title', '')} ({p.get('year', '')}) [{p.get('source', '')}]"
        for i, p in enumerate(candidates)
    )

    prompt = (
        f"研究主题: {topic}\n"
        f"请评估以下 {len(candidates)} 篇论文与该主题的相关性，输出每篇的分数（0-10）：\n\n"
        f"{items_text}\n\n"
        f"输出格式: 每行一个数字，如:\n8\n3\n6\n..."
    )

    try:
        result = llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=prompt)]
        )
        text = result.content if hasattr(result, "content") else str(result)
        scores = [float(s.strip()) for s in re.findall(r"^\s*(\d+(?:\.\d+)?)\s*$", text, re.M)]
        # 兜底: 也尝试提取任意数字行
        if len(scores) < len(candidates):
            scores = [float(s) for s in re.findall(r"\d+(?:\.\d+)?", text)][: len(candidates)]
    except Exception as e:
        logger.warning(f"LLM 打分失败, 跳过: {e}")
        for p in candidates:
            p["relevance_score"] = 5.0  # 打分失败时给默认分, 不剔除
        return candidates

    for i, p in enumerate(candidates):
        score = scores[i] if i < len(scores) else 5.0
        p["relevance_score"] = score
        if score >= threshold:
            scored.append(p)
        else:
            logger.debug(f"LLM 打分剔除 ({score}): {p.get('title', '')[:60]}")

    logger.info(f"LLM 打分过滤: {len(candidates)} -> {len(scored)} (阈值 {threshold})")
    return scored
