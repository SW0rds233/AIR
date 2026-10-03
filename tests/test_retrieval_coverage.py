from __future__ import annotations

"""P0-1 场景 ②/§1 契约第 2 行: 自主检索要留下可审查的覆盖记录。

用假检索函数与假知识服务验证"检索了哪些式、命中/入库多少、哪些只有摘要、
哪里失败、哪些范围没覆盖"; 不触网, 不依赖预建资料库。
"""

from src.kb.bridge import gather_sources
from src.kb.service import SearchOutcome, SourceRef
from src.research.question_planner import formulate
from src.research.schemas import RetrievalCoverage, SourcePolicy, SourceSummary

REQUEST = "分析信道变化对射频指纹可分性的影响, 并给出理论结论与仿真建议"


def _contract(summary: SourceSummary):
    return formulate(REQUEST, source_summary=summary)


PAPER_A = {"title": "Channel effects on RFF separability", "doi": "10.1/a",
           "abstract": "在高信噪比下", "year": "2021", "api_source": "arXiv"}
PAPER_B = {"title": "RFF under low SNR", "doi": "10.1/b",
           "abstract": "低信噪比下可分性下降", "year": "2022", "api_source": "OpenAlex"}


class FakeService:
    """只实现 gather_sources 用到的接口。"""

    def __init__(self, refs_by_query=None, has_fulltext=True, failures=None):
        self.topic = "rf-A"
        self.available = True
        self.usable = True
        self._refs = refs_by_query or {}
        self._has_fulltext = has_fulltext
        self._failures = list(failures or [])

    def search(self, request):
        return SearchOutcome(refs=list(self._refs.get(request.query, [])),
                             failures=list(self._failures))

    def resolve(self, ref):
        return {"has_fulltext": self._has_fulltext, "title": ref.title,
                "doc_type": ref.doc_type or "journal", "year": ref.year}

    def read(self, ref):
        return {"text": ref.excerpt or "原文片段", "locator": ref.locator, "page": 1}


def test_autonomous_policy_harvests_ingests_and_records_coverage(monkeypatch):
    ingested = {}

    def fake_ingest(topic, papers, embed=False, store=None):
        ingested["topic"] = topic
        ingested["titles"] = [p["title"] for p in papers]
        return {"ingested": [{"doc_id": "d1"}], "merged": [{"doc_id": "d2"}], "errors": []}

    monkeypatch.setattr("src.kb.ingest.ingest_machine", fake_ingest)
    calls: list[str] = []

    def search_fn(query, limit):
        calls.append(query)
        return [PAPER_A, PAPER_B] if len(calls) == 1 else [PAPER_A]

    items, coverage = gather_sources(
        None, _contract(SourceSummary(autonomous_retrieval=True)),
        topic="rf-A", policy=SourcePolicy.autonomous, search_fn=search_fn, max_queries=3)

    assert calls, "自主模式必须真的发出检索式"
    assert coverage.policy is SourcePolicy.autonomous
    assert coverage.queries and coverage.engines == ["arXiv", "Semantic Scholar", "OpenAlex"]
    assert coverage.hits == 4                      # 首轮 2 条 + 其后每轮 1 条 (A 重复命中)
    assert coverage.ingested == 1 and coverage.duplicates == 1
    assert ingested["topic"] == "rf-A" and len(ingested["titles"]) == 2  # 跨检索式去重
    assert coverage.started_at and coverage.finished_at
    assert "策略 autonomous" in coverage.scope_note
    assert items == []                             # 没有知识服务时不产出证据


def test_user_kb_policy_never_searches_outside(monkeypatch):
    def search_fn(query, limit):  # pragma: no cover - 被调用即失败
        raise AssertionError("user_kb 策略不得外搜")

    ref = SourceRef(source_id="doc-1", title="A", doc_type="journal", year="2021",
                    excerpt="片段", locator="p.3")
    contract = _contract(SourceSummary(source_set_id="rf-A", documents=2))
    from src.research.query_planner import plan_queries

    service = FakeService(has_fulltext=True)
    service._refs = {plan_queries(contract, limit=2)[0].text: [ref]}
    items, coverage = gather_sources(
        service, contract, topic="rf-A", policy=SourcePolicy.user_kb,
        search_fn=search_fn, max_queries=2)

    assert coverage.engines == []                  # 未外搜
    assert coverage.fulltext_available == 1 and coverage.abstract_only == 0
    assert len(items) == 1 and items[0].location        # 可回到原文


def test_duplicate_records_across_queries_merge_once():
    shared = SourceRef(source_id="doc-1", title="A", doc_type="journal", excerpt="x")
    other = SourceRef(source_id="doc-2", title="B", doc_type="journal", excerpt="y")
    service = FakeService(has_fulltext=False)
    queries = []
    contract = _contract(SourceSummary(source_set_id="rf-A", documents=2))
    from src.research.query_planner import plan_queries

    for query in plan_queries(contract, limit=2):
        queries.append(query.text)
    service._refs = {queries[0]: [shared, other], queries[1]: [shared]}
    items, coverage = gather_sources(service, contract, policy=SourcePolicy.user_kb,
                                     max_queries=2)
    assert len(items) == 2, [i.title for i in items]
    assert coverage.duplicates >= 1
    assert coverage.abstract_only == 2             # 无全文如实计数, 不当成已读全文


def test_failures_and_no_hit_are_recorded_separately():
    def broken(query, limit):
        raise TimeoutError("network down")

    _, coverage = gather_sources(
        None, _contract(SourceSummary(autonomous_retrieval=True)),
        topic="rf-A", policy=SourcePolicy.autonomous, search_fn=broken, max_queries=2)
    assert any("TimeoutError" in f for f in coverage.failures)
    assert any("未返回任何可用记录" in u for u in coverage.uncovered)

    service = FakeService()  # 检索执行成功但零命中
    _, empty = gather_sources(
        service, _contract(SourceSummary(source_set_id="rf-A", documents=1)),
        policy=SourcePolicy.user_kb, max_queries=2)
    assert not empty.failures
    assert any("不等于不存在" in u for u in empty.uncovered)


def test_both_policy_merges_user_kb_with_autonomous_retrieval(monkeypatch):
    monkeypatch.setattr("src.kb.ingest.ingest_machine",
                        lambda topic, papers, embed=False, store=None: {
                            "ingested": papers, "merged": [], "errors": []})
    contract = _contract(SourceSummary(source_set_id="rf-A", documents=1))
    from src.research.query_planner import plan_queries

    ref = SourceRef(source_id="doc-9", title="KB 命中", doc_type="journal", excerpt="z")
    service = FakeService()
    query = plan_queries(contract, limit=1)[0].text
    service._refs = {query: [ref]}

    items, coverage = gather_sources(
        service, contract, topic="rf-A", policy=SourcePolicy.both,
        search_fn=lambda q, n: [PAPER_A], max_queries=1)
    assert coverage.engines and coverage.ingested == 1
    assert len(items) == 1 and items[0].title == "KB 命中"
    assert "both" in coverage.scope_note


def test_coverage_absorb_accumulates_rounds():
    first = RetrievalCoverage(queries=["q1"], engines=["arXiv"], hits=2, failures=["f1"],
                              uncovered=["u1"])
    second = RetrievalCoverage(queries=["q1", "q2"], engines=["OpenAlex"], hits=3,
                               ingested=1, failures=["f2"], uncovered=["u2"],
                              finished_at="2026-10-01T00:00:00+00:00")
    first.absorb(second)
    assert first.queries == ["q1", "q2"]
    assert first.engines == ["arXiv", "OpenAlex"]
    assert first.hits == 5 and first.ingested == 1
    assert first.failures == ["f1", "f2"] and first.uncovered == ["u1", "u2"]
    assert first.finished_at == "2026-10-01T00:00:00+00:00"
