from __future__ import annotations

"""核验服务 (合并计划 §6.2 / §9 R2): 把"该验什么"变成可落盘的 `VerificationRecord`。

它解决的是什么
--------------
`_run_tool` / `_act_check_step` / `_act_rule_obligation` 三块逻辑此前只存在于
`research/loop.py` 的 `TheoryEngine` 里。合并计划 §3.1 G06 要求团队形式化路径能自己
走完"候选/义务 → **工具核验** → 结构化记录 → 状态归并", 而这些能力**不能**由
另一个完整研究总控提供 (§1.1: 不保留第二个引擎)。

因此这里按职责抽出**核验**这一件事, 只有三种来源, 都保持确定性、零 LLM:

1. **工具核验**: 义务可编码成 SymPy/Z3/统计后端的调用 → 真子进程执行
   (`verification.runner`), 读真实退出码与证书;
2. **规则核验**: 义务能由命题记录里的显式声明/参数确定性判定 (设计可行性、
   识别假设、范围、混淆处理、证据分级) → 不调任何外部进程;
3. **独立审查**: 没有工具可核验的义务 → 保持未决, 明确标注"待人工/独立审查确认"。

服务**只产出记录与义务处置**, 不写命题状态: 命题状态归并由
`reasoning_kernel.claim_state_for` 负责, 由唯一提交口 (`research/commit.py`) 落盘。
这样"谁写了科学状态"始终只有一处。

与引擎的关系: 引擎的相关方法改为调用本服务, 用**差异测试**锁住等价性
(`tests/test_verification_service.py`), 因此在引擎退役前两条路径不会各自漂移。
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.research.reasoning_kernel import (
    ObligationDisposition,
    obligation_disposition_for,
)
from src.research.schemas import (
    Coverage,
    ObligationStatus,
    ProofObligation,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
    VerificationScope,
    coverage_for_tool,
    hash_payload,
    support_kind_for_tool,
)
from src.verification.runner import available_tools
from src.verification.schemas import VerificationResult, VerificationStatus

__all__ = [
    "CheckOutcome",
    "VerificationService",
    "design_necessity_evaluation",
]


#: 规则型义务 (没有工具后端, 由显式声明/参数确定性判定)。与引擎 `_act_check_step`
#: 的分派一致: `rule` / `design_necessity` 走规则核验, `informal_review` 保持未决。
RULE_ACCEPTANCE_METHODS: frozenset[str] = frozenset({"rule", "design_necessity"})

#: 独立审查类验收: 没有工具可核验, 不得自动关闭。
INFORMAL_ACCEPTANCE_METHODS: frozenset[str] = frozenset({"informal_review", "manual"})


@dataclass
class CheckOutcome:
    """一次核验的完整结果 (记录 + 义务处置 + 可复核的说明)。

    字段刻意与"落盘需要什么"一一对应, 让调用方 (引擎 / 提交服务) 只做搬运:
    `record` 进对象库, `obligation_updates` 进义务对象, `events` 进事件流。
    """

    tool: str = ""
    evaluated: bool = False              # 是否真的执行/判定过 (False = 只是没有可用后端)
    closed: bool = False                 # 义务是否可关闭
    record: VerificationRecord | None = None
    result: VerificationResult | None = None
    disposition: ObligationDisposition | None = None
    obligation_updates: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, dict]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 是否属于"科学结论"(反例) —— 引擎把它写进路线失败档案, 团队侧只记事件。
    scientific: bool = False
    recovery_condition: str = ""


class VerificationService:
    """把 (命题, 义务) 变成核验记录的服务 (团队与引擎共用同一份判据)。"""

    def __init__(self, *, store: Any | None = None, runner: Any | None = None,
                 available: dict[str, bool] | None = None,
                 problem_id: str = "", run_id: str = "",
                 evidence: Callable[[], list[Any]] | None = None) -> None:
        self.store = store
        self.runner = runner
        self._available = available
        self.problem_id = problem_id
        self.run_id = run_id
        #: 证据来源 (规则型 `evidence_support` 义务需要读已判定支持关系的证据)。
        self._evidence_provider = evidence

    # ------------------------------------------------------------------
    # 后端可用性
    # ------------------------------------------------------------------
    def available(self) -> dict[str, bool]:
        """当前可用的核验后端 (探测结果缓存; 缺失的后端报告 unavailable)。"""
        if self._available is None:
            self._available = available_tools()
        return dict(self._available)

    def _runner(self):
        if self.runner is None:
            from src.verification.runner import VerificationRunner

            self.runner = VerificationRunner()
        return self.runner

    # ------------------------------------------------------------------
    # 计划: 义务 → 检查项
    # ------------------------------------------------------------------
    def plan_check(self, claim: Any, obligation: ProofObligation):
        """该义务能否编码成一次工具调用 (不能则返回 None, 由调用方走规则/未决)。"""
        from src.research.theorist import _check_for

        return _check_for(obligation, claim, self.available())

    # ------------------------------------------------------------------
    # 核验
    # ------------------------------------------------------------------
    def check(self, claim: Any, obligation: ProofObligation, *,
              record_id: str = "", evidence: list[Any] | None = None
              ) -> CheckOutcome:
        """对一个义务执行核验。

        分派顺序与引擎一致: 设计可行性 → 反例搜索 → 规则 → 独立审查 → 工具。
        **顺序不能改**: `design_necessity` 义务的 `acceptance_method` 也是 `rule`,
        但它的判定链与证书形态不同, 先判它能避免走到通用规则分支后"未知规则型义务";
        `refute` 义务同样可能带着 `sympy` 的 acceptance_method, 必须走在通用工具分支前。
        """
        if obligation.acceptance_method == "design_necessity" or \
                obligation.kind == "design_necessity":
            return self.check_design_necessity(claim, obligation, record_id=record_id)
        if obligation.kind == "refute":
            return self.check_counterexample(claim, obligation, record_id=record_id)
        if obligation.acceptance_method in RULE_ACCEPTANCE_METHODS:
            return self.check_rule(claim, obligation, evidence=evidence)
        if obligation.acceptance_method in INFORMAL_ACCEPTANCE_METHODS:
            return self._informal(obligation)
        return self.check_tool(claim, obligation, record_id=record_id)

    # ---- 反例搜索 (§5.4 `_act_seek_counterexample` 的能力) ----
    def has_counterexample_check(self, claim: Any) -> bool:
        """该命题能否编码成反例搜索 (不能则**不应**为它造反例义务)。"""
        from src.research.critic import counterexample_check

        return counterexample_check(claim, self.available()) is not None

    def check_counterexample(self, claim: Any, obligation: ProofObligation, *,
                             record_id: str = "", ) -> CheckOutcome:
        """搜索反例: **没找到反例 ≠ 命题为真**。

        判定用内核的 `counterexample_verdict_for`: 只有给出**可回代的反例**才算反驳
        (`refuted`); 工具跑过但没给反例只记一次"有限测试证据" (义务保持 blocked),
        因此该义务永远不会把命题升级为 supported —— 这与计划 §7.2 的纪律一致。
        """
        from src.research.critic import counterexample_check
        from src.research.reasoning_kernel import counterexample_verdict_for

        check = counterexample_check(claim, self.available())
        if check is None:
            return CheckOutcome(
                tool="counterexample", evaluated=False, closed=False,
                obligation_updates={
                    "status": ObligationStatus.blocked,
                    "validation_status": ValidationStatus.unsupported,
                    "detail": "当前命题形式无法编码反例搜索",
                },
                notes=["先补出可核验的结构化形式"],
                recovery_condition="先补出可核验的结构化形式")
        result = self._run_tool(claim, obligation, check)
        validation = _to_validation_status(result)
        verdict = counterexample_verdict_for(validation, result.counterexample,
                                             result.detail or "")
        record = self._record_from(claim, obligation, check, result,
                                   record_id=record_id or f"ce-{claim.id}",
                                   usable=(verdict.action == "refute"))
        if verdict.action == "refute":
            updates = {
                "status": ObligationStatus.refuted,
                "validation_status": validation,
                "support_kind": support_kind_for_tool(check.tool),
                "coverage": coverage_for_tool(check.tool),
                "counterexample": dict(result.counterexample or {}),
                "detail": verdict.reason,
            }
            events = [("counterexample", {"claim_id": claim.id,
                                          "witness": dict(result.counterexample or {})})]
        else:
            # 未找到反例只是有限测试证据: 义务不得关闭, 也不得据此升级命题
            updates = {
                "status": ObligationStatus.blocked,
                "validation_status": validation,
                "detail": verdict.reason or verdict.note,
            }
            events = []
        return CheckOutcome(
            tool="counterexample", evaluated=True,
            closed=False, record=record, result=result,
            obligation_updates=updates, events=events,
            notes=[verdict.note] if verdict.note else [],
            scientific=verdict.scientific,
            recovery_condition=verdict.recovery_condition)

    # ---- 工具核验 ----
    def check_tool(self, claim: Any, obligation: ProofObligation, *,
                   record_id: str = "") -> CheckOutcome:
        """按义务构造检查项并真执行; 无可用后端时如实报告 unavailable。"""
        check = self.plan_check(claim, obligation)
        if check is None:
            return CheckOutcome(
                evaluated=False, closed=False,
                obligation_updates={
                    "status": ObligationStatus.blocked,
                    "validation_status": ValidationStatus.unsupported,
                    "detail": "无可用验证适配器",
                },
                notes=["无可用验证适配器"],
                recovery_condition="安装可用后端或改用可审查的自然语言推导",
            )
        result = self._run_tool(claim, obligation, check)
        disposition = obligation_disposition_for(result, obligation)
        updates: dict[str, Any] = {
            "validation_status": disposition.validation_status,
            "support_kind": support_kind_for_tool(check.tool),
            "coverage": coverage_for_tool(check.tool),
            "detail": disposition.reason or disposition.note,
        }
        if disposition.action == "closed":
            updates["status"] = ObligationStatus.closed
        elif disposition.action == "refuted":
            updates["status"] = ObligationStatus.refuted
            updates["counterexample"] = dict(result.counterexample or {})
        else:
            updates["status"] = ObligationStatus.blocked
        record = self._record_from(claim, obligation, check, result,
                                   record_id=record_id,
                                   usable=disposition.mark_record_usable)
        outcome = CheckOutcome(
            tool=check.tool, evaluated=True, closed=disposition.action == "closed",
            record=record, result=result, disposition=disposition,
            obligation_updates=updates,
            scientific=disposition.scientific,
            recovery_condition=disposition.recovery_condition,
            notes=[disposition.note] if disposition.note else [],
        )
        if disposition.action == "closed":
            outcome.events.append(("obligation_closed", {
                "claim_id": claim.id, "obligation_id": obligation.id,
                "tool": check.tool}))
        elif disposition.action == "refuted":
            outcome.events.append(("claim_refuted", {
                "claim_id": claim.id, "witness": dict(result.counterexample or {})}))
        return outcome

    def _run_tool(self, claim: Any, obligation: ProofObligation, check) -> VerificationResult:
        """执行一次工具核验 (带账本幂等: 同一输入不重复执行/计费)。"""
        idem = (f"tool:{claim.id}:"
                f"{obligation.id if obligation is not None else 'ce'}:"
                f"{hash_payload(check.arguments)}")
        run_id = f"run-{hash_payload([claim.id, check.tool, check.operation, check.arguments])}"
        if self.store is not None:
            try:
                self.store.begin_tool_run(
                    run_id, check.tool, check.operation, check.arguments,
                    problem_id=self.problem_id or getattr(claim, "problem_id", ""),
                    input_versions={claim.id: int(getattr(claim, "version", 1) or 1)},
                    idempotency_key=idem)
            except Exception:  # noqa: BLE001 - 账本不可用不阻断核验, 但不会假装"已去重"
                pass
        result = self._runner().run(check.tool, check.operation, check.arguments)
        if self.store is not None:
            try:
                self.store.finish_tool_run(
                    run_id, result.status.value,
                    result.model_dump(mode="json") if hasattr(result, "model_dump") else {},
                    tool_version=getattr(result, "tool_version", ""),
                    detail=getattr(result, "detail", ""))
            except Exception:  # noqa: BLE001
                pass
        return result

    def _record_from(self, claim: Any, obligation: ProofObligation | None, check,
                     result: VerificationResult, *, record_id: str = "",
                     usable: bool = False,
                     support_kind: SupportKind | None = None) -> VerificationRecord:
        record = VerificationRecord(
            id=record_id or f"ver-{claim.id}-{obligation.id if obligation else 'ce'}",
            tool=check.tool, tool_version=getattr(result, "tool_version", ""),
            input_hash=hash_payload({"tool": check.tool, "op": check.operation,
                                     "args": check.arguments}),
            claim_id=claim.id, claim_version=int(getattr(claim, "version", 1) or 1),
            assumption_ids=list(getattr(claim, "assumption_ids", []) or []),
            scope=VerificationScope.target,
            arguments=dict(check.arguments),
            raw_output=getattr(result, "raw_output", "") or "",
            status=getattr(result.status, "value", str(result.status)),
            certificate=getattr(result, "certificate", "") or "",
            counterexample=dict(getattr(result, "counterexample", None) or {}),
            verification_closure=self.closure(claim),
            validation_status=_to_validation_status(result),
            support_kind=support_kind or support_kind_for_tool(check.tool),
            # 原始结果先按"尚未确认可用"落盘, 判定确认后才解除 —— 顺序不能颠倒,
            # 否则裁决命题状态时会看到一条还没被采纳的记录。
            stale=not usable,
        )
        if obligation is not None:
            from src.research.schemas import ObjectRef

            record.obligation_ref = ObjectRef(id=obligation.id,
                                              version=int(getattr(obligation, "version", 1) or 1))
            record.link_status = "confirmed"
        return record

    def closure(self, claim: Any) -> dict[str, int]:
        """验证绑定的是具体陈述与依赖版本, 不只是 claim id。"""
        closure: dict[str, int] = {claim.id: int(getattr(claim, "version", 1) or 1)}
        if self.store is not None:
            from src.research.store import KIND_ASSUMPTION, KIND_SPEC

            problem_id = self.problem_id or getattr(claim, "problem_id", "")
            spec_version = self.store.latest_version(KIND_SPEC, problem_id) if problem_id else 0
            if spec_version:
                closure[problem_id] = spec_version
            for assumption_id in (getattr(claim, "assumption_ids", []) or []):
                version = self.store.latest_version(KIND_ASSUMPTION, assumption_id)
                if version:
                    closure[assumption_id] = version
        model_ref = getattr(claim, "model_ref", None)
        if model_ref is not None:
            closure[model_ref.id] = int(getattr(model_ref, "version", 1) or 1)
        return closure

    # ---- 规则核验 ----
    def check_rule(self, claim: Any, obligation: ProofObligation, *,
                   evidence: list[Any] | None = None) -> CheckOutcome:
        """规则型义务: 由确定性条件关闭 (混淆处理/范围/证据分级/因果声明)。"""
        ok, certificate, extra = _rule_verdict(claim, obligation, self._evidence_rows(evidence))
        if extra.get("detail"):
            obligation.detail = extra["detail"]
        updates: dict[str, Any] = {
            "status": ObligationStatus.closed if ok else ObligationStatus.blocked,
            "validation_status": (ValidationStatus.verified if ok
                                  else ValidationStatus.unknown),
            "detail": (obligation.detail if ok else extra.get("detail", obligation.detail)),
        }
        if ok:
            updates["coverage"] = Coverage.target
            updates["support_kind"] = extra.get("support_kind", SupportKind.informal_argument)
        record = None
        if ok:
            from src.research.theorist import PlannedCheck

            record = self._record_from(
                claim, obligation,
                PlannedCheck(tool="rule", operation=obligation.kind,
                             arguments={"kind": obligation.kind}),
                VerificationResult(tool="rule", status=VerificationStatus.passed,
                                   certificate=certificate, detail=certificate),
                record_id=f"ver-{claim.id}-{obligation.id}",
                usable=True,
                support_kind=extra.get("support_kind", SupportKind.informal_argument))
        return CheckOutcome(
            tool="rule", evaluated=True, closed=ok, record=record,
            obligation_updates=updates,
            events=[("obligation_closed", {"claim_id": claim.id,
                                           "obligation_id": obligation.id,
                                           "tool": "rule"})] if ok else [],
            notes=[] if ok else [extra.get("detail", "")],
            recovery_condition="" if ok else extra.get("recovery_condition", ""),
        )

    def _evidence_rows(self, evidence: list[Any] | None) -> list[Any]:
        if evidence is not None:
            return list(evidence)
        if self._evidence_provider is not None:
            return list(self._evidence_provider() or [])
        return []

    # ---- 设计可行性 (S1 判定链) ----
    def check_design_necessity(self, claim: Any, obligation: ProofObligation, *,
                               record_id: str = "") -> CheckOutcome:
        """设计/计数类存在性义务的规则验收 (证书可复核, 三态语义)。

        - `nonexistent`: 存在被违反的必要条件 → 关闭义务, 记录可采纳;
        - `necessary_met`: 必要条件全过但存在性未定 → **保持未关闭** (受阻),
          交付等级不得因此升级;
        - 其他/异常 → 保持未决并如实记录原因, 不冒充已判定。
        """
        evaluation = design_necessity_evaluation(claim)
        if evaluation is None:
            claim.design_v = getattr(claim, "design_v", None)
            return CheckOutcome(
                tool="design_necessity", evaluated=False, closed=False,
                obligation_updates={
                    "status": ObligationStatus.blocked,
                    "validation_status": ValidationStatus.unknown,
                    "detail": "该义务需要命题记录设计参数 (v,k,λ), 当前缺失",
                },
                notes=["该义务需要命题记录设计参数 (v,k,λ), 当前缺失"],
            )
        verdict = evaluation["verdict"]
        record = VerificationRecord(
            id=record_id or f"ver-{claim.id}-{obligation.id}",
            tool="design_necessity", tool_version="1",
            input_hash=evaluation["input_hash"],
            claim_id=claim.id, claim_version=int(getattr(claim, "version", 1) or 1),
            assumption_ids=list(getattr(claim, "assumption_ids", []) or []),
            scope=VerificationScope.target,
            arguments=evaluation["arguments"],
            checked_formula=evaluation["checked_formula"],
            premises=evaluation["premises"],
            output_digest=evaluation["digest"],
            raw_output=evaluation["raw_output"],
            validation_status=(ValidationStatus.verified if verdict == "nonexistent"
                               else ValidationStatus.unknown),
            support_kind=SupportKind.theorem_application,
            verification_closure=self.closure(claim),
            stale=verdict != "nonexistent",
        )
        from src.research.schemas import ObjectRef

        record.obligation_ref = ObjectRef(id=obligation.id,
                                          version=int(getattr(obligation, "version", 1) or 1))
        record.link_status = "confirmed"
        updates: dict[str, Any] = {"detail": evaluation["detail"]}
        events: list[tuple[str, dict]] = []
        # 结果对象必须一并给出: 提交口**重算**义务处置时只看 `result`, 不采信角色
        # 提交的义务状态。缺了它, 一个已被证书关闭的义务会被当成"未决"。
        if verdict == "nonexistent":
            result = VerificationResult(
                tool="design_necessity", status=VerificationStatus.passed,
                certificate=evaluation["digest"], detail=evaluation["detail"],
                raw_output=evaluation["raw_output"])
        elif verdict == "necessary_met":
            result = VerificationResult(
                tool="design_necessity", status=VerificationStatus.unknown,
                detail=evaluation["detail"], raw_output=evaluation["raw_output"])
        else:
            result = VerificationResult(
                tool="design_necessity", status=VerificationStatus.unsupported,
                detail=evaluation["detail"], raw_output=evaluation["raw_output"])
        if verdict == "nonexistent":
            updates.update({"status": ObligationStatus.closed,
                            "validation_status": ValidationStatus.verified,
                            "support_kind": SupportKind.theorem_application,
                            "coverage": Coverage.target})
            events.append(("design_necessity_evaluated",
                           {"claim_id": claim.id, "obligation_id": obligation.id,
                            "verdict": verdict, "closed": True,
                            "theorem_sources": evaluation["sources"],
                            "certificate": evaluation["digest"]}))
            return CheckOutcome(tool="design_necessity", evaluated=True, closed=True,
                                record=record, result=result,
                                obligation_updates=updates, events=events)
        if verdict == "necessary_met":
            updates.update({"status": ObligationStatus.blocked,
                            "validation_status": ValidationStatus.unknown})
            events.append(("design_necessity_evaluated",
                           {"claim_id": claim.id, "obligation_id": obligation.id,
                            "verdict": verdict, "closed": False,
                            "theorem_sources": evaluation["sources"],
                            "certificate": evaluation["digest"]}))
            return CheckOutcome(
                tool="design_necessity", evaluated=True, closed=False,
                record=record, result=result, obligation_updates=updates, events=events,
                scientific=True,
                recovery_condition="给出显式构造, 或找到能排除该参数的更强定理")
        updates.update({"status": ObligationStatus.blocked,
                        "validation_status": ValidationStatus.unknown})
        return CheckOutcome(tool="design_necessity", evaluated=True, closed=False,
                            record=record, result=result,
                            obligation_updates=updates, events=events)

    # ---- 独立审查 (无工具可核验) ----
    def _informal(self, obligation: ProofObligation) -> CheckOutcome:
        detail = obligation.detail or ""
        if not detail.endswith("[待人工/独立审查确认]"):
            detail = (detail + " [待人工/独立审查确认]").strip()
        return CheckOutcome(
            tool="informal_review", evaluated=False, closed=False,
            obligation_updates={
                "status": ObligationStatus.blocked,
                "validation_status": ValidationStatus.unknown,
                "detail": detail,
            },
            notes=["独立论证审查没有工具可核验, 缺确认时保持未决"],
            recovery_condition="由人工或独立审查者确认该意见已被处理",
        )


def _to_validation_status(result: VerificationResult) -> ValidationStatus:
    mapping = {
        "passed": ValidationStatus.verified,
        "failed": ValidationStatus.counterexample_found,
        "unknown": ValidationStatus.unknown,
        "unsupported": ValidationStatus.unsupported,
        "timeout": ValidationStatus.timeout,
        "unavailable": ValidationStatus.unavailable,
        "error": ValidationStatus.execution_error,
    }
    return mapping.get(getattr(result.status, "value", str(result.status)),
                       ValidationStatus.execution_error)


#: 规则型义务里"由显式声明关闭"的那几种 (计划书 §7.4)。
CAUSAL_DECLARATION_KINDS: frozenset[str] = frozenset({
    "identification_assumptions", "design_feasibility",
    "measurement_and_missing", "error_structure",
})


def _rule_verdict(claim: Any, obligation: ProofObligation,
                  evidence: list[Any]) -> tuple[bool, str, dict[str, Any]]:
    """规则型义务的确定性判定; 返回 (是否关闭, 证书, 附加字段)。

    与引擎 `_act_rule_obligation` 逐条一致 (差异测试把守), 但**不写义务对象**:
    判定结果通过返回值交给调用方落盘, 因此"判定"与"记录"是分开的两件事。
    """
    from src.research.schemas import EvidenceGrade, StudyDesign, SupportKindOfEvidence

    study = claim.study
    kind = obligation.kind
    extra: dict[str, Any] = {}

    if kind == "control_confound":
        if study.confounders or study.confounder_handling:
            return True, (study.confounder_handling or ", ".join(study.confounders)), extra
        return False, "", {"detail": "未列出混淆因素或识别策略 (不得默认无混淆)"}

    if kind == "scope_check":
        from src.research.classification import uses_population_scope

        if not uses_population_scope(claim):
            if claim.scope_conditions.strip():
                return True, claim.scope_conditions.strip(), extra
            return False, "", {"detail": "缺少研究对象、环境与适用条件"}
        if claim.scope_population and claim.scope_region and claim.scope_period:
            return True, (f"{claim.scope_population} / {claim.scope_region} / "
                          f"{claim.scope_period}"), extra
        return False, "", {"detail": "缺少人群/地区/时期范围"}

    if kind == "predictive_validation":
        if study.predictive_validation.strip():
            return True, study.predictive_validation.strip(), extra
        return False, "", {"detail": "缺少样本外划分、评价指标与基线比较方案"}

    if kind == "evidence_support":
        supporters = [e for e in evidence
                      if getattr(e, "support", None) in (
                          SupportKindOfEvidence.supports,
                          SupportKindOfEvidence.partially_supports)]
        design = study.design
        has_own_estimate = bool(claim.effect_estimate) and design in (
            StudyDesign.rct, StudyDesign.did, StudyDesign.iv, StudyDesign.rdd,
            StudyDesign.matching, StudyDesign.observational)
        if has_own_estimate:
            grade = (EvidenceGrade.identification_based
                     if design in (StudyDesign.rct, StudyDesign.did, StudyDesign.iv,
                                   StudyDesign.rdd, StudyDesign.matching)
                     else EvidenceGrade.single_source)
            extra["evidence_grade"] = grade
            return True, f"研究自身估计 ({design.value}) → {grade.value}", extra
        if supporters:
            from src.research.evidence import grade_evidence

            grade = grade_evidence(supporters)
            if grade != EvidenceGrade.unsupported:
                extra["evidence_grade"] = grade
                return True, grade.value, extra
            return False, "", {"detail": "支持关系已判定但证据等级仍不足"}
        return False, "", {"detail": "尚无已判定支持关系的证据 (召回不等于支持)"}

    if kind in CAUSAL_DECLARATION_KINDS:
        if kind == "identification_assumptions":
            if study.identification_assumptions:
                return True, "; ".join(study.identification_assumptions), extra
            return False, "", {"detail": (
                "未列出识别假设; 声明研究设计不等于识别成立 "
                "(如平行趋势/排他性/可忽略性等)")}
        if kind == "design_feasibility":
            if study.design_feasibility.strip():
                return True, study.design_feasibility.strip(), extra
            return False, "", {"detail": (
                "未说明该设计在现有数据上为何可行 (分组/前后期/工具/断点是否存在)")}
        if kind == "measurement_and_missing":
            if study.measurement_notes.strip() and study.missing_data_handling.strip():
                return True, (f"测量: {study.measurement_notes.strip()}; "
                              f"缺失: {study.missing_data_handling.strip()}"), extra
            missing = []
            if not study.measurement_notes.strip():
                missing.append("测量方案")
            if not study.missing_data_handling.strip():
                missing.append("缺失数据机制与处理")
            return False, "", {"detail": "未说明" + "、".join(missing)
                                       + " (不得默认测量无误、无缺失)"}
        if not study.error_structure.strip():
            return False, "", {"detail": "未说明误差结构 (聚类/异方差/自相关), 区间估计可能不可靠"}
        return True, study.error_structure.strip(), extra

    return False, "", {"detail": f"未知规则型义务 {kind}"}


def design_necessity_evaluation(claim: Any) -> dict[str, Any] | None:
    """设计/计数类存在性命题的确定性判定 (纯计算, 零 IO, 不写状态)。

    **只用命题记录里的参数重算**, 不读义务文本里的数字 —— 因此"改参数留旧证书"的
    偷换会在对齐检查里失败。返回 `None` 表示命题没有可判定的设计参数。
    """
    from src.research import design_feasibility as df

    if getattr(claim, "design_v", None) is None or getattr(claim, "design_k", None) is None:
        return None
    params = df.DesignParams(v=claim.design_v, k=claim.design_k,
                             lam=claim.design_lambda or 1,
                             b=claim.design_b, r=claim.design_r)
    try:
        report = df.check(params)
    except Exception as e:  # noqa: BLE001 - 判定失败必须保持未决, 不得冒充结论
        return {
            "verdict": "error",
            "arguments": {},
            "premises": [],
            "checked_formula": "",
            "digest": "",
            "raw_output": f"可行性判定执行失败: {type(e).__name__}: {e}",
            "input_hash": "",
            "sources": [],
            "detail": f"可行性判定执行失败: {type(e).__name__}: {e}",
        }

    certificate = report.certificate_dict()
    digest = report.certificate_digest()
    certificate["sha256"] = digest
    premises = [f"{item.theorem_cn}: {item.statement}" for item in report.checks]
    premises.append("引用定理: " + ("、".join(report.sources()) or "-"))
    premises += [f"未判定: {item.condition} ({item.reason})" for item in report.unchecked]
    checked_formula = (f"r=λ(v-1)/(k-1)={params.lam}·({params.v}-1)/({params.k}-1)="
                       f"{report.r}; b=vr/k={params.v}·{report.r}/{params.k}={report.b}")
    if report.verdict == "nonexistent":
        detail = ("证书: 存在被违反的必要条件 → 不存在。判定链: "
                  + " | ".join(f"{item.claim_cn}: {item.conclusion}" for item in report.checks
                               if not item.result)
                  + f"; 引用定理: {'、'.join(report.sources()) or '-'}"
                  + f"; 证书 sha256={digest[:16]}")
    elif report.verdict == "necessary_met":
        detail = ("必要条件全部满足, 但存在性未定 (未关闭): 需要显式构造或更强的排除定理。"
                  + "已通过: " + "; ".join(f"{item.claim_cn} ({item.conclusion})"
                                           for item in report.checks if item.result)
                  + (f"; 未判定: {'; '.join(item.reason for item in report.unchecked)}"
                     if report.unchecked else ""))
    else:
        detail = ("判定信息不足, 需要补充计数约束: "
                  + json.dumps(certificate.get("design"), ensure_ascii=False))
    return {
        "verdict": report.verdict,
        "arguments": {
            "design_v": params.v, "design_k": params.k, "design_lambda": params.lam,
            "design_b": params.b, "design_r": params.r,
            "design_verdict": report.verdict,
            "design_report": certificate,
        },
        "premises": premises,
        "checked_formula": checked_formula,
        "digest": digest,
        "raw_output": report.describe(),
        "input_hash": hash_payload({"design": certificate.get("design"),
                                    "counts": certificate.get("counts")}),
        "sources": report.sources(),
        "detail": detail,
    }
