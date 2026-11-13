"""Regressions from the 2026-11-13 manuscript and its repeated reviews."""
import json

import pytest

from src.agents.protocol import ContextPack, ObjectRef, ReviewIssueRef
from src.agents.review import _needs_from, _review_materials
from src.agents.writing import apply_block_patch, revision_targets
from src.publication.schemas import Block, Manuscript, Section


def test_review_materials_keep_cited_source_after_large_evidence_pool():
    cited = {"source_id": "cited", "title": "Published current-year article", "year": "2026",
             "publication_verified_by": "crossref", "excerpt": "Actual scope of the study."}
    block = Block(text="Only a methodological analogy.")
    block.add_ref("source", ObjectRef(id="cited"))
    manuscript = Manuscript(sections=[Section(heading="Methods", blocks=[block])])
    context = ContextPack(objects={"manuscript": [manuscript.to_dict()], "evidence": [
        {"source_id": f"unrelated-{i}", "excerpt": "x" * 1500} for i in range(100)] + [cited]})
    material = _review_materials(context)
    payload = json.loads(material[material.index("{"):])
    assert payload["sources"][0]["source_id"] == "cited"
    assert payload["sources"][0]["publication_verified_by"] == "crossref"
    assert payload["review_date"]


def test_citation_wording_repair_goes_to_writer():
    issue = ReviewIssueRef(issue_id="issue-citation", category="citation", severity="major", suggested_owner="writing",
        summary="标注为方法类比，不得当作直接效应证据", affected_refs=[ObjectRef(id="blk-1")])
    need = _needs_from([issue])[0]
    assert need.owner == "writing" and need.kind.value == "manuscript_revision"


def test_revision_targets_cover_all_located_blocks_up_to_bound():
    blocks = [Block(block_id=f"b-{i}", text=f"paragraph {i}") for i in range(6)]
    manuscript = Manuscript(sections=[Section(heading="Methods", blocks=blocks)])
    targets = revision_targets(manuscript, [], {"focus_blocks": [b.block_id for b in blocks]})
    assert len(targets) == 6


def test_patch_can_replace_citation_without_changing_claim_identity():
    block = Block(block_id="b", text="Original background.")
    block.add_ref("source", ObjectRef(id="old"))
    block.add_ref("claim", ObjectRef(id="claim-1", version=2))
    manuscript = Manuscript(sections=[Section(heading="Methods", blocks=[block])])
    result = apply_block_patch(manuscript, [{"block_id": "b", "text": "New, qualified background.",
        "source_ids": ["new"]}], {"b"}, source_ids={"new"})
    assert [(k, r.id, r.version) for k, r in zip(result.all_blocks()[0].ref_kinds,
        result.all_blocks()[0].refs)] == [("claim", "claim-1", 2), ("source", "new", 1)]
    with pytest.raises(ValueError):
        apply_block_patch(manuscript, [{"block_id": "b", "source_ids": ["invented"]}],
                          {"b"}, source_ids={"new"})


def test_exhausted_retry_budget_emits_deferred_event_once():
    from src.utils.network_budget import NetworkBudget, NetworkUnavailable, retry_wait, task_network_budget
    events = []
    budget = NetworkBudget(retry_attempts=3, emit=lambda kind, data: events.append(kind))
    with task_network_budget(budget):
        for _ in range(2):
            with pytest.raises(NetworkUnavailable):
                retry_wait(4)
    assert events.count("network_unavailable") == 1
    assert budget.deferred


def test_cjk_path_set_has_breaks_without_splitting_text_commands():
    from src.publication.render_latex import _display_math
    formula = (r"\Delta Y=\sum_k\Delta Y_k,\qquad k\in\{\text{通信与信息成本},\ "
               r"\text{海上安全与救助},\ \text{渔情与海况信息},\ \text{监管与合规},\ \text{市场与供应链接入}\}")
    rendered = _display_math(formula)
    assert r"\begin{gathered}" in rendered
    assert rendered.count(r"\\") >= 2
    for label in ("通信与信息成本", "海上安全与救助", "渔情与海况信息", "监管与合规", "市场与供应链接入"):
        assert r"\text{" + label + "}" in rendered


def test_formula_assumption_evidence_cannot_certify_whole_formula():
    from types import SimpleNamespace
    from src.research.schemas import Claim, SourceEvidence
    from src.research.evidence import build_support_judge, independent_supporters
    claim = Claim(statement="Y = a * c", lhs="Y", rhs="a*c", variables=["Y", "a", "c"],
                  premises=["终端采用率取值需实测"])
    excerpt = "The measured terminal adoption rate was 20 percent in the sampled fleet."
    prompts = []
    class LLM:
        def invoke(self, messages):
            prompts.append(messages[-1].content)
            return SimpleNamespace(content=json.dumps({"relation": "supports", "quote": excerpt,
                "support_scope": "parameter_assumption", "supported_target": "终端采用率取值需实测",
                "condition_notes": ["仅适用于该样本船队"], "reason": "sample estimate"}))
    source = SourceEvidence(excerpt=excerpt, location="abstract", content_level="abstract")
    judged = build_support_judge(LLM())(claim, source)
    assert "终端采用率取值需实测" in prompts[0]
    assert judged.support_scope == "parameter_assumption"
    assert judged.support.value == "partially_supports"
    assert independent_supporters([judged]) == []


def test_evidence_task_cap_still_allows_writing():
    from src.agents.protocol import AgentTask, AgentResult, ResearchNeed
    from src.agents.supervisor import SupervisorAgent, ResearchBrief, TeamPlan
    brief = ResearchBrief(main_question="研究通信", deliverables=["full_paper"])
    done = [AgentTask(agent="evidence", objective=f"检索{i}") for i in range(8)]
    plan = TeamPlan()
    for task in done:
        plan.add_task(task)
    plan.add_task(AgentTask(agent="writing", objective="撰写论文"))
    results = {t.task_id: AgentResult(task_id=t.task_id, agent="evidence", outcome="completed") for t in done}
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results,
        open_needs=[ResearchNeed(kind="more_sources", statement="再找一批资料")])
    assert [t.agent for t in decision.tasks] == ["writing"]


def test_model_cap_counts_nested_and_helper_calls_once():
    from src.utils.network_budget import (NetworkBudget, NetworkState, ModelBudgetExceeded,
                                         model_call, task_network_budget, network_allowed)
    state = NetworkState(evidence_model_attempts=149)
    budget = NetworkBudget(state=state, role="evidence")
    with task_network_budget(budget):
        assert model_call(lambda: model_call(lambda: "ok", timeout=1, phase="helper"),
                          timeout=1, phase="llm") == "ok"
        assert state.evidence_model_attempts == 150 and budget.model_attempts == 1
        with pytest.raises(ModelBudgetExceeded):
            model_call(lambda: pytest.fail("must not call SDK"), timeout=1, phase="helper")
        assert not network_allowed()
    assert budget.deferred


@pytest.mark.parametrize("fixed", [True, False])
def test_current_year_review_conflict_gets_one_correction(monkeypatch, fixed):
    from types import SimpleNamespace
    from src.agents.review import ReviewAgent, REVIEW_CATEGORIES
    from src.agents.protocol import AgentTask, UsageRecord
    monkeypatch.setattr("src.agents.review._review_date", lambda: "2026-11-13")
    wrong = {"issues": [{"category": "citation", "severity": "major", "summary": "出版年份异常（2026年）"}],
             "category_scores": {c: 100 for c in REVIEW_CATEGORIES}}
    corrected = {**wrong, "issues": []} if fixed else wrong
    calls = []
    class LLM:
        def invoke(self, messages):
            calls.append(messages[-1].content)
            return SimpleNamespace(content=json.dumps(corrected, ensure_ascii=False))
    runtime = SimpleNamespace(llm_available=lambda *_: True, llm=lambda *_a, **_k: LLM())
    agent = ReviewAgent()
    monkeypatch.setattr(agent, "tool_loop", lambda *_a, **_k: (json.dumps(wrong), []))
    result = agent._run(AgentTask(agent="review", objective="审阅"), ContextPack(), runtime, UsageRecord(), [])
    assert len(calls) == 1 and "2026-11-13" in calls[0]
    assert result.payload["semantic_reviewed"] is fixed
    assert result.payload["accepted"] is fixed


def test_current_review_retires_resolved_tasks_without_dropping_research():
    from src.graph.research_graph import _current_review_needs
    from src.agents.protocol import AgentResult, ResearchNeed
    old = ResearchNeed(kind="more_sources", statement="旧引用问题", hints={"review_issue_ids": ["old"]})
    pending = ResearchNeed(kind="more_sources", statement="当前引用问题", hints={"review_issue_ids": ["current"]})
    research = ResearchNeed(kind="derivation", statement="独立的研究义务")
    result = AgentResult(task_id="review", issues=[ReviewIssueRef(issue_id="current")])
    assert _current_review_needs([old, pending, research], result) == [pending, research]


def test_context_keeps_cited_sources_before_row_limit():
    from types import SimpleNamespace
    from src.graph.research_graph import TeamRun
    from src.agents.protocol import AgentTask
    source = {"id": "cited", "source_id": "cited", "excerpt": "Actual evidence."}
    block = Block(text="Background.")
    block.add_ref("source", ObjectRef(id="cited"))
    rows = {"evidence": [{"id": f"uncited-{i}"} for i in range(60)] + [source],
            "manuscript": [Manuscript(sections=[Section(heading="Intro", blocks=[block])]).to_dict()]}
    run = TeamRun.__new__(TeamRun)
    run.projection = SimpleNamespace(rows=lambda kind: rows.get(kind, []))
    run._cached_default_builder = None
    run._source_set_rows = []
    run.outcome = SimpleNamespace(brief=None)
    run.request = "研究"
    run.attachments = []
    pack = run._default_context_builder()(AgentTask(agent="review", objective="审阅"), {})
    assert pack.objects["evidence"][0] == source


def test_citation_followup_only_loads_selected_issues():
    from src.agents.writing import build_packet_from_context
    from src.agents.protocol import AgentTask
    context = ContextPack(objects={"review_issue": [
        {"issue_id": "old", "summary": "旧稿问题"}, {"issue_id": "current", "summary": "当前换引"}]})
    packet = build_packet_from_context(AgentTask(agent="writing", objective="更新引用",
        hints={"review_issue_ids": ["current"], "need_kind": "manuscript_revision"}), context)
    assert [i["issue_id"] for i in packet.review_issues] == ["current"]


def test_metadata_review_uses_exact_doi_and_keeps_scientific_work_out_of_text_repair():
    from types import SimpleNamespace
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import AgentTask, UsageRecord
    from src.agents.review import _revision_need
    issue = ReviewIssueRef(issue_id="metadata", category="citation", severity="major",
        suggested_owner="writing", summary="核对出版元数据", locator="DOI:10.1234/example.2025.")
    need = _needs_from([issue])[0]
    assert need.owner == "evidence" and need.hints["query"] == "10.1234/example.2025"
    plan = EvidenceAgent().plan_retrieval(AgentTask(agent="evidence", objective=need.statement,
        hints=need.hints), ContextPack(), SimpleNamespace(), UsageRecord())
    assert [q["text"] for q in plan["queries"]] == ["10.1234/example.2025"]
    writing_issue = ReviewIssueRef(issue_id="readable", category="readability", summary="统一术语")
    revision = _revision_need(ContextPack(), [writing_issue], {"decision": "major_revision",
        "score": 40, "threshold": 80, "category_scores": {"science": 0, "readability": 60},
        "category_notes": {"science": "需要真实数据", "readability": "统一术语"}})
    assert revision.hints["review_category_scores"] == {"readability": 60}


def test_network_failures_are_deferred_not_errors():
    from types import SimpleNamespace
    from src.agents.protocol import TaskBudget, UsageRecord
    from src.agents.runtime import BudgetedLLM
    from src.utils.network_budget import NetworkUnavailable

    class Inner:
        def __init__(self, error): self.error = error
        def invoke(self, *_args, **_kwargs): raise self.error

    events = []
    llm = BudgetedLLM(Inner(NetworkUnavailable("网络暂不可用")), UsageRecord(), TaskBudget(),
                      None, "task-net", "evidence",
                      on_event=lambda kind, data: events.append(kind))
    with pytest.raises(NetworkUnavailable):
        llm.invoke("hi")
    assert events.count("llm_deferred") == 1
    assert "llm_error" not in events

    events.clear()
    llm = BudgetedLLM(Inner(RuntimeError("boom")), UsageRecord(), TaskBudget(),
                      None, "task-err", "evidence",
                      on_event=lambda kind, data: events.append(kind))
    with pytest.raises(RuntimeError):
        llm.invoke("hi")
    assert events.count("llm_error") == 1
    assert "llm_deferred" not in events


def test_evidence_call_cap_stops_new_research_but_keeps_writing():
    from src.agents.protocol import AgentResult, AgentTask, ResearchNeed
    from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan

    brief = ResearchBrief(main_question="研究通信", deliverables=["full_paper"])
    done = [AgentTask(agent="evidence", objective=f"检索{i}") for i in range(8)]
    plan = TeamPlan()
    for task in done:
        plan.add_task(task)
    plan.add_task(AgentTask(agent="writing", objective="撰写论文"))
    results = {t.task_id: AgentResult(task_id=t.task_id, agent="evidence",
                                      outcome="completed",
                                      payload={"model_attempts": 20}) for t in done}
    assert sum(r.payload["model_attempts"] for r in results.values()) >= 150
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results,
        open_needs=[ResearchNeed(kind="more_sources", statement="再找一批资料")])
    assert [t.agent for t in decision.tasks] == ["writing"]


def test_same_repair_target_stops_after_two_attempts():
    from src.agents.protocol import AgentResult, AgentTask, ResearchNeed
    from src.agents.supervisor import ResearchBrief, SupervisorAgent, TeamPlan

    brief = ResearchBrief(main_question="研究通信")
    first = AgentTask(agent="evidence", objective="核对来源", hints={"repair_key": "repair-1"})
    second = AgentTask(agent="evidence", objective="核对来源(重试)", hints={"repair_key": "repair-1"})
    plan = TeamPlan()
    plan.add_task(first)
    plan.add_task(second)
    results = {t.task_id: AgentResult(task_id=t.task_id, agent="evidence", outcome="completed")
               for t in (first, second)}
    need = ResearchNeed(kind="more_sources", statement="第三次核对同一目标",
                        hints={"repair_key": "repair-1", "root_cause_kind": "citation"})
    decision = SupervisorAgent().decide(brief=brief, plan=plan, results=results, open_needs=[need])
    assert decision.tasks == []


def test_evidence_targets_split_links_from_parameter_sources():
    from src.research.mechanisms import evidence_targets
    from src.research.schemas import Claim

    chain = Claim(statement="通信成本下降 → 信息成本下降 → 交易价格改善")
    targets = evidence_targets(chain)
    assert len(targets["mechanism_link"]) == 2
    assert targets["parameter_assumption"] == []

    formula = Claim(statement="Y = a * c", lhs="Y", rhs="a*c",
                    premises=["终端采用率需实测"], evidence_requirements=["弹性 a 需文献支持"])
    formula_targets = evidence_targets(formula)
    assert "终端采用率需实测" in formula_targets["parameter_assumption"]
    assert "弹性 a 需文献支持" in formula_targets["parameter_assumption"]
    assert formula_targets["mechanism_link"] == []


def test_patch_records_revision_log_with_issue_ids_and_changed_blocks():
    from types import SimpleNamespace
    from src.agents.protocol import AgentTask, UsageRecord
    from src.agents.writing import WritingAgent, WritingPacket
    from src.publication.schemas import Block, Manuscript, Section

    prior = Manuscript(sections=[Section(heading="方法", blocks=[Block(block_id="b1", text="旧文")])])
    packet = WritingPacket(prior_manuscript=prior.to_dict(),
                           review_issues=[{"issue_id": "i1", "summary": "更换引用"}])
    task = AgentTask(agent="writing", objective="修订", hints={"review_cycle": True,
                                                              "focus_blocks": ["b1"]})
    response = SimpleNamespace(content=json.dumps({"blocks": [{"block_id": "b1", "text": "新文"}]}))
    runtime = SimpleNamespace(llm_available=lambda *_: True,
                              llm=lambda *_a, **_k: SimpleNamespace(invoke=lambda _messages: response))
    patched, note = WritingAgent()._patch_with_llm(task, runtime, UsageRecord(), packet)
    assert patched is not None, note
    assert "新文" in patched.all_blocks()[0].text
    log = patched.revision_log[-1]
    assert log["issue_ids"] == ["i1"]
    assert log["changed_blocks"] == ["b1"]
