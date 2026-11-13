"""Identity of the reader-facing manuscript, independent of task attempts."""
import hashlib
import json


def manuscript_identity(row: dict) -> dict:
    row = row.get("manuscript", row)
    content = {key: row.get(key, "") for key in ("title", "abstract", "keywords")}
    content["sections"] = [{
        "heading": section.get("heading", ""), "level": section.get("level", 1),
        "blocks": [{key: block.get(key) for key in
                    ("block_id", "role", "heading", "text", "math", "data", "refs", "ref_kinds")}
                   for block in section.get("blocks", [])],
    } for section in row.get("sections", [])]
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"manuscript_id": row.get("manuscript_id") or row.get("id") or "",
            "version": row.get("version", 1), "content_hash": digest}
