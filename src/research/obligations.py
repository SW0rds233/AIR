from __future__ import annotations

"""义务的**排序口径** (计划书 §4.1 / §7.4)。

为什么单独成模块
----------------
"先做什么"是研究调度与只读呈现**共用**的判据: 引擎按它决定下一个动作, 工作台按它
决定缺口与义务的展示顺序。此前这份表长在 `research/loop.py` 里, 于是"读工作台"这类
只读需求也得依赖那个能执行研究的引擎 —— 引擎退役时工作台会跟着消失。

顺序为什么是这样 (不能随意调):
- **证据缺口优先于未关闭义务**: 检索/原文/支持关系判定会改变后续义务的**可判定性**;
  反过来先跑工具, 只会在缺条件时得到一堆 `unknown`。
- 规则型义务 (`design_necessity` 等) 必须先于其依赖的估计类义务, 否则规则在缺参数的
  情况下被评估成"未满足", 得到的是假失败。
- 因果识别类声明必须在**效应估计之后**再评估 (计划书 §7.4)。
"""

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
