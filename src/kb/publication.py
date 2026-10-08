"""Resolve publication versions and rank retrieval candidates before ingestion."""
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from math import isfinite, log1p
from urllib.parse import quote
from src.publication.references import normalize_doi, citation_eligible

PUBLISHED_TYPES = {"journal-article", "proceedings-article", "book", "monograph", "book-chapter"}
_STOP = set("the a an of for and in on to with by from using use study research result solution certificate existence construction order length size distance matrix matrices binary code codes current original source theorem bound".split())


def tokens(text):
    return set(t.lower() for t in re.findall(r"[A-Za-z][A-Za-z-]{2,}|[\u4e00-\u9fff]{2,}", str(text or ""))
               if t.lower() not in _STOP)


def relevance_score(paper, query):
    if paper.get("_semantic_rejected") is True:
        return 0.0
    if paper.get("_relevance_verified") is True:
        return 1.0
    terms = tokens(query)
    if not terms:
        return 0.0
    title = tokens(paper.get("title"))
    body = tokens(paper.get("abstract"))
    return (2 * len(terms & title) + len(terms & body)) / (3 * len(terms))


def candidate_priority(paper, query, *, current_year=None):
    """Rank topical candidates by bounded recency and citation signals.

    Topic relevance remains the primary gate. Citation counts are discovery
    metadata, never evidence that a paper supports a research claim.
    """
    relevance = relevance_score(paper, query)
    year_now = current_year or datetime.now(timezone.utc).year
    try:
        year = int(paper.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    try:
        citations = max(float(paper.get("citations") or 0), 0.0)
    except (TypeError, ValueError):
        citations = 0.0
    if not isfinite(citations):
        citations = 0.0
    freshness = max(0.0, 1.0 - max(year_now - year, 0) / 15) if 1900 <= year <= year_now + 1 else 0.0
    citation_signal = min(log1p(citations) / log1p(1000), 1.0)
    quality = 0.55 * freshness + 0.45 * citation_signal
    return (int(relevance > 0), min(int(relevance * 4), 4), quality, relevance)


def _fetch(url, params=None):
    from src.utils.http_client import get_with_retry
    return get_with_retry(url, params=params, read_timeout=15, max_retries=1).json()


def _failure(label, exc):
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"{label}失败: {type(exc).__name__}" + (f" HTTP {status}" if status else "")


def _matches(paper, candidate):
    old = re.sub(r"\W", "", str(paper.get("title") or "").casefold())
    new = re.sub(r"\W", "", str(candidate.get("title") or "").casefold())
    similarity = SequenceMatcher(None, old, new).ratio()
    def surnames(value):
        return {name.strip().split()[-1].casefold() for name in re.split(r"[,;；]", str(value or ""))
                if name.strip()}
    author_tokens = surnames(paper.get("authors"))
    return bool(old and new and similarity >= .88
                and author_tokens and author_tokens & surnames(candidate.get("authors")))


def _crossref(row):
    dates = (row.get("published") or row.get("issued") or {}).get("date-parts") or [[]]
    authors = ", ".join(" ".join(str(a.get(k) or "") for k in ("given", "family")).strip()
                        or str(a.get("name") or "") for a in row.get("author") or [])
    return {"title": (row.get("title") or [""])[0], "authors": authors,
            "venue": (row.get("container-title") or [""])[0], "year": str(dates[0][0]) if dates[0] else "",
            "doi": normalize_doi(row.get("DOI")), "url": row.get("URL") or "",
            "publication_type": row.get("type", ""), "publisher": row.get("publisher", ""),
            "volume": row.get("volume", ""), "issue": row.get("issue", ""),
            "pages": row.get("page") or row.get("article-number") or "",
            "publication_verified_by": "crossref", "publication_status": "published"}


def _openalex(row):
    loc = next((v for v in row.get("locations") or [row.get("primary_location") or {}]
                if v.get("is_published") and (v.get("source") or {}).get("type") != "repository"), {})
    source = loc.get("source") or {}
    biblio = row.get("biblio") or {}
    pages = "-".join(str(biblio[k]) for k in ("first_page", "last_page") if biblio.get(k))
    return {"title": row.get("title", ""), "authors": ", ".join(
                (a.get("author") or {}).get("display_name", "") for a in row.get("authorships") or []),
            "venue": source.get("display_name", ""), "year": str(row.get("publication_year") or ""),
            "doi": normalize_doi(row.get("doi")), "url": loc.get("landing_page_url") or "",
            "volume": biblio.get("volume") or "", "issue": biblio.get("issue") or "", "pages": pages,
            "publication_type": {"article": "journal-article"}.get(row.get("type"), row.get("type", "")),
            "publication_verified_by": "openalex",
            "publication_status": "published" if loc and not row.get("is_retracted") else "unknown",
            "pdf_url": loc.get("pdf_url") or ""}


def verify_publication(paper, fetch=None):
    """Title+author identity is required; a DOI or arXiv journal-ref alone is not proof."""
    if citation_eligible(paper):
        return dict(paper)
    fetch = fetch or _fetch
    out, candidates, errors = dict(paper), [], []
    doi = normalize_doi(paper.get("publication_doi") or paper.get("doi"))
    if doi and not doi.lower().startswith("10.48550/arxiv"):
        try:
            row = fetch(f"https://api.crossref.org/works/{quote(doi, safe='')}").get("message") or {}
            if row.get("type") in PUBLISHED_TYPES and not row.get("update-to"):
                candidates.append(_crossref(row))
            for relation in (row.get("relation") or {}).get("is-preprint-of") or []:
                linked = normalize_doi(relation.get("id"))
                if linked:
                    version = fetch(f"https://api.crossref.org/works/{quote(linked, safe='')}").get("message") or {}
                    if version.get("type") in PUBLISHED_TYPES:
                        candidates.append(_crossref(version))
        except Exception as exc:
            errors.append(_failure("Crossref DOI核查", exc))
    if not any(_matches(paper, row) for row in candidates):
        try:
            data = fetch("https://api.crossref.org/works", {"query.title": paper.get("title", ""), "rows": 5})
            candidates.extend(_crossref(row) for row in (data.get("message") or {}).get("items") or []
                              if row.get("type") in PUBLISHED_TYPES and not row.get("update-to"))
        except Exception as exc:
            errors.append(_failure("Crossref题名核查", exc))
    match = next((row for row in candidates if _matches(paper, row)), None)
    if match is None:
        try:
            from src.tools.search_tools import OPENALEX_API_KEY
            params = {"search": paper.get("title", ""), "per-page": 5}
            if OPENALEX_API_KEY:
                params["api_key"] = OPENALEX_API_KEY
            data = fetch("https://api.openalex.org/works", params)
            match = next((candidate for row in data.get("results") or []
                          if (candidate := _openalex(row))["publication_status"] == "published"
                          and candidate["publication_type"] in PUBLISHED_TYPES
                          and _matches(paper, candidate)), None)
        except Exception as exc:
            errors.append(_failure("OpenAlex出版核查", exc))
    if match:
        preprint_url = paper.get("url", "") if "arxiv" in str(paper.get("url", "")) else paper.get("preprint_url", "")
        # Keep the actual excerpt and its version; publication lookup is not a full-text read.
        out.update(match)
        out["preprint_url"] = preprint_url
        out["publication_note"] = "题名和作者已匹配正式出版记录；摘录仍需核查版本差异" if preprint_url else "正式出版元数据已核查"
    else:
        out["publication_status"] = "preprint" if "arxiv" in str(paper.get("url", "")) else "unknown"
        out["publication_verified_by"] = ""
        out["publication_note"] = "; ".join(errors) or "未找到可确认的正式出版版本（不等于未出版）"
    return out
