from __future__ import annotations

"""ModelingAgent 问题建模 (合并计划 §3.1 / §7.1)。

职责: 从研究契约与可定位资料提取变量、量纲、域、约束、机制与假设, 比较候选模型。
提交 `ModelProposal`: 数学/概念模型、形式化映射、假设来源、模型比较、待核查条件。

合并要点 (§7.1): `loop._act_propose_model` + `modeling.py` + `problem_formulator.py`
的能力归属这里 —— 由 LLM 形成模型提案, 由规则/形式解析检验**忠实度与结构**。
因此 `validate_model_payload()` 是独立的确定性检查, 不依赖模型自述。
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
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec
from src.agents.tools import readonly_data_tools

__all__ = ["ModelingAgent", "validate_model_payload"]


class ModelingAgent(AgentBase):
    """问题建模角色。"""

    role = "modeling"
    prompt_version = "modeling/v1"
    kinds = ("model", "assumption", "definition")

    SYSTEM = """你是"问题建模"智能体。

你的任务: 按研究契约把问题落成**可检验的模型**, 并说明它为什么忠实于原题。

必须给出:
1. 变量与量纲 (每个变量: 名称、含义、单位/量纲、取值范围或域);
2. 假设 (逐条写出来源: 题目给定 / 资料支持 / 本研究引入);
3. 机制或方程的定性形式;
4. 候选模型比较: 至少说明为什么选它、舍弃了哪些、各自在什么条件下适用;
5. 待核查条件 (还没有证据支撑的部分)。

纪律:
- 不得为了适配某个求解器而偷换题意; 量词与域必须与原题一致;
- 变不出来就如实列为未知, 不要编造;
- 只提交**候选模型**, 不声明结论成立。

最终输出 JSON:
{"models": [{"name": "", "mechanism": "", "variables": [{"symbol": "", "meaning": "", "unit": "", "domain": ""}],
            "assumptions": [{"statement": "", "origin": "user_assumption|proposed|external"}],
            "applicability": "", "why_chosen": "", "rejected": [{"name": "", "reason": ""}],
            "open_conditions": [""]}],
 "unknowns": [""]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        # 建模需要看数据的真实结构 (列/单位/缺失), 因此给只读数据工具
        return readonly_data_tools()

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        candidates: list[dict[str, Any]] = []
        unknowns: list[str] = []
        parse_note = ""
        # 有已读原文时先走**抽取出来的**建模内核 (合并计划 M2): 它从证据构造互相竞争的
        # 候选机制并显式选中一个, 与旧引擎路径共用同一份实现 (差异测试见
        # tests/test_reasoning_kernel.py)。没有可用原文时内核返回
        # `requires_evidence`, 角色据此提出补检索需求而**不编模型**。
        kernel_result = self.propose_via_kernel(task, context, runtime, usage)
        if kernel_result is not None:
            return kernel_result

        if runtime.llm_available() and not task.budget.exceeded_by(usage):
            from langchain_core.messages import HumanMessage, SystemMessage

            prompt = (
                f"# 建模目标\n{task_intent(task)}\n"
                f"# 验收标准\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria) + "\n"
                f"# 上下文\n{context_summary(context)}"
            )
            llm = runtime.llm(task, usage, stage="modeling")
            try:
                result = llm.invoke([SystemMessage(content=self.SYSTEM),
                                     HumanMessage(content=prompt)])
                payload, parse_note = extract_json(getattr(result, "content", ""))
            except Exception as e:  # noqa: BLE001 - 调用失败退到确定性路径
                payload, parse_note = None, f"建模调用失败: {e}"
            if isinstance(payload, dict):
                candidates = [c for c in (payload.get("models") or [])
                              if isinstance(c, dict)]
                unknowns = as_list_of_str(payload.get("unknowns"))

        if not candidates:
            candidates = self.fallback_models(task, context)
            if parse_note:
                unknowns.append(f"模型输出解析失败, 已用规则建模: {parse_note}")

        if not candidates:
            return self.blocked(
                task,
                "无法从当前契约与资料形成候选模型",
                needs=[ResearchNeed(
                    kind=NeedKind.model_condition,
                    statement="缺少可用于建模的变量/条件说明",
                    why="没有变量与域就无法建立可检验模型",
                    acceptance=["补充变量定义与适用范围, 或明确说明题目不要求建模"],
                    blocking=False)],
                usage=usage)

        changes: list[ChangeProposal] = []
        checks: list[str] = []
        for index, model in enumerate(candidates):
            problems = validate_model_payload(model)
            checks.extend(problems)
            changes.append(build_proposal(
                "model",
                payload={**model, "index": index, "validation_problems": problems,
                         "subquestion": task.subquestion},
                rationale=clip(str(model.get("why_chosen", "") or ""), 300)
                          or "候选模型 (忠实度由规则检查)",
                input_versions={},
            ))
        payload_out = {"schema": "ModelProposal/v1", "models": candidates,
                       "unknowns": unknowns, "fidelity_checks": checks}
        summary = f"提出 {len(candidates)} 个候选模型"
        if checks:
            summary += f"; 忠实度检查发现 {len(checks)} 处需核查"
        return self.completed(task, summary, changes=changes, unresolved=checks[:6],
                              usage=usage, payload=payload_out)

    # ---- 建模内核 (M2): 从证据构造竞争候选并显式选中一人 ----
    def propose_via_kernel(self, task: AgentTask, context: ContextPack,
                           runtime: AgentRuntime,
                           usage: UsageRecord) -> AgentResult | None:
        """走 `reasoning_kernel.model_proposal_for`。

        返回 `None` 表示"上下文里没有可建模的命题/原文", 调用方退回通用路径
        (LLM 提案或规则建模); 返回 `blocked` 表示"内核明确说缺原文" —— 那时必须提
        补检索需求, 而不是生成一个没有来源的模型。
        """
        from src.research.reasoning_kernel import model_proposal_for

        claim, evidence = _modelling_subject(context)
        if claim is None:
            return None
        if not evidence:
            # 有命题但没读到原文: 内核会拒绝, 这里如实提出需求 (不调 LLM 编模型)
            needs = [ResearchNeed(
                kind=NeedKind.model_condition,
                statement="缺少可用于建模的已读原文",
                why="没有可定位原文就无法给出有来源的候选机制",
                acceptance=["先定向检索并读取原文, 或明确说明该问题不需要建模"],
                blocking=False)]
            return self.blocked(task, "缺少可用于建模的已读原文", needs=needs,
                                usage=usage)
        outcome = model_proposal_for(claim, evidence=evidence,
                                     contract=None, available=None)
        if not outcome.ok:
            if outcome.payload.get("requires_evidence"):
                return self.blocked(
                    task, outcome.summary,
                    needs=[ResearchNeed(
                        kind=NeedKind.model_condition,
                        statement=outcome.summary,
                        why="建模必须基于已读原文",
                        acceptance=["补齐可定位原文后重新建模"])],
                    usage=usage)
            return None

        changes = [build_proposal(
            "model",
            payload={**model, "index": index,
                     "why_chosen": bool(model.get("selected")),
                     "subquestion": task.subquestion},
            rationale=("候选机制 (由已读原文比较得出; 是否忠实仍由规则检查)"
                       if not model.get("selected")
                       else "选中机制 (比较结果见 comparison)"),
            input_versions={claim.id: claim.version},
        ) for index, model in enumerate(outcome.payload["models"])]
        runtime.emit("modeling_kernel_used", {
            "task_id": task.task_id, "claim_id": claim.id,
            "candidates": len(outcome.payload["models"]),
        })
        return self.completed(
            task, f"建模 (内核): {outcome.summary}",
            changes=changes, unresolved=outcome.notes, usage=usage,
            payload={"schema": "ModelProposal/v1", "kernel": True,
                     "claim_id": claim.id,
                     "chosen_id": outcome.payload["chosen_id"],
                     "comparison": outcome.payload["comparison"],
                     "declaration": outcome.payload["declaration"],
                     "distinguishing": outcome.payload["distinguishing"]})

    # ---- 确定性建模 ----
    def fallback_models(self, task: AgentTask,
                        context: ContextPack) -> list[dict[str, Any]]:
        """规则建模: 从上下文里已识别出的对象/变量构造一个**结构完整**的候选。

        它不发明变量: 只把已经写出来的对象、单位、约束搬到模型结构里, 未知的一律
        列进 `open_conditions`。这样"没有 LLM"时也能给出可检查的模型, 而不是空壳。
        """
        variables = as_list_of_str(_first(context, "brief", "variables"))
        objects = as_list_of_str(_first(context, "brief", "objects")
                                 or _first(context, "claim", "objects"))
        constraints = as_list_of_str(_first(context, "brief", "constraints"))
        quantifier = str(_first(context, "brief", "quantifiers") or "")
        if not variables and not objects:
            return []
        return [{
            "name": "问题原样形式化 (规则建模)",
            "mechanism": "未由模型给出机制; 只记录题目已声明的对象与约束",
            "variables": [{"symbol": name, "meaning": "", "unit": "", "domain": ""}
                          for name in (variables or objects)],
            "assumptions": [],
            "applicability": clip(task.objective, 300),
            "why_chosen": "规则建模: 保持原题的量词与对象, 不引入未声明的机制",
            "rejected": [],
            "open_conditions": ([f"量词: {quantifier}"] if quantifier else [])
                                + [f"约束: {c}" for c in constraints[:6]]
                                + ["机制与变量语义尚需确认 (无模型输出时不做假设)"],
        }]


def validate_model_payload(model: dict[str, Any]) -> list[str]:
    """忠实度与结构检查 (零 LLM)。

    只检查**可判定**的问题: 缺名称、变量无量纲/域、假设无来源、机制为空。
    这些是"模型看起来完整但其实无法检验"的常见形式, 与科学正确性无关。
    """
    problems: list[str] = []
    name = str(model.get("name", "") or "").strip()
    if not name:
        problems.append("候选模型缺少名称")
    if not str(model.get("mechanism", "") or "").strip():
        problems.append(f"模型 {name or '(未命名)'} 没有说明机制/方程形式")
    variables = model.get("variables") or []
    if not isinstance(variables, list):
        problems.append(f"模型 {name or '(未命名)'} 的 variables 不是列表")
    else:
        for index, variable in enumerate(variables, 1):
            if not isinstance(variable, dict):
                problems.append(f"模型 {name} 的第 {index} 个变量不是对象")
                continue
            if not str(variable.get("symbol", "") or "").strip():
                problems.append(f"模型 {name} 的第 {index} 个变量缺少符号")
            if not str(variable.get("domain", "") or "").strip():
                problems.append(
                    f"模型 {name} 的变量 {variable.get('symbol', index)} 未给出域/取值范围")
    assumptions = model.get("assumptions") or []
    if isinstance(assumptions, list):
        for index, assumption in enumerate(assumptions, 1):
            if not isinstance(assumption, dict):
                problems.append(f"模型 {name} 的第 {index} 条假设不是对象")
                continue
            if not str(assumption.get("origin", "") or "").strip():
                problems.append(
                    f"模型 {name} 的第 {index} 条假设没有来源 (题目/资料/本研究)")
    return problems


def _modelling_subject(context: ContextPack):
    """从角色上下文还原"可建模的命题 + 已读原文"。

    与 `ReasoningAgent` 的还原口径一致: 图状态只放引用, 角色侧按引用重建领域对象,
    因此内核只接受领域对象 (类型层面保证它不会被喂进自由文本当命题)。
    """
    from src.research.schemas import Claim, SourceEvidence

    claim: Claim | None = None
    for row in context.objects.get("claim") or []:
        candidate = _parse(Claim, row)
        if candidate is not None and candidate.statement:
            claim = candidate
            break
    if claim is None:
        return None, []
    evidence = [e for e in (_parse(SourceEvidence, row)
                            for row in context.objects.get("evidence") or [])
                if e is not None and e.excerpt]
    return claim, evidence


def _parse(model: Any, row: Any) -> Any:
    """快照行 → 领域对象; 字段不全返回 None (不猜)。"""
    if not isinstance(row, dict):
        return None
    try:
        return model(**row)
    except Exception:  # noqa: BLE001 - 单行字段不全就跳过该行
        return None


def _first(context: ContextPack, kind: str, key: str) -> Any:
    rows = context.objects.get(kind) or []
    for row in rows:
        if row.get(key):
            return row[key]
    return []
