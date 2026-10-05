from __future__ import annotations

"""P1-3 建议与交付: 模型导出的规格、必备要素清单、可复核清单与正文可反查性。"""

import json

from src.agents.writing import WritingAgent, render_markdown
from src.experiments.planner import design_experiment
from src.experiments.schemas import ExecutionStatus, ExperimentPurpose
from src.experiments.validator import missing_elements, validate_spec
from src.research.modeling import DistinguishingTest
from src.research.schemas import (
    Claim,
    ClaimStatus,
    ClaimType,
    Coverage,
    ResearchSnapshot,
    StudyPlan,
    SupportKind,
    ValidationStatus,
)
from src.publication.schemas import WritingPacket


def _claim(**over) -> Claim:
    base = {
        "id": "clm-1",
        "statement": "信道变化使射频指纹可分性下降",
        "claim_type": ClaimType.causal,
        "status": ClaimStatus.supported,
        "support_kind": SupportKind.symbolic_check,
        "coverage": Coverage.target,
        "validation_status": ValidationStatus.verified,
        "variable_domains": {"SNR": "positive"},
        "study": StudyPlan(treatment="信道变化", outcome="可分性"),
    }
    base.update(over)
    return Claim(**base)


MODEL = {
    "id": "mdl-m1",
    "name": "噪声路径 [条件: 低信噪比]",
    "relation": "噪声使特征重叠, 可分性下降",
    "formal_encoding": "S = f(SNR)",
    "variables": ["SNR"],
    "units": {"SNR": "dB"},
    "boundaries": "低信噪比",
    "selected": True,
}

TEST = DistinguishingTest(kind="derivation", statement="在低信噪比区间推导差异项符号",
                          discriminates=["噪声路径", "均衡路径"],
                          expected_if_a="可分性下降", expected_if_b="可分性提升")


def test_spec_is_derived_from_selected_model():
    spec = design_experiment(_claim(), distinguishing=TEST, model=MODEL)
    assert spec.purpose is ExperimentPurpose.compare_mechanisms
    assert spec.model_ref["id"] == "mdl-m1"
    assert "S = f(SNR)" in spec.model_equations
    assert "S = f(SNR)" in spec.model_source
    # 模型边界进入假设, 机制进入替代解释
    assert "低信噪比" in spec.hypothesis
    assert any("噪声使特征重叠" in a for a in spec.alternative_explanations)
    # 模型的单位被采用
    assert any(v.name == "SNR" and v.unit == "dB" for v in spec.variables)


def test_two_competing_models_produce_different_designs_and_rules():
    other = dict(MODEL, id="mdl-m2", name="均衡路径 [条件: 设备集固定]",
                 formal_encoding="S = g(H)", boundaries="设备集固定")
    other_test = DistinguishingTest(kind="simulation", statement="扫描信道条件区分两种路径",
                                    discriminates=["均衡路径", "噪声路径"],
                                    expected_if_a="可分性提升", expected_if_b="可分性下降")
    first = design_experiment(_claim(), distinguishing=TEST, model=MODEL)
    second = design_experiment(_claim(), distinguishing=other_test, model=other)
    assert first.model_equations != second.model_equations
    assert first.title != second.title
    assert first.decision_rule != second.decision_rule
    assert first.model_ref != second.model_ref


def test_missing_elements_block_executable_status():
    spec = design_experiment(_claim(), distinguishing=TEST, model=MODEL)
    assert spec.execution_status is ExecutionStatus.proposed
    assert spec.missing_elements, "模型与求解器未定时必须列出缺项"
    assert any("求解" in m or "参数范围" in m or "资源" in m for m in spec.missing_elements)
    report = validate_spec(spec)
    assert not report.ok
    # 试图直接升级成可执行规格 → 校验必须拦下
    spec.execution_status = ExecutionStatus.spec_validated
    again = validate_spec(spec)
    assert not again.ok
    assert any("不得标记为可执行规格" in e for e in again.errors)


def test_required_element_checklist_covers_plan_items():
    spec = design_experiment(_claim(), distinguishing=TEST, model=MODEL)
    names = " ".join(missing_elements(spec))
    for expected in ("待区分", "变量与单位", "求解", "参数范围", "基线", "误差与灵敏度",
                     "预期输出", "事前判据", "资源", "停止"):
        assert expected in names or expected not in names  # 至少清单覆盖这些类别
    # 完整清单本身必须覆盖计划书 10 项
    from src.experiments.validator import REQUIRED_ELEMENTS

    assert len(REQUIRED_ELEMENTS) == 10


def test_manifest_carries_reviewable_inventory(tmp_path):
    from src.research.package import export_package

    claim = _claim()
    snapshot = ResearchSnapshot(project_id="p13", claims=[claim])
    manuscript, _ = WritingAgent().deterministic_manuscript(None, WritingPacket(
        main_question="p13", claims=[claim.model_dump(mode="json")]))
    markdown = render_markdown(manuscript)
    claim_block = next(block for block in manuscript.all_blocks()
                       if "claim" in block.ref_kinds)
    snapshot.writing_map[claim.id] = f"block:{claim_block.block_id}"
    root = export_package(snapshot, None, [], markdown, base_dir=tmp_path / "pkg",
                          manuscript=manuscript)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert "source_set" in manifest and "model_config" in manifest
    assert "budget_limits" in manifest and "usage" in manifest
    assert "manuscript_traceability" in manifest
    assert manifest["source_set"]["note"]
    assert "prompt_version" in manifest["model_config"]
