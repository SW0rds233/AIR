from __future__ import annotations

"""结论与交付门槛 (P3)。

两道门槛分离:
1. 理论有效性门槛: 结论明确、假设完整、依赖闭合、无循环、无未处理反例、
   无过期验证、验证等级符合约定。
2. 论文表达门槛: 摘要/正文/定理/证明/结论一致, 无遗漏条件或夸大范围。

旧有 50 分制只可用于表达质量参考, 不决定理论有效性。

计划书 §2 P0-A06/A07 修复点:
- 门槛必须检查**验证记录的状态/版本/域与全部义务**, 而不只是"存在一条记录";
- 只检查本次快照实际引用的有效记录是否 stale (§4.3-6), 历史 stale 不永久阻止交付;
- 反例语义: 运行错误不得冒充反驳; 未找到反例不得当作普遍成立;
- 因果结论要求识别假设、设计、范围、混淆处理与证据分级分别成立。
"""

import re
from dataclasses import dataclass, field

from src.research.dependency_graph import DependencyGraph
from src.research.schemas import (
    Assurance,
    Claim,
    ClaimStatus,
    ClaimType,
    Coverage,
    EvidenceGrade,
    ObligationStatus,
    ProofObligation,
    ResearchSnapshot,
    StudyDesign,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def render(self) -> str:
        status = "通过" if self.passed else "未通过"
        lines = [f"门槛{status}"]
        for r in self.reasons:
            lines.append(f"- {r}")
        for u in self.unresolved:
            lines.append(f"- 未决: {u}")
        return "\n".join(lines)


def theory_validity_gate(
    claims: list[Claim],
    obligations: list[ProofObligation],
    verifications: list[VerificationRecord],
    edges: list[tuple[str, str]],
    snapshot: ResearchSnapshot | None = None,
) -> GateResult:
    reasons: list[str] = []
    unresolved: list[str] = []

    graph = DependencyGraph()
    for src, dst in edges:
        try:
            graph.add_edge(src, dst)
        except Exception:  # noqa: BLE001
            reasons.append(f"存在循环/非法依赖: {src} -> {dst}")
    cycle = graph.find_cycle()
    if cycle:
        reasons.append("存在循环证明: " + " -> ".join(cycle))

    # 只检查本次快照实际引用的记录 (计划书 §4.3-6)
    if snapshot is not None and snapshot.verification_index:
        referenced: set[str] = set()
        for ids in snapshot.verification_index.values():
            referenced.update(ids)
        in_scope = [v for v in verifications if v.id in referenced] or verifications
    else:
        in_scope = verifications

    for obligation in obligations:
        if obligation.required and obligation.status == ObligationStatus.open:
            unresolved.append(f"未关闭义务 {obligation.id}: {obligation.statement}")
        if (obligation.status == ObligationStatus.refuted
                and not obligation.counterexample
                and obligation.validation_status != ValidationStatus.counterexample_found):
            reasons.append(
                f"义务 {obligation.id} 被标记为被否决, 但没有满足前提的反例或可审查反证"
            )
        if obligation.validation_status in (ValidationStatus.timeout,
                                            ValidationStatus.unavailable,
                                            ValidationStatus.execution_error):
            unresolved.append(
                f"义务 {obligation.id} 因运行/能力问题未决 ({obligation.validation_status.value})"
            )

    for claim in claims:
        if claim.status in (ClaimStatus.proposed, ClaimStatus.in_progress):
            unresolved.append(f"结论未定 {claim.id}: {claim.statement}")
            continue
        if claim.status == ClaimStatus.blocked:
            unresolved.append(f"结论受阻 {claim.id}: {claim.statement}")
            continue
        if claim.status not in (ClaimStatus.supported, ClaimStatus.refuted):
            continue

        own = [v for v in in_scope if v.claim_id == claim.id and not v.stale]
        if not own:
            reasons.append(f"结论 {claim.id} 缺少有效验证记录")
        for record in own:
            if not _aligned(record, claim):
                reasons.append(f"结论 {claim.id} 的验证编码与原命题不一致 (陈述被偷换)")
            if record.validation_status == ValidationStatus.counterexample_found \
                    and claim.status == ClaimStatus.supported:
                reasons.append(f"结论 {claim.id} 存在反例记录却被标记为 supported")
        if claim.assurance == Assurance.unverified:
            reasons.append(f"结论 {claim.id} 已定但验证等级为 unverified")
        if claim.verification_scope is None:
            reasons.append(f"结论 {claim.id} 未标注验证覆盖范围")
        if claim.status == ClaimStatus.supported:
            required = [o for o in obligations if o.claim_id == claim.id and o.required]
            not_closed = [o for o in required if o.status != ObligationStatus.closed]
            if not_closed:
                reasons.append(
                    f"结论 {claim.id} 被标记为 supported, 但仍有 {len(not_closed)} 条必要义务未关闭"
                )
            if required and claim.coverage != Coverage.target:
                reasons.append(f"结论 {claim.id} 的全部义务已关闭但覆盖范围仍为 {claim.coverage.value}")
            if claim.support_kind == SupportKind.none:
                reasons.append(f"结论 {claim.id} 未声明支持方式 (support_kind)")
            # P0-4: 以原文/非形式化论证支持的强结论, 必须有可定位引文;
            # 工具核验类结论 (符号/求解/形式化/数值/统计) 另有验证输入作依据。
            if claim.support_kind in (SupportKind.textual_support,
                                      SupportKind.informal_argument):
                linked_ids = {lk.source_ref.id for lk in (snapshot.evidence_links
                                                          if snapshot is not None else [])
                              if lk.claim_ref.id == claim.id}
                quotes = [e for e in (snapshot.evidence if snapshot is not None else [])
                          if (e.claim_id == claim.id or e.id in linked_ids)
                          and e.support_evidence and (e.location or e.page)]
                if not quotes:
                    reasons.append(
                        f"结论 {claim.id} 以 {claim.support_kind.value} 支持, 但缺少可定位引文 "
                        f"(需要原文引文 + 页/节)")
            # 具名定理的机器可复核应用: 必须留下定理陈述与参数输入 (证书), 不允许
            # 用"引用了一条定理"当作无条件成立。
            if claim.support_kind == SupportKind.theorem_application:
                with_certificate = [
                    r for r in own
                    if (r.premises or r.checked_formula)
                    and (r.arguments or {}).get("design_report", {}).get("evidence")]
                if not with_certificate:
                    reasons.append(
                        f"结论 {claim.id} 以 {claim.support_kind.value} 支持, "
                        "但验证记录缺少可复核的定理陈述与参数证书")
        # 条件性结论必须写明适用域, 不得当无条件结论
        if claim.status == ClaimStatus.refuted and claim.coverage != Coverage.target:
            reasons.append(f"否定性结论 {claim.id} 未覆盖完整目标条件")
        if claim.verification_closure and snapshot is not None:
            stale_deps = _stale_closure(claim, snapshot)
            if stale_deps:
                reasons.append(
                    f"结论 {claim.id} 的验证依赖已换代, 需重验: {', '.join(stale_deps)}"
                )

    for claim in claims:
        if claim.claim_type == ClaimType.causal and claim.status == ClaimStatus.supported:
            estimate = claim.effect_estimate or {}
            if estimate.get("ci_low") is None or estimate.get("ci_high") is None:
                reasons.append(f"因果结论 {claim.id} 缺少不确定性区间 (CI)")
            if claim.study.design == StudyDesign.none:
                reasons.append(f"因果结论 {claim.id} 未声明研究设计")
            if not (claim.scope_population and claim.scope_region and claim.scope_period):
                reasons.append(f"因果结论 {claim.id} 未限定人群/地区/时期")
            if not claim.study.confounders and not claim.study.confounder_handling:
                reasons.append(f"因果结论 {claim.id} 未声明混淆处理")
            if claim.evidence_grade == EvidenceGrade.unsupported:
                reasons.append(f"因果结论 {claim.id} 缺少带可信度分级的证据")
            # 计划书 §7.4: 识别假设 / 设计可行性 / 测量与缺失 / 误差结构必须各自成立,
            # 且不得用占位或合成数据产出"现实因果结论"。
            if not claim.study.identification_assumptions:
                reasons.append(f"因果结论 {claim.id} 未列出识别假设 (设计声明不等于识别成立)")
            if not claim.study.design_feasibility.strip():
                reasons.append(f"因果结论 {claim.id} 未说明设计在现有数据上的可行性")
            if not claim.study.measurement_notes.strip():
                reasons.append(f"因果结论 {claim.id} 未说明测量方案")
            if not claim.study.missing_data_handling.strip():
                reasons.append(f"因果结论 {claim.id} 未说明缺失数据机制与处理")
            if not claim.study.error_structure.strip():
                reasons.append(f"因果结论 {claim.id} 未说明误差结构 (区间估计可靠性未知)")
            if claim.study.data_source_kind in ("placeholder", "synthetic"):
                reasons.append(
                    f"因果结论 {claim.id} 使用 {claim.study.data_source_kind} 数据, "
                    "只能作为方法演示, 不得作为现实因果结论"
                )
        if (claim.claim_type == ClaimType.normative
                and claim.status in (ClaimStatus.supported, ClaimStatus.refuted)):
            reasons.append(f"规范判断 {claim.id} 不得标记为真/假, 应记录为价值选择")

    # 只对本次快照实际引用且已 stale 的记录报错
    referenced_ids = set()
    if snapshot is not None:
        for ids in snapshot.verification_index.values():
            referenced_ids.update(ids)
    stale = [v for v in in_scope if v.stale and (not referenced_ids or v.id in referenced_ids)]
    if stale:
        reasons.append(f"存在 {len(stale)} 条被本次交付引用的过期验证记录, 需重算")

    passed = not reasons and not unresolved
    return GateResult(passed=passed, reasons=reasons, unresolved=unresolved)


def _stale_closure(claim: Claim, snapshot: ResearchSnapshot) -> list[str]:
    """检查结论的验证依赖闭包是否已换代 (假设/模型版本变化)。"""
    stale: list[str] = []
    current: dict[str, int] = {}
    for assumption in snapshot.assumptions:
        current[assumption.id] = assumption.version
    for model in snapshot.models:
        current[model.id] = model.version
    for obj_id, version in (claim.verification_closure or {}).items():
        if obj_id == claim.id:
            continue
        latest = current.get(obj_id)
        if latest is not None and latest != version:
            stale.append(f"{obj_id} v{version}→v{latest}")
    return stale


def _normalize_expr(text: str) -> str:
    return "".join(str(text or "").split()).replace("**", "^")


def _aligned(record: VerificationRecord, claim: Claim) -> bool:
    """核对验证请求编码的变量域/关系/表达式与原命题是否一致 (防止陈述被偷换)。"""
    args = record.arguments or {}
    # 设计/计数类命题: 判定输入 (v,k,λ,b,r) 必须与命题记录的一致, 否则"换一组
    # 参数再引用旧证书"就能伪造结论 (陈述偷换)。**先于**空参数短路检查。
    if claim.design_v is not None or claim.design_k is not None:
        for key, expected in (("design_v", claim.design_v), ("design_k", claim.design_k),
                              ("design_lambda", claim.design_lambda),
                              ("design_b", claim.design_b), ("design_r", claim.design_r)):
            if key not in args:
                return False
            recorded = args.get(key)
            if (recorded is None) != (expected is None):
                return False
            if recorded is not None and int(recorded) != int(expected):
                return False
        verdict = args.get("design_verdict")
        if claim.design_verdict and verdict and str(verdict) != claim.design_verdict:
            return False
    if not args:
        return True
    if "relation" in args and args["relation"] and args["relation"] != claim.relation.value:
        return False
    if "lhs" in args and args["lhs"] and _normalize_expr(args["lhs"]) != _normalize_expr(claim.lhs):
        return False
    if "rhs" in args and args["rhs"] and _normalize_expr(args["rhs"]) != _normalize_expr(claim.rhs):
        return False
    if args.get("expr") and claim.expr and _normalize_expr(args["expr"]) != _normalize_expr(claim.expr):
        return False
    if args.get("wrt") and claim.wrt and args["wrt"] != claim.wrt:
        return False
    if args.get("direction") and claim.direction and args["direction"] != claim.direction:
        return False
    if args.get("variables") and claim.variables and list(args["variables"]) != list(claim.variables):
        return False
    # 域/量词一致性 (计划书 §7.3 陈述对齐)
    assumptions = args.get("assumptions") or {}
    if assumptions and claim.variable_domains:
        for var, domain in claim.variable_domains.items():
            got = assumptions.get(var)
            if got is not None and str(got).lower() != str(domain).lower():
                return False
    return True


def delivery_gate(manuscript: str, snapshot: ResearchSnapshot,
                  theory_gate: GateResult | None = None) -> GateResult:
    """论文表达门槛 + 研究有效性门槛的联合出口检查 (计划书 §9.1、§14)。"""
    reasons: list[str] = []
    unresolved: list[str] = []

    if not manuscript or len(manuscript.strip()) < 200:
        reasons.append("研究稿过短或为空")

    mapped = set(snapshot.writing_map.keys())
    for claim in snapshot.claims:
        if claim.status in (ClaimStatus.supported, ClaimStatus.refuted) and claim.id not in mapped:
            reasons.append(f"主要结论 {claim.id} 未映射到正文位置")

    # 未决义务的结论不得作为定理写入正文
    for obligation in snapshot.obligations:
        if obligation.status in (ObligationStatus.open, ObligationStatus.blocked) \
                and obligation.claim_id in mapped:
            reasons.append(f"未关闭义务 {obligation.id} 的结论被写入正文")

    # 正文不得把"证明计划"当作"已完成证明"陈述
    if snapshot.attempts:
        complete = [a for a in snapshot.attempts if a.status == "complete"]
        proof_claims = [c for c in snapshot.claims
                        if c.claim_type.value in ("definitional", "descriptive")
                        and c.status == ClaimStatus.supported]
        if proof_claims and not complete:
            unresolved.append(
                "已定结论缺少已完成的证明尝试记录, 正文引用证明时须标明为非形式化论证"
            )

    # 结论的条件必须出现在正文中 (不得删去条件后仍声称交付)
    for claim in snapshot.claims:
        if claim.status != ClaimStatus.supported:
            continue
        if claim.variable_domains:
            missing = [f"{v}∈{d}" for v, d in claim.variable_domains.items()
                       if v not in manuscript]
            if missing and len(missing) == len(claim.variable_domains):
                reasons.append(f"结论 {claim.id} 的适用条件未出现在正文中: {', '.join(missing)}")

    # 实验建议不得出现"已执行"结果
    for spec in snapshot.experiment_specs or []:
        status = str(spec.get("execution_status", "proposed"))
        if status in ("proposed", "spec_validated") \
                and ("实验结果" in manuscript or "实测" in manuscript):
            reasons.append(
                f"实验规格 {spec.get('id')} 尚未执行 ({status}), 正文不得出现实验结果"
            )

    if snapshot.claims and not any(c.status == ClaimStatus.supported for c in snapshot.claims) \
            and not any(c.status == ClaimStatus.refuted for c in snapshot.claims):
        unresolved.append("没有任何已确定结论, 只能作为研究备忘录导出")

    if theory_gate is not None and not theory_gate.passed:
        unresolved.append("研究有效性门槛未通过, 只能作为研究备忘录/条件性报告导出")

    passed = not reasons and not unresolved
    return GateResult(passed=passed, reasons=reasons, unresolved=unresolved)


def publication_gate(manuscript: str, snapshot: ResearchSnapshot,
                     references: list[dict] | None = None,
                     compile_status: str = "",
                     blocks: list | None = None,
                     checklist: dict | None = None) -> GateResult:
    """出版完备门槛 (方案 v2 §5 阶段 1): 判断能否称为"完整论文"。

    与"论文表达门槛"的区别: 表达门槛问的是"正文有没有如实表达研究结论";
    本门槛问的是"**能不能作为一篇完整论文交付**"—— 摘要/关键词/参考文献章节、
    正文引用与文献表的双向一致、结论可反查、`.tex` 能编译成 PDF、没有悬空的
    `\\ref`/`\\cite` 与未标注占位符。

    **缺项一律如实列为理由/未决, 不因为"看起来完整"就通过**; 无文献可用时
    参考文献章节仍必须在位并说明检索范围, 否则不算出版完备 (方案 v2 §0)。
    """
    reasons: list[str] = []
    unresolved: list[str] = []
    text = manuscript or ""
    references = list(references or [])
    kinds = {getattr(b, "kind", "") for b in (blocks or [])}
    # 有结构化块时以块为准: 正文里偶然出现"摘要"两个字不能算有摘要
    structured = bool(blocks)

    # 1) 摘要与关键词
    if structured:
        if "abstract" not in kinds:
            reasons.append("缺少摘要")
        if "keywords" not in kinds:
            reasons.append("缺少关键词")
    else:
        if "摘要" not in text and "\\begin{abstract}" not in text:
            reasons.append("缺少摘要")
        if "关键词" not in text and "Key words" not in text:
            reasons.append("缺少关键词")

    # 2) 参考文献章节必须在位 (即便为空, 也要说明检索范围)
    has_ref_section = ("参考文献" in text or "\\begin{thebibliography}" in text)
    if not has_ref_section:
        reasons.append("缺少参考文献章节")

    # 3) 正文引用与文献表双向一致
    cited = _cited_numbers(text)
    listed = _listed_numbers(text, len(references))
    unlisted = sorted(n for n in cited if n not in listed)
    uncited = sorted(n for n in listed if n not in cited)
    if unlisted:
        reasons.append(f"正文引用了参考文献表中没有的编号: {unlisted}")
    if uncited and references:
        unresolved.append(f"参考文献表中未被正文引用的条目: {uncited}")

    # 4) 核心结论必须能在正文反查到
    for claim in snapshot.claims:
        if claim.status in (ClaimStatus.supported, ClaimStatus.refuted) \
                and claim.id not in text:
            reasons.append(f"核心结论 {claim.id} 未出现在正文中 (无法反查)")

    # 5) 悬空引用与未标注占位符
    if _dangling_refs(text):
        reasons.append(f"存在悬空的 \\ref/\\cite: {', '.join(_dangling_refs(text)[:5])}")
    placeholders = _unlabelled_placeholders(text)
    if placeholders:
        reasons.append(f"存在未标注的占位符: {', '.join(placeholders[:5])}")

    # 6) 编译状态: 环境性缺项 (没有 xelatex) 记未决, 不虚升等级
    if compile_status == "ok":
        pass
    elif compile_status == "unavailable":
        unresolved.append("本机没有可用的 LaTeX 引擎: 只交付 .tex, 无法交付 .pdf")
    elif compile_status in ("", "not_attempted") or compile_status.startswith("deferred"):
        unresolved.append("尚未尝试编译 .tex: 无法确认能否交付 .pdf")
    else:
        reasons.append(f"LaTeX 编译失败: {compile_status}")

    passed = not reasons
    return GateResult(passed=passed, reasons=reasons, unresolved=unresolved)


def _cited_numbers(text: str) -> set[int]:
    """正文里出现的引用编号: `[1]` / `[1,2]` / `\\cite{...}` / `\\upcite{...}`。"""
    numbers: set[int] = set()
    for group in re.findall(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]", text or ""):
        for part in re.split(r"[,\-–]", group):
            part = part.strip()
            if part.isdigit():
                numbers.add(int(part))
    for group in re.findall(r"\\(?:up)?cite\{([^}]*)\}", text or ""):
        for key in re.split(r"[,\s]+", group):
            match = re.fullmatch(r"ref(\d+)", key.strip())
            if match:
                numbers.add(int(match.group(1)))
    return numbers


def _listed_numbers(text: str, reference_count: int) -> set[int]:
    """参考文献表里实际存在的编号 (按 `[n]` 行或 `\\bibitem{refN}` 计)。"""
    numbers: set[int] = set()
    for match in re.finditer(r"^\s*\[(\d+)\]", text or "", re.MULTILINE):
        numbers.add(int(match.group(1)))
    for match in re.finditer(r"\\bibitem\{ref(\d+)\}", text or ""):
        numbers.add(int(match.group(1)))
    if not numbers and reference_count:
        numbers = set(range(1, reference_count + 1))
    return numbers


def _dangling_refs(text: str) -> list[str]:
    """`\\ref{}`/`\\cite{}` 的目标是否在同文件里定义。"""
    body = text or ""
    labels = set(re.findall(r"\\label\{([^}]*)\}", body))
    bibitems = set(re.findall(r"\\bibitem\{([^}]*)\}", body))
    dangling: list[str] = []
    for target in re.findall(r"\\ref\{([^}]*)\}", body):
        if target and target not in labels:
            dangling.append(f"\\ref{{{target}}}")
    for group in re.findall(r"\\(?:up)?cite\{([^}]*)\}", body):
        for key in re.split(r"[,\s]+", group):
            if key and key not in bibitems:
                dangling.append(f"\\cite{{{key}}}")
    return dangling


# 允许的占位符: 必须带"待补"标注, 否则视为未标注占位符
_PLACEHOLDER_ALLOWED = ("待作者补充", "待补", "占位", "TODO(作者)")
_PLACEHOLDER_PAT = re.compile(r"(XXXX|xxx+|待填写|待定|\{作者\}|作者姓名|单位名称)")


def _unlabelled_placeholders(text: str) -> list[str]:
    found: list[str] = []
    for match in _PLACEHOLDER_PAT.finditer(text or ""):
        window = (text or "")[max(0, match.start() - 12): match.end() + 12]
        if any(mark in window for mark in _PLACEHOLDER_ALLOWED):
            continue
        found.append(match.group(0))
    return found


def publication_complete(publication: GateResult) -> bool:
    """出版完备判定: 没有缺项理由 **且** 没有未决项。

    为什么不能只看 `passed`: 出版层的"未决"是**实质缺项**而不是提示 ——
    例如"尚未尝试编译 .tex, 无法确认能否交付 .pdf"。若只看 `passed`, 一份没有
    PDF、没有参考文献的稿件会被报成"完整论文", 这正是方案 v2 §0 明确禁止的
    "看起来完整"。存在未决项时如实降级为"论文草稿", 并写明缺什么。
    """
    return bool(publication.passed and not publication.unresolved)


def classify_deliverable(theory_gate: GateResult, delivery: GateResult,
                         publication: GateResult | None = None) -> str:
    """四级交付 (方案 v2 §5 阶段 1):

    完整论文 (研究 ✓ + 表达 ✓ + 出版完备) > 论文草稿 (前两道 ✓) >
    条件性研究报告 > 研究备忘录。

    出版门槛未过时**仍可交付"论文草稿"** —— 这是合法终态; 但绝不允许把
    未过出版门槛的产出写成"完整论文"。
    """
    if theory_gate.passed and delivery.passed:
        if publication is None or publication_complete(publication):
            return "完整论文"
        return "论文草稿"
    if any(c and "没有任何已确定结论" in c for c in delivery.unresolved):
        return "研究备忘录"
    if any(g for g in theory_gate.reasons) or delivery.reasons:
        return "条件性研究报告"
    return "研究备忘录"
