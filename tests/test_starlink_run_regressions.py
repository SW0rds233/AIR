"""Regressions reproduced from the 2026-10-11 Starlink maritime run."""

from src.agents.protocol import AgentTask, ContextPack
from src.research.schemas import ResearchSnapshot


def test_unbound_both_policy_reads_the_conversation_library():
    from src.agents.evidence import _topic_of
    from src.utils.conversation_layout import conversation_topic

    session_id = "d" * 32
    task = AgentTask(
        agent="evidence", objective="检索中国星网海事资料",
        project_id="proj-mv3omgh65ukz", problem_id="problem",
        session_id=session_id, source_policy="both",
    )
    assert _topic_of(task, ContextPack(request="中国星网与中国航海业")) == conversation_topic(session_id)


def test_latex_normalizer_keeps_standard_control_words_and_repairs_glued_relation():
    from src.publication.render_latex import _separate_glued_commands

    valid = (
        r"\bigl(x\bigr) \Bigl[x\Bigr] \bigcup A \bigcap B "
        r"\caption{Figure} \multicolumn{2}{c}{X} \includegraphics{a.png} "
        r"\middle| \limsup_n \argmax_x \leqslant \leqq \llap{x}"
    )
    assert _separate_glued_commands(valid) == valid
    assert _separate_glued_commands(r"0\leqz\leqRH") == r"0\leq z\leq RH"


def test_unexecuted_experiment_gate_does_not_reject_plans_for_measurement():
    from src.research.acceptance import delivery_gate

    snapshot = ResearchSnapshot(project_id="test", experiment_specs=[
        {"id": "valobj-1", "execution_status": "proposed"},
    ])
    manuscript = ("本文讨论中国星网海事应用。" * 15
                  + "参数仍需实测，本报告不给定任何参数取值。"
                  + "表中注明需要实测或仿真标定。")
    result = delivery_gate(manuscript, snapshot)
    assert not any("实验规格 valobj-1 尚未执行" in reason for reason in result.reasons)


def test_unexecuted_experiment_gate_still_rejects_claimed_results():
    from src.research.acceptance import delivery_gate

    snapshot = ResearchSnapshot(project_id="test", experiment_specs=[
        {"id": "valobj-1", "execution_status": "proposed"},
    ])
    manuscript = "本文讨论中国星网海事应用。" * 15 + "本研究实测结果表明时延降低。"
    result = delivery_gate(manuscript, snapshot)
    assert any("实验规格 valobj-1 尚未执行" in reason for reason in result.reasons)


def test_empty_library_tool_result_does_not_recommend_changing_authorization(tmp_path, monkeypatch):
    from src import config
    from src.agents.tools import _search_local_kb

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    result = _search_local_kb("海事卫星通信", topic="unregistered-library")
    assert "没有可检索的已登记资料" in result
    assert "autonomous" not in result


def test_stale_history_is_not_reported_as_cited_without_a_reference():
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import VerificationRecord

    stale = VerificationRecord(id="ver-old", claim_id="clm-one", stale=True)
    snapshot = ResearchSnapshot(project_id="test", verifications=[stale])
    unreferenced = theory_validity_gate([], [], [stale], [], snapshot=snapshot)
    assert not any("被本次交付引用" in reason for reason in unreferenced.reasons)

    snapshot.verification_index = {"clm-one": ["ver-old"]}
    referenced = theory_validity_gate([], [], [stale], [], snapshot=snapshot)
    assert any("被本次交付引用" in reason for reason in referenced.reasons)


def test_traceability_explains_when_all_claims_remain_proposed():
    from src.research.package import _traceability
    from src.research.schemas import Claim

    snapshot = ResearchSnapshot(project_id="test", claims=[Claim(statement="待证命题")])
    result = _traceability(snapshot, "# 研究备忘录\n待证命题")
    assert result["status"] == "unverifiable"
    assert result["ok"] is False
    assert result["total_claims"] == 1
    assert result["core_claims"] == 0


def test_figure_review_request_goes_to_figure_agent():
    from src.agents.protocol import ReviewIssueRef
    from src.agents.review import _needs_from

    issue = ReviewIssueRef(
        issue_id="issue-figure", category="figure", severity="major",
        summary="全文无图表", suggested_owner="figures",
    )
    needs = _needs_from([issue])
    assert len(needs) == 1
    assert needs[0].owner == "figures"


def test_unjudged_retrieval_coverage_is_not_registered_as_a_scientific_claim():
    from src.agents.reasoning import ReasoningAgent

    task = AgentTask(agent="reasoning", objective="研究中国星网对航海业的影响")
    context = ContextPack(request="研究中国星网对航海业的影响", objects={
        "evidence": [{"title": "候选文献", "locator": "abstract:1",
                      "relation": "insufficient", "source_id": "doc-1"}],
    })
    claims, note = ReasoningAgent().fallback_claims(task, context, "synthesis")
    assert claims == []
    assert "支持关系" in note

    context.objects["evidence"][0]["relation"] = "contradicts"
    claims, _ = ReasoningAgent().fallback_claims(task, context, "synthesis")
    assert any("相反证据" in claim["statement"] for claim in claims)


def test_review_sees_only_the_current_manuscript_version():
    from src.agents.review import _draft_text, deterministic_issues

    old = {"title": "旧稿", "abstract": "本文已证明一个尚未证明的结论", "sections": []}
    current = {"title": "修订稿", "abstract": "当前仅提出研究假说", "sections": []}
    context = ContextPack(request="科研问题", objects={"manuscript": [old, current]})
    draft = _draft_text(context)
    assert "修订稿" in draft
    assert "旧稿" not in draft
    assert not any(issue.category == "science" for issue in deterministic_issues(context))


def test_compilation_reassessment_keeps_review_block_and_manifest(tmp_path):
    import json

    from src.graph.team_session import _record_delivery_verdict
    from src.research.delivery import apply_review_gate

    review = {"decision": "revise", "accepted": False, "current": True, "score": 65}
    delivery = apply_review_gate({"accepted": True, "blocking": [], "level": "论文草稿"}, review)
    assert delivery["accepted"] is False
    assert delivery["review"] == review
    assert any("独立审阅" in reason for reason in delivery["blocking"])

    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    _record_delivery_verdict(tmp_path, delivery)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["delivery_accepted"] is False
    assert manifest["review"] == review
    assert manifest["delivery_blocking"] == delivery["blocking"]

    approved = apply_review_gate({"accepted": True, "blocking": []},
                                 {"decision": "accepted", "accepted": True, "current": True})
    assert approved["accepted"] is True
    stale = apply_review_gate({"accepted": True, "blocking": []},
                              {"decision": "accepted", "accepted": True, "current": False})
    assert stale["accepted"] is False
