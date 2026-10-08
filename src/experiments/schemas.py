from __future__ import annotations

"""实验/仿真建议的数据模型 (计划书 §8.1)。

本期只做**规格**, 不执行: `execution_status` 只能是 `proposed` / `spec_validated`,
`executed` / `analyzed` 需要真实执行日志与产物才能写入。

硬约束 (计划书 §8.3、§4.3-8):
- 未运行的实验只有方案, 不得出现在"实验结果"中;
- 建议结果不能反向充当现有结论的证据;
- 样本量与重复次数依目标区间宽度/效应量设计, 信息不足时给出估计流程, 不编造统一次数。
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.research.schemas import new_id, utcnow


def _new_id(prefix: str) -> str:
    return new_id(prefix)


class ExperimentKind(str, Enum):
    simulation = "simulation"
    observational = "observational"
    interventional = "interventional"
    verification = "verification"   # 用实现验证解析特例


class ExecutionStatus(str, Enum):
    proposed = "proposed"
    spec_validated = "spec_validated"
    executed = "executed"
    analyzed = "analyzed"


class ExperimentPurpose(str, Enum):
    """该建议究竟要解决什么 (计划书 §8.3 的五类闭合关系)。"""

    test_model_assumption = "test_model_assumption"     # 检验模型假设是否现实
    check_approximation_limit = "check_approximation_limit"  # 理论近似何时失效
    compare_mechanisms = "compare_mechanisms"           # 比较两个候选机制
    explore_hard_region = "explore_hard_region"         # 解析困难区域生成猜想
    verify_implementation = "verify_implementation"     # 验证实现符合解析特例


class VariableSpec(BaseModel):
    name: str
    role: str = "manipulated"   # manipulated / controlled / observed
    unit: str = ""
    range: str = ""
    basis: str = ""             # 参数范围的依据 (物理/数值)


class ExperimentSpec(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("exp"))
    version: int = 1
    claim_id: str = ""
    claim_version: int = 1
    gap_id: str = ""
    # 建议必须由**被选模型**导出, 而不是套模板; 记录模型来源便于复核
    model_ref: dict[str, Any] = Field(default_factory=dict)
    model_source: str = ""        # 模型名 + 形式化片段摘要
    target_gap: str = ""          # 本条建议针对的具体缺口
    title: str = ""
    purpose: ExperimentPurpose = ExperimentPurpose.test_model_assumption
    kind: ExperimentKind = ExperimentKind.simulation
    # 研究目的
    hypothesis: str = ""
    alternative_explanations: list[str] = Field(default_factory=list)
    decision_rule: str = ""      # 什么结果会使我们改变判断
    # 设计
    design: str = ""
    variables: list[VariableSpec] = Field(default_factory=list)
    baseline: str = ""
    ablations: list[str] = Field(default_factory=list)
    randomization: str = ""
    # 模型与数据
    model_equations: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    data_permissions: str = ""
    initial_conditions: list[str] = Field(default_factory=list)
    measurement: str = ""
    # 运行规格
    parameter_ranges: dict[str, str] = Field(default_factory=dict)
    repetitions: str = ""
    random_seed: str = ""
    solver: str = ""
    numerical_precision: str = ""
    budget: str = ""
    # 分析计划
    metrics: list[str] = Field(default_factory=list)
    predeclared_comparisons: list[str] = Field(default_factory=list)
    error_estimation: str = ""
    sensitivity_checks: list[str] = Field(default_factory=list)
    stop_criteria: str = ""
    # 质量检查
    analytic_special_cases: list[str] = Field(default_factory=list)
    unit_checks: list[str] = Field(default_factory=list)
    conservation_checks: list[str] = Field(default_factory=list)
    leakage_risks: list[str] = Field(default_factory=list)
    confounders: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    # 交付物
    deliverables: list[str] = Field(default_factory=list)
    expected_figures: list[str] = Field(default_factory=list)
    expected_tables: list[str] = Field(default_factory=list)
    # 执行状态
    execution_status: ExecutionStatus = ExecutionStatus.proposed
    authorization: str = ""
    artifacts: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    # 计划书要求的必备要素里**还缺哪些** (缺关键项只能维持草案)
    missing_elements: list[str] = Field(default_factory=list)
    notes: str = ""
    created_at: str = Field(default_factory=utcnow)
