from __future__ import annotations

"""调度与执行链路的失败用例 (计划书 §5.3 / §7.2)。

这些用例针对**集成层**缺陷: 单个动作的实现正确, 但协调者从不选中它,
或者动作拿到的对象类型不对 —— 早期实现因此让"推导步骤+反方审查"和
"LLM 推导"在真实运行中从未执行过 (只有直接调用处理器才跑得通)。
"""

from src.research.schemas import (
    ClaimQuestion,
    ResearchSpec,
    StudyDesign,
    StudyPlan,
)


def _causal_spec(pid: str) -> ResearchSpec:
    question = ClaimQuestion(
        statement="技术A提高了行业产出", category="causal", claim_type="causal",
        study=StudyPlan(design=StudyDesign.observational, treatment="技术A",
                        outcome="行业产出"),
    )
    return ResearchSpec(project_id=pid, questions=[question], confirmed=True)


def test_runtime_runs_review_before_verification(tmp_path):
    """真实运行必须自动走到 derive_step, 并产出反方审查义务。

    只测处理器是不够的: 协调者过去从不选中 derive_step (它不在任何缺口的
    resolving 动作里), 于是八项审查在真实研究中从未执行。
    """
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.store import KIND_ATTEMPT, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = _causal_spec("sched1")
    store = ResearchStore("sched1", db_path=tmp_path / "sched1.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=20))
    result = engine.run()

    actions = [d["action"] for d in result.decisions]
    assert "derive_step" in actions, f"协调者必须选中推导+审查动作: {actions}"
    assert actions.index("derive_step") < actions.index("check_step"), \
        "审查应在验证之前发生 (先补条件再判定)"
    attempts = store.list_latest(KIND_ATTEMPT)
    assert attempts, "推导尝试必须落盘"
    assert all(a["status"] == "complete" for a in attempts), \
        "一次完整的推导尝试必须标记为 complete (否则交付门槛认为正文引用了无记录的证明)"

    kinds = {o.kind for o in result.snapshot.obligations}
    assert any(k.startswith("adversarial_") for k in kinds), \
        "反方审查清单必须产出义务, 而不是只写事件"
    store.close()


def test_runtime_derive_accepts_obligation_target(tmp_path):
    """derive_step 的目标是义务时, 必须回查到所属命题而不是静默失败。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ActionType, ResearchAction
    from src.research.store import KIND_ATTEMPT, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = _causal_spec("sched2")
    store = ResearchStore("sched2", db_path=tmp_path / "sched2.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    assert engine.bootstrap()
    obligation = engine._obligations()[0]

    ok = engine._act_derive_step(ResearchAction(action_type=ActionType.derive_step,
                                               object_id=obligation.id))
    assert ok is True, "以义务 id 派发时必须追溯到命题"
    assert store.list_latest(KIND_ATTEMPT), "应写入推导尝试"
    assert not any("无法从对象" in n for n in engine._notes)
    store.close()


def test_stuck_obligation_promotes_information_actions():
    """义务因能力/数据受阻时, 产生新信息的动作必须获得与 check_step 同级的优先级。

    生产链路上还会由执行账本去重拦住重复的 check_step; 这里只保证排序本身
    不再把同一工具排在唯一首选位置 (否则会一直空转)。
    """
    from src.research.coordinator import rank_actions
    from src.research.schemas import ActionType, ValidationStatus

    state = {
        "primary_gap": {"gap_type": "open_obligation", "object_id": "obl-1",
                        "claim_id": "clm-1", "resolving_actions": ["check_step",
                                                                   "plan_proof"]},
        "open_obligations": [{"id": "obl-1", "claim_id": "clm-1",
                              # model_dump 出来的枚举就是枚举成员, 两种写法都必须识别
                              "validation_status": ValidationStatus.unsupported,
                              "status": "blocked", "kind": "estimate_effect"}],
        "reviewed_claims": ["clm-1"],
        "gaps": [{"gap_type": "open_obligation"}],
        "dismissed_actions": [],
        "budget_remaining": 10,
    }
    ranked = [(a.value, why) for _, a, why in rank_actions(
        state, [ActionType.check_step, ActionType.derive_step, ActionType.propose_model,
                ActionType.switch_strategy, ActionType.deliver_partial])]
    info = [name for name, _ in ranked if name in ("derive_step", "propose_model",
                                                  "switch_strategy")]
    assert info, ranked
    # 受阻义务必须让"信息动作"获得专门的优先级理由, 而不是落在兜底档
    assert any("受阻" in why or "信息" in why for name, why in ranked if name in info), ranked


def test_unreviewed_obligation_prefers_derive_step():
    """尚未审查过的义务: 先用推导步骤补条件, 而不是直接跑验证工具。"""
    from src.research.coordinator import rank_actions
    from src.research.schemas import ActionType

    state = {
        "primary_gap": {"gap_type": "open_obligation", "object_id": "obl-1",
                        "claim_id": "clm-1", "resolving_actions": ["derive_step",
                                                                   "check_step"]},
        "open_obligations": [{"id": "obl-1", "claim_id": "clm-1",
                              "validation_status": "unchecked",
                              "status": "open", "kind": "prove_inequality"}],
        "reviewed_claims": [],
        "gaps": [{"gap_type": "open_obligation"}],
        "dismissed_actions": [],
        "budget_remaining": 10,
    }
    ranked = [a.value for _, a, _ in rank_actions(
        state, [ActionType.check_step, ActionType.derive_step])]
    assert ranked[0] == "derive_step", ranked


def test_accepted_data_causal_claim_still_supported(tmp_path):
    """反方审查不得阻断已经满足既有门槛的结论 (审查意见是 required=False)。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import StudyDesign, StudyPlan
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    rows = []
    for g, t, y in (("T", "pre", 10.0), ("T", "post", 20.0),
                    ("C", "pre", 10.0), ("C", "post", 12.0)):
        for _ in range(6):
            rows.append({"g": g, "t": t, "y": y})
    study = StudyPlan(
        design=StudyDesign.did, treatment="技术A", outcome="行业产出",
        population="制造业", region="华东", period="2015-2023",
        counterfactual="未采用技术A", confounders=["企业规模"],
        confounder_handling="地区固定效应",
        identification_assumptions=["平行趋势", "SUTVA"],
        design_feasibility="处理组/对照组各 4 期观测",
        measurement_notes="不变价增加值",
        missing_data_handling="缺失率 <1%, 近似 MAR",
        error_structure="按地区聚类稳健标准误",
        data_source_kind="public", group_col="g", time_col="t", outcome_col="y",
        treated_label="T", control_label="C", pre_label="pre", post_label="post",
        rows=rows,
    )
    question = ClaimQuestion(statement="技术A提高了行业产出", category="causal",
                             claim_type="causal", study=study)
    spec = ResearchSpec(project_id="advok", questions=[question], confirmed=True)
    store = ResearchStore("advok", db_path=tmp_path / "advok.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=40))
    result = engine.run()

    claim = result.snapshot.claims[0]
    assert claim.status.value == "supported", (claim.status, result.gate.reasons)
    assert result.gate.passed, (result.gate.reasons, result.gate.unresolved)
    # 声明完整的因果命题: 八项审查应当都能给出结论, 并且没有任何一项阻断交付
    adversarial = [o for o in result.snapshot.obligations
                   if o.kind.startswith("adversarial_")]
    for obligation in adversarial:
        assert obligation.required is False, "审查意见不得阻断已满足门槛的结论"
        assert obligation.status.value != "closed", \
            "没有工具/人工确认时审查义务不得被自动关闭"
    store.close()


def test_weak_causal_claim_records_adversarial_obligations(tmp_path):
    """信息缺失的因果命题必须留下审查义务 (且不阻断性升级为因果结论)。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = _causal_spec("vis2")
    store = ResearchStore("vis2", db_path=tmp_path / "vis2.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=20))
    result = engine.run()
    adversarial = [o for o in result.snapshot.obligations
                   if o.kind.startswith("adversarial_")]
    assert len(adversarial) >= 3, [o.kind for o in adversarial]
    for obligation in adversarial:
        assert obligation.required is False
        assert obligation.acceptance_method == "informal_review"
        assert obligation.status.value != "closed"
    # 结论不得被升级
    assert all(c.status.value != "supported" for c in result.snapshot.claims)
    store.close()


def test_review_obligations_are_visible_in_delivery(tmp_path):
    """审查意见必须出现在交付快照中, 供工作台与正文呈现 (计划书 §9.5)。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = _causal_spec("vis1")
    store = ResearchStore("vis1", db_path=tmp_path / "vis1.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=20))
    result = engine.run()
    for obligation in result.snapshot.obligations:
        if obligation.kind.startswith("adversarial_"):
            assert obligation.statement.startswith("反方审查[")
            assert obligation.detail
    store.close()
