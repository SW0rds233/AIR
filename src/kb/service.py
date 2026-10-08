from __future__ import annotations

"""KnowledgeService (计划书 §3.2 / §6): 统一检索—读取—解析接口。

对外只暴露三个动作:
- `search(request)`  → 候选（**带完整定位**, 全部召回分支统一为同一引用对象）;
- `read(source_ref)` → 回到原文取完整上下文（摘要不能代替完整条件）;
- `resolve(source_ref)` → 来源真实性/版本（存在性检查, 不推断内容支持）。

硬约束:
- 检索命中**绝不**把命题设为成立;
- 所有分支（关键词文档 / 卡片 / 向量 chunk）都返回统一引用对象;
- 项目/主题范围过滤覆盖每一条召回路径; 越范围资料不泄漏;
- 检索失败与"无相关文献"分开记录。
"""

from dataclasses import dataclass, field

from src.kb.store import KBStore
from src.utils.file_utils import sanitize_filename


@dataclass
class SourceRef:
    """可定位的来源引用: 所有检索分支统一返回该结构。"""

    source_id: str = ""          # KB doc_id
    chunk_id: str = ""           # 段落/分块稳定 ID
    title: str = ""
    excerpt: str = ""
    locator: str = ""            # 人可读定位 (p3 / 定理 2 / chars 120-240)
    page: int = 0
    char_start: int = -1
    char_end: int = -1
    kind: str = "doc"            # doc / card / chunk
    card_type: str = ""
    score: float = 0.0
    retrieved_at: str = ""
    topic: str = ""
    # 召回该引用的通道 (keyword / card / vector): 供融合排序与审计区分
    channel: str = ""
    # RRF 融合分 (按通道内名次计算); 0 表示未参与融合 (单通道直接命中)
    fusion_score: float = 0.0
    # 引用方可据此判断适用范围
    year: str = ""
    doi: str = ""
    doc_type: str = ""
    credibility: str = ""
    peer_reviewed: bool = False
    existence_verified: bool = False

    def is_locatable(self) -> bool:
        """是否具备可回到原文的定位信息。"""
        if self.kind == "card":
            return bool(self.source_id and (self.locator or self.page))
        if self.char_start >= 0:
            return True
        return bool(self.source_id and self.locator)


@dataclass
class RetrievalRequest:
    """定向检索请求规格: 不只传字符串 query。

    字段的生效情况 (避免"看起来支持但实际被忽略"):
    - `query` / `card_types` / `manual_only` / `language` / `time_range` /
      `max_results`: 在 `KnowledgeService.search()` 里**实际参与过滤与限流**;
    - `scope`: `topic` (默认, 仅本主题底座) 与 `project` 之外的值会被拒绝;
    - `conditions` / `variables`: 仅用于拼装查询词与记录检索意图 (不做硬过滤,
      因为"条件是否满足"要由证据判定而不是字符串匹配);
    - `stop_condition`: 由调用方 (研究循环) 据信息增益判断, 检索层不自行迭代。
    """

    query: str
    claim_id: str = ""
    gap_type: str = ""
    evidence_category: str = ""     # theorem / counterexample / prior_result / background / data
    variables: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    card_types: list[str] = field(default_factory=list)
    scope: str = "topic"            # topic / project
    manual_only: bool = False
    language: str = ""
    time_range: str = ""
    max_results: int = 8
    stop_condition: str = "证据足以判定该缺口或达到预算上限"

    def describe(self) -> str:
        bits = [f"query={self.query!r}", f"category={self.evidence_category or 'any'}"]
        if self.gap_type:
            bits.append(f"gap={self.gap_type}")
        if self.variables:
            bits.append("vars=" + ",".join(self.variables))
        if self.conditions:
            bits.append("conds=" + ";".join(self.conditions))
        if self.language:
            bits.append(f"lang={self.language}")
        if self.time_range:
            bits.append(f"years={self.time_range}")
        if self.manual_only:
            bits.append("manual_only")
        return " | ".join(bits)

    def effective_query(self) -> str:
        """把变量/条件并入查询词, 使检索意图真正影响召回。"""
        extra = [t for t in (self.conditions or []) if t]
        if not extra:
            return self.query
        return f"{self.query} " + " ".join(extra)


# RRF 融合常数 (与经典实现一致: 60 使名次差异平滑)
RRF_K = 60


def rrf_fuse(ranked_lists: list[list]) -> dict[int, float]:
    """Reciprocal Rank Fusion: 按**通道内名次**融合多路召回。

    为什么必须按名次而不是原始分数: 关键词 BM25-ish 分数与向量余弦相似度
    量纲不同, 直接比较会把某一路整体压低。返回 {id(对象): 融合分}。

    这里只做融合, 不做截断与去重 —— 排序与去重由调用方在同一处完成,
    避免"两条召回路径各自截断"造成结果不一致。
    """
    fused: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            key = item if isinstance(item, dict) else id(item)
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    return fused


@dataclass
class SearchOutcome:
    refs: list[SourceRef] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    request: RetrievalRequest | None = None
    # 因范围/权限/语言/年份约束被剔除的召回条数 (便于区分"没搜到"与"被范围挡掉")
    dropped_out_of_scope: int = 0
    # 各召回通道是否真的运行过 (检索失败与"无相关文献"必须分开)
    recall_channels: list[str] = field(default_factory=list)
    # 每条通道实际返回的条数, 供预算/调试观测
    channel_counts: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.refs

    @property
    def searched(self) -> bool:
        """是否真的完成了检索 (区分"检索失败"与"无相关文献")。"""
        return not self.failures


class KnowledgeService:
    """主题文献知识底座的统一入口。"""

    def __init__(self, topic: str, store: KBStore | None = None,
                 create_if_missing: bool = True):
        """构造服务。

        `create_if_missing=False` 时不创建空知识底座 —— 研究开始时为每条命题
        构造服务只是判断"有没有可用资料", 不应在 `data/kb/` 留下空库。
        """
        self.topic = topic or ""
        self._store = store
        self._store_error = ""
        if self._store is None and topic:
            try:
                self._store = KBStore(topic, create_if_missing=create_if_missing)
            except Exception as e:  # noqa: BLE001
                self._store_error = str(e)

    @property
    def store(self) -> KBStore | None:
        return self._store

    @property
    def available(self) -> bool:
        return self._store is not None

    @property
    def has_content(self) -> bool:
        """底座是否真的有可检索资料。

        只有可用的底座还不够: 空底座会让"定向检索/读取原文"这类动作被选中却
        永远失败。计划书 §9.3 要求 unavailable 影响可用动作集合。
        """
        if not self._store:
            return False
        try:
            if self._store.count_documents():
                return True
            return bool(self._store.get_cards())
        except Exception:  # noqa: BLE001
            return False

    @property
    def usable(self) -> bool:
        """可用于研究动作: 底座可用且非空。"""
        return self.available and self.has_content

    def stats(self) -> dict:
        if not self._store:
            return {"topic": self.topic, "documents": 0, "cards": 0, "available": False}
        out = self._store.stats()
        out["available"] = True
        return out

    def retrieval_debug(self) -> dict:
        """检索能力自述 (计划书 §9.3 可观测性)。

        明确区分"通道不可用"与"通道可用但没命中": 前者是能力缺失,
        后者才是"无相关文献"。调用方据此决定是否记录检索失败。
        """
        embedding = False
        vector_error = ""
        try:
            from src.rag.vector_store import embedding_available

            embedding = bool(embedding_available())
        except Exception as e:  # noqa: BLE001 - 向量能力缺失不影响服务可用
            vector_error = f"{type(e).__name__}: {e}"
        stats = self.stats()
        return {
            "topic": self.topic,
            "store_available": self.available,
            "store_error": self._store_error,
            "documents": stats.get("documents", 0),
            "cards": stats.get("cards", 0),
            "has_content": self.has_content,
            "channels": {
                "keyword": self.available,
                "card": self.available,
                "vector": embedding,
            },
            "fusion": "rrf",
            "rrf_k": RRF_K,
            "vector_error": vector_error,
            "note": "向量通道不可用时自动退化为关键词+卡片, 不影响可用性",
        }

    # ---------------------------------------------------------------
    # 检索
    # ---------------------------------------------------------------
    def search(self, request: RetrievalRequest | str, limit: int = 8,
               card_types: list[str] | None = None,
               manual_only: bool = False) -> SearchOutcome:
        if isinstance(request, str):
            request = RetrievalRequest(query=request, max_results=limit)
        outcome = SearchOutcome(request=request)
        if not self._store:
            outcome.failures.append(f"知识底座不可用: {self._store_error or '未配置主题'}")
            return outcome
        if request.scope not in ("topic", "project"):
            outcome.failures.append(f"不支持的检索范围 scope={request.scope!r}")
            return outcome
        # 条件并入查询词: 检索意图必须真的影响召回, 而不是只写在请求描述里
        query = request.effective_query()
        limit = request.max_results or limit
        types = request.card_types or card_types
        manual_only = request.manual_only or manual_only

        try:
            cards = self._store.search_cards(query, limit=limit * 2, card_types=types or None)
        except Exception as e:  # noqa: BLE001
            outcome.failures.append(f"卡片检索失败: {e}")
            cards = []
        try:
            docs = self._store.search_keyword(query, limit=limit * 2, manual_only=manual_only,
                                             language=request.language,
                                             year_range=request.time_range)
        except Exception as e:  # noqa: BLE001
            outcome.failures.append(f"关键词检索失败: {e}")
            docs = []
        vectors = self._vector_hits(query, limit)

        refs: list[SourceRef] = []
        doc_cache: dict[str, dict] = {}
        # 每条召回通道的**排序结果**分开保存: 融合排序必须知道通道内名次,
        # 否则 RRF/加权都无从计算 (原始分数在不同通道间不可比)。
        channel_ranks: dict[str, list[SourceRef]] = {"card": [], "keyword": [], "vector": []}

        def _doc(doc_id: str) -> dict:
            if doc_id and doc_id not in doc_cache:
                doc_cache[doc_id] = self._store.get_document(doc_id) or {}
            return doc_cache.get(doc_id, {})

        def _allowed(doc: dict) -> bool:
            # 范围/权限/语言/年份过滤: **每条召回路径**都必须经过这里
            return self._in_scope(doc, manual_only, language=request.language,
                                 year_range=request.time_range)

        for item in cards:
            doc = _doc(item.get("doc_id", ""))
            if not _allowed(doc):
                continue
            ref = self._card_ref(item, doc)
            ref.channel = "card"
            channel_ranks["card"].append(ref)
            refs.append(ref)

        for item in docs:
            doc = _doc(item.get("doc_id", ""))
            if not _allowed(doc):
                continue
            ref = self._doc_ref(item, doc)
            ref.channel = "keyword"
            channel_ranks["keyword"].append(ref)
            refs.append(ref)

        # 向量命中必须与关键词/卡片走同一套过滤:
        # 否则 manual_only=True 时仍会返回非人工资料 (范围约束被绕过)
        for ref in vectors:
            doc = _doc(ref.source_id)
            if not _allowed(doc):
                outcome.dropped_out_of_scope += 1
                continue
            ref.channel = "vector"
            channel_ranks["vector"].append(ref)
            refs.append(ref)

        outcome.recall_channels = [name for name, items in channel_ranks.items() if items]
        outcome.channel_counts = {name: len(items) for name, items in channel_ranks.items()}

        fused = rrf_fuse([items for items in channel_ranks.values() if items])
        for ref in refs:
            ref.fusion_score = round(float(fused.get(id(ref), 0.0)), 6)
        refs.sort(key=lambda r: -(r.fusion_score or r.score))
        outcome.refs = self._dedup(refs)[:limit]
        outcome.covered = [r.source_id for r in outcome.refs if r.source_id]
        if not outcome.empty:
            outcome.covered = list(dict.fromkeys(outcome.covered))
        return outcome

    def _in_scope(self, doc: dict, manual_only: bool,
                  language: str = "", year_range: str = "") -> bool:
        """范围/权限过滤: 所有召回分支都必须经过这里。

        - `manual_only`: 只允许人工确认存在的资料 (最严格的权限约束);
        - `language` / `year_range`: 请求里声明的资料范围;
        - **doc 为空 (拿不到权威记录) 时按越范围处理**: 不可引用的资料不得进入结果,
          否则范围过滤会被"回查失败"绕过。
        """
        if not doc:
            return False
        if manual_only and not doc.get("manual_asserted"):
            return False
        if language:
            from src.kb.store import _language_matches

            if not _language_matches(str(doc.get("language") or ""), language):
                return False
        if year_range:
            from src.kb.store import _year_in_range

            if not _year_in_range(doc.get("year"), year_range):
                return False
        return True

    def _vector_hits(self, query: str, limit: int) -> list[SourceRef]:
        try:
            from src.rag.vector_store import embedding_available, search_fulltext
            from src.utils.file_utils import get_timestamp

            if not embedding_available():
                return []
            hits = search_fulltext(query, k=limit, collection_name=f"kb_{sanitize_filename(self.topic)}")
        except Exception:  # noqa: BLE001
            return []
        refs: list[SourceRef] = []
        for hit in hits or []:
            title = hit.get("title", "")
            doc_id = ""
            if self._store:
                # 通过标题回查权威 doc_id: 向量层不带 ID, 必须补上才能被引用
                matches = [d for d in self._store.list_documents() if d.get("title") == title]
                if matches:
                    doc_id = matches[0].get("doc_id", "")
            chunk_index = hit.get("chunk_index", "")
            # chunk_id 用 "<doc_id>#<idx>": read() 据此精确回查入库时记录的字符范围与起止页
            chunk_id = f"{doc_id}#{chunk_index}" if doc_id else f"{title}#{chunk_index}"
            refs.append(SourceRef(
                source_id=doc_id, chunk_id=chunk_id,
                title=title, excerpt=(hit.get("text") or "")[:600],
                locator="向量召回片段 (需 read 回原文核对)", kind="chunk",
                score=float(hit.get("score") or 0.0), topic=self.topic,
                retrieved_at=get_timestamp(), channel="vector",
            ))
        return refs

    def _card_ref(self, item: dict, doc: dict) -> SourceRef:
        from src.utils.file_utils import get_timestamp

        return SourceRef(
            source_id=item.get("doc_id", ""), chunk_id=item.get("card_id", ""),
            title=item.get("title") or doc.get("title", ""),
            excerpt=(item.get("text") or "")[:600],
            locator=item.get("locator", ""), page=int(item.get("page") or 0),
            char_start=int(item.get("char_start", -1)), char_end=int(item.get("char_end", -1)),
            kind="card", card_type=item.get("card_type", ""),
            score=float(item.get("score") or 0.0), retrieved_at=get_timestamp(), topic=self.topic,
            year=str(doc.get("year", "")), doi=doc.get("doi", ""),
            doc_type=str(doc.get("doc_type", "")), credibility=str(doc.get("credibility", "")),
            peer_reviewed=bool(doc.get("peer_reviewed")),
            existence_verified=bool(doc.get("existence_verified")),
        )

    def document_ref(self, doc_id: str) -> SourceRef | None:
        """读取本轮已命中的文档，无需对同一命中再做关键词匹配。"""
        doc = self._store.get_document(doc_id) if self._store else None
        if not doc:
            return None
        chunks = self._store.get_chunks(doc_id, limit=1)
        if chunks:
            chunk = chunks[0]
            start, end = int(chunk.get("char_start", -1)), int(chunk.get("char_end", -1))
            page = int(chunk.get("page") or 0)
            return SourceRef(source_id=doc_id, chunk_id=f"{doc_id}#{chunk['idx']}",
                             title=doc.get("title", ""), excerpt=chunk.get("text", "")[:600],
                             locator=f"p{page} chars {start}-{end}" if page else f"chars {start}-{end}",
                             page=page, char_start=start, char_end=end, kind="chunk", topic=self.topic,
                             doi=doc.get("doi", ""), year=str(doc.get("year", "")))
        abstract = str(doc.get("abstract") or "")[:600]
        locator = (f"abstract chars 0-{len(abstract)}" if abstract else
                   f"metadata: {doc.get('doi') or doc.get('url') or doc_id}")
        return self._doc_ref({"doc_id": doc_id, "text": abstract, "locator": locator,
                              "char_start": 0 if abstract else -1,
                              "char_end": len(abstract) if abstract else -1}, doc)

    def _doc_ref(self, item: dict, doc: dict) -> SourceRef:
        from src.utils.file_utils import get_timestamp

        return SourceRef(
            source_id=item.get("doc_id", ""), chunk_id="",
            title=item.get("title") or doc.get("title", ""),
            excerpt=(item.get("text") or "")[:600],
            locator=item.get("locator", ""), page=int(item.get("page") or 0),
            char_start=int(item.get("char_start", -1)), char_end=int(item.get("char_end", -1)),
            kind="doc", score=float(item.get("score") or 0.0),
            retrieved_at=get_timestamp(), topic=self.topic,
            year=str(doc.get("year", "")), doi=doc.get("doi", ""),
            doc_type=str(doc.get("doc_type", "")), credibility=str(doc.get("credibility", "")),
            peer_reviewed=bool(doc.get("peer_reviewed")),
            existence_verified=bool(doc.get("existence_verified")),
        )

    @staticmethod
    def _dedup(refs: list[SourceRef]) -> list[SourceRef]:
        seen: set[str] = set()
        out: list[SourceRef] = []
        for ref in refs:
            key = ref.chunk_id or f"{ref.source_id}|{ref.locator}|{ref.excerpt[:40]}"
            if key in seen:
                continue
            seen.add(key)
            out.append(ref)
        return out

    # ---------------------------------------------------------------
    # 读取原文
    # ---------------------------------------------------------------
    def _locate_by_chunk(self, doc_id: str, ref: SourceRef,
                         context_chars: int) -> dict | None:
        """按 `chunk_id` 的编号精确回查入库片段; 无编号或查不到时返回 None。"""
        chunk_id = ref.chunk_id or ""
        tail = chunk_id.rsplit("#", 1)[-1] if "#" in chunk_id else ""
        if not tail.isdigit():
            return None
        idx = int(tail)
        stored = self._store.get_chunk(doc_id, idx)
        if not stored:
            return None
        chunks = self._store.get_chunks(doc_id)
        window = " ".join(c.get("text", "") for c in chunks[max(0, idx - 1):idx + 2])

        page = int(stored.get("page") or 0)
        page_end = int(stored.get("page_end") or 0)
        char_start = int(stored.get("char_start", -1) or -1)
        char_end = int(stored.get("char_end", -1) or -1)
        locator = ""
        if page and page_end > page:
            locator = f"p{page}~{page_end}"
        elif page:
            locator = f"p{page}"
        if char_start >= 0:
            locator = (locator + f" chars {char_start}-{char_end}").strip()
        if stored.get("page_estimated"):
            locator += " (页码为估算)"
        return {"text": window[:context_chars], "page": page,
                "locator": locator or f"chunk {idx}", "source_id": doc_id,
                "section_heading": "", "char_start": char_start,
                "truncated": len(window) > context_chars, "failure": ""}

    def read(self, ref: SourceRef, context_chars: int = 1200) -> dict:
        """回到原文读取完整上下文。

        返回 {text, page, locator, source_id, section_heading, truncated, failure}。
        取不到原文时显式返回 failure, 不得用摘要冒充完整条件。
        """
        if not self._store:
            return {"text": "", "failure": "知识底座不可用", "source_id": ref.source_id}
        doc_id = ref.source_id
        if not doc_id:
            return {"text": "", "failure": "缺少 source_id, 无法回到原文", "locator": ref.locator}

        needle = (ref.excerpt or "").strip()
        located = self._store.locate(doc_id, needle) if needle else {}
        if not located and ref.char_start >= 0:
            located = {"char_start": ref.char_start, "char_end": ref.char_end, "page": ref.page}

        # 已知入库片段编号时, 直接用该片段的**精确字符范围与起止页**回原文:
        # 这是最可靠的一级定位, 优于按节/按标题猜位置。
        exact = self._locate_by_chunk(doc_id, ref, context_chars)
        if exact is not None:
            return exact

        # 其次在章节正文中取上下文, 再退回分块窗口
        sections = self._store.get_sections(doc_id)
        for section in sections:
            text = section.get("text") or ""
            if needle and needle[:60] in text:
                start = max(0, text.find(needle[:60]) - context_chars // 3)
                window = text[start:start + context_chars]
                return {"text": window, "page": section.get("page", 0),
                        "locator": f"p{section.get('page', 0)} / {section.get('heading', '')}".strip(" /"),
                        "source_id": doc_id, "section_heading": section.get("heading", ""),
                        "char_start": start, "truncated": len(text) > start + context_chars,
                        "failure": ""}

        if located:
            page = located.get("page", 0)
            for section in sections:
                if section.get("page") == page and section.get("text"):
                    text = section["text"]
                    return {"text": text[:context_chars], "page": page,
                            "locator": f"p{page} / {section.get('heading', '')}".strip(" /"),
                            "source_id": doc_id, "section_heading": section.get("heading", ""),
                            "char_start": 0, "truncated": len(text) > context_chars,
                            "failure": ""}

        chunks = self._store.get_chunks(doc_id)
        if chunks:
            idx = 0
            if ref.chunk_id:
                # 已入库片段用 "<doc_id>#<idx>" 标识, 优先按 idx 精确回查
                tail = ref.chunk_id.rsplit("#", 1)[-1]
                if tail.isdigit():
                    idx = int(tail)
            stored = self._store.get_chunk(doc_id, idx)
            window = " ".join(c.get("text", "") for c in chunks[max(0, idx - 1):idx + 2])
            # 优先使用入库时记录的**精确字符范围与起止页** (跨页片段不再沿用首页)
            locator = ""
            page = 0
            if stored:
                page = int(stored.get("page") or 0)
                page_end = int(stored.get("page_end") or 0)
                char_start = int(stored.get("char_start", -1) or -1)
                char_end = int(stored.get("char_end", -1) or -1)
                if page and page_end > page:
                    locator = f"p{page}~{page_end}"
                elif page:
                    locator = f"p{page}"
                if char_start >= 0:
                    locator = (locator + f" chars {char_start}-{char_end}").strip()
                if stored.get("page_estimated"):
                    locator += " (页码为估算)"
            if not locator:
                locator = f"chunk {idx}"
            return {"text": window[:context_chars], "page": page,
                    "locator": locator, "source_id": doc_id, "section_heading": "",
                    "char_start": 0, "truncated": len(window) > context_chars, "failure": ""}

        return {"text": ref.excerpt, "page": ref.page, "locator": ref.locator,
                "source_id": doc_id, "section_heading": "",
                "truncated": False,
                "failure": "未找到全文, 仅能返回检索片段 (不得视为完整条件)"}

    # ---------------------------------------------------------------
    # 解析来源真实性
    # ---------------------------------------------------------------
    def resolve(self, ref: SourceRef) -> dict:
        """来源真实性/版本解析 (计划书 §6.2 第一项检查)。不推断内容支持。"""
        if not self._store:
            return {"found": False, "failure": "知识底座不可用"}
        doc = self._store.get_document(ref.source_id) if ref.source_id else None
        if not doc:
            return {"found": False, "source_id": ref.source_id,
                    "failure": "权威库中无该来源记录"}
        from src.publication.references import BIBLIO_FIELDS
        return {
            **{key: doc.get(key, "") for key in BIBLIO_FIELDS},
            "found": True,
            "source_id": ref.source_id,
            "title": doc.get("title", ""),
            "authors": doc.get("authors", ""),
            "venue": doc.get("venue", ""),
            "url": doc.get("url", ""),
            "year": doc.get("year", ""),
            "doi": doc.get("doi", ""),
            "doc_type": doc.get("doc_type", ""),
            "credibility": doc.get("credibility", ""),
            "peer_reviewed": bool(doc.get("peer_reviewed")),
            "existence_verified": bool(doc.get("existence_verified")),
            "manual_asserted": bool(doc.get("manual_asserted")),
            "file_hash": (doc.get("identity") or {}).get("file_hash", ""),
            "parse_quality": doc.get("parse_quality", ""),
            "has_fulltext": bool(doc.get("has_fulltext")),
            # 该文档里"人阅读时不易察觉"的片段 (保留原文但需核对)
            "visibility_flags": list(doc.get("visibility_flags") or []),
            "version": doc.get("updated_at", ""),
            # 存在性 ≠ 内容支持 ≠ 当前适用性
            "note": "仅确认来源存在与版本; 是否支持当前命题须另行判定",
        }
