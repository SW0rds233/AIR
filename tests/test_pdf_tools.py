"""PDF 下载与分块器单元测试（离线测试）

运行:
    python -m pytest tests/test_pdf_tools.py -v
或（无 pytest）:
    python tests/test_pdf_tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.pdf_fetcher import extract_arxiv_id, download_arxiv_pdf, _is_downloadable_oa_url
from src.rag.chunker import chunk_text, chunk_paper_fulltext


def test_extract_arxiv_id_abs():
    assert extract_arxiv_id("https://arxiv.org/abs/2301.12345") == "2301.12345"
    assert extract_arxiv_id("https://arxiv.org/abs/2301.12345v2") == "2301.12345v2"


def test_extract_arxiv_id_pdf():
    assert extract_arxiv_id("https://arxiv.org/pdf/2106.09685") == "2106.09685"


def test_extract_arxiv_id_invalid():
    assert extract_arxiv_id("https://example.com/not-arxiv") is None
    assert extract_arxiv_id("") is None


def test_extract_arxiv_id_cs_prefix():
    assert extract_arxiv_id("https://arxiv.org/abs/cs.CL/0011004") == "cs.CL/0011004"


def test_oa_url_downloadable_judgement():
    """OA 链接可下载判断: 忽略 ?query 参数, 接受 .pdf 与 /pdf 两种结尾;
    付费墙黑名单主机 (IEEE/Elsevier 等) 仍拒绝"""
    # 带 query 参数的 .pdf 直链 (MDPI 等) → 可下载
    assert _is_downloadable_oa_url(
        "https://www.mdpi.com/1424-8220/21/24/8398/pdf?version=1639962946"
    )
    # 无扩展名的 /pdf 路径段 (MDPI) → 可下载
    assert _is_downloadable_oa_url("https://www.mdpi.com/1424-8220/21/24/8398/pdf")
    # 普通 .pdf 直链 → 可下载
    assert _is_downloadable_oa_url("https://downloads.hindawi.com/journals/wcmc/2021/12345.pdf")
    # 黑名单主机 → 拒绝
    assert not _is_downloadable_oa_url("https://ieeexplore.ieee.org/ielx7/123/456/789.pdf")
    assert not _is_downloadable_oa_url("https://www.sciencedirect.com/science/article/pii/123.pdf")
    # HTML 落地页 → 拒绝
    assert not _is_downloadable_oa_url("https://www.mdpi.com/1424-8220/21/24/8398/htm")
    assert not _is_downloadable_oa_url("https://www.nature.com/articles/s41598-020-12345")
    assert not _is_downloadable_oa_url("")



def test_chunk_text_short():
    assert chunk_text("short text") == ["short text"]


def test_chunk_text_long():
    long_text = "This is sentence one. " * 400
    chunks = chunk_text(long_text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    # 全部块拼接应覆盖大部分原文
    joined = "".join(chunks)
    assert len(joined) > len(long_text) * 0.8


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_paper_fulltext():
    chunks = chunk_paper_fulltext("Test Paper", "word " * 5000, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    assert chunks[0]["title"] == "Test Paper"
    assert chunks[0]["chunk_index"] == 0
    assert chunks[1]["chunk_index"] == 1


def test_download_throttles_progress_output():
    """高重复下载提示降噪: 每 5 篇一次进度 + 结束跳过汇总, 而非逐篇打印"""
    import io
    import contextlib
    import time

    import src.tools.pdf_fetcher as pf

    papers = [{"title": f"Paper {i}", "url": f"https://arxiv.org/abs/{2000 + i}.0000{i}"} for i in range(1, 13)]
    orig_arxiv = pf.download_arxiv_pdf
    orig_doi = pf._resolve_pdf_from_doi
    orig_sleep = time.sleep
    pf.download_arxiv_pdf = lambda url, save_dir=None, nice_name=None: None
    pf._resolve_pdf_from_doi = lambda doi, save_dir, nice_name=None: None
    time.sleep = lambda *a, **k: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            pf.download_pdfs_for_papers(papers, limit=20)
        out = buf.getvalue()
        assert "进度 5/" in out
        assert "进度 10/" in out
        assert "跳过 12 篇" in out
        assert "跳过 (" not in out  # 无逐篇跳过
    finally:
        pf.download_arxiv_pdf = orig_arxiv
        pf._resolve_pdf_from_doi = orig_doi
        time.sleep = orig_sleep


if __name__ == "__main__":
    tests = [
        test_extract_arxiv_id_abs,
        test_extract_arxiv_id_pdf,
        test_extract_arxiv_id_invalid,
        test_extract_arxiv_id_cs_prefix,
        test_oa_url_downloadable_judgement,
        test_chunk_text_short,
        test_chunk_text_long,
        test_chunk_text_empty,
        test_chunk_paper_fulltext,
        test_download_throttles_progress_output,
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
