from __future__ import annotations

"""从快照派生新问题 (计划书 §9.1 的第三种操作; 合并计划 §5.5 的抽取项)。

`fork` 与 `resume` 的区别: `resume` 继续**同一**问题的未完成动作; `fork` 新建问题,
只把指定结论作为**待重验前提**导入, **不复制**任何验证记录 —— 旧验证绑定的是旧命题
版本, 直接复用等于静默继承结论。

为什么把它从引擎里抽出来
------------------------
这段逻辑其实只做三类事: 读快照、按新身份复制命题并**重置状态**、在**同一步**里
创建新问题的规格与血缘记录。它需要的是"存储 + 输入规格", 不需要研究引擎的任何执行
能力。留在引擎里意味着: 派生接口 (HTTP) 必须建一个引擎, 而引擎退役时派生能力会跟着
消失 —— 派生是**用户可见功能**, 不该被绑在实现细节上。

血缘必须显式记录 (导入哪些、哪些必须重算、复用了什么), 否则"这个新问题是从哪来的、
哪些结论是继承的"事后无法回答。
"""

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
