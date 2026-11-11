"""Explicit, resumable vector indexing of already registered library chunks."""
from __future__ import annotations

import hashlib
from typing import Any

from src.kb.store import KBStore, default_db_path
from src.utils.file_utils import sanitize_filename


def chunk_vector_id(doc_id: str, index: int, text: str) -> str:
    return "kbchunk-" + hashlib.sha256(f"{doc_id}:{index}:{text}".encode()).hexdigest()


def reindex_library(topic: str, *, batch_size: int = 32) -> dict[str, Any]:
    report: dict[str, Any] = {"ok": False, "topic": topic, "indexed": 0,
                              "skipped": 0, "total": 0, "errors": []}
    if not topic or not default_db_path(topic).is_file():
        report["errors"].append("资料库不存在")
        return report
    from langchain_core.documents import Document
    from src.rag.vector_store import embedding_available, get_vector_store

    if not embedding_available(force=True):
        report["errors"].append("Embedding 不可用，请检查配置与配额后重试")
        return report
    store = KBStore(topic)
    try:
        vector = get_vector_store(f"kb_{sanitize_filename(topic)}")
        known = set(vector.get(include=[]).get("ids") or [])
        pending = []
        for record in store.list_documents():
            for chunk in store.get_chunks(record["doc_id"]):
                text = str(chunk.get("text") or "").strip()
                if not text:
                    continue
                report["total"] += 1
                index = int(chunk["idx"])
                vector_id = chunk_vector_id(record["doc_id"], index, text)
                if vector_id in known:
                    report["skipped"] += 1
                    continue
                pending.append((vector_id, Document(page_content=text, metadata={
                    "doc_id": record["doc_id"], "title": record.get("title") or "",
                    "chunk_index": index, "page": int(chunk.get("page") or 0),
                })))
        for start in range(0, len(pending), max(1, batch_size)):
            batch = pending[start:start + max(1, batch_size)]
            vector.add_documents([item[1] for item in batch], ids=[item[0] for item in batch])
            report["indexed"] += len(batch)
        report["ok"] = True
    except Exception as error:
        # Stop on the first provider error. Successfully indexed batches remain resumable.
        report["errors"].append(f"{type(error).__name__}: {str(error)[:240]}")
    finally:
        store.close()
    return report
