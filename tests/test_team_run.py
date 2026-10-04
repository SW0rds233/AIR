from __future__ import annotations

"""团队闭环与任务持久化的行为用例 (合并计划 §10 M1 / M4)。

这些用例固定的是**机制不变量**, 而不是某次运行的输出:
- 主控循环能跑通"检索 → 综合 → 写作 → 审阅", 且不重复派工 (实测过三种无限循环);
- 补派/返工成功后能解锁下游依赖 (否则写作永不派发);
- 失败/受阻的依赖不再阻塞下游, 但同一件"角色+目标"不会被重复派;
- 任务生命周期、幂等、恢复视图与 read-set 事务一致性 (M4);
- 投影层只**登记**候选, 不写命题真值 (判定层职责不越界)。
"""

import pytest

from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    TaskOutcome,
    TaskStatus,
)
from src.agents.supervisor import SupervisorAgent, assignment_key
from src.graph.research_graph import TeamRun, slim_object_rows
from src.research.projection import TeamProjection
from src.research.store import ResearchStore
from src.research.task_store import (
    ReadSetConflict,
    TaskNotFound,
    TaskStore,
    check_read_set,
    summarize_recovery,
)


def _brief(request: str):
    return SupervisorAgent().brief(request, project_id="p1", problem_id="q1",
                                  source_policy="user_kb")


def _task(objective: str, agent: str = "evidence", **over) -> AgentTask:
    data = {"agent": agent, "objective": objective, "project_id": "p1",
            "problem_id": "q1"}
    data.update(over)
    return AgentTask(**data)


# ==========================================================================
# 主控循环: 不重复派工 (三种实测过的无限循环)
# ==========================================================================
def test_supervisor_does_not_redispatch_same_role_and_objective():
    """同一"角色+目标"只派一次 —— 否则每轮都派一个新任务直到烧光预算。"""
    supervisor = SupervisorAgent()
    brief = _brief("解释某机理并写成论文")
    plan = supervisor.plan(brief, version=1)
    first = supervisor.decide(brief=brief, plan=plan, results={})
    assert first.decision.value == "dispatch"
    dispatched = {assignment_key(brief, t.agent, t.objective) for t in first.tasks}
    # 本轮所有可派任务都受阻
    results = {t.task_id: AgentResult(task_id=t.task_id, agent=t.agent,
                                      outcome=TaskOutcome.blocked,
                                      failure_reason="缺输入")
               for t in first.tasks}
    again = supervisor.decide(brief=brief, plan=plan, results=results,
                              dispatched=dispatched)
    re_dispatched = [t for t in again.tasks
                     if assignment_key(brief, t.agent, t.objective) in dispatched]
    assert not re_dispatched, f"重复派工: {[t.objective for t in re_dispatched]}"


def test_supervisor_does_not_repeat_a_need_kind_forever():
    """需求措辞里的计数会变, 但同一类需求不得反复变成新任务。"""
    from src.agents.protocol import ResearchNeed

    supervisor = SupervisorAgent()
    brief = _brief("写一篇综述")
    plan = supervisor.plan(brief, version=1)
    need_one = ResearchNeed(kind="more_sources", statement="3 条命中缺少定位")
    first = supervisor.decide(brief=brief, plan=plan, results={}, open_needs=[need_one])
    assert first.decision.value == "dispatch"
    dispatched = {assignment_key(brief, t.agent, t.objective) for t in first.tasks}
    dispatched |= {str((t.hints or {}).get("need_assignment", "")) for t in first.tasks}
    # 同一类需求、计数变了 -> 不该再派
    need_two = ResearchNeed(kind="more_sources", statement="2 条命中缺少定位")
    second = supervisor.decide(brief=brief, plan=plan, results={},
                               open_needs=[need_two], dispatched=dispatched)
    repeated = [t for t in second.tasks
                if str((t.hints or {}).get("need_assignment", "")) in dispatched]
    assert not repeated


def test_settled_failure_does_not_block_downstream_forever():
    """已尝试过并失败的前置不再阻塞下游 —— 否则交付永远卡死。"""
    supervisor = SupervisorAgent()
    brief = _brief("解释某机理并写成论文")
    plan = supervisor.plan(brief, version=1)
    writing = next(t for t in plan.tasks if t["agent"] == "writing")
    by_id = {t["task_id"]: t for t in plan.tasks}
    results = {tid: AgentResult(task_id=tid, agent=by_id[tid]["agent"],
                                outcome=TaskOutcome.blocked, failure_reason="资料不可用")
               for tid in writing["depends_on"]}
    decision = supervisor.decide(brief=brief, plan=plan, results=results)
    assert decision.decision.value == "dispatch"
    assert any(t.agent == "writing" for t in decision.tasks), decision.reason


def test_equivalent_completion_unlocks_dependents():
    """补派任务换了 task_id 但目标相同 -> 视为原任务已完成, 解锁下游。"""
    supervisor = SupervisorAgent()
    brief = _brief("解释某机理并写成论文")
    plan = supervisor.plan(brief, version=1)
    writing = next(t for t in plan.tasks if t["agent"] == "writing")
    deps = list(writing["depends_on"])
    results: dict[str, AgentResult] = {}
    objectives: dict[str, str] = {}
    # 原任务全部受阻
    for tid in deps:
        record = next(t for t in plan.tasks if t["task_id"] == tid)
        results[tid] = AgentResult(task_id=tid, agent=record["agent"],
                                   outcome=TaskOutcome.blocked, failure_reason="缺")
        objectives[tid] = record["objective"]
    # 补派任务: 新 id, 与某个受阻任务同目标, 且成功
    clone = next(t for t in plan.tasks if t["agent"] == "reasoning")
    results["task-rework"] = AgentResult(task_id="task-rework", agent=clone["agent"],
                                         outcome=TaskOutcome.completed, summary="ok")
    objectives["task-rework"] = clone["objective"]
    dispatched = {assignment_key(brief, clone["agent"], clone["objective"])}
    decision = supervisor.decide(brief=brief, plan=plan, results=results,
                                 objectives=objectives, dispatched=dispatched)
    assert any(t.agent == "writing" for t in decision.tasks), decision.reason


def test_replan_keeps_task_identity_so_edges_stay_valid():
    """replan 不得换任务 id —— 换了之后依赖边会指向不存在的任务。"""
    supervisor = SupervisorAgent()
    brief = _brief("解释某机理并写成论文")
    plan = supervisor.plan(brief, version=1)
    ids_before = set(plan.task_ids())
    edges_before = {k: list(v) for k, v in plan.edges.items()}
    first_id = next(iter(ids_before))
    failed = {first_id: AgentResult(task_id=first_id,
                                    outcome=TaskOutcome.failed,
                                    failure_reason="x")}
    replanned = supervisor.replan(plan, failed, version=2)
    assert set(replanned.task_ids()) == ids_before
    assert replanned.edges == edges_before
    assert replanned.version == 2
    record = next(t for t in replanned.tasks if t["task_id"] == first_id)
    assert record["attempt"] == 2
    assert "x" in record["hints"]["previous_failure"]


def test_adopt_keeps_existing_tasks_and_edges():
    supervisor = SupervisorAgent()
    brief = _brief("写一篇综述")
    plan = supervisor.plan(brief, version=1)
    before = set(plan.task_ids())
    extra = _task("补检索: 缺 BRC 定理原文")
    supervisor.adopt(plan, extra, depends_on=[])
    assert set(plan.task_ids()) == before | {extra.task_id}
    assert extra.task_id in plan.edges
    # 依赖边仍然有效 (指向既有任务)
    for deps in plan.edges.values():
        assert set(deps) <= set(plan.task_ids())


def test_replan_stops_at_version_limit():
    supervisor = SupervisorAgent(max_plan_version=2)
    brief = _brief("写综述")
    plan = supervisor.plan(brief, version=2)
    failed = {t: AgentResult(task_id=t, outcome=TaskOutcome.failed, failure_reason="x")
              for t in plan.task_ids()}
    assert supervisor.replan(plan, failed, version=3).version == 2


def test_decide_reports_clarification_and_stop_states():
    supervisor = SupervisorAgent()
    brief = _brief("写综述")
    plan = supervisor.plan(brief, version=1)
    stopped = supervisor.decide(brief=brief, plan=plan, stopped=True)
    assert stopped.decision.value == "stop_with_report"
    delivered = supervisor.decide(brief=brief, plan=plan, delivered=True)
    assert delivered.decision.value == "deliver"


# ==========================================================================
# 团队闭环 (离线, 真跑七个角色)
# ==========================================================================
def test_team_run_completes_a_research_task_offline(tmp_path, monkeypatch):
    """一条自然语言任务能自主走完 检索 → 综合 → 写作 → 审阅, 无需用户选模式。

    这里给一个**真实的小资料库**: 资料范围可用时检索角色应当成功 (而不是"资料范围
    不可用")。没有资料库的场景由下一个用例覆盖。
    """
    from src import config
    from src.kb.identity import build_identity
    from src.kb.schema import LitRecord, Provenance
    from src.kb.store import KBStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    topic = "kb-team"
    store = KBStore(topic)
    item = {"title": "The Nonexistence of Certain Finite Projective Planes",
            "authors": "X", "year": "2001"}
    store.upsert_document(
        LitRecord(doc_id="doc-1", title=item["title"], authors="X", year="2001",
                  abstract="Bruck-Ryser-Chowla.", manual_asserted=True,
                  has_fulltext=True, identity=build_identity(item)),
        search_text=("The Nonexistence of Certain Finite Projective Planes design "
                     "block design BIBD combinatorial design Bruck Ryser Chowla "
                     "nonexistence"))
    store.set_identity(["title:thenonexistenceofcertainfiniteprojectiveplanes|x|2001"],
                       "doc-1")
    store.add_provenance("doc-1", [Provenance(origin="manual", detail="x.pdf")])
    store.append_event("ingest_manual", {"doc_id": "doc-1"})
    store.close()

    with TeamRun(project_id="proj-team", problem_id="prob-1",
                 request="解释该机理并写成论文: 参数 2-(211,15,1) 的设计是否存在",
                 source_set_ids=[topic], source_policy="user_kb",
                 max_rounds=20) as team:
        team.set_source_sets([{"source_set_id": topic, "documents": 1,
                               "cards": 0, "indexed": False}])
        out = team.run()
        manuscript = team.projection.rows("manuscript")
        evidence = team.projection.rows("evidence")

    roles = {r.agent for r in out.results.values()
             if r.outcome in (TaskOutcome.completed, TaskOutcome.partial)}
    assert "evidence" in roles
    assert "reasoning" in roles
    assert "writing" in roles
    assert "review" in roles
    assert out.status == TaskStatus.completed.value
    assert evidence, "检索角色必须登记可定位来源"
    assert evidence[0].get("locator")
    assert manuscript, "写作角色必须真的产出稿件"


def _prepared_kb(tmp_path, monkeypatch, topic: str = "kb-step"):
    """建一个小型真实资料库 (与上一个用例同构), 供逐轮/一次跑完的对照使用。"""
    from src import config
    from src.kb.identity import build_identity
    from src.kb.schema import LitRecord, Provenance
    from src.kb.store import KBStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    store = KBStore(topic)
    item = {"title": "The Nonexistence of Certain Finite Projective Planes",
            "authors": "X", "year": "2001"}
    store.upsert_document(
        LitRecord(doc_id="doc-1", title=item["title"], authors="X", year="2001",
                  abstract="Bruck-Ryser-Chowla.", manual_asserted=True,
                  has_fulltext=True, identity=build_identity(item)),
        search_text=("The Nonexistence of Certain Finite Projective Planes design "
                     "block design BIBD combinatorial design Bruck Ryser Chowla "
                     "nonexistence"))
    store.set_identity(["title:thenonexistenceofcertainfiniteprojectiveplanes|x|2001"],
                       "doc-1")
    store.add_provenance("doc-1", [Provenance(origin="manual", detail="x.pdf")])
    store.append_event("ingest_manual", {"doc_id": "doc-1"})
    store.close()
    return topic


def test_stepwise_loop_matches_a_single_run(tmp_path, monkeypatch):
    """**差异测试**: `prepare()` + 反复 `step()` 与一次 `run()` 必须等价。

    这是把主控循环改成"可逐步驱动"的判据 —— 不是看代码被搬到哪里, 而是看两条路径
    是否**只有一份实现**、且给出同一结果 (任务顺序、状态、停止原因、用量、登记对象)。
    会话引擎接入后, 界面看到的每一步都来自这里, 因此这条等价性必须成立。
    """
    topic = _prepared_kb(tmp_path, monkeypatch, "kb-step")
    request = "解释该机理并写成论文: 参数 2-(211,15,1) 的设计是否存在"
    key = {"request": request, "source_set_ids": [topic], "source_policy": "user_kb",
           "max_rounds": 20}
    sources = [{"source_set_id": topic, "documents": 1, "cards": 0, "indexed": False}]

    with TeamRun(project_id="proj-mono", problem_id="prob-1", **key) as whole:
        whole.set_source_sets(sources)
        out_all = whole.run()

    with TeamRun(project_id="proj-step", problem_id="prob-1", **key) as stepped:
        stepped.set_source_sets(sources)
        assert stepped.loop.rounds == 0 and not stepped.loop.finished
        stepped.prepare()
        # 准备是幂等的: 重复调用不得换掉任务身份 (否则依赖边与幂等键失效)
        plan_id = stepped.loop.plan.plan_id
        stepped.prepare()
        assert stepped.loop.plan.plan_id == plan_id
        rounds = 0
        while stepped.step():
            rounds += 1
            assert rounds < 100, "逐轮驱动没有收敛"
        out_step = stepped.outcome
        state = stepped.loop

    assert state.finished, "step() 返回 False 之后状态必须已收尾"
    # 任务顺序与状态一致 (任务 id 由计划/派工决定, 同一输入下必须逐项相同)
    assert [whole.outcome.results[t].agent
            for t in out_all.task_order] == \
           [out_step.results[t].agent for t in out_step.task_order]
    assert out_step.status == out_all.status
    assert out_step.stop_reason == out_all.stop_reason
    assert out_step.rounds == out_all.rounds
    assert out_step.usage.to_dict() == out_all.usage.to_dict()
    # 任务 id 是随机生成的 (每次运行不同), 因此按**角色序列**比较产出结构:
    # 逐轮驱动与一次跑完必须登记同一批候选种类、同一批角色成果。
    assert [out_step.results[t].agent for t in out_step.task_order] == \
        [out_all.results[t].agent for t in out_all.task_order]
    assert len(out_step.results) == len(out_all.results)
    # 逐轮驱动同样必须把稿件登记进投影 (驱动方式不得影响产物)
    with TeamRun(project_id="proj-step2", problem_id="prob-1", **key) as again:
        again.set_source_sets(sources)
        while again.step():
            pass
        assert again.projection.rows("manuscript"), "逐轮驱动也必须登记稿件"
        assert again.projection.rows("evidence"), "逐轮驱动也必须登记可定位来源"


def test_team_run_reports_unavailable_sources_without_pretending(tmp_path, monkeypatch):
    """没有可用资料范围时不伪造检索成功, 也不无限重派。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    with TeamRun(project_id="proj-nokb", problem_id="prob-1",
                 request="判断参数为 2-(211,15,1) 的设计是否存在",
                 source_policy="user_kb", max_rounds=12) as team:
        out = team.run()
    assert out.status != TaskStatus.completed.value
    summaries = " ".join(r.summary for r in out.results.values())
    assert "资料范围不可用" in summaries or "检索未执行" in summaries


def test_team_run_records_task_lifecycle_and_is_bounded(tmp_path, monkeypatch):
    """没有资料范围时不能无限重派: 轮次有界, 且如实报未决。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    with TeamRun(project_id="proj-noloop", problem_id="prob-1",
                 request="判断参数为 2-(211,15,1) 的设计是否存在",
                 source_policy="user_kb", max_rounds=12) as team:
        out = team.run()
        tasks = team.task_store.tasks()

    assert out.rounds < 12, "不应跑满轮次上限 (说明存在重复派工)"
    assert out.status in (TaskStatus.partial.value, TaskStatus.completed.value)
    assert out.stop_reason
    assert tasks, "任务必须落盘"
    assert all(t.get("status") for t in tasks)
    # 同一"角色+目标"不能出现两次派工记录
    seen: set[tuple[str, str]] = set()
    for record in tasks:
        key = (str(record.get("agent")), str(record.get("objective")))
        assert key not in seen, f"重复派工: {key}"
        seen.add(key)


def test_team_run_honours_cancellation(tmp_path, monkeypatch):
    from src import config
    from src.agents.runtime import CancelToken

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    cancel = CancelToken()
    cancel.cancel("用户停止")

    with TeamRun(project_id="proj-cancel", problem_id="prob-1",
                 request="写一篇综述", source_policy="user_kb", cancel=cancel,
                 max_rounds=6) as team:
        out = team.run()
    assert out.status == "stopped"
    assert "停止" in out.stop_reason


# ==========================================================================
# M2: 不同任务类型走不同策略 (抽取出来的推理内核)
# ==========================================================================
def test_math_request_uses_the_formal_derivation_kernel(tmp_path, monkeypatch):
    """精确数学题必须走形式化推导内核, 并产出可审查的推导记录。

    M2 的验收要求 "精确数学题、机理题与纯综述走不同子任务图"; 这条用例固定"数学题
    确实走了内核那条分支", 反向保护见
    `test_reasoning_kernel.py::test_reasoning_agent_uses_the_kernel`。
    """
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    with TeamRun(project_id="proj-kernel", problem_id="p1",
                 request="对所有实数 x 证明: x**2 >= 0, 并给出严格证明",
                 source_policy="user_kb", max_rounds=12) as team:
        # 给角色一个可形式化的命题 (团队 пути的上下文来自投影层)
        from src.research.schemas import Claim

        claim = Claim(id="clm-team", statement="对所有实数 x: x**2 >= 0",
                      variables=["x"], variable_domains={"x": "real"})
        team.projection.store.put("claim", claim.id,
                                  {**claim.model_dump(mode="json"), "status": "proposed"})
        outcome = team.run()

    reasoning = [r for r in outcome.results.values() if r.agent == "reasoning"]
    assert reasoning, "数学题必须派推理任务"
    # 必须**真的**走了形式化推导内核 (不是"提交了候选就算")
    used_kernel = [r for r in reasoning if r.payload.get("kernel")]
    assert used_kernel, (
        "数学题没有走形式化推导内核: "
        + "; ".join(r.summary for r in reasoning))
    assert used_kernel[0].proposed_changes, "内核路径必须交出结构化成果"
    # 反方审查必须真的跑过 (计划书 §7.2 的八项清单)
    summary = used_kernel[0].summary
    assert "反方审查" in summary
    # 角色仍只提交候选, 不得声明改变结论
    assert all(p.may_change_conclusion is False
               for p in used_kernel[0].proposed_changes)


def test_survey_request_does_not_enter_the_derivation_kernel(tmp_path, monkeypatch):
    """纯综述不得被强行转成形式化推导 (合并计划 §7.1 的明确要求)。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    with TeamRun(project_id="proj-survey", problem_id="p1",
                 request="总结该方向的研究进展并写一篇综述",
                 source_policy="user_kb", max_rounds=12) as team:
        outcome = team.run()

    for result in outcome.results.values():
        if result.agent != "reasoning":
            continue
        assert not result.payload.get("kernel"), (
            "综述类请求不得进入形式化推导内核: " + result.summary)


def test_derivation_strategy_is_chosen_by_the_question(tmp_path, monkeypatch):
    """策略选择是可判定的: 证明题走 derivation, 综述走 synthesis。"""
    from src.agents.reasoning import classify_strategy

    assert classify_strategy("对所有实数 x 证明 x**2 >= 0") == "derivation"
    assert classify_strategy("判断该设计是否存在, 给出严格证明") == "derivation"
    assert classify_strategy("总结该方向的研究进展并综述") == "synthesis"
    assert classify_strategy("解释该机理为什么会出现") == "synthesis"
    assert classify_strategy("用样本估计该效应", has_data=True) == "quantitative"



def test_projection_registers_candidates_without_deciding_truth(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    store = ResearchStore("proj-proj", tmp_path / "data" / "p.sqlite")
    try:
        projection = TeamProjection(store)
        task = _task("检索 2-设计不存在性的文献", agent="evidence")
        result = AgentResult(
            task_id=task.task_id, agent="evidence", outcome=TaskOutcome.completed,
            summary="ok",
            proposed_changes=[ChangeProposal(
                kind="evidence",
                payload={"title": "Nonexistence of biplanes", "source_id": "doc-1",
                         "locator": "p7 / Theorem 10", "relation": "insufficient"},
                rationale="检索候选材料")],
        )
        versions = projection.register(task, result)
        rows = projection.rows("evidence")
        assert rows and versions
        row = rows[0]
        # 登记了出处与来源身份
        assert row["locator"] == "p7 / Theorem 10"
        assert row["source_id"] == "doc-1"
        # **没有**写命题真值这类判定字段
        assert "status" not in row or row.get("status") not in ("supported", "refuted")
        # 审计线索齐全
        assert row["_submitted_by"] == "evidence"
        assert row["_task_id"] == task.task_id
    finally:
        store.close()


def test_projection_is_idempotent_for_same_candidate(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    store = ResearchStore("proj-idem", tmp_path / "data" / "p.sqlite")
    try:
        projection = TeamProjection(store)
        task = _task("检索", agent="evidence")

        def _result() -> AgentResult:
            return AgentResult(
                task_id=task.task_id, agent="evidence", outcome=TaskOutcome.completed,
                summary="ok",
                proposed_changes=[ChangeProposal(
                    kind="evidence", payload={"source_id": "doc-1", "locator": "p1"})])

        projection.register(task, _result())
        versions_second = projection.register(task, _result())
        assert versions_second == {}
        assert len(projection.rows("evidence")) == 1
    finally:
        store.close()


def test_projection_rejects_unregistered_kind(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    store = ResearchStore("proj-kind", tmp_path / "data" / "p.sqlite")
    try:
        projection = TeamProjection(store)
        task = _task("x", agent="evidence")
        result = AgentResult(task_id=task.task_id, agent="evidence",
                             outcome=TaskOutcome.completed, summary="ok",
                             proposed_changes=[ChangeProposal(kind="snapshot",
                                                              payload={"a": 1})])
        assert projection.register(task, result) == {}
        assert projection.skipped
        assert projection.rows("evidence") == []
    finally:
        store.close()


def test_slim_object_rows_truncates_long_text():
    rows = [{"id": "m1", "version": 1, "title": "t",
             "sections": [{"heading": "1", "blocks": [
                 {"block_id": "b1", "role": "claim", "text": "x" * 5000}]}]}]
    slim = slim_object_rows(rows, text_limit=100)
    block = slim[0]["sections"][0]["blocks"][0]
    assert len(block["text"]) == 100
    assert block["block_id"] == "b1"


# ==========================================================================
# 任务存储与 read-set 一致性 (M4)
# ==========================================================================
def _task_store(tmp_path, name="p1") -> TaskStore:
    return TaskStore(name, ResearchStore(name, tmp_path / f"{name}.sqlite"))


def test_task_store_lifecycle_and_recovery_view(tmp_path):
    store = _task_store(tmp_path, "lifecycle")
    try:
        done = _task("已完成的事", agent="evidence")
        store.create(done)
        store.finish(done.task_id, AgentResult(task_id=done.task_id, agent="evidence",
                                               outcome=TaskOutcome.completed,
                                               summary="ok"))
        blocked = _task("受阻的事", agent="reasoning")
        store.create(blocked)
        store.finish(blocked.task_id, AgentResult(task_id=blocked.task_id,
                                                  agent="reasoning",
                                                  outcome=TaskOutcome.blocked,
                                                  failure_reason="缺条件"))
        queued = _task("待办的事", agent="writing")
        store.create(queued)

        assert store.task(done.task_id)["status"] == TaskStatus.completed.value
        assert store.task(blocked.task_id)["status"] == TaskStatus.waiting.value
        view = store.recoverable()
        assert [t["task_id"] for t in view["done"]] == [done.task_id]
        assert [t["task_id"] for t in view["queued"]] == [queued.task_id]
        assert [t["task_id"] for t in view["waiting"]] == [blocked.task_id]
        assert "已提交" in summarize_recovery(view)
    finally:
        store.close()


def test_task_store_create_is_idempotent_by_key(tmp_path):
    store = _task_store(tmp_path, "idem")
    try:
        first = _task("同一件事", agent="evidence", project_id="idem")
        second = AgentTask(agent="evidence", objective="同一件事",
                           project_id="idem", problem_id="q1")
        assert first.idempotency_key == second.idempotency_key
        store.create(first)
        store.create(second)
        assert len(store.tasks()) == 1
    finally:
        store.close()


def test_task_idempotency_key_ignores_plan_version_but_not_wording():
    """计划升版不等于要做新东西; 目标变了才是新任务。"""
    from src.agents.protocol import idempotency_key_for

    same_v1 = idempotency_key_for("p", "q", 1, "evidence", "检索 A")
    same_v2 = idempotency_key_for("p", "q", 2, "evidence", "检索 A")
    other = idempotency_key_for("p", "q", 1, "evidence", "检索 B")
    assert same_v1 == same_v2
    assert same_v1 != other


def test_task_store_find_by_key_and_unknown_task(tmp_path):
    store = _task_store(tmp_path, "find")
    try:
        task = _task("检索 A", agent="evidence")
        store.create(task)
        assert store.find_by_key(task.idempotency_key)["task_id"] == task.task_id
        assert store.find_by_key("") is None
        with pytest.raises(TaskNotFound):
            store.task("task-missing")
        assert store.maybe_task("task-missing") is None
        assert store.next_attempt(task.task_id) == 2
    finally:
        store.close()


def test_read_set_conflict_is_detected_and_refused(tmp_path):
    """依据 v2 产出、对象已到 v3 -> 拒绝合入并如实记录 (不静默丢弃)。"""
    store = _task_store(tmp_path, "readset")
    try:
        task = _task("依据 v2 的工作", agent="reasoning")
        store.create(task)
        result = AgentResult(task_id=task.task_id, agent="reasoning",
                             outcome=TaskOutcome.completed, summary="ok",
                             input_versions={"claim-1": 2})
        with pytest.raises(ReadSetConflict) as excinfo:
            store.commit_result(task, result, current_versions={"claim-1": 3})
        assert excinfo.value.stale == {"claim-1": (2, 3)}
        # 冲突被记进事件, 不是静默失败
        assert any(e.get("type") == "input_version_conflict" for e in store.store.events())
    finally:
        store.close()


def test_read_set_check_semantics():
    assert check_read_set({"a": 1}, {"a": 1}) == {}
    assert check_read_set({"a": 1}, {"a": 2}) == {"a": (1, 2)}
    # 依据里出现但当前没有的对象不算过期 (可能是新建对象)
    assert check_read_set({"b": 1}, {"a": 5}) == {}
    # 当前版本更低 (回退场景) 不算过期
    assert check_read_set({"a": 3}, {"a": 2}) == {}


def test_commit_result_is_idempotent_and_checks_versions(tmp_path):
    from src.research.store import StepAlreadyApplied

    store = _task_store(tmp_path, "commit")
    try:
        task = _task("提交一次", agent="reasoning")
        store.create(task)
        result = AgentResult(task_id=task.task_id, agent="reasoning",
                             outcome=TaskOutcome.completed, summary="ok",
                             input_versions={"claim-1": 1})
        first = store.commit_result(task, result, current_versions={"claim-1": 1})
        assert first["read_set_checked"] is True
        with pytest.raises(StepAlreadyApplied):
            store.commit_result(task, result, current_versions={"claim-1": 1})
    finally:
        store.close()


def test_commit_result_without_versions_is_marked_unchecked(tmp_path):
    store = _task_store(tmp_path, "nocheck")
    try:
        task = _task("无版本信息", agent="writing")
        store.create(task)
        result = AgentResult(task_id=task.task_id, agent="writing",
                             outcome=TaskOutcome.completed, summary="ok")
        outcome = store.commit_result(task, result)
        assert outcome["read_set_checked"] is False   # 如实标注, 不假装检查过
    finally:
        store.close()


def test_agent_runs_are_recorded_per_attempt(tmp_path):
    from src.research.task_store import new_agent_run

    store = _task_store(tmp_path, "runs")
    try:
        task = _task("会重试的事", agent="evidence")
        store.create(task)
        store.record_run(new_agent_run(task, attempt=1))
        store.record_run(new_agent_run(task, attempt=2))
        runs = store.runs(task.task_id)
        assert len(runs) == 2
        assert {r["attempt"] for r in runs} == {1, 2}
        # 同一 run 重复登记是幂等的
        store.record_run(new_agent_run(task, attempt=2))
        assert len(store.runs(task.task_id)) == 2
    finally:
        store.close()


def test_task_store_status_update_uses_optimistic_revision(tmp_path):
    store = _task_store(tmp_path, "status")
    try:
        task = _task("改状态", agent="figures")
        store.create(task)
        store.update_status(task.task_id, TaskStatus.running, note="开始")
        assert store.task(task.task_id)["status"] == TaskStatus.running.value
        assert store.task(task.task_id)["status_note"] == "开始"
        store.cancel(task.task_id, "用户取消")
        assert store.task(task.task_id)["status"] == TaskStatus.cancelled.value
    finally:
        store.close()
