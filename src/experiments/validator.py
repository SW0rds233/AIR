from __future__ import annotations

"""实验/仿真规格校验 (计划书 §8.1/§8.2)。

`spec_validated` 只表示**规格通过检查**, 不代表科学假设已经成立,
也不代表实验已经执行。本模块不执行任何代码、不产生任何数值结果。
"""

from dataclasses import dataclass, field

from src.experiments.schemas import ExecutionStatus, ExperimentSpec

# 必填信息 (计划书 §8.1 表格): 缺任一项都不算可执行规格
REQUIRED_FIELDS = (
    ("hypothesis", "要检验的假设"),
    ("decision_rule", "什么结果会改变判断"),
    ("design", "设计类型与对照"),
    ("baseline", "对照/基线"),
    ("measurement", "测量方案"),
    ("error_estimation", "误差/区间估计方案"),
    ("stop_criteria", "失败/停止判据"),
)

# P1-3: 计划书逐项列出的必备要素 (交付前必须逐条交代; 缺关键项只能维持草案)
REQUIRED_ELEMENTS: tuple[tuple[str, str], ...] = (
    ("alternatives", "待区分的两个解释或待核验假设 (alternative_explanations)"),
    ("variables", "变量与单位 (variables)"),
    ("solver", "模型/求解方式 (solver, model_equations)"),
    ("parameter_ranges", "参数范围及其依据 (parameter_ranges)"),
    ("baseline", "基线与对照 (baseline, ablations)"),
    ("error_estimation", "误差与灵敏度检查 (error_estimation, sensitivity_checks)"),
    ("expected_output", "预期输出格式 (expected_figures/expected_tables/deliverables)"),
    ("decision_rule", "事前判据 (decision_rule)"),
    ("resources", "所需数据/设备及估算资源 (data_sources, budget)"),
    ("stop_criteria", "失败与停止条件 (stop_criteria)"),
)

# 只有真实执行过的状态才允许携带产物
_EXECUTED_STATUSES = {ExecutionStatus.executed, ExecutionStatus.analyzed}


@dataclass
class SpecReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": list(self.errors), "warnings": list(self.warnings)}

    def render(self) -> str:
        lines = [f"规格校验: {'通过' if self.ok else '未通过'}"]
        for e in self.errors:
            lines.append(f"- 缺失/无效: {e}")
        for w in self.warnings:
            lines.append(f"- 提示: {w}")
        return "\n".join(lines)


def missing_elements(spec: ExperimentSpec) -> list[str]:
    """逐条检查计划书要求的必备要素, 返回缺失项说明 (P1-3)。

    与 `REQUIRED_FIELDS` 的区别: 这里覆盖"变量与单位、参数依据、预期输出、资源"等
    交付审阅者要逐条核对的内容, 缺项会写进规格与工作台, 而不是只留在校验报告里。
    """
    missing: list[str] = []
    if not [a for a in spec.alternative_explanations if str(a).strip()]:
        missing.append(REQUIRED_ELEMENTS[0][1])
    if not spec.variables or any(not v.unit and v.role != "controlled" for v in spec.variables):
        missing.append(REQUIRED_ELEMENTS[1][1])
    if not str(spec.solver or "").strip() or "待定" in str(spec.solver):
        missing.append(REQUIRED_ELEMENTS[2][1])
    pending = [n for n, r in (spec.parameter_ranges or {}).items()
               if not str(r).strip() or "待定" in str(r) or "依据" in str(r)]
    if not spec.parameter_ranges or pending:
        missing.append(REQUIRED_ELEMENTS[3][1])
    if not str(spec.baseline or "").strip() or not spec.ablations:
        missing.append(REQUIRED_ELEMENTS[4][1])
    if not str(spec.error_estimation or "").strip() or not spec.sensitivity_checks:
        missing.append(REQUIRED_ELEMENTS[5][1])
    if not (spec.expected_figures or spec.expected_tables or spec.deliverables):
        missing.append(REQUIRED_ELEMENTS[6][1])
    if not str(spec.decision_rule or "").strip():
        missing.append(REQUIRED_ELEMENTS[7][1])
    if not str(spec.budget or "").strip() or "待" in str(spec.budget):
        missing.append(REQUIRED_ELEMENTS[8][1])
    if not str(spec.stop_criteria or "").strip():
        missing.append(REQUIRED_ELEMENTS[9][1])
    return missing


def validate_spec(spec: ExperimentSpec) -> SpecReport:
    errors: list[str] = []
    warnings: list[str] = []

    for name, label in REQUIRED_FIELDS:
        value = getattr(spec, name, "")
        if not value or not str(value).strip():
            errors.append(f"缺少{label} ({name})")

    if not spec.variables:
        errors.append("未声明操纵/控制/观测变量")
    if not any(v.role == "manipulated" for v in spec.variables):
        errors.append("未声明可操纵变量 (无法形成可检验设计)")
    if not spec.metrics:
        errors.append("未声明主要指标")
    if not spec.deliverables:
        warnings.append("未声明交付物清单")

    # 单位与量纲 (计划书 R5): 操纵/观测变量缺单位 → **不得**算可执行规格。
    # 早期实现把它降级为 warning, 于是"待定单位"的草案也能拿到 spec_validated。
    missing_units = [v.name for v in spec.variables if not v.unit and v.role != "controlled"]
    if missing_units:
        errors.append("以下变量未标注单位 (量纲检查无法完成, 只能作为草案): "
                      + ", ".join(missing_units))
    if not spec.unit_checks:
        warnings.append("未声明单位检查")

    # 参数范围必须给依据, 否则不得声称可执行 (关键 `待定` 项)
    pending_ranges = [name for name, rng in (spec.parameter_ranges or {}).items()
                      if "待定" in str(rng) or not str(rng).strip()]
    if pending_ranges:
        errors.append("以下参数的范围缺少依据 (只能作为草案): " + ", ".join(pending_ranges))

    # 求解器/资源等关键项待定 → 只能作为草案
    for name, label in (("solver", "求解器/数值方案"), ("budget", "资源上限")):
        value = str(getattr(spec, name, "") or "")
        if not value.strip() or "待定" in value or "待授权" in value:
            errors.append(f"{label}未确定 (只能作为草案): {value or '(空)'}")

    # 找不到可区分的解释 → 建议没有目标 (计划书 §8.3: 建议必须绑定要区分的解释)
    if not [a for a in spec.alternative_explanations if str(a).strip()]:
        errors.append("未声明待区分的替代解释 (建议没有明确目标)")

    # 重复策略: 不允许编造统一重复次数
    if "统一" in spec.repetitions and "不" not in spec.repetitions:
        errors.append("重复次数不得使用与问题无关的统一值")
    if not spec.repetitions:
        errors.append("未给出重复/误差估计流程")

    # 执行状态与产物一致性
    if spec.execution_status in _EXECUTED_STATUSES and not spec.artifacts:
        errors.append("标记为已执行但没有真实产物 (日志/配置 hash/结果文件)")
    if spec.execution_status in _EXECUTED_STATUSES and not spec.authorization:
        errors.append("标记为已执行但缺少授权记录")
    if spec.execution_status == ExecutionStatus.spec_validated and not spec.validation.get("ok"):
        warnings.append("状态为 spec_validated 但校验报告不完整")

    # P1-3: 必备要素缺项一律记进规格本身 (工作台与交付审阅都要看到)
    spec.missing_elements = missing_elements(spec)
    if spec.missing_elements:
        warnings.append("缺少必备要素: " + "; ".join(spec.missing_elements))
        if spec.execution_status == ExecutionStatus.spec_validated:
            errors.append("存在缺失的必备要素, 不得标记为可执行规格: "
                          + "; ".join(spec.missing_elements))

    return SpecReport(ok=not errors, errors=errors, warnings=warnings)
