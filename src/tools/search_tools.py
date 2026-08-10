from __future__ import annotations

import re as _re

from langchain_core.tools import tool
import httpx
import urllib.parse
import xml.etree.ElementTree as ET

from src.config import ARXIV_MAX_RESULTS, SEMANTIC_SCHOLAR_MAX_RESULTS

ARXIV_API_URL = "https://export.arxiv.org/api/query"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

MAX_QUERY_LEN = 100


def _clean_query(query: str) -> str:
    """清洗检索查询：截断超长文本、去除 markdown 标记/多余空格

    防止 LLM 把整段指令当查询传给学术 API (OpenAlex 对超长查询返回 400)。
    注意: 下划线必须**替换为空格**而非删除 —
    arXiv/S2/OpenAlex 按词索引, 删掉下划线会把 "RF_fingerprinting" 拼成
    "RFfingerprinting" 导致 0 命中 (实测 arXiv: 0 vs 5 hits)。
    """
    if not query:
        return ""
    # 去除 markdown 标记 (* # ` >), 保留词内容
    cleaned = _re.sub(r"[*#`>]", "", query)
    # 下划线 → 空格 (技术关键词 RF_fingerprinting → RF fingerprinting)
    cleaned = cleaned.replace("_", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned[:MAX_QUERY_LEN]


def _norm_title(title: str) -> str:
    """标题归一化: 小写 + 去所有非字母数字（用于去重与匹配）

    借鉴 gpt-researcher 的 URL 归一化去重思路 —
    "RF Fingerprinting" / "RF_fingerprinting" / "RF-fingerprinting"
    应为同一篇论文。
    """
    return _re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _dedup_key(paper: dict) -> str:
    """论文去重键: 优先 DOI（同一 DOI 即同一论文），否则归一化标题

    修复: 原来只按 title.strip().lower() 精确匹配，
    标题大小写/下划线/连字符变体无法合并，arXiv v1/v2 会重复。
    """
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    norm = _norm_title(paper.get("title", ""))
    if norm:
        return f"title:{norm}"
    return f"url:{paper.get('url', '')}"


def merge_papers(existing: list[dict], found: list[dict]) -> list[dict]:
    """合并论文列表（按 DOI/归一化标题去重，保留已有顺序）

    供文献检索/子查询检索共用，避免各处重复实现去重逻辑。
    """
    seen = {_dedup_key(p) for p in existing}
    for p in found:
        if "error" in p or not p.get("title"):
            continue
        key = _dedup_key(p)
        if key and key not in seen:
            seen.add(key)
            existing.append(p)
    return existing


@tool
def arxiv_search(query: str, max_results: int = 20) -> list[dict]:
    """Search arXiv for papers matching a query string.

    限流/失败时返回空列表（不抛异常，避免中断流水线）。
    """
    import time

    query = _clean_query(query)
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(max_results, ARXIV_MAX_RESULTS),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }

    response = None
    for attempt in range(3):
        try:
            response = httpx.get(ARXIV_API_URL, params=params, timeout=30)
            if response.status_code == 429:
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return []  # 三次失败, 返回空列表

    if response is None:
        return []

    try:
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return []  # 无法解析, 返回空列表
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
            "api_source": "arXiv",  # 数据源标识 (信任判断用)
            "abstract": abstract[:1000],
            "citations": 0,
            "url": url,
            "doi": "",
            "bibtex": _to_bibtex(title, authors, year, url),
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
    import time

    for attempt in range(3):
        try:
            response = httpx.get(
                SEMANTIC_SCHOLAR_SEARCH_URL, params=params, headers=headers, timeout=30
            )
            if response.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            break
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return []  # 三次失败返回空列表

    data = response.json()

    papers = []
    for item in data.get("data", []):
        authors_list = item.get("authors", [])
        authors = ", ".join(a.get("name", "") for a in authors_list[:5])
        year = str(item.get("year", ""))
        url = item.get("url", "")
        arxiv_id = item.get("externalIds", {}).get("ArXiv", "")
        # 官方 BibTeX (借鉴 AI-Scientist-v2: bibtex 只从 API 取, 不凭记忆生成)
        bibtex = ((item.get("citationStyles") or {}).get("bibtex", "") or "").strip()
        venue = (item.get("publicationVenue") or {}).get("name", "") or ""

        papers.append({
            "title": item.get("title", ""),
            "authors": authors,
            "year": year,
            "source": venue or "Semantic Scholar",
            "venue": venue,  # 真实出版出处 (期刊/会议名, 供 GB/T 7714 格式化)
            "api_source": "Semantic Scholar",  # 数据源标识 (信任判断用)
            "abstract": (item.get("abstract") or "")[:1000],
            "citations": item.get("citationCount", 0),
            "url": url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
            "doi": item.get("externalIds", {}).get("DOI", "") or "",
            "retracted": bool(item.get("isRetracted")),  # 撤稿检测 (借鉴 OpenAI4S)
            "bibtex": bibtex,
        })

    return papers


@tool
def openalex_search(query: str, max_results: int = 20) -> list[dict]:
    """Search OpenAlex for papers. 支持中英文查询，覆盖部分中文期刊文献。

    OpenAlex 免费无需 Key，对中文关键词有较好支持（收录约 10-20% 中文期刊）。
    """
    query = _clean_query(query)
    params = {
        "search": query,
        "per-page": min(max_results, 50),
        "select": "title,authorships,publication_year,doi,primary_location,cited_by_count",
    }
    try:
        resp = httpx.get(
            "https://api.openalex.org/works",
            params=params,
            headers={"User-Agent": "AIR-AIResearch/0.1"},
            timeout=30,
        )
        resp.raise_for_status()
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
        papers.append({
            "title": item.get("title", ""),
            "authors": authors,
            "year": str(item.get("publication_year", "")),
            "source": venue or "OpenAlex",
            "venue": venue,  # 真实出版出处 (期刊/会议名, 供 GB/T 7714 格式化)
            "api_source": "OpenAlex",  # 数据源标识 (信任判断用)
            "abstract": "",
            "citations": item.get("cited_by_count", 0),
            "url": item.get("doi", "") or f"https://openalex.org/works/{item.get('id', '').split('/')[-1]}",
            "doi": item.get("doi", "") or "",
            "bibtex": "",
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
