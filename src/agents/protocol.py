from __future__ import annotations

"""统一智能体协议: 派工、成果、研究需求与权限 (合并计划 §5, M0)。

为什么需要这个模块
------------------
合并前系统里"让某个环节干活"的方式至少有四种: 综述图按 `stages` 顺序调用节点、
理论图由 `TheoryEngine._dispatch()` 按 `ActionType` 派发、`literature_reviewer`
自己跑一个工具循环、写作/审阅则是一次单趟 LLM 调用。四者之间没有共同契约, 于是:

- 无法回答"这条结论是哪次派工、依据哪些输入版本产生的";
- 子智能体受阻时只能把字符串塞回上游, 主控看不出"缺什么", 也就无法补派工;
- 权限约束散落在提示词与布尔标记里 (`may_change_conclusion=False`),
  模型自己写一个 `True` 就绕过了。

本模块把这几件事固定成**数据契约**:

1. `AgentTask` —— 主控派什么活, 依据哪些**版本化**对象, 什么算完成, 花多少预算;
2. `AgentResult` —— 子智能体交回什么: 成果、候选变更、验证引用、**后续需求**、未决项;
3. `ResearchNeed` —— 跨职责的需求 (缺来源 / 缺模型条件 / 缺验证), 由主控转成新任务,
   子智能体之间不互相派工 (第一版不开放任意再生成团队);
4. `CapabilityGrant` —— 由**运行时签发**的权限令牌。模型能拿到它, 但造不出它:
   构造入口带 `issued_by` 签名, `is_valid()` 校验签发者。写权限 (提交候选变更) 与
   读范围 (可读的资料源/工具) 分开, 缺权限时不静默放行, 直接判无效;
5. `TaskBudget` —— 派发前**预留**的额度。并发任务是各自预留, 因此
   `reserve()` 之后 `remaining` 就变小, 不会出现"每个并发任务都以为自己有全额余额"。

设计约束 (来自合并计划 §5.2 与 §14.3)
------------------------------------
- 结论等级只能由证书链、工具重算与中央状态规则产生。这里的 `ChangeProposal`
  只是**候选**: 它带 `may_change_conclusion`, 但该字段的用途是"运行时据此决定是否
  送入判定层", 不是"布尔为真就改写状态"。
- 智能体只提交候选与解释; 审阅只能降级, 不能升级 —— 这条由 `registry` 的
  能力表与运行时校验共同保证, 不靠提示词。
- 判定层 (`src/verification/**`、`acceptance.py`) 保持零 LLM; 本模块不引入 LLM 调用。
"""

import hashlib
import hmac
import json
from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, Field, field_validator, model_validator

from src.research.schemas import ObjectRef, SourceKind, new_id, stable_id, utcnow

#: 唯一合法签发者标识。放在模块级 (而不是类的私有属性) 是为了让"谁能签发令牌"
#: 一目了然, 且不受 pydantic 私有属性规则影响。
GRANT_ISSUER = "air.runtime"

__all__ = [
    "AGENT_CAPABILITIES",
    "AGENT_ROLES",
    "ARTIFACT_KINDS",
    "CAPABILITY_TOOLS",
    "GRANT_ISSUER",
    "NEED_OWNER",
    "OBJECT_KINDS",
    "OBJECT_KIND_ALIASES",
    "OUTCOME_TERMINAL",
    "EXTERNAL_SCOPES",
    "READ_SCOPES",
    "SOURCE_POLICY_SCOPES",
    "RETIRED_STATUSES",
    "ROLE_LABELS",
    "WORKER_ROLES",
    "WRITABLE_KINDS",
    "AgentContract",
    "AgentResult",
    "AgentRun",
    "AgentTask",
    "ArtifactKind",
    "ArtifactRef",
    "CapabilityGrant",
    "ChangeProposal",
    "ContextPack",
    "GrantError",
    "IssueSeverity",
    "NeedKind",
    "ResearchNeed",
    "ReviewIssueRef",
    "SourceKind",
    "TaskBudget",
    "TaskOutcome",
    "TaskStatus",
    "UsageRecord",
    "agent_capability",
    "canonical_object_kind",
    "grant_for",
    "idempotency_key_for",
    "is_agent_role",
    "role_of",
    "signature_of",
    "writable_kinds",
]

# ----------------------------------------------------------------------
# 角色
# ----------------------------------------------------------------------
#: 合并计划 §3 的团队: 1 个主控 + 7 类功能子智能体。
#: 角色数不等于进程数, 也不要求七个不同模型 —— 这里只是**契约身份**。
AGENT_ROLES: tuple[str, ...] = (
    "supervisor",
    "evidence",
    "modeling",
    "reasoning",
    "validation",
    "writing",
    "figures",
    "review",
)

#: 可以承接 `AgentTask` 的角色 (主控自己不承接派工, 只派发)。
WORKER_ROLES: tuple[str, ...] = tuple(r for r in AGENT_ROLES if r != "supervisor")

#: 角色的中文名 (日志与界面使用; 不参与判定)。
ROLE_LABELS: dict[str, str] = {
    "supervisor": "主控",
    "evidence": "检索与证据整理",
    "modeling": "问题建模",
    "reasoning": "推理与结论综合",
    "validation": "验证方案",
    "writing": "写作",
    "figures": "配图",
    "review": "独立审阅",
}


def is_agent_role(name: str) -> bool:
    return name in AGENT_ROLES


def role_of(name: str) -> str:
    """归一角色名: 支持 `EvidenceAgent` / `evidence_agent` / `evidence` 三种写法。

    兼容入口存在的原因: 旧的综述/理论图里角色是以类名或函数名出现的
    (`literature_reviewer`、`paper_writer`), 合并期需要把旧名字映射到新契约,
    但**不能**因此放宽校验 —— 认不出来就返回空串, 由调用方如实报告。
    """
    key = str(name or "").strip()
    if not key:
        return ""
    lowered = key.lower().replace("-", "_")
    lowered = lowered.removesuffix("_agent")
    if lowered in AGENT_ROLES:
        return lowered
    # `WritingAgent` / `EvidenceAgent` 这类 CamelCase 写法
    snake = _camel_to_snake(key)
    snake = snake.removesuffix("_agent")
    if snake in AGENT_ROLES:
        return snake
    return _LEGACY_ROLE_ALIASES.get(lowered, "") or _LEGACY_ROLE_ALIASES.get(snake, "")


def _camel_to_snake(name: str) -> str:
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index and (not name[index - 1].isupper()
                                         or (index + 1 < len(name) and name[index + 1].islower())):
            out.append("_")
        out.append(char.lower())
    return "".join(out)


#: 旧模块/函数名 -> 新契约角色。只收录**确实同职责**的映射, 不做宽泛猜测。
_LEGACY_ROLE_ALIASES: dict[str, str] = {
    "literature_reviewer": "evidence",
    "literature": "evidence",
    "pdf_ingestor": "evidence",
    "retrieval": "evidence",
    "kb": "evidence",
    "question_planner": "modeling",
    "problem_formulator": "modeling",
    "modeling": "modeling",
    "theorist": "reasoning",
    "derivation": "reasoning",
    "critic": "reasoning",
    "argument": "reasoning",
    "novelty": "reasoning",
    "novelty_compare": "reasoning",
    "experiments": "validation",
    "planner": "validation",
    "outline_generator": "writing",
    "theory_writer": "writing",
    "figure_generator": "figures",
    "figure_llm": "figures",
    "paper_reviewer": "review",
    "citation_checker": "review",
    "adversarial": "review",
}


# ----------------------------------------------------------------------
# 预算与用量
# ----------------------------------------------------------------------
class UsageRecord(BaseModel):
    """一次派工实际发生的用量。

    `calls`/`tokens`/`cost_usd` 缺失时用 `None` 表示**未知**, 不用 0 冒充 ——
    费用报告里"没记录"与"花了 0 元"必须能区分 (合并计划 §5.3)。
    """

    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    models: list[str] = Field(default_factory=list)
    price_notes: list[str] = Field(default_factory=list)
    #: 取不到用量的部分如实标注 (例如网关不返回 usage), 不填 0 假装完整。
    unknown_parts: list[str] = Field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def merge(self, other: UsageRecord) -> UsageRecord:
        """累计另一次 agent run 的用量 (重试/续跑时使用)。"""
        costs = [c for c in (self.cost_usd, other.cost_usd) if c is not None]
        return UsageRecord(
            llm_calls=self.llm_calls + other.llm_calls,
            tool_calls=self.tool_calls + other.tool_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=(round(sum(costs), 6) if costs else None),
            models=list(dict.fromkeys([*self.models, *other.models])),
            price_notes=list(dict.fromkeys([*self.price_notes, *other.price_notes])),
            unknown_parts=list(dict.fromkeys([*self.unknown_parts, *other.unknown_parts])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "models": list(self.models),
            "price_notes": list(self.price_notes),
            "unknown_parts": list(self.unknown_parts),
        }


class TaskBudget(BaseModel):
    """派发前预留的任务额度。

    `reserve()` 就地扣减可用余额并返回一个不可变的快照; 运行时在派发**之前**调用它,
    因此并发任务拿到的是各自预留后的额度, 不存在"都以为自己有全额"的情况。
    """

    max_llm_calls: int = 0            # 0 = 不限
    max_tool_calls: int = 0           # 0 = 不限
    max_tokens: int = 0               # 0 = 不限
    max_cost_usd: float | None = None  # None = 不限
    max_seconds: float = 0.0          # 0 = 不限
    reserved: bool = False
    reserved_at: str = ""

    def unbounded(self) -> bool:
        return (not self.max_llm_calls and not self.max_tool_calls
                and not self.max_tokens and self.max_cost_usd is None
                and not self.max_seconds)

    def reserve(self) -> TaskBudget:
        """预留本任务额度, 返回带 `reserved=True` 的副本。"""
        if self.reserved:
            return self.model_copy(deep=True)
        clone = self.model_copy(deep=True)
        clone.reserved = True
        clone.reserved_at = utcnow()
        return clone

    @staticmethod
    def _exceeded(limit: int, used: int) -> bool:
        return bool(limit) and used >= limit

    def exceeded_by(self, usage: UsageRecord) -> list[str]:
        """已超出的额度项 (空列表表示仍在预算内)。"""
        out: list[str] = []
        if self._exceeded(self.max_llm_calls, usage.llm_calls):
            out.append(f"llm_calls {usage.llm_calls}/{self.max_llm_calls}")
        if self._exceeded(self.max_tool_calls, usage.tool_calls):
            out.append(f"tool_calls {usage.tool_calls}/{self.max_tool_calls}")
        if self._exceeded(self.max_tokens, usage.total_tokens):
            out.append(f"tokens {usage.total_tokens}/{self.max_tokens}")
        if (self.max_cost_usd is not None and usage.cost_usd is not None
                and usage.cost_usd > self.max_cost_usd):
            out.append(f"cost_usd {usage.cost_usd}/{self.max_cost_usd}")
        return out

    def fraction_used(self, usage: UsageRecord) -> dict[str, float]:
        """各项用量占额度的比例 (未设额度的项不出现在结果里)。"""
        out: dict[str, float] = {}
        if self.max_llm_calls:
            out["llm_calls"] = usage.llm_calls / self.max_llm_calls
        if self.max_tool_calls:
            out["tool_calls"] = usage.tool_calls / self.max_tool_calls
        if self.max_tokens:
            out["tokens"] = usage.total_tokens / self.max_tokens
        if self.max_cost_usd:
            out["cost_usd"] = (usage.cost_usd or 0.0) / self.max_cost_usd
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_llm_calls": self.max_llm_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "max_seconds": self.max_seconds,
            "reserved": self.reserved,
            "reserved_at": self.reserved_at,
            "unbounded": self.unbounded(),
        }


class RunBudget(BaseModel):
    """一次 run 的全局额度; 向任务分配额度时**就地**扣减。"""

    total: TaskBudget = Field(default_factory=TaskBudget)
    allocated: TaskBudget = Field(default_factory=TaskBudget)
    allocated_cost_usd: float = 0.0
    allocations: list[str] = Field(default_factory=list)   # task_id 列表

    def allocate(self, request: TaskBudget, task_id: str = "") -> tuple[TaskBudget, str]:
        """按全局余额裁剪一次任务的申请额度。

        返回 `(实际额度, 说明)`。说明非空表示**被裁剪过** —— 调用方必须把它记进
        决策日志, 而不是让任务悄悄拿到更少的额度。
        """
        notes: list[str] = []
        grant = request.model_copy(deep=True)

        def _cap_int(limit: int, used: int, total: int) -> int:
            if not total:
                return limit
            room = max(0, total - used)
            if not limit or limit > room:
                notes.append(f"额度被全局余额裁剪到 {room}")
                return room
            return limit

        grant.max_llm_calls = _cap_int(grant.max_llm_calls,
                                       self.allocated.max_llm_calls,
                                       self.total.max_llm_calls)
        grant.max_tool_calls = _cap_int(grant.max_tool_calls,
                                        self.allocated.max_tool_calls,
                                        self.total.max_tool_calls)
        grant.max_tokens = _cap_int(grant.max_tokens,
                                    self.allocated.max_tokens,
                                    self.total.max_tokens)
        if self.total.max_cost_usd is not None:
            room = max(0.0, self.total.max_cost_usd - self.allocated_cost_usd)
            if grant.max_cost_usd is None or grant.max_cost_usd > room:
                grant.max_cost_usd = round(room, 6)
                notes.append(f"费用额度被全局余额裁剪到 {grant.max_cost_usd} USD")

        self.allocated.max_llm_calls += grant.max_llm_calls
        self.allocated.max_tool_calls += grant.max_tool_calls
        self.allocated.max_tokens += grant.max_tokens
        if grant.max_cost_usd:
            self.allocated_cost_usd = round(self.allocated_cost_usd + grant.max_cost_usd, 6)
        if task_id:
            self.allocations.append(task_id)
        return grant.reserve(), "; ".join(notes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total.to_dict(),
            "allocated": self.allocated.to_dict(),
            "allocated_cost_usd": self.allocated_cost_usd,
            "allocations": list(self.allocations),
        }


# ----------------------------------------------------------------------
# 权限
# ----------------------------------------------------------------------
class GrantError(RuntimeError):
    """权限不足或令牌无效 (不静默放行)。"""


#: 可读范围: 资料源/工具/上下文。运行时据此裁剪 `ContextPack`, 而不是靠提示词。
READ_SCOPES: frozenset[str] = frozenset({
    "sources",        # 检索与已登记来源
    "fulltext",       # 已取得的全文/定位
    "cases",          # 案例卡
    "datasets",       # 只读数据源
    "objects",        # 版本化研究对象 (只读)
    "tools:search",
    "tools:pdf",
    "tools:vector",
    "tools:readonly_data",
    "tools:math",
    "tools:stats",
    "tools:figure",
    "tools:latex",
})

#: 可写能力: 提交候选变更所需的能力名。判定层写状态**不在**此表内 ——
#: 状态由中央规则产生, 智能体没有对应能力可申请。
CAPABILITY_TOOLS: frozenset[str] = frozenset({
    "propose_claim",
    "propose_model",
    "propose_derivation",
    "propose_evidence",
    "propose_validation_plan",
    "propose_manuscript",
    "propose_figure",
    "propose_review",
})

#: 资料策略 → **只有这些策略才允许**的外部取数能力 (G09)。
#:
#: 角色能力表 (`AGENT_CAPABILITIES`) 说的是"这个角色**能**做什么", 用户授权说的是
#: "这次**准**做什么"。两者必须求交: 此前 `grant_for()` 只看角色表, 于是一个
#: `source_policy="user_kb"` 的任务照样拿到 `tools:search` —— 也就是"只用本地库"
#: 的任务可以联网外搜。这不只是越权, 还会把外部来源混进本该受控的证据链。
#:
#: `both` 同时包含本地与外部; 空策略 (未声明) 按**最保守**处理 (只给本地), 因为
#: "没说要联网"不等于"可以联网"。
SOURCE_POLICY_SCOPES: dict[str, frozenset[str]] = {
    "user_kb": frozenset(),
    "both": frozenset({"tools:search", "tools:pdf", "tools:vector"}),
    "autonomous": frozenset({"tools:search", "tools:pdf", "tools:vector"}),
    "": frozenset(),
}

#: 需要用户外部授权才能使用的读范围 (与 `SOURCE_POLICY_SCOPES` 的取值并集一致)。
EXTERNAL_SCOPES: frozenset[str] = frozenset({"tools:search", "tools:pdf", "tools:vector"})


class CapabilityGrant(BaseModel):
    """运行时签发的权限令牌。

    模型能看到令牌内容, 但**造不出**它: 构造入口要求 `issued_by == _ISSUER`,
    且 `issued_at` 非空。`is_valid()` 同时校验签发者与签发时间。

    不使用密码学签名: 令牌在进程内传递, 威胁模型是"模型自己拼一个 Grant 对象"
    而不是"伪造一条外部消息"; 因此校验签发者标识即可, 并保留 `signature`
    供未来跨进程传递时升级。
    """

    grant_id: str = ""
    task_id: str = ""
    agent: str = ""
    read_scopes: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    may_submit: bool = False
    may_change_conclusion: bool = False
    #: 本次任务**允许**使用的资料源 (来自用户授权 ∩ 任务范围)。空 = 未指定集合。
    source_set_ids: list[str] = Field(default_factory=list)
    #: 本次任务生效的资料策略 (`user_kb` / `autonomous` / `both`)。
    #: 它决定了外部检索能力是否被签发 —— 见 `SOURCE_POLICY_SCOPES`。
    source_policy: str = ""
    #: 允许读写的本机路径 (路径导入等按范围授权的动作据此校验)。
    allowed_paths: list[str] = Field(default_factory=list)
    issued_by: str = ""
    issued_at: str = ""
    expires_at: str = ""
    signature: str = ""

    #: 唯一合法签发者标识 (见模块级 `GRANT_ISSUER`)。
    @model_validator(mode="after")
    def _check_fields(self) -> CapabilityGrant:
        unknown_scopes = [s for s in self.read_scopes if s not in READ_SCOPES]
        if unknown_scopes:
            raise ValueError(f"未登记的读范围: {', '.join(sorted(unknown_scopes))}")
        unknown_tools = [t for t in self.tools if t not in CAPABILITY_TOOLS]
        if unknown_tools:
            raise ValueError(f"未登记的可写能力: {', '.join(sorted(unknown_tools))}")
        if self.may_submit and not self.tools:
            raise ValueError("may_submit=True 但没有任何可写能力")
        if self.may_change_conclusion and not self.may_submit:
            raise ValueError("may_change_conclusion 需要 may_submit")
        return self

    def is_valid(self) -> bool:
        return self.issued_by == GRANT_ISSUER and bool(self.issued_at) and bool(self.grant_id)

    def allows_read(self, scope: str) -> bool:
        return self.is_valid() and scope in self.read_scopes

    def allows_tool(self, tool: str) -> bool:
        """可写能力检查。读工具走 `allows_read('tools:xxx')`。"""
        return self.is_valid() and self.may_submit and tool in self.tools

    def allows_source_set(self, source_set_id: str) -> bool:
        """该资料源是否在本次任务的授权范围内。

        **未指定集合时按"未授权"处理**: 令牌里 `source_set_ids` 为空意味着这次派工
        没有携带任何用户资料库授权 (例如纯自足的形式化任务), 而不是"任意资料源都行"。
        这一条正是 G09 的修复点 —— 此前工具可以拿任意 `source_id` 去读。
        """
        if not self.is_valid():
            return False
        return bool(source_set_id) and source_set_id in self.source_set_ids

    def allows_path(self, path: str) -> bool:
        """本机路径是否在授权目录内 (前缀匹配, 大小写按平台)。

        用规范化后的路径做前缀比较, 避免 `../` 之类逃逸出授权目录。
        """
        if not self.is_valid() or not path:
            return False
        import os

        def norm(value: str) -> str:
            return os.path.normcase(os.path.normpath(str(value)))

        target = norm(path)
        for root in self.allowed_paths:
            base = norm(root)
            if target == base or target.startswith(base.rstrip("\\/") + os.sep):
                return True
        return False

    def require_tool(self, tool: str) -> None:
        if not self.allows_tool(tool):
            raise GrantError(
                f"任务 {self.task_id or '(未知)'} 的令牌不允许能力 {tool!r}"
                f" (已授权: {', '.join(self.tools) or '无'})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "read_scopes": list(self.read_scopes),
            "tools": list(self.tools),
            "may_submit": self.may_submit,
            "may_change_conclusion": self.may_change_conclusion,
            "valid": self.is_valid(),
            "issued_at": self.issued_at,
        }


#: 各角色的默认可写能力与可读范围 (运行时据此签发令牌; 单一真相源)。
AGENT_CAPABILITIES: dict[str, dict[str, Any]] = {
    "evidence": {
        "tools": ("propose_evidence",),
        "reads": ("sources", "fulltext", "cases", "datasets", "objects",
                  "tools:search", "tools:pdf", "tools:vector", "tools:readonly_data"),
        "may_change_conclusion": False,
    },
    "modeling": {
        "tools": ("propose_model",),
        "reads": ("sources", "objects", "tools:search", "tools:math"),
        "may_change_conclusion": False,
    },
    "reasoning": {
        "tools": ("propose_claim", "propose_derivation"),
        "reads": ("sources", "fulltext", "cases", "objects", "tools:search",
                  "tools:math", "tools:stats", "tools:readonly_data"),
        "may_change_conclusion": False,
    },
    "validation": {
        "tools": ("propose_validation_plan",),
        "reads": ("sources", "datasets", "objects", "tools:readonly_data",
                  "tools:stats", "tools:math"),
        "may_change_conclusion": False,
    },
    "writing": {
        "tools": ("propose_manuscript",),
        "reads": ("sources", "fulltext", "objects", "tools:latex"),
        "may_change_conclusion": False,
    },
    "figures": {
        "tools": ("propose_figure",),
        "reads": ("datasets", "objects", "tools:figure"),
        "may_change_conclusion": False,
    },
    # 审阅只能提出意见: 它可以要求降级 (由中央规则执行), 但不能升级, 也不能写状态。
    "review": {
        "tools": ("propose_review",),
        "reads": ("sources", "fulltext", "cases", "objects"),
        "may_change_conclusion": False,
    },
    "supervisor": {
        "tools": (),
        "reads": ("sources", "cases", "datasets", "objects",
                  "tools:search", "tools:readonly_data"),
        "may_change_conclusion": False,
    },
}


#: 每个角色**可以**提交的对象种类。超出即被运行时拦下 (合并计划 §5.2:
#: "不能只靠 `may_change_conclusion=False` 布尔标记", 工具注册与存储入口必须
#: 实际限制写入)。放在协议层是为了让角色实现与运行时校验引用同一份真相源。
WRITABLE_KINDS: dict[str, frozenset[str]] = {
    "evidence": frozenset({"evidence", "source", "card", "case", "dataset"}),
    "modeling": frozenset({"model", "assumption", "definition"}),
    "reasoning": frozenset({"claim", "obligation", "verification", "gap"}),
    "validation": frozenset({"validation_plan"}),
    "writing": frozenset({"manuscript", "block"}),
    "figures": frozenset({"figure"}),
    "review": frozenset({"review_issue", "revision_task"}),
    "supervisor": frozenset({"brief", "team_plan", "task"}),
}


def writable_kinds(agent: str) -> frozenset[str]:
    """该角色可提交的对象种类 (未登记角色返回空集 —— 不放行)。"""
    return WRITABLE_KINDS.get(role_of(agent) or agent, frozenset())


def grant_for(task: AgentTask, *, extra_reads: tuple[str, ...] = (),
              extra_tools: tuple[str, ...] = (),
              authorized_source_sets: Sequence[str] | None = None,
              allowed_paths: Sequence[str] | None = None) -> CapabilityGrant:
    """按 **角色能力 ∩ 用户授权 ∩ 本次任务范围** 为一个任务签发令牌。

    三者的分工 (§3.2 G09):
    - **角色能力** (`AGENT_CAPABILITIES`): 这个角色能做什么 (静态);
    - **用户授权** (`source_policy` + `authorized_source_sets`): 这次准做什么
      (例如"只用本地库"的任务**不得**获得联网检索能力);
    - **任务范围** (`task.source_set_ids`): 本次派工实际携带哪些资料源。

    `extra_*` 供运行时按任务契约**显式**加授权 (例如某次检索任务额外读取一个已授权
    数据源); 它不是给模型用的入口 —— 模型没有调用 `grant_for` 的路径。
    注意 `extra_reads` 也必须过用户授权: 例外只对"本地只读"生效, 不能用来绕过
    资料策略拿到联网能力。
    """
    role = role_of(task.agent)
    spec = AGENT_CAPABILITIES.get(role)
    if spec is None:
        raise GrantError(f"未登记的角色 {task.agent!r} 无法签发权限令牌")
    policy = str(task.source_policy or "").strip()
    if policy not in SOURCE_POLICY_SCOPES:
        raise GrantError(
            f"未登记的资料策略 {policy!r} 无法签发权限令牌"
            f" (可选: {', '.join(sorted(k for k in SOURCE_POLICY_SCOPES if k))})"
        )
    permitted_by_policy = SOURCE_POLICY_SCOPES[policy]
    tools = list(dict.fromkeys([*spec["tools"], *extra_tools]))
    reads = [
        scope for scope in dict.fromkeys([*spec["reads"], *extra_reads])
        # 外部取数能力必须逐项得到用户授权 (本地范围不受影响)
        if scope not in EXTERNAL_SCOPES or scope in permitted_by_policy
    ]
    # 授权的资料源 = 用户给的合法集合 ∩ 本次任务声明的范围
    declared = [str(value) for value in (task.source_set_ids or []) if str(value)]
    if authorized_source_sets is None:
        source_sets = declared
    else:
        allowed = {str(value) for value in authorized_source_sets if str(value)}
        source_sets = [value for value in declared if value in allowed]
    unknown_tools = [t for t in tools if t not in CAPABILITY_TOOLS]
    if unknown_tools:
        raise GrantError(f"能力表含未登记的可写能力: {', '.join(unknown_tools)}")
    grant = CapabilityGrant(
        grant_id=new_id("grant"),
        task_id=task.task_id,
        agent=role,
        read_scopes=reads,
        tools=tools,
        may_submit=bool(tools),
        may_change_conclusion=bool(spec["may_change_conclusion"]),
        source_set_ids=source_sets,
        source_policy=policy,
        allowed_paths=[str(value) for value in (allowed_paths or []) if str(value)],
        issued_by=GRANT_ISSUER,
        issued_at=utcnow(),
    )
    return grant


class AgentContract(BaseModel):
    """一个角色对外的职责声明 (注册表使用; 也是行为测试的依据)。"""

    agent: str
    label: str = ""
    objective: str = ""
    #: 必须提交的成果类型 (用于运行时校验结果是否满足契约)。
    deliverables: list[str] = Field(default_factory=list)
    #: 该角色可以提出的后续需求类型。
    need_kinds: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    may_change_conclusion: bool = False
    #: 是否能把科学结论降级 (只有审阅能提出)。
    may_request_downgrade: bool = False

    @field_validator("agent")
    @classmethod
    def _known(cls, value: str) -> str:
        role = role_of(value)
        if not role:
            raise ValueError(f"未登记的角色: {value!r}")
        return role

    @model_validator(mode="after")
    def _fill_defaults(self) -> AgentContract:
        spec = AGENT_CAPABILITIES.get(self.agent, {})
        if not self.reads:
            self.reads = list(spec.get("reads", ()))
        if not self.tools:
            self.tools = list(spec.get("tools", ()))
        if not self.label:
            self.label = ROLE_LABELS.get(self.agent, self.agent)
        return self


def agent_capability(agent: str) -> AgentContract | None:
    role = role_of(agent)
    if not role:
        return None
    return AgentContract(agent=role)


# ----------------------------------------------------------------------
# 对象与产物身份
# ----------------------------------------------------------------------
#: 权威对象的种类。**一次迁移中每种对象只能有一个权威写入口** (合并计划 §6.1),
#: 因此这里是封闭集合: 拼错种类会在构造 `ObjectRef` 时被拒, 而不是静默建出
#: 第二份真相源。
OBJECT_KINDS: frozenset[str] = frozenset({
    "claim",
    "obligation",
    "model",
    "assumption",
    "definition",
    "route",
    "gap",
    "evidence",
    "source",
    "card",
    "case",
    "dataset",
    "snapshot",
    "manuscript",
    "block",
    "figure",
    "review_issue",
    "revision_task",
    "validation_plan",
    "verification",
    "brief",
    "team_plan",
    "task",
    "artifact",
})

#: 旧代码里出现过的别名 -> 权威种类。用于兼容适配器, 不做双向同步。
OBJECT_KIND_ALIASES: dict[str, str] = {
    "proof_obligation": "obligation",
    "proof": "verification",
    "verification_record": "verification",
    "research_model": "model",
    "lit_record": "source",
    "source_evidence": "source",
    "reviewissue": "review_issue",
    "issue": "review_issue",
    "figure_spec": "figure",
    "experiment_spec": "validation_plan",
    "paper": "manuscript",
    "report": "manuscript",
}


def canonical_object_kind(kind: str) -> str:
    """归一对象种类名; 认不出来返回空串 (调用方如实报错, 不猜)。"""
    key = str(kind or "").strip().lower().replace("-", "_")
    if key in OBJECT_KINDS:
        return key
    return OBJECT_KIND_ALIASES.get(key, "")


class ArtifactKind(str, Enum):
    """产物的物理形态 (与"研究对象种类"不同: 一份 manuscript 有 tex/pdf 两个产物)。"""

    text = "text"
    markdown = "markdown"
    latex = "latex"
    pdf = "pdf"
    svg = "svg"
    png = "png"
    json = "json"
    csv = "csv"
    python = "python"
    other = "other"


ARTIFACT_KINDS: frozenset[str] = frozenset(k.value for k in ArtifactKind)


class ArtifactRef(BaseModel):
    """产物引用: 只带标识与摘要, **正文不平铺进图状态** (合并计划 §6.1)。"""

    artifact_id: str
    kind: ArtifactKind = ArtifactKind.other
    uri: str = ""                     # 相对产物根的路径; 绝对路径不进入共享状态
    label: str = ""
    object_ref: ObjectRef | None = None    # 对应哪个版本化研究对象
    sha256: str = ""
    bytes: int = 0
    produced_by: str = ""             # agent_run_id
    created_at: str = Field(default_factory=utcnow)
    #: 供展示的摘要 (不承载可写状态); 长文正文留在产物文件里。
    summary: str = ""

    @field_validator("uri")
    @classmethod
    def _no_absolute(cls, value: str) -> str:
        """绝对路径不进入共享状态: 界面与日志只展示相对位置 (合并计划 §13.3)。"""
        text = str(value or "")
        if len(text) > 2 and text[1] == ":" and text[2] in "\\/":
            raise ValueError(f"产物引用不得使用绝对路径: {text}")
        if text.startswith(("\\\\", "//")):
            raise ValueError(f"产物引用不得使用绝对路径: {text}")
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "uri": self.uri,
            "label": self.label,
            "object_ref": (self.object_ref.model_dump() if self.object_ref else None),
            "sha256": self.sha256,
            "bytes": self.bytes,
            "produced_by": self.produced_by,
            "created_at": self.created_at,
            "summary": self.summary,
        }


# ----------------------------------------------------------------------
# 研究需求与候选变更
# ----------------------------------------------------------------------
class NeedKind(str, Enum):
    """跨职责需求的种类 (合并计划 §4.2「受阻处理」的机器可读形式)。"""

    more_sources = "more_sources"            # 缺来源 -> 派检索
    source_locator = "source_locator"        # 有来源但定位不到原文 -> 派检索
    reader_source = "reader_source"          # 需重新阅读某来源
    model_condition = "model_condition"      # 缺模型条件 -> 派建模
    counterexample = "counterexample"        # 出现反例 -> 派推理修订
    empirical_support = "empirical_support"  # 缺经验支持 -> 派验证方案
    derivation = "derivation"                # 缺推导 -> 派推理
    clause = "clause"                        # 引理缺适用条件 (计划 §4.3 的例子)
    figure_data = "figure_data"              # 图表发现数据缺口
    manuscript_revision = "manuscript_revision"
    review = "review"
    clarification = "clarification"          # 只能由主控转成 request_clarification


#: 需求种类 -> 应当承接的角色 (主控据此转成 AgentTask; 只是建议, 主控可另派)。
NEED_OWNER: dict[str, str] = {
    NeedKind.more_sources.value: "evidence",
    NeedKind.source_locator.value: "evidence",
    NeedKind.reader_source.value: "evidence",
    NeedKind.model_condition.value: "modeling",
    NeedKind.counterexample.value: "reasoning",
    NeedKind.derivation.value: "reasoning",
    NeedKind.clause.value: "evidence",
    NeedKind.empirical_support.value: "validation",
    NeedKind.figure_data.value: "evidence",
    NeedKind.manuscript_revision.value: "writing",
    NeedKind.review.value: "review",
    NeedKind.clarification.value: "supervisor",
}


class ResearchNeed(BaseModel):
    """子智能体向上提出的**具体**需求。

    只说"我缺东西"不够: 主控要能据此直接构造下一个任务, 因此这里必须写清
    缺什么、为什么、需要什么才算满足、卡住了哪个对象。
    """

    need_id: str = Field(default_factory=lambda: new_id("need"))
    kind: NeedKind
    #: 一句话需求 (主控会把它作为新任务 objective 的素材)。
    statement: str
    why: str = ""
    #: 满足条件: 主控据此写新任务的 acceptance_criteria。
    acceptance: list[str] = Field(default_factory=list)
    #: 需要哪个角色接 (默认按 NEED_OWNER 推导)。
    owner: str = ""
    #: 受阻塞的研究对象版本 (证据未定时的强结论写作属于这种情况)。
    blocked_refs: list[ObjectRef] = Field(default_factory=list)
    #: 建议的检索式/参数等提示 (是提示, 不是指令)。
    hints: dict[str, Any] = Field(default_factory=dict)
    #: 提出者 (agent_run_id) 与时间。
    raised_by: str = ""
    created_at: str = Field(default_factory=utcnow)
    #: 是否阻断当前交付 (审阅可置 True)。
    blocking: bool = False

    @model_validator(mode="after")
    def _fill_owner(self) -> ResearchNeed:
        if not self.owner:
            self.owner = NEED_OWNER.get(self.kind.value, "")
        return self

    def assignment_key(self, project_id: str, problem_id: str) -> str:
        """该需求的**稳定**派工键 (只由需求类型与承接角色决定)。

        为什么不用 `statement`: 需求措辞常含**会变的计数** ("3 条命中缺少定位" ->
        "2 条…"), 拿它当键会让同一条需求每轮都变成新任务, 主控于是反复派工直到烧光
        预算 (实测过)。需求类型 + 角色才是"这件事"的身份; 措辞只作为任务描述。
        """
        return idempotency_key_for(project_id, problem_id, 0, self.owner,
                                   f"need:{self.kind.value}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "need_id": self.need_id,
            "kind": self.kind.value,
            "statement": self.statement,
            "why": self.why,
            "acceptance": list(self.acceptance),
            "owner": self.owner,
            "blocked_refs": [r.model_dump() for r in self.blocked_refs],
            "hints": dict(self.hints),
            "raised_by": self.raised_by,
            "created_at": self.created_at,
            "blocking": self.blocking,
        }


class ChangeProposal(BaseModel):
    """智能体提交的**候选**变更。

    这只是候选: 运行时检查 schema、对象归属、输入版本、资料权限与证据后, 才由
    统一状态提交模块 (`research/kernel.py`) 决定是否写入。`may_change_conclusion`
    为真**不代表**结论会变, 它只表示"这条候选有权被送进判定层"; 实际等级仍由
    证书链与中央规则产生。
    """

    proposal_id: str = Field(default_factory=lambda: new_id("chg"))
    kind: str                                # 对象种类 (OBJECT_KINDS)
    object_id: str = ""                      # 空 = 新建
    expected_revision: int | None = None     # 依据的版本; 与当前不一致则过期
    payload: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    #: 依据的输入版本 (read-set)。运行时据此做事务一致性检查 (合并计划 §6.3)。
    input_versions: dict[str, int] = Field(default_factory=dict)
    evidence_refs: list[ObjectRef] = Field(default_factory=list)
    verification_refs: list[ObjectRef] = Field(default_factory=list)
    may_change_conclusion: bool = False

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        canonical = canonical_object_kind(value)
        if not canonical:
            raise ValueError(f"未登记的对象种类: {value!r}")
        return canonical

    def idempotency_key(self, task_id: str = "") -> str:
        """同一依据 + 同一候选内容的确定性键 (重连/重试不得重复提交)。"""
        return stable_id(
            "chgkey",
            task_id or self.proposal_id,
            self.kind,
            self.object_id,
            self.expected_revision if self.expected_revision is not None else "",
            hashlib.sha256(
                json.dumps(self.payload, sort_keys=True, ensure_ascii=False,
                           default=str).encode("utf-8")
            ).hexdigest()[:16],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "object_id": self.object_id,
            "expected_revision": self.expected_revision,
            "payload": dict(self.payload),
            "rationale": self.rationale,
            "input_versions": dict(self.input_versions),
            "evidence_refs": [r.model_dump() for r in self.evidence_refs],
            "verification_refs": [r.model_dump() for r in self.verification_refs],
            "may_change_conclusion": self.may_change_conclusion,
        }


class IssueSeverity(str, Enum):
    blocking = "blocking"      # 阻断交付
    major = "major"
    minor = "minor"
    advisory = "advisory"


class ReviewIssueRef(BaseModel):
    """审阅问题引用 (稳定的 issue id + 受影响对象; 详情留在 ReviewReport 里)。"""

    issue_id: str
    severity: IssueSeverity = IssueSeverity.minor
    category: str = ""                        # science / citation / readability / figure
    summary: str = ""
    affected_refs: list[ObjectRef] = Field(default_factory=list)
    #: 建议由哪个角色返工 (主控决定是否采纳)。
    suggested_owner: str = ""
    acceptance: list[str] = Field(default_factory=list)
    blocking: bool = False
    #: 原始定位 (原文片段/行号), 供界面回链。
    locator: str = ""

    @field_validator("suggested_owner")
    @classmethod
    def _owner_known(cls, value: str) -> str:
        if not value:
            return ""
        role = role_of(value)
        if not role:
            raise ValueError(f"未登记的角色: {value!r}")
        return role

    @model_validator(mode="after")
    def _blocking_from_severity(self) -> ReviewIssueRef:
        if self.severity == IssueSeverity.blocking:
            self.blocking = True
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity.value,
            "category": self.category,
            "summary": self.summary,
            "affected_refs": [r.model_dump() for r in self.affected_refs],
            "suggested_owner": self.suggested_owner,
            "acceptance": list(self.acceptance),
            "blocking": self.blocking,
            "locator": self.locator,
        }


# ----------------------------------------------------------------------
# 任务
# ----------------------------------------------------------------------
class TaskStatus(str, Enum):
    """任务生命周期 (合并计划 §6.2)。"""

    queued = "queued"
    running = "running"
    waiting = "waiting"          # 等依赖结果, 不是失败
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class TaskOutcome(str, Enum):
    """子智能体交回的结果判定 (与任务状态区分: 状态由运行时维护)。"""

    completed = "completed"
    partial = "partial"
    blocked = "blocked"          # 缺输入/权限/前置条件 -> 主控补派工
    failed = "failed"            # 执行失败 (工具/解析/超预算)
    cancelled = "cancelled"


OUTCOME_TERMINAL: frozenset[str] = frozenset(o.value for o in TaskOutcome)

#: 已完成任务在恢复时必须跳过的状态 (幂等)。
RETIRED_STATUSES: frozenset[str] = frozenset({
    TaskStatus.completed.value,
    TaskStatus.partial.value,
})


def idempotency_key_for(project_id: str, problem_id: str, plan_version: int,
                        agent: str, objective: str) -> str:
    """同一"角色 + 目标"的确定性键 —— **与计划版本无关**。

    用途: 重连、超时重试或主控重新派工时识别"这件事已经做过了", 避免重复付费调用与
    无限重派 (合并计划 §5.3)。措辞变化会改变键 —— 这是有意的: 目标变了就是新任务。

    `plan_version` 只作为审计字段参与签名之外的记录, **不进入键**: 计划升版不代表
    要做新东西, 把版本算进键会让"同一句需求"每升一版就变成新任务 (实测: 同一件事
    被派十几次直到预算耗尽)。
    """
    del plan_version                        # 有意不参与键; 见 docstring
    return stable_id("taskkey", project_id, problem_id, role_of(agent) or agent,
                     " ".join(str(objective).split()))


class AgentTask(BaseModel):
    """主控派给某个子智能体的一次任务 (合并计划 §5 的接口示意, 已补齐默认值)。

    每次派工必须能回答: 要回答哪个子问题、预期获得什么新信息、依赖哪些结果、
    什么算完成、失败如何处理 (§4.2)。因此 `objective`/`acceptance_criteria`/
    `depends_on` 不是可选说明, 而是契约字段。
    """

    task_id: str = Field(default_factory=lambda: new_id("task"))
    agent: str
    objective: str
    #: 这条任务要回答的子问题 (可为空 = 直接服务于主问题)。
    subquestion: str = ""
    #: 预期获得的新信息 (主控派工依据; 用于事后判断"缺口有没有减少")。
    expected_gain: str = ""
    project_id: str = ""
    problem_id: str = ""
    run_id: str = ""
    branch_id: str = ""
    plan_version: int = 1
    input_refs: list[ObjectRef] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    #: 运行时签发的权限令牌 id (令牌本体随 ContextPack 传递)。
    capability_grant_id: str = ""
    budget: TaskBudget = Field(default_factory=TaskBudget)
    idempotency_key: str = ""
    #: 携带的资料源范围 (只读, 受资料权限约束)。
    source_set_ids: list[str] = Field(default_factory=list)
    source_policy: str = ""
    #: 任务级提示 (例如建议检索式); 是提示, 不是指令。
    hints: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)
    attempt: int = 1
    #: 失败如何处理 (主控在派工时声明, 而不是让子智能体自己决定重试无限次)。
    on_failure: str = ""
    #: 是否需要人工判断才能继续 (主控按 §4.1 判断)。
    needs_human: bool = False

    @field_validator("agent")
    @classmethod
    def _known_agent(cls, value: str) -> str:
        role = role_of(value)
        if not role:
            raise ValueError(f"未登记的角色: {value!r}")
        if role == "supervisor":
            raise ValueError("主控不承接派工任务 (supervisor 只派发)")
        return role

    @field_validator("subquestion", "objective")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = str(value or "").strip()
        return text

    @model_validator(mode="after")
    def _fill_keys(self) -> AgentTask:
        if not self.objective:
            raise ValueError("AgentTask.objective 不得为空")
        if not self.idempotency_key:
            self.idempotency_key = idempotency_key_for(
                self.project_id, self.problem_id, self.plan_version,
                self.agent, self.objective)
        return self

    def is_worker(self) -> bool:
        return self.agent in WORKER_ROLES

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "objective": self.objective,
            "subquestion": self.subquestion,
            "expected_gain": self.expected_gain,
            "project_id": self.project_id,
            "problem_id": self.problem_id,
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "plan_version": self.plan_version,
            "input_refs": [r.model_dump() for r in self.input_refs],
            "depends_on": list(self.depends_on),
            "acceptance_criteria": list(self.acceptance_criteria),
            "capability_grant_id": self.capability_grant_id,
            "budget": self.budget.to_dict(),
            "idempotency_key": self.idempotency_key,
            "source_set_ids": list(self.source_set_ids),
            "source_policy": self.source_policy,
            "hints": dict(self.hints),
            "created_at": self.created_at,
            "attempt": self.attempt,
            "on_failure": self.on_failure,
            "needs_human": self.needs_human,
        }


class ContextPack(BaseModel):
    """按任务组装的**只读**上下文 (合并计划 §6.1: 角色视图由 context 层组装)。

    图状态只保存身份与引用; 这里携带的是已裁到任务范围的快照与令牌。
    绝不放评测答案: `evals/cases/*/expected_notes.md` 不得进入任何子智能体上下文。
    """

    pack_id: str = Field(default_factory=lambda: new_id("ctx"))
    task_id: str = ""
    agent: str = ""
    grant: CapabilityGrant | None = None
    #: 已裁范围的研究对象 (只读快照; 不是可写状态)。
    objects: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    #: 资料源摘要 (来源身份与权限; 不含全文副本)。
    sources: list[dict[str, Any]] = Field(default_factory=list)
    #: 上游任务的成果摘要 (按依赖传递, 不广播完整聊天历史)。
    upstream: list[dict[str, Any]] = Field(default_factory=list)
    #: 原始任务文本与附件**内容摘要** (附件始终带角色与来源, 不因此变成指令)。
    request: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    #: 组装时已知的缺口 (让子智能体知道自己在补什么)。
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def scoped_objects(self, kind: str) -> list[dict[str, Any]]:
        return list(self.objects.get(canonical_object_kind(kind) or kind, []))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "grant": (self.grant.to_dict() if self.grant else None),
            "objects": {k: list(v) for k, v in self.objects.items()},
            "sources": list(self.sources),
            "upstream": list(self.upstream),
            "request": self.request,
            "attachments": list(self.attachments),
            "gaps": list(self.gaps),
            "notes": list(self.notes),
        }


class AgentResult(BaseModel):
    """子智能体交回的成果 (合并计划 §5)。

    `outcome` 与"主问题是否解决"是两件事: 任务完成不等于研究完成 —— 主控据
    `followup_needs` 与缺口变化决定下一步。
    """

    task_id: str
    agent_run_id: str = ""
    agent: str = ""
    outcome: TaskOutcome = TaskOutcome.completed
    summary: str = ""
    #: 本次实际读取的对象版本 (read-set), 运行时据此做事务一致性检查。
    input_versions: dict[str, int] = Field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    proposed_changes: list[ChangeProposal] = Field(default_factory=list)
    #: 被运行时**拒绝**的候选 (越权 / 越界 / 令牌不允许)。
    #:
    #: 它们只进审计记录, **绝不进权威对象库** (§3.2 G07)。此前 `finalize()` 只把整条
    #: 结果降级为 partial, 候选仍留在 `proposed_changes` 里, 于是投影层照样登记 ——
    #: 实测一个 evidence 角色提交的 claim 被判违规后, 库里仍出现 `status=supported`。
    #: 保留(而不是丢弃)是为了让"模型提了什么、为什么被拒"可追溯。
    rejected_changes: list[dict[str, Any]] = Field(default_factory=list)
    verification_refs: list[ObjectRef] = Field(default_factory=list)
    followup_needs: list[ResearchNeed] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    issues: list[ReviewIssueRef] = Field(default_factory=list)
    usage: UsageRecord = Field(default_factory=UsageRecord)
    #: 失败/受阻原因 (outcome 非 completed 时必须说明)。
    failure_reason: str = ""
    #: 结构化成果载荷 (各角色自有 schema; 例如 EvidenceBundle / ModelProposal)。
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)
    #: 是否需要主控立刻重新规划 (例如出现反例)。
    replan: bool = False

    @field_validator("agent")
    @classmethod
    def _agent_known(cls, value: str) -> str:
        if not value:
            return ""
        role = role_of(value)
        if not role:
            raise ValueError(f"未登记的角色: {value!r}")
        return role

    @model_validator(mode="after")
    def _needs_reason(self) -> AgentResult:
        if (self.outcome in (TaskOutcome.failed, TaskOutcome.blocked, TaskOutcome.cancelled)
                and not self.failure_reason.strip()):
            self.failure_reason = f"{self.outcome.value}: 未说明原因"
        if self.outcome == TaskOutcome.blocked and not self.followup_needs:
            # 受阻却没说缺什么 -> 主控无从补派工。如实记一条未决项, 但不编造需求。
            self.unresolved.append("受阻未给出具体需求 (followup_needs 为空)")
        return self

    @property
    def is_success(self) -> bool:
        return self.outcome == TaskOutcome.completed

    def blocking_issues(self) -> list[ReviewIssueRef]:
        return [i for i in self.issues if i.blocking]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_run_id": self.agent_run_id,
            "agent": self.agent,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "input_versions": dict(self.input_versions),
            "artifact_refs": [a.to_dict() for a in self.artifact_refs],
            "proposed_changes": [c.to_dict() for c in self.proposed_changes],
            "verification_refs": [r.model_dump() for r in self.verification_refs],
            "followup_needs": [n.to_dict() for n in self.followup_needs],
            "unresolved": list(self.unresolved),
            "issues": [i.to_dict() for i in self.issues],
            "usage": self.usage.to_dict(),
            "failure_reason": self.failure_reason,
            "payload": dict(self.payload),
            "created_at": self.created_at,
            "replan": self.replan,
        }


class AgentRun(BaseModel):
    """一次具体执行 (重试产生新 agent_run, 复用原 task_id)。

    合并计划 §5.3: 记录 `agent/task/agent_run/prompt_version/model/tool_run/
    input_versions/output_hash`。
    """

    agent_run_id: str = Field(default_factory=lambda: new_id("run"))
    task_id: str = ""
    agent: str = ""
    attempt: int = 1
    status: TaskStatus = TaskStatus.queued
    prompt_version: str = ""
    models: list[str] = Field(default_factory=list)
    tool_runs: list[str] = Field(default_factory=list)
    input_versions: dict[str, int] = Field(default_factory=dict)
    usage: UsageRecord = Field(default_factory=UsageRecord)
    output_hash: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    #: 取消信号观测到的时刻 (停止 run 时通知运行中任务)。
    cancel_requested_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_run_id": self.agent_run_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "attempt": self.attempt,
            "status": self.status.value,
            "prompt_version": self.prompt_version,
            "models": list(self.models),
            "tool_runs": list(self.tool_runs),
            "input_versions": dict(self.input_versions),
            "usage": self.usage.to_dict(),
            "output_hash": self.output_hash,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "cancel_requested_at": self.cancel_requested_at,
        }


def signature_of(payload: Any, secret: str = "air.protocol") -> str:
    """结构化载荷的稳定摘要 (审计用: 同一输入得到同一签名)。

    不是密码学安全用途, 只用于"两份成果是否同源"的比对与审计记录完整性。
    """
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


