"""Regressions from the Nov 27 review of numbered tables and prior issue checks."""
import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.agents.review import ReviewAgent, _review_grounding_conflicts, _review_prompt
from src.agents.review_state import latest_review_issues
from src.agents.supervisor import TeamPlan
from src.graph.research_graph import TeamLoopState, _review_before_delivery


def context():
    return ContextPack(objects={"manuscript": [{"manuscript_id": "m", "version": 4,
        "sections": [{"blocks": [{"role": "table", "block_id": "blk-vars",
            "heading": "变量与单位", "text": "下表说明变量。",
            "data": {"header": ["变量", "单位"], "rows": [["X", "米"]]}},
            {"block_id": "blk-results", "text": "比较仍缺少观测。"}]}]}],
        "review_issue": [{"id": "store-old", "issue_id": "old", "category": "readability",
                          "severity": "major", "summary": "相同内容重复",
                          "affected_refs": [{"id": "blk-vars"}]}]})


def run_review(monkeypatch, issues, checks):
    agent = ReviewAgent()
    output = {"issues": issues, "previous_issue_checks": checks,
              "category_scores": {"readability": 75, "fidelity": 85, "science": 82,
                                  "citation": 88, "completeness": 80},
              "category_notes": {"readability": "调整表格解释的重叠段。"}}
    monkeypatch.setattr(agent, "tool_loop", lambda *args, **kwargs: (json.dumps(output), []))
    return agent._run(AgentTask(task_id="review", agent="review", objective="复核稿件"),
                      context(), SimpleNamespace(llm_available=lambda *_: True), UsageRecord(), [])


def test_review_can_quote_numbered_published_table_and_cells():
    ctx = context()
    draft = _review_prompt(ctx).split("# 核查材料")[0]
    for quote in ["表 1 变量与单位", "| X | 米 |"]:
        assert quote in draft
        assert not _review_grounding_conflicts({"issues": [{"block_id": "blk-vars",
                                                    "quoted_text": quote}]}, ctx)
    assert _review_grounding_conflicts({"issues": [{"block_id": "blk-vars",
                                                  "quoted_text": "表 9 变量与单位"}]}, ctx)


def test_persisted_issue_reuse_and_score_explanation_reach_final_revision(monkeypatch):
    current = [{"issue_id": "fresh", "category": "readability", "severity": "major",
                "summary": "同一个变量出现两种定义", "acceptance": ["合并重复定义"],
                "block_id": "blk-vars", "quoted_text": "表 1 变量与单位"},
               {"issue_id": "other", "category": "readability", "severity": "advisory",
                "summary": "应先定义比较单位", "acceptance": ["补充单位"],
                "block_id": "blk-results", "quoted_text": "比较仍缺少观测。"}]
    checks = [{"issue_id": "old", "status": "unresolved", "block_ids": ["blk-vars"],
               "reason": "重复定义仍待修订，见 fresh。"}]
    result = run_review(monkeypatch, current, checks)
    assert result.payload["semantic_reviewed"]
    adjustment = result.payload["validation_details"]["score_adjustments"]["readability"]
    assert (adjustment["reported"], adjustment["effective"], adjustment["issue_deductions"]["old"]) == (75, 67, 30)
    change = next(c for c in result.proposed_changes if c.object_id == "store-old")
    assert change.payload["status"] == "open" and change.payload["dedupe_key"] == "old"
    state = TeamLoopState(plan=TeamPlan(), results={"review": result})
    task, _ = _review_before_delivery(state, ["review"], 3, 24)
    assert task.hints["review_category_scores"]["readability"] == 67
    assert "old 扣 30 分" in task.hints["review_category_notes"]["readability"]
    assert any("合并重复定义" in criterion for criterion in task.acceptance_criteria)


def test_resolved_requires_current_block_and_persists_even_if_review_finds_nothing(monkeypatch):
    check = {"issue_id": "old", "status": "resolved", "block_ids": ["blk-vars"],
             "reason": "表 1 已把变量与单位放在同一行。"}
    result = run_review(monkeypatch, [], [check])
    assert result.payload["accepted"]
    closed, = result.proposed_changes
    assert closed.object_id == "store-old" and closed.payload["status"] == "resolved"
    assert closed.payload["reviewed_manuscript"]["version"] == 4
    assert latest_review_issues([closed.payload]) == []
    invalid = run_review(monkeypatch, [], [{**check, "block_ids": ["blk-nonexistent"]}])
    assert not invalid.payload["accepted"] and not invalid.payload["semantic_reviewed"]


def test_current_issue_scope_survives_history_exceeding_projection_limit():
    from src.research.projection import object_rows
    rows = [{"id": str(i), "issue_id": str(i), "_task_id": "current" if i >= 40 else "old",
             "_submitted_at": f"00:{i:02d}"} for i in range(43)]
    store = SimpleNamespace(list_latest=lambda *_: list(rows), latest_version=lambda *_: 1)
    assert {r["id"] for r in latest_review_issues(object_rows(store, "review_issue"))} == {"40", "41", "42"}


def test_open_major_survives_many_more_recently_closed_findings():
    from src.research.projection import TeamProjection
    rows = [{"id": str(i), "issue_id": str(i), "dedupe_key": str(i),
             "status": "open" if i == 0 else "resolved", "severity": "major",
             "_submitted_at": f"00:{i:02d}"} for i in range(55)]
    store = SimpleNamespace(list_latest=lambda *_: list(rows), latest_version=lambda *_: 1)
    active = TeamProjection(store).rows("review_issue")
    assert [row["issue_id"] for row in active] == ["0"]


def test_invalid_review_cannot_displace_open_issues_during_legacy_migration():
    from src.agents.review import _issue_from, _issue_updates
    ctx = context()
    old = ctx.objects["review_issue"][0]
    provisional = _issue_from({"issue_id": "tentative", "summary": "尚待核实"})
    updates = _issue_updates([provisional], ctx, AgentTask(agent="review", objective="复审"),
                             [], False, {"version": 4})
    assert not updates
    assert latest_review_issues([old]) == [old]
