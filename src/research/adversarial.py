from __future__ import annotations

"""反方审查清单 (计划书 §7.2 / §7.4)。

计划书要求独立审查"指向具体对象/步骤并产生新的义务", 而不是只给一个总分。
早期实现只有两条硬检查 (漏条件、循环引证), 应用类的质量问题完全没被审查:
样本选择偏差、外部效度、测量误差、混淆识别、多重比较、反向因果、指标替换、
结论强度 —— 这八项是应用/因果结论最常见的失效方式, 缺一项就可能把
"数据能支持的关联"写成"因果结论"。

设计约束
--------
1. **不判定成立**: 本清单只产出待核验义务 (informal_review), 不关闭任何义务、
   不提升任何证据等级、不改写任何命题状态;
2. **不适用必须说明原因**: 每项检查都返回 `not_applicable`/`satisfied` 并给出理由,
   审计记录里能看到"八项都跑过", 而不是只有命中项;
3. **缺信息不等于通过**: 该问的信息 (设计/混淆/测量/适用范围) 缺失时一律
   生成义务, 不回退成"没有发现问题";
4. **只看已登记字段**: 不猜测未记录的细节 (不读取"也许作者做过"的假设)。
"""

from dataclasses import dataclass, field

from src.research.schemas import (
    SUPPORTING_RELATIONS,
    Claim,
    ClaimType,
    EvidenceGrade,
    SourceEvidence,
    StudyDesign,
)

# 检查状态: 命中 (需处理) / 未命中 (可以说明理由) / 不适用 (类型或设计不涉及)
HIT = "hit"
CLEAR = "clear"
NOT_APPLICABLE = "not_applicable"

# 设计是否属于"有识别策略"的设计 (非此类设计的因果声明必须额外说明)
_IDENTIFIED_DESIGNS = (StudyDesign.rct, StudyDesign.did, StudyDesign.iv, StudyDesign.rdd,
                      StudyDesign.matching)

# 指标替换的征兆: 用代理/中间指标顶替目标结果
_PROXY_HINTS = ("代理", "proxy", "surrogate", "替代指标", "中间指标", "间接指标")
_OUTCOME_HINTS = ("产出", "收益", "增长", "效率", "收入", "productivity", "output",
                  "revenue", "growth")

# 显著性/多重比较的征兆
_MULTIPLE_HINTS = ("多重比较", "多重检验", "bonferroni", "holm", "fdr", "假发现率",
                   "family-wise", "预注册", "preregist", "预登记")
_SUBGROUP_HINTS = ("分组", "子样本", "异质性", "subgroup", "heterogeneity", "分样本")

# 反向因果的征兆
_REVERSE_HINTS = ("反向因果", "reverse causality", "互为因果", "同时决定",
                  "内生性", "endogeneity", "联立", "simultaneity")

# 样本选择偏差的征兆
_SELECTION_HINTS = ("选择偏差", "自选择", "幸存者", "selection bias", "survivorship",
                    "样本选择", "进入样本", "attrition", "流失", "截断", "门槛样本")
_SAMPLE_FIELDS = ("population", "region", "period")

# 测量误差的征兆
_MEASUREMENT_HINTS = ("测量误差", "measurement error", "信度", "效度", "reliability",
                      "validity", "编码规则", "口径")
_MISSING_HINTS = ("缺失", "missing", "mcar", "mar", "mnar", "插值", "删除样本")

# 外部效度的征兆
_EXTERNAL_HINTS = ("外部效度", "external validity", "可推广", "外推", "推广性",
                   "generaliz", "适用范围", "边界条件")

# 结论强度: 这些措辞在证据不足时属于过度断言
_STRONG_WORDS = ("证明", "必然", "一定", "毫无疑问", "显然", "证实", "proves",
                 "certainly", "undoubtedly", "establishes")
_HEDGE_WORDS = ("可能", "倾向于", "在……条件下", "在...条件下", "限于", "建议",
                "may", "suggests", "likely", "under the condition")


@dataclass
class Finding:
    """单项检查结果 (可审计)。"""

    check_id: str
    title: str
    status: str                     # hit / clear / not_applicable
    reason: str = ""
    obligation_statement: str = ""
    obligation_kind: str = "informal_review"
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == HIT

    def describe(self) -> str:
        label = {"hit": "需处理", "clear": "已说明", "not_applicable": "不适用"}[self.status]
        return f"[{label}] {self.title}: {self.reason}"


@dataclass
class AdversarialReview:
    """八项检查的完整结果。"""

    findings: list[Finding] = field(default_factory=list)

    @property
    def hits(self) -> list[Finding]:
        return [f for f in self.findings if f.status == HIT]

    @property
    def checked(self) -> int:
        return len(self.findings)

    def summary(self) -> str:
        if not self.findings:
            return "反方审查: 未执行"
        return (f"反方审查: {len(self.findings)} 项检查, "
                f"{len(self.hits)} 项提出待核验义务, "
                f"{sum(1 for f in self.findings if f.status == NOT_APPLICABLE)} 项不适用")

    def digest(self) -> list[dict]:
        return [{"check": f.check_id, "title": f.title, "status": f.status,
                 "reason": f.reason} for f in self.findings]


CHECK_TITLES = {
    "sample_selection_bias": "样本选择偏差",
    "external_validity": "外部效度",
    "measurement_error": "测量误差与缺失",
    "confounding": "混淆识别",
    "multiple_comparisons": "多重比较",
    "reverse_causality": "反向因果",
    "metric_substitution": "指标替换",
    "conclusion_strength": "结论强度",
}


def _text_of(claim: Claim) -> str:
    """命题自述文本 (不含 study.outcome, 避免指标替换检查自我循环)。

    `study.outcome` 是"被测量的指标"本身, 若混进自述文本, 任何结果变量都会被
    当成"目标概念"从而让指标替换检查恒定命中。这里只收集**说明性**字段。
    """
    study = claim.study
    parts = [
        claim.statement, claim.notes, claim.scope_population, claim.scope_conditions, claim.scope_region,
        claim.scope_period, study.population, study.region, study.period,
        study.counterfactual, study.confounder_handling,
        " ".join(study.identification_assumptions or []),
        study.design_feasibility, study.scenario_parameters,
        study.measurement_notes, study.missing_data_handling,
        study.error_structure, study.treatment, study.notes,
    ]
    return " ".join(p for p in parts if p)


def _hits(text: str, hints: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(h.lower() in low for h in hints)


def _supporting(evidence: list[SourceEvidence]) -> list[SourceEvidence]:
    return [e for e in evidence if e.support in SUPPORTING_RELATIONS]


def _is_applied(claim: Claim) -> bool:
    """是否需要应用/因果类审查 (形式化命题不涉及这些失效方式)。"""
    from src.research.classification import has_empirical_context, is_theoretical_claim
    if is_theoretical_claim(claim):
        return False
    # Descriptive is a semantic type, not evidence that a claim has samples.
    if claim.claim_type == ClaimType.descriptive:
        if not has_empirical_context(claim) and claim.strategy in {"derivation", "proof"}:
            return False
    return claim.claim_type in (ClaimType.causal, ClaimType.associational,
                                ClaimType.descriptive, ClaimType.predictive,
                                ClaimType.scenario)


def review_claim(claim: Claim,
                 evidence: list[SourceEvidence] | None = None
                 ) -> AdversarialReview:
    """对命题执行八项反方审查; 只输出待处理项, 不修改任何状态。"""
    evidence = list(evidence or [])
    text = _text_of(claim)
    supporting = _supporting(evidence)
    review = AdversarialReview()

    review.findings.append(_check_sample_selection(claim, text))
    review.findings.append(_check_external_validity(claim, text))
    review.findings.append(_check_measurement(claim, text, supporting))
    review.findings.append(_check_confounding(claim, text, supporting))
    review.findings.append(_check_multiple_comparisons(claim, text, supporting))
    review.findings.append(_check_reverse_causality(claim, text, evidence))
    review.findings.append(_check_metric_substitution(claim, text))
    review.findings.append(_check_conclusion_strength(claim, text, evidence))
    return review


# ----------------------------------------------------------------------
# 1. 样本选择偏差
# ----------------------------------------------------------------------
def _check_sample_selection(claim: Claim, text: str) -> Finding:
    if not _is_applied(claim):
        return Finding("sample_selection_bias", CHECK_TITLES["sample_selection_bias"],
                       NOT_APPLICABLE, "形式化命题不涉及样本选择")
    if _hits(text, _SELECTION_HINTS):
        return Finding("sample_selection_bias", CHECK_TITLES["sample_selection_bias"],
                       CLEAR, "已记录样本选择/流失相关说明")
    # 判断依据是**命题是否声明了样本范围**, 而不是"有没有文献证据":
    # 有没有外部证据由证据缺口与 evidence_support 义务负责, 不在这里重复计一次。
    empty = [f for f in _SAMPLE_FIELDS if not getattr(claim.study, f, "")]
    if empty:
        return Finding(
            "sample_selection_bias", CHECK_TITLES["sample_selection_bias"], HIT,
            f"未说明样本如何进入分析 (缺 {'、'.join(empty)}), 无法排除选择偏差",
            obligation_statement="说明样本选择过程与流失情况, 并评估选择偏差方向",
            detail="需给出抽样/入选规则、排除标准与流失率; 不得默认样本代表总体",
        )
    return Finding("sample_selection_bias", CHECK_TITLES["sample_selection_bias"],
                   CLEAR, "样本范围已登记")


# ----------------------------------------------------------------------
# 2. 外部效度
# ----------------------------------------------------------------------
def _check_external_validity(claim: Claim, text: str) -> Finding:
    if not _is_applied(claim):
        return Finding("external_validity", CHECK_TITLES["external_validity"],
                       NOT_APPLICABLE, "形式化命题不涉及外推")
    from src.research.classification import uses_population_scope

    if not uses_population_scope(claim):
        if claim.scope_conditions.strip():
            return Finding("external_validity", CHECK_TITLES["external_validity"],
                           CLEAR, "已登记研究对象与适用条件")
        return Finding(
            "external_validity", CHECK_TITLES["external_validity"], HIT,
            "未限定研究对象、环境与适用条件，结论可能被外推到未研究范围",
            obligation_statement="限定研究对象、环境与适用条件，并说明不可外推的部分",
        )
    if _hits(text, _EXTERNAL_HINTS):
        return Finding("external_validity", CHECK_TITLES["external_validity"],
                       CLEAR, "已声明适用范围或外推限制")
    missing = [name for name, value in (("人群", claim.scope_population or claim.study.population),
                                        ("地区", claim.scope_region or claim.study.region),
                                        ("时期", claim.scope_period or claim.study.period))
               if not value]
    if missing:
        return Finding(
            "external_validity", CHECK_TITLES["external_validity"], HIT,
            f"未限定适用{'/'.join(missing)}, 结论可能被外推到未研究范围",
            obligation_statement="限定结论适用的人群/地区/时期, 并说明不可外推的部分",
            detail="应用类结论必须给出适用边界; 未声明的维度不得默认成立",
        )
    return Finding("external_validity", CHECK_TITLES["external_validity"],
                   CLEAR, "人群/地区/时期均已限定")


# ----------------------------------------------------------------------
# 3. 测量误差与缺失
# ----------------------------------------------------------------------
def _check_measurement(claim: Claim, text: str,
                       supporting: list[SourceEvidence]) -> Finding:
    if not _is_applied(claim):
        return Finding("measurement_error", CHECK_TITLES["measurement_error"],
                       NOT_APPLICABLE, "形式化命题不涉及测量")
    has_measurement = bool(claim.study.measurement_notes) or _hits(text, _MEASUREMENT_HINTS)
    has_missing = bool(claim.study.missing_data_handling) or _hits(text, _MISSING_HINTS)
    if has_measurement and has_missing:
        return Finding("measurement_error", CHECK_TITLES["measurement_error"],
                       CLEAR, "测量口径与缺失处理均已说明")
    gaps = []
    if not has_measurement:
        gaps.append("测量口径/误差来源")
    if not has_missing:
        gaps.append("缺失数据机制与处理")
    return Finding(
        "measurement_error", CHECK_TITLES["measurement_error"], HIT,
        f"未说明 {'、'.join(gaps)}, 测量误差可能主导结论",
        obligation_statement="说明测量方案 (口径/误差来源) 与缺失数据机制及处理方式",
        detail="缺失机制需区分 MCAR/MAR/MNAR; 不得默认无缺失或误差可忽略",
    )


# ----------------------------------------------------------------------
# 4. 混淆识别
# ----------------------------------------------------------------------
def _check_confounding(claim: Claim, text: str,
                       supporting: list[SourceEvidence]) -> Finding:
    if not _is_applied(claim) or claim.claim_type not in (ClaimType.causal, ClaimType.associational,
                                ClaimType.predictive):
        return Finding("confounding", CHECK_TITLES["confounding"], NOT_APPLICABLE,
                       "该命题类型不声称变量间因果/关联效应")
    declared = bool(claim.study.confounders) or bool(claim.study.confounder_handling)
    if claim.claim_type == ClaimType.causal:
        identified = claim.study.design in _IDENTIFIED_DESIGNS
        if declared and (identified or claim.study.identification_assumptions):
            return Finding("confounding", CHECK_TITLES["confounding"], CLEAR,
                           "已声明混淆因素与识别策略")
        missing = []
        if not declared:
            missing.append("混淆因素清单/识别策略")
        if not identified and not claim.study.identification_assumptions:
            missing.append("识别假设 (该设计本身不构成识别策略)")
        return Finding(
            "confounding", CHECK_TITLES["confounding"], HIT,
            f"因果结论缺少 {'; '.join(missing)}",
            obligation_statement="列出混淆因素并说明识别策略及其不可检验假设",
            detail="观察性设计必须给出识别假设; 不得以'已控制变量'代替识别策略",
        )
    if declared:
        return Finding("confounding", CHECK_TITLES["confounding"], CLEAR,
                       "已声明混淆因素 (关联结论)")
    return Finding(
        "confounding", CHECK_TITLES["confounding"], HIT,
        "关联结论未列出可能混淆, 易被误读为因果",
        obligation_statement="列出可能混淆关联的因素, 并说明结论为关联而非因果",
        detail="混淆未排除前只能报告关联强度",
    )


# ----------------------------------------------------------------------
# 5. 多重比较
# ----------------------------------------------------------------------
def _check_multiple_comparisons(claim: Claim, text: str,
                                supporting: list[SourceEvidence]) -> Finding:
    if not _is_applied(claim) or claim.claim_type not in (ClaimType.causal, ClaimType.associational,
                                ClaimType.predictive, ClaimType.descriptive):
        return Finding("multiple_comparisons", CHECK_TITLES["multiple_comparisons"],
                       NOT_APPLICABLE, "该命题类型不涉及假设检验")
    if _hits(text, _MULTIPLE_HINTS):
        return Finding("multiple_comparisons", CHECK_TITLES["multiple_comparisons"],
                       CLEAR, "已说明多重比较/预注册安排")
    if _hits(text, _SUBGROUP_HINTS) or len(supporting) > 1:
        return Finding(
            "multiple_comparisons", CHECK_TITLES["multiple_comparisons"], HIT,
            "存在多结果/分组或多来源比较, 未说明多重比较校正或预注册",
            obligation_statement="说明检验次数、是否预注册, 以及多重比较校正方式",
            detail="未校正的多重比较会抬高假阳性; 不得只报告显著结果",
        )
    return Finding("multiple_comparisons", CHECK_TITLES["multiple_comparisons"],
                   CLEAR, "单一假设检验, 不涉及多重比较")


# ----------------------------------------------------------------------
# 6. 反向因果
# ----------------------------------------------------------------------
def _check_reverse_causality(claim: Claim, text: str,
                             evidence: list[SourceEvidence]) -> Finding:
    if not _is_applied(claim) or claim.claim_type not in (ClaimType.causal, ClaimType.predictive):
        return Finding("reverse_causality", CHECK_TITLES["reverse_causality"],
                       NOT_APPLICABLE, "该命题不声称方向性效应")
    design = claim.study.design
    if design in (StudyDesign.rct, StudyDesign.iv, StudyDesign.rdd):
        return Finding("reverse_causality", CHECK_TITLES["reverse_causality"], CLEAR,
                       f"{design.value} 设计本身排除了反向因果 (需保持设计假设)")
    if _hits(text, _REVERSE_HINTS):
        return Finding("reverse_causality", CHECK_TITLES["reverse_causality"], CLEAR,
                       "已讨论反向因果/内生性")
    has_temporal = bool(claim.study.period) or any(
        e.year and e.study_design in _IDENTIFIED_DESIGNS for e in evidence)
    if design == StudyDesign.did and claim.study.period:
        return Finding("reverse_causality", CHECK_TITLES["reverse_causality"], CLEAR,
                       "双重差分 + 明确时期, 时序上先于结果")
    return Finding(
        "reverse_causality", CHECK_TITLES["reverse_causality"], HIT,
        "未说明时序或排除反向因果的手段" + ("" if has_temporal else " (也缺时期信息)"),
        obligation_statement="说明时序关系并排除反向因果 (滞后项/工具变量/自然实验)",
        detail="横截面关联无法区分方向; 不得默认处理导致结果",
    )


# ----------------------------------------------------------------------
# 7. 指标替换
# ----------------------------------------------------------------------
def _check_metric_substitution(claim: Claim, text: str) -> Finding:
    if not _is_applied(claim):
        return Finding("metric_substitution", CHECK_TITLES["metric_substitution"],
                       NOT_APPLICABLE, "形式化命题不涉及指标选择")
    outcome = claim.study.outcome or claim.statement
    proxy = _hits(text, _PROXY_HINTS) or _hits(outcome, _PROXY_HINTS)
    # 只有当被测量指标是代理/替代物, 而命题陈述谈的是目标概念时才是"指标替换"
    conceptual_gap = proxy and (_hits(claim.statement, _OUTCOME_HINTS) or bool(claim.study.outcome))
    if conceptual_gap:
        return Finding(
            "metric_substitution", CHECK_TITLES["metric_substitution"], HIT,
            f"用代理指标 ({outcome}) 代替目标概念, 未说明二者关系",
            obligation_statement="说明代理指标与目标概念的关系及已知偏离",
            detail="代理指标不得直接当作目标结果; 需给出效度证据或明确限定解释范围",
        )
    return Finding("metric_substitution", CHECK_TITLES["metric_substitution"], CLEAR,
                   "未发现以代理指标顶替目标结果的写法")


# ----------------------------------------------------------------------
# 8. 结论强度
# ----------------------------------------------------------------------
def _check_conclusion_strength(claim: Claim, text: str,
                               evidence: list[SourceEvidence]) -> Finding:
    strong = _hits(claim.statement, _STRONG_WORDS)
    if not strong:
        return Finding("conclusion_strength", CHECK_TITLES["conclusion_strength"], CLEAR,
                       "结论措辞未出现绝对化断言")
    if claim.claim_type == ClaimType.definitional:
        return Finding("conclusion_strength", CHECK_TITLES["conclusion_strength"],
                       NOT_APPLICABLE, "形式化命题的措辞强度由验证器判定")
    if _hits(claim.statement, _HEDGE_WORDS):
        return Finding("conclusion_strength", CHECK_TITLES["conclusion_strength"], CLEAR,
                       "虽有强措辞但同时给出了限定条件")
    if claim.evidence_grade in (EvidenceGrade.identification_based,
                                EvidenceGrade.expert_reviewed):
        return Finding("conclusion_strength", CHECK_TITLES["conclusion_strength"], CLEAR,
                       f"证据等级 {claim.evidence_grade.value} 足以支撑该措辞")
    return Finding(
        "conclusion_strength", CHECK_TITLES["conclusion_strength"], HIT,
        f"结论使用绝对化措辞, 但证据等级仅为 {claim.evidence_grade.value}",
        obligation_statement="弱化结论措辞或补充证据等级, 使断言强度与证据一致",
        detail="应用类结论的强度不得超过证据等级; 需人工/独立审查确认措辞",
    )


# ----------------------------------------------------------------------
# 与义务机制对接
# ----------------------------------------------------------------------
def findings_to_obligations(review: AdversarialReview, claim: Claim,
                            existing_statements: set[str] | None = None) -> list:
    """把命中的检查项转成 `informal_review` 义务。

    仍未绕开核验: 这些义务在 `loop._act_check_step` 里保持 blocked,
    直到人工/独立审查确认, 因此审查意见不会自动改变任何命题状态。

    这些义务是 `required=False`: 它们表达"已知的实践性保留意见", 会随交付物
    一起呈现, 但**不再额外阻断**已经满足既有门槛的结论 ——
    否则任何结论都会因为"补一条样本说明"而永远无法交付。
    """
    from src.research.schemas import Coverage, ProofObligation

    existing = {s.strip() for s in (existing_statements or set())}
    obligations = []
    for finding in review.hits:
        statement = f"反方审查[{finding.title}]: {finding.obligation_statement or finding.reason}"
        if statement.strip() in existing:
            continue
        obligations.append(ProofObligation(
            statement=statement,
            kind=f"adversarial_{finding.check_id}",
            acceptance_method="informal_review",
            claim_id=claim.id,
            claim_version=claim.version,
            detail=(f"{finding.reason}; {finding.detail}".strip("; ")),
            coverage=Coverage.subgoal,
            required=False,
        ))
    return obligations


def review_claim_with_obligations(claim: Claim,
                                  evidence: list[SourceEvidence] | None = None,
                                  existing_statements: set[str] | None = None
                                  ) -> tuple[AdversarialReview, list]:
    """执行审查并返回 (审查结果, 新义务)。"""
    review = review_claim(claim, evidence)
    return review, findings_to_obligations(review, claim, existing_statements)


def mentions_multiple_testing(text: str) -> bool:
    """供测试与外部调用: 文本是否已声明多重比较处理。"""
    return _hits(text or "", _MULTIPLE_HINTS)


__all__ = [
    "CLEAR",
    "HIT",
    "NOT_APPLICABLE",
    "AdversarialReview",
    "Finding",
    "findings_to_obligations",
    "mentions_multiple_testing",
    "review_claim",
    "review_claim_with_obligations",
]
