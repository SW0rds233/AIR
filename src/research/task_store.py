from __future__ import annotations

"""团队任务生命周期与事务一致性 (合并计划 §5.3 / §6.3 / §10 M4)。

三个必须解决的问题
------------------
1. **任务生命周期要持久化**: `queued/running/waiting/completed/partial/failed/
   cancelled` 必须能跨重连与进程重启恢复, 否则"断线重放、停止、恢复、重试不重复提交"
   无从上谈起 (M4 验收)。
2. **read-set 一致性**: `ResearchStore.put()` 已有 `expected_revision`, 但
   `submit_step()` 不接收"整个读取集合的期望版本"。于是"Agent 依据 v2 产出、对象已到
   v3"仍会被合入 —— 这就丢了那份成果本该触发的复核。本模块在**同一提交路径**里做
   版本检查、候选接受、依赖失效与事件写入, 只用 Python 锁不能替代它 (§6.3)。
3. **幂等续跑**: 已提交的任务在恢复时必须跳过; 重试用**原 task_id + 新 attempt/
   agent_run_id**, 防止重连或超时触发重复付费调用 (§5.3)。

设计取舍: 任务与运行记录复用同一 `ResearchStore`(SQLite) 持久化基础, 不新造第二个
存储 —— 合并计划 §6.1 要求"一次迁移中每种对象只能指定唯一权威写入口"。
"""

import json
from enum import Enum
from typing import Any

from src.agents.protocol import (
    RETIRED_STATUSES,
    AgentResult,
    AgentRun,
    AgentTask,
    TaskOutcome,
    TaskStatus,
    UsageRecord,
    utcnow,
)
from src.research.schemas import stable_id
from src.research.store import ResearchStore

__all__ = [
    "KIND_AGENT_RUN",
    "KIND_TASK",
    "ReadSetConflict",
    "TaskNotFound",
    "TaskStore",
    "check_read_set",
]

KIND_TASK = "team_task"
KIND_AGENT_RUN = "agent_run"


class TaskNotFound(KeyError):
    """任务不存在 (调用方按缺失处理, 不静默建新任务)。"""


class ReadSetConflict(RuntimeError):
    """依据的输入版本已经过期 (对象在派工之后又换代了)。

    这不是错误处理细节, 而是**科研正确性**要求: 依据 v2 得出的结论不能直接合到 v3
    之上。拒绝合入, 记录过期, 由主控决定重做或重新核对 (§6.3)。
    """

    def __init__(self, stale: dict[str, tuple[int, int]], message: str = ""):
        self.stale = stale          # object_id -> (依据版本, 当前版本)
        detail = ", ".join(f"{k}@v{old}->v{new}" for k, (old, new) in stale.items())
        super().__init__(message or f"输入版本已过期: {detail}")


def check_read_set(read_set: dict[str, int],
                   current: dict[str, int]) -> dict[str, tuple[int, int]]:
    """比较"依据的版本"与"当前版本", 返回过期项。

    `read_set` 里出现而 `current` 里没有的对象**不算过期**(可能是新建对象);
    只有当前版本严格大于依据版本才判过期 —— 相同版本是正常并发读。
    """
    stale: dict[str, tuple[int, int]] = {}
    for object_id, used in (read_set or {}).items():
        latest = current.get(object_id)
        if latest is None:
            continue
        if int(latest) > int(used):
            stale[object_id] = (int(used), int(latest))
    return stale


class TaskStore:
    """团队任务的权威存储 (复用 `ResearchStore` 的 SQLite 与事务)。"""

    def __init__(self, project_id: str, store: ResearchStore | None = None,
                 *, db_path: str | None = None) -> None:
        self.project_id = project_id
        self._owns_store = store is None
        self.store = store or ResearchStore(project_id, db_path)

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> TaskStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 生命周期 ----
    def create(self, task: AgentTask, *, plan_id: str = "") -> int:
        """登记任务为 `queued`。

        幂等: 同一 `idempotency_key` 的 `queued/running` 任务已存在时**不重复登记**,
        返回既有版本 (恢复与重连的关键)。
        """
        existing = self.find_by_key(task.idempotency_key)
        if existing and existing["status"] in (TaskStatus.queued.value,
                                               TaskStatus.running.value):
            return int(existing["version"])
        payload = self._payload(task, status=TaskStatus.queued.value, plan_id=plan_id)
        return self.store.put(KIND_TASK, task.task_id, payload)

    def update_status(self, task_id: str, status: TaskStatus | str, *,
                      note: str = "", plan_id: str = "") -> int:
        current = self.task(task_id)
        value = status.value if isinstance(status, TaskStatus) else str(status)
        payload = dict(current)
        payload.pop("version", None)
        payload.pop("id", None)
        payload.pop("created_at", None)
        payload["status"] = value
        payload["updated_at"] = utcnow()
        if note:
            payload["status_note"] = note
        if plan_id:
            payload["plan_id"] = plan_id
        return self.store.put(KIND_TASK, task_id, payload,
                              expected_revision=int(current["version"]))

    def start(self, task_id: str, agent_run: AgentRun) -> int:
        """标记任务进入 `running` 并登记本次执行 (attempt 递增在新 run 上)。"""
        self.record_run(agent_run)
        return self.update_status(task_id, TaskStatus.running,
                                  note=f"attempt {agent_run.attempt}")

    def finish(self, task_id: str, result: AgentResult) -> int:
        """写入终态与结果摘要 (结果全文不铺进任务记录, 只留引用与摘要)。"""
        current = self.task(task_id)
        payload = dict(current)
        payload.pop("version", None)
        payload.pop("id", None)
        payload.pop("created_at", None)
        payload["status"] = _status_for(result.outcome).value
        payload["updated_at"] = utcnow()
        payload["outcome"] = result.outcome.value
        payload["summary"] = result.summary
        payload["failure_reason"] = result.failure_reason
        payload["agent_run_id"] = result.agent_run_id
        payload["needs"] = [n.to_dict() for n in result.followup_needs]
        payload["unresolved"] = list(result.unresolved)
        payload["issue_ids"] = [i.issue_id for i in result.issues]
        payload["usage"] = result.usage.to_dict()
        payload["artifact_refs"] = [a.to_dict() for a in result.artifact_refs]
        payload["proposal_ids"] = [p.proposal_id for p in result.proposed_changes]
        return self.store.put(KIND_TASK, task_id, payload,
                              expected_revision=int(current["version"]))

    def cancel(self, task_id: str, reason: str = "") -> int:
        return self.update_status(task_id, TaskStatus.cancelled, note=reason)

    # ---- 读取 ----
    def task(self, task_id: str) -> dict[str, Any]:
        record = self.store.get(KIND_TASK, task_id)
        if record is None:
            raise TaskNotFound(task_id)
        record["version"] = self.store.latest_version(KIND_TASK, task_id)
        return record

    def maybe_task(self, task_id: str) -> dict[str, Any] | None:
        try:
            return self.task(task_id)
        except TaskNotFound:
            return None

    def find_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        return next((t for t in self.tasks()
                     if t.get("idempotency_key") == idempotency_key), None)

    def tasks(self, *, status: str = "") -> list[dict[str, Any]]:
        rows = self.store.list_latest(KIND_TASK)
        out: list[dict[str, Any]] = []
        for row in rows:
            row["version"] = self.store.latest_version(KIND_TASK, str(row.get("task_id", "")))
            if status and row.get("status") != status:
                continue
            out.append(row)
        return out

    def by_status(self, status: TaskStatus | str) -> list[dict[str, Any]]:
        return self.tasks(status=status.value if isinstance(status, TaskStatus)
                          else str(status))

    # ---- 恢复 ----
    def recoverable(self) -> dict[str, list[dict[str, Any]]]:
        """恢复视图: 已提交(跳过)/运行中/待派/受阻失败。

        - `done`: 已提交, 恢复时**跳过** (幂等);
        - `running`: 上次中断时仍在跑, 需要识别未完成的工具调用;
        - `queued`: 尚未派发;
        - `retryable`: failed/blocked, 重试用原 task_id + 新 attempt。
        """
        tasks = self.tasks()
        running = [t for t in tasks if t.get("status") == TaskStatus.running.value]
        return {
            "done": [t for t in tasks if t.get("status") in RETIRED_STATUSES],
            "running": running,
            "queued": [t for t in tasks if t.get("status") == TaskStatus.queued.value],
            "waiting": [t for t in tasks if t.get("status") == TaskStatus.waiting.value],
            "retryable": [t for t in tasks
                          if t.get("status") in (TaskStatus.failed.value,
                                                 TaskStatus.cancelled.value)],
            "unfinished_tool_runs": self.store.unresolved_tool_runs(),
        }

    def next_attempt(self, task_id: str) -> int:
        """重试时的 attempt 号 (任务记录里的 attempt + 1)。"""
        record = self.maybe_task(task_id)
        if record is None:
            return 1
        return int(record.get("attempt", 1) or 1) + 1

    # ---- agent run ----
    def record_run(self, run: AgentRun) -> int:
        payload = run.to_dict()
        payload.pop("agent_run_id", None)
        payload["run_id"] = run.agent_run_id
        payload["task_id"] = run.task_id
        existing = self.store.get(KIND_AGENT_RUN, run.agent_run_id)
        if existing is not None:
            return self.store.latest_version(KIND_AGENT_RUN, run.agent_run_id)
        return self.store.put(KIND_AGENT_RUN, run.agent_run_id, payload)

    def runs(self, task_id: str = "") -> list[dict[str, Any]]:
        rows = self.store.list_latest(KIND_AGENT_RUN)
        if not task_id:
            return rows
        return [row for row in rows if row.get("task_id") == task_id]

    # ---- 事务提交 (read-set 检查 + 候选接受 + 事件同事务) ----
    def commit_result(self, task: AgentTask, result: AgentResult, *,
                      accepted_writes: list[tuple[str, str, dict[str, Any]]] | None = None,
                      events: list[tuple[str, dict]] | None = None,
                      current_versions: dict[str, int] | None = None) -> dict[str, Any]:
        """提交一次任务成果。

        步骤 (缺一不可):
        1. 用**对象当前版本**与结果的 read-set 比对; 过期即抛 `ReadSetConflict`
           —— 不把依据旧版本的成果合入, 也不静默丢弃;
        2. 通过后把任务终态、接受的候选写入与事件放进同一事务提交;
        3. 返回 `{versions, idempotency_key}` 供主控记录。

        `current_versions` 由调用方给出 (`ResearchStore.version_index(kind)` 的并集);
        缺省时只跳过版本检查并**如实标注**, 不假装检查过。
        """
        read_set = dict(result.input_versions or {})
        if current_versions is None:
            checked = False
            stale: dict[str, tuple[int, int]] = {}
        else:
            checked = True
            stale = check_read_set(read_set, current_versions)
        if stale:
            self.store.append_event("input_version_conflict", {
                "task_id": task.task_id, "agent": task.agent,
                "stale": {k: list(v) for k, v in stale.items()},
                "outcome": result.outcome.value,
            })
            raise ReadSetConflict(stale)

        writes: list[tuple[str, str, dict[str, Any]]] = list(accepted_writes or [])
        payload = result.to_dict()
        payload["_read_set"] = read_set
        payload["_read_set_checked"] = checked
        writes.append((KIND_TASK, task.task_id, _task_payload(task, result)))
        key = stable_id("commit", task.task_id, task.idempotency_key,
                        result.agent_run_id or "no-run")
        event_rows = list(events or []) + [("task_result", {
            "task_id": task.task_id, "agent": task.agent,
            "outcome": result.outcome.value,
            "read_set_checked": checked,
        })]
        versions = self.store.submit_step(writes, event_rows, idempotency_key=key)
        return {"versions": versions, "idempotency_key": key, "read_set_checked": checked}

    # ---- 内部 ----
    def _payload(self, task: AgentTask, *, status: str, plan_id: str = "") -> dict[str, Any]:
        data = task.to_dict()
        data["status"] = status
        data["task_id"] = task.task_id
        data["created_at"] = task.created_at
        data["updated_at"] = utcnow()
        data["plan_id"] = plan_id
        return data


def _status_for(outcome: TaskOutcome) -> TaskStatus:
    return {
        TaskOutcome.completed: TaskStatus.completed,
        TaskOutcome.partial: TaskStatus.partial,
        TaskOutcome.blocked: TaskStatus.waiting,
        TaskOutcome.failed: TaskStatus.failed,
        TaskOutcome.cancelled: TaskStatus.cancelled,
    }.get(outcome, TaskStatus.failed)


def _task_payload(task: AgentTask, result: AgentResult) -> dict[str, Any]:
    data = task.to_dict()
    data["status"] = _status_for(result.outcome).value
    data["updated_at"] = utcnow()
    data["outcome"] = result.outcome.value
    data["summary"] = result.summary
    data["failure_reason"] = result.failure_reason
    data["agent_run_id"] = result.agent_run_id
    data["needs"] = [n.to_dict() for n in result.followup_needs]
    data["unresolved"] = list(result.unresolved)
    return data


def new_agent_run(task: AgentTask, *, attempt: int | None = None,
                  prompt_version: str = "", models: list[str] | None = None,
                  agent_run_id: str = "") -> AgentRun:
    """构造一次执行记录 (重试复用 task_id, 换新的 agent_run_id)。

    执行身份是**确定性**的 `(task_id, attempt, prompt_version)`: 断线重连或超时重试
    时用同一个 id 去重, 避免同一 attempt 被登记两次并重复计费。
    """
    run_id = agent_run_id or stable_id("agentrun", task.task_id,
                                       attempt or task.attempt, prompt_version or "")
    return AgentRun(agent_run_id=run_id, task_id=task.task_id, agent=task.agent,
                    attempt=attempt or task.attempt,
                    status=TaskStatus.running,
                    prompt_version=prompt_version or "",
                    models=list(models or []),
                    input_versions={ref.id: ref.version for ref in task.input_refs},
                    usage=UsageRecord(), started_at=utcnow())


def fingerprint_task(task: AgentTask) -> str:
    """任务内容指纹 (审计: 同一 idempotency_key 的任务内容是否一致)。"""
    body = json.dumps({"agent": task.agent, "objective": task.objective,
                       "input_refs": [r.model_dump() for r in task.input_refs]},
                      sort_keys=True, ensure_ascii=False)
    return stable_id("taskfp", task.task_id, body)


class TeamRunState(str, Enum):
    """团队运行状态 (与"单个任务状态"区分)。"""

    queued = "queued"
    running = "running"
    waiting_user = "waiting_user"
    done = "done"
    stopped = "stopped"
    error = "error"


def summarize_recovery(view: dict[str, list[dict[str, Any]]]) -> str:
    """把恢复视图渲染成一句人可读的说明 (界面与日志使用)。"""
    return (f"已提交 {len(view.get('done', []))} 个任务 (恢复时跳过), "
            f"运行中 {len(view.get('running', []))}, 待派 {len(view.get('queued', []))}, "
            f"可重试 {len(view.get('retryable', []))}, "
            f"未完成工具调用 {len(view.get('unfinished_tool_runs', []))}")
