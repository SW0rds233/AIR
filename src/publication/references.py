"""GB/T 7714 sequential bibliography from verified publication metadata.

Missing fields are not invented; passage locators belong in the audit ledger.
"""
import re
from typing import Any
from pydantic import BaseModel


class PublicationMetadata(BaseModel):
    publication_status: str = "unknown"
    publication_type: str = ""
    publication_verified_by: str = ""
    publication_note: str = ""
    preprint_url: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    publication_place: str = ""
    relevance_reason: str = ""


BIBLIO_FIELDS = tuple(PublicationMetadata.model_fields)


def normalize_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or "").strip(), flags=re.I)


def citation_eligible(source: dict[str, Any]) -> bool:
    return (source.get("publication_status") == "published"
            and bool(source.get("publication_verified_by"))
            and source.get("publication_type") in {"journal-article", "proceedings-article", "book", "monograph", "book-chapter", "thesis", "standard", "report"}
            and "arxiv" not in str(source.get("venue", "")).lower()
            and (bool(source.get("venue")) if source.get("publication_type") in {
                "journal-article", "proceedings-article"} else bool(source.get("publisher")))
            and not normalize_doi(source.get("doi", "")).lower().startswith("10.48550/arxiv")
            and bool(source.get("title") and source.get("authors") and source.get("year")))


def _authors(value) -> str:
    names = value if isinstance(value, list) else re.split(r"\s*[,;；]\s*", str(value or ""))
    out = []
    for name in names[:3]:
        name = str(name).strip()
        if re.search(r"[\u4e00-\u9fff]", name):
            out.append(name)
        else:
            parts = name.split()
            out.append((parts[-1].upper() + " " + " ".join(p[0].upper() for p in parts[:-1])).strip()
                       if len(parts) > 1 else name.upper())
    if len(names) > 3:
        out.append("等" if any(re.search(r"[\u4e00-\u9fff]", str(n)) for n in names) else "et al")
    return ", ".join(out)


def format_reference(source: dict[str, Any]) -> str:
    kind = source.get("publication_type") or source.get("doc_type") or "journal-article"
    title, year = str(source.get("title") or ""), str(source.get("year") or "")
    authors, venue = _authors(source.get("authors")), str(source.get("venue") or "")
    code = {"journal-article": "J", "journal": "J", "proceedings-article": "C", "conference": "C",
            "book": "M", "monograph": "M", "book-chapter": "M", "thesis": "D",
            "standard": "S", "report": "R"}.get(kind, "J")
    entry = f"{authors}. {title}[{code}]"
    if code in {"J", "C"}:
        entry += ("//" if code == "C" else ". ") + venue + f", {year}"
        volume, issue, pages = (str(source.get(key) or "") for key in ("volume", "issue", "pages"))
        if volume:
            entry += f", {volume}"
        if issue:
            entry += f"({issue})"
        if pages:
            entry += f": {pages}"
    else:
        entry += ". " + ": ".join(str(v) for v in (source.get("publication_place"), source.get("publisher")) if v)
        entry += f", {year}"
    doi = normalize_doi(source.get("doi", ""))
    entry = entry.rstrip(". ,") + "."
    if doi:
        entry += f" DOI:{doi}."
    return entry


def citation_suffix(text: str, mark: str) -> str:
    value = text.rstrip()
    if not mark:
        return value
    return value[:-1] + mark + value[-1] if value.endswith(("。", ".", "；", ";")) else value + mark


def cited_reference_rows(manuscript, sources) -> list[dict]:
    """Use actual first-citation numbering, not source-store insertion order."""
    from src.publication.schemas import assign_render_numbers
    numbers = assign_render_numbers(manuscript)["citations"]
    indexed = {}
    for source in sources:
        for key in (source.get("id"), source.get("source_id")):
            if key:
                indexed[str(key)] = source
    return [{**indexed.get(source_id, {}), "source_id": source_id, "index": index,
             "locator": indexed.get(source_id, {}).get("locator")
                        or indexed.get(source_id, {}).get("location", "")}
            for source_id, index in sorted(numbers.items(), key=lambda pair: pair[1])]
