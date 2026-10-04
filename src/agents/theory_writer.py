from __future__ import annotations

"""理论论文写作 (P3)。

从**冻结的研究快照**生成研究稿: 只允许表达已确定结论, 不得新增未经验证的主要结论,
不得修改命题条件或验证状态。输出 claim_id → 正文位置 的映射, 供一致性核验。

计划书 §7.1 / §14 的两条硬约束:
- 正文必须区分**验证记录**与**证明计划**: 只有存在有效验证记录时才写"证明",
  仅有推导计划时必须显式标注"计划/待核验", 不得包装成已完成证明;
- 写作发现推导漏洞时应退回研究阶段新增义务, 而不是用润色掩盖 —— 因此这里对
  未关闭义务只如实列出, 不生成断言式表述。
"""

from typing import Any

from src.rag.theory_render import Block, Manuscript
from src.research.argument import writing_gaps
from src.research.design_feasibility import (
    certificate_of,
    is_design_claim,
    render_certificate_chain,
)
from src.research.schemas import (
    Claim,
    ClaimStatus,
    ClaimType,
    ObligationStatus,
    ResearchSnapshot,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)

_SUPPORT_CN = {
    SupportKind.none: "未声明",
    SupportKind.textual_support: "原文支持",
    SupportKind.informal_argument: "非形式化论证",
    SupportKind.theorem_application: "具名定理应用 (参数经机器复核)",
    SupportKind.symbolic_check: "符号检查",
    SupportKind.constraint_solve: "约束求解",
    SupportKind.formal_proof: "形式化证明",
    SupportKind.numerical_test: "数值测试",
    SupportKind.statistical_estimate: "统计估计",
}


def _math(text: str) -> str:
    """把 Python 风格表达式转为 $...$ LaTeX 数学。"""
    expr = (text or "").replace("**", "^")
    return f"${expr}$"


def _claim_kind(claim: Claim) -> str:
    if claim.origin.value == "derived" and claim.dependencies:
        return "corollary"
    return "theorem"


_DIRECTION_CN = {
    "increasing": "严格单调递增",
    "decreasing": "严格单调递减",
    "nondecreasing": "单调非减",
    "nonincreasing": "单调非增",
}


def _claim_body(claim: Claim) -> str:
    # 等号条件/派生结论: 直接展示其陈述, 避免误显示为原不等式
    if claim.id.startswith("eq-") or "等号条件" in (claim.statement or ""):
        return claim.statement
    if claim.claim_type == ClaimType.causal:
        estimate = claim.effect_estimate or {}
        est = estimate.get("estimate")
        if est is not None:
            lo, hi = estimate.get("ci_low"), estimate.get("ci_high")
            return (f"在声明设计与假设下, 「{claim.study.treatment}」对「{claim.study.outcome}」"
                    f"的效应估计为 {est:.4g} (95% CI [{lo:.4g}, {hi:.4g}], "
                    f"设计={claim.study.design.value}, 证据等级={claim.evidence_grade.value})")
        return claim.statement
    if claim.expr and claim.wrt:
        label = _DIRECTION_CN.get(claim.direction, "单调性待定")
        return f"在声明域与假设下, {_math(claim.expr)} 关于 {_math(claim.wrt)} {label}"
    rel = claim.relation.value
    if rel in (">=", "<=", ">", "<") and claim.lhs and claim.rhs:
        return f"在声明域与假设下, {_math(claim.lhs)} {rel} {_math(claim.rhs)}"
    return claim.statement


def _condition_text(claim: Claim) -> str:
    parts = [f"{var} ∈ {domain}" for var, domain in (claim.variable_domains or {}).items()]
    if claim.claim_type == ClaimType.causal:
        scope = "/".join(x for x in (claim.scope_population, claim.scope_region,
                                     claim.scope_period) if x)
        if scope:
            parts.append(f"适用 {scope}")
        handling = claim.study.confounder_handling or "、".join(claim.study.confounders or [])
        if handling:
            parts.append(f"混淆处理: {handling}")
    return "、".join(parts)


def _valid_records(snapshot: ResearchSnapshot, claim_id: str) -> list[VerificationRecord]:
    """该结论可用的**有效**验证记录 (排除 stale 与不可结论状态)。"""
    out = []
    for record in snapshot.verifications:
        if record.claim_id != claim_id or record.stale:
            continue
        if record.validation_status in (ValidationStatus.timeout,
                                        ValidationStatus.unavailable,
                                        ValidationStatus.execution_error,
                                        ValidationStatus.unknown,
                                        ValidationStatus.unchecked):
            continue
        out.append(record)
    return out


def _closed_obligations(snapshot: ResearchSnapshot, claim_id: str) -> list:
    return [o for o in snapshot.obligations
            if o.claim_id == claim_id and o.status == ObligationStatus.closed]


def _design_record(snapshot: ResearchSnapshot, claim: Claim):
    """该命题的**设计判定证书记录** (具名定理应用), 无则返回 None。"""
    if not is_design_claim(claim):
        return None
    for record in snapshot.verifications:
        if record.claim_id != claim.id or record.stale:
            continue
        if certificate_of(record):
            return record
    return None


def _proof_or_plan(claim: Claim, snapshot: ResearchSnapshot) -> str:
    """区分"已验证证明"与"证明计划" (计划书 §7.1), 并给出可逐步复核的论证链。

    R4: 只有存在与该结论对齐的有效验证记录时才写"证明"; 无论哪种情况都附上
    `argument.build_argument` 生成的论证链 —— 步骤、依赖、义务与验证输入可逐条
    反查, 而不是只给一段证书摘要。

    S1: 具名定理应用类结论 (设计/计数存在性) **不附通用代数论证链** —— 那会让
    正文显示与结论无关的步骤 (凭空造出"差式比较"之类)。改附证书判定链, 每一步
    给出定理、条件、输入与结论。
    """
    from src.research.argument import build_argument

    records = _valid_records(snapshot, claim.id)
    obligations = _closed_obligations(snapshot, claim.id)
    attempts = [a for a in snapshot.attempts if a.target_claim_id == claim.id]

    design_record = _design_record(snapshot, claim)
    if design_record is not None:
        certificate = certificate_of(design_record)
        closed = ", ".join(f"{o.kind}" for o in obligations) or "-"
        head = (f"证明与验证: 覆盖范围 {claim.coverage.value}, 支持方式 "
                f"{_SUPPORT_CN.get(claim.support_kind, claim.support_kind.value)}; "
                f"已关闭义务 [{closed}]; 验证记录 {design_record.tool}"
                f"({design_record.validation_status.value})")
        chain = render_certificate_chain(certificate)
        return head + "\n\n判定链 (可逐条反查定理与输入):\n" + chain

    if records:
        detail = []
        for record in records:
            tool = record.tool or "?"
            cert = record.certificate or record.raw_output[:80] or "-"
            detail.append(f"{tool}({record.validation_status.value}): {cert}")
        closed = ", ".join(f"{o.kind}" for o in obligations) or "-"
        support = _SUPPORT_CN.get(claim.support_kind, claim.support_kind.value)
        head = (f"证明与验证: 覆盖范围 {claim.coverage.value}, 支持方式 {support}; "
                f"已关闭义务 [{closed}]; 验证记录 " + "; ".join(detail))
    elif attempts and any(a.steps for a in attempts):
        steps = "; ".join(s.statement for s in attempts[0].steps if s.statement)
        open_obligations = [o for o in snapshot.obligations
                            if o.claim_id == claim.id and o.status != ObligationStatus.closed]
        pending = "; ".join(f"{o.kind}: {o.statement[:60]}" for o in open_obligations) or "-"
        head = (f"推导计划 (尚未完成核验, 不构成证明): {steps}。"
                f"未关闭义务: {pending}")
    else:
        head = "本结论缺少可展示的推导或验证记录, 仅为条件性表述。"

    chain = build_argument(snapshot, claim)
    return head + "\n\n" + chain.render()


def trace_manuscript(snapshot: ResearchSnapshot, manuscript: Manuscript,
                     markdown: str = "") -> dict:
    """逐条核对核心论断能否从正文回到冻结快照 (计划书 P1-3)。

    只查"能不能反查", 不判断论证是否正确:
    - `unmapped_claims`: 已确定结论在 `writing_map` 里没有位置 (写作自行补足);
    - `missing_anchors`: 映射锚点没出现在正文里 (映射与正文不一致);
    - `unlabeled_blocks`: 正文里的命题/结论段落没有对象标签 (无法归属到快照对象)。
    """
    text = markdown or ""
    core = [c for c in snapshot.claims
            if c.status in (ClaimStatus.supported, ClaimStatus.refuted)]
    mapped: list[dict] = []
    unmapped: list[str] = []
    missing: list[str] = []
    for claim in core:
        anchor = (manuscript.writing_map or {}).get(claim.id, "")
        if not anchor:
            unmapped.append(claim.id)
            continue
        mapped.append({"claim_id": claim.id, "anchor": anchor})
        # 渲染器在定理/命题行里显示对象 id (锚点用于 LaTeX label), 两者之一出现即可
        if text and anchor not in text and claim.id not in text:
            missing.append(claim.id)
    claim_blocks = [b for b in manuscript.blocks
                    if getattr(b, "kind", "") in ("claim", "proposition", "lemma",
                                                  "theorem", "corollary")]
    unlabeled = [b.title or b.text[:40] for b in claim_blocks if not getattr(b, "label", "")]
    ok = not unmapped and not missing and not unlabeled
    return {
        "ok": ok,
        "mapped": mapped,
        "unmapped_claims": unmapped,
        "missing_anchors": missing,
        "unlabeled_blocks": unlabeled,
        "core_claims": len(core),
        "note": ("正文中的每条核心论断都能回到冻结快照对象"
                 if ok else "正文与快照不一致: 见 unmapped_claims / missing_anchors / unlabeled_blocks"),
    }


def build_manuscript(snapshot: ResearchSnapshot, topic: str = "",
                     delivery_level: str = "") -> Manuscript:
    title = f"关于「{topic or snapshot.project_id}」的理论研究"
    manuscript = Manuscript(title=title)
    supported = [c for c in snapshot.claims if c.status == ClaimStatus.supported]
    refuted = [c for c in snapshot.claims if c.status == ClaimStatus.refuted]
    unresolved = [c for c in snapshot.claims
                  if c.status in (ClaimStatus.blocked, ClaimStatus.proposed, ClaimStatus.in_progress)]

    manuscript.blocks.append(Block("heading", "引言"))
    manuscript.blocks.append(Block(
        "prose",
        "本文研究上述问题, 给出可追溯的定义、假设、命题与验证记录。"
        "所有主要结论均绑定具体验证等级, 局部验证不升级为整体证明。"
        + (f" 交付级别: {delivery_level}。" if delivery_level else ""),
    ))

    manuscript.blocks.append(Block("heading", "问题定义与假设"))
    if snapshot.models:
        for model in snapshot.models:
            manuscript.blocks.append(Block(
                "prose",
                f"- 模型 {model.id} ({'已选' if model.selected else '候选'}): "
                f"{model.natural_language or model.name}; 编码 {model.formal_encoding or '-'}; "
                f"来源 {', '.join(model.source_refs) or '-'}; 适用范围: "
                f"{model.verified_scope or '未声明'}"))
    if snapshot.assumptions:
        for asm in snapshot.assumptions:
            state = "已接受" if asm.accepted else "已修订/放弃"
            manuscript.blocks.append(Block("prose", f"- 假设 {asm.id} ({state}): {asm.statement}"))
    else:
        manuscript.blocks.append(Block("prose", "无显式假设。"))

    manuscript.blocks.append(Block("heading", "主要结果"))
    for claim in supported:
        anchor = f"{_claim_kind(claim)}-{claim.id}"
        manuscript.blocks.append(Block(
            _claim_kind(claim), _claim_body(claim), label=claim.id, title=claim.id))
        manuscript.writing_map[claim.id] = anchor
        manuscript.blocks.append(Block("proof", _proof_or_plan(claim, snapshot)))
        condition = _condition_text(claim)
        manuscript.blocks.append(Block(
            "prose", f"（结论 {claim.id}, 支持方式 {_SUPPORT_CN.get(claim.support_kind, '未声明')}, "
                     f"验证等级 {claim.assurance.value}, 覆盖范围 {claim.coverage.value}"
                     + (f", 适用条件 {condition}" if condition else "") + "）"))

    for claim in refuted:
        anchor = f"proposition-{claim.id}"
        body = _claim_body(claim)
        witness = _counterexample_of(snapshot, claim)
        manuscript.blocks.append(Block(
            "proposition", f"原命题为假: {body}", label=claim.id, title=claim.id))
        if witness:
            manuscript.blocks.append(Block(
                "proof", f"反例 (满足声明前提): 取 {witness}, 原命题不成立。"))
        else:
            manuscript.blocks.append(Block(
                "proof", "该否定性结论缺少可回代的反例见证, 需补充后复核。"))
        manuscript.writing_map[claim.id] = anchor

    if not supported and not refuted:
        manuscript.blocks.append(Block("prose", "尚无已确定结论, 见未决问题报告。"))

    manuscript.blocks.append(Block("heading", "已有工作与新颖性"))
    if snapshot.novelty:
        for record in snapshot.novelty:
            manuscript.blocks.append(Block(
                "prose",
                f"- 结论 {record.claim_id}: 新颖性状态 {record.status.value}; "
                f"{record.conclusion or record.bounded_statement}; "
                f"检索日期 {record.search_date or '-'}; 覆盖源 {', '.join(record.covered_sources) or '-'}"))
    else:
        manuscript.blocks.append(Block("prose", "尚未进行已有工作比较, 不得宣称原创性。"))

    manuscript.blocks.append(Block("heading", "证据与适用性"))
    if snapshot.evidence:
        for item in snapshot.evidence:
            manuscript.blocks.append(Block(
                "prose",
                f"- [{item.id}] {item.title or item.literature_id} @ {item.location or '无定位'}; "
                f"关系={item.support.value}; 来源类型={item.source_kind.value}; "
                f"可信度={item.credibility.value}; 存在性={'已核' if item.existence_verified else '未核'}"
                + (f"; 理由: {item.support_reason}" if item.support_reason else "")))
    else:
        manuscript.blocks.append(Block("prose", "无外部证据; 结论仅在声明模型与假设下成立。"))

    manuscript.blocks.append(Block("heading", "实验/仿真建议"))
    if snapshot.experiment_specs:
        for spec in snapshot.experiment_specs:
            status = str(spec.get("execution_status", "proposed"))
            manuscript.blocks.append(Block(
                "prose",
                f"- {spec.get('id')} [{status}] 目的={spec.get('purpose')}; "
                f"检验 {spec.get('claim_id')}; 判据: {spec.get('decision_rule', '')[:120]}; "
                f"状态说明: {'已执行' if status in ('executed', 'analyzed') else '未执行 (不得作为已验证证据)'}"))
    else:
        manuscript.blocks.append(Block("prose", "未生成实验/仿真规格。"))

    manuscript.blocks.append(Block("heading", "适用范围与局限"))
    if unresolved:
        for claim in unresolved:
            manuscript.blocks.append(Block(
                "prose", f"- 未决 {claim.id}: {claim.statement} (状态: {claim.status.value})"))
    else:
        manuscript.blocks.append(Block("prose", "本结论仅在声明模型与假设下成立, 不承担实证验证。"))
    open_obligations = [o for o in snapshot.obligations
                        if o.status in (ObligationStatus.open, ObligationStatus.blocked)]
    for obligation in open_obligations:
        manuscript.blocks.append(Block(
            "prose",
            f"- 未关闭义务 {obligation.id} ({obligation.status.value}): "
            f"{obligation.statement} — {obligation.detail or '待处理'}"))

    manuscript.blocks.append(Block("heading", "结论"))
    manuscript.blocks.append(Block(
        "prose",
        f"共 {len(supported)} 个命题在给定条件下成立, {len(refuted)} 个命题被严格反例否定, "
        f"{len(unresolved)} 个未决。所有结论的验证等级与依赖关系见证明附录与验证记录。",
    ))

    manuscript.blocks.append(Block("heading", "证明附录: 验证记录"))
    for record in snapshot.verifications:
        if record.stale:
            continue
        certificate = certificate_of(record)
        detail = (f"- [{record.tool}] claim={record.claim_id}@v{record.claim_version} "
                  f"status={record.status} validation={record.validation_status.value} "
                  f"coverage={record.scope.value} "
                  f"cert={record.certificate or certificate.get('sha256', '-')} "
                  f"counterexample={record.counterexample or '-'} "
                  f"closure={record.verification_closure or '-'}")
        if certificate:
            # 具名定理应用: 附录必须能反查到"用了哪条定理、输入是什么"
            detail += "\n" + render_certificate_chain(certificate)
        manuscript.blocks.append(Block("prose", detail))

    # R4: 写作阶段发现的缺口如实列在正文里 (不回填结论, 也不掩盖)
    gaps = writing_gaps(snapshot)
    if gaps:
        manuscript.blocks.append(Block("heading", "写作阶段发现的缺口 (需回到研究循环)"))
        for gap in gaps:
            flag = "待研究" if gap.severity == "blocking" else "提示"
            manuscript.blocks.append(Block(
                "prose",
                f"- [{flag}] {gap.claim_id or '(全局)'} {gap.kind}: {gap.statement}"
                + (f" — {gap.detail}" if gap.detail else "")))
    return manuscript


def _counterexample_of(snapshot: ResearchSnapshot, claim: Claim) -> str:
    for record in snapshot.verifications:
        if record.claim_id == claim.id and record.counterexample:
            return ", ".join(f"{k}={v}" for k, v in record.counterexample.items())
    for obligation in snapshot.obligations:
        if obligation.claim_id == claim.id and obligation.counterexample:
            return ", ".join(f"{k}={v}" for k, v in obligation.counterexample.items())
    return ""


def render_research_report(snapshot: ResearchSnapshot, gate=None, notes: list[str] | None = None,
                           delivery_gate_result=None) -> str:
    """未决/部分结果研究报告 (未通过交付门槛时使用)。"""
    lines = [f"# 研究报告: {snapshot.project_id}", ""]
    lines.append("## 结论状态")
    for claim in snapshot.claims:
        lines.append(f"- {claim.id} [{claim.status.value}/{claim.assurance.value}/"
                     f"{claim.support_kind.value}/{claim.coverage.value}]: {claim.statement}")
    lines.append("")
    lines.append("## 未决问题")
    for obligation in snapshot.obligations:
        if obligation.status in (ObligationStatus.open, ObligationStatus.blocked):
            lines.append(f"- {obligation.id} ({obligation.status.value}/{obligation.validation_status.value}): "
                         f"{obligation.statement} — {obligation.detail}")
    if not any(o.status in (ObligationStatus.open, ObligationStatus.blocked)
               for o in snapshot.obligations):
        lines.append("- 无")
    if snapshot.routes:
        lines.append("")
        lines.append("## 研究路线")
        for route in snapshot.routes:
            lines.append(f"- {route.id} [{route.status.value}] 策略={route.strategy} "
                         f"失败原因={route.failure_reason or '-'} "
                         f"恢复条件={route.recovery_condition or '-'}")
    if snapshot.experiment_specs:
        lines.append("")
        lines.append("## 实验/仿真规格 (均未执行)")
        for spec in snapshot.experiment_specs:
            lines.append(f"- {spec.get('id')} [{spec.get('execution_status')}] "
                         f"claim={spec.get('claim_id')}: {spec.get('title', '')}")
    if snapshot.evidence:
        lines.append("")
        lines.append("## 证据与来源定位")
        for item in snapshot.evidence:
            lines.append(f"- [{item.id}] {item.title} @ {item.location or '无定位'} "
                         f"关系={item.support.value}")
    if gate is not None:
        lines.append("")
        lines.append("## 研究有效性门槛")
        lines.append(gate.render())
    if delivery_gate_result is not None:
        lines.append("")
        lines.append("## 论文表达门槛")
        lines.append(delivery_gate_result.render())
    if notes:
        lines.append("")
        lines.append("## 过程说明")
        for note in notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def run_theory_writing(snapshot: ResearchSnapshot, topic: str = "",
                       delivery_level: str = "",
                       writer: Any = None,
                       *,
                       task: Any = None,
                       context: Any = None,
                       runtime: Any = None,
                       usage: Any = None) -> tuple[str, dict[str, str]]:
    """理论稿件主文 (合并计划 §7.3: **主文统一由 WritingAgent 形成**)。

    这里不再自己组装正文: 走 `agents.writing.write_main_manuscript` 这个唯一入口。
    离线 (无 LLM) 时它由快照确定性起草, 输出与本函数旧实现逐字一致, 因此
    "离线可复现"不因合并而下降; 有模型时正文来自写作智能体, 逐段带依据对象 id。

    `writer` 可注入 (测试/替换实现), 透传给 WritingAgent; `task`/`context`/`runtime`
    给了才会尝试模型起草, 否则直接确定性起草 (离线路径)。
    """
    from src.agents.writing import write_main_manuscript

    _, markdown, writing_map, _ = write_main_manuscript(
        snapshot, topic, delivery_level, agent=writer, task=task, context=context,
        runtime=runtime, usage=usage)
    return markdown, writing_map
