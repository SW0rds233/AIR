from __future__ import annotations

"""可审查对照与仿真建议的失败用例 (计划书 §3 R4 / R5)。

反向安全测试:
- 没有原文条件时只能作**有界表述** (不能凭标题相似度宣称等价或不同);
- "未分析差异"不得被填成"已分析且无差异";
- 关键项 (单位/参数范围依据/求解器/资源) 待定时, 规格**只能保持草案**;
- 同一领域的不同缺口必须得到不同的设计;
- 建议不得声称已执行。
"""

import pytest


def _claim(**over):
    from src.research.schemas import Claim, ClaimType

    base = {
        "statement": "对所有实数 x: x**2 >= 0",
        "lhs": "x**2", "rhs": "0", "relation": ">=",
        "variables": ["x"], "variable_domains": {"x": "real"},
        "claim_type": ClaimType.definitional,
    }
    base.update(over)
    return Claim(**base)


def _row(**over):
    from src.research.schemas import NoveltyComparisonRow

    base = {"result": "已有定理"}
    base.update(over)
    return NoveltyComparisonRow(**base)


# --------------------------------------------------------------------------
# R4: 逐项差异分析
# --------------------------------------------------------------------------
def test_equivalent_work_is_reported_as_equivalent():
    from src.research.novelty_compare import EQUIVALENT, analyze_rows

    rows, comparisons = analyze_rows(_claim(), [
        _row(premises="x∈real", conclusion="x**2>=0"),
    ])
    assert comparisons[0].relation == EQUIVALENT
    assert "等价" in rows[0].difference


def test_missing_text_is_not_comparable_not_guessed():
    """没有原文条件 → 只能"未能判断", 且要说明缺什么。"""
    from src.research.novelty_compare import NOT_COMPARABLE, analyze_rows

    rows, comparisons = analyze_rows(_claim(), [_row()])
    assert comparisons[0].relation == NOT_COMPARABLE
    assert "未能判断" in rows[0].difference
    assert "缺" in rows[0].difference
    # 不得留空 (留空会被上层当成"尚未分析")
    assert rows[0].difference.strip()


def test_condition_direction_is_classified():
    from src.research.novelty_compare import (
        CONDITIONS_STRONGER,
        CONDITIONS_WEAKER,
        compare_with_row,
    )
    from src.research.novelty_compare import (
        CONDITIONS_STRONGER as CS,
    )

    # 已有工作条件更弱/结论更强 → 本研究更受限
    stronger = compare_with_row(_claim(), _row(
        premises="x∈real", conclusion="x**2>=0, y**2>=0"))
    assert stronger.relation == CONDITIONS_STRONGER

    # 本研究条件更少且结论更弱 → 已有工作更强
    weaker = compare_with_row(_claim(), _row(
        premises="x∈real, x>0", conclusion="x**2>=0, y**2>=0"))
    assert weaker.relation == CONDITIONS_WEAKER
    assert CS != CONDITIONS_WEAKER


def test_analysis_never_upgrades_on_its_own():
    """差异分析只产出文本与判定, 不得改变命题或新颖性状态。"""
    from src.research.novelty import assess
    from src.research.schemas import NoveltyComparisonRow, NoveltyStatus

    claim = _claim()
    before = claim.status.value
    record = assess(claim, lookup=lambda c: [NoveltyComparisonRow(result="只有标题")])
    assert claim.status.value == before
    # 无可比对的差异 → 保持待比较
    assert record.status == NoveltyStatus.unchecked
    assert "尚未分析" in record.conclusion or "不得据此宣称创新" in record.conclusion


def test_difference_is_recorded_when_comparable():
    from src.research.novelty import assess
    from src.research.schemas import NoveltyComparisonRow, NoveltyStatus

    claim = _claim()
    record = assess(claim, lookup=lambda c: [NoveltyComparisonRow(
        result="已有定理", premises="x∈real", conclusion="x**2>=0")])
    assert record.rows and record.rows[0].difference
    assert record.status == NoveltyStatus.known_equivalent
    assert "不得宣称原创" in record.conclusion
    summary = record.difference_summary
    assert summary.get("has_equivalent") is True


def test_novelty_summary_counts_relations():
    from src.research.novelty_compare import analyze_rows, summarize

    _rows, comparisons = analyze_rows(_claim(), [
        _row(premises="x∈real", conclusion="x**2>=0"),
        _row(result="无原文"),
    ])
    summary = summarize(comparisons)
    assert summary["total"] == 2
    assert summary["comparable"] == 1
    assert summary["not_comparable"] == 1
    assert summary["has_equivalent"] is True


def test_render_marks_bounded_statements():
    from src.research.novelty_compare import analyze_rows, render

    text = render(_claim(), analyze_rows(_claim(), [_row()])[1])
    assert "无法比较" in text or "未能判断" in text
    assert "有界表述" in text


# --------------------------------------------------------------------------
# R5: 建议针对缺口 + 关键项待定只能草案
# --------------------------------------------------------------------------
def test_pending_items_block_spec_validated():
    from src.experiments.planner import design_experiment
    from src.experiments.schemas import ExecutionStatus

    spec = design_experiment(_claim(), gaps=[], evidence=[])
    # 变量没有单位、参数范围无依据、求解器与资源待定 → 只能是草案
    assert spec.execution_status == ExecutionStatus.proposed, spec.execution_status
    assert spec.validation["ok"] is False
    joined = " ".join(spec.validation["errors"])
    assert "单位" in joined
    assert "草案" in spec.notes


def test_complete_spec_can_be_validated():
    from src.experiments.planner import design_experiment
    from src.experiments.schemas import ExecutionStatus, VariableSpec

    spec = design_experiment(_claim(), gaps=[], evidence=[])
    # 把关键项补齐后应当可以通过校验
    spec.variables = [VariableSpec(name="snr", role="manipulated", unit="dB",
                                   range="0-30", basis="设备手册")]
    spec.parameter_ranges = {"snr": "0-30 dB (设备手册)"}
    spec.solver = "显式 Runge-Kutta 4 阶 (步长 1e-4, 收敛检查)"
    spec.budget = "单机 4 核 / 30 分钟"
    from src.experiments.validator import validate_spec

    report = validate_spec(spec)
    assert report.ok is True, report.errors
    assert spec.execution_status in (ExecutionStatus.proposed,
                                     ExecutionStatus.spec_validated)


def test_different_gaps_get_different_designs():
    """同一领域的不同缺口 → 设计不同 (计划书 R5 验收)。"""
    from src.experiments.planner import design_experiment

    claim = _claim()
    evidence_gap = [{"gap_type": "missing_evidence", "target_ref": claim.id,
                     "statement": "有 2 条候选证据尚未判定支持关系"}]
    obligation_gap = [{"gap_type": "open_obligation", "target_ref": claim.id,
                       "obligation_kind": "prove_inequality",
                       "statement": "未关闭义务: 核验不等式"}]
    route_gap = [{"gap_type": "route_exhausted", "target_ref": claim.id,
                  "statement": "当前路线无进展"}]

    designs = {}
    for name, gap in (("evidence", evidence_gap), ("obligation", obligation_gap),
                      ("route", route_gap)):
        spec = design_experiment(claim, gaps=gap, evidence=[])
        designs[name] = spec.design
    assert len(set(designs.values())) == 3, designs
    assert any("证据缺口" in d for d in designs.values())
    assert any("义务核验" in d for d in designs.values())
    assert any("换路" in d for d in designs.values())


def test_distinguishing_test_drives_the_spec():
    from src.experiments.planner import design_experiment
    from src.experiments.schemas import ExperimentPurpose
    from src.research.modeling import compare_from_evidence
    from src.research.schemas import SourceEvidence

    claim = _claim()
    # 两条结构不同、方向相反的带定位原文结果 → 才能形成可区分检验
    evidence = [SourceEvidence(id="ev-1", title="文献 A", claim_id=claim.id,
                               location="p.2", excerpt="在低信噪比下噪声使特征分布重叠, 可分性下降"),
                SourceEvidence(id="ev-2", title="文献 B", claim_id=claim.id,
                               location="p.9", excerpt="信道均衡通过抑制漂移使可分性提升")]
    comparison = compare_from_evidence(claim, evidence)
    assert comparison.distinguishing
    spec = design_experiment(claim, gaps=[], evidence=evidence,
                             distinguishing=comparison.distinguishing[0])
    assert spec.purpose == ExperimentPurpose.compare_mechanisms
    assert spec.title.startswith("可区分检验")
    assert len(spec.alternative_explanations) >= 2
    assert "保留该机制" in spec.decision_rule and "替代解释" in spec.decision_rule


def test_spec_never_claims_execution():
    from src.experiments.planner import design_experiment
    from src.experiments.schemas import ExecutionStatus

    spec = design_experiment(_claim(), gaps=[], evidence=[])
    assert spec.execution_status not in (ExecutionStatus.executed,
                                         ExecutionStatus.analyzed)
    assert spec.artifacts == []
    assert any("未执行" in l for l in spec.limitations)
    assert spec.authorization == ""


@pytest.mark.parametrize("missing", ["unit", "range", "solver", "budget"])
def test_each_pending_item_alone_blocks_validation(missing):
    from src.experiments.planner import design_experiment
    from src.experiments.schemas import VariableSpec
    from src.experiments.validator import validate_spec

    spec = design_experiment(_claim(), gaps=[], evidence=[])
    spec.variables = [VariableSpec(name="snr", role="manipulated", unit="dB",
                                   range="0-30", basis="手册")]
    spec.parameter_ranges = {"snr": "0-30 dB"}
    spec.solver = "RK4"
    spec.budget = "4 核 / 30 分钟"
    if missing == "unit":
        spec.variables[0].unit = ""
    elif missing == "range":
        spec.parameter_ranges = {"snr": "(待定, 需物理依据)"}
    elif missing == "solver":
        spec.solver = "(待定: 依模型方程选择)"
    else:
        spec.budget = "单次任务资源上限待授权后填写"
    report = validate_spec(spec)
    assert report.ok is False, missing
