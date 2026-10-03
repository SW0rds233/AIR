from __future__ import annotations

"""理论研究者 (P1): 生成证明计划、可审计步骤与验证请求。

只提出候选证明与验证请求; 不写入命题状态 (由执行器与规则层写入)。
"""

from dataclasses import dataclass, field

from src.research.schemas import (
    Assurance,
    Claim,
    ProofAttempt,
    ProofObligation,
    ProofStep,
    Relation,
)


@dataclass
class PlannedCheck:
    tool: str
    operation: str
    arguments: dict


@dataclass
class Plan:
    attempt: ProofAttempt
    checks: list[tuple[str, PlannedCheck]] = field(default_factory=list)  # (obligation_id, check)
    strategy: str = ""
    # 计划来源: rules (规则模板) / llm (LLM 结构化推导, 计划书 §7.1); 供审计区分
    source: str = "rules"
    # 新增的待核验子目标义务 (仅来自 LLM 推导); 它们进义务集合后同样需要核验
    proposed: list[ProofObligation] = field(default_factory=list)
    notes: str = ""


def _strategy_for(claim: Claim) -> str:
    if claim.expr and claim.wrt:
        return "monotonicity_differentiation"
    if claim.relation in (Relation.ge, Relation.le, Relation.gt, Relation.lt):
        if claim.relation in (Relation.gt, Relation.lt):
            return "nonnegative_difference_strict"
        return "nonnegative_difference"
    if claim.relation == Relation.eq:
        return "identity_simplification"
    return "direct"


def _steps_for(claim: Claim, strategy: str) -> list[ProofStep]:
    if strategy == "monotonicity_differentiation":
        return [
            ProofStep(index=1, statement=f"考察 {claim.expr} 关于 {claim.wrt} 的导数",
                      justification="单调性判定", rule="differentiation"),
            ProofStep(index=2, statement="判定导数在声明域内的符号",
                      justification="符号运算核验", rule="sign_analysis",
                      requires_conditions=[f"{v} 的域为 {d}"
                                           for v, d in claim.variable_domains.items()]),
            ProofStep(index=3, statement=f"由导数符号得出{claim.direction or '单调'}结论",
                      justification="单调性结论", rule="conclusion"),
        ]
    diff = f"({claim.lhs}) - ({claim.rhs})"
    steps = [
        ProofStep(index=1, statement=f"考察差 D = {diff}", justification="差式比较法",
                  rule="definition"),
        ProofStep(index=2, statement="化简 D 并判定其符号", justification="符号运算核验",
                  rule="algebraic_simplification",
                  requires_conditions=[f"{v} 的域为 {d}" for v, d in claim.variable_domains.items()]),
    ]
    if strategy == "nonnegative_difference_strict":
        steps.append(ProofStep(index=3, statement="检查等号是否可达, 判定严格性",
                               justification="严格不等式需排除取等", rule="strictness_check"))
    elif strategy == "nonnegative_difference":
        steps.append(ProofStep(index=3, statement="由 D ≥ 0 得结论, 并给出等号条件",
                               justification="非负性结论", rule="conclusion"))
    elif strategy == "identity_simplification":
        steps.append(ProofStep(index=3, statement="由差恒为 0 得恒等成立",
                               justification="恒等变换", rule="conclusion"))
    else:
        steps.append(ProofStep(index=3, statement="按声明规则推出结论", justification="直接推导",
                               rule="conclusion"))
    return steps


def _check_for(obligation: ProofObligation, claim: Claim, available: dict[str, bool]) -> PlannedCheck | None:
    """按义务种类构造验证请求。

    `available` 决定用哪个后端: 只有确实可用的后端才会被选中 (报告 unavailable
    的适配器必须影响可用动作集合, 不能反复调用不存在的工具)。
    """
    assumptions = {v: d for v, d in claim.variable_domains.items()}
    # 只在显式声明不可用时才禁用 (available 为空 dict 时保持兼容默认)
    def _avail(tool: str, default: bool = True) -> bool:
        return bool(available.get(tool, default))

    if obligation.kind == "estimate_effect":
        if not _avail("stats"):
            return None
        study = claim.study
        args = {
            "rows": study.rows, "data_ref": study.data_ref,
            # 结构化数据源 (CSV/SQLite) 的表名与过滤条件: 缺失时只读适配器会拒绝
            "table": study.data_table, "where": study.data_where,
            "group_col": study.group_col, "outcome_col": study.outcome_col,
            "time_col": study.time_col, "treated_label": study.treated_label,
            "control_label": study.control_label, "pre_label": study.pre_label,
            "post_label": study.post_label,
        }
        design = study.design.value
        if design == "did":
            return PlannedCheck("stats", "difference_in_differences", args)
        if design in ("rct", "observational", "matching"):
            return PlannedCheck("stats", "mean_difference", args)
        return None
    if obligation.kind == "prove_monotonicity":
        args = {
            "expr": claim.expr, "wrt": claim.wrt, "direction": claim.direction,
            "variables": claim.variables, "assumptions": assumptions,
        }
        if _avail("sympy"):
            return PlannedCheck("sympy", "prove_monotonicity", args)
        return None
    if obligation.kind == "prove_inequality":
        args = {
            "lhs": claim.lhs, "rhs": claim.rhs, "relation": claim.relation.value,
            "variables": claim.variables, "assumptions": assumptions,
        }
        if _avail("sympy"):
            return PlannedCheck("sympy", "prove_inequality", args)
        if _avail("z3"):
            # 前提必须真实传入: 空前提会让 z3 把弱化后的命题当成普遍成立
            premises = [f"{v} in Reals" for v in claim.variables]
            premises += [f"{v} {op} 0" for v, op in _domain_premises(claim.variable_domains)]
            return PlannedCheck("z3", "check_implication",
                                {"premises": premises,
                                 "conclusion": f"({claim.lhs}) {claim.relation.value} ({claim.rhs})",
                                 "variables": {v: "Real" for v in claim.variables}})
        return None
    if obligation.kind == "equality_condition":
        if not _avail("sympy"):
            return None
        return PlannedCheck("sympy", "equality_condition",
                            {"lhs": claim.lhs, "rhs": claim.rhs, "variables": claim.variables,
                             "assumptions": assumptions})
    if obligation.kind == "prove_identity":
        if not _avail("sympy"):
            return None
        return PlannedCheck("sympy", "prove_identity",
                            {"lhs": claim.lhs, "rhs": claim.rhs, "variables": claim.variables,
                             "assumptions": assumptions})
    if obligation.kind == "check_implication":
        args = obligation_detail_arguments(obligation)
        if not args:
            # 没有可核验编码 → 明确返回 None, 由调用方标为 unsupported, 不假装通过
            return None
        tool = args.pop("tool", "z3")
        if not _avail(tool):
            return None
        return PlannedCheck(tool, "check_implication", args)
    if obligation.kind == "evidence_support":
        # 证据支持义务由确定性规则判定 (无工具后端); 返回 None 表示"不走工具",
        # 由 loop 的规则分支处理。
        return None
    return None


def _domain_premises(domains: dict[str, str]) -> list[tuple[str, str]]:
    """把变量域翻译成 z3 可接受的前提约束。"""
    mapping = [
        (("positive", "pos"), ">"),
        (("nonnegative", "nonneg", "r>=0"), ">="),
        (("nonzero", "non_zero"), "!="),
    ]
    premises: list[tuple[str, str]] = []
    for var, domain in (domains or {}).items():
        low = str(domain).lower()
        for names, op in mapping:
            if low in names:
                premises.append((var, op))
                break
    return premises


def obligation_detail_arguments(obligation: ProofObligation) -> dict:
    """从义务的 detail 中取出可核验编码。

    detail 约定为 JSON 字符串 (含 premises/conclusion/variables/tool)。解析失败时
    返回空 dict, 由调用方标为 unsupported —— **绝不用空前提代替真实前提**,
    否则 z3 会把弱化后的命题当成普遍成立。
    """
    import json

    detail = (obligation.detail or "").strip()
    if not detail.startswith("{"):
        return {}
    try:
        data = json.loads(detail)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    if not data.get("conclusion"):
        return {}
    return data


def plan_proof(claim: Claim, obligations: list[ProofObligation],
               available: dict[str, bool] | None = None) -> Plan:
    available = available or {}
    strategy = _strategy_for(claim)
    attempt = ProofAttempt(
        target_claim_id=claim.id,
        target_version=claim.version,
        strategy=strategy,
        steps=_steps_for(claim, strategy),
        subgoals=[o.statement for o in obligations],
        status="in_progress",
    )
    checks: list[tuple[str, PlannedCheck]] = []
    for obligation in obligations:
        check = _check_for(obligation, claim, available)
        if check is not None:
            checks.append((obligation.id, check))
    return Plan(attempt=attempt, checks=checks, strategy=strategy, source="rules")


def plan_proof_deep(claim: Claim, obligations: list[ProofObligation],
                    available: dict[str, bool] | None = None, llm=None,
                    *, budget_exhausted=None) -> Plan:
    """带 LLM 结构化推导的证明规划 (计划书 §7.1)。

    规则: LLM 只能**增加**待核验内容, 不能替代规则路径。
    - 规则路径始终计算, 因此工具可用性判断 (哪些后端能承接哪些义务) 不受模型影响;
    - LLM 步骤并入 `attempt.steps` (仅在规则无步骤或模型给出更细步骤时), 全部标记为待核验;
    - LLM 子目标并入 `proposed`, 必须落成义务并经核验才可能影响命题状态;
    - 任何失败/非法输出只写 `notes`, 结果与纯规则路径等价。
    """
    from src.research.derivation import describe_plan, plan_derivation

    plan = plan_proof(claim, obligations, available)
    if llm is None:
        return plan

    derived = plan_derivation(
        claim, obligations, llm, budget_exhausted=budget_exhausted)
    plan.notes = " ".join(x for x in (describe_plan(derived) if derived.steps or derived.rejected
                                      else "", derived.notes) if x).strip()
    if not derived.ok:
        return plan

    # `source` 精确反映模型贡献: llm / llm_steps / llm_subgoals
    plan.source = derived.source
    if derived.strategy:
        plan.strategy = f"{plan.strategy}+{derived.strategy}"
    plan.attempt.strategy = plan.strategy
    llm_steps = derived.to_proof_steps()
    if llm_steps:
        # 规则步骤保留在前 (它们定义工具核验所需的形式结构), LLM 细化步骤接在后面
        offset = len(plan.attempt.steps)
        for step in llm_steps:
            step.index = offset + step.index
        plan.attempt.steps.extend(llm_steps)
    existing = {o.statement.strip() for o in obligations}
    for subgoal in derived.subgoals:
        if subgoal.statement in existing:
            continue
        existing.add(subgoal.statement)
        plan.proposed.append(subgoal.to_obligation(claim))
    plan.attempt.subgoals = [o.statement for o in obligations] + \
        [o.statement for o in plan.proposed]
    return plan


# 工具 -> 验证等级 (整体目标)
TOOL_ASSURANCE = {
    "sympy": Assurance.symbolic_checked,
    "z3": Assurance.solver_checked,
    "lean": Assurance.formally_checked,
    "informal_review": Assurance.informal_reviewed,
}
