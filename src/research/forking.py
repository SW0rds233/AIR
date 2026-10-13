from __future__ import annotations

"""从快照派生新问题。"""

from typing import Any, Callable

from src.research.schemas import (
    Assurance,
    ClaimStatus,
    Coverage,
    NoveltyStatus,
    ResearchSpec,
    SupportKind,
    ValidationStatus,
    new_id,
    utcnow,
)
from src.research.store import KIND_CLAIM, KIND_SPEC, ResearchStore

__all__ = ["fork_from_snapshot"]


def fork_from_snapshot(store: ResearchStore, spec: ResearchSpec, *,
                       source_snapshot_id: str = "",
                       claim_ids: list[str] | None = None,
                       new_problem_id: str = "",
                       on_claim: Callable[[Any], list[tuple[str, str, dict]]] | None = None,
                       ) -> dict[str, Any]:
    """从既有冻结快照派生一个新问题。

    `on_claim(forked_claim)` 可选: 调用方为每条派生命题追加自己的写入 (旧引擎用它登记
    研究路线)。团队路径不传 —— 路线是旧引擎的策略记账, 团队由主控派工。
    返回 `{ok, lineage, imported_claims, new_problem_id, spec_created, note}`。
    """
    source = store.load_snapshot(source_snapshot_id or None)
    if source is None:
        return {"ok": False, "reason": "未找到可派生的快照"}
    selected = [c for c in source.claims if (not claim_ids or c.id in claim_ids)]
    if not selected:
        return {"ok": False, "reason": "快照中没有可导入的结论"}

    new_problem = new_problem_id or new_id("prob")
    imported: list[str] = []
    writes: list[tuple[str, str, dict]] = []
    for claim in selected:
        forked = claim.model_copy(update={
            "id": new_id("clm"), "version": 1,
            "problem_id": new_problem,   # R6: 派生命题归属**新**问题
            "status": ClaimStatus.proposed,
            "assurance": Assurance.unverified,
            "support_kind": SupportKind.none,
            "coverage": Coverage.step,
            "validation_status": ValidationStatus.unchecked,
            "novelty_status": NoveltyStatus.unchecked,
            "verification_scope": None,
            "verification_closure": {},
            "obligations": [],
            "dependencies": list(claim.dependencies),
            "notes": (claim.notes + f" 由快照 {source.snapshot_id} 的 {claim.id} 派生导入, "
                                    "其前提与条件需在本问题下重新检查").strip(),
        })
        writes.append((KIND_CLAIM, forked.id, forked.model_dump(mode="json")))
        imported.append(forked.id)
        if on_claim is not None:
            writes.extend(on_claim(forked) or [])

    # F0-5: 派生必须同时创建**新问题的规格**。只导入命题而不建规格时, 新问题在存储里
    # 没有身份, 工作台按 problem_id 查不到 (404), 也无法续研。
    forked_spec = spec.model_copy(update={
        "problem_id": new_problem,
        "problem_statement": spec.problem_statement,
        "original_request": (spec.original_request
                             or spec.problem_statement or spec.direction),
        "questions": [],          # 新问题尚未确认研究路线: 由用户/研究者重新确认
        "candidates": [],
        "confirmed": True,        # 命题已从快照导入, 可直接进入研究循环
        "selected_candidate_id": "",
        "unknown_fields": [],
    })
    writes.append((KIND_SPEC, forked_spec.problem_id,
                   forked_spec.model_dump(mode="json")))

    lineage = {
        "source_project_id": source.project_id,
        "source_snapshot_id": source.snapshot_id,
        "source_problem_id": spec.problem_id,
        "new_problem_id": new_problem,
        "imported_claims": imported,
        "reused_verifications": [],   # 明确不复用: 必须重算
        "must_recompute": imported,
        "created_at": utcnow(),
    }
    store.submit_step(
        writes=writes,
        events=[("forked_from_snapshot", lineage)],
        idempotency_key=f"fork:{source.snapshot_id}:{new_problem}",
    )
    return {"ok": True, "lineage": lineage, "imported_claims": imported,
            "new_problem_id": new_problem, "problem_id": new_problem,
            "spec_created": True,
            "note": (f"已从快照 {source.snapshot_id} 派生 {len(imported)} 条结论作为"
                     f"待重验命题 (新问题 {new_problem})")}
