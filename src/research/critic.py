from __future__ import annotations

"""反驳与验证者 (P1): 寻找反例、检查未满足条件与逻辑跳跃。

不接收"应当通过"的结论暗示; 只按命题与假设本身工作。
"""

from src.research.schemas import Claim, ProofAttempt, Relation
from src.research.theorist import PlannedCheck


def counterexample_check(claim: Claim, available: dict[str, bool] | None = None) -> PlannedCheck | None:
    available = available or {}
    if not available.get("sympy", True):
        return None
    if claim.expr and claim.wrt:
        return PlannedCheck("sympy", "find_counterexample", {
            "expr": claim.expr, "wrt": claim.wrt, "direction": claim.direction,
            "variables": claim.variables,
            "assumptions": dict(claim.variable_domains),
        })
    if claim.relation in (Relation.gt, Relation.lt, Relation.ge, Relation.le, Relation.ne):
        return PlannedCheck("sympy", "find_counterexample", {
            "lhs": claim.lhs, "rhs": claim.rhs, "relation": claim.relation.value,
            "variables": claim.variables,
            "assumptions": dict(claim.variable_domains),
        })
    return None


# 需要附带条件的关键规则 (错用定理/漏条件)。
# 触发词必须是**真的在指这件事**: 例如"排除驻点"里的"除"不是除法,
# 早期实现按子串匹配, 于是任何含"排除"的推导都会凭空产生一条"需声明除数非零"。
_RULE_CONDITIONS = (
    ("division", "nonzero", "使用除法需声明除数非零"),
    ("jensen", "convex", "使用 Jensen 不等式需凸性与定义域条件"),
    ("swap_limit", "uniform", "交换极限与积分/求和需相应适用条件"),
    ("sqrt", "nonnegative", "开方需被开方量非负/实值"),
)

# 中文触发词 -> (用于确认"确实在讲该规则"的判定函数, 提示)
_CN_TRIGGERS = (
    ("jensen", "凸", "使用 Jensen 不等式需凸性与定义域条件"),
    ("交换极限", "一致收敛", "交换极限需一致收敛等适用条件"),
    ("开方", "非负", "开方需被开方量非负/实值"),
)


def _mentions_division(text: str) -> bool:
    """文本是否**真的**在讨论除法 (而不是"排除/去除/除此之外")。"""
    idx = 0
    while True:
        pos = text.find("除", idx)
        if pos < 0:
            return False
        before = text[max(0, pos - 1):pos]
        after = text[pos + 1:pos + 2]
        # "排除/去除/除此/除…之外" 都是常见非除法用法
        if before in ("排", "去") or after in ("外", "此", "了"):
            idx = pos + 1
            continue
        return True


def review_attempt(attempt: ProofAttempt) -> list[str]:
    """审查证明步骤中的漏条件/循环等硬问题, 返回问题列表。"""
    issues: list[str] = []
    text = " ".join(
        f"{s.statement} {s.justification} {s.rule} {' '.join(s.requires_conditions)}"
        for s in attempt.steps
    )
    low = text.lower()
    if _mentions_division(text) and "nonzero" not in low and "非零" not in text:
        issues.append("使用除法需声明除数非零")
    for trigger, condition, message in _RULE_CONDITIONS[1:]:
        if trigger in low and condition not in low:
            issues.append(message)
    for trigger, condition, message in _CN_TRIGGERS:
        if trigger in text and condition not in text:
            issues.append(message)
    # 循环证明: 步骤引用自身目标
    for step in attempt.steps:
        if attempt.target_claim_id and attempt.target_claim_id in step.justification:
            issues.append("步骤引用了目标命题自身 (疑似循环论证)")
    return issues


def issues_to_obligations(attempt: ProofAttempt, claim: Claim,
                          existing_statements: set[str] | None = None):
    """把审查意见转为**新的证明义务** (计划书 §5.5)。

    计划书要求反方审查的每条意见"指向具体对象/步骤并产生新的义务,
    而不是只输出一个总分"。生成的义务仍需通过工具核验才能关闭,
    因此审查意见不会直接改变任何命题状态。

    这些义务是 `required=False`: 它们是**已知的实践性保留意见**, 会随交付物
    一起呈现 (计划书 §9.5), 但不会把已经满足既有门槛的结论重新判为不可交付。
    """
    from src.research.schemas import Coverage, ProofObligation

    existing = {s.strip() for s in (existing_statements or set())}
    obligations = []
    for issue in review_attempt(attempt):
        statement = f"独立审查提出: {issue}"
        if statement.strip() in existing:
            continue
        obligations.append(ProofObligation(
            statement=statement,
            kind="informal_review",
            acceptance_method="informal_review",   # 需人工/独立审查确认, 不走工具
            claim_id=claim.id,
            claim_version=claim.version,
            detail=f"来源: 推导尝试 {attempt.id} ({attempt.strategy}); 原意见: {issue}",
            coverage=Coverage.subgoal,
            required=False,
        ))
    return obligations


