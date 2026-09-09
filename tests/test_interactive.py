"""对话式协作 (Human-in-the-loop) 第1+2步测试：暂停节点 + 人工决策路由 + 意见解析执行 (离线测试)

运行:
    python tests/test_interactive.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph.pipeline import (
    human_outline_node,
    human_draft_node,
    human_review_node,
    should_continue_review,
    route_after_human_outline,
    parse_human_intent,
    _parse_config_change,
    _human_feedback_to_contract,
    increment_revision,
    _is_confirm,
    _is_quit,
    _is_finalize,
)
from src.graph.state import PipelineState


def test_helpers_classify_responses():
    assert _is_confirm("")
    assert _is_confirm("y")
    assert _is_confirm("继续")
    assert _is_quit("q")
    assert _is_quit("停止")
    assert _is_finalize("f")
    assert _is_finalize("定稿")
    assert not _is_confirm("把3.2节改一下")


def test_outline_node_noop_when_not_interactive():
    assert human_outline_node({"interactive": False}) == {}


def test_draft_node_noop_when_not_interactive():
    assert human_draft_node({"interactive": False}) == {}


def test_review_node_noop_when_not_interactive():
    assert human_review_node({"interactive": False}) == {"human_review_decision": ""}


def test_finalize_decision_overrides_machine_gate():
    """用户在审稿暂停点选择定稿 → 即使低分也结束修订循环"""
    state = {
        "review_score": 30,  # 低分本应继续修订
        "revision_count": 0,
        "max_revisions": 3,
        "human_review_decision": "finalize",
        "error": None,
    }
    assert should_continue_review(state) == "end"


def test_no_decision_falls_through_to_machine_gate():
    """非交互模式下没有人工决策 → 走原有机器门禁"""
    state = {
        "review_score": 30,
        "revision_count": 0,
        "max_revisions": 3,
        "human_review_decision": "",
        "error": None,
    }
    assert should_continue_review(state) == "paper_writing"


def test_review_interrupt_resume_finalize_roundtrip():
    """端到端验证 interrupt() → Command(resume='f') 决策闭环"""
    g = StateGraph(PipelineState)
    g.add_node("human_review", human_review_node)
    g.set_entry_point("human_review")
    g.add_conditional_edges(
        "human_review",
        should_continue_review,
        {"paper_writing": END, "end": END},
    )
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-finalize"}}
    state = {
        "interactive": True,
        "review_score": 40,
        "review_report": "## 审稿报告\n内容",
        "revision_count": 1,
        "max_revisions": 3,
        "error": None,
    }

    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1], "应命中 interrupt 暂停点"

    for _ in app.stream(Command(resume="f"), cfg):
        pass
    final = app.get_state(cfg).values
    assert final["human_review_decision"] == "finalize"
    assert should_continue_review(final) == "end"


def test_review_interrupt_resume_revise_roundtrip():
    """用户选择继续修订 → decision=revise"""
    g = StateGraph(PipelineState)
    g.add_node("human_review", human_review_node)
    g.set_entry_point("human_review")
    g.add_conditional_edges(
        "human_review",
        should_continue_review,
        {"paper_writing": END, "end": END},
    )
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-revise"}}
    state = {
        "interactive": True,
        "review_score": 30,
        "review_report": "内容",
        "revision_count": 0,
        "max_revisions": 3,
        "error": None,
    }

    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1]

    for _ in app.stream(Command(resume=""), cfg):
        pass
    final = app.get_state(cfg).values
    assert final["human_review_decision"] == "revise"
    assert should_continue_review(final) == "paper_writing"


def test_draft_node_skips_revision_rounds():
    """初稿暂停点只在 revision_count==0 时触发, 修订轮次不重复暂停"""
    g = StateGraph(PipelineState)
    g.add_node("human_draft", human_draft_node)
    g.set_entry_point("human_draft")
    g.add_edge("human_draft", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-draft"}}
    state = {
        "interactive": True,
        "paper_draft": "草稿",
        "revision_count": 2,  # 修订轮 → 应跳过暂停
    }
    events = list(app.stream(state, cfg))
    assert all("__interrupt__" not in ev for ev in events)


def test_outline_feedback_recorded():
    """大纲暂停点: 输入修改意见 → 记录进 human_feedback 并继续"""
    g = StateGraph(PipelineState)
    g.add_node("human_outline", human_outline_node)
    g.set_entry_point("human_outline")
    g.add_edge("human_outline", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-outline"}}
    state = {"interactive": True, "paper_outline": "## 1. 引言\n..."}
    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1]

    for _ in app.stream(Command(resume="把3.2节的方法分类改为按信号来源划分"), cfg):
        pass
    final = app.get_state(cfg).values
    assert final["human_feedback"] == "把3.2节的方法分类改为按信号来源划分"
    assert final["human_outline_route"] == "outline_generation"
    assert final["outline_iteration"] == 1


# ---------------- 第2步: 意见解析执行 ----------------

def test_parse_human_intent_kinds():
    assert parse_human_intent("", "outline")["kind"] == "none"
    assert parse_human_intent("子主题改为联邦学习,边缘计算", "outline")["kind"] == "scope"
    assert parse_human_intent("关键词加上设备识别", "outline")["kind"] == "scope"
    assert parse_human_intent("轮次改为5", "outline")["kind"] == "config"
    assert parse_human_intent("把3.2节的方法分类改一下", "outline")["kind"] == "regenerate_outline"
    assert parse_human_intent("摘要太啰嗦", "draft")["kind"] == "revise"
    assert parse_human_intent("合并挑战章节与结论的重复", "review")["kind"] == "revise"


def test_parse_config_change():
    assert _parse_config_change("轮次改为5") == {"max_revisions": 5}
    assert _parse_config_change("最多改3轮") == {"max_revisions": 3}
    assert _parse_config_change("摘要精简") == {}


def test_human_feedback_to_contract():
    item = _human_feedback_to_contract("摘要精简到200字", "draft", 1)
    assert item["id"] == "R-HUMAN-01"
    assert item["priority"] == "高"
    assert "摘要精简到200字" in item["problem"]
    assert item["human"] is True


def test_route_after_human_outline_defaults_to_writing():
    assert route_after_human_outline({}) == "paper_writing"
    assert route_after_human_outline({"human_outline_route": "outline_generation"}) == "outline_generation"
    assert route_after_human_outline({"human_outline_route": "literature_review"}) == "literature_review"


def test_outline_scope_change_updates_subtopics_and_routes_to_retrieval():
    """大纲暂停点: 含'子主题'的意见 → 更新子主题并重新检索"""
    import src.graph.pipeline as pl

    orig = pl._extract_scope
    pl._extract_scope = lambda feedback: {"sub_topics": ["联邦学习", "边缘计算"], "keywords": []}
    try:
        g = StateGraph(PipelineState)
        g.add_node("human_outline", human_outline_node)
        g.set_entry_point("human_outline")
        g.add_edge("human_outline", END)
        app = g.compile(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t-scope"}}
        state = {"interactive": True, "paper_outline": "## 1. 引言", "sub_topics": ["旧子主题"]}
        events = list(app.stream(state, cfg))
        assert "__interrupt__" in events[-1]
        for _ in app.stream(Command(resume="增加子主题：联邦学习、边缘计算"), cfg):
            pass
        final = app.get_state(cfg).values
        assert final["sub_topics"] == ["联邦学习", "边缘计算"]
        assert final["human_outline_route"] == "literature_review"
        assert final["outline_iteration"] == 0
    finally:
        pl._extract_scope = orig


def test_outline_config_change_updates_max_revisions():
    """大纲暂停点: 含'轮次'的意见 → 更新最大修订轮次并进入撰写"""
    g = StateGraph(PipelineState)
    g.add_node("human_outline", human_outline_node)
    g.set_entry_point("human_outline")
    g.add_edge("human_outline", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-config"}}
    state = {"interactive": True, "paper_outline": "## 1. 引言", "max_revisions": 3}
    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1]
    for _ in app.stream(Command(resume="轮次改为5"), cfg):
        pass
    final = app.get_state(cfg).values
    assert final["max_revisions"] == 5
    assert final["human_outline_route"] == "paper_writing"


def test_draft_feedback_becomes_contract_item():
    """初稿暂停点: 输入修改意见 → 转为修订契约条目 (首轮修订执行)"""
    g = StateGraph(PipelineState)
    g.add_node("human_draft", human_draft_node)
    g.set_entry_point("human_draft")
    g.add_edge("human_draft", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-draft-fb"}}
    state = {"interactive": True, "paper_draft": "草稿", "revision_count": 0}
    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1]
    for _ in app.stream(Command(resume="摘要太啰嗦，精简到200字"), cfg):
        pass
    final = app.get_state(cfg).values
    assert final["human_revision_contract"][0]["id"] == "R-HUMAN-01"
    assert "摘要太啰嗦" in final["human_revision_contract"][0]["problem"]
    assert final["human_feedback"] == ""


def test_review_feedback_becomes_contract_and_revise():
    """审稿暂停点: 输入修改意见 → 继续修订 + 转为修订契约条目"""
    g = StateGraph(PipelineState)
    g.add_node("human_review", human_review_node)
    g.set_entry_point("human_review")
    g.add_conditional_edges(
        "human_review",
        should_continue_review,
        {"paper_writing": END, "end": END},
    )
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-review-fb"}}
    state = {
        "interactive": True,
        "review_score": 30,
        "review_report": "内容",
        "revision_count": 1,
        "max_revisions": 3,
        "error": None,
    }
    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1]
    for _ in app.stream(Command(resume="挑战章节和结论有重复，请合并"), cfg):
        pass
    final = app.get_state(cfg).values
    assert final["human_review_decision"] == "revise"
    assert final["human_revision_contract"][0]["id"] == "R-HUMAN-01"
    assert "挑战章节和结论有重复" in final["human_revision_contract"][0]["problem"]


def test_pending_human_contract_forces_revision():
    """机器门禁已达标 (45/50) 但有待处理人工要求 → 仍强制修订执行"""
    state = {
        "review_score": 45,
        "revision_count": 0,
        "max_revisions": 3,
        "human_review_decision": "revise",
        "human_revision_contract": [{"id": "R-HUMAN-01"}],
        "error": None,
    }
    assert should_continue_review(state) == "paper_writing"


def test_pending_human_contract_respects_max_revisions():
    """已达最大轮次 + 待处理人工要求 → 结束 (防止死循环)"""
    state = {
        "review_score": 45,
        "revision_count": 3,
        "max_revisions": 3,
        "human_review_decision": "revise",
        "human_revision_contract": [{"id": "R-HUMAN-01"}],
        "error": None,
    }
    assert should_continue_review(state) == "end"


def test_increment_revision_merges_and_clears_human_contract():
    """increment_revision 把人工意见作为最高优先级契约条目, 且执行后清空队列"""
    import src.graph.pipeline as pl

    orig = pl._resolve_review_suggestions
    pl._resolve_review_suggestions = lambda report, topic, refs, prev_blocked=None: ([], [], [])
    try:
        state = {
            "research_topic": "测试",
            "review_report": "审稿意见: 引言不够清晰",
            "paper_draft": "旧版论文内容",
            "revision_count": 0,
            "verified_references": [],
            "human_revision_contract": [
                {"id": "R-HUMAN-01", "status": "未解决", "priority": "高",
                 "problem": "（用户人工要求）摘要要更精简", "evidence": "用户意见", "human": True},
            ],
        }
        r = increment_revision(state)
        assert r["revision_contract"][0]["id"] == "R-HUMAN-01"
        assert "摘要要更精简" in r["revision_prompt"]
        assert r["human_revision_contract"] == []
    finally:
        pl._resolve_review_suggestions = orig


if __name__ == "__main__":
    from src.utils.console import ensure_utf8_console

    ensure_utf8_console()
    tests = [
        test_helpers_classify_responses,
        test_outline_node_noop_when_not_interactive,
        test_draft_node_noop_when_not_interactive,
        test_review_node_noop_when_not_interactive,
        test_finalize_decision_overrides_machine_gate,
        test_no_decision_falls_through_to_machine_gate,
        test_review_interrupt_resume_finalize_roundtrip,
        test_review_interrupt_resume_revise_roundtrip,
        test_draft_node_skips_revision_rounds,
        test_outline_feedback_recorded,
        test_parse_human_intent_kinds,
        test_parse_config_change,
        test_human_feedback_to_contract,
        test_route_after_human_outline_defaults_to_writing,
        test_outline_scope_change_updates_subtopics_and_routes_to_retrieval,
        test_outline_config_change_updates_max_revisions,
        test_draft_feedback_becomes_contract_item,
        test_review_feedback_becomes_contract_and_revise,
        test_pending_human_contract_forces_revision,
        test_pending_human_contract_respects_max_revisions,
        test_increment_revision_merges_and_clears_human_contract,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
