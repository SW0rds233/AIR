"""默认可外搜不代表显式离线运行可以暗中触网。"""

from src.kb import bridge
from src.research.schemas import RetrievalCoverage, SourcePolicy


def test_explicit_offline_prevents_uninjected_external_search(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setattr(bridge, "_default_search_fn",
                        lambda: (_ for _ in ()).throw(AssertionError("不应触网")))
    coverage = RetrievalCoverage(policy=SourcePolicy.both)
    bridge._harvest_external(["Bruck Ryser"], SourcePolicy.both, coverage,
                             topic="test", search_fn=None, per_query=2, ingest=False)
    assert coverage.executed is False
    assert "离线" in coverage.failures[0]


def test_injected_search_remains_testable_offline(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "0")
    called = []
    coverage = RetrievalCoverage(policy=SourcePolicy.autonomous)
    bridge._harvest_external(["test query"], SourcePolicy.autonomous, coverage,
                             topic="", search_fn=lambda query, limit: called.append(query) or [],
                             per_query=2, ingest=False)
    assert called == ["test query"]
    assert coverage.executed is True
