"""流水线逻辑测试：评分阈值换算 / 修订指令传递 / max_revisions 生效（离线测试）

运行:
    python tests/test_pipeline_logic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph.pipeline import should_continue_review, increment_revision


def _state(score=45, rev=0, hallucinated=None, not_found=0, max_rev=3):
    return {
        "review_score": score,
        "revision_count": rev,
        "hallucinated_refs": hallucinated or [],
        "citation_not_found_count": not_found,
        "max_revisions": max_rev,
        "error": None,
    }


def test_high_score_no_revision():
    """45/50 (≈90/100) 高分且无虚构引用 → 结束"""
    assert should_continue_review(_state(score=45)) == "end"


def test_low_score_triggers_revision():
    """30/50 (≈60/100) 低分 → 修订"""
    assert should_continue_review(_state(score=30)) == "paper_writing"


def test_high_score_with_hallucination():
    """高分但有虚构引用 → 修订"""
    assert should_continue_review(_state(score=45, hallucinated=["fake"], not_found=1)) == "paper_writing"


def test_max_revisions_respected():
    """达到 max_revisions → 结束"""
    assert should_continue_review(_state(score=30, rev=1, max_rev=2)) == "paper_writing"
    assert should_continue_review(_state(score=30, rev=2, max_rev=2)) == "end"


def test_error_ends():
    """有错误 → 结束"""
    s = _state(score=30)
    s["error"] = "boom"
    assert should_continue_review(s) == "end"


def test_increment_revision_writes_prompt():
    """修订指令包含审稿意见/引文核查/旧版论文"""
    state = {
        "research_topic": "测试",
        "review_report": "审稿意见: 引言不够清晰",
        "citation_report": {"report_md": "引文核查: 1条虚构"},
        "paper_draft": "旧版论文内容",
        "revision_count": 0,
    }
    r = increment_revision(state)
    assert r["revision_count"] == 1
    assert "revision_prompt" in r
    assert "审稿意见" in r["revision_prompt"]
    assert "引文核查" in r["revision_prompt"]
    assert "旧版论文" in r["revision_prompt"]


if __name__ == "__main__":
    tests = [
        test_high_score_no_revision,
        test_low_score_triggers_revision,
        test_high_score_with_hallucination,
        test_max_revisions_respected,
        test_error_ends,
        test_increment_revision_writes_prompt,
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
