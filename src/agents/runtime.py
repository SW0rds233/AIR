from __future__ import annotations

"""统一智能体运行时: 有界工具循环、权限校验、用量记账与结构化结果 (合并计划 §5, M1)。

这个模块替代的是原来的 `literature_reviewer._run_agent_loop()`: 那段代码本身是
**唯一现成的工具调用闭环**, 但带四个硬编码 —— `_TOOL_MAP` 字典、`all_papers`
隐式收集器、进程级 `tracker` 单例、以及"消息条数 > 8 就停"的循环终止条件。
它只能服务文献检索一个角色; 复制七份会让每个角色各有一套预算/取消/记账语义。

因此这里把机制抽出来, 保留可复用的部分 (调用→观察→再决策), 去掉硬编码:

- 工具由调用方**注入** (`ToolSpec`), 不再有模块级 `_TOOL_MAP`;
- 观察结果收集进 `ToolObservation` 列表 (而不是往一个共享 list 里灌);
- 用量记在**本次任务**的 `UsageRecord` 上, 不再写进程级单例 (合并计划 §7.4 点名
  `cost_tracker.tracker` 是全局资源, 团队并发前必须处理其归属);
- 停止条件由 `task.budget` 与取消信号决定, 不再用消息条数。

工具循环只在**有 LLM 且工具可用**时启用; 离线或预算为零时由角色的确定性实现
兜底 —— 判定层 (`src/verification/**`、`acceptance.py`) 保持零 LLM。
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.agents.protocol import (
    CAPABILITY_TOOLS,
    AgentResult,
    AgentTask,
    CapabilityGrant,
    ChangeProposal,
    ContextPack,
    GrantError,
    TaskBudget,
    TaskOutcome,
    UsageRecord,
    grant_for,
    role_of,
    utcnow,
    writable_kinds,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Agent",
    "AgentRuntime",
    "BudgetedLLM",
    "CancelToken",
    "InProcessExecutor",
    "ObservationSink",
    "TaskCancelled",
    "ToolLoop",
    "ToolObservation",
    "ToolSpec",
    "budget_from_env",
    "downgrade_only_gate",
    "observation_text",
    "tool_spec",
]


class TaskCancelled(RuntimeError):
    """任务被取消 (停止 run / 超时)。

    用异常而不是返回值传播, 是因为取消可能发生在任意一次 LLM 或工具调用之间;
    返回值只覆盖"我检查到取消"这一种情况, 会让深层调用继续执行。
    """


# ----------------------------------------------------------------------
# 取消
# ----------------------------------------------------------------------
@dataclass
class CancelToken:
    """协作式取消信号 (合并计划 §5.3)。

    停止 run 时: 未派发的任务直接取消, 运行中的任务通过这个令牌收到通知。
    不把线程强杀 —— 否则已发生的费用与半成品产物就无法如实记账。
    """

    cancelled: bool = False
    reason: str = ""
    requested_at: str = ""
    #: 被通知过的 task_id (供"取消后必须封存已有成果"的检查)。
    notified: list[str] = field(default_factory=list)

    def cancel(self, reason: str = "") -> None:
        self.cancelled = True
        self.reason = reason or self.reason or "运行被停止"
        self.requested_at = self.requested_at or utcnow()

    def raise_if_cancelled(self, task_id: str = "") -> None:
        if not self.cancelled:
            return
        if task_id and task_id not in self.notified:
            self.notified.append(task_id)
        raise TaskCancelled(self.reason or "任务被取消")


# ----------------------------------------------------------------------
# 结构化观察
# ----------------------------------------------------------------------
class ObservationSink(Protocol):
    """工具调用与阶段事件的落盘入口 (由运行时/图提供)。

    单独定义协议是为了让测试可以注入记录器, 而不必启动真实存储。
    """

    def event(self, kind: str, payload: dict[str, Any]) -> None: ...


class _NullSink:
    def event(self, kind: str, payload: dict[str, Any]) -> None:  # pragma: no cover
        return None


@dataclass
class ToolObservation:
    """一次工具调用及其结果。

    `status` 区分**工具执行失败**、**解析失败**与**正常返回** (合并计划 §5:
    "工具执行失败、解析失败、任务完成和科学结论未决须可区分")。
    """

    name: str
    arguments: dict[str, Any]
    status: str = "ok"          # ok / tool_error / blocked / invalid_arguments
    result: Any = None
    error: str = ""
    elapsed_ms: int = 0
    #: 该观察是否可作为研究证据 (检索/阅读类为 True; 纯工具查询为 False)。
    evidential: bool = False
    #: 由工具声明的后续需求 (工具可以告诉角色"这里缺什么")。
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        preview = self.result
        if isinstance(preview, (list, tuple)):
            preview = f"[{len(preview)} 项]"
        elif isinstance(preview, dict):
            preview = {k: preview[k] for k in list(preview)[:12]}
        elif isinstance(preview, str):
            preview = preview[:400]
        return {
            "name": self.name,
            "arguments": dict(self.arguments),
            "status": self.status,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "evidential": self.evidential,
            "notes": list(self.notes),
            "result_preview": preview,
        }


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
@dataclass
class ToolSpec:
    """一个可注入的工具。

    - `fn`: 真实实现 (可以是普通函数或 `StructuredTool` 的 `.func`);
    - `capability`: 需要的**可写**能力名 (在 `CAPABILITY_TOOLS` 中); 只读工具留空;
    - `read_scope`: 需要的读范围 (例如 `tools:search`); 无读范围限制留空;
    - `llm_visible`: 是否作为 function schema 绑定给模型 (确定性工具不必暴露)。
    """

    name: str
    fn: Callable[..., Any]
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    capability: str = ""
    read_scope: str = ""
    llm_visible: bool = True
    evidential: bool = False
    #: 结果后处理: 把工具原始返回转成给模型看的紧凑文本 (None 用默认 str)。
    formatter: Callable[[Any], str] | None = None

    def __post_init__(self) -> None:
        if self.capability and self.capability not in CAPABILITY_TOOLS:
            raise ValueError(f"未登记的可写能力: {self.capability!r}")
        # StructuredTool 通过 .func 访问原始函数 (直接调用不可用 —— 已修过的真实缺陷)
        if hasattr(self.fn, "func") and callable(self.fn.func):
            self.fn = self.fn.func

    def as_langchain_tool(self):
        """包装为 LangChain StructuredTool (供 bind_tools 使用)。"""
        from langchain_core.tools import StructuredTool

        return StructuredTool.from_function(
            func=self.fn, name=self.name,
            description=self.description or self.name,
        )

    def render(self, result: Any) -> str:
        if self.formatter is not None:
            try:
                return str(self.formatter(result))
            except Exception as e:  # noqa: BLE001 - 格式化失败不丢结果
                return f"{result!r} (格式化失败: {e})"
        if isinstance(result, str):
            return result
        return str(result)


def tool_spec(name: str, fn: Callable[..., Any], **kwargs: Any) -> ToolSpec:
    return ToolSpec(name=name, fn=fn, **kwargs)


# ----------------------------------------------------------------------
# 记账 LLM
# ----------------------------------------------------------------------
class BudgetedLLM:
    """把 LLM 调用计入**本次任务**的用量, 并在超预算/取消时拒绝继续调用。

    与 `cost_tracker.MeteredLLM` 的区别: 后者写进程级单例 (`tracker`), 团队并发时
    分不清归属; 这里把用量写进任务的 `UsageRecord`, 由运行时分账。
    """

    def __init__(self, inner, usage: UsageRecord, budget: TaskBudget,
                 cancel: CancelToken | None = None, task_id: str = "",
                 stage: str = "team", *, on_usage: Callable[[dict], None] | None = None):
        self._inner = inner
        self.usage = usage
        self.budget = budget
        self._cancel = cancel
        self._task_id = task_id
        self._stage = stage
        self._on_usage = on_usage

    def invoke(self, *args, **kwargs):
        if self._cancel is not None:
            self._cancel.raise_if_cancelled(self._task_id)
        over = self.budget.exceeded_by(self.usage)
        if over:
            raise TaskCancelled(f"预算已用尽 ({'; '.join(over)})")
        result = self._inner.invoke(*args, **kwargs)
        self._account(result)
        return result

    def _account(self, result) -> None:
        from src.utils.cost_tracker import estimate_cost, price_source, usage_metadata_of

        self.usage.llm_calls += 1
        model = (getattr(self._inner, "model_name", "")
                 or getattr(self._inner, "model", "") or "")
        usage = usage_metadata_of(result)
        if not usage:
            if "llm_usage" not in self.usage.unknown_parts:
                self.usage.unknown_parts.append("llm_usage")
            self.usage.models = list(dict.fromkeys([*self.usage.models, str(model)]))
            if self._on_usage is not None:
                self._on_usage({"model": str(model), "stage": self._stage,
                                "tokens": None})
            return
        self.usage.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.usage.output_tokens += int(usage.get("output_tokens", 0) or 0)
        cost = estimate_cost(model, usage.get("input_tokens", 0),
                             usage.get("output_tokens", 0))
        self.usage.cost_usd = round((self.usage.cost_usd or 0.0) + cost, 6)
        self.usage.models = list(dict.fromkeys([*self.usage.models, str(model)]))
        note = price_source(model)
        if note not in self.usage.price_notes:
            self.usage.price_notes.append(note)
        if self._on_usage is not None:
            self._on_usage({"model": str(model), "stage": self._stage,
                            "tokens": usage, "cost_usd": cost})

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ----------------------------------------------------------------------
# 工具循环
# ----------------------------------------------------------------------
class ToolLoop:
    """有界工具调用闭环 (调用 → 执行工具 → 观察回喂 → 再决策)。

    终止条件有三个, 缺一不可:
    1. 模型不再请求工具 (它自己认为够了);
    2. `max_rounds` 用尽;
    3. 预算/取消信号触发。

    与旧实现的关键差别: 停止不再依赖"消息条数 > 8"; 每次工具调用都落进
    `ToolObservation`, 由调用方决定哪些算证据。
    """

    def __init__(self, tools: list[ToolSpec], *, max_rounds: int = 3,
                 result_budget: int = 6000, sink: ObservationSink | None = None):
        self.tools = {t.name: t for t in tools}
        self.max_rounds = max(1, max_rounds)
        self.result_budget = result_budget
        self.sink = sink or _NullSink()
        self.observations: list[ToolObservation] = []

    def run(self, llm, messages: list, *, grant: CapabilityGrant | None = None,
            cancel: CancelToken | None = None, task_id: str = "",
            usage: UsageRecord | None = None,
            budget: TaskBudget | None = None) -> tuple[str, list[ToolObservation]]:
        """执行循环; 返回 (模型最后的文本, 工具观察列表)。"""
        from langchain_core.messages import HumanMessage, ToolMessage

        visible = [t for t in self.tools.values() if t.llm_visible]
        bound = llm
        if visible:
            try:
                bound = llm.bind_tools([t.as_langchain_tool() for t in visible])
            except Exception as e:  # noqa: BLE001 - 绑定失败则退化为单趟调用
                self.sink.event("tool_loop_degraded",
                                {"reason": f"无法绑定工具: {e}", "task_id": task_id})
        final_text = ""
        needs_final_answer = False
        for round_index in range(self.max_rounds):
            if cancel is not None:
                cancel.raise_if_cancelled(task_id)
            if budget is not None and usage is not None:
                over = budget.exceeded_by(usage)
                if over:
                    self.sink.event("budget_exhausted",
                                    {"task_id": task_id, "over": over})
                    break
            result = bound.invoke(messages)
            messages.append(result)
            final_text = _content_of(result)
            calls = getattr(result, "tool_calls", None) or []
            needs_final_answer = bool(calls)
            if not calls:
                break
            for call in calls:
                observation = self._execute(call, grant=grant)
                self.observations.append(observation)
                self.sink.event("tool_run", {
                    "task_id": task_id, "tool": observation.name,
                    "status": observation.status, "elapsed_ms": observation.elapsed_ms,
                })
                messages.append(ToolMessage(
                    content=observation_text(observation, self.result_budget),
                    tool_call_id=str(call.get("id", "")),
                    name=observation.name,
                ))
        if needs_final_answer and not (budget is not None and usage is not None
                                       and budget.exceeded_by(usage)):
            if cancel is not None:
                cancel.raise_if_cancelled(task_id)
            messages.append(HumanMessage(content="工具执行轮次已结束。请根据已有观察返回最终结构化结果，不再请求工具。"))
            result = llm.invoke(messages)
            messages.append(result)
            final_text = _content_of(result)
            # 个别后端即使未绑定工具仍返回调用；也必须配齐协议消息。
            for call in getattr(result, "tool_calls", None) or []:
                messages.append(ToolMessage(content="工具轮次已结束，此调用未执行。",
                                            tool_call_id=str(call.get("id", "")),
                                            name=str(call.get("name", ""))))
        return final_text, list(self.observations)

    def _execute(self, call: dict, *, grant: CapabilityGrant | None) -> ToolObservation:
        name = str(call.get("name", "") or "")
        args = call.get("args", {}) or {}
        if not isinstance(args, dict):
            return ToolObservation(name=name, arguments={}, status="invalid_arguments",
                                   error="工具参数不是对象")
        spec = self.tools.get(name)
        if spec is None:
            return ToolObservation(name=name, arguments=args, status="tool_error",
                                   error=f"工具 {name!r} 不存在于本任务的工具集")
        # 权限检查: 可写能力必须由运行时签发的令牌授权
        if spec.capability and (grant is None or not grant.allows_tool(spec.capability)):
            return ToolObservation(
                name=name, arguments=args, status="blocked",
                error=f"令牌未授权能力 {spec.capability!r}")
        if spec.read_scope and (grant is None or not grant.allows_read(spec.read_scope)):
            return ToolObservation(
                name=name, arguments=args, status="blocked",
                error=f"令牌未授权读范围 {spec.read_scope!r}")
        started = time.monotonic()
        try:
            result = spec.fn(**args)
        except TaskCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - 工具失败回灌给模型自我修正
            return ToolObservation(name=name, arguments=args, status="tool_error",
                                   error=str(e),
                                   elapsed_ms=int((time.monotonic() - started) * 1000))
        return ToolObservation(
            name=name, arguments=args, status="ok", result=result,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            evidential=spec.evidential)


def _content_of(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return "".join(str(part.get("text", part) if isinstance(part, dict) else part)
                       for part in content)
    return str(content or "")


def observation_text(observation: ToolObservation, budget: int = 6000) -> str:
    """把观察转成回喂给模型的文本 (失败也回灌, 让模型能自我修正)。"""
    if not observation.ok:
        return f"[Tool Call Error] 工具 {observation.name} {observation.status}: {observation.error}"
    text = observation.result if isinstance(observation.result, str) else str(
        observation.result)
    return text[:budget]


# ----------------------------------------------------------------------
# 角色基类
# ----------------------------------------------------------------------
class Agent(Protocol):
    """一个子智能体的实现契约。

    `run` 必须返回结构化 `AgentResult`; 抛出的异常由执行器转成 `failed` 结果,
    而不是让整个 run 崩掉 (合并计划 §4.2: 工具故障可有界重试)。
    """

    role: str

    def run(self, task: AgentTask, context: ContextPack,
            runtime: AgentRuntime) -> AgentResult: ...


# ----------------------------------------------------------------------
# 运行时
# ----------------------------------------------------------------------
class AgentRuntime:
    """任务执行的唯一入口: 签发令牌、校验成果、记账、落事件。

    权限在这里**实际生效**: 智能体只能通过 `runtime` 拿到令牌与工具, 而令牌由
    `grant_for()` 按角色能力表签发。模型即使在自己的输出里写一个
    `may_change_conclusion=True` 的令牌对象, `is_valid()` 也是 False (签发者不匹配)。
    """

    def __init__(self, *, sink: ObservationSink | None = None,
                 cancel: CancelToken | None = None,
                 llm_factory: Callable[[str], Any] | None = None,
                 max_tool_rounds: int = 3,
                 run_budget: Any | None = None) -> None:
        self.sink = sink or _NullSink()
        self.cancel = cancel or CancelToken()
        self._llm_factory = llm_factory
        self.max_tool_rounds = max_tool_rounds
        self.run_budget = run_budget
        #: 各角色已解析出的模型 (`stage -> 模型或 None`)。
        #:
        #: 为什么需要它: `llm_available()` 原先只判断"工厂是否存在", 而工厂**可以返回
        #: None** (显式离线)。两者混在一起, 离线模式下角色会以为模型可用、调用时抛
        #: `RuntimeError`, 再退回确定性实现 —— 症状被掩盖成"跑通了", 实际每次都在
        #: 走异常路径。这里按需解析一次并缓存, 让"有没有模型"成为**实测事实**。
        self._resolved_llm: dict[str, Any] = {}
        # Only executed, authorized tool observations populate this run-local cache.
        self.search_cache: dict[str, list[dict]] = {}
        #: 事件计数 (供测试与运行指标断言)。
        self.events: list[dict[str, Any]] = []
        #: 本运行的研究存储 (由装配层注入; 团队里就是 `TeamRun.task_store.store`)。
        #: 角色**不直接**读写它 —— 核验服务用它记账与算输入闭包, 对象写入只走
        #: 唯一提交口 (合并计划 §6.2)。
        self.research_store: Any = None

    # ---- 核验服务 (合并计划 §4: "VerificationService 类型化请求与结果提交") ----
    def verification_backends(self) -> dict[str, bool]:
        """当前可用的核验后端 (缺失的后端必须影响可用动作集合)。"""
        from src.verification.runner import available_tools

        return available_tools()

    def run_verification(self, task: AgentTask, claim: Any, obligation: Any, *,
                         evidence: list[Any] | None = None, run_id: str = ""):
        """对一个义务执行核验, 返回结构化结果 (`CheckOutcome`)。

        为什么核验**不**由角色自己建服务: 核验要读研究库 (工具账本、输入版本闭包),
        而角色的契约是"只提交候选、不直接读写研究存储"。因此核验由运行时按本运行的
        存储执行, 角色只负责**选核查目标**并把结果作为候选提交。
        """
        outcome = self.verification_service().check(claim, obligation, evidence=evidence)
        self.emit("verification_executed", {
            "task_id": task.task_id, "claim_id": getattr(claim, "id", ""),
            "obligation_id": getattr(obligation, "id", ""),
            "tool": outcome.tool, "evaluated": outcome.evaluated,
            "closed": outcome.closed,
            "status": (outcome.record.status if outcome.record is not None else "")})
        return outcome

    def verification_service(self, *, problem_id: str = "", run_id: str = ""):
        """按本运行的研究存储建一个核验服务 (角色用它挑选并执行核验目标)。"""
        from src.research.verification_service import VerificationService

        return VerificationService(store=self.research_store,
                                   problem_id=problem_id or "",
                                   run_id=run_id)

    def _resolve_llm(self, stage: str) -> Any:
        """解析某个角色的模型 (缓存); 工厂返回 None 或抛错都如实记为该角色无模型。"""
        key = str(stage or "")
        if key in self._resolved_llm:
            return self._resolved_llm[key]
        if self._llm_factory is None:
            self._resolved_llm[key] = None
            return None
        try:
            model = self._llm_factory(key)
        except Exception as e:  # noqa: BLE001 - 取模型失败要可见, 但不能拖垮研究
            self.emit("llm_unavailable", {"stage": key,
                                          "reason": f"{type(e).__name__}: {e}"})
            model = None
        self._resolved_llm[key] = model
        return model

    # ---- 事件 ----
    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append({"kind": kind, "payload": dict(payload)})
        self.sink.event(kind, payload)

    # ---- 权限 ----
    def issue_grant(self, task: AgentTask, *,
                    extra_reads: tuple[str, ...] = (),
                    extra_tools: tuple[str, ...] = ()) -> CapabilityGrant:
        """为任务签发令牌。

        显式加授权只走这里 (由运行时按任务契约决定); 模型没有调用入口。
        """
        grant = grant_for(task, extra_reads=extra_reads, extra_tools=extra_tools)
        task.capability_grant_id = grant.grant_id
        self.emit("capability_granted", {
            "task_id": task.task_id, "agent": task.agent,
            "grant_id": grant.grant_id, "tools": list(grant.tools),
            "read_scopes": list(grant.read_scopes),
        })
        return grant

    # ---- 预算 ----
    def reserve_budget(self, task: AgentTask) -> str:
        """预留额度; 被全局余额裁剪时返回**说明** (调用方必须记录)。"""
        if self.run_budget is None:
            task.budget = task.budget.reserve()
            return ""
        grant, note = self.run_budget.allocate(task.budget, task.task_id)
        task.budget = grant
        if note:
            self.emit("budget_trimmed", {"task_id": task.task_id, "note": note})
        return note

    # ---- LLM ----
    def llm(self, task: AgentTask, usage: UsageRecord, stage: str = "") -> Any:
        """取一个**计入本任务用量**的 LLM。

        未配置模型时抛 `RuntimeError` —— 明确失败胜过让角色偷偷改成单趟调用。
        角色应先用 `llm_available()` 判断, 不可用时走确定性实现。
        """
        inner = self._resolve_llm(stage or task.agent)
        if inner is None:
            raise RuntimeError("运行时未注入可用的 LLM (离线模式请用确定性实现)")
        return BudgetedLLM(inner, usage, task.budget, self.cancel, task.task_id,
                           stage=stage or task.agent,
                           on_usage=lambda rec: self.emit("llm_call", {
                               "task_id": task.task_id, "agent": task.agent, **rec}))

    def llm_available(self, stage: str = "") -> bool:
        """是否真的有可用模型。

        `stage` 留空时按**主控**解析 —— 它是"这次运行有没有模型"的代表, 与
        `TeamRun.runtime.llm_available()` 的既有语义一致 (审计就是用它判断 G02)。
        """
        return self._resolve_llm(stage or "supervisor") is not None

    # ---- 成果校验 ----
    def validate_result(self, task: AgentTask, result: AgentResult,
                        grant: CapabilityGrant | None = None) -> list[str]:
        """校验成果是否符合契约; 返回违规说明 (不修改成果)。"""
        problems: list[str] = []
        if result.task_id != task.task_id:
            problems.append(f"结果的 task_id ({result.task_id}) 与任务 ({task.task_id}) 不一致")
        if result.agent and result.agent != task.agent:
            problems.append(f"结果的 agent ({result.agent}) 与任务 ({task.agent}) 不一致")
        for proposal, _reason in self._proposal_violations(task, result, grant):
            problems.append(_reason)
        if result.outcome == TaskOutcome.completed and not (
                result.summary or result.artifact_refs or result.proposed_changes
                or result.verification_refs or result.payload):
            problems.append("完成但没有任何成果 (summary/产物/候选/核验/载荷全为空)")
        return problems

    def _proposal_violations(self, task: AgentTask, result: AgentResult,
                             grant: CapabilityGrant | None) -> list[tuple[ChangeProposal, str]]:
        """逐条候选的违规说明 —— 违规是**候选级**的, 不是整条结果级的。

        这一点很关键: 若按"整条结果违规"处理, 一个越权候选会把同一任务里合法的候选
        一起拖下水; 若只降级整条结果 (修复前的行为), 越权候选本身仍会被登记。
        因此这里给出**每条候选自己的**理由, 由 `finalize()` 精准隔离 (§3.2 G07)。
        """
        allowed = writable_kinds(task.agent)
        violations: list[tuple[ChangeProposal, str]] = []
        for proposal in result.proposed_changes:
            reasons: list[str] = []
            if proposal.kind not in allowed:
                reasons.append(
                    f"角色 {task.agent} 不得提交 {proposal.kind} 类变更 "
                    f"(允许: {', '.join(sorted(allowed)) or '无'})")
            if proposal.may_change_conclusion and task.agent == "review":
                reasons.append("审阅只能提出降级建议, 不得提交改变结论真值的候选")
            if proposal.may_change_conclusion and grant is not None \
                    and not grant.may_change_conclusion:
                reasons.append(
                    f"令牌不允许提交改变结论的分支 (proposal {proposal.proposal_id})")
            if reasons:
                violations.append((proposal, "; ".join(reasons)))
        return violations

    def finalize(self, task: AgentTask, result: AgentResult,
                 grant: CapabilityGrant | None = None) -> AgentResult:
        """校验、**隔离**越权候选并落事件; 违规时把结果降级为 partial 并写明原因。

        两条并行的处置 (§3.2 G07):
        1. **候选级隔离**: 违规候选从 `proposed_changes` 移入 `rejected_changes`
           (带理由)。只有留在 `proposed_changes` 里的候选才可能被登记为权威对象 ——
           "拒绝候选不得进权威库"因此是**结构性**保证, 而不是靠调用方自觉。
        2. **结果级降级**: 仍把 `completed` 降为 `partial` 并写明原因, 让调用方看到
           "这次成果不符合契约"。降级而不是整体丢弃: 合规的那部分成果仍然有用。
        """
        violations = self._proposal_violations(task, result, grant)
        quarantined: list[ChangeProposal] = []
        if violations:
            rejected_ids = {proposal.proposal_id for proposal, _r in violations}
            for proposal, reason in violations:
                result.rejected_changes.append({
                    "kind": proposal.kind,
                    "object_id": proposal.object_id,
                    "proposal_id": proposal.proposal_id,
                    "may_change_conclusion": proposal.may_change_conclusion,
                    "reason": reason,
                    # 只留结构摘要: 被拒的载荷不再往下游传播, 但审计时能看出提了什么
                    "payload_keys": sorted((proposal.payload or {}).keys()),
                    "agent": task.agent,
                    "task_id": task.task_id,
                })
            quarantined = [p for p in result.proposed_changes
                           if p.proposal_id in rejected_ids]
            result.proposed_changes = [p for p in result.proposed_changes
                                       if p.proposal_id not in rejected_ids]
        problems = self.validate_result(task, result, grant)
        if violations:
            # 越权候选同样把整条结果降级: 让下游一眼看出"这次成果里有不合契约的东西",
            # 而不是因为违规部分已被隔离就显示成一次干净完成。
            problems = problems + [
                f"候选 {proposal.object_id or proposal.proposal_id} 被拒: {reason}"
                for proposal, reason in violations]
        if problems:
            self.emit("result_rejected", {"task_id": task.task_id,
                                          "problems": problems})
            if result.outcome == TaskOutcome.completed:
                result.outcome = TaskOutcome.partial
                result.summary = (result.summary + " | " if result.summary else "") + \
                    "成果不符合契约: " + "; ".join(problems)
        if quarantined:
            self.emit("candidates_quarantined", {
                "task_id": task.task_id, "agent": task.agent,
                "rejected": [p.proposal_id for p in quarantined],
                "kept": [p.proposal_id for p in result.proposed_changes],
            })
        result.usage = result.usage
        self.emit("task_result", {"task_id": task.task_id, "agent": task.agent,
                                  "outcome": result.outcome.value,
                                  "problems": problems,
                                  "usage": result.usage.to_dict()})
        return result


#: 每个角色可以提交的对象种类见 `protocol.WRITABLE_KINDS` (与角色能力表同一份
#: 真相源, 避免运行时与角色实现各写一份而漂移)。


def downgrade_only_gate(agent: str, proposal: ChangeProposal) -> tuple[bool, str]:
    """审阅的"只可降级、不可升级"闸门 (合并计划 §14.3 不得让渡的不变量)。

    返回 `(是否允许, 原因)`。判定依据是候选载荷里的**显式等级变化**, 不是提示词:
    审阅提出 `target_level` 高于 `current_level` 时拒绝。
    """
    if role_of(agent) != "review":
        return True, ""
    payload = proposal.payload or {}
    current = str(payload.get("current_level", "") or "")
    target = str(payload.get("target_level", "") or "")
    if not current or not target or current == target:
        return True, ""
    order = {"unsupported": 0, "anecdotal": 1, "single_source": 2, "converging": 3,
             "identification_based": 4, "expert_reviewed": 5,
             "unverified": 0, "informal_reviewed": 1, "empirical_estimated": 2,
             "symbolic_checked": 3, "solver_checked": 4, "formally_checked": 5}
    low, high = order.get(current), order.get(target)
    if low is None or high is None:
        return True, ""      # 无法比较的等级名不改判, 交由判定层
    if high > low:
        return False, f"审阅不得把等级从 {current} 提升到 {target}"
    return True, ""


# ----------------------------------------------------------------------
# 执行器
# ----------------------------------------------------------------------
@dataclass
class _Backend:
    agent: Agent
    prompt_version: str = ""


class InProcessExecutor:
    """同进程任务执行器 (第一版形态; 后续可按需换成 worker)。

    职责: 依角色找实现 → 派发前预留预算 → 签发令牌 → 运行 → 记账 → 收敛为
    `AgentResult`。任何异常都收敛成 `failed` 结果并带上原因, 不让 run 崩掉。
    """

    def __init__(self, runtime: AgentRuntime, *,
                 backends: dict[str, _Backend] | None = None) -> None:
        self.runtime = runtime
        self._backends: dict[str, _Backend] = dict(backends or {})

    def register(self, agent: Agent, *, prompt_version: str = "") -> None:
        role = role_of(getattr(agent, "role", "")) or getattr(agent, "role", "")
        if not role:
            raise ValueError(f"未登记的角色: {getattr(agent, 'role', '')!r}")
        self._backends[role] = _Backend(agent=agent, prompt_version=prompt_version)

    def registered_roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def backend_for(self, agent: str) -> Agent | None:
        backend = self._backends.get(role_of(agent) or agent)
        return backend.agent if backend else None

    def execute(self, task: AgentTask, context: ContextPack | None = None) -> AgentResult:
        """同步执行一次任务 (幂等语义由调用方按 idempotency_key 保证)。"""
        started = utcnow()
        role = task.agent
        backend = self._backends.get(role)
        if backend is None:
            return AgentResult(
                task_id=task.task_id, agent=role, outcome=TaskOutcome.failed,
                failure_reason=f"没有注册 {role} 角色的实现")
        if self.runtime.cancel.cancelled:
            return AgentResult(task_id=task.task_id, agent=role,
                               outcome=TaskOutcome.cancelled,
                               failure_reason=self.runtime.cancel.reason or "运行已停止")
        note = self.runtime.reserve_budget(task)
        grant = self.runtime.issue_grant(task)
        pack = context or ContextPack(task_id=task.task_id, agent=role, grant=grant)
        if pack.grant is None:
            pack.grant = grant
        self.runtime.emit("task_started", {
            "task_id": task.task_id, "agent": role, "objective": task.objective,
            "started_at": started, "budget": task.budget.to_dict(),
            "attempt": task.attempt, "trimmed": note,
        })
        try:
            result = backend.agent.run(task, pack, self.runtime)
        except TaskCancelled as e:
            result = AgentResult(task_id=task.task_id, agent=role,
                                 outcome=TaskOutcome.cancelled,
                                 failure_reason=str(e))
        except GrantError as e:
            result = AgentResult(task_id=task.task_id, agent=role,
                                 outcome=TaskOutcome.failed,
                                 failure_reason=f"权限不足: {e}")
        except Exception as e:
            logger.debug("任务执行失败", exc_info=True)
            result = AgentResult(task_id=task.task_id, agent=role,
                                 outcome=TaskOutcome.failed,
                                 failure_reason=f"{type(e).__name__}: {e}")
        if not result.agent:
            result.agent = role
        result = self.runtime.finalize(task, result, grant)
        return result


def budget_from_env(*, actions: int = 0, tool_calls: int = 0) -> TaskBudget:
    """由图的预算参数构造任务额度 (兼容旧图传进来的 action/tool 上限)。"""
    return TaskBudget(max_llm_calls=actions, max_tool_calls=tool_calls)
