from __future__ import annotations

"""检索式规划: 由问题契约与缺口生成**多条**检索式, 并说明为什么这样问。

计划书 §1 契约第 2 行要求系统"在授权范围内自行设计检索式"; 单一查询词
(`claim.statement`) 既覆盖不到机制/定义/反例等不同角度, 也无法在覆盖记录里
说清"检索了什么、为什么"。
"""

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


def _terms_in(text: str, terminology: dict[str, list[str]] | None) -> list[str]:
    """命中题目/契约术语表的变体 (中/英/缩写), 用于生成跨语言检索式。

    显式术语表为空时**回退到领域术语对照表** (`publication_evidence.terminology_variants`):
    中文题的检索式若只有中文, 外部检索会给出无关结果 (实测: OpenAlex 把"组合设计"
    当成土木工程的"路面结构组合设计"), 必须补上英文术语变体。
    """
    source = text or ""
    terms: list[str] = []
    if terminology:
        low = source.lower()
        for concept, variants in terminology.items():
            if concept and (concept.lower() in low
                            or any(v.lower() in low for v in variants)):
                terms.extend(v for v in variants if v and v.lower() not in low)
    if not terms and re.search(r"[\u4e00-\u9fff]", source):
        from src.research.publication_evidence import terminology_variants

        terms.extend(terminology_variants(source))
    return list(dict.fromkeys(term for term in terms if term))


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
# 只做角度而非领域的泛词: **整类排除**出跨语言检索式。
# 依据来自实测 (OpenAlex, 每个检索式取前 3 条看相关性):
#   `projective plane nonexistence`               -> 3/3 相关
#   `necessary condition projective plane`        -> 3/3 相关
#   `does not exist necessary condition …`        -> 1/3 (混进 "Finite semifields")
#   `necessary condition projective plane existence count` -> 2/3 (混进 "Necessary
#                                                          Condition Analysis (NCA)")
# 结论: 泛词 (existence/count/design/theorem/order…) 会把领域名词短语稀释掉。
# 注意 `nonexistence` **不在**泛词表里 —— 否定性结论词是强领域信号, 必须保留。
_GENERIC_TERMS = {"design", "theorem", "count", "counting", "order", "graph",
                  "finite", "symmetric", "symmetry", "block", "necessity",
                  "existence", "necessary condition", "condition"}


# 中文结论/关系 -> 英文意图词。用于**从题干识别研究意图**, 而不管术语表里凑巧命中了几个同义词。
# 必要性来自实测: 题干"射影平面 不存在性"会同时命中 `nonexistence`/`non-existence`/
# `does not exist` 三个同义变体, 全堆进检索式后返回的是 "Nonexistence, Vague Existence,
# Merely Possible Existence" 这类无关结果; 而配对成 `projective plane nonexistence`
# 才会返回 "The Nonexistence of Certain Finite Projective Planes" 等 3/3 命中。
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

    由实测确定的三层规则:
    1. 泛词整类剔除 (`existence`/`count`/`design`/`necessary condition`…);
    2. **意图词与领域名词短语配对**: 只有领域词太泛 (只查 `projective plane` 返回
       "Projective planes"、"Affine and projective planes" 教科书条目), 只有意图词更糟
       (`nonexistence non-existence does not exist` 返回液滴模型等无关结果);
       配对后 (`projective plane nonexistence`) 才 3/3 命中;
    3. 题干里声明的结论关系 (`不存在`/`反例`/`等价`…) 若术语表没给, 由题干补上意图词。
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

    variants = _terms_in(f"{text} {treatment} {outcome}", terminology)
    if variants and queries:
        # 跨语言检索式**精选核心英文术语**: 中英混排会稀释英文匹配, 而把命中到的术语
        # 全堆进去更糟 —— 实测 `projective plane design existence necessary condition
        # counting count` 返回 "Necessary Condition Analysis"、"动态车辆路径问题",
        # 而 `projective plane nonexistence` 精准返回 "The Nonexistence of Certain
        # Finite Projective Planes"。因此: 只留英文, 且按"领域术语 > 意图词"取前几个。
        core = _select_english_terms(variants, relation_text=f"{text} {treatment} {outcome}")
        if core:
            queries.insert(1 if len(queries) > 1 else len(queries), Query(
                text=_clean(" ".join(core)),
                angle="terminology",
                why="用术语表的英文变体覆盖非中文文献 (中文关键词在英文库里等于噪声)",
                terms=core))

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


__all__ = ["Query", "plan_queries", "research_question_text"]
