from __future__ import annotations

"""推理动作的内核 (合并计划 §7.1 / M2「按职责抽取」)。

这一层解决的是什么
------------------
合并计划 §7.1 要求把 `research/loop.py` 里属于推理的动作**按职责抽走**, 而不是把
`TheoryEngine` 整体包成一个子智能体; §2 也明确 `TheoryEngine` "不能继续作为主控
下面第二个全权总控"。

但抽取必须**逐字等价**: `loop.py` 的 `_act_plan_proof` / `_act_derive_step` 里混着
三件不同性质的东西 ——

1. **纯计算**: 由命题与义务算出证明计划、推导步骤、待核验义务 (无 IO、无状态);
2. **持久化**: 把结果与事件写进存储 (`append_step`);
3. **状态判定**: 按记录重算命题状态 (`_reconcile_claim`, 判定层, 保持零 LLM)。

本模块只取第 1 件, 并把结果连同**幂等键与事件载荷**一起返回。这样:

- 引擎的 `_act_*` 变成"调内核 + 落盘", 计算只有一份实现 (不会出现两套推导);
- 角色 (`ReasoningAgent`) 可以直接调内核, 于是"团队路径"与"旧引擎路径"在推导上
  **同源** —— 差异测试因此是有意义的 (`tests/test_reasoning_kernel.py`);
- 判定层完全不动: 内核不写命题真值, 不产生验证等级。

`ReasoningOutcome.events` 的载荷字段与 `loop.py` 原实现逐字一致 (例如
`derive_step` 的 `formal_gap` / `review_obligations` / `adversarial_checked`),
因为 `research/logging_schema.py` 的 `EVENT_SPECS` 会校验必需键 —— 少一个键就会被
记成契约异常, 界面显示为 0。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.research.adversarial import review_claim_with_obligations
from src.research.critic import counterexample_check, issues_to_obligations
from src.research.schemas import (
    ObligationStatus,
    ProofAttempt,
    ProofObligation,
    ProofStep,
    hash_payload,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from src.research.schemas import Claim

__all__ = [
    "ReasoningOutcome",
    "derive_steps_for",
    "design_steps_for",
    "is_design_claim",
    "plan_proof_for",
    "seek_counterexample_for",
]


# ----------------------------------------------------------------------
# 结果
# ----------------------------------------------------------------------
@dataclass
class ReasoningOutcome:
    """一次推理动作的**纯**结果。

    `writes` / `events` / `idempotency_key` 与 `loop.append_step` 的入参形状一致,
    便于引擎直接落盘; 角色侧则只用 `summary` 与 `payload` 构造候选变更。
    """

    ok: bool = False
    #: (kind, object_id, data) —— 直接交给 `store.submit_step`
    writes: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    #: (event_type, payload)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    idempotency_key: str = ""
    #: 给主控/界面看的可读说明 (不含科学结论)
    notes: list[str] = field(default_factory=list)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    #: 该动作是否推进了研究 (决定账本 status 与是否触发失效重检)
    progressed: bool = False


# ----------------------------------------------------------------------
# 设计/计数存在性: 推导步骤 = 证书判定链 (S1)
# ----------------------------------------------------------------------
def is_design_claim(claim: Claim | None) -> bool:
    """该命题是否为"设计/计数存在性"类 (由题面参数决定, 不看措辞)。

    这个判据必须与 `loop.py` 用的**同一个**实现, 否则两条路径会对同一命题走不同
    分支 (一条出证书链、一条出通用代数步骤), 差异测试立刻失败。
    """
    if claim is None:
        return False
    from src.research.design_feasibility import is_design_claim as _impl

    return bool(_impl(claim))


def design_steps_for(claim: Claim, *, obligations: list[ProofObligation],
                     step_builder: Callable[[Any, Any], list[ProofStep]]
                     ) -> ReasoningOutcome:
    """设计/计数命题的推导步骤 = **确定性重算**的证书判定链。

    步骤内容不含自由生成的数学步骤, 因此不产生新的"反方审查"义务 —— 参数的适用性
    已由 `design_necessity` 义务的证书逐条覆盖。
    """
    from src.research import design_feasibility as df
    from src.research.store import KIND_ATTEMPT

    params = df.DesignParams(v=claim.design_v or 0, k=claim.design_k or 0,
                             lam=claim.design_lambda or 1,
                             b=claim.design_b, r=claim.design_r)
    report = df.check(params)
    certificate = report.certificate_dict()
    certificate["sha256"] = report.certificate_digest()
    own = [o for o in obligations
           if o.claim_id == claim.id and o.kind == df.OBLIGATION_KIND]
    steps = step_builder(certificate, own[0] if own else None)
    attempt = ProofAttempt(
        id=f"pf-{claim.id}-v{claim.version}", target_claim_id=claim.id,
        target_version=claim.version, strategy="design_necessity_certificate",
        steps=steps, subgoals=[o.statement for o in obligations
                               if o.claim_id == claim.id],
        status="complete",
    )
    return ReasoningOutcome(
        ok=True,
        writes=[(KIND_ATTEMPT, attempt.id, attempt.model_dump(mode="json"))],
        events=[("derive_step", {"claim_id": claim.id, "steps": len(steps),
                                 "formal_gap": False, "review_obligations": 0,
                                 "adversarial_checked": 0, "adversarial_hits": 0,
                                 "adversarial": "证书判定链 (无需自由推导)"})],
        idempotency_key=f"derive:{claim.id}:{report.certificate_digest()[:16]}",
        summary=f"{len(steps)} 步证书判定链",
        # 键名与事件载荷分工明确: `steps` 是**数量** (与 `derive_step` 事件一致),
        # `step_items` 才是步骤本身; 两者同名会让下游读错类型 (踩过一次)。
        payload={"strategy": "design_necessity_certificate",
                 "steps": len(steps),
                 "step_items": [s.model_dump(mode="json") for s in steps],
                 "certificate": certificate},
        progressed=True,
    )


def build_design_steps(certificate: dict[str, Any],
                       obligation: ProofObligation | None) -> list[ProofStep]:
    """把证书判定链转成 `ProofStep` 列表 (逐条可反查定理与输入)。"""
    from src.research import design_feasibility as df

    steps: list[ProofStep] = []
    for index, (text, rule) in enumerate(
            df.theorem_application_steps(certificate), start=1):
        steps.append(ProofStep(
            index=index, statement=text, justification="由证书重算, 可反查",
            rule=rule,
            obligation_ref=(_ref_of(obligation) if obligation is not None else None)))
    return steps


def _ref_of(obligation: ProofObligation):
    from src.research.schemas import ObjectRef

    return ObjectRef(id=obligation.id, version=obligation.version)


# ----------------------------------------------------------------------
# 通用推导步骤 + 反方审查
# ----------------------------------------------------------------------
def derive_steps_for(claim: Claim, *, attempt: ProofAttempt,
                     obligations: list[ProofObligation],
                     evidence: list[Any],
                     ) -> ReasoningOutcome:
    """生成可审查的推导步骤, 并把反方审查意见转成**新的证明义务**。

    合并计划 §7.1 / §5.5: 审查意见不直接改命题状态, 而是转为待核验义务。
    应用/数据类命题没有可符号推导的形式化片段, 但**反方审查仍然适用** —— 这类命题
    会补一条显式的"尚无形式化片段"步骤 (早期实现因"没有结构化形式"直接返回失败,
    导致八项检查在实践中从未对应用类命题执行过)。
    """
    from src.research.store import KIND_ATTEMPT, KIND_OBLIGATION

    formal_gap = False
    if not attempt.steps:
        formal_gap = True
        attempt.steps = [ProofStep(
            index=1,
            statement=f"该命题尚无形式化片段, 无法给出符号推导 (陈述: {claim.statement[:80]})",
            justification="尚无可核验的形式化编码，需要补充适用条件与核验方法；不代表命题为假",
            rule="adversarial_review_only",
        )]

    existing = {o.statement for o in obligations if o.claim_id == claim.id}
    rule_obligations = issues_to_obligations(attempt, claim,
                                             existing_statements=existing)
    existing |= {o.statement for o in rule_obligations}
    # 计划书 §7.2: 反方审查清单八项必须都跑过, 并在审计里留下每项结论
    review, review_obligations = review_claim_with_obligations(
        claim, evidence=[e for e in evidence if e.claim_id == claim.id],
        existing_statements=existing)
    new_obligations = list(rule_obligations) + list(review_obligations)
    # 一次推导尝试到此已完整记录 (步骤 + 审查意见 + 新增义务), 因此标记 complete:
    # 这只表示"记录完整", **不代表结论成立** —— 状态仍由中央规则按验证记录计算。
    attempt.status = "complete"
    writes: list[tuple[str, str, dict[str, Any]]] = [
        (KIND_ATTEMPT, attempt.id, attempt.model_dump(mode="json"))]
    writes += [(KIND_OBLIGATION, o.id, o.model_dump(mode="json"))
               for o in new_obligations]
    notes: list[str] = []
    if formal_gap:
        notes.append(f"{claim.id}: 无可形式化片段, 已改为以反方审查清单产出待核验义务")
    if new_obligations:
        notes.append(f"{claim.id}: 独立审查提出 {len(new_obligations)} 条待核验义务 "
                     f"({review.summary()})")
    return ReasoningOutcome(
        ok=True,
        writes=writes,
        events=[("derive_step", {"claim_id": claim.id, "steps": len(attempt.steps),
                                 "formal_gap": formal_gap,
                                 "review_obligations": len(new_obligations),
                                 "adversarial_checked": review.checked,
                                 "adversarial_hits": len(review.hits),
                                 "adversarial": review.digest()})],
        idempotency_key=f"derive:{claim.id}:"
                        f"{hash_payload([s.statement for s in attempt.steps])}",
        notes=notes,
        summary=(f"{len(attempt.steps)} 步推导; 反方审查 {review.checked} 项, "
                 f"新增 {len(new_obligations)} 条待核验义务"),
        payload={"steps": len(attempt.steps),
                 "step_items": [s.model_dump(mode="json") for s in attempt.steps],
                 "formal_gap": formal_gap,
                 "review_obligations": [o.model_dump(mode="json")
                                        for o in new_obligations],
                 "adversarial": review.digest()},
        progressed=True,
    )


# ----------------------------------------------------------------------
# 证明计划
# ----------------------------------------------------------------------
def plan_proof_for(claim: Claim, *, obligations: list[ProofObligation],
                   available: dict[str, bool] | None = None,
                   llm: Any = None,
                   budget_exhausted: Callable[[], bool] | None = None,
                   already_planned: bool = False,
                   reuse_plan: Any = None,
                   on_plan: Callable[[Any], None] | None = None,
                   ) -> ReasoningOutcome:
    """为命题制定证明计划。

    规则路径**始终**计算 (工具可用性判断不受模型影响); 有 LLM 且该命题版本尚未做过
    模型推导时, 走 `plan_proof_deep`, 它只能**增加**待核验内容, 不能替代规则路径。

    `reuse_plan`: 调用方 (引擎) 已缓存且版本匹配的计划。传进来时**复用同一对象** ——
    否则引擎缓存的是引擎算的那份、内核算的是另一份, 两边会在步骤上分叉, 而且
    引擎的"同一版本只调用一次模型"去重会变成两份真相。这是"抽取不得改变行为"的
    具体要求。

    `on_plan`: 内核算出计划后回调一次。引擎据此把**内核实际用的那份**写回自己的缓存
    (`_plans`), 避免"引擎缓存一份、内核算一份"。
    """
    from src.research.store import KIND_ATTEMPT, KIND_OBLIGATION
    from src.research.theorist import plan_proof, plan_proof_deep

    open_obligations = [o for o in obligations
                        if o.claim_id == claim.id
                        and o.status == ObligationStatus.open]
    plan = reuse_plan
    used_llm = False
    if plan is None:
        plan = plan_proof(claim, open_obligations, available)
        if llm is not None and not already_planned and not (
                budget_exhausted is not None and budget_exhausted()):
            plan = plan_proof_deep(claim, open_obligations, available, llm=llm,
                                   budget_exhausted=budget_exhausted)
            used_llm = True
        if on_plan is not None:
            on_plan(plan)
    attempt = plan.attempt
    attempt.id = f"pf-{claim.id}-v{claim.version}"
    notes = [f"{claim.id}: {plan.notes}"] if plan.notes else []
    return ReasoningOutcome(
        ok=True,
        # 计划本身**不是**一次推导记录: 步骤由 `derive_steps_for` 负责写入。
        # 这里只落计划与它提出的义务 (与 `_act_plan_proof` 的写入集合一致)。
        writes=[(KIND_ATTEMPT, attempt.id, attempt.model_dump(mode="json"))]
        + [(KIND_OBLIGATION, o.id, o.model_dump(mode="json")) for o in plan.proposed],
        events=[("plan_proof", {"claim_id": claim.id, "strategy": plan.strategy,
                                "source": plan.source,
                                "steps": len(attempt.steps),
                                "subgoals": len(plan.proposed)})],
        idempotency_key=f"plan:{claim.id}:{plan.strategy}",
        notes=notes,
        summary=(f"策略 {plan.strategy} ({plan.source}), "
                 f"{len(attempt.steps)} 步, {len(plan.proposed)} 个子目标"),
        payload={"strategy": plan.strategy, "source": plan.source,
                 "used_llm": used_llm,
                 "checks": [c.tool for _, c in plan.checks],
                 "attempt": attempt.model_dump(mode="json"),
                 "proposed": [o.model_dump(mode="json") for o in plan.proposed]},
        progressed=True,
    )


# ----------------------------------------------------------------------
# 反例搜索 (候选)
# ----------------------------------------------------------------------
def seek_counterexample_for(claim: Claim, *, available: dict[str, bool] | None = None
                            ) -> ReasoningOutcome:
    """判断该命题能否编码成反例搜索; 返回**可核验的检查项**而不是执行它。

    为什么只到"候选": 执行工具与写验证记录属于判定层 (`loop._run_tool` +
    `_reconcile_claim`), 内核不碰; 角色侧拿到 `payload["check"]` 后提交候选,
    由判定层决定是否采纳与如何更新状态。
    """
    check = counterexample_check(claim, available)
    if check is None:
        return ReasoningOutcome(
            ok=False, summary="当前命题形式无法编码反例搜索",
            notes=["先补出可核验的结构化形式"],
            payload={"reason": "unsupported"})
    return ReasoningOutcome(
        ok=True,
        summary=f"可执行反例搜索: {check.tool}",
        payload={"tool": check.tool, "check": check.model_dump(mode="json")
                 if hasattr(check, "model_dump") else str(check)},
        progressed=False,   # 搜索本身由判定层执行, 内核不改状态
    )


@dataclass
class CounterexampleVerdict:
    """反例搜索结果的**纯**判定: 下一步该怎样、该记什么。

    `reason` 会进入路线失败记录 (`RouteManager.record_failure`), 因此必须与引擎里的
    逐字字符串一致 —— 改措辞会让"同一输入不重复搜索"的判据失效。
    """

    action: str                    # refute / record_test / blocked / failed
    scientific: bool = False       # 是否属于"科学性结论" (而不是运行/能力问题)
    reason: str = ""
    note: str = ""
    recovery_condition: str = ""


def counterexample_verdict_for(status: Any,
                               counterexample: dict[str, Any] | None,
                               detail: str = "") -> CounterexampleVerdict:
    """把一次反例搜索的验证状态映射为义务/命题层面的处置 (零 LLM)。

    依据 (合并计划 §7.3: 统一验证结果语义):
    - 找到满足前提的反例 → 命题被反驳 (`refute`);
    - 工具通过但没给反例 → 只是**有限测试证据**, 不构成证明 (`record_test`);
    - 报"找到反例"却给不出可回代的反例 → 记 blocked, 不得当作数学反驳;
    - 其余一律按运行/能力问题记录, 不改命题真值。
    """
    from src.research.schemas import ValidationStatus

    if status == ValidationStatus.counterexample_found:
        if not counterexample:
            return CounterexampleVerdict(
                action="blocked", scientific=False,
                reason=f"未给出可回代的反例: {detail}",
                note="补出满足前提的反例后再判定为假")
        return CounterexampleVerdict(
            action="refute", scientific=True,
            reason=f"找到反例 {counterexample}",
            note=f"反例: {counterexample}",
            recovery_condition="改为弱化命题或增加条件")
    if status == ValidationStatus.verified:
        return CounterexampleVerdict(
            action="record_test", scientific=False,
            reason="反例搜索未发现反例 (有限测试证据, 不构成证明)",
            note="反例搜索未发现反例 (有限测试证据, 不构成证明)")
    return CounterexampleVerdict(
        action="failed", scientific=False,
        reason=detail or getattr(status, "value", str(status)),
        recovery_condition="改用其他后端或缩小问题范围")


#: 同一输入下重复搜索不会得到不同结果, 这些失败种类视为"已试过"。
COUNTEREXAMPLE_REPEAT_FAILURES: frozenset[str] = frozenset({
    "unsupported", "unknown", "no_counterexample_found", "read_failed",
})


@dataclass
class ObligationDisposition:
    """一次工具核验结果 → **义务处置**的纯映射 (合并计划 §7.4 / M2 剩余)。

    为什么留在这里而不是搬进角色: `ValidationStatus`/`ObligationStatus` 的对应关系
    是判定层的一部分 (M2 的边界明确写了 `_act_check_step`/`_run_tool`/`_reconcile_claim`
    **保持零 LLM 且是唯一状态写入点**)。抽出来的只有"结果 → 该怎么处置"的纯函数:
    角色/引擎负责"执行工具", 这里负责"结果意味着什么", 二者共用同一份实现。
    """

    checked: bool = True
    action: str = "blocked"            # closed / refuted / blocked / rejected
    validation_status: Any = None      # ValidationStatus (职责上由调用方提供的枚举)
    status: Any = None                 # VerificationStatus (用于措辞与失败种类)
    reason: str = ""                   # 进路线失败记录 (措辞必须与旧实现一致)
    note: str = ""                     # 进 notes 的一句话 (可为空)
    scientific: bool = False
    recovery_condition: str = ""
    #: 是否需要随后的**领域化**收尾 (引擎执行, 判定层不写状态):
    apply_estimate: bool = False       # estimate_effect 成功 → 把估计写入内存态
    record_equality: bool = False      # equality_condition 成功 → 追加等式命题
    keep_counterexample: bool = False  # 保留可回代反例
    mark_record_usable: bool = False   # 解除记录的 stale (确认可用)
    #: 是否把这次未决写进**路线失败记录**。`estimate_effect` 的失败只是"这次估计没支持
    #: 结论", 不是"该路线失败" —— 旧实现不为它记失败, 这里必须保持同样的语义。
    record_route_failure: bool = True


def obligation_disposition_for(result: Any, obligation: Any) -> ObligationDisposition:
    """把工具结果映射为义务处置 (零 LLM, 不写任何状态)。

    与 `_to_validation_status` 同一张映射表; 因此这里是**唯一**的"结果→义务状态"
    判据。调用方负责落盘与 `_reconcile_claim` (唯一状态写入点)。

    规则 (逐条对应计划书的判定要求):
    - `passed` → 义务关闭 (closed), 记录解除 stale;
    - `failed` + **可回代反例** → 义务被反驳 (refuted), 属科学结论;
    - 声明 `failed` 却给不出反例 → blocked, **不得**当作数学反驳;
    - `estimate_effect` 类义务的失败只表示"未支持结论", 区间跨零不等于效应不存在;
    - 其余 (unknown/timeout/unsupported/unavailable/error) 一律保持未决并退出 open 集合,
      使循环到达不动点而不是无限重试同一输入。
    """
    from src.research.schemas import ValidationStatus

    mapping = {
        "passed": ValidationStatus.verified,
        "failed": ValidationStatus.counterexample_found,
        "unknown": ValidationStatus.unknown,
        "unsupported": ValidationStatus.unsupported,
        "timeout": ValidationStatus.timeout,
        "unavailable": ValidationStatus.unavailable,
        "error": ValidationStatus.execution_error,
    }
    status_value = getattr(getattr(result, "status", None), "value",
                           str(getattr(result, "status", "") or ""))
    validation = mapping.get(status_value, ValidationStatus.execution_error)
    detail = str(getattr(result, "detail", "") or "")
    kind = str(getattr(obligation, "kind", "") or "")
    counterexample = dict(getattr(result, "counterexample", None) or {})

    if validation == ValidationStatus.verified:
        return ObligationDisposition(
            action="closed", validation_status=validation,
            status=getattr(result, "status", None),
            reason=detail or "验证通过", note="", scientific=False,
            apply_estimate=(kind == "estimate_effect"),
            record_equality=(kind == "equality_condition"),
            mark_record_usable=True,
        )

    if validation == ValidationStatus.counterexample_found:
        if not counterexample:
            return ObligationDisposition(
                action="blocked", validation_status=validation,
                status=getattr(result, "status", None),
                reason=f"未给出可回代的反例: {detail}",
                note="补出满足前提的反例后再判定为假", scientific=False,
                recovery_condition="补出满足前提的反例后再判定为假")
        if kind == "estimate_effect":
            return ObligationDisposition(
                action="blocked", validation_status=validation,
                status=getattr(result, "status", None),
                reason="效应估计未支持结论 (区间跨零不等于效应不存在)",
                note="", scientific=False, record_route_failure=False)
        return ObligationDisposition(
            action="refuted", validation_status=validation,
            status=getattr(result, "status", None),
            reason=f"找到反例 {counterexample}",
            note=detail or "找到满足前提的反例", scientific=True,
            keep_counterexample=True, mark_record_usable=True,
            recovery_condition="修改命题条件或改为弱化版本后另建命题",
        )

    # 未决: 保持 blocked 并如实记录原因, 不映射为通过或数学反驳
    return ObligationDisposition(
        action="blocked", validation_status=validation,
        status=getattr(result, "status", None),
        reason=detail or status_value,
        note=f"未决({status_value}): {detail}" if detail else f"未决({status_value})",
        scientific=False,
        recovery_condition="出现新证据/条件后重试, 或改用其他后端",
    )


def novelty_comparison_for(claim: Claim, *, lookup: Any,
                           evidence: list[Any] | None = None,
                           evidence_scope: list[str] | None = None,
                           retrieval_scope: dict[str, Any] | None = None,
                           ) -> ReasoningOutcome:
    """新颖性对照 (零 LLM 判定; `lookup` 决定可比对的既有工作)。

    关键约束 (合并计划 §7.2、§14.3): 检索命中**不等于**结论已被别人做过, 所以
    `novelty.assess` 只给候选对照行与 `unchecked` 状态, 由人/独立审查确认等价性。
    本函数不写命题真值, 只返回"该记什么"。
    """
    from src.research import novelty as novelty_mod

    record = novelty_mod.assess(
        claim, lookup, evidence=list(evidence or []),
        evidence_scope=[x for x in (evidence_scope or []) if x],
        retrieval_scope=dict(retrieval_scope or {}))
    return ReasoningOutcome(
        ok=True,
        summary=f"对照 {len(record.rows)} 条既有工作 → {record.status.value}",
        payload={"status": record.status.value,
                 "rows": [r.model_dump(mode="json") if hasattr(r, "model_dump")
                          else dict(r) for r in record.rows],
                 "record": record.model_dump(mode="json")
                 if hasattr(record, "model_dump") else {}},
        # 新颖性对照不改变研究推进状态: 它只产出对照记录供审阅
        progressed=False,
    )


def model_proposal_for(claim: Claim, *, evidence: list[Any], contract: Any = None,
                       available: dict[str, bool] | None = None,
                       ) -> ReasoningOutcome:
    """从已读原文提出**候选模型并比较**, 再显式选中一个 (计划书 §3 R2 / §5.2)。

    为什么必须有多个候选 (§3 R2): 早期实现按命题类型套一个通用模板就结束, 于是
    "候选与舍弃理由"无从审查。这里至少构造互相竞争的候选, 比较来源/忠实度/可验证性,
    并给出选中理由; 候选预测冲突时另产出**可区分检验**。

    `requires_evidence`: 没有可用原文时**不编模型** —— 调用方据此去补检索, 而不是
    凭空造一个 `ResearchModel` 再声称"已建模"。
    """
    from src.research.capability import candidate_scheme, declare_capability
    from src.research.modeling import compare_from_evidence
    from src.research.schemas import ObjectRef, Origin, ResearchModel

    usable = [e for e in evidence if getattr(e, "excerpt", "")]
    if not usable:
        return ReasoningOutcome(
            ok=False, summary="缺少可用原文, 无法提出有来源的模型",
            notes=["先做定向检索并读取原文, 再建模"],
            payload={"requires_evidence": True})

    comparison = compare_from_evidence(claim, usable, contract)
    _, scheme, fidelity_note = candidate_scheme(claim)
    declaration = declare_capability(
        _claim_category(claim), available=available,
        has_data=bool(getattr(claim.study, "data_ref", "")
                      or getattr(claim.study, "rows", 0)),
        has_design=getattr(claim.study.design, "value", "none") not in ("", "none"))

    models: list[ResearchModel] = []
    for mechanism in comparison.mechanisms:
        models.append(ResearchModel(
            name=f"{mechanism.name}-{claim.id}",
            natural_language=f"基于 {len(usable)} 条原文的候选机制 "
                             f"({mechanism.name}), 待核验; {fidelity_note}",
            formal_encoding=mechanism.formal_encoding,
            variables=list(claim.variables),
            variable_domains=dict(claim.variable_domains),
            mechanism=mechanism.relation or mechanism.name or scheme,
            source_refs=list(mechanism.source_refs),
            origin=Origin.proposed,
            assumptions=list(claim.assumption_ids) + list(mechanism.assumptions),
            approximations=[mechanism.noise] if mechanism.noise else [],
            boundaries=mechanism.boundaries,
            fidelity=mechanism.fidelity,
            verified_scope="未验证: 该模型下的结论需重新推导与核验",
        ))
    if not models:
        return ReasoningOutcome(
            ok=False, summary="原文不足以形成候选机制",
            payload={"requires_evidence": True})

    # §5.2: 提出候选后**必须显式选中一个**, 否则 `ResearchModel.selected` 永远为假,
    # "结论依据哪个模型"无从审计。
    chosen = next((m for m in models if m.name.startswith(
        next((x.name for x in comparison.mechanisms if x.id == comparison.selected), ""))),
        models[0])
    chosen.selected = True
    updated = claim.model_copy(update={
        "model_ref": ObjectRef(id=chosen.id, version=chosen.version)})
    return ReasoningOutcome(
        ok=True, progressed=True,
        summary=(f"{len(models)} 个候选模型, 选中 {chosen.name}; "
                 f"弱候选 {len(comparison.weak)} 个"),
        notes=([(f"{claim.id}: 候选模型 {len(models)} 个, 其中 "
                  f"{len(comparison.weak)} 个为弱候选 (缺来源或无可观测预测)")]
               if comparison.weak else []),
        payload={"models": [m.model_dump(mode="json") for m in models],
                 "chosen_id": chosen.id,
                 "updated_claim": updated.model_dump(mode="json"),
                 "comparison": comparison.to_dict(),
                 "declaration": declaration.describe(),
                 "distinguishing": [t.to_dict() for t in comparison.distinguishing],
                 # 领域对象本身 (不是它的序列化): `design_experiment` 需要属性访问。
                 # payload 里的 `distinguishing` 是给人看/落盘的副本, 调用方要用对象。
                 "_distinguishing_objects": list(comparison.distinguishing)},
        idempotency_key=f"model:{claim.id}:"
                        f"{hash_payload([m.source_refs for m in models])}",
    )


def validation_plan_for(claim: Claim, *, distinguishing: Any = None,
                        open_obligation: bool = False,
                        gaps: list[Any] | None = None,
                        evidence: list[Any] | None = None,
                        model: dict[str, Any] | None = None,
                        ) -> ReasoningOutcome:
    """为具体未决问题生成实验/仿真规格 (计划书 §8)。

    两条硬约束 (合并计划 §1 与 §11):
    1. **绝不产生"已执行结果"** —— `execution_status` 只到规格校验, 未执行的数据不得
       当作证据;
    2. **不套模板充数** —— 既没有竞争预测也没有未闭义务时明确拒绝 (`refused=True`),
       调用方据此记账, 而不是生成一份看起来完整的空建议。

    `distinguishing` 接受领域对象或它的序列化字典: 角色上下文里只有引用与摘要, 引擎
    手里是对象; 两种形态都必须能用, 否则"角色能规划"就只是纸面能力。
    """
    from src.experiments.planner import design_experiment

    if distinguishing is None and not open_obligation:
        return ReasoningOutcome(
            ok=False, summary="既没有竞争预测也没有未闭义务, 不套模板生成建议",
            payload={"refused": True,
                     "reason": "no_competing_prediction_or_open_obligation"})

    test = _as_distinguishing_test(distinguishing)
    spec = design_experiment(
        claim,
        gaps=[g.model_dump(mode="json") if hasattr(g, "model_dump") else dict(g)
              for g in (gaps or [])],
        evidence=list(evidence or []),
        distinguishing=test,
        model=dict(model or {}))
    return ReasoningOutcome(
        ok=True, progressed=True,
        summary=(f"实验/仿真建议 (未执行): {spec.id} "
                 f"[{spec.execution_status}]"),
        payload={"spec_id": spec.id, "spec": spec.model_dump(mode="json"),
                 "executed": False},
        idempotency_key=f"exp:{claim.id}",
        # 提醒调用方: 这是建议, 不是结果
        notes=["该建议未执行; 未执行的实验不得作为证据"],
    )


def _as_distinguishing_test(value: Any) -> Any:
    """把"可区分检验"的两种形态 (领域对象 / 字典) 归一为领域对象。

    类定义在 `research/modeling.py` (不是 experiments); 角色上下文里只有序列化后的
    字典, 引擎手里是对象, 两种形态都必须能用 —— 否则"角色能规划验证方案"只是纸面能力。
    """
    if value is None:
        return None
    from src.research.modeling import DistinguishingTest

    if isinstance(value, DistinguishingTest):
        return value
    if isinstance(value, dict):
        try:
            return DistinguishingTest(**value)
        except Exception:  # noqa: BLE001 - 字段不全时退回 None (planner 走缺省分支)
            return None
    return value


def _claim_category(claim: Claim) -> str:
    """命题在能力矩阵里的问题类型 (**唯一口径**, 合并计划 §7.2)。

    先看命题类型本身; 定义性命题再区分单调性/恒等/不等式 —— 否则能力声明会把
    "可符号核验的不等式命题"说成"未知问题类型 formal: 需先澄清类型再研究",
    而那正是团队建模角色用来决定"要不要提前澄清"的输入 (实测)。

    **这里曾经与 `loop._claim_category` 分叉**: 内核只回 causal/empirical/scenario/
    normative/formal, 引擎还会回 monotonicity/identity/inequality。而且内核的文档
    声称 `tests/test_reasoning_kernel.py` 有一致性断言 —— 那条断言**并不存在**, 于是
    分叉一直没被发现。现在完整规则只在这一处, `loop._claim_category` 转发到这里,
    一致性断言补在 `tests/test_reasoning_kernel.py`。
    """
    from src.research.schemas import ClaimType, Relation

    claim_type = getattr(claim, "claim_type", ClaimType.definitional)
    if claim_type != ClaimType.definitional:
        return claim_type.value
    if getattr(claim, "expr", "") and getattr(claim, "wrt", ""):
        return "monotonicity"
    if getattr(claim, "relation", None) == Relation.eq:
        return "identity"
    if getattr(claim, "relation", None) in (Relation.ge, Relation.le,
                                            Relation.gt, Relation.lt):
        return "inequality"
    return "definitional"


# ----------------------------------------------------------------------
# 命题状态归并 (唯一的状态判据, 零 LLM)
# ----------------------------------------------------------------------
def assurance_for_support_kind(support_kind: Any) -> Any:
    """支持方式 → 保证等级 (与 `loop._assurance_for` 同一张表)。

    引擎侧的同名实现已改为调用这里, 因此"哪种支持算几级保证"只有一处定义。
    """
    from src.research.schemas import Assurance, SupportKind

    return {
        SupportKind.theorem_application: Assurance.symbolic_checked,
        SupportKind.symbolic_check: Assurance.symbolic_checked,
        SupportKind.constraint_solve: Assurance.solver_checked,
        SupportKind.formal_proof: Assurance.formally_checked,
        SupportKind.statistical_estimate: Assurance.empirical_estimated,
        SupportKind.informal_argument: Assurance.informal_reviewed,
    }.get(support_kind)


@dataclass
class ClaimStateDisposition:
    """由义务聚合与有效验证记录算出的命题状态 (**纯映射, 不写任何状态**)。

    为什么把它抽到内核 (合并计划 §3.1 G06): 团队形式化路径必须能自己走完
    "义务 → 工具核验 → 结构化记录 → **状态归并**", 而状态归并的判据只能有一份 ——
    否则团队路径与旧引擎会在"什么时候算 supported"上分叉, 那正是"团队接管研究"
    最难发现的一类错误。抽取后引擎的 `_reconcile_claim` 变成"调内核 + 落盘",
    团队侧由提交服务调用同一个函数。
    """

    status: Any = None
    coverage: Any = None
    validation_status: Any = None
    support_kind: Any = None
    assurance: Any = None
    verification_scope: Any = None
    note: str = ""


def claim_state_for(claim: Any, obligations: list[Any], records: list[Any],
                    *, closure: dict[str, int] | None = None,
                    aligned: Callable[[Any, Any], bool] | None = None,
                    ) -> ClaimStateDisposition:
    """按义务集合与**非 stale** 验证记录算出命题该处于什么状态。

    规则 (与原 `loop._reconcile_claim` 逐条一致, 差异由 `test_reasoning_kernel`
    的等价性用例把守):

    1. 任一必要义务被反例否决 → `refuted`;
    2. 全部必要义务关闭 → 还要有**与命题对齐**的有效验证记录才 `supported`;
       统计估计的区间跨零则保持 `blocked`;
    3. 存在受阻义务且没有待办必要义务 → `blocked`;
    4. 其余保持 `in_progress`(或原状态), 验证维度为 `unknown`。

    `aligned` 是"记录是否与原命题编码一致"的判据 (`acceptance._aligned`); 缺省时
    按"有记录即算对齐"处理 —— 调用方必须显式传入才具备对齐检查, 不传就是放弃检查,
    因此生产调用方一律要传。
    """
    from src.research.schemas import (
        ClaimStatus,
        Coverage,
        ObligationStatus,
        ValidationStatus,
        VerificationScope,
    )

    def _is_aligned(record: Any) -> bool:
        return True if aligned is None else bool(aligned(record, claim))

    obligations = list(obligations)
    records = [r for r in records if not getattr(r, "stale", False)]
    required = [o for o in obligations if getattr(o, "required", True)]
    open_required = [o for o in required
                     if getattr(o, "status", None) == ObligationStatus.open]
    # **反例不受 required 限制**: 找到反例就是命题不成立, 与"这条义务是不是必要义务"
    # 无关。反例搜索义务通常是 `required=False` (它回答的是"命题是否根本不成立",
    # 而不是"结论还缺哪一步"), 若把反例也按 required 过滤, 一个被机器找到反例的命题
    # 会被判成"未决", 那正是最不该发生的漏判。
    refuted = [o for o in obligations
               if getattr(o, "status", None) == ObligationStatus.refuted]
    blocked = [o for o in required
               if getattr(o, "status", None) == ObligationStatus.blocked]
    closed = [o for o in required
              if getattr(o, "status", None) == ObligationStatus.closed]

    if refuted:
        witness = next((getattr(o, "counterexample", {}) for o in refuted
                        if getattr(o, "counterexample", {})), {})
        support_kind = getattr(refuted[0], "support_kind", None) or claim.support_kind
        return ClaimStateDisposition(
            status=ClaimStatus.refuted, coverage=Coverage.target,
            validation_status=ValidationStatus.counterexample_found,
            support_kind=support_kind,
            assurance=(assurance_for_support_kind(support_kind) or claim.assurance),
            verification_scope=VerificationScope.target,
            note=f"被反例否决: {witness}")

    if required and not open_required and not blocked and len(closed) == len(required):
        aligned_records = [r for r in records if _is_aligned(r)]
        estimate = claim.effect_estimate or {}
        if estimate and not estimate.get("ci_excludes_zero", True):
            return ClaimStateDisposition(
                status=ClaimStatus.blocked, coverage=Coverage.step,
                validation_status=ValidationStatus.unknown,
                note="效应95%CI包含0, 不足以支持该结论")
        if not aligned_records:
            return ClaimStateDisposition(
                status=ClaimStatus.in_progress, coverage=Coverage.step,
                validation_status=ValidationStatus.unknown,
                note="义务已关闭但缺少与原命题对齐的有效验证记录")
        kinds = [o.support_kind for o in closed if o.support_kind is not None
                 and getattr(o.support_kind, "value", "") != "none"]
        from src.research.schemas import SupportKind

        support_kind = kinds[0] if kinds else SupportKind.informal_argument
        return ClaimStateDisposition(
            status=ClaimStatus.supported, coverage=Coverage.target,
            validation_status=ValidationStatus.verified,
            support_kind=support_kind,
            assurance=(assurance_for_support_kind(support_kind) or claim.assurance),
            verification_scope=VerificationScope.target)

    if blocked and not open_required:
        return ClaimStateDisposition(
            status=ClaimStatus.blocked,
            validation_status=ValidationStatus.unknown,
            note="存在受阻义务, 保持未决")

    if claim.status == ClaimStatus.supported:
        # 已确证的命题不因"这一轮没有新记录"被降级 (状态只由义务与记录推导,
        # 不由"本次调用看到多少"推导) —— 与原实现一致。
        validation_status = claim.validation_status
    else:
        validation_status = ValidationStatus.unknown
    return ClaimStateDisposition(
        status=(ClaimStatus.in_progress if (closed or records) else claim.status),
        validation_status=validation_status)


def apply_claim_state(claim: Any, disposition: ClaimStateDisposition,
                      *, closure: dict[str, int] | None = None) -> Any:
    """把状态处置落到 `Claim` 的一份**新副本**上 (不写存储)。

    只做字段更新: 版本、事件与落盘由调用方负责 (引擎写 `KIND_CLAIM`, 团队侧由
    提交服务在同一事务里写)。
    """
    updates: dict[str, Any] = {"verification_closure": dict(closure or {})}
    if disposition.status is not None:
        updates["status"] = disposition.status
    if disposition.coverage is not None:
        updates["coverage"] = disposition.coverage
    if disposition.validation_status is not None:
        updates["validation_status"] = disposition.validation_status
    if disposition.support_kind is not None:
        updates["support_kind"] = disposition.support_kind
    if disposition.assurance is not None:
        updates["assurance"] = disposition.assurance
    if disposition.verification_scope is not None:
        updates["verification_scope"] = disposition.verification_scope
    if disposition.note:
        updates["notes"] = (str(claim.notes or "") + " " + disposition.note).strip()
    return claim.model_copy(update=updates)


__all__ = [
    "COUNTEREXAMPLE_REPEAT_FAILURES",
    "ClaimStateDisposition",
    "CounterexampleVerdict",
    "ObligationDisposition",
    "ReasoningOutcome",
    "apply_claim_state",
    "assurance_for_support_kind",
    "build_design_steps",
    "claim_state_for",
    "counterexample_verdict_for",
    "derive_steps_for",
    "design_steps_for",
    "is_design_claim",
    "model_proposal_for",
    "novelty_comparison_for",
    "obligation_disposition_for",
    "plan_proof_for",
    "seek_counterexample_for",
    "validation_plan_for",
]
