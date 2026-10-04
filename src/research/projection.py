from __future__ import annotations

"""团队成果投影: 把子智能体提交的候选登记为可查询对象 (合并计划 §5.2 / §6.1)。

职责边界 (必须说清, 否则就成了第二个判定层)
------------------------------------------
- 本模块做的是**登记**: 候选变更带幂等键写进 `ResearchStore`, 成为可被下游角色
  读取的版本化对象, 并保留"哪次任务、哪个依据版本、谁提交的"审计线索;
- 本模块**不做判定**: 它不写命题真值 (`supported/refuted`), 不产生验证等级,
  不改交付等级。这些都是判定层 (`verification/`、`acceptance.py`、中央状态规则)
  的职责, 智能体只能提交候选 (§5.2、§14.3)。

为什么要它
----------
没有这一层, 子智能体提交的候选就只停在内存里: 写作角色看不到证据, 审阅角色看不到
稿件, "依据哪些版本产出"也无从追溯 —— 合并计划 §6.1 要求研究产物都要登记,
并把引用写进图状态而不是复制正文。
"""

from typing import Any

from src.agents.protocol import AgentResult, AgentTask, ChangeProposal, ObjectRef
from src.research.schemas import stable_id, utcnow
from src.research.store import ResearchStore

__all__ = [
    "KIND_MAP",
    "TeamProjection",
    "latest_versions",
    "object_rows",
]

#: 候选变更的对象种类 -> 存储 kind。**一次迁移中每种对象只有一个权威写入口**:
#: 这里就是这些种类的唯一写入口 (除判定层自己产出的 verification/claim 状态)。
KIND_MAP: dict[str, str] = {
    "evidence": "evidence",
    "source": "evidence",
    "card": "evidence",
    "case": "evidence",
    "dataset": "evidence",
    "model": "model",
    "assumption": "assumption",
    "definition": "definition",
    "claim": "claim",
    "validation_plan": "validation_plan",
    "manuscript": "manuscript",
    "figure": "figure",
    "review_issue": "review_issue",
    "revision_task": "review_issue",
}


class TeamProjection:
    """把团队成员提交的候选登记进研究存储, 并提供角色视图所需的查询。"""

    def __init__(self, store: ResearchStore, *, run_id: str = "") -> None:
        self.store = store
        #: 本投影所属的运行 (§3.3 G14): `rows()` 据此只返回本次运行的对象。
        #: 留空 = 项目级投影 (调用方要显式这样构造, 例如归档浏览)。
        self.run_id = str(run_id or "")
        self.registered: dict[str, int] = {}      # proposal_id -> 存储版本
        self.skipped: list[dict[str, str]] = []   # 未登记的候选与原因

    # ---- 登记 ----
    def register(self, task: AgentTask, result: AgentResult) -> dict[str, int]:
        """登记一次成果里的全部候选变更; 返回 `{object_id: version}`。

        幂等: 同一候选的 `idempotency_key` 已登记过则跳过 (重连/重试不重复写入)。
        """
        versions: dict[str, int] = {}
        for proposal in result.proposed_changes:
            version = self._register_one(task, result, proposal)
            if version is not None:
                versions[proposal.object_id or proposal.proposal_id] = version
        return versions

    def _register_one(self, task: AgentTask, result: AgentResult,
                      proposal: ChangeProposal) -> int | None:
        kind = KIND_MAP.get(proposal.kind)
        if kind is None:
            self.skipped.append({"proposal_id": proposal.proposal_id,
                                 "reason": f"未登记的候选种类 {proposal.kind!r}"})
            return None
        key = proposal.idempotency_key(task.task_id)
        if self.store.has_event(f"projection:{key}"):
            return None                      # 幂等命中: 不重复登记
        payload = dict(proposal.payload)
        object_id = proposal.object_id or _object_id_for(kind, proposal, task)
        payload.setdefault("id", object_id)
        payload["_submitted_by"] = result.agent or task.agent
        payload["_task_id"] = task.task_id
        payload["_agent_run_id"] = result.agent_run_id
        payload["_plan_version"] = task.plan_version
        payload["_proposal_id"] = proposal.proposal_id
        payload["_input_versions"] = dict(proposal.input_versions)
        payload["_submitted_at"] = utcnow()
        payload["_may_change_conclusion"] = proposal.may_change_conclusion
        payload["_rationale"] = proposal.rationale
        # 保留**候选自己的种类** (§6.1): `KIND_MAP` 把 source/card/case/dataset 都归到
        # `evidence` 存储族 (为了让查询与版本化只有一处实现), 但若不额外留一个区分字段,
        # "这条是原始来源 / 案例卡 / 数据集卡"就在权威记录里彻底消失 —— 计划书明确要求
        # "不能把 case/dataset 无区别映成 evidence 丢类型"。族用于存储, 这个字段用于语义。
        payload["_candidate_kind"] = str(proposal.kind)
        # 对象必须记住自己的**运行身份** (§3.3 G14): 否则查询只能读"项目全部最新对象",
        # 同项目两次运行的对象会互相串; 有了这三个字段才能按 problem/run 裁剪。
        payload["_scope"] = {
            "project_id": str(task.project_id),
            "problem_id": str(task.problem_id),
            "run_id": str(task.run_id),
        }
        try:
            version = self.store.put(kind, object_id, payload,
                                     expected_revision=proposal.expected_revision)
        except Exception as e:  # noqa: BLE001 - 版本冲突等如实记录, 不静默覆盖
            self.skipped.append({"proposal_id": proposal.proposal_id,
                                 "reason": f"登记失败: {e}"})
            self.store.append_event("projection_rejected", {
                "task_id": task.task_id, "proposal_id": proposal.proposal_id,
                "kind": kind, "reason": str(e),
            })
            return None
        self.store.append_event("projection_registered", {
            "task_id": task.task_id, "proposal_id": proposal.proposal_id,
            "kind": kind, "object_id": object_id, "version": version,
            "agent": result.agent or task.agent,
        }, idempotency_key=f"projection:{key}")
        self.registered[proposal.proposal_id] = version
        return version

    # ---- 查询 (角色视图) ----
    def rows(self, kind: str, *, limit: int = 40) -> list[dict[str, Any]]:
        """本运行的对象行。

        默认**按本运行的 `run_id` 裁剪**: `TeamProjection` 总是绑定在某次运行上,
        返回项目全部最新对象会让"同项目的另一次运行"混进当前画面 (§3.3 G14)。
        需要项目级视图时调用 `project_rows`。
        """
        return object_rows(self.store, kind, limit=limit,
                           run_id=getattr(self, "run_id", "") or "")

    def project_rows(self, kind: str, *, limit: int = 40) -> list[dict[str, Any]]:
        """项目级对象行 (刻意跨运行, 用于历史归档/对比)。"""
        return object_rows(self.store, kind, limit=limit)

    def evidence_rows(self) -> list[dict[str, Any]]:
        return self.rows("evidence")

    def claim_rows(self) -> list[dict[str, Any]]:
        return self.rows("claim")

    def manuscript_rows(self) -> list[dict[str, Any]]:
        return self.rows("manuscript")

    def latest_versions(self) -> dict[str, int]:
        return latest_versions(self.store)


def object_rows(store: ResearchStore, kind: str, *, limit: int = 40,
                run_id: str = "") -> list[dict[str, Any]]:
    """取某类对象的最新版本行 (带版本号)。

    `run_id` 非空时**只返回该次运行产出的对象** (§3.3 G14): 项目级 `list_latest`
    会把同一项目的历史运行全部混在一起, 默认视图必须按当前运行裁剪。
    留空表示"刻意要项目级视图" (例如历史归档浏览), 调用方要显式选择。
    """
    stored = KIND_MAP.get(kind, kind)
    rows = store.list_latest(stored)
    if run_id:
        rows = [row for row in rows
                if str((row.get("_scope") or {}).get("run_id", "")) == run_id]
    rows = rows[:limit]
    for row in rows:
        object_id = str(row.get("id", ""))
        if object_id:
            row["version"] = store.latest_version(stored, object_id)
    return rows


def latest_versions(store: ResearchStore) -> dict[str, int]:
    """把各类对象的版本索引合并为 `{object_id: version}` (read-set 检查用)。"""
    merged: dict[str, int] = {}
    for stored in sorted(set(KIND_MAP.values())):
        merged.update(store.version_index(stored))
    return merged


def _object_id_for(kind: str, proposal: ChangeProposal, task: AgentTask) -> str:
    """新建对象时派生**确定性** ID。

    确定性很重要: 重复导入/重放同一批候选时不产生内容相同但 ID 不同的对象,
    否则幂等键失效、版本历史被污染 (合并计划 §6.3: 来源 ID/hash 是稳定身份)。
    """
    basis = (kind, proposal.kind, task.project_id, task.problem_id,
             proposal.rationale, _stable_payload(proposal.payload))
    return stable_id(f"{kind[:3]}obj", *basis)


def _stable_payload(payload: dict[str, Any]) -> str:
    import json

    keys = ("source_id", "title", "name", "statement", "locator", "schema",
            "issue_id", "paper_id", "figure_id")
    return json.dumps({k: payload.get(k) for k in keys if payload.get(k) is not None},
                      sort_keys=True, ensure_ascii=False, default=str)


def refs_of(rows: list[dict[str, Any]]) -> list[ObjectRef]:
    return [ObjectRef(id=str(row.get("id", "")), version=int(row.get("version", 1) or 1))
            for row in rows if row.get("id")]
