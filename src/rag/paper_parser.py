from __future__ import annotations

"""PDF 全文解析

两种解析器:
1. GROBID (首选, 结构化): 需要本地/远程 GROBID 服务, 通过 scipdf_parser 调用
2. PyMuPDF (回退, 纯文本): 无外部依赖

参考: papercast (arXiv + GROBID 流水线), scipdf_parser
"""

import os
import tempfile
import warnings
from pathlib import Path
from typing import Optional

from src.config import GROBID_BASE_URL

# 抑制 scipdf 内部 BeautifulSoup 用 HTML 解析器解析 XML 的噪音警告
# (XMLParsedAsHTMLWarning 不影响功能, 但会刷屏输出;
#  注意必须按 category 过滤, 按 message 匹配实际警告文本不含警告类名)
try:
    from bs4 import XMLParsedAsHTMLWarning as _XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=_XMLParsedAsHTMLWarning)
except ImportError:
    warnings.filterwarnings("ignore", category=UserWarning, module="scipdf")

try:
    import fitz
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

HAS_SCIPDF = False
_scipdf = None
if GROBID_BASE_URL:
    try:
        import scipdf as _scipdf  # type: ignore
        HAS_SCIPDF = True
    except ImportError:
        HAS_SCIPDF = False


def extract_text_from_pdf(pdf_path: str, max_chars: int = 50000) -> str:
    """提取论文全文（优先 GROBID 结构化解析，回退 PyMuPDF）"""
    # GROBID 结构化解析（可分离标题/摘要/正文/参考文献）
    if HAS_SCIPDF:
        try:
            # scipdf 0.5x: parse_pdf 返回 TEI XML 字符串, parse_pdf_to_dict 返回结构化 dict
            parse_fn = getattr(_scipdf, "parse_pdf_to_dict", None) or _scipdf.parse_pdf
            if parse_fn is _scipdf.parse_pdf_to_dict:
                article = parse_fn(pdf_path, fulltext=True, grobid_url=GROBID_BASE_URL)
            else:
                article = parse_fn(pdf_path, fulltext=True, grobid_url=GROBID_BASE_URL)
            sections = []
            if article.get("abstract"):
                sections.append("Abstract: " + article["abstract"])
            for sec in article.get("sections", []):
                heading = sec.get("heading", "")
                text = sec.get("text", "")
                if text:
                    sections.append(f"{heading}\n{text}")
            if article.get("references"):
                refs = "\n".join(r.get("title", "") for r in article["references"])
                sections.append("References:\n" + refs)
            text = "\n\n".join(sections)
            if len(text.strip()) > 500:
                return text[:max_chars]
        except Exception:
            pass  # 回退到 PyMuPDF

    if not HAS_PYMUPDF:
        raise ImportError(
            "No PDF parser available. Install pymupdf, or configure GROBID_BASE_URL + scipdf_parser."
        )

    doc = fitz.open(pdf_path)
    text_parts = []
    total = 0
    for page in doc:
        page_text = page.get_text()
        text_parts.append(page_text)
        total += len(page_text)
        if total >= max_chars:
            break
    doc.close()
    return "\n\n".join(text_parts)[:max_chars]


def extract_text_from_bytes(pdf_bytes: bytes, max_chars: int = 50000) -> str:
    if not (HAS_PYMUPDF or HAS_SCIPDF):
        raise ImportError("No PDF parser available.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        return extract_text_from_pdf(tmp_path, max_chars)
    finally:
        os.unlink(tmp_path)
