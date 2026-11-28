import json
from types import SimpleNamespace

from src.agents.protocol import AgentResult, AgentTask, ContextPack, UsageRecord
from src.agents.runtime import AgentRuntime
from src.graph.research_graph import TeamLoopState, TeamRunOutcome
from src.graph.team_session import TeamSession
from src.research.snapshot import snapshot_from_store
from src.research.store import ResearchStore


def test_partial_result_records_its_authors_diagnostics():
    runtime = AgentRuntime()
    task = AgentTask(task_id="w", agent="writing", objective="修订")
    result = AgentResult(task_id="w", agent="writing", outcome="partial",
                         summary="两处待核查", unresolved=["块 A 缺少出处", "块 B 仍引用旧版命题"])
    runtime.finalize(task, result)
    event = runtime.events[-1]["payload"]
    assert event["outcome"] == "partial" and event["problems"] == []
    assert event.get("summary") == result.summary and event.get("unresolved") == result.unresolved


def test_partial_revision_reports_pending_blocks_before_background_gaps(monkeypatch):
    from src.agents.writing import WritingAgent
    from src.graph.node_progress import describe_node
    from src.publication.schemas import Manuscript

    agent, runtime = WritingAgent(), AgentRuntime()
    manuscript = Manuscript(title="论文", revision_log=[{"unaddressed_blocks": ["blk-b", "blk-c"]}],
                            gaps=[{"detail": "其他未决项"}] * 6)
    monkeypatch.setattr(agent, "_patch_with_llm", lambda *args: (manuscript, ""))
    monkeypatch.setattr(agent, "audit_manuscript", lambda *args: [])
    task = AgentTask(task_id="revise", agent="writing", objective="修订", hints={"review_cycle": True})
    result = agent._run(task, ContextPack(objects={"manuscript": [manuscript.to_dict()]}),
                        runtime, UsageRecord(), [])
    runtime.finalize(task, result)
    event = runtime.events[-1]["payload"]
    assert event["outcome"] == "partial" and event["unresolved"][0] == "待修订段落：blk-b、blk-c"
    assert "blk-b、blk-c" in describe_node("research_team", {"event": "task_result", **event})


def test_export_records_actual_terminal_state_after_publication(tmp_path):
    outcome = TeamRunOutcome(status="partial", rounds=17, stop_reason="修订上限 3 次")
    session = TeamSession.__new__(TeamSession)
    session.team = SimpleNamespace(outcome=outcome, loop=TeamLoopState(finished=True, review_revisions=3),
                                   max_review_revisions=3, save_state=lambda **kw: None,
                                   runtime=SimpleNamespace(emit=lambda *args: None))
    (tmp_path / "manifest.json").write_text("{}", "utf-8")
    delivery = {"level": "条件性研究报告", "accepted": False, "blocking": ["审阅未通过"]}
    session._publish_final_delivery(delivery, package=tmp_path, compilation_status="ok")
    manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert manifest["stop_reason"] == outcome.stop_reason
    assert manifest["review_revisions"] == 3
    assert manifest["termination"]["status"] == "partial" and manifest["termination"]["rounds"] == 17
    assert manifest["delivery_accepted"] is False and manifest["termination"]["max_review_revisions"] == 3


def test_snapshot_retains_registered_routes_and_counterexample_gaps(tmp_path):
    scope = {"run_id": "r", "problem_id": "q"}
    store = ResearchStore("p", tmp_path / "snapshot.sqlite")
    try:
        store.put("gap", "gap-1", {"id": "gap-1", "type": "unresolved_claim", "_scope": scope,
                                   "statement": "条件改变时可能不成立", "witness": "成本随之增加"})
        store.put("route", "rte-1", {"id": "rte-1", "goal": "验证条件", "_scope": scope})
        store.put("route", "rte-2", {"id": "rte-2", "_scope": {"problem_id": "other"}})
        snapshot = snapshot_from_store(store, project_id="p", problem_id="q", run_id="r")
        assert [route.id for route in snapshot.routes] == ["rte-1"]
        gap, = snapshot.gaps
        assert gap.id == "gap-1" and gap.gap_type.value == "unresolved_claim"
        assert "条件改变时可能不成立" in gap.statement and "成本随之增加" in gap.statement
        assert gap.target_ref.id == "q"
    finally:
        store.close()
