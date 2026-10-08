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
    ObjectRef,
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

    §3.1 G06 补了一条判据: **题面能被精确形式化**时也走 `derivation`。只靠关键词会漏掉
    真实的形式化问题 —— 实测"参数 2-(211,15,1) 的设计是否存在"没有出现"证明/推导"
    这些词, 于是被判成综述策略, 命题停在候选状态、交付降级为研究备忘录, 而它其实
    有唯一的确定答案。判据用的是与 `build_spec_from_input` 同一份"精确计数约束抽取",
    不是再加一组关键词。
    """
    body = str(text or "")
    from src.research.classification import is_formal_question
    if is_formal_question(body):
        return "derivation"
    if any(hint in body for hint in _SYMBOLIC_HINTS):
        return "derivation"
    if _precisely_formalisable(body):
        return "derivation"
    if has_data or any(hint in body for hint in _DATA_HINTS):
        return "quantitative"
    if any(hint in body for hint in _SYNTHESIS_HINTS):
        return "synthesis"
    return "synthesis"


def _precisely_formalisable(text: str) -> bool:
    """题面是否含可精确抽取的约束 (如计数参数) —— 抽得出就走形式化。"""
    if not str(text or "").strip():
        return False
    try:
        from src.research.design_feasibility import formulate_from_text

        return formulate_from_text(text) is not None
    except Exception:  # noqa: BLE001 - 抽取失败按"不可精确形式化"处理, 不影响综述路径
        return False


class ReasoningAgent(AgentBase):
    """推理与结论综合角色。"""

    role = "reasoning"
    prompt_version = "reasoning/v1"
    kinds = ("claim", "obligation", "verification", "gap", "attempt")

    SYSTEM = """你是"推理与结论综合"智能体。

你的任务: 针对给定子问题给出**可审查**的推理或综合结论。

你必须区分三种东西, 不得混为一谈:
1. 形式化可核验的部分 (等式/不等式/约束): 写成可交给符号或约束求解器检查的形式;
2. 只能非形式化论证的部分: 明确标注为非形式化, 并给出前提;
3. 引用了他人定理的部分: 写清定理名称、条件与出处; 条件不满足就**不要**使用该定理。
对机制或跨学科问题，要写清“条件/作用链/可观测后果”，区分文献实测、模型假说
与本研究推论；没有实验数据时不得把建议验证的机理写成已证实因果结论。

纪律:
- 你只提出候选命题与推导; 结论是否成立由判定层按证书链判定, 你不宣布"已证明";
- 任何一步依赖未验证的条件, 就把它列进 "missing_conditions" 并提出 need, 不要跳过;
- 不得为了凑出完整证明而编造中间步骤或引用不存在的定理;
- 若文献证据互相冲突, 如实写出冲突并说明哪种解释更可能, 而不是只挑支持自己的一方;
- 没有足够依据时如实给出"未决"与理由。
- 推导中若需要新的定理出处、机制研究或数据，提出带具体关键词与核查目标的
  more_sources 需求；外部检索与入库由检索角色统一执行，不把未经登记的摘要当作依据。
- strategy 表示研究方法；claim_type 表示命题语义类型，不能填 derivation 等策略名。
  纯数学/形式化命题应明确标注 study.design="theory"，同时交代变量域与证明义务。
  不得把本轮检索不足表述成“学术界未决”；文献事实需要原文核查，形式化子结论需要实际核验。
  数学推导所得的候选断言通常归 descriptive，而不是凭此宣称已证明。
  非人群研究用 scope_conditions 记录对象、环境及适用条件；有样本范围的研究才填
  scope_population/scope_region/scope_period。预测命题的 study.predictive_validation
  应记录样本外划分、评价指标和基线比较，不能用 design_feasibility 代替。
  情景命题的 study.scenario_parameters 应记录参数、范围和取值依据；
  study.design_feasibility 只表示因果研究设计在现有数据上的可行性。
  数学等价、概率公式和矩阵恒等式不是实证因果/关联命题；研究建议不是数学命题。
  每次只提交解决当前子问题所必需的少量命题（建议最多8条），复用已有命题 id，
  修正旧命题时提供其 id 和修正条件，避免重新生成整套文献摘要命题。
  可编码的结论必须给 lhs/rhs/relation/variables/variable_domains；不能编码时给逐步论证和缺口。
  不得把综述、检索进度或写作建议注册成需要证明的科学命题。

最终输出 JSON:
{"strategy": "derivation|synthesis|counterexample|quantitative",
 "claims": [{"statement": "", "claim_type": "definitional|descriptive|associational|causal|predictive|scenario|normative",
             "study": {"design": "theory|observational|simulation|none"},
             "lhs": "", "rhs": "", "relation": "==|>=|<=|>|<|custom",
             "variables": [""], "variable_domains": {}, "id": "",
             "reasoning": [""], "premises": [""], "informal": true}],
 "counterexamples": [{"statement": "", "witness": ""}],
 "missing_conditions": [""], "limitations": [""],
 "needs": [{"kind": "more_sources|clause|model_condition|derivation|empirical_support", "statement": "", "why": "", "acceptance": [""]}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        # 推理可核读已登记来源并提出定向补检索需求；外检统一由证据角色入库。
        return [*math_tools(), *[t for t in evidence_tools()
                                 if t.name in ("search_local_kb", "read_source")]]

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        topic = _topic_of(context)
        strategy = classify_strategy(
            f"{task_intent(task)}\n{_research_text(context)}",
            has_data=bool(context.objects.get("dataset")) or bool(topic and _has_data(context)))
        if task.hints.get("need_kind") == "derivation" or task.hints.get("claim_id"):
            strategy = "derivation"
        # 形式化推导策略先走**闭环路径** (合并计划 §3.1 G06): 它把
        # "候选/义务 → 工具核验 → 结构化记录 → 状态归并"整条链接进同一任务协议 ——
        # 义务与核验记录作为候选提交, 命题状态由唯一提交口按内核判据重算。
        if strategy == "derivation":
            formal = self.formal_closure(task, context, runtime, usage)
            if formal is not None:
                return formal
        claims: list[dict[str, Any]] = []
        counterexamples: list[dict[str, Any]] = []
        missing: list[str] = []
        limitations: list[str] = []
        needs: list[ResearchNeed] = []
        parse_note = ""
        observations = []

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

        # A tool hit is not yet a registered, readable source. Evidence owns ingestion.
        from src.agents.evidence import _observed_searches
        queries, cached = _observed_searches(observations, task.source_policy or "user_kb")
        known = {str(row.get("doi") or row.get("url") or row.get("title") or "")
                 for row in context.objects.get("evidence") or []}
        for query in queries:
            new_hits = [row for row in cached.get(query, [])
                        if str(row.get("doi") or row.get("url") or row.get("title") or "") not in known]
            if not new_hits:
                continue
            needs.append(ResearchNeed(
                kind=NeedKind.more_sources, statement=f"登记并核读推理联想到的文献: {query}",
                why="检索工具返回的摘要不能冒充已经核对过的原文或定理证据",
                acceptance=["文献入库、提供可读摘录与定位，回到推导核查适用条件"],
                hints={"query": query, "research_context": "reasoning_lookup",
                       "trigger_id": f"reasoning-source:{query}"}, blocking=True))

        if not claims and not counterexamples:
            fallback_claims, fallback_note = self.fallback_claims(task, context,
                                                                  strategy)
            claims = fallback_claims
            if parse_note and not fallback_claims:
                return self.blocked(
                    task,
                    f"推理没有产出可审查内容 ({parse_note})",
                    needs=needs + [ResearchNeed(
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

        from src.research.classification import is_formal_question, has_empirical_context
        from src.research.constraint_fidelity import conflicting_code_parameters
        from src.research.schemas import stable_id
        research_text = _research_text(context)
        theory_context = is_formal_question(research_text)
        changes = []
        fidelity_issues: list[str] = []
        seen_statements: set[str] = set()
        known = {str(row.get("id")): row for row in context.objects.get("claim", [])}
        for candidate in claims:
            claim = dict(candidate)
            statement = str(claim.get("statement") or "").strip()
            if not statement or statement in seen_statements:
                continue
            conflicts = conflicting_code_parameters(research_text, statement)
            if conflicts:
                fidelity_issues.extend(conflicts)
                needs.append(ResearchNeed(
                    kind=NeedKind.model_condition,
                    statement="核对候选命题与原题的显式参数",
                    why="；".join(conflicts),
                    acceptance=["以原题参数重写候选，并逐项核对模型与结论的变量含义"],
                    blocking=True))
                continue
            seen_statements.add(statement)
            if theory_context and not has_empirical_context(claim):
                if claim.get("claim_type") == "normative":
                    limitations.append("研究建议（非待证命题）: " + statement)
                    continue
                if claim.get("claim_type") in {"causal", "associational", "predictive", "scenario"}:
                    claim["claim_type_input"] = claim["claim_type"]
                    claim["claim_type"] = "descriptive"
                    claim["classification_basis"] = "数学题面且无实证设计/样本；语义纠偏不构成核验"
                claim["study"] = {**dict(claim.get("study") or {}), "design": "theory"}
            requested_id = str(claim.get("id") or "")
            existing = known.get(requested_id) or next((row for row in known.values()
                if str(row.get("statement") or "").strip() == statement), None)
            object_id = str(existing["id"]) if existing else stable_id("clm", {
                "project": task.project_id, "problem": task.problem_id, "statement": statement})
            claim["id"] = object_id
            version = int(existing.get("version") or 1) if existing else 1
            encoding_changed = existing and any(claim.get(key) and claim.get(key) != existing.get(key)
                for key in ("lhs", "rhs", "expr", "wrt", "relation", "variable_domains", "study"))
            if existing and str(existing.get("statement") or "").strip() == statement and not encoding_changed:
                # Repeated prose is not new scientific progress and must not reset proof state.
                continue
            if existing:
                claim = {**existing, **claim, "version": version + 1}
            changes.append(build_proposal("claim", object_id=object_id,
                payload={**claim, "strategy": strategy, "subquestion": task.subquestion,
                         "informal": bool(claim.get("informal", True))},
                rationale=clip("; ".join(as_list_of_str(claim.get("reasoning"))[:3]), 400),
                input_versions={object_id: version} if existing else {}))
            if claim.get("study", {}).get("design") == "theory":
                needs.append(ResearchNeed(kind=NeedKind.derivation,
                    statement="独立核验数学候选: " + statement[:500],
                    why="推理产出尚非核验证据，需要回到具体命题执行证明义务",
                    blocked_refs=[ObjectRef(id=object_id, version=int(claim.get("version") or 1))],
                    acceptance=["核对编码与原命题等价、前提与定理条件，并提交实际核验；不能编码时明确未决"],
                    hints={"claim_id": object_id, "trigger_id": "reasoning-proof:" + object_id},
                    blocking=True))
        changes.extend(build_proposal(
            "gap",
            payload={"type": "unresolved_claim", "statement": c.get("statement", ""),
                     "witness": c.get("witness", ""), "subquestion": task.subquestion},
            rationale="推理给出反例候选 (是否成立由判定层核验)",
        ) for c in counterexamples)

        summary = f"策略 {strategy}: 提出 {sum(c.kind == 'claim' for c in changes)} 条新增/修订命题候选"
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
            "fidelity_issues": fidelity_issues,
        }
        if fidelity_issues and not changes and not counterexamples:
            return self.partial(
                task, "候选命题与题面参数冲突，未提交研究结论",
                needs=needs, unresolved=fidelity_issues,
                usage=usage, payload=payload_out)
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
                              unresolved=[*fidelity_issues[:4], *missing[:4], *limitations[:4]],
                              usage=usage, payload=payload_out, replan=replan)

    # ---- 形式化推导: 团队闭环 (合并计划 §3.1 G06) ----
    def formal_closure(self, task: AgentTask, context: ContextPack,
                       runtime: AgentRuntime,
                       usage: UsageRecord) -> AgentResult | None:
        """走完"命题/义务 → 工具核验 → 结构化记录 → 状态归并"。

        与旧实现 (只把计划压成一条 claim 候选) 的区别就是 G06 的缺口本身:
        核验记录与义务必须作为候选**一起提交**, 否则判定层永远看不到"这条命题
        依据什么被支持", 团队形式化路径也就不闭环。

        边界 (没有让步):
        - 判定层仍是唯一状态写入点 —— 这里提交的 claim 候选**不带**科学等级,
          状态由唯一提交口按 `reasoning_kernel.claim_state_for` 重算;
        - 工具结果不可伪造 —— 核验由运行时 (本运行的研究存储) 真执行, 记录带
          输入 hash 与完整 `VerificationResult`;
        - 不能编码的义务保持未决 —— 没有可用后端时如实记 `unsupported`,
          不把"没验"写成"验过"。

        返回 `None` 表示"这条任务没有可形式化的对象", 调用方退回通用策略。
        """
        from src.research.reasoning_kernel import (
            build_design_steps,
            derive_steps_for,
            design_steps_for,
            is_design_claim,
            plan_proof_for,
        )

        subject = _formal_subject(task, context)
        if subject is None:
            return None
        claim, obligations, evidence, form_notes = subject
        if not obligations:
            # 命题已在库里但没有义务: 用**同一份**形式化判据给它拆义务, 而不是让角色
            # 自己列一份 (否则"该命题需要验什么"会有两份说法)。
            obligations = _obligations_for_existing_claim(claim)
            if obligations:
                form_notes.append(
                    f"为已有命题补齐 {len(obligations)} 条待核验义务 (形式化判据)")

        # 1. 证明计划 (规则路径始终计算; 内核只允许模型**增加**待核验内容)
        try:
            plan_outcome = plan_proof_for(claim, obligations=obligations,
                                          available=runtime.verification_backends(),
                                          llm=None)
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

        # 3. 义务集合: 已有 + 计划提出的 + 反方审查新增的 (去重, 按 id 与陈述)
        proposed = [_parse(_obligation_model(), row)
                    for row in (plan_outcome.payload.get("proposed") or [])]
        reviewed = [_parse(_obligation_model(), row)
                    for row in (steps_outcome.payload.get("review_obligations") or [])]
        pending = _merge_obligations(
            claim, [*obligations, *proposed, *reviewed])
        # 反例搜索是**独立的一条路**: 假命题只有它会给出明确结论 (见 helper 文档)。
        pending = _with_counterexample_obligation(claim, pending, runtime)

        # 4. 逐条核验: 工具/规则/独立审查由核验服务分派, 结果作为候选提交
        changes: list[ChangeProposal] = []
        verified = 0
        unresolved: list[str] = []
        needs: list[ResearchNeed] = []
        for obligation in pending:
            outcome = runtime.run_verification(task, claim, obligation,
                                               evidence=evidence)
            usage.tool_calls += 1 if outcome.evaluated else 0
            obligation_payload = {
                **obligation.model_dump(mode="json"),
                **dict(outcome.obligation_updates or {}),
            }
            changes.append(build_proposal(
                "obligation", payload=obligation_payload, object_id=obligation.id,
                rationale=f"义务核验 ({outcome.tool or '未执行'}): "
                          f"{obligation.statement[:120]}",
                input_versions={claim.id: claim.version}))
            if outcome.record is not None:
                verified += 1
                changes.append(build_proposal(
                    "verification",
                    payload={"record": outcome.record.model_dump(mode="json"),
                             "result": (outcome.result.model_dump(mode="json")
                                        if outcome.result is not None else {}),
                             "obligation_id": obligation.id},
                    object_id=outcome.record.id,
                    rationale=f"核验记录 {outcome.tool}: {outcome.record.status}",
                    input_versions=dict(outcome.record.verification_closure or {})))
            if not outcome.closed:
                reason = str(outcome.obligation_updates.get("detail", "")
                             or obligation.statement)
                unresolved.append(f"{obligation.statement[:120]}: {reason[:200]}")
                needs.append(ResearchNeed(
                    kind=(NeedKind.model_condition if obligation.kind == "formal_argument" else NeedKind.clause),
                    statement=f"义务未关闭: {clip(obligation.statement, 160)}",
                    why=reason[:300],
                    blocked_refs=[ObjectRef(id=claim.id, version=claim.version)],
                    hints={"claim_id": claim.id, "obligation_id": obligation.id,
                           "trigger_id": "obligation:" + obligation.id},
                    acceptance=["给出可定位依据、工具核验记录或显式声明"],
                    blocking=False))

        # 5. claim 候选: 只带**结构化事实** (陈述/形式片段/步骤), 不带科学等级
        claim_payload = claim.model_dump(mode="json")
        for name in ("status", "assurance", "support_kind", "validation_status",
                     "coverage", "verification_scope", "verification_closure"):
            claim_payload.pop(name, None)
        claim_payload["strategy"] = str(plan_outcome.payload.get("strategy")
                                        or claim_payload.get("strategy") or "derivation")
        if not is_design_claim(claim):
            claim_payload["steps"] = steps_outcome.payload.get("step_items") or []
        claim_payload["kernel"] = True
        claim_payload["subquestion"] = task.subquestion
        changes.append(build_proposal(
            "claim", payload=claim_payload, object_id=claim.id,
            rationale=(f"形式化闭环: {plan_outcome.summary}; {steps_outcome.summary}; "
                       f"核验 {verified} 条"),
            input_versions={claim.id: claim.version},
            may_change_conclusion=False))
        # 证据归属 (§6.1): 把上下文里的可用证据绑定到**本条命题的当前版本**。
        # 没有这一步, 交付包里只有"一堆材料", 讲不出"这条结论依据哪条来源、适用条件
        # 是什么"; 而关系强度取自材料自己的判定 (不确定就是 insufficient), 不默认支持。
        changes.extend(_evidence_links_for(task, claim, context))
        for kind, object_id, payload in steps_outcome.writes:
            if kind == "attempt":
                changes.append(build_proposal("attempt", payload=payload, object_id=object_id,
                    rationale="保存实际推导步骤；complete 表示记录完整，不表示命题已证明",
                    input_versions={claim.id: claim.version}))

        form_notes.extend(steps_outcome.notes)
        # 摘要里保留核验与独立审查的计数: 界面与用例据此看出"到底跑过什么",
        # 只报"闭环了"会掩盖"一条义务都没验"这种真实情况。
        summary = (f"形式化闭环: {plan_outcome.summary}; {steps_outcome.summary}; "
                   f"{len(pending)} 条义务, 核验 {verified} 条, "
                   f"未关闭 {len(unresolved)} 条")
        runtime.emit("reasoning_kernel_used", {
            "task_id": task.task_id, "claim_id": claim.id,
            "design": is_design_claim(claim), "obligations": len(pending),
            "verified": verified, "unresolved": len(unresolved)})
        return self.completed(
            task, summary, changes=changes, needs=needs,
            unresolved=[*form_notes[:4], *unresolved[:8]],
            usage=usage,
            payload={"schema": "ReasoningResult/v1", "kernel": True,
                     "strategy": "derivation", "claim_id": claim.id,
                     "plan": plan_outcome.payload, "steps": steps_outcome.payload,
                     "obligations": len(pending), "verified": verified})

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


def _formalisable_subject(context: ContextPack, task: AgentTask | None = None):
    """从角色上下文还原"可形式化的命题 + 义务 + 证据"。

    图状态里只放引用与摘要 (`unified_state` 的约束), 因此角色侧按引用重建**领域对象**
    再交给内核 —— 内核只接受领域对象, 不接受任意字典, 这样"内核里没有 LLM、不接受
    自由文本当命题"就是类型层面的事实。
    """
    from src.research.schemas import Claim, ProofObligation, SourceEvidence

    rows = list(context.objects.get("claim") or [])
    requested = str((task.hints or {}).get("claim_id") or "") if task else ""
    requested_ids = [requested] if requested else [ref.id for ref in (task.input_refs if task else [])
                                                if any(row.get("id") == ref.id for row in rows)]
    if requested_ids:
        rows = [row for row in rows if row.get("id") in requested_ids]
    else:
        # General exploration must not repeatedly pick an unencoded background claim.
        rows = [row for row in rows if (row.get("lhs") and row.get("rhs"))
                or (row.get("expr") and row.get("wrt")) or row.get("design_v")]
    claim: Claim | None = None
    for row in rows:
        claim = _parse(Claim, row)
        if claim is not None and claim.statement:
            break
        claim = None
    if claim is None:
        return None, [], []

    obligations = [o for o in (_parse(ProofObligation, row)
                               for row in context.objects.get("obligation") or [])
                   if o is not None and o.claim_id in ("", claim.id) and o.claim_version == claim.version]
    evidence = [e for e in (_parse(SourceEvidence, row)
                            for row in context.objects.get("evidence") or [])
                if e is not None]
    return claim, obligations, evidence


def _formal_subject(task: AgentTask, context: ContextPack):
    """这条任务要形式化**什么**: 上下文里已有的命题, 或从题面自己形式化出来的。

    为什么角色必须能自己形式化 (合并计划 §3.1 G06 / §9 R2): 团队要接管形式化研究,
    就不能假设"已有人把命题放好了"。此前团队路径只在上下文里找 claim, 找不到就
    退回通用策略 → 一条纯形式化题面**永远进不了形式化闭环** (实测: 团队跑
    `2-(211,15,1)` 那类题只有 evidence/reasoning(blocked)/writing 三个角色)。
    返回 `(claim, obligations, evidence, notes)`; 无法形式化时返回 `None`。
    """
    claim, obligations, evidence = _formalisable_subject(context, task)
    if claim is not None:
        return claim, obligations, evidence, []
    if task.hints.get("claim_id"):
        return None  # Never substitute another assertion for a missing requested target.

    formulated = _formulate_from_request(task, context)
    if formulated is None:
        return None
    claim, obligations, notes = formulated
    return claim, obligations, evidence, notes


def _formulate_from_request(task: AgentTask, context: ContextPack):
    """把题面形式化成"命题 + 义务" (确定性路径, 零 LLM)。

    形式化能力本身在 `research/formulation.py` (从旧引擎按职责抽出), 因此团队与
    引擎对同一题面得到的是**同一批**命题与义务, 不会各写一套编码。

    候选文本**逐个尝试**而不是拼在一起: 原始请求、子问题措辞、主控画像是同一道题的
    三种说法, 拼起来会让关系符两侧混入后一种说法的文本 —— 实测 `x**2 >= x` 的右端
    变成 `"x 存在性判定"`, 交给工具就是语法错误, 于是报告 unsupported, 而命题其实
    完全可判定。谁先能形式化就用谁, 不混合。
    """
    candidates = [
        str(context.request or ""),
        task_intent(task),
        str((context.objects.get("brief") or [{}])[0].get("main_question", "")
            if context.objects.get("brief") else ""),
    ]
    candidates.extend(str(row.get("text") or row.get("content") or row.get("extracted_text") or "")
                      for row in context.attachments)
    seen: set[str] = set()
    for text in candidates:
        body = text.strip()
        if not body or body in seen:
            continue
        seen.add(body)
        formulated = _try_formulate(body, task)
        if formulated is None:
            continue
        claim, obligations, notes = formulated
        return claim, obligations, notes
    return None


def _try_formulate(text: str, task: AgentTask):
    """对**一段**文本做形式化; 抽不出命题时返回 None。"""
    from src.research.formulation import formulate_problem

    try:
        formulated = formulate_problem(
            request=text, project_id=task.project_id or "research",
            problem_id=task.problem_id or "problem",
            available=_available_backends())
    except Exception:  # noqa: BLE001 - 该说法形式化失败就换下一种, 不编命题
        return None
    if not formulated.claims:
        return None
    notes = list(formulated.notes)
    claim = _first_claim(formulated)
    if claim is None:
        return None
    if len(formulated.claims) > 1:
        notes.append(f"题面抽出 {len(formulated.claims)} 条命题, 本次先处理第一条")
    obligations = [o for o in (_parse(_obligation_model(),
                                      o.model_dump(mode="json")
                                      if hasattr(o, "model_dump") else o)
                               for o in formulated.obligations) if o is not None]
    # 义务可能没有绑定命题 (跨命题共享): 只保留指向本条命题的, 没有归属的归给本条。
    obligations = [o for o in obligations if o.claim_id in ("", claim.id)]
    for obligation in obligations:
        if not obligation.claim_id:
            obligation.claim_id = claim.id
            obligation.claim_version = claim.version
    return claim, obligations, notes


def _first_claim(formulated: Any):
    """取第一条可解析的命题; 无法解析时返回 None (不猜、不补默认语义)。"""
    for item in formulated.claims:
        claim = _parse(_claim_model(), item.model_dump(mode="json")
                       if hasattr(item, "model_dump") else item)
        if claim is not None and claim.statement:
            return claim
    return None


def _available_backends() -> dict[str, bool]:
    """形式化时用的后端可用性 (义务的 `acceptance_method` 由它决定)。"""
    from src.verification.runner import available_tools

    return available_tools()


def _claim_model():
    from src.research.schemas import Claim

    return Claim


def _obligation_model():
    from src.research.schemas import ProofObligation

    return ProofObligation


def _evidence_links_for(task: AgentTask, claim: Any,
                        context: ContextPack) -> list[ChangeProposal]:
    """上下文证据 → **本条命题当前版本**的归属候选 (§6.1 EvidenceLink)。

    只在材料确实带支持关系判定时才写"条件匹配"; 其余情况如实写 `insufficient`
    (检索命中不等于支持)。关系的来源是材料自己的 `relation` 字段, 这里不重新判语义
    —— 规则层判不出"这段文字支持这个论断", 编一个关系比不写更糟。
    """
    from src.research.schemas import (
        EvidenceLink,
        ObjectRef,
        SupportKindOfEvidence,
        ValidationStatus,
    )

    valid = {kind.value for kind in SupportKindOfEvidence}
    out: list[ChangeProposal] = []
    for row in (context.objects.get("evidence") or [])[:20]:
        source_id = str(row.get("source_id") or row.get("id") or "")
        if not source_id:
            continue
        relation = str(row.get("relation") or "insufficient")
        if relation not in valid:
            relation = "insufficient"
        link = EvidenceLink(
            claim_ref=ObjectRef(id=claim.id,
                                version=int(getattr(claim, "version", 1) or 1)),
            source_ref=ObjectRef(id=source_id,
                                 version=int(row.get("version", 1) or 1)),
            relation=SupportKindOfEvidence(relation),
            excerpt=str(row.get("excerpt", "") or "")[:300],
            locator=str(row.get("locator", "") or row.get("location", "") or ""),
            condition_match=relation in ("supports", "partially_supports"),
            condition_notes=str(row.get("note", "") or ""),
            review_status=(ValidationStatus.unchecked if relation == "insufficient"
                           else ValidationStatus.verified),
            reviewer="reasoning",
        )
        out.append(build_proposal(
            "evidence_link", payload=link.model_dump(mode="json"),
            object_id=link.id,
            rationale=f"命题 {claim.id} ← 证据 {source_id} ({relation})",
            input_versions={claim.id: int(getattr(claim, "version", 1) or 1)},
        ))
    return out


def _obligations_for_existing_claim(claim: Any) -> list[Any]:
    """已有命题的义务清单 (来自唯一的形式化判据, 不是角色自己列的)。"""
    from src.research.problem_formulator import obligations_for_claim
    from src.research.schemas import Relation

    wants_equality = getattr(claim, "relation", None) == Relation.eq
    rows = obligations_for_claim(claim, _available_backends(),
                                 wants_equality=wants_equality)
    out = []
    for row in rows:
        parsed = _parse(_obligation_model(), row.model_dump(mode="json")
                        if hasattr(row, "model_dump") else row)
        if parsed is not None:
            out.append(parsed)
    return out


def _with_counterexample_obligation(claim: Any, obligations: list[Any],
                                    runtime: AgentRuntime) -> list[Any]:
    """可编码反例搜索时，补一条**反例义务** (合并计划 §5.4 的能力)。

    为什么必须独立于"命题有没有其他义务": 反例搜索回答的是"命题是否根本不成立",
    而证明义务回答的是"命题能否被证明"。只在前者存在时才需要后者 —— 实测
    `x**2 >= x` 这类**假命题**在只有 `prove_inequality` 义务时只会得到 unsupported,
    结论停在"受阻", 而它其实有一个明确的反例。

    反例义务的结论同样要经核验: 只有拿到**可回代的反例**才算被否决,
    工具跑过没找到反例不构成证明 (`counterexample_verdict_for` 的判据)。
    """
    from src.research.schemas import ProofObligation

    if any(getattr(o, "kind", "") == "refute" for o in obligations):
        return obligations
    try:
        if not runtime.verification_service().has_counterexample_check(claim):
            return obligations
    except Exception:  # noqa: BLE001 - 不可用时不造假: 少一条义务, 不假装搜过
        return obligations
    obligations.append(ProofObligation(
        statement=f"搜索反例: {clip(getattr(claim, 'statement', ''), 160)}",
        kind="refute",
        acceptance_method="refute",
        claim_id=claim.id,
        claim_version=int(getattr(claim, "version", 1) or 1),
        # **非必要义务**: "没找到反例"不构成证明, 因此它不能挡住已证明的结论。
        # 但一旦真的找到反例, 状态归并会把命题判为 refuted (反例不受 required 限制)。
        required=False,
        detail="找满足前提的反例; 找不到不等于命题成立 (只记有限测试证据)",
    ))
    return obligations


def _merge_obligations(claim: Any, obligations: list[Any]) -> list[Any]:
    """义务去重: 同 id 只留一条; 同陈述只留一条 (反方审查会重复提出同一条)。"""
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    out: list[Any] = []
    for obligation in obligations:
        if obligation is None:
            continue
        if obligation.claim_id and obligation.claim_id != claim.id:
            continue
        key = str(getattr(obligation, "statement", "")).strip()
        if obligation.id in seen_ids or (key and key in seen_text):
            continue
        seen_ids.add(obligation.id)
        if key:
            seen_text.add(key)
        out.append(obligation)
    return out


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


def _research_text(context: ContextPack) -> str:
    parts = [context.request]
    parts.extend(str(row.get("main_question") or "") for row in context.objects.get("brief", []))
    parts.extend(str(row.get("text") or row.get("content") or row.get("extracted_text") or "")
                 for row in context.attachments)
    from src.research.query_planner import research_question_text
    return research_question_text("\n".join(parts))


def _topic_of(context: ContextPack) -> str:
    for row in context.sources:
        candidate = str(row.get("source_set_id", "") or "")
        if candidate:
            return candidate
    return ""


def _has_data(context: ContextPack) -> bool:
    return bool(context.objects.get("dataset"))
