from __future__ import annotations

"""研究路线与失败记忆 (计划书 §4.1 ResearchRoute、§5.4 真正的路线切换, P1-2 失败驱动修订)。

修复 (计划书 §2 P1): 旧 `switch_strategy` 直接把 claim 改成 blocked, blocked 对象
不会自然进入有效重试路线 —— 等于"失败后无法改变研究方法"。现在:
- 换路产生一条**新的 ResearchRoute** 对象 (含目标、父路线、策略、预算、
  失败原因、恢复条件), 旧路线保留在历史中不覆盖;
- 失败档案 (`FailureRecord`) 持久保存失败原因、反例、不可用工具与新证据条件,
  重启后不会重复犯同样的错误;
- 被 blocked 的命题在满足恢复条件时重新进入有效动作集合。

P1-2 追加: 失败必须**分类** (资料没找到 / 模型被反例否定 / 形式化失败 / 工具未知 /
预算耗尽), 每类映射到不同的下一动作; 换路必须给出**实质变化** (新条件、新子命题或
不同机制), 只换工具名不算修订, 直接给出停止理由。
"""

import json
from dataclasses import dataclass, field

from src.research.schemas import (
    ActionType,
    FailureKind,
    ObjectRef,
    ResearchRoute,
    RouteStatus,
    new_id,
    utcnow,
)

# 自由文本失败类型 → 五类失败 (老调用点继续可用, 但数据变成可判定的类型)
_KIND_ALIASES: dict[str, FailureKind] = {
    "no_retrieval_hit": FailureKind.source_missing,
    "retrieval_failed": FailureKind.source_missing,
    "read_failed": FailureKind.source_missing,
    "no_card_extracted": FailureKind.source_missing,
    "missing_model_evidence": FailureKind.source_missing,
    "counterexample": FailureKind.model_refuted,
    "no_counterexample": FailureKind.model_refuted,
    "no_counterexample_found": FailureKind.model_refuted,
    "encoding_mismatch": FailureKind.formalization_failed,
    "formalization_failed": FailureKind.formalization_failed,
    "unknown": FailureKind.tool_unknown,
    "timeout": FailureKind.tool_unknown,
    "unsupported": FailureKind.tool_unknown,
    "unavailable": FailureKind.tool_unknown,
    "route_exhausted": FailureKind.budget_exhausted,
    "budget": FailureKind.budget_exhausted,
    "budget_exhausted": FailureKind.budget_exhausted,
}

# 每类失败 → 允许的下一动作 (只能在动作注册表内调度; 空元组表示只能停止/部分交付)
NEXT_ACTIONS: dict[FailureKind, tuple[ActionType, ...]] = {
    FailureKind.source_missing: (ActionType.retrieve_targeted, ActionType.read_source),
    FailureKind.model_refuted: (ActionType.propose_model, ActionType.revise_hypothesis,
                                ActionType.switch_strategy),
    FailureKind.formalization_failed: (ActionType.revise_hypothesis, ActionType.plan_proof,
                                       ActionType.switch_strategy),
    FailureKind.tool_unknown: (ActionType.switch_strategy,),
    FailureKind.budget_exhausted: (ActionType.deliver_partial, ActionType.stop_with_report),
}

_NEXT_HINT = {
    FailureKind.source_missing: "扩大术语/范围或换资料源后再读原文",
    FailureKind.model_refuted: "提出不同机制或新子命题后重试",
    FailureKind.formalization_failed: "先修编码/换表述, 再重新拆解义务",
    FailureKind.tool_unknown: "换后端或缩小问题范围; 同一工具不再重试",
    FailureKind.budget_exhausted: "输出部分结果与未决项, 不再派发新动作",
}


def classify_failure(kind: str) -> FailureKind:
    """把自由文本失败类型归入五类之一 (未知类型按工具类处理, 不当作"无失败")。"""
    text = (kind or "").strip().lower()
    if text in _KIND_ALIASES:
        return _KIND_ALIASES[text]
    if "budget" in text or "预算" in text:
        return FailureKind.budget_exhausted
    if any(w in text for w in ("retriev", "read", "no_hit", "missing", "资料", "检索")):
        return FailureKind.source_missing
    if any(w in text for w in ("counterexample", "refut", "反例", "否定")):
        return FailureKind.model_refuted
    if any(w in text for w in ("encod", "formal", "编码", "形式化")):
        return FailureKind.formalization_failed
    return FailureKind.tool_unknown


@dataclass
class FailureRecord:
    """失败档案条目: 区分"数学上错误"与"这次工具未能处理"。"""

    failure_id: str = field(default_factory=lambda: new_id("fail"))
    claim_id: str = ""
    route_id: str = ""
    kind: str = ""            # 原始自由文本类型
    failure_kind: FailureKind | None = None   # 归类后的失败类型
    reason: str = ""
    scientific: bool = False  # True = 数学上被否定; False = 运行/能力问题, 可重试
    tool: str = ""
    detail: str = ""
    recovery_condition: str = ""
    next_actions: list[str] = field(default_factory=list)   # 该类失败允许的下一动作
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict:
        return {
            "failure_id": self.failure_id, "claim_id": self.claim_id, "route_id": self.route_id,
            "kind": self.kind,
            "failure_kind": self.failure_kind.value if self.failure_kind else "",
            "reason": self.reason, "scientific": self.scientific,
            "tool": self.tool, "detail": self.detail,
            "recovery_condition": self.recovery_condition,
            "next_actions": list(self.next_actions), "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FailureRecord:
        items = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        raw = items.get("failure_kind")
        if raw:
            try:
                items["failure_kind"] = FailureKind(raw)
            except ValueError:
                items["failure_kind"] = classify_failure(str(raw))
        return cls(**items)


class RouteManager:
    """路线的创建、失败登记与重试判定 (内存态, 由引擎持久化到 runtime)。"""

    def __init__(self, routes: list[ResearchRoute] | None = None,
                 failures: list[FailureRecord] | None = None):
        self.routes: list[ResearchRoute] = list(routes or [])
        self.failures: list[FailureRecord] = list(failures or [])
        self._revision = 0

    # ---- 路线 ----
    def active_route(self, claim_id: str) -> ResearchRoute | None:
        for route in reversed(self.routes):
            if route.status == RouteStatus.active and (route.target_ref and route.target_ref.id == claim_id):
                return route
        return None

    def ensure_route(self, claim_id: str, goal: str = "", strategy: str = "") -> ResearchRoute:
        route = self.active_route(claim_id)
        if route is not None:
            return route
        route = ResearchRoute(
            goal=goal or f"研究结论 {claim_id}", strategy=strategy,
            target_ref=ObjectRef(id=claim_id, version=1),
        )
        self.routes.append(route)
        return route

    def switch(self, claim_id: str, reason: str, recovery_condition: str = "",
               new_strategy: str = "") -> ResearchRoute:
        """真实换路: 挂起/否决旧路线, 新建路线; 不修改命题状态。"""
        previous = self.active_route(claim_id)
        if previous is not None:
            previous.status = RouteStatus.failed
            previous.failure_reason = reason
            previous.recovery_condition = recovery_condition
        strategies = {r.strategy for r in self.routes if r.target_ref and r.target_ref.id == claim_id}
        strategy = new_strategy or self._next_strategy(previous.strategy if previous else "", strategies)
        route = ResearchRoute(
            goal=previous.goal if previous else f"研究结论 {claim_id}",
            parent_id=previous.id if previous else "",
            target_ref=ObjectRef(id=claim_id, version=1),
            strategy=strategy,
            budget_actions=max(3, (previous.budget_actions if previous else 10) // 2),
            recovery_condition=recovery_condition,
        )
        self.routes.append(route)
        return route

    @staticmethod
    def _next_strategy(current: str, used: set[str]) -> str:
        order = ["nonnegative_difference", "nonnegative_difference_strict",
                 "monotonicity_differentiation", "identity_simplification",
                 "targeted_retrieval", "counterexample_search", "special_case_first",
                 "weaker_conclusion"]
        for name in order:
            if name not in used and name != current:
                return name
        return "manual_review"

    def used_strategies(self, claim_id: str) -> set[str]:
        return {r.strategy for r in self.routes
                if r.target_ref and r.target_ref.id == claim_id and r.strategy}

    def routes_for(self, claim_id: str) -> list[ResearchRoute]:
        return [r for r in self.routes if r.target_ref and r.target_ref.id == claim_id]

    def exhausted(self, claim_id: str, max_routes: int) -> bool:
        return len(self.routes_for(claim_id)) >= max(1, max_routes)

    # ---- 失败档案 ----
    def record_failure(self, claim_id: str, kind: str, reason: str, *,
                       scientific: bool, tool: str = "", detail: str = "",
                       recovery_condition: str = "") -> FailureRecord:
        failure_kind = classify_failure(kind)
        route = self.active_route(claim_id)
        record = FailureRecord(
            claim_id=claim_id, route_id=route.id if route else "", kind=kind,
            failure_kind=failure_kind,
            reason=reason, scientific=scientific, tool=tool, detail=detail,
            recovery_condition=recovery_condition,
            next_actions=[a.value for a in NEXT_ACTIONS[failure_kind]],
        )
        self.failures.append(record)
        # 路线闭环: 记下实际结果与据此得到的下一判断
        if route is not None:
            route.actual_outcome = reason[:300]
            route.failure_kind = failure_kind
            route.next_judgement = (f"{failure_kind.value}: {_NEXT_HINT[failure_kind]}")
        return record

    def next_actions_for(self, claim_id: str) -> list[str]:
        """最近一次失败允许的下一动作 (空表示没有失败记录)。"""
        failures = self.failures_for(claim_id)
        return list(failures[-1].next_actions) if failures else []

    def revise(self, claim_id: str, failure_kind: FailureKind, *,
               reason: str = "", new_condition: str = "", new_subclaim: str = "",
               new_mechanism: str = "", tool_change: str = "", max_routes: int = 5,
               ) -> tuple[ResearchRoute | None, str]:
        """按失败类型提出新路线。

        必须给出**实质变化**: 新条件、新子命题或不同机制。只换工具名/措辞 → 返回
        `(None, 停止理由)` 并记一条 `no_progress` 失败, 而不是再造一条同义路线。
        例外: 失败类型是"工具未知"时, 换一个**没试过**的后端也算实质变化
        (计划书对该类的处置就是"换后端或缩小问题范围")。
        """
        change = (new_mechanism and f"不同机制: {new_mechanism}") or \
                 (new_subclaim and f"新子命题: {new_subclaim}") or \
                 (new_condition and f"新条件: {new_condition}") or ""
        if not change and failure_kind is FailureKind.tool_unknown and tool_change \
                and not self.tried(claim_id, "tool_unknown", tool_change):
            change = f"换后端: {tool_change}"
        if not change:
            self.record_failure(
                claim_id, "no_progress",
                reason or "失败后没有新条件/新子命题/不同机制, 只换了工具或说法",
                scientific=False,
                recovery_condition="需要新条件、新子命题或不同机制后才能继续")
            return None, ("无法继续: 失败后没有新条件、新子命题或不同机制; "
                          "只换工具名不构成研究路线修订")
        if self.exhausted(claim_id, max_routes):
            self.record_failure(claim_id, "route_exhausted",
                                f"已用尽 {max_routes} 条路线", scientific=False,
                                recovery_condition="人工指定新路线或补充资料后重试")
            return None, f"无法继续: 已用尽 {max_routes} 条路线, 输出未决报告"

        route = self.switch(claim_id, reason=reason or change,
                            recovery_condition=_NEXT_HINT[failure_kind],
                            new_strategy=self._strategy_for(failure_kind,
                                                            self.used_strategies(claim_id)))
        route.substantive_change = change
        route.failure_kind = failure_kind
        route.target_gap = route.target_gap or failure_kind.value
        route.expected_observation = (route.expected_observation
                                      or f"按新路线执行后, {failure_kind.value} 应被消除或转为未决说明")
        route.next_judgement = f"{failure_kind.value}: {_NEXT_HINT[failure_kind]}"
        self._revision += 1
        return route, f"新路线 {route.strategy} ({change})"

    @staticmethod
    def _strategy_for(failure_kind: FailureKind, used: set[str]) -> str:
        preferred = {
            FailureKind.source_missing: "targeted_retrieval",
            FailureKind.model_refuted: "counterexample_search",
            FailureKind.formalization_failed: "identity_simplification",
            FailureKind.tool_unknown: "special_case_first",
            FailureKind.budget_exhausted: "weaker_conclusion",
        }[failure_kind]
        if preferred not in used:
            return preferred
        return RouteManager._next_strategy("", used)

    def failures_for(self, claim_id: str) -> list[FailureRecord]:
        return [f for f in self.failures if f.claim_id == claim_id]

    def scientific_failure(self, claim_id: str, kind: str = "") -> FailureRecord | None:
        """是否存在"数学上被否定"的失败 (如已找到反例)。"""
        for failure in reversed(self.failures):
            if (failure.claim_id == claim_id and failure.scientific
                    and (not kind or failure.kind == kind)):
                return failure
        return None

    def tried(self, claim_id: str, kind: str, tool: str = "") -> bool:
        """该(类)失败是否已经尝试过 —— 避免重启后重复同一失败动作。

        比较的是**归类后的失败类型** (老档案只有自由文本 kind 时回落到文本比较),
        否则 "unknown" 与 "tool_unknown" 会被当成两回事, 同一后端会被反复重试。
        """
        wanted = classify_failure(kind)
        for failure in self.failures:
            if failure.claim_id != claim_id:
                continue
            recorded = failure.failure_kind or classify_failure(failure.kind)
            if recorded != wanted:
                continue
            if not tool or failure.tool == tool:
                return True
        return False

    def retryable(self, claim_id: str, new_evidence: bool = False) -> bool:
        """是否值得重试: 只有非科学性失败, 或出现了新证据/满足恢复条件时才重试。"""
        if self.scientific_failure(claim_id):
            return False
        if new_evidence:
            return True
        return any(not f.scientific for f in self.failures_for(claim_id))

    # ---- 持久化 ----
    def dump(self) -> dict:
        return {
            "routes": [r.model_dump(mode="json") for r in self.routes],
            "failures": [f.to_dict() for f in self.failures],
        }

    @classmethod
    def load(cls, data: dict | None) -> RouteManager:
        data = data or {}
        return cls(
            routes=[ResearchRoute.model_validate(r) for r in data.get("routes", [])],
            failures=[FailureRecord.from_dict(f) for f in data.get("failures", [])],
        )

    @staticmethod
    def dumps(manager: RouteManager) -> str:
        return json.dumps(manager.dump(), ensure_ascii=False)
