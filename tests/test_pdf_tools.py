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

from src.tools.pdf_fetcher import extract_arxiv_id, download_arxiv_pdf
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


if __name__ == "__main__":
    tests = [
        test_extract_arxiv_id_abs,
        test_extract_arxiv_id_pdf,
        test_extract_arxiv_id_invalid,
        test_extract_arxiv_id_cs_prefix,
        test_chunk_text_short,
        test_chunk_text_long,
        test_chunk_text_empty,
        test_chunk_paper_fulltext,
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
