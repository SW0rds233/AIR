from __future__ import annotations

"""统一事件键与可聚合指标 (研究日志契约)。

为什么需要这个模块
------------------
研究日志此前是"各处随手 `("kind", {...})`": 同一个概念在不同地方可能用不同键名
(`claim` / `claim_id` / `target`), 也可能少写关键字段, 于是:

- 工作台无法可靠聚合"这次研究发生了什么"（只能靠字符串猜）;
- 少字段时是**静默**的: 事件照样落盘, 但下游读不到, 表现为"界面显示 0"。

本模块把事件类型与**必需键**写成一份目录 (`EVENT_SPECS`), 由
`ResearchStore.append_event` 在唯一入口校验:

- 未登记的事件类型: 仍入库（不丢证据）, 但计入 `anomalies` 并写一条
  `log_anomaly` 事件 —— **记录而不是阻断**, 与注入扫描的处理原则一致;
- 缺必需键: 同上, 异常里说明缺哪几个键。

因此"统一日志键"不是靠纪律, 而是每次写入都会被检查, 且异常本身可被查询。

指标
----
`metrics_from_store(store)` 给出可聚合的运行指标: 动作/工具调用与失败、提议采用与
拒绝、义务与结论计数、换路次数、缺口、异常, 以及资源用量与停止原因。它只读日志与
账本, 不触发任何研究动作, 也不改变任何结论状态。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EventSpec:
    """一种研究事件的契约: 必需键 + 用途说明。"""

    kind: str
    required: tuple[str, ...] = ()
    summary: str = ""
    surface: bool = False      # 是否值得在工作台"研究过程"里展示

    def missing(self, payload: dict | None) -> list[str]:
        data = payload or {}
        return [key for key in self.required if key not in data]


def _spec(kind: str, required: tuple[str, ...] = (), summary: str = "",
          surface: bool = False) -> tuple[str, EventSpec]:
    return kind, EventSpec(kind=kind, required=required, summary=summary, surface=surface)


# ----------------------------------------------------------------------
# 事件目录 (唯一权威)
# ----------------------------------------------------------------------
EVENT_SPECS: dict[str, EventSpec] = dict([
    # 生命周期
    _spec("run_start", ("project_id", "problem_id"), "研究运行开始", surface=True),
    _spec("formulated", ("claims", "obligations", "problem_id"),
          "问题被形式化为命题与义务", surface=True),
    _spec("capability_declared", ("claim_id", "problem_id", "category", "action"),
          "声明该命题类型的前置条件与动作", surface=True),
    _spec("candidate_confirmed", ("index", "statement"), "确认候选主路线",
          surface=True),
    _spec("log_anomaly", ("kind", "missing", "reason"),
          "日志契约异常 (键缺失/未登记类型)"),

    # 决策与提议 (R1)
    _spec("proposal", ("accepted", "rejected"), "本轮提议与拒绝理由", surface=True),
    _spec("proposal_used", ("action_type", "why_now"), "结构化提议被采用",
          surface=True),
    _spec("proposal_rejected", ("reasons",), "结构化提议被拒", surface=True),
    _spec("proposal_failed", ("reason",), "提议调用失败", surface=True),
    _spec("proposal_skipped", ("reason",), "提议被跳过 (预算/停用)", surface=True),

    # 推导与验证
    _spec("plan_proof", ("claim_id", "strategy"), "生成证明计划", surface=True),
    _spec("derive_step", ("claim_id", "steps"), "生成可审查推导步骤与反方审查",
          surface=True),
    _spec("verification_recorded", ("claim_id", "record_id", "status"),
          "登记一条验证记录 (原始工具结果)", surface=True),
    _spec("obligation_closed", ("claim_id", "obligation_id", "tool"),
          "义务被验证关闭", surface=True),
    _spec("rule_obligation_evaluated", ("claim_id", "obligation_id", "closed"),
          "规则型义务被判定", surface=True),
    _spec("design_necessity_evaluated",
          ("claim_id", "obligation_id", "verdict", "closed"),
          "设计/计数存在性判定的证书记录 (含引用定理)", surface=True),
    _spec("claim_refuted", ("claim_id", "witness"), "命题被反例否定", surface=True),
    _spec("counterexample", ("claim_id", "witness"), "找到反例", surface=True),
    _spec("claim_state_reconciled", ("claim_id", "status"), "命题状态按记录重算",
          surface=True),
    _spec("equality_condition", ("claim_id", "condition"), "派生出等号条件命题",
          surface=True),
    _spec("effect_estimated", ("claim_id", "estimate"), "得到效应估计", surface=True),

    # 证据与资料 (R0/R3)
    _spec("evidence_retrieved", ("claim_id", "count", "query"), "定向检索结果",
          surface=True),
    _spec("retrieval_coverage",
          ("problem_id", "policy", "queries", "hits", "ingested", "fulltext_available",
           "abstract_only", "executed", "uncovered"),
          "检索覆盖记录 (检索式/引擎/命中/入库/全文可得性/未覆盖)", surface=True),
    _spec("source_read", ("evidence_id", "locator"), "回到原文定位", surface=True),
    _spec("cards_extracted", ("count",), "抽取定理/定义卡", surface=True),
    _spec("evidence_interpreted", ("claim_id", "judged"), "判定证据支持关系",
          surface=True),
    _spec("evidence_attached", ("claim_id", "count"), "证据挂接到命题", surface=True),

    # 模型与实验 (R2/R5)
    _spec("model_proposed", ("claim_id", "model_id", "candidates"), "提出候选模型",
          surface=True),
    _spec("model_selected", ("claim_id", "model_id", "reason"), "选中领域模型",
          surface=True),
    _spec("distinguishing_test_proposed", ("claim_id", "kind", "statement"),
          "提出可区分检验", surface=True),
    _spec("experiment_spec_proposed", ("claim_id", "spec_id", "status"),
          "生成实验/仿真建议 (不自动执行)", surface=True),

    # 新颖性与路线
    _spec("novelty_assessed", ("claim_id", "status"), "新颖性对照结论", surface=True),
    _spec("route_switched", ("claim_id", "route_id", "strategy", "reason"),
          "策略换路 (新分支)", surface=True),
    _spec("hypothesis_revised", ("original", "weaker", "reason"), "假设被弱化修订",
          surface=True),
    _spec("assumption_revised", ("assumption_id",), "假设被修订并传播失效",
          surface=True),

    # 用户反馈与派生
    _spec("user_feedback", ("feedback", "actions"), "用户意见被转成对象级动作",
          surface=True),
    _spec("forked_from_snapshot", ("source_problem_id", "new_problem_id",
                                   "imported_claims"), "从快照派生新问题",
          surface=True),

    # 写作回流 (R4)
    _spec("writing_gaps_fed_back", ("count", "gaps", "problem_id"),
          "写作缺口回流成研究义务", surface=True),

    # 资料摄入 (知识库侧)
    _spec("ingest_manual", ("doc_id",), "摄入人工资料"),
    _spec("ingest_machine", ("doc_id", "title"), "摄入机读资料"),
])

SURFACE_EVENT_KINDS: tuple[str, ...] = tuple(
    kind for kind, spec in EVENT_SPECS.items() if spec.surface)

KNOWN_EVENT_KINDS: tuple[str, ...] = tuple(EVENT_SPECS)


def abnormal_event(payload: dict | None, kind: str) -> str:
    """返回该事件的契约问题描述 (无问题返回空串)。"""
    spec = EVENT_SPECS.get(kind)
    if spec is None:
        return f"未登记的事件类型 {kind!r}"
    missing = spec.missing(payload)
    if missing:
        return f"事件 {kind!r} 缺少必需键: {', '.join(missing)}"
    return ""


def validate_event(kind: str, payload: dict | None) -> list[str]:
    """公共校验: 返回缺失的必需键 (未登记类型返回空列表, 由调用方按类型处理)。"""
    spec = EVENT_SPECS.get(kind)
    return spec.missing(payload) if spec else []


def is_registered(kind: str) -> bool:
    return kind in EVENT_SPECS


# ----------------------------------------------------------------------
# 指标聚合
# ----------------------------------------------------------------------
@dataclass
class RunMetrics:
    """可聚合的运行指标 (只读日志与账本, 不改动任何研究状态)。"""

    events: dict[str, int] = field(default_factory=dict)
    actions: dict[str, int] = field(default_factory=dict)
    tool_calls: dict[str, int] = field(default_factory=dict)
    proposals: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    stopped_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "events": dict(self.events),
            "actions": dict(self.actions),
            "tool_calls": dict(self.tool_calls),
            "proposals": dict(self.proposals),
            "counts": dict(self.counts),
            "anomalies": list(self.anomalies),
            "usage": dict(self.usage),
            "stopped_reason": self.stopped_reason,
        }


def belongs_to_problem(payload: dict | None, problem_id: str,
                       *, claim_ids: set[str] | None = None) -> bool:
    """事件是否属于该研究问题 (R6: 日志也按问题隔离)。

    判定顺序:
    1. payload 里显式写了 `problem_id` → 必须一致;
    2. payload 里引用了命题 (`claim_id`) → 该命题必须在当前问题的命题集合里;
    3. 两者都没有 → 保留 (旧数据没有归属字段, 不能凭缺失就丢弃)。
    """
    data = payload or {}
    owner = str(data.get("problem_id", "") or "")
    if owner:
        return owner == problem_id
    claim = str(data.get("claim_id", "") or "")
    if not claim and data.get("kind"):
        # `log_anomaly` 用 `kind` 记录被检查的事件类型, 不带 claim_id
        claim = str(data.get("record_id", "") or "")
    if claim and claim_ids is not None:
        return claim in claim_ids
    return True


def metrics_from_store(store, problem_id: str = "", *,
                       claim_ids: set[str] | None = None,
                       limit_anomalies: int = 20) -> dict:
    """从研究库聚合指标。

    - `events`: 各事件类型出现次数 (带 problem_id 的事件按问题过滤);
    - `actions`: 动作账本的执行状态分布 (succeeded/failed/running/...);
    - `tool_calls`: 工具调用状态分布 (含外部工具的 unknown);
    - `proposals`: 提议采用/拒绝/失败/跳过次数;
    - `counts`: 义务与结论的**当前问题**计数 (传 `claim_ids` 时按命题归属过滤,
      否则只统计能归属到该问题的对象);
    - `anomalies`: 最近的事件契约异常 (缺键/未登记类型, 最多 limit_anomalies 条)。
    """
    metrics = RunMetrics()
    problem = problem_id or ""
    rows = store.events()
    if problem:
        rows = [e for e in rows
                if belongs_to_problem(e.get("payload"), problem, claim_ids=claim_ids)]
    for event in rows:
        kind = str(event.get("type", ""))
        metrics.events[kind] = metrics.events.get(kind, 0) + 1
        issue = abnormal_event(event.get("payload"), kind)
        if issue and issue not in metrics.anomalies:
            metrics.anomalies.append(issue)
    # 已落盘的契约异常是权威记录 (上面的重算只覆盖当前事件表)
    for record in store.log_anomalies(limit=limit_anomalies):
        issue = str(record.get("missing", "") or record.get("reason", ""))
        if issue and issue not in metrics.anomalies:
            metrics.anomalies.append(issue)

    for record in store.list_actions(problem):
        status = str(record.get("status", "") or "unknown")
        metrics.actions[status] = metrics.actions.get(status, 0) + 1
    metrics.tool_calls = _tool_run_counts(store, problem)

    metrics.proposals = {
        "used": metrics.events.get("proposal_used", 0),
        "rejected": metrics.events.get("proposal_rejected", 0),
        "failed": metrics.events.get("proposal_failed", 0),
        "skipped": metrics.events.get("proposal_skipped", 0),
    }

    from src.research.schemas import ObligationStatus
    from src.research.store import KIND_CLAIM, KIND_GAP, KIND_OBLIGATION

    def _mine(claim_id: str) -> bool:
        if claim_ids is None:
            return True
        return not claim_id or str(claim_id) in claim_ids

    obligations = [o for o in store.list_latest(KIND_OBLIGATION)
                   if _mine(o.get("claim_id", ""))]
    all_claims = store.list_latest(KIND_CLAIM)
    if claim_ids is None:
        claims = all_claims
    else:
        # 只统计属于本问题的命题: 归属由调用方 (引擎的 `_claims()`) 判定,
        # 这里不再"猜"旧数据的归属, 否则 B 问题会把 A 的结论算成自己的。
        claims = [c for c in all_claims if str(c.get("id", "")) in claim_ids]
    closed = ObligationStatus.closed.value
    metrics.counts = {
        "obligations": len(obligations),
        "obligations_closed": sum(1 for o in obligations if o.get("status") == closed),
        "claims": len(claims),
        "verifications": metrics.events.get("verification_recorded", 0),
        "route_switches": metrics.events.get("route_switched", 0),
        "writing_gaps_fed_back": metrics.events.get("writing_gaps_fed_back", 0),
        "experiment_specs": sum(1 for d in store.list_latest(KIND_GAP)
                                if str(d.get("id", "")).startswith("exp-")
                                and _mine(d.get("claim_id", ""))),
    }
    metrics.anomalies = metrics.anomalies[:limit_anomalies]
    return metrics.to_dict()


def _tool_run_counts(store, problem_id: str = "") -> dict[str, int]:
    """工具调用状态分布 (外部工具未知状态必须能看到)。"""
    out: dict[str, int] = {}
    for row in store.list_tool_runs(problem_id):
        status = str(row.get("status", "") or "unknown")
        out[status] = out.get(status, 0) + 1
    return out


__all__ = [
    "EVENT_SPECS",
    "KNOWN_EVENT_KINDS",
    "SURFACE_EVENT_KINDS",
    "EventSpec",
    "RunMetrics",
    "abnormal_event",
    "belongs_to_problem",
    "is_registered",
    "metrics_from_store",
    "validate_event",
]
