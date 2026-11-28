import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.agents.review import ReviewAgent, _issue_from, judge_review


def finding(key, summary, quote, fix, block="blk-result", severity="major"):
    return {"issue_id": key, "category": "science", "severity": severity,
            "block_id": block, "quoted_text": quote, "summary": summary, "acceptance": [fix]}


def test_repeat_finding_counts_once_but_independent_defects_in_same_quote_remain():
    quote = "阈值增加，所以所有船都遵守；下表的量以小时计算。"
    original = finding("first", "把模型阈值推广为行为", quote, "撤回对实际行为的推断", severity="minor")
    repeat = finding("repeat", "由阈值不能推断实际合规", quote, "撤回对实际行为的推断")
    independent = finding("unit", "小时与分钟混用", quote, "统一时间单位", severity="minor")
    score = judge_review([_issue_from(row) for row in [original, repeat, independent]],
                         category_scores={"science": 90})
    deductions = score["score_adjustments"]["science"]["issue_deductions"]
    assert sorted(deductions.values()) == [10, 30]
    assert score["category_scores"]["science"] == 60 and len(score["fatal_issue_ids"]) == 1


def test_mixed_grounding_retains_valid_deductions_and_quarantines_reported_subscore(monkeypatch):
    current = "该阈值可直接外推至所有船舶。"
    valid = finding("real", "该模型没有控制外推所需的条件", current, "补充外推假设")
    invalid = finding("bad", "报告了一项实验", "这句话没有写过。", "核实原始数据")
    run = lambda rows: run_review(monkeypatch, current, rows)
    mixed = run([valid, invalid])
    assert len(mixed.issues) == 1
    assert mixed.payload["reported_category_scores"]["science"] == 20
    assert mixed.payload["category_scores"]["science"] == 70
    assert mixed.payload["category_scores"]["fidelity"] == 92
    assert not mixed.payload["accepted"]
    rejected = run([invalid])
    assert rejected.payload["decision"] == "unreviewed" and not rejected.payload["accepted"]
    no_anchor = {"category": "readability", "summary": "段落应改写", "repair_action": "revise_text"}
    assert run([no_anchor]).payload["decision"] == "unreviewed"


def run_review(monkeypatch, text, rows):
    review = ReviewAgent()
    score = dict.fromkeys(["science", "fidelity", "readability", "citation", "completeness"], 92)
    output = {"issues": rows, "category_scores": {**score, "science": 20},
              "category_notes": {"science": "结论和实验有待核查。"}}
    monkeypatch.setattr(review, "tool_loop", lambda *a, **kw: (json.dumps(output), []))
    ctx = ContextPack(objects={"manuscript": [{"sections": [
        {"heading": "结果", "blocks": [{"block_id": "blk-result", "text": text}]}]}]})
    return review._run(AgentTask(agent="review", objective="审阅"), ctx,
                       SimpleNamespace(llm_available=lambda *_: True), UsageRecord(), [])


def test_superseded_duplicate_can_inherit_rechecked_anchor_only_from_named_live_finding():
    from src.agents.review import _issue_updates

    quote = "若某船在较高检测概率下仍然违规，则该船在较低检测概率下必然违规。"
    live = _issue_from(finding("issue-current", "激励条件与行为选择混同", quote, "限制推论范围"))
    old = {**live.to_dict(), "id": "review-old", "issue_id": "old", "dedupe_key": "old", "status": "open"}
    ctx = ContextPack(objects={"review_issue": [old], "manuscript": [{"sections": [
        {"heading": "结果", "blocks": [{"block_id": "blk-result", "text": quote}]}]}]})
    check = {"issue_id": "old", "status": "superseded", "block_ids": [],
             "reason": "与 issue-current 为同一问题，已被其覆盖。"}
    task = AgentTask(agent="review", objective="跟踪原段落")
    def update():
        return _issue_updates([live], ctx, task, [check], True, {})[-1].payload
    assert update()["status"] == "superseded" and update()["check_block_ids"] == ["blk-result"]
    ctx.objects["review_issue"][0]["quoted_text"] = "另一个独立问题。"
    assert update()["status"] == "not_rechecked"
