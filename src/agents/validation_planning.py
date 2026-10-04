from __future__ import annotations

"""ValidationPlanningAgent 验证方案 (合并计划 §3.1 / §7.1)。

职责: 判断什么验证能区分竞争解释, 产出仿真/实验**实现建议**。
提交 `ValidationPlan`: 变量/单位、参数来源、对照、指标、判据、预算与停止条件,
并明确标注**未执行**。

合并要点 (§7.1): `loop._act_design_experiment` + `experiments/planner.py/schemas.py/
validator.py` 归这里; 当前的规则模板作草稿与校验, 增加按具体模型和缺口修订。

硬约束: 本轮范围只到"实施建议" —— 不自动执行仿真/实验, 也不把查询到的数据
写成新实验 (合并计划 §1 与 §11)。
"""

from typing import Any

from src.agents.base import (
    AgentBase,
    build_proposal,
    clip,
    context_summary,
    extract_json,
    task_intent,
)
from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ContextPack,
    NeedKind,
    ResearchNeed,
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec
from src.agents.tools import readonly_data_tools, stats_tools

__all__ = ["ValidationPlanningAgent", "validate_plan_payload"]


class ValidationPlanningAgent(AgentBase):
    """验证方案角色。"""

    role = "validation"
    prompt_version = "validation/v1"
    kinds = ("validation_plan",)

    SYSTEM = """你是"验证方案"智能体。

你的任务: 针对未决义务或竞争解释, 设计**能区分它们**的验证方案, 并写成可执行的建议。

方案必须包含:
1. 要区分的假设/解释 (至少两个, 否则不需要验证);
2. 自变量/干预、因变量/观测、单位;
3. 参数取值与**来源** (资料实测 / 文献 / 假设, 逐项标注);
4. 对照组与混杂控制;
5. 指标、判据 (什么结果支持哪种解释) 与阈值;
6. 所需数据、工具与预算、停止条件;
7. 该方案的局限与什么情况下它无法区分。

纪律:
- 只给**建议**: 你不执行仿真或实验, 也不把查询到的数据说成新的实验结果;
- 参数没有来源就标"假设"并列出核查需求, 不得编造数值;
- 方案无法区分竞争解释时如实说明。

最终输出 JSON:
{"competing": [""], "design": {"treatment": "", "outcome": "", "units": "", "controls": [""], "confounders": [""]},
 "parameters": [{"name": "", "value": "", "unit": "", "source": "measured|literature|assumption", "note": ""}],
 "metrics": [{"name": "", "criterion": "", "supports": ""}],
 "budget": {"compute": "", "data": "", "time": ""}, "stop_conditions": [""],
 "limitations": [""], "needs": [{"kind": "more_sources|empirical_support|model_condition", "statement": "", "why": ""}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        return [*readonly_data_tools(), *stats_tools()]

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        plan: dict[str, Any] = {}
        parse_note = ""
        # 先把内核生成的规格 (若有) 作为草稿交给模型/规则细化。
        # 合并计划 M2: 实验规格的**构造与拒绝判据**在
        # `reasoning_kernel.validation_plan_for` —— 它保证 `executed=False`, 并在
        # "既没有竞争预测也没有未闭义务"时拒绝生成, 不套模板充数。
        kernel_outcome = self.plan_via_kernel(task, context)
        if kernel_outcome is not None and not kernel_outcome.ok \
                and kernel_outcome.payload.get("refused"):
            # 内核明确拒绝: 不得用规则模板顶上, 否则就是"套模板充数"
            return self.partial(task, kernel_outcome.summary,
                                unresolved=[kernel_outcome.summary], usage=usage,
                                payload={"schema": "ValidationPlan/v1",
                                         "refused": True,
                                         "reason": kernel_outcome.payload.get("reason", "")})
        if kernel_outcome is not None and kernel_outcome.ok:
            # 内核已经给出规格: 它是 `ExperimentSpec` 的结构, 不能当自由文本方案再走
            # `validate_plan_payload` (那套字段是"自然语言方案"的契约, 会误判为缺判据)。
            return self.completed(
                task, kernel_outcome.summary,
                changes=[build_proposal(
                    "validation_plan",
                    payload={**kernel_outcome.payload["spec"],
                             "subquestion": task.subquestion, "executed": False,
                             "kernel": True},
                    rationale="按竞争预测/未闭义务给出实验实施建议 (**未执行**)",
                    input_versions={})],
                unresolved=list(kernel_outcome.notes), usage=usage,
                payload={"schema": "ValidationPlan/v1", "kernel": True,
                         "spec": kernel_outcome.payload["spec"],
                         "executed": False})

        if runtime.llm_available() and not task.budget.exceeded_by(usage):
            from langchain_core.messages import HumanMessage, SystemMessage

            prompt = (
                f"# 验证需求\n{task_intent(task)}\n"
                f"# 预期收益\n{task.expected_gain or '(未声明)'}\n"
                f"# 上下文\n{context_summary(context)}"
            )
            llm = runtime.llm(task, usage, stage="validation")
            try:
                result = llm.invoke([SystemMessage(content=self.SYSTEM),
                                     HumanMessage(content=prompt)])
                payload, parse_note = extract_json(getattr(result, "content", ""))
            except Exception as e:  # noqa: BLE001
                payload, parse_note = None, f"验证方案调用失败: {e}"
            if isinstance(payload, dict):
                plan = payload

        if not plan:
            plan = self.fallback_plan(task, context)
            if parse_note:
                plan.setdefault("limitations", []).append(
                    f"模型输出不可用 ({parse_note}), 本方案由规则模板生成")

        problems = validate_plan_payload(plan)
        if problems and not (plan.get("metrics") or plan.get("parameters")):
            return self.blocked(
                task, "验证方案缺少可执行内容: " + "; ".join(problems[:3]),
                needs=[ResearchNeed(
                    kind=NeedKind.empirical_support,
                    statement="缺少可用于设计验证的模型与数据约束",
                    why="没有变量/数据约束就无法给出可区分检验",
                    acceptance=["补充模型变量与可用数据范围"],
                )],
                usage=usage, payload={"schema": "ValidationPlan/v1", "plan": plan})

        plan["executed"] = False          # 明确标注: 建议, 未执行
        plan["validation_problems"] = problems
        change = build_proposal(
            "validation_plan",
            payload={**plan, "subquestion": task.subquestion, "executed": False},
            rationale="按竞争解释与可用数据给出验证实施建议 (未执行)",
            input_versions={},
        )
        summary = ("给出验证方案 (未执行): "
                   f"{len(plan.get('competing') or [])} 个竞争解释, "
                   f"{len(plan.get('metrics') or [])} 个判据")
        if problems:
            summary += f"; {len(problems)} 处需核查"
        return self.completed(task, summary, changes=[change],
                              unresolved=problems[:5], usage=usage,
                              payload={"schema": "ValidationPlan/v1", "plan": plan,
                                       "executed": False})

    # ---- 验证方案内核 (M2) ----
    def plan_via_kernel(self, task: AgentTask, context: ContextPack):
        """走 `reasoning_kernel.validation_plan_for`: 生成**未执行**的实验/仿真规格。

        返回 `None` 表示上下文里没有可规划的对象; 返回 `ok=False` 表示内核**明确拒绝**
        (没有竞争预测也没有未闭义务) —— 那时不得用规则模板顶上, 否则就是"套模板充数"。
        """
        from src.research.reasoning_kernel import validation_plan_for

        claim = _planning_subject(context)
        if claim is None:
            return None
        distinguishing = _distinguishing_from(context)
        open_obligation = bool(_rows(context, "obligation"))
        return validation_plan_for(
            claim, distinguishing=distinguishing, open_obligation=open_obligation,
            gaps=_rows(context, "gap"), evidence=_evidence_rows(context),
            model=next(iter(_rows(context, "model")), {}) or {})

    # ---- 规则模板 ----
    def fallback_plan(self, task: AgentTask, context: ContextPack) -> dict[str, Any]:
        """规则模板: 从模型与数据卡里取真实变量, 未知项标为假设。

        它**不发明**参数值: 参数只来自上下文里已登记的数据卡/模型, 其余进
        `needs` 由主控派检索或建模。
        """
        models = context.objects.get("model") or []
        variables: list[dict[str, str]] = []
        for model in models:
            for variable in model.get("variables") or []:
                if isinstance(variable, dict):
                    variables.append({
                        "name": str(variable.get("symbol", "") or ""),
                        "unit": str(variable.get("unit", "") or ""),
                        "domain": str(variable.get("domain", "") or ""),
                    })
        datasets = context.objects.get("dataset") or []
        competing = [
            clip(str(model.get("name", "") or ""), 120) for model in models[:4]
        ] or ["当前结论成立", "当前结论不成立 (存在反例或替代解释)"]
        parameters = [{
            "name": str(item.get("name", "") or ""),
            "value": "",
            "unit": str(item.get("unit", "") or ""),
            "source": "assumption",
            "note": "规则模板未取得该参数取值: 需检索或实测",
        } for item in datasets[:5]]
        return {
            "competing": competing,
            "design": {
                "treatment": str(_first(context, "brief", "variables") or ""),
                "outcome": clip(task_intent(task), 160),
                "units": "",
                "controls": [],
                "confounders": [],
            },
            "parameters": parameters,
            "metrics": [{
                "name": "与竞争解释的可区分性",
                "criterion": "需要给出定量判据; 规则模板未指定阈值",
                "supports": "",
            }],
            "budget": {"compute": "未估算", "data": "见资料范围", "time": "未估算"},
            "stop_conditions": ["达到判据阈值", "预算上限", "无法取得必需数据时停止"],
            "limitations": [
                "本方案由规则模板生成, 未按具体竞争解释细化",
                *(["上下文里没有候选模型, 竞争解释由通用二分法代替"] if not models else []),
            ],
            "needs": ([{
                "kind": "empirical_support",
                "statement": "缺少参数实测值/文献值",
                "why": "没有参数来源时方案只能停在建议层面",
            }] if not datasets else []),
        }


def validate_plan_payload(plan: dict[str, Any]) -> list[str]:
    """确定性检查: 方案是否**可区分**且**未越权**。

    只查可判定项: 竞争解释数量、参数来源标注、判据是否有阈值、是否误标已执行。
    """
    problems: list[str] = []
    competing = plan.get("competing") or []
    if not isinstance(competing, list) or len(competing) < 2:
        problems.append("验证方案没有列出至少两个竞争解释 (无法区分则不需要该验证)")
    metrics = plan.get("metrics") or []
    if not metrics:
        problems.append("验证方案没有给出判据指标")
    else:
        for index, metric in enumerate(metrics, 1):
            if not isinstance(metric, dict):
                problems.append(f"第 {index} 个判据不是对象")
                continue
            criterion = str(metric.get("criterion", "") or "")
            if not criterion.strip():
                problems.append(f"第 {index} 个判据没有给出判据内容")
            elif "未指定阈值" in criterion or "未给出" in criterion:
                problems.append(f"第 {index} 个判据缺少可执行的阈值")
    for index, parameter in enumerate(plan.get("parameters") or [], 1):
        if not isinstance(parameter, dict):
            problems.append(f"第 {index} 个参数不是对象")
            continue
        source = str(parameter.get("source", "") or "")
        if source not in ("measured", "literature", "assumption"):
            problems.append(
                f"参数 {parameter.get('name', index)} 的来源未标注 "
                f"(measured/literature/assumption)")
    if plan.get("executed") is True:
        problems.append("本轮范围内验证方案不得标记为已执行")
    return problems


def _rows(context: ContextPack, kind: str) -> list[dict[str, Any]]:
    return [row for row in (context.objects.get(kind) or []) if isinstance(row, dict)]


def _evidence_rows(context: ContextPack) -> list[Any]:
    from src.research.schemas import SourceEvidence

    return [item for item in (_parse_row(SourceEvidence, row)
                              for row in _rows(context, "evidence"))
            if item is not None]


def _parse_row(model: Any, row: dict[str, Any]) -> Any:
    """快照行 → 领域对象; 字段不全返回 None (不猜)。"""
    try:
        return model(**row)
    except Exception:  # noqa: BLE001 - 单行字段不全就跳过该行
        return None


def _planning_subject(context: ContextPack):
    """可规划的对象: 命题。

    没有命题时返回 None —— 验证方案必须挂在一条具体命题上, 否则"要区分什么"无从定义。
    """
    from src.research.schemas import Claim

    for row in _rows(context, "claim"):
        claim = _parse_row(Claim, row)
        if claim is not None and claim.statement:
            return claim
    return None


def _distinguishing_from(context: ContextPack):
    """上下文里已登记的"可区分检验"候选 (来自模型比较)。"""
    for row in _rows(context, "model"):
        for test in row.get("distinguishing") or []:
            if isinstance(test, dict) and test.get("statement"):
                return test
    return None


def _first(context: ContextPack, kind: str, key: str) -> Any:
    rows = context.objects.get(kind) or []
    for row in rows:
        if row.get(key):
            return row[key]
    return []
