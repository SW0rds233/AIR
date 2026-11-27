from queue import Queue

import pytest

from src.utils import network_budget as net


def test_wakeup_discards_queued_reply_even_if_task_clock_paused(monkeypatch):
    ticks = [100.0]
    events = []
    class PausedQueue(Queue):
        def get(self, *args, **kwargs):
            reply = super().get(*args, **kwargs)
            ticks[0] += 11573
            return reply

    monkeypatch.setattr(net, "Queue", PausedQueue)
    monkeypatch.setattr(net, "_watchdog_clock", lambda: ticks[0], raising=False)
    budget = net.NetworkBudget(seconds=600, emit=lambda k, p: events.append((k, p)))
    with net.task_network_budget(budget), pytest.raises(net.TaskDeadlineExceeded):
        net.model_call(lambda: "stale draft", timeout=120, phase="llm")
    assert budget.poll_interrupted and budget.deferred
    assert budget.model_attempts == 1 and not budget.state.failures
    assert ("task_watchdog_gap", 11573) in [(k, p.get("gap_seconds")) for k, p in events]


def test_evidence_allowance_is_rechecked_between_tasks_in_one_dispatch(tmp_path, monkeypatch):
    from src.agents.protocol import AgentResult, AgentTask
    from src.agents.supervisor import ResearchBrief, SupervisorAgent, SupervisorDecision, TeamPlan
    from src.graph.research_graph import TeamLoopState, TeamRun
    from src.research.task_store import TaskStore

    store = TaskStore("p", db_path=str(tmp_path / "team.sqlite"))
    try:
        run = TeamRun(project_id="p", problem_id="q", run_id="r", task_store=store,
                      supervisor=SupervisorAgent(), max_rounds=24)
        tasks = [AgentTask(agent="evidence", objective=s) for s in ("first", "bulk", "metadata")]
        tasks[-1].hints.update(root_cause_kind="citation", repair_action="verify_source", metadata_only=True)
        run.loop = TeamLoopState(brief=ResearchBrief(main_question="test"), plan=TeamPlan(
            tasks=[t.model_dump(mode="json") for t in tasks], edges={t.task_id: [] for t in tasks}))
        monkeypatch.setattr(run, "prepare", lambda: run.loop.brief)
        monkeypatch.setattr(run.supervisor, "decide", lambda **kw:
                            SupervisorDecision(decision="dispatch", tasks=tasks))
        monkeypatch.setattr(run, "_recheck_evidence", lambda *args: None)
        calls = []
        def execute(task, _results):
            calls.append(task.objective)
            run.runtime.network_state.evidence_model_attempts = 150
            return AgentResult(task_id=task.task_id, agent="evidence", outcome="partial")
        monkeypatch.setattr(run, "_execute_one", execute)
        assert run.step()
        assert calls == ["first", "metadata"]
    finally:
        store.close()


def test_final_delivery_event_updates_the_run_progress_without_losing_task_details(tmp_path, monkeypatch):
    import json
    from src.research import progress

    root = tmp_path / "progress"
    root.mkdir()
    monkeypatch.setattr(progress, "progress_dir", lambda *args: root)
    identity = root / "progress.json"
    identity.write_text('{"latest_task": {"task_id": "writer"}}', encoding="utf-8")
    delivery = {"phase": "after_export", "compilation_status": "ok", "accepted": False,
                "snapshot_id": "snap-one", "run_status": "partial"}
    progress.append_run_event(project_id="p", problem_id="q", run_id="r",
                              kind="delivery_assessed", payload=delivery)
    report = json.loads(identity.read_text("utf-8"))
    assert report["latest_task"] == {"task_id": "writer"}
    assert report["stage"] == "exported" and report["delivery"] == delivery
