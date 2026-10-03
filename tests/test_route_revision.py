from __future__ import annotations

"""P1-2 失败驱动的研究路线修订。

计划书验收: 相互冲突的资料、一个模型反例、一次验证 `unknown` 三者引出的下一路线不同,
且研究日志能回答"为何现在做、做后学到什么、为什么停"。
"""

from src.research.coordinator import decide, rank_actions
from src.research.routes import NEXT_ACTIONS, RouteManager, classify_failure
from src.research.schemas import ActionType, FailureKind


def _manager() -> RouteManager:
    manager = RouteManager()
    manager.ensure_route("clm-1", goal="检验可分性结论")
    return manager


def test_five_failure_kinds_map_to_distinct_next_actions():
    mapping = {
        "no_retrieval_hit": FailureKind.source_missing,
        "counterexample": FailureKind.model_refuted,
        "encoding_mismatch": FailureKind.formalization_failed,
        "unknown": FailureKind.tool_unknown,
        "budget_exhausted": FailureKind.budget_exhausted,
    }
    kinds = {classify_failure(raw) for raw in mapping}
    assert kinds == set(FailureKind), kinds
    first_actions = {kind: NEXT_ACTIONS[kind][0] for kind in FailureKind}
    # 三类实质不同的失败必须引向不同动作 (预算/工具类可以都指向换路或收尾)
    assert first_actions[FailureKind.source_missing] is ActionType.retrieve_targeted
    assert first_actions[FailureKind.model_refuted] is ActionType.propose_model
    assert first_actions[FailureKind.formalization_failed] is ActionType.revise_hypothesis
    assert first_actions[FailureKind.tool_unknown] is ActionType.switch_strategy
    assert first_actions[FailureKind.budget_exhausted] is ActionType.deliver_partial


def test_failure_record_carries_type_and_route_closes_the_loop():
    manager = _manager()
    route = manager.active_route("clm-1")
    route.target_gap = "missing_condition"
    route.expected_observation = "换路线后应出现可判定的条件差异"
    manager.record_failure("clm-1", "no_retrieval_hit", "在所检索范围内无命中",
                           scientific=False, recovery_condition="扩大术语/范围后重试")
    failure = manager.failures[-1]
    assert failure.failure_kind is FailureKind.source_missing
    assert failure.next_actions == [a.value for a in NEXT_ACTIONS[FailureKind.source_missing]]
    # 路线闭环: 实际结果与下一判断都被写回
    assert route.actual_outcome.startswith("在所检索范围内无命中")
    assert route.failure_kind is FailureKind.source_missing
    assert "扩大术语" in route.next_judgement
    assert manager.next_actions_for("clm-1") == failure.next_actions


def test_tool_only_change_is_refused_with_stop_reason():
    manager = _manager()
    manager.record_failure("clm-1", "unknown", "求解器返回 unknown", scientific=False,
                           tool="sympy", recovery_condition="换后端后重试")
    route, message = manager.revise("clm-1", FailureKind.tool_unknown, reason="换个后端再试")
    assert route is None
    assert "没有新条件、新子命题或不同机制" in message
    assert manager.failures[-1].failure_kind is FailureKind.tool_unknown or \
        manager.failures[-1].kind == "no_progress"
    # 只换同一后端的名字同样不算修订
    route2, _ = manager.revise("clm-1", FailureKind.tool_unknown, tool_change="sympy")
    assert route2 is None


def test_substantive_change_creates_new_route_with_closed_loop_fields():
    manager = _manager()
    route, message = manager.revise("clm-1", FailureKind.model_refuted,
                                    new_mechanism="信道均衡路径")
    assert route is not None, message
    assert route.substantive_change == "不同机制: 信道均衡路径"
    assert route.failure_kind is FailureKind.model_refuted
    assert route.expected_observation and route.next_judgement
    assert route.strategy == "counterexample_search"
    assert len(manager.routes) == 2


def test_untried_backend_counts_as_substantive_for_tool_unknown():
    manager = _manager()
    manager.record_failure("clm-1", "unknown", "sympy 无法处理", scientific=False,
                           tool="sympy")
    route, message = manager.revise("clm-1", FailureKind.tool_unknown, tool_change="z3")
    assert route is not None, message
    assert route.substantive_change == "换后端: z3"


def test_route_budget_exhaustion_stops_with_reason():
    manager = _manager()
    for index in range(4):
        manager.revise("clm-1", FailureKind.model_refuted,
                       new_mechanism=f"机制 {index}")
    route, message = manager.revise("clm-1", FailureKind.model_refuted,
                                    new_mechanism="机制 5", max_routes=5)
    assert route is None
    assert "已用尽" in message
    assert manager.failures[-1].failure_kind is FailureKind.budget_exhausted


def test_failure_kind_boosts_matching_action_with_explanation():
    state = {
        "budget_remaining": 6,
        "retrieval_available": True,
        "gaps": [{"gap_type": "missing_evidence", "object_id": "clm-1",
                  "resolving_actions": ["retrieve_targeted"]}],
        "primary_gap": {"gap_type": "missing_evidence", "object_id": "clm-1",
                        "resolving_actions": ["retrieve_targeted"]},
        "unresolved_claims": [{"id": "clm-1", "version": 1}],
        "questions": [{"id": "clm-1", "version": 1}],
        "model_gaps": [{"id": "gap-m1", "object_id": "clm-1"}],
        "retrieval_requests": [{"object_id": "clm-1", "query": "信道 可分性"}],
        "dismissed_actions": [],
        "failure_next_actions": ["propose_model"],
        "failure_judgement": "model_refuted: 提出不同机制或新子命题后重试",
    }
    ranked = rank_actions(state)
    order = [action for _, action, _ in ranked]
    assert order[0] is ActionType.propose_model, order[:3]
    assert "按失败类型选择下一动作" in ranked[0][2]
    assert ActionType.retrieve_targeted in order


def test_expensive_action_reason_explains_gain_and_cost_cap():
    state = {"budget_remaining": 2, "gaps": [], "primary_gap": {},
             "unresolved_claims": [{"id": "clm-1", "version": 1}],
             "dismissed_actions": []}
    action = decide(state)
    assert action.estimated_cost >= 1
    if action.estimated_cost >= 3 or state["budget_remaining"] <= action.estimated_cost:
        assert "预期信息增益" in action.reason
        assert "成本上限" in action.reason
