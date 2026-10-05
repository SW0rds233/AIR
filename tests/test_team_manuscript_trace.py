"""团队交付的追溯必须检查真实稿件，而不是另造旧稿件自证。"""

from src.agents.protocol import AgentTask, ContextPack
from src.agents.writing import (WritingAgent, _manuscript_from_payload,
                                build_packet_from_context, finalise_manuscript,
                                render_markdown)
from src.publication.schemas import Block, BlockRole, Manuscript, Section, WritingPacket
from src.research.package import _traceability
from src.research.schemas import (Claim, ClaimStatus, Coverage, ObjectRef,
                                  ResearchSnapshot, SupportKind, ValidationStatus)


def _case():
    snapshot = ResearchSnapshot(project_id="trace-team")
    snapshot.claims = [Claim(id="clm-1", statement="结论一", status=ClaimStatus.supported,
                             support_kind=SupportKind.symbolic_check,
                             coverage=Coverage.target,
                             validation_status=ValidationStatus.verified)]
    snapshot.writing_map = {"clm-1": "block:blk-1"}
    block = Block(block_id="blk-1", role=BlockRole.claim, text="结论一")
    block.add_ref("claim", ObjectRef(id="clm-1"))
    manuscript = Manuscript(title="测试稿", sections=[Section(heading="结果", blocks=[block])])
    return snapshot, manuscript


def test_real_team_manuscript_passes_traceability():
    snapshot, manuscript = _case()
    report = _traceability(snapshot, render_markdown(manuscript), manuscript)
    assert report["ok"] is True
    assert report["checked"] == "markdown"
    assert report["mapped"] == [{"claim_id": "clm-1", "anchor": "block:blk-1"}]


def test_missing_input_fields_are_explained_to_reader():
    task = AgentTask(task_id="write-1", agent="writing", objective="写正文",
                     project_id="p", problem_id="q", run_id="r")
    context = ContextPack(request="研究问题", objects={
        "brief": [{"unknown_fields": ["source_set_ids", "quantifiers"]}],
    })
    manuscript, _ = WritingAgent().deterministic_manuscript(
        task, build_packet_from_context(task, context))
    markdown = render_markdown(manuscript)
    assert "未绑定用户资料库" in markdown
    assert "量词或适用范围" in markdown
    assert "\nsource_set_ids\n" not in markdown


def test_model_references_cannot_invent_bibliography_entries():
    packet = WritingPacket(
        claims=[{"id": "clm-1", "statement": "结论"}],
        obligations=[{"id": "obl-1", "claim_id": "clm-1"}],
        verifications=[{"id": "ver-1", "claim_id": "clm-1"}],
        sources=[],
    )
    manuscript = _manuscript_from_payload({
        "title": "研究报告",
        "sections": [{"heading": "结论", "blocks": [{
            "role": "claim", "text": "结论",
            "ref_ids": ["clm-1", "obl-1", "ver-1", "fake-source"]
        }]}],
    }, packet)
    assert manuscript is not None
    block = manuscript.all_blocks()[0]
    assert block.ref_kinds == ["claim", "obligation", "verification"]
    assert block.needs_check is True
    assert any(g["kind"] == "unknown_reference" for g in manuscript.gaps)
    finalise_manuscript(manuscript, packet)
    assert "依据: [" not in render_markdown(manuscript)


def test_unbound_model_claim_is_downgraded_not_guessed():
    packet = WritingPacket(claims=[{"id": "clm-1", "statement": "真实结论"}])
    manuscript = _manuscript_from_payload({
        "sections": [{"heading": "论证", "blocks": [
            {"role": "claim", "text": "因此先转化为射影平面问题"},
            {"role": "claim", "text": "真实结论", "ref_ids": ["clm-1"]},
        ]}],
    }, packet)
    assert manuscript is not None
    finalise_manuscript(manuscript, packet)
    first, second = manuscript.all_blocks()[:2]
    assert first.role.value == "reasoning"
    assert first.needs_check is True
    assert first.ref_kinds == []
    assert second.role.value == "claim"
    assert second.ref_kinds == ["claim"]


def test_missing_anchor_and_unreferenced_claim_are_reported():
    snapshot, manuscript = _case()
    assert _traceability(snapshot, "# 空正文", manuscript)["missing_anchors"] == ["clm-1"]
    manuscript.sections[0].blocks[0].refs.clear()
    manuscript.sections[0].blocks[0].ref_kinds.clear()
    report = _traceability(snapshot, render_markdown(manuscript), manuscript)
    assert report["ok"] is False
    assert report["unlabeled_blocks"] == ["blk-1"]


def test_deterministic_team_paper_includes_the_verified_theorem_chain():
    """problem2 这类定理应用不能只写一句“不存在”，必须显示可复核的判定链。"""
    packet = WritingPacket(
        main_question="211 个节点能否构成所述排班？",
        claims=[{"id": "clm-1", "statement": "2-(211,15,1) 设计不存在",
                 "status": "supported", "version": 1}],
        verifications=[{"id": "ver-1", "claim_id": "clm-1", "version": 1,
                        "validation_status": "verified", "stale": False,
                        "arguments": {"design_report": {
                            "design": {"v": 211, "k": 15, "lam": 1},
                            "counts": {"r": 15, "b": 211, "symmetric": True, "order": 14},
                            "evidence": [{"condition": "Bruck–Ryser–Chowla 必要条件",
                                          "result": False, "theorem": "Bruck–Ryser–Chowla",
                                          "statement": "阶为 2 mod 4 时需为两个平方数之和",
                                          "inputs": {"n": 14},
                                          "conclusion": "14 不是两个平方数之和"}]}}}],
    )
    manuscript, _ = WritingAgent().deterministic_manuscript(None, packet)
    text = render_markdown(manuscript)
    assert "Bruck–Ryser–Chowla" in text
    assert "n=14" in text
    assert "14 不是两个平方数之和" in text
    assert any("verification" in block.ref_kinds for block in manuscript.all_blocks())


def test_two_square_certificate_states_the_actual_obstructing_factor():
    from src.research.design_feasibility import DesignParams, check, render_certificate_chain

    report = check(DesignParams(v=211, k=15, lam=1))
    brc = next(item for item in report.checks if item.key == "brc_projective_plane")
    assert brc.result is False
    assert "7≡3 (mod 4)" in brc.conclusion
    assert "指数为奇数 (1)" in brc.conclusion
    equivalence = next(item for item in report.checks
                       if item.key == "projective_plane_equivalence")
    assert "r=k=15, b=v=211" in equivalence.conclusion
    assert "r=k=15=b" not in equivalence.conclusion
    paper_chain = render_certificate_chain(report.certificate_dict())
    assert paper_chain.index("射影平面与对称设计等价") < paper_chain.index(
        "Bruck–Ryser–Chowla 定理 (射影平面)")
    assert "不依赖关联矩阵的穷举" in paper_chain
