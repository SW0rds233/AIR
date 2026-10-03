from __future__ import annotations

"""P1-1 有界命题比较: 同结论不同条件 / 已知等价 / 缺全文, 以及检索范围与证据库分离。

计划书验收: 三组对照必须分别给出**正确的有界关系**或"无法比较", 不因题名相似就认定等价/创新。
"""

from src.research.novelty import assess, render_novelty
from src.research.novelty_compare import (
    DIFFERENT,
    EQUIVALENT,
    NOT_COMPARABLE,
    SAME,
    compare_prior,
)
from src.research.schemas import (
    Claim,
    ClaimType,
    NoveltyComparisonRow,
    NoveltyStatus,
    SourceEvidence,
)


def _claim(**over) -> Claim:
    base = {
        "statement": "对所有实数 x: x**2 >= 0",
        "claim_type": ClaimType.definitional,
        "lhs": "x**2",
        "rhs": "0",
        "relation": ">=",
        "variables": ["x"],
        "variable_domains": {"x": "real"},
    }
    base.update(over)
    return Claim(**base)


def _row(**over) -> NoveltyComparisonRow:
    base = {"result": "已有工作 A", "premises": "x∈real", "conclusion": "x**2≥0",
            "method": "推导", "applicability": "实数域"}
    base.update(over)
    return NoveltyComparisonRow(**base)


def test_equivalent_prior_result_is_reported_equivalent():
    comparison = compare_prior(_claim(), _row())
    assert comparison.relation == EQUIVALENT
    assert comparison.dimensions["conclusion"] == SAME
    assert not comparison.bounded


def test_same_conclusion_different_conditions_is_comparable_not_equal():
    """同结论但条件不同: 必须给出条件关系, 不能判等价。"""
    comparison = compare_prior(_claim(), _row(premises="x∈positive"))
    assert comparison.relation != EQUIVALENT
    assert comparison.dimensions["premises"] in ("stronger", "weaker", "unknown")


def test_missing_fulltext_is_not_comparable_with_reason():
    """缺全文: 只有标题 → 无法比较, 且写明缺什么。"""
    evidence = SourceEvidence(id="ev-1", title="似曾相识的标题", excerpt="",
                              source_id="doc-9")
    comparison = compare_prior(_claim(), evidence)
    assert comparison.relation == NOT_COMPARABLE
    assert "未能判断" in comparison.difference_text
    assert comparison.notes, "不可比必须说明缺什么"


def test_similar_title_alone_never_means_equivalent():
    evidence = SourceEvidence(id="ev-2", title="x**2 >= 0 的一个新证明", excerpt="",
                              source_id="doc-10")
    comparison = compare_prior(_claim(), evidence)
    assert comparison.relation != EQUIVALENT
    assert comparison.relation != DIFFERENT


def test_structured_extraction_from_read_fulltext():
    evidence = SourceEvidence(
        id="ev-3", title="实数平方的非负性", source_id="doc-3", location="§2 p.4",
        excerpt="在实数域下, 对任意实数 x, 都有 x**2 >= 0。本文用配方法给出推导。")
    comparison = compare_prior(_claim(), evidence)
    assert comparison.relation == EQUIVALENT
    assert any("§2 p.4" in note for note in comparison.notes)
    # 结构化字段确实来自原文, 而不是标题
    assert comparison.result


def test_scope_separates_authorized_library_from_search_range():
    def lookup(claim):
        return [_row()]

    lookup.covered_sources = ["arXiv", "OpenAlex"]
    lookup.kind = "external"
    record = assess(_claim(), lookup=lookup, evidence_scope=["rf-A"],
                    retrieval_scope={"engines": ["arXiv"], "uncovered": ["中文期刊"]})
    assert record.evidence_scope == ["rf-A"]
    assert record.retrieval_scope["kind"] == "external"
    assert record.retrieval_scope["covered"] == ["arXiv", "OpenAlex"]
    assert record.retrieval_scope["uncovered"] == ["中文期刊"]

    rendered = render_novelty(record)
    assert "授权研究证据库: rf-A" in rendered
    assert "新颖性检索范围" in rendered
    assert "未覆盖 中文期刊" in rendered
    # 有界表述: 不得出现"世界首次/原创发现"这类断言
    assert "首次" not in rendered and "原创发现" not in rendered


def test_unchecked_record_stays_bounded_without_lookup():
    record = assess(_claim(), lookup=None, evidence_scope=["rf-A"])
    assert record.status is NoveltyStatus.unchecked
    assert "无法判定" in record.conclusion
    assert "在所检索范围内尚未发现等价结果" in render_novelty(record)
