from __future__ import annotations

"""问题类型能力矩阵与领域模型选择 (计划书 §5.2 / §7.2)。

解决的问题
----------
1. **问题类型曾经只有两类落点**: 候选问题里的 `category` 只区分
   "单调性/不等式" 与 "因果", 其余类型 (预测/情景/规范/描述/关联) 会被
   静默退化成"可符号核验的不等式", 于是模型会去"证明"一个本来需要数据或
   论证才能回答的问题。本模块为每个可声称的类型给出**显式能力声明**:
   该类型接受什么证据、是否需要数据/验证后端、缺什么时请求澄清。

2. **领域模型从未被"选中"**: 早期实现会为一条命题生成候选模型却从不写
   `ResearchModel.selected`, 于是 `theory_writer` 里所有模型都显示为"候选",
   结论实际依据哪个模型无从审计。`select_model` 把"选中"变成一次显示动作:
   写入选中的**具体版本**, 其余候选在同一命题范围内全部取消选中。

不可升级保证
------------
- 能力声明只是**前置条件描述**, 不会关闭任何义务、不会提升任何证据等级;
- 缺少必需项 (数据/后端) 时声明 `action="clarify"`, 并给出澄清问题 ——
  不得用"看起来很合理"的推导替代数据;
- 不认识的问题类型一律 `action="clarify"` (fail-closed), 不退化成默认类型。
"""

from dataclasses import dataclass, field

from src.research.schemas import ClaimType

# 问题类型 -> 命题类型 (category 是用户/候选层的说法, claim_type 是判定层的语义)
CATEGORY_TO_CLAIM_TYPE: dict[str, ClaimType] = {
    "inequality": ClaimType.definitional,
    "identity": ClaimType.definitional,
    "monotonicity": ClaimType.definitional,
    "definitional": ClaimType.definitional,
    "descriptive": ClaimType.descriptive,
    "associational": ClaimType.associational,
    "correlational": ClaimType.associational,
    "causal": ClaimType.causal,
    "applied": ClaimType.causal,
    "predictive": ClaimType.predictive,
    "forecast": ClaimType.predictive,
    "scenario": ClaimType.scenario,
    "normative": ClaimType.normative,
    "policy": ClaimType.normative,
}


@dataclass
class Capability:
    """一个可声称的问题类型的前置条件与验收方式。"""

    category: str
    claim_type: ClaimType
    # 验收所依据的验证后端 (空元组表示没有工具后端, 只能论证/数据)
    backends: tuple[str, ...]
    requires_data: bool = False
    requires_design: bool = False
    # 该类型的最低证据等级 (应用类结论不得低于此级别)
    min_evidence_grade: str = "unsupported"
    description: str = ""
    obligation_kinds: tuple[str, ...] = field(default_factory=tuple)


# 单一事实来源: 类型 -> 能力。新增类型必须在这里登记, 否则 fail-closed。
CAPABILITIES: dict[str, Capability] = {
    "definitional": Capability(
        category="definitional", claim_type=ClaimType.definitional,
        backends=("sympy", "z3", "lean"),
        description="定义性命题: 在声明变量域内可由符号/求解器内核验",
        obligation_kinds=("prove_inequality", "prove_identity", "prove_monotonicity",
                          "equality_condition", "check_implication"),
    ),
    "descriptive": Capability(
        category="descriptive", claim_type=ClaimType.descriptive,
        backends=("stats",), requires_data=True,
        min_evidence_grade="single_source",
        description="描述性命题: 需要可核查的观测数据或权威统计, 不能靠推导成立",
        obligation_kinds=("estimate_effect", "evidence_support", "scope_check"),
    ),
    "associational": Capability(
        category="associational", claim_type=ClaimType.associational,
        backends=("stats",), requires_data=True, requires_design=True,
        min_evidence_grade="single_source",
        description="关联性命题: 需数据与(准)实验设计, 且不得表述为因果",
        obligation_kinds=("estimate_effect", "control_confound", "scope_check",
                          "evidence_support"),
    ),
    "causal": Capability(
        category="causal", claim_type=ClaimType.causal,
        backends=("stats",), requires_data=True, requires_design=True,
        min_evidence_grade="identification_based",
        description="因果命题: 需识别策略与四类独立声明 (识别假设/可行性/测量与缺失/误差结构)",
        obligation_kinds=("estimate_effect", "control_confound", "scope_check",
                          "evidence_support", "identification_assumptions",
                          "design_feasibility", "measurement_and_missing",
                          "error_structure"),
    ),
    "predictive": Capability(
        category="predictive", claim_type=ClaimType.predictive,
        backends=("stats",), requires_data=True, requires_design=True,
        min_evidence_grade="single_source",
        description="预测性命题: 需样本外评估方案与基线比较, 缺一不可",
        obligation_kinds=("estimate_effect", "scope_check", "evidence_support",
                          "predictive_validation"),
    ),
    "scenario": Capability(
        category="scenario", claim_type=ClaimType.scenario,
        backends=(), requires_data=True, requires_design=True,
        min_evidence_grade="anecdotal",
        description="情景/仿真命题: 只能给出条件性结论, 必须声明情景参数与适用边界",
        obligation_kinds=("scope_check", "evidence_support", "scenario_parameters"),
    ),
    "normative": Capability(
        category="normative", claim_type=ClaimType.normative,
        backends=(), requires_data=False, requires_design=False,
        min_evidence_grade="expert_reviewed",
        description="规范性命题: 依赖价值判断, 只能由独立审查确认, 系统不得自动判定成立",
        obligation_kinds=("evidence_support", "scope_check"),
    ),
}

# 方向输入时的缺省类型: 只有在文本给出显式关系时才会被选。
DEFAULT_CATEGORY = "definitional"


def normalize_category(category: str) -> str:
    """把候选层的 category 归一化到能力矩阵的键; 未知类型返回空串 (fail-closed)。"""
    key = (category or "").strip().lower()
    if not key:
        return ""
    if key in CAPABILITIES:
        return key
    claim_type = CATEGORY_TO_CLAIM_TYPE.get(key)
    if claim_type is not None and claim_type.value in CAPABILITIES:
        return claim_type.value
    return ""


def claim_type_for(category: str) -> ClaimType:
    """未知类型不得被当作 definitional: 返回 normative 之前先显式判定。

    这里的选择是**保守**的: 未登记类型按 `normative` 处理, 即"需独立审查、
    不得自动判定成立", 而不是退化成可符号核验的定义性命题。
    """
    key = normalize_category(category)
    if key:
        return CAPABILITIES[key].claim_type
    return ClaimType.normative


@dataclass
class Declaration:
    """一次能力声明 (可审计, 会写进事件与工作台)。"""

    category: str = ""
    claim_type: ClaimType = ClaimType.normative
    known: bool = False
    action: str = "clarify"      # proceed / clarify
    required_backends: tuple[str, ...] = ()
    available_backends: tuple[str, ...] = ()
    missing_backends: tuple[str, ...] = ()
    requires_data: bool = False
    has_data: bool = False
    requires_design: bool = False
    has_design: bool = False
    min_evidence_grade: str = "unsupported"
    questions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action != "proceed"

    def describe(self) -> str:
        if not self.known:
            return f"未知问题类型 {self.category or '(空)'}: 需先澄清类型再研究"
        parts = [f"类型 {self.category} → 命题类型 {self.claim_type.value}"]
        if self.required_backends:
            parts.append("后端 " + "/".join(self.required_backends)
                         + ("(可用)" if not self.missing_backends else
                            f"(缺 {'/'.join(self.missing_backends)})"))
        if self.requires_data:
            parts.append("需数据" + ("(已声明)" if self.has_data else "(未声明)"))
        if self.requires_design:
            parts.append("需设计" + ("(已声明)" if self.has_design else "(未声明)"))
        if self.questions:
            parts.append("待澄清: " + "; ".join(self.questions))
        return "; ".join(parts)


def capability_for(category: str) -> Capability | None:
    key = normalize_category(category)
    return CAPABILITIES.get(key) if key else None


def declare_capability(category: str, *, available: dict[str, bool] | None = None,
                       has_data: bool = False, has_design: bool = False) -> Declaration:
    """给出"该类型的问题在本系统里是否可研究"的显式声明。

    - 未登记的 category → `action="clarify"` (不臆造能力);
    - 必需后端不可用 → `clarify` 并说明缺哪个后端;
    - 必需数据/设计缺失 → `clarify` 并给出具体澄清问题。
    """
    available = available or {}
    decl = Declaration(category=category)
    cap = capability_for(category)
    if cap is None:
        decl.questions.append(
            f"「{category or '(未给出类型)'}」不是已登记的问题类型, 请确认它属于哪一类: "
            + " / ".join(sorted(CAPABILITIES)))
        decl.known = False
        decl.action = "clarify"
        return decl

    decl.known = True
    decl.claim_type = cap.claim_type
    decl.required_backends = cap.backends
    decl.available_backends = tuple(b for b in cap.backends if available.get(b))
    decl.missing_backends = tuple(b for b in cap.backends if not available.get(b))
    decl.requires_data = cap.requires_data
    decl.has_data = bool(has_data)
    decl.requires_design = cap.requires_design
    decl.has_design = bool(has_design)
    decl.min_evidence_grade = cap.min_evidence_grade

    if cap.backends and not decl.available_backends:
        decl.reasons.append(
            f"缺少必需验证后端 ({'/'.join(cap.backends)}), 该类型无法自动核验")
    if cap.requires_data and not has_data:
        decl.reasons.append("缺少可核查的数据来源, 结论只能停留在待验证")
        decl.questions.append("请提供公开可核查的数据来源 (或允许系统仅做方法设计)")
    if cap.requires_design and not has_design:
        decl.reasons.append("缺少研究设计, 无法区分该类型所需的识别条件")
        decl.questions.append("请确认研究设计 (RCT/DiD/IV/RDD/匹配/观察/仿真)")
    if cap.claim_type == ClaimType.normative:
        decl.reasons.append("规范性结论不得由系统自动判定成立, 需独立审查")
        # 不是"不能研究", 而是"不能由系统自行判定" —— 必须由人确认是否继续
        decl.questions.append("该结论依赖价值判断, 请确认由谁作为独立审查方认定其成立")

    decl.action = "clarify" if decl.questions else "proceed"
    return decl


# ----------------------------------------------------------------------
# 领域模型选择
# ----------------------------------------------------------------------
def candidate_scheme(claim) -> tuple[str, str, str]:
    """按命题类型给出**候选**模型方案 (名称, 机制说明, 忠实度说明)。

    这里只描述"该类型的问题需要什么样的模型", 不声称任何结论成立。
    """
    claim_type = getattr(claim, "claim_type", ClaimType.definitional)
    if claim_type == ClaimType.causal:
        return ("因果识别模型",
                "结果 = 处理效应 + 混淆/趋势项 + 误差; 识别策略决定可估参数",
                "模型必须写明识别假设与研究对象、适用条件, 否则结论不得外推")
    if claim_type == ClaimType.predictive:
        return ("预测模型",
                "结果 = 特征函数 + 模型误差; 需样本外评估方案",
                "预测精度不等于因果解释, 二者不得互相代替")
    if claim_type == ClaimType.scenario:
        return ("情景/仿真模型",
                "状态随参数演化的规则系统; 结论仅在给定情景参数下成立",
                "情景参数是假设而非事实, 必须随结论一起给出")
    if claim_type == ClaimType.associational:
        return ("关联模型",
                "两个可观测量之间的统计依赖关系",
                "关联不得被表述为因果; 混淆未被排除前只能报告关联强度")
    if claim_type == ClaimType.descriptive:
        return ("描述模型",
                "在声明的研究对象与条件内描述分布或水平",
                "描述统计不可外推到未研究对象或条件")
    if claim_type == ClaimType.normative:
        return ("规范论证模型",
                "由价值前提与事实前提共同推出应当如何",
                "价值前提必须显式列出; 系统不得自动判定规范结论成立")
    return ("形式化模型",
            "命题涉及的符号表达式及其定义域/假设",
            "模型只覆盖形式化片段, 不等同于对原问题的完整建模")


def select_model(store, claim, models: list, *, reason: str = "",
                 prefer: str = "") -> dict:
    """在候选模型里**显式选中**一个具体版本, 并取消同一命题下其他模型的选中。

    选中动作只影响审计与展示:
    - 写入选中的模型 id 与**版本**, 其余候选 `selected=False`;
    - 不改变任何结论状态、不关闭任何义务、不提升任何证据等级;
    - 没有候选模型时显式返回 `ok=False` 与原因, 不制造"默认已选模型"。

    `models` 必须是**全部候选**: 取消选中要在全集上做。`prefer` 指定优先
    选中的模型 id; 未指定时按来源完整度自动选择。

    返回值里 `updates` 是应落盘的模型对象列表, `claim` 是应落盘的命题副本;
    本函数**不写库** —— 落盘由调用方在同一个原子步骤里完成。
    """
    del store  # 保留签名兼容; 本函数纯计算, 不做任何持久化
    candidates = [m for m in (models or []) if m is not None]
    if claim is None:
        return {"ok": False, "reason": "缺少命题, 无法选择模型", "selected": ""}
    if not candidates:
        return {"ok": False, "reason": "没有候选模型可供选择", "selected": ""}

    if prefer and not any(m.id == prefer for m in candidates):
        return {"ok": False, "reason": f"指定的模型不在候选中: {prefer}", "selected": ""}

    def _score(m) -> tuple:
        # 忠实度说明与来源越完整越优先; 同分时保持原顺序 (稳定排序)
        refs = len(getattr(m, "source_refs", []) or [])
        complete = sum(bool(getattr(m, f, "")) for f in
                       ("natural_language", "formal_encoding", "fidelity", "boundaries"))
        return (refs, complete)

    if prefer:
        chosen = next(m for m in candidates if m.id == prefer)
    else:
        chosen = max(candidates, key=_score)

    from src.research.schemas import ObjectRef

    for model in candidates:
        model.selected = model.id == chosen.id
    claim.model_ref = ObjectRef(id=chosen.id, version=chosen.version)
    return {
        "ok": True,
        "selected": chosen.id,
        "version": chosen.version,
        "reason": reason or ("指定选择" if prefer else "按来源完整度选择"),
        "candidates": len(candidates),
        "name": chosen.name,
        "updates": candidates,
        "claim": claim,
    }
