"""Query coverage and evidence ordering for mixed Chinese and English research."""

from src.kb.bridge import _harvest_external
from src.kb.publication import candidate_priority
from src.research.query_planner import choose_queries, plan_queries
from src.research.schemas import RetrievalCoverage, SourcePolicy


TERMS = {
    "钙钛矿太阳能电池": ["perovskite solar cell", "PSC"],
    "湿度": ["humidity"],
}


def test_chinese_question_plans_full_english_name_and_acronym_separately():
    planned = plan_queries(goal="研究钙钛矿太阳能电池的湿度稳定性", terminology=TERMS,
                           limit=5)
    assert any("钙钛矿太阳能电池" in row.text for row in planned)
    assert any(row.angle == "terminology" and "perovskite solar cell" in row.text
               and "PSC" not in row.text for row in planned)
    assert any(row.angle == "abbreviation" and "PSC" in row.text
               and "perovskite" in row.text for row in planned)


def test_english_synonyms_are_alternatives_not_conjunctive_requirements():
    planned = plan_queries(goal="研究射频指纹识别方法", limit=5)
    english = [row.text for row in planned if row.angle.startswith("terminology")]
    assert any("RF fingerprinting" in query for query in english)
    assert any("radio frequency fingerprinting" in query for query in english)
    assert all(not ("RF fingerprinting" in query
                    and "radio frequency fingerprinting" in query) for query in english)


def test_partial_term_proposal_keeps_registered_field_translations():
    planned = plan_queries(goal="研究钙钛矿太阳能电池的湿度稳定性",
                           terminology={"湿度": ["humidity"]}, limit=5)
    assert any("perovskite solar cell" in row.text for row in planned)


def test_model_queries_cannot_crowd_out_bilingual_coverage():
    planned = plan_queries(goal="研究钙钛矿太阳能电池的湿度稳定性", terminology=TERMS,
                           limit=4)
    selected = choose_queries(planned, [f"模型检索建议 {i}" for i in range(4)], limit=4)
    assert len(selected) == 4
    assert "模型检索建议 0" in selected
    assert any("钙钛矿太阳能电池" in query for query in selected)
    assert any("perovskite solar cell" in query for query in selected)
    assert any("PSC" in query for query in selected)


def test_model_term_proposal_can_extend_an_unrecognised_chinese_domain():
    from langchain_core.messages import AIMessage
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import AgentTask, ContextPack, UsageRecord
    from src.agents.runtime import AgentRuntime

    class Model:
        def invoke(self, _messages):
            return AIMessage(content='{"concepts":[{"name":"量子点","english":'
                             '"quantum dot (QD)","connection":"材料名称"}],"queries":[]}')

    question = "研究量子点薄膜的稳定性"
    plan = EvidenceAgent().plan_retrieval(
        AgentTask(agent="evidence", objective="检索", source_policy="both"),
        ContextPack(request=question), AgentRuntime(llm_factory=lambda _stage: Model()),
        UsageRecord())
    assert any("quantum dot" in row["text"] for row in plan["queries"])
    assert any("QD" in row["text"] for row in plan["queries"])
    assert any("量子点" in row["text"] for row in plan["queries"])


def test_recent_and_cited_papers_rank_higher_within_relevance_band():
    query = "perovskite humidity"
    base = {"title": "Perovskite humidity", "abstract": ""}
    old = {**base, "year": "2008", "citations": 8}
    recent = {**base, "year": "2025", "citations": 8}
    influential = {**base, "year": "2025", "citations": 250}
    unrelated = {"title": "Unrelated subject", "year": "2025", "citations": 100000}
    assert candidate_priority(influential, query, current_year=2026) > candidate_priority(
        recent, query, current_year=2026)
    assert candidate_priority(recent, query, current_year=2026) > candidate_priority(
        old, query, current_year=2026)
    assert candidate_priority(old, query, current_year=2026) > candidate_priority(
        unrelated, query, current_year=2026)


def test_external_harvest_applies_priority_before_per_query_cutoff(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    query = "perovskite humidity"
    rows = [
        {"title": "Perovskite humidity old", "abstract": query, "year": "2008",
         "citations": 8, "doi": "10.1/old"},
        {"title": "Perovskite humidity recent", "abstract": query, "year": "2025",
         "citations": 8, "doi": "10.1/recent"},
        {"title": "Perovskite humidity influential", "abstract": query,
         "year": "2025", "citations": 250, "doi": "10.1/influential"},
        {"title": "Unrelated topic", "abstract": "unrelated", "year": "2025",
         "citations": 100000, "doi": "10.1/unrelated"},
    ]
    coverage = RetrievalCoverage(policy=SourcePolicy.autonomous)
    items = _harvest_external([query], SourcePolicy.autonomous, coverage,
                              topic="ranking-fixture", search_fn=lambda _query, _limit: rows,
                              per_query=2, ingest=False)
    assert [item.title for item in items] == [rows[2]["title"], rows[1]["title"]]


def test_missing_translation_is_recorded_as_a_coverage_limit(tmp_path, monkeypatch):
    from src import config
    from src.kb.bridge import gather_sources
    from src.research.schemas import Claim

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _items, coverage = gather_sources(
        None, None, topic="unknown-chinese-domain", policy=SourcePolicy.autonomous,
        claim=Claim(statement="研究未知专有术语的性质"),
        search_fn=lambda _query, _limit: [], ingest=False)
    assert any("英文术语对照" in note for note in coverage.uncovered)
