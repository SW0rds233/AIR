from __future__ import annotations

"""可审查的论证链与写作缺口回流 (计划书 §3 R4)。

计划书指出的两个缺陷:

1. 研究稿的"证明与验证"段只列证书摘要, 没有把**关键推导步骤与依赖**按论文可
   审查的形式连接起来 —— 读者无法从断言追到它依据的步骤、义务、证据与验证输入;
2. 写作发现缺口时应当**回到研究循环**新增义务, 而不是用润色掩盖。

本模块提供两件事:

- `build_argument(snapshot, claim)` → `Argument`: 把一条结论的论证链显式化 ——
  条件 (适用域/读到的定义) → 关键步骤 (含依赖与所用条件) → 义务与验证输入 →
  反例/未决项 → 证据引用。每一步都能反查到具体对象 ID;
- `writing_gaps(snapshot)` → 待回流的研究缺口: 缺少已完成推导尝试、缺少验证输入、
  引用了不存在的证据、未关闭义务、"声称支持但没有可用验证记录"等。这些缺口由
  研究循环转成义务 (而不是在正文里写一句"略")。

硬约束
------
- 本模块**不改写任何结论状态**, 也不生成正文断言;
- 缺口只在有真实依据时提出 (每条都指向具体对象), 不做风格性挑刺;
- `Argument.render()` 输出的是"可核查事实", 不声称结论成立。
"""

from dataclasses import dataclass, field

from src.research.schemas import (
    Claim,
    ClaimStatus,
    ObligationStatus,
    ResearchSnapshot,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)

# 非科学性失败: 这些状态不能当作"已验证", 也不能当作"命题为假"
_NON_SCIENTIFIC = (ValidationStatus.timeout, ValidationStatus.unavailable,
                   ValidationStatus.execution_error, ValidationStatus.unknown,
                   ValidationStatus.unchecked)

_SUPPORT_CN = {
    SupportKind.none: "未声明", SupportKind.textual_support: "原文支持",
    SupportKind.informal_argument: "非形式化论证", SupportKind.symbolic_check: "符号检查",
    SupportKind.theorem_application: "具名定理应用 (参数经机器复核)",
    SupportKind.constraint_solve: "约束求解", SupportKind.formal_proof: "形式化证明",
    SupportKind.numerical_test: "数值测试", SupportKind.statistical_estimate: "统计估计",
}


@dataclass
class Step:
    """论证链中的一步 (可反查对象)。"""

    index: int
    statement: str
    rule: str = ""
    justification: str = ""
    requires_conditions: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    obligation_id: str = ""
    obligation_status: str = ""
    verification_id: str = ""
    verification_tool: str = ""
    verification_status: str = ""
    verification_link: str = ""   # confirmed / unconfirmed (旧数据隐式关联)

    def describe(self) -> str:
        bits = [f"{self.index}. {self.statement}"]
        if self.rule:
            bits.append(f"[规则 {self.rule}]")
        if self.justification:
            bits.append(f"(依据: {self.justification})")
        if self.requires_conditions:
            bits.append("要求条件: " + "、".join(self.requires_conditions))
        if self.depends_on:
            bits.append("依赖 " + ", ".join(self.depends_on))
        if self.obligation_id:
            bits.append(f"义务 {self.obligation_id}({self.obligation_status})")
        if self.verification_id:
            link = "(关联待确认)" if self.verification_link == "unconfirmed" else ""
            bits.append(f"验证 {self.verification_id}/{self.verification_tool}"
                        f"({self.verification_status}){link}")
        return " ".join(bits)

    def to_dict(self) -> dict:
        return {
            "index": self.index, "statement": self.statement, "rule": self.rule,
            "justification": self.justification,
            "requires_conditions": list(self.requires_conditions),
            "depends_on": list(self.depends_on),
            "obligation_id": self.obligation_id,
            "obligation_status": self.obligation_status,
            "verification_id": self.verification_id,
            "verification_tool": self.verification_tool,
            "verification_status": self.verification_status,
            "verification_link": self.verification_link,
        }


@dataclass
class Argument:
    """一条结论的完整论证链 (步骤 + 依赖 + 义务 + 验证输入 + 未决项)。"""

    claim_id: str
    claim_version: int = 1
    status: str = ""
    conditions: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    evidence_refs: list[dict] = field(default_factory=list)
    model_ref: dict = field(default_factory=dict)
    counterexamples: list[str] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def has_chain(self) -> bool:
        return bool(self.steps)

    def render(self) -> str:
        """Markdown 片段: 只列**可核查事实**, 不替读者下结论。"""
        lines = [f"**论证链 ({self.claim_id}@v{self.claim_version}, 状态 {self.status})**", ""]
        if self.conditions:
            lines.append("- 适用条件: " + "、".join(self.conditions))
        if self.model_ref:
            lines.append(f"- 模型依据: {self.model_ref.get('id', '')}"
                         f"@v{self.model_ref.get('version', '')}")
        if self.steps:
            lines.append("- 步骤:")
            for step in self.steps:
                lines.append(f"    - {step.describe()}")
        else:
            lines.append("- 步骤: 无已记录的推导步骤 (无法逐步复核)")
        if self.evidence_refs:
            lines.append("- 证据引用:")
            for ref in self.evidence_refs:
                lines.append(f"    - [{ref.get('id', '')}] {ref.get('title', '')} "
                             f"@ {ref.get('locator') or '无定位'} "
                             f"关系={ref.get('relation', '')}")
        if self.counterexamples:
            lines.append("- 反例: " + "; ".join(self.counterexamples))
        if self.open_items:
            lines.append("- 未决项:")
            for item in self.open_items:
                lines.append(f"    - {item}")
        if self.caveats:
            lines.append("- 说明: " + "; ".join(self.caveats))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id, "claim_version": self.claim_version,
            "status": self.status, "conditions": list(self.conditions),
            "steps": [s.to_dict() for s in self.steps],
            "evidence_refs": list(self.evidence_refs),
            "model_ref": dict(self.model_ref),
            "counterexamples": list(self.counterexamples),
            "open_items": list(self.open_items), "caveats": list(self.caveats),
            "has_chain": self.has_chain,
        }


def _valid_records(snapshot: ResearchSnapshot, claim_id: str) -> list[VerificationRecord]:
    """可用验证记录: 排除 stale 与运行/能力类失败 (它们不是验证结论)。"""
    out = []
    for record in snapshot.verifications:
        if record.claim_id != claim_id or record.stale:
            continue
        if record.validation_status in _NON_SCIENTIFIC:
            continue
        out.append(record)
    return out


def _has_certificate_chain(records: list[VerificationRecord]) -> bool:
    """验证记录里是否有**可逐条反查的判定链** (设计证书)。

    `certificate_of` 读取 `record.arguments["design_report"]`, 渲染出来的链形如
    "判定对象 → 每条必要条件 (依据定理/输入/结论)", 它本身就是可展示的推导步骤。
    因此有它的结论不该被判定为"没有推导步骤"。
    """
    from src.research.design_feasibility import certificate_of

    for record in records:
        certificate = certificate_of(record)
        if not certificate:
            continue
        if certificate.get("evidence") or certificate.get("design") \
                or certificate.get("counts"):
            return True
    return False


def build_argument(snapshot: ResearchSnapshot, claim: Claim) -> Argument:
    """把一条结论的论证链显式化 (可逐步反查)。"""
    argument = Argument(claim_id=claim.id, claim_version=claim.version,
                        status=claim.status.value)
    argument.conditions = [f"{v} ∈ {d}" for v, d in (claim.variable_domains or {}).items()]
    if claim.claim_type.value == "causal":
        scope = "/".join(x for x in (claim.scope_population, claim.scope_region,
                                     claim.scope_period) if x)
        if scope:
            argument.conditions.append(f"适用 {scope}")
    if claim.model_ref is not None:
        argument.model_ref = {"id": claim.model_ref.id, "version": claim.model_ref.version}

    attempts = [a for a in snapshot.attempts if a.target_claim_id == claim.id]
    records = _valid_records(snapshot, claim.id)
    by_claim_obligations = [o for o in snapshot.obligations if o.claim_id == claim.id]

    # 步骤: 来自推导尝试; 每一步挂上它能对上的义务与验证记录
    index = 0
    unconfirmed: list[str] = []
    for attempt in attempts:
        for step in attempt.steps:
            index += 1
            obligation = _match_obligation(step, by_claim_obligations)
            record, link_status = _match_record(obligation, records, claim)
            if link_status == "unconfirmed":
                unconfirmed.append(
                    f"步骤 {index} 的验证记录 {record.id} 只有旧数据的隐式关联 "
                    f"(证书里出现义务 ID), 关联待人工确认")
            argument.steps.append(Step(
                index=index,
                statement=step.statement,
                rule=step.rule,
                justification=step.justification,
                requires_conditions=list(step.requires_conditions),
                depends_on=list(claim.dependencies),
                obligation_id=obligation.id if obligation else "",
                obligation_status=obligation.status.value if obligation else "",
                verification_id=record.id if record else "",
                verification_tool=record.tool if record else "",
                verification_status=(record.validation_status.value if record else ""),
                verification_link=link_status,
            ))

    # 证据引用: 只列属于本命题的证据 (逐命题归属)
    links = [lk for lk in snapshot.evidence_links if lk.claim_ref.id == claim.id]
    linked_ids = {lk.source_ref.id for lk in links}
    for item in snapshot.evidence:
        if item.claim_id != claim.id and item.id not in linked_ids:
            continue
        relation = item.support
        for link in links:
            if link.source_ref.id == item.id:
                relation = link.relation
                break
        argument.evidence_refs.append({
            "id": item.id, "title": item.title or item.literature_id,
            "locator": item.location, "relation": relation.value,
            "existence_verified": bool(item.existence_verified),
        })

    for obligation in by_claim_obligations:
        if obligation.counterexample:
            argument.counterexamples.append(
                ", ".join(f"{k}={v}" for k, v in obligation.counterexample.items()))
    for record in records:
        if record.counterexample:
            argument.counterexamples.append(
                ", ".join(f"{k}={v}" for k, v in record.counterexample.items()))

    for obligation in by_claim_obligations:
        if obligation.status != ObligationStatus.closed:
            argument.open_items.append(
                f"义务 {obligation.id} ({obligation.kind}, {obligation.status.value}): "
                f"{obligation.statement[:80]}")

    # 声明与证据一致: 结论声称支持但缺少可用验证记录时, 必须在论证链里点明
    if claim.status == ClaimStatus.supported and not records:
        argument.caveats.append("该结论标记为 supported, 但快照中没有可用的验证记录")
    if argument.has_chain and not records:
        argument.caveats.append("步骤仅为推导计划, 尚未获得工具核验")
    # P0-4: 有义务却对不上验证输入的步骤必须点出来, 不能被"链看起来完整"掩盖
    unlinked = [s.index for s in argument.steps if s.obligation_id and not s.verification_id]
    if unlinked:
        argument.caveats.append(
            "步骤 " + ", ".join(str(i) for i in unlinked)
            + " 对不上可用的验证输入: 只算推导计划, 不构成核验")
    # P0-4: 旧数据的隐式验证关联一律标"待确认", 不当作已验证
    argument.caveats.extend(unconfirmed)
    if unconfirmed:
        argument.caveats.append(
            f"有 {len(unconfirmed)} 处验证关联来自旧数据的隐式匹配, 需人工确认后才能视为依据")
    return argument


def _match_obligation(step, obligations: list) -> object | None:
    """把一步推导对应到义务: 显式 `obligation_ref` 优先, 其次 step_id, 最后文本包含。"""
    ref = getattr(step, "obligation_ref", None)
    if ref is not None and getattr(ref, "id", ""):
        for obligation in obligations:
            if obligation.id == ref.id:
                return obligation
        return None
    for obligation in obligations:
        if obligation.step_id and obligation.step_id == str(step.index):
            return obligation
    text = (step.statement or "")[:20]
    if text:
        for obligation in obligations:
            if text and text in (obligation.statement or ""):
                return obligation
    return None


def _record_is_for(record: VerificationRecord, obligation_id: str) -> bool:
    """旧数据: 证书/原始输出/参数里出现义务 ID —— 只能算**隐式**关联。"""
    if not obligation_id:
        return False
    haystack = " ".join([record.certificate or "", record.raw_output or "",
                         str(record.arguments or {})])
    return obligation_id in haystack


def _match_record(obligation, records: list[VerificationRecord],
                  claim: Claim | None = None) -> tuple[object | None, str]:
    """把义务对应到验证记录, 并说明关联是否**已确认** (P0-4)。

    1. 记录带 `obligation_ref` 且 id/版本一致 → confirmed;
    2. 旧数据里证书出现义务 ID → 记录为 `unconfirmed` (供人工确认, 不暗中补边);
    3. 其余一律不匹配 —— **不再**回退到"同一命题、同一版本的验证记录",
       那会把"有关的验证"展示成"这一步的依据"。
    """
    if obligation is None:
        return None, ""
    for record in records:
        ref = getattr(record, "obligation_ref", None)
        if ref is not None and getattr(ref, "id", "") == obligation.id:
            version = getattr(ref, "version", 0) or 0
            if not version or version == getattr(obligation, "version", 1):
                return record, "confirmed"
            continue
        if _record_is_for(record, obligation.id):
            return record, "unconfirmed"
    return None, ""


# ----------------------------------------------------------------------
# 写作缺口 → 回流研究循环
# ----------------------------------------------------------------------
@dataclass
class WritingGap:
    """写作阶段发现的缺口 (必须回流成义务, 而不是在正文里掩盖)。

    `severity` 同时说明**缺口能否由研究循环自己消解**:

    - `blocking`: 当前研究能力有办法把它关掉 (补推导步骤、补验证输入), 因此回流后
      应当**重开研究循环**去处理;
    - `advisory`: 只能由人确认或补资料才能消除 (悬空引用、缺不确定性区间等)。
      把这类缺口标成阻塞只会让结论永远无法交付, 而不会带来任何新信息 ——
      因此它们作为"已知保留意见"随交付物呈现, 同时**由验收门槛独立拦下**
      (见 `research/acceptance.py`: supported 无验证记录、因果结论缺 CI 等)。
    """

    claim_id: str
    kind: str
    statement: str
    detail: str = ""
    severity: str = "blocking"     # blocking / advisory

    def to_dict(self) -> dict:
        return {"claim_id": self.claim_id, "kind": self.kind,
                "statement": self.statement, "detail": self.detail,
                "severity": self.severity}


def writing_gaps(snapshot: ResearchSnapshot) -> list[WritingGap]:
    """检查正文会暴露的缺口: 每条都指向具体对象与缺什么。

    只收录**有依据**的缺口:
    - supported 但没有可用验证记录 (声称与证据不一致);
    - supported 但没有任何推导尝试 (无法逐步复核);
    - 试验证等级声称"形式化/符号"但验证记录覆盖范围只有 step;
    - 证据引用指向快照里不存在的来源 (悬空引用);
    - 因果结论缺少效应估计或区间;
    - 实验建议缺少判据 (decision_rule)。
    """
    gaps: list[WritingGap] = []
    evidence_ids = {e.id for e in snapshot.evidence}

    for claim in snapshot.claims:
        records = _valid_records(snapshot, claim.id)
        attempts = [a for a in snapshot.attempts if a.target_claim_id == claim.id]
        if claim.status == ClaimStatus.supported and not records:
            gaps.append(WritingGap(
                claim_id=claim.id, kind="missing_verification_input",
                statement=f"结论 {claim.id} 声称成立但没有可用验证记录",
                detail="正文无法给出可核查的验证输入; 需补充验证或降级表述"))
        # "没有可展示的推导步骤"只在**既无推导步骤、也无证书判定链**时才算缺口。
        # 计数/组合设计类结论的推导链存放在验证记录的设计证书里 (定理 → 条件 → 输入
        # → 结论逐条可反查), 它比 `attempts.steps` 更可核查; 只看 attempts 会把这类
        # 结论误判成"没有推导", 进而回流出一条无法消解的阻塞义务, 让运行以
        # "有一条未关闭义务"收尾 (现场: synthesize_results 之后卡住, 无法成文)。
        if claim.status == ClaimStatus.supported and not any(a.steps for a in attempts) \
                and not _has_certificate_chain(records):
            gaps.append(WritingGap(
                claim_id=claim.id, kind="missing_argument_chain",
                statement=f"结论 {claim.id} 没有可展示的推导步骤",
                detail="正文无法逐步复核; 需生成推导步骤与反方审查"))
        for record in records:
            if (claim.assurance.value in ("formally_checked", "solver_checked")
                    and record.scope.value == "step"):
                gaps.append(WritingGap(
                    claim_id=claim.id, kind="scope_mismatch",
                    statement=f"结论 {claim.id} 的验证等级为 {claim.assurance.value}, "
                              f"但记录只覆盖局部步骤",
                    detail="局部验证不得写成整体证明; 需关闭其余义务或降低等级",
                    severity="advisory"))
        for link in snapshot.evidence_links:
            if link.claim_ref.id == claim.id and link.source_ref.id not in evidence_ids:
                gaps.append(WritingGap(
                    claim_id=claim.id, kind="dangling_evidence_ref",
                    statement=f"结论 {claim.id} 引用了不存在的证据 "
                              f"{link.source_ref.id}",
                    detail="悬空引用会让正文无法回查原文; 需补齐证据或删除引用",
                    severity="advisory"))
        if claim.claim_type.value == "causal" and claim.status == ClaimStatus.supported:
            estimate = claim.effect_estimate or {}
            if estimate.get("ci_low") is None or estimate.get("ci_high") is None:
                gaps.append(WritingGap(
                    claim_id=claim.id, kind="missing_uncertainty",
                    statement=f"因果结论 {claim.id} 缺少不确定性区间",
                    detail="正文不得只报点估计; 需补区间或明确标注为不完整",
                    severity="advisory"))

    for spec in snapshot.experiment_specs:
        if not str(spec.get("decision_rule", "")).strip():
            gaps.append(WritingGap(
                claim_id=str(spec.get("claim_id", "")), kind="suggestion_without_rule",
                statement=f"实验建议 {spec.get('id')} 没有判据",
                detail="没有判据的建议无法改变判断; 需补 decision_rule",
                severity="advisory"))
    for spec in snapshot.experiment_specs:
        if str(spec.get("execution_status", "proposed")) in ("executed", "analyzed") \
                and not spec.get("artifacts"):
            gaps.append(WritingGap(
                claim_id=str(spec.get("claim_id", "")), kind="execution_without_artifact",
                statement=f"实验建议 {spec.get('id')} 标记为已执行但没有产物",
                detail="不得声称已执行; 需补产物或改回草案",
                severity="advisory"))

    # 去重 (同一 claim + kind + statement 只留一条)
    seen: set[tuple[str, str, str]] = set()
    out: list[WritingGap] = []
    for gap in gaps:
        key = (gap.claim_id, gap.kind, gap.statement)
        if key in seen:
            continue
        seen.add(key)
        out.append(gap)
    return out


__all__ = [
    "Argument",
    "Step",
    "WritingGap",
    "build_argument",
    "writing_gaps",
]
