from __future__ import annotations

"""统一运行时 (M1) 的行为用例: 有界工具循环、权限闸门、预算、取消与失败收敛。

这些用例固定的是"**机制**必须实际生效", 不是提示词约定:
- 未授权的能力即使模型请求也返回 blocked;
- 角色不得提交越界对象种类 (超界即降级为 partial);
- 预算用尽后不再调用模型;
- 取消在任意一次调用之间被观测到;
- 任何异常收敛为 failed 结果并带原因, 不让 run 崩掉。
"""

import json
from types import SimpleNamespace

import pytest

from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    ContextPack,
    TaskBudget,
    UsageRecord,
)
from src.agents.runtime import (
    AgentRuntime,
    BudgetedLLM,
    CancelToken,
    InProcessExecutor,
    TaskCancelled,
    ToolLoop,
    ToolSpec,
    budget_from_env,
    downgrade_only_gate,
)


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------
class _EchoAgent:
    """正常完成的角色 (只提交本职责内的候选)。"""

    def __init__(self, role: str, kind: str = ""):
        self.role = role
        self._kind = kind

    def run(self, task, context, runtime):
        changes = []
        if self._kind:
            changes.append(ChangeProposal(kind=self._kind, payload={"note": "candidate"}))
        return AgentResult(task_id=task.task_id, agent=self.role,
                           outcome="completed", summary=f"{self.role} 完成",
                           proposed_changes=changes)


class _OverreachAgent:
    """越界提交: 写作角色提交 claim 变更。"""

    role = "writing"

    def run(self, task, context, runtime):
        return AgentResult(task_id=task.task_id, agent="writing", outcome="completed",
                           summary="试图直接改命题",
                           proposed_changes=[ChangeProposal(kind="claim",
                                                            payload={"statement": "x"})])


class _BoomAgent:
    role = "reasoning"

    def run(self, task, context, runtime):
        raise ValueError("推导崩溃")


class _BlockedAgent:
    role = "reasoning"

    def run(self, task, context, runtime):
        from src.agents.protocol import ResearchNeed

        return AgentResult(task_id=task.task_id, agent="reasoning", outcome="blocked",
                           failure_reason="引理 L 缺适用条件",
                           followup_needs=[ResearchNeed(kind="clause",
                                                        statement="查引理 L 条件")])


class _ScriptedLLM:
    """按脚本返回带/不带工具调用的回合。"""

    def __init__(self, rounds: list[dict]):
        self.rounds = rounds
        self.calls: list[list] = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages, *args, **kwargs):
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.rounds) - 1)
        round_spec = self.rounds[index]
        return SimpleNamespace(content=round_spec.get("content", ""),
                               tool_calls=list(round_spec.get("tool_calls", [])),
                               response_metadata={}, usage_metadata=None)


def _task(agent: str = "evidence", **over) -> AgentTask:
    data = {"agent": agent, "objective": "完成子任务", "project_id": "p",
            "problem_id": "q"}
    data.update(over)
    return AgentTask(**data)


# --------------------------------------------------------------------------
# 正常完成
# --------------------------------------------------------------------------
def test_executor_returns_completed_result_and_emits_events():
    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_EchoAgent("evidence", kind="evidence"))
    task = _task()
    result = executor.execute(task, ContextPack(task_id=task.task_id, agent="evidence"))

    assert result.outcome.value == "completed"
    assert result.summary == "evidence 完成"
    kinds = [e["kind"] for e in runtime.events]
    assert "capability_granted" in kinds
    assert "task_started" in kinds
    assert "task_result" in kinds
    assert task.capability_grant_id


def test_executor_reports_missing_backend_instead_of_silently_succeeding():
    executor = InProcessExecutor(AgentRuntime())
    task = _task("figures")
    result = executor.execute(task)
    assert result.outcome.value == "failed"
    assert "figures" in result.failure_reason
    assert executor.registered_roles() == ()


# --------------------------------------------------------------------------
# 权限: 越界提交被降级
# --------------------------------------------------------------------------
def test_overreaching_proposal_downgrades_result_to_partial():
    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_OverreachAgent())
    task = _task("writing")
    result = executor.execute(task, ContextPack(task_id=task.task_id, agent="writing"))

    assert result.outcome.value == "partial"     # 不是 completed
    assert "不符合契约" in result.summary
    rejected = [e for e in runtime.events if e["kind"] == "result_rejected"]
    assert rejected and any("claim" in p for p in rejected[0]["payload"]["problems"])


def test_each_role_can_only_write_its_own_object_kinds():
    from src.agents.protocol import WRITABLE_KINDS

    assert "evidence" in WRITABLE_KINDS["evidence"]
    assert "claim" not in WRITABLE_KINDS["writing"]
    assert "claim" in WRITABLE_KINDS["reasoning"]
    assert "verification" not in WRITABLE_KINDS["evidence"]


def test_review_gate_only_allows_downgrade():
    lower = ChangeProposal(kind="claim", payload={"current_level": "converging",
                                                  "target_level": "single_source"})
    higher = ChangeProposal(kind="claim", payload={"current_level": "single_source",
                                                   "target_level": "converging"})
    same = ChangeProposal(kind="claim", payload={"current_level": "converging",
                                                 "target_level": "converging"})
    assert downgrade_only_gate("review", lower) == (True, "")
    ok, reason = downgrade_only_gate("review", higher)
    assert ok is False and "提升" in reason
    assert downgrade_only_gate("review", same)[0] is True
    # 非审阅角色不受此闸门限制 (它们的越界由对象种类闸门拦)
    assert downgrade_only_gate("reasoning", higher)[0] is True


def test_rejected_candidate_never_reaches_authoritative_storage():
    """越权候选只能进审计记录, **不得**被登记为权威对象 (§3.2 G07)。

    这是审计探针 `audit_probes.py::rejected_candidate_still_persisted` 的正式回归:
    修复前, 一个 evidence 角色提交的 claim 被判违规、结果降级为 partial, **但候选
    仍留在 `proposed_changes` 里**, 于是投影层照样登记, 库里出现 `status=supported`。
    现在越权候选被移入 `rejected_changes`, 合法候选照常登记。
    """
    from src.agents.protocol import grant_for
    from src.research.projection import TeamProjection
    from src.research.store import ResearchStore

    store = ResearchStore("g07", db_path=":memory:")
    try:
        task = AgentTask(agent="evidence", objective="read only",
                         source_policy="user_kb")
        grant = grant_for(task)
        result = AgentResult(
            task_id=task.task_id, agent="evidence", outcome="completed",
            summary="候选",
            proposed_changes=[
                # 越权: evidence 角色不得提交 claim
                ChangeProposal(kind="claim", object_id="bad-claim",
                               payload={"statement": "未核验断言", "status": "supported"}),
                # 合规: evidence 角色可以提交来源
                ChangeProposal(kind="source", object_id="ok-source",
                               payload={"title": "合法来源"}),
            ])
        runtime = AgentRuntime()
        finalized = runtime.finalize(task, result, grant)
        TeamProjection(store).register(task, finalized)

        # 结构性保证: 越权候选已不在待登记列表里
        assert [p.object_id for p in finalized.proposed_changes] == ["ok-source"]
        # 审计留痕: 被拒候选与理由可见
        assert [item["object_id"] for item in finalized.rejected_changes] == ["bad-claim"]
        assert "不得提交 claim" in finalized.rejected_changes[0]["reason"]
        # 权威库里没有越权对象, 合规对象正常落库
        assert store.get("claim", "bad-claim") is None
        # `source` 归入 evidence 存储族 (KIND_MAP), 但候选种类必须被保留下来
        stored = store.get("evidence", "ok-source")
        assert stored is not None
        assert stored["_candidate_kind"] == "source"
        # 结果**仍然降级**: 不能因为违规部分被隔离就把这次完成显示成干净的
        assert finalized.outcome.value == "partial"
        assert "不符合契约" in finalized.summary
    finally:
        store.close()


def test_candidate_kind_survives_the_evidence_family_mapping():
    """source / case / dataset 归同一存储族, 但各自种类不得丢失 (§6.1)。"""
    from src.agents.protocol import grant_for
    from src.research.projection import TeamProjection
    from src.research.store import ResearchStore

    store = ResearchStore("kinds", db_path=":memory:")
    try:
        task = AgentTask(agent="evidence", objective="收集材料",
                         source_policy="user_kb")
        grant = grant_for(task)
        result = AgentResult(
            task_id=task.task_id, agent="evidence", outcome="completed", summary="c",
            proposed_changes=[
                ChangeProposal(kind="source", object_id="s1", payload={"title": "来源"}),
                ChangeProposal(kind="case", object_id="c1", payload={"title": "案例"}),
                ChangeProposal(kind="dataset", object_id="d1", payload={"title": "数据"}),
            ])
        finalized = AgentRuntime().finalize(task, result, grant)
        TeamProjection(store).register(task, finalized)
        kinds = {row: store.get("evidence", row)["_candidate_kind"]
                 for row in ("s1", "c1", "d1")}
        assert kinds == {"s1": "source", "c1": "case", "d1": "dataset"}
    finally:
        store.close()


def test_review_result_cannot_claim_conclusion_change():
    """审阅提交 may_change_conclusion=True 的候选 -> 违约并降级。"""

    class _ReviewEscalating:
        role = "review"

        def run(self, task, context, runtime):
            return AgentResult(task_id=task.task_id, agent="review", outcome="completed",
                               summary="我认为这条结论更强",
                               proposed_changes=[ChangeProposal(
                                   kind="review_issue", may_change_conclusion=True,
                                   payload={"summary": "x"})])

    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_ReviewEscalating())
    task = _task("review")
    result = executor.execute(task, ContextPack(task_id=task.task_id, agent="review"))
    assert result.outcome.value == "partial"
    assert "降级建议" in result.summary


def test_empty_completion_is_rejected():
    class _Empty:
        role = "modeling"

        def run(self, task, context, runtime):
            return AgentResult(task_id=task.task_id, agent="modeling", outcome="completed")

    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_Empty())
    result = executor.execute(_task("modeling"), ContextPack())
    assert result.outcome.value == "partial"
    assert "没有任何成果" in result.summary


# --------------------------------------------------------------------------
# 工具循环与能力检查
# --------------------------------------------------------------------------
def _observation_count(loop: ToolLoop, status: str) -> int:
    return sum(1 for o in loop.observations if o.status == status)


def test_tool_loop_executes_tools_and_feeds_results_back():
    seen_queries: list[str] = []

    def search(q: str, limit: int = 2):
        seen_queries.append(q)
        return [{"title": "T1"}, {"title": "T2"}]

    loop = ToolLoop([ToolSpec(name="search", fn=search, read_scope="tools:search",
                              evidential=True)], max_rounds=3)
    llm = _ScriptedLLM([
        {"tool_calls": [{"name": "search", "args": {"q": "2-design"}, "id": "c1"}]},
        {"content": "检索完成"},
    ])
    runtime = AgentRuntime()
    # 联网检索是**需要用户授权**的能力 (G09): 任务必须显式声明允许外部取数的资料
    # 策略, 才会拿到 `tools:search`。此前不声明也能检索 —— 那是越权。
    grant = runtime.issue_grant(_task(source_policy="autonomous"))
    text, observations = loop.run(llm, [], grant=grant, usage=UsageRecord())

    assert text == "检索完成"
    assert seen_queries == ["2-design"]
    assert len(observations) == 1 and observations[0].ok
    assert observations[0].evidential is True
    # 工具结果确实回灌进了对话 (而不是被丢弃)
    fed = [m for m in llm.calls[-1] if getattr(m, "type", "") == "tool"]
    assert fed and "T1" in str(fed[0].content)


def test_search_capability_requires_declared_source_policy():
    """未声明资料策略的任务**不得**获得联网检索能力 (G09 的回归点)。"""
    runtime = AgentRuntime()
    declared_none = runtime.issue_grant(_task())
    assert "tools:search" not in declared_none.read_scopes
    assert declared_none.allows_read("sources") is True   # 本地来源仍可读
    local_only = runtime.issue_grant(_task(source_policy="user_kb"))
    assert "tools:search" not in local_only.read_scopes
    for policy in ("autonomous", "both"):
        asserted = runtime.issue_grant(_task(source_policy=policy))
        assert "tools:search" in asserted.read_scopes, policy


def test_tool_loop_blocks_unauthorized_capability():
    loop = ToolLoop([ToolSpec(name="write_claim", fn=lambda: {"ok": True},
                              capability="propose_claim")], max_rounds=2)
    llm = _ScriptedLLM([
        {"tool_calls": [{"name": "write_claim", "args": {}, "id": "c1"}]},
    ])
    runtime = AgentRuntime()
    # evidence 角色的令牌不含 propose_claim
    grant = runtime.issue_grant(_task("evidence"))
    _, observations = loop.run(llm, [], grant=grant, usage=UsageRecord())
    assert _observation_count(loop, "blocked") == 1
    assert "未授权能力" in observations[0].error


def test_tool_loop_blocks_unauthorized_read_scope():
    loop = ToolLoop([ToolSpec(name="read_data", fn=list, read_scope="tools:readonly_data")],
                    max_rounds=2)
    llm = _ScriptedLLM([{"tool_calls": [{"name": "read_data", "args": {}, "id": "c1"}]}])
    runtime = AgentRuntime()
    # writing 角色没有只读数据范围
    grant = runtime.issue_grant(_task("writing"))
    _, observations = loop.run(llm, [], grant=grant, usage=UsageRecord())
    assert observations[0].status == "blocked"
    assert "读范围" in observations[0].error


def test_tool_loop_reports_unknown_tool_and_tool_exception_distinctly():
    def broken():
        raise RuntimeError("后端 500")

    loop = ToolLoop([ToolSpec(name="broken", fn=broken)], max_rounds=2)
    llm = _ScriptedLLM([{"tool_calls": [
        {"name": "broken", "args": {}, "id": "c1"},
        {"name": "ghost", "args": {}, "id": "c2"},
    ]}])
    runtime = AgentRuntime()
    grant = runtime.issue_grant(_task())
    _, observations = loop.run(llm, [], grant=grant, usage=UsageRecord())
    by_name = {o.name: o for o in observations}
    assert by_name["broken"].status == "tool_error"
    assert "后端 500" in by_name["broken"].error
    assert by_name["ghost"].status == "tool_error"
    assert "不存在于本任务" in by_name["ghost"].error


def test_tool_loop_stops_at_max_rounds():
    def ping():
        return "pong"

    loop = ToolLoop([ToolSpec(name="ping", fn=ping)], max_rounds=2)
    # 模型永远请求工具: 必须被轮次上限截断, 不能无限跑
    llm = _ScriptedLLM([{"tool_calls": [{"name": "ping", "args": {}, "id": "c1"}]}])
    runtime = AgentRuntime()
    grant = runtime.issue_grant(_task())
    loop.run(llm, [], grant=grant, usage=UsageRecord())
    assert len(llm.calls) <= 2
    assert len(loop.observations) >= 1


def test_invalid_tool_arguments_are_not_crashes():
    loop = ToolLoop([ToolSpec(name="search", fn=lambda q: [])], max_rounds=2)
    llm = _ScriptedLLM([{"tool_calls": [{"name": "search", "args": ["not", "a", "dict"],
                                         "id": "c1"}]}])
    runtime = AgentRuntime()
    _, observations = loop.run(llm, [], grant=runtime.issue_grant(_task()),
                               usage=UsageRecord())
    assert observations[0].status == "invalid_arguments"


def test_tool_spec_accepts_structured_tool_via_func():
    from langchain_core.tools import StructuredTool

    def real(q: str) -> str:
        return f"hit:{q}"

    wrapper = StructuredTool.from_function(func=real, name="search_all_sources",
                                           description="search")
    spec = ToolSpec(name="search_all_sources", fn=wrapper)
    assert spec.fn is real            # 通过 .func 取回原始实现
    assert spec.fn(q="x") == "hit:x"


def test_tool_spec_rejects_unregistered_capability():
    with pytest.raises(ValueError):
        ToolSpec(name="x", fn=lambda: 1, capability="write_state_directly")


# --------------------------------------------------------------------------
# 预算
# --------------------------------------------------------------------------
def test_budgeted_llm_accounts_usage_and_stops_at_budget():
    class _UsageLLM:
        model_name = "deepseek-v4-flash"

        def __init__(self):
            self.n = 0

        def invoke(self, messages):
            self.n += 1
            return SimpleNamespace(content="ok", response_metadata={},
                                   usage_metadata={"input_tokens": 100,
                                                   "output_tokens": 50,
                                                   "total_tokens": 150})

    usage = UsageRecord()
    budget = TaskBudget(max_llm_calls=2).reserve()
    llm = BudgetedLLM(_UsageLLM(), usage, budget)
    llm.invoke([])
    llm.invoke([])
    assert usage.llm_calls == 2
    assert usage.total_tokens == 300
    assert usage.cost_usd and usage.cost_usd > 0
    assert "deepseek-v4-flash" in usage.models
    with pytest.raises(TaskCancelled):
        llm.invoke([])                # 第 3 次被预算拦下


def test_budgeted_llm_marks_unknown_usage_instead_of_zero():
    class _NoUsageLLM:
        model_name = "some-gateway-model"

        def invoke(self, messages):
            return SimpleNamespace(content="ok", response_metadata={},
                                   usage_metadata=None)

    usage = UsageRecord()
    llm = BudgetedLLM(_NoUsageLLM(), usage, TaskBudget())
    llm.invoke([])
    assert usage.llm_calls == 1
    assert usage.cost_usd is None
    assert "llm_usage" in usage.unknown_parts


def test_budget_trimming_is_reported():
    from src.agents.protocol import RunBudget

    runtime = AgentRuntime(run_budget=RunBudget(total=TaskBudget(max_llm_calls=3)))
    task = _task(budget=TaskBudget(max_llm_calls=10))
    note = runtime.reserve_budget(task)
    assert note
    assert task.budget.max_llm_calls == 3
    assert task.budget.reserved is True
    assert any(e["kind"] == "budget_trimmed" for e in runtime.events)


def test_budget_from_env_maps_legacy_limits():
    budget = budget_from_env(actions=5, tool_calls=7)
    assert budget.max_llm_calls == 5 and budget.max_tool_calls == 7


# --------------------------------------------------------------------------
# 取消与失败收敛
# --------------------------------------------------------------------------
def test_cancel_token_is_observed_between_calls():
    cancel = CancelToken()
    usage = UsageRecord()

    class _Inner:
        def invoke(self, messages):
            cancel.cancel("用户停止")
            return SimpleNamespace(content="x", response_metadata={},
                                   usage_metadata=None)

    llm = BudgetedLLM(_Inner(), usage, TaskBudget(), cancel, "t1")
    llm.invoke([])                                   # 第一次放行
    with pytest.raises(TaskCancelled):
        llm.invoke([])                               # 取消后立刻拒绝


def test_cancelled_run_does_not_execute_new_tasks():
    cancel = CancelToken()
    cancel.cancel("运行已停止")
    runtime = AgentRuntime(cancel=cancel)
    executor = InProcessExecutor(runtime)
    executor.register(_EchoAgent("evidence"))
    result = executor.execute(_task(), ContextPack())
    assert result.outcome.value == "cancelled"
    assert "运行已停止" in result.failure_reason
    assert not any(e["kind"] == "task_started" for e in runtime.events)


def test_tool_loop_checks_cancellation_before_each_round():
    cancel = CancelToken()
    loop = ToolLoop([ToolSpec(name="ping", fn=lambda: "pong")], max_rounds=3)

    class _CancelAfterFirst:
        def __init__(self):
            self.n = 0
            self.inner = _ScriptedLLM([{"tool_calls": [
                {"name": "ping", "args": {}, "id": "c"}]}])

        def bind_tools(self, tools):
            self.inner.bind_tools(tools)
            return self

        def invoke(self, messages, *a, **k):
            self.n += 1
            if self.n >= 2:
                cancel.cancel("停止")
            return self.inner.invoke(messages)

    with pytest.raises(TaskCancelled):
        loop.run(_CancelAfterFirst(), [], grant=AgentRuntime().issue_grant(_task()),
                 cancel=cancel, task_id="t1", usage=UsageRecord())


def test_agent_exception_becomes_failed_result_with_reason():
    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_BoomAgent())
    result = executor.execute(_task("reasoning"), ContextPack())
    assert result.outcome.value == "failed"
    assert "ValueError" in result.failure_reason
    assert "推导崩溃" in result.failure_reason


def test_blocked_agent_result_is_preserved_with_its_need():
    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_BlockedAgent())
    result = executor.execute(_task("reasoning"), ContextPack())
    assert result.outcome.value == "blocked"
    assert result.followup_needs[0].owner == "evidence"
    assert result.followup_needs[0].kind.value == "clause"


def test_grant_error_becomes_failed_with_permission_reason():
    class _GrantMisuse:
        role = "evidence"

        def run(self, task, context, runtime):
            context.grant.require_tool("propose_claim")     # 越权
            return AgentResult(task_id=task.task_id, agent="evidence",
                               outcome="completed", summary="x")

    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_GrantMisuse())
    result = executor.execute(_task(), ContextPack())
    assert result.outcome.value == "failed"
    assert "权限不足" in result.failure_reason


# --------------------------------------------------------------------------
# 结果与身份
# --------------------------------------------------------------------------
def test_result_task_id_mismatch_is_flagged():
    class _WrongId:
        role = "evidence"

        def run(self, task, context, runtime):
            return AgentResult(task_id="someone-else", agent="evidence",
                               outcome="completed", summary="x")

    runtime = AgentRuntime()
    executor = InProcessExecutor(runtime)
    executor.register(_WrongId())
    result = executor.execute(_task(), ContextPack())
    assert result.outcome.value == "partial"
    assert "task_id" in result.summary


def test_tool_observations_are_recorded_in_events():
    events: list[tuple[str, dict]] = []

    class _Sink:
        def event(self, kind, payload):
            events.append((kind, payload))

    runtime = AgentRuntime(sink=_Sink())
    loop = ToolLoop([ToolSpec(name="ping", fn=lambda: "pong")], max_rounds=2,
                    sink=runtime.sink)
    llm = _ScriptedLLM([{"tool_calls": [{"name": "ping", "args": {}, "id": "c"}]}])
    loop.run(llm, [], grant=runtime.issue_grant(_task()), usage=UsageRecord())
    tool_events = [p for k, p in events if k == "tool_run"]
    assert tool_events and tool_events[0]["tool"] == "ping"
    assert tool_events[0]["status"] == "ok"
    assert json.dumps(tool_events[0], ensure_ascii=False)
