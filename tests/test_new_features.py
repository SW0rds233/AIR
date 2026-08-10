"""新功能测试：成本追踪 / 证据账本 / 引用预验证（离线测试）

运行:
    python tests/test_new_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch, MagicMock

from src.utils.cost_tracker import estimate_cost, UsageTracker
from src.agents.citation_checker import build_evidence_ledger
from src.agents.citation_prechecker import (
    verify_reference_list,
    build_verified_reference_sheet,
)


def test_estimate_cost_deepseek():
    cost = estimate_cost("deepseek-chat", 1000000, 1000000)
    assert abs(cost - 1.37) < 0.001


def test_estimate_cost_gpt4o():
    cost = estimate_cost("gpt-4o", 1000000, 1000000)
    assert abs(cost - 12.5) < 0.001


def test_estimate_cost_unknown_model():
    cost = estimate_cost("some-unknown-model", 1000, 1000)
    assert cost > 0  # 使用默认价格


def test_usage_tracker_accumulate():
    t = UsageTracker()
    t.add_call("deepseek-chat", {"input_tokens": 1000, "output_tokens": 500}, "review")
    t.add_call("deepseek-chat", {"input_tokens": 2000, "output_tokens": 1000}, "write")
    s = t.summary()
    assert s["total_calls"] == 2
    assert s["total_input_tokens"] == 3000
    assert s["total_output_tokens"] == 1500
    assert s["total_cost_usd"] > 0
    assert "review" in s["by_stage"]
    assert "write" in s["by_stage"]


def test_usage_tracker_ignores_none():
    t = UsageTracker()
    t.add_call("deepseek-chat", None, "review")
    assert t.summary()["total_calls"] == 0


def test_evidence_ledger_verified():
    report = {
        "total_refs": 1,
        "verified": 1,
        "ambiguous": 0,
        "not_found": 0,
        "errors": 0,
        "results": [
            {
                "ref_number": 1,
                "claimed_title": "Attention Is All You Need",
                "detail": "Verified via OpenAlex",
                "matched_title": "Attention Is All You Need",
                "similarity": 1.0,
                "status": "VERIFIED",
                "doi": "10.1234/xyz",
                "venue": "",
            }
        ],
        "coverage": {
            "inline_citations": 1,
            "ref_entries": 1,
            "unreferenced_citations": [],
            "uncited_refs": [],
            "coverage_ok": True,
        },
    }
    ledger = build_evidence_ledger(report)
    assert "证据账本" in ledger
    assert "VERIFIED" in ledger
    assert "10.1234/xyz" in ledger
    assert "所有引用均已通过验证" in ledger


def test_evidence_ledger_hallucinated():
    report = {
        "total_refs": 1,
        "verified": 0,
        "ambiguous": 0,
        "not_found": 1,
        "errors": 0,
        "results": [
            {
                "ref_number": 1,
                "claimed_title": "Fake Paper",
                "detail": "Best match below 0.4",
                "matched_title": "",
                "similarity": 0.1,
                "status": "NOT_FOUND",
                "doi": "",
                "venue": "",
            }
        ],
        "coverage": {
            "inline_citations": 1,
            "ref_entries": 1,
            "unreferenced_citations": [],
            "uncited_refs": [],
            "coverage_ok": True,
        },
    }
    ledger = build_evidence_ledger(report)
    assert "疑似虚构引用" in ledger


@patch("src.agents.citation_prechecker.verify_single_citation")
def test_verify_reference_list(mock_verify):
    def fake_verify(rec):
        r = MagicMock()
        if rec.title.startswith("Real"):
            r.status, r.matched_title, r.doi, r.similarity = (
                "VERIFIED", rec.title, "10.1/abc", 0.9,
            )
        else:
            r.status, r.matched_title, r.doi, r.similarity = (
                "NOT_FOUND", "", "", 0.1,
            )
        return r

    mock_verify.side_effect = fake_verify
    papers = [
        {"title": "Real Paper A", "authors": "A", "year": "2020"},
        {"title": "Real Paper B", "authors": "B", "year": "2021"},
        {"title": "Fake Paper C", "authors": "C", "year": "2022"},
    ]
    result = verify_reference_list(papers, limit=3)
    assert len(result["verified"]) == 2
    assert len(result["not_found"]) == 1
    assert "候选引用预验证" in result["report_md"]


def test_build_verified_reference_sheet():
    verified = [
        {"ref_number": 1, "title": "Real Paper", "authors": "A", "year": "2020",
         "bibtex": "@article{a2020}"},
    ]
    sheet = build_verified_reference_sheet(verified)
    assert "可信参考文献清单" in sheet
    assert "Real Paper" in sheet
    assert "@article{a2020}" in sheet
    assert "只能引用" in sheet


if __name__ == "__main__":
    tests = [
        test_estimate_cost_deepseek,
        test_estimate_cost_gpt4o,
        test_estimate_cost_unknown_model,
        test_usage_tracker_accumulate,
        test_usage_tracker_ignores_none,
        test_evidence_ledger_verified,
        test_evidence_ledger_hallucinated,
        test_verify_reference_list,
        test_build_verified_reference_sheet,
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
