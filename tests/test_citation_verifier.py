"""引文验证工具单元测试（离线测试，mock 网络请求）

运行:
    python -m pytest tests/test_citation_verifier.py -v
或（无 pytest）:
    python tests/test_citation_verifier.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch, MagicMock
from src.tools.citation_verifier import (
    CitationRecord,
    extract_references_from_draft,
    check_inline_citation_coverage,
    verify_single_citation,
    _title_similarity,
    verify_draft_citations,
)


def test_title_similarity():
    assert _title_similarity("Attention Is All You Need", "Attention Is All You Need") == 1.0
    assert _title_similarity("Attention Is All You Need", "BERT") < 0.3
    score = _title_similarity(
        "Attention Is All You Need",
        "Attention is All you Need: A Transformer Architecture",
    )
    assert score > 0.5


def test_extract_references_numeric():
    draft = """
# 测试论文

正文引用 [1] 和 [2] 的方法。

## 参考文献

[1] Attention Is All You Need (2017)
[2] BERT: Pre-training of Deep Bidirectional Transformers (2019)
"""
    records = extract_references_from_draft(draft)
    assert len(records) == 2
    assert records[0].ref_number == 1
    assert "Attention" in records[0].title
    assert records[1].ref_number == 2


def test_extract_references_bibtex():
    draft = """
# 测试论文

正文引用 [1]。

## 参考文献

[1] @article{vaswani2017, title={Attention Is All You Need}, author={Vaswani, Ashish}}
"""
    records = extract_references_from_draft(draft)
    assert len(records) == 1
    assert records[0].title == "Attention Is All You Need"
    assert "Vaswani" in records[0].authors


def test_check_coverage():
    draft = """
正文引用 [1] [2] [3] [5]。

## 参考文献

[1] A (2017)
[2] B (2018)
[3] C (2019)
"""
    cov = check_inline_citation_coverage(draft, ref_count=3)
    assert cov["coverage_ok"] is False
    assert cov["unreferenced_citations"] == [5]
    assert cov["uncited_refs"] == []


def test_check_coverage_ok():
    draft = """
正文引用 [1] [2]。

## 参考文献

[1] A (2017)
[2] B (2018)
"""
    cov = check_inline_citation_coverage(draft, ref_count=2)
    assert cov["coverage_ok"] is True


@patch("src.tools.citation_verifier.httpx.get")
def test_verify_single_citation_verified(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "message": {
            "items": [
                {
                    "title": ["Attention Is All You Need"],
                    "DOI": "10.5555/123",
                    "container-title": ["NeurIPS"],
                    "issued": {"date-parts": [[2017]]},
                }
            ]
        }
    }
    mock_get.return_value = mock_resp

    rec = CitationRecord(ref_number=1, title="Attention Is All You Need", year="2017")
    result = verify_single_citation(rec)
    assert result.status == "VERIFIED"
    assert result.doi == "10.5555/123"


@patch("src.tools.citation_verifier.httpx.get")
def test_verify_single_citation_not_found(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"items": []}}
    mock_get.return_value = mock_resp

    rec = CitationRecord(ref_number=1, title="完全虚构的论文标题 XYZXYZ", year="2020")
    result = verify_single_citation(rec)
    assert result.status == "NOT_FOUND"


def test_verify_draft_citations_offline_with_mock():
    draft = """
# 测试论文

正文 [1] [2]。

## 参考文献

[1] Attention Is All You Need (2017)
[2] Some Fake Paper That Does Not Exist (2020)
"""
    with patch("src.tools.citation_verifier.httpx.get") as mock_get:
        # CrossRef: 第一调用返回论文1命中，第二调用返回空
        mock_resp1 = MagicMock()
        mock_resp1.json.return_value = {
            "message": {
                "items": [
                    {
                        "title": ["Attention Is All You Need"],
                        "DOI": "10.5555/aaa",
                        "container-title": ["NeurIPS"],
                        "issued": {"date-parts": [[2017]]},
                    }
                ]
            }
        }
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = {"message": {"items": []}}
        mock_get.side_effect = [mock_resp1, mock_resp2]

        report = verify_draft_citations(draft)

    assert report["total_refs"] == 2
    assert report["verified"] == 1
    assert report["not_found"] >= 1
    assert len(report["hallucinated_refs"]) >= 1


if __name__ == "__main__":
    tests = [
        test_title_similarity,
        test_extract_references_numeric,
        test_extract_references_bibtex,
        test_check_coverage,
        test_check_coverage_ok,
        test_verify_single_citation_verified,
        test_verify_single_citation_not_found,
        test_verify_draft_citations_offline_with_mock,
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
