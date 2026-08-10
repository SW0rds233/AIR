"""GB/T 7714 参考文献格式化 + 图表质量检测测试（离线）

运行:
    python tests/test_references_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch, MagicMock

from src.rag.reference_formatter import (
    format_gbt7714_entry,
    build_references_section,
    strip_references_section,
    attach_references_section,
)
from src.rag.figure_llm import check_png_quality, _extract_review_issues
from src.tools.citation_verifier import (
    _parse_ref_content,
    extract_references_from_draft,
    verify_draft_citations,
)
from src.tools.venue_resolver import (
    looks_like_real_venue,
    resolve_venue,
    resolve_venue_batch,
)


# ---------- 出处解析 ----------

def test_looks_like_real_venue():
    assert looks_like_real_venue("IEEE Transactions on Wireless Communications")
    assert not looks_like_real_venue("arXiv")
    assert not looks_like_real_venue("OpenAlex")
    assert not looks_like_real_venue("Semantic Scholar")
    assert not looks_like_real_venue("")
    assert not looks_like_real_venue("https://example.com")


@patch("src.tools.venue_resolver.httpx.get")
def test_resolve_venue_via_crossref(mock_get):
    """DOI → CrossRef container-title + 卷期页码"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "container-title": ["IEEE Transactions on Wireless Communications"],
            "volume": "43",
            "issue": "5",
            "page": "100-110",
        }
    }
    mock_get.return_value = mock_resp
    info = resolve_venue({"title": "T", "doi": "10.1000/xyz", "url": ""})
    assert info["venue"] == "IEEE Transactions on Wireless Communications"
    assert info["volume"] == "43"
    assert info["pages"] == "100-110"


@patch("src.tools.venue_resolver.httpx.get")
def test_resolve_venue_skips_existing(mock_get):
    """已有真实出处 → 零 API 调用"""
    info = resolve_venue({"title": "T", "source": "NeurIPS", "venue": "NeurIPS", "doi": "10.1/x"})
    assert info["venue"] == "NeurIPS"
    mock_get.assert_not_called()


@patch("src.tools.venue_resolver.httpx.get")
def test_resolve_venue_arxiv_journal_ref(mock_get):
    """arXiv journal_ref 元素 → 期刊名"""
    xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <arxiv:journal_ref>IEEE Transactions on Information Forensics and Security</arxiv:journal_ref>
    <arxiv:doi>10.1000/abc</arxiv:doi>
  </entry>
</feed>"""
    arxiv_resp = MagicMock()
    arxiv_resp.status_code = 200
    arxiv_resp.text = xml
    # arXiv DOI 的 CrossRef 跟进请求返回 404 (无卷期页码)
    cross_resp = MagicMock()
    cross_resp.status_code = 404
    mock_get.side_effect = [arxiv_resp, cross_resp]
    info = resolve_venue({"title": "T", "doi": "", "url": "https://arxiv.org/abs/2105.04492"})
    assert info["venue"] == "IEEE Transactions on Information Forensics and Security"


def test_resolve_venue_no_sources():
    info = resolve_venue({"title": "T", "doi": "", "url": ""})
    assert info == {}


# ---------- venue 感知的 GB/T 7714 ----------

def test_format_with_venue_and_volume():
    ref = {
        "ref_number": 5,
        "title": "Deep Learning for RF Fingerprinting",
        "authors": "J Smith, K Brown",
        "year": "2020",
        "venue": "IEEE Transactions on Wireless Communications",
        "volume": "43",
        "issue": "5",
        "pages": "100-110",
        "doi": "10.1000/xyz",
    }
    out = format_gbt7714_entry(ref)
    assert "[J]" in out
    assert "IEEE Transactions on Wireless Communications" in out
    assert "43(5): 100-110" in out   # 卷(期): 页码
    assert "DOI: 10.1000/xyz" in out


def test_format_conference_venue():
    ref = {
        "ref_number": 6,
        "title": "RF Fingerprinting at the Physical Layer",
        "authors": "张三",
        "year": "2021",
        "venue": "IEEE Conference on Communications (ICC)",
    }
    out = format_gbt7714_entry(ref)
    assert "[C]" in out               # 会议 → [C]


def test_format_arxiv_when_no_venue():
    ref = {
        "ref_number": 7,
        "title": "Preprint Paper",
        "authors": "A B",
        "year": "2024",
        "venue": "",
        "url": "https://arxiv.org/abs/2411.06925",
        "api_source": "arXiv",
    }
    out = format_gbt7714_entry(ref)
    assert "[EB/OL]" in out
    assert "arXiv: 2411.06925" in out


# ---------- GB/T 7714 格式化 ----------

def test_format_arxiv_entry():
    ref = {
        "ref_number": 1,
        "title": "Practical Fingerprinting of RF Devices in the Wild",
        "authors": "Ashish Vaswani, Noam Shazeer, Niki Parmar",
        "year": "2021",
        "url": "https://arxiv.org/abs/2105.04492",
        "doi": "",
        "source": "arXiv",
        "api_source": "arXiv",
    }
    out = format_gbt7714_entry(ref)
    assert out.startswith("[1] ")
    assert "VASWANI A" in out          # 英文作者姓氏大写+首字母
    assert "Practical Fingerprinting" in out
    assert "[EB/OL]" in out            # arXiv → 电子资源
    assert "arXiv: 2105.04492" in out  # 出处含 arXiv id
    assert "2021" in out


def test_format_journal_entry_with_doi():
    ref = {
        "ref_number": 2,
        "title": "Deep Learning for RF Fingerprinting",
        "authors": "张三, 李四",
        "year": "2020",
        "url": "",
        "doi": "10.1000/xyz",
        "source": "IEEE Transactions on Wireless Communications",
    }
    out = format_gbt7714_entry(ref)
    assert out.startswith("[2] ")
    assert "张三, 李四" in out         # 中文作者保持原名
    assert "[J]" in out                # 期刊
    assert "IEEE Transactions on Wireless Communications" in out
    assert "DOI: 10.1000/xyz" in out
    assert "2020" in out


def test_format_many_authors_et_al():
    ref = {
        "ref_number": 3,
        "title": "A Survey of Wireless Fingerprinting",
        "authors": "J Smith, K Brown, C Lee, D Wang, E Zhao",
        "year": "2022",
        "doi": "10.1/abc",
        "source": "ACM Computing Surveys",
    }
    out = format_gbt7714_entry(ref)
    assert ", 等." in out              # 超过 3 作者 → 等
    assert "SMITH J, BROWN K, LEE C" in out


def test_build_and_attach_references_section():
    refs = [
        {"ref_number": 1, "title": "Paper A", "authors": "张三", "year": "2020",
         "url": "https://arxiv.org/abs/2001.00001", "source": "arXiv", "api_source": "arXiv"},
        {"ref_number": 2, "title": "Paper B", "authors": "Smith, J", "year": "2021",
         "doi": "10.2/b", "source": "IEEE Journal"},
    ]
    draft = "正文内容 [1] 和 [2] 的方法。\n\n## 结论\n结束。"
    new_draft = attach_references_section(draft, refs)
    assert "## 参考文献" in new_draft
    assert "[1] " in new_draft and "[2] " in new_draft
    # 参考文献在全文最后
    assert new_draft.rindex("## 参考文献") > new_draft.rindex("## 结论")
    # 重复附加不产生多个章节
    again = attach_references_section(new_draft, refs)
    assert again.count("## 参考文献") == 1


def test_strip_references_section():
    draft = "正文\n\n## 参考文献\n\n[1] junk\n[2] junk\n"
    cleaned = strip_references_section(draft)
    assert "## 参考文献" not in cleaned
    assert "正文" in cleaned


# ---------- GB/T 7714 引文提取 ----------

def test_parse_gbt7714_line():
    rec = _parse_ref_content(
        1,
        "VASWANI A, SHAZEER N, PARMAR N, 等. Attention is all you need[J]. NeurIPS, 2017. DOI: 10.5555/123.",
    )
    assert rec.title == "Attention is all you need"
    assert rec.year == "2017"


def test_parse_gbt7714_chinese():
    rec = _parse_ref_content(2, "张三, 李四. 基于深度学习的射频指纹识别[EB/OL]. arXiv: 2411.06925, 2024.")
    assert rec.title == "基于深度学习的射频指纹识别"
    assert rec.year == "2024"


def test_extract_from_deterministic_refs():
    """确定性参考文献章节 → 提取 + 编号直连匹配清单"""
    draft = (
        "正文 [1] [2]。\n\n## 参考文献\n\n"
        "[1] VASWANI A, 等. Attention is all you need[J]. NeurIPS, 2017. DOI: 10.5555/123.\n"
        "[2] 张三, 李四. 射频指纹识别[EB/OL]. arXiv: 2411.06925, 2024.\n"
    )
    verified = [
        {"ref_number": 1, "title": "Attention Is All You Need", "authors": "Vaswani",
         "year": "2017", "doi": "10.5555/123"},
        {"ref_number": 2, "title": "射频指纹识别研究综述", "authors": "张三",
         "year": "2024", "doi": ""},
    ]
    records, has_section = extract_references_from_draft(draft, return_has_section=True)
    assert has_section is True
    assert len(records) == 2
    report = verify_draft_citations(draft, verified)
    assert report["verified"] == 2
    assert report["not_found"] == 0
    assert report["total_refs"] == 2


# ---------- PNG 质量检测 ----------

def test_png_quality_blank_rejected(tmp_path=None):
    import os
    from PIL import Image

    d = Path(sys._getframe(0).f_globals.get("__file__")).resolve().parent
    p = d / "_blank_test.png"
    Image.new("RGB", (800, 600), (255, 255, 255)).save(p)
    try:
        ok, reason = check_png_quality(str(p))
        assert ok is False
        assert "空白" in reason or "纯色" in reason
    finally:
        if p.exists():
            p.unlink()


def test_png_quality_too_small():
    from PIL import Image

    d = Path(sys._getframe(0).f_globals.get("__file__")).resolve().parent
    p = d / "_tiny_test.png"
    Image.new("RGB", (100, 50), (0, 0, 0)).save(p)
    try:
        ok, reason = check_png_quality(str(p))
        assert ok is False
        assert "尺寸" in reason or "过小" in reason
    finally:
        if p.exists():
            p.unlink()


def test_png_quality_good_image():
    import numpy as np
    from PIL import Image

    d = Path(sys._getframe(0).f_globals.get("__file__")).resolve().parent
    p = d / "_good_test.png"
    arr = np.random.default_rng(0).integers(0, 255, (600, 800, 3), dtype=np.uint8)
    Image.fromarray(arr).save(p)
    try:
        ok, reason = check_png_quality(str(p))
        assert ok is True, reason
    finally:
        if p.exists():
            p.unlink()


def test_png_quality_missing():
    ok, reason = check_png_quality("nonexistent_file_xyz.png")
    assert ok is False
    assert "不存在" in reason


# ---------- 审阅意见解析 ----------

def test_extract_review_issues():
    text = "问题列表:\n- 缺少 x 轴标签\n- 图例过多\n（若全部合规则输出: 问题列表:\n- 无）"
    issues = _extract_review_issues(text)
    assert "缺少 x 轴标签" in issues
    assert "图例过多" in issues
    assert "若全部合规则" not in issues  # 模板回显不作为问题


def test_extract_review_issues_none():
    assert _extract_review_issues("问题列表:\n- 无") == []
    assert _extract_review_issues("") == []


if __name__ == "__main__":
    tests = [
        test_looks_like_real_venue,
        test_resolve_venue_via_crossref,
        test_resolve_venue_skips_existing,
        test_resolve_venue_arxiv_journal_ref,
        test_resolve_venue_no_sources,
        test_format_with_venue_and_volume,
        test_format_conference_venue,
        test_format_arxiv_when_no_venue,
        test_format_arxiv_entry,
        test_format_journal_entry_with_doi,
        test_format_many_authors_et_al,
        test_build_and_attach_references_section,
        test_strip_references_section,
        test_parse_gbt7714_line,
        test_parse_gbt7714_chinese,
        test_extract_from_deterministic_refs,
        test_png_quality_blank_rejected,
        test_png_quality_too_small,
        test_png_quality_good_image,
        test_png_quality_missing,
        test_extract_review_issues,
        test_extract_review_issues_none,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"FAIL: {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
