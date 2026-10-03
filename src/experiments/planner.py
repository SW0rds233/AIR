from __future__ import annotations

"""实验/仿真规格生成 (计划书 §8.1)。

只生成**可审核的规格**, 不执行、不产生数值结果。

设计原则:
- 建议必须绑定具体未决问题/模型假设/理论边界; 没有要区分的解释就不算交付;
- 样本量与重复次数依目标区间宽度/效应量设计; 信息不足时给出估计流程;
- 只允许 `proposed / spec_validated`; `executed / analyzed` 需真实产物。
"""


from src.experiments.schemas import (
    ExecutionStatus,
    ExperimentKind,
    ExperimentPurpose,
    ExperimentSpec,
    VariableSpec,
)
from src.experiments.validator import validate_spec
from src.research.schemas import Claim, ClaimType, SourceEvidence, StudyDesign


def _purpose_for(claim: Claim) -> ExperimentPurpose:
    if claim.study.design != StudyDesign.none or claim.claim_type == ClaimType.causal:
        return ExperimentPurpose.test_model_assumption
    if claim.claim_type in (ClaimType.associational, ClaimType.predictive):
        return ExperimentPurpose.compare_mechanisms
    return ExperimentPurpose.check_approximation_limit


def _kind_for(claim: Claim) -> ExperimentKind:
    if claim.claim_type in (ClaimType.causal, ClaimType.associational, ClaimType.predictive):
        return ExperimentKind.simulation
    return ExperimentKind.verification


def _unit_for(claim: Claim, name: str) -> str:
    """从变量名里识别单位; 识别不到返回空串 (由校验器判为"只能作为草案")。"""
    from src.research.modeling import infer_unit_from_text

    return infer_unit_from_text(name)


def _gap_design(claim: Claim, gaps: list[dict], gap: dict) -> str:
    """按**具体缺口**给出不同的设计 (R5: 同一领域不同缺口应得到不同建议)。"""
    gap_type = str(gap.get("gap_type", ""))
    statement = str(gap.get("statement", ""))
    if gap_type == "missing_evidence" or "证据" in statement:
        return ("证据缺口设计: 先在授权资料源内做定向检索与原文条件比对, "
                "把不一致的来源列为对照; 不把检索无命中当成支持")
    if gap_type == "open_obligation":
        kind = str(gap.get("obligation_kind", "") or "")
        if kind == "estimate_effect" or claim.claim_type == ClaimType.causal:
            return ("效应估计设计: 声明识别策略与对照/处理组构造, "
                    "固定时间窗口, 报告聚类或稳健标准误下的区间")
        return ("义务核验设计: 针对未关闭义务构造受控参数扫描, "
                "把该义务的判定条件写成事前停止判据")
    if gap_type == "encoding_mismatch":
        return ("编码修复设计: 先修正形式化编码 (定义域/量词/关系), "
                "再用最小反例搜索确认修复后的编码与原命题一致")
    if gap_type == "route_exhausted":
        return ("换路设计: 更换数值方案或用不同精度重跑, 与失败路线做对照, "
                "记录原路线失败原因以免重复")
    if claim.claim_type == ClaimType.causal:
        return ("因果设计: 以声明设计为主, 列出识别假设并给出可检验的推论, "
                "用安慰剂/前置期检验作为对照")
    return ("受控参数扫描: 固定基准参数, 每次仅改变一个操纵变量, "
            "并记录解析特例作为对照")


def design_experiment(claim: Claim,
                      gaps: list[dict] | None = None,
                      evidence: list[SourceEvidence] | None = None,
                      distinguishing=None,
                      model: dict | None = None) -> ExperimentSpec:
    """为一个具体未决命题生成实验/仿真规格 (P1-3)。

    - `distinguishing` 为 `research.modeling.DistinguishingTest` 时, 目标是"区分两个候选机制";
    - `model` 为**被选模型** (Mechanism/ResearchModel 的 dict): 变量、量纲、方程/边界、
      指标与对照都从它导出, 而不是套一份通用参数扫描;
    - 缺关键要素时只维持草案, `missing_elements` 逐条写清缺什么。
    """
    gaps = gaps or []
    evidence = evidence or []
    gap = next((g for g in gaps if claim.id in str(g.get("target_ref", ""))), {})
    supporters = [e for e in evidence if e.support.value in ("supports", "partially_supports")]
    model = dict(model or {})

    variables: list[VariableSpec] = []
    for var, domain in (claim.variable_domains or {}).items():
        variables.append(VariableSpec(name=var, role="manipulated",
                                      unit=_unit_for(claim, var), range=str(domain),
                                      basis="由命题声明的变量域确定"))
    # P1-3: 被选模型的变量与单位优先 (模型说了算, 而不是命题文本)
    model_units = {str(k): str(v) for k, v in dict(model.get("units") or {}).items()}
    for name in (model.get("variables") or []):
        name = str(name)
        unit = model_units.get(name, "") or _unit_for(claim, name)
        existing = next((v for v in variables if v.name == name), None)
        if existing is not None:
            # 模型给了单位就采用 (命题文本里往往识别不出单位)
            if unit and not existing.unit:
                existing.unit = unit
                existing.basis = "单位来自被选模型"
            continue
        variables.append(VariableSpec(name=name, role="manipulated", unit=unit,
                                      basis="来自被选模型的变量表"))
    if claim.study.treatment:
        variables.append(VariableSpec(name=claim.study.treatment, role="manipulated",
                                      unit=_unit_for(claim, claim.study.treatment),
                                      basis="命题的处理变量"))
    if claim.study.outcome:
        variables.append(VariableSpec(name=claim.study.outcome, role="observed",
                                      unit=_unit_for(claim, claim.study.outcome),
                                      basis="命题的结果变量"))
    for confounder in claim.study.confounders or []:
        variables.append(VariableSpec(name=confounder, role="controlled",
                                      unit=_unit_for(claim, confounder),
                                      basis="命题声明的混淆因素"))
    if not variables:
        variables.append(VariableSpec(name="待明确", role="manipulated",
                                      basis="命题尚未给出可操纵变量, 需先补齐模型"))

    hypothesis = claim.statement
    alternatives = []
    if distinguishing is not None:
        # 建议的目标是区分两个候选解释
        hypothesis = distinguishing.statement
        alternatives = list(getattr(distinguishing, "discriminates", []) or [])
    # P1-3: 被选模型的边界/预测进入假设与替代解释 (建议继承真实模型)
    if model.get("boundaries"):
        hypothesis = f"{hypothesis} [模型边界: {str(model['boundaries'])[:80]}]"
    if model.get("relation"):
        alternatives.append(f"被选模型给出的机制: {str(model['relation'])[:120]}")
    if claim.status.value == "blocked":
        alternatives.append("该结论在更弱条件下成立 (需重新表述命题)")
        alternatives.append("该结论需要额外假设才成立 (需显式补出条件)")
    if claim.relation.value in (">", "<"):
        alternatives.append("等号可达, 结论只对非严格不等式成立")
    if not alternatives:
        alternatives.append("该结论的近似在参数区间边缘失效")

    decision_rule = (
        "预先声明: 若在声明的参数范围内观察到与结论方向一致且超出数值误差的表现, "
        "则该模型假设获得支持; 若出现系统性相反表现, 则记录反例并回到研究循环")
    if distinguishing is not None:
        names = list(getattr(distinguishing, "discriminates", []) or [])
        first = names[0] if names else "机制A"
        second = names[1] if len(names) > 1 else "机制B"
        decision_rule = (f"事先声明: 若观测符合「{first}」的预测, 保留该机制; "
                         f"若符合「{second}」的预测, 改用替代解释; "
                         f"两者都不符合则判定为未区分并回到研究循环")

    model_equations = ([f"{claim.lhs} {claim.relation.value} {claim.rhs}"] if claim.lhs else [])
    if model.get("formal_encoding"):
        model_equations = [str(model["formal_encoding"])] + model_equations
    spec = ExperimentSpec(
        claim_id=claim.id, claim_version=claim.version,
        gap_id=str(gap.get("gap_type", "")),
        model_ref={"id": str(model.get("id", "")), "name": str(model.get("name", ""))},
        model_source=(f"{model.get('name', '')}: {str(model.get('formal_encoding', ''))[:120]}"
                      if model else ""),
        target_gap=str(gap.get("gap_type", "") or ""),
        title=(f"可区分检验: {distinguishing.kind}" if distinguishing is not None
               else f"验证 {claim.id}: {hypothesis[:60]}"),
        purpose=(ExperimentPurpose.compare_mechanisms if distinguishing is not None
                 else _purpose_for(claim)),
        kind=(ExperimentKind.verification
              if distinguishing is not None and distinguishing.kind == "derivation"
              else _kind_for(claim)),
        hypothesis=hypothesis,
        alternative_explanations=alternatives,
        decision_rule=decision_rule,
        design=_gap_design(claim, gaps, gap),
        variables=variables,
        baseline="使用无条件/闭式解或已知解析特例作为基线",
        ablations=["移除单一假设后重跑, 观察结论是否仍成立",
                   "改用不同数值精度重跑, 排除离散化误差"],
        randomization="随机种子固定并记录; 若涉及采样, 使用独立重复并报告区间",
        model_equations=model_equations,
        data_sources=[e.title for e in supporters[:5]] or ["(待补充数据来源与权限)"],
        data_permissions="本地执行; 未经授权不外发原始数据",
        initial_conditions=[f"{v} ∈ {d}" for v, d in (claim.variable_domains or {}).items()],
        measurement="按变量定义记录单位与测量方式; 单位不一致即判规格无效",
        parameter_ranges={v.name: (v.range if v.range and "待" not in v.range
                                   else f"(待定, 需物理依据: {v.name})")
                          for v in variables if v.range},
        repetitions=("重复次数依目标区间宽度与效应量确定; 当前缺少方差/效应量估计, "
                     "先做 5~10 次试跑估计方差, 再按所需精度反推次数 (不预设统一值)"),
        random_seed="固定并写入配置 hash",
        solver="(待定: 依模型方程选择, 需记录离散化步长与收敛检查)",
        numerical_precision="记录浮点精度与误差量级, 与解析特例对比",
        budget="单次任务资源上限待授权后填写",
        metrics=["与结论方向一致的量化指标", "数值误差与解析基线的偏差", "参数边缘的行为"],
        predeclared_comparisons=["基准参数 vs 逐项改变的参数", "解析特例 vs 数值结果"],
        error_estimation=("报告采样/离散化误差区间; 不允许只报点估计。"
                          "区间跨零不得解释为效应不存在"),
        sensitivity_checks=["参数范围边界", "数值精度变化", "初始条件扰动"],
        stop_criteria="达到预设精度或触发预算上限; 参数范围边界无物理依据时停止并补充依据",
        analytic_special_cases=[f"等号条件: {claim.lhs} = {claim.rhs}"] if claim.lhs else [],
        unit_checks=["所有操纵/观测变量的单位一致且量纲正确"],
        conservation_checks=["若模型含守恒量, 检查其数值漂移"],
        leakage_risks=["若使用真实数据, 需按设备/会话/信道划分, 避免同一来源跨训练与评估"],
        confounders=list(claim.study.confounders or []) or ["(需先列出可能混淆因素)"],
        limitations=["仿真只验证模型行为, 不证明模型描述现实",
                     "本规格未执行, 不得作为已验证证据引用"],
        deliverables=["配置文件", "结果表格式说明", "分析脚本骨架 (可选)"],
        expected_figures=["主指标随操纵变量的变化曲线", "误差/区间图"],
        expected_tables=["参数扫描结果表", "敏感性检查表"],
        execution_status=ExecutionStatus.proposed,
        authorization="",
        notes=f"由未决命题 {claim.id} 生成; 证据分级 {claim.evidence_grade.value}; "
              f"已判定支持来源 {len(supporters)} 条",
    )
    report = validate_spec(spec)
    spec.validation = report.to_dict()
    # R5: **关键项待定时只能保持草案** —— 缺单位/缺范围依据/求解器待定的规格
    # 不获得 spec_validated (它表示"具备足够信息可执行", 不是"格式齐全")
    if report.ok:
        spec.execution_status = ExecutionStatus.spec_validated
    else:
        spec.execution_status = ExecutionStatus.proposed
        spec.notes = (spec.notes + f"; 仍为草案, 缺少: {'; '.join(report.errors[:4])}").strip()
    return spec
