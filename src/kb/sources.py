from __future__ import annotations

"""资料源登记与绑定 (计划书 §3 R0)。

问题
----
研究入口原来只带主题/项目/问题/预算, 没有"用哪个资料库"; 研究图按
`spec.domain` 或原始请求字符串去找**同名**知识底座 —— 用户建好的资料库若名字
不同, 引擎就找不到, 于是静默退回"无知识库", 而用户以为系统在用他给的资料。

本模块把"资料源"变成可列举、可绑定、可校验的对象:

- `list_source_sets()`: 列出每个已建知识底座的文档数、语言/年份范围、卡片数、
  是否建了向量索引、最后更新时间 —— 入口据此让用户选择;
- `describe_source_set(id)`: 单个资料源的可读范围与索引状态;
- `validate_binding(id)`: 绑定是否可用; 不可用时给出**明确原因**, 而不是静默降级。

约束: 只读枚举 (不创建空底座); 不把来源真实性判定写在这里 (那是 §6.2)。
"""

from dataclasses import dataclass, field

from src.kb.store import KBStore, default_db_path, topic_dir


def _kb_root():
    """知识底座根目录 (每次调用时取, 便于测试隔离与运行期配置变更)。"""
    from src.config import DATA_DIR

    return DATA_DIR / "kb"


@dataclass
class SourceSet:
    source_set_id: str
    kind: str = "kb"
    documents: int = 0
    manual_documents: int = 0
    with_fulltext: int = 0
    cards: int = 0
    languages: list[str] = field(default_factory=list)
    year_range: str = ""
    indexed: bool = False          # 是否已建向量索引
    updated_at: str = ""
    readable: bool = True
    note: str = ""
    #: 这个库从哪来: managed (data/kb 下自建) / path_import (用户给定本机路径) /
    #: upload (界面上传)。合并计划 §13.3: "从哪来、能读哪些根"必须可查。
    origin: str = "managed"
    #: 路径导入库登记的文件数 (managed 库为 0)。
    imported_files: int = 0
    #: 已失效 (原文件被移动/删除) 的登记文件数。
    stale_files: int = 0

    def describe(self) -> str:
        if not self.readable:
            return f"{self.source_set_id}: 不可读 ({self.note})"
        bits = [f"{self.documents} 篇", f"{self.cards} 张卡"]
        if self.languages:
            bits.append("语言 " + "/".join(self.languages))
        if self.year_range:
            bits.append(f"年份 {self.year_range}")
        bits.append("已建索引" if self.indexed else "未建索引")
        if self.origin == "path_import":
            bits.append(f"路径导入 {self.imported_files} 个文件")
            if self.stale_files:
                bits.append(f"{self.stale_files} 个已失效")
        return f"{self.source_set_id}: " + ", ".join(bits)

    def to_dict(self) -> dict:
        return {
            "source_set_id": self.source_set_id, "kind": self.kind,
            "documents": self.documents, "manual_documents": self.manual_documents,
            "with_fulltext": self.with_fulltext, "cards": self.cards,
            "languages": list(self.languages), "year_range": self.year_range,
            "indexed": self.indexed, "updated_at": self.updated_at,
            "readable": self.readable, "note": self.note,
            "origin": self.origin, "imported_files": self.imported_files,
            "stale_files": self.stale_files,
            "description": self.describe(),
        }


def _path_import_stats(topic: str) -> tuple[int, int]:
    """该库的路径导入登记数 (文件数, 失效数); 非路径导入库返回 (0, 0)。

    只读登记文件, 不触发任何导入或解析。
    """
    try:
        from src.kb.path_import import load_path_refs
    except Exception:  # noqa: BLE001
        return 0, 0
    refs = load_path_refs(topic)
    if not refs:
        return 0, 0
    return len(refs), sum(1 for r in refs if r.missing_since)


def _vector_indexed(topic: str) -> bool:
    """该主题是否已建向量索引 (用于入口显示"索引状态")。

    通过向量层的计数能力判断; 向量能力不可用时返回 False 并视为"未建索引",
    不把"没有向量后端"说成"已建索引"。
    """
    try:
        from src.rag.vector_store import embedding_available, get_vector_store

        if not embedding_available():
            return False
        store = get_vector_store(f"kb_{_sanitize(topic)}")
        collection = getattr(store, "_collection", None)
        if collection is not None and hasattr(collection, "count"):
            return int(collection.count()) > 0
        return False
    except Exception:  # noqa: BLE001 - 向量能力缺失不影响资料源可读性
        return False


def _sanitize(name: str) -> str:
    from src.utils.file_utils import sanitize_filename

    return sanitize_filename(name)


def _language_year(store: KBStore) -> tuple[list[str], str]:
    """资料源的可读范围: 已登记的语言与年份区间 (没登记就不写)。"""
    docs = store.list_documents()
    languages = sorted({str(d.get("language") or "").strip() for d in docs
                        if str(d.get("language") or "").strip()})
    years = sorted({int(y) for y in
                    (str(d.get("year") or "").strip()[:4] for d in docs)
                    if y.isdigit() and 1900 <= int(y) <= 2100})
    year_range = (f"{years[0]}" if len(years) == 1
                  else (f"{years[0]}-{years[-1]}" if years else ""))
    return languages, year_range


def list_source_sets(include_unreadable: bool = True) -> list[SourceSet]:
    """枚举 data/kb/ 下**已存在**的知识底座 (不创建新的)。"""
    kb_root = _kb_root()
    out: list[SourceSet] = []
    if not kb_root.exists():
        return out
    for child in sorted(kb_root.iterdir()):
        if not child.is_dir():
            continue
        # 目录名是 sanitize 后的主题名; 用真实目录反查主题, 避免二次 sanitize 漂移
        topic = child.name
        db_path = child / "kb.sqlite"
        if not db_path.exists():
            if include_unreadable:
                out.append(SourceSet(source_set_id=topic, readable=False,
                                     note="目录存在但没有 kb.sqlite (未摄入任何资料)"))
            continue
        try:
            store = KBStore(topic, db_path=db_path, create_if_missing=False)
        except Exception as e:  # noqa: BLE001
            if include_unreadable:
                out.append(SourceSet(source_set_id=topic, readable=False,
                                     note=f"无法打开: {e}"))
            continue
        try:
            stats = store.stats()
            languages, year_range = _language_year(store)
            imported, stale = _path_import_stats(topic)
            out.append(SourceSet(
                source_set_id=topic,
                documents=int(stats.get("documents", 0)),
                manual_documents=int(stats.get("manual", 0)),
                with_fulltext=int(stats.get("with_fulltext", 0)),
                cards=int(stats.get("cards", 0)),
                languages=languages, year_range=year_range,
                indexed=_vector_indexed(topic),
                updated_at=str(child.stat().st_mtime_ns),
                origin="path_import" if imported else "managed",
                imported_files=imported, stale_files=stale,
            ))
        finally:
            store.close()
    return out


def describe_source_set(source_set_id: str) -> SourceSet:
    """单个资料源的可读范围与索引状态 (不存在时 `readable=False` 并说明原因)。"""
    topic = (source_set_id or "").strip()
    if not topic:
        return SourceSet(source_set_id="", readable=False, note="未指定资料源")
    db_path = default_db_path(topic)
    if not db_path.exists():
        return SourceSet(source_set_id=topic, readable=False,
                         note=f"资料源不存在: 没有 {db_path.name}")
    try:
        store = KBStore(topic, db_path=db_path, create_if_missing=False)
    except Exception as e:  # noqa: BLE001
        return SourceSet(source_set_id=topic, readable=False, note=f"无法打开: {e}")
    try:
        stats = store.stats()
        languages, year_range = _language_year(store)
        imported, stale = _path_import_stats(topic)
        return SourceSet(
            source_set_id=topic,
            documents=int(stats.get("documents", 0)),
            manual_documents=int(stats.get("manual", 0)),
            with_fulltext=int(stats.get("with_fulltext", 0)),
            cards=int(stats.get("cards", 0)),
            languages=languages, year_range=year_range,
            indexed=_vector_indexed(topic),
            updated_at=str(db_path.stat().st_mtime_ns),
            origin="path_import" if imported else "managed",
            imported_files=imported, stale_files=stale,
        )
    finally:
        store.close()


def validate_binding(source_set_id: str) -> dict:
    """校验资料源绑定是否可用于研究。

    返回 `{ok, reason, source_set, warnings}`。**没有可用资料时明确不 ok** —— 调用方
    应在确认研究之前把这件事告诉用户, 而不是默默退回"无知识库"。
    """
    info = describe_source_set(source_set_id)
    warnings: list[str] = []
    if not info.readable:
        return {"ok": False, "reason": info.note, "source_set": info.to_dict(),
                "warnings": warnings}
    if info.documents == 0 and info.cards == 0:
        return {"ok": False, "reason": f"资料源 {info.source_set_id} 是空的 (0 篇/0 卡)",
                "source_set": info.to_dict(), "warnings": warnings}
    if info.with_fulltext == 0 and info.cards == 0:
        warnings.append("资料源没有全文也没有卡片, 只能返回检索片段")
    if not info.indexed:
        warnings.append("未建向量索引: 仅关键词+卡片召回 (结果可能不完整)")
    return {"ok": True, "reason": "", "source_set": info.to_dict(),
            "warnings": warnings}


_MODEL_MARKERS = ("模型", "方程", "公式", "机理", "机制", "定理")
_DATA_MARKERS = ("数据集", "样本数据", "观测数据", "面板数据", "数据库表", "csv", "我提供数据")


def build_source_summary(source_set_id: str = "", source_set_kind: str = "kb",
                         request: str = "", autonomous_retrieval: bool = False):
    """把"手上有什么"整理成 formulate() 的输入摘要 (只描述材料, 不含结论)。

    判定顺序: 结构化数据集 → 有观测数据; 题目自带模型/机理措辞 → 有理论模型;
    其余仍按资料库文献处理。依据会写进契约的 `basis`, 便于人审区分规则与模型判断。
    """
    from src.research.schemas import SourceSummary

    summary = SourceSummary(source_set_id=(source_set_id or "").strip(),
                            autonomous_retrieval=bool(autonomous_retrieval))
    if summary.source_set_id:
        info = describe_source_set(summary.source_set_id)
        if info.readable:
            summary.documents = info.documents
            summary.fulltext_documents = info.with_fulltext
    text = (request or "").lower()
    if (source_set_kind or "").strip() == "dataset" or any(m in text for m in _DATA_MARKERS):
        summary.has_observational_data = True
        summary.data_note = ("来源类型 dataset" if (source_set_kind or "") == "dataset"
                             else "题目声明了可用数据")
    if any(m in text for m in _MODEL_MARKERS):
        summary.has_theory_model = True
        summary.theory_model_note = "题目给出模型/机理措辞"
    return summary


def resolve_topic(spec) -> str:
    """研究应使用的资料库主题: 显式绑定优先, 否则退回旧的主题名匹配。"""
    bound = str(getattr(spec, "source_set_id", "") or "").strip()
    if bound:
        return bound
    return str(getattr(spec, "domain", "") or
               getattr(spec, "original_request", "") or "").strip()


def resolve_topic_with_binding(spec) -> tuple[str, str]:
    """返回 (主题, 绑定说明)。绑定不可用时给出可展示的原因。

    注意: 绑定不可用返回空主题 + 原因 —— 由调用方决定是"停止并要求用户重新选择"
    还是"在报告中标注未使用任何资料库", 本函数不替调用方静默降级。
    """
    bound = str(getattr(spec, "source_set_id", "") or "").strip()
    if not bound:
        return resolve_topic(spec), ""
    check = validate_binding(bound)
    if check["ok"]:
        warnings = "; ".join(check["warnings"])
        return bound, (f"已绑定资料源 {bound}" + (f" (提示: {warnings})" if warnings else ""))
    return "", f"绑定的资料源不可用: {check['reason']}"


__all__ = [
    "SourceSet",
    "describe_source_set",
    "list_source_sets",
    "resolve_topic",
    "resolve_topic_with_binding",
    "topic_dir",
    "validate_binding",
]
