from __future__ import annotations

"""主题 KB 权威存储 (SQLite) 与关键词检索。

- 文档记录/来源溯源/章节/分块/卡片/事件全部落 SQLite;
- 关键词检索用 LIKE 预筛 + Python 打分 (对中文子串稳健, 不依赖分词器);
- 无 embedding 时仍可完整使用。
"""

import json
import re
import sqlite3
import threading
from pathlib import Path

from src.kb.schema import Card, Chunk, LitRecord, Provenance, Section
from src.utils.file_utils import sanitize_filename


def topic_dir(topic: str) -> Path:
    from src.config import DATA_DIR

    return DATA_DIR / "kb" / sanitize_filename(topic)


def default_db_path(topic: str) -> Path:
    return topic_dir(topic) / "kb.sqlite"


def _tokenize(query: str) -> list[str]:
    terms: list[str] = []
    for tok in re.split(r"[\s,，;；、。.!?？:：/\\|()（）\[\]【】]+", query or ""):
        tok = tok.strip().lower()
        if len(tok) >= 2 and tok not in terms:
            terms.append(tok)
    # 中文长句: 补充 2-gram, 提升子串召回 (无分词器时的稳健兜底)
    for run in re.findall(r"[\u4e00-\u9fff]+", query or ""):
        if len(run) >= 3:
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg not in terms:
                    terms.append(bg)
    return terms[:60]


def _language_matches(record_language: str, requested: str) -> bool:
    """语言过滤: 记录未标注时不过滤 (避免把未标注资料一刀切掉)。"""
    got = (record_language or "").strip().lower()
    want = (requested or "").strip().lower()
    if not got:
        return True
    if got == want:
        return True
    # 常见别名归一
    alias = {"zh": {"chinese", "中文", "zh-cn", "zh_cn"},
             "en": {"english", "英文"},
             "chinese": {"zh", "中文"}, "english": {"en", "英文"}}
    return got in alias.get(want, set()) or want in alias.get(got, set())


def _year_in_range(year, year_range: str) -> bool:
    """年份范围过滤: 形如 "2019-2026" / "2019-" / "-2026" / "2020"。

    记录无年份时不过滤 (检索边界不因缺元数据而缩小), 但也不当作匹配成功。
    """
    text = str(year or "").strip()
    if not text:
        return True
    try:
        value = int(re.search(r"\d{4}", text).group(0))
    except Exception:  # noqa: BLE001
        return True

    spec = (year_range or "").strip().replace("—", "-").replace("~", "-")
    if not spec:
        return True
    m = re.match(r"^(\d{4})?\s*-\s*(\d{4})?$", spec)
    if m:
        low = int(m.group(1)) if m.group(1) else None
        high = int(m.group(2)) if m.group(2) else None
        if low is not None and value < low:
            return False
        return not (high is not None and value > high)
    years = {int(y) for y in re.findall(r"\d{4}", spec)}
    return (not years) or (value in years)


class KBStore:
    def __init__(self, topic: str, db_path: str | Path | None = None,
                 create_if_missing: bool = True):
        """打开主题知识底座。

        `create_if_missing=False` 用于**只读探测**: 研究开始时会为每条命题构造
        知识底座以判断"是否有可用资料", 早期实现会因此在 `data/kb/<topic>/`
        为每个被研究过的主题留下一个空库文件。探测不应产生副作用。
        """
        self.topic = topic
        self.db_path = Path(db_path) if db_path else default_db_path(topic)
        if not create_if_missing and not self.db_path.exists():
            raise FileNotFoundError(f"知识底座不存在: {self.db_path}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY, title TEXT, authors TEXT, year TEXT, venue TEXT,
                    doc_type TEXT, language TEXT, manual INTEGER DEFAULT 0,
                    credibility TEXT, peer_reviewed INTEGER DEFAULT 0,
                    has_fulltext INTEGER DEFAULT 0, search_text TEXT, data TEXT, updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS identity (
                    key TEXT PRIMARY KEY, doc_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS retrieval_queries (
                    query TEXT PRIMARY KEY, searched_at TEXT NOT NULL,
                    results INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS provenance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT, origin TEXT, detail TEXT, url TEXT, file TEXT, fetched_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sections (
                    doc_id TEXT, idx INTEGER, heading TEXT, page INTEGER, text TEXT,
                    PRIMARY KEY (doc_id, idx)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    doc_id TEXT, idx INTEGER, section_index INTEGER, page INTEGER, text TEXT,
                    page_end INTEGER DEFAULT 0, char_start INTEGER DEFAULT -1,
                    char_end INTEGER DEFAULT -1, page_estimated INTEGER DEFAULT 0,
                    PRIMARY KEY (doc_id, idx)
                );
                CREATE TABLE IF NOT EXISTS cards (
                    card_id TEXT PRIMARY KEY, doc_id TEXT, card_type TEXT, text TEXT,
                    locator TEXT, page INTEGER, heading TEXT, confidence REAL, data TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, payload TEXT, created_at TEXT
                );
                """
            )
            self._conn.commit()

    # ---- 文档 ----
    def upsert_document(self, record: LitRecord, search_text: str = "") -> None:
        record.updated_at = record.created_at
        data = record.model_dump(mode="json")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO documents(doc_id,title,authors,year,venue,doc_type,language,"
                "manual,credibility,peer_reviewed,has_fulltext,search_text,data,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.doc_id, record.title, record.authors, record.year, record.venue,
                 record.doc_type.value, record.language, 1 if record.manual_asserted else 0,
                 record.credibility, 1 if record.peer_reviewed else 0,
                 1 if record.has_fulltext else 0, search_text or record.title,
                 json.dumps(data, ensure_ascii=False), record.updated_at),
            )
            self._conn.commit()

    def set_identity(self, keys: list[str], doc_id: str) -> None:
        with self._lock:
            for key in keys:
                if not key:
                    continue
                self._conn.execute(
                    "INSERT OR IGNORE INTO identity(key, doc_id) VALUES (?,?)", (key, doc_id))
            self._conn.commit()

    def find_by_identity(self, keys: list[str]) -> str | None:
        with self._lock:
            for key in keys:
                row = self._conn.execute("SELECT doc_id FROM identity WHERE key=?", (key,)).fetchone()
                if row:
                    return row["doc_id"]
        return None

    def query_recently_searched(self, query: str, *, days: int = 7) -> bool:
        """Only successful, nonempty searches are cached; empty coverage is retried."""
        from datetime import datetime, timedelta, timezone

        with self._lock:
            row = self._conn.execute(
                "SELECT searched_at, results FROM retrieval_queries WHERE query=?",
                (" ".join(query.casefold().split()),)).fetchone()
        if not row or int(row["results"]) <= 0:
            return False
        try:
            stamp = datetime.fromisoformat(row["searched_at"])
            return stamp >= datetime.now(timezone.utc) - timedelta(days=days)
        except ValueError:
            return False

    def mark_query_searched(self, query: str, results: int) -> None:
        from datetime import datetime, timezone

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO retrieval_queries(query,searched_at,results) VALUES (?,?,?)",
                (" ".join(query.casefold().split()),
                 datetime.now(timezone.utc).isoformat(), max(0, int(results))))
            self._conn.commit()

    def cached_pdf_for(self, keys: list[str]) -> str:
        """Find an existing PDF by document identity, never by a title-like filename."""
        doc_id = self.find_by_identity(keys)
        if not doc_id:
            return ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT file FROM provenance WHERE doc_id=? AND file<>'' ORDER BY id DESC",
                (doc_id,)).fetchall()
        for row in rows:
            path = Path(row["file"])
            if path.suffix.lower() == ".pdf" and path.is_file():
                return str(path)
        return ""

    def get_document(self, doc_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row:
            return None
        rec = json.loads(row["data"])
        return rec

    def list_documents(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM documents ORDER BY year DESC, title").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def add_provenance(self, doc_id: str, items: list[Provenance]) -> None:
        with self._lock:
            for p in items:
                self._conn.execute(
                    "INSERT INTO provenance(doc_id,origin,detail,url,file,fetched_at) VALUES (?,?,?,?,?,?)",
                    (doc_id, p.origin, p.detail, p.url, p.file, p.fetched_at))
            self._conn.commit()

    def append_event(self, type_: str, payload: dict) -> None:
        from src.kb.schema import now

        with self._lock:
            self._conn.execute("INSERT INTO events(type,payload,created_at) VALUES (?,?,?)",
                               (type_, json.dumps(payload, ensure_ascii=False), now()))
            self._conn.commit()

    # ---- 章节/分块/卡片 ----
    def add_sections(self, sections: list[Section]) -> None:
        with self._lock:
            for s in sections:
                self._conn.execute(
                    "INSERT OR REPLACE INTO sections(doc_id,idx,heading,page,text) VALUES (?,?,?,?,?)",
                    (s.doc_id, s.index, s.heading, s.page, s.text))
            self._conn.commit()

    def add_chunks(self, chunks: list[Chunk]) -> None:
        with self._lock:
            for c in chunks:
                self._conn.execute(
                    "INSERT OR REPLACE INTO chunks(doc_id,idx,section_index,page,text,"
                    "page_end,char_start,char_end,page_estimated) VALUES (?,?,?,?,?,?,?,?,?)",
                    (c.doc_id, c.index, c.section_index, c.page, c.text,
                     c.page_end, c.char_start, c.char_end, 1 if c.page_estimated else 0))
            self._conn.commit()

    def add_cards(self, cards: list[Card]) -> None:
        with self._lock:
            for c in cards:
                self._conn.execute(
                    "INSERT OR REPLACE INTO cards(card_id,doc_id,card_type,text,locator,page,heading,"
                    "confidence,data) VALUES (?,?,?,?,?,?,?,?,?)",
                    (c.card_id, c.doc_id, c.card_type.value, c.text, c.locator, c.page,
                     c.heading, c.confidence, json.dumps(c.model_dump(mode="json"), ensure_ascii=False)))
            self._conn.commit()

    def get_cards(self, doc_id: str = "", card_types: list[str] | None = None) -> list[dict]:
        sql = "SELECT data FROM cards WHERE 1=1"
        params: list = []
        if doc_id:
            sql += " AND doc_id=?"
            params.append(doc_id)
        if card_types:
            sql += " AND card_type IN (" + ",".join("?" for _ in card_types) + ")"
            params.extend(card_types)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def get_sections(self, doc_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT idx,heading,page,text FROM sections WHERE doc_id=? ORDER BY idx",
                (doc_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_chunk(self, doc_id: str, index: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT idx,section_index,page,text,page_end,char_start,char_end,"
                "page_estimated FROM chunks WHERE doc_id=? AND idx=?",
                (doc_id, index)).fetchone()
        return dict(row) if row else None

    def get_chunks(self, doc_id: str, limit: int = 0) -> list[dict]:
        sql = ("SELECT idx,section_index,page,text,page_end,char_start,char_end,"
               "page_estimated FROM chunks WHERE doc_id=? ORDER BY idx")
        params: list = [doc_id]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def locate(self, doc_id: str, needle: str, start_at: int = 0) -> dict:
        """在原文中定位一个片段, 返回 {page, section_index, heading, char_start, char_end}。

        计划书 §6.1-3: 片段必须记录实际起止页/字符范围, 不能沿用整节首个页码。
        """
        needle = (needle or "").strip()
        if not needle:
            return {}
        lowered = needle.lower()
        for section in self.get_sections(doc_id):
            text = section.get("text") or ""
            pos = text.lower().find(lowered)
            if pos >= 0:
                return {"page": section.get("page", 0), "section_index": section.get("idx", -1),
                        "heading": section.get("heading", ""), "char_start": pos,
                        "char_end": pos + len(needle), "match_scope": "section"}
        # 回退到分块级定位 (分块保留了所属页)
        for chunk in self.get_chunks(doc_id):
            text = chunk.get("text") or ""
            pos = text.lower().find(lowered)
            if pos >= 0:
                return {"page": chunk.get("page", 0),
                        "section_index": chunk.get("section_index", -1), "heading": "",
                        "char_start": pos, "char_end": pos + len(needle),
                        "match_scope": "chunk"}
        return {}

    def count_documents(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return int(row["n"] if row else 0)

    # ---- 检索 ----
    def _doc_rows(self, terms: list[str]) -> list[sqlite3.Row]:
        if not terms:
            return self._conn.execute(
                "SELECT doc_id,title,search_text,year,doc_type,manual,language"
                " FROM documents").fetchall()
        sql_cols = ("SELECT doc_id,title,search_text,year,doc_type,manual,language"
                    " FROM documents WHERE ")
        clauses = " AND ".join("LOWER(search_text) LIKE ?" for _ in terms)
        params = [f"%{t}%" for t in terms]
        rows = self._conn.execute(sql_cols + clauses, params).fetchall()
        if not rows and len(terms) > 1:
            clauses = " OR ".join("LOWER(search_text) LIKE ?" for _ in terms)
            rows = self._conn.execute(sql_cols + clauses, params).fetchall()
        return rows

    def search_keyword(self, query: str, limit: int = 10,
                       doc_type: str = "", manual_only: bool = False,
                       language: str = "", year_range: str = "") -> list[dict]:
        """关键词检索。

        `manual_only` / `language` / `year_range` 都在这里生效: 范围与权限约束
        必须覆盖每条召回路径, 不能在向量或卡片分支上被绕过 (计划书 §9.4)。
        """
        terms = _tokenize(query)
        with self._lock:
            rows = self._doc_rows(terms)
        scored = []
        for row in rows:
            if doc_type and row["doc_type"] != doc_type:
                continue
            if manual_only and not row["manual"]:
                continue
            if language and not _language_matches(row["language"], language):
                continue
            if year_range and not _year_in_range(row["year"], year_range):
                continue
            text = (row["search_text"] or "").lower()
            score = sum(text.count(t) for t in terms) if terms else 0
            snippet, char_start = self._snippet_at(text, terms)
            scored.append({"kind": "doc", "doc_id": row["doc_id"], "title": row["title"],
                           "text": snippet, "score": float(score), "source": "doc",
                           "year": row["year"], "locator": f"chars {char_start}-{char_start + len(snippet)}",
                           "page": 0, "char_start": char_start,
                           "char_end": char_start + len(snippet)})
        scored.sort(key=lambda x: (-x["score"], x["title"]))
        return scored[:limit]

    def search_cards(self, query: str, limit: int = 10,
                     card_types: list[str] | None = None) -> list[dict]:
        terms = _tokenize(query)
        with self._lock:
            rows = self._conn.execute("SELECT data FROM cards").fetchall()
        out = []
        for row in rows:
            card = json.loads(row["data"])
            if card_types and card["card_type"] not in card_types:
                continue
            text = (card["text"] or "").lower()
            score = sum(text.count(t) for t in terms) if terms else 0
            if score <= 0:
                continue
            snippet, char_start = self._snippet_at(text, terms)
            card["kind"] = "card"
            card["score"] = float(score) * float(card.get("confidence", 1.0))
            card["source"] = card.get("card_type", "card")
            card["excerpt"] = snippet
            card["char_start"] = char_start
            card["char_end"] = char_start + len(snippet)
            # 卡片定位来自抽取阶段 (p3 / 定理 2); 若无则退回片段级定位
            if not card.get("locator"):
                card["locator"] = f"chars {char_start}-{char_start + len(snippet)}"
            out.append(card)
        out.sort(key=lambda x: -x["score"])
        return out[:limit]

    @staticmethod
    def _snippet(text: str, terms: list[str], width: int = 120) -> str:
        snippet, _ = KBStore._snippet_at(text, terms, width)
        return snippet

    @staticmethod
    def _snippet_at(text: str, terms: list[str], width: int = 120) -> tuple[str, int]:
        """返回 (片段, 片段在原文中的起始字符位置)。"""
        for term in terms:
            pos = text.find(term)
            if pos != -1:
                start = max(0, pos - width // 2)
                return text[start:start + width], start
        return text[:width], 0

    def stats(self) -> dict:
        with self._lock:
            docs = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            manual = self._conn.execute("SELECT COUNT(*) AS n FROM documents WHERE manual=1").fetchone()["n"]
            fulltext = self._conn.execute("SELECT COUNT(*) AS n FROM documents WHERE has_fulltext=1").fetchone()["n"]
            cards = self._conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
            by_type = {r["doc_type"]: r["n"] for r in self._conn.execute(
                "SELECT doc_type, COUNT(*) AS n FROM documents GROUP BY doc_type")}
            by_card = {r["card_type"]: r["n"] for r in self._conn.execute(
                "SELECT card_type, COUNT(*) AS n FROM cards GROUP BY card_type")}
        return {"topic": self.topic, "documents": docs, "manual": manual,
                "with_fulltext": fulltext, "cards": cards,
                "by_doc_type": by_type, "by_card_type": by_card}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
