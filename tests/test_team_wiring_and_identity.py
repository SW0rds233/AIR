from __future__ import annotations

"""唯一装配与运行身份的回归 (§3.1 G02 / G14, §9 R0)。

审计复现过两件事, 都在这里固定:

- **G02**: 团队入口没有注入模型工厂, `AgentRuntime.llm_available()` 恒为 False ——
  主控与七类角色全退化成规则模板, 却照样能跑出一份"完整"交付包;
- **G14**: `supervisor.plan()` 产出的任务 `run_id` 全为空, `team_projection()`
  又用 `not t.get("run_id")` 放行 —— 同项目两次运行的对象会互相串。

另有一条**顺带发现的真实缺陷**: `llm_available()` 原先只判断"工厂是否存在",
而工厂可以返回 None (显式离线), 于是离线模式下角色以为模型可用、调用时抛异常、
再退回确定性实现 —— 症状被掩盖成"跑通了"。
"""

import pytest

from src.agents.protocol import AgentTask, AgentResult
from src.agents.runtime import AgentRuntime
from src.research.projection import TeamProjection
from src.research.store import ResearchStore
from src.research.task_store import TaskStore


# --------------------------------------------------------------------------
# G02: 角色模型接线
# --------------------------------------------------------------------------
def test_llm_available_reflects_the_factory_result_not_its_existence():
    """`llm_available()` 必须是**实测**: 工厂返回 None 就是没有模型。"""
    offline = AgentRuntime(llm_factory=lambda stage: None)
    assert offline.llm_available() is False
    assert offline.llm_available("writing") is False

    class _Model:
        def invoke(self, *_a, **_k):
            return "ok"

    online = AgentRuntime(llm_factory=lambda stage: _Model())
    assert online.llm_available() is True


def test_offline_runtime_reports_unavailable_and_raises_clearly():
    """离线时 `llm()` 要给出明确失败, 而不是 AttributeError 之类。"""
    from src.agents.protocol import UsageRecord

    runtime = AgentRuntime(llm_factory=lambda stage: None)
    task = AgentTask(agent="writing", objective="写", source_policy="user_kb")
    with pytest.raises(RuntimeError):
        runtime.llm(task, UsageRecord())


def test_factory_failure_is_visible_not_silently_offline():
    """取模型失败要落事件 (可见), 不能与"显式离线"混为一谈。"""

    def boom(stage: str):
        raise RuntimeError("没有配置 API key")

    runtime = AgentRuntime(llm_factory=boom)
    assert runtime.llm_available() is False
    assert any(e["kind"] == "llm_unavailable" for e in runtime.events), runtime.events


def test_factory_is_resolved_per_role_and_cached():
    """角色模型按需解析且只解析一次 (避免每个任务都重建模型/重复计费风险)。"""
    seen: list[str] = []

    def factory(stage: str):
        seen.append(stage)
        return object()

    runtime = AgentRuntime(llm_factory=factory)
    runtime.llm_available("writing")
    runtime.llm_available("writing")
    assert seen == ["writing"]
    runtime.llm_available("review")
    assert seen == ["writing", "review"]


def test_team_entry_injects_a_role_model_factory(monkeypatch):
    """HTTP 装配出来的团队必须**带**模型工厂 (否则整个团队只跑规则模板)。"""
    from src import server

    class _Session:
        request = {"project_id": "p", "problem_id": "q", "request": "研究",
                   "source_policy": "user_kb"}
        run_id = "run-x"
        session_id = "s-1"
        topic = "t"

        def emit(self, *_a, **_k):
            pass

    monkeypatch.delenv("THEORY_LLM", raising=False)
    seen: list[str] = []

    def fake_build_llm(key: str):
        seen.append(key)

        class _Model:
            def invoke(self, *_a, **_k):
                return "ok"

        return _Model()

    monkeypatch.setattr("src.config.build_llm", fake_build_llm)
    app = server._build_team_app(_Session())
    team = app.session.team
    assert team.runtime.llm_available() is True
    # 角色 -> 模型配置键的映射确实生效 (主控用 coordinator, 审阅用 reviewer)
    team.runtime.llm_available("writing")
    team.runtime.llm_available("review")
    assert "coordinator" in seen and "reviewer" in seen


def test_offline_switch_is_respected_by_the_team_entry(monkeypatch):
    """`THEORY_LLM=0` 是显式离线: 团队入口如实报告"无模型"。"""
    from src import server

    class _Session:
        request = {"project_id": "p", "problem_id": "q", "request": "研究"}
        run_id = "run-y"
        session_id = "s-2"
        topic = "t"

        def emit(self, *_a, **_k):
            pass

    monkeypatch.setenv("THEORY_LLM", "0")
    app = server._build_team_app(_Session())
    assert app.session.team.runtime.llm_available() is False


# --------------------------------------------------------------------------
# G14: 运行身份
# --------------------------------------------------------------------------
def test_plan_tasks_carry_the_run_identity(monkeypatch):
    """计划里**每条**任务都必须带 run_id/project_id/problem_id。"""
    from src.graph.research_graph import TeamRun

    store = ResearchStore("g14", db_path=":memory:")
    try:
        team = TeamRun(project_id="proj-1", problem_id="prob-1", run_id="run-42",
                       request="证明不等式并写成论文",
                       task_store=TaskStore("proj-1", store))
        team.prepare()
        tasks = team.loop.plan.tasks
        assert tasks, "没有产出任何任务"
        for task in tasks:
            assert task.get("run_id") == "run-42", task
            assert task.get("project_id") == "proj-1"
            assert task.get("problem_id") == "prob-1"
    finally:
        store.close()


def test_projection_does_not_admit_run_less_tasks():
    """投影只接受**本运行**的任务 (无 run_id 的旧记录不得冒充本次运行)。"""
    from src.team_api import team_projection

    store = ResearchStore("g14-api", db_path=":memory:")
    try:
        task_store = TaskStore("g14-api", store)
        task_store.create(AgentTask(agent="evidence", objective="本运行",
                                    project_id="g14-api", run_id="run-A"))
        task_store.create(AgentTask(agent="evidence", objective="无运行身份",
                                    project_id="g14-api", run_id=""))
        task_store.create(AgentTask(agent="evidence", objective="另一次运行",
                                    project_id="g14-api", run_id="run-B"))
    finally:
        store.close()
    projection = team_projection("g14-api", "run-A")
    objectives = {str(t.get("objective", "")) for t in projection["tasks"]}
    assert "本运行" in objectives
    assert "另一次运行" not in objectives
    assert "无运行身份" not in objectives, "无 run_id 的任务被算进了本次运行"


def test_projection_rows_are_scoped_to_the_run():
    """对象查询默认只返回本运行产出的对象。"""
    store = ResearchStore("g14-rows", db_path=":memory:")
    try:
        projection_a = TeamProjection(store, run_id="run-A")
        projection_b = TeamProjection(store, run_id="run-B")
        for projection, run in ((projection_a, "run-A"), (projection_b, "run-B")):
            task = AgentTask(agent="evidence", objective="o", project_id="p",
                             problem_id="q", run_id=run)
            result = AgentResult(
                task_id=task.task_id, agent="evidence", outcome="completed",
                summary="s",
                proposed_changes=[_source_proposal(f"src-{run}")])
            projection.register(task, result)
        a_rows = projection_a.rows("evidence")
        b_rows = projection_b.rows("evidence")
        assert [row["id"] for row in a_rows] == ["src-run-A"]
        assert [row["id"] for row in b_rows] == ["src-run-B"]
        # 项目级视图刻意跨运行 (归档/对比用), 必须显式调用
        assert {row["id"] for row in projection_a.project_rows("evidence")} == {
            "src-run-A", "src-run-B"}
    finally:
        store.close()


def test_objects_record_their_scope():
    """对象必须记住自己的 project/problem/run (否则无法按运行裁剪)。"""
    store = ResearchStore("g14-scope", db_path=":memory:")
    try:
        projection = TeamProjection(store, run_id="run-Z")
        task = AgentTask(agent="evidence", objective="o", project_id="pj",
                         problem_id="pb", run_id="run-Z")
        result = AgentResult(task_id=task.task_id, agent="evidence",
                             outcome="completed", summary="s",
                             proposed_changes=[_source_proposal("s1")])
        projection.register(task, result)
        row = store.get("evidence", "s1")
        assert row["_scope"] == {"project_id": "pj", "problem_id": "pb",
                                 "run_id": "run-Z"}
    finally:
        store.close()


def _source_proposal(object_id: str):
    from src.agents.protocol import ChangeProposal

    return ChangeProposal(kind="source", object_id=object_id,
                          payload={"title": object_id})
