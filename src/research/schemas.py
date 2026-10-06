from __future__ import annotations

"""理论研究对象模型 (P0)。

设计原则 (对应《下一步执行方案》第 5 节):
- 命题的"来源 / 状态 / 验证等级"三个维度分开记录, 不用单一可信分数覆盖。
- 所有对象带稳定 ID 与版本; LLM 只提交候选变更, 状态由校验层写入。
- 未关闭关键义务的命题不得标记为 supported。
"""

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def stable_id(prefix: str, *parts: Any) -> str:
    """确定性 ID: 同一来源/同一内容永远得到同一 ID。

    证据/卡片等"由来源决定"的对象必须用确定性 ID, 否则重复检索会产生内容相同
    但 ID 不同的对象, 使幂等键失效、重复计费并污染版本历史。
    """
    basis = "|".join(str(p) for p in parts)
    return f"{prefix}-{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:12]}"


class Origin(str, Enum):
    """命题来源。"""

    external = "external"            # 外部结果
    user_assumption = "user_assumption"  # 用户假设
    proposed = "proposed"            # 本研究提出
    derived = "derived"              # 本研究推导


class ClaimStatus(str, Enum):
    proposed = "proposed"
    in_progress = "in_progress"
    supported = "supported"
    refuted = "refuted"
    blocked = "blocked"


class Assurance(str, Enum):
    """验证等级 (兼容字段)。

    计划书 §4.2: 把 informal/empirical/symbolic/solver/formal 排成一条等级链
    不宜继续用于跨类型比较 —— 数学证明、经验支持与因果识别不是同一量尺。
    因此本枚举保留用于旧输出兼容, 但不再承担"谁更强"的判定职责:
    跨类型比较一律改用 support_kind + coverage + validation_status。
    仅同一 support_kind 内部允许排序 (见 SUPPORT_KIND_RANK)。
    """

    unverified = "unverified"
    informal_reviewed = "informal_reviewed"
    empirical_estimated = "empirical_estimated"
    symbolic_checked = "symbolic_checked"
    solver_checked = "solver_checked"
    formally_checked = "formally_checked"


# 注意: 仅用于同一 support_kind 内部的强弱比较, 不得跨类型使用。
ASSURANCE_RANK = {
    Assurance.unverified: 0,
    Assurance.informal_reviewed: 1,
    Assurance.empirical_estimated: 2,
    Assurance.symbolic_checked: 3,
    Assurance.solver_checked: 4,
    Assurance.formally_checked: 5,
}


class SupportKind(str, Enum):
    """支持方式 (计划书 §4.2): 描述"结论靠什么被支持", 不是一条等级链。"""

    none = "none"
    textual_support = "textual_support"        # 原文支持
    informal_argument = "informal_argument"    # 非形式化论证
    theorem_application = "theorem_application"  # 具名定理的机器可复核应用
    symbolic_check = "symbolic_check"          # 符号检查
    constraint_solve = "constraint_solve"      # 约束求解
    formal_proof = "formal_proof"              # 形式化证明
    numerical_test = "numerical_test"          # 数值测试
    statistical_estimate = "statistical_estimate"  # 统计估计


SUPPORT_KIND_RANK = {
    SupportKind.none: 0,
    SupportKind.numerical_test: 1,
    SupportKind.textual_support: 2,
    SupportKind.informal_argument: 3,
    # 具名定理 + 参数由机器重算的证书, 强于一般非形式化论证、弱于符号/形式化核验
    SupportKind.theorem_application: 4,
    SupportKind.symbolic_check: 4,
    SupportKind.constraint_solve: 5,
    SupportKind.formal_proof: 6,
    SupportKind.statistical_estimate: 3,
}


class Coverage(str, Enum):
    """覆盖范围 (计划书 §4.2): 该支持覆盖到哪一层。"""

    step = "step"
    subgoal = "subgoal"
    target = "target"


class ValidationStatus(str, Enum):
    """验证结果状态 (计划书 §4.3 / §7.3 的统一语义)。

    verified 是唯一可用来支持结论的状态; 其余一律不得升级为支持。
    """

    unchecked = "unchecked"
    verified = "verified"
    counterexample_found = "counterexample_found"
    unknown = "unknown"
    encoding_mismatch = "encoding_mismatch"
    invalid_input = "invalid_input"
    timeout = "timeout"
    unavailable = "unavailable"
    unsupported = "unsupported"
    execution_error = "execution_error"


# 运行/能力问题 (交给路线规划处理), 与"命题为假"严格区分。
NON_SCIENTIFIC_STATUSES = {
    ValidationStatus.encoding_mismatch,
    ValidationStatus.invalid_input,
    ValidationStatus.timeout,
    ValidationStatus.unavailable,
    ValidationStatus.unsupported,
    ValidationStatus.execution_error,
}


class SupportKindOfEvidence(str, Enum):
    """证据与命题的关系 (计划书 §6.2): 支持 / 反对 / 部分 / 背景 / 不足。

    初始必须是 `insufficient` —— 检索命中不构成支持关系。
    """

    insufficient = "insufficient"
    supports = "supports"
    contradicts = "contradicts"
    partially_supports = "partially_supports"
    background = "background"


SUPPORTING_RELATIONS = {
    SupportKindOfEvidence.supports,
    SupportKindOfEvidence.partially_supports,
}


class GapType(str, Enum):
    """研究缺口类型 (计划书 §4.1 ResearchGap)。"""

    missing_definition = "missing_definition"
    missing_model = "missing_model"
    missing_evidence = "missing_evidence"
    missing_condition = "missing_condition"
    open_obligation = "open_obligation"
    unresolved_claim = "unresolved_claim"
    novelty_unchecked = "novelty_unchecked"
    encoding_mismatch = "encoding_mismatch"
    route_exhausted = "route_exhausted"
    budget_exhausted = "budget_exhausted"


class RouteStatus(str, Enum):
    """研究路线状态 (计划书 §4.1 ResearchRoute / §5.4)。"""

    active = "active"
    suspended = "suspended"
    failed = "failed"
    abandoned = "abandoned"
    completed = "completed"


class ReferenceStatus(str, Enum):
    """文献被引用的适用性 (计划书 §6.2 第三项检查)。"""

    unchecked = "unchecked"
    applicable = "applicable"
    out_of_scope = "out_of_scope"
    unknown = "unknown"


class ObjectRef(BaseModel):
    """统一对象引用 (计划书 §4.1)。"""

    id: str
    version: int = 1

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"{self.id}@v{self.version}"


class VerificationScope(str, Enum):
    """验证覆盖范围: 局部步骤 / 完整目标。"""

    step = "step"
    target = "target"


class NoveltyStatus(str, Enum):
    unchecked = "unchecked"
    known_equivalent = "known_equivalent"
    potentially_distinct = "potentially_distinct"
    expert_reviewed = "expert_reviewed"


class ObligationStatus(str, Enum):
    open = "open"
    closed = "closed"
    refuted = "refuted"
    blocked = "blocked"


class SourceKind(str, Enum):
    journal = "journal"
    conference = "conference"
    preprint = "preprint"
    textbook = "textbook"
    manual = "manual"
    dataset = "dataset"
    report = "report"
    patent = "patent"
    standard = "standard"
    other = "other"


class ClaimType(str, Enum):
    """命题类型: 决定可接受的证据与验收标准。"""

    definitional = "definitional"
    descriptive = "descriptive"
    associational = "associational"
    causal = "causal"
    predictive = "predictive"
    scenario = "scenario"
    normative = "normative"


class StudyDesign(str, Enum):
    rct = "rct"
    did = "did"                  # 双重差分
    iv = "iv"                    # 工具变量
    rdd = "rdd"                  # 断点回归
    matching = "matching"
    observational = "observational"
    qualitative = "qualitative"
    simulation = "simulation"
    theory = "theory"
    none = "none"


class EvidenceGrade(str, Enum):
    """应用类结论的证据等级 (不得静默升级)。"""

    unsupported = "unsupported"
    anecdotal = "anecdotal"
    single_source = "single_source"
    converging = "converging"
    identification_based = "identification_based"
    expert_reviewed = "expert_reviewed"


class Credibility(str, Enum):
    high = "high"          # 同行评审期刊/官方统计/标准
    medium = "medium"      # 工作论文/行业报告/预印本
    low = "low"            # 新闻/博客/未署名材料


EVIDENCE_GRADE_RANK = {
    EvidenceGrade.unsupported: 0,
    EvidenceGrade.anecdotal: 1,
    EvidenceGrade.single_source: 2,
    EvidenceGrade.converging: 3,
    EvidenceGrade.identification_based: 4,
    EvidenceGrade.expert_reviewed: 5,
}


class StudyPlan(BaseModel):
    """因果/应用研究的设计与数据契约。"""

    design: StudyDesign = StudyDesign.none
    treatment: str = ""
    outcome: str = ""
    population: str = ""
    region: str = ""
    period: str = ""
    counterfactual: str = ""
    confounders: list[str] = Field(default_factory=list)
    confounder_handling: str = ""
    # ---- 计划书 §7.4: 因果结论需把以下各项列为独立研究义务 ----
    identification_assumptions: list[str] = Field(default_factory=list)  # 识别假设
    design_feasibility: str = ""          # 设计可行性 (为何该设计在本数据上可用)
    measurement_notes: str = ""           # 测量方案
    missing_data_handling: str = ""       # 缺失机制与处理
    error_structure: str = ""             # 误差结构 (聚类/异方差/自相关等)
    # 数据来源性质: 防止用占位数据产出"现实因果结论" (§7.4)
    data_source_kind: str = "unspecified"  # unspecified / real / placeholder / synthetic
    data_source_note: str = ""
    treatment_col: str = ""
    outcome_col: str = ""
    group_col: str = ""
    time_col: str = ""
    treated_label: str = ""
    control_label: str = ""
    pre_label: str = ""
    post_label: str = ""
    data_ref: str = ""
    # 结构化数据源 (计划书 §6.3): data_ref 指向 CSV/SQLite 时的表名与过滤条件。
    # 这些字段只是**查询意图**, 实际读取仍由只读适配器做授权与上限校验。
    data_table: str = ""
    data_where: str = ""
    rows: list[dict] = Field(default_factory=list)  # 内联小样本 (公开数据)
    notes: str = ""

class Relation(str, Enum):
    ge = ">="
    le = "<="
    gt = ">"
    lt = "<"
    eq = "=="
    ne = "!="
    custom = "custom"


class ActionType(str, Enum):
    clarify_problem = "clarify_problem"
    retrieve_targeted = "retrieve_targeted"
    read_source = "read_source"
    extract_result = "extract_result"
    interpret_evidence = "interpret_evidence"
    propose_claim = "propose_claim"
    propose_model = "propose_model"
    plan_proof = "plan_proof"
    derive_step = "derive_step"
    check_step = "check_step"
    seek_counterexample = "seek_counterexample"
    compare_prior_work = "compare_prior_work"
    revise_hypothesis = "revise_hypothesis"
    switch_strategy = "switch_strategy"
    design_experiment = "design_experiment"
    synthesize_results = "synthesize_results"
    deliver_partial = "deliver_partial"
    write_paper = "write_paper"
    stop_with_report = "stop_with_report"


class ClaimQuestion(BaseModel):
    """候选研究问题 (可程序化构造, 也可由方向解析/LLM 生成)。

    category:
    - inequality: LHS 关系 RHS (现有)
    - identity: LHS = RHS
    - monotonicity: 因变量 expr 关于自变量 wrt 的单调/影响方向
    """

    statement: str
    category: str = "inequality"
    claim_type: ClaimType = ClaimType.definitional
    # 稳定候选 ID: 用户确认后绑定, 不得"重新生成候选再按旧序号选择"
    candidate_id: str = ""
    confirmed_version: int = 0
    lhs: str = ""
    rhs: str = ""
    relation: Relation = Relation.custom
    # monotonicity 专用
    expr: str = ""
    wrt: str = ""
    direction: str = ""  # increasing / decreasing / nondecreasing / nonincreasing
    dependent: str = ""   # 因变量的人可读名称
    independent: str = ""  # 自变量的人可读名称
    # 应用/因果专用
    study: StudyPlan = Field(default_factory=StudyPlan)
    variables: list[str] = Field(default_factory=list)
    variable_domains: dict[str, str] = Field(default_factory=dict)
    wants_proof: bool = True
    wants_equality_condition: bool = False
    # 方向输入阶段的候选元信息
    known_results: str = ""
    difference: str = ""
    verifiability: str = ""
    difficulty: str = ""
    recommended: bool = False
    rationale: str = ""
    derived_from: str = ""  # 如 "strict_version_of:clm-xxx"
    raw: str = ""


class SourcePolicy(str, Enum):
    """资料授权策略: 决定系统能用哪些资料 (P0-1 的两种盲测场景)。"""

    user_kb = "user_kb"        # 只用用户提供并授权的资料库
    autonomous = "autonomous"  # 只给方向: 授权系统自行检索
    both = "both"              # 用户资料库 + 自主检索, 合并去重


class RetrievalCoverage(BaseModel):
    """检索覆盖记录 —— "发现与审读资料"阶段的可审查对象。

    它回答的是"检索了哪些式、哪些库、什么时候、命中与入库多少、哪些只有摘要、
    哪些范围没覆盖、哪里失败了", 而不是"我引用了哪些文献"。
    """

    policy: SourcePolicy = SourcePolicy.user_kb
    executed: bool = False     # 是否真的执行过检索 (区分"检索失败"与"无命中")
    queries: list[str] = Field(default_factory=list)
    engines: list[str] = Field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    hits: int = 0
    ingested: int = 0
    duplicates: int = 0
    fulltext_available: int = 0
    abstract_only: int = 0
    uncovered: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    scope_note: str = ""
    # 引用来源: research_loop (研究循环已采信) / publication_layer (出版层新检索) /
    # not_executed (没检索)。区分这三者很重要 —— "没检索"不能当作"无先例"的依据,
    # 而"引文来自研究循环"也不该被误读成"出版层检索过且无新命中"。
    origin: str = ""

    def absorb(self, other: RetrievalCoverage) -> None:
        """合并另一轮检索的覆盖记录 (同一问题的多轮检索累加, 不覆盖历史)。"""
        self.executed = self.executed or other.executed
        self.queries = list(dict.fromkeys(self.queries + other.queries))
        self.engines = list(dict.fromkeys(self.engines + other.engines))
        self.hits += other.hits
        self.ingested += other.ingested
        self.duplicates += other.duplicates
        self.fulltext_available += other.fulltext_available
        self.abstract_only += other.abstract_only
        self.uncovered = list(dict.fromkeys(self.uncovered + other.uncovered))
        self.failures = list(dict.fromkeys(self.failures + other.failures))
        self.finished_at = other.finished_at or self.finished_at
        if other.scope_note and other.scope_note not in self.scope_note:
            self.scope_note = "; ".join(x for x in (self.scope_note, other.scope_note) if x)


class TaskKind(str, Enum):
    """问题契约判定的研究类型: 决定允许的方法与能给出的结论强度。"""

    mechanism = "mechanism"                # 机理/机制推导
    formal_proof = "formal_proof"          # 形式化证明
    empirical_causal = "empirical_causal"  # 经验因果估计 (必须真有数据)
    scenario = "scenario"                  # 条件性情景分析


class SourceSummary(BaseModel):
    """formulate() 的输入摘要: 只说明手里有什么, 不含任何结论。"""

    source_set_id: str = ""
    documents: int = 0
    fulltext_documents: int = 0
    has_theory_model: bool = False
    has_observational_data: bool = False
    theory_model_note: str = ""
    data_note: str = ""
    autonomous_retrieval: bool = False

    @property
    def has_sources(self) -> bool:
        return bool(self.source_set_id) or self.documents > 0 or self.autonomous_retrieval


class ResearchPath(BaseModel):
    """一条候选研究路径: 能产生什么结论、还缺什么。"""

    path_id: str = Field(default_factory=lambda: new_id("path"))
    task_kind: TaskKind = TaskKind.mechanism
    statement: str = ""
    produces: str = ""
    requires: list[str] = Field(default_factory=list)
    recommended: bool = False


class ProblemContract(BaseModel):
    """冻结的问题契约 (计划书 P0-2)。

    `clarification` 非空表示"先问一个问题, 不冻结"; `frozen_version > 0` 表示
    用户已确认。改题必须新版本或新问题, 不得静默替换。
    """

    goal: str = ""
    task_kind: TaskKind = TaskKind.mechanism
    objects: list[str] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict)
    scope: str = ""
    available_sources: list[str] = Field(default_factory=list)
    allowed_methods: list[str] = Field(default_factory=list)
    forbidden_substitutions: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    stop_criteria: list[str] = Field(default_factory=list)
    paths: list[ResearchPath] = Field(default_factory=list)
    clarification: str = ""
    basis: str = ""          # 判定依据: 让人看得出是规则、资料摘要还是模型定的
    frozen_version: int = 0

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarification)

    @property
    def frozen(self) -> bool:
        return self.frozen_version > 0

    def chosen_path(self) -> ResearchPath | None:
        if not self.paths:
            return None
        return next((p for p in self.paths if p.recommended), self.paths[0])


class ResearchSpec(BaseModel):
    """研究问题规格: "证明什么、在哪些条件下、需要什么强度的结论"。"""

    project_id: str
    problem_id: str = Field(default_factory=lambda: new_id("prob"))
    version: int = 1
    original_request: str = ""
    problem_statement: str = ""
    domain: str = ""
    # 资料源绑定 (计划书 §3 R0): 用户显式选择的资料库/数据源。
    # 为空时退回"按主题名找同名知识底座"的旧行为 (向后兼容)。
    source_set_id: str = ""
    source_set_kind: str = "kb"        # kb / files / dataset
    source_set_note: str = ""
    objects: list[str] = Field(default_factory=list)
    variables: list[str] = Field(default_factory=list)
    variable_domains: dict[str, str] = Field(default_factory=dict)
    quantifiers: str = ""  # 如 "forall"
    questions: list[ClaimQuestion] = Field(default_factory=list)
    # 方向输入 (模糊研究想法) 的解析结果与候选问题
    direction: str = ""
    dependent: str = ""
    independent: list[str] = Field(default_factory=list)
    research_type: str = ""  # monotonicity / bound / extremum / threshold / general
    contract: ProblemContract | None = None  # 用户确认后冻结的问题契约 (P0-2)
    source_policy: SourcePolicy = SourcePolicy.user_kb
    coverage: RetrievalCoverage | None = None  # 检索覆盖记录 (P0-1/§1 契约第 2 行)
    candidates: list[ClaimQuestion] = Field(default_factory=list)
    confirmed: bool = False
    selected_candidate_id: str = ""  # 已确认的候选 ID (稳定引用)
    user_constraints: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    scope_change_policy: str = "新命题与原命题并列保存, 不得静默替换"
    unknown_fields: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class Assumption(BaseModel):
    id: str = Field(default_factory=lambda: new_id("asm"))
    version: int = 1
    statement: str
    origin: Origin = Origin.user_assumption
    applicability: str = ""
    accepted: bool = True
    notes: str = ""


class Definition(BaseModel):
    id: str = Field(default_factory=lambda: new_id("def"))
    version: int = 1
    symbol: str
    type: str = ""
    unit: str = ""
    domain: str = ""
    statement: str = ""
    depends_on: list[str] = Field(default_factory=list)


from src.publication.references import PublicationMetadata


class SourceEvidence(PublicationMetadata):
    id: str = Field(default_factory=lambda: new_id("ev"))
    literature_id: str = ""
    version: int = 1
    title: str = ""
    authors: str = ""
    venue: str = ""
    doi: str = ""
    url: str = ""
    file_hash: str = ""
    location: str = ""  # 页码/节号/定理号 (人可读定位)
    excerpt: str = ""
    content_level: str = "unknown"
    source_set_id: str = ""
    retrieval_queries: list[str] = Field(default_factory=list)
    source_kind: SourceKind = SourceKind.other
    evidence_grade: EvidenceGrade = EvidenceGrade.unsupported
    credibility: Credibility = Credibility.low
    study_design: StudyDesign = StudyDesign.none
    population: str = ""
    region: str = ""
    year: str = ""
    effect_size: str = ""
    peer_reviewed: bool = False
    existence_verified: bool = False
    # ---- 三件事分开记录 (计划书 §6.2) ----
    # 1. 来源真实性 existence_verified / credibility / file_hash
    # 2. 内容支持关系: 默认 insufficient, 必须显式判定
    support: SupportKindOfEvidence = SupportKindOfEvidence.insufficient
    support_reason: str = ""
    support_evidence: str = ""  # 审查理由所依据的原文片段
    reviewer: str = ""          # 判定者 (rule / llm / human)
    # 3. 当前适用性
    reference_status: ReferenceStatus = ReferenceStatus.unchecked
    applicability_conditions: list[str] = Field(default_factory=list)
    # 定位与版本 (计划书 §6.1): 检索结果必须可回到原文
    source_id: str = ""         # KB doc_id 等权威来源 ID
    # 命题归属 (计划书 §3 R3): 该证据是为哪条命题召回的。
    # 逐命题缺口统计只读这个字段 + 版本化的 EvidenceLink, 不从全局证据列表推断。
    claim_id: str = ""
    chunk_id: str = ""          # 段落/分块稳定 ID
    page: int = 0
    char_start: int = -1        # 片段在原文中的字符范围
    char_end: int = -1
    text_hash: str = ""         # 原文片段内容 hash
    # 原始记录 hash: 保证多篇转引同一来源不被算成独立支持 (计划书 §6.2)
    original_record_hash: str = ""
    retrieved_at: str = ""
    contradicts: list[str] = Field(default_factory=list)  # 与之矛盾的证据 id
    notes: str = ""

    @property
    def content_supports(self) -> bool:
        """兼容视图: 是否建立了支持关系 (不再是一个可直接赋 True 的字段)。"""
        return self.support in SUPPORTING_RELATIONS

    @property
    def support_strength(self) -> int:
        if self.support == SupportKindOfEvidence.supports:
            return 2
        if self.support == SupportKindOfEvidence.partially_supports:
            return 1
        return 0

    @property
    def independence_key(self) -> str:
        """独立来源键: 同一原始记录/同一文件 hash 的多次转引视为同一来源。"""
        return self.original_record_hash or self.file_hash or self.doi.lower() or self.title.strip().lower()


class EvidenceLink(BaseModel):
    """命题—证据关系 (计划书 §4.1 EvidenceLink)。

    把"文献可信"与"支持本命题"分成两种关系; 关系强度与审查状态显式记录。
    """

    id: str = Field(default_factory=lambda: new_id("lnk"))
    claim_ref: ObjectRef
    source_ref: ObjectRef
    relation: SupportKindOfEvidence = SupportKindOfEvidence.insufficient
    excerpt: str = ""
    locator: str = ""
    condition_match: bool = False
    condition_notes: str = ""
    review_status: ValidationStatus = ValidationStatus.unchecked
    reviewer: str = ""
    created_at: str = Field(default_factory=utcnow)


class ResearchGap(BaseModel):
    """研究缺口 (计划书 §4.1): 让控制器基于缺口而不是阶段名推进。"""

    id: str = Field(default_factory=lambda: new_id("gap"))
    gap_type: GapType
    target_ref: ObjectRef
    statement: str = ""
    blocking: list[str] = Field(default_factory=list)
    resolving_actions: list[str] = Field(default_factory=list)
    resolution_criteria: str = ""
    created_at: str = Field(default_factory=utcnow)


class FailureKind(str, Enum):
    """失败类型 (P1-2): 不同失败必须引向不同下一动作, 而不是笼统一句"失败"。"""

    source_missing = "source_missing"              # 资料没找到 / 读取失败 / 缺全文
    model_refuted = "model_refuted"                # 候选模型被反例否定
    formalization_failed = "formalization_failed"  # 形式化或编码与原命题不一致
    tool_unknown = "tool_unknown"                  # 工具 unknown / 超时 / 不支持
    budget_exhausted = "budget_exhausted"          # 预算耗尽, 只能部分交付


class ResearchRoute(BaseModel):
    """研究路线 (计划书 §4.1 / §5.4): 能真正换路, 不重复尝试同一失败方法。"""

    id: str = Field(default_factory=lambda: new_id("rte"))
    version: int = 1
    goal: str = ""
    parent_id: str = ""
    target_ref: ObjectRef | None = None
    strategy: str = ""
    subgoals: list[str] = Field(default_factory=list)
    budget_actions: int = 10
    status: RouteStatus = RouteStatus.active
    failure_reason: str = ""
    recovery_condition: str = ""
    attempts: int = 0
    # P1-2 路线闭环: 目标缺口 → 预期可观察结果 → 动作 → 实际结果 → 下一判断
    target_gap: str = ""
    expected_observation: str = ""
    action_ref: str = ""
    actual_outcome: str = ""
    next_judgement: str = ""
    failure_kind: FailureKind | None = None
    substantive_change: str = ""   # 本次修订新在哪里 (新条件/新子命题/不同机制)
    created_at: str = Field(default_factory=utcnow)


class ModelVariable(BaseModel):
    """模型变量声明；未知的含义、单位和域保留为空。"""

    symbol: str = Field(min_length=1)
    meaning: str = ""
    unit: str = ""
    domain: str = ""


class ModelAssumption(BaseModel):
    """模型内的假设声明，或对已登记假设的引用。"""

    id: str = ""
    statement: str = ""
    origin: Origin | None = None
    source_ids: list[str] = Field(default_factory=list)
    applicability: str = ""

    @model_validator(mode="after")
    def _has_statement_or_reference(self) -> ModelAssumption:
        if not self.id.strip() and not self.statement.strip():
            raise ValueError("模型假设必须提供 statement 或已登记假设的 id")
        return self


class ResearchModel(BaseModel):
    """候选领域模型 (计划书 §4.1 / §5.2): 系统必须能够建模。

    natural_language 与 formal_encoding 必须对应; 任一变化都使相关验证过期。
    """

    id: str = Field(default_factory=lambda: new_id("mdl"))
    version: int = 1
    name: str = ""
    natural_language: str = ""
    formal_encoding: str = ""
    claim_id: str = ""
    variables: list[ModelVariable] = Field(default_factory=list)
    variable_domains: dict[str, str] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    mechanism: str = ""
    source_refs: list[str] = Field(default_factory=list)  # 来源: 文献/用户/系统提出
    origin: Origin = Origin.proposed
    assumptions: list[ModelAssumption] = Field(default_factory=list)
    applicability: str = ""
    why_chosen: str | bool = ""
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    open_conditions: list[str] = Field(default_factory=list)
    validation_problems: list[str] = Field(default_factory=list)
    subquestion: str = ""
    approximations: list[str] = Field(default_factory=list)
    boundaries: str = ""
    fidelity: str = ""          # 与问题的忠实度说明
    verified_scope: str = ""    # 该模型下结论的适用范围
    selected: bool = False
    created_at: str = Field(default_factory=utcnow)

    @model_validator(mode="before")
    @classmethod
    def _normalise_declarations(cls, value: Any) -> Any:
        """把既有字符串记录与团队结构化提案读成同一契约。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if isinstance(data.get("variables"), list):
            data["variables"] = [
                {"symbol": variable} if isinstance(variable, str) else variable
                for variable in data["variables"]
            ]
        if isinstance(data.get("assumptions"), list):
            assumptions = []
            for assumption in data["assumptions"]:
                if isinstance(assumption, str):
                    assumption = ({"id": assumption}
                                  if assumption.startswith(("asm-", "asmobj-", "assobj-"))
                                  else {"statement": assumption})
                assumptions.append(assumption)
            data["assumptions"] = assumptions
        if isinstance(data.get("source_ids"), list) and isinstance(data.get("source_refs", []), list):
            data["source_refs"] = [*data.get("source_refs", []), *data["source_ids"]]
        return data

    @model_validator(mode="after")
    def _complete_metadata(self) -> ResearchModel:
        for variable in self.variables:
            for field_name, index in (("domain", self.variable_domains), ("unit", self.units)):
                declared = getattr(variable, field_name)
                indexed = index.get(variable.symbol, "")
                if declared and indexed and declared != indexed:
                    raise ValueError(f"变量 {variable.symbol} 的 {field_name} 声明不一致")
                if declared:
                    index[variable.symbol] = declared
                elif indexed:
                    setattr(variable, field_name, indexed)
        sources = [*self.source_refs]
        for assumption in self.assumptions:
            sources.extend(assumption.source_ids)
        self.source_refs = list(dict.fromkeys(sources))
        return self


class ActionExecution(BaseModel):
    """动作执行账本记录 (计划书 §4.1 / §9.2): 分开"研究对象是什么"和"哪次执行做了什么"。"""

    action_id: str
    run_id: str = ""
    project_id: str = ""
    problem_id: str = ""
    branch_id: str = ""
    object_id: str = ""          # 本次动作实际作用的对象 (回填后可用于精确避让)
    action_type: str = ""
    status: str = "pending"      # pending / running / succeeded / failed / unknown
    request_hash: str = ""
    # 对象版本指纹 {object_id: version}; 内容指纹另存 request_hash
    input_versions: dict[str, int] = Field(default_factory=dict)
    idempotency_key: str = ""
    artifacts: list[str] = Field(default_factory=list)
    cost: int = 0
    detail: str = ""
    started_at: str = Field(default_factory=utcnow)
    finished_at: str = ""


class ProofStep(BaseModel):
    index: int
    statement: str
    justification: str = ""
    rule: str = ""
    uses_assumptions: list[str] = Field(default_factory=list)
    requires_conditions: list[str] = Field(default_factory=list)
    # P0-4: 步骤显式指向它服务的证明义务 (含版本); 不再靠"同一命题"猜归属
    obligation_ref: ObjectRef | None = None


class ProofAttempt(BaseModel):
    id: str = Field(default_factory=lambda: new_id("pf"))
    target_claim_id: str
    target_version: int = 1
    strategy: str = ""
    steps: list[ProofStep] = Field(default_factory=list)
    subgoals: list[str] = Field(default_factory=list)
    failed_at: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    budget_used: int = 0
    status: str = "in_progress"  # in_progress / complete / failed / abandoned


class ProofObligation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("obl"))
    version: int = 1
    statement: str
    kind: str = ""  # prove_inequality / equality_condition / prove_identity / refute / check_implication
    local_context: str = ""
    dependencies: list[str] = Field(default_factory=list)
    acceptance_method: str = ""  # sympy / z3 / lean / informal_review / rule / manual
    status: ObligationStatus = ObligationStatus.open
    claim_id: str = ""
    claim_version: int = 1  # 义务绑定到具体命题版本, 版本变化即失效
    required: bool = True   # 非必要义务不阻塞目标结论
    step_id: str = ""
    detail: str = ""
    # 计划书 §7.3: 统一验证结果语义 (不再只有 passed/failed 两种解释)
    validation_status: ValidationStatus = ValidationStatus.unchecked
    support_kind: SupportKind = SupportKind.none
    counterexample: dict[str, Any] = Field(default_factory=dict)
    coverage: Coverage = Coverage.target

    @property
    def is_scientific_result(self) -> bool:
        """运行/能力问题不是科学结论, 不得当作"命题为假"。"""
        return self.validation_status not in NON_SCIENTIFIC_STATUSES


class Claim(BaseModel):
    id: str = Field(default_factory=lambda: new_id("clm"))
    version: int = 1
    statement: str
    # 所属研究问题 (计划书 R6: 同一项目可研究多个问题, 对象必须归属到具体问题)。
    # 旧数据为空 → 归属当前正在研究的问题 (由工作台过滤逻辑判定, 不猜测)。
    problem_id: str = ""
    claim_type: ClaimType = ClaimType.definitional
    strategy: str = ""  # 推理方法与命题语义类型是两个维度。
    claim_type_input: str = ""  # 兼容旧记录时保留原始误填值以便审计。
    # 应用/因果: 作用对象与范围
    scope_population: str = ""
    scope_region: str = ""
    scope_period: str = ""
    study: StudyPlan = Field(default_factory=StudyPlan)
    effect_estimate: dict[str, Any] = Field(default_factory=dict)
    evidence_grade: EvidenceGrade = EvidenceGrade.unsupported
    argument: dict[str, Any] = Field(default_factory=dict)  # warrant/qualifier/rebuttals
    # 结构化形式 (可核验的数学片段), 供验证器使用
    lhs: str = ""
    rhs: str = ""
    relation: Relation = Relation.custom
    # 单调性命题: expr 关于 wrt 的单调/影响方向
    expr: str = ""
    wrt: str = ""
    direction: str = ""
    variables: list[str] = Field(default_factory=list)
    variable_domains: dict[str, str] = Field(default_factory=dict)
    # ---- 设计/计数类存在性命题 (2-(v,k,λ)) ----
    # 这几项是**判定输入的结构化副本**: 验证记录必须与它们逐项一致才被接受
    # (对齐检查, 见 acceptance._aligned), 因此改参数 = 换命题 = 原验证失效。
    # 数值一律来自题面抽取, 不在此处写任何题目常量。
    design_v: int | None = None
    design_k: int | None = None
    design_lambda: int | None = None
    design_b: int | None = None
    design_r: int | None = None
    design_verdict: str = ""      # nonexistent / necessary_met / insufficient
    assumption_ids: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)  # 依赖的 claim id
    origin: Origin = Origin.proposed
    status: ClaimStatus = ClaimStatus.proposed
    assurance: Assurance = Assurance.unverified
    verification_scope: VerificationScope | None = None
    novelty_status: NoveltyStatus = NoveltyStatus.unchecked
    obligations: list[str] = Field(default_factory=list)  # obligation id
    # ---- 计划书 §4.2: 结论状态与验证维度分开 ----
    support_kind: SupportKind = SupportKind.none
    coverage: Coverage = Coverage.step
    validation_status: ValidationStatus = ValidationStatus.unchecked
    model_ref: ObjectRef | None = None  # 结论所依据的模型版本
    route_id: str = ""
    # 该结论被验证时所用的对象版本闭包 (假设/模型), 变化后即失效
    verification_closure: dict[str, int] = Field(default_factory=dict)
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalise_legacy_strategy_type(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("claim_type") != "derivation":
            return value
        data = dict(value)
        data["claim_type_input"] = data.get("claim_type_input") or "derivation"
        data["claim_type"] = ClaimType.descriptive.value
        data["strategy"] = data.get("strategy") or "derivation"
        # This is classification compatibility only, never evidence of proof.
        return data

    @model_validator(mode="after")
    def _guard_supported(self) -> Claim:
        # 局部式子被验证不得把整体升级为完整验证
        if (self.status == ClaimStatus.supported
                and self.verification_scope == VerificationScope.step
                and ASSURANCE_RANK[self.assurance] >= ASSURANCE_RANK[Assurance.symbolic_checked]):
            raise ValueError(
                f"claim {self.id}: 仅局部步骤验证不足以将整体标记为 {self.assurance.value}"
            )
        # 存在 counterexample_found 记录时不得同时标记为 supported
        if (self.status == ClaimStatus.supported
                and self.validation_status == ValidationStatus.counterexample_found):
            raise ValueError(f"claim {self.id}: 已发现反例, 不得标记为 supported")
        # supported 必须是 target 覆盖, 且支持方式明确
        if self.status == ClaimStatus.supported:
            if self.coverage != Coverage.target:
                raise ValueError(f"claim {self.id}: supported 需 coverage=target (义务需全部关闭)")
            if self.support_kind == SupportKind.none:
                raise ValueError(f"claim {self.id}: supported 需声明 support_kind")
            if self.validation_status != ValidationStatus.verified:
                raise ValueError(f"claim {self.id}: supported 需 validation_status=verified")
        return self


class VerificationRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ver"))
    tool: str = ""
    tool_version: str = ""
    input_hash: str = ""
    claim_id: str = ""
    claim_version: int = 1
    # P0-4: 显式指向被验证的义务 (含版本), 取代"证书里出现义务 ID"的隐式关联
    obligation_ref: ObjectRef | None = None
    link_status: str = ""   # confirmed / unconfirmed (旧数据隐式关联, 需人工确认)
    assumption_ids: list[str] = Field(default_factory=list)
    scope: VerificationScope = VerificationScope.target
    arguments: dict[str, Any] = Field(default_factory=dict)
    # 受检公式、前提与输入/输出摘要: 让人能复核"到底验了什么"
    checked_formula: str = ""
    premises: list[str] = Field(default_factory=list)
    output_digest: str = ""
    raw_output: str = ""
    status: str = ""  # 见 src.verification.schemas.VerificationStatus
    validation_status: ValidationStatus = ValidationStatus.unchecked
    support_kind: SupportKind = SupportKind.none
    # 验证绑定的是具体陈述与依赖版本闭包 (计划书 §4.1): {object_id: version}
    verification_closure: dict[str, int] = Field(default_factory=dict)
    certificate: str = ""
    counterexample: dict[str, Any] = Field(default_factory=dict)
    stale: bool = False
    created_at: str = Field(default_factory=utcnow)


class ResearchAction(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    action_type: ActionType
    object_id: str = ""
    preconditions: list[str] = Field(default_factory=list)
    input_versions: dict[str, int] = Field(default_factory=dict)
    target_gap: str = ""
    estimated_cost: int = 1
    termination_condition: str = ""
    reason: str = ""


class NoveltyComparisonRow(BaseModel):
    result: str
    premises: str = ""
    conclusion: str = ""
    applicability: str = ""
    strength: str = ""
    equality_conditions: str = ""
    method: str = ""
    difference: str = ""
    # P1-1: 先前结果的**定位** (页/节), 让"可比/不可比"能回到原文核对
    locator: str = ""


class NoveltyRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("nov"))
    claim_id: str = ""
    claim_version: int = 1
    status: NoveltyStatus = NoveltyStatus.unchecked
    rows: list[NoveltyComparisonRow] = Field(default_factory=list)
    search_date: str = ""
    queries: list[str] = Field(default_factory=list)
    covered_sources: list[str] = Field(default_factory=list)
    # P1-1: 两类范围必须分开记录 —— 用户授权的**研究证据库** vs 新颖性**检索范围**,
    # 否则会把"我只查了这个库"说成"整个领域没有先例"。
    evidence_scope: list[str] = Field(default_factory=list)
    retrieval_scope: dict[str, Any] = Field(default_factory=dict)
    inaccessible: list[str] = Field(default_factory=list)
    conclusion: str = ""
    # R4: 逐项差异分析的汇总 (各关系计数/是否可比), 供工作台与交付物展示
    difference_summary: dict[str, Any] = Field(default_factory=dict)
    # 有界表述: 无等价结果时不得宣称"世界首次"
    bounded_statement: str = "在所检索范围内尚未发现等价结果"


class ResearchSnapshot(BaseModel):
    project_id: str
    # R6: 快照必须自带**问题与运行身份**, 而不是靠调用方"取第一个规格"猜。
    # 旧快照缺失时为空串, 由迁移回填 (不猜测归属)。
    problem_id: str = ""
    run_id: str = ""
    branch_id: str = ""
    snapshot_id: str = Field(default_factory=lambda: new_id("snap"))
    created_at: str = Field(default_factory=utcnow)
    spec_version: int = 1
    assumptions: list[Assumption] = Field(default_factory=list)
    definitions: list[Definition] = Field(default_factory=list)
    models: list[ResearchModel] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    obligations: list[ProofObligation] = Field(default_factory=list)
    attempts: list[ProofAttempt] = Field(default_factory=list)
    evidence: list[SourceEvidence] = Field(default_factory=list)
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    verifications: list[VerificationRecord] = Field(default_factory=list)
    routes: list[ResearchRoute] = Field(default_factory=list)
    gaps: list[ResearchGap] = Field(default_factory=list)
    dependency_edges: list[tuple[str, str]] = Field(default_factory=list)  # (from -> to)
    novelty: list[NoveltyRecord] = Field(default_factory=list)
    experiment_specs: list[dict] = Field(default_factory=list)
    writing_map: dict[str, str] = Field(default_factory=dict)  # claim_id -> 正文锚点
    # 冻结时该结论所占用的有效验证记录 (计划书 §4.3-6: 只检查本次快照引用的记录)
    verification_index: dict[str, list[str]] = Field(default_factory=dict)


def hash_payload(payload: Any) -> str:
    """稳定的输入哈希, 用于验证记录与幂等键。"""
    import json

    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]



# 工具 -> 支持方式 (计划书 §4.2): 描述"靠什么被支持", 不做跨类型强弱比较。
TOOL_SUPPORT_KIND = {
    "sympy": SupportKind.symbolic_check,
    "z3": SupportKind.constraint_solve,
    "lean": SupportKind.formal_proof,
    "stats": SupportKind.statistical_estimate,
    "informal_review": SupportKind.informal_argument,
}

# 工具 -> 覆盖范围: 局部式子被验证不等于整体目标被证明。
# sympy/z3 处理的是"某个编码片段", 因而默认只覆盖 step;
# 只有 obligations 全部关闭时, 才由状态规则把结论覆盖范围提升到 target。
TOOL_COVERAGE = {
    "sympy": Coverage.step,
    "z3": Coverage.step,
    "stats": Coverage.target,   # 统计估计针对的是目标效应本身
    "lean": Coverage.target,    # 形式化目标是整体声明
    "informal_review": Coverage.subgoal,
}


def support_kind_for_tool(tool: str) -> SupportKind:
    return TOOL_SUPPORT_KIND.get(tool, SupportKind.none)


def coverage_for_tool(tool: str) -> Coverage:
    return TOOL_COVERAGE.get(tool, Coverage.step)
