from __future__ import annotations

"""问题形式化 (P1/P2)。

输入明确问题时先忠实形式化 (量词/变量域/关系), 不因原命题难证而静默替换。
无法可靠解析时把字段记入 unknown_fields, 交由调度器请求澄清, 不猜测填满。
"""

import re
from dataclasses import dataclass, field

from src.research.schemas import (
    Assumption,
    Claim,
    ClaimQuestion,
    ClaimType,
    Definition,
    Origin,
    ProofObligation,
    Relation,
    ResearchSpec,
)

_SUPERS = {"²": "**2", "³": "**3", "¹": "**1", "⁴": "**4", "⁰": "**0"}
_UNICODE_OPS = {
    "−": "-", "–": "-", "×": "*", "·": "*", "∙": "*", "÷": "/",
    "≤": "<=", "≥": ">=", "≠": "!=", "＝": "=", "＞": ">", "＜": "<", "≡": "==",
}
_DOMAIN_WORDS = {
    "正实数": "positive", "非负实数": "nonnegative", "实数": "real", "整数": "integer",
    "有理数": "rational", "复数": "complex", "自然数": "integer",
}
_CLAUSE_LEAD = re.compile(
    r"^(?:再|然后|并|且)?\s*(?:请)?(?:判断|证明|检验|验证|说明|给出|看|问|有|使得|满足|成立)?"
    r"\s*(?:是否|能否|如果|若)?\s*"
)
_TRAILING = re.compile(
    # 题面常把"给出严格证明/并说明理由"这类**指令**接在表达式后面。不剥掉它们, 右端
    # 会变成 "0, 并给出严格" 这种带中文的串, 交给工具就是语法错误 → 报告 unsupported,
    # 而命题其实完全可判定 (实测: `x**2 >= 0` 因右端残留而验不了)。
    r"[，,。;；\s]*(?:并|请|再|同时)?\s*(?:给出|说明|补充|列出)?\s*"
    r"(?:严格|完整|详细|简要|形式化)?\s*"
    r"(?:证明|反例|等号条件|等号成立条件|过程|推导|理由|依据|适用条件).*$"
)


@dataclass
class Formulation:
    assumptions: list[Assumption] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    obligations: list[ProofObligation] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    questions: list[ClaimQuestion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def normalize_math(text: str) -> str:
    t = text or ""
    for a, b in _SUPERS.items():
        t = t.replace(a, b)
    for a, b in _UNICODE_OPS.items():
        t = t.replace(a, b)
    return t.replace("−", "-").strip()


def _detect_quantifier(clause: str) -> tuple[dict[str, str], list[str], str]:
    """返回 (变量域, 变量名, 量词)。"""
    for word, dom in _DOMAIN_WORDS.items():
        idx = clause.find(word)
        if idx == -1:
            continue
        tail = clause[idx + len(word):]
        # 只取紧邻的变量声明段 (字母 + 分隔符), 避免把后续表达式中的函数名当作变量
        run_match = re.match(r"[\s]*([a-zA-Z\s、,，和]*)", tail)
        run = run_match.group(1) if run_match else ""
        names = list(dict.fromkeys(re.findall(r"[a-zA-Z]", run)))
        return {n: dom for n in names}, names, "forall"
    return {}, [], ""


def _split_relation(expr: str) -> tuple[str, Relation, str] | None:
    m = re.search(r"(>=|<=|!=|==|>|<|=)", expr)
    if not m:
        return None
    lhs, rhs = expr[:m.start()], expr[m.end():]
    rel_map = {
        ">=": Relation.ge, "<=": Relation.le, ">": Relation.gt, "<": Relation.lt,
        "==": Relation.eq, "=": Relation.eq, "!=": Relation.ne,
    }
    return lhs, rel_map[m.group(1)], rhs


def _clean_expr(expr: str) -> str:
    e = _CLAUSE_LEAD.sub("", expr or "").strip()
    e = _TRAILING.sub("", e).strip()
    return e.strip("，,。;；:： 、()（）")


def _infer_variables(expr: str) -> list[str]:
    names = re.findall(r"(?<![A-Za-z])([a-zA-Z])(?![A-Za-z])", expr or "")
    return list(dict.fromkeys(names))


_FUNCS_LOWER = {
    "sqrt", "exp", "log", "abs", "sin", "cos", "tan", "asin", "acos", "atan",
    "sinh", "cosh", "tanh", "max", "min", "sign", "floor", "ceiling", "factorial",
    "binomial", "rational", "pi", "e", "oo", "inf",
}


def fix_implicit_products(expr: str, variables: list[str]) -> str:
    """把隐式乘积 (2xy) 规范为显式乘积 (2*x*y), 只按已声明变量切分字母串。"""
    e = expr or ""
    e = re.sub(r"(?<=\d)(?=[a-zA-Z(])", "*", e)
    e = re.sub(r"(?<=\))(?=[a-zA-Z0-9(])", "*", e)

    def _split_run(m: re.Match) -> str:
        run = m.group(0)
        if run.lower() in _FUNCS_LOWER or run in variables:
            return run
        parts, i = [], 0
        ordered = sorted([v for v in variables if v], key=len, reverse=True)
        while i < len(run):
            hit = next((v for v in ordered if run.startswith(v, i)), None)
            if hit is None:
                return run
            parts.append(hit)
            i += len(hit)
        return "*".join(parts)

    return re.sub(r"[A-Za-z]+", _split_run, e)


def _make_claim(question: ClaimQuestion, origin: Origin, dependencies: list[str], notes: str,
                assumption_ids: list[str] | None = None) -> Claim:
    variables = question.variables or _infer_variables(f"{question.lhs} {question.rhs}")
    domains = dict(question.variable_domains) or {v: "real" for v in variables}
    for v in variables:
        domains.setdefault(v, "real")
    return Claim(
        statement=question.statement,
        claim_type=question.claim_type,
        scope_population=question.study.population,
        scope_region=question.study.region,
        scope_period=question.study.period,
        study=question.study,
        lhs=fix_implicit_products(question.lhs, variables),
        rhs=fix_implicit_products(question.rhs, variables),
        relation=question.relation,
        expr=fix_implicit_products(question.expr, variables) if question.expr else "",
        wrt=question.wrt,
        direction=question.direction,
        variables=variables,
        variable_domains=domains,
        assumption_ids=list(assumption_ids or []),
        origin=origin,
        dependencies=dependencies,
        notes=notes,
    )


def _rule_obligation(claim: Claim, ver: int, statement: str, kind: str,
                     detail: str = "") -> ProofObligation:
    return ProofObligation(
        statement=statement, kind=kind, acceptance_method="rule",
        claim_id=claim.id, claim_version=ver, detail=detail,
    )


def _applied_obligations(claim: Claim, ver: int,
                         method_available: dict[str, bool]) -> list[ProofObligation]:
    """非定义型命题的义务集合 (计划书 §7.2 / §7.4)。

    这些类型的共同点: **结论强度取决于证据与设计, 而不是推导**。因此义务里
    必须包含范围限定、证据分级, 以及该类型特有的前置条件 (预测需样本外方案,
    关联需说明不得表述为因果, 情景需声明参数, 规范需独立审查)。
    """
    stats_ok = method_available.get("stats", True)
    obligations = [
        ProofObligation(
            statement=f"在声明范围 (人群/地区/时期) 内限定结论: {claim.statement}",
            kind="scope_check", acceptance_method="rule",
            claim_id=claim.id, claim_version=ver,
            detail="应用类结论必须给出适用边界, 不得无限外推",
        ),
        _rule_obligation(claim, ver,
                         "给出支持性证据并分级 (至少一条带可信度分级的公开来源)",
                         "evidence_support",
                         "不得以'系统内部推导'充当外部证据"),
    ]
    if claim.claim_type == ClaimType.associational:
        obligations.append(_rule_obligation(
            claim, ver, "说明该结论是关联而非因果 (并列出未排除的混淆)",
            "control_confound", "关联关系不得表述为因果效应"))
    if claim.claim_type == ClaimType.predictive:
        obligations.append(_rule_obligation(
            claim, ver, "给出样本外评估方案与基线比较",
            "design_feasibility", "需说明划分方式、评价指标与基线模型"))
    if claim.claim_type == ClaimType.scenario:
        obligations.append(_rule_obligation(
            claim, ver, "声明情景参数及其取值依据",
            "design_feasibility", "情景参数是假设, 必须随结论一起给出"))
    if claim.claim_type == ClaimType.normative:
        obligations.append(_rule_obligation(
            claim, ver, "列出价值前提并交由独立审查确认",
            "evidence_support", "规范性结论不得由系统自动判定成立"))
    if claim.study.data_ref or claim.study.rows:
        obligations.append(ProofObligation(
            statement=f"在声明设计与数据下估计「{claim.study.treatment or claim.statement[:20]}」"
                      f"对「{claim.study.outcome or '结果'}」的效应",
            kind="estimate_effect", acceptance_method="stats" if stats_ok else "manual",
            claim_id=claim.id, claim_version=ver,
            detail="仅有设计与说明不足以给出效应, 需要真实数据",
        ))
    return obligations


def _obligations_for(claim: Claim, wants_equality: bool, method_available: dict[str, bool]) -> list[ProofObligation]:
    obligations: list[ProofObligation] = []
    use_sympy = method_available.get("sympy", True)
    method = "sympy" if use_sympy else "manual"
    # 义务绑定到具体命题版本: 命题换代后义务不得沿用
    ver = claim.version

    from src.research.classification import is_theoretical_claim
    theoretical = is_theoretical_claim(claim)
    if theoretical and not (claim.lhs and claim.rhs) and not (claim.expr and claim.wrt):
        return [ProofObligation(
            statement=f"在明确变量域、前提与量词下独立核查论证: {claim.statement}",
            kind="formal_argument", acceptance_method="informal_review",
            claim_id=claim.id, claim_version=ver,
            detail="尚无可核验编码；需补充逐步推导与定理条件，不能以文献数量或自述证明关闭")]

    if claim.claim_type == ClaimType.causal and not theoretical:
        stats_ok = method_available.get("stats", True)
        obligations.append(ProofObligation(
            statement=f"在声明设计与数据下估计「{claim.study.treatment}」对「{claim.study.outcome}」的效应",
            kind="estimate_effect",
            acceptance_method="stats" if stats_ok else "manual",
            claim_id=claim.id,
            claim_version=ver,
            detail=f"研究设计: {claim.study.design.value}; 数据来源: data_ref/rows",
        ))
        obligations.append(ProofObligation(
            statement="说明混淆因素处理方式",
            kind="control_confound",
            acceptance_method="rule",
            claim_id=claim.id,
            claim_version=ver,
            detail="须列出混淆因素或说明识别策略 (不得默认无混淆)",
        ))
        obligations.append(ProofObligation(
            statement="限定结论适用范围 (人群/地区/时期)",
            kind="scope_check",
            acceptance_method="rule",
            claim_id=claim.id,
            claim_version=ver,
            detail="应用类结论必须给出适用边界",
        ))
        obligations.append(ProofObligation(
            statement="给出支持性证据并分级",
            kind="evidence_support",
            acceptance_method="rule",   # 由确定性规则判定支持关系与分级, 不走工具
            claim_id=claim.id,
            claim_version=ver,
            detail="至少一条已判定支持关系且带可信度分级的公开文献/数据证据",
        ))
        # 计划书 §7.4: "声明设计 + 填了混淆说明 + 区间不跨零"不足以建立因果结论。
        # 识别假设 / 设计可行性 / 测量与缺失机制 / 误差结构必须各自成为独立义务。
        obligations.append(ProofObligation(
            statement="列出因果识别假设 (需要哪些不可检验假设才成立)",
            kind="identification_assumptions",
            acceptance_method="rule",
            claim_id=claim.id,
            claim_version=ver,
            detail="如平行趋势、排他性、无干扰、可忽略性等; 不得默认成立",
        ))
        obligations.append(ProofObligation(
            statement="说明研究设计在现有数据上的可行性",
            kind="design_feasibility",
            acceptance_method="rule",
            claim_id=claim.id,
            claim_version=ver,
            detail="说明为何该设计适用于本数据 (分组/前后期/工具/断点是否真实存在)",
        ))
        obligations.append(ProofObligation(
            statement="说明测量方案与缺失数据机制及处理方式",
            kind="measurement_and_missing",
            acceptance_method="rule",
            claim_id=claim.id,
            claim_version=ver,
            detail="测量误差来源, 缺失机制 (MCAR/MAR/MNAR) 与处理策略, 不得默认无缺失",
        ))
        obligations.append(ProofObligation(
            statement="说明误差结构估计方式",
            kind="error_structure",
            acceptance_method="rule",
            claim_id=claim.id,
            claim_version=ver,
            detail="聚类/异方差/自相关处理; 区间估计必须与该结构一致",
        ))
        return obligations

    if claim.claim_type != ClaimType.definitional and not theoretical:
        # 计划书 §7.2: 描述/关联/预测/情景/规范类问题**不能**靠符号推导成立,
        # 也不能因为"没有可核验的不等式"就没有义务 —— 否则会被当成无需证据的命题。
        return _applied_obligations(claim, ver, method_available)

    if claim.expr and claim.wrt:
        obligations.append(ProofObligation(
            statement=f"核验 {claim.expr} 关于 {claim.wrt} 的{claim.direction or '单调'}性",
            kind="prove_monotonicity",
            acceptance_method=method,
            claim_id=claim.id,
            claim_version=ver,
            detail="对因变量求导并判定符号; 严格单调还需排除驻点",
        ))
        return obligations
    if claim.relation in (Relation.ge, Relation.le, Relation.gt, Relation.lt):
        obligations.append(ProofObligation(
            statement=f"在声明域与假设下核验: {claim.statement}",
            kind="prove_inequality",
            acceptance_method=method,
            claim_id=claim.id,
            claim_version=ver,
            detail="严格不等式还需确认等号是否可达",
        ))
    elif claim.relation == Relation.eq:
        obligations.append(ProofObligation(
            statement=f"核验恒等: {claim.lhs} = {claim.rhs}",
            kind="prove_identity",
            acceptance_method=method,
            claim_id=claim.id,
            claim_version=ver,
        ))
    if wants_equality:
        obligations.append(ProofObligation(
            statement="求等号成立条件",
            kind="equality_condition",
            acceptance_method=method,
            claim_id=claim.id,
            claim_version=ver,
        ))
    return obligations


def parse_questions(text: str) -> list[ClaimQuestion]:
    """规则解析明确问题文本 → 形式化问题 (尽力而为, 不猜测缺失域)。"""
    norm = normalize_math(text)
    # 只在句末标点/换行处切分子问题, 不切分号 (分号后常接"给出证明/等号条件"等指令)
    clauses = [c.strip() for c in re.split(r"[。\n]+", norm) if c.strip()]
    questions: list[ClaimQuestion] = []
    for clause in clauses:
        # 这里的轻量解析器只支持标量表达式；附件溯源行、Markdown/LaTeX
        # 公式和带下标的函数若硬拆，会把哈希或 d_H(c_i,c_j) 截成伪命题。
        if (re.match(r"^\[附件\b.*\bsha256\s*=", clause)
                or re.search(r"[\\$_]", clause)
                or "<<<EXTERNAL_DATA_" in clause):
            continue
        domains, names, _quant = _detect_quantifier(clause)
        wants_eq = bool(re.search(r"等号(成立)?条件", clause))
        # "把 A 换成 B" / "改成" → 基于上一问题派生
        m_switch = re.search(r"(?:把|将)?\s*(>=|<=|!=|==|>|<|=)\s*(?:换|改)(?:成|为)\s*(>=|<=|!=|==|>|<|=)", clause)
        if m_switch and questions:
            prev = questions[-1]
            rel_map = {
                ">=": Relation.ge, "<=": Relation.le, ">": Relation.gt, "<": Relation.lt,
                "==": Relation.eq, "=": Relation.eq, "!=": Relation.ne,
            }
            questions.append(ClaimQuestion(
                statement=f"{prev.statement} 的严格/变体形式 ({m_switch.group(2)})",
                lhs=prev.lhs, rhs=prev.rhs, relation=rel_map[m_switch.group(2)],
                variables=list(prev.variables), variable_domains=dict(prev.variable_domains),
                wants_proof=True, derived_from=prev.derived_from or "",
                raw=clause,
            ))
            continue
        m_complex = re.search(r"(改成|换成|变为).{0,4}(复数|complex)", clause)
        if m_complex and questions:
            prev = questions[-1]
            questions.append(ClaimQuestion(
                statement=f"{prev.statement} (复数域变体)",
                lhs=prev.lhs, rhs=prev.rhs, relation=prev.relation,
                variables=list(prev.variables),
                variable_domains={v: "complex" for v in prev.variables},
                wants_proof=True, derived_from=prev.derived_from, raw=clause,
            ))
            continue
        split = _split_relation(clause)
        if not split:
            continue
        lhs_raw, rel, rhs_raw = split
        # 去掉关系符号之前混入的量词/变量声明, 仅保留表达式
        prefix_cut = len(lhs_raw)
        for word in _DOMAIN_WORDS:
            pos = lhs_raw.rfind(word)
            if pos != -1:
                prefix_cut = min(prefix_cut, pos + len(word))
        tail = lhs_raw[prefix_cut:] if prefix_cut < len(lhs_raw) else lhs_raw
        # 量词段之后常跟"变量列表 + 连接词" (如 "对所有实数 x 都有 x**2 >= x" 里的
        # "x 都有")。它们不是表达式的一部分, 但 `_CLAUSE_LEAD` 只剥开头的词, 于是
        # 解析出来的 lhs 会是 "x 都有 x**2" —— 交给工具就是一句语法错误, 表现为
        # "表达式语法错误/unsupported", 而真正的命题其实完全可判定 (实测)。
        # 只在连接词**后面确实还有内容**时才切, 否则会把 "x**2" 这种正常表达式切坏。
        connective = re.search(r"(都有|均有|有|满足|使得|使|则|成立)", tail)
        if connective:
            candidate = tail[connective.end():].strip()
            if candidate:
                tail = candidate
        # 去掉残留的变量声明/连接词 (如 "x、y，是否有 x**2+y**2" → "x**2+y**2")
        segments = re.split(r"(?:是否|能否|，|,|、|：|:)", tail)
        lhs = _clean_expr(segments[-1] if segments else tail)
        rhs = _clean_expr(rhs_raw)
        if not lhs or not rhs:
            continue
        variables = names or _infer_variables(f"{lhs} {rhs}")
        var_domains = domains or {v: "real" for v in variables}
        lhs = fix_implicit_products(lhs, variables)
        rhs = fix_implicit_products(rhs, variables)
        questions.append(ClaimQuestion(
            statement=f"对所有 {', '.join(variables) if variables else '?'}: {lhs} {rel.value} {rhs}",
            lhs=lhs, rhs=rhs, relation=rel,
            variables=variables, variable_domains=var_domains,
            wants_proof=True, wants_equality_condition=wants_eq, raw=clause,
        ))
    return questions


def obligations_for_claim(claim: Claim,
                          method_available: dict[str, bool] | None = None,
                          *, wants_equality: bool = False) -> list[ProofObligation]:
    """为一条**已有**命题构造它应有的证明义务 (与 `formulate` 同一实现)。

    为什么需要这个入口 (合并计划 §3.1 G06): 团队的形式化闭环要能处理"命题已经在
    库里、义务还没拆出来"的情形 (例如命题由建模/写作上游登记)。让角色自己写一套
    "该命题需要什么义务"就是第二份形式化判据 —— 同一命题在两条路径上会被拆成不同
    的待核验清单, 而核验是照着这份清单做的。
    """
    return _obligations_for(claim, wants_equality, method_available or {})


def formulate(spec: ResearchSpec, method_available: dict[str, bool] | None = None) -> Formulation:
    method_available = method_available or {}
    form = Formulation()
    questions = list(spec.questions)
    if not questions and spec.problem_statement:
        questions = parse_questions(spec.problem_statement)
    form.questions = questions
    if not questions:
        form.unknown_fields.append("questions")
        return form

    # 变量域假设 (去重), 并记录 变量 -> 假设ID 供依赖失效传播
    seen_dom: set[tuple[str, str]] = set()
    var_assumption: dict[str, str] = {}
    for q in questions:
        for var, dom in (q.variable_domains or {}).items():
            key = (var, dom)
            if key in seen_dom:
                continue
            seen_dom.add(key)
            asm = Assumption(
                statement=f"{var} ∈ {dom}", origin=Origin.user_assumption,
                applicability="问题声明的变量域",
            )
            form.assumptions.append(asm)
            var_assumption.setdefault(var, asm.id)

    id_map: dict[int, Claim] = {}
    for idx, q in enumerate(questions):
        if q.category == "monotonicity" and (not q.expr or not q.wrt):
            form.unknown_fields.append(f"monotonicity_expr:{q.statement[:40]}")
            form.notes.append(
                f"候选「{q.statement[:40]}」缺少因变量关于自变量的显式表达式, 无法核验单调性"
            )
            continue
        deps: list[str] = []
        origin = Origin.proposed
        notes = ""
        if q.raw and re.search(r"(换|改)(?:成|为)", q.raw):
            # 派生自前一问题
            if idx > 0 and (idx - 1) in id_map:
                deps = [id_map[idx - 1].id]
            origin = Origin.derived
            notes = "由原命题经强度/范围变更派生; 与原命题分别保存, 不得静默替换"
        claim_assumptions = [var_assumption[v] for v in
                             (q.variables or _infer_variables(f"{q.lhs} {q.rhs}"))
                             if v in var_assumption]
        claim = _make_claim(q, origin, deps, notes, claim_assumptions)
        id_map[idx] = claim
        form.claims.append(claim)
        form.obligations.extend(_obligations_for(claim, q.wants_equality_condition, method_available))
    return form


def propose_candidate_questions(direction: str, llm=None) -> list[ClaimQuestion]:
    """研究方向输入: 生成可检验候选问题。无 LLM 时返回空 (不臆造)。"""
    if llm is None:
        return []
    import json

    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = (
        "把下面的研究方向拆成 1-3 个可严格核验的候选问题。只输出 JSON 数组, "
        '每项形如 {"statement","lhs","rhs","relation","variables":[],"variable_domains":{}}。'
        "relation 取值 >= <= > < == !=。无法确定时不要臆造字段。\n\n"
        f"方向: {direction}"
    )
    try:
        result = llm.invoke([SystemMessage(content="只输出 JSON。"), HumanMessage(content=prompt)])
        text = result.content if hasattr(result, "content") else str(result)
        m = re.search(r"\[.*\]", text, re.DOTALL)
        data = json.loads(m.group(0)) if m else []
        out = []
        for item in data:
            rel = {"<=": Relation.le, ">=": Relation.ge, "<": Relation.lt, ">": Relation.gt,
                   "==": Relation.eq, "!=": Relation.ne}.get(item.get("relation"), Relation.custom)
            out.append(ClaimQuestion(
                statement=item.get("statement", ""), lhs=item.get("lhs", ""), rhs=item.get("rhs", ""),
                relation=rel, variables=item.get("variables", []),
                variable_domains=item.get("variable_domains", {}),
            ))
        return out
    except Exception:  # noqa: BLE001
        return []
