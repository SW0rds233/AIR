from __future__ import annotations

"""反方审查清单的失败用例 (计划书 §7.2 / §7.4)。

反向安全测试: 八项检查必须**都跑过并留下结论**, 缺信息必须变成义务,
且任何审查意见都不得直接改变命题状态或关闭义务。
"""

import pytest


def _causal(**overrides):
    from src.research.schemas import Claim, ClaimType, StudyDesign, StudyPlan

    base = {
        "statement": "技术A提高了行业产出",
        "claim_type": ClaimType.causal,
        "study": StudyPlan(design=StudyDesign.observational, treatment="技术A",
                           outcome="行业产出"),
    }
    base.update(overrides)
    return Claim(**base)


def _formal():
    from src.research.schemas import Claim

    return Claim(statement="x^2 >= 0", lhs="x**2", rhs="0", relation=">=")


EXPECTED_CHECKS = {
    "sample_selection_bias", "external_validity", "measurement_error", "confounding",
    "multiple_comparisons", "reverse_causality", "metric_substitution",
    "conclusion_strength",
}


# --------------------------------------------------------------------------
# 覆盖率: 八项都要跑, 且每项都有结论
# --------------------------------------------------------------------------
def test_all_eight_checks_run_for_applied_claim():
    from src.research.adversarial import CLEAR, HIT, NOT_APPLICABLE, review_claim

    review = review_claim(_causal())
    assert {f.check_id for f in review.findings} == EXPECTED_CHECKS
    assert review.checked == 8
    for finding in review.findings:
        assert finding.status in (HIT, CLEAR, NOT_APPLICABLE), finding
        assert finding.reason, f"{finding.check_id} 缺少理由"
        if finding.status == HIT:
            assert finding.obligation_statement, f"{finding.check_id} 命中却未给出义务"


def test_formal_claim_declares_not_applicable_instead_of_silence():
    from src.research.adversarial import NOT_APPLICABLE, review_claim

    review = review_claim(_formal())
    assert review.checked == 8
    statuses = {f.check_id: f.status for f in review.findings}
    # 形式化命题不涉及应用类失效方式: 必须显式说明"不适用", 而不是假装通过
    for check_id in ("sample_selection_bias", "external_validity", "measurement_error",
                     "confounding", "reverse_causality", "metric_substitution"):
        assert statuses[check_id] == NOT_APPLICABLE, check_id


def test_missing_information_becomes_obligation_not_pass():
    from src.research.adversarial import review_claim

    review = review_claim(_causal())
    hits = {f.check_id for f in review.hits}
    # 没写样本/范围/测量/识别/时序 → 五项必须命中
    assert {"sample_selection_bias", "external_validity", "measurement_error",
            "confounding", "reverse_causality"} <= hits
    assert review.summary().startswith("反方审查: 8 项检查")


def test_declaring_the_required_information_clears_the_check():
    from src.research.adversarial import CLEAR, review_claim
    from src.research.schemas import (
        SourceEvidence,
        StudyDesign,
        StudyPlan,
        SupportKindOfEvidence,
    )

    claim = _causal(study=StudyPlan(
        design=StudyDesign.observational, treatment="技术A", outcome="行业产出",
        population="制造业企业", region="华东", period="2015-2023",
        confounders=["企业规模", "地区经济"],
        confounder_handling="倾向得分匹配 + 地区固定效应",
        identification_assumptions=["可忽略性", "SUTVA"],
        measurement_notes="产出按不变价增加值计量, 单位: 亿元",
        missing_data_handling="缺失率 <1%, MCAR 检验通过后按企业-年度插值",
        notes="已排除反向因果: 处理发生于 t 年, 结果取 t+1 年",
    ))
    evidence = [SourceEvidence(
        id="ev-1", title="制造业技术采用调查", claim_id=claim.id,
        support=SupportKindOfEvidence.supports,
        study_design=StudyDesign.observational,
    )]
    statuses = {f.check_id: f.status for f in review_claim(claim, evidence).findings}
    for check_id in ("sample_selection_bias", "external_validity", "measurement_error",
                     "confounding", "reverse_causality"):
        assert statuses[check_id] == CLEAR, check_id


# --------------------------------------------------------------------------
# 单项语义
# --------------------------------------------------------------------------
def test_rct_design_clears_reverse_causality():
    from src.research.adversarial import CLEAR, review_claim
    from src.research.schemas import StudyDesign, StudyPlan

    claim = _causal(study=StudyPlan(design=StudyDesign.rct, treatment="A", outcome="Y"))
    findings = {f.check_id: f for f in review_claim(claim).findings}
    assert findings["reverse_causality"].status == CLEAR
    assert "rct" in findings["reverse_causality"].reason


def test_associational_claim_is_not_asked_for_causal_identification():
    from src.research.adversarial import HIT, review_claim
    from src.research.schemas import Claim, ClaimType

    claim = Claim(statement="A 与 Y 正相关", claim_type=ClaimType.associational)
    findings = {f.check_id: f for f in review_claim(claim).findings}
    # 关联结论要点是"不得表述为因果", 而不是给出识别策略
    assert findings["confounding"].status == HIT
    assert "关联" in findings["confounding"].obligation_statement


def test_multiple_comparisons_hit_when_subgroups_without_correction():
    from src.research.adversarial import CLEAR, HIT, review_claim
    from src.research.schemas import Claim, ClaimType

    vague = Claim(statement="A 提高了 Y, 并按行业分组做了异质性分析",
                  claim_type=ClaimType.causal)
    assert {f.check_id: f for f in review_claim(vague).findings}[
        "multiple_comparisons"].status == HIT

    declared = Claim(statement="A 提高了 Y, 按行业分组分析并用 Bonferroni 校正",
                     claim_type=ClaimType.causal)
    assert {f.check_id: f for f in review_claim(declared).findings}[
        "multiple_comparisons"].status == CLEAR


def test_metric_substitution_flagged_for_proxy_outcome():
    from src.research.adversarial import HIT, review_claim
    from src.research.schemas import Claim, ClaimType, StudyPlan

    claim = Claim(statement="技术A提高了行业产出",
                  claim_type=ClaimType.causal,
                  study=StudyPlan(treatment="技术A", outcome="专利数量 (代理指标)",
                                  notes="用专利数量作为产出代理"))
    findings = {f.check_id: f for f in review_claim(claim).findings}
    assert findings["metric_substitution"].status == HIT
    assert "代理" in findings["metric_substitution"].obligation_statement


def test_conclusion_strength_hit_only_when_evidence_is_weak():
    from src.research.adversarial import CLEAR, HIT, review_claim
    from src.research.schemas import Claim, ClaimType, EvidenceGrade

    weak = Claim(statement="技术A必然提高行业产出", claim_type=ClaimType.causal,
                 evidence_grade=EvidenceGrade.unsupported)
    assert {f.check_id: f for f in review_claim(weak).findings}[
        "conclusion_strength"].status == HIT

    strong = Claim(statement="技术A必然提高行业产出", claim_type=ClaimType.causal,
                   evidence_grade=EvidenceGrade.identification_based)
    assert {f.check_id: f for f in review_claim(strong).findings}[
        "conclusion_strength"].status == CLEAR

    hedged = Claim(statement="技术A可能提高行业产出 (限于制造业)",
                   claim_type=ClaimType.causal,
                   evidence_grade=EvidenceGrade.unsupported)
    assert {f.check_id: f for f in review_claim(hedged).findings}[
        "conclusion_strength"].status == CLEAR


# --------------------------------------------------------------------------
# 与义务机制对接: 不得自动关闭
# --------------------------------------------------------------------------
def test_findings_become_informal_review_obligations_without_duplicates():
    from src.research.adversarial import review_claim_with_obligations

    claim = _causal()
    review, obligations = review_claim_with_obligations(claim)
    assert len(obligations) == len(review.hits)
    for obligation in obligations:
        assert obligation.acceptance_method == "informal_review"
        assert obligation.kind.startswith("adversarial_")
        assert obligation.claim_id == claim.id
        assert obligation.claim_version == claim.version

    # 同一命题重复审查不得重复生成相同义务
    existing = {o.statement for o in obligations}
    _, again = review_claim_with_obligations(claim, existing_statements=existing)
    assert again == []


def test_engine_derive_step_runs_adversarial_checklist(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import (
        ClaimQuestion,
        ObligationStatus,
        ResearchSpec,
        StudyDesign,
        StudyPlan,
    )
    from src.research.store import KIND_OBLIGATION, ResearchStore
    from src.verification.runner import VerificationRunner

    question = ClaimQuestion(
        statement="技术A提高了行业产出", category="causal",
        claim_type="causal",
        study=StudyPlan(design=StudyDesign.observational, treatment="技术A",
                        outcome="行业产出"),
    )
    spec = ResearchSpec(project_id="adv1", questions=[question], confirmed=True)
    store = ResearchStore("adv1", db_path=tmp_path / "adv1.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=40))
    assert engine.bootstrap()
    claim = engine._claims()[0]
    assert engine.plan_for_claim(claim).attempt.steps, "因果命题也应有可审查步骤"
    assert engine._act_derive_step.__self__ is engine

    from src.research.schemas import ActionType, ResearchAction

    assert engine._act_derive_step(ResearchAction(action_type=ActionType.derive_step,
                                                 object_id=claim.id)) is True

    events = [e for e in store.events() if e["type"] == "derive_step"]
    assert events, "derive_step 必须写入事件"
    payload = events[-1]["payload"]
    assert payload["adversarial_checked"] == 8, "八项检查必须都执行"
    assert payload["adversarial_hits"] >= 3
    digest = payload["adversarial"]
    assert {d["check"] for d in digest} == EXPECTED_CHECKS
    assert all(d["status"] for d in digest)

    stored = [o for o in store.list_latest(KIND_OBLIGATION)
              if str(o.get("kind", "")).startswith("adversarial_")]
    assert stored, "审查意见必须落成义务"
    for entry in stored:
        assert entry["acceptance_method"] == "informal_review"
        assert entry["status"] != ObligationStatus.closed.value
    store.close()


def test_adversarial_obligation_stays_blocked_on_check(tmp_path):
    """审查类义务没有工具可核验: check_step 只能让它保持 blocked。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import (
        ActionType,
        ClaimQuestion,
        ObligationStatus,
        ResearchAction,
        ResearchSpec,
        StudyDesign,
        StudyPlan,
    )
    from src.research.store import KIND_OBLIGATION, ResearchStore
    from src.verification.runner import VerificationRunner

    question = ClaimQuestion(
        statement="技术A提高了行业产出", category="causal", claim_type="causal",
        study=StudyPlan(design=StudyDesign.observational, treatment="技术A",
                        outcome="行业产出"),
    )
    spec = ResearchSpec(project_id="adv2", questions=[question], confirmed=True)
    store = ResearchStore("adv2", db_path=tmp_path / "adv2.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=40))
    assert engine.bootstrap()
    claim = engine._claims()[0]
    engine.plan_for_claim(claim)
    engine._act_derive_step(ResearchAction(action_type=ActionType.derive_step,
                                           object_id=claim.id))
    target = next(o for o in store.list_latest(KIND_OBLIGATION)
                  if str(o.get("kind", "")).startswith("adversarial_"))

    engine._act_check_step(ResearchAction(action_type=ActionType.check_step,
                                          object_id=target["id"]))
    after = [o for o in store.list_latest(KIND_OBLIGATION) if o["id"] == target["id"]][-1]
    assert after["status"] == ObligationStatus.blocked.value
    assert "待人工/独立审查确认" in after["detail"]
    store.close()


@pytest.mark.parametrize("design", ["rct", "did", "iv", "rdd", "matching"])
def test_identified_designs_do_not_need_extra_identification_for_confounding(design):
    from src.research.adversarial import CLEAR, review_claim
    from src.research.schemas import Claim, ClaimType, StudyPlan

    claim = Claim(statement="A 导致 Y", claim_type=ClaimType.causal,
                  study=StudyPlan(design=design, treatment="A", outcome="Y",
                                  confounders=["规模"]))
    findings = {f.check_id: f for f in review_claim(claim).findings}
    assert findings["confounding"].status == CLEAR, design
