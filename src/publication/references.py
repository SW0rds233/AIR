"""GB/T 7714 sequential bibliography from verified publication metadata.

Missing fields are not invented; passage locators belong in the audit ledger.
"""
import json
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
    author_metadata: str = ""  # JSON given/family/name; survives string-valued KB storage.
    editors: str = ""
    edition: str = ""
    medium: str = ""
    accessed_date: str = ""
    updated_date: str = ""
    author_verified_by: str = ""
    author_source_url: str = ""
    author_note: str = ""


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
            and bool(source.get("title") and source.get("authors") and source.get("year"))
            and not author_metadata_problems(source))


def research_citation_eligible(source: dict[str, Any]) -> bool:
    """A verified publication still needs a usable relation to this research."""
    relation = source.get("relation") or source.get("support") or ""
    relation = getattr(relation, "value", relation)
    return citation_eligible(source) and str(relation) != "insufficient"


def _author_rows(source):
    try:
        metadata = json.loads(source.get('author_metadata') or 'null')
    except (ValueError, TypeError):
        metadata = None
    return metadata if isinstance(metadata, list) and metadata else source.get('authors')


def author_metadata_problems(source: dict) -> list[str]:
    """Reject obvious affiliation/footnote contamination, never guess the author."""
    if source.get('author_verified_by') == 'unresolved':
        return [source.get('author_note') or '作者原始署名尚未核准']
    value = _author_rows(source)
    names = value if isinstance(value, list) else re.split(r'\s*[,;；]\s*', str(value or ''))
    if not isinstance(value, list) and re.search(r'[\u4e00-\u9fff]\s+[\u4e00-\u9fff]', str(value or '')):
        return ['中文作者姓名含分隔空格，姓/名顺序和完整署名须回出版原页核对']
    personal = [name for name in names if isinstance(name, dict) and name.get('family')]
    if (any(re.search(r'[\u4e00-\u9fff]', str(name.get('family'))) for name in personal)
            and any(re.search(r'[A-Za-z]', str(name.get('family'))) for name in personal)
            and sum(name.get('sequence') == 'first' for name in personal) > 1):
        return ['中英文署名中出现重复第一作者，须按出版原页区分作者与译名']
    if (len(personal) >= 2 and all(str(name.get('given') or '').isupper()
            and len(str(name.get('given') or '')) > 1 and str(name.get('family') or '').istitle()
            for name in personal)):
        return ['作者 given/family 字段疑似整体倒置，须核对原始署名，不能直接缩写']
    for name in names:
        text = (str(name.get('name') or '') or ' '.join(str(name.get(k) or '') for k in ('family','given'))
                ) if isinstance(name, dict) else str(name)
        if re.search(r'\b\d{5,6}\b|@', text) or (len(text) > 140 and re.search(r'[;；]', text)):
            return ['作者字段含地址或多个机构串，须回出版原页核对完整作者列表']
        if isinstance(name, dict) and name.get('name') and not name.get('family'):
            continue  # A corporate author is legitimate; an address is not.
        if (re.search(r'(^|\s)\d+[.)]?\s|@', text) or (len(names) > 1 and re.search(
                r'\b(?:university|department|laboratory|research cent(?:er|re))\b|大学|实验室', text, re.I))):
            return ['作者字段含疑似机构、地址或脚注，须回出版原页核对，不能当作姓名缩写']
    return []


def _authors(value) -> str:
    names = value if isinstance(value, list) else re.split(r"\s*[,;；]\s*", str(value or ""))
    names = [name for name in names if name]
    out = []
    for name in names[:3]:
        if isinstance(name, dict):
            family, given = str(name.get('family') or ''), str(name.get('given') or '')
            if family:
                out.append(family + given if re.search(r'[\u4e00-\u9fff]', family + given)
                           else (family.upper() + ' ' + ' '.join(p[0].upper() for p in re.findall(r'[A-Za-z]+', given))).strip())
            elif name.get('name'):
                out.append(str(name['name']))
            continue
        name = str(name).strip()
        if re.search(r"[\u4e00-\u9fff]", name):
            out.append(re.sub(r'\s+', '', name))
        elif re.search(r'\b(?:university|society|organization|organisation|institute|department)\b', name, re.I):
            out.append(name)
        elif re.fullmatch(r"[A-Z][A-Z'’ -]+(?: [A-Z]\.?)+", name):
            out.append(name.replace('.', ''))
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
    authors, venue = _authors(_author_rows(source)), str(source.get("venue") or "")
    code = {"journal-article": "J", "journal": "J", "proceedings-article": "C", "conference": "C",
            "book": "M", "monograph": "M", "book-chapter": "M", "thesis": "D",
            "standard": "S", "report": "R", "webpage": "EB", "web-page": "EB"}.get(kind, "Z")
    online = str(source.get('medium') or '').upper() == 'OL'
    entry = f"{authors}. {title}[{code}{'/OL' if online else ''}]"
    if code == "J":
        entry += ". " + venue + f", {year}"
        volume, issue, pages = (str(source.get(key) or "") for key in ("volume", "issue", "pages"))
        if volume:
            entry += f", {volume}"
        if issue:
            entry += f"({issue})"
        if pages:
            entry += f": {pages}"
    else:
        if code == 'C' or kind == 'book-chapter':
            editors = _authors(source.get('editors'))
            entry += "//" + ((editors + '. ') if editors else '') + venue
        edition = str(source.get('edition') or '')
        if edition and edition not in {'1','1版','1st'}:
            entry += '. ' + edition
        chinese = bool(re.search(r'[\u4e00-\u9fff]', title + authors))
        place = source.get('publication_place') or ('' if online else '[出版地不详]' if chinese else '[S.l.]')
        publisher = source.get('publisher') or ('' if online else '[出版者不详]' if chinese else '[s.n.]')
        entry += ". " + ": ".join(str(v) for v in (place, publisher) if v)
        entry += f", {year}"
        if source.get('pages'):
            entry += ': ' + str(source['pages'])
    if online:
        if source.get('updated_date'):
            entry += '(' + str(source['updated_date']) + ')'
        if source.get('accessed_date'):
            entry += '[' + str(source['accessed_date']) + ']'
    doi = normalize_doi(source.get("doi", ""))
    entry = entry.rstrip(". ,") + "."
    if online and source.get('url'):
        entry += ' ' + str(source['url']).rstrip('.') + '.'
    if doi:
        entry += f" DOI:{doi}."
    return entry


def citation_suffix(text: str, mark: str) -> str:
    value = text.rstrip()
    if not mark:
        return value
    return value[:-1] + mark + value[-1] if value.endswith(("。", ".", "；", ";")) else value + mark


def citation_numbers(block, numbers: dict[str, int]) -> list[int]:
    """One ordering rule for every renderer; source identity stays unchanged."""
    return sorted({numbers[ref.id] for kind, ref in zip(block.ref_kinds, block.refs)
                   if kind == "source" and ref.id in numbers})


def citation_group(numbers: list[int]) -> str:
    """GB/T 7714-2015 numeric groups: ascending, unique, consecutive ranges."""
    values = sorted(set(numbers))
    groups = []
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[j + 1] == values[j] + 1:
            j += 1
        groups.append(str(values[i]) if i == j else f"{values[i]}-{values[j]}")
        i = j + 1
    return "[" + ",".join(groups) + "]" if groups else ""


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
