from __future__ import annotations

"""KB → 研究引擎的桥接: 把主题文献底座变成可调用的证据与新颖性对照。

计划书 §2/P0 修复点 (A01):
- 旧实现直接设置 `content_supports=True`, 并把高可信来源映射为 `converging` ——
  命中一篇材料可能被误当作"已支持命题"或"多源汇合证据"。
- 现在: 召回只产生**候选证据** (`support=insufficient`), 支持/反对关系由
  独立的判定步骤 (规则或带条件的语义抽取) 写入 `EvidenceLink`;
  `converging` 只在存在 ≥2 个**独立**支持来源时由 `grade_evidence` 给出。
"""

from collections.abc import Callable

from src.kb.schema import CardType, DocType
from src.kb.service import KnowledgeService, RetrievalRequest, SourceRef
from src.research.evidence import assess_support, detect_contradictions
from src.research.query_planner import plan_queries
from src.research.schemas import (
    Claim,
    Credibility,
    NoveltyComparisonRow,
    ProblemContract,
    ReferenceStatus,
    RetrievalCoverage,
    SourceEvidence,
    SourceKind,
    SourcePolicy,
    StudyDesign,
    SupportKindOfEvidence,
    stable_id,
    utcnow,
)

# 外部检索默认覆盖的库 (与 tools/search_tools.search_all_sources 一致)
_EXTERNAL_ENGINES = ("arXiv", "Semantic Scholar", "OpenAlex")

_DOC_TYPE_TO_SOURCE = {
    DocType.journal: SourceKind.journal,
    DocType.conference: SourceKind.conference,
    DocType.thesis: SourceKind.other,
    DocType.preprint: SourceKind.preprint,
    DocType.report: SourceKind.report,
    DocType.standard: SourceKind.standard,
    DocType.patent: SourceKind.patent,
    DocType.book: SourceKind.textbook,
    DocType.other: SourceKind.other,
}

# 与具体研究命题无关的卡片类型: 只作背景, 不作为支持证据
_BACKGROUND_CARDS = {CardType.method, CardType.dataset, CardType.other}


def _credibility(value: str) -> Credibility:
    try:
        return Credibility(value)
    except ValueError:
        return Credibility.low


def ref_to_evidence(service: KnowledgeService, ref: SourceRef,
                    claim: Claim | None = None) -> SourceEvidence:
    """把可定位的检索引用转成候选证据 (不改写支持关系)。"""
    resolved = service.resolve(ref) if service.available else {}
    from src.publication.references import BIBLIO_FIELDS
    doc_type = str(resolved.get("doc_type") or ref.doc_type or "other")
    try:
        source_kind = _DOC_TYPE_TO_SOURCE.get(DocType(doc_type), SourceKind.other)
    except ValueError:
        source_kind = SourceKind.other

    # 回到原文取完整上下文 (计划书 §6.1-4): 摘要不能替代完整条件
    read = service.read(ref) if service.available else {}
    excerpt = (read.get("text") or ref.excerpt or "")[:2000]
    locator = read.get("locator") or ref.locator

    item = SourceEvidence(
        **{key: resolved.get(key, "") for key in BIBLIO_FIELDS},
        # 确定性 ID: 同一来源 + 同一片段永远得到同一证据对象
        id=stable_id("ev", ref.source_id or ref.title, ref.chunk_id, (ref.excerpt or "")[:200]),
        literature_id=ref.source_id or ref.doi or ref.title,
        title=ref.title or str(resolved.get("title", "")),
        authors=str(resolved.get("authors", "")),
        venue=str(resolved.get("venue", "")),
        doi=ref.doi or str(resolved.get("doi", "")),
        url=str(resolved.get("url", "")),
        content_level=("fulltext" if resolved.get("has_fulltext") and read.get("text") and not read.get("failure") else
                       "abstract" if excerpt else "metadata"),
        source_set_id=service.topic,
        source_kind=source_kind,
        credibility=_credibility(str(resolved.get("credibility") or ref.credibility or "low")),
        peer_reviewed=bool(resolved.get("peer_reviewed", ref.peer_reviewed)),
        existence_verified=bool(resolved.get("existence_verified", ref.existence_verified)),
        study_design=StudyDesign.observational,
        year=str(resolved.get("year") or ref.year or ""),
        excerpt=excerpt,
        file_hash=str(resolved.get("file_hash", "")),
        # 定位与版本 (计划书 §6.1): 检索结果必须可回到原文
        source_id=ref.source_id,
        chunk_id=ref.chunk_id,
        location=locator,
        page=int(read.get("page") or ref.page or 0),
        char_start=int(read.get("char_start", ref.char_start if ref.char_start is not None else -1)),
        char_end=int(ref.char_end if ref.char_end is not None else -1),
        original_record_hash=_record_key(ref, resolved),
        retrieved_at=ref.retrieved_at,
        # 三件事分开: 召回 ≠ 支持
        support=SupportKindOfEvidence.background if ref.card_type in {c.value for c in _BACKGROUND_CARDS}
        else SupportKindOfEvidence.insufficient,
        support_reason="知识底座召回候选材料, 支持关系尚未判定",
        reviewer="rule",
        reference_status=ReferenceStatus.unchecked,
        # P1-4: 该来源含视觉异常片段时, 证据必须带上"需核对"标记
        notes=("该来源含视觉异常片段 (需核对, 不作为强证据): " + "; ".join(
            str(f.get("locator", "")) for f in (resolved.get("visibility_flags") or [])[:3])
            if resolved.get("visibility_flags") else ""),
    )
    if item.support == SupportKindOfEvidence.background:
        item.support_reason = "对象型卡片 (方法/数据/其他), 只作背景"
    if item.page:
        item.location = item.location or f"p{item.page}"
    if read.get("failure"):
        item.notes = (item.notes + " " + str(read["failure"])).strip()
    if item.content_level == "abstract" and not item.location.lower().startswith("abstract"):
        item.location = "abstract " + item.location
    return item


def _record_key(ref: SourceRef, resolved: dict) -> str:
    """同一原始记录的键: 保证多篇转引同一来源不被算成独立支持。"""
    doi = (ref.doi or str(resolved.get("doi", ""))).lower()
    if doi:
        return f"doi:{doi}"
    file_hash = str(resolved.get("file_hash", ""))
    if file_hash:
        return f"hash:{file_hash}"
    if ref.source_id:
        return f"doc:{ref.source_id}"
    return f"title:{(ref.title or '').strip().lower()}"


def build_retrieval_request(claim: Claim, gap_type: str = "",
                            category: str = "", max_results: int = 5) -> RetrievalRequest:
    """按命题构造定向检索请求 (计划书 §6.4): 检索请求绑定缺口而不是裸字符串。"""
    query = claim.statement or ""
    if claim.study.treatment:
        query = f"{claim.study.treatment} {claim.study.outcome or claim.statement}"
    card_types: list[str] = []
    if category == "theorem":
        card_types = [CardType.theorem.value, CardType.lemma.value,
                      CardType.proposition.value, CardType.corollary.value]
    elif category == "definition":
        card_types = [CardType.definition.value]
    elif category == "assumption":
        card_types = [CardType.assumption.value]
    elif category == "counterexample":
        card_types = [CardType.result.value, CardType.limitation.value]
    conditions = []
    for var, domain in (claim.variable_domains or {}).items():
        conditions.append(f"{var} ∈ {domain}")
    return RetrievalRequest(
        query=query, claim_id=claim.id, gap_type=gap_type, evidence_category=category,
        variables=list(claim.variables or []), conditions=conditions,
        card_types=card_types, max_results=max_results,
        # 研究循环里明确不要人工资料时才收紧范围; 默认不限语言/年份
        stop_condition="取得可定位原文并显式判定支持关系, 或达到工具/动作预算",
    )


def retrieve_evidence(service: KnowledgeService, claim: Claim, gap_type: str = "",
                      category: str = "", k: int = 5,
                      judge_support: bool = False) -> list[SourceEvidence]:
    """按缺口定向检索并返回候选证据 (可选做规则层支持判定)。"""
    request = build_retrieval_request(claim, gap_type=gap_type, category=category, max_results=k)
    outcome = service.search(request)
    items = [ref_to_evidence(service, ref, claim) for ref in outcome.refs]
    if judge_support:
        items = [assess_support(claim, item) for item in items]
    return detect_contradictions(items)


def retrieve_evidence_detailed(service: KnowledgeService, claim: Claim, gap_type: str = "",
                               category: str = "", k: int = 5,
                               judge_support: bool = False) -> tuple[list[SourceEvidence], dict]:
    """返回 (候选证据, 检索元信息)。检索失败与"无相关文献"分开记录。"""
    request = build_retrieval_request(claim, gap_type=gap_type, category=category, max_results=k)
    outcome = service.search(request)
    items = [ref_to_evidence(service, ref, claim) for ref in outcome.refs]
    if judge_support:
        items = [assess_support(claim, item) for item in items]
    items = detect_contradictions(items)
    meta = {
        "query": request.query,
        "request": request.describe(),
        "refs": len(outcome.refs),
        "failures": list(outcome.failures),
        "searched": outcome.searched,
        "covered_sources": list(outcome.covered),
        # 可观测性 (计划书 §9.3): 哪条召回通道真的跑了、多少条被范围挡掉
        "recall_channels": list(outcome.recall_channels),
        "channel_counts": dict(outcome.channel_counts),
        "dropped_out_of_scope": outcome.dropped_out_of_scope,
    }
    return items, meta


def _paper_key(paper: dict) -> str:
    """外部检索命中的同一原始记录键 (与 KB 侧的记录键保持同一口径)。"""
    from src.kb.identity import build_identity, normalize_doi

    doi = normalize_doi(paper.get("doi") or "")
    if doi:
        return f"doi:{doi}"
    identity = build_identity(paper)
    if identity.title_norm:
        return (f"title:{identity.title_norm}|{identity.first_author_norm}|"
                f"{identity.year}")
    return ""


def _default_search_fn() -> Callable[[str, int], list[dict]] | None:
    try:
        from src.tools.search_tools import search_all_sources

        return getattr(search_all_sources, "func", search_all_sources)
    except Exception:  # noqa: BLE001 - 检索能力缺失时如实记失败, 不静默降级
        return None


def _embedding_enabled() -> bool:
    try:
        from src.rag.vector_store import embedding_available

        return bool(embedding_available())
    except Exception:
        return False


def _paper_evidence(paper: dict, *, topic: str = "") -> SourceEvidence:
    """保留真正返回的摘要和书目信息，不把元数据当作已读全文。"""
    from src.publication.references import BIBLIO_FIELDS
    excerpt = str(paper.get("abstract") or "")[:2000]
    source_id = stable_id("lit", _paper_key(paper))
    return SourceEvidence(
        id=source_id, source_id=source_id, literature_id=_paper_key(paper),
        title=str(paper.get("title") or ""), authors=str(paper.get("authors") or ""),
        year=str(paper.get("year") or ""), venue=str(paper.get("venue") or ""),
        doi=str(paper.get("doi") or ""), url=str(paper.get("url") or ""),
        **{key: paper.get(key, "") for key in BIBLIO_FIELDS},
        excerpt=excerpt, location=(f"abstract chars 0-{len(excerpt)}" if excerpt else
                                  f"metadata: {paper.get('doi') or paper.get('url') or source_id}"),
        char_start=0 if excerpt else -1, char_end=len(excerpt) if excerpt else -1,
        content_level="abstract" if excerpt else "metadata", source_set_id=topic,
        retrieved_at=utcnow(), retrieval_queries=list(paper.get("_retrieval_queries") or []),
        support_reason="外部检索候选材料，支持关系尚未判定",
        notes="未入库；只读到摘要/书目信息，不能核对完整定理条件")


def _harvest_external(queries: list[str], policy: SourcePolicy, coverage: RetrievalCoverage,
                      *, topic: str, search_fn, per_query: int, ingest: bool) -> list[SourceEvidence]:
    """按规划的多条检索式自主检索, 命中入库, 并把覆盖情况写进 coverage。"""
    import os

    # 显式离线运行不触网；测试仍可注入 search_fn 验证自主检索逻辑。
    if search_fn is None and os.getenv("THEORY_LLM", "").strip().lower() in {"0", "false", "off"}:
        coverage.failures.append("显式离线运行: 未执行外部文献检索")
        coverage.uncovered.append("外部文献未核查")
        return []
    fn = search_fn if search_fn is not None else _default_search_fn()
    if fn is None:
        coverage.failures.append("外部检索能力不可用 (工具未安装或配置缺失)")
        return []
    coverage.engines = list(_EXTERNAL_ENGINES)
    fetched: dict[str, dict] = {}
    from src.kb.store import KBStore, default_db_path

    cached_store = KBStore(topic, create_if_missing=False) if default_db_path(topic).exists() else None
    successful_queries: dict[str, int] = {}
    for query in queries:
        if cached_store is not None and cached_store.query_recently_searched(query) \
                and cached_store.search_keyword(query, limit=1):
            coverage.executed = True
            coverage.uncovered.append(f"查询「{query}」复用同领域已入库结果；7 天后可刷新外检")
            continue
        try:
            papers = fn(query, per_query) or []
        except Exception as e:  # noqa: BLE001 - 单条检索式失败不影响其余
            coverage.failures.append(f"检索式「{query}」失败: {type(e).__name__}: {e}")
            continue
        # 只要有一条检索式跑完, 就算"检索执行过": 之后的零命中是"无命中"而不是"失败"
        coverage.executed = True
        from src.kb.publication import relevance_score
        # The semantic selector returns rejected rows for audit; they must never
        # proceed to PDF download or ingestion.
        usable = [paper for paper in papers if isinstance(paper, dict)
                  and paper.get("title") and not paper.get("_semantic_rejected")]
        successful_queries[query] = len(usable)
        coverage.failures.extend(str(paper.get("error")) for paper in papers
                                 if isinstance(paper, dict) and paper.get("error"))
        coverage.hits += len(usable)
        ranked = sorted(usable, key=lambda paper: relevance_score(paper, query), reverse=True)
        papers = [paper for paper in ranked if relevance_score(paper, query) >= .1][:per_query]
        if len(papers) < len(usable):
            coverage.uncovered.append(f"查询 {query}: {len(usable) - len(papers)} 条低相关候选未纳入研究材料")
        for paper in papers:
            key = _paper_key(paper)
            if key:
                # Search-result metadata is untrusted: only our PDF cache/downloader
                # may introduce a local file path for full-text parsing.
                metadata = {field: value for field, value in paper.items()
                            if field not in {"pdf_path", "_semantic_rejected"}}
                record = fetched.setdefault(key, {**metadata, "_retrieval_queries": []})
                if query not in record["_retrieval_queries"]:
                    record["_retrieval_queries"].append(query)
    if not fetched:
        if cached_store is not None:
            cached_store.close()
        if successful_queries:
            coverage.uncovered.append("本次外检无新增可用记录；继续检查已入库资料")
        return []
    if not ingest:
        if cached_store is not None:
            cached_store.close()
        coverage.uncovered.append(f"{len(fetched)} 条命中未入库 (调用方关闭入库)，保留摘要候选")
        return [_paper_evidence(paper) for paper in fetched.values()]
    # 先取全文并落盘, 再入库 —— 顺序不能反: 入库时 `pdf_path` 必须已存在, 否则只会写入
    # 元数据 (无 sections/chunks/定理卡片), 后续拿不到可定位的原文引文。
    # 这一步与综述模式的 `pdf_ingestion` 节点**复用同一套下载工具**
    # (`download_pdfs_for_papers`), 只按本轮规模上限控制。
    from src.kb.publication import verify_publication
    publications = [verify_publication(paper) for paper in fetched.values()]
    for paper in publications:
        if paper.get("publication_status") != "published":
            coverage.uncovered.append(f"未确认正式出版: {paper.get('title')}；{paper.get('publication_note')}")
    papers = _download_fulltext_for(publications, topic=topic, per_query=per_query,
                                   coverage=coverage)
    from src.kb.ingest import ingest_machine

    from src.kb.store import KBStore

    store = None
    try:
        store = KBStore(topic)
        report = ingest_machine(topic, papers, embed=_embedding_enabled(), store=store)
        for query, count in successful_queries.items():
            store.mark_query_searched(query, count)
        coverage.ingested += len(report["ingested"])
        coverage.duplicates += len(report["merged"])
        coverage.failures.extend(str(e) for e in report.get("errors", []))
        ids = {row["title"]: row["doc_id"] for row in [*report["ingested"], *report["merged"]]}
        service = KnowledgeService(topic, store=store)
        items = []
        for paper in papers:
            ref = service.document_ref(ids.get(paper.get("title"), ""))
            item = ref_to_evidence(service, ref) if ref else _paper_evidence(paper)
            item.retrieval_queries = list(paper.get("_retrieval_queries") or [])
            items.append(item)
        return items
    except Exception as e:
        coverage.failures.append(f"命中资料入库失败: {type(e).__name__}: {e}")
        coverage.uncovered.append("已保留外部命中的摘要/元数据，入库与全文核对尚未完成")
        return [_paper_evidence(paper) for paper in papers]
    finally:
        if cached_store is not None:
            cached_store.close()
        if store is not None:
            store.close()


def _download_fulltext_for(papers, *, topic: str, per_query: int,
                           coverage: RetrievalCoverage) -> list[dict]:
    """给命中论文下载全文 PDF 并回填 `pdf_path` (失败不阻断入库)。

    为什么值得做: 只有元数据时, 引用守门拿不到可核对的引文片段
    (`quote_is_locatable` 需要原文), 于是"检索到了却一条也引不了"; 有全文后入库会写出
    sections/chunks 与**定理卡片** (`kb/cards.py`, 定位形如 `p3 / 定理 2`),
    "引用某个具名定理"才有真正可核对的出处。

    规模控制: 上限取 `per_query` 与 `PDF_DOWNLOAD_LIMIT` 的较小值 —— 下载是逐篇限流的
    重活, 不能因为一次检索命中很多就无限下载。
    """
    candidates = list(papers)
    if not candidates:
        return candidates
    try:
        from src.config import PDF_DOWNLOAD_LIMIT
        from src.tools.pdf_fetcher import download_pdfs_for_papers
    except Exception as e:  # noqa: BLE001 - 工具不可用时退回元数据入库
        coverage.failures.append(f"全文下载工具不可用: {type(e).__name__}: {e}")
        return candidates

    limit = max(1, min(int(per_query or 1), int(PDF_DOWNLOAD_LIMIT)))
    from src.kb.identity import build_identity, candidate_keys
    from src.kb.store import KBStore, default_db_path

    existing_store = KBStore(topic, create_if_missing=False) if default_db_path(topic).exists() else None
    missing: list[dict] = []
    for paper in candidates:
        if existing_store is not None:
            pdf = existing_store.cached_pdf_for(candidate_keys(build_identity(paper)))
            if pdf:
                paper["pdf_path"] = pdf
                continue
        missing.append(paper)
    if existing_store is not None:
        existing_store.close()
    try:
        with_pdf = download_pdfs_for_papers(missing[:limit], limit=limit) if missing else []
    except Exception as e:  # noqa: BLE001 - 下载失败不得中断研究
        coverage.failures.append(f"全文下载失败: {type(e).__name__}: {e}")
        return candidates

    downloaded = [p for p in candidates if p.get("pdf_path")]
    coverage.fulltext_available = len(downloaded)
    if not downloaded:
        coverage.uncovered.append(
            f"命中 {len(candidates)} 条但未取得全文 (无开放获取链接或下载失败), "
            "相关结论只能按摘要核对")
        return candidates
    # 用**带 pdf_path 的版本**替换原记录, 否则入库时拿不到全文
    by_key = {_paper_key(p): p for p in with_pdf}
    return [by_key.get(_paper_key(p), p) for p in candidates]


def _open_service(topic: str) -> KnowledgeService | None:
    """打开已存在的资料库 (不存在就返回 None, 不建空库)。"""
    if not topic:
        return None
    try:
        service = KnowledgeService(topic, create_if_missing=False)
    except Exception:  # noqa: BLE001 - 装配失败按"无可用资料"处理
        return None
    return service if service.available else None


def _claim_goal(claim: Claim | None) -> str:
    """没有问题契约时的检索目标 (沿用"处理+结果"的旧口径)。"""
    if claim is None:
        return ""
    if claim.study.treatment:
        return f"{claim.study.treatment} {claim.study.outcome or claim.statement}"
    return claim.statement or ""


def gather_sources(
    service: KnowledgeService | None,
    contract: ProblemContract | None,
    *,
    topic: str = "",
    policy: SourcePolicy = SourcePolicy.user_kb,
    claim: Claim | None = None,
    gap_type: str = "",
    k: int = 6,
    max_queries: int = 4,
    per_query: int = 4,
    terminology: dict[str, list[str]] | None = None,
    extra_queries: list[str] | None = None,
    search_fn: Callable[[str, int], list[dict]] | None = None,
    ingest: bool = True,
) -> tuple[list[SourceEvidence], RetrievalCoverage]:
    """按授权策略采集研究资料, 并留下可审查的覆盖记录 (P0-1 场景 A/B)。

    - `user_kb`: 只在用户授权的资料库内检索 (不越权外搜);
    - `autonomous`: 自主设计检索式外搜并入库 (允许没有预建资料库);
    - `both`: 先外搜入库, 再连同用户资料库一起检索, 按 DOI/hash/标题去重合并。

    返回值中的证据只是**候选材料**: 支持/反对关系仍须独立判定。
    """
    coverage = RetrievalCoverage(policy=policy, started_at=utcnow())
    query_limit = max(int(max_queries), 1)
    # 检索式只规划一次: 外部检索与资料库检索用同一组式子, 覆盖记录才能对上
    planned = plan_queries(contract, goal=_claim_goal(claim), terminology=terminology,
                           limit=query_limit)
    # 检索智能体通过工具循环提出的查询优先进入同一条入库/覆盖路径；
    # 不另起一套"模型搜到了但结果没登记"的隐形检索。
    from src.research.query_planner import research_question_text
    selected = [research_question_text(str(q))[:300] for q in (extra_queries or []) if str(q).strip()]
    queries = list(dict.fromkeys([*selected, *(q.text for q in planned)]))[:query_limit]
    coverage.queries = list(queries)
    external_items: list[SourceEvidence] = []
    if policy in (SourcePolicy.autonomous, SourcePolicy.both):
        topic = topic or stable_id("research-kb", _claim_goal(claim), contract.goal if contract else "")
        external_items = _harvest_external(queries, policy, coverage, topic=topic, search_fn=search_fn,
                                          per_query=per_query, ingest=ingest)

    refs: list[SourceRef] = []
    # 自主检索刚入库的资料, 与用户资料库走同一条检索路径 (这就是"合并"的含义)
    owned_service = service is None
    service = service if service is not None else _open_service(topic)
    # autonomous may reuse its own research/shared cache, never an unrelated user KB.
    if policy is SourcePolicy.autonomous and not topic.startswith(("shared-", "research-")):
        service_for_search = None
    else:
        service_for_search = service
    if service_for_search is not None:
        from src.rag.relevance_filter import is_off_domain, mentions, resolve_domain

        field_terms = resolve_domain(_claim_goal(claim) or
                                     (contract.goal if contract else topic))
        for query in queries:
            outcome = service.search(RetrievalRequest(
                query=query, claim_id=claim.id if claim else "", gap_type=gap_type,
                max_results=k))
            coverage.failures.extend(str(f) for f in outcome.failures)
            if outcome.searched:
                coverage.executed = True
            else:
                coverage.uncovered.append(f"资料库检索未执行: {query}")
            if field_terms.is_empty():
                refs.extend(outcome.refs)
            else:
                kept = [ref for ref in outcome.refs
                        if mentions(field_terms.in_domain,
                                    f"{ref.title} {ref.excerpt}")
                        and not is_off_domain(ref.title, field_terms)]
                if len(kept) < len(outcome.refs):
                    coverage.uncovered.append(
                        f"查询「{query}」排除 {len(outcome.refs) - len(kept)} 条跨领域已入库资料")
                refs.extend(kept)
        if not refs and not external_items:
            coverage.uncovered.append("在所授权资料内没有命中 (不等于不存在)")
    elif policy is SourcePolicy.autonomous and not external_items:
        coverage.uncovered.append("没有可用的用户资料库, 本次只用自主检索结果")
    elif not external_items:
        uncovered_note = "没有可用的用户资料库 (未绑定或为空)"
        if uncovered_note not in coverage.uncovered:
            coverage.uncovered.append(uncovered_note)

    merged: list[SourceRef] = []
    seen: set[str] = set()
    for ref in refs:
        resolved = service.resolve(ref) if service is not None and service.available else {}
        key = _record_key(ref, resolved)
        if key in seen:
            coverage.duplicates += 1
            continue
        seen.add(key)
        merged.append(ref)
        if resolved.get("has_fulltext"):
            coverage.fulltext_available += 1
        else:
            coverage.abstract_only += 1

    items = [ref_to_evidence(service, ref, claim) for ref in merged] if service is not None else []
    items.extend(external_items)
    unique: dict[str, SourceEvidence] = {}
    for item in items:
        key = f"doi:{item.doi.lower()}" if item.doi else item.source_id or item.literature_id
        previous = unique.get(key)
        if previous is None or (previous.content_level != "fulltext" and item.content_level == "fulltext"):
            if previous is not None:
                item.retrieval_queries = list(dict.fromkeys([*previous.retrieval_queries, *item.retrieval_queries]))
            unique[key] = item
        elif item.retrieval_queries:
            previous.retrieval_queries = list(dict.fromkeys([*previous.retrieval_queries, *item.retrieval_queries]))
    items = list(unique.values())
    coverage.fulltext_available = sum(item.content_level == "fulltext" for item in items)
    coverage.abstract_only = sum(item.content_level == "abstract" for item in items)
    coverage.scope_note = (
        f"策略 {policy.value}: 检索式 {len(queries)} 条, "
        f"外部命中 {coverage.hits} 条 (入库 {coverage.ingested}, 重复 {coverage.duplicates}), "
        f"可用来源去重后 {len(items)} 条 (全文 {coverage.fulltext_available}, "
        f"仅摘要 {coverage.abstract_only})")
    coverage.finished_at = utcnow()
    if owned_service and service is not None and service.store is not None:
        service.store.close()
    return detect_contradictions(items), coverage


def novelty_lookup(service: KnowledgeService, k: int = 5) -> Callable[[Claim], list[NoveltyComparisonRow]]:
    """构造新颖性对照查询: 返回可比对的结构化行, 但**不**自行判定等价。"""

    def _lookup(claim: Claim) -> list[NoveltyComparisonRow]:
        query = claim.statement
        if claim.study.treatment:
            query = f"{claim.study.treatment} {claim.study.outcome or claim.statement}"
        outcome = service.search(RetrievalRequest(query=query, claim_id=claim.id,
                                                  evidence_category="prior_result",
                                                  max_results=k))
        rows: list[NoveltyComparisonRow] = []
        for ref in outcome.refs:
            if ref.kind == "card":
                rows.append(NoveltyComparisonRow(
                    result=ref.title or ref.locator or ref.card_type,
                    premises="(需人工比对)",
                    conclusion=(ref.excerpt or "")[:300],
                    applicability=ref.year,
                    method=ref.card_type,
                    difference="",   # 差异未分析 → 保持待比较
                ))
            else:
                rows.append(NoveltyComparisonRow(
                    result=ref.title,
                    premises="(需人工比对)",
                    conclusion=(ref.excerpt or "")[:300],
                    applicability=ref.year,
                    difference="",
                ))
        return rows

    # 供 NoveltyRecord 记录检索边界 (本地知识底座)
    _lookup.covered_sources = [service.topic or "local-kb"]  # type: ignore[attr-defined]
    _lookup.kind = "local_kb"  # type: ignore[attr-defined]
    return _lookup
