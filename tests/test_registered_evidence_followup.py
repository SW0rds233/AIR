import json
from types import SimpleNamespace

from src.agents.evidence import EvidenceAgent
from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.agents.reasoning import ReasoningAgent
from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan


def test_claim_created_after_retrieval_reassesses_registered_excerpt(monkeypatch):
    from src.research.snapshot import _evidence_of
    from src.research.schemas import Claim, ProofObligation
    from src.research.verification_service import _rule_verdict

    quote = "The vessel terminal costs ranged from 900 to 1200 USD."
    condition = "渔船侧终端成本取值依据"
    source = {"id": "doc-price", "source_id": "doc-price", "title": "Vessel terminal costs",
              "locator": "Table 1", "location": "obsolete page", "excerpt": quote, "content_level": "fulltext",
              "support": "supports", "relation": "background", "support_reason": "old verdict", "claim_id": ""}
    ctx = ContextPack(request="如何理解渔船的采用决策", objects={"evidence": [source]})
    candidate = {"claim_type": "descriptive", "statement": "固定覆盖时，终端成本影响采纳阈值。",
                 "evidence_requirements": [condition]}
    reasoner = ReasoningAgent()
    monkeypatch.setattr(reasoner, "tool_loop", lambda *a, **k: (json.dumps({"claims": [candidate]}), []))
    runtime = SimpleNamespace(llm_available=lambda *a: True, emit=lambda *a: None)
    result = reasoner._run(AgentTask(agent="reasoning", objective="提出条件命题"),
                           ctx, runtime, UsageRecord(), [])
    claim = next(p.payload for p in result.proposed_changes if p.kind == "claim")
    followup = next(n for n in result.followup_needs if n.hints.get("assess_registered"))
    plan = TeamPlan(tasks=[{"task_id": "write", "agent": "writing", "objective": "写论文", "depends_on": []}])
    decision = SupervisorAgent().decide(brief=ResearchBrief(main_question=ctx.request), plan=plan,
                                        open_needs=[followup])
    task, = decision.tasks
    assert task.hints["claim_id"] == claim["id"] and task.hints["claim_version"] == 1
    ctx.objects["claim"] = [claim]
    judge = {"relation": "partially_supports", "support_scope": "parameter_assumption",
             "supported_target": condition, "condition_notes": ["仅限所观察船型"],
             "reason": "实测费用提供同类终端的费用范围", "quote": quote}
    runtime.llm = lambda *a, **k: SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(judge)))
    evidence = EvidenceAgent()
    monkeypatch.setattr(evidence, "retrieve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("external search")))
    checked = evidence._run(task, ctx, runtime, UsageRecord(), [])
    proposal, = [p for p in checked.proposed_changes if p.kind == "evidence"]
    assert proposal.object_id != source["id"] and proposal.payload["source_id"] == source["id"]
    assert proposal.payload["claim_id"] == claim["id"] and proposal.payload["claim_version"] == 1
    assert proposal.payload["support_evidence"] == quote
    assert proposal.payload["support_scope"] == "parameter_assumption"
    projected = _evidence_of(proposal.payload)
    assert projected.support.value == proposal.payload["relation"] == "partially_supports"
    assert projected.location == "Table 1" and projected.support_reason == judge["reason"]
    duty = ProofObligation(kind="evidence_support", claim_id=claim["id"], statement="检验整条命题")
    assert not _rule_verdict(Claim.model_validate(claim), duty, [projected])[0]
    assert next(p.payload for p in checked.proposed_changes if p.kind == "evidence_link")["relation"] == "background"
    assert source["claim_id"] == ""
    ctx.objects["evidence"].append(proposal.payload)
    repeated = evidence._run(task, ctx, runtime, UsageRecord(), [])
    assert repeated.outcome.value == "completed" and not repeated.proposed_changes
    ctx.objects["claim"][0] = {**claim, "version": 2}
    assert not evidence._run(task, ctx, runtime, UsageRecord(), []).proposed_changes


def test_committed_assessment_visible_after_many_initial_hits(tmp_path):
    from src.graph.research_graph import TeamRun
    from src.research.projection import TeamProjection
    from src.research.store import ResearchStore

    store = ResearchStore("p", tmp_path / "sources.sqlite")
    try:
        scope = {"run_id": "run"}
        store.put("claim", "clm", {"id": "clm", "statement": "待核对参数", "version": 2, "_scope": scope})
        for i in range(51):
            store.put("evidence", f"doc-{i}", {"id": f"doc-{i}", "source_id": f"doc-{i}", "_scope": scope})
        store.put("evidence", "assessment", {"id": "assessment", "source_id": "doc-50", "claim_id": "clm",
                                           "claim_version": 2, "_scope": scope})
        run = TeamRun.__new__(TeamRun)
        run.run_id = "run"
        run.projection = TeamProjection(store, run_id="run")
        run._source_set_rows = []
        run._cached_default_builder = None
        run.outcome = SimpleNamespace(brief=None)
        run.request = ""
        run.attachments = []
        pack = run._default_context_builder()(AgentTask(agent="reasoning", objective="核验"), {})
        assert pack.objects["evidence"][0]["id"] == "assessment"
        assert len({row["source_id"] for row in pack.objects["evidence"]}) == 51
    finally:
        store.close()
