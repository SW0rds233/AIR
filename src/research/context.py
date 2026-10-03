from __future__ import annotations

"""ContextPack 组装 (计划书 §5.3-2、§5.5 记忆三层)。

控制器每一步只读取**当前目标与相关对象**, 不把整段历史聊天塞进提示词。
三层记忆:
1. 原始资料与工具产物 (不可变, 落盘);
2. 权威研究对象 (SQLite 版本化);
3. 供模型读取的压缩摘要 —— 必须带对象引用, 不能反过来覆盖原始证据。

摘要只用于导航: 它引用 `object_id@version`, 因此对象换代后摘要自动失效,
不会被误当作当前结论。
"""

from dataclasses import dataclass, field

from src.research.schemas import (
    Claim,
    ProofObligation,
    ResearchGap,
    ResearchRoute,
    SourceEvidence,
)

# 提示词上下文预算 (字符): 只放导航信息, 原始证据另行读取
DEFAULT_BUDGET_CHARS = 6000


@dataclass
class ContextPack:
    goal: str = ""
    gaps: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    obligations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)
    budget: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # 计划书 §3 R1: 提议必须能看到"当前候选项与冲突", 否则只能按缺口排序
    models: list[dict] = field(default_factory=list)          # 候选领域模型
    conflicts: list[dict] = field(default_factory=list)       # 证据冲突 (支持/反对并存)
    available_actions: list[str] = field(default_factory=list)  # 当前可派发的动作

    def render(self, budget_chars: int = DEFAULT_BUDGET_CHARS) -> str:
        """渲染为带对象引用的压缩摘要 (供模型提议动作用)。"""
        lines: list[str] = []
        lines.append(f"# 研究目标\n{self.goal or '(未指定)'}")
        if self.claims:
            lines.append("\n# 结论状态 (object_id@version)")
            for c in self.claims:
                lines.append(f"- {c['ref']} [{c['status']}] {c['statement']}")
        if self.obligations:
            lines.append("\n# 未决证明义务")
            for o in self.obligations:
                lines.append(f"- {o['ref']} ({o['kind']}, {o['status']}) {o['statement']}")
        if self.models:
            lines.append("\n# 候选领域模型 (object_id@version)")
            for m in self.models:
                mark = "已选" if m.get("selected") else "候选"
                lines.append(f"- {m['ref']} [{mark}] {m['name']}: {m['mechanism']}")
        if self.conflicts:
            lines.append("\n# 证据冲突 (同一命题上既有支持又有反对)")
            for c in self.conflicts:
                lines.append(f"- {c['claim_ref']}: 支持 {c['supports']} 条 / 反对 {c['contradicts']} 条"
                             f" → 需可区分检验")
        if self.gaps:
            lines.append("\n# 当前缺口 (按紧迫度)")
            for g in self.gaps:
                acts = ",".join(g.get("resolving_actions") or [])
                lines.append(f"- [{g['gap_type']}] {g['target_ref']}: {g['statement']} → 可用动作 {acts}")
        if self.evidence:
            lines.append("\n# 可用证据 (仅导航, 须 read 回原文)")
            for e in self.evidence:
                lines.append(f"- {e['ref']} [{e['support']}] {e['title']} @ {e['locator']}")
        if self.routes:
            lines.append("\n# 研究路线")
            for r in self.routes:
                lines.append(f"- {r['ref']} [{r['status']}] 策略={r['strategy']} 失败原因={r['failure_reason']}")
        if self.failures:
            lines.append("\n# 失败档案 (不得重复同一失败方法)")
            for f in self.failures:
                tag = "科学性失败" if f["scientific"] else "运行/能力问题"
                lines.append(f"- {f['claim_id']} {tag}/{f['kind']}: {f['reason']} (恢复条件: {f['recovery_condition'] or '无'})")
        if self.available_actions:
            lines.append("\n# 当前可派发动作 (只能从这里选)")
            lines.append("- " + ", ".join(self.available_actions))
        lines.append("\n# 预算")
        lines.append(f"- 剩余动作 {self.budget.get('actions', 0)}, 剩余工具调用 {self.budget.get('tool_calls', 0)}")
        if self.permissions:
            lines.append("\n# 权限/范围约束")
            for k, v in self.permissions.items():
                lines.append(f"- {k}: {v}")
        text = "\n".join(lines)
        if len(text) > budget_chars:
            text = text[:budget_chars] + "\n...(上下文已截断, 需要细节时请用 read_source 取原文)"
        return text

    def as_state(self) -> dict:
        return {
            "goal": self.goal, "gaps": self.gaps, "claims": self.claims,
            "obligations": self.obligations, "evidence": self.evidence,
            "failures": self.failures, "routes": self.routes, "budget": self.budget,
            "models": self.models, "conflicts": self.conflicts,
            "available_actions": self.available_actions,
        }


def _ref(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    version = getattr(obj, "version", None)
    obj_id = getattr(obj, "id", "")
    return f"{obj_id}@v{version}" if version is not None else str(obj_id)


def build_context_pack(
    goal: str,
    claims: list[Claim],
    obligations: list[ProofObligation],
    evidence: list[SourceEvidence],
    gaps: list[ResearchGap],
    routes: list[ResearchRoute],
    failures: list[dict] | None = None,
    budget: dict | None = None,
    permissions: dict | None = None,
    max_items: int = 12,
    models: list | None = None,
    available_actions: list[str] | None = None,
) -> ContextPack:
    pack = ContextPack(
        goal=goal,
        budget=budget or {},
        permissions=permissions or {},
        failures=list(failures or [])[:max_items],
        available_actions=list(available_actions or []),
    )
    pack.gaps = [
        {"gap_type": g.gap_type.value, "target_ref": _ref(g.target_ref),
         "statement": g.statement, "resolving_actions": list(g.resolving_actions)}
        for g in gaps[:max_items]
    ]
    pack.claims = [
        {"ref": _ref(c), "status": c.status.value, "statement": (c.statement or "")[:160],
         "support_kind": c.support_kind.value, "coverage": c.coverage.value}
        for c in claims[:max_items]
    ]
    pack.obligations = [
        {"ref": _ref(o), "kind": o.kind, "status": o.status.value,
         "statement": (o.statement or "")[:160]}
        for o in obligations[:max_items]
    ]
    pack.evidence = [
        {"ref": _ref(e), "support": e.support.value, "title": (e.title or "")[:80],
         "locator": e.location}
        for e in evidence[:max_items]
    ]
    pack.routes = [
        {"ref": _ref(r), "status": r.status.value, "strategy": r.strategy,
         "failure_reason": r.failure_reason}
        for r in routes[:max_items]
    ]
    # 候选模型 (计划书 §3 R1): 提议需要知道"现在有哪些模型可选/已选"
    pack.models = [
        {"ref": _ref(m), "name": (getattr(m, "name", "") or "")[:80],
         "mechanism": (getattr(m, "mechanism", "") or "")[:120],
         "selected": bool(getattr(m, "selected", False))}
        for m in (models or [])[:max_items]
    ]
    # 证据冲突: 同一命题上同时存在支持与反对 → 需要可区分的检验
    by_claim: dict[str, dict[str, int]] = {}
    for item in evidence:
        if not item.claim_id:
            continue
        bucket = by_claim.setdefault(item.claim_id, {"supports": 0, "contradicts": 0})
        if item.support.value in ("supports", "partially_supports"):
            bucket["supports"] += 1
        elif item.support.value == "contradicts":
            bucket["contradicts"] += 1
    pack.conflicts = [
        {"claim_ref": claim_id, "supports": counts["supports"],
         "contradicts": counts["contradicts"]}
        for claim_id, counts in by_claim.items()
        if counts["supports"] and counts["contradicts"]
    ][:max_items]
    return pack
