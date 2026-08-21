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
    strip_evidence_markers,
    attach_references_section,
    renumber_citations,
    find_citation_numbers,
    is_published_ref,
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
)


# ---------- 出处解析 ----------

def test_looks_like_real_venue():
    assert looks_like_real_venue("IEEE Transactions on Wireless Communications")
    assert not looks_like_real_venue("arXiv")
    assert not looks_like_real_venue("OpenAlex")
    assert not looks_like_real_venue("Semantic Scholar")
    assert not looks_like_real_venue("")
    assert not looks_like_real_venue("https://example.com")


@patch("src.tools.venue_resolver.get_with_retry")
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


@patch("src.tools.venue_resolver.get_with_retry")
def test_resolve_venue_skips_existing(mock_get):
    """已有真实出处 → 零 API 调用"""
    info = resolve_venue({"title": "T", "source": "NeurIPS", "venue": "NeurIPS", "doi": "10.1/x"})
    assert info["venue"] == "NeurIPS"
    mock_get.assert_not_called()


@patch("src.tools.venue_resolver.get_with_retry")
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
    assert ", et al." in out           # 超过 3 作者英文 → et al. (GB/T 7714 外文文献)
    assert "SMITH J, BROWN K, LEE C" in out


def test_format_early_access_journal_has_access_date():
    """无卷期的在线优先出版期刊必须带引用日期。

    审稿人曾反复以"在线优先出版缺引用日期/格式不规范"为由把参考文献维度
    判为 Critical, 而参考文献章节由系统生成、Writer 无法修改 → 在源头补齐。
    """
    import re

    ref = {
        "ref_number": 23,
        "title": "Federated Learning for RF Fingerprinting",
        "authors": "J Smith",
        "year": "2026",
        "venue": "IEEE Internet of Things Journal",
        "doi": "10.1109/jiot.2026.3710099",
    }
    out = format_gbt7714_entry(ref)
    assert "[J]" in out
    assert re.search(r"在线优先出版\[\d{4}-\d{2}-\d{2}\]", out)


def test_format_strips_embedded_venue_year():
    """venue 内嵌年份导致年份重复: 'Journal 2021, 2020, 8(10)' → 'Journal, 2020, 8(10)'
    (实测 [42] 被审稿人判格式异常, 参考文献维度无法上 4 分)"""
    ref = {"ref_number": 42, "title": "IoT Security Using RF-DNA Fingerprints",
           "authors": "D Reising, J Cancellieri",
           "venue": "IEEE Internet of Things Journal 2021", "year": "2020",
           "volume": "8", "issue": "10", "pages": "8356-8371", "doi": "10.1109/x"}
    out = format_gbt7714_entry(ref)
    assert "Journal 2021" not in out
    assert ", 2020, 8(10): 8356-8371" in out


def test_format_strips_conference_location():
    """GB/T 7714 会议条目不含地点: '…(ETFA), Stuttgart, Germany' → '…(ETFA)'
    (实测 [58] 被审稿人判'会议地点位置违规')"""
    ref = {"ref_number": 58, "title": "Towards Adaptive RF Fingerprint-based Authentication",
           "authors": "E Lomba, R Severino",
           "venue": "IEEE 27th International Conference on Emerging Technologies and Factory Automation (ETFA), Stuttgart, Germany, 2023",
           "year": "2023", "doi": "10.1109/y"}
    out = format_gbt7714_entry(ref)
    assert "Stuttgart" not in out and "Germany" not in out
    assert "(ETFA), 2023" in out


def test_sort_and_merge_citation_groups():
    """同一处多引用必须升序: 相邻独立括号合并, 组内排序去重 (GB/T 7714 顺序编码制)"""
    from src.rag.reference_formatter import sort_and_merge_citation_groups

    # 实测案例: 重编号后 [13][11][1] 未按升序排列
    assert sort_and_merge_citation_groups("贡献[13][11][1]。") == "贡献[1,11,13]。"
    assert sort_and_merge_citation_groups("见[5,3]与[2, 7]") == "见[3,5]与[2,7]"
    assert sort_and_merge_citation_groups("单个[4]不变") == "单个[4]不变"
    assert sort_and_merge_citation_groups("[3][3]去重") == "[3]去重"
    # 图表占位符与脚注不受影响
    assert sort_and_merge_citation_groups("[图1: 分类]与[^1]脚注") == "[图1: 分类]与[^1]脚注"
    # 被文字隔开的引用不合并
    assert sort_and_merge_citation_groups("如[3]和[1]所述") == "如[3]和[1]所述"


def test_renumber_citations_sorts_multi_citation_groups():
    from src.rag.reference_formatter import renumber_citations

    draft = (
        "# 标题\n\n本文贡献[13][11][1]。\n\n后文再次引用[13]。\n\n"
        "## 参考文献\n\n[1] A.\n[11] B.\n[13] C.\n"
    )
    out = renumber_citations(draft)
    # 首次出现按升序阅读顺序: 1→1, 11→2, 13→3; 多引用组升序输出
    assert "本文贡献[1,2,3]。" in out
    assert "后文再次引用[3]。" in out


def test_renumber_draft_and_refs_sorts_after_remapping():
    """重映射可能产生新乱序 (旧[3,5]→新[2,1]), 必须在映射后再次排序"""
    from src.rag.reference_formatter import renumber_draft_and_refs

    draft = "# 标题\n\n先引用[5]，再同时引用[3][5]。\n\n## 参考文献\n\n[3] B.\n[5] A.\n"
    refs = [{"ref_number": 3, "title": "B"}, {"ref_number": 5, "title": "A"}]
    new_draft, new_refs = renumber_draft_and_refs(draft, refs)
    # 首次出现: 5→1, 3→2 → 组 [3,5] 映射为 [2,1] → 排序后 [1,2]
    assert "再同时引用[1,2]。" in new_draft
    by_num = {r["ref_number"]: r["title"] for r in new_refs}
    assert by_num[1] == "A" and by_num[2] == "B"


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


def test_strip_evidence_markers():
    draft = "方法难以统一建模[6][缺证据]。实现高效利用[缺证据：需补充ISAC文献]。正常[3]。"
    cleaned = strip_evidence_markers(draft)
    assert "[缺证据]" not in cleaned
    assert "缺证据" not in cleaned
    assert "难以统一建模[6]" in cleaned
    assert "正常[3]" in cleaned


def test_find_citation_numbers_multinumber():
    assert find_citation_numbers("文本[2,7]和[1]与[1,4,8]。") == [2, 7, 1, 1, 4, 8]


def test_is_published_ref_rejects_preprint_doi():
    assert is_published_ref({"doi": "10.36227/techrxiv.21569253.v1"}) is False  # TechRxiv
    assert is_published_ref({"doi": "10.2139/ssrn.1234"}) is False  # SSRN
    assert is_published_ref({"doi": "10.48550/arXiv.2211.10379"}) is False  # arXiv
    assert is_published_ref({"url": "https://arxiv.org/abs/2211.10379"}) is False
    assert is_published_ref({"doi": "10.1109/tifs.2024.3515796"}) is True  # 正式期刊
    assert is_published_ref({"api_source": "人工导入"}) is True


def test_renumber_citations_multinumber():
    """多编号引用 [2,7] 必须先于 [1] 出现时, 重编号应遵循首次出现顺序"""
    draft = (
        "面临挑战[2,7]。需求突出[1]。再次[2]。\n\n"
        "## 参考文献\n\n"
        "[1] Paper A\n[2] Paper B\n[7] Paper G\n"
    )
    r = renumber_citations(draft)
    body = r.split("## 参考文献")[0]
    # 首次出现顺序: 2→1, 7→2, 1→3 → 正文应为 [1,2] [3] [1]
    assert "[1,2]" in body
    assert "[3]" in body
    assert "面临挑战[1,2]。需求突出[3]。再次[1]。" in body.replace("\n", "") or "面临挑战[1,2]。需求突出[3]。再次[1]。" in body
    # 参考文献按新编号重排: 2→1 (Paper B), 7→2 (Paper G), 1→3 (Paper A)
    assert "[1] Paper B" in r
    assert "[2] Paper G" in r
    assert "[3] Paper A" in r


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
        test_format_early_access_journal_has_access_date,
        test_format_strips_embedded_venue_year,
        test_format_strips_conference_location,
        test_sort_and_merge_citation_groups,
        test_renumber_citations_sorts_multi_citation_groups,
        test_renumber_draft_and_refs_sorts_after_remapping,
        test_build_and_attach_references_section,
        test_strip_references_section,
        test_strip_evidence_markers,
        test_find_citation_numbers_multinumber,
        test_is_published_ref_rejects_preprint_doi,
        test_renumber_citations_multinumber,
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
