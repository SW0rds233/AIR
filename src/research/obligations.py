from __future__ import annotations

"""义务的排序口径。"""

from typing import Iterable, TypeVar

from src.research.schemas import ProofObligation

__all__ = [
    "OBLIGATION_PRIORITY",
    "DEFAULT_PRIORITY",
    "priority_of",
    "sort_by_priority",
    "obligation_kind",
    "obligation_claim",
]

#: 义务种类 → 优先级 (数值越小越先做)。
OBLIGATION_PRIORITY: dict[str, int] = {
    "estimate_effect": 0, "prove_inequality": 0, "prove_monotonicity": 0,
    "prove_identity": 0, "check_implication": 0, "equality_condition": 1,
    "control_confound": 2, "scope_check": 3, "evidence_support": 4,
    # 设计/计数类存在性判定: 与计数关系同层, 先于一般规则型义务
    "design_necessity": 2,
    # 因果识别类声明必须在效应估计之后再评估
    "identification_assumptions": 4, "design_feasibility": 4,
    "predictive_validation": 4, "scenario_parameters": 4,
    "measurement_and_missing": 4, "error_structure": 4,
}

#: 未登记种类的默认优先级 (排在已登记之后, 但仍会被调度)。
DEFAULT_PRIORITY = 5

T = TypeVar("T", bound=ProofObligation)


def priority_of(obligation: ProofObligation) -> int:
    """单条义务的优先级。"""
    kind = getattr(obligation, "kind", "") or ""
    return OBLIGATION_PRIORITY.get(str(kind), DEFAULT_PRIORITY)


def sort_by_priority(obligations: Iterable[T]) -> list[T]:
    """按优先级**稳定**排序 (同优先级保持原有顺序, 便于复现)。"""
    return sorted(obligations, key=priority_of)


def obligation_kind(obligations: Iterable[ProofObligation], obligation_id: str) -> str:
    """按 id 找义务种类; 找不到返回空串 (不猜)。"""
    for obligation in obligations:
        if obligation.id == obligation_id:
            return obligation.kind
    return ""


def obligation_claim(obligations: Iterable[ProofObligation], obligation_id: str) -> str:
    """按 id 找义务所属命题; 找不到返回空串 (不猜)。"""
    for obligation in obligations:
        if obligation.id == obligation_id:
            return obligation.claim_id
    return ""
