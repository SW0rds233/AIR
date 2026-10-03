from __future__ import annotations

"""阶段 2 (方案 v2 M3): 为出版层获取**可引用**的证据。

它在链路里的位置
----------------
研究循环后半段可能已经产生 `snapshot.evidence` (研究过程中召回并判定过的证据)。
本模块负责的是**出版前的补齐**: 出版层需要"相关工作比较"和参考文献表, 当快照里的
证据不足以支撑这两节时, 按资料授权策略再检索一轮, 并把结果**逐条判定支持关系**。

三条硬约束
----------
1. **命中 != 支持**: 检索命中只证明"文献存在", 不证明"内容支持该命题"。因此每条
   命中都先建为 `insufficient`, 再经 `assess_support` 得到候选关系; 没有可定位引文时
   **最多只到 `partially_supports`** (`quote_is_locatable`), 绝不能靠标题相似升级为支持。
2. **离线不联网**: `THEORY_LLM=0` 或检索源不可用时, 如实记"未执行检索"并保持
   参考文献表为空 —— 方案 v2 明确禁止用伪造引用把版面填满。
3. **缺记录 != 无命中**: 覆盖记录区分"没检索"、"检索失败"与"检索过无命中"三种状态,
   因为"未检索"不能作为"无先例"的依据 (发布门槛 4)。
"""

import re
from dataclasses import dataclass, field

from src.rag import reference_list as refmod
from src.research.schemas import (
    Claim,
    ResearchSnapshot,
    RetrievalCoverage,
    SourceEvidence,
    SourcePolicy,
    SupportKindOfEvidence,
)

_THEORY_LLM_OFF = "0"

# 中文命题 vs 英文来源的**术语对照**。
# 必要性来自实测: `assess_support` 用"命题里的词是否出现在原文"判支持关系, 而中文命题
# 与英文摘要没有一个共同词 —— 真正的相关工作 (例如 "Nonexistence Certificates for Ovals
# in a Projective Plane of Order Ten") 会被判成"原文未出现命题的主题词"而丢弃。
# 这里只做**术语**对照, 不翻译句子; 命中只升级到 `background` (背景相关工作), 不声称支持。
_TERM_MAP: dict[str, tuple[str, ...]] = {
    "射影平面": ("projective plane",),
    "设计": ("design",),
    "不存在": ("nonexistence", "non-existence", "does not exist", "impossible"),
    "存在性": ("existence",),
    "必要条件": ("necessary condition",),
    "定理": ("theorem",),
    "组合": ("combinatorial", "combinatorics"),
    "区组": ("block", "block design"),
    "对称": ("symmetric", "symmetry"),
    "计数": ("counting", "count"),
    "图": ("graph",),
    "猜想": ("conjecture",),
    "分类": ("classification",),
    "构造": ("construction", "construct"),
    "有限": ("finite",),
    "阶": ("order",),
    # 记号型术语: 题面常写作 `2-设计`、`t-设计`、`(v,k,λ)` 设计。`-` 会把"设计"断开,
    # 于是术语表匹配不到, 领域词全部丢失、只剩泛意图词 —— 实测该情况让检索式退化成
    # `nonexistence` 一个词, 返回 "The Nonexistence of Character Traits"、
    # "Nonexistence theorems for traversable wormholes" 这类完全无关的论文。
    "设计存在性": ("design existence",),
    "-设计": ("block design", "combinatorial design", "BIBD"),
    "平衡不完全区组": ("balanced incomplete block design", "BIBD"),
    "射影": ("projective",),
}

# 记号型占位符 -> 术语含义: `2-设计`/`3-设计` 的编号不改变领域
_MARKER_TERMS: tuple[tuple[str, str], ...] = (
    ("-设计", "设计"),
    ("-design", "设计"),
)


def _expand_marker_terms(text: str) -> str:
    """把 `2-设计` 这类记号还原成术语表能命中的形式 (`2 设计`)。

    只做**记号**层面的还原, 不改动任何数字/语义。注意连字符要变成空格而不是删除:
    直接删会把"设计"粘到前面的数字上 (`2-设计` -> `2设计`), 术语表反而匹配不到
    `-设计` 之外的其他键。见 `_TERM_MAP` 里的实测说明。
    """
    expanded = text or ""
    for marker, term in _MARKER_TERMS:
        expanded = expanded.replace(marker, " " + term)
    return expanded
_LATIN_THEORY_MARKERS = ("projective plane", "combinatorial design", "block design",
                         "bruck", "ryser", "chowla", "design theory",
                         "nonexistence", "classification of finite")


def terminology_variants(text: str) -> list[str]:
    """从中文文本里取出**英文术语变体** (供检索规划使用)。

    必要性来自实测: OpenAlex 的 `search` 对中文关键词几乎不做语义匹配 ——
    查"射影平面 不存在性"返回的是"非平行光束对窄带滤光片光谱性能的影响"、
    "济南-淄博-泰安地区地表伽玛辐射特征"; 查"组合设计 存在性"返回土木工程的
    "路面结构组合设计"。也就是说**中文查询会被当成无关领域的关键词**。

    因此中文题在检索前必须把领域术语换成英文变体再查。这里只做**术语**替换,
    不翻译句子; 取不到变体就返回空列表 (调用方保持原查询, 不臆造英文)。
    """
    haystack = _expand_marker_terms(text or "")
    if not haystack:
        return []
    variants: list[str] = []
    for term, translations in _TERM_MAP.items():
        if term in haystack:
            for translation in translations:
                if translation not in variants:
                    variants.append(translation)
    return variants


# 判定"背景相关"所需的最少命中数。1 个泛词 (design/theorem) 会把
# "polypill design"、"fusion pilot plant design" 这类完全无关的工作拉进来 ——
# 实测过一次: 11 条"背景文献"里只有 2 条与组合设计有关。因此要求**至少 2 个**
# 不同术语命中, 或者命中一个强领域短语。
_RELATED_MIN_HITS = 2


def _background_terms(claim_text: str) -> list[str]:
    """从中文命题里提取可对照的英文学术术语 (用于判定"背景相关")。"""
    terms: list[str] = []
    for chinese, english in _TERM_MAP.items():
        if chinese in (claim_text or ""):
            terms.extend(english)
    return list(dict.fromkeys(terms))


def _relatedness(text: str, terms: list[str]) -> int:
    """主题相关度: 命中的不同术语数; 命中强领域短语时直接给 2 (视为相关)。"""
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _LATIN_THEORY_MARKERS):
        return max(2, _RELATED_MIN_HITS)
    hits = {term for term in terms if term and term in lowered}
    return len(hits)


def _is_topically_related(text: str, terms: list[str]) -> bool:
    return _relatedness(text, terms) >= _RELATED_MIN_HITS


@dataclass
class PublicationEvidence:
    """出版层的一轮检索结果。"""

    references: refmod.ReferenceList = field(default_factory=refmod.ReferenceList)
    coverage: RetrievalCoverage = field(default_factory=RetrievalCoverage)
    notes: list[str] = field(default_factory=list)
    executed: bool = False
    # 逐条可引用的证据 (命题 → 证据)。**必须随结果一起传出去**: 这些证据不在冻结
    # 快照里, 而正文的"与已有工作比较"要靠它渲染。此前只传了参考文献表, 于是出现
    # "参考文献 11 条、第 4 节却写'未发现可比较的工作'"的自相矛盾 (实测)。
    evidence: list = field(default_factory=list)

    def evidence_for(self, claim_id: str) -> list:
        return [item for item in self.evidence
                if not claim_id or item.claim_id == claim_id]


def should_search(snapshot: ResearchSnapshot, engine, policy: SourcePolicy) -> tuple[bool, str]:
    """是否要为出版层再检索一轮。返回 `(是否需要, 原因)`。

    只在两种情况下不检索, 且原因必须可解释:
    - 离线模式 (`THEORY_LLM=0`) 不联网;
    - 研究循环已有关键证据, 重复检索只增加成本而不改判定。

    **不再因为"没有本地资料库"而放弃检索**: 外部检索 (arXiv/OpenAlex) 是自主检索的
    正经来源, 而"没有参考文献"正是现场反馈的问题 (论文只有结论没有引用)。
    真正决定能不能联网的是**资料授权策略**: `user_kb` 只用用户资料库 (空就如实说明),
    `autonomous` / `both` 允许外部检索 —— 这是用户对"能不能联网"的显式选择。
    """
    import os

    if os.getenv("THEORY_LLM", "1") == _THEORY_LLM_OFF:
        return False, "离线模式 (THEORY_LLM=0): 不联网检索"
    if getattr(snapshot, "evidence", None):
        return False, "研究循环已产生证据, 不重复检索"
    knowledge = getattr(engine, "knowledge", None)
    has_kb = bool(knowledge is not None and getattr(knowledge, "usable", False))
    if has_kb:
        return True, "需要为出版层检索相关工作"
    if policy in (SourcePolicy.autonomous, SourcePolicy.both):
        return True, "本地无资料库, 改用外部检索 (授权策略允许自主检索)"
    if policy is SourcePolicy.user_kb:
        return False, ("授权仅限用户资料库, 但资料库为空 (未上传文献); "
                       "如需检索外部文献请把资料范围改为「自主检索」或「两者合并」")
    return False, "无可用的检索源 (资料库/自主检索均不可用)"


def gather_publication_evidence(snapshot: ResearchSnapshot, engine,
                                references: refmod.ReferenceList | None = None,
                                limit: int = 6) -> PublicationEvidence:
    """检索 + 逐条判定支持关系 → 参考文献表与覆盖记录。

    已有引用表 (来自快照证据) 会**并入**结果: 出版层不丢研究循环已经采信的证据。
    不额外检索时也不会留下模糊记录: 覆盖记录会说明引用来自研究循环
    (`origin=research_loop`), 与"没检索过"区分开。
    """
    references = references or refmod.ReferenceList()
    policy = getattr(getattr(engine, "spec", None), "source_policy", SourcePolicy.user_kb)
    needed, reason = should_search(snapshot, engine, policy)
    coverage = RetrievalCoverage(
        policy=policy, executed=False,
        scope_note=(reason if not needed else ""),
        origin=("research_loop" if (references.references and not needed)
                else ("not_executed" if not needed else "publication_layer")),
    )
    bundle = PublicationEvidence(references=references, coverage=coverage)
    if not needed:
        bundle.notes.append(f"出版层未执行检索: {reason}")
        if references.references:
            bundle.notes.append(
                f"参考文献表来自研究循环已采信的证据 ({len(references.references)} 条)")
        return bundle

    knowledge = engine.knowledge
    queries = _queries(snapshot)
    coverage.queries = list(queries)
    external = knowledge is None or not getattr(knowledge, "usable", False)
    coverage.engines = (["external: arXiv/Semantic Scholar/OpenAlex"] if external
                        else [getattr(knowledge, "describe", lambda: "")() or "knowledge"])
    coverage.origin = "publication_layer_external" if external else "publication_layer"
    bundle.executed = True

    hits: list[SourceEvidence] = []
    try:
        for query in queries:
            refs = _search(query, limit, knowledge if not external else None)
            for ref in refs:
                hits.append(_to_evidence(ref, snapshot))
    except Exception as e:  # noqa: BLE001 - 检索失败不得中断研究收尾
        # 检索**尝试过**但失败: 必须如实记成 executed=True + failures,
        # 否则"检索失败"会被读成"没检索", 而两者对"能否宣称无先例"的含义完全不同。
        coverage.executed = True
        coverage.failures.append(f"{type(e).__name__}: {e}")
        bundle.executed = True
        bundle.notes.append(f"出版层检索失败 ({type(e).__name__}): {e}")
        return bundle

    coverage.hits = len(hits)
    # 检索**确实执行过** (可能命中 0 条): 与"没检索"必须区分 ——
    # 前者不构成"无先例"的依据, 后者连依据都谈不上。此前这里漏写, 覆盖记录会
    # 一边写着命中 18 条、一边写着 executed=false, 自相矛盾。
    coverage.executed = True
    bundle.executed = True
    deduped = _dedup(hits)
    coverage.duplicates = len(hits) - len(deduped)
    accepted: list[SourceEvidence] = []
    for item in deduped:
        item = _judge_support(snapshot, item)
        if item.support in (SupportKindOfEvidence.supports,
                            SupportKindOfEvidence.partially_supports,
                            SupportKindOfEvidence.background):
            accepted.append(item)
    # 只保留最相关的前若干条: 外部检索按词索引, 尾部命中常常只是"含同一个泛词"。
    # 排序依据是术语相关度 + 是否被判为支持 (支持关系强于背景)。
    accepted = _rank_and_cap(accepted, snapshot, limit=8)
    coverage.ingested = len(accepted)
    bundle.evidence = accepted
    if not accepted:
        coverage.scope_note = ("检索已执行但未找到可引用的相关工作; "
                               "参考文献表为空, 已有工作比较按“未发现”表述")
    merged = _merge(references, refmod.build_reference_list(accepted))
    bundle.references = merged
    bundle.notes.append(
        f"出版层检索: 命中 {coverage.hits} 条, 去重后 {len(deduped)} 条, "
        f"判定可引用 {len(accepted)} 条, 参考文献表共 {len(merged.references)} 条")
    return bundle


def _rank_and_cap(items: list[SourceEvidence], snapshot: ResearchSnapshot,
                  limit: int = 8) -> list[SourceEvidence]:
    """按相关度排序并截断, 避免把"恰好含同一个泛词"的命中当成相关工作。"""
    claim = snapshot.claims[0] if snapshot.claims else None
    terms = _background_terms(claim.statement) if claim else []

    def _score(item: SourceEvidence) -> tuple:
        strong = item.support in (SupportKindOfEvidence.supports,
                                  SupportKindOfEvidence.partially_supports)
        return (1 if strong else 0,
                _relatedness(f"{item.title} {item.excerpt}", terms),
                int(item.year or 0))

    return sorted(items, key=_score, reverse=True)[:limit]


def _queries(snapshot: ResearchSnapshot) -> list[str]:
    """检索式来自**研究对象的客观特征**, 不是自由发挥。"""
    queries: list[str] = []
    for claim in snapshot.claims:
        if claim.design_v and claim.design_k and claim.design_lambda:
            queries.append(f"2-({claim.design_v},{claim.design_k},"
                           f"{claim.design_lambda}) design existence")
            if claim.design_lambda == 1 and claim.design_k:
                queries.append(f"projective plane of order {claim.design_k - 1}")
            break
    return queries or ["theoretical existence proof of the stated structure"]


def _search(query: str, limit: int, knowledge) -> list:
    """一次检索: 有资料库走资料库, 否则走外部检索 (arXiv/OpenAlex)。

    两个必须处理的现实细节:
    - `search_tools` 里的检索函数被 `@tool` 包装成 `StructuredTool`, **不能直接调用**,
      要经 `.func` 取原始实现 (实测: 直接调用报 `'StructuredTool' object is not callable`);
    - 多源聚合可能因为其中一个源不可用而整体失败 (例如 Semantic Scholar 限流 429),
      此时**退到单源 arXiv** —— 否则一个源的问题会让整篇论文没有引用。

    检索失败与"检索过无命中"必须分开: 前者记 `failures`, 后者记 `hits=0`。
    """
    if knowledge is not None:
        return _refs_of(knowledge.search(query, limit=limit))
    from src.tools import search_tools as tools

    def _raw(name: str):
        fn = getattr(tools, name, None)
        return getattr(fn, "func", fn) if fn is not None else None

    aggregate = _raw("search_all_sources")
    single = _raw("arxiv_search")
    errors: list[str] = []
    try:
        if aggregate is not None:
            results = list(aggregate(query, max_results=limit) or [])
            if results:
                return results
    except Exception as e:  # noqa: BLE001 - 退到单源, 不直接放弃
        errors.append(f"多源检索失败 ({type(e).__name__}: {e})")
    if single is not None:
        try:
            return list(single(query, max_results=limit) or [])
        except Exception as e:  # noqa: BLE001
            errors.append(f"arXiv 检索失败 ({type(e).__name__}: {e})")
    if errors:
        raise RuntimeError("; ".join(errors))
    return []


def _refs_of(result) -> list:
    if result is None:
        return []
    if isinstance(result, list):
        return result
    for attribute in ("refs", "results", "hits", "items"):
        value = getattr(result, attribute, None)
        if isinstance(value, list):
            return value
    return []


def _stable_locator(url: str, doi: str = "", openalex_id: str = "") -> str:
    """从命中的标识符取**稳定定位**: `arXiv:2001.11974` / `doi:10.4230/…` / `W123`。

    必要性来自实测: 外部检索结果没有页/节定位, 而引用守门要求"可定位出处",
    于是每条外部命中都会被判成"无可定位出处"而无法引用 —— 结果是"检索到了却一条
    也引不了"。

    早期实现**只认 arXiv URL**, 而 OpenAlex 返回的全是 DOI 链接
    (`https://doi.org/10.4230/lipics.itp.2026.19`), 于是 OpenAlex 命中的定位恒为空、
    永远只能当背景相关——包括"引用某个具名定理"时找到的那个定理原始出处
    (实测: `_arxiv_locator` 对 DOI 返回 `''`)。这里按标识符的稳定性排序:
    arXiv ID > DOI > OpenAlex ID。
    """
    text = (url or "").strip()
    lowered = text.lower()
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+)", lowered)
    if match:
        return f"arXiv:{match.group(1)}"
    normalized = _normalize_doi(doi) or _normalize_doi(text)
    if normalized:
        return f"doi:{normalized}"
    identifier = (openalex_id or "").strip()
    if not identifier and "openalex.org/" in lowered:
        identifier = text.rstrip("/").rsplit("/", 1)[-1]
    return f"openalex:{identifier}" if identifier else ""


def _normalize_doi(value: str) -> str:
    """`https://doi.org/10.1/x`、`doi:10.1/x`、`10.1/x` -> `10.1/x` (统一小写)。"""
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip()
    # DOI 合法性: 以 `10.` 开头且含 `/`; 否则视为无效 (避免把普通 URL 当 DOI 著录)
    return text.lower() if text.startswith("10.") and "/" in text else ""


def _arxiv_locator(url: str) -> str:
    """向后兼容的 arXiv 定位 (保留给已有调用点)。"""
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+)", (url or "").lower())
    return f"arXiv:{match.group(1)}" if match else ""


def _to_evidence(ref, snapshot: ResearchSnapshot) -> SourceEvidence:
    """检索命中 → 证据对象。**命中即存在, 支持关系待判定**。

    兼容两种命中形态: 资料库的 `kb.service.SourceRef` (`source_id`, `excerpt`,
    `locator`) 与外部检索的字典 (`authors`/`abstract`/`url`, 无定位字段)。
    """
    from src.research.evidence import classify_source

    claim_id = snapshot.claims[0].id if snapshot.claims else ""
    get = (ref.get if isinstance(ref, dict) else
           lambda key, default="": getattr(ref, key, default))
    url = str(get("url", "") or "")
    doi = str(get("doi", "") or "")
    openalex_id = str(get("openalex_id", "") or "")
    # DOI / OpenAlex ID 与 arXiv ID 等价可用作定位 (见 `_stable_locator` 的实测说明)
    locator = (str(get("locator", "") or "")
               or _stable_locator(url, doi, openalex_id))
    doc_id = str(get("source_id", "") or get("doc_id", "") or "")
    paper = {
        "doc_id": doc_id,
        "title": str(get("title", "") or ""),
        "authors": str(get("authors", "") or ""),
        "abstract": str(get("abstract", "") or get("excerpt", "") or ""),
        "year": str(get("year", "") or ""),
        "venue": str(get("venue", "") or get("source", "") or ""),
        "url": url,
        "doi": doi,
        "locator": locator,
        "page": int(get("page", 0) or 0),
        "file_hash": str(get("file_hash", "") or ""),
    }
    try:
        item = classify_source(paper)
    except Exception:  # noqa: BLE001 - 构造失败按最低可信度处理, 不抛给收尾
        item = SourceEvidence(title=paper["title"], excerpt=paper["abstract"])
    item.claim_id = claim_id
    item.source_id = doc_id or item.source_id
    if locator and not item.location:
        item.location = locator
    if paper["authors"] and not item.notes:
        item.notes = f"作者: {paper['authors']}"
    return item


def _judge_support(snapshot: ResearchSnapshot, item: SourceEvidence) -> SourceEvidence:
    """逐条判定支持关系, 并**堵住两条会制造假引用的路径**。

    规则层判定 (`assess_support`) 的产物只是候选, 但它的两条"支持"提示
    (处理/结果同时出现、部分匹配命题对象) **都不看原文出处**: 没有定位就无法核对,
    因此没有页/节定位时一律降为待审 —— 这是"禁止靠标题相似声称支持"的落地点。
    即使有定位, 规则层也最多给 `partially_supports`, 绝不升级为强支持。
    """
    from src.research.evidence import assess_support, mark_pending, quote_is_locatable

    claim = next((c for c in snapshot.claims if c.id == item.claim_id), None)
    if claim is None and snapshot.claims:
        claim = snapshot.claims[0]
    if claim is None:
        return item
    item = assess_support(claim, item)
    related = _is_topically_related(f"{item.title} {item.excerpt}",
                                    _background_terms(claim.statement))
    if item.support not in (SupportKindOfEvidence.supports,
                            SupportKindOfEvidence.partially_supports):
        if not related:
            return item          # 真的无关: 保持 insufficient, 不进参考文献表
        # 主题相关但**没有被判定为支持**: 只能作为"背景相关工作"引用。
        # 这条区分很关键 —— 相关工作一节需要能引用真实存在的同类研究,
        # 但不得暗示它们支持本文结论 (方案 v2 §3 防火墙)。
        item.support = SupportKindOfEvidence.background
        item.support_reason = ("主题相关 (术语对照命中), 但未判定为支持本文结论; "
                               "仅作背景相关工作引用")
        item.reviewer = "rule"
    located = bool(item.location or item.page)
    if not located:
        original = item.support.value
        return mark_pending(item, "命中无可定位出处 (缺页/节定位), 不支持引用",
                            original=original)
    # 规则层只说"主题词出现过", 不给出引文 (它没有出处意识)。这里把**实际出现的
    # 片段**作为引文登记, 再要求它能在已读取的原文里定位 —— 定位不了就降为待审。
    # 顺序很关键: 不能因为"判定器没填引文"就一律降级 (那会把可核查的候选也扔掉),
    # 也不能不核对就采信 (那就是靠标题相似声称支持)。
    snippet = _locatable_snippet(item, claim)
    if snippet:
        item.support_evidence = snippet
        item.support_reason = (item.support_reason + "; 引文可在已读取原文中定位")
    if not quote_is_locatable(item, (item.support_evidence or "").strip()):
        original = item.support.value
        return mark_pending(item, "无可定位引文, 支持关系降为待审", original=original)
    return item


def _locatable_snippet(item: SourceEvidence, claim: Claim, window: int = 60) -> str:
    """从已读取的原文里取一段**确实包含命题主题词**的引文片段。"""
    text = item.excerpt or ""
    if not text:
        return ""
    tokens = [token for token in (claim.statement or "").split() if len(token) >= 2]
    for token in tokens:
        index = text.find(token)
        if index >= 0:
            start = max(0, index - window // 3)
            return text[start:start + window].strip()
    return text[:window].strip()


def _dedup(items: list[SourceEvidence]) -> list[SourceEvidence]:
    seen: set[str] = set()
    unique: list[SourceEvidence] = []
    for item in items:
        identity = (item.file_hash or item.doi or item.title or item.id).lower()
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def _merge(existing: refmod.ReferenceList,
           extra: refmod.ReferenceList) -> refmod.ReferenceList:
    """合并两份参考文献表并**重新编号**, 保证正文引用编号连续且唯一。

    重新编号是必须的: 正文引用编号由本表位置唯一决定, 两份表各自从 ref1 开始,
    直接拼接会出现两个 `ref1` → 正文 `[1]` 指向哪一条就不可预期。
    """
    merged = refmod.ReferenceList()
    seen: set[str] = set()
    for item in list(existing.references) + list(extra.references):
        identity = (item.source_id or item.doi or item.title).lower()
        if identity in seen:
            continue
        seen.add(identity)
        item.key = f"ref{len(merged.references) + 1}"
        merged.references.append(item)
    merged.excluded = list(existing.excluded) + list(extra.excluded)
    return merged
