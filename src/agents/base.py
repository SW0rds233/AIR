from __future__ import annotations

"""子智能体公共设施 (合并计划 §3.1 / §8)。

七个功能角色共享三件事, 因此放在一处而不是各写一遍:

1. **素材投影**: 从 `ContextPack` 里取"本角色该看的对象", 不把整份研究状态塞进提示词;
2. **结构化输出解析**: 模型返回 JSON 时容错解析, 解析失败**如实报告**(不猜内容);
3. **确定性兜底**: 没有 LLM / 预算为零时, 角色仍要产出**真实**成果 —— 要么用规则
   与已有存储算出结果, 要么明确 `blocked`。绝不允许"没有模型就假装完成"。

角色实现只依赖 `AgentRuntime` 暴露的接口 (授权令牌、记账 LLM、事件), 不直接读写
研究存储: 状态写入是判定层的职责 (§5.2)。
"""

import json
import re
from typing import Any

from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ArtifactRef,
    ChangeProposal,
    ContextPack,
    ResearchNeed,
    TaskOutcome,
    UsageRecord,
    new_id,
    role_of,
)
from src.agents.runtime import AgentRuntime, TaskCancelled, ToolLoop, ToolSpec

__all__ = [
    "AgentBase",
    "as_bool",
    "as_list_of_str",
    "build_proposal",
    "clip",
    "context_summary",
    "extract_json",
    "packet_lines",
    "record_artifact",
    "task_intent",
    "visible_objects",
]


# ----------------------------------------------------------------------
# 上下文投影
# ----------------------------------------------------------------------
def visible_objects(context: ContextPack, kind: str,
                    limit: int = 20) -> list[dict[str, Any]]:
    """取本角色可见的某一类对象 (按任务范围裁剪后的只读快照)。"""
    rows = context.objects.get(kind) or []
    return list(rows[:limit])


def context_summary(context: ContextPack, *, budget_chars: int = 4000) -> str:
    """把上下文渲染为带对象引用的紧凑摘要 (导航用, 不含全文)。"""
    lines: list[str] = []
    if context.request:
        lines.append(f"# 原始请求\n{clip(context.request, 800)}")
    for name, rows in context.objects.items():
        if not rows:
            continue
        lines.append(f"\n# {name}")
        for row in rows[:12]:
            ref = f"{row.get('id', '?')}@v{row.get('version', '?')}"
            desc = (row.get("statement") or row.get("title") or row.get("summary")
                    or row.get("name") or "")
            status = row.get("status") or row.get("support") or ""
            lines.append(f"- {ref} [{status}] {clip(str(desc), 160)}")
    if context.sources:
        lines.append("\n# 资料源")
        for source in context.sources[:8]:
            lines.append(f"- {source.get('source_set_id', '')}: "
                         f"{source.get('documents', 0)} 篇, "
                         f"{source.get('cards', 0)} 卡, "
                         f"{'已建索引' if source.get('indexed') else '未建索引'}")
    if context.upstream:
        lines.append("\n# 上游成果")
        for item in context.upstream[:8]:
            lines.append(f"- [{item.get('agent', '')}] {clip(str(item.get('summary', '')), 200)}")
    if context.gaps:
        lines.append("\n# 已知缺口")
        for gap in context.gaps[:10]:
            lines.append(f"- [{gap.get('type', '')}] {clip(str(gap.get('statement', '')), 160)}")
    if context.notes:
        lines.append("\n# 说明")
        lines.extend(f"- {clip(note, 200)}" for note in context.notes[:8])
    text = "\n".join(lines)
    if len(text) > budget_chars:
        text = text[:budget_chars] + "\n...(上下文已截断, 需要细节时请用工具取原文)"
    return text


def packet_lines(items: list[dict[str, Any]], *, fields: tuple[str, ...],
                 limit: int = 40) -> str:
    """把素材渲染成逐行文本 (供提示词或工具输入)。"""
    out: list[str] = []
    for index, item in enumerate(items[:limit], 1):
        parts = [f"{index}."]
        for field in fields:
            value = item.get(field)
            if value in (None, "", [], {}):
                continue
            parts.append(f"{field}={clip(str(value), 240)}")
        out.append(" ".join(parts))
    return "\n".join(out)


# ----------------------------------------------------------------------
# 结构化输出解析
# ----------------------------------------------------------------------
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def extract_json(text: str) -> tuple[Any, str]:
    """从模型输出里取 JSON; 返回 `(对象, 失败原因)`。

    解析失败时**返回原因而不是空字典** —— "模型没给出合规输出"与"模型给出了空结果"
    必须能区分 (合并计划 §5: 工具执行失败、解析失败、任务完成须可区分)。
    """
    body = str(text or "").strip()
    if not body:
        return None, "输出为空"
    candidates: list[str] = []
    for match in _JSON_BLOCK.finditer(body):
        candidates.append(match.group(1))
    candidates.append(body)
    brace = _outermost_object(body)
    if brace:
        candidates.append(brace)
    parsed = _first_parsable(candidates)
    if parsed is not None:
        return parsed, ""
    return None, "输出中没有可解析的 JSON 对象"


def _first_parsable(candidates: list[str]) -> Any:
    """逐个候选尝试解析; 全失败返回 None (调用方据此如实报解析失败)。"""
    return next((value for value in (_try_load(c) for c in candidates)
                 if value is not None), None)


def _try_load(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - 解析失败交给下一个候选
        return None


def _outermost_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def as_list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "是", "真")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def clip(text: str, limit: int = 200) -> str:
    body = " ".join(str(text or "").split())
    return body[:limit] + ("…" if len(body) > limit else "")


def task_intent(task: AgentTask) -> str:
    """任务真正要解决的表述。

    补派任务的 `objective` 是**需求类型名** (用于去重, 见 `needs_to_tasks`), 人可读的
    需求措辞放在 `hints["need_statement"]`。角色提示词与判断都应使用后者, 否则模型
    看到的"目标"只有 "derivation" 这样的词。
    """
    hint = str((task.hints or {}).get("need_statement", "") or "").strip()
    return hint or task.objective


# ----------------------------------------------------------------------
# 成果构造
# ----------------------------------------------------------------------
def build_proposal(kind: str, *, payload: dict[str, Any], object_id: str = "",
                   expected_revision: int | None = None,
                   rationale: str = "",
                   input_versions: dict[str, int] | None = None,
                   evidence_refs: list[Any] | None = None,
                   verification_refs: list[Any] | None = None,
                   may_change_conclusion: bool = False) -> ChangeProposal:
    """构造候选变更 (统一入口, 便于审计与测试)。"""
    return ChangeProposal(
        kind=kind, object_id=object_id, expected_revision=expected_revision,
        payload=payload, rationale=rationale,
        input_versions=dict(input_versions or {}),
        evidence_refs=list(evidence_refs or []),
        verification_refs=list(verification_refs or []),
        may_change_conclusion=may_change_conclusion,
    )


def record_artifact(*, kind: str, uri: str, label: str = "", summary: str = "",
                    object_ref: Any = None, produced_by: str = "",
                    sha256: str = "", size: int = 0) -> ArtifactRef:
    return ArtifactRef(artifact_id=new_id("art"), kind=kind, uri=uri, label=label,
                       summary=summary, object_ref=object_ref,
                       produced_by=produced_by, sha256=sha256, bytes=size)


# ----------------------------------------------------------------------
# 角色基类
# ----------------------------------------------------------------------
class AgentBase:
    """七个功能角色的公共骨架。

    子类只需实现 `_run(task, context, runtime, usage, tools)` 并声明 `role`/`kinds`。
    基类负责: 预算与取消的统一检查、工具集构造、异常收敛为 `blocked`/`failed`,
    以及"LLM 不可用时走确定性实现"的分派。
    """

    role: str = ""
    prompt_version: str = "v1"
    #: 可提交的对象种类 (与 protocol.WRITABLE_KINDS 一致; 这里冗余声明只是为了让
    #: 每个角色文件自解释, 实际授权以能力表为准)。
    kinds: tuple[str, ...] = ()

    def run(self, task: AgentTask, context: ContextPack,
            runtime: AgentRuntime) -> AgentResult:
        role = role_of(self.role) or self.role
        usage = UsageRecord()
        tools = self.tools(task, context, runtime)
        try:
            runtime.cancel.raise_if_cancelled(task.task_id)
        except TaskCancelled as e:
            return AgentResult(task_id=task.task_id, agent=role,
                               outcome=TaskOutcome.cancelled,
                               failure_reason=str(e), usage=usage)
        try:
            result = self._run(task, context, runtime, usage, tools)
        except TaskCancelled as e:
            return AgentResult(task_id=task.task_id, agent=role,
                               outcome=TaskOutcome.cancelled,
                               failure_reason=str(e), usage=usage)
        if not isinstance(result, AgentResult):
            return AgentResult(task_id=task.task_id, agent=role,
                               outcome=TaskOutcome.failed, usage=usage,
                               failure_reason=f"{role} 返回了非 AgentResult 对象")
        if not result.agent:
            result.agent = role
        if not result.agent_run_id:
            result.agent_run_id = new_id("run")
        # 用量以基类统计为准 (角色实现不应各记一份)
        result.usage = usage.merge(result.usage) if usage.llm_calls or usage.tool_calls \
            else result.usage
        return result

    # ---- 供子类覆盖 ----
    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        return []

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        raise NotImplementedError

    # ---- 公共辅助 ----
    def tool_loop(self, task: AgentTask, runtime: AgentRuntime, tools: list[ToolSpec],
                  usage: UsageRecord, messages: list,
                  grant: Any = None, *, max_rounds: int | None = None,
                  ) -> tuple[str, list[Any]]:
        """跑一次有界工具循环 (仅在 LLM 可用时调用)。"""
        loop = ToolLoop(tools, max_rounds=max_rounds or runtime.max_tool_rounds,
                        sink=runtime.sink)
        llm = runtime.llm(task, usage, stage=self.role)
        return loop.run(llm, messages, grant=grant, cancel=runtime.cancel,
                        task_id=task.task_id, usage=usage, budget=task.budget)

    def blocked(self, task: AgentTask, reason: str, *,
                needs: list[ResearchNeed] | None = None,
                summary: str = "", usage: UsageRecord | None = None,
                payload: dict[str, Any] | None = None) -> AgentResult:
        """受阻结果: 必须说明缺什么, 否则主控无从补派工。"""
        return AgentResult(
            task_id=task.task_id, agent=role_of(self.role) or self.role,
            outcome=TaskOutcome.blocked, summary=summary or reason,
            failure_reason=reason, followup_needs=list(needs or []),
            usage=usage or UsageRecord(), payload=dict(payload or {}),
            replan=True,
        )

    def partial(self, task: AgentTask, summary: str, *,
                changes: list[ChangeProposal] | None = None,
                artifacts: list[ArtifactRef] | None = None,
                unresolved: list[str] | None = None,
                usage: UsageRecord | None = None,
                payload: dict[str, Any] | None = None) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, agent=role_of(self.role) or self.role,
            outcome=TaskOutcome.partial, summary=summary,
            proposed_changes=list(changes or []), artifact_refs=list(artifacts or []),
            unresolved=list(unresolved or []), usage=usage or UsageRecord(),
            payload=dict(payload or {}),
        )

    def completed(self, task: AgentTask, summary: str, *,
                  changes: list[ChangeProposal] | None = None,
                  artifacts: list[ArtifactRef] | None = None,
                  unresolved: list[str] | None = None,
                  needs: list[ResearchNeed] | None = None,
                  issues: list[Any] | None = None,
                  usage: UsageRecord | None = None,
                  payload: dict[str, Any] | None = None,
                  replan: bool = False) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, agent=role_of(self.role) or self.role,
            outcome=TaskOutcome.completed, summary=summary,
            proposed_changes=list(changes or []), artifact_refs=list(artifacts or []),
            unresolved=list(unresolved or []), followup_needs=list(needs or []),
            issues=list(issues or []), usage=usage or UsageRecord(),
            payload=dict(payload or {}), replan=replan,
        )
