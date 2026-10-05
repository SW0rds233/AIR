from __future__ import annotations

"""领域候选模型比较的用例 (计划书 §3 R2)。

计划书要求: 不能"仅把影响归为因果、写一条占位方程就结束"; 应提出**带条件的模型
选项**, 比较候选机制, 并在冲突预测时给出可区分检验。反向要点也一样重要:
- 候选必须带来源引用, 没有来源/没有可观测预测的候选要被标为弱候选;
- 缺失的单位/边界/噪声必须显式列出, 不得默认具备;
- 候选机制不得直接改变任何命题状态。
"""

import pytest


def _claim(**over):
    from src.research.schemas import Claim, ClaimType, StudyDesign, StudyPlan

    base = {
        "statement": "信道变化影响射频指纹可分性",
        "claim_type": ClaimType.causal,
        "variables": ["snr", "separability"],
        "variable_domains": {"snr": "positive", "separability": "real"},
        "study": StudyPlan(design=StudyDesign.observational, treatment="信道变化",
                           outcome="指纹可分性", population="设备集", region="实验室",
                           period="2020-2024"),
    }
    base.update(over)
    return Claim(**base)


def _evidence(eid="ev-1", excerpt="在低信噪比下噪声使特征分布重叠, 可分性下降", claim_id="",
              location="p.3"):
    from src.research.schemas import SourceEvidence

    return SourceEvidence(id=eid, title=f"文献 {eid}", source_id="doc-1",
                          excerpt=excerpt, claim_id=claim_id, source_kind="journal",
                          location=location)


def test_mechanisms_come_from_anchored_results_not_templates():
    """P0-3: 机制必须来自带定位的原文结果, 且不同结构才算不同机制。"""
    from src.research.modeling import candidate_mechanisms

    claim = _claim()
    # 同一结构(路径+条件)的两条结果 → 只算一条机制, 不硬凑对照
    same = candidate_mechanisms(claim, [
        _evidence("ev-1", "在低信噪比下噪声使特征分布重叠, 可分性下降"),
        _evidence("ev-2", "低信噪比下噪声使特征重叠, 可分性下降")])
    assert len(same) == 1, [m.name for m in same]
    assert same[0].source_refs and same[0].anchors
    assert "原文" in same[0].fidelity

    # 路径/条件不同 → 两条结构不同的机制
    competing = candidate_mechanisms(claim, [
        _evidence("ev-1", "在低信噪比下噪声使特征分布重叠, 可分性下降"),
        _evidence("ev-2", "当设备集固定时, 信道均衡通过抑制漂移使可分性提升",
                  location="p.7")])
    assert len(competing) == 2, [m.name for m in competing]
    assert len({m.structural_key for m in competing}) == 2
    assert {m.sign for m in competing} == {1, -1}


def test_no_anchor_evidence_yields_only_a_hypothesis():
    """没有可定位原文时只给待检假设, 不得套模板冒充机制。"""
    from src.research.modeling import candidate_mechanisms, synthesize_models

    claim = _claim()
    mechanisms = candidate_mechanisms(claim, [])
    assert len(mechanisms) == 1
    hypothesis = mechanisms[0]
    assert hypothesis.origin == "template"
    assert hypothesis.supported is False
    assert hypothesis.missing
    # 有原文但没有定位 → 同样不算锚点 (定位是硬要求)
    unanchored = candidate_mechanisms(claim, [_evidence(location="")])
    assert len(unanchored) == 1 and not unanchored[0].source_refs

    comparison = synthesize_models(None, [], claim=claim)
    assert comparison.selected == ""
    assert "无法形成竞争模型" in comparison.note
    assert "待检假设" in comparison.why_selected


def test_mechanisms_report_missing_units_and_boundaries():
    from src.research.modeling import build_terms, candidate_mechanisms

    claim = _claim()
    mechanisms = candidate_mechanisms(claim, [])
    # 没有资料时: 噪声/边界/来源都必须是"缺失", 不得默认具备
    for mechanism in mechanisms:
        assert mechanism.missing, mechanism
        assert mechanism.supported is False
    terms = build_terms(claim, [])
    assert {t.name for t in terms} >= {"snr", "separability"}
    assert all(t.unit == "" for t in terms if t.name in ("snr", "separability")), \
        "未声明单位时不得臆造单位"


def test_term_table_detects_declared_units():
    from src.research.modeling import build_terms

    claim = _claim(statement="输出随投入变化", variables=["output_yi_yuan"],
                   variable_domains={"output_yi_yuan": "positive"})
    terms = build_terms(claim, [])
    unit = next(t for t in terms if t.name == "output_yi_yuan").unit
    assert unit == "亿元"


def test_comparison_selects_source_backed_mechanism_with_reason():
    from src.research.modeling import compare_from_evidence

    claim = _claim()
    comparison = compare_from_evidence(claim, [_evidence(claim_id=claim.id)])
    assert comparison.selected, comparison
    chosen = next(m for m in comparison.mechanisms if m.id == comparison.selected)
    assert chosen.source_refs, "有来源的候选应当胜出"
    assert comparison.why_selected
    assert "来源引用" in comparison.why_selected
    # 有来源、有预测的候选不算弱候选; 待检假设才必须显式标弱
    assert comparison.weak == [], comparison.weak
    assert "候选模型" in comparison.describe()


def test_conflicting_predictions_produce_distinguishing_test():
    from src.research.modeling import compare_from_evidence

    comparison = compare_from_evidence(_claim(), [
        _evidence("ev-1", "在低信噪比下噪声使特征分布重叠, 可分性下降"),
        _evidence("ev-2", "当设备集固定时, 信道均衡通过抑制漂移使可分性提升",
                  location="p.7")])
    conflicts = comparison.conflicts
    assert conflicts, "两条不同路径的原文机制方向相反, 应当冲突"
    assert conflicts[0]["kind"] == "prediction_conflict"
    assert "方向相反" in conflicts[0]["basis"]
    tests = comparison.distinguishing
    assert tests, "冲突预测必须产出可区分检验"
    test = tests[0]
    assert test.kind in ("derivation", "simulation")
    assert len(test.discriminates) == 2
    assert test.expected_if_a and test.expected_if_b
    assert "区分" in test.statement or "分离" in test.statement


def test_no_conflict_still_yields_necessity_check():
    """没有方向冲突时也要给出"条件是否必要"的检验, 不能选一个就结束。"""
    from src.research.modeling import Mechanism, compare_mechanisms

    a = Mechanism(id="a", name="A", relation="x", predictions=["p"], source_refs=["ev"])
    b = Mechanism(id="b", name="B", relation="x", predictions=["p"])
    comparison = compare_mechanisms([a, b])
    assert comparison.selected == "a"
    assert not comparison.conflicts
    # compare_mechanisms 本身不造检验; 端到端入口负责补"必要性检验"
    from src.research.modeling import compare_from_evidence

    end = compare_from_evidence(_claim(), [
        _evidence("ev-1", "在低信噪比下噪声使特征分布重叠, 可分性下降"),
        _evidence("ev-2", "低信噪比下干扰使特征混叠, 可分性下降", location="p.9")])
    assert end.distinguishing
    assert not end.conflicts


@pytest.mark.parametrize("kind", ["derivation", "simulation"])
def test_distinguishing_test_kind_depends_on_encodability(kind):
    from src.research.modeling import Mechanism, distinguishing_test

    encodable = kind == "derivation"
    a = Mechanism(id="a", name="A", predictions=["条件成立"],
                  formal_encoding="x >= 0" if encodable else "")
    b = Mechanism(id="b", name="B", predictions=["无条件成立"])
    test = distinguishing_test([a, b], {"a": "a", "b": "b",
                                        "a_prediction": a.predictions[0],
                                        "b_prediction": b.predictions[0]})
    assert test.kind == kind, test.kind
    assert test.cost >= 1
