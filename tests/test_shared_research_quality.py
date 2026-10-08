"""Regression checks for field transfer, library reuse and review acceptance."""

from pathlib import Path
from types import SimpleNamespace
import json

from src.agents.evidence import _topic_of
from src.agents.protocol import (AgentResult, AgentTask, ContextPack, IssueSeverity,
                                 ReviewIssueRef, TaskOutcome)
from src.agents.review import judge_review
from src.agents.review import ReviewAgent
from src.agents.supervisor import ResearchBrief, needs_to_tasks
from src.agents.writing import WritingAgent
from src.agents.protocol import UsageRecord
from src.kb.bridge import _download_fulltext_for, _harvest_external, gather_sources
from src.kb.identity import build_identity, candidate_keys, normalize_doi
from src.kb.path_import import FileStatus, ScanRequest, scan_paths
from src.kb.schema import Provenance
from src.kb.store import KBStore
from src.research.progress import write_plan_progress, write_run_progress
from src.research.publication_evidence import terminology_variants
from src.research.question_planner import build_spec_from_input, formulate
from src.research.schemas import Claim, RetrievalCoverage, SourcePolicy, TaskKind
from src.tools.pdf_fetcher import academic_filename


QUESTION = "说明湿度如何导致钙钛矿太阳能电池性能衰减"


def test_generated_outputs_and_download_cache_are_not_importable(tmp_path, monkeypatch):
    from src import config

    output = tmp_path / "outputs"
    cache = tmp_path / "data" / "pdfs"
    output.mkdir()
    cache.mkdir(parents=True)
    manuscript = output / "manuscript.md"
    pdf = cache / "paper.pdf"
    manuscript.write_text("old AIR draft", encoding="utf-8")
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(config, "OUTPUT_DIR", output)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    report = scan_paths(ScanRequest(paths=[str(manuscript), str(pdf)],
                                    allow_roots=[str(tmp_path)]))
    assert len(report.denied) == 2
    assert all(row.status == FileStatus.denied for row in report.denied)


def test_perovskite_request_gets_mechanism_contract_and_shared_field(monkeypatch):
    from src.rag import relevance_filter

    fixture_root = Path(__file__).parent / "fixtures" / "domain_terms"
    monkeypatch.setattr(relevance_filter, "cases_root", lambda: fixture_root)
    relevance_filter.load_terms.cache_clear()
    terms = terminology_variants(QUESTION)
    assert "perovskite solar cell" in terms
    assert "humidity" in terms
    assert formulate(QUESTION).task_kind == TaskKind.mechanism
    assert build_spec_from_input(QUESTION, project_id="p").domain == "perovskite-stability"
    task = AgentTask(agent="evidence", objective="查找文献", project_id="p1", problem_id="q1")
    context = ContextPack(task_id=task.task_id, agent="evidence", request=QUESTION,
                          objects={"brief": [{"main_question": QUESTION}]})
    assert _topic_of(task, context) == "shared-perovskite-stability"
    relevance_filter.load_terms.cache_clear()


def test_shipped_domain_terms_are_parseable():
    from src.rag.relevance_filter import _load_terms_file, cases_root

    root = cases_root()
    for name in ("rf-fingerprint", "perovskite-stability", "combinatorial-design"):
        path = root / f"{name}.md"
        assert path.is_file(), f"missing runtime domain glossary: {path}"
        terms = _load_terms_file(path)
        assert terms.domain == name and terms.aliases and terms.in_domain


def test_frontend_bundle_assets_are_present():
    root = Path(__file__).resolve().parents[1] / "src" / "web"
    assert list((root / "assets").glob("index-*.js")), "frontend JavaScript bundle missing"
    assert list((root / "assets").glob("index-*.css")), "frontend CSS bundle missing"
    built_index = root / "dist" / "index.html"
    if built_index.is_file():
        import re

        for asset in re.findall(r'/(assets/index-[^"\s]+)',
                                built_index.read_text(encoding="utf-8")):
            assert (root / asset).is_file(), f"referenced frontend bundle missing: {asset}"


def test_roles_cannot_bypass_shared_library_with_direct_external_tools():
    from src.agents.reasoning import ReasoningAgent

    task = AgentTask(agent="evidence", objective="查文献", source_policy="both")
    context = ContextPack(request=QUESTION)
    runtime = SimpleNamespace()
    for agent in (ReviewAgent(), WritingAgent(), ReasoningAgent()):
        assert not {tool.name for tool in agent.tools(task, context, runtime)} & {
            "search_all_sources", "arxiv_search", "openalex_search",
            "semantic_scholar_search"}
    from src.agents.evidence import EvidenceAgent

    assert not {tool.name for tool in EvidenceAgent().tools(task, context, runtime)} & {
        "search_all_sources", "arxiv_search", "openalex_search",
        "semantic_scholar_search"}


def test_unknown_field_does_not_inherit_other_projects_domain():
    from src.rag.relevance_filter import domain_terms, has_domain_signal

    assert domain_terms("一种全新的跨学科研究问题").is_empty()
    assert not has_domain_signal("Hadamard matrix")
    task = AgentTask(agent="evidence", objective="查文献", project_id="p", problem_id="q")
    context = ContextPack(request="一种全新的跨学科研究问题")
    assert _topic_of(task, context, {"field_key": "novel-field",
                                     "field_confidence": 0.95}) == "shared-novel-field"
    assert _topic_of(task, context, {"field_key": "novel-field",
                                     "field_confidence": 0.5}) == "research-p-q"


def test_semantically_rejected_search_hits_never_enter_ingestion(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    coverage = RetrievalCoverage(policy=SourcePolicy.autonomous)
    papers = [{"title": "Perovskite solar cell humidity degradation",
               "abstract": "Moisture and degradation in photovoltaic cells."},
              {"title": "Mathematical projective plane construction",
               "abstract": "Unrelated design theory.", "_semantic_rejected": True}]
    rows = _harvest_external(["perovskite humidity"], SourcePolicy.autonomous,
                             coverage, topic="shared-perovskite-stability",
                             search_fn=lambda query, count: papers,
                             per_query=3, ingest=False)
    assert len(rows) == 1
    assert "Perovskite" in rows[0].title


def test_identity_cache_reuses_pdf_and_query_receipt(tmp_path, monkeypatch):
    from src import config
    from src.tools import pdf_fetcher

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    paper = {"title": "Humidity in perovskite solar cells", "authors": "A. Smith",
             "year": "2024", "doi": "10.1234/example"}
    keys = candidate_keys(build_identity(paper))
    pdf = tmp_path / "cached.pdf"
    pdf.write_bytes(b"%PDF cached")
    store = KBStore("shared-perovskite-stability")
    try:
        store.set_identity(keys, "doc-existing")
        store.add_provenance("doc-existing", [Provenance(origin="machine", file=str(pdf))])
        store.mark_query_searched("perovskite humidity", 1)
        assert store.query_recently_searched("perovskite humidity")
        assert store.cached_pdf_for(keys) == str(pdf)
    finally:
        store.close()
    monkeypatch.setattr(pdf_fetcher, "download_pdfs_for_papers",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("cached PDF must not be downloaded")))
    coverage = RetrievalCoverage(policy=SourcePolicy.autonomous)
    result = _download_fulltext_for([paper], topic="shared-perovskite-stability",
                                    per_query=3, coverage=coverage)
    assert result[0]["pdf_path"] == str(pdf)
    assert coverage.fulltext_available == 1


def test_second_study_in_shared_field_searches_library_before_external(tmp_path, monkeypatch):
    from src import config
    from src.kb import bridge
    from src.kb.service import KnowledgeService
    from src.kb import publication

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bridge, "_download_fulltext_for",
                        lambda papers, **kwargs: list(papers))
    monkeypatch.setattr(bridge, "_embedding_enabled", lambda: False)
    monkeypatch.setattr(publication, "verify_publication", lambda paper: dict(paper))
    monkeypatch.setattr(KnowledgeService, "_vector_hits", lambda *args, **kwargs: [])
    calls = []

    def external(query, count):
        calls.append(query)
        return [{"title": "Perovskite solar cell humidity degradation",
                 "authors": "A. Smith", "year": "2024", "doi": "10.1234/field",
                 "abstract": "Humidity degrades perovskite solar cell efficiency.",
                 "publication_status": "published", "publication_type": "journal-article",
                 "publication_verified_by": "fixture"}]

    kwargs = dict(topic="shared-perovskite-stability", policy=SourcePolicy.autonomous,
                  claim=Claim(statement=QUESTION), extra_queries=["perovskite humidity"],
                  max_queries=1, search_fn=external)
    first, first_coverage = gather_sources(None, None, **kwargs)
    from src.kb.ingest import ingest_machine

    ingest_machine("shared-perovskite-stability", [{
        "title": "Humidity analogies in projective plane design",
        "authors": "B. Mathematician", "year": "2020",
        "abstract": "A mathematical analogy, not a photovoltaic study."}])
    second, second_coverage = gather_sources(None, None, **kwargs)
    assert len(calls) == 1
    assert first and second
    assert all("projective plane" not in row.title for row in second)
    assert first_coverage.ingested == 1
    assert second_coverage.ingested == 0
    assert second_coverage.executed


def test_readable_filename_never_dedupes_distinct_doi():
    base = {"title": "Same title", "authors": "A. Smith", "year": "2024"}
    assert academic_filename({**base, "doi": "10.1/a"}) != academic_filename(
        {**base, "doi": "10.1/b"})
    assert normalize_doi("https://doi.org/10.1/A") == normalize_doi("doi:10.1/a")
    assert academic_filename({**base, "doi": "https://doi.org/10.1/A"}) == \
        academic_filename({**base, "doi": "10.1/a"})


def test_major_science_or_citation_issue_forces_major_revision():
    for category in ("science", "fidelity", "citation"):
        issue = ReviewIssueRef(issue_id=category, severity=IssueSeverity.major,
                               category=category, summary="原则性错误")
        decision = judge_review([issue], threshold=80, semantic_reviewed=True,
                                category_scores={name: 100 for name in
                                                 ("science", "fidelity", "citation",
                                                  "readability", "figure", "completeness")})
        assert decision["decision"] == "major_revision"
        assert not decision["accepted"]
    assert judge_review([], threshold=80, semantic_reviewed=True,
                        category_scores={name: 60 for name in
                                         ("science", "fidelity", "citation", "readability",
                                          "figure", "completeness")})["decision"] == "revision"
    assert judge_review([], threshold=80, semantic_reviewed=False)["decision"] == "unreviewed"


def test_task_progress_is_saved_before_final_package(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)

    class EmptyStore:
        def list_latest(self, kind):
            return []

    task = AgentTask(agent="evidence", objective="查文献", project_id="proj", problem_id="prob")
    result = AgentResult(task_id=task.task_id, agent="evidence",
                         outcome=TaskOutcome.completed, summary="已检索")
    planned = write_plan_progress(project_id="proj", problem_id="prob",
                                  run_id="run1", brief={"main_question": QUESTION},
                                  plan={"tasks": []})
    assert (planned / "brief.json").is_file()
    root = write_run_progress(EmptyStore(), project_id="proj", problem_id="prob",
                              run_id="run1", task=task, result=result)
    assert root == Path(tmp_path) / "research" / "proj" / "run1" / "progress"
    assert (root / "progress.json").is_file()
    from src.server import list_artifacts

    visible = list_artifacts(project_id="proj", problem_id="prob", run_id="run1")
    assert any(row["name"].endswith("progress/progress.json")
               for row in visible["files"])


def test_clean_review_without_object_changes_still_commits_task(tmp_path):
    from src.research.commit import ResearchCommitService
    from src.research.store import ResearchStore
    from src.research.task_store import KIND_TASK

    store = ResearchStore("p", tmp_path / "research.sqlite")
    try:
        task = AgentTask(agent="review", objective="审阅", project_id="p",
                         problem_id="q", run_id="r")
        result = AgentResult(task_id=task.task_id, agent="review",
                             outcome=TaskOutcome.completed, summary="审阅通过",
                             payload={"accepted": True, "score": 91})
        outcome = ResearchCommitService(store).commit(task, result, current_versions={})
        assert outcome.task_saved
        assert store.get(KIND_TASK, task.task_id)["status"] == "completed"
    finally:
        store.close()


def test_low_score_review_returns_to_writer_then_requires_new_review(monkeypatch):
    manuscript = {"id": "ms-1", "manuscript_id": "ms-1", "version": 2,
                  "title": "旧稿", "abstract": "", "sections": []}
    context = ContextPack(request=QUESTION, objects={
        "brief": [{"main_question": QUESTION, "deliverables": ["full_paper"]}],
        "manuscript": [manuscript]})
    review_task = AgentTask(agent="review", objective="独立审阅", project_id="p",
                            problem_id="q", run_id="r", hints={"review_threshold": 80})
    agent = ReviewAgent()
    scores = {name: 70 for name in ("science", "fidelity", "citation",
                                    "readability", "figure", "completeness")}
    monkeypatch.setattr(agent, "tool_loop", lambda *args, **kwargs: (
        json.dumps({"issues": [], "category_scores": scores,
                    "category_notes": {name: "补充可核查论证" for name in scores}}), []))
    review_runtime = SimpleNamespace(llm_available=lambda stage: True)
    reviewed = agent._run(review_task, context, review_runtime, UsageRecord(), [])
    assert reviewed.payload["decision"] == "revision"
    assert not reviewed.payload["accepted"]
    brief = ResearchBrief(project_id="p", problem_id="q", main_question=QUESTION)
    revision_tasks = needs_to_tasks(reviewed.followup_needs, brief=brief, plan_version=2)
    assert revision_tasks and revision_tasks[0].agent == "writing"
    assert revision_tasks[0].hints["review_cycle"]
    writing_runtime = SimpleNamespace(llm_available=lambda stage: False)
    revised = WritingAgent()._run(revision_tasks[0], context, writing_runtime,
                                  UsageRecord(), [])
    assert revised.payload["manuscript"]["version"] == 3
    assert any(need.kind.value == "review" for need in revised.followup_needs)


def test_review_cannot_accept_missing_scores(monkeypatch):
    agent = ReviewAgent()
    monkeypatch.setattr(agent, "tool_loop", lambda *args, **kwargs: (
        '{"issues": []}', []))
    task = AgentTask(agent="review", objective="审阅", hints={"review_threshold": 80})
    result = agent._run(task, ContextPack(request=QUESTION),
                        SimpleNamespace(llm_available=lambda stage: True),
                        UsageRecord(), [])
    assert result.payload["decision"] == "unreviewed"
    assert not result.payload["accepted"]
