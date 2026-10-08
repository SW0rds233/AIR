from __future__ import annotations

"""唯一提交口 (合并计划 §6.2): `ResearchCommitService`。

为什么需要它
------------
合并计划 §6.2 要求"唯一提交接口 `commit_result(task, result, execution_receipts,
expected_run_revision)` 隐藏授权、冲突、验证和落盘细节, 调用方不再拼 put/append_event"。
在此之前团队走的是 `task_store.finish()` 之后**逐条** `projection.register()` ——
事务能力没用上 (G08), 而且"谁有资格写科学状态"散落在角色、投影与判定层三处。

它做什么 (顺序即不可颠倒)
------------------------
1. **校验**: 任务/运行/角色、可写对象种类、结果 schema —— 与运行时同一份能力表;
2. **read-set**: 按"实际读取的对象版本"核对当前版本; 过期返回 `stale_input`,
   不把依据旧版本的成果合入 (科研正确性, 不是错误处理细节);
3. **拒绝候选自报科学等级**: `claim` 候选里的 `status/assurance/support_kind/
   validation_status` 一律**丢弃并重置**, 由判定层从义务与核验记录重算;
4. **核验落盘**: `verification` 候选按 `obligation_disposition_for` 重算义务处置,
   并据此写义务状态 (拒绝"角色说通过了"当结论);
5. **状态归并**: `reasoning_kernel.claim_state_for` 算出命题状态 —— 这是唯一的状态
   写入点, 零 LLM;
6. **一个事务**: 任务终态 + 接受的对象写入 + 事件 + 幂等收据同事务提交, 失败回滚。

`expected_run_revision` 语义: 调用方给出"它读到的对象版本索引"
(`projection.latest_versions()` 的并集)。缺省 (`None`) 时**跳过检查并如实标注**
(`read_set_checked=False`), 不假装检查过。
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    TaskOutcome,
    writable_kinds,
)
from src.research.projection import KIND_MAP, object_payload_for
from src.research.constraint_fidelity import conflicting_code_parameters
from src.research.reasoning_kernel import claim_state_for
from src.research.schemas import (
    Claim,
    Coverage,
    ObligationStatus,
    ProofObligation,
    ProofAttempt,
    ResearchModel,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
    coverage_for_tool,
    stable_id,
    support_kind_for_tool,
)
from src.research.task_store import (
    KIND_TASK,
    _task_payload,
    check_read_set,
)
from src.research.store import StepAlreadyApplied
from src.verification.schemas import VerificationResult

__all__ = ["CommitOutcome", "ResearchCommitService"]

#: 由**提交服务**重算、不接受角色自报的状态字段 (合并计划 §6.2 第 3 条)。
#: 只对 `claim` 生效: 别的对象没有"科学等级"这回事。
_CLAIM_STATE_FIELDS: tuple[str, ...] = (
    "status", "assurance", "support_kind", "validation_status", "coverage",
    "verification_scope", "verification_closure",
)

#: 核验记录的工具 → 义务覆盖范围 (与 `schemas.TOOL_COVERAGE` 同源, 补上规则型工具)。
_EXTRA_TOOL_COVERAGE: dict[str, Coverage] = {
    "design_necessity": Coverage.target,
    "rule": Coverage.target,
}


@dataclass
class CommitOutcome:
    """一次提交的结果 (调用方据此记事件、报错与界面显示)。"""

    accepted: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    versions: dict[str, int] = field(default_factory=dict)
    #: claim_id -> {"status": ..., "version": ...} (判定层算出的状态)
    claims: dict[str, dict[str, Any]] = field(default_factory=dict)
    read_set_checked: bool = False
    idempotent: bool = False
    stale: dict[str, tuple[int, int]] = field(default_factory=dict)
    task_saved: bool = False

    @property
    def committed(self) -> bool:
        return bool(self.accepted) and not self.stale and not self.idempotent


class ResearchCommitService:
    """团队成果的权威提交边界 (唯一写权威对象的地方)。"""

    def __init__(self, store: Any, *, project_id: str = "", problem_id: str = "",
                 run_id: str = "", task_store: Any | None = None) -> None:
        self.store = store
        self.project_id = project_id
        self.problem_id = problem_id
        self.run_id = run_id
        #: 可选: 复用 `TaskStore` 的终态写法, 保证任务记录的字段集合只有一份实现。
        self.task_store = task_store

    # ------------------------------------------------------------------
    # 提交
    # ------------------------------------------------------------------
    def commit(self, task: AgentTask, result: AgentResult, *,
               current_versions: dict[str, int] | None = None,
               events: list[tuple[str, dict]] | None = None,
               reject_reasons: list[dict[str, Any]] | None = None,
               ) -> CommitOutcome:
        outcome = CommitOutcome(rejected=list(reject_reasons or []))

        # ---- 1. 授权/种类校验 (与运行时同一份能力表; 运行时的隔离是候选级的,
        #         这里再查一遍是因为提交口是**最后**一道门) ----
        allowed = writable_kinds(task.agent)
        accepted: list[ChangeProposal] = []
        spec = self.store.get("spec", task.problem_id) if task.problem_id else None
        problem_statement = str((spec or {}).get("problem_statement") or "")
        for proposal in result.proposed_changes:
            kind = KIND_MAP.get(proposal.kind)
            if kind is None:
                outcome.rejected.append({
                    "proposal_id": proposal.proposal_id, "kind": proposal.kind,
                    "reason": f"未登记的候选种类 {proposal.kind!r}"})
                continue
            if proposal.kind not in allowed:
                outcome.rejected.append({
                    "proposal_id": proposal.proposal_id, "kind": proposal.kind,
                    "reason": (f"角色 {task.agent} 不得提交 {proposal.kind} 类变更 "
                               f"(允许: {', '.join(sorted(allowed)) or '无'})")})
                continue
            if kind == "model":
                try:
                    ResearchModel.model_validate(proposal.payload)
                except ValidationError as e:
                    outcome.rejected.append({
                        "proposal_id": proposal.proposal_id, "kind": proposal.kind,
                        "reason": f"模型字段不符合契约: {e}"})
                    continue
            if kind == "claim":
                try:
                    # Ignore self-reported proof state, but reject malformed content.
                    Claim.model_validate(_strip_claimed_state(proposal.payload))
                except ValidationError as e:
                    outcome.rejected.append({
                        "proposal_id": proposal.proposal_id, "kind": proposal.kind,
                        "reason": f"命题字段不符合契约: {e}"})
                    continue
                conflicts = conflicting_code_parameters(
                    problem_statement, str(proposal.payload.get("statement") or ""))
                if conflicts:
                    outcome.rejected.append({
                        "proposal_id": proposal.proposal_id, "kind": proposal.kind,
                        "reason": "候选命题与题面冲突: " + "；".join(conflicts)})
                    continue
            if kind == "attempt":
                try:
                    ProofAttempt.model_validate(proposal.payload)
                except ValidationError as e:
                    outcome.rejected.append({"proposal_id": proposal.proposal_id,
                        "kind": proposal.kind, "reason": f"证明尝试字段不符合契约: {e}"})
                    continue
            accepted.append(proposal)
        outcome.accepted = [p.proposal_id for p in accepted]
        if not accepted:
            # A clean review (or a blocked evidence task) has no object proposal,
            # but its task result is still an authoritative, resumable outcome.
            if current_versions is not None:
                outcome.read_set_checked = True
                stale = check_read_set(dict(result.input_versions or {}), current_versions)
                if stale:
                    outcome.stale = stale
                    return outcome
            key = stable_id("commit", task.project_id, task.problem_id, task.run_id,
                            task.task_id, task.idempotency_key,
                            result.agent_run_id or "no-run")
            try:
                self.store.submit_step(
                    [(KIND_TASK, task.task_id, _task_payload(task, result))],
                    list(events or []) + [("commit_result", {
                        "task_id": task.task_id, "agent": task.agent,
                        "run_id": task.run_id, "outcome": result.outcome.value,
                        "accepted": 0, "rejected": len(outcome.rejected),
                        "read_set_checked": outcome.read_set_checked})],
                    idempotency_key=key)
                outcome.task_saved = True
            except StepAlreadyApplied:
                outcome.idempotent = True
            return outcome

        # ---- 2. read-set: 依据的版本过期就不合入 ----
        read_set = dict(result.input_versions or {})
        if current_versions is None:
            outcome.read_set_checked = False
        else:
            outcome.read_set_checked = True
            stale = check_read_set(read_set, current_versions)
            if stale:
                outcome.stale = stale
                self.store.append_event("input_version_conflict", {
                    "task_id": task.task_id, "agent": task.agent,
                    "stale": {k: list(v) for k, v in stale.items()},
                    "outcome": result.outcome.value,
                })
                return outcome

        # ---- 3-5. 整理写入: 核验 → 义务 → 命题状态 ----
        plan = self._plan_writes(task, result, accepted)
        writes: list[tuple[str, str, dict[str, Any]]] = [
            (kind, obj_id, payload) for (kind, obj_id), payload in plan["writes"].items()]
        writes.append((KIND_TASK, task.task_id, _task_payload(task, result)))

        event_rows = list(events or []) + list(plan["events"]) + [(
            "commit_result", {
                "task_id": task.task_id, "agent": task.agent, "run_id": task.run_id,
                "outcome": result.outcome.value,
                "accepted": len(accepted), "rejected": len(outcome.rejected),
                "read_set_checked": outcome.read_set_checked,
                "claims": {cid: info.get("status", "") for cid, info in plan["claims"].items()},
            })]

        key = stable_id("commit", task.project_id, task.problem_id, task.run_id,
                        task.task_id, task.idempotency_key,
                        result.agent_run_id or "no-run")
        try:
            versions = self.store.submit_step(writes, event_rows, idempotency_key=key)
        except StepAlreadyApplied:
            # 同一 attempt 重放: 不产生第二个对象, 也不改状态
            outcome.idempotent = True
            outcome.versions = {}
            return outcome
        outcome.versions = versions
        outcome.claims = plan["claims"]
        outcome.task_saved = True
        return outcome

    # ------------------------------------------------------------------
    # 写入规划 (纯: 不碰存储, 便于单测)
    # ------------------------------------------------------------------
    def _plan_writes(self, task: AgentTask, result: AgentResult,
                     accepted: list[ChangeProposal]) -> dict[str, Any]:
        writes: dict[tuple[str, str], dict[str, Any]] = {}
        events: list[tuple[str, dict]] = []
        staged: dict[str, ChangeProposal] = {}
        reasons: dict[str, str] = {}

        # 3a. 先把除 verification 之外的候选放进来 (verification 依赖义务, 稍后处理)
        for proposal in accepted:
            kind = KIND_MAP[proposal.kind]
            if proposal.kind == "verification":
                continue
            mapped = proposal
            if kind == "claim":
                mapped = proposal.model_copy(update={"payload": _strip_claimed_state(proposal.payload)})
            object_id, payload = object_payload_for(task, result, mapped, kind)
            if proposal.kind == "claim":
                # 丢弃角色自报的科学等级, 由判定层重算 (§6.2 第 3 条)
                payload = _strip_claimed_state(payload)
            if proposal.kind == "obligation":
                # 归一化会**重建**载荷 (统一字段名与缺省值), 因此必须把唯一提交口刚写上的
                # 元数据 (`_scope` 等) 重新带上 —— 否则义务对象没有运行身份, 按 run 裁剪的
                # 视图 (交付摘要的登记计数、按 run 过滤的产物清单) 会漏掉它 (实测:
                # 命题有身份、义务没有)。
                payload = _keep_meta(
                    _normalise_obligation(payload,
                                          claim_hint=payload.get("claim_id", "")),
                    payload)
            writes[(kind, object_id)] = payload
            staged[proposal.proposal_id] = proposal

        # 证据角色只提交“相反来源”的候选关系；真正的科学状态变更在唯一提交口
        # 与证据关系同事务完成。没有可定位来源、未复核关系或引用了旧命题版本时
        # 只保留材料，不凭它推翻先前结论。
        reopened = self._reopen_for_contradictions(writes, events)

        # 3b. 核验候选: 按义务处置重算, 并写回义务状态
        touched_claims: set[str] = set(reopened) | {
            str(payload.get("id", "")) for (kind, _), payload in list(writes.items())
            if kind == "claim" and payload.get("id")
        }
        for proposal in accepted:
            if proposal.kind != "verification":
                continue
            payload = dict(proposal.payload or {})
            record_data = dict(payload.get("record") or {})
            result_data = dict(payload.get("result") or {})
            obligation_id = str(payload.get("obligation_id") or
                                (record_data.get("obligation_ref") or {}).get("id", "") or "")
            claim_id = str(record_data.get("claim_id") or payload.get("claim_id") or "")
            if not claim_id:
                continue
            obligation = self._obligation_for(obligation_id, writes)
            if obligation is None:
                events.append(("verification_orphaned", {
                    "task_id": task.task_id, "record_id": record_data.get("id", ""),
                    "obligation_id": obligation_id,
                    "reason": "核验记录指向的义务不存在, 不做义务处置"}))
                touched_claims.add(claim_id)
                _put_record(writes, record_data, task, result)
                continue
            disposition = _disposition_for(result_data, obligation)
            record_data = _apply_disposition(record_data, obligation, disposition,
                                             result_data)
            _put_record(writes, record_data, task, result)
            obligation_payload = _keep_meta(
                _apply_obligation_disposition(obligation, disposition, record_data),
                writes.get(("obligation", obligation.id)) or _scope_meta(task, result))
            writes[("obligation", obligation.id)] = obligation_payload
            touched_claims.add(claim_id)
            if disposition.reason:
                reasons[claim_id] = str(disposition.reason)
            events.extend(_verification_events(claim_id, obligation, disposition,
                                               record_data))

        # 3c. 状态归并: 受影响的命题按当前义务/记录重算
        claims: dict[str, dict[str, Any]] = {}
        for claim_id in sorted(touched_claims):
            if not claim_id:
                continue
            payload = self._claim_payload(claim_id, writes)
            if payload is None:
                continue
            claim = _claim_of(payload)
            if claim is None:
                continue
            obligations = self._obligations_for_claim(claim_id, writes)
            records = self._records_for_claim(claim_id, writes)
            disposition = claim_state_for(
                claim, obligations, records, closure=claim.verification_closure,
                aligned=_aligned)
            updated = _apply_claim_disposition(payload, disposition)
            writes[("claim", claim_id)] = updated
            claims[claim_id] = {
                "status": str(updated.get("status", "")),
                "version": int(updated.get("version") or 1),
                "validation_status": str(updated.get("validation_status", "")),
                "support_kind": str(updated.get("support_kind", "")),
            }
            events.append(("claim_state_reconciled", {
                "claim_id": claim_id, "status": str(updated.get("status", "")),
                "coverage": str(updated.get("coverage", "")),
                "support_kind": str(updated.get("support_kind", "")),
                "by": "commit_service"}))
            # 依赖失效的**触发点**: 状态真的变了才记一条, 主控据此重派受影响的
            # 写作/配图/验证方案任务。交付层另有按 `verification_closure` 的过期
            # 判定 (`acceptance._stale_closure`), 两处互补: 这里管"结论变了",
            # 那里管"依据换代了"。
            previous = str(payload.get("status", ""))
            current = str(updated.get("status", ""))
            if previous and current and previous != current:
                events.append(("claim_state_changed", {
                    "claim_id": claim_id, "previous": previous, "current": current,
                    "reason": str(reasons.get(claim_id, "")) or
                              "义务/核验记录更新导致状态变化",
                    "by": "commit_service"}))

        return {"writes": writes, "events": events, "claims": claims,
                "reasons": reasons}

    def _reopen_for_contradictions(
        self, writes: dict[tuple[str, str], dict[str, Any]],
        events: list[tuple[str, dict]],
    ) -> set[str]:
        reopened: set[str] = set()
        for (kind, _), link in list(writes.items()):
            if kind != "evidence_link" or link.get("relation") != "contradicts":
                continue
            if link.get("review_status") != "verified":
                continue
            claim_ref, source_ref = link.get("claim_ref") or {}, link.get("source_ref") or {}
            claim_id, source_id = str(claim_ref.get("id") or ""), str(source_ref.get("id") or "")
            if not claim_id or not source_id or claim_id in reopened:
                continue
            source = writes.get(("evidence", source_id)) or self.store.get("evidence", source_id)
            if not source or not (link.get("locator") or source.get("locator")
                                  or source.get("location")):
                continue
            claim = self.store.get("claim", claim_id)
            if not claim or claim.get("status") != "supported":
                continue
            # 跨问题来源/命题不能通过一个 link 改写另一项研究的结论。
            if not self._same_problem(claim) or not self._same_problem(source):
                continue
            current_version = self.store.latest_version("claim", claim_id)
            if int(claim_ref.get("version", 0) or 0) != current_version:
                events.append(("contradiction_for_old_claim_version", {
                    "claim_id": claim_id, "source_id": source_id,
                    "expected": current_version, "found": claim_ref.get("version")}))
                continue
            updated = dict(claim)
            updated.update({"status": "in_progress", "assurance": "unverified",
                            "support_kind": "none", "coverage": "step",
                            "validation_status": "unknown", "verification_scope": None,
                            "verification_closure": {}})
            updated["notes"] = (str(updated.get("notes") or "") +
                                f" 相反来源 {source_id} ({link.get('locator') or source.get('locator')})"
                                " 待重新核验").strip()
            writes[("claim", claim_id)] = updated
            for record in _list_latest(self.store, "verification"):
                if str(record.get("claim_id") or "") != claim_id or record.get("stale"):
                    continue
                changed = dict(record)
                changed["stale"] = True
                changed["raw_output"] = (str(changed.get("raw_output") or "") +
                                         f" | 相反来源 {source_id} 触发重验").strip()
                writes[("verification", str(record["id"]))] = changed
            for obligation in _list_latest(self.store, "obligation"):
                if (str(obligation.get("claim_id") or "") != claim_id or
                        not obligation.get("required", True) or
                        obligation.get("status") != "closed"):
                    continue
                changed = dict(obligation)
                changed["status"] = "open"
                changed["validation_status"] = "unchecked"
                writes[("obligation", str(obligation["id"]))] = changed
            reopened.add(claim_id)
            events.append(("evidence_contradiction_reopened", {
                "claim_id": claim_id, "source_id": source_id,
                "claim_version": current_version,
                "reason": "可定位相反来源需重新核验义务与结论"}))
        return reopened

    def _same_problem(self, payload: dict[str, Any]) -> bool:
        scope = payload.get("_scope") or {}
        project = str(scope.get("project_id") or payload.get("project_id") or "")
        problem = str(scope.get("problem_id") or payload.get("problem_id") or "")
        return ((not project or project == self.project_id) and
                (not problem or problem == self.problem_id))

    # ---- 读取辅助 (只在本事务的写入集与存储之间取值) ----
    def _obligation_for(self, obligation_id: str,
                        writes: dict[tuple[str, str], dict[str, Any]]
                        ) -> ProofObligation | None:
        if not obligation_id:
            return None
        payload = writes.get(("obligation", obligation_id))
        if payload is None:
            payload = self.store.get("obligation", obligation_id)
        return _obligation_of(payload) if payload else None

    def _obligations_for_claim(self, claim_id: str,
                               writes: dict[tuple[str, str], dict[str, Any]]
                               ) -> list[ProofObligation]:
        out: dict[str, ProofObligation] = {}
        for (kind, obj_id), payload in writes.items():
            if kind != "obligation":
                continue
            obligation = _obligation_of(payload)
            if obligation is not None and obligation.claim_id == claim_id:
                out[obj_id] = obligation
        for row in _list_latest(self.store, "obligation"):
            obligation = _obligation_of(row)
            if obligation is None or obligation.claim_id != claim_id:
                continue
            out.setdefault(obligation.id, obligation)
        return list(out.values())

    def _records_for_claim(self, claim_id: str,
                           writes: dict[tuple[str, str], dict[str, Any]]
                           ) -> list[VerificationRecord]:
        out: dict[str, VerificationRecord] = {}
        for (kind, obj_id), payload in writes.items():
            if kind != "verification":
                continue
            record = _record_of(payload)
            if record is not None and record.claim_id == claim_id:
                out[obj_id] = record
        for row in _list_latest(self.store, "verification"):
            record = _record_of(row)
            if record is None or record.claim_id != claim_id:
                continue
            out.setdefault(record.id, record)
        return list(out.values())

    def _claim_payload(self, claim_id: str,
                       writes: dict[tuple[str, str], dict[str, Any]]
                       ) -> dict[str, Any] | None:
        payload = writes.get(("claim", claim_id))
        if payload is not None:
            return dict(payload)
        row = self.store.get("claim", claim_id)
        return dict(row) if row else None


# ----------------------------------------------------------------------
# 纯辅助 (无 IO)
# ----------------------------------------------------------------------
def _strip_claimed_state(payload: dict[str, Any]) -> dict[str, Any]:
    """丢弃角色自报的科学等级。

    不能只检查 `may_change_conclusion` 布尔值 (§6.2 第 3 条): 一个角色完全可以
    在布尔为 False 的候选里写 `status="supported"`。这里直接**重置**状态字段,
    随后由 `claim_state_for` 从义务与核验记录重算。
    """
    out = dict(payload)
    for name in _CLAIM_STATE_FIELDS:
        out.pop(name, None)
    out["status"] = "proposed"
    out["assurance"] = "unverified"
    out["support_kind"] = "none"
    out["validation_status"] = "unchecked"
    out["coverage"] = "step"
    out.pop("verification_scope", None)
    return out


def _normalise_obligation(payload: dict[str, Any],
                          *, claim_hint: str = "") -> dict[str, Any]:
    """义务候选的规范化: 状态字段一律回到 `open`, 由核验决定后续。

    角色提交义务时**不该**顺带宣布它已关闭 —— 那等于绕开核验直接改结论依据。
    """
    out = dict(payload)
    out["status"] = ObligationStatus.open.value
    out["validation_status"] = ValidationStatus.unchecked.value
    out["support_kind"] = SupportKind.none.value
    if claim_hint and not out.get("claim_id"):
        out["claim_id"] = claim_hint
    return out


def _scope_meta(task: AgentTask | None, result: AgentResult | None) -> dict[str, Any]:
    """唯一提交口写在对象上的元数据 (`_scope` / 提交者 / 任务与候选身份)。

    单独抽出来, 是因为**重建载荷**的函数 (`_normalise_obligation`、
    `_apply_obligation_disposition`) 会把元数据一起丢掉 —— 而它们的结果同样要落盘。
    """
    if task is None:
        return {}
    meta: dict[str, Any] = {
        "_scope": {
            "project_id": str(task.project_id),
            "problem_id": str(task.problem_id),
            "run_id": str(task.run_id),
        },
        "_task_id": task.task_id,
    }
    if result is not None:
        meta["_submitted_by"] = result.agent or task.agent
        meta["_agent_run_id"] = result.agent_run_id
    return meta


def _keep_meta(payload: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    """保留 `original` 里 `_` 前缀的元数据 (重建载荷后的必做一步)。"""
    meta = {k: v for k, v in (original or {}).items() if k.startswith("_")}
    return {**payload, **meta}


def _put_record(writes: dict[tuple[str, str], dict[str, Any]],
                record_data: dict[str, Any],
                task: AgentTask | None = None, result: AgentResult | None = None) -> None:
    """核验记录也必须带**运行身份** (§3.3 G14): 否则按 run 裁剪的查询看不到它。"""
    record_id = str(record_data.get("id") or "")
    if not record_id:
        return
    payload = {k: v for k, v in record_data.items() if not k.startswith("_")}
    payload["id"] = record_id
    if task is not None:
        payload["_scope"] = {
            "project_id": str(task.project_id),
            "problem_id": str(task.problem_id),
            "run_id": str(task.run_id),
        }
        payload["_candidate_kind"] = "verification"
    if result is not None:
        payload["_submitted_by"] = result.agent or (task.agent if task else "")
        payload["_task_id"] = task.task_id if task else ""
        payload["_agent_run_id"] = result.agent_run_id
    writes[("verification", record_id)] = payload


def _disposition_for(result_data: dict[str, Any], obligation: ProofObligation):
    """由提交服务重算义务处置 (不接受角色自报的义务状态)。"""
    from src.research.reasoning_kernel import obligation_disposition_for

    try:
        result = VerificationResult.model_validate(result_data) if result_data else None
    except Exception:  # noqa: BLE001 - 结果载荷不完整时按"未决"处理, 不当成通过
        result = None
    if result is None:
        return obligation_disposition_for(
            _BlankResult(status="error", detail="核验结果载荷缺失或非法"),
            obligation)
    return obligation_disposition_for(result, obligation)


class _BlankResult:
    """`obligation_disposition_for` 接受的最小结果形态 (缺载荷时用它如实报未知)。"""

    def __init__(self, *, status: str, detail: str) -> None:
        self.status = status
        self.detail = detail
        self.counterexample: dict[str, Any] = {}
        self.certificate = ""
        self.raw_output = ""


def _apply_disposition(record_data: dict[str, Any], obligation: ProofObligation,
                       disposition, result_data: dict[str, Any]) -> dict[str, Any]:
    """把处置结果写进核验记录 (含 `stale` 解除) 与义务。"""
    out = dict(record_data)
    out["stale"] = not disposition.mark_usable if hasattr(disposition, "mark_usable") \
        else not getattr(disposition, "mark_record_usable", False)
    if disposition.action == "refuted":
        out["counterexample"] = dict(result_data.get("counterexample") or {})
    return out


def _apply_obligation_disposition(obligation: ProofObligation, disposition,
                                  record_data: dict[str, Any] | None = None
                                  ) -> dict[str, Any]:
    """义务的新载荷: 状态/验证维度/支持方式/覆盖范围全部来自**核验记录**。

    支持方式与覆盖范围取自记录 (`tool` 字段) 而不是义务的 `acceptance_method`:
    设计可行性义务的 `acceptance_method` 是 `rule`, 但它靠**具名定理 + 机器重算的
    参数**支持 (`theorem_application`), 按 `rule` 反查会退化成 `informal_argument`,
    结论的支持强度就此被悄悄降低。
    """
    payload = obligation.model_dump(mode="json")
    payload.pop("version", None)
    payload["status"] = {
        "closed": ObligationStatus.closed.value,
        "refuted": ObligationStatus.refuted.value,
    }.get(disposition.action, ObligationStatus.blocked.value)
    validation = disposition.validation_status
    payload["validation_status"] = getattr(validation, "value",
                                           str(validation or "unknown"))
    record = record_data or {}
    tool = str(record.get("tool") or payload.get("acceptance_method") or "")
    support = SupportKind.none
    if record.get("support_kind"):
        try:
            support = SupportKind(str(record["support_kind"]))
        except ValueError:
            support = SupportKind.none
    if support == SupportKind.none:
        support = support_kind_for_tool(tool)
    if support != SupportKind.none:
        payload["support_kind"] = support.value
    payload["coverage"] = _coverage_value(tool)
    payload["detail"] = disposition.reason or payload.get("detail", "")
    return payload


def _coverage_value(tool: str) -> str:
    coverage = _EXTRA_TOOL_COVERAGE.get(tool) or coverage_for_tool(tool)
    return coverage.value


def _verification_events(claim_id: str, obligation: ProofObligation, disposition,
                         record_data: dict[str, Any]) -> list[tuple[str, dict]]:
    if disposition.action == "closed":
        return [("obligation_closed", {"claim_id": claim_id,
                                       "obligation_id": obligation.id,
                                       "tool": str(record_data.get("tool", ""))})]
    if disposition.action == "refuted":
        return [("claim_refuted", {"claim_id": claim_id,
                                   "witness": dict(record_data.get("counterexample") or {})})]
    return []


def _apply_claim_disposition(payload: dict[str, Any], disposition) -> dict[str, Any]:
    out = dict(payload)
    if disposition.status is not None:
        out["status"] = getattr(disposition.status, "value", str(disposition.status))
    if disposition.coverage is not None:
        out["coverage"] = getattr(disposition.coverage, "value", str(disposition.coverage))
    if disposition.validation_status is not None:
        out["validation_status"] = getattr(disposition.validation_status, "value",
                                           str(disposition.validation_status))
    if disposition.support_kind is not None:
        out["support_kind"] = getattr(disposition.support_kind, "value",
                                      str(disposition.support_kind))
    if disposition.assurance is not None:
        out["assurance"] = getattr(disposition.assurance, "value", str(disposition.assurance))
    if disposition.verification_scope is not None:
        out["verification_scope"] = getattr(disposition.verification_scope, "value",
                                            str(disposition.verification_scope))
    if disposition.note:
        out["notes"] = (str(out.get("notes") or "") + " " + disposition.note).strip()
    # `version` 是科学陈述/编码的版本，不是对象存储的修订号。状态归并只改变
    # 证明状态，不能把 v2 命题重置为默认 v1；存储修订号由 ResearchStore 单独管理。
    return out


def _claim_of(payload: dict[str, Any]) -> Claim | None:
    try:
        return Claim.model_validate({k: v for k, v in payload.items()
                                     if not k.startswith("_")})
    except Exception:  # noqa: BLE001 - 字段不全的候选不参与状态归并
        return None


def _obligation_of(payload: dict[str, Any] | None) -> ProofObligation | None:
    if not payload:
        return None
    try:
        return ProofObligation.model_validate({k: v for k, v in payload.items()
                                               if not k.startswith("_")})
    except Exception:  # noqa: BLE001
        return None


def _record_of(payload: dict[str, Any] | None) -> VerificationRecord | None:
    if not payload:
        return None
    try:
        return VerificationRecord.model_validate({k: v for k, v in payload.items()
                                                  if not k.startswith("_")})
    except Exception:  # noqa: BLE001
        return None


def _list_latest(store: Any, kind: str) -> list[dict[str, Any]]:
    try:
        return list(store.list_latest(kind) or [])
    except Exception:  # noqa: BLE001 - 某类读不出来不等于"该类为空", 但不能崩掉提交
        return []


def _aligned(record: VerificationRecord, claim: Claim) -> bool:
    from src.research.acceptance import _aligned as aligned

    return bool(aligned(record, claim))


def should_commit(result: AgentResult) -> bool:
    """是否值得提交 (取消/完全失败的成果不写权威对象)。"""
    return result.outcome in (TaskOutcome.completed, TaskOutcome.partial) or \
        bool(result.proposed_changes)
