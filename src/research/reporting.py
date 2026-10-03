from __future__ import annotations

"""工作台只读投影与交付引用 (计划书 §4)。

计划书对 `reporting.py` 的要求是"输入明确的 `project/problem/snapshot`, 输出稳定的
工作台只读投影和交付引用; 前端不需要理解研究库有多少张表, 服务端的多个入口也不应
各自用'取第一个规格'的代码拼对象"。

本模块就是那层投影, 只做三件事:

1. **把研究对象投影成稳定字段** (`claims_payload` / `obligations_payload` / ...):
   字段名与前端读取的键一一对应, 由本模块统一定义, 页面不得自行推算;
2. **给出统计计数的单一来源** (`objects_counts`, F0-3): 缺失字段在前端会显示成 0,
   因此计数必须一次算清、口径在此固定;
3. **说明"知道了什么、还缺什么"** (`coverage_notes`): 未覆盖的现实因素、未关闭义务、
   尚未检索/未比较等, 由服务端显式声明, 而不是让前端从空数组里猜。

硬约束:
- 只读: 不写库、不改任何结论状态、不触发研究动作;
- **不猜归属**: 调用方已经完成问题级过滤 (见 `server._research_state` 的 `_owned`),
  本模块不再按题目去"推断"某对象属于哪个问题;
- 字段一旦出现在这里就是**契约**: 改名/删字段必须同时改前端与契约测试。
"""

from typing import Any

# 前端读取的工作台统计字段 (F0-3): 缺失会被显示成 0, 因此必须全部给出
COUNT_KEYS = (
    "claims", "claims_supported", "claims_refuted", "claims_open", "claims_blocked",
    "obligations", "obligations_open", "obligations_blocked", "obligations_closed",
    "evidence", "verifications", "models", "routes",
)

# 预算/用量的字段 (工作台顶部提示条读取)
BUDGET_KEYS = ("actions_used", "tool_calls_used", "max_actions", "max_tool_calls", "done")


def _status(item: Any) -> str:
    value = getattr(item, "status", "")
    return getattr(value, "value", value) or ""


def objects_counts(claims, obligations, evidence, verifications,
                   models=None, routes=None) -> dict:
    """工作台统计字段的**单一来源** (F0-3)。

    计数口径写在这里: 前端不得再自行推算, 也不得用缺失字段兜成 0。
    """
    return {
        "claims": len(claims),
        "claims_supported": len([c for c in claims if _status(c) == "supported"]),
        "claims_refuted": len([c for c in claims if _status(c) == "refuted"]),
        "claims_open": len([c for c in claims if _status(c) in ("proposed", "in_progress")]),
        "claims_blocked": len([c for c in claims if _status(c) == "blocked"]),
        "obligations": len(obligations),
        "obligations_open": len([o for o in obligations if _status(o) == "open"]),
        "obligations_blocked": len([o for o in obligations if _status(o) == "blocked"]),
        "obligations_closed": len([o for o in obligations if _status(o) == "closed"]),
        "evidence": len(evidence),
        "verifications": len(verifications),
        "models": len(models or []),
        "routes": len(routes or []),
    }


def uncovered_factors(claim) -> list[str]:
    """结论明确**未覆盖**的现实因素 (§4.3-4: 不得写成无条件成立)。"""
    gaps: list[str] = []
    study = claim.study
    if claim.claim_type.value == "causal":
        if not study.identification_assumptions:
            gaps.append("识别假设未列出")
        if not study.missing_data_handling.strip():
            gaps.append("缺失数据机制未说明")
        if not study.error_structure.strip():
            gaps.append("误差结构未说明")
    if study.data_source_kind in ("placeholder", "synthetic"):
        gaps.append(f"数据来源为 {study.data_source_kind}, 非现实数据")
    if claim.model_ref is not None:
        gaps.append("结论仅在所声明模型下成立")
    return gaps


def claims_payload(claims, problem_id: str = "") -> list[dict]:
    """结论投影: 每条都带状态、支持方式、覆盖范围、未覆盖因素。"""
    return [
        {
            "id": c.id, "statement": c.statement,
            "problem_id": str(getattr(c, "problem_id", "") or problem_id),
            "status": c.status.value, "support_kind": c.support_kind.value,
            "coverage": c.coverage.value, "validation_status": c.validation_status.value,
            "assurance": c.assurance.value, "novelty_status": c.novelty_status.value,
            "evidence_grade": c.evidence_grade.value,
            "claim_type": c.claim_type.value,
            "model_ref": ({"id": c.model_ref.id, "version": c.model_ref.version}
                          if c.model_ref else None),
            "conditions": [f"{v} ∈ {d}" for v, d in (c.variable_domains or {}).items()],
            "effect_estimate": c.effect_estimate or {},
            "study_design": c.study.design.value,
            "not_covered": uncovered_factors(c),
            "notes": c.notes,
        }
        for c in claims
    ]


def obligations_payload(obligations) -> list[dict]:
    return [
        {
            "id": o.id, "statement": o.statement, "kind": o.kind,
            "status": o.status.value, "validation_status": o.validation_status.value,
            "claim_id": o.claim_id, "required": o.required,
            "detail": o.detail, "counterexample": o.counterexample or {},
        }
        for o in obligations
    ]


def verifications_payload(verifications) -> list[dict]:
    return [
        {
            "id": v.id, "claim_id": v.claim_id, "tool": v.tool,
            "status": v.status, "validation_status": v.validation_status.value,
            "scope": v.scope.value, "stale": v.stale,
            "certificate": v.certificate,
            "counterexample": v.counterexample or {},
        }
        for v in verifications
    ]


def evidence_payload(evidence) -> list[dict]:
    """证据投影: 保留可回查定位与支持关系, 摘要截断但不改写。"""
    return [
        {
            "id": e.id, "title": e.title, "source_id": e.source_id,
            "locator": e.location, "page": e.page,
            "support": e.support.value, "support_reason": e.support_reason,
            "reviewer": e.reviewer, "source_kind": e.source_kind.value,
            "credibility": e.credibility.value,
            "existence_verified": e.existence_verified,
            "excerpt": (e.excerpt or "")[:400],
            # P1-4: 含视觉异常片段的来源在工作台标出 (需核对, 不作为强证据)
            "notes": e.notes,
            "needs_review": "需核对" in (e.notes or ""),
        }
        for e in evidence
    ]


def routes_payload(routes) -> list[dict]:
    return [
        {
            "id": r.id, "strategy": r.strategy, "status": r.status.value,
            "goal": r.goal, "failure_reason": r.failure_reason,
            "recovery_condition": r.recovery_condition, "attempts": r.attempts,
        }
        for r in routes
    ]


def experiments_payload(specs, *, foreign_claim_ids: set[str] | None = None) -> list[dict]:
    """实验/仿真建议投影: 明确"建议/未执行"的状态字段必须原样带出 (R5)。"""
    foreign = foreign_claim_ids or set()
    return [
        {
            "id": s.get("id"), "title": s.get("title"),
            "claim_id": s.get("claim_id"),
            "purpose": s.get("purpose"),
            "execution_status": s.get("execution_status"),
            "decision_rule": s.get("decision_rule"),
            "metrics": s.get("metrics") or [],
            "limitations": s.get("limitations") or [],
        }
        for s in specs
        if str(s.get("claim_id", "")) not in foreign
    ]


def novelty_payload(records) -> list[dict]:
    return [
        {
            "claim_id": n.get("claim_id"), "status": n.get("status"),
            "conclusion": n.get("conclusion"),
            "covered_sources": n.get("covered_sources") or [],
            "inaccessible": n.get("inaccessible") or [],
        }
        for n in records
    ]


def steps_payload(attempts, *, owned) -> list[dict]:
    """推导步骤投影 (F1-5: 反馈的对象选择器需要可作用的步骤清单)。"""
    return [
        {"id": f"{a.get('target_claim_id', '')}:{s.get('index')}",
         "text": f"{s.get('index')}:{s.get('statement', '')}",
         "claim_id": a.get("target_claim_id", "")}
        for a in attempts
        for s in (a.get("steps") or [])
        if owned(a.get("target_claim_id", ""))
    ]


def assumptions_payload(assumptions) -> list[dict]:
    return [{"id": a.id, "statement": a.statement, "accepted": bool(a.accepted)}
            for a in assumptions]


def gaps_payload(gaps) -> list[dict]:
    return [{"gap_type": g.gap_type.value, "statement": g.statement,
             "resolving_actions": g.resolving_actions}
            for g in gaps]


def budget_payload(engine) -> dict:
    """预算/用量投影: 只读引擎计数, 不重算。"""
    return {
        "actions_used": engine._actions,
        "tool_calls_used": engine._tool_calls,
        "max_actions": engine.budget.max_actions,
        "max_tool_calls": engine.budget.max_tool_calls,
        "done": engine.done,
    }


def coverage_notes(counts: dict, *, metrics: dict | None = None,
                   novelty: list[dict] | None = None) -> list[str]:
    """把"还缺什么"变成显式说明 (而不是让前端从空数组里猜)。

    这些说明不影响任何结论状态; 它们只解释界面上的 0 是"确实没有"还是"尚未做"。
    """
    notes: list[str] = []
    if counts.get("claims", 0) == 0:
        notes.append("该项目下该问题尚无结论 (研究可能尚未形式化)")
    if counts.get("obligations_open", 0):
        notes.append(f"仍有 {counts['obligations_open']} 条义务未关闭")
    if counts.get("obligations_blocked", 0):
        notes.append(f"有 {counts['obligations_blocked']} 条义务受阻 (等待条件或人工确认)")
    if counts.get("evidence", 0) == 0:
        notes.append("尚无归属到本问题的证据")
    if counts.get("verifications", 0) == 0:
        notes.append("尚无验证记录")
    if not novelty:
        notes.append("尚未开展新颖性对照; 不得据此宣称原创")
    if metrics is not None:
        anomalies = metrics.get("anomalies") or []
        if anomalies:
            notes.append(f"日志契约异常 {len(anomalies)} 条 (见 log_anomalies)")
        if metrics.get("stopped_reason"):
            notes.append(f"研究因预算停止: {metrics['stopped_reason']}")
    return notes


def workbench_projection(*, project_id: str, problem_id: str, claims,
                         obligations, verifications, evidence, routes, models,
                         engine, store, problems: list[dict], spec,
                         experiment_specs, novelty_records, attempts,
                         foreign_claim_ids: set[str] | None = None) -> dict:
    """组装工作台只读投影。

    调用方 (`server._research_state`) 负责: 打开库、解析问题、按问题过滤对象。
    本函数只负责**投影与计数**, 因此可以对任意输入单独测试。
    """
    counts = objects_counts(claims, obligations, evidence, verifications,
                            models=models, routes=routes)
    metrics = engine.metrics()
    novelty = novelty_payload(novelty_records)
    return {
        "project_id": project_id,
        "problem_id": problem_id,
        # R6: 统一身份 (project/problem/run/branch) —— 前端与产物清单读取同一组值
        "run_id": engine.run_id,
        "branch_id": engine.branch_id,
        "snapshots": store.list_snapshots(problem_id=problem_id),
        "problems": problems,
        "spec": {
            "problem_statement": spec.problem_statement,
            "original_request": spec.original_request,
            "direction": spec.direction,
            "questions": [q.model_dump(mode="json") for q in spec.questions],
            "candidates": [c.model_dump(mode="json") for c in spec.candidates],
            "confirmed": spec.confirmed,
            "selected_candidate_id": spec.selected_candidate_id,
            "variable_domains": spec.variable_domains,
            "unknown_fields": spec.unknown_fields,
            # P0-2: 问题契约 (研究类型、允许的方法、研究路径、澄清问题)
            "contract": (spec.contract.model_dump(mode="json")
                         if spec.contract is not None else None),
            # P0-1/§1 契约第 2 行: 授权策略与检索覆盖记录 (用了哪些检索式/库, 缺什么)
            "source_policy": spec.source_policy.value,
            "coverage": (spec.coverage.model_dump(mode="json")
                         if spec.coverage is not None else None),
        },
        "claims": claims_payload(claims, problem_id),
        "obligations": obligations_payload(obligations),
        "verifications": verifications_payload(verifications),
        # 计划书 §5.2 / §7.2: 领域模型选中情况与问题类型能力声明
        "model_selection": engine.model_selection(),
        # R2: 候选机制的完整比较 (候选、舍弃理由、可区分检验、术语与量纲)
        "modeling": next((engine.model_comparison(c.id) for c in claims
                          if engine.model_comparison(c.id)), {}),
        "assumptions": assumptions_payload(engine._assumptions()),
        "steps": steps_payload(attempts, owned=lambda cid: True),
        "evidence": evidence_payload(evidence),
        "experiments": experiments_payload(experiment_specs,
                                           foreign_claim_ids=foreign_claim_ids),
        "routes": routes_payload(routes),
        "novelty": novelty,
        "decisions": engine.decisions[-30:],
        "events": engine.event_digest(limit=40),
        # 统一日志键的监控指标: 只读聚合, 不触发任何研究动作
        "metrics": metrics,
        "log_anomalies": store.log_anomalies(limit=10),
        "gaps": gaps_payload(engine._gaps(claims, obligations)),
        "budget": budget_payload(engine),
        "objects": counts,
        # 把"还缺什么"显式说出来, 而不是让前端从空数组猜
        "coverage_notes": coverage_notes(counts, metrics=metrics, novelty=novelty),
    }


__all__ = [
    "BUDGET_KEYS",
    "COUNT_KEYS",
    "assumptions_payload",
    "budget_payload",
    "claims_payload",
    "coverage_notes",
    "evidence_payload",
    "experiments_payload",
    "gaps_payload",
    "novelty_payload",
    "objects_counts",
    "obligations_payload",
    "routes_payload",
    "steps_payload",
    "uncovered_factors",
    "verifications_payload",
    "workbench_projection",
]
