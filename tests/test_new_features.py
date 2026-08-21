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
from src.agents.citation_guard import check_citation_semantics


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


def test_check_citation_semantics_mismatch_removed():
    refs = [
        {"ref_number": 62, "authors": "Zehua Liu, Ning Jia, Yue Cao",
         "title": "Video Swin Transformer"},
        {"ref_number": 17, "authors": "Jiabao Yu, Aiqun Hu",
         "title": "A Robust RF Fingerprinting Approach"},
        {"ref_number": 9, "authors": "Ruiqi Kong, He Chen", "title": "DeepCRF"},
    ]
    draft = (
        "Sankhe等人提出的ORACLE系统进行分类 [62]。\n"
        "Yu等人提出多采样卷积神经网络 [17]，提升鲁棒性。\n"
        "Chen等人提出DeepCRF [9]。\n"
        "结合A与B [17][9]。\n"
    )
    r = check_citation_semantics(draft, refs)
    # 只有 [62] 错配 (Sankhe 不在 Swin Transformer 作者中)
    assert [m["num"] for m in r["mismatches"]] == [62]
    assert "[62]" not in r["repaired_draft"]
    # 正确引用与多引用句子不受影响
    assert "[17]" in r["repaired_draft"]
    assert "[9]" in r["repaired_draft"]


def test_check_citation_semantics_no_false_positive():
    refs = [
        {"ref_number": 1, "authors": "Sankhe Kuldeep, Belgiovine Marco",
         "title": "ORACLE: Optimized Radio classification"},
    ]
    draft = "Sankhe等人提出ORACLE系统 [1]。"
    r = check_citation_semantics(draft, refs)
    assert r["mismatches"] == []
    assert "[1]" in r["repaired_draft"]


def test_check_citation_semantics_bare_deng_mismatch():
    """裸「X等」+ 归属动词: 检出作者错配 (实测案例: 正文 Zeng等[51] 而 [51] 作者为 Yu)"""
    refs = [
        {"ref_number": 51, "authors": "Jiabao Yu, Aiqun Hu, Guiyun Li",
         "title": "A Robust RF Fingerprinting Approach Using Multisampling CNN"},
    ]
    draft = "Zeng等则较早探索了基于深度表示学习的端到端设备识别方法[51]。"
    r = check_citation_semantics(draft, refs)
    assert [m["num"] for m in r["mismatches"]] == [51]
    assert "[51]" not in r["repaired_draft"]


def test_check_citation_semantics_bare_deng_correct_author():
    refs = [
        {"ref_number": 51, "authors": "Jiabao Yu, Aiqun Hu", "title": "Multisampling CNN"},
    ]
    draft = "Yu等提出多采样率卷积神经网络方法[51]。"
    r = check_citation_semantics(draft, refs)
    assert r["mismatches"] == []
    assert "[51]" in r["repaired_draft"]


def test_check_citation_semantics_tech_term_enum_not_author():
    """「Transformer等」「LoRa等」是技术名词枚举, 不得误判为作者引用"""
    refs = [
        {"ref_number": 5, "authors": "Mehdi Saeidi, Saeed Rasti", "title": "RF Fingerprinting"},
    ]
    draft = "Transformer等深度模型也被用于射频指纹识别[5]。LoRa等低功耗技术面临类似挑战[5]。"
    r = check_citation_semantics(draft, refs)
    assert r["mismatches"] == []
    assert r["repaired_draft"].count("[5]") == 2


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
        test_check_citation_semantics_mismatch_removed,
        test_check_citation_semantics_no_false_positive,
        test_check_citation_semantics_bare_deng_mismatch,
        test_check_citation_semantics_bare_deng_correct_author,
        test_check_citation_semantics_tech_term_enum_not_author,
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
