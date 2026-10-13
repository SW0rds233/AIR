from inspect import signature
from types import SimpleNamespace

from src.agents.protocol import AgentResult, AgentTask, TaskOutcome
from src.agents.supervisor import (
    DecisionKind,
    ResearchBrief,
    SupervisorDecision,
    TeamPlan,
)
from src.graph.research_graph import (
    TeamLoopState,
    TeamRun,
    TeamRunOutcome,
    rebuild_loop_state,
)


def test_default_review_revision_limit_is_three():
    assert signature(TeamRun).parameters["max_review_revisions"].default == 3


def test_resume_rebuilds_revision_count_from_task_history():
    brief = ResearchBrief(project_id="p", problem_id="q", main_question="研究问题")
    plan = TeamPlan(brief_id=brief.brief_id)
    results = {}
    for index in range(3):
        task = AgentTask(agent="writing", objective=f"审稿修订 {index}",
                         hints={"review_cycle": True})
        plan.add_task(task)
        results[task.task_id] = AgentResult(
            task_id=task.task_id, agent="writing", outcome=TaskOutcome.completed)
    restored = rebuild_loop_state({
        "brief": brief.to_dict(), "plan": plan.to_dict(),
        "results": {task_id: result.to_dict() for task_id, result in results.items()},
    })
    assert restored is not None
    assert restored.review_revisions == 3


def test_resume_preserves_review_order_for_delivery_gate():
    brief = ResearchBrief(project_id="p", problem_id="q", main_question="研究问题")
    plan = TeamPlan(brief_id=brief.brief_id)
    writer = AgentTask(agent="writing", objective="修订", hints={"review_cycle": True})
    reviewer = AgentTask(agent="review", objective="复审")
    for task in (writer, reviewer):
        plan.add_task(task)
    results = {task.task_id: AgentResult(task_id=task.task_id, agent=task.agent,
                                       outcome=TaskOutcome.completed).to_dict()
               for task in (writer, reviewer)}
    payload = {
        "brief": brief.to_dict(), "plan": plan.to_dict(), "results": results,
        "task_order": [writer.task_id, reviewer.task_id], "max_review_revisions": 3,
    }
    run = TeamRun.__new__(TeamRun)
    run._load_run_payload = lambda: payload
    run.max_review_revisions = 3
    run.review_threshold = 80
    run.outcome = TeamRunOutcome(run_id="r")
    assert run.restore_state()
    assert run.outcome.task_order == [writer.task_id, reviewer.task_id]
    assert run.loop.review_revisions == 1


def test_team_stops_before_fourth_review_requested_revision():
    brief = ResearchBrief(project_id="p", problem_id="q", main_question="研究星网对渔业的影响")
    plan = TeamPlan(brief_id=brief.brief_id)
    results = {}

    def finished_task(agent: str, index: int, *, revision: bool = False) -> None:
        task = AgentTask(agent=agent, objective=f"{agent}-{index}",
                         project_id="p", problem_id="q", run_id="r",
                         hints={"review_cycle": True} if revision else {})
        plan.add_task(task)
        results[task.task_id] = AgentResult(
            task_id=task.task_id, agent=agent, outcome=TaskOutcome.completed,
            summary="仍需修改" if agent == "review" else "稿件已写出",
            payload={"accepted": False, "decision": "major_revision"}
            if agent == "review" else {},
        )

    finished_task("writing", 0)
    for index in range(1, 4):
        finished_task("review", index)
        finished_task("writing", index, revision=True)
    finished_task("review", 4)

    next_revision = AgentTask(agent="writing", objective="按审稿意见继续修订",
                              project_id="p", problem_id="q", run_id="r",
                              hints={"review_cycle": True})
    decision = SupervisorDecision(decision=DecisionKind.dispatch,
                                  tasks=[next_revision], reason="审稿未通过")
    executed = []
    run = TeamRun.__new__(TeamRun)
    run.prepare = lambda: None
    run.loop = TeamLoopState(brief=brief, plan=plan, results=results)
    run.outcome = TeamRunOutcome(run_id="r")
    run.cancel = SimpleNamespace(cancelled=False)
    run.runtime = SimpleNamespace(run_budget=None, emit=lambda *_: None)
    run.supervisor = SimpleNamespace(
        decide=lambda **_: decision,
        adopt=lambda p, t: p.add_task(t),
        replan=lambda p, *_args, **_kwargs: p,
    )
    run.max_rounds = 30
    run.max_review_revisions = 3
    run._execute_one = lambda task, *_: (
        executed.append(task.task_id),
        AgentResult(task_id=task.task_id, agent=task.agent,
                    outcome=TaskOutcome.completed, summary="稿件已写出"),
    )[1]
    run._refresh_retry_allowance = lambda *_: None
    run.save_state = lambda **_: True

    def finish(state, reason, status=""):
        state.finished = True
        run.outcome.status = status
        run.outcome.stop_reason = reason

    run._finish = finish

    assert run.step() is False
    assert not executed
    assert run.outcome.status == "partial"
    assert "3" in run.outcome.stop_reason
