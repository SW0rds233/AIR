from __future__ import annotations

"""团队与资料库的 HTTP 接口 (合并计划 §9.4 / §13.4)。

设计约束
--------
- **本模块不做科学判定**: 它只把团队运行与资料库登记的结果按结构化形式返回;
  "交付等级""命题状态"等结论由判定层产生。
- **view 不触发执行**: 读取运行状态只读存储; 只有显式启动/resume 才会跑任务
  (§9.3: "页面刷新、浏览器后退与分享页面地址不自动重启任务")。
- 资料库接口遵守 §13.3 的安全规则: 路径只回显 basename, 拒绝项单独列出,
  删除只解除**该库**登记, 不动用户原文件与共享向量库。

路由:
- `GET  /api/team/roles`                    角色与**真实可用**能力
- `GET  /api/team/{project_id}/{run_id}`    任务/依赖/状态/预算投影
- `POST /api/team/run`                      启动一次团队运行 (team_v1)
- `POST /api/library/scan`                  只扫描不导入 (先预览再确认)
- `POST /api/library/import`                按请求导入 (幂等)
- `GET  /api/library/{source_set_id}`       库来源、文件数、hash 清单、失效文件
- `DELETE /api/library/{source_set_id}`     只解除该库登记
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agents.registry import describe_team
from src.agents.runtime import CancelToken
from src.graph.research_graph import TeamRun
from src.research.store import ResearchStore
from src.research.task_store import TaskStore

router = APIRouter()


# ----------------------------------------------------------------------
# 请求体
# ----------------------------------------------------------------------
class TeamRunRequest(BaseModel):
    request: str
    project_id: str = ""
    problem_id: str = ""
    run_id: str = ""
    source_set_ids: list[str] = Field(default_factory=list)
    source_policy: str = "user_kb"
    autonomous_retrieval: bool = False
    max_rounds: int = 24
    attachment_ids: list[str] = Field(default_factory=list)


class ScanRequestModel(BaseModel):
    paths: list[str] = Field(default_factory=list)
    recursive: bool = True
    label: str = ""
    max_files: int = 0
    max_bytes: int = 0
    allow_roots: list[str] = Field(default_factory=list)


class ImportRequestModel(ScanRequestModel):
    topic: str = ""
    embed: bool = False
    idempotency_key: str = ""


# ----------------------------------------------------------------------
# 团队
# ----------------------------------------------------------------------
@router.get("/api/team/roles")
def team_roles() -> dict[str, Any]:
    """团队角色清单 + 当前**真实可用**能力 (不是静态声明)。"""
    return {"roles": describe_team()}


@router.get("/api/team/{project_id}/{run_id}")
def team_projection(project_id: str, run_id: str) -> dict[str, Any]:
    """任务的依赖、状态与预算投影 (只读; 返回一致快照的 revision/event_seq)。"""
    store = ResearchStore(project_id)
    try:
        task_store = TaskStore(project_id, store)
        tasks = [t for t in task_store.tasks() if not t.get("run_id") or
                 t.get("run_id") == run_id]
        events = [e for e in store.events()
                  if str((e.get("payload") or {}).get("run_id", "")) == run_id]
    finally:
        store.close()
    from src.research.projection import KIND_MAP, object_rows

    store = ResearchStore(project_id)
    try:
        objects = {kind: object_rows(store, kind, limit=50) for kind in KIND_MAP}
    finally:
        store.close()
    return {
        "project_id": project_id,
        "run_id": run_id,
        "tasks": tasks,
        "edges": {str(t.get("task_id", "")): list(t.get("depends_on") or [])
                  for t in tasks},
        "event_seq": max((int(e.get("seq", 0) or 0) for e in events), default=0),
        "objects": {k: len(v) for k, v in objects.items()},
        "by_status": _tally(tasks, "status"),
        "by_agent": _tally(tasks, "agent"),
    }


@router.post("/api/team/run")
def run_team(req: TeamRunRequest) -> dict[str, Any]:
    """启动一次团队运行并返回结果摘要。

    同步执行 (第一版形态): 客户端拿到的是**已完成**的一轮闭环。后续接入 worker 后
    这里应改为返回 run_id 并由 SSE 推送进度 —— 接口形状 (run_id + 状态查询) 已经
    按那个方向设计。
    """
    if not req.request.strip():
        raise HTTPException(400, "request 不得为空")
    project_id = req.project_id.strip() or "proj-team"
    cancel = CancelToken()
    team = TeamRun(project_id=project_id, problem_id=req.problem_id,
                   run_id=req.run_id, request=req.request,
                   source_set_ids=req.source_set_ids,
                   source_policy=req.source_policy,
                   autonomous_retrieval=req.autonomous_retrieval,
                   max_rounds=max(1, req.max_rounds), cancel=cancel)
    source_sets = _source_rows(req.source_set_ids)
    try:
        team.set_source_sets(source_sets)
        outcome = team.run()
        projection = team.projection
        result = {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "rounds": outcome.rounds,
            "stop_reason": outcome.stop_reason,
            "brief": outcome.brief.to_dict() if outcome.brief else None,
            "plan": outcome.plan.to_dict() if outcome.plan else None,
            "decisions": [d.to_dict() for d in outcome.decisions],
            "tasks": [r.to_dict() for r in outcome.results.values()],
            "unresolved_report": outcome.unresolved_report,
            "usage": outcome.usage.to_dict(),
            "objects": {kind: len(projection.rows(kind))
                        for kind in ("evidence", "claim", "manuscript", "figure",
                                     "review_issue", "validation_plan")},
        }
    finally:
        team.close()
    return result


# ----------------------------------------------------------------------
# 资料库 (§13)
# ----------------------------------------------------------------------
@router.post("/api/library/scan")
def library_scan(req: ScanRequestModel) -> dict[str, Any]:
    """只扫描不导入: 返回逐条状态 (含 denied 与截断提示) 供用户**先预览再确认**。"""
    from src.kb import path_import as pi

    report = pi.scan_paths(_scan_request(pi, req))
    return report.to_dict()


@router.post("/api/library/import")
def library_import(req: ImportRequestModel) -> dict[str, Any]:
    """按请求导入; 幂等键防重复导入; 部分失败**逐条返回**。"""
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    topic = (req.topic or req.label or "").strip()
    if not topic:
        raise HTTPException(400, "topic 或 label 至少填一项 (库名)")
    if req.idempotency_key:
        existing = _find_import_report(topic, req.idempotency_key)
        if existing is not None:
            return existing
    store = KBStore(topic)
    try:
        report = pi.import_paths(store, topic, _scan_request(pi, req), embed=req.embed)
    finally:
        store.close()
    payload = report.to_dict()
    if req.idempotency_key:
        _remember_import_report(topic, req.idempotency_key, payload)
    return payload


@router.get("/api/library/{source_set_id}")
def library_detail(source_set_id: str) -> dict[str, Any]:
    """库的来源类型、根目录、文件数、hash 清单与失效文件 (不返回系统绝对路径)。"""
    from src.kb import path_import as pi
    from src.kb.sources import describe_source_set

    summary = pi.library_summary(source_set_id)
    if not summary.get("readable"):
        raise HTTPException(404, summary.get("note", "资料库不存在"))
    source = describe_source_set(source_set_id)
    return {**summary, "source_set": source.to_dict()}


@router.delete("/api/library/{source_set_id}")
def library_delete(source_set_id: str, delete_records: bool = False) -> dict[str, Any]:
    """只解除**该库**的登记; 不删除用户原文件, 不清空共享向量库。"""
    from src.kb import path_import as pi

    outcome = pi.unregister_library(source_set_id, delete_records=delete_records)
    if not outcome.get("ok"):
        raise HTTPException(400, outcome.get("reason", "解除登记失败"))
    return outcome


# ----------------------------------------------------------------------
# 内部
# ----------------------------------------------------------------------
def _scan_request(pi, req: ScanRequestModel):
    kwargs: dict[str, Any] = {
        "paths": list(req.paths),
        "recursive": req.recursive,
        "label": req.label,
        "allow_roots": list(req.allow_roots),
    }
    if req.max_files > 0:
        kwargs["max_files"] = req.max_files
    if req.max_bytes > 0:
        kwargs["max_bytes"] = req.max_bytes
    return pi.ScanRequest(**kwargs)


def _source_rows(source_set_ids: list[str]) -> list[dict[str, Any]]:
    """资料源摘要 (只放可读范围, 不放全文)。"""
    from src.kb.sources import describe_source_set

    rows: list[dict[str, Any]] = []
    for source_set_id in source_set_ids or []:
        info = describe_source_set(source_set_id)
        rows.append(info.to_dict())
    return rows


def _tally(tasks: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for task in tasks:
        key = str(task.get(field, "") or "unknown")
        out[key] = out.get(key, 0) + 1
    return out


def _import_marker_path(topic: str):
    from src.kb.store import topic_dir

    return topic_dir(topic) / "import_reports.json"


def _find_import_report(topic: str, key: str) -> dict[str, Any] | None:
    import json

    path = _import_marker_path(topic)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 标记文件损坏时视为没有记录
        return None
    return data.get(key)


def _remember_import_report(topic: str, key: str, payload: dict[str, Any]) -> None:
    import json

    path = _import_marker_path(topic)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    data[key] = payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["router"]
