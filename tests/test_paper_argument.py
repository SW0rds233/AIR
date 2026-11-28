import json

from src.agents.writer_plan import build_paper_outline, refine_outline
from src.publication.schemas import WritingPacket


def test_a_short_argument_can_integrate_related_work_and_discussion():
    from types import SimpleNamespace
    from src.agents.writing import WritingAgent

    packet = WritingPacket(claims=[{"id": "c", "statement": "条件阈值"}])
    base = build_paper_outline(packet)
    roles = ["introduction", "method", "result", "conclusion"]
    rows = [{"role": role, "heading": title, "purpose": purpose}
            for role, title, purpose in zip(roles, ["两种解释", "阈值定义", "何时跨过阈值", "可区分的预测"],
                ["比较解释与文献", "列出可比条件", "推导阈值的必要条件", "说明区分两种解释的观测"])]
    outline = refine_outline({"sections": rows}, base)
    assert [row["heading"] for row in outline] == [row["heading"] for row in rows]
    assert refine_outline({"sections": rows[:2] + rows[3:]}, base) == base
    payload = {"sections": [{**row, "blocks": [{"text": row["purpose"]}]} for row in rows]}
    llm = SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps(payload)))
    manuscript, note = WritingAgent()._draft_compact_with_llm(llm, packet, base, [])
    assert not note and [section.role for section in manuscript.sections] == roles


def test_planning_and_abstract_get_exact_checked_targets_and_open_links():
    from src.agents.writer_plan import claim_basis
    packet = WritingPacket(claims=[{"id":"c", "version":2, "status":"supported",
        "statement":"检测率上升，行为改变", "formal_spec":{"premises":["x>=0"], "conclusion":"x+1>x"}}],
        verifications=[{"claim_id":"c", "claim_version":1, "id":"old", "validation_status":"verified",
                        "arguments":{"conclusion":"obsolete"}},
                       {"claim_id":"c", "claim_version":2, "id":"v2", "validation_status":"verified",
                        "scope":"target", "arguments":{"premises":["x>=0"], "conclusion":"x+1>x"}}],
        obligations=[{"claim_id":"c","claim_version":2,"status":"open","statement":"观测个体行为"}])
    basis = claim_basis(packet)
    assert basis[0]["checked"] == [{"id":"v2","scope":"target","premises":["x>=0"],"target":"x+1>x"}]
    assert "观测个体行为" in basis[0]["open_conditions"]
    assert "obsolete" not in json.dumps(basis)


def test_section_and_fallback_read_the_checked_claim_version():
    from src.agents.writing import _abstract_of, _manuscript_from_payload, finalise_manuscript
    packet = WritingPacket(
        claims=[{"id": "clm-cost", "version": 2, "status": "supported",
                 "statement": "提高查获率后渔船必然合规", "formal_spec": {"conclusion": "q2*F>=C"}}],
        verifications=[{"id": "v2", "claim_id": "clm-cost", "claim_version": 2,
                        "scope": "target", "validation_status": "verified",
                        "arguments": {"premises": ["q1*F>=C", "q2>q1", "F>0"], "conclusion": "q2*F>=C"}}],
        input_versions={"clm-cost": 2},
    )
    summary = _abstract_of(packet)
    assert "渔船必然合规" not in summary
    assert all(term in summary for term in ("q1*F>=C", "q2>q1", "F>0", "q2*F>=C"))
    manuscript = _manuscript_from_payload({"sections": [{"role": "result", "blocks": [
        {"role": "claim", "ref_ids": ["clm-cost"], "text": "达到阈值。"},
        {"role": "claim", "text": "旧结果 clm-cost。"},
    ]}]}, packet)
    current, prior = manuscript.sections[0].blocks
    prior.input_versions = {"clm-cost": 1}
    finalise_manuscript(manuscript, packet)
    assert [(ref.id, ref.version) for ref in current.refs] == [("clm-cost", 2)]
    assert [(ref.id, ref.version) for ref in prior.refs] == [("clm-cost", 1)]
