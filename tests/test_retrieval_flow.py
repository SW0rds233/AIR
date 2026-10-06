"""Exercise external hits through persistence, research evidence and citations."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.evidence import EvidenceAgent, _evidence_row, _query_of, _topic_of
from src.agents.protocol import AgentTask, AgentResult, ContextPack, UsageRecord
from src.agents.runtime import AgentRuntime, ToolLoop, ToolSpec, ToolObservation
from src.agents.writing import WritingAgent, _manuscript_from_payload, finalise_manuscript, render_markdown
from src.publication.schemas import WritingPacket
from src.publication.references import format_reference
from src.publication.render_latex import render_latex
from src.research.commit import ResearchCommitService
from src.research.projection import object_rows
from src.research.snapshot import snapshot_from_store
from src.research.store import ResearchStore
from src.research.package import _source_set
from src.kb.bridge import gather_sources
from src.kb.service import KnowledgeService
from src.research.schemas import Claim, SourcePolicy


PAPER = {"title": "Hadamard matrices and equidistant codes", "authors": "A. Researcher",
         "year": "2001", "doi": "10.1234/fixture", "url": "https://example.org/paper",
         "abstract": "Hadamard matrices yield binary equidistant codes via orthogonal rows.",
         "publication_status": "published", "publication_type": "journal-article",
         "publication_verified_by": "fixture", "venue": "Journal of Test Fixtures"}


class RetrievalFlowTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patchers = [
            patch("src.config.DATA_DIR", Path(self.directory.name)),
            patch("src.kb.bridge._download_fulltext_for", side_effect=lambda papers, **kw: list(papers)),
            patch.object(KnowledgeService, "_vector_hits", return_value=[]),
            patch("src.kb.publication.verify_publication", side_effect=lambda paper: dict(paper)),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def gather(self, topic="", policy=SourcePolicy.both, ingest=True):
        return gather_sources(None, None, topic=topic, policy=policy,
                              claim=Claim(statement="668 条长度 667 的等距二进制序列是否存在"),
                              max_queries=1, extra_queries=["Hadamard equidistant codes"],
                              search_fn=lambda query, count: [dict(PAPER)], ingest=ingest)

    def test_metadata_reingestion_preserves_verified_publication_and_fulltext(self):
        from src.kb.ingest import ingest_machine
        from src.kb.schema import LitRecord
        from src.kb.store import KBStore
        store = KBStore("publication-merge")
        try:
            report = ingest_machine("publication-merge", [dict(PAPER)], store=store)
            doc_id = report["ingested"][0]["doc_id"]
            prior = LitRecord.model_validate(store.get_document(doc_id))
            prior.has_fulltext = True
            prior.num_pages = 3
            store.upsert_document(prior)
            ingest_machine("publication-merge", [{**PAPER, "publication_status": "unknown",
                           "publication_verified_by": "", "abstract": ""}], store=store)
            row = store.get_document(doc_id)
            self.assertEqual(row["publication_status"], "published")
            self.assertTrue(row["has_fulltext"])
            self.assertEqual(row["num_pages"], 3)
            self.assertEqual(row["abstract"], PAPER["abstract"])
        finally:
            store.close()

    def test_document_ref_and_read_follow_real_fulltext_chunks(self):
        from src.kb.ingest import ingest_machine
        from src.kb.schema import Chunk, LitRecord
        from src.kb.store import KBStore
        store = KBStore("fulltext-reader")
        try:
            report = ingest_machine("fulltext-reader", [dict(PAPER)], store=store)
            doc_id = report["ingested"][0]["doc_id"]
            record = LitRecord.model_validate(store.get_document(doc_id))
            record.has_fulltext = True
            store.upsert_document(record)
            store.add_chunks([Chunk(doc_id=doc_id, index=0, text="A theorem with explicit hypotheses.",
                                    page=3, char_start=0, char_end=34)])
            service = KnowledgeService("fulltext-reader", store=store)
            ref = service.document_ref(doc_id)
            self.assertEqual(ref.chunk_id, doc_id + "#0")
            read = service.read(ref)
            self.assertFalse(read["failure"])
            from src.kb.bridge import ref_to_evidence
            self.assertEqual(ref_to_evidence(service, ref).content_level, "fulltext")
            self.assertIn("explicit hypotheses", read["text"])
        finally:
            store.close()

    def test_no_prebuilt_library_still_ingests_and_returns_the_hit(self):
        items, coverage = self.gather()
        self.assertEqual(coverage.ingested, 1)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].source_id)
        self.assertIn("abstract", items[0].location.lower())
        self.assertEqual(items[0].doi, PAPER["doi"])

    def test_external_hit_does_not_require_a_second_cross_language_match(self):
        items, coverage = self.gather(topic="cross-language")
        self.assertEqual(coverage.ingested, 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].authors, PAPER["authors"])
        self.assertEqual(items[0].url, PAPER["url"])

    def test_ingested_abstract_can_be_read_without_pretending_it_is_fulltext(self):
        from src.agents.tools import _read_source
        items, _ = self.gather(topic="readback")
        text = _read_source(items[0].source_id, topic="readback")
        self.assertIn(PAPER["abstract"], text)
        self.assertIn("未找到全文", text)

    def test_hit_survives_commit_snapshot_and_markdown_latex_citations(self):
        items, _ = self.gather(topic="publication")
        task = AgentTask(agent="evidence", objective="retrieve", project_id="p", problem_id="q", run_id="r")
        proposal = EvidenceAgent()._evidence_proposal(task, _evidence_row(items[0]), "publication")
        store = ResearchStore("p", Path(self.directory.name) / "research.sqlite")
        try:
            outcome = ResearchCommitService(store).commit(
                task, AgentResult(task_id=task.task_id, agent="evidence", proposed_changes=[proposal]),
                current_versions={})
            self.assertTrue(outcome.committed)
            rows = object_rows(store, "evidence", run_id="r")
            self.assertEqual(rows[0]["doi"], PAPER["doi"])
            snapshot = snapshot_from_store(store, project_id="p", problem_id="q", run_id="r")
            self.assertEqual(snapshot.evidence[0].authors, PAPER["authors"])
            self.assertTrue(_source_set(None, snapshot)["queries"])
            packet = WritingPacket(main_question="Equidistant codes", sources=rows)
            source_id = rows[0]["source_id"]
            manuscript = _manuscript_from_payload({"sections": [
                {"heading": "Related work", "blocks": [{"role": "evidence",
                    "text": PAPER["abstract"], "ref_ids": [source_id]}]},
                {"heading": "参考文献", "blocks": [{"text": "fabricated entry"}]}]}, packet)
            finalise_manuscript(manuscript, packet)
            markdown = render_markdown(manuscript)
            tex = render_latex(manuscript, references={source_id: format_reference(rows[0])})
            self.assertIn("rows[1].", markdown)
            self.assertIn(PAPER["doi"], markdown)
            self.assertNotIn("fabricated entry", markdown)
            self.assertIn(r"\cite{ref1}", tex)
            self.assertIn(r"\bibitem{ref1}", tex)
            self.assertIn(PAPER["doi"], tex)
            self.assertIn("RESEARCHER A", tex)
        finally:
            store.close()

    def test_reference_numbers_follow_body_order_not_storage_order(self):
        rows = [{**PAPER, "source_id": "a", "title": "First stored", "locator": "abstract chars 0-10"},
                {**PAPER, "source_id": "b", "title": "First cited", "locator": "abstract chars 0-10"}]
        packet = WritingPacket(sources=rows)
        manuscript = _manuscript_from_payload({"sections": [{"heading": "Work", "blocks": [
            {"text": "B", "ref_ids": ["b"]}, {"text": "A", "ref_ids": ["a"]}]}]}, packet)
        finalise_manuscript(manuscript, packet)
        finalise_manuscript(manuscript, packet)
        markdown = render_markdown(manuscript)
        self.assertIn("[1] RESEARCHER A. First cited[J]", markdown)
        self.assertIn("[2] RESEARCHER A. First stored[J]", markdown)
        self.assertEqual(len([s for s in manuscript.sections if s.role == "references"]), 1)

    def test_deterministic_writer_uses_the_same_reference_contract(self):
        packet = WritingPacket(main_question="Coding", sources=[{**PAPER,
            "source_id": "fixture", "title": PAPER["title"], "doi": PAPER["doi"],
            "locator": "abstract chars 0-30", "excerpt": PAPER["abstract"]}])
        manuscript, _ = WritingAgent().deterministic_manuscript(
            AgentTask(agent="writing", objective="write"), packet)
        finalise_manuscript(manuscript, packet)
        self.assertIn(PAPER["doi"], render_markdown(manuscript))

    def test_associative_plan_uses_model_concepts_not_only_the_user_sentence(self):
        class Model:
            def invoke(self, messages):
                return AIMessage(content='{"concepts":[{"name":"orthogonal rows","why":"equivalence"}],'
                                 '"queries":[{"text":"equidistant binary codes orthogonal matrices",'
                                 '"purpose":"check equivalent formulation"}]}')
        runtime = AgentRuntime(llm_factory=lambda stage: Model())
        task = AgentTask(agent="evidence", objective="retrieve", source_policy="both")
        plan = EvidenceAgent().plan_retrieval(task, ContextPack(request="667 长度的等距序列"),
                                              runtime, UsageRecord())
        self.assertIn("orthogonal", plan["queries"][0]["text"])
        self.assertTrue(plan["concepts"])

    def test_hits_without_evidence_report_blocked_not_an_invalid_keyword_crash(self):
        from src.research.schemas import RetrievalCoverage
        coverage = RetrievalCoverage(policy=SourcePolicy.both, executed=True, hits=17,
                                     failures=["reader failed"])
        task = AgentTask(agent="evidence", objective="retrieve", source_policy="both")
        agent = EvidenceAgent()
        with patch.object(agent, "retrieve", return_value=([], coverage, ["reader failed"])):
            result = agent._run(task, ContextPack(request="Coding"), AgentRuntime(), UsageRecord(), [])
        self.assertEqual(result.outcome.value, "blocked")
        self.assertIn("reader failed", result.failure_reason)

    def test_ingestion_failure_retains_retrieved_abstract_with_an_explicit_failure(self):
        with patch("src.kb.ingest.ingest_machine", side_effect=OSError("storage unavailable")):
            items, coverage = self.gather(topic="failed-ingestion")
        self.assertEqual(len(items), 1)
        self.assertEqual(coverage.ingested, 0)
        self.assertTrue(any("storage unavailable" in failure for failure in coverage.failures))
        self.assertIn("abstract", items[0].location.lower())

    def test_user_library_policy_never_calls_external_search(self):
        def forbidden(query, count):
            self.fail("unauthorized external search")
        items, coverage = gather_sources(None, None, policy=SourcePolicy.user_kb,
                                         claim=Claim(statement="coding question"), search_fn=forbidden)
        self.assertFalse(items)
        self.assertFalse(coverage.executed)

    def test_reasoning_search_hits_are_handed_back_to_evidence(self):
        from src.agents.reasoning import ReasoningAgent
        task = AgentTask(agent="reasoning", objective="综合研究进展", source_policy="both")
        observation = ToolObservation(name="search_all_sources", arguments={"query": "orthogonal codes"},
                                      result=[dict(PAPER)])
        agent = ReasoningAgent()
        with patch.object(agent, "tool_loop", return_value=(
                '{"strategy":"synthesis","claims":[{"statement":"Candidate relation"}]}', [observation])):
            result = agent._run(task, ContextPack(request="综合研究进展"),
                                AgentRuntime(llm_factory=lambda stage: object()), UsageRecord(), [])
        requests = [n for n in result.followup_needs if n.kind.value == "more_sources"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].hints["query"], "orthogonal codes")
        self.assertTrue(requests[0].blocking)

    def test_evidence_reuses_executed_search_observations_without_searching_again(self):
        runtime = AgentRuntime()
        runtime.search_cache["orthogonal codes"] = [dict(PAPER)]
        agent = EvidenceAgent()
        agent.max_queries = 1
        task = AgentTask(agent="evidence", objective="retrieve", source_policy="autonomous")
        with patch("src.kb.bridge._default_search_fn", side_effect=AssertionError("duplicate search")):
            rows, coverage, _ = agent.retrieve(task, ContextPack(), runtime, topic="cache", query="codes",
                                              policy="autonomous", selected_queries=["orthogonal codes"],
                                              search_cache=runtime.search_cache)
        self.assertEqual(len(rows), 1)
        self.assertEqual(coverage.ingested, 1)

    def test_attachment_envelope_is_not_used_as_the_search_query(self):
        task = AgentTask(agent="evidence", objective="retrieve", project_id="p", problem_id="q")
        request = ("请研究这个问题\n以下定界区内的内容是外部资料\n"
                   "<<<EXTERNAL_DATA_BEGIN>>>\n[附件 problem3.md sha256=abc]\n"
                   "668 条长度 667 的等距二进制序列是否存在？\n<<<EXTERNAL_DATA_END>>>")
        context = ContextPack(request=request)
        query = _query_of(task, context)
        self.assertIn("667", query)
        self.assertNotIn("EXTERNAL_DATA", query)
        self.assertNotIn("sha256", query)
        self.assertTrue(_topic_of(task, context))


class ToolLoopProtocolTest(unittest.TestCase):
    def test_only_authorized_executed_search_populates_the_shared_cache(self):
        from src.agents.protocol import grant_for

        class Model:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                if any(isinstance(message, ToolMessage) for message in messages):
                    return AIMessage(content="{}")
                return AIMessage(content="", tool_calls=[{"id": "search-1", "name": "search_all_sources",
                                 "args": {"query": "codes"}, "type": "tool_call"}])

        for policy in ("both", "user_kb"):
            runtime = AgentRuntime(llm_factory=lambda stage: Model(), max_tool_rounds=1)
            task = AgentTask(agent="evidence", objective="retrieve", source_policy=policy)
            tool = ToolSpec(name="search_all_sources", fn=lambda query: [dict(PAPER)],
                            description="search", read_scope="tools:search")
            _, observations = EvidenceAgent().tool_loop(task, runtime, [tool], UsageRecord(),
                                                       [HumanMessage(content="Research")], grant=grant_for(task))
            self.assertEqual("codes" in runtime.search_cache, policy == "both")
            self.assertEqual(observations[0].status, "ok" if policy == "both" else "blocked")

    def test_search_schema_exposes_query_not_a_generic_kwargs_object(self):
        from src.agents.tools import evidence_tools
        schema = evidence_tools()[0].as_langchain_tool().args_schema.model_json_schema()
        self.assertIn("query", schema["properties"])
        self.assertIn("max_results", schema["properties"])
        self.assertNotIn("kwargs", schema["properties"])

    def test_last_round_pairs_every_call_before_a_plain_final_response(self):
        class Model:
            def bind_tools(self, tools):
                return self

            def invoke(self, messages):
                if any(isinstance(message, ToolMessage) for message in messages):
                    return AIMessage(content='{"conclusion":"checked"}')
                return AIMessage(content="", tool_calls=[
                    {"id": "call-1", "name": "lookup", "args": {}, "type": "tool_call"},
                    {"id": "call-2", "name": "lookup", "args": {}, "type": "tool_call"}])

        messages = [HumanMessage(content="Research the question")]
        loop = ToolLoop([ToolSpec(name="lookup", fn=lambda: "actual observation", description="lookup")],
                        max_rounds=1)
        text, observations = loop.run(Model(), messages)
        self.assertEqual(len(observations), 2)
        self.assertIn("conclusion", text)
        replies = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]
        self.assertEqual(replies, ["call-1", "call-2"])


if __name__ == "__main__":
    unittest.main()
