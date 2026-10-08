from __future__ import annotations

"""由问题契约与术语对照规划可追溯的多角度检索式。"""

import re
from dataclasses import dataclass, field

from src.research.schemas import ProblemContract, TaskKind

# 角度 → (查询模板, 为什么这样问)
_ANGLES: tuple[tuple[str, str, str], ...] = (
    ("result", "{t} {o}", "先取直接给出结论的原始结果"),
    ("mechanism", "{t} 机理 机制 {o}", "找机制/作用路径, 而不是只看结论"),
    ("definition", "{o} 定义 度量", "确认结果变量的定义与度量口径"),
    ("method", "{o} 测量 估计 方法", "确认已有工作用什么方法与设置"),
    ("boundary", "{t} {o} 适用范围 条件 边界", "找条件的适用范围, 避免无条件推广"),
    ("counterexample", "{t} {o} 反例 失效 例外", "主动找会推翻过强结论的反例"),
)
_ORDER = {
    TaskKind.mechanism: ("mechanism", "result", "definition", "method", "boundary", "counterexample"),
    TaskKind.formal_proof: ("definition", "result", "method", "counterexample", "boundary", "mechanism"),
    TaskKind.empirical_causal: ("result", "method", "definition", "boundary", "counterexample", "mechanism"),
    TaskKind.scenario: ("boundary", "result", "mechanism", "definition", "method", "counterexample"),
}


@dataclass
class Query:
    """一条检索式: 文本 + 角度 + 依据。"""

    text: str
    angle: str = "result"
    why: str = ""
    terms: list[str] = field(default_factory=list)


def _matched_terminology(text: str, terminology: dict[str, list[str]] | None):
    """Keep synonym groups so one query never requires two names for one concept."""
    from src.rag.relevance_filter import resolve_domain

    field = resolve_domain(text)
    entries = dict(field.translations)
    for concept, variants in (terminology or {}).items():
        entries[concept] = list(dict.fromkeys([*(entries.get(concept) or []), *variants]))
    lower = text.casefold()
    matched = [(concept, tuple(variants)) for concept, variants in entries.items()
               if concept and (concept.casefold() in lower or any(
                   variant and variant.casefold() in lower for variant in variants))]
    return field, matched


def research_question_text(text: str) -> str:
    """保留附件题面内容，移除传输定界符、哈希与外部资料提示样板。"""
    body = str(text or "").strip()
    blocks = re.findall(r"<<<EXTERNAL_DATA_BEGIN>>>(.*?)<<<EXTERNAL_DATA_END>>>", body, re.S)
    if blocks:
        prefix = body.split("<<<EXTERNAL_DATA_BEGIN>>>", 1)[0]
        prefix = re.split(r"以下定界区内", prefix, maxsplit=1)[0].strip()
        body = "\n\n".join([*blocks, prefix])
    body = re.sub(r"\[附件[^\]\n]*\]", "", body)
    body = re.sub(r"<<<EXTERNAL_DATA_(?:BEGIN|END)>>>", "", body)
    return body.strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", research_question_text(text)).strip()


# 核心意图词 (研究与结论强相关的谓词)。带这些词的术语最值得进英文检索式。
_INTENT_TERMS = ("nonexistence", "non-existence", "does not exist", "impossible",
                 "existence", "classification", "construction", "construct",
                 "counterexample", "conjecture")
# 角度泛词不作为领域检索核心；结论关系词由题面意图单独补入。
_GENERIC_TERMS = {"design", "theorem", "count", "counting", "order", "graph",
                  "finite", "symmetric", "symmetry", "block", "necessity",
                  "existence", "necessary condition", "condition"}


# 中文结论关系转为英文检索意图，避免把同义变体堆在一条查询里。
_RELATION_INTENT: tuple[tuple[tuple[str, ...], str], ...] = (
    (("不存在", "不可能", "非存在", "nonexistence"), "nonexistence"),
    (("反例",), "counterexample"),
    (("等价",), "equivalence"),
    (("分类",), "classification"),
    (("构造", "生成方法"), "construction"),
    (("存在性", "是否存在", "能否存在"), "existence"),
)


def _relation_intent(text: str) -> str:
    """从题干识别英文意图词 (取第一个命中的关系; 没有则空串)。"""
    haystack = text or ""
    for tokens, intent in _RELATION_INTENT:
        if any(token in haystack for token in tokens):
            return intent
    return ""


def _select_english_terms(variants: list[str], limit: int = 3,
                          relation_text: str = "") -> list[str]:
    """从术语变体里**精选**跨语言检索式的英文词。

    剔除泛词；将题面结论关系与领域名词配对；同义意图词只取一个。
    """
    english = [term for term in variants
               if term and not re.search(r"[\u4e00-\u9fff]", term)]
    domains: list[str] = []
    intents: list[str] = []
    for term in dict.fromkeys(english):
        cleaned = term.strip()
        lowered = cleaned.lower()
        if lowered in _GENERIC_TERMS:
            continue
        if any(word in lowered for word in _INTENT_TERMS):
            intents.append(cleaned)
        else:
            domains.append(cleaned)
    # 领域名词短语优先 (多词 > 单词)
    domains.sort(key=lambda term: (0 if " " in term else 1, term))

    # 同义意图词只留一个: 题干声明的意图优先, 否则用术语表给的第一个
    declared = _relation_intent(relation_text)
    intent = declared or (intents[0] if intents else "")

    core: list[str] = []
    if intent:
        core.append(intent)
    core.extend(domains)
    return core[:max(int(limit), 1)]


def _is_abbreviation(term: str) -> bool:
    """Only glossary-declared acronyms qualify; do not guess them from initials."""
    value = term.strip()
    return bool(re.fullmatch(r"[A-Z0-9]{2,8}", value) and re.search(r"[A-Z]", value))


def _chinese_concepts(text: str, terminology: dict[str, list[str]] | None,
                      field) -> list[str]:
    entries = dict(field.translations)
    entries.update(terminology or {})
    return list(dict.fromkeys(
        concept for concept in entries
        if re.search(r"[\u4e00-\u9fff]", concept) and concept in text))


def choose_queries(planned: list[Query], selected: list[str], limit: int) -> list[str]:
    """Keep model suggestions while reserving Chinese and mapped English coverage."""
    ceiling = max(int(limit), 1)
    pool = list(dict.fromkeys([*(q.strip() for q in selected if q.strip()),
                               *(q.text for q in planned if q.text)]))
    chosen = pool[:ceiling]
    essential: list[str] = []
    chinese = next((q.text for q in planned if q.angle == "chinese_terms"), "") or next(
        (q.text for q in planned if re.search(r"[\u4e00-\u9fff]", q.text)), "")
    english = next((q.text for q in planned if q.angle == "terminology"), "")
    abbreviation = next((q.text for q in planned if q.angle == "abbreviation"), "")
    if chinese and ceiling >= 2:
        essential.append(chinese)
    if english and ceiling >= 2:
        essential.append(english)
    if abbreviation and ceiling >= 4:
        essential.append(abbreviation)
    protected: set[str] = set()
    for query in essential:
        if query in chosen:
            protected.add(query)
            continue
        if len(chosen) < ceiling:
            chosen.append(query)
        else:
            replacement = next((i for i in range(len(chosen) - 1, -1, -1)
                                if chosen[i] not in protected), None)
            if replacement is not None:
                chosen[replacement] = query
        protected.add(query)
    return list(dict.fromkeys(chosen))


def plan_queries(contract: ProblemContract | None = None, *, goal: str = "",
                 terminology: dict[str, list[str]] | None = None,
                 limit: int = 6) -> list[Query]:
    """生成 1–`limit` 条检索式。

    - 有处理/结果对象时按角度展开; 否则退回整句目标 (不臆造对象);
    - 术语表给出变体时补一条跨语言检索式;
    - 角度顺序按研究类型排 (机理问题先找机理, 因果问题先找结果与方法)。
    """
    text = _clean(goal or (contract.goal if contract else ""))
    objects = list(contract.objects) if contract else []
    outcome = objects[0] if len(objects) > 0 else ""
    treatment = objects[1] if len(objects) > 1 else ""
    kind = contract.task_kind if contract else TaskKind.scenario

    queries: list[Query] = []
    if treatment and outcome:
        by_angle = {name: _clean(template.format(t=treatment, o=outcome))
                    for name, template, _ in _ANGLES}
        why_by_angle = {name: why for name, _, why in _ANGLES}
        for name in _ORDER[kind]:
            queries.append(Query(text=by_angle[name], angle=name, why=why_by_angle[name]))
    elif text:
        queries.append(Query(text=text[:240], angle="goal", why="按题面目标检索，关联概念由研究角色补充"))

    term_text = f"{text} {contract.goal if contract else ''} {treatment} {outcome}"
    field, matched = _matched_terminology(term_text, terminology)
    if matched and queries:
        # Full names and acronyms are separate searches: requiring both in one
        # query misses papers that use only one of the two forms.
        canonical: list[str] = []
        alternatives: list[tuple[str, str]] = []
        abbreviations: list[str] = []
        for _concept, variants in matched:
            full = [variant for variant in variants
                    if not re.search(r"[\u4e00-\u9fff]", variant)
                    and not _is_abbreviation(variant)]
            abbreviations.extend(variant for variant in variants if _is_abbreviation(variant))
            if full:
                canonical.append(full[0])
                if len(full) > 1:
                    alternatives.append((full[0], full[1]))
        preferred = [term for term in field.query_core if term in canonical]
        core = list(dict.fromkeys([*preferred, *_select_english_terms(
            canonical, relation_text=term_text)]))[:3]
        if core:
            queries.insert(1 if len(queries) > 1 else len(queries), Query(
                text=_clean(" ".join(core)),
                angle="terminology",
                why="用已登记的英文全称检索英文文献，并与中文查询分开记录",
                terms=core))
        if abbreviations:
            context = [next((word for word in term.split()
                             if len(word) >= 4 and word.lower() not in _GENERIC_TERMS), "")
                       for term in core]
            context = [word for word in context if word][:2]
            for abbreviation in abbreviations[:2]:
                query = _clean(" ".join([abbreviation, *context]))
                if query:
                    queries.insert(2 if len(queries) > 2 else len(queries), Query(
                        text=query, angle="abbreviation",
                        why="用术语表声明的缩写加领域上下文检索，避免缩写单独造成歧义",
                        terms=[abbreviation, *context]))
        chinese = _chinese_concepts(term_text, terminology, field)
        if len(chinese) >= 2:
            queries.insert(min(3, len(queries)), Query(
                text=_clean(" ".join(chinese[:4])), angle="chinese_terms",
                why="用题面命中的中文专有名词生成精简关键词检索式",
                terms=chinese[:4]))
        for original, alternative in alternatives[:1]:
            if original in core:
                alt_terms = [alternative if term == original else term for term in core]
                queries.insert(min(4, len(queries)), Query(
                    text=_clean(" ".join(alt_terms)), angle="terminology_alternative",
                    why="同一概念的另一种已登记英文名称单独检索，避免同义词相互限制",
                    terms=alt_terms))

    seen: set[str] = set()
    out: list[Query] = []
    for query in queries:
        key = query.text.lower()
        if not query.text or key in seen:
            continue
        seen.add(key)
        out.append(query)
        if len(out) >= max(int(limit), 1):
            break
    return out


__all__ = ["Query", "choose_queries", "plan_queries", "research_question_text"]
