from __future__ import annotations

"""参考文献表与正文引用编号的**双向映射** (方案 v2 §5 阶段 3)。

为什么单独一个模块
------------------
期刊式论文有两条必须同时成立、且互相约束的约束:

1. 正文里出现的每个引用编号, 参考文献表里必须能查到;
2. 参考文献表里的每一条, 正文里必须真的引用过 (否则就是"凑引用")。

把它做成独立模块而不是散在写作函数里, 是为了让这两条能被**确定性检查**
(`publication_checks`) 反复验证, 并且 Markdown 与 LaTeX 共用同一份编号分配 ——
编号一旦在两处各算一次, 就会出现"正文 `[3]` 指向表里另一条"这种最难查的错。

GB/T 7714 著录格式由 `src/rag/reference_formatter.py` 负责, 本模块只做编号与表格。
"""

from dataclasses import dataclass, field

# 引用占位: 正文里写 `[[REF:key]]`, 渲染阶段统一替换为 `[n]` (或 `\cite{key}`)
CITE_PATTERN = r"\[\[REF:([A-Za-z0-9_\-]+)\]\]"


def citation_marker(key: str) -> str:
    """正文里应当写入的引用占位**字面量**: `[[REF:key]]`。

    必须提供这个函数, 而不是让调用方去改 `CITE_PATTERN`: 那是**正则**, 直接对正则做
    字符串替换会留下字面反斜杠 (`\\[\\[REF:ref1\\]\\]`), 于是占位永远不会被解析成
    `[n]`, 而是原样印进正文 (实测过: 参考文献表有 1 条, 正文却显示 `[[REF:ref1]]`)。
    """
    return f"[[REF:{key}]]"


@dataclass
class Reference:
    """一条被采信的参考文献。"""

    key: str                      # 稳定引用键 (ref1 / ref2 …)
    title: str = ""
    authors: str = ""
    year: str = ""
    venue: str = ""
    doi: str = ""
    url: str = ""
    source_id: str = ""           # 对应 SourceEvidence.id (便于反查定位)
    location: str = ""            # 页码/节号等定位
    support: str = ""             # 支持关系 (supports / partially_supports / background)
    formatted: str = ""           # GB/T 7714 著录文本 (由 formatter 提供)

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "authors": self.authors,
                "year": self.year, "venue": self.venue, "doi": self.doi,
                "url": self.url, "source_id": self.source_id,
                "location": self.location, "support": self.support,
                "formatted": self.formatted}


@dataclass
class ReferenceList:
    """参考文献表 + 正文引用占位 -> 编号 的映射。"""

    references: list[Reference] = field(default_factory=list)
    # 未采信但被提及的材料 (缺全文/未能核实): 只出现在"检索覆盖"说明里, 不得进文献表
    excluded: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.references)

    def keys(self) -> list[str]:
        return [item.key for item in self.references]

    def number_of(self, key: str) -> int | None:
        for index, item in enumerate(self.references, start=1):
            if item.key == key:
                return index
        return None

    def render_markdown(self, reason: str = "") -> str:
        """参考文献章节 (Markdown)。

        引用表为空时**仍然渲染章节**并写明检索范围与原因 —— 方案 v2 §0 要求
        "参考文献章节必须在位", 否则出版门槛不通过 (不能靠删章节"看起来完整")。
        """
        lines = ["## 参考文献", ""]
        if not self.references:
            lines.append(reason or "在所检索范围内未发现可引用的等价工作。")
            if self.excluded:
                lines.append("")
                lines.append("检索到但未采信的材料 (原因见检索覆盖记录):")
                for item in self.excluded[:20]:
                    lines.append(f"- {item.get('title', '')} — {item.get('reason', '')}")
            return "\n".join(lines) + "\n"
        for index, item in enumerate(self.references, start=1):
            text = item.formatted or _fallback_format(item)
            lines.append(f"[{index}] {text}")
        if self.excluded:
            lines.append("")
            lines.append("检索到但未采信的材料 (原因见检索覆盖记录):")
            for item in self.excluded[:20]:
                lines.append(f"- {item.get('title', '')} — {item.get('reason', '')}")
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict:
        return {"references": [item.to_dict() for item in self.references],
                "excluded": list(self.excluded)}


def _fallback_format(item: Reference) -> str:
    """formatter 不可用时的最小著录 (宁可朴素, 不可编造字段)。"""
    bits = [b for b in (item.authors, item.title) if b]
    tail = []
    if item.venue:
        tail.append(item.venue)
    if item.year:
        tail.append(str(item.year))
    if item.doi:
        tail.append(f"DOI: {item.doi}")
    elif item.url:
        tail.append(item.url)
    head = ". ".join(bits)
    return (head + ". " + ", ".join(tail)).strip(". ") if head else (item.title or "未命名文献")


def build_reference_list(evidence: list, formatter=None) -> ReferenceList:
    """从**已判定支持关系**的证据构造参考文献表。

    只收 `supports` / `partially_supports` / `background` 三类: 检索命中本身不构成
    引用资格 (`insufficient` 一律进 `excluded`, 并在覆盖记录里给原因)。
    """
    refs: list[Reference] = []
    excluded: list[dict] = []
    seen: set[str] = set()
    for item in evidence or []:
        support = getattr(getattr(item, "support", None), "value", "") or ""
        title = getattr(item, "title", "") or getattr(item, "literature_id", "") or ""
        if support not in ("supports", "partially_supports", "background"):
            excluded.append({"title": title,
                             "reason": f"支持关系未判定为可引用 ({support or '未判定'})"})
            continue
        identity = getattr(item, "file_hash", "") or getattr(item, "doi", "") or title
        if identity in seen:
            continue
        seen.add(identity)
        ref = Reference(
            key=f"ref{len(refs) + 1}",
            title=title,
            authors=getattr(item, "authors", "") or "",
            year=str(getattr(item, "year", "") or ""),
            venue=getattr(item, "venue", "") or getattr(item, "source", "") or "",
            doi=getattr(item, "doi", "") or "",
            url=getattr(item, "url", "") or "",
            source_id=getattr(item, "id", "") or "",
            location=getattr(item, "location", "") or "",
            support=support,
        )
        ref = _format_reference(ref, item, formatter)
        refs.append(ref)
    return ReferenceList(references=refs, excluded=excluded)


def _format_reference(ref: Reference, evidence, formatter) -> Reference:
    """调用既有 GB/T 7714 格式化器; 失败时退回最小著录 (不编造字段)。"""
    reader = formatter
    if reader is None:
        try:
            from src.rag.reference_formatter import format_gbt7714_entry

            reader = format_gbt7714_entry
        except Exception:  # noqa: BLE001 - 格式化器不可用不影响引用资格
            reader = None
    if reader is None:
        return ref
    payload = {
        "ref_number": ref.key.replace("ref", ""),
        "title": ref.title, "authors": ref.authors, "year": ref.year,
        "venue": ref.venue, "doi": ref.doi, "url": ref.url,
    }
    try:
        text = reader(payload)
    except Exception:  # noqa: BLE001
        text = ""
    if isinstance(text, str) and text.strip():
        ref.formatted = text.strip()
    return ref


def render_markdown_citations(text: str, references: ReferenceList,
                             missing: list[str] | None = None) -> str:
    """把正文里的 `[[REF:key]]` 占位替换为 `[n]`。

    未在文献表中的键**不静默丢弃**: 记入 `missing` 并替换为显式标记, 让
    `publication_checks` 能把它挡下来 (静默删除会让"引用不存在"变成看不见)。
    """
    import re

    def _sub(match):
        key = match.group(1)
        number = references.number_of(key)
        if number is None:
            if missing is not None:
                missing.append(key)
            return f"[未收录引用:{key}]"
        return f"[{number}]"

    return re.sub(CITE_PATTERN, _sub, text or "")


def render_latex_citations(text: str, references: ReferenceList,
                           missing: list[str] | None = None) -> str:
    """LaTeX 侧同样替换为 `\\upcite{refN}` (编号由 thebibliography 决定)。"""
    import re

    def _sub(match):
        key = match.group(1)
        if references.number_of(key) is None:
            if missing is not None:
                missing.append(key)
            return f"[未收录引用:{key}]"
        return f"\\upcite{{{key}}}"

    return re.sub(CITE_PATTERN, _sub, text or "")


__all__ = ["CITE_PATTERN", "Reference", "ReferenceList", "build_reference_list",
           "render_latex_citations", "render_markdown_citations"]
