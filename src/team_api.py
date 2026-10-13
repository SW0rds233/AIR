from __future__ import annotations

"""提供团队运行和文献资料库的 HTTP 接口。"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.agents.registry import describe_team
from src.agents.runtime import CancelToken
from src.graph.research_graph import TeamRun
from src.research.store import ResearchStore
from src.research.task_store import TaskStore

router = APIRouter()


# 请求体
class TeamRunRequest(BaseModel):
    request: str
    project_id: str = ""
    problem_id: str = ""
    run_id: str = ""
    source_set_ids: list[str] = Field(default_factory=list)
    source_policy: str = "user_kb"
    autonomous_retrieval: bool = False
    max_rounds: int = 24
    review_threshold: int = Field(default=80, ge=0, le=100)
    attachment_ids: list[str] = Field(default_factory=list)


class ScanRequestModel(BaseModel):
    paths: list[str] = Field(default_factory=list)
    recursive: bool = True
    label: str = ""
    max_files: int = 0
    max_bytes: int = 0
    allow_roots: list[str] = Field(default_factory=list)
    folder_grants: list[str] = Field(default_factory=list)


class ImportRequestModel(ScanRequestModel):
    topic: str = ""
    embed: bool = False
    idempotency_key: str = ""
    session_id: str = ""


# 团队
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
        # 只接受**本运行**的任务: 以前 `not t.get("run_id")` 也算通过, 于是任意运行
        # 的窗口都会把"没有 run 的历史任务"一并算进来, 同项目多次运行互相串
        #。无 run_id 的旧记录宁可不出现在投影里, 也不冒充本次运行。
        tasks = [t for t in task_store.tasks() if str(t.get("run_id", "")) == run_id]
        events = [e for e in store.events()
                  if str((e.get("payload") or {}).get("run_id", "")) == run_id]
    finally:
        store.close()
    from src.research.projection import KIND_MAP, object_rows

    store = ResearchStore(project_id)
    try:
        # 对象同样按本运行裁剪: 项目级 list_latest 会把别的运行混进来
        objects = {kind: object_rows(store, kind, limit=50, run_id=run_id)
                   for kind in KIND_MAP}
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
                   max_rounds=max(1, req.max_rounds),
                   review_threshold=req.review_threshold, cancel=cancel)
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
            # 交付评估必须随响应返回: 界面与人工复核要能回答
            # "为什么是这一级", 而不是只看到一个 status。
            "delivery": dict(outcome.delivery),
            "objects": {kind: len(projection.rows(kind))
                        for kind in ("evidence", "claim", "manuscript", "figure",
                                     "review_issue", "validation_plan")},
        }
    finally:
        team.close()
    return result


# 资料库
@router.post("/api/library/pick-folder")
def library_pick_folder(request: Request) -> dict[str, Any]:
    """在运行 AIR 的本机打开系统文件夹选择器。"""
    from ipaddress import ip_address
    from urllib.parse import urlsplit

    def is_loopback(host: str) -> bool:
        try:
            return ip_address(host).is_loopback
        except ValueError:
            return host == "localhost"

    peer = request.client.host if request.client else ""
    origin = request.headers.get("origin", "")
    origin_host = urlsplit(origin).hostname if origin else ""
    if not is_loopback(peer) or (origin and not is_loopback(origin_host or "")):
        raise HTTPException(403, "文件夹选择器只允许从本机页面打开")
    from src.kb.folder_picker import issue_grant, pick_folder

    try:
        selected = pick_folder()
    except Exception as error:  # GUI 不可用时保留手动填路径入口
        raise HTTPException(503, f"无法打开文件夹选择器: {error}") from error
    try:
        grant_token = issue_grant(selected) if selected else ""
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"path": str(selected) if selected else "", "cancelled": selected is None,
            "grant_token": grant_token}


@router.post("/api/library/scan")
def library_scan(req: ScanRequestModel) -> dict[str, Any]:
    """只扫描不导入: 返回逐条状态 (含 denied 与截断提示) 供用户**先预览再确认**。"""
    from src.kb import path_import as pi

    report = pi.scan_paths(_scan_request(pi, req))
    return report.to_dict()


@router.post("/api/library/import")
def library_import(req: ImportRequestModel) -> dict[str, Any]:
    """按请求导入; 幂等键防重复导入; 部分失败逐条返回。"""
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    topic = req.topic.strip() or req.label.strip()
    if not topic:
        raise HTTPException(400, "topic 或 label 至少填一项 (库名)")
    if req.topic.strip() and req.label.strip() and req.topic.strip() != req.label.strip():
        raise HTTPException(400, "库名与导入主题不一致")
    session_id = req.session_id.strip()
    if session_id:
        import re
        from src.sessions.store import conversation_exists

        if not re.fullmatch(r"[0-9a-f]{32}", session_id) or not conversation_exists(session_id):
            raise HTTPException(404, "当前对话不存在，无法绑定资料库")
    from src.kb.store import default_db_path, topic_dir

    existed = default_db_path(topic).is_file()
    canonical_id = topic_dir(topic).name

    def with_binding(report: dict[str, Any]) -> dict[str, Any]:
        result = dict(report)
        result["library_existed"] = existed
        result["source_set_id"] = canonical_id
        result["bound_session_id"] = ""
        result["binding_error"] = ""
        from src.kb.sources import validate_binding

        check = validate_binding(canonical_id)
        if not check["ok"]:
            result["binding_error"] = check["reason"]
        elif session_id:
            from src.sessions.store import bind_conversation_library

            try:
                bind_conversation_library(session_id, canonical_id)
            except KeyError:
                result["binding_error"] = "当前对话已不存在，资料库已导入但未绑定"
            else:
                result["bound_session_id"] = session_id
        return result

    scan_request = _scan_request(pi, req)
    scan = pi.scan_paths(scan_request)
    signature = pi.scan_signature(scan)
    cache_key = f"{req.idempotency_key}#{signature}" if req.idempotency_key else ""
    if cache_key:
        existing = _find_import_report(topic, cache_key)
        if existing is not None:
            payload = dict(existing)
            payload["cached"] = True
            payload["cache_key"] = cache_key
            payload["notes"] = [
                *(existing.get("notes") or []),
                "命中幂等缓存: 本次未重新导入 (上次批次 "
                f"{existing.get('batch_id') or '未知'}, 目录内容与上次完全一致)。"
                "若刚往该目录新增了文件, 请重新「扫描预览」后再次导入。",
            ]
            return with_binding(payload)
    store = KBStore(topic)
    try:
        report = pi.import_paths(store, topic, scan_request, embed=req.embed, scan=scan)
    finally:
        store.close()
    payload = report.to_dict()
    payload["cached"] = False
    payload["cache_key"] = cache_key
    if cache_key:
        _remember_import_report(topic, cache_key, payload)
    return with_binding(payload)


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


# 内部
def _scan_request(pi, req: ScanRequestModel):
    from src.kb.folder_picker import granted_roots

    if req.allow_roots:
        raise HTTPException(
            400,
            "allow_roots 已废弃：网页请求不能自行扩展授权根。"
            "请在页面用「从文件管理器选取文件夹」授权目标目录，"
            "或在服务端配置 DATA_READ_ROOTS（分号分隔的绝对路径）。")
    try:
        roots = granted_roots(req.folder_grants)
    except ValueError as error:
        raise HTTPException(410, str(error)) from error
    kwargs: dict[str, Any] = {
        "paths": list(req.paths),
        "recursive": req.recursive,
        "label": req.label,
        # HTTP 客户端不能自行扩展授权根；只有本机系统选择窗口签发的短期授权可用。
        "allow_roots": roots,
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


#: 幂等报告最多保留的条数。目录每次变化都会产生一个新的「键#签名」,
#: 不设上限时 `import_reports.json` 会随使用时间无限增长。
_IMPORT_REPORT_KEEP = 20


def _remember_import_report(topic: str, key: str, payload: dict[str, Any]) -> None:
    import json

    path = _import_marker_path(topic)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    # 去掉旧格式键(不含 `#内容签名`): 新逻辑只按「客户端键#签名」查找,
    # 这类条目再也命中不了, 留着只是垃圾。顺带把无上限的增长压到最近 N 条
    # (batch_id 形如 batch-YYYYMMDD_HHMMSS, 字典序即时间序)。
    data = {k: v for k, v in data.items() if "#" in k}
    data[key] = payload
    if len(data) > _IMPORT_REPORT_KEEP:
        newest = sorted(data.items(),
                        key=lambda item: str((item[1] or {}).get("batch_id") or ""),
                        reverse=True)[:_IMPORT_REPORT_KEEP]
        data = dict(newest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["router"]
