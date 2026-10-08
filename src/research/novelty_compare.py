from __future__ import annotations

"""与已有工作的逐项差异分析 (计划书 §3 R4)。

计划书的判断: 现有对照仍主要依靠检索结果的标题/摘要, 差异栏可能为空;
新颖性状态能保持待比较, 但"与先前工作有何不同"尚未真正完成。

本模块把"差异分析"变成**逐项判定 + 可审计结果**:

- 比对维度: 前提 (premises) / 模型 (method/model) / 结论 (conclusion) /
  证明方法 (method) / 适用域 (applicability);
- 每个维度给出 `same` / `contains` / `contained` / `stronger` / `weaker` /
  `unknown`, 只有 `unknown` 以外的判定才允许支撑"差异";
- 整体关系: `equivalent` / `conclusion_contained` / `conditions_stronger` /
  `conditions_weaker` / `different` / `not_comparable`;
- **没有原文或覆盖不充分时只作有界表述**: 判定为 `not_comparable` 并写明缺什么,
  绝不用标题相似度冒充"等价/不同"。

硬约束: 本模块不改变任何结论状态; 它只产出可写入 `NoveltyComparisonRow.difference`
的文本与结构化判定, 供 `novelty.assess` 决定新颖性状态。
"""

import re
from dataclasses import dataclass, field

from src.research.schemas import Claim, NoveltyComparisonRow, SourceEvidence

# 维度判定
SAME = "same"
CONTAINS = "contains"          # 已有工作更强 (包含本研究)
CONTAINED = "contained"        # 本研究更强 (包含已有工作)
STRONGER = "stronger"          # 本研究条件更强 (更受限)
WEAKER = "weaker"
UNKNOWN = "unknown"

# 整体关系
EQUIVALENT = "equivalent"
CONCLUSION_CONTAINED = "conclusion_contained"
CONDITIONS_STRONGER = "conditions_stronger"
CONDITIONS_WEAKER = "conditions_weaker"
DIFFERENT = "different"
NOT_COMPARABLE = "not_comparable"

_RELATION_LABEL = {
    EQUIVALENT: "等价 (已有工作可直接覆盖本研究结论)",
    CONCLUSION_CONTAINED: "已有工作结论包含本研究 (本研究是特例)",
    CONDITIONS_STRONGER: "本研究条件更强/更受限 (结论弱于已有工作)",
    CONDITIONS_WEAKER: "本研究条件更弱/更一般 (强于已有工作, 需核对是否真的成立)",
    DIFFERENT: "结论/方法不同",
    NOT_COMPARABLE: "无法比较 (缺少原文条件或覆盖不足)",
}

_DIM_LABEL = {
    "premises": "前提", "conclusion": "结论", "method": "证明方法",
    "applicability": "适用域", "model": "模型",
}


@dataclass
class Comparison:
    """一条已有工作的逐项比对结果。"""

    result: str = ""
    relation: str = NOT_COMPARABLE
    dimensions: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    bounded: bool = True
    evidence_id: str = ""

    @property
    def difference_text(self) -> str:
        """写入 `NoveltyComparisonRow.difference` 的文本。

        `not_comparable` 时写"未能判断"而不是留空或编造差异 —— 留空会被上层当作
        "尚未分析", 而这里其实已经分析过、只是无法判定。
        """
        if self.relation == NOT_COMPARABLE:
            return "未能判断 (缺 " + "、".join(self.notes or ["原文条件"]) + ")"
        parts = [f"{_DIM_LABEL.get(k, k)}: {v}" for k, v in self.dimensions.items()
                 if v != UNKNOWN]
        label = _RELATION_LABEL.get(self.relation, self.relation)
        return f"{label} — " + "; ".join(parts) if parts else label

    def describe(self) -> str:
        return (f"{self.result[:60]}: {_RELATION_LABEL.get(self.relation, self.relation)}"
                + (f" (缺 {'、'.join(self.notes)})" if self.notes else ""))

    def to_dict(self) -> dict:
        return {"result": self.result, "relation": self.relation,
                "dimensions": dict(self.dimensions), "notes": list(self.notes),
                "bounded": self.bounded, "difference": self.difference_text,
                "evidence_id": self.evidence_id}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())


_DOMAIN_WORDS = ("real", "reals", "integer", "integers", "positive", "nonnegative",
                 "nonzero", "complex", "实数", "复数", "整数", "正数", "非负", "非零")


_DOMAIN_CANON = {
    "real": "real", "reals": "real", "实数": "real", "实数域": "real",
    "integer": "integer", "integers": "integer", "整数": "integer", "整数域": "integer",
    "positive": "positive", "正数": "positive",
    "nonnegative": "nonnegative", "非负": "nonnegative",
    "nonzero": "nonzero", "非零": "nonzero",
    "complex": "complex", "复数": "complex", "复数域": "complex",
}


def _domain_tokens(text: str) -> set[str]:
    """把域声明归一成同一口径, 让 "x∈real" 与中文 "实数域/任意实数" 可比。"""
    tokens: set[str] = set()
    for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)?\s*(?:∈|in)\s*([A-Za-z\u4e00-\u9fff]+)",
                             text or "", re.IGNORECASE):
        canon = _DOMAIN_CANON.get(match.group(2).lower())
        if canon:
            tokens.add(f"dom:{canon}")
    for match in re.finditer(r"(实数域|复数域|整数域|实数|复数|整数|正数|非负|非零)", text or ""):
        canon = _DOMAIN_CANON.get(match.group(1))
        if canon:
            tokens.add(f"dom:{canon}")
    return tokens


def _condition_tokens(text: str) -> set[str]:
    """抽取**条件**片段: 域声明 (归一为 dom:*) 与限定式 (`x>0`)。"""
    tokens = _domain_tokens(text)
    for match in re.finditer(
            r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*\s*(?:>=|<=|>|<)\s*[-+]?\d+(?:\.\d+)?",
            text or ""):
        tokens.add(_normalize(match.group(0)))
    return tokens


def _comparison_tokens(text: str) -> set[str]:
    """抽取数学关系片段 (形如 x**2>=0 / y<=1/2 / a=b); 统一比较符号写法。"""
    tokens: set[str] = set()
    pattern = (r"[A-Za-z0-9_^*/.\-+()]+\s*(?:>=|<=|!=|==|>|<|=|≥|≤|≠)\s*"
               r"[A-Za-z0-9_^*/.\-+()]+")
    for match in re.finditer(pattern, text or ""):
        token = _normalize(match.group(0))
        token = token.replace("≥", ">=").replace("≤", "<=").replace("≠", "!=")
        if token and not token.startswith(("=", ">", "<")):
            tokens.add(token.replace(">=", "≥").replace("<=", "≤"))
    return tokens


def _conclusion_tokens(text: str) -> set[str]:
    """结论片段: 关系片段中剔除"仅仅是域声明/限定条件"的部分。

    早期版本把 `x∈real` 这样的域声明也算成结论, 于是"条件相同、结论相同"的对照
    会被判成"条件更强", 关系判定随之失真。
    """
    conditions = _condition_tokens(text)
    normalized_conditions = {c.replace("∈", "∈") for c in conditions}
    return {t for t in _comparison_tokens(text)
            if t not in normalized_conditions
            and not t.startswith(tuple(c for c in normalized_conditions if c))}


def _subset(our: set[str], theirs: set[str]) -> tuple[bool, bool]:
    """返回 (ours ⊆ theirs, theirs ⊆ ours); 空集合按"不可比较"处理。"""
    if not our or not theirs:
        return False, False
    return our <= theirs, theirs <= our


def _structured_conditions(claim: Claim) -> set[str]:
    """从**结构化字段**取本研究的前提条件: 变量域 + 范围 + 假设。

    不从命题陈述里抠条件 —— 陈述里的不等式是**结论**, 把它当条件会让"条件相同、
    结论相同"的对照被判成"条件不同"(真实案例: x∈real vs x∈real)。
    """
    tokens: set[str] = set()
    for var, domain in (claim.variable_domains or {}).items():
        if domain:
            tokens |= _domain_tokens(f"{var}∈{domain}")
    scope = f"{claim.scope_population} {claim.scope_region} {claim.scope_period}".strip()
    if scope:
        tokens.add(_normalize(scope))
    return tokens


def compare_with_row(claim: Claim,
                     row: NoveltyComparisonRow,
                     evidence: SourceEvidence | None = None) -> Comparison:
    """把一条已有工作与当前命题逐项比对。

    只有拿到**可比较的文本** (原文片段或结构化的前提/结论字段) 才给出非
    `not_comparable` 的判定; 否则保持有界表述。
    """
    comparison = Comparison(result=row.result or row.conclusion or "(未命名结果)",
                            evidence_id=getattr(evidence, "id", ""))
    prior_text = " ".join([row.premises, row.conclusion, row.method,
                           row.applicability, row.strength, row.equality_conditions,
                           (evidence.excerpt if evidence else "")]).strip()
    if not prior_text:
        comparison.notes.append("已有工作的前提/结论文本")
        return comparison
    our_text = " ".join([claim.statement, claim.lhs, claim.rhs, claim.expr,
                         " ".join(f"{v}∈{d}" for v, d in (claim.variable_domains or {}).items())])

    # 条件只从**结构化字段**取 (不解析陈述文本: 里面的不等式是结论, 定性词也不可靠)
    our_conditions = _structured_conditions(claim)
    their_conditions = _condition_tokens(prior_text)
    our_conclusions = _conclusion_tokens(our_text)
    their_conclusions = _conclusion_tokens(prior_text)

    dims: dict[str, str] = {}
    # 前提/条件
    if our_conditions and their_conditions:
        ours_subset, theirs_subset = _subset(our_conditions, their_conditions)
        if ours_subset and theirs_subset:
            dims["premises"] = SAME
        elif ours_subset:
            dims["premises"] = WEAKER      # 我们条件更少 → 更一般
        elif theirs_subset:
            dims["premises"] = STRONGER    # 我们条件更多 → 更受限
        else:
            dims["premises"] = UNKNOWN
    else:
        dims["premises"] = UNKNOWN
        comparison.notes.append("条件文本")

    # 结论
    if our_conclusions and their_conclusions:
        ours_subset, theirs_subset = _subset(our_conclusions, their_conclusions)
        if ours_subset and theirs_subset:
            dims["conclusion"] = SAME
        elif ours_subset:
            dims["conclusion"] = CONTAINED   # 我们的结论是他们的子集 → 我们更弱
        elif theirs_subset:
            dims["conclusion"] = CONTAINS
        else:
            dims["conclusion"] = UNKNOWN
    else:
        dims["conclusion"] = UNKNOWN
        comparison.notes.append("结论文本")

    # 证明方法 / 适用域: 只有结构化字段给出时才判定
    dims["method"] = (SAME if row.method and claim.study.design.value
                      and _normalize(row.method) == _normalize(claim.study.design.value)
                      else UNKNOWN)
    dims["applicability"] = (SAME if row.applicability and claim.scope_population
                             and _normalize(row.applicability) == _normalize(claim.scope_population)
                             else UNKNOWN)
    comparison.dimensions = dims

    comparison.relation = _overall(dims)
    comparison.bounded = comparison.relation in (NOT_COMPARABLE, DIFFERENT)
    if comparison.relation == NOT_COMPARABLE and not comparison.notes:
        comparison.notes.append("可比对维度")
    return comparison


def _overall(dims: dict[str, str]) -> str:
    premises = dims.get("premises", UNKNOWN)
    conclusion = dims.get("conclusion", UNKNOWN)
    if premises == SAME and conclusion == SAME:
        return EQUIVALENT
    if conclusion in (SAME, CONTAINS) and premises == SAME:
        return CONCLUSION_CONTAINED
    if conclusion == CONTAINED and premises in (STRONGER, SAME):
        return CONDITIONS_STRONGER
    # 结论相同但前提更少 → 已有工作是本研究的一般化, 本研究是它的特例
    if conclusion == SAME and premises == WEAKER:
        return CONDITIONS_STRONGER
    # 结论相同但本研究条件更少 → 本研究更一般
    if conclusion == SAME and premises == STRONGER:
        return CONDITIONS_WEAKER
    if conclusion == CONTAINED and premises == WEAKER:
        return CONDITIONS_WEAKER
    if conclusion == UNKNOWN:
        return NOT_COMPARABLE
    if premises == UNKNOWN:
        return NOT_COMPARABLE
    return DIFFERENT


_BOUNDARY_SENTENCE = re.compile(r"(在|当|若|如果|假设)[^。;；]{0,40}(下|时|条件|区间|情况)")
_METHOD_WORDS = ("仿真", "实验", "测量", "估计", "推导", "证明", "回归", "仿真实验",
                 "simulation", "experiment", "measurement", "derivation", "proof",
                 "regression", "estimation")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。;；!?！？\n]", text or "") if s.strip()]


def _fill_from_evidence(row: NoveltyComparisonRow,
                        evidence: SourceEvidence) -> NoveltyComparisonRow:
    """从**已读原文**抽取结构化前提/结论/方法/适用域/定位。

    只在原文确实给出对应信息时填写; 抽不到就留空并由比较环节判 `not_comparable`,
    不用标题或摘要相似度凑数。
    """
    excerpt = evidence.excerpt or ""
    updates: dict[str, str] = {}
    if not row.locator:
        updates["locator"] = evidence.location or (f"p.{evidence.page}" if evidence.page else "")
    sentences = _sentences(excerpt)
    if not row.premises:
        boundary = next((s for s in sentences if _BOUNDARY_SENTENCE.search(s)), "")
        if boundary:
            updates["premises"] = boundary[:200]
    if not row.conclusion:
        updates["conclusion"] = (sentences[0] if sentences else excerpt[:200])[:200]
    if not row.applicability:
        updates["applicability"] = evidence.location or row.applicability
    if not row.method:
        method = next((w for w in _METHOD_WORDS if w in excerpt.lower()), "")
        if method:
            updates["method"] = method
    if not row.result:
        updates["result"] = evidence.title or evidence.literature_id
    return row.model_copy(update=updates) if updates else row


def _as_row_and_evidence(source_result) -> tuple[NoveltyComparisonRow, SourceEvidence | None]:
    if isinstance(source_result, NoveltyComparisonRow):
        return source_result, None
    if isinstance(source_result, SourceEvidence):
        return NoveltyComparisonRow(result=source_result.title or source_result.literature_id), source_result
    raise TypeError(f"不支持的先前结果类型: {type(source_result).__name__}")


def _formal_implication(claim: Claim, row: NoveltyComparisonRow) -> tuple[str, str]:
    """可形式化的子命题: 用符号计算辅助判断蕴含方向。

    Returns: (dimension, relation); 判不出来返回 ("", "") —— 不猜。
    只在两边都给出**同一比较符**的闭式表达式时尝试; 工具不可用或无法证明则放弃。
    """
    ours = f"{claim.lhs} {claim.relation.value} {claim.rhs}".strip() if (claim.lhs or claim.rhs) else ""
    theirs = (row.conclusion or "").strip()
    if not ours or not theirs:
        return "", ""
    op_match = re.search(r"(>=|<=|>|<|==|=)", theirs)
    if not op_match or op_match.group(1) not in ours:
        return "", ""
    try:
        import sympy
        from sympy.parsing.sympy_parser import parse_expr
    except Exception:  # noqa: BLE001 - 无符号计算能力时不做形式化辅助
        return "", ""

    def _diff(expr_text: str) -> object | None:
        parts = re.split(r"(?:>=|<=|>|<|==|=)", expr_text, maxsplit=1)
        if len(parts) != 2:
            return None
        try:
            return parse_expr(parts[0]) - parse_expr(parts[1])
        except Exception:  # noqa: BLE001
            return None

    our_diff, their_diff = _diff(ours), _diff(theirs)
    if our_diff is None or their_diff is None:
        return "", ""
    try:
        delta = sympy.simplify(our_diff - their_diff)
        if delta == 0:
            return "conclusion", SAME
        # 只在符号计算**证明**了方向时才给判定, 否则保持不可比
        syms = {s: sympy.Symbol(s, real=True) for s in map(str, delta.free_symbols)}
        if not syms:
            return "", ""
        nonneg = sympy.ask(sympy.Q.nonnegative(delta), sympy.Q.positive(syms[next(iter(syms))]))
        if nonneg is True:
            return "conclusion", CONTAINED       # 本研究结论更强
        if nonneg is False:
            return "conclusion", CONTAINS        # 已有工作更强
    except Exception:  # noqa: BLE001
        return "", ""
    return "", ""


def compare_prior(claim: Claim, source_result) -> Comparison:
    """逐维、有界地比较一条先前结果 (计划书 §3 接口 `compare_prior`)。

    `source_result` 可以是 `SourceEvidence` (已读原文, 会先抽结构化字段)
    或 `NoveltyComparisonRow` (检索层已给出的行)。
    """
    row, evidence = _as_row_and_evidence(source_result)
    if evidence is not None:
        row = _fill_from_evidence(row, evidence)
    comparison = compare_with_row(claim, row, evidence)
    if row.locator:
        comparison.notes.append(f"定位: {row.locator}")
    dimension, relation = _formal_implication(claim, row)
    if relation:
        comparison.dimensions[dimension] = relation
        comparison.relation = _overall(comparison.dimensions)
        comparison.bounded = comparison.relation in (NOT_COMPARABLE, DIFFERENT)
    elif claim.lhs or claim.rhs:
        comparison.notes.append("形式化蕴含方向未能证明")
    return comparison


def analyze_rows(claim: Claim,
                 rows: list[NoveltyComparisonRow],
                 evidence: list[SourceEvidence] | None = None,
                 ) -> tuple[list[NoveltyComparisonRow], list[Comparison]]:
    """对一批已有工作逐条分析, 返回 (**带 difference 的行**, 比对结果)。

    只在能给出判定时填 `difference`; `not_comparable` 写"未能判断"并说明缺什么,
    因此不会把"未分析"伪装成"已分析且无差异"。
    """
    by_id = {e.id: e for e in (evidence or [])}
    by_title = {_normalize(e.title): e for e in (evidence or []) if e.title}
    out_rows: list[NoveltyComparisonRow] = []
    results: list[Comparison] = []
    for row in rows:
        source = by_id.get((row.method or "").strip()) or by_title.get(_normalize(row.result))
        comparison = compare_prior(claim, source) if source is not None else compare_prior(claim, row)
        results.append(comparison)
        updated = row.model_copy(update={
            "difference": comparison.difference_text,
            "locator": row.locator or comparison.dimensions.get("applicability", ""),
        })
        out_rows.append(updated)
    return out_rows, results


def summarize(results: list[Comparison]) -> dict:
    """汇总: 各类关系计数 + 是否有可支撑"可能不同"的判定。"""
    counts: dict[str, int] = {}
    for item in results:
        counts[item.relation] = counts.get(item.relation, 0) + 1
    comparable = [r for r in results if r.relation != NOT_COMPARABLE]
    return {
        "total": len(results),
        "by_relation": counts,
        "comparable": len(comparable),
        "has_distinct": any(r.relation in (DIFFERENT, CONDITIONS_STRONGER,
                                           CONDITIONS_WEAKER) for r in results),
        "has_equivalent": any(r.relation == EQUIVALENT for r in results),
        "not_comparable": counts.get(NOT_COMPARABLE, 0),
    }


def render(claim: Claim, results: list[Comparison]) -> str:
    """供研究稿/工作台展示的逐项对照 (不夸大: 不可比就写不可比)。"""
    lines = [f"### 与已有工作对照 ({claim.id})", "",
             "| 已有结果 | 关系 | 逐项判定 |", "|---|---|---|"]
    for item in results:
        dims = "; ".join(f"{_DIM_LABEL.get(k, k)}={v}"
                         for k, v in item.dimensions.items() if v != UNKNOWN) or "—"
        lines.append(f"| {item.result[:60]} | {_RELATION_LABEL.get(item.relation, item.relation)} "
                     f"| {dims} |")
    summary = summarize(results)
    if summary["not_comparable"]:
        lines.append("")
        lines.append(f"其中 {summary['not_comparable']} 条因缺少原文条件或覆盖不足而无法比较, "
                     "只能作有界表述。")
    return "\n".join(lines)


__all__ = [
    "CONCLUSION_CONTAINED",
    "CONDITIONS_STRONGER",
    "CONDITIONS_WEAKER",
    "DIFFERENT",
    "EQUIVALENT",
    "NOT_COMPARABLE",
    "Comparison",
    "analyze_rows",
    "compare_prior",
    "compare_with_row",
    "render",
    "summarize",
]
