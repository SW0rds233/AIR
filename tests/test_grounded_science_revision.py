import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, UsageRecord
from src.agents.writing import WritingAgent
from src.publication.manuscript_state import manuscript_identity
from src.publication.schemas import Block, Manuscript, Section, WritingPacket
from src.research.delivery import assess_delivery
from src.research.store import ResearchStore


def test_current_review_can_block_an_unlinked_paragraph_and_writer_repairs_it_first(tmp_path):
    quote = "若某船在较高检测概率下仍然违规，则该船在较低检测概率下必然违规。"
    block = Block(block_id="blk-wrong", text=quote)
    other = Block(block_id="blk-caption", text="另一个段落。")
    paper = Manuscript(sections=[Section(blocks=[other, block])])
    issue = {"issue_id": "issue-inference", "severity": "major", "category": "science",
             "status": "open", "repair_action": "revise_text", "quoted_text": quote,
             "acceptance": ["修正逆否推论"],
             "affected_refs": [{"id": "blk-wrong"}, {"id": "clm-threshold"}],
             "reviewed_manuscript": manuscript_identity(paper.to_dict())}
    store = ResearchStore("proj", tmp_path / "audit.sqlite")
    try:
        store.put("manuscript", paper.manuscript_id, paper.to_dict())
        store.put("review_issue", "review-1", issue)
        def reasons():
            return assess_delivery(store, project_id="proj", problem_id="problem", run_id="run").publication.reasons
        assert any("issue-inference" in s and "blk-wrong" in s for s in reasons())
        store.put("review_issue", "review-1", {**issue, "status": "resolved"})
        assert not any("issue-inference" in s for s in reasons())
        store.put("review_issue", "review-1", {**issue, "reviewed_manuscript": {**issue["reviewed_manuscript"], "content_hash": "old"}})
        assert not any("issue-inference" in s for s in reasons())
    finally:
        store.close()

    responses = [
        {"blocks": [{"block_id": "blk-wrong", "source_ids": [], "text": quote + "见前文。"}]},
        {"blocks": [{"block_id": "blk-wrong", "text": "在罚则和成本固定时，原先达到激励阈值的船只仍达到阈值。"}]},
        {"blocks": [{"block_id": "blk-caption", "text": "相应图注已补齐。"}]},
    ]
    def invoke(messages):
        return SimpleNamespace(content=json.dumps(responses.pop(0)))
    runtime = SimpleNamespace(llm_available=lambda *_: True,
                              llm=lambda *a, **kw: SimpleNamespace(invoke=invoke))
    packet = WritingPacket(prior_manuscript=paper.to_dict(), review_issues=[issue])
    task = AgentTask(agent="writing", objective="分别改正推论与图注", hints={"focus_blocks": ["blk-caption"]})
    result, note = WritingAgent()._patch_with_llm(task, runtime, UsageRecord(), packet)
    assert result is not None, note
    assert quote not in result.all_blocks()[1].text


def test_an_unfixed_quote_cannot_be_rebound_after_a_failed_retry():
    from src.agents.protocol import ObjectRef

    block = Block(block_id="blk-x", text="所有船只必然违规。")
    block.add_ref("claim", ObjectRef(id="clm-x", version=1))
    paper = Manuscript(sections=[Section(blocks=[block])])
    issue = {"category": "science", "severity": "major", "repair_action": "revise_text",
             "quoted_text": block.text, "affected_refs": [{"id": block.block_id}], "acceptance": ["删除越界推断"]}
    packet = WritingPacket(prior_manuscript=paper.to_dict(), review_issues=[issue],
                           claims=[{"id": "clm-x", "version": 2}])
    def invoke(messages):
        if len(messages) > 2:
            raise RuntimeError("重试中断")
        return SimpleNamespace(content=json.dumps({"blocks": [{"block_id": "blk-x", "text": block.text + "其他说明。",
            "checked_claim_versions": {"clm-x": 2}}]}))

    runtime = SimpleNamespace(llm_available=lambda *_: True, llm=lambda *a, **kw: SimpleNamespace(invoke=invoke))
    result, note = WritingAgent()._patch_with_llm(AgentTask(agent="writing", objective="修正结论"), runtime,
                                                  UsageRecord(), packet)
    assert "重试中断" in note and result.all_blocks()[0].text.endswith("其他说明。")
    assert result.all_blocks()[0].refs[0].version == 1
    assert result.revision_log[-1]["unaddressed_blocks"] == ["blk-x"]


def test_delivery_status_keeps_a_short_distinct_cause_and_full_source_data():
    from src.research.acceptance import theory_validity_gate
    from src.research.delivery import delivery_stop_reason
    from src.research.schemas import Claim

    statement = "给定处罚与合规成本，检测概率介于零与一之间。" * 24
    claim = Claim(id="clm-threshold", statement=statement)
    reason, = theory_validity_gate([claim], [], [], []).unresolved
    assert "clm-threshold" in reason and len(reason) <= 110 and claim.statement == statement
    verdict = {"level": "条件性研究报告", "blocking": [reason, "待复审"], "unresolved": [reason, "缺乏观测"]}
    status = delivery_stop_reason("修订达到上限", verdict)
    assert status.count("clm-threshold") == 1 and "另 1 项" in status
    assert delivery_stop_reason(status, verdict) == status
