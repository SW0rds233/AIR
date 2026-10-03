from __future__ import annotations

"""KB 数据模型: 权威文献记录、来源溯源、章节/分块、知识卡片。"""

import hashlib
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.utils.file_utils import get_timestamp


def now() -> str:
    return get_timestamp()


class DocType(str, Enum):
    journal = "journal"
    conference = "conference"
    thesis = "thesis"
    preprint = "preprint"
    report = "report"
    standard = "standard"
    patent = "patent"
    book = "book"
    other = "other"


class CardType(str, Enum):
    definition = "definition"
    theorem = "theorem"
    lemma = "lemma"
    proposition = "proposition"
    corollary = "corollary"
    assumption = "assumption"
    method = "method"
    dataset = "dataset"
    result = "result"
    limitation = "limitation"
    other = "other"


class IdentityKeys(BaseModel):
    doi: str = ""
    title_norm: str = ""
    first_author_norm: str = ""
    year: str = ""
    file_hash: str = ""
    openalex_id: str = ""

    def strong(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.file_hash:
            return f"hash:{self.file_hash}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        if self.title_norm:
            return f"title:{self.title_norm}|{self.first_author_norm}|{self.year}"
        return ""


class Provenance(BaseModel):
    origin: str = ""          # manual / machine / enriched
    detail: str = ""          # 文件路径 / API 名
    url: str = ""
    file: str = ""
    fetched_at: str = Field(default_factory=now)


class LitRecord(BaseModel):
    doc_id: str
    title: str = ""
    authors: str = ""
    year: str = ""
    venue: str = ""
    doc_type: DocType = DocType.other
    language: str = ""        # zh / en / ""
    abstract: str = ""
    doi: str = ""
    url: str = ""
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    manual_asserted: bool = False
    existence_verified: bool = False
    peer_reviewed: bool = False
    credibility: str = "low"  # high / medium / low
    parse_quality: str = ""   # ok / short / ocr / failed
    # P1-4: 视觉异常片段 (接近背景色/极小字/页外/被覆盖), 含页码与定位
    visibility_flags: list[dict] = Field(default_factory=list)
    num_pages: int = 0
    has_fulltext: bool = False
    identity: IdentityKeys = Field(default_factory=IdentityKeys)
    provenance: list[Provenance] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class Section(BaseModel):
    doc_id: str
    index: int
    heading: str = ""
    page: int = 0
    text: str = ""
    # 该节在原文中的字符范围 (可选; 供跨页定位)
    char_start: int = -1
    char_end: int = -1
    page_end: int = 0          # 该节结束页 (>page 表示跨页)


class Chunk(BaseModel):
    doc_id: str
    index: int
    section_index: int = -1
    page: int = 0              # 片段**起始**页
    page_end: int = 0          # 片段**结束**页 (跨页时 > page)
    text: str = ""
    # 片段在**本节**内的字符范围 (精确); -1 表示未记录
    char_start: int = -1
    char_end: int = -1
    page_estimated: bool = False   # 页号是否按字符比例估算 (非逐页解析时)

    @property
    def spans_pages(self) -> bool:
        return self.page_end > self.page > 0

    def locator(self) -> str:
        """人可读定位: 优先字符范围, 页号标注是否估算。"""
        if self.page > 0 and self.spans_pages:
            base = f"p{self.page}~{self.page_end}"
        elif self.page > 0:
            base = f"p{self.page}"
        else:
            base = ""
        if self.char_start >= 0:
            chars = f"chars {self.char_start}-{self.char_end}"
            base = f"{base} {chars}".strip() if base else chars
        if base and self.page_estimated:
            base += " (页码为估算)"
        return base


class Card(BaseModel):
    card_id: str
    doc_id: str
    card_type: CardType
    text: str
    locator: str = ""       # 如 "p3 / 定理 2"
    page: int = 0
    heading: str = ""
    confidence: float = 1.0
    symbols: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


def compute_file_hash(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()[:16]
