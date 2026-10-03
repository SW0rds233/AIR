from __future__ import annotations

"""新颖性审查 (P2)。

证明正确不意味着结论新颖; 未检索到相同结论也不意味着世界首次发现。
无检索能力时保持 unchecked, 并使用有界表述 ("在所检索范围内尚未发现等价结果")。
"""

from collections.abc import Callable

from src.research.schemas import (
    Claim,
    NoveltyComparisonRow,
    NoveltyRecord,
    NoveltyStatus,
    SourceEvidence,
    utcnow,
)


def _is_equivalent(row: NoveltyComparisonRow) -> bool:
    text = (row.difference or "").lower()
    return "等价" in text or "same" in text


def _comparison_basis(record: NoveltyRecord) -> str:
    """判定依据: 只有**分析过差异且确实不同**的行才允许支撑"可能不同"。

    计划书 §6.2 / A13 / R4: 检索命中本身不构成新颖性结论。因此
    - difference 为空的行走 (未分析) 一律不计入判定, 记录保持待比较;
    - difference 明确写"等价"的行走计为已知等价结果;
    - difference 写"未能判断/无法比较"的行走**既不算等价也不算不同**
      (分析过但结论不可比), 保持待比较;
    - 只有写明条件/结论差异的行走才算"可能不同"。
    """
    if any(_is_equivalent(r) for r in record.rows):
        return "known_equivalent"
    for row in record.rows:
        text = (row.difference or "").strip()
        if not text:
            continue
        if "未能判断" in text or "无法比较" in text:
            continue
        if any(word in text for word in ("不同", "更强", "更弱", "更窄", "更广",
                                        "更受限", "更一般", "包含", "特例", "差异",
                                        "distinct", "differ")):
            return "potentially_distinct"
    return "pending"


def assess(
    claim: Claim,
    lookup: Callable[[Claim], list[NoveltyComparisonRow]] | None = None,
    covered_sources: list[str] | None = None,
    evidence: list[SourceEvidence] | None = None,
    analyze: bool = True,
    evidence_scope: list[str] | None = None,
    retrieval_scope: dict | None = None,
) -> NoveltyRecord:
    """评估新颖性。

    R4: 命中之后先做**逐项差异分析** (`novelty_compare.analyze_rows`), 只有能给出
    判定的对照才填 `difference`; 无法比较的写成"未能判断 (缺 …)", 因此"已有相近
    结果但差异未分析"不会被误当成"已分析且无差异"。

    P1-1: `evidence_scope` 是用户**授权的研究证据库**, `retrieval_scope` 是本次
    新颖性调查实际覆盖的检索范围; 两者分开记录, 不许用前者冒充后者。
    """
    record = NoveltyRecord(
        claim_id=claim.id,
        claim_version=claim.version,
        # 检索日期用 UTC, 与其余对象时间戳口径一致
        search_date=utcnow()[:10],
        queries=[claim.statement],
        covered_sources=list(covered_sources or []),
        evidence_scope=list(evidence_scope or []),
        retrieval_scope=dict(retrieval_scope or {}),
    )
    if lookup is None:
        record.status = NoveltyStatus.unchecked
        record.conclusion = "未接入已有工作检索, 无法判定新颖性"
        return record
    try:
        rows = lookup(claim)
    except Exception as e:  # noqa: BLE001
        record.status = NoveltyStatus.unchecked
        record.inaccessible.append(f"检索失败: {e}")
        record.conclusion = "检索失败, 保持未决"
        return record
    rows = list(rows or [])
    pre_analyzed = [r for r in rows if (r.difference or "").strip()]
    if analyze and rows and not pre_analyzed:
        # 只对**尚未分析**的行做自动比对; 已带 difference 的行走 (由调用方或人工
        # 提供) 一律保留 —— 自动分析不得覆盖已有的、可能更权威的差异判定。
        try:
            from src.research.novelty_compare import analyze_rows, summarize

            rows, comparisons = analyze_rows(claim, rows, evidence=evidence)
            record.difference_summary = summarize(comparisons)
        except Exception as e:  # noqa: BLE001 - 差异分析失败不得吞掉检索结果
            record.inaccessible.append(f"差异分析失败: {e}")
    record.rows = rows
    # lookup 自身声明的覆盖源 (用于说明检索边界)
    covered = getattr(lookup, "covered_sources", None)
    if covered:
        record.covered_sources = sorted(set(record.covered_sources) | set(covered))
        record.retrieval_scope.setdefault("covered", list(covered))
    kind = getattr(lookup, "kind", "")
    if kind:
        record.retrieval_scope.setdefault("kind", str(kind))
    if not rows:
        record.status = NoveltyStatus.unchecked
        record.conclusion = "在所检索范围内尚未发现等价结果 (有界表述)"
        return record

    basis = _comparison_basis(record)
    if basis == "known_equivalent":
        record.status = NoveltyStatus.known_equivalent
        record.conclusion = "已存在等价结果, 本研究为复现/理论整理, 不得宣称原创"
    elif basis == "potentially_distinct":
        record.status = NoveltyStatus.potentially_distinct
        record.conclusion = "检索到相近结果且已分析差异, 初步判定可能不同 (待专家复核)"
    else:
        # 命中但差异未分析: 保持待比较, 不得升级为"可能不同"
        record.status = NoveltyStatus.unchecked
        record.conclusion = (
            f"检索到 {len(record.rows)} 条相近结果, 但前提/结论差异尚未分析; "
            "保持待比较, 不得据此宣称创新"
        )
    return record


def render_novelty(record: NoveltyRecord) -> str:
    lines = [f"## 新颖性: {record.claim_id}", "", f"- 状态: {record.status.value}"]
    lines.append(f"- 检索日期: {record.search_date}")
    if record.queries:
        lines.append("- 查询: " + "; ".join(record.queries))
    # P1-1: 授权证据库与新颖性检索范围分开写, 不许用前者冒充后者
    if record.evidence_scope:
        lines.append("- 授权研究证据库: " + ", ".join(record.evidence_scope))
    if record.retrieval_scope:
        scope = record.retrieval_scope
        bits = []
        if scope.get("kind"):
            bits.append(f"通道 {scope['kind']}")
        if scope.get("covered"):
            bits.append("覆盖 " + ", ".join(str(x) for x in scope["covered"]))
        if scope.get("uncovered"):
            bits.append("未覆盖 " + ", ".join(str(x) for x in scope["uncovered"]))
        if bits:
            lines.append("- 新颖性检索范围: " + "; ".join(bits))
    elif record.covered_sources:
        lines.append("- 新颖性检索范围: " + ", ".join(record.covered_sources))
    if record.inaccessible:
        lines.append("- 检索受限: " + "; ".join(record.inaccessible))
    if record.rows:
        lines.append("")
        lines.append("| 已有结果 | 定位 | 差异 |")
        lines.append("|---|---|---|")
        for r in record.rows:
            lines.append(f"| {r.result} | {r.locator or '—'} | {r.difference} |")
    lines.append("")
    lines.append(f"结论: {record.conclusion}")
    lines.append(f"（{record.bounded_statement}）")
    return "\n".join(lines)
