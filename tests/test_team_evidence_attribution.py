from __future__ import annotations

"""团队检索的证据归属不能把一份材料误配给所有命题。"""

from src.agents.evidence import EvidenceAgent, _observed_searches
from src.agents.protocol import AgentTask, ContextPack, NeedKind, ResearchNeed
from src.agents.runtime import ToolObservation


def _task() -> AgentTask:
    return AgentTask(task_id="t-evidence", agent="evidence", objective="查找反例",
                     project_id="p", problem_id="p1", run_id="r1")


def _context() -> ContextPack:
    return ContextPack(objects={"claim": [
        {"id": "c1", "version": 2, "statement": "A"},
        {"id": "c2", "version": 1, "statement": "B"},
    ]})


def test_unscoped_source_never_supports_or_refutes_every_claim():
    links = EvidenceAgent()._evidence_links(
        _task(), [{"source_id": "s1", "locator": "p.2",
                   "relation": "contradicts"}], _context())
    assert len(links) == 2
    assert {link.payload["relation"] for link in links} == {"insufficient"}
    assert {link.payload["source_ref"]["id"] for link in links} == {"s1"}


def test_explicit_claim_binding_limits_contradiction_to_that_claim():
    links = EvidenceAgent()._evidence_links(
        _task(), [{"source_id": "s1", "claim_id": "c1", "locator": "p.2",
                   "relation": "contradicts"}], _context())
    assert len(links) == 1
    assert links[0].payload["claim_ref"] == {"id": "c1", "version": 2}
    assert links[0].payload["relation"] == "contradicts"


def test_distinct_contradictions_get_distinct_stable_followup_keys():
    common = dict(kind=NeedKind.counterexample, statement="复核冲突")
    first = ResearchNeed(**common, hints={"trigger_id": "contradiction:c1:2:s1"})
    repeat = ResearchNeed(**common, hints={"trigger_id": "contradiction:c1:2:s1"})
    second = ResearchNeed(**common, hints={"trigger_id": "contradiction:c1:2:s2"})
    assert first.assignment_key("p", "p1") == repeat.assignment_key("p", "p1")
    assert first.assignment_key("p", "p1") != second.assignment_key("p", "p1")


def test_model_selected_queries_enter_the_single_retrieval_path():
    from src.kb.bridge import gather_sources
    from src.research.schemas import Claim, SourcePolicy

    observations = [
        ToolObservation(name="search_all_sources", arguments={"query": "specific theorem"},
                        status="ok", result=[{"title": "Real hit", "doi": "10.1/x"}]),
        ToolObservation(name="search_all_sources", arguments={"query": "failed query"},
                        status="tool_error", result=[]),
    ]
    queries, cached = _observed_searches(observations, "autonomous")
    assert queries == ["specific theorem"]
    assert cached["specific theorem"][0]["title"] == "Real hit"
    local_queries, local_cache = _observed_searches(observations, "user_kb")
    assert local_queries == [] and local_cache == {}
    _, coverage = gather_sources(None, None, policy=SourcePolicy.user_kb,
                                 claim=Claim(statement="generic problem"),
                                 extra_queries=queries, max_queries=2)
    assert coverage.queries[0] == "specific theorem"
    assert "failed query" not in coverage.queries
