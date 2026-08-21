from __future__ import annotations

import re as _re

from langchain_core.tools import tool
import os
import urllib.parse
import xml.etree.ElementTree as ET

from src.config import ARXIV_MAX_RESULTS, SEMANTIC_SCHOLAR_MAX_RESULTS
from src.utils.http_client import get_with_retry, CircuitBreakerOpenError

ARXIV_API_URL = "https://export.arxiv.org/api/query"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")

MAX_QUERY_LEN = 100


def _clean_query(query: str) -> str:
    """清洗检索查询：截断超长文本、去 markdown 标记。

    下划线替换为空格（arXiv/S2/OpenAlex 按词索引）。
    """
    if not query:
        return ""
    # 去除 markdown 标记 (* # ` >), 保留词内容
    cleaned = _re.sub(r"[*#`>]", "", query)
    # 下划线 → 空格 (技术关键词 RF_fingerprinting → RF fingerprinting)
    cleaned = cleaned.replace("_", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned[:MAX_QUERY_LEN]


def _clean_title(title: str) -> str:
    """去除 arXiv 预印本版标题的 "Pre-print:"/"Preprint:" 前缀

    该前缀导致预印本版与正式版被当成两篇不同论文 (审稿人判"重复引用")。
    """
    t = (title or "").strip()
    return _re.sub(r"^[Pp]re-?print\s*[:：]\s*", "", t)


def _norm_title(title: str) -> str:
    """标题归一化: 小写 + 去所有非字母数字，用于去重与匹配

    额外去除 "Pre-print:"/"Preprint:" 等前缀 (arXiv 预印本版与正式版
    常仅相差该前缀, 是同一工作, 必须合并去重)。
    """
    t = (title or "").lower()
    t = _re.sub(r"^\s*pre-?print\s*[:：]\s*", "", t)
    return _re.sub(r"[^a-z0-9]", "", t)


def _dedup_key(paper: dict) -> str:
    """论文去重键: 优先 DOI，否则归一化标题"""
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    norm = _norm_title(paper.get("title", ""))
    if norm:
        return f"title:{norm}"
    return f"url:{paper.get('url', '')}"


def merge_papers(existing: list[dict], found: list[dict]) -> list[dict]:
    """合并论文列表，按 DOI **或** 归一化标题去重。

    关键: 同一篇论文的 arXiv 版(无 DOI, 以标题为键)与正式版(有 DOI, 以 DOI 为键)
    会被旧逻辑判为两篇。这里同时维护 DOI 集合与标题集合, 任一命中即去重。
    """
    seen_dois: set[str] = set()
    seen_titles: set[str] = set()
    for p in existing:
        doi = (p.get("doi") or "").strip().lower()
        if doi:
            seen_dois.add(doi)
        norm = _norm_title(p.get("title", ""))
        if norm:
            seen_titles.add(norm)

    for p in found:
        if "error" in p or not p.get("title"):
            continue
        p["title"] = _clean_title(p.get("title", ""))
        doi = (p.get("doi") or "").strip().lower()
        norm = _norm_title(p.get("title", ""))
        if doi and doi in seen_dois:
            continue
        if norm and norm in seen_titles:
            continue
        if doi:
            seen_dois.add(doi)
        if norm:
            seen_titles.add(norm)
        existing.append(p)
    return existing


def dedup_papers(papers: list[dict]) -> list[dict]:
    """对已有论文列表去重 (按 DOI 或归一化标题), 保持原相对顺序。

    用于最终可信清单组装前的兜底去重: 检索/补录多路径合并后,
    同一篇文献可能以不同编号重复出现 (如 [38]/[51])。
    """
    seen_dois: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []
    for p in papers:
        doi = (p.get("doi") or "").strip().lower()
        norm = _norm_title(p.get("title", ""))
        if doi and doi in seen_dois:
            continue
        if norm and norm in seen_titles:
            continue
        if doi:
            seen_dois.add(doi)
        if norm:
            seen_titles.add(norm)
        out.append(p)
    return out


@tool
def arxiv_search(query: str, max_results: int = 20) -> list[dict]:
    """Search arXiv for papers matching a query string.

    限流/失败时返回空列表（不抛异常，避免中断流水线）。
    """
    query = _clean_query(query)
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(max_results, ARXIV_MAX_RESULTS),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }

    try:
        response = get_with_retry(ARXIV_API_URL, params=params, read_timeout=30)
    except Exception:
        return []

    try:
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return []
    papers = []
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        title = " ".join(title_el.text.split()) if title_el is not None and title_el.text else ""

        authors = []
        for author in entry.findall("atom:author", ns):
            name_el = author.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text)

        summary_el = entry.find("atom:summary", ns)
        abstract = " ".join(summary_el.text.split()) if summary_el is not None and summary_el.text else ""

        link_el = entry.find("atom:id", ns)
        url = link_el.text.strip() if link_el is not None and link_el.text else ""

        published_el = entry.find("atom:published", ns)
        year = published_el.text[:4] if published_el is not None and published_el.text else ""

        papers.append({
            "title": title,
            "authors": ", ".join(authors[:5]),
            "year": year,
            "source": "arXiv",
            "api_source": "arXiv",
            "abstract": abstract[:1000],
            "citations": 0,
            "url": url,
            "doi": "",
            "bibtex": _to_bibtex(title, authors, year, url),
            "published": False,  # arXiv 记录不含发表信息, precheck 阶段解析
        })

    return papers


@tool
def semantic_scholar_search(query: str, max_results: int = 20) -> list[dict]:
    """Search Semantic Scholar for papers matching a query."""
    query = _clean_query(query)
    params = {
        "query": query,
        "limit": min(max_results, SEMANTIC_SCHOLAR_MAX_RESULTS),
        "fields": "title,authors,year,abstract,citationCount,externalIds,url,publicationVenue,isRetracted,retractionReason,citationStyles",
    }
    headers = {"Accept": "application/json"}

    try:
        response = get_with_retry(
            SEMANTIC_SCHOLAR_SEARCH_URL, params=params, headers=headers, read_timeout=30
        )
    except Exception:
        return []

    data = response.json()

    papers = []
    for item in data.get("data", []):
        authors_list = item.get("authors", [])
        authors = ", ".join(a.get("name", "") for a in authors_list[:5])
        year = str(item.get("year", ""))
        url = item.get("url", "")
        arxiv_id = item.get("externalIds", {}).get("ArXiv", "")
        bibtex = ((item.get("citationStyles") or {}).get("bibtex", "") or "").strip()
        venue = (item.get("publicationVenue") or {}).get("name", "") or ""

        papers.append({
            "title": item.get("title", ""),
            "authors": authors,
            "year": year,
            "source": venue or "Semantic Scholar",
            "venue": venue,
            "api_source": "Semantic Scholar",
            "abstract": (item.get("abstract") or "")[:1000],
            "citations": item.get("citationCount", 0),
            "url": url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
            "arxiv_id": arxiv_id,  # 独立保存, 供 PDF 下载节点构造 arXiv 链接
            "doi": item.get("externalIds", {}).get("DOI", "") or "",
            "retracted": bool(item.get("isRetracted")),
            "bibtex": bibtex,
            "published": bool(venue),
        })

    return papers


@tool
def openalex_search(query: str, max_results: int = 20) -> list[dict]:
    """Search OpenAlex for papers. 支持中英文查询，覆盖部分中文期刊文献。

    OpenAlex 2025 起按用量计费, 免费层日预算耗尽返回 429。
    配置 OPENALEX_API_KEY 可获礼貌池更高额度。
    """
    query = _clean_query(query)
    params = {
        "search": query,
        "per-page": min(max_results, 50),
        "select": "title,authorships,publication_year,doi,primary_location,cited_by_count",
    }
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    try:
        resp = get_with_retry(
            "https://api.openalex.org/works",
            params=params,
            headers={"User-Agent": "AIR-AIResearch/0.1 (mailto:contact@example.com)"},
            read_timeout=30,
        )
        data = resp.json()
    except Exception as e:
        return [{"error": f"OpenAlex search failed: {e}"}]

    papers = []
    for item in data.get("results", []):
        authors = ", ".join(
            a.get("author", {}).get("display_name", "")
            for a in item.get("authorships", [])[:5]
            if a.get("author", {}).get("display_name")
        )
        loc = item.get("primary_location") or {}
        source = loc.get("source") or {}
        venue = source.get("display_name", "") or ""
        # 预印本时 primary_location 可能是 arXiv, 从 landing_page_url 提取 arXiv ID
        from src.tools.pdf_fetcher import extract_arxiv_id

        arxiv_id = extract_arxiv_id(loc.get("landing_page_url", "") or "")
        papers.append({
            "title": item.get("title", ""),
            "authors": authors,
            "year": str(item.get("publication_year", "")),
            "source": venue or "OpenAlex",
            "venue": venue,
            "api_source": "OpenAlex",
            "abstract": "",
            "citations": item.get("cited_by_count", 0),
            "url": item.get("doi", "") or f"https://openalex.org/works/{item.get('id', '').split('/')[-1]}",
            "arxiv_id": arxiv_id,  # 独立保存, 供 PDF 下载节点构造 arXiv 链接
            "doi": item.get("doi", "") or "",
            "bibtex": "",
            "published": bool(venue),
        })

    return papers


@tool
def search_all_sources(query: str, max_results: int = 20) -> list[dict]:
    """Search arXiv, Semantic Scholar and OpenAlex, deduplicate by title.

    OpenAlex 支持中文关键词，可补充中文期刊文献。
    注意: 本函数为原始实现, 由 @tool 包装的 search_all_sources_tool 对外暴露。
    代码内部调用请使用本函数 (或 .func 属性)。
    """
    # @tool 装饰后这些函数变成 StructuredTool, 需通过 .func 访问原始实现
    arxiv_fn = arxiv_search.func if hasattr(arxiv_search, "func") else arxiv_search
    ss_fn = semantic_scholar_search.func if hasattr(semantic_scholar_search, "func") else semantic_scholar_search
    oa_fn = openalex_search.func if hasattr(openalex_search, "func") else openalex_search

    arxiv_results = arxiv_fn(query, max_results)
    ss_results = ss_fn(query, max_results)
    oa_results = oa_fn(query, max_results)
    merged: list[dict] = []
    # 按 DOI/归一化标题去重 (借鉴 gpt-researcher 的 URL 归一化去重)
    merge_papers(merged, arxiv_results + ss_results + oa_results)
    return merged


def _to_bibtex(title: str, authors: list[str], year: str, url: str) -> str:
    key = f"arxiv{year}_{authors[0].split()[-1] if authors else 'unknown'}"
    author_str = " and ".join(authors)
    arxiv_id = url.split("/")[-1] if "/" in url else url
    return (
        f"@misc{{{key},\n"
        f"  title = {{{title}}},\n"
        f"  author = {{{author_str}}},\n"
        f"  year = {{{year}}},\n"
        f"  eprint = {{{arxiv_id}}},\n"
        f"  archivePrefix = {{arXiv}},\n"
        f"}}"
    )
