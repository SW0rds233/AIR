from __future__ import annotations

"""文献身份解析与跨入口去重。

匹配优先级: DOI > 文件 hash > OpenAlex ID > 归一化(标题)+首作者+年份。
人工项永不因机器项"相似"被静默替换: 命中即合并为同一 doc_id 并保留全部来源。
"""

import re

from src.kb.schema import IdentityKeys


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (title or "").lower())


def first_author(authors: str) -> str:
    if not authors:
        return ""
    part = re.split(r"[,，;；、]| and ", authors.strip())[0]
    return re.sub(r"[^a-z\u4e00-\u9fff]", "", part.lower())


def build_identity(item: dict, file_hash: str = "") -> IdentityKeys:
    return IdentityKeys(
        doi=(item.get("doi") or "").strip().lower(),
        title_norm=normalize_title(item.get("title", "")),
        first_author_norm=first_author(item.get("authors", "")),
        year=str(item.get("year", "") or ""),
        file_hash=file_hash,
        openalex_id=(item.get("openalex_id") or "").strip().lower(),
    )


def candidate_keys(identity: IdentityKeys) -> list[str]:
    keys = []
    if identity.doi:
        keys.append(f"doi:{identity.doi}")
    if identity.file_hash:
        keys.append(f"hash:{identity.file_hash}")
    if identity.openalex_id:
        keys.append(f"openalex:{identity.openalex_id}")
    if identity.title_norm:
        keys.append(f"title:{identity.title_norm}|{identity.first_author_norm}|{identity.year}")
        # 弱键: 仅标题 (供跨年份/作者缺失时兜底, 命中需人工确认)
        keys.append(f"titleonly:{identity.title_norm}")
    return keys


def resolve_doc_id(keys: list[str]) -> str:
    """无既有记录时, 由强键派生稳定 doc_id。"""
    import hashlib

    basis = next((k for k in keys if k.startswith(("doi:", "openalex:", "hash:"))), keys[0] if keys else "")
    return "doc-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
