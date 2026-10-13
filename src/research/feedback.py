from __future__ import annotations

"""用户反馈 → 对象级修订。"""

from typing import Any

from src.agents.protocol import NeedKind, ResearchNeed
from src.research.schemas import ObjectRef

__all__ = [
    "FEEDBACK_ROLE_BY_KIND",
    "find_latest_team_run",
    "feedback_need_for",
    "target_candidates",
]

#: 对象种类 → 承接反馈的角色。未登记的种类**拒绝**而不是猜 (猜错就是改错对象)。
FEEDBACK_ROLE_BY_KIND: dict[str, str] = {
    "claim": "reasoning",
    "obligation": "reasoning",
    "verification": "reasoning",
    "gap": "reasoning",
    "evidence": "evidence",
    "source": "evidence",
    "card": "evidence",
    "case": "evidence",
    "dataset": "evidence",
    "evidence_link": "evidence",
    "model": "modeling",
    "assumption": "modeling",
    "definition": "modeling",
    "validation_plan": "validation",
    "manuscript": "writing",
    "block": "writing",
    "figure": "figures",
    "review_issue": "review",
    "revision_task": "review",
}

#: 修复类反馈的对象种类 → 需求种类 (决定主控怎么派工)。
_NEED_KIND_BY_ROLE: dict[str, NeedKind] = {
    "writing": NeedKind.manuscript_revision,
    "evidence": NeedKind.more_sources,
    "modeling": NeedKind.model_condition,
    "reasoning": NeedKind.derivation,
    "validation": NeedKind.empirical_support,
    "figures": NeedKind.figure_data,
    "review": NeedKind.review,
}

# 无具体对象的人工干预必须由用户显式选作用范围，不能从任意自然语言猜。
INTERVENTION_ROLE_BY_SCOPE = {
    "sources": "evidence",
    "method": "modeling",
    "writing": "writing",
    "review": "review",
    "validation": "validation",
}


def find_latest_team_run(store: Any, problem_id: str) -> dict[str, Any]:
    """该问题**最近一次**团队运行状态 (`{}` 表示没有 —— 不猜、不新建)。"""
    best: dict[str, Any] = {}
    try:
        rows = store.list_latest("team_run") or []
    except Exception:  # noqa: BLE001 - 读不出来就当作"没有团队运行"
        return {}
    for row in rows:
        if str(row.get("problem_id", "")) != problem_id:
            continue
        if not best or str(row.get("saved_at", "")) >= str(best.get("saved_at", "")):
            best = row
    return best


def target_candidates(store: Any, problem_id: str, *, limit: int = 12) -> list[dict]:
    """可能的反馈对象 (供用户确认; 只列**属于本问题**的对象)。"""
    out: list[dict] = []
    plan = (("claim", "claim"), ("obligation", "obligation"),
            ("evidence", "evidence"), ("model", "model"),
            ("manuscript", "manuscript"), ("validation_plan", "validation_plan"),
            ("review_issue", "review_issue"))
    for kind, label in plan:
        try:
            rows = store.list_latest(kind) or []
        except Exception:  # noqa: BLE001
            continue
        for row in rows:
            owner = str(row.get("problem_id", "") or row.get("_scope", {}).get("problem_id", ""))
            if owner and owner != problem_id:
                continue
            if not owner and len({str(r.get("problem_id", ""))
                                  for r in rows if r.get("problem_id")}) > 1:
                continue
            text = (row.get("statement") or row.get("title") or row.get("summary")
                    or row.get("name") or "")
            out.append({"object_id": str(row.get("id", "")), "kind": label,
                        "version": int(row.get("version", 1) or 1),
                        "text": str(text)[:160]})
            if len(out) >= limit:
                return out
    return out


def feedback_need_for(*, project_id: str, problem_id: str, text: str,
                      target_object_id: str = "",
                      store: Any = None,
                      scope: str = "object") -> tuple[ResearchNeed | None, dict[str, Any]]:
    """反馈 → 一条可派工的需求; 无法定位对象时返回澄清信息 (不猜)。

    返回 `(need, info)`: `need` 为 `None` 时 `info` 里给出 `needs_clarification` 与
    候选对象列表, 调用方据此**问用户**, 而不是替用户选一个对象。
    """
    body = str(text or "").strip()
    if not body:
        return None, {"ok": False, "reason": "反馈内容为空"}
    if scope == "question":
        return None, {"ok": False, "needs_clarification": True,
                      "question": "改变原研究问题请从已有快照派生新问题，避免覆盖原问题的契约与结论。",
                      "reason": "研究问题身份不可在同一次运行中改写"}
    if scope != "object":
        role = INTERVENTION_ROLE_BY_SCOPE.get(scope)
        if role is None:
            return None, {"ok": False, "reason": f"未知的人工调整范围: {scope}"}
        if target_object_id:
            return None, {"ok": False, "reason": "范围调整与对象修订不能同时指定"}
        need = ResearchNeed(
            kind=_NEED_KIND_BY_ROLE[role],
            statement=f"按用户意见调整{scope}工作: {body[:400]}",
            why=f"用户在研究过程中调整{scope}工作: {body[:200]}",
            acceptance=["明确说明执行了什么调整及其依据",
                        "如无法执行，说明限制并保留原结果"],
            owner=role, blocking=False,
            hints={"feedback": body[:400], "intervention_scope": scope},
        )
        return need, {"ok": True, "scope": scope, "owner": role}
    if not target_object_id:
        candidates = target_candidates(store, problem_id) if store is not None else []
        return None, {
            "ok": False, "needs_clarification": True,
            "question": "这条意见针对哪个对象? 请指定对象 id (下面是本问题的候选)",
            "candidates": candidates,
            "reason": "反馈必须落到具体对象, 无法确定时不猜",
        }
    found = _locate(store, target_object_id)
    if found is None:
        return None, {
            "ok": False, "needs_clarification": True,
            # 措辞覆盖两种可能: "根本没这个 id" 与 "它属于别的问题" 对用户是同一件事
            # (要改的对象不在当前问题里), 因此都明确说出"不在当前研究问题内"。
            "question": (f"对象 {target_object_id} 不存在, 或不在当前研究问题 "
                         f"{problem_id} 内, 请确认"),
            "reason": "对象不在当前研究问题内",
            "candidates": target_candidates(store, problem_id) if store is not None else [],
        }
    kind, row = found
    # **必须限定在当前研究问题内**: 同一个项目里另一个问题的对象也存在于同一个库里,
    # 只看"对象存在"就会把 A 问题的结论当成 B 问题的对象改掉。旧引擎有这条判据,
    # 抽取时漏掉过一次 (被 e2e 的"对象不属于本问题"用例抓到)。
    owner = str(row.get("problem_id", "") or (row.get("_scope") or {}).get("problem_id", ""))
    if owner and owner != problem_id:
        return None, {
            "ok": False, "needs_clarification": True,
            "question": (f"对象 {target_object_id} 属于问题 {owner}, "
                         f"不在当前研究问题 {problem_id} 内, 请确认要改哪一个"),
            "reason": "对象不在当前研究问题内",
            "owner_problem_id": owner,
            "candidates": target_candidates(store, problem_id) if store is not None else [],
        }
    role = FEEDBACK_ROLE_BY_KIND.get(kind)
    if role is None:
        return None, {"ok": False, "reason": f"该类对象 ({kind}) 没有承接反馈的角色"}
    need = ResearchNeed(
        kind=_NEED_KIND_BY_ROLE.get(role, NeedKind.clause),
        statement=f"按用户意见修订 {kind} {target_object_id}: {body[:400]}",
        why=f"用户对 {target_object_id} 提出意见: {body[:200]}",
        acceptance=["修订后的对象版本可查, 且与原意见逐条对应",
                    "若意见无法执行, 明确说明原因而不是静默忽略"],
        owner=role,
        blocking=False,
        blocked_refs=[ObjectRef(id=target_object_id,
                                version=int(row.get("version", 1) or 1))],
        hints={"feedback": body[:400], "target_kind": kind,
               "target_object_id": target_object_id},
    )
    return need, {"ok": True, "target_kind": kind, "owner": role,
                  "target_object_id": target_object_id}


def _locate(store: Any, object_id: str) -> tuple[str, dict] | None:
    """在已知种类里找该对象 (找到唯一一个才返回)。

    用 `strip_meta=False` 取行: 反馈必须能判断对象**属于哪个研究问题**, 而归属记在
    `problem_id`/`_scope` 里 (默认读取会把 `_` 前缀的元数据剥掉)。
    """
    if store is None or not object_id:
        return None
    for kind in ("claim", "obligation", "verification", "evidence", "model",
                 "assumption", "definition", "manuscript", "figure",
                 "validation_plan", "review_issue"):
        try:
            row = store.get(kind, object_id, strip_meta=False)
        except Exception:  # noqa: BLE001
            continue
        if row:
            return kind, row
    return None
