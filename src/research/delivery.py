from __future__ import annotations

"""交付评估 (合并计划 §3.2 G12): 区分"依赖已终止"与"验收已通过"。

这一层解决的问题 (G12 的原文)
----------------------------
"角色跑过仍被当成交付依据": `_missing_roles()` 把 partial 也算满足, `TeamSession.export()`
未传完整研究/出版门槛, `TeamApp.stream()` 吞导出异常 —— 于是"某个角色跑过一次"就等于
"可以交付", 而且交付包是不是完整论文全靠运气。

因此这里做两件事, 且只有这两件:

1. **验收判定**: 用与研究侧同一套门槛 (`acceptance.theory_validity_gate` /
   `delivery_gate` / `publication_gate`) 判断当前产出属于哪一级交付
   (`classify_deliverable`), 并给出**具体**的阻塞理由与未决项 —— 不看"谁跑过",
   只看"有没有结论、结论有没有依据、正文有没有如实表达、出版是否完备";
2. **不升级**: 门槛没过一律降级 (完整论文 > 论文草稿 > 条件性研究报告 > 研究备忘录),
   绝不允许把未过门槛的产出写成"完整论文"。

判定结果进入运行结果与交付包 manifest, 界面与人工复核据此看到"为什么是这一级"。
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from src.research.acceptance import (
    GateResult,
    classify_deliverable,
    delivery_gate,
    publication_gate,
    theory_validity_gate,
)
from src.research.snapshot import snapshot_from_store

__all__ = ["DeliveryAssessment", "MANUSCRIPT_MIN_CHARS", "assess_delivery"]

#: 正文短于这个长度就不算"研究稿"(与 `delivery_gate` 的判据同源)。
MANUSCRIPT_MIN_CHARS = 200


@dataclass
class DeliveryAssessment:
    """一次交付评估的完整结论 (等级 + 两道门槛 + 具体理由)。"""

    level: str = "研究备忘录"
    accepted: bool = False
    #: 研究有效性 / 论文表达 / 出版完备
    theory: GateResult | None = None
    delivery: GateResult | None = None
    publication: GateResult | None = None
    #: 阻塞性理由 (有它就不能称为完整交付)
    blocking: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from src.research.package import gate_manifest_fields
        return {
            "level": self.level, "accepted": self.accepted,
            "blocking": list(self.blocking), "unresolved": list(self.unresolved),
            "counts": dict(self.counts), "notes": list(self.notes),
            "theory_passed": bool(self.theory and self.theory.passed),
            "delivery_passed": bool(self.delivery and self.delivery.passed),
            "publication_passed": bool(self.publication and self.publication.passed),
            **gate_manifest_fields(self.theory, self.delivery, self.publication),
        }

    def describe(self) -> str:
        lines = [f"交付等级: {self.level}"]
        for reason in self.blocking:
            lines.append(f"- 阻塞: {reason}")
        for item in self.unresolved:
            lines.append(f"- 未决: {item}")
        return "\n".join(lines)


def assess_delivery(store: Any, *, project_id: str, problem_id: str, run_id: str,
                    manuscript_md: str = "", references: Iterable[dict[str, Any]] = (),
                    compile_status: str = "", blocks: list[Any] | None = None,
                    latex_source: str = "",
                    checklist: dict[str, Any] | None = None,
                    on_skip=None) -> DeliveryAssessment:
    """按三道门槛评估当前产出, 返回等级与理由 (不写任何状态)。"""
    snapshot = snapshot_from_store(store, project_id=project_id, problem_id=problem_id,
                                   run_id=run_id, on_skip=on_skip)
    from src.research.snapshot import latest_manuscript_row
    from src.publication.schemas import Manuscript
    from src.publication.references import cited_reference_rows
    if blocks is None:
        row = latest_manuscript_row(store, problem_id=problem_id)
        payload = row.get("manuscript") if isinstance(row.get("manuscript"), dict) else row
        if payload:
            draft = Manuscript.model_validate({k: v for k, v in payload.items() if not k.startswith("_")})
            blocks = draft.all_blocks()
            references = cited_reference_rows(draft, [source.model_dump(mode="json") for source in snapshot.evidence])
    theory = theory_validity_gate(
        snapshot.claims, snapshot.obligations, snapshot.verifications,
        snapshot.dependency_edges, snapshot=snapshot)
    delivery = delivery_gate(manuscript_md, snapshot, theory_gate=theory)
    publication = publication_gate(
        manuscript_md, snapshot, references=list(references),
        compile_status=compile_status, blocks=blocks, checklist=checklist,
        latex_source=latex_source)
    level = classify_deliverable(theory, delivery, publication)
    assessment = DeliveryAssessment(
        level=level,
        accepted=bool(theory.passed and delivery.passed and publication.passed),
        theory=theory, delivery=delivery, publication=publication,
        blocking=[*theory.reasons, *delivery.reasons, *publication.reasons],
        unresolved=[*theory.unresolved, *delivery.unresolved,
                    *publication.unresolved],
        counts={
            "claims": len(snapshot.claims),
            "obligations": len(snapshot.obligations),
            "verifications": len(snapshot.verifications),
            "evidence": len(snapshot.evidence),
            "manuscript_chars": len(manuscript_md or ""),
        })
    if not (manuscript_md or "").strip():
        assessment.notes.append("没有正文: 只能作为研究备忘录导出")
    if assessment.unresolved:
        assessment.notes.append(
            f"{len(assessment.unresolved)} 项未决: 交付物必须如实标注未决项, "
            "不得表述为已完成的结论")
    return assessment
