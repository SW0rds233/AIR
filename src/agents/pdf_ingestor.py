from __future__ import annotations

"""PDF 全文摄入节点：下载 PDF → 解析全文 → 分块 → 向量入库

实现参考:
- papercast: arXiv + GROBID/LangChain 流水线
- Corvus: GROBID 解析 + RAG 向量检索
"""

import logging
from typing import Optional

from src.graph.state import PipelineState
from src.tools.pdf_fetcher import download_pdfs_for_papers, download_arxiv_pdf
from src.rag.paper_parser import extract_text_from_pdf
from src.rag.chunker import chunk_paper_fulltext
from src.rag.vector_store import add_fulltext_chunks

logger = logging.getLogger(__name__)


def ingest_pdf_fulltext(
    paper: dict,
    max_chars: int = 30000,
    chunk_size: int = 500,
    overlap: int = 100,
) -> dict:
    """单篇论文：解析全文、分块、入库

    Returns:
        {"title": ..., "num_chunks": N} 或 {"title": ..., "error": ...}
    """
    pdf_path = paper.get("pdf_path")
    if not pdf_path:
        return {"title": paper.get("title", ""), "error": "no pdf_path"}

    try:
        full_text = extract_text_from_pdf(pdf_path, max_chars=max_chars)
        if len(full_text.strip()) < 500:
            return {"title": paper.get("title", ""), "error": "text too short, likely scanned PDF"}

        chunks = chunk_paper_fulltext(
            title=paper.get("title", "untitled"),
            full_text=full_text,
            chunk_size=chunk_size,
            overlap=overlap,
        )

        meta = {
            "authors": paper.get("authors", ""),
            "year": paper.get("year", ""),
            "source": paper.get("source", ""),
            "url": paper.get("url", ""),
            "bibtex": paper.get("bibtex", ""),
        }
        n = add_fulltext_chunks(chunks, paper_meta=meta)
        return {"title": paper.get("title", ""), "num_chunks": n}
    except Exception as e:
        logger.warning(f"Failed to ingest {paper.get('title', '')}: {e}")
        return {"title": paper.get("title", ""), "error": str(e)}


def run_pdf_ingestion(state: PipelineState) -> dict:
    """流水线节点：批量下载并摄入论文全文"""
    papers = state.get("retrieved_papers", [])
    if not papers:
        return {
            "current_phase": "pdf_ingestion",
            "ingested_papers": [],
            "ingestion_report": "## PDF 全文摄入\n\n没有可摄入的论文。",
        }

    # 1. 批量下载 PDF（最多 20 篇, 覆盖 30+ 参考文献的证据需求）
    limit = min(20, len(papers))
    with_pdf = download_pdfs_for_papers(papers, limit=limit)
    logger.info(f"Downloaded {len(with_pdf)}/{limit} PDFs")

    # 2. 逐篇解析 + 分块 + 入库
    ingested = []
    for paper in with_pdf:
        result = ingest_pdf_fulltext(paper)
        if "num_chunks" in result:
            ingested.append(result)
        else:
            logger.info(f"Skip {result['title']}: {result.get('error', '')}")

    total_chunks = sum(i.get("num_chunks", 0) for i in ingested)

    lines = [
        "# PDF 全文摄入报告",
        "",
        f"- 候选论文: {len(papers)} 篇",
        f"- 成功下载 PDF: {len(with_pdf)} 篇",
        f"- 成功解析入库: {len(ingested)} 篇",
        f"- 向量块总数: {total_chunks}",
        "",
        "## 已摄入论文",
        "",
    ]
    for i in ingested:
        lines.append(f"- {i['title']} ({i['num_chunks']} chunks)")

    return {
        "current_phase": "pdf_ingestion",
        "ingested_papers": ingested,
        "ingestion_report": "\n".join(lines),
    }
