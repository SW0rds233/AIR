from __future__ import annotations

"""请求 → 问题契约 → 可检验候选问题。

两层职责:
1. `formulate()`: 读题目与资料摘要, 定研究类型 (机理/证明/经验因果/情景) 与允许的方法,
   给出 1–3 条研究路径; 判定不了就问一个会改变路线的澄清问题。
2. `generate_candidates()`: 按契约产出候选命题 —— 没有数据不产出经验因果候选,
   要求机理的问题也不被降级成"待收集数据的计划"。

候选必须显式区分: 已知结果 / 本研究差异 / 可核验性 / 预期难点;
无显式函数关系时不得臆造, 标记 verifiability 并请求补充。
"""

import json
import re
from dataclasses import dataclass, field

from src.research.capability import claim_type_for
from src.research.schemas import (
    ClaimQuestion,
    ClaimType,
    ProblemContract,
    Relation,
    ResearchPath,
    ResearchSpec,
    SourceSummary,
    StudyDesign,
    StudyPlan,
    TaskKind,
)

_DIRECTION_IN_CROWD = re.compile(
    r"在\s*(.{1,30}?)\s*(?:的)?(?:作用|影响|条件)(?:下)?[，,]?\s*(.{1,30}?)\s*"
    r"(?:会|将|如何|怎么|怎样)?\s*(?:的)?(?:变化|改变|变动|如何)"
)
_AS_X_INCREASES = re.compile(
    r"随着\s*(.{1,30}?)\s*(?:的)?\s*(?:增大|增加|上升|提高|变大|减小|减少|下降|降低)[，,]?\s*"
    r"(.{1,30}?)\s*(?:会|将|如何|怎么|怎样)?\s*(?:的)?(?:变化|改变|变动|如何)"
)
_EFFECT_ON = re.compile(r"(.{1,40}?)\s*对\s*(.{1,40}?)\s*的\s*影响")
_EN_EFFECT = re.compile(r"effect of\s+(.{1,40}?)\s+on\s+(.{1,40}?)(?:\?|\.|$)", re.IGNORECASE)
_EN_AFFECT = re.compile(r"how\s+does\s+(.{1,40}?)\s+affect\s+(.{1,40}?)(?:\?|\.|$)", re.IGNORECASE)

_DIRECTION_HINT = {
    "增大": "increasing", "增加": "increasing", "上升": "increasing", "提高": "increasing",
    "变大": "increasing", "减小": "decreasing", "减少": "decreasing", "下降": "decreasing",
    "降低": "decreasing",
}


@dataclass
class DirectionAnalysis:
    independent: str = ""
    dependent: str = ""
    research_type: str = "monotonicity"
    direction_hint: str = ""
    raw: str = ""
    matched: bool = False
    notes: list[str] = field(default_factory=list)


def parse_direction(text: str) -> DirectionAnalysis:
    text = (text or "").strip()
    analysis = DirectionAnalysis(raw=text)
    if not text:
        return analysis
    for pattern in (_DIRECTION_IN_CROWD, _AS_X_INCREASES, _EFFECT_ON, _EN_EFFECT, _EN_AFFECT):
        m = pattern.search(text)
        if not m:
            continue
        groups = [g.strip() for g in m.groups() if g]
        if len(groups) >= 2:
            analysis.independent = groups[0]
            analysis.dependent = groups[1]
            analysis.matched = True
            if pattern is _AS_X_INCREASES:
                for word, hint in _DIRECTION_HINT.items():
                    if word in text:
                        analysis.direction_hint = hint
                        break
            break
    if not analysis.matched:
        analysis.notes.append("未能从想法中识别自变量/因变量, 需要澄清")
    elif not analysis.dependent:
        analysis.notes.append("缺少因变量")
    return analysis


_APPLIED_CAUSE = re.compile(
    r"(.{1,30}?)\s*(?:导致|引起|造成|带来|使得)\s*(.{1,40}?)\s*(?:发生)?\s*"
    r"(?:变化|改变|增长|下降|提升|减少|波动)"
)
_APPLIED_IMPACT = re.compile(r"(.{1,30}?)\s*对\s*(.{1,40}?)\s*的?\s*影响")
_EN_IMPACT = re.compile(r"(?:impact|effect) of\s+(.{1,40}?)\s+on\s+(.{1,40}?)(?:\?|\.|$)",
                        re.IGNORECASE)
_LEAD_VERBS = re.compile(
    r"^(?:我想研究|我想要研究|我想|想要|希望|研究|探讨|分析|评估|关于|针对|请|帮我|请你)+"
)


def _clean_subject(text: str) -> str:
    return _LEAD_VERBS.sub("", (text or "").strip()).strip("，,。;；:：的 ")


@dataclass
class EffectDirection:
    """影响类问题的处理/结果对象 (如「A 对 B 的影响」)。"""

    treatment: str = ""
    outcome: str = ""
    matched: bool = False


def parse_effect_direction(text: str) -> EffectDirection:
    text = (text or "").strip()
    for pattern in (_APPLIED_CAUSE, _APPLIED_IMPACT, _EN_IMPACT):
        m = pattern.search(text)
        if m:
            groups = [g.strip() for g in m.groups() if g]
            if len(groups) >= 2:
                return EffectDirection(treatment=_clean_subject(groups[0]),
                                       outcome=_clean_subject(groups[1]), matched=True)
    return EffectDirection()


def _causal_candidate(analysis: EffectDirection, statement: str) -> ClaimQuestion:
    study = StudyPlan(
        design=StudyDesign.observational,
        treatment=analysis.treatment,
        outcome=analysis.outcome,
        counterfactual=f"未引入{analysis.treatment}的情形",
        notes="需补充研究对象、适用条件与公开数据来源后才能估计",
    )
    return ClaimQuestion(
        statement=statement or f"{analysis.treatment}对{analysis.outcome}的影响",
        category="applied",
        claim_type=ClaimType.causal,
        study=study,
        dependent=analysis.outcome,
        independent=analysis.treatment,
        known_results="未检索",
        difference="待与已有研究比较",
        verifiability="需要公开数据与研究设计 (DiD/RCT 等) 才能估计效应",
        difficulty="中高",
        recommended=True,
        rationale="基于想法中的技术/行业因果措辞生成的因果候选",
        raw=statement,
    )


def _symbol_of(name: str) -> str:
    """从名称抽取可用符号 (优先单个拉丁字母, 否则返回空)。"""
    letters = re.findall(r"(?<![A-Za-z])([A-Za-z])(?![A-Za-z])", name or "")
    if len(letters) == 1:
        return letters[0]
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", name or "")
    return tokens[0] if tokens else ""


def _rule_candidate(analysis: DirectionAnalysis, statement: str) -> ClaimQuestion:
    wrt = _symbol_of(analysis.independent)
    return ClaimQuestion(
        statement=statement or analysis.raw,
        category="monotonicity",
        dependent=analysis.dependent,
        independent=analysis.independent,
        wrt=wrt,
        direction=analysis.direction_hint,
        expr="",  # 无显式函数关系, 不臆造
        variables=[wrt] if wrt else [],
        variable_domains={wrt: "real"} if wrt else {},
        known_results="未检索",
        difference="待与已有结果比较",
        verifiability="需要给出因变量关于自变量的显式表达式后核验",
        difficulty="待评估",
        recommended=True,
        rationale="基于想法中的自变量/因变量抽取的单调性候选",
        raw=analysis.raw,
    )


def _llm_candidates(direction: str, llm) -> list[ClaimQuestion]:
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = (
        "把下面的研究想法拆成 1-3 个可检验的候选问题。若涉及技术/政策对行业或场景的"
        "影响, 用 category=causal 并给出研究设计; 否则优先单调性/不等式。只输出 JSON 数组, 每项字段:\n"
        '{"statement","category","expr","wrt","direction","dependent","independent",'
        '"variables":[],"variable_domains":{},"study":{},'
        '"known_results","difference","verifiability","difficulty","recommended","rationale"}\n'
        "规则:\n"
        "- category 只能取: monotonicity | inequality | identity | causal | predictive | "
        "scenario | descriptive | associational | normative\n"
        "  · monotonicity/inequality/identity: 可在声明域内由符号/求解器核验;\n"
        "  · causal/associational/descriptive/predictive: 必须有数据与研究设计, "
        "不得仅凭推导回答;\n"
        "  · scenario: 结论只在给定情景参数下成立, 必须写明参数;\n"
        "  · normative: 依赖价值判断, 需独立审查, 不得声称可自动判定;\n"
        "- category=causal 时 study 字段: {design: did|rct|iv|rdd|matching|observational, "
        "treatment, outcome, population, region, period, counterfactual, confounders:[]}; "
        "没有数据时不要编造 rows/data_ref;\n"
        "- 单调性必须给出 expr (因变量本身的显式符号表达式, 不是它的导数) 与 wrt (自变量符号), "
        "direction ∈ increasing/decreasing/nondecreasing/nonincreasing;\n"
        "- 例: 研究 f(x)=x**2 在正实数域上关于 x 的单调性 → expr=\"x**2\", wrt=\"x\", "
        "direction=\"increasing\";\n"
        "- 无法给出显式表达式时 expr 留空, 不得臆造;\n"
        "- variables 列出表达式中的符号, variable_domains 给出 real/integer/positive 等;\n"
        "- 把题目中的前提(如 x>0, 系数为正)写入 variable_domains, 不要遗漏;\n"
        "- 恰有一项 recommended=true;\n"
        "- known_results/difference/verifiability/difficulty 用简短中文填写。\n\n"
        f"研究想法: {direction}"
    )
    result = llm.invoke([SystemMessage(content="你是理论研究问题规划助手, 只输出 JSON。"),
                         HumanMessage(content=prompt)])
    text = result.content if hasattr(result, "content") else str(result)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    data = json.loads(m.group(0)) if m else []
    candidates: list[ClaimQuestion] = []
    for item in data[:3]:
        rel = {">=": Relation.ge, "<=": Relation.le, ">": Relation.gt, "<": Relation.lt,
               "==": Relation.eq}.get(item.get("relation"), Relation.custom)
        category = item.get("category", "monotonicity")
        study_raw = item.get("study", {}) or {}
        design = study_raw.get("design", "none")
        try:
            design_enum = StudyDesign(design)
        except ValueError:
            design_enum = StudyDesign.none
        study = StudyPlan(
            design=design_enum,
            treatment=study_raw.get("treatment", item.get("independent", "")),
            outcome=study_raw.get("outcome", item.get("dependent", "")),
            population=study_raw.get("population", ""),
            region=study_raw.get("region", ""),
            period=study_raw.get("period", ""),
            counterfactual=study_raw.get("counterfactual", ""),
            confounders=list(study_raw.get("confounders", []) or []),
            confounder_handling=study_raw.get("confounder_handling", ""),
            group_col=study_raw.get("group_col", ""),
            outcome_col=study_raw.get("outcome_col", ""),
            time_col=study_raw.get("time_col", ""),
            treated_label=study_raw.get("treated_label", ""),
            control_label=study_raw.get("control_label", ""),
            pre_label=study_raw.get("pre_label", ""),
            post_label=study_raw.get("post_label", ""),
            data_ref=study_raw.get("data_ref", ""),
            rows=list(study_raw.get("rows", []) or []),
        )
        claim_type = claim_type_for(category)
        candidates.append(ClaimQuestion(
            statement=item.get("statement", direction),
            category=category,
            claim_type=claim_type,
            lhs=item.get("lhs", ""), rhs=item.get("rhs", ""), relation=rel,
            expr=item.get("expr", ""), wrt=item.get("wrt", ""),
            direction=item.get("direction", ""),
            dependent=item.get("dependent", ""), independent=item.get("independent", ""),
            study=study,
            variables=item.get("variables", []),
            variable_domains=item.get("variable_domains", {}),
            known_results=item.get("known_results", ""),
            difference=item.get("difference", ""),
            verifiability=item.get("verifiability", ""),
            difficulty=item.get("difficulty", ""),
            recommended=bool(item.get("recommended", False)),
            rationale=item.get("rationale", ""),
            raw=direction,
        ))
    if candidates and not any(c.recommended for c in candidates):
        candidates[0].recommended = True
    return candidates


def _scenario_candidate(effect: EffectDirection, statement: str) -> ClaimQuestion:
    """情景候选: 结论只在给定参数下成立, 不得写成无条件因果。"""
    return ClaimQuestion(
        statement=statement or f"{effect.treatment}在给定情景下对{effect.outcome}的影响",
        category="scenario",
        claim_type=ClaimType.scenario,
        dependent=effect.outcome,
        independent=effect.treatment,
        known_results="未检索",
        difference="待与已有结果比较",
        verifiability="需要情景参数范围 (取值依据 + 边界) 才能给出条件性结论",
        difficulty="中",
        rationale="缺少理论模型与观测数据: 只做条件性情景分析",
        raw=statement,
    )


def generate_candidates(spec: ResearchSpec, llm=None) -> list[ClaimQuestion]:
    """按问题契约给出候选命题。

    契约决定候选类型: 没有数据就不产出经验因果候选, 要求机理的问题也不被降级成
    "待收集数据的计划"。`contract` 为空 (老规格) 时保持历史行为。
    """
    request = spec.direction or spec.original_request or spec.problem_statement
    if llm is not None:
        try:
            candidates = _llm_candidates(request, llm)
            if candidates:
                return candidates
        except Exception:  # noqa: BLE001
            pass
    kind = spec.contract.task_kind if spec.contract else None
    effect = parse_effect_direction(request)
    if kind is TaskKind.empirical_causal:
        return [_causal_candidate(effect, request)] if effect.matched else []
    if kind is TaskKind.scenario:
        if effect.matched:
            return [_scenario_candidate(effect, request)]
        analysis = parse_direction(request)
        return [_rule_candidate(analysis, request)] if analysis.matched else []
    if kind is None and effect.matched:
        return [_causal_candidate(effect, request)]
    analysis = parse_direction(request)
    if analysis.matched:
        return [_rule_candidate(analysis, request)]
    return []


def recommended_index(candidates: list[ClaimQuestion]) -> int:
    for idx, candidate in enumerate(candidates):
        if candidate.recommended:
            return idx
    return 0


# 方向性措辞: 出现时优先按"研究方向"处理 (文本里含 = 等符号也通常是定义而非待证命题)
_DIRECTION_MARKERS = (
    "研究", "想法", "我想", "想要", "希望", "探索", "如何变化", "怎么变化", "怎样变化",
    "随", "影响下", "的影响", "变化趋势", "趋势", "如何", "怎样",
)
_MECHANISM_MARKERS = (
    "机理", "机制", "原理", "物理过程", "作用路径", "为什么会", "为何", "本质",
)

_THEORY_REQUIRES = ["机制描述或可引用的模型来源", "变量与量纲", "适用边界与噪声/误差假设"]
_DATA_REQUIRES = ["可估计的观测数据 (处理/结果/时间字段)",
                  "识别策略 (DiD/RCT/IV/RDD 等)", "混淆与缺失处理"]
_SCENARIO_REQUIRES = ["情景参数范围及其依据", "参数边界与灵敏度检查"]

_ALLOWED_METHODS = {
    TaskKind.mechanism: ["符号推导", "量纲检查", "受限求解器核验", "反例搜索", "文献条件比对"],
    TaskKind.formal_proof: ["形式化编码", "受限求解器核验", "反例搜索", "证明义务拆解"],
    TaskKind.empirical_causal: ["识别策略设计", "只读数据查询", "估计与稳健性检查"],
    TaskKind.scenario: ["参数化情景推导", "条件比对", "灵敏度分析"],
}
_FORBIDDEN = {
    TaskKind.mechanism: "不得把机理问题降级为「待收集数据的经验估计」计划",
    TaskKind.formal_proof: "不得用自然语言论证替代可核验的形式化编码",
    TaskKind.empirical_causal: "不得在缺数据或未声明识别假设时声称已估计因果效应",
    TaskKind.scenario: ("不得把只在给定情景下成立的结论写成无条件结论; "
                        "缺数据时不得声称已估计因果效应"),
}


def _has_any(text: str, markers) -> bool:
    low = (text or "").lower()
    return any(m in low for m in markers)


def _decide_task_kind(text: str, summary: SourceSummary,
                      explicit_formal: bool) -> tuple[TaskKind, str]:
    """按"题目措辞 + 资料摘要"判定研究类型, 并给出可审计的依据。"""
    influence = parse_effect_direction(text).matched
    from src.research.classification import is_formal_question
    if is_formal_question(text) and not summary.has_observational_data:
        return TaskKind.formal_proof, "题面要求数学对象的存在性、构造或证明；尚需分别核验编码和论证"
    if explicit_formal and not influence:
        return TaskKind.formal_proof, "题目含显式关系/量词, 按形式化证明处理"
    if influence and summary.has_observational_data:
        note = f": {summary.data_note}" if summary.data_note else ""
        return TaskKind.empirical_causal, f"有可估计的观测数据{note}"
    if summary.has_theory_model:
        note = f": {summary.theory_model_note}" if summary.theory_model_note else ""
        return TaskKind.mechanism, f"有理论模型/机理描述, 不做经验估计{note}"
    if _has_any(text, _MECHANISM_MARKERS):
        return TaskKind.mechanism, "题目要求机理说明, 且没有观测数据"
    # "如何导致/为何导致"询问作用链，而不是要求估计一个经验处理效应。
    if re.search(r"(?:如何|怎样|为何|为什么|how|why).{0,35}(?:导致|引起|造成|使|lead|cause)",
                 text, re.IGNORECASE):
        return TaskKind.mechanism, "题目询问导致结果的作用链，先建立机理模型并核对文献"
    if influence:
        return TaskKind.scenario, "只给文献/无数据: 不做经验因果估计, 只给条件性情景结论"
    return TaskKind.scenario, "缺少可判定的问题类型信号"


def _build_paths(kind: TaskKind, text: str, summary: SourceSummary) -> list[ResearchPath]:
    """1–3 条候选研究路径, 每条说明能产生什么结论、还缺什么。"""
    effect = parse_effect_direction(text)
    subject = f"{effect.treatment} 对 {effect.outcome}" if effect.matched else "该问题"
    theory_kind = TaskKind.formal_proof if kind is TaskKind.formal_proof else TaskKind.mechanism
    if kind is TaskKind.formal_proof:
        return [ResearchPath(
            task_kind=theory_kind,
            statement=f"把「{text}」形式化, 逐步证明或构造反例",
            produces="可核验的证明或反例",
            requires=_THEORY_REQUIRES + ["可形式化的命题片段"],
            recommended=True,
        )]
    return [
        ResearchPath(
            task_kind=theory_kind,
            statement=f"从资料中的机制构造模型, 推导「{subject}」的条件性结论",
            produces="带假设与适用域的条件性结论 (可能附带反例)",
            requires=_THEORY_REQUIRES,
            recommended=kind is TaskKind.mechanism,
        ),
        ResearchPath(
            task_kind=TaskKind.empirical_causal,
            statement=f"用观测数据估计「{subject}」的因果效应",
            produces="带识别假设与区间估计的效应量",
            requires=_DATA_REQUIRES,
            recommended=kind is TaskKind.empirical_causal,
        ),
        ResearchPath(
            task_kind=TaskKind.scenario,
            statement=f"在明确给定的情景参数下分析「{subject}」",
            produces="只在该情景下成立的条件性结论",
            requires=_SCENARIO_REQUIRES,
            recommended=kind is TaskKind.scenario,
        ),
    ]


def _clarification_for(summary: SourceSummary, kind: TaskKind) -> str:
    """只在"换一种材料就会换一条路线"时问一个问题。"""
    if summary.has_theory_model or summary.has_observational_data or summary.has_sources:
        return ""
    if kind is TaskKind.formal_proof:
        return ""
    return ("这个问题的结论取决于手上有什么: 你能提供理论模型/机理描述, 还是可估计的观测数据, "
            "还是只授权我从文献推导? 三种选择会走不同的研究路径")


def formulate(request: str, topic: str = "", source_summary: SourceSummary | None = None,
              llm=None) -> ProblemContract:
    """请求 → 问题契约 (计划书 P0-2 / §3 接口)。

    只读题目与资料摘要: 先定研究类型与允许的方法, 再给出 1–3 条研究路径;
    研究类型无法判定且缺材料时给出**一个**会改变路线的澄清问题, 不猜。
    用户确认后由调用方写 `frozen_version` 冻结。
    """
    from src.research.problem_formulator import parse_questions

    text = (request or topic or "").strip()
    summary = source_summary or SourceSummary()
    explicit_formal = bool(parse_questions(text)) and not _has_any(text, _DIRECTION_MARKERS)
    kind, basis = _decide_task_kind(text, summary, explicit_formal)
    effect = parse_effect_direction(text)
    return ProblemContract(
        goal=text,
        task_kind=kind,
        objects=[o for o in (effect.outcome, effect.treatment) if o],
        available_sources=[summary.source_set_id] if summary.source_set_id else [],
        allowed_methods=list(_ALLOWED_METHODS[kind]),
        forbidden_substitutions=[_FORBIDDEN[kind]],
        success_criteria=[
            "结论能追溯到明确的前提、来源或验证输入",
            "关键义务闭合, 或如实列出未决项与缺什么",
        ],
        stop_criteria=[
            "资料覆盖不足或关键全文不可得: 说明覆盖限制, 不把无命中当成不存在",
            "预算耗尽: 输出部分结果与未决项",
        ],
        paths=_build_paths(kind, text, summary),
        clarification=_clarification_for(summary, kind),
        basis=basis,
    )


def build_spec_from_input(request: str, topic: str = "", project_id: str = "",
                          problem_id: str = "problem",
                          source_summary: SourceSummary | None = None) -> ResearchSpec:
    """区分"明确问题"与"研究方向", 并附上问题契约。

    有显式关系/量词且无方向措辞 → problem_statement; 否则 → direction。
    例外: 题面能被**精确形式化** (计数约束抽取成功) 时按明确问题处理 —— 这类文本
    常含"希望/要求"等方向措辞 ("管理员希望安排恰好 211 轮"), 但它给出的是可判定的
    精确问题; 判成"研究方向"会先去生成候选路线, 反而把用户引到不对题的路线上。
    """
    from src.research.design_feasibility import formulate_from_text
    from src.research.problem_formulator import parse_questions

    text = (request or topic or "").strip()
    has_direction_marker = _has_any(text, _DIRECTION_MARKERS)
    from src.research.classification import is_formal_question
    explicit = (bool(parse_questions(text)) and not has_direction_marker) \
        or formulate_from_text(text) is not None or is_formal_question(text)
    if not project_id:
        from src.utils.file_utils import sanitize_filename

        project_id = sanitize_filename(topic or text)[:30].strip("_") or "research"
    contract = formulate(request=request, topic=topic, source_summary=source_summary)
    from src.rag.relevance_filter import resolve_domain

    field = resolve_domain(text)
    return ResearchSpec(
        project_id=project_id,
        problem_id=problem_id,
        original_request=request or topic,
        problem_statement=text if explicit else "",
        direction="" if explicit else text,
        domain=field.domain,
        research_type=contract.task_kind.value,
        contract=contract,
    )
