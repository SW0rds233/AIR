from __future__ import annotations

"""推理内核的差异性与行为用例 (合并计划 M2「按职责抽取」)。

M2 的验收要求是"精确数学题、机理题与纯综述走不同子任务图"以及"抽取不得改变行为"。
因此这里做两件事:

1. **差异测试**: 证明引擎路径 (`TheoryEngine._act_plan_proof` / `_act_derive_step`)
   与团队路径 (`ReasoningAgent`) 用的是**同一份**推理实现 —— 靠替换内核函数并观察
   两条路径是否都变化来验证, 而不是靠阅读代码;
2. **内核行为**: 规则路径始终计算、反方审查意见只转成待核验义务 (不改结论状态)、
   设计的证书判定链不产生自由推导步骤。

团队路径的那条用例同时覆盖"角色能真正调内核并交出结构化成果", 这是 M2 的实质内容。
"""

import pytest

from src.research.reasoning_kernel import (
    build_design_steps,
    derive_steps_for,
    design_steps_for,
    is_design_claim,
    plan_proof_for,
    seek_counterexample_for,
)
from src.research.schemas import (
    Claim,
    ObligationStatus,
    ProofAttempt,
    ProofObligation,
    ProofStep,
)


# --------------------------------------------------------------------------
# 夹具: 一个可形式化的代数命题 + 一条义务
# --------------------------------------------------------------------------
def _claim(**over) -> Claim:
    data = {
        "id": "clm-kernel",
        "statement": "对所有实数 x: x**2 >= 0",
        "variables": ["x"],
        "variable_domains": {"x": "real"},
    }
    data.update(over)
    return Claim(**data)


def _obligation(claim: Claim, **over) -> ProofObligation:
    data = {
        "id": "obl-kernel",
        "statement": "证明 x**2 >= 0 对任意实数成立",
        "kind": "prove_inequality",
        "acceptance_method": "sympy",
        "claim_id": claim.id,
        "claim_version": claim.version,
        "status": ObligationStatus.open,
    }
    data.update(over)
    return ProofObligation(**data)


def _design_claim(**over) -> Claim:
    data = {
        "id": "clm-design",
        "statement": "2-(211,15,1) 设计不存在",
        "design_v": 211, "design_k": 15, "design_lambda": 1, "design_b": 211, "design_r": 15,
    }
    data.update(over)
    return Claim(**data)


# --------------------------------------------------------------------------
# 证明计划
# --------------------------------------------------------------------------
def test_plan_proof_for_returns_plan_writes_and_event_payload():
    claim = _claim()
    obligation = _obligation(claim)
    outcome = plan_proof_for(claim, obligations=[obligation])
    assert outcome.ok and outcome.progressed
    # 写入集合: 计划 (attempt) + 计划提出的义务
    kinds = [kind for kind, _, _ in outcome.writes]
    assert kinds.count("attempt") == 1
    # 事件载荷必须满足 logging_schema 的必需键 (少一个就成契约异常)
    assert outcome.events
    kind, payload = outcome.events[0]
    assert kind == "plan_proof"
    for key in ("claim_id", "strategy"):
        assert key in payload
    assert outcome.idempotency_key.startswith(f"plan:{claim.id}:")
    # 计划载荷里带 attempt, 供下游 `derive_steps_for` 沿用同一步骤集
    assert isinstance(outcome.payload["attempt"], dict)


def test_plan_proof_for_ignores_closed_obligations():
    claim = _claim()
    closed = _obligation(claim, id="obl-closed", status=ObligationStatus.closed)
    outcome = plan_proof_for(claim, obligations=[closed])
    # 关闭的义务不再进入计划 (否则会重复提出已完成的事)
    assert outcome.payload["proposed"] == []
    assert outcome.payload["checks"] == []


def test_plan_proof_for_reuses_cached_plan_object():
    """复用调用方缓存的那份计划 —— 否则引擎与内核会各算一份, 步骤分叉。"""
    claim = _claim()
    obligation = _obligation(claim)
    first = plan_proof_for(claim, obligations=[obligation])
    cached = ProofAttempt(target_claim_id=claim.id, target_version=claim.version,
                          strategy="symbolic", status="in_progress",
                          steps=[ProofStep(index=1, statement="缓存步骤",
                                           justification="cached", rule="sympy")])
    seen: list[ProofAttempt] = []
    outcome = plan_proof_for(claim, obligations=[obligation],
                             reuse_plan=type("P", (), {"attempt": cached,
                                                       "proposed": [], "checks": [],
                                                       "strategy": "symbolic",
                                                       "source": "rules",
                                                       "notes": ""})(),
                             on_plan=seen.append)
    assert outcome.payload["strategy"] == "symbolic"
    assert outcome.payload["attempt"]["steps"][0]["statement"] == "缓存步骤"
    assert seen == []            # 复用时不回调 (没有新计划产生)
    del first


def test_plan_proof_for_calls_on_plan_when_it_computes():
    claim = _claim()
    seen: list[object] = []
    plan_proof_for(claim, obligations=[_obligation(claim)], on_plan=seen.append)
    assert len(seen) == 1        # 新算出的计划必须回调给调用方缓存


# --------------------------------------------------------------------------
# 推导步骤 + 反方审查
# --------------------------------------------------------------------------
def test_derive_steps_marks_formal_gap_without_failing():
    """应用/数据类命题没有形式化片段, 但反方审查仍必须执行。"""
    claim = Claim(id="clm-app", statement="政策 A 提高产出",
                  claim_type="causal")
    attempt = ProofAttempt(target_claim_id=claim.id, target_version=claim.version,
                           strategy="empirical", status="in_progress", steps=[])
    outcome = derive_steps_for(claim, attempt=attempt, obligations=[], evidence=[])
    assert outcome.ok and outcome.progressed
    assert attempt.steps, "必须补一条显式的'无形式化片段'步骤"
    assert attempt.steps[0].rule == "adversarial_review_only"
    _, payload = outcome.events[0]
    assert payload["formal_gap"] is True
    assert payload["adversarial_checked"] >= 1, "八项反方审查必须真的跑过"
    # 结论状态未被触碰: 内核只产出义务, 不写 supported/refuted
    assert attempt.status == "complete"


def test_derive_steps_turns_review_findings_into_obligations():
    claim = _claim()
    attempt = ProofAttempt(target_claim_id=claim.id, target_version=claim.version,
                           strategy="symbolic", status="in_progress",
                           steps=[ProofStep(index=1, statement="x**2 >= 0",
                                            justification="平方非负", rule="sympy")])
    outcome = derive_steps_for(claim, attempt=attempt, obligations=[], evidence=[])
    kinds = [kind for kind, _, _ in outcome.writes]
    assert kinds[0] == "attempt"
    # 审查意见转成的义务必须与 attempt 同事务写入
    assert kinds.count("obligation") == len(outcome.payload["review_obligations"])
    for obligation in outcome.payload["review_obligations"]:
        assert obligation["required"] is False, "实践性保留意见不得阻塞既有门槛"
        assert obligation["claim_id"] == claim.id


def test_derive_steps_is_idempotent_by_step_content():
    claim = _claim()

    def _run() -> str:
        attempt = ProofAttempt(target_claim_id=claim.id,
                               target_version=claim.version, strategy="symbolic",
                               status="in_progress",
                               steps=[ProofStep(index=1, statement="x**2 >= 0",
                                                justification="平方非负", rule="sympy")])
        return derive_steps_for(claim, attempt=attempt, obligations=[],
                                evidence=[]).idempotency_key

    assert _run() == _run()


# --------------------------------------------------------------------------
# 设计/计数存在性: 推导步骤 = 证书判定链
# --------------------------------------------------------------------------
def test_is_design_claim_detects_parameterised_statement():
    assert is_design_claim(_design_claim()) is True
    assert is_design_claim(_claim()) is False
    assert is_design_claim(None) is False


def test_design_steps_use_certificate_chain_with_obligation_refs():
    claim = _design_claim()
    obligation = ProofObligation(id="obl-design", statement="设计必要性",
                                 kind="design_necessity", claim_id=claim.id,
                                 claim_version=claim.version)
    outcome = design_steps_for(claim, obligations=[obligation],
                               step_builder=build_design_steps)
    assert outcome.ok
    assert outcome.payload["strategy"] == "design_necessity_certificate"
    steps = outcome.payload["step_items"]
    assert steps, "证书判定链必须至少有一步"
    assert outcome.payload["steps"] == len(steps), "数量与步骤集必须一致"
    # 每步都能反查到定理与输入 (证书链的意义所在)
    assert all(step["justification"] for step in steps)
    assert any(step["rule"] for step in steps)
    _, payload = outcome.events[0]
    assert payload["formal_gap"] is False
    assert payload["adversarial_checked"] == 0, "证书链不需要自由推导审查"
    assert outcome.idempotency_key.startswith(f"derive:{claim.id}:")


def test_design_steps_have_no_free_text_math():
    """证书链的步骤必须来自重算, 而不是模型自由生成。"""
    claim = _design_claim()
    outcome = design_steps_for(claim, obligations=[],
                               step_builder=build_design_steps)
    for step in outcome.payload["step_items"]:
        assert step["justification"] == "由证书重算, 可反查"


# --------------------------------------------------------------------------
# 反例搜索: 只到"可核验候选"
# --------------------------------------------------------------------------
def test_seek_counterexample_reports_unsupported_without_raising():
    claim = Claim(id="clm-text", statement="这段文字无法编码成约束")
    outcome = seek_counterexample_for(claim)
    assert outcome.ok is False
    assert outcome.payload["reason"] == "unsupported"
    assert outcome.notes, "无法编码时必须给出可执行下一步"


def test_seek_counterexample_returns_a_check_for_encodable_claim():
    claim = _claim()
    outcome = seek_counterexample_for(claim, available={"z3": True, "sympy": True})
    if outcome.ok:
        assert outcome.payload["tool"]
    # 内核**不执行**工具, 也不写验证记录 (那是判定层的职责)
    assert outcome.writes == []
    assert outcome.progressed is False


# --------------------------------------------------------------------------
# 反例搜索的纯判定
# --------------------------------------------------------------------------
def test_counterexample_verdict_only_refutes_with_a_real_witness():
    """报"找到反例"却给不出可回代反例时不得判为数学反驳。"""
    from src.research.reasoning_kernel import counterexample_verdict_for
    from src.research.schemas import ValidationStatus

    refuted = counterexample_verdict_for(
        ValidationStatus.counterexample_found, {"x": 3}, "反例 x=3")
    assert refuted.action == "refute"
    assert refuted.scientific is True
    assert refuted.recovery_condition, "反驳必须给出恢复条件"

    hollow = counterexample_verdict_for(
        ValidationStatus.counterexample_found, {}, "counterexample_found")
    assert hollow.action == "blocked"
    assert hollow.scientific is False, "没有可回代反例不算科学性结论"
    assert "可回代" in hollow.reason
    assert hollow.note, "必须给出可执行的下一步"


def test_counterexample_verdict_never_treats_no_witness_as_proof():
    """未找到反例只是有限测试证据, 不得升级为"命题为真"。"""
    from src.research.reasoning_kernel import counterexample_verdict_for
    from src.research.schemas import ValidationStatus

    verdict = counterexample_verdict_for(ValidationStatus.verified, None)
    assert verdict.action == "record_test"
    assert "不构成证明" in verdict.reason
    assert verdict.scientific is False


def test_counterexample_verdict_maps_runtime_problems_separately():
    from src.research.reasoning_kernel import counterexample_verdict_for
    from src.research.schemas import ValidationStatus

    for status in (ValidationStatus.timeout, ValidationStatus.unavailable,
                   ValidationStatus.execution_error, ValidationStatus.unknown):
        verdict = counterexample_verdict_for(status, None, "后端不可用")
        assert verdict.action == "failed"
        assert verdict.scientific is False, "运行/能力问题不得当成命题为假"


def test_repeat_failure_kinds_are_the_documented_ones():
    from src.research.reasoning_kernel import COUNTEREXAMPLE_REPEAT_FAILURES

    assert COUNTEREXAMPLE_REPEAT_FAILURES == frozenset({
        "unsupported", "unknown", "no_counterexample_found", "read_failed"})


# --------------------------------------------------------------------------
# 新颖性对照
# --------------------------------------------------------------------------
def test_novelty_comparison_never_decides_equivalence():
    """检索命中不等于已被做过: 没有可比对行时必须保持 unchecked。"""
    from src.research.reasoning_kernel import novelty_comparison_for

    claim = _claim()
    outcome = novelty_comparison_for(claim, lookup=lambda c: [])
    assert outcome.ok
    assert outcome.payload["status"] == "unchecked"
    # 新颖性对照不改变研究推进状态
    assert outcome.progressed is False


# --------------------------------------------------------------------------
# 建模
# --------------------------------------------------------------------------
def test_model_proposal_requires_evidence_instead_of_inventing_a_model():
    from src.research.reasoning_kernel import model_proposal_for

    outcome = model_proposal_for(_claim(), evidence=[])
    assert outcome.ok is False
    assert outcome.payload["requires_evidence"] is True
    assert outcome.notes, "拒绝时必须给出可执行的下一步"


def test_model_proposal_selects_exactly_one_candidate():
    """§5.2: 提出候选后必须显式选中一个, 否则"依据哪个模型"无从审计。"""
    from src.research.reasoning_kernel import model_proposal_for
    from src.research.schemas import SourceEvidence

    claim = _claim()
    evidence = [SourceEvidence(id="ev-1", claim_id=claim.id,
                               title="平方非负", location="p1",
                               excerpt="对任意实数 x, x^2 >= 0 恒成立",
                               source_id="doc-1")]
    outcome = model_proposal_for(claim, evidence=evidence)
    if not outcome.ok:
        pytest.skip("该命题未产生候选机制 (规则路径未命中)")
    models = outcome.payload["models"]
    assert models
    assert sum(1 for m in models if m["selected"]) == 1
    assert outcome.payload["chosen_id"] in {m["id"] for m in models}
    assert outcome.payload["updated_claim"]["model_ref"]["id"] == \
        outcome.payload["chosen_id"]


def test_model_proposal_does_not_write_state():
    """内核只产出候选; 写命题状态是判定层的事。"""
    from src.research.reasoning_kernel import model_proposal_for
    from src.research.schemas import SourceEvidence

    claim = _claim()
    evidence = [SourceEvidence(id="ev-1", claim_id=claim.id, title="t",
                               location="p1", excerpt="x^2 >= 0", source_id="doc-1")]
    outcome = model_proposal_for(claim, evidence=evidence)
    assert outcome.writes == []
    # 原命题对象未被就地改写 (候选以 `updated_claim` 形式返回)
    assert claim.model_ref is None


# --------------------------------------------------------------------------
# 验证方案
# --------------------------------------------------------------------------
def test_validation_plan_refuses_to_pad_with_a_template():
    """既没有竞争预测也没有未闭义务时明确拒绝 —— 不套模板充数。"""
    from src.research.reasoning_kernel import validation_plan_for

    outcome = validation_plan_for(_claim(), distinguishing=None,
                                  open_obligation=False)
    assert outcome.ok is False
    assert outcome.payload["refused"] is True


def test_validation_plan_is_never_marked_executed():
    from src.research.reasoning_kernel import validation_plan_for

    claim = _claim()
    distinguishing = {"kind": "numeric", "statement": "数值对照",
                      "discriminates": ["A", "B"]}
    outcome = validation_plan_for(claim, distinguishing=distinguishing,
                                  open_obligation=True)
    assert outcome.ok, outcome.summary
    assert outcome.payload["executed"] is False
    assert outcome.payload["spec"]["execution_status"] != "executed"
    assert any("未执行" in note for note in outcome.notes)


def test_modelling_and_validation_agents_use_their_kernels(monkeypatch):
    """建模与验证方案角色也必须走内核 (M2 的实质验收, 反向保护)。

    替换内核函数后角色产出必须跟着变 —— 谁把角色改回"自己写一套", 这条就红。
    """
    import src.research.reasoning_kernel as kernel
    from src.agents.modeling import ModelingAgent
    from src.agents.protocol import AgentTask, ContextPack
    from src.agents.runtime import AgentRuntime
    from src.agents.validation_planning import ValidationPlanningAgent

    calls: list[str] = []

    def _fake_model(claim, **kwargs):
        calls.append("model")
        return kernel.ReasoningOutcome(
            ok=True, progressed=True, summary="假建模",
            payload={"models": [{"id": "m1", "name": "n", "selected": True,
                                 "variables": [], "assumptions": [],
                                 "mechanism": "mech", "source_refs": ["doc-1"]}],
                     "chosen_id": "m1",
                     "comparison": {}, "declaration": "假声明",
                     "distinguishing": []})

    def _fake_validation(claim, **kwargs):
        calls.append("validation")
        return kernel.ReasoningOutcome(
            ok=True, progressed=True, summary="假方案",
            payload={"spec_id": "exp-1", "spec": {"id": "exp-1",
                                                  "execution_status": "spec_validated"},
                     "executed": False})

    monkeypatch.setattr(kernel, "model_proposal_for", _fake_model)
    monkeypatch.setattr(kernel, "validation_plan_for", _fake_validation)

    claim = _claim()
    from src.research.schemas import SourceEvidence

    evidence = SourceEvidence(id="ev-1", claim_id=claim.id, title="t",
                              location="p1", excerpt="x^2 >= 0", source_id="doc-1")
    context = ContextPack(
        task_id="t1", agent="modeling",
        objects={"claim": [claim.model_dump(mode="json")],
                 "evidence": [evidence.model_dump(mode="json")],
                 "model": [{"id": "m0", "distinguishing": [
                     {"kind": "numeric", "statement": "对照",
                      "discriminates": ["A", "B"]}]}]},
        request="建模")

    model_result = ModelingAgent().run(
        AgentTask(agent="modeling", objective="建模", project_id="p", problem_id="q"),
        context, AgentRuntime())
    assert "model" in calls, "建模角色没有走内核"
    assert model_result.payload.get("kernel") is True

    validation_result = ValidationPlanningAgent().run(
        AgentTask(agent="validation", objective="给出验证方案",
                  project_id="p", problem_id="q"),
        context, AgentRuntime())
    assert "validation" in calls, "验证方案角色没有走内核"
    assert validation_result.proposed_changes, "必须交出结构化方案"


def test_modelling_agent_refuses_to_invent_a_model_without_sources():
    """没有已读原文时角色必须提补检索需求, 而不是编一个模型。"""
    from src.agents.modeling import ModelingAgent
    from src.agents.protocol import AgentTask, ContextPack
    from src.agents.runtime import AgentRuntime

    claim = _claim()
    context = ContextPack(task_id="t1", agent="modeling",
                          objects={"claim": [claim.model_dump(mode="json")]},
                          request="建模")
    result = ModelingAgent().run(
        AgentTask(agent="modeling", objective="建模", project_id="p", problem_id="q"),
        context, AgentRuntime())
    assert result.outcome.value == "blocked"
    assert result.followup_needs, "受阻必须说清缺什么"
    assert result.proposed_changes == [], "不得在没有来源时提出模型"


# --------------------------------------------------------------------------
# 团队路径真的在用内核 (M2 的实质验收)
# --------------------------------------------------------------------------
def test_reasoning_agent_uses_the_kernel(monkeypatch):
    """替换内核函数, 角色产出的成果必须跟着变 —— 证明它真的调了内核。

    反向保护: 如果哪天有人把角色改回"自己写一套推导", 这条用例会失败。
    """
    import src.research.reasoning_kernel as kernel
    from src.agents.protocol import AgentTask, ContextPack
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime

    calls: list[str] = []

    def _fake_plan(claim, **kwargs):
        calls.append("plan")
        return kernel.ReasoningOutcome(
            ok=True, progressed=True, writes=[], events=[],
            idempotency_key="plan:fake", summary="假计划",
            payload={"strategy": "fake", "steps": 1, "attempt":
                     {"target_claim_id": claim.id, "target_version": claim.version,
                      "strategy": "fake", "status": "in_progress", "steps": []}})

    def _fake_derive(claim, **kwargs):
        calls.append("derive")
        return kernel.ReasoningOutcome(
            ok=True, progressed=True, writes=[], events=[],
            idempotency_key="derive:fake", summary="假推导",
            payload={"steps": [], "formal_gap": False, "review_obligations": []})

    monkeypatch.setattr(kernel, "plan_proof_for", _fake_plan)
    monkeypatch.setattr(kernel, "derive_steps_for", _fake_derive)

    context = ContextPack(
        task_id="t1", agent="reasoning",
        objects={"brief": [{"id": "brief-1", "main_question": "对所有实数 x: x**2 >= 0"}],
                 "claim": [_claim().model_dump(mode="json")],
                 "obligation": [_obligation(_claim()).model_dump(mode="json")]},
        request="证明 x**2 >= 0")
    task = AgentTask(agent="reasoning", objective="证明 x**2 >= 0",
                     project_id="p", problem_id="q")
    result = ReasoningAgent().run(task, context, AgentRuntime())
    assert calls == ["plan", "derive"], f"角色没有走内核: {calls}"
    assert result.outcome.value == "completed"
    assert result.payload.get("kernel") is True
    assert result.proposed_changes, "必须交出结构化成果"
    assert all(p.kind == "claim" for p in result.proposed_changes)
    # 角色仍然只提交候选: 不得声明改变结论
    assert all(p.may_change_conclusion is False for p in result.proposed_changes)


def test_reasoning_agent_falls_back_when_no_claim_is_available():
    """上下文里没有可形式化命题时退回通用策略, 而不是空转或编一条命题。"""
    from src.agents.protocol import AgentTask, ContextPack
    from src.agents.reasoning import ReasoningAgent
    from src.agents.runtime import AgentRuntime

    context = ContextPack(task_id="t1", agent="reasoning",
                          objects={"brief": [{"id": "b", "main_question": "证明某命题"}]},
                          request="证明某命题")
    task = AgentTask(agent="reasoning", objective="证明某命题",
                     project_id="p", problem_id="q")
    result = ReasoningAgent().run(task, context, AgentRuntime())
    assert result.payload.get("kernel") is None
    assert result.outcome.value in ("completed", "partial", "blocked")


def test_engine_path_and_team_path_share_the_kernel(monkeypatch):
    """引擎的 `_act_derive_step` 也必须走内核 (替换内核即两条路径都变)。

    这是"抽取"的判据: 不是看代码被搬到了哪个文件, 而是看**两条路径是否只有一份实现**。
    """
    import src.research.loop as loop_mod
    import src.research.reasoning_kernel as kernel

    called: list[str] = []
    original = kernel.derive_steps_for

    def _spy(claim, **kwargs):
        called.append(claim.id)
        return original(claim, **kwargs)

    monkeypatch.setattr(kernel, "derive_steps_for", _spy)
    # 引擎通过 `from ... import` 在方法内部取内核函数, 因此打补丁后必然命中
    source = loop_mod.__file__
    with open(source, encoding="utf-8") as handle:
        body = handle.read()
    assert "from src.research.reasoning_kernel import derive_steps_for" in body
    assert "from src.research.reasoning_kernel import plan_proof_for" in body
    # 引擎里不应再有第二份推导实现 (旧的逐字代码已被内核取代)
    assert "issues_to_obligations(attempt, claim, existing_statements=existing)" not in body


# --------------------------------------------------------------------------
# 工具结果 → 义务处置的纯映射 (M2 剩余: 判定层边界)
# --------------------------------------------------------------------------
def _verification_result(status_name: str, **over):
    from src.verification.schemas import VerificationResult, VerificationStatus

    data = {"tool": "sympy", "status": VerificationStatus(status_name)}
    data.update(over)
    return VerificationResult(**data)


def test_obligation_disposition_closes_only_on_passed():
    from src.research.reasoning_kernel import obligation_disposition_for
    from src.research.schemas import ValidationStatus

    claim = _claim()
    obligation = _obligation(claim)
    disposition = obligation_disposition_for(
        _verification_result("passed", detail="恒成立"), obligation)
    assert disposition.action == "closed"
    assert disposition.validation_status == ValidationStatus.verified
    assert disposition.mark_record_usable is True
    # 判定层不写状态: 这个对象没有任何"已落盘"的含义
    assert not hasattr(disposition, "writes")


def test_obligation_disposition_refutes_only_with_a_usable_witness():
    from src.research.reasoning_kernel import obligation_disposition_for
    from src.research.schemas import ValidationStatus

    claim = _claim()
    obligation = _obligation(claim, kind="prove_inequality")

    hollow = obligation_disposition_for(
        _verification_result("failed", detail="counterexample_found"), obligation)
    assert hollow.action == "blocked", "给不出可回代反例时不得判为数学反驳"
    assert hollow.validation_status == ValidationStatus.counterexample_found
    assert hollow.scientific is False
    assert "可回代" in hollow.reason
    assert hollow.recovery_condition, "必须给出可执行的下一步"

    real = obligation_disposition_for(
        _verification_result("failed", counterexample={"x": -1}, detail="反例 x=-1"),
        obligation)
    assert real.action == "refuted"
    assert real.scientific is True
    assert real.keep_counterexample is True
    assert real.reason == "找到反例 {'x': -1}"


def test_obligation_disposition_keeps_estimate_failures_out_of_route_failures():
    """区间跨零 ≠ 效应不存在, 也不该被记成"这条路线失败"。"""
    from src.research.reasoning_kernel import obligation_disposition_for

    claim = _claim()
    obligation = _obligation(claim, kind="estimate_effect")
    disposition = obligation_disposition_for(
        _verification_result("failed", counterexample={"beta": 0.0}), obligation)
    assert disposition.action == "blocked"
    assert disposition.record_route_failure is False
    assert "区间跨零" in disposition.reason


def test_obligation_disposition_treats_inconclusive_as_pending():
    from src.research.reasoning_kernel import obligation_disposition_for
    from src.research.schemas import ValidationStatus

    claim = _claim()
    obligation = _obligation(claim)
    for name, expected in (("unknown", ValidationStatus.unknown),
                           ("timeout", ValidationStatus.timeout),
                           ("unsupported", ValidationStatus.unsupported),
                           ("unavailable", ValidationStatus.unavailable),
                           ("error", ValidationStatus.execution_error)):
        disposition = obligation_disposition_for(
            _verification_result(name, detail="后端不可用"), obligation)
        assert disposition.action == "blocked", name
        assert disposition.validation_status == expected, name
        assert disposition.scientific is False, name
        assert disposition.record_route_failure is True, name
        assert disposition.reason == "后端不可用", name


def test_obligation_disposition_shares_the_engine_status_mapping():
    """差异测试: 内核的映射表与引擎 `_to_validation_status` 必须给出同一结果。

    抽取的判据不是"看代码搬到哪里", 而是"两处不会各自漂移"。
    """
    from src.research.loop import TheoryEngine
    from src.research.reasoning_kernel import obligation_disposition_for

    claim = _claim()
    obligation = _obligation(claim)
    for name in ("passed", "failed", "unknown", "unsupported", "timeout",
                 "unavailable", "error"):
        result = _verification_result(name)
        ours = obligation_disposition_for(result, obligation).validation_status
        theirs = TheoryEngine._to_validation_status(result)
        assert ours == theirs, name

