from __future__ import annotations

"""PDF 全文摄入节点：下载 PDF → 解析全文 → 分块 → 向量入库"""

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
    max_chars: int = None,
    chunk_size: int = 500,
    overlap: int = 100,
) -> dict:
    """单篇论文：解析全文、分块、入库

    Returns:
        {"title": ..., "num_chunks": N} 或 {"title": ..., "error": ...}
    """
    if max_chars is None:
        from src.config import PDF_FULLTEXT_MAX_CHARS

        max_chars = PDF_FULLTEXT_MAX_CHARS

    pdf_path = paper.get("pdf_path")
    if not pdf_path:
        return {"title": paper.get("title", ""), "error": "no pdf_path"}

    # SKIP_EMBEDDING=1 时跳过解析+向量化 (可选增强), 直接返回 0 块
    from src.rag.vector_store import embedding_available

    if not embedding_available():
        return {"title": paper.get("title", ""), "num_chunks": 0}

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
    """流水线节点：批量下载并摄入论文全文

    优先级排序: 标题含主题关键词 → arXiv 论文 → 其他。
    确保与主题最相关的论文优先入库，避免被泛领域热门论文挤占配额。
    """
    import re as _re

    filtered = state.get("retrieved_papers", [])
    unfiltered = state.get("unfiltered_papers", [])
    topic = state.get("research_topic", "")

    # 候选池: 优先 LLM 过滤后的论文 (retrieved_papers 带相关性打分),
    # 未过滤池补规则过滤后未覆盖的论文 (避免损失可下载的 arXiv 全文)
    # 补充论文要求 ≥2 个关键词命中 (强主题信号), 防止弱相关论文挤占下载配额
    try:
        from src.rag.relevance_filter import (
            rule_filter,
            extract_keywords,
            _normalize_keyword,
        )

        user_kw = ", ".join(state.get("topic_keywords", []))
        norm_kws = [_normalize_keyword(k) for k in extract_keywords(topic, user_kw)]
        candidates = list(filtered)
        if unfiltered:
            filtered_titles = {(p.get("title", "") or "").lower() for p in filtered}
            extras = rule_filter(unfiltered, topic, user_kw)
            added = 0
            for p in extras:
                if (p.get("title", "") or "").lower() in filtered_titles:
                    continue
                text = _normalize_keyword(
                    f"{p.get('title', '') or ''} {p.get('abstract', '') or ''}"
                )
                hits = sum(1 for k in norm_kws if k and k in text)
                if hits < 2:
                    continue
                candidates.append(p)
                filtered_titles.add((p.get("title", "") or "").lower())
                added += 1
            if added:
                print(f"  [pdf_ingestion] 候选池: LLM过滤 {len(filtered)} + 规则补充(≥2命中) {added} = {len(candidates)} 篇")
    except Exception as e:
        logger.warning(f"candidate filter skipped: {e}")
        candidates = unfiltered if unfiltered else filtered

    if not candidates:
        return {
            "current_phase": "pdf_ingestion",
            "ingested_papers": [],
            "ingestion_report": "## PDF 全文摄入\n\n没有可摄入的论文。",
        }

    # 关键词匹配用完整短语 (逗号分隔, 不拆散复合词), 与规则过滤保持一致
    try:
        from src.rag.relevance_filter import extract_keywords, _normalize_keyword

        user_kw = ", ".join(state.get("topic_keywords", []))
        score_words = [_normalize_keyword(k) for k in extract_keywords(topic, user_kw)]
    except Exception:
        score_words = []

    def _topic_score(p: dict) -> int:
        text = _normalize_keyword(
            f"{p.get('title', '') or ''} {p.get('abstract', '') or ''}"
        )
        return sum(1 for w in score_words if w and w in text)

    # 先按主题相关度粗排, 只对 Top 40 做出处解析 (批量请求, 避免逐篇调 API)
    prelim = sorted(candidates, key=lambda p: (-_topic_score(p), -int(p.get("citations", 0) or 0)))
    top_for_resolve = prelim[:40]
    print(f"  [pdf_ingestion] 出处解析 Top {len(top_for_resolve)} 篇 (共 {len(candidates)} 候选)...")

    try:
        from src.tools.venue_resolver import resolve_venues_fast

        resolved = resolve_venues_fast(top_for_resolve, max_papers=40)
        if resolved:
            print(f"  [pdf_ingestion] 出处解析: {resolved} 篇获得期刊信息")
    except Exception as e:
        logger.warning(f"venue resolution skipped: {e}")

    def _priority_key(p: dict) -> tuple:
        url = (p.get("url", "") or "").lower()
        has_arxiv = 1 if "arxiv" in url else 0
        has_doi = 1 if (p.get("doi") or "").strip() else 0
        published = 1 if (p.get("published") or (p.get("venue") or "").strip()) else 0
        citations = int(p.get("citations", 0) or 0)
        # LLM 相关性打分优先 (未过滤池补充的论文无分数, 给默认 0)
        rel_score = float(p.get("relevance_score", 0) or 0)
        # 已发表论文默认高于预印本; 仅在"无 arXiv 且无 DOI"(确定无法下载)
        # 时才降到预印本之后。层内: LLM 相关性分 → 关键词命中 → 被引量
        if published and has_arxiv:
            tier = 0
        elif published and has_doi:
            tier = 1
        elif has_arxiv:
            tier = 2
        elif published:
            tier = 3
        else:
            tier = 4
        return (tier, -rel_score, -_topic_score(p), -published, -citations)

    ordered = sorted(candidates, key=_priority_key)

    from src.config import PDF_DOWNLOAD_LIMIT

    limit = min(PDF_DOWNLOAD_LIMIT, len(ordered))
    print(f"  [pdf_ingestion] 开始下载 {limit} 篇 PDF (每篇间隔 3s 限流)...")
    with_pdf = download_pdfs_for_papers(ordered, limit=limit)
    published_in = len([p for p in with_pdf if (p.get("published") or (p.get("venue") or "").strip())])
    print(f"  [pdf_ingestion] 下载完成: {len(with_pdf)}/{limit} 篇 (已发表 {published_in})")
    logger.info(f"Downloaded {len(with_pdf)}/{limit} PDFs (已发表: {published_in})")

    # 2. 逐篇解析 + 分块 + 入库 (向量化最耗时, 打印进度以便区分"卡死"与"慢")
    ingested = []
    for i, paper in enumerate(with_pdf, 1):
        title = paper.get("title", "")[:50]
        print(f"  [pdf_ingestion] 解析入库 {i}/{len(with_pdf)}: {title}")
        result = ingest_pdf_fulltext(paper)
        if "num_chunks" in result:
            ingested.append(result)
            print(f"    ↳ {result['num_chunks']} chunks 已入库")
        else:
            print(f"    ↳ 跳过: {result.get('error', '')}")
            logger.info(f"Skip {result['title']}: {result.get('error', '')}")

    # 3. 人工导入 PDF (data/manual_pdfs/): 全文入库 + 文献元数据进入引用清单
    manual_papers, manual_ingested = [], []
    try:
        from src.rag.manual_pdfs import ingest_all_manual_pdfs

        manual_papers, manual_ingested = ingest_all_manual_pdfs()
    except Exception as e:
        logger.warning(f"manual pdf ingestion skipped: {e}")

    total_chunks = sum(i.get("num_chunks", 0) for i in ingested + manual_ingested)

    lines = [
        "# PDF 全文摄入报告",
        "",
        f"- 候选论文: {len(candidates)} 篇",
        f"- 成功下载 PDF: {len(with_pdf)} 篇 (已发表 {published_in} 篇)",
        f"- 成功解析入库: {len(ingested)} 篇",
        f"- 人工导入 PDF: {len(manual_papers)} 篇 (入库 {len(manual_ingested)})",
        f"- 向量块总数: {total_chunks}",
        "",
        "## 已摄入论文",
        "",
    ]
    for i in ingested:
        lines.append(f"- {i['title']} ({i['num_chunks']} chunks)")
    if manual_ingested:
        lines.append("")
        lines.append("## 人工导入论文")
        lines.append("")
        for i in manual_ingested:
            lines.append(f"- {i['title']} ({i['num_chunks']} chunks)")

    return {
        "current_phase": "pdf_ingestion",
        "ingested_papers": ingested + manual_ingested,
        "manual_papers": manual_papers,
        "ingestion_report": "\n".join(lines),
    }
