from __future__ import annotations

"""KB 入库: 人工 (meta.json) 与机器检索结果, 归一化 + 去重合并。

关键约束:
- 人工项绝不因机器项"相似"被替换; 人工字段优先;
- 解析不依赖 embedding; 向量入库为可选增强;
- 每次入库写入 provenance, 可审计。
"""

import json
from pathlib import Path

from src.kb.cards import extract_cards
from src.kb.identity import build_identity, candidate_keys, resolve_doc_id
from src.kb.parse import parse_document
from src.kb.schema import (
    Chunk,
    DocType,
    LitRecord,
    Provenance,
    compute_file_hash,
)
from src.kb.store import KBStore, topic_dir
from src.utils.file_utils import get_timestamp

_SUPPORTED = (".pdf", ".txt", ".md", ".markdown", ".docx")


def ensure_topic(topic: str) -> Path:
    tdir = topic_dir(topic)
    (tdir / "manual").mkdir(parents=True, exist_ok=True)
    (tdir / "machine").mkdir(parents=True, exist_ok=True)
    return tdir


def load_meta(topic: str) -> dict:
    meta_path = topic_dir(topic) / "manual" / "meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _doc_type_from(item: dict) -> DocType:
    published_type = {"journal-article": DocType.journal, "proceedings-article": DocType.conference}.get(item.get("publication_type"))
    if item.get("publication_status") == "published" and published_type:
        return published_type
    t = str(item.get("type", "") or "").strip()
    if t in ("D", "thesis", "学位论文"):
        return DocType.thesis
    venue = f"{item.get('venue', '')} {item.get('source', '')}".lower()
    if "arxiv" in venue or "preprint" in venue:
        return DocType.preprint
    if any(k in venue for k in ("学报", "期刊", "journal", "transactions")):
        return DocType.journal
    if any(k in venue for k in ("conference", "会议", "proceedings", "symposium")):
        return DocType.conference
    if any(k in venue for k in ("report", "报告", "白皮书")):
        return DocType.report
    if "standard" in venue or "标准" in venue:
        return DocType.standard
    if "专利" in venue or "patent" in venue:
        return DocType.patent
    return DocType.other


def _credibility(doc_type: DocType, manual: bool) -> tuple[bool, str]:
    peer = doc_type in (DocType.journal, DocType.conference)
    if peer:
        return True, "high"
    if manual or doc_type in (DocType.thesis, DocType.report, DocType.preprint,
                              DocType.standard, DocType.patent, DocType.book):
        return False, "medium"
    return False, "low"


def _build_search_text(record: LitRecord, sections_text: str) -> str:
    parts = [record.title, record.authors, record.venue, record.abstract, record.keywords and " ".join(record.keywords), sections_text]
    return "\n".join(p for p in parts if p)


def _page_span_for(offsets: list[tuple[int, int]], section) -> list[tuple[int, int]]:
    """把本节内的字符偏移映射为起始/结束页。

    逐页解析器未提供节内页边界时, 按"节内字符位置比例"估算页号, 并**显式标注为估算**
    (计划书 §6.1-3: 不能沿用整节首个页码; 也不得把估算页号冒充精确页码)。
    """
    text_len = max(1, len(getattr(section, "text", "") or ""))
    first = int(getattr(section, "page", 0) or 0)
    last = int(getattr(section, "page_end", 0) or 0) or first
    if first <= 0:
        return [(0, 0) for _ in offsets]
    total_pages = max(1, last - first + 1)
    out: list[tuple[int, int]] = []
    for start, end in offsets:
        start_page = first + min(total_pages - 1, int(start / text_len * total_pages))
        end_page = first + min(total_pages - 1,
                               int(max(0, end - 1) / text_len * total_pages))
        out.append((start_page, max(start_page, end_page)))
    return out


def _chunks_from_sections(doc_id: str, sections) -> list[Chunk]:
    """按节切块, 每块记录**本节内的精确字符范围**与起止页。

    早前实现把整节的 `section.page` 赋给该节切出的每一个 chunk, 导致跨页片段定位错误
    (计划书 §6.1-3 点名)。现在记录真实字符范围, 并按比例估算起止页且标注为估算。
    """
    from src.rag.chunker import chunk_text_with_offsets

    chunks: list[Chunk] = []
    idx = 0
    for section in sections:
        pieces = chunk_text_with_offsets(getattr(section, "text", "") or "", 500, 100)
        spans = _page_span_for([(s, e) for _, s, e in pieces], section)
        section_page = int(getattr(section, "page", 0) or 0)
        section_page_end = int(getattr(section, "page_end", 0) or 0)
        estimated = section_page > 0 and section_page_end > section_page
        for (piece, start, end), (page, page_end) in zip(pieces, spans):
            chunks.append(Chunk(
                doc_id=doc_id, index=idx, section_index=section.index,
                page=page or section_page,
                page_end=page_end or page or section_page,
                text=piece, char_start=start, char_end=end,
                page_estimated=estimated,
            ))
            idx += 1
    return chunks


def _embed_chunks(topic: str, doc_id: str, chunks: list[Chunk], record: LitRecord,
                  embed: bool) -> int:
    if not embed or not chunks:
        return 0
    try:
        from src.rag.vector_store import add_fulltext_chunks, embedding_available

        if not embedding_available():
            return 0
    except Exception:  # noqa: BLE001
        return 0
    chunk_dicts = [{"title": record.title, "chunk_index": c.index, "text": c.text} for c in chunks]
    meta = {"authors": record.authors, "year": record.year,
            "source": record.venue or record.doc_type.value, "url": record.url, "bibtex": ""}
    from src.utils.file_utils import sanitize_filename

    return add_fulltext_chunks(chunk_dicts, collection_name=f"kb_{sanitize_filename(topic)}",
                               paper_meta=meta)


def _merge_or_create(store: KBStore, item: dict, file_hash: str = "") -> tuple[str, bool]:
    """返回 (doc_id, is_new)。命中既有记录则合并 provenance, 不覆盖人工字段。"""
    identity = build_identity(item, file_hash=file_hash)
    keys = candidate_keys(identity)
    existing = store.find_by_identity(keys)
    if existing:
        return existing, False
    return resolve_doc_id(keys), True


def ingest_manual(topic: str, embed: bool = True, store: KBStore | None = None) -> dict:
    ensure_topic(topic)
    store = store or KBStore(topic)
    meta = load_meta(topic)
    manual_dir = topic_dir(topic) / "manual"
    report = {"topic": topic, "ingested": [], "merged": [], "errors": []}

    for path in sorted(manual_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _SUPPORTED:
            continue
        if path.name == "meta.json":
            continue
        info = meta.get(path.name, {}) or {}
        from src.rag.manual_pdfs import _parse_filename

        title, year = _parse_filename(path.name)
        item = {
            "title": info.get("title") or title,
            "authors": info.get("authors", ""),
            "year": str(info.get("year", year) or ""),
            "venue": info.get("venue", ""),
            "type": info.get("type", ""),
            "doi": info.get("doi", ""),
            "url": info.get("url", ""),
            "abstract": info.get("abstract", ""),
            "keywords": info.get("keywords", []),
            "tags": info.get("tags", []),
            "language": info.get("language", ""),
        }
        file_hash = compute_file_hash(str(path))
        doc_id, is_new = _merge_or_create(store, item, file_hash)
        doc_type = _doc_type_from(item)
        peer, credibility = _credibility(doc_type, manual=True)

        parsed = parse_document(str(path), doc_id=doc_id)
        sections_text = "\n".join(s.text for s in parsed.sections)
        record = LitRecord(
            doc_id=doc_id, title=item["title"], authors=item["authors"], year=item["year"],
            venue=item["venue"], doc_type=doc_type,
            language=item["language"] or parsed.language, abstract=item["abstract"],
            doi=item["doi"], url=item["url"], keywords=list(item["keywords"] or []),
            tags=list(item["tags"] or []), manual_asserted=True, existence_verified=True,
            peer_reviewed=peer, credibility=credibility, parse_quality=parsed.parse_quality,
            num_pages=len(parsed.pages) or (1 if parsed.full_text else 0),
            has_fulltext=bool(parsed.full_text), identity=build_identity(item, file_hash),
            # 视觉异常片段随文档落库, 供检索/工作台标注"需核对"
            visibility_flags=list(parsed.visibility_flags),
        )
        if not is_new:
            prior = store.get_document(doc_id) or {}
            record.created_at = prior.get("created_at", record.created_at)
        store.upsert_document(record, search_text=_build_search_text(record, sections_text))
        store.set_identity(candidate_keys(record.identity), doc_id)
        store.add_provenance(doc_id, [Provenance(origin="manual", detail=path.name,
                                                 file=str(path), fetched_at=get_timestamp())])

        if parsed.full_text:
            store.add_sections(parsed.sections)
            chunks = _chunks_from_sections(doc_id, parsed.sections)
            store.add_chunks(chunks)
            cards = extract_cards(doc_id, parsed)
            store.add_cards(cards)
            _embed_chunks(topic, doc_id, chunks, record, embed)
        store.append_event("ingest_manual", {"doc_id": doc_id, "file": path.name,
                                             "quality": parsed.parse_quality})
        (report["merged"] if not is_new else report["ingested"]).append(
            {"doc_id": doc_id, "title": record.title, "quality": parsed.parse_quality,
             "cards": len(store.get_cards(doc_id))})

    return report


def ingest_machine(topic: str, papers: list[dict], embed: bool = False,
                   store: KBStore | None = None) -> dict:
    ensure_topic(topic)
    store = store or KBStore(topic)
    report = {"topic": topic, "ingested": [], "merged": [], "errors": []}
    from src.publication.references import BIBLIO_FIELDS
    from src.kb.identity import normalize_doi
    for paper in papers:
        title = (paper.get("title") or "").strip()
        if not title:
            continue
        doc_id, is_new = _merge_or_create(store, paper)
        prior = store.get_document(doc_id) if not is_new else None
        pdf_path = paper.get("pdf_path")
        if prior and (prior.get("has_fulltext") or not (pdf_path and Path(pdf_path).is_file())) and all(
            str(prior.get(field) or "").strip().casefold()
            == (normalize_doi(paper.get(field) or "") if field == "doi" else
                str(paper.get(field) or "").strip().casefold())
            for field in ("title", "authors", "year", "venue", "doi", "abstract",
                          "publication_status", "publication_type", "publication_verified_by")
        ):
            report["merged"].append({"doc_id": doc_id, "title": title,
                                     "reused_existing": True})
            continue
        doc_type = _doc_type_from(paper)
        peer, credibility = _credibility(doc_type, manual=False)
        record = LitRecord(
            doc_id=doc_id, title=title, authors=paper.get("authors", ""),
            year=str(paper.get("year", "") or ""), venue=paper.get("venue", ""),
            doc_type=doc_type, language=paper.get("language", ""),
            abstract=paper.get("abstract", ""), doi=normalize_doi(paper.get("doi") or ""),
            url=paper.get("url", ""), keywords=list(paper.get("keywords", []) or []),
            manual_asserted=False, existence_verified=bool(paper.get("doi") or paper.get("url")),
            peer_reviewed=peer, credibility=credibility,
            identity=build_identity(paper),
            **{key: str(paper.get(key) or "") for key in BIBLIO_FIELDS},
        )
        parsed = None
        if pdf_path and Path(pdf_path).exists():
            parsed = parse_document(pdf_path, doc_id=doc_id)
            record.has_fulltext = bool(parsed.full_text)
            record.num_pages = len(parsed.pages)
            record.parse_quality = parsed.parse_quality
            record.visibility_flags = list(parsed.visibility_flags)
        if not is_new:
            prior = store.get_document(doc_id) or {}
            record.created_at = prior.get("created_at", record.created_at)
            if not record.abstract:
                record.abstract = prior.get("abstract") or ""
            # A repeated metadata-only hit must not erase an already read full text.
            if not record.has_fulltext and prior.get("has_fulltext"):
                for field in ("has_fulltext", "num_pages", "parse_quality", "visibility_flags"):
                    if field in prior:
                        setattr(record, field, prior[field])
            from src.publication.references import citation_eligible
            if citation_eligible(prior) and not citation_eligible(record.model_dump()):
                for field in (*BIBLIO_FIELDS, "title", "authors", "year", "venue", "doi", "url"):
                    if field in prior:
                        setattr(record, field, prior[field])
                record.identity = build_identity(record.model_dump())
                record.doc_type = _doc_type_from(record.model_dump())
                record.peer_reviewed, record.credibility = _credibility(record.doc_type, manual=False)
        store.upsert_document(record, search_text=_build_search_text(
            record, "\n".join(s.text for s in parsed.sections) if parsed else ""))
        store.set_identity(candidate_keys(record.identity), doc_id)
        store.add_provenance(doc_id, [Provenance(
            origin="machine", detail=paper.get("api_source", paper.get("source", "")),
            url=paper.get("url", ""), file=str(pdf_path or ""), fetched_at=get_timestamp())])
        if parsed and parsed.full_text:
            store.add_sections(parsed.sections)
            chunks = _chunks_from_sections(doc_id, parsed.sections)
            store.add_chunks(chunks)
            store.add_cards(extract_cards(doc_id, parsed))
            _embed_chunks(topic, doc_id, chunks, record, embed)
        store.append_event("ingest_machine", {"doc_id": doc_id, "title": title})
        (report["ingested"] if is_new else report["merged"]).append({"doc_id": doc_id, "title": title})
    return report
