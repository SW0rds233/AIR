from __future__ import annotations

"""experiments: 实验/仿真建议 (计划书 §8)。

本期只做规格与校验 (第一层), 不执行 (第二层需真实隔离执行能力后才开放)。
"""

from src.experiments.planner import design_experiment
from src.experiments.schemas import (
    ExecutionStatus,
    ExperimentKind,
    ExperimentPurpose,
    ExperimentSpec,
    VariableSpec,
)
from src.experiments.validator import SpecReport, validate_spec

__all__ = [
    "ExecutionStatus",
    "ExperimentKind",
    "ExperimentPurpose",
    "ExperimentSpec",
    "SpecReport",
    "VariableSpec",
    "design_experiment",
    "validate_spec",
]
