"""Resolve publication versions and rank retrieval candidates before ingestion."""
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from math import isfinite, log1p
from urllib.parse import quote
from src.publication.references import normalize_doi, citation_eligible, author_metadata_problems

PUBLISHED_TYPES = {"journal-article", "proceedings-article", "book", "monograph", "book-chapter", "report", "standard", "dissertation"}
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


def _provenance(paper):
    try:
        value = json.loads(paper.get('metadata_provenance') or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


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
    def author_name(author):
        family, given = str(author.get('family') or ''), str(author.get('given') or '')
        return ((family + given) if re.search(r'[\u4e00-\u9fff]', family + given)
                else ' '.join(v for v in (given, family) if v)) or str(author.get('name') or '')
    authors = ", ".join(author_name(a) for a in row.get("author") or [])
    return {"title": (row.get("title") or [""])[0], "authors": authors,
            "author_metadata": json.dumps(row.get('author') or [], ensure_ascii=False),
            "venue": (row.get("container-title") or [""])[0], "year": str(dates[0][0]) if dates[0] else "",
            "doi": normalize_doi(row.get("DOI")), "url": row.get("URL") or "",
            "publication_type": {'dissertation':'thesis'}.get(row.get('type'), row.get('type', '')), "publisher": row.get("publisher", ""),
            "volume": row.get("volume", ""), "issue": row.get("issue", ""),
            "pages": row.get("page") or row.get("article-number") or "",
            "issn": ", ".join(row.get("ISSN") or []),
            "published_date": "-".join(str(part) for part in dates[0]),
            "publisher_url": ((row.get("resource") or {}).get("primary") or {}).get("URL") or "",
            "publication_source_url": "https://api.crossref.org/works/" + quote(normalize_doi(row.get("DOI")), safe=''),
            "publication_verified_by": "crossref", "publication_status": "published"}


def _csl(row):
    """Content negotiation is a second structured route, never a scraped citation."""
    normalized = dict(row)
    for key in ('title', 'container-title', 'ISSN'):
        if isinstance(normalized.get(key), str):
            normalized[key] = [normalized[key]]
    normalized['type'] = {'article-journal':'journal-article', 'paper-conference':'proceedings-article',
                          'chapter':'book-chapter'}.get(row.get('type'), row.get('type'))
    return _crossref(normalized) if normalized.get('type') in PUBLISHED_TYPES else {}


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


def verify_publication(paper, fetch=None, *, force=False, author_fetch=None, metadata_only=False,
                       publisher_fetch=None, metadata_urls=()):
    """Title+author identity is required; a DOI or arXiv journal-ref alone is not proof."""
    author_issues = author_metadata_problems(paper)
    if citation_eligible(paper) and not force:
        return dict(paper)
    fetch = fetch or _fetch
    out, candidates, errors = dict(paper), [], []
    doi = normalize_doi(paper.get("publication_doi") or paper.get("doi"))
    allowed_dois = {doi.lower()} if doi else set()
    authoritative_response = False
    if doi and not doi.lower().startswith("10.48550/arxiv"):
        try:
            row = fetch(f"https://api.crossref.org/works/{quote(doi, safe='')}").get("message") or {}
            authoritative_response = bool(row.get('DOI'))
            if row.get("type") in PUBLISHED_TYPES and not row.get("update-to"):
                candidates.append(_crossref(row))
            for relation in (row.get("relation") or {}).get("is-preprint-of") or []:
                linked = normalize_doi(relation.get("id"))
                if linked:
                    allowed_dois.add(linked.lower())
                    version = fetch(f"https://api.crossref.org/works/{quote(linked, safe='')}").get("message") or {}
                    if version.get("type") in PUBLISHED_TYPES:
                        candidates.append(_crossref(version))
        except Exception as exc:
            errors.append(_failure("Crossref DOI核查", exc))
        if not candidates:
            try:
                url = f"https://api.crossref.org/works/{quote(doi, safe='')}/transform/application/vnd.citationstyles.csl+json"
                candidate = _csl(fetch(url))
                if candidate and normalize_doi(candidate.get('doi')).lower() == doi.lower():
                    authoritative_response = True
                    candidate.update(publication_verified_by='crossref-csl', publication_source_url=url)
                    candidates.append(candidate)
            except Exception as exc:
                errors.append(_failure('CSL 元数据核查', exc))
    def matches(row):
        # Exact DOI + exact title also permits correcting a corrupted author field.
        candidate_doi = normalize_doi(row.get('doi')).lower()
        if (doi and not doi.lower().startswith('10.48550/arxiv')
                and candidate_doi and candidate_doi not in allowed_dois):
            return False
        return _matches(paper, row) or bool(doi and normalize_doi(row.get('doi')).lower() == doi.lower()
            and re.sub(r'\W', '', str(paper.get('title') or '').casefold())
            == re.sub(r'\W', '', str(row.get('title') or '').casefold()))
    if not any(matches(row) for row in candidates):
        try:
            data = fetch("https://api.crossref.org/works", {"query.title": paper.get("title", ""), "rows": 5})
            candidates.extend(_crossref(row) for row in (data.get("message") or {}).get("items") or []
                              if row.get("type") in PUBLISHED_TYPES and not row.get("update-to"))
        except Exception as exc:
            errors.append(_failure("Crossref题名核查", exc))
    match = next((row for row in candidates if matches(row)), None)
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
                          and matches(candidate)), None)
        except Exception as exc:
            errors.append(_failure("OpenAlex出版核查", exc))
    if match:
        preprint_url = paper.get("url", "") if "arxiv" in str(paper.get("url", "")) else paper.get("preprint_url", "")
        # Keep the actual excerpt and its version; publication lookup is not a full-text read.
        out.update(match)
        provenance = _provenance(paper)
        same_version = normalize_doi(match.get('doi')).lower() == doi.lower()
        if same_version and citation_eligible(paper):
            for key in ('volume', 'issue', 'pages', 'issn', 'publisher', 'published_date'):
                if not match.get(key) and paper.get(key):
                    out[key] = paper[key]
        if metadata_only and paper.get('author_verified_by') == 'publisher' and not author_issues:
            for key in ('authors', 'author_metadata', 'author_verified_by', 'author_source_url', 'author_note'):
                if paper.get(key):
                    out[key] = paper[key]
        out["preprint_url"] = preprint_url
        out["publication_note"] = "题名和作者已匹配正式出版记录；摘录仍需核查版本差异" if preprint_url else "正式出版元数据已核查"
        provenance.update({key: match.get('publication_source_url') or match.get('url')
            for key, value in match.items() if value and key not in {'publication_note', 'publication_status'}})
        if out.get('author_verified_by') == 'publisher' and out.get('author_source_url'):
            provenance.update({key:out['author_source_url'] for key in ('authors', 'author_metadata')})
        out['metadata_provenance'] = json.dumps(provenance, ensure_ascii=False)
    elif citation_eligible(paper) and errors and not authoritative_response:
        out['publication_note'] = '本次刷新未完成，保留既有核准记录（不视为再次核验成功）；' + '; '.join(errors)
    else:
        out["publication_status"] = "preprint" if "arxiv" in str(paper.get("url", "")) else "unknown"
        out["publication_verified_by"] = ""
        out["publication_note"] = "; ".join(errors) or "未找到可确认的正式出版版本（不等于未出版）"
    if doi and force and metadata_only and match and any(not out.get(key) for key in ('volume', 'pages')):
        # Published online records can omit later volume/pages. Supplement only
        # missing fields from the exact work, without rewriting its dates/authors.
        try:
            from src.tools.search_tools import OPENALEX_API_KEY
            url = 'https://api.openalex.org/works/https://doi.org/' + quote(doi, safe='/')
            extra = _openalex(fetch(url, {'api_key':OPENALEX_API_KEY} if OPENALEX_API_KEY else {}))
            if (normalize_doi(extra.get('doi')).lower() == doi.lower() and matches(extra)
                    and extra.get('publication_status') == 'published'):
                provenance = _provenance(out)
                for key in ('volume', 'issue', 'pages'):
                    if not out.get(key) and extra.get(key):
                        out[key] = extra[key]
                        provenance[key] = url
                out['metadata_provenance'] = json.dumps(provenance, ensure_ascii=False)
        except Exception as exc:
            errors.append(_failure('OpenAlex DOI补充核查', exc))
    check_authors = bool(author_issues or author_metadata_problems(out) or (force and not metadata_only))
    if doi and (check_authors or (force and (not match or any(not out.get(k) for k in ('volume', 'issue', 'pages'))))):
        from src.kb.publisher_metadata import fetch_publisher_metadata
        try:
            correction = (author_fetch(doi) if author_fetch else
                          (publisher_fetch or fetch_publisher_metadata)(doi,
                              urls=[*metadata_urls, out.get('publisher_url'), paper.get('publisher_url'), paper.get('url')]))
        except Exception as exc:
            correction = {}
            errors.append(_failure('出版原页作者核查', exc))
        if correction:
            candidate = {**correction, 'year':str(correction.get('published_date') or '')[:4],
                         'publication_type':correction.get('publication_type') or (
                             'journal-article' if correction.get('venue') else ''),
                         'publication_status':'published', 'publication_verified_by':'publisher'}
            if not match and matches(candidate) and citation_eligible(candidate):
                for key, value in candidate.items():
                    if value:
                        out[key] = value
                out['publication_note'] = 'DOI 与题名/署名匹配出版原页的结构化元数据；内容支持另行审查'
            # Only the DOI-matched article's structured metadata may fill missing
            # bibliography fields. Never change excerpts or claim support relations.
            for key in ('authors', 'author_metadata', 'author_verified_by', 'author_source_url', 'author_note'):
                if correction.get(key):
                    out[key] = correction[key]
            provenance = _provenance(out)
            if correction.get('authors'):
                provenance.update({key:correction.get('author_source_url', '') for key in ('authors', 'author_metadata')})
            for key in ('volume', 'issue', 'pages', 'issn', 'publisher', 'published_date'):
                if not out.get(key) and correction.get(key):
                    out[key] = correction[key]
                    provenance[key] = correction.get('publication_source_url') or correction.get('author_source_url', '')
            out['metadata_provenance'] = json.dumps(provenance, ensure_ascii=False)
        elif errors and metadata_only:
            out['publication_note'] = str(out.get('publication_note') or '') + '；补充字段仍待核查：' + '; '.join(errors)
        if check_authors and not correction.get('author_verified_by'):
            out.update(author_verified_by='unresolved', author_note='作者原始署名仍待核查；'
                       + '; '.join(errors or author_issues or author_metadata_problems(out)
                                   or ['未取得 DOI 匹配的出版原页作者列表']))
    return out
