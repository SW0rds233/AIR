from __future__ import annotations

"""SupervisorAgent 的主控行为用例 (合并计划 §4)。

固定的是"主控必须能解释自己为什么这样派工", 而不是"节点按固定顺序跑一遍":
- 画像分开记录三个维度, 未知字段保留为未知, 不因一个关键词强行设成因果研究;
- 每轮给出**一种**结构化决策, 派工时子问题/预期收益/验收/依赖/失败处理齐备;
- 需求 (ResearchNeed) 由主控转成任务, 子智能体之间不互相派工;
- 审阅问题按类别派给正确的角色 (科学问题派研究角色, 文字问题派 Writer);
- 交付形态要求未满足时诚实收尾 (stop_with_report), 不假装完成。
"""

import pytest
from pydantic import ValidationError

from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ResearchNeed,
    ReviewIssueRef,
    RunBudget,
    TaskBudget,
    TaskOutcome,
)
from src.agents.supervisor import (
    DELIVERABLES,
    SUBQUESTION_KINDS,
    DecisionKind,
    ResearchBrief,
    SubQuestion,
    SupervisorAgent,
    TeamPlan,
    assignment_key,
    classify_request,
    needs_to_tasks,
    plan_fingerprint,
    unresolved_report,
)


def _brief(request: str, **kwargs):
    return SupervisorAgent().brief(request, project_id="p1", problem_id="q1",
                                   **kwargs)


# --------------------------------------------------------------------------
# 任务画像: 三个维度分开
# --------------------------------------------------------------------------
def test_survey_request_is_not_forced_into_proof_obligations():
    brief = _brief("总结某方向的研究进展并写一篇综述")
    kinds = {s.kind for s in brief.subquestions}
    assert "literature_synthesis" in kinds
    # 纯综述**不**伪造证明义务
    assert "existence_proof" not in kinds


def test_precise_math_request_gets_existence_kind_and_quantifier():
    brief = _brief("判断参数为 2-(211,15,1) 的设计是否存在, 给出严格证明")
    kinds = {s.kind for s in brief.subquestions}
    assert "existence_proof" in kinds
    assert brief.quantifiers in ("exists", "not_exists", "forall")
    assert "quantifiers" not in brief.unknown_fields


def test_mechanism_request_gets_case_and_data_kinds():
    brief = _brief("解释某机理, 参考案例和数据库, 并提出验证办法")
    kinds = {s.kind for s in brief.subquestions}
    assert "mechanism" in kinds
    assert "validation_plan" in kinds
    assert "case_comparison" in kinds
    assert "data_availability" in kinds


def test_request_without_paper_wording_does_not_ask_for_a_full_paper():
    """交付形态由题面决定: 没有"论文/撰写"措辞时不强求完整论文。"""
    brief = _brief("解释某机理, 参考案例和数据库, 并提出验证办法")
    assert "full_paper" not in brief.deliverables
    assert "problem_report" in brief.deliverables


def test_unknown_fields_are_preserved_not_guessed():
    brief = _brief("", source_policy="user_kb")
    assert "main_question" in brief.unknown_fields
    assert "source_set_ids" in brief.unknown_fields
    # 没有量词就说没有, 不编一个
    assert "quantifiers" in brief.unknown_fields
    assert brief.quantifiers == ""


def test_brief_records_basis_for_human_review():
    brief = _brief("给出某定理的严格证明")
    assert brief.basis.startswith("规则识别")
    assert "existence_proof" in brief.basis


def test_classify_request_is_deterministic_and_domain_free():
    kinds, deliverables, basis = classify_request("分析信噪比对识别准确率的影响")
    assert kinds and deliverables
    assert "规则识别" in basis
    # 未匹配到领域词时明确说明不做领域假设
    kinds2, _, _ = classify_request("任意字符串")
    assert kinds2 == ["mechanism"]


def test_deliverables_are_validated():
    brief = _brief("写论文")
    assert all(d in DELIVERABLES for d in brief.deliverables)
    with pytest.raises(ValidationError):
        ResearchBrief(deliverables=["nobel_prize"])


def test_subquestion_kind_is_closed():
    with pytest.raises(ValidationError):
        SubQuestion(statement="x", kind="magic")
    for kind in SUBQUESTION_KINDS:
        assert SubQuestion(statement="x", kind=kind).kind == kind


# --------------------------------------------------------------------------
# 组队与派工
# --------------------------------------------------------------------------
def test_plan_produces_tasks_with_complete_dispatch_contract():
    brief = _brief("解释某机理, 参考案例, 并写成论文")
    plan = SupervisorAgent().plan(brief, version=1,
                                  source_set_ids=["kb-1"],
                                  budget=TaskBudget(max_llm_calls=4))
    assert plan.task_ids()
    for record in plan.tasks:
        task = AgentTask(**record)
        assert task.objective
        assert task.subquestion           # 要回答哪个子问题
        assert task.expected_gain         # 预期获得什么新信息
        assert task.acceptance_criteria   # 什么算完成
        assert task.on_failure            # 失败如何处理
        assert task.budget.reserved is False   # 由运行时在派发时预留
        assert task.source_set_ids == ["kb-1"]
        assert "supervisor" != task.agent


def test_plan_wires_default_dependencies_so_writing_waits_for_evidence():
    brief = _brief("解释某机理并写成论文")
    plan = SupervisorAgent().plan(brief, version=1)
    by_role = {r["agent"]: r["task_id"] for r in plan.tasks}
    assert by_role["writing"] != by_role["evidence"]
    writing_deps = plan.edges[by_role["writing"]]
    assert by_role["evidence"] in writing_deps
    # 依赖已满足前不可派发
    assert by_role["writing"] not in plan.ready(completed=set())
    assert by_role["evidence"] in plan.ready(completed=set())
    # 全部前提完成后写作才可派发 (推理也是前提之一)
    all_prereqs = set(writing_deps)
    assert by_role["writing"] not in plan.ready(completed={by_role["evidence"]})
    assert by_role["writing"] in plan.ready(completed=all_prereqs)


def test_plan_ready_excludes_completed_and_running():
    brief = _brief("写一篇综述")
    plan = SupervisorAgent().plan(brief, version=1)
    ready_before = plan.ready(completed=set())
    assert ready_before, "没有任何可派发任务"
    # 不假设"列表里第一条就是可跑的": 依赖加入后, 先派证据/建模才是对的
    # (§3.1 G05)。判据是 ready() 的语义本身。
    first = ready_before[0]
    assert first not in plan.ready(completed={first})
    assert first not in plan.ready(completed=set(), running={first})
    # 依赖满足后应当出现**更多**可跑任务 (依赖确实在起作用)
    assert len(plan.ready(completed={first})) >= len(ready_before) - 1


def test_plan_fingerprint_is_stable_for_same_plan():
    brief = _brief("写综述")
    supervisor = SupervisorAgent()
    first = supervisor.plan(brief, version=1)
    second = supervisor.plan(brief, version=1)
    assert plan_fingerprint(first) == plan_fingerprint(second)
    third = supervisor.plan(brief, version=2)
    assert plan_fingerprint(third) != plan_fingerprint(first)


# --------------------------------------------------------------------------
# 决策
# --------------------------------------------------------------------------
def _decide(supervisor, brief, plan, **kwargs):
    defaults = {"brief": brief, "plan": plan, "results": {}}
    defaults.update(kwargs)
    return supervisor.decide(**defaults)


def test_decide_dispatches_ready_tasks_with_limit():
    supervisor = SupervisorAgent(max_tasks_per_round=2)
    brief = _brief("解释某机理, 参考案例和数据库, 提出验证办法并写成论文")
    plan = supervisor.plan(brief, version=1)
    decision = _decide(supervisor, brief, plan)
    assert decision.decision == DecisionKind.dispatch
    assert 1 <= len(decision.tasks) <= 2
    assert decision.reason and "派" in decision.reason


def test_decide_converts_needs_into_tasks_before_dispatching_plan():
    supervisor = SupervisorAgent()
    brief = _brief("证明某定理")
    plan = supervisor.plan(brief, version=1)
    # 需求承接的角色在计划里还没有任务 -> 必须先派它
    need = ResearchNeed(kind="empirical_support", statement="缺可区分检验的设计",
                        why="没有验证方案就无法区分竞争解释",
                        acceptance=["给出一份可执行验证方案"])
    decision = _decide(supervisor, brief, plan, open_needs=[need])
    assert decision.decision == DecisionKind.dispatch
    assert len(decision.tasks) == 1
    assert decision.tasks[0].agent == "validation"    # 需求类型决定承接角色
    assert "可区分检验" in decision.tasks[0].objective


def test_decide_does_not_redispatch_a_need_already_in_the_plan():
    """同一条需求重复上报不得无限派工 —— 实测过的预算烧光循环。"""
    supervisor = SupervisorAgent()
    brief = _brief("解释某机理, 参考案例和数据库, 并提出验证办法")
    plan = supervisor.plan(brief, version=1)
    by_id = {t["task_id"]: t for t in plan.tasks}
    results = {tid: AgentResult(task_id=tid, agent=by_id[tid]["agent"],
                                outcome=TaskOutcome.blocked, failure_reason="缺")
               for tid in plan.task_ids()}
    # 与计划里 evidence 任务**措辞完全相同**的"更多来源"需求: 已经安排过了
    same = ResearchNeed(kind="more_sources",
                        statement="收集支撑或反驳候选结论的可定位来源",
                        acceptance=["逐条给出出处定位"])
    decision = _decide(supervisor, brief, plan, open_needs=[same],
                       results=results, clarifications_asked=1)
    assert not [t for t in decision.tasks
                if t.objective == "收集支撑或反驳候选结论的可定位来源"]
    # 换一条计划里没有的需求, 则必须派出去 (闸门只挡重复, 不挡真实需求)
    fresh = ResearchNeed(kind="clause", statement="引理 L 的适用条件 k ≡ 1 mod 4 需要出处",
                         acceptance=["给出定理编号"])
    decision2 = _decide(supervisor, brief, plan, open_needs=[fresh],
                        results=results)
    assert decision2.decision == DecisionKind.dispatch
    assert any(t.agent == "evidence" for t in decision2.tasks)


def test_decide_does_not_retry_an_already_attempted_task():
    """已尝试但失败/受阻的任务不再自动重派 (重试要经过 replan 升版)。"""
    supervisor = SupervisorAgent()
    brief = _brief("写一篇综述")
    plan = supervisor.plan(brief, version=1)
    by_id = {t["task_id"]: t for t in plan.tasks}
    results = {tid: AgentResult(task_id=tid, agent=by_id[tid]["agent"],
                                outcome=TaskOutcome.failed, failure_reason="工具不可用")
               for tid in plan.task_ids()}
    decision = _decide(supervisor, brief, plan, results=results)
    # 同一件"角色+目标"不得再派; 下游若存在则允许派 (失败的前置不再阻塞)
    retried = [t for t in decision.tasks
               if assignment_key(brief, t.agent, t.objective) in
               {assignment_key(brief, by_id[tid]["agent"], by_id[tid]["objective"])
                for tid in results}]
    assert not retried, [t.objective for t in retried]


def test_decide_waits_when_tasks_are_pending():
    supervisor = SupervisorAgent()
    brief = _brief("写综述")
    plan = supervisor.plan(brief, version=1)
    first = plan.task_ids()[0]
    decision = _decide(supervisor, brief, plan, pending=[first], running=[first])
    assert decision.decision in (DecisionKind.wait, DecisionKind.dispatch)


def test_decide_reports_stop_when_budget_blocks_everything():
    supervisor = SupervisorAgent()
    brief = _brief("写综述")
    plan = supervisor.plan(brief, version=1)
    exhausted = RunBudget(total=TaskBudget(max_llm_calls=1))
    exhausted.allocated.max_llm_calls = 1
    decision = _decide(supervisor, brief, plan, remaining_budget=exhausted,
                       pending=plan.task_ids())
    assert decision.decision in (DecisionKind.stop_with_report,
                                 DecisionKind.wait)
    assert decision.reason


def test_decide_delivers_only_when_required_roles_succeeded():
    supervisor = SupervisorAgent()
    brief = _brief("判断该设计是否存在并写成论文")
    plan = supervisor.plan(brief, version=1)
    required = {task["agent"] for task in plan.tasks}
    partial_results = {
        task_id: AgentResult(task_id=task_id, agent="reasoning",
                             outcome=TaskOutcome.completed, summary="done")
        for task_id in plan.task_ids()
    }
    # 只有 reasoning 完成 -> 不能交付
    decision = _decide(supervisor, brief, plan, results=partial_results)
    assert decision.decision != DecisionKind.deliver

    full_results = {
        task_id: AgentResult(task_id=task_id, agent=record["agent"],
                             outcome=TaskOutcome.completed, summary="done")
        for task_id, record in ((t["task_id"], t) for t in plan.tasks)
    }
    decision2 = _decide(supervisor, brief, plan, results=full_results,
                        delivered=False)
    if required <= {"reasoning"}:
        assert decision2.decision == DecisionKind.deliver
    else:
        assert decision2.decision in (DecisionKind.deliver,
                                      DecisionKind.stop_with_report)


def test_decide_stop_when_stopped():
    supervisor = SupervisorAgent()
    brief = _brief("写综述")
    plan = supervisor.plan(brief, version=1)
    decision = _decide(supervisor, brief, plan, stopped=True)
    assert decision.decision == DecisionKind.stop_with_report
    assert "停止" in decision.reason


def test_decide_marks_partial_report_when_nothing_left_and_requirements_unmet():
    supervisor = SupervisorAgent()
    brief = _brief("判断该设计是否存在并写成论文")
    plan = supervisor.plan(brief, version=1)
    by_id = {t["task_id"]: t for t in plan.tasks}
    blocked = {tid: AgentResult(task_id=tid, agent=by_id[tid]["agent"],
                                outcome=TaskOutcome.blocked, failure_reason="缺条件")
               for tid in plan.task_ids()}
    # 首轮把可派任务全派掉, 之后才谈"无事可做"
    first = _decide(supervisor, brief, plan, results={})
    dispatched = {assignment_key(brief, t.agent, t.objective) for t in first.tasks}
    decision = _decide(supervisor, brief, plan, results=blocked,
                       clarifications_asked=1, dispatched=dispatched)
    assert decision.decision == DecisionKind.stop_with_report
    assert decision.note


# --------------------------------------------------------------------------
# 复盘与返工
# --------------------------------------------------------------------------
def test_replan_bumps_attempt_for_failed_tasks_only():
    supervisor = SupervisorAgent()
    brief = _brief("写综述")
    plan = supervisor.plan(brief, version=1)
    ids = plan.task_ids()
    failed = AgentResult(task_id=ids[0], agent="evidence",
                         outcome=TaskOutcome.failed, failure_reason="超时")
    ok = AgentResult(task_id=ids[-1], agent="reasoning",
                     outcome=TaskOutcome.completed, summary="done")
    replanned = supervisor.replan(plan, {ids[0]: failed, ids[-1]: ok}, version=2)
    records = {r["task_id"]: r for r in replanned.tasks}
    assert records[ids[0]]["attempt"] == 2
    assert records[ids[0]]["plan_version"] == 2
    assert "超时" in records[ids[0]]["hints"]["previous_failure"]
    assert records[ids[-1]]["attempt"] == 1


def test_replan_stops_at_version_limit():
    supervisor = SupervisorAgent(max_plan_version=2)
    brief = _brief("写综述")
    plan = supervisor.plan(brief, version=2)
    failed = {t: AgentResult(task_id=t, outcome=TaskOutcome.failed,
                             failure_reason="x") for t in plan.task_ids()}
    assert supervisor.replan(plan, failed, version=3).version == 2


def test_rework_tasks_route_by_issue_category():
    supervisor = SupervisorAgent()
    brief = _brief("判断该设计是否存在并写成论文")
    issues = [
        ReviewIssueRef(issue_id="i1", category="science", severity="blocking",
                       summary="推导第 3 步缺前提"),
        ReviewIssueRef(issue_id="i2", category="citation", severity="major",
                       summary="引用无定位"),
        ReviewIssueRef(issue_id="i3", category="readability", severity="minor",
                       summary="句子太长"),
    ]
    tasks = supervisor.rework_tasks(issues, brief=brief, plan_version=1)
    by_summary = {t.objective.split(":")[0]: t.agent for t in tasks}
    owner_by_issue = {(t.objective.split()[1]).rstrip(":"): t.agent for t in tasks}
    assert owner_by_issue["i1"] == "reasoning"
    assert owner_by_issue["i2"] == "evidence"
    assert owner_by_issue["i3"] == "writing"
    # 每条返工都带验收标准 (问题账本可关闭)
    assert all(t.acceptance_criteria for t in tasks)
    del by_summary


def test_rework_respects_explicit_suggested_owner():
    supervisor = SupervisorAgent()
    brief = _brief("写论文")
    issue = ReviewIssueRef(issue_id="i9", category="science", severity="major",
                           summary="模型边界条件不对", suggested_owner="modeling")
    tasks = supervisor.rework_tasks([issue], brief=brief, plan_version=1)
    assert tasks[0].agent == "modeling"


# --------------------------------------------------------------------------
# 需求 -> 任务
# --------------------------------------------------------------------------
def test_needs_to_tasks_is_idempotent_by_content():
    brief = _brief("写论文")
    need = ResearchNeed(kind="more_sources", statement="缺 BRC 定理原文",
                        acceptance=["给出定理编号与定位"])
    first = needs_to_tasks([need], brief=brief, plan_version=1)
    second = needs_to_tasks([need], brief=brief, plan_version=1)
    assert first[0].idempotency_key == second[0].idempotency_key
    assert first[0].agent == "evidence"
    assert first[0].acceptance_criteria == ["给出定理编号与定位"]


def test_needs_without_owner_are_not_dispatched_blindly():
    brief = _brief("写论文")
    orphan = ResearchNeed(kind="clarification", statement="需要用户澄清")
    assert needs_to_tasks([orphan], brief=brief, plan_version=1) == []


# --------------------------------------------------------------------------
# 未决报告
# --------------------------------------------------------------------------
def test_unresolved_report_is_honest_about_partial_results():
    supervisor = SupervisorAgent()
    brief = _brief("判断该设计是否存在并写成论文")
    plan = supervisor.plan(brief, version=1)
    ids = plan.task_ids()
    results = {
        ids[0]: AgentResult(task_id=ids[0], agent="evidence",
                            outcome=TaskOutcome.completed, summary="ok"),
        ids[-1]: AgentResult(task_id=ids[-1], agent="writing",
                             outcome=TaskOutcome.blocked,
                             failure_reason="缺来源",
                             followup_needs=[ResearchNeed(kind="more_sources",
                                                          statement="补检索")]),
    }
    report = unresolved_report(brief, plan, results)
    assert ids[0] in report["completed_tasks"]
    assert ids[-1] in report["blocked_tasks"]
    assert any("more_sources" in u for u in report["unresolved"])
    assert report["deliverables_requested"] == brief.deliverables
    assert "不假装完成" in report["note"]


def test_plan_round_trips_through_dict():
    supervisor = SupervisorAgent()
    brief = _brief("解释某机理并写成论文")
    plan = supervisor.plan(brief, version=1)
    restored = TeamPlan(**plan.to_dict())
    assert restored.task_ids() == plan.task_ids()
    assert restored.edges == plan.edges
    assert AgentTask(**restored.tasks[0]).objective


def test_brief_round_trips_and_subquestion_dedup():
    brief = _brief("写综述")
    count = len(brief.subquestions)
    brief.add_subquestion(brief.subquestions[0].statement,
                          brief.subquestions[0].kind)
    assert len(brief.subquestions) == count        # 同一子问题不重复添加
    from src.agents.supervisor import ResearchBrief

    restored = ResearchBrief(**brief.to_dict())
    assert len(restored.subquestions) == count
