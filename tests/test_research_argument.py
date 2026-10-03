from __future__ import annotations

"""论证链与写作缺口回流 (计划书 §3 R4)。

反向安全测试:
- 论证链只允许列**可核查事实** (步骤/义务/验证/证据都指向真实对象),
  不得把"有步骤"写成"已证明";
- 缺口只在有真实依据时提出 (每条指向具体对象), 没有依据时不得凭空挑刺;
- 缺口回流必须落成**真义务** (指向命题与命题版本, 带能核查它的验收方法),
  且幂等: 重复调用不会重复创建, 也不会空转;
- `advisory` 缺口不得重开研究循环 (否则只能人工消除的缺口会让结论永远无法交付),
  `blocking` 缺口才重开, 且重开后必须收敛 (预算仍是最外层护栏);
- 回流只新增义务, **不改写任何结论状态**。
"""

import pytest

from src.research.argument import build_argument, writing_gaps
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    EvidenceLink,
    ObjectRef,
    ProofAttempt,
    ProofObligation,
    ProofStep,
    ResearchSnapshot,
    SourceEvidence,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)


def _claim(**overrides) -> Claim:
    base = {
        "id": "clm-1",
        "statement": "对所有实数 x、y: x^2 + y^2 >= 2*x*y",
        "problem_id": "prob",
        "lhs": "x**2 + y**2",
        "rhs": "2*x*y",
        "relation": ">=",
        "variables": ["x", "y"],
        "variable_domains": {"x": "real", "y": "real"},
    }
    base.update(overrides)
    return Claim(**base)


def _supported(**overrides) -> Claim:
    base = {
        "status": ClaimStatus.supported,
        "assurance": "symbolic_checked",
        "support_kind": SupportKind.symbolic_check,
        "coverage": Coverage.target,
        "validation_status": ValidationStatus.verified,
    }
    base.update(overrides)
    return _claim(**base)


def _record(**overrides) -> VerificationRecord:
    base = {
        "id": "ver-1",
        "tool": "sympy",
        "claim_id": "clm-1",
        "claim_version": 1,
        "status": "passed",
        "validation_status": ValidationStatus.verified,
        "scope": "target",
        "detail": "closes obl-1",
        "certificate": "x**2 + y**2 - 2*x*y = (x - y)**2 >= 0",
    }
    base.update(overrides)
    return VerificationRecord(**base)


def _attempt(**overrides) -> ProofAttempt:
    base = {
        "id": "pf-1",
        "target_claim_id": "clm-1",
        "target_version": 1,
        "strategy": "nonnegative_difference",
        "steps": [
            ProofStep(index=0, statement="把差式配方为平方", justification="代数变形",
                      rule="nonnegative_difference",
                      requires_conditions=["x、y 为实数"]),
        ],
    }
    base.update(overrides)
    return ProofAttempt(**base)


def _obligation(**overrides) -> ProofObligation:
    base = {
        "id": "obl-1",
        "statement": "x^2 + y^2 - 2*x*y >= 0",
        "kind": "prove_inequality",
        "claim_id": "clm-1",
        "claim_version": 1,
    }
    base.update(overrides)
    return ProofObligation(**base)


def _snapshot(claims, **overrides) -> ResearchSnapshot:
    return ResearchSnapshot(project_id="p", claims=list(claims), **overrides)


# --------------------------------------------------------------------------
# 论证链: 可逐条反查
# --------------------------------------------------------------------------
def test_argument_chain_links_every_step_to_objects():
    claim = _supported()
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        obligations=[_obligation(step_id="0")],
        verifications=[_record(obligation_ref=ObjectRef(id="obl-1", version=1))],
    )
    argument = build_argument(snapshot, claim)

    assert argument.claim_id == "clm-1"
    assert argument.claim_version == 1
    assert argument.conditions == ["x ∈ real", "y ∈ real"]
    assert argument.has_chain
    assert len(argument.steps) == 1
    step = argument.steps[0]
    assert step.statement == "把差式配方为平方"
    assert step.rule == "nonnegative_difference"
    assert step.obligation_id == "obl-1"
    assert step.obligation_status == "open"
    assert step.verification_link == "confirmed"
    assert step.verification_id == "ver-1"
    assert step.verification_tool == "sympy"
    assert argument.caveats == []

    rendered = argument.render()
    assert "论证链 (clm-1@v1, 状态 supported)" in rendered
    assert "步骤" in rendered
    assert "obl-1" in rendered
    assert "ver-1/sympy" in rendered
    # 证书摘要不外泄为"结论成立"的断言
    assert "证毕" not in rendered


def test_legacy_implicit_verification_link_is_marked_unconfirmed():
    """P0-4: 旧数据只有隐式关联时必须标"关联待确认", 不得当作这一步的依据。"""
    claim = _supported()
    legacy = _record(certificate="本次核验针对 obl-1 的结论")
    snapshot = _snapshot([claim], attempts=[_attempt()],
                         obligations=[_obligation(step_id="0")], verifications=[legacy])
    argument = build_argument(snapshot, claim)

    step = argument.steps[0]
    assert step.verification_id == "ver-1"
    assert step.verification_link == "unconfirmed"
    assert any("待人工确认" in c for c in argument.caveats), argument.caveats
    assert "关联待确认" in step.describe()


def test_unrelated_verification_is_not_silently_attached():
    """没有显式引用也没有义务痕迹 → 不补边, 并如实说明缺验证输入。"""
    claim = _supported()
    snapshot = _snapshot([claim], attempts=[_attempt()],
                         obligations=[_obligation(step_id="0")],
                         verifications=[_record(certificate="无关的核验记录")])
    argument = build_argument(snapshot, claim)

    step = argument.steps[0]
    assert step.verification_id == ""
    assert step.verification_link == ""
    assert argument.caveats, "缺验证输入必须在论证链里点明"


def test_argument_chain_without_steps_says_so_instead_of_implying_proof():
    claim = _supported(support_kind=SupportKind.informal_argument)
    argument = build_argument(_snapshot([claim]), claim)

    assert not argument.has_chain
    assert "无已记录的推导步骤" in argument.render()
    assert "该结论标记为 supported, 但快照中没有可用的验证记录" in argument.caveats


def test_argument_chain_marks_plan_only_and_lists_open_items():
    claim = _claim(status=ClaimStatus.in_progress)
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        obligations=[_obligation(status="blocked", statement="缺少前提 x、y 为实数")],
    )
    argument = build_argument(snapshot, claim)

    assert "步骤仅为推导计划, 尚未获得工具核验" in argument.caveats
    assert any("obl-1" in item for item in argument.open_items)
    assert "未决项" in argument.render()


def test_stale_and_nonscientific_records_are_not_usable_evidence():
    claim = _supported()
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        verifications=[
            _record(id="ver-stale", stale=True),
            _record(id="ver-timeout", validation_status=ValidationStatus.timeout),
            _record(id="ver-unknown", validation_status=ValidationStatus.unknown),
        ],
    )
    argument = build_argument(snapshot, claim)

    assert argument.steps[0].verification_id == ""
    assert "该结论标记为 supported, 但快照中没有可用的验证记录" in argument.caveats


def test_evidence_refs_are_attributed_per_claim():
    claim = _supported()
    other = _supported(id="clm-2", statement="另一条结论")
    mine = SourceEvidence(id="ev-1", title="我的来源", location="p.3", claim_id="clm-1",
                          support="supports", existence_verified=True)
    theirs = SourceEvidence(id="ev-2", title="别人的来源", claim_id="clm-2",
                            support="supports")
    snapshot = _snapshot(
        [claim, other],
        evidence=[mine, theirs],
        evidence_links=[EvidenceLink(
            id="lnk-1", claim_ref=ObjectRef(id="clm-1", version=1),
            source_ref=ObjectRef(id="ev-1", version=1), relation="supports")],
    )
    argument = build_argument(snapshot, claim)

    ids = [ref["id"] for ref in argument.evidence_refs]
    assert ids == ["ev-1"]
    assert "我的来源" in argument.render()
    assert "别人的来源" not in argument.render()


# --------------------------------------------------------------------------
# 缺口: 只在有依据时提出
# --------------------------------------------------------------------------
def test_clean_snapshot_has_no_gaps():
    claim = _supported()
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        obligations=[_obligation(status="closed", validation_status="verified")],
        verifications=[_record()],
    )
    assert writing_gaps(snapshot) == []


def test_supported_without_records_and_without_steps_is_reported():
    claim = _supported()
    gaps = writing_gaps(_snapshot([claim]))

    kinds = {gap.kind: gap.severity for gap in gaps}
    assert kinds["missing_verification_input"] == "blocking"
    assert kinds["missing_argument_chain"] == "blocking"
    for gap in gaps:
        assert gap.claim_id == "clm-1"
        assert gap.statement and gap.detail


def test_steps_present_removes_only_the_chain_gap():
    claim = _supported()
    gaps = writing_gaps(_snapshot([claim], attempts=[_attempt()]))

    kinds = {gap.kind for gap in gaps}
    assert "missing_argument_chain" not in kinds
    assert "missing_verification_input" in kinds


def test_dangling_evidence_ref_is_advisory():
    """只能人工补齐的缺口不得标成阻塞: 否则结论永远无法交付。"""
    claim = _supported()
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        verifications=[_record()],
        evidence_links=[EvidenceLink(
            id="lnk-x", claim_ref=ObjectRef(id="clm-1", version=1),
            source_ref=ObjectRef(id="ev-missing", version=1), relation="supports")],
    )
    gaps = writing_gaps(snapshot)

    assert [g.kind for g in gaps] == ["dangling_evidence_ref"]
    assert gaps[0].severity == "advisory"
    assert "ev-missing" in gaps[0].statement


def test_scope_mismatch_requires_local_record_on_stronger_claim():
    """整体判定却只有局部记录 → 必须报出 (等级不得被局部验证升级)。"""
    claim = _supported(assurance="solver_checked")
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        verifications=[_record(scope="step")],
        obligations=[_obligation(status="closed", validation_status="verified")],
    )
    gaps = writing_gaps(snapshot)

    assert [g.kind for g in gaps] == ["scope_mismatch"]
    assert gaps[0].severity == "advisory"
    assert "局部验证不得写成整体证明" in gaps[0].detail


def test_causal_supported_claim_without_interval_is_reported():
    claim = _supported(claim_type="causal", effect_estimate={"point": 0.4})
    gaps = writing_gaps(_snapshot([claim], attempts=[_attempt()],
                                 verifications=[_record(tool="stats")]))

    assert "missing_uncertainty" in {g.kind for g in gaps}


def test_experiment_gaps_are_grounded_in_specs():
    claim = _supported()
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        verifications=[_record()],
        experiment_specs=[
            {"id": "exp-1", "claim_id": "clm-1", "decision_rule": ""},
            {"id": "exp-2", "claim_id": "clm-1", "decision_rule": "效应>0.2",
             "execution_status": "executed", "artifacts": []},
        ],
    )
    gaps = writing_gaps(snapshot)

    by_kind = {gap.kind: gap for gap in gaps}
    assert by_kind["suggestion_without_rule"].severity == "advisory"
    assert "exp-1" in by_kind["suggestion_without_rule"].statement
    assert by_kind["execution_without_artifact"].severity == "advisory"
    assert "exp-2" in by_kind["execution_without_artifact"].statement


def test_gaps_are_deduplicated():
    claim = _supported()
    duplicate = EvidenceLink(
        id="lnk-a", claim_ref=ObjectRef(id="clm-1", version=1),
        source_ref=ObjectRef(id="ev-missing", version=1), relation="supports")
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        verifications=[_record()],
        evidence_links=[duplicate, duplicate.model_copy(update={"id": "lnk-b"})],
    )
    gaps = [g for g in writing_gaps(snapshot) if g.kind == "dangling_evidence_ref"]

    assert len(gaps) == 1


# --------------------------------------------------------------------------
# 写作: 缺口与论证链必须出现在正文里
# --------------------------------------------------------------------------
def test_manuscript_contains_argument_chain_and_gap_section():
    from src.agents.theory_writer import build_manuscript
    from src.rag.theory_render import render_markdown

    claim = _supported()
    snapshot = _snapshot(
        [claim],
        attempts=[_attempt()],
        obligations=[_obligation(status="closed", validation_status="verified")],
        verifications=[_record()],
        evidence_links=[EvidenceLink(
            id="lnk-x", claim_ref=ObjectRef(id="clm-1", version=1),
            source_ref=ObjectRef(id="ev-missing", version=1), relation="supports")],
    )
    text = render_markdown(build_manuscript(snapshot, "测试主题"))

    assert "论证链 (clm-1@v1" in text
    assert "写作阶段发现的缺口 (需回到研究循环)" in text
    assert "dangling_evidence_ref" in text
    assert "悬空引用" in text


# --------------------------------------------------------------------------
# 回流: 缺口 → 真义务 → 重开研究循环
# --------------------------------------------------------------------------
@pytest.fixture()
def engine(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="r4fb", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("r4fb", db_path=tmp_path / "r4fb.sqlite")
    eng = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                       budget=ResearchBudget(max_actions=15))
    assert eng.bootstrap()
    yield eng, store
    store.close()


def _inject_dangling(store, claim) -> None:
    from src.research.store import KIND_EVIDENCE_LINK

    store.put(KIND_EVIDENCE_LINK, "lnk-x", EvidenceLink(
        id="lnk-x", claim_ref=ObjectRef(id=claim.id, version=claim.version),
        source_ref=ObjectRef(id="ev-does-not-exist", version=1)).model_dump(mode="json"))


def _inject_supported_without_records(store, problem_id: str) -> None:
    from src.research.store import KIND_CLAIM

    orphan = _supported(id="clm-orphan", statement="孤立的 supported 命题",
                        problem_id=problem_id)
    store.put(KIND_CLAIM, orphan.id, orphan.model_dump(mode="json"))


def test_feedback_creates_obligations_bound_to_claim_version(engine):
    from src.research.store import KIND_OBLIGATION

    eng, store = engine
    claim = eng._claims()[0]
    _inject_dangling(store, claim)

    assert eng.writing_gap_feedback() is False, "advisory 缺口不应要求重开研究循环"
    stored = {o.get("gap_kind"): o for o in store.list_latest(KIND_OBLIGATION)
              if o.get("gap_kind")}
    assert set(stored) == {"dangling_evidence_ref"}
    entry = stored["dangling_evidence_ref"]
    assert entry["claim_id"] == claim.id
    assert entry["claim_version"] == claim.version
    assert entry["required"] is False
    assert entry["acceptance_method"] == "informal_review"
    assert entry["kind"] == "writing_dangling_evidence_ref"
    # 命题状态不得被回流改写
    assert eng._get_claim(claim.id).status == claim.status


def test_feedback_is_idempotent(engine):
    from src.research.store import KIND_OBLIGATION

    eng, store = engine
    claim = eng._claims()[0]
    _inject_dangling(store, claim)

    assert eng.writing_gap_feedback() is False
    before = len(store.list_latest(KIND_OBLIGATION))
    assert eng.writing_gap_feedback() is False
    assert len(store.list_latest(KIND_OBLIGATION)) == before


def test_blocking_gap_reopens_loop_and_converges(engine):
    eng, store = engine
    _inject_supported_without_records(store, eng.spec.problem_id)

    assert eng.writing_gap_feedback() is True, "可消解的阻塞缺口必须要求继续研究"
    result = eng.run()

    kinds = {o.kind for o in result.snapshot.obligations}
    assert "writing_missing_verification_input" in kinds
    assert "writing_missing_argument_chain" in kinds
    # 重开后必须收敛, 不得无限重跑 (缺口幂等 + 预算护栏)
    assert result.usage["actions"] <= 15


def test_feedback_ignores_gaps_of_other_problems(engine):
    """缺口指向别的研究问题的命题时不得写进本问题的义务集 (R6)。

    `writing_gap_feedback` 是按本问题的快照工作的; 这里直接喂一份包含外来命题的
    快照, 检查它**不猜归属**: 外来缺口既不生成义务, 也要留下可审计的理由。
    """
    from src.research.argument import writing_gaps
    from src.research.store import KIND_OBLIGATION

    eng, store = engine
    mine = eng._claims()[0]
    other = _supported(id="clm-other", statement="另一个问题的结论", problem_id="prob-other")
    foreign = _snapshot(
        [mine, other],
        evidence_links=[
            EvidenceLink(id="lnk-mine", claim_ref=ObjectRef(id=mine.id, version=1),
                         source_ref=ObjectRef(id="ev-missing", version=1)),
            EvidenceLink(id="lnk-foreign", claim_ref=ObjectRef(id=other.id, version=1),
                         source_ref=ObjectRef(id="ev-missing", version=1)),
        ],
    )
    kinds = writing_gaps(foreign)
    assert {g.claim_id for g in kinds} == {mine.id, other.id}

    assert eng.writing_gap_feedback(foreign) is False
    created = [o for o in store.list_latest(KIND_OBLIGATION) if o.get("gap_kind")]
    assert {o["claim_id"] for o in created} == {mine.id}
    assert any("已拒绝回流" in note and other.id in note for note in eng.notes)


def test_clean_run_creates_no_writing_obligations(engine):
    """正常跑完的结论不应被写作缺口机制误伤 (回归)。"""
    from src.research.store import KIND_OBLIGATION

    eng, store = engine
    result = eng.run()

    assert result.feedback_reopened is False
    assert [o.kind for o in result.snapshot.obligations
            if o.kind.startswith("writing_")] == []
    assert not [o for o in store.list_latest(KIND_OBLIGATION) if o.get("gap_kind")]
    assert [c.status.value for c in result.snapshot.claims] == ["supported"]


def test_derived_equality_claim_has_reviewable_steps(tmp_path):
    """等号条件这类派生命题也要有可逐步复核的步骤, 否则收尾时会自造阻塞缺口。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    text = ("判断对所有实数 x、y，是否有 x² + y² ≥ 2xy；给出证明和等号条件。")
    store = ResearchStore("eqsteps", db_path=tmp_path / "eqsteps.sqlite")
    engine = TheoryEngine(ResearchSpec(project_id="eqsteps", problem_statement=text), store,
                          runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=40))
    result = engine.run()

    derived = [c for c in result.snapshot.claims if c.id.startswith("eq-")]
    assert derived, "该问题应派生出等号条件命题"
    for claim in derived:
        attempts = [a for a in result.snapshot.attempts
                    if a.target_claim_id == claim.id and a.steps]
        assert attempts, f"{claim.id} 缺少可复核步骤"
        assert attempts[0].strategy == "equality_condition"
        argument = build_argument(result.snapshot, claim)
        assert argument.has_chain
        assert "parent_claim" in argument.render()
    assert writing_gaps(result.snapshot) == []
    assert result.gate.passed
    store.close()


def test_advisory_gap_does_not_reopen_loop(engine):
    eng, store = engine
    claim = eng._claims()[0]
    _inject_dangling(store, claim)

    result = eng.run()

    assert result.feedback_reopened is False
    gap_obligations = [o for o in result.snapshot.obligations
                       if o.kind == "writing_dangling_evidence_ref"]
    assert len(gap_obligations) == 1
    assert gap_obligations[0].required is False
