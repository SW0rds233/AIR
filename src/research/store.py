from __future__ import annotations

"""理论研究对象的版本化存储 (P0)。

- 以 SQLite 为权威存储 (Chroma 只提供检索, 不作为命题真伪来源)。
- 每个对象按 (project_id, kind, obj_id, version) 保存, 旧版本保留用于审计。
- 事件日志带幂等键, 避免暂停恢复/重试重复增加引理或重复支付工具调用成本。
- 快照以 JSON 保存, 用于冻结交付与断点恢复。

计划书 §9.2 的两条硬约束:
1. `INSERT OR REPLACE` 不适合不可变研究历史 —— `append_version` 用纯 INSERT:
   同版本同内容幂等返回, 同版本不同内容抛 VersionConflict (绝不覆盖)。
2. 对象写入与事件登记必须处于同一事务 —— `submit_step` 把一次研究步骤的
   全部对象版本与事件放在一个 SQLite 事务里提交, 并用幂等键去重。
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from src.research.schemas import ActionExecution, ResearchSnapshot, hash_payload, utcnow

KIND_SPEC = "spec"
KIND_ASSUMPTION = "assumption"
KIND_DEFINITION = "definition"
KIND_EVIDENCE = "evidence"
KIND_EVIDENCE_LINK = "evidence_link"
KIND_CLAIM = "claim"
KIND_ATTEMPT = "attempt"
KIND_OBLIGATION = "obligation"
KIND_VERIFICATION = "verification"
KIND_ACTION = "action"
KIND_NOVELTY = "novelty"
KIND_MODEL = "model"
KIND_ROUTE = "route"
KIND_GAP = "gap"
KIND_RUNTIME = "runtime"
#: 团队运行的**可恢复循环状态** (合并计划 §3.3 G16): 画像/计划/已交回成果/派工历史。
#: 与 `KIND_RUNTIME` 分开: 后者是旧理论引擎的运行计数 (动作数/用量/路由状态),
#: 两者字段与语义都不同, 混用会让"恢复到哪一步"说不清。
KIND_TEAM_RUN = "team_run"


class RevisionConflict(RuntimeError):
    """单次变更带 expected_revision; 版本不一致时拒绝写入。"""


class VersionConflict(RuntimeError):
    """同一对象版本被写入不同内容; 研究历史不可覆盖。"""


class StepAlreadyApplied(RuntimeError):
    """幂等键命中: 该研究步骤已提交过, 本次不重复执行。"""


def default_db_path(project_id: str) -> Path:
    from src.config import DATA_DIR

    return DATA_DIR / "research" / f"{project_id}.sqlite"


class ResearchStore:
    def __init__(self, project_id: str, db_path: str | Path | None = None):
        self.project_id = project_id
        self.db_path = Path(db_path) if db_path else default_db_path(project_id)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS objects (
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    obj_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, kind, obj_id, version)
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idem
                    ON events(project_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    project_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    problem_id TEXT,
                    run_id TEXT,
                    branch_id TEXT,
                    PRIMARY KEY (project_id, snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS tool_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    problem_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    input_versions TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    tool_version TEXT,
                    detail TEXT,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_runs_idem
                    ON tool_runs(project_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE TABLE IF NOT EXISTS action_ledger (
                    action_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    problem_id TEXT NOT NULL,
                    run_id TEXT,
                    branch_id TEXT,
                    object_id TEXT,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    input_versions TEXT NOT NULL,
                    idempotency_key TEXT,
                    artifacts TEXT,
                    cost INTEGER DEFAULT 0,
                    detail TEXT,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_action_ledger_idem
                    ON action_ledger(project_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                """
            )
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """补齐旧库缺失的列 (历史研究库不做破坏性迁移)。"""
        wanted = {
            "action_ledger": {"object_id": "TEXT"},
            # R6: 快照升到"问题/运行/分支"身份。旧库的列先补空, 再从 data 里
            # 已有的身份字段回填 (缺失的保持 NULL —— 不猜归属)。
            "snapshots": {"problem_id": "TEXT", "run_id": "TEXT", "branch_id": "TEXT"},
        }
        for table, columns in wanted.items():
            existing = {row[1] for row in
                        self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not existing:
                continue
            for name, decl in columns.items():
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self._backfill_snapshot_identity()

    def _backfill_snapshot_identity(self) -> None:
        """从快照 JSON 回填 identity 列; 只回填 JSON 里真实存在的值。"""
        rows = self._conn.execute(
            "SELECT snapshot_id, data FROM snapshots"
            " WHERE problem_id IS NULL OR run_id IS NULL OR branch_id IS NULL"
        ).fetchall()
        for snapshot_id, payload in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError):
                continue
            updates = {key: str(data.get(key) or "")
                       for key in ("problem_id", "run_id", "branch_id")
                       if data.get(key)}
            if not updates:
                continue
            assignments = ", ".join(f"{key}=?" for key in updates)
            self._conn.execute(
                f"UPDATE snapshots SET {assignments} WHERE project_id=? AND snapshot_id=?",
                (*updates.values(), self.project_id, snapshot_id),
            )

    # ---- 读写辅助 ----
    @staticmethod
    def _prepare(data: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
        """取出调用方声明的版本号 (``__version__``), 其余字段原样保存。"""
        version = data.get("__version__")
        if version is None:
            return data, None
        payload = {k: v for k, v in data.items() if k != "__version__"}
        return payload, int(version)

    @staticmethod
    def _restore(data: dict, version: int) -> dict:
        return {**data, "__version__": version}

    def version_index(self, kind: str) -> dict[str, int]:
        """{obj_id: 最新版本号} —— 供上层重建对象的版本号。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT obj_id, MAX(version) FROM objects WHERE project_id=? AND kind=?"
                " GROUP BY obj_id",
                (self.project_id, kind),
            ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    # ---- 对象读写 ----
    def put(
        self,
        kind: str,
        obj_id: str,
        data: dict[str, Any],
        version: int | None = None,
        expected_revision: int | None = None,
    ) -> int:
        """写入对象的一个版本并**返回实际生效的版本号**。

        版本语义 (计划书 §9.2: 不可变研究历史):
        - 版本号由存储层分配, 只增不减, 每个版本一个独立行;
        - 同内容重复写入幂等返回既有版本;
        - 同版本不同内容 → VersionConflict (绝不覆盖, 也绝不静默降级)。
        """
        with self._lock:
            latest = self.latest_version(kind, obj_id)
            if expected_revision is not None and latest != expected_revision:
                raise RevisionConflict(
                    f"{kind}:{obj_id} 版本不一致 (期望 {expected_revision}, 实际 {latest})"
                )
            if version is not None and version != latest + 1:
                raise VersionConflict(
                    f"{kind}:{obj_id} 显式版本 {version} 与下一个版本 {latest + 1} 不一致"
                )
            if latest:
                current = self._raw_data(kind, obj_id, latest)
                if current is not None and self._same_payload(current, data):
                    return latest
            ver = latest + 1
            self._conn.execute(
                "INSERT INTO objects(project_id, kind, obj_id, version, data, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (self.project_id, kind, obj_id, ver, json.dumps(data, ensure_ascii=False), utcnow()),
            )
            self._conn.commit()
            return ver

    def get(self, kind: str, obj_id: str, version: int | None = None,
            strip_meta: bool = True) -> dict | None:
        with self._lock:
            if version is None:
                row = self._conn.execute(
                    "SELECT data, version FROM objects WHERE project_id=? AND kind=? AND obj_id=?"
                    " ORDER BY version DESC LIMIT 1",
                    (self.project_id, kind, obj_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT data, version FROM objects"
                    " WHERE project_id=? AND kind=? AND obj_id=? AND version=?",
                    (self.project_id, kind, obj_id, version),
                ).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        return data if strip_meta else self._restore(data, int(row[1]))

    def latest_version(self, kind: str, obj_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(version) FROM objects WHERE project_id=? AND kind=? AND obj_id=?",
                (self.project_id, kind, obj_id),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def list_latest(self, kind: str, strip_meta: bool = True) -> list[dict]:
        """返回某类对象的最新版本。

        附带的 `__obj_id__` / `__version__` 元数据只在 `strip_meta=False` 时保留;
        默认返回纯对象数据 (调用方通常只关心业务字段)。
        需要 obj_id 的场景请用 `version_index()` 或 `strip_meta=False`。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT o.data, o.version, o.obj_id FROM objects o
                JOIN (
                    SELECT obj_id, MAX(version) AS mv FROM objects
                    WHERE project_id=? AND kind=? GROUP BY obj_id
                ) m ON o.obj_id = m.obj_id AND o.version = m.mv
                WHERE o.project_id=? AND o.kind=?
                ORDER BY o.created_at, o.obj_id
                """,
                (self.project_id, kind, self.project_id, kind),
            ).fetchall()
        out = []
        for row in rows:
            data = json.loads(row[0])
            if strip_meta:
                out.append(data)
            else:
                out.append({**self._restore(data, int(row[1])), "__obj_id__": row[2]})
        return out

    def list_versions(self, kind: str, obj_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, data FROM objects WHERE project_id=? AND kind=? AND obj_id=?"
                " ORDER BY version",
                (self.project_id, kind, obj_id),
            ).fetchall()
        return [{"version": r[0], "data": json.loads(r[1])} for r in rows]

    @staticmethod
    def _same_payload(a: dict, b: dict) -> bool:
        return hash_payload(a) == hash_payload(b)

    def _raw_data(self, kind: str, obj_id: str, version: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM objects WHERE project_id=? AND kind=? AND obj_id=? AND version=?",
                (self.project_id, kind, obj_id, version),
            ).fetchone()
        return json.loads(row[0]) if row else None

    # ---- 原子研究步骤提交 (计划书 §9.2) ----
    def submit_step(
        self,
        writes: list[tuple[str, str, dict[str, Any]]],
        events: list[tuple[str, dict]] | None = None,
        idempotency_key: str = "",
    ) -> dict[str, int]:
        """把一个研究步骤的全部对象版本与事件放进同一事务提交。

        writes: [(kind, obj_id, data)] —— 各写入一个新版本 (**版本号只增不减**)。
        events: [(type, payload)] —— 与 writes 同事务写入, 共用 idempotency_key。
        返回 {obj_id: version}。

        幂等: 若 idempotency_key 已登记, 抛 StepAlreadyApplied, 调用方不得重复执行。

        事件幂等键的分配 (实测踩过): `events` 的唯一索引是
        `(project_id, idempotency_key)`, 而一个步骤可以带**多条**事件 —— 若每条都用
        同一个基键, 第二条就会撞唯一索引, 整个步骤被回滚 (表现为"提交失败但看不出
        为什么")。因此只有**第一条**事件带基键 (它同时就是"这一步已提交"的判据),
        其余带 `基键#序号`。`_event_exists(基键)` 仍然等价于"该步骤已提交"。
        """
        events = events or []
        with self._lock:
            if idempotency_key and self._event_exists(idempotency_key):
                raise StepAlreadyApplied(idempotency_key)
            versions: dict[str, int] = {}
            try:
                for kind, obj_id, data in writes:
                    ver = self.latest_version(kind, obj_id) + 1
                    self._conn.execute(
                        "INSERT INTO objects(project_id, kind, obj_id, version, data, created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (self.project_id, kind, obj_id, ver,
                         json.dumps(data, ensure_ascii=False), utcnow()),
                    )
                    versions[obj_id] = ver
                for index, (type_, payload) in enumerate(events):
                    key = None
                    if idempotency_key:
                        key = idempotency_key if index == 0 else f"{idempotency_key}#{index}"
                    self._insert_event(type_, payload, key)
                # 统一日志键: 同事务记录契约异常 (缺必需键/未登记类型)。
                # 用 `INSERT OR IGNORE`: 同一条契约异常会被**重复**观察到, 而它的
                # 键是按内容算的 —— 若让重复插入抛错, 第二次提交就整个回滚了。
                # 异常日志的设计原则是"记录而不是阻断", 因此这里只记一次。
                for type_, payload in events:
                    anomaly = self._anomaly_of(type_, payload)
                    if anomaly is not None:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO events"
                            "(project_id, type, payload, idempotency_key, created_at)"
                            " VALUES (?,?,?,?,?)",
                            (self.project_id, "log_anomaly",
                             json.dumps(anomaly, ensure_ascii=False),
                             f"anomaly:{type_}:{hash_payload(anomaly)}", utcnow()),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return versions

    def _event_exists(self, idempotency_key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE project_id=? AND idempotency_key=? LIMIT 1",
            (self.project_id, idempotency_key),
        ).fetchone()
        return row is not None

    def has_event(self, idempotency_key: str) -> bool:
        with self._lock:
            return self._event_exists(idempotency_key)

    # ---- 工具运行账本 (计划书 §9.2-1/5, §9.3) ----
    def begin_tool_run(
        self,
        run_id: str,
        tool: str,
        operation: str,
        arguments: dict,
        problem_id: str = "",
        input_versions: dict[str, int] | None = None,
        idempotency_key: str = "",
    ) -> bool:
        """事务内登记待执行的工具调用; 幂等键命中返回 False (不得重复执行)。"""
        with self._lock:
            if idempotency_key:
                row = self._conn.execute(
                    "SELECT status FROM tool_runs WHERE project_id=? AND idempotency_key=?",
                    (self.project_id, idempotency_key),
                ).fetchone()
                if row:
                    return False
            try:
                self._conn.execute(
                    "INSERT INTO tool_runs(run_id, project_id, problem_id, tool, operation,"
                    " request_hash, input_versions, status, idempotency_key, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, self.project_id, problem_id, tool, operation,
                     hash_payload({"tool": tool, "op": operation, "args": arguments}),
                     json.dumps(input_versions or {}), "running",
                     idempotency_key or None, utcnow()),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False

    def finish_tool_run(self, run_id: str, status: str, result: dict | None = None,
                        tool_version: str = "", detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tool_runs SET status=?, result=?, tool_version=?, detail=?, finished_at=?"
                " WHERE run_id=? AND project_id=?",
                (status, json.dumps(result or {}, ensure_ascii=False), tool_version, detail,
                 utcnow(), run_id, self.project_id),
            )
            self._conn.commit()

    def get_tool_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT run_id, tool, operation, status, result, tool_version, detail,"
                " request_hash, created_at, finished_at FROM tool_runs WHERE run_id=? AND project_id=?",
                (run_id, self.project_id),
            ).fetchone()
        if not row:
            return None
        return {"run_id": row[0], "tool": row[1], "operation": row[2], "status": row[3],
                "result": json.loads(row[4]) if row[4] else {}, "tool_version": row[5],
                "detail": row[6], "request_hash": row[7], "created_at": row[8],
                "finished_at": row[9]}

    def list_tool_runs(self, problem_id: str = "") -> list[dict]:
        sql = ("SELECT run_id, tool, operation, status, created_at, finished_at, detail"
               " FROM tool_runs WHERE project_id=?")
        params: list = [self.project_id]
        if problem_id:
            sql += " AND problem_id=?"
            params.append(problem_id)
        sql += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{"run_id": r[0], "tool": r[1], "operation": r[2], "status": r[3],
                 "created_at": r[4], "finished_at": r[5], "detail": r[6]} for r in rows]

    def unresolved_tool_runs(self) -> list[dict]:
        """进程中断后仍处于 running 的工具调用: 外部状态未知, 不得当作失败或成功。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, tool, operation, created_at FROM tool_runs"
                " WHERE project_id=? AND status IN ('running','pending') ORDER BY created_at",
                (self.project_id,),
            ).fetchall()
        return [{"run_id": r[0], "tool": r[1], "operation": r[2], "created_at": r[3]} for r in rows]

    # ---- 动作执行账本 (计划书 §4.1 ActionExecution) ----
    def record_action(self, execution: ActionExecution) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO action_ledger(action_id, project_id, problem_id, run_id,"
                    " branch_id, object_id, action_type, status, request_hash, input_versions,"
                    " idempotency_key, artifacts, cost, detail, started_at, finished_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (execution.action_id, self.project_id, execution.problem_id, execution.run_id,
                     execution.branch_id, execution.object_id, execution.action_type,
                     execution.status, execution.request_hash,
                     json.dumps(execution.input_versions),
                     execution.idempotency_key or None, json.dumps(execution.artifacts),
                     execution.cost, execution.detail, execution.started_at, execution.finished_at),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                self._conn.rollback()

    def list_actions(self, problem_id: str = "") -> list[dict]:
        sql = ("SELECT action_id, action_type, status, request_hash, input_versions, artifacts,"
               " cost, detail, started_at, finished_at, run_id, branch_id, object_id"
               " FROM action_ledger WHERE project_id=?")
        params: list = [self.project_id]
        if problem_id:
            sql += " AND problem_id=?"
            params.append(problem_id)
        sql += " ORDER BY started_at, action_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{"action_id": r[0], "action_type": r[1], "status": r[2], "request_hash": r[3],
                 "input_versions": json.loads(r[4] or "{}"), "artifacts": json.loads(r[5] or "[]"),
                 "cost": r[6], "detail": r[7], "started_at": r[8], "finished_at": r[9],
                 "run_id": r[10] or "", "branch_id": r[11] or "", "object_id": r[12] or ""}
                for r in rows]

    # ---- 事件日志 ----
    def _anomaly_of(self, type_: str, payload: dict) -> dict | None:
        """事件契约检查 (统一日志键): 有问题时返回一条 `log_anomaly` 事件。

        原则是**记录而不是阻断**: 事件本身仍然入库 (它是证据), 但异常会被写进同一
        事务, 因此"某个事件少了必需键"不再表现为界面上的一个 0。
        为避免递归, `log_anomaly` 自身不再检查。
        """
        if type_ == "log_anomaly":
            return None
        from src.research.logging_schema import abnormal_event

        issue = abnormal_event(payload, type_)
        if not issue:
            return None
        return {"kind": type_, "missing": issue,
                "reason": "事件契约不满足 (统一日志键)", "payload_keys": sorted(
                    str(k) for k in (payload or {}))}

    def _insert_event(self, type_: str, payload: dict,
                      idempotency_key: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO events(project_id, type, payload, idempotency_key, created_at)"
            " VALUES (?,?,?,?,?)",
            (self.project_id, type_, json.dumps(payload, ensure_ascii=False),
             idempotency_key, utcnow()),
        )

    def append_event(self, type_: str, payload: dict, idempotency_key: str | None = None) -> bool:
        """追加事件; 幂等键重复时静默跳过并返回 False。"""
        with self._lock:
            anomaly = self._anomaly_of(type_, payload)
            try:
                self._insert_event(type_, payload, idempotency_key)
                if anomaly is not None:
                    self._insert_event("log_anomaly", anomaly,
                                       f"anomaly:{type_}:{hash_payload(anomaly)}")
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False

    def log_anomalies(self, limit: int = 50) -> list[dict]:
        """最近的事件契约异常 (缺必需键 / 未登记类型)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, payload, created_at FROM events"
                " WHERE project_id=? AND type='log_anomaly' ORDER BY seq DESC LIMIT ?",
                (self.project_id, int(limit)),
            ).fetchall()
        return [{"seq": r[0], "created_at": r[2], **(json.loads(r[1]) or {})}
                for r in rows]

    def events(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, type, payload, created_at FROM events WHERE project_id=? ORDER BY seq",
                (self.project_id,),
            ).fetchall()
        return [
            {"seq": r[0], "type": r[1], "payload": json.loads(r[2]), "created_at": r[3]}
            for r in rows
        ]

    # ---- 快照 ----
    def save_snapshot(self, snapshot: ResearchSnapshot, allow_replace: bool = False) -> str:
        """冻结快照。默认不可覆盖: 同 snapshot_id 写入不同内容即冲突。

        例外: `allow_replace=True` 仅用于同一冻结快照上补充排版映射
        (writing_map), 不改变任何研究结论。
        """
        payload = snapshot.model_dump_json()
        identity = (snapshot.problem_id or "", snapshot.run_id or "", snapshot.branch_id or "")
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM snapshots WHERE project_id=? AND snapshot_id=?",
                (self.project_id, snapshot.snapshot_id),
            ).fetchone()
            if row is not None:
                if row[0] == payload:
                    return snapshot.snapshot_id
                if not allow_replace:
                    raise VersionConflict(
                        f"快照 {snapshot.snapshot_id} 已冻结且内容不同, 不可覆盖"
                    )
                self._conn.execute(
                    "UPDATE snapshots SET data=?, created_at=?, problem_id=?, run_id=?,"
                    " branch_id=? WHERE project_id=? AND snapshot_id=?",
                    (payload, snapshot.created_at, *identity,
                     self.project_id, snapshot.snapshot_id),
                )
                self._conn.commit()
                return snapshot.snapshot_id
            self._conn.execute(
                "INSERT INTO snapshots(project_id, snapshot_id, data, created_at,"
                " problem_id, run_id, branch_id) VALUES (?,?,?,?,?,?,?)",
                (self.project_id, snapshot.snapshot_id, payload, snapshot.created_at,
                 *identity),
            )
            self._conn.commit()
        return snapshot.snapshot_id

    def load_snapshot(self, snapshot_id: str | None = None) -> ResearchSnapshot | None:
        with self._lock:
            if snapshot_id is None:
                row = self._conn.execute(
                    "SELECT data FROM snapshots WHERE project_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (self.project_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT data FROM snapshots WHERE project_id=? AND snapshot_id=?",
                    (self.project_id, snapshot_id),
                ).fetchone()
        return ResearchSnapshot.model_validate_json(row[0]) if row else None

    def list_snapshots(self, problem_id: str = "", run_id: str = "") -> list[dict]:
        """快照索引 (不含正文): 供工作台按**问题/运行**列出交付。

        R6: 权威状态与查询都按问题隔离。旧快照的 identity 列为空时按 JSON 里的
        身份字段判定 (`_backfill_snapshot_identity` 已尽量回填); 两者都没有的旧数据
        只有在不指定 `problem_id`/`run_id` 时才会被列出, 不会被塞进别的运行结果里。
        """
        sql = ("SELECT snapshot_id, created_at, problem_id, run_id, branch_id"
               " FROM snapshots WHERE project_id=?")
        params: list[Any] = [self.project_id]
        if problem_id:
            sql += " AND problem_id=?"
            params.append(problem_id)
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        sql += " ORDER BY created_at DESC, rowid DESC"
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [{"snapshot_id": r[0], "created_at": r[1], "project_id": self.project_id,
                 "problem_id": r[2] or "", "run_id": r[3] or "", "branch_id": r[4] or ""}
                for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> ResearchStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
