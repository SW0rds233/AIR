from __future__ import annotations

"""ReasoningAgent 推理与结论综合 (合并计划 §3.1 / §7.1)。

职责: 分解义务、推导、查反例、**综合文献/案例并回答问题**。
提交 `ReasoningResult`: 推导链、命题候选、工具请求/记录、结论建议、局限、补检索请求。

两条硬约束 (§7.1、§14.3):
1. 它只能**提出**命题与推导, 状态 (supported/refuted) 由判定层按证书链与中央规则产生;
2. 综述也归这里 —— `synthesis` 策略负责主题归纳、定义比较、证据冲突解释与研究空白
   综合; 纯文献清单可跳过。形式化证明只是**一种**策略, 不能把所有研究问题都强制
   转成 SymPy 可解的式子。

"引理缺条件"这类受阻情形在这里产生 `ResearchNeed(clause)`, 由主控派检索/建模
—— 而不是让推理智能体无限等待, 也不是让写作者自己编补条件。
"""

from typing import Any

from src.agents.base import (
    AgentBase,
    as_list_of_str,
    build_proposal,
    clip,
    context_summary,
    extract_json,
    task_intent,
)
from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    ContextPack,
    NeedKind,
    ResearchNeed,
    TaskOutcome,
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec
from src.agents.tools import evidence_tools, math_tools

__all__ = ["ReasoningAgent", "classify_strategy"]


#: 推理策略。`synthesis` 是综述/机理类问题的默认策略; `derivation` 走形式化核验。
STRATEGIES: tuple[str, ...] = (
    "derivation",     # 推导 + 形式化核验
    "counterexample",  # 找反例
    "synthesis",      # 文献/案例综合与定义比较
    "quantitative",   # 数据/统计推断
)

_SYMBOLIC_HINTS = ("证明", "推导", "定理", "不存在", "存在性", "∀", "对所有", "prove",
                   "theorem", "derive")
_SYNTHESIS_HINTS = ("综述", "研究进展", "分类", "冲突", "空白", "定义比较", "梳理",
                    "review", "synthesis", "taxonomy")
_DATA_HINTS = ("回归", "估计", "统计", "样本", "面板", "回归系数", "regression", "estimate")


def classify_strategy(text: str, *, has_data: bool = False) -> str:
    """决定推理策略 (规则)。

    合并计划 §7.1 明确"不能把所有研究问题都强制转成 SymPy 可解的式子", 因此这里
    只在题面**确实**要求判定/证明时才选 `derivation`; 否则默认 `synthesis`。
    """
    body = str(text or "")
    if any(hint in body for hint in _SYMBOLIC_HINTS):
        return "derivation"
    if has_data or any(hint in body for hint in _DATA_HINTS):
        return "quantitative"
    if any(hint in body for hint in _SYNTHESIS_HINTS):
        return "synthesis"
    return "synthesis"


class ReasoningAgent(AgentBase):
    """推理与结论综合角色。"""

    role = "reasoning"
    prompt_version = "reasoning/v1"
    kinds = ("claim", "obligation", "verification", "gap")

    SYSTEM = """你是"推理与结论综合"智能体。

你的任务: 针对给定子问题给出**可审查**的推理或综合结论。

你必须区分三种东西, 不得混为一谈:
1. 形式化可核验的部分 (等式/不等式/约束): 写成可交给符号或约束求解器检查的形式;
2. 只能非形式化论证的部分: 明确标注为非形式化, 并给出前提;
3. 引用了他人定理的部分: 写清定理名称、条件与出处; 条件不满足就**不要**使用该定理。

纪律:
- 你只提出候选命题与推导; 结论是否成立由判定层按证书链判定, 你不宣布"已证明";
- 任何一步依赖未验证的条件, 就把它列进 "missing_conditions" 并提出 need, 不要跳过;
- 不得为了凑出完整证明而编造中间步骤或引用不存在的定理;
- 若文献证据互相冲突, 如实写出冲突并说明哪种解释更可能, 而不是只挑支持自己的一方;
- 没有足够依据时如实给出"未决"与理由。

最终输出 JSON:
{"strategy": "derivation|synthesis|counterexample|quantitative",
 "claims": [{"statement": "", "claim_type": "definitional|descriptive|associational|causal|predictive|scenario|normative",
             "reasoning": [""], "premises": [""], "informal": true}],
 "counterexamples": [{"statement": "", "witness": ""}],
 "missing_conditions": [""], "limitations": [""],
 "needs": [{"kind": "more_sources|clause|model_condition|derivation|empirical_support", "statement": "", "why": "", "acceptance": [""]}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        # 推理能请求补检索 (合并计划 §4.3 的"推导中能请求文献"), 也能请求符号核验
        return [*math_tools(), *[t for t in evidence_tools()
                                 if t.name in ("search_local_kb", "search_all_sources",
                                               "read_source")]]

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        topic = _topic_of(context)
        strategy = classify_strategy(
            f"{task.objective}\n{context.request}",
            has_data=bool(context.objects.get("dataset")) or bool(topic and _has_data(context)))
        # 形式化推导策略先走**抽取出来的推理内核** (合并计划 M2「按职责抽取」):
        # 团队路径与旧引擎路径共用同一份"证明计划 + 推导步骤 + 反方审查"实现,
        # 因此两边的步骤集与待核验义务不会分叉 (用例见 tests/test_reasoning_kernel.py)。
        if strategy == "derivation":
            kernel_result = self.derive_via_kernel(task, context, runtime, usage)
            if kernel_result is not None:
                return kernel_result
        claims: list[dict[str, Any]] = []
        counterexamples: list[dict[str, Any]] = []
        missing: list[str] = []
        limitations: list[str] = []
        needs: list[ResearchNeed] = []
        parse_note = ""

        if runtime.llm_available() and not task.budget.exceeded_by(usage):
            from langchain_core.messages import HumanMessage, SystemMessage

            prompt = (
                f"# 子问题\n{task_intent(task)}\n"
                f"# 预期收益\n{task.expected_gain or '(未声明)'}\n"
                f"# 建议策略\n{strategy}\n"
                f"# 可用证据\n"
                + "\n".join(f"- {clip(str(s.get('locator', '')) , 40)} | "
                            f"{clip(str(s.get('title', '')), 120)} | "
                            f"{s.get('relation', 'insufficient')}"
                            for s in (context.objects.get("evidence") or [])[:15])
                + f"\n# 上下文\n{context_summary(context)}"
            )
            try:
                text, observations = self.tool_loop(
                    task, runtime, tools, usage,
                    [SystemMessage(content=self.SYSTEM), HumanMessage(content=prompt)],
                    grant=context.grant)
                usage.tool_calls += len(observations)
                payload, parse_note = extract_json(text)
            except Exception as e:  # noqa: BLE001
                payload, parse_note = None, f"推理调用失败: {e}"
            if isinstance(payload, dict):
                claims = [c for c in (payload.get("claims") or []) if isinstance(c, dict)]
                counterexamples = [c for c in (payload.get("counterexamples") or [])
                                   if isinstance(c, dict)]
                missing = as_list_of_str(payload.get("missing_conditions"))
                limitations = as_list_of_str(payload.get("limitations"))
                needs = _needs_from_payload(payload.get("needs"))
                strategy = str(payload.get("strategy") or strategy)

        if not claims and not counterexamples:
            fallback_claims, fallback_note = self.fallback_claims(task, context,
                                                                  strategy)
            claims = fallback_claims
            if parse_note and not fallback_claims:
                return self.blocked(
                    task,
                    f"推理没有产出可审查内容 ({parse_note})",
                    needs=[ResearchNeed(
                        kind=NeedKind.derivation,
                        statement="推理调用失败且规则路径也没能给出推导",
                        why="没有推导就无法支撑结论",
                        acceptance=["给出至少一条可审查的推导或明确说明无法推导"],
                    )],
                    usage=usage)
            if fallback_note:
                limitations.append(fallback_note)

        # 前提缺失统一转成需求 (主控据此派检索/建模)
        for condition in missing:
            needs.append(ResearchNeed(
                kind=NeedKind.clause,
                statement=f"缺少条件: {condition}",
                why="该条件是该步推导的前提, 未核实就不能使用",
                acceptance=["给出该条件的可定位出处, 或说明它不成立"],
                blocking=False))

        changes = [build_proposal(
            "claim",
            payload={**claim, "strategy": strategy, "subquestion": task.subquestion,
                     "informal": bool(claim.get("informal", True))},
            rationale=clip("; ".join(as_list_of_str(claim.get("reasoning"))[:3]), 400),
            input_versions={},
        ) for claim in claims]
        changes.extend(build_proposal(
            "gap",
            payload={"type": "unresolved_claim", "statement": c.get("statement", ""),
                     "witness": c.get("witness", ""), "subquestion": task.subquestion},
            rationale="推理给出反例候选 (是否成立由判定层核验)",
        ) for c in counterexamples)

        summary = f"策略 {strategy}: 提出 {len(claims)} 条命题候选"
        if counterexamples:
            summary += f", {len(counterexamples)} 条反例候选"
        if missing:
            summary += f"; {len(missing)} 处条件待补"
        if parse_note:
            summary += f" | {parse_note}"

        payload_out = {
            "schema": "ReasoningResult/v1",
            "strategy": strategy,
            "claims": claims,
            "counterexamples": counterexamples,
            "missing_conditions": missing,
            "limitations": limitations,
        }
        outcome = TaskOutcome.completed if (claims or counterexamples) \
            else TaskOutcome.blocked
        if outcome == TaskOutcome.blocked:
            return self.blocked(
                task, "没有可审查的推理产出",
                needs=needs or [ResearchNeed(
                    kind=NeedKind.derivation, statement="需要重新推导",
                    why="当前没有命题或反例候选")],
                usage=usage, payload=payload_out)
        replan = bool(counterexamples)
        return self.completed(task, summary, changes=changes, needs=needs,
                              unresolved=[*missing[:4], *limitations[:4]],
                              usage=usage, payload=payload_out, replan=replan)

    # ---- 形式化推导: 走抽取出来的推理内核 (M2) ----
    def derive_via_kernel(self, task: AgentTask, context: ContextPack,
                          runtime: AgentRuntime,
                          usage: UsageRecord) -> AgentResult | None:
        """用 `research/reasoning_kernel.py` 生成证明计划与推导步骤。

        为什么角色要调内核而不是自己重写一套: 合并计划 §7.1 要求把
        `_act_plan_proof`/`_act_derive_step` 的**能力**抽到推理角色, 而 §14.3 要求
        判定层保持零 LLM、结论只由证书与规则产生。内核正好落在这条边界上 ——
        它做纯计算 (计划/步骤/反方审查), 把命题真值留给判定层。

        返回 `None` 表示"上下文里没有可形式化的命题", 调用方退回通用策略
        (综合/量化), 而不是空转或编一条命题出来。
        """
        claim, obligations, evidence = _formalisable_subject(context)
        if claim is None:
            return None

        from src.research.reasoning_kernel import (
            build_design_steps,
            derive_steps_for,
            design_steps_for,
            is_design_claim,
            plan_proof_for,
        )

        # 1. 证明计划 (规则路径始终计算; 有 LLM 时内核只允许它**增加**待核验内容)
        try:
            plan_outcome = plan_proof_for(claim, obligations=obligations,
                                          available=None, llm=None)
        except Exception as e:  # noqa: BLE001 - 计划失败退到通用策略, 不静默成功
            runtime.emit("reasoning_kernel_failed",
                         {"task_id": task.task_id, "stage": "plan", "reason": str(e)})
            return None

        # 2. 推导步骤 + 反方审查 (设计/计数类走证书判定链)
        if is_design_claim(claim):
            steps_outcome = design_steps_for(claim, obligations=obligations,
                                             step_builder=build_design_steps)
        else:
            steps_outcome = derive_steps_for(
                claim, attempt=_attempt_from_plan(plan_outcome),
                obligations=obligations, evidence=evidence)

        changes: list[ChangeProposal] = []
        for stage, outcome in (("plan", plan_outcome), ("derive", steps_outcome)):
            if not outcome.ok:
                continue
            changes.append(build_proposal(
                "claim",
                payload={
                    "id": claim.id, "version": claim.version,
                    "statement": claim.statement,
                    "strategy": str(outcome.payload.get("strategy") or "derivation"),
                    "stage": stage,
                    "steps": outcome.payload.get("steps"),
                    "formal_gap": outcome.payload.get("formal_gap"),
                    "review_obligations": len(outcome.payload.get("review_obligations")
                                              or []),
                    "subquestion": task.subquestion,
                    "informal": False,
                    "kernel": True,
                },
                rationale=f"{stage}: {outcome.summary}",
                input_versions={claim.id: claim.version},
            ))
        if not changes:
            return None

        missing = [n for n in steps_outcome.notes if "形式化片段" in n]
        needs: list[ResearchNeed] = []
        for obligation in (steps_outcome.payload.get("review_obligations") or []):
            needs.append(ResearchNeed(
                kind=NeedKind.clause,
                statement=f"独立审查提出待核验义务: {clip(str(obligation.get('statement', '')), 200)}",
                why="反方审查意见必须经核验才能关闭, 不直接改结论状态",
                acceptance=["给出可定位依据或工具核验记录"],
                blocking=False))
        runtime.emit("reasoning_kernel_used", {
            "task_id": task.task_id, "claim_id": claim.id,
            "design": is_design_claim(claim),
            "obligations": len(obligations),
        })
        return self.completed(
            task,
            f"形式化推导 (内核): {plan_outcome.summary}; {steps_outcome.summary}",
            changes=changes, needs=needs, unresolved=missing,
            usage=usage,
            payload={"schema": "ReasoningResult/v1", "kernel": True,
                     "strategy": "derivation",
                     "claim_id": claim.id,
                     "plan": plan_outcome.payload,
                     "steps": steps_outcome.payload},)

    def fallback_claims(self, task: AgentTask, context: ContextPack,
                        strategy: str) -> tuple[list[dict[str, Any]], str]:
        """规则路径: 不发明科学内容, 只把**已有**材料整理成可审查的候选结构。

        这是"综合"策略的确定性形态。规则层**判不出语义支持** (检索命中不等于支持,
        文本相似也不等于支持), 因此这里:

        - 有已判定为支持/部分支持的来源时, 生成"该子问题有来源支持"的候选并附定位;
        - 只有候选材料 (关系未定) 时, 生成"材料已收集、支持关系待判定"的候选 ——
          这是**诚实的中间结论**, 不是"已证明";
        - 什么都没有时返回空列表 (调用方据此如实报未决)。
        """
        evidence = context.objects.get("evidence") or []
        if not evidence:
            return [], "规则路径没有证据可用, 未生成候选命题"
        supporting = [e for e in evidence
                      if str(e.get("relation", "")) in ("supports", "partially_supports")]
        contradicting = [e for e in evidence
                         if str(e.get("relation", "")) == "contradicts"]
        locatable = [e for e in evidence if e.get("locator")]
        subject = _statement_for(task, context)

        claims: list[dict[str, Any]] = []
        if supporting:
            claims.append({
                "statement": subject,
                "claim_type": "descriptive",
                "reasoning": [
                    "规则综合: 以下可定位来源对该子问题给出支持关系",
                    *[f"{clip(str(e.get('title', '')), 80)} @ {e.get('locator')}"
                      for e in supporting[:5]],
                ],
                "premises": [clip(str(e.get("excerpt", "")), 200) for e in supporting[:5]],
                "informal": True,
                "support_refs": [str(e.get("source_id") or e.get("id") or "")
                                 for e in supporting[:5]],
                "needs_check": False,
            })
        elif locatable:
            # 材料已收集但支持关系未判定: 诚实记录, 不声称结论成立
            claims.append({
                "statement": f"关于「{clip(subject, 120)}」已取得 {len(locatable)} 条"
                             f"可定位来源, 支持关系尚未判定",
                "claim_type": "descriptive",
                "reasoning": [
                    ("规则综合: 命中材料具备可回到原文的定位, 但规则层不判定语义支持 "
                     "(命中不等于支持)"),
                    *[f"{clip(str(e.get('title', '')), 80)} @ {e.get('locator')}"
                      for e in locatable[:5]],
                ],
                "premises": [clip(str(e.get("excerpt", "")), 200) for e in locatable[:5]],
                "informal": True,
                "support_refs": [str(e.get("source_id") or e.get("id") or "")
                                 for e in locatable[:5]],
                "needs_check": True,
            })
        else:
            claims.append({
                "statement": f"关于「{clip(subject, 120)}」取得 {len(evidence)} 条材料, "
                             f"但均无可用定位",
                "claim_type": "descriptive",
                "reasoning": ["规则综合: 命中材料没有可回到原文的定位, 不能作为引用依据"],
                "premises": [],
                "informal": True,
                "support_refs": [],
                "needs_check": True,
            })
        if contradicting:
            claims.append({
                "statement": f"关于「{clip(subject, 120)}」存在相反证据",
                "claim_type": "descriptive",
                "reasoning": [
                    "规则综合: 以下来源与候选结论冲突, 需要可区分检验",
                    *[f"{clip(str(e.get('title', '')), 80)} @ {e.get('locator')}"
                      for e in contradicting[:5]],
                ],
                "premises": [clip(str(e.get("excerpt", "")), 200) for e in contradicting[:5]],
                "informal": True,
                "support_refs": [str(e.get("source_id") or e.get("id") or "")
                                 for e in contradicting[:5]],
                "needs_check": True,
            })
        note = (f"规则综合生成 {len(claims)} 条候选 (支持 {len(supporting)} / "
                f"冲突 {len(contradicting)} / 可定位 {len(locatable)}); 未做形式化核验")
        return claims, note


def _formalisable_subject(context: ContextPack):
    """从角色上下文还原"可形式化的命题 + 义务 + 证据"。

    图状态里只放引用与摘要 (`unified_state` 的约束), 因此角色侧按引用重建**领域对象**
    再交给内核 —— 内核只接受领域对象, 不接受任意字典, 这样"内核里没有 LLM、不接受
    自由文本当命题"就是类型层面的事实。
    """
    from src.research.schemas import Claim, ProofObligation, SourceEvidence

    claim: Claim | None = None
    for row in context.objects.get("claim") or []:
        claim = _parse(Claim, row)
        if claim is not None and claim.statement:
            break
        claim = None
    if claim is None:
        return None, [], []

    obligations = [o for o in (_parse(ProofObligation, row)
                               for row in context.objects.get("obligation") or [])
                   if o is not None and o.claim_id in ("", claim.id)]
    evidence = [e for e in (_parse(SourceEvidence, row)
                            for row in context.objects.get("evidence") or [])
                if e is not None]
    return claim, obligations, evidence


def _parse(model: Any, row: Any) -> Any:
    """把快照行还原为领域对象; 字段不全时返回 None (不猜、不补默认语义)。"""
    if not isinstance(row, dict):
        return None
    try:
        return model(**row)
    except Exception:  # noqa: BLE001 - 单行字段不全就跳过该行
        return None


def _attempt_from_plan(plan_outcome) -> Any:
    """从内核返回的计划载荷里还原 `ProofAttempt` (推导步骤的载体)。"""
    from src.research.schemas import ProofAttempt

    raw = (plan_outcome.payload or {}).get("attempt")
    if not isinstance(raw, dict):
        return ProofAttempt()
    try:
        return ProofAttempt(**raw)
    except Exception:  # noqa: BLE001 - 载荷不全时给空尝试, 由内核补"无形式化片段"步骤
        return ProofAttempt()


def _needs_from_payload(value: Any) -> list[ResearchNeed]:
    out: list[ResearchNeed] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        need = _need_from(item)
        if need is not None:
            out.append(need)
    return out


def _need_from(item: dict[str, Any]) -> ResearchNeed | None:
    """单条需求还原; 类型未登记时跳过 (而不是崩溃或猜一个类型)。"""
    try:
        return ResearchNeed(
            kind=str(item.get("kind") or "more_sources"),
            statement=str(item.get("statement") or ""),
            why=str(item.get("why") or ""),
            acceptance=as_list_of_str(item.get("acceptance")),
        )
    except Exception:  # noqa: BLE001 - 未登记的需求类型跳过
        return None


def _statement_for(task: AgentTask, context: ContextPack) -> str:
    """候选命题的主语: 优先用**研究问题**, 而不是子任务措辞。

    理由与 EvidenceAgent 的检索目标一致: 子问题措辞是"要做什么", 命题主语应该是
    "要判定什么"。拿子任务措辞当命题主语会把待检验的判断变成任务描述。
    """
    for row in context.objects.get("brief") or []:
        question = str(row.get("main_question", "") or "").strip()
        if question:
            return question
    if str(context.request or "").strip():
        return str(context.request).strip()
    return task_intent(task)


def _topic_of(context: ContextPack) -> str:
    for row in context.sources:
        candidate = str(row.get("source_set_id", "") or "")
        if candidate:
            return candidate
    return ""


def _has_data(context: ContextPack) -> bool:
    return bool(context.objects.get("dataset"))
