import json
from types import SimpleNamespace

from src.agents.modeling import ModelingAgent
from src.agents.protocol import AgentTask, ContextPack, TaskOutcome, UsageRecord
from src.agents.reasoning import ReasoningAgent
from src.agents.review import deterministic_issues
from src.agents.runtime import AgentRuntime
from src.agents.writing import WritingAgent, finalise_manuscript
from src.publication.schemas import Block, BlockRole, Manuscript, Section, WritingPacket
from src.research.schemas import Claim, ObjectRef


def test_empirical_model_does_not_use_insufficient_keyword_hits():
    task = AgentTask(
        agent="modeling", objective="研究星网对渔业产业的影响",
        source_policy="both", source_set_ids=["星网_星链"],
    )
    context = ContextPack(
        request="研究星网系统建设对临海渔业产业的影响",
        objects={"evidence": [{
            "id": "ev-1", "source_id": "doc-1", "title": "Satellite communication",
            "excerpt": "A reference list mentions fisheries economics.",
            "locator": "abstract chars 0-70", "content_level": "abstract",
            "relation": "insufficient",
        }]},
    )
    runtime = SimpleNamespace(llm_available=lambda *_: True)
    result = ModelingAgent()._run(task, context, runtime, UsageRecord(), [])
    assert result.outcome == TaskOutcome.blocked
    assert not result.proposed_changes
    assert any(need.kind.value == "more_sources" for need in result.followup_needs)


def test_background_paper_cannot_back_external_framework_assumption():
    candidate = {
        "name": "Unverified framework", "mechanism": "A textbook equation",
        "formal_encoding": "y = a*x", "variables": [{"symbol": "x", "domain": "real"}],
        "source_refs": ["doc-b"],
        "assumptions": [{"statement": "External equation is valid here",
                         "origin": "external", "source_ids": ["doc-b"]}],
    }
    response = SimpleNamespace(content=json.dumps({"models": [candidate]}))
    runtime = SimpleNamespace(
        llm_available=lambda *_: True,
        llm=lambda *_args, **_kwargs: SimpleNamespace(invoke=lambda _messages: response),
    )
    task = AgentTask(agent="modeling", objective="研究产业影响", source_policy="both")
    context = ContextPack(request="研究某产业影响", objects={
        "evidence": [{"id": "ev-b", "source_id": "doc-b", "title": "Background",
                      "excerpt": "Only background context", "locator": "p. 2",
                      "content_level": "fulltext", "relation": "background"}],
    })
    result = ModelingAgent()._run(task, context, runtime, UsageRecord(), [])
    assert not result.proposed_changes
    assert result.outcome == TaskOutcome.blocked


def test_writer_removes_insufficient_source_from_citations():
    packet = WritingPacket(sources=[{
        "source_id": "doc-2026", "title": "A fisheries article", "authors": "A Author",
        "year": "2026", "venue": "Research Journal",
        "publication_status": "published", "publication_verified_by": "crossref",
        "publication_type": "journal-article", "relation": "insufficient",
    }])
    block = Block(role=BlockRole.evidence, text="该文献支持本研究结论。")
    block.add_ref("source", ObjectRef(id="doc-2026"))
    manuscript = Manuscript(title="研究", sections=[Section(heading="研究", blocks=[block])])
    finalise_manuscript(manuscript, packet)
    assert not any(kind == "source" for kind in block.ref_kinds)
    assert any(gap.get("kind") == "ineligible_reference" for gap in manuscript.gaps)


def test_writer_flags_unverified_known_claim_in_conclusion():
    packet = WritingPacket(claims=[{"id": "clm-1", "status": "blocked", "statement": "模型恒等式"}])
    block = Block(role=BlockRole.claim, text="已知的是模型内部恒等式成立；核验尚未闭合。")
    block.add_ref("claim", ObjectRef(id="clm-1"))
    manuscript = Manuscript(title="研究", sections=[Section(heading="结论", blocks=[block])])
    findings = WritingAgent().audit_manuscript(manuscript, packet)
    assert any("已知" in finding and "未核验" in finding for finding in findings)

    block.text = "候选恒等式不是已知结论，仍待核验。"
    findings = WritingAgent().audit_manuscript(manuscript, packet)
    assert not any("使用已知/已证实/已核验措辞" in finding for finding in findings)


def test_reviewer_flags_published_but_insufficient_citation():
    context = ContextPack(objects={
        "evidence": [{
            "source_id": "doc-1", "title": "A published paper", "authors": "A Author",
            "year": "2026", "venue": "Research Journal", "locator": "abstract chars 0-60",
            "publication_status": "published", "publication_verified_by": "crossref",
            "publication_type": "journal-article", "relation": "insufficient",
        }],
        "manuscript": [{"sections": [{"blocks": [{
            "block_id": "b1", "text": "由此可得研究结论。",
            "ref_kinds": ["source"], "refs": [{"id": "doc-1"}],
        }]}]}],
    })
    findings = deterministic_issues(context)
    assert any(issue.category == "citation" and "支持关系不足" in issue.summary
               for issue in findings)


def test_empirical_question_does_not_prove_unsourced_model_identity():
    claim = Claim(
        id="clm-identity", statement="候选模型中的恒等式",
        claim_type="descriptive", strategy="identity_simplification",
        study={"design": "theory"}, lhs="x+x", rhs="2*x", relation="==",
        origin="proposed",
    )
    task = AgentTask(
        agent="reasoning", objective="核验候选模型公式",
        source_policy="both", hints={"claim_id": claim.id},
    )
    context = ContextPack(request="研究星网建设对临海渔业的影响", objects={
        "brief": [{"main_question": "研究星网建设对临海渔业的影响"}],
        "claim": [claim.model_dump(mode="json")],
        "evidence": [{"id": "ev-1", "title": "Satellite communication",
                      "excerpt": "General background", "support": "insufficient"}],
    })
    result = ReasoningAgent().formal_closure(task, context, AgentRuntime(), UsageRecord())
    assert result is not None and result.outcome == TaskOutcome.blocked
    assert not any(change.kind == "obligation" for change in result.proposed_changes)


def test_empirical_formula_with_registered_support_can_be_checked():
    claim = Claim(
        id="clm-supported-formula", statement="来源支持的条件性公式",
        claim_type="descriptive", strategy="identity_simplification",
        study={"design": "theory"}, lhs="x+x", rhs="2*x", relation="==",
        origin="proposed",
    )
    context = ContextPack(request="研究某产业影响", objects={
        "brief": [{"main_question": "研究某产业影响"}],
        "claim": [claim.model_dump(mode="json")],
        "evidence": [{"id": "ev-2", "claim_id": claim.id,
                      "title": "Relevant method", "excerpt": "A located equation",
                      "relation": "supports", "locator": "p. 3"}],
    })
    task = AgentTask(agent="reasoning", objective="核验来源支持的公式",
                     source_policy="both", hints={"claim_id": claim.id})
    result = ReasoningAgent().formal_closure(task, context, AgentRuntime(), UsageRecord())
    assert result is not None
    assert "暂不生成数学证明义务" not in result.summary
