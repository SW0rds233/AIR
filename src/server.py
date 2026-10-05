#!/usr/bin/env python
"""AIR 对话式协作 Web 界面 (FastAPI + SSE)

启动:
    python -m src.server
    或 uvicorn src.server:app --host 127.0.0.1 --port 8000

前端为原生 HTML + JS (无构建), 由 / 直接返回 src/web/index.html。
后端把 LangGraph 流水线跑在后台线程, 通过 SSE 把节点进度与 interrupt 暂停点
推给浏览器; 用户响应经 /api/sessions/{id}/respond 回传, 由 Command(resume=...) 续跑。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from src.config import DATA_DIR, OUTPUT_DIR

#: **唯一运行时形态**: 团队会话引擎 (主控 + 七类角色)。
#: 合并计划 §3/M5 要消除的就是"用户或客户端选引擎"这件事 —— 选择一旦存在, 两条路径
#: 就会各自漂移 (G01 的成因)。这个常量只用于**如实标注**会话由谁服务, 代码里不再有
#: 任何"按 mode 分流"的分支: 所有研究请求都进同一张团队图。
TEAM_ENGINE = "team"

from src.graph.node_progress import describe_node as _describe_node
from src.utils.console import ensure_utf8_console
from src.utils.conversation_store import (
    delete_conversation,
    list_conversations,
    load_conversation,
)
from src.utils.file_utils import safe_join, sanitize_filename
# M4 / §8: 会话生命周期、注册表、事件与运行线程归属资源全部抽到 `src/sessions/`;
# 本模块只保留 FastAPI 端点、图装配与工作台读取。
# 下面这些名字仍然挂在 `server` 上 —— 它们是既有调用方的契约:
#   `server.Session` / `server.SESSIONS` / `server.shutdown_sessions()` /
#   `server._persist_session` / `server._final_summary` / `server._run_session` /
#   `server._STOP` / `server.EVENT_LOG_LIMIT` / `server.CHECKPOINT_DIR` /
#   `server._make_checkpointer`
# `EVENT_LOG_LIMIT` 属于"命名空间兼容再导出" (`tests/test_session_events.py` 直接读
# `server.EVENT_LOG_LIMIT`), 因此显式标注为有意保留:
from src.sessions.controller import EVENT_LOG_LIMIT, Session  # noqa: F401
from src.sessions.events import replay_gap_note
from src.sessions.runtime import STOP as _STOP
from src.sessions.store import (  # noqa: F401 - 兼容再导出 (公共 API)
    SESSIONS,
    final_summary as _final_summary,
    persist_session as _persist_session,
    session_record as _session_record,
    shutdown_sessions,
)

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"
# F4: Vite 构建产物 (源码模板在 WEB_DIR, 产物在 dist/ 与 assets/)
WEB_DIST = WEB_DIR / "dist"
WEB_BUILT_INDEX = WEB_DIST / "index.html"
WEB_ASSETS = WEB_DIR / "assets"


def output_dir() -> Path:
    """交付/产物根目录 (**调用时**读取配置)。

    为什么不在模块级直接引用 `OUTPUT_DIR`: 模块只在首次导入时求值一次, 于是
    "先导入 server 的测试/进程"会把产物目录永久钉在那一次的配置上 ——
    后续 monkeypatch `config.OUTPUT_DIR`(测试隔离) 或运行时改配置都不生效,
    表现为"产物清单为空"这类难查的问题。这里按调用时取值, 保持单一权威。
    """
    from src import config

    return Path(getattr(config, "OUTPUT_DIR", OUTPUT_DIR))


def _web_dev_mode() -> bool:
    """开发模式开关 (仅供 `vite dev`): 允许直接提供源码模板。

    生产路径只提供构建产物 —— 源码模板引用 `/src/main.ts`, 在浏览器里没有任何脚本,
    页面能打开但按钮全都没反应, 属于"看起来正常其实坏了"的状态。
    """
    return os.getenv("AIR_WEB_DEV", "").strip() == "1"


def _index_page_path() -> Path:
    """优先提供**已构建**页面; 未构建时回退到源码模板 (仅 `vite dev` 使用)。"""
    return WEB_BUILT_INDEX if WEB_BUILT_INDEX.is_file() else INDEX_HTML


_NOT_BUILT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>AIR · 前端尚未构建</title></head>
<body style="font-family:system-ui;max-width:720px;margin:8vh auto;line-height:1.7">
<h1>前端尚未构建</h1>
<p>服务端只提供构建产物 <code>src/web/dist/index.html</code>，当前该文件不存在，
因此不返回未构建的源码模板（它引用 <code>/src/main.ts</code>，在浏览器里没有任何脚本）。</p>
<p>请在项目根目录执行：</p>
<pre style="background:#f6f8fb;padding:12px;border-radius:6px">cd src/web
npm install
npm run build</pre>
<p>然后刷新本页。若你是在做前端开发（已有 <code>vite dev</code> 在跑），
请设置环境变量 <code>AIR_WEB_DEV=1</code> 后重启服务端。</p>
</body></html>
"""


def _csp_headers() -> dict:
    return {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'"),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
CHECKPOINT_DIR = DATA_DIR / "checkpoints"


def _make_checkpointer(session_id: str):
    """为单个会话创建 SQLite 持久化 checkpointer (断点续跑用)。

    每会话一个 DB 文件 (data/checkpoints/{session_id}.sqlite), 避免多会话并发写
    同一 SQLite 的锁问题; 开启 WAL 提升并发。SQLite 依赖缺失时回退内存。
    返回 (checkpointer, conn), conn 由 Session 持有以防被提前 GC。
    """
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        db_path = CHECKPOINT_DIR / f"{session_id}.sqlite"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return SqliteSaver(conn), conn
    except Exception as e:
        print(f"  [warning] SQLite checkpointer 不可用, 回退内存: {e}")
        return MemorySaver(), None


def _resolve_engine() -> str:
    """**没有选择**了: 所有研究请求都进团队会话引擎 (合并计划 §3 / M5 / G01)。

    这里保留一个返回常量的函数, 只是为了让调用点读起来仍然明确"当前由谁服务",
    它不接收 `mode` —— 一旦接收, 就等于承认"可以按 mode 分流", 而分流的另一条路
    (旧形式化引擎的图) 已经删除。旧客户端传来的 `mode` 会被 Pydantic 忽略。
    """
    return TEAM_ENGINE


def _build_app_for_mode(checkpointer, mode: str = "", *, session=None):
    """构建会话的图应用 —— 只有**一张图**: 团队会话引擎 (主控 + 七类角色)。

    参数 `mode` 仍然接受, 但**不再影响结果**: 合并计划 §3/M5 要求消除"选引擎", 而
    保留一个"看起来还能选"的形参正是分流的入口。这里显式忽略它, 并把理由写下来,
    免得下次有人以为"传 theory 还能走旧图"。旧会话记录里残留的 `mode` 字段因此不会
    让续跑悄悄换引擎 —— 它本来也换不了, 因为那条路径已经删除。
    """
    return _build_team_app(session)


def _team_llm_factory(stage: str = ""):
    """按角色取一个模型实例 (团队会话的角色模型接线, §3.1 G02)。

    实现只有一份, 在 `src/bootstrap.py` —— 这一层保留名字是因为会话装配按名称取它
    (`_build_team_app`), 而 CLI/脚本走 `run_team_session` 用的是**同一个**函数。
    接线写两遍就会漂移: 某个角色在一条路径上有模型、在另一条路径上静默退化成规则
    模板, 两边看起来都"跑通了"。

    离线约定: `THEORY_LLM=0` 时返回 `None` (沿用项目既有的离线开关)。取模型失败时
    **如实抛错**, 不返回 None: 把"模型不可用"混进"离线模式"会让"自主科研没跑成"
    看起来像"故意离线"。
    """
    from src.bootstrap import role_llm_factory

    return role_llm_factory(stage)


def _build_team_app(session):
    """构建团队会话的"图应用" (供会话驱动逐轮调用)。

    需要 `session` 才能把事件接到会话出口 —— 团队进度 (主控决策、角色成果) 必须
    出现在 SSE 里, 否则界面又回到"正在思考"。

    **输入必须整份进入团队** (合并计划 §3.1 G03): 研究身份 (project/problem/run)、
    附件 (id/hash/文本) 与资料策略都在 `session.request` 里, 因此这里逐一取用;
    此前只取了 project/problem/request 三项, 附件与资料范围实际从未进团队。
    """
    from src.graph.research_graph import TeamRun
    from src.graph.team_session import TeamApp, TeamSession

    if session is None:                     # 没有会话对象时不接出口 (测试/内省用)
        team = TeamRun(project_id="", request="", max_rounds=1)
        return TeamApp(TeamSession(team))
    request = session.request or {}
    run_id = session.run_id or str(request.get("run_id", "") or "")
    team = TeamRun(
        project_id=str(request.get("project_id", "") or session.session_id),
        problem_id=str(request.get("problem_id", "") or ""),
        run_id=run_id,
        request=str(request.get("request") or request.get("topic") or session.topic),
        # 附件: 把**已规范化**的清单与文本一起交给团队 (文本是外部资料, 已在入口
        # 做过定界与越权扫描), 团队据此理解"题面在附件里"这件事。
        attachments=list(request.get("attachments") or []),
        attachment_text=str(request.get("attachment_text") or ""),
        source_set_ids=[str(request.get("source_set_id", "") or "")],
        source_policy=str(request.get("source_policy", "user_kb") or "user_kb"),
        max_rounds=int(request.get("max_rounds", 24) or 24),
        # 角色模型接线 (G02): 不注入的话整个团队只会跑规则模板
        llm_factory=_team_llm_factory,
    )
    return TeamApp(TeamSession(team, emit=session.emit))


def _make_session(thread_id: str, *, topic: str = "", session_id: str,
                  checkpointer=None, checkpoint_conn=None, run_id: str = "",
                  mode: str = "", request: dict[str, Any] | None = None) -> Session:
    """构造一个会话 (显式注入按模式构建的图应用)。

    这是**唯一**的会话装配入口: 新建与续跑都走这里, 避免两处各写一遍建图逻辑
    (漏掉一处就会出现"续跑的会话没有图"这类难查的不一致)。

    `Session` 也保留了缺省建图能力 (直接 `Session(...)` 仍然可用), 但那会绕过
    "模式 → 图"的唯一决策点; 入口一律显式注入。

    **`request` 必须在建图之前落位** (合并计划 §3.1 G03): 团队装配 (`_build_team_app`)
    从 `session.request` 读研究身份 (project/problem/run) 与资料 (附件、资料源策略),
    先建图再回填就会让团队拿到空身份和默认策略 —— 这正是审计复现的那条。
    """
    session = Session(
        thread_id,
        topic=topic,
        session_id=session_id,
        checkpointer=checkpointer,
        checkpoint_conn=checkpoint_conn,
        run_id=run_id,
        mode=_resolve_engine(),
        # 先不给 app: 团队引擎需要会话对象才能把进度接到会话出口 (SSE), 因此
        # 两段式装配 —— 先构造会话并落位输入快照, 再注入 app。
        app=None,
    )
    session.request = dict(request or {})
    session.app = _build_app_for_mode(checkpointer, session=session)
    return session


class StartRequest(BaseModel):
    topic: str = ""
    request: str = ""
    keywords: list[str] = []
    subtopics: list[str] = []
    time_range: str = ""
    # 遗留兼容字段 (合并计划 §3 / M5: 统一入口不要求用户选模式)。
    # 统一入口 (合并计划 §3 / M5 / G01): **没有模式选择**。旧客户端可能仍带 `mode`
    # 字段, 它会被 Pydantic 忽略 —— 所有研究请求都进同一张团队图。
    # 旧的 `mode` 参数保留在建会话签名里只为兼容既有调用点, 不再参与分流。
    project_id: str = ""
    problem_id: str = "problem"
    research_spec: dict = {}
    # `max_actions`/`max_tool_calls` 已删除: 那是**旧理论引擎**的动作预算旋钮, 团队运行
    # 的预算是轮次与任务额度 (由 `TeamRun` 决定)。旧客户端仍可传这两个键 —— Pydantic
    # 忽略未声明字段, 因此不会报错, 但也不会再影响任何东西 (而不是"看起来设置了").
    # 显式续研同一问题: 与 start 分开语义 (计划书 §9.1)。
    # resume=true 时复用已落盘规格, 即使本次请求文本不同也不报冲突。
    resume: bool = False
    # 资料源绑定 (计划书 §3 R0): 用户显式选择的资料库; 为空时按主题名匹配
    source_set_id: str = ""
    source_set_kind: str = "kb"
    # 资料授权策略 (P0-1): user_kb=只用授权资料库; autonomous=只给方向自主检索; both=两者合并
    source_policy: str = "both"
    # 上传的"问题说明"附件 (id 来自 POST /api/uploads kind=problem): 文本并入问题陈述
    attachment_ids: list[str] = []


class RespondRequest(BaseModel):
    response: str
    # 反馈作用对象所属的研究问题 (多问题项目必须显式给出, 否则服务端返回 409)
    problem_id: str = ""
    # 响应对应的中断 ID (F2 幂等键): 留空时按服务端当前等待的暂停点处理
    interrupt_id: str = ""
    # 对象选择器指定的作用对象 (F1-5): 不填时由语义解析决定
    object_id: str = ""
    # 人工修订的幂等身份。同一 ID 的网络重试不应重复派工；不同 ID 的同类意见可再次派工。
    feedback_id: str = ""
    # object=修订具体对象；其他范围由用户显式指定，不对模糊文本擅自猜测。
    scope: str = "object"


class ForkRequest(BaseModel):
    """从既有快照派生新研究问题 (计划书 §9.1: start / resume / fork 三种操作)。"""

    snapshot_id: str = ""
    claim_ids: list[str] = []
    # 源问题: 派生所依据的研究问题
    problem_id: str = ""
    # 新问题 id: 留空时由服务端生成
    new_problem_id: str = ""
    project_id: str = ""


# `Session` / `SESSIONS` / `_session_record` / `_persist_session` 已移到
# `src/sessions/` (controller + store), 在文件顶部 import 后保持这些名字可用。
# 这里不再保留第二份实现 —— 两份状态各自漂移正是 P0 类缺陷的成因。


def problem_index(store) -> list[dict]:
    """项目下的所有研究问题 (计划书 F0-2 / R6: 问题是一等身份)。

    `ResearchSpec` 的主键就是 `problem_id`, 因此枚举规格即枚举问题。
    返回顺序稳定 (按 obj_id), 便于前端展示与测试断言。
    """
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_SPEC

    out: list[dict] = []
    for obj_id in sorted(store.version_index(KIND_SPEC)):
        data = store.get(KIND_SPEC, obj_id)
        if not data:
            continue
        try:
            spec = ResearchSpec.model_validate(data)
        except Exception:  # noqa: BLE001 - 坏规格不应让整个工作台 500
            out.append({"problem_id": obj_id, "invalid": True, "statement": ""})
            continue
        out.append({
            "problem_id": obj_id,
            "statement": (spec.problem_statement or spec.original_request
                          or spec.direction or ""),
            "direction": spec.direction,
            "confirmed": bool(spec.confirmed),
            "version": store.latest_version(KIND_SPEC, obj_id),
        })
    return out


def resolve_problem(store, problem_id: str = "") -> tuple[str, dict]:
    """把 (project, problem_id 可能为空) 解析成**唯一**问题规格 (F0-2)。

    约束:
    - 指定 `problem_id` 时精确取该规格; 不存在 → `ProblemNotFound`;
    - 未指定且项目只有一个问题 → 用它 (向后兼容旧请求);
    - 未指定且项目有多个问题 → `ProblemAmbiguous`, 由调用方返回 409 让用户选择;
      **绝不**擅自取第一个 (早期实现正是这样把 A 问题的对象显示在 B 问题上)。
    """
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_SPEC

    if problem_id:
        data = store.get(KIND_SPEC, problem_id)
        if not data:
            raise ProblemNotFound(problem_id)
        return problem_id, ResearchSpec.model_validate(data)

    index = problem_index(store)
    if not index:
        raise ProblemNotFound("")
    if len(index) > 1:
        raise ProblemAmbiguous(index)
    only = index[0]["problem_id"]
    data = store.get(KIND_SPEC, only)
    if not data:
        raise ProblemNotFound(only)
    return only, ResearchSpec.model_validate(data)


class ProblemNotFound(LookupError):
    def __init__(self, problem_id: str):
        self.problem_id = problem_id
        super().__init__(f"项目下没有该研究问题: {problem_id or '(未指定)'}")


class ProblemAmbiguous(LookupError):
    def __init__(self, problems: list[dict]):
        self.problems = problems
        super().__init__(f"项目含 {len(problems)} 个研究问题, 必须显式指定 problem_id")


def _claims_of_problem(claims: list, problem_id: str, problem_ids: set[str]) -> list:
    """按研究问题过滤命题 (F0-2 / R6: 对象必须归属到具体问题)。

    归属规则 (必须显式, 不能靠"取第一个"):
    - 命题记录了 `problem_id` → 只属于该问题;
    - 命题没有记录 (旧库遗留) 且项目只有**一个问题** → 属于它 (向后兼容);
    - 命题没有记录且项目有多个问题 → 不归属任何问题, 只在当前问题**完全
      没有**已标注命题时用于兜底显示 (否则一个遗留命题会出现在每个问题里)。
    """
    ids = sorted(problem_ids)
    if len(ids) <= 1:
        return list(claims)
    owned = [c for c in claims if str(getattr(c, "problem_id", "") or "") == problem_id]
    if owned:
        return owned
    return [c for c in claims if not str(getattr(c, "problem_id", "") or "")]


def _research_state(project_id: str, problem_id: str = "") -> dict:
    """汇总**单个研究问题**的对象状态。

    只读: 从存储读出对象与派生值, **不建研究引擎**、不触发任何研究动作、不改结论状态
    (合并计划 §5.5: 只读端点不该依赖"能执行研究动作"的组件)。全部对象按当前问题过滤
    —— 同项目多问题时不得互相串数据 (F0-2 / R6)。
    """
    # 只读接口: 打开前先确认库文件存在, 避免"查询不存在的项目"顺手建出空库
    # (否则工作台的一次 404 查询就会在 data/research/ 留下垃圾文件)
    from src.research.store import (
        KIND_ATTEMPT,
        KIND_GAP,
        KIND_NOVELTY,
        ResearchStore,
        default_db_path,
    )

    if not default_db_path(project_id).exists():
        raise HTTPException(404, "该项目下没有研究问题规格")
    store = ResearchStore(project_id)
    try:
        problems = problem_index(store)
        problem_id, spec = resolve_problem(store, problem_id)
    except ProblemNotFound as e:
        store.close()
        raise HTTPException(404, str(e)) from e
    except ProblemAmbiguous as e:
        store.close()
        raise HTTPException(409, {"message": str(e), "problems": e.problems}) from e

    # ---- 只读检视: 对象与派生值都从存储读出 (没有引擎, 也没有执行能力) ----
    from src.research.inspection import StoreInspection

    view = StoreInspection(store, spec,
                           knowledge_available=_knowledge_available_for(spec))
    all_claims = view.claims(problem_id)
    # 项目里**全部**命题 id (含别的问题的): 未知 id 与"别的问题的对象"必须区分,
    # 否则别的问题的实验建议会因为 id 不在本问题里而被当成"未知"照旧展示。
    known_claim_ids = {str(d.get("id", "")) for d in store.list_latest("claim")}
    claims = _claims_of_problem(all_claims, problem_id,
                                {p["problem_id"] for p in problems})
    scoped_ids = {c.id for c in claims}
    # 同一项目多问题时, 无法归属到当前问题的对象一律不展示 (fail-closed);
    # 单问题项目里旧数据 (无 problem_id) 仍然展示, 不凭空清空工作台。
    multi_problem = len(problems) > 1

    def _owned(claim_id: str) -> bool:
        """对象是否属于当前问题 (R6)。

        - 明确属于本问题 → 展示;
        - 明确是项目里**别的问题**的命题 → 不展示 (fail-closed);
        - 无法归属 (旧数据没有 problem_id, 或 id 未知) → 单问题项目照旧展示,
          多问题项目下隐藏, 因为此时"归属不明"本身就是问题。
        """
        if not claim_id:
            return not multi_problem
        if claim_id in scoped_ids:
            return True
        if claim_id in known_claim_ids:
            return False
        return not multi_problem

    obligations = sorted([o for o in view.obligations() if _owned(o.claim_id)],
                         key=lambda o: _obligation_sort_key(o.kind))
    verifications = [v for v in view.verifications() if _owned(v.claim_id)]
    evidence = [e for e in view.evidence() if _owned(e.claim_id)]
    routes = view.routes()
    routes = [r for r in routes
              if _owned(r.target_ref.id if r.target_ref else "")]
    models = view.models()

    # 实验中引用**别的问题**命题的建议不得出现在本问题的工作台 (R6)
    foreign_claim_ids = known_claim_ids - scoped_ids

    # 组装交给深模块 reporting (计划书 §4): 服务端只负责取数、解析问题与过滤,
    # 字段契约与计数口径集中在那一处, 便于单独测试。
    from src.research import reporting

    payload = reporting.workbench_projection(
        project_id=project_id, problem_id=problem_id,
        claims=claims, obligations=obligations, verifications=verifications,
        evidence=evidence, routes=routes, models=models, store=store,
        problems=problems, spec=spec,
        run_id=view.identity()["run_id"], branch_id=view.identity()["branch_id"],
        metrics=view.metrics(claims, problem_id=problem_id),
        model_selection=view.model_selection(claims, available=None),
        modeling=next((view.model_comparison(c) for c in claims
                       if view.model_comparison(c)), {}),
        assumptions=view.assumptions(),
        decisions=view.decisions()[-30:],
        events=view.event_digest(claims, problem_id=problem_id, limit=40),
        gaps=view.gaps(claims, obligations),
        budget=view.budget_payload(),
        experiment_specs=[d for d in store.list_latest(KIND_GAP)
                          if str(d.get("id", "")).startswith("exp-")],
        novelty_records=[n for n in store.list_latest(KIND_NOVELTY)
                         if _owned(str(n.get("claim_id", "")))],
        attempts=[a for a in store.list_latest(KIND_ATTEMPT)
                  if _owned(a.get("target_claim_id", ""))],
        foreign_claim_ids=foreign_claim_ids,
    )
    store.close()
    return payload


def _knowledge_available_for(spec) -> bool:
    """该问题是否已有**非空**知识底座 (只探测已存在的库, 不创建空库)。"""
    from src.kb.service import KnowledgeService

    topic = getattr(spec, "domain", "") or getattr(spec, "original_request", "") or ""
    if not topic:
        return False
    try:
        service = KnowledgeService(topic, create_if_missing=False)
        return bool(service and service.available)
    except Exception:  # noqa: BLE001 - 探测失败按"不可用"处理, 不影响只读呈现
        return False


def _obligation_sort_key(kind: str) -> int:
    """义务排序优先级 (唯一口径在 `research.obligations`)。"""
    from src.research.obligations import OBLIGATION_PRIORITY

    return OBLIGATION_PRIORITY.get(kind, 9)


def objects_counts(claims, obligations, evidence, verifications,
                   models=None, routes=None) -> dict:
    """工作台统计字段 (F0-3) —— 委托给 `research.reporting` 的唯一实现。

    计数口径必须只有一处 (`reporting.objects_counts`): 这里保留同名入口只是为了
    不打断既有调用方, 任何口径调整都应改那一处。
    """
    from src.research import reporting

    return reporting.objects_counts(claims, obligations, evidence, verifications,
                                    models=models, routes=routes)


def _uncovered_factors(claim) -> list[str]:
    """结论明确未覆盖的现实因素 (§4.3-4) —— 委托给 `research.reporting`。"""
    from src.research import reporting

    return reporting.uncovered_factors(claim)


def _normalized_input(req: StartRequest) -> dict:
    """规范化输入与文件, 并**在创建任何运行之前**分配研究身份 (合并计划 §3.1 G03)。

    计划要求的顺序是硬要求: 规范化输入与文件 → 校验资料授权 → 分配身份 → 创建
    run/session → 注入同一输入快照 → 启动。此前 `start_session` 先 `_make_session()`
    (其中已经建好 `TeamRun`) 再回填 `session.request`/`run_id`, 于是团队拿到的是
    自动 problem 与空 run 身份、默认资料策略, 附件也从未进团队 —— HTTP 返回的 ID
    与任务/SQLite/事件/包里的 ID 因此可能不是同一个。

    这里产出的字典是**唯一**输入快照: `initial_state`、`session.request` 与团队装配
    都从它取, 不再各自重新推导 (重新推导就是身份漂移的来源)。
    """
    from src.research.schemas import SourcePolicy

    request_text = (req.request or "").strip() or (req.topic or "").strip()
    topic_or_request = (req.topic or req.request).strip() or request_text
    topic_text = (req.topic or "").strip()
    if not topic_text:
        from src.utils.file_utils import derive_topic

        topic_text = derive_topic(request_text) or request_text
    project_id = (req.project_id or "").strip() or (
        sanitize_filename(topic_or_request)[:30].strip("_") or "research")
    problem_id = (req.problem_id or "").strip() or "problem"
    base = sanitize_filename(topic_or_request)[:20].strip("_") or "research"
    # **运行身份必须真的唯一**: 此前是"主题 + 秒级时间戳", 同一秒内用同一主题启动两次
    # 会得到**同一个 run_id**, 而所有对象都按 run 归属 —— 于是第二次运行会看到第一次
    # 运行的对象、复用它们的命题 (实测: 同项目两个问题的结论集合完全相同)。加一个
    # 短随机后缀, 既保持可读, 又让"两次运行"永远是两次。
    run_id = (f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
              f"_{uuid.uuid4().hex[:4]}")

    policy = str(getattr(req, "source_policy", "") or "user_kb").strip()
    allowed_policies = {p.value for p in SourcePolicy}
    if policy not in allowed_policies:
        raise HTTPException(400, f"未知的资料授权策略: {policy} (可选: {sorted(allowed_policies)})")

    # 附件: 只认属于本项目/本问题的问题说明附件 (A 的附件不得挂到 B), 文本按有界
    # 长度传入并显式标注为外部资料 (其中的指令不得执行)。
    rejected: list[str] = []
    hashes: list[dict] = []
    attachment_text = ""
    if getattr(req, "attachment_ids", None):
        from src.utils import uploads
        from src.utils.external_data import wrap_external_with_scan

        allowed, denied = uploads.resolve_problem_attachments(
            list(req.attachment_ids), project_id, problem_id)
        rejected = [str(i) for i in denied]
        hashes = [{"attachment_id": i.get("attachment_id"),
                   "filename": i.get("filename"),
                   "sha256": i.get("sha256"),
                   "parse_quality": i.get("parse_quality"),
                   "visibility_flags": i.get("visibility_flags") or []}
                  for i in allowed]
        raw = uploads.problem_text([i.get("attachment_id") for i in allowed],
                                   project_id, problem_id)
        if raw:
            bounded = raw[: uploads.MAX_ATTACHMENT_CHARS]
            attachment_text, scan = wrap_external_with_scan(bounded, source="问题说明附件")
            if scan.suspicious:
                rejected.extend(["scan:" + flag for flag in scan.flags])

    return {
        "topic": topic_text,
        "request": request_text,
        "project_id": project_id,
        "problem_id": problem_id,
        "run_id": run_id,
        "keywords": list(req.keywords or []),
        "subtopics": list(req.subtopics or []),
        "time_range": req.time_range,
        "attachment_ids": list(getattr(req, "attachment_ids", None) or []),
        "attachments": hashes,
        "attachment_text": attachment_text,
        "attachment_rejected": rejected,
        "source_set_id": str(getattr(req, "source_set_id", "") or ""),
        "source_set_kind": str(getattr(req, "source_set_kind", "") or "kb"),
        "source_policy": policy,
    }


def _session_request(snapshot: dict) -> dict:
    """会话落盘的请求快照 (续跑要能重建同一身份与同一资料范围)。

    字段集合必须能被 `StartRequest(**request)` 还原 (`resume_session` 依赖这一点),
    因此这里只加键, 不改既有键的含义。

    **不再写入 `mode`**: 引擎选择已删除 (G01)。旧会话记录里可能仍有 `mode`, 它既不被
    `StartRequest` 接受 (字段已删除, Pydantic 忽略), 也不影响续跑走哪张图 —— 只有一张。
    """
    return {
        "topic": snapshot["topic"],
        "request": snapshot["request"],
        "keywords": list(snapshot["keywords"]),
        "subtopics": list(snapshot["subtopics"]),
        "time_range": snapshot["time_range"],
        "project_id": snapshot["project_id"],
        "problem_id": snapshot["problem_id"],
        "attachment_ids": list(snapshot["attachment_ids"]),
        "source_set_id": snapshot["source_set_id"],
        "source_set_kind": snapshot["source_set_kind"],
        "source_policy": snapshot["source_policy"],
        # 只读的规范化结果 (团队装配读它们; `StartRequest` 会忽略未声明字段)
        "run_id": snapshot["run_id"],
        "attachments": list(snapshot["attachments"]),
        "attachment_text": snapshot["attachment_text"],
        "attachment_rejected": list(snapshot["attachment_rejected"]),
    }


def _validate_source_binding(snapshot: dict) -> list[str]:
    """校验资料源绑定可用 (在创建 run 之前), 返回警告。

    不可用时**明确告知**而不是静默退回"无知识库": 后者会让一次绑定错误的运行看起来
    像"没检索到资料"。
    """
    source_set_id = str(snapshot.get("source_set_id") or "").strip()
    if not source_set_id:
        return []
    from src.kb.sources import validate_binding

    check = validate_binding(source_set_id)
    if not check["ok"]:
        raise HTTPException(409, {
            "message": f"资料源不可用, 请重新选择: {check['reason']}",
            "source_set_id": source_set_id,
            "reason": check["reason"],
        })
    return list(check["warnings"])


def _ensure_problem_spec(snapshot: dict, *, resume: bool = False) -> tuple[
        dict | None, bool, dict | None]:
    """在创建运行之前把**问题规格**落盘, 并判定身份冲突 (F0-4 / P0-2)。

    为什么放在入口而不是等团队自己落盘:
    - **冲突必须能被拒绝**: 同一个 `problem_id` 已研究另一个请求时必须 409, 让用户
      选择 `resume=true` 继续原题, 或换 `problem_id` 研究新题。此前只有旧引擎入口做了
      这件事, 团队入口会静默沿用旧规格 —— 用户看到的是"我改了问题, 但研究结果没变"。
    - **规格必须先于运行存在**: 工作台、问题列表与交付清单都按 `problem_id` 找规格。

    返回 `(conflict, spec_reused, contract)`; `conflict` 非空表示必须由调用方决定
    继续还是换题 (本函数**不**自行覆盖)。
    """
    from src.kb.sources import build_source_summary
    from src.research.question_planner import build_spec_from_input
    from src.research.schemas import ResearchSpec, SourcePolicy
    from src.research.store import KIND_SPEC, ResearchStore

    project_id = str(snapshot["project_id"])
    problem_id = str(snapshot["problem_id"])
    request_text = str(snapshot["request"] or snapshot["topic"] or "")
    source_summary = build_source_summary(
        source_set_id=snapshot["source_set_id"],
        source_set_kind=snapshot["source_set_kind"] or "kb",
        request=request_text)
    # 授权自主检索时, 没有预建资料库也算合法输入 (P0-1 场景 ②)
    source_summary.autonomous_retrieval = snapshot["source_policy"] in ("autonomous", "both")
    store = ResearchStore(project_id)
    try:
        conflict = _existing_spec_conflict(store, problem_id, request_text,
                                          source_summary=source_summary)
        stored = store.get(KIND_SPEC, problem_id)
        if stored:
            if conflict is not None and not resume:
                # 不覆盖已确认的问题: 交给调用方返回 409, 让用户选择
                try:
                    contract = ResearchSpec.model_validate(stored).contract
                except Exception:  # noqa: BLE001
                    contract = None
                return (conflict, True,
                        contract.model_dump(mode="json") if contract else None)
            try:
                spec = ResearchSpec.model_validate(stored)
            except Exception:  # noqa: BLE001 - 坏规格: 重建而不是继续用坏的
                spec = None
            if spec is not None:
                return (None, True,
                        spec.contract.model_dump(mode="json") if spec.contract else None)
        spec = build_spec_from_input(
            request=request_text, topic=str(snapshot["topic"]), project_id=project_id,
            problem_id=problem_id, source_summary=source_summary)
        if snapshot["source_set_id"]:
            spec.source_set_id = snapshot["source_set_id"]
            spec.source_set_kind = snapshot["source_set_kind"] or "kb"
        spec.source_policy = SourcePolicy(snapshot["source_policy"])
        store.put(KIND_SPEC, problem_id, spec.model_dump(mode="json"))
        contract_row = (spec.contract.model_dump(mode="json") if spec.contract else None)
        return (conflict, False, contract_row)
    finally:
        store.close()


def build_initial_state(req: StartRequest, snapshot: dict | None = None) -> dict:
    # 自然语言请求与检索缓存加载均由入口 research_planner_node 处理,
    # 此处仅透传原始输入 (主题可能为空, 待 Planner 提取)。
    # run_id 用作 outputs/{run_id}/ 产物子目录名, 使不同对话产物隔离、便于查看;
    # 存进 state 后经 checkpoint 持久化, 断点续跑也能沿用同一目录。
    # `snapshot` 由入口传入 (同一份输入快照); 直接调用时按请求现场规范化。
    snap = snapshot or _normalized_input(req)
    return {
        "messages": [],
        "research_topic": req.topic.strip(),
        "research_request": req.request.strip(),
        "topic_keywords": req.keywords or [],
        "sub_topics": req.subtopics or [],
        "time_range": req.time_range,
        "run_id": snap["run_id"],
        "project_id": snap["project_id"],
        "problem_id": snap["problem_id"],
        # G03: 附件与资料范围随初始状态一起进入团队 (此前它们不进团队)
        "attachment_ids": list(snap["attachment_ids"]),
        "attachment_candidates": snap["attachment_text"],
        "attachment_rejected": list(snap["attachment_rejected"]),
        "source_set_id": snap["source_set_id"],
        "source_policy": snap["source_policy"],
        # `revision_count` / `current_phase` / `interactive` 由会话装配与前端读取;
        # `max_revisions` 与 `skip_retrieval` 已删除 (R5): 修订轮次现在由主控按轮次与
        # "写作缺口回流"驱动, 检索与否由资料授权策略 + 覆盖记录决定 —— 这两个旋钮
        # 此前只是被透传, 没有任何模块读, 留着会让使用者以为"设了就生效"。
        "revision_count": 0,
        "retrieved_papers": [],
        "current_phase": "start",
        "interactive": True,
    }


# 会话运行时的 stdout 桥/运行作用域: 这些名字此前在**旧引擎分支**里定义, 与引擎
# 是否使用无关 (团队会话同样要按 run 归属日志)。删除旧分支时一并搬到这里, 保持
# "实现只有一份在 `sessions/runtime`" 的约定。
from src.sessions import runtime as _session_runtime  # noqa: E402

_StdoutBridge = _session_runtime.StdoutBridge
_StdoutDispatcher = _session_runtime._StdoutDispatcher
_STDOUT_BRIDGES = _session_runtime._STDOUT_BRIDGES
_STDOUT_LOCK = _session_runtime._STDOUT_LOCK
_RUN_SCOPE_STACKS = _session_runtime._RUN_SCOPE_STACKS
_RUN_SCOPE_LOCK = _session_runtime._RUN_SCOPE_LOCK
_install_stdout_dispatcher = _session_runtime._install_stdout_dispatcher
_bind_stdout_bridge = _session_runtime.bind_stdout_bridge
_unbind_stdout_bridge = _session_runtime.unbind_stdout_bridge


def _run_session(session: Session, initial_state: dict | None = None):
    """在后台线程运行流水线, 事件经 queue → SSE 推给前端。

    initial_state=None 时表示断点续跑: 用同一 thread_id + 持久化 checkpointer 从
    上次 checkpoint 继续 (stream(None, config))。若上次停在 interrupt, stream 会
    重新抛出 __interrupt__, 进入下方等待用户响应的分支。
    """
    inputs: Any = initial_state
    # M4: 不替换全局 stdout, 只把**本线程**绑定到本会话的日志桥 (见 _StdoutDispatcher)
    _bind_stdout_bridge(session)
    try:
        while True:
            interrupted = False
            for event in session.app.stream(inputs, session.config):
                if session.stop_flag.is_set():
                    interrupted = False
                    break
                if "__interrupt__" in event:
                    interrupted = True
                    intr = event["__interrupt__"][0]
                    session.status = "waiting"
                    # F2: 为每个 interrupt 生成稳定 ID, 供前端回传与幂等去重
                    try:
                        from src.research.schemas import hash_payload

                        interrupt_id = "int-" + hash_payload(
                            {"thread": session.thread_id, "n": len(session.answered_interrupts),
                             "value": intr.value if isinstance(intr.value, (dict, list, str))
                             else str(intr.value)})[:12]
                    except Exception:  # noqa: BLE001
                        interrupt_id = f"int-{len(session.answered_interrupts)}"
                    session.pending_interrupt_id = interrupt_id
                    payload = intr.value if isinstance(intr.value, dict) else {"value": intr.value}
                    payload = {**payload, "interrupt_id": interrupt_id}
                    session.emit({"type": "interrupt", "payload": payload})
                    _persist_session(session)
                    response = session.responses.get()  # 阻塞等待用户响应
                    session.answered_interrupts.append(interrupt_id)
                    session.pending_interrupt_id = ""
                    if response is _STOP or session.stop_flag.is_set():
                        session.emit({"type": "stopped"})
                        session.status = "stopped"
                        _persist_session(session)
                        return
                    session.status = "running"
                    inputs = Command(resume=response)
                else:
                    for node_name, node_state in event.items():
                        # planner 提取出真实主题后回填会话 topic (start 时只有原始 request,
                        # 自然语言入口下 topic 是超长指令而非真实主题), 使历史列表显示真实主题
                        topic = ((node_state or {}).get("research_topic")
                                 or (node_state or {}).get("topic") or "").strip()
                        if topic and topic != session.topic:
                            session.topic = topic
                            _persist_session(session)
                        session.emit({
                            "type": "node",
                            "name": node_name,
                            "text": _describe_node(node_name, node_state),
                            # 研究进度已由团队事件 (`team_event` → SSE) 携带: 每个角色
                            # 的派工/成果/缺口都从图里发出。此前这里另外建一个研究引擎
                            # 去"补一份进度摘要"—— 那是**第二个读模型的入口**, 也是
                            # 只读接口依赖引擎的又一处 (G01)。
                        })
            if session.stop_flag.is_set():
                session.emit({"type": "stopped"})
                session.status = "stopped"
                _persist_session(session)
                return
            if not interrupted:
                break
        try:
            final = session.app.get_state(session.config).values
        except Exception:
            final = {}
        session.final_state = final
        session.status = "done"
        session.emit({"type": "done", "state": _final_summary(final)})
        _persist_session(session)
    except Exception as e:
        session.error = str(e)
        session.status = "error"
        session.emit({"type": "error", "message": str(e)})
        _persist_session(session)
    finally:
        # M4: 只解绑本线程的日志桥, 全局 stdout 保持不变 (不再"恢复"上一个会话的桥)
        _unbind_stdout_bridge()


app = FastAPI(title="AIR智能体研究系统")

# 团队与资料库接口 (合并计划 §9.4 / §13.4): 独立路由模块, 避免 server.py 继续膨胀。
# 导入放在 app 构造之后是为了保持既有的导入顺序 (该模块会 import ResearchStore)。
from src.team_api import router as team_router  # noqa: E402

app.include_router(team_router)


@app.get("/")
def index():
    # P2: 缺构建产物时给**可操作的错误提示**, 不返回未构建的源码模板
    if not WEB_BUILT_INDEX.is_file() and not _web_dev_mode():
        return HTMLResponse(_NOT_BUILT_PAGE, status_code=503, headers=_csp_headers())
    page = _index_page_path()
    if not page.is_file():
        raise HTTPException(404, "前端页面不存在: src/web/index.html")
    # F3: 本地应用的最小 CSP。页面不再从 CDN 取脚本 (marked 已改为本地模块),
    # 因此脚本只能来自同源; 禁止内联脚本与 object, 限制连接/图片/字体来源。
    return HTMLResponse(page.read_text(encoding="utf-8-sig"),
                        headers=_csp_headers())


@app.get("/assets/{name:path}")
def web_asset(name: str):
    """Vite 构建产物 (计划书 §2 F4): 只从 src/web/assets 提供。"""
    path = safe_join(WEB_ASSETS, name)
    if path is None or not path.is_file():
        raise HTTPException(404, "前端资源不存在")
    media = ("application/javascript" if path.suffix == ".js"
             else "text/css" if path.suffix == ".css"
             else "application/octet-stream")
    return FileResponse(str(path), media_type=media,
                        headers={"Cache-Control": "no-cache"})



def _normalize_request_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _existing_spec_conflict(store, problem_id: str, request_text: str,
                            source_summary=None) -> dict | None:
    """同一 problem_id 已存在规格时的冲突判定 (F0-4 / P0-2)。

    冲突有两种: 研究请求文本不同, 或同一请求在新资料条件下判定出**不同的研究类型**。
    返回 None 表示可以沿用; 否则给出选项 (resume / new_problem) 让用户选择,
    不静默继续旧题。
    """
    from src.research.question_planner import formulate
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_SPEC

    existing = store.get(KIND_SPEC, problem_id)
    if not existing:
        return None
    try:
        spec = ResearchSpec.model_validate(existing)
    except Exception:  # noqa: BLE001 - 坏规格按冲突处理, 不静默覆盖
        return {"message": f"已有规格 {problem_id} 无法解析, 请换用新的 problem_id",
                "problem_id": problem_id, "options": ["new_problem", "resume"]}
    stored_request = spec.original_request or spec.problem_statement or spec.direction
    if not _normalize_request_text(request_text):
        return None
    if _normalize_request_text(stored_request) == _normalize_request_text(request_text):
        old_kind = spec.contract.task_kind.value if spec.contract else ""
        if source_summary is not None and old_kind:
            new_kind = formulate(request_text, source_summary=source_summary).task_kind.value
            if new_kind != old_kind:
                return {
                    "message": (f"问题 {problem_id} 已有契约 ({old_kind}), "
                                f"当前输入判定为 {new_kind}: 研究类型变了, 不静默沿用"),
                    "problem_id": problem_id,
                    "existing_contract_kind": old_kind,
                    "requested_contract_kind": new_kind,
                    "options": ["resume", "new_problem"],
                    "hint": "沿用原契约请用 resume=true; 按新契约研究请换一个 problem_id",
                }
        return None
    return {
        "message": (f"问题 {problem_id} 已在研究另一个请求, 不覆盖已有规格"),
        "problem_id": problem_id,
        "existing_request": stored_request,
        "requested": request_text,
        "options": ["resume", "new_problem"],
        "hint": "继续原问题请用 resume=true; 研究新问题请换一个 problem_id",
    }


@app.get("/api/sources")
def list_sources():
    """资料源清单 (计划书 §3 R0): 每个已建资料库的规模、范围与索引状态。

    只枚举已存在的知识底座, 不创建新目录。
    """
    from src.kb.sources import list_source_sets

    return {"sources": [s.to_dict() for s in list_source_sets()]}


@app.get("/api/sources/{source_set_id}")
def describe_source(source_set_id: str):
    """单个资料源的可读范围与绑定校验结果。"""
    from src.kb.sources import describe_source_set, validate_binding

    check = validate_binding(source_set_id)
    return {"source_set_id": source_set_id,
            "source_set": check["source_set"],
            "bindable": check["ok"],
            "reason": check["reason"],
            "warnings": check["warnings"],
            "describe": describe_source_set(source_set_id).describe()}


@app.post("/api/sessions")
def start_session(req: StartRequest):
    if not (req.topic and req.topic.strip()) and not (req.request and req.request.strip()):
        raise HTTPException(400, "研究主题或自然语言描述至少填写一项")
    # ---- §3.1 G03 的固定顺序 ----
    # ① 规范化输入与文件 + ② 分配身份: 都在创建任何运行之前完成。
    snapshot = _normalized_input(req)
    thread_id = sanitize_filename((req.topic or req.request).strip()) + "_" + uuid.uuid4().hex[:6]
    session_id = uuid.uuid4().hex
    # 统一入口 (合并计划 §3 / M5 / G01): **用户不选引擎**。旧客户端若仍带 `mode`,
    # 它会被 Pydantic 忽略 (字段已从 `StartRequest` 删除); 所有研究请求都进同一张团队图。
    mode = _resolve_engine()
    # ③ 校验资料授权 (在创建 run 之前): 绑定不可用就明确拒绝, 不让研究静默退回"无资料"
    source_warnings = _validate_source_binding(snapshot)
    # ③b 规格与冲突判定也必须在**创建运行之前**: 身份相同但问题不同时直接 409, 不留下
    # 一个"建了会话却立刻失败"的悬挂记录, 也不让用户看到"我改了问题但结果没变"。
    conflict, spec_reused, contract = _ensure_problem_spec(snapshot,
                                                           resume=bool(req.resume))
    if conflict is not None and not req.resume:
        raise HTTPException(409, conflict)
    # ④ 创建 run/session: 初始状态与会话请求是**同一份快照**, 身份因此不会漂移
    checkpointer, conn = _make_checkpointer(session_id)
    session = _make_session(
        thread_id,
        topic=(req.topic or req.request).strip(),
        session_id=session_id,
        checkpointer=checkpointer,
        checkpoint_conn=conn,
        run_id=snapshot["run_id"],
        mode=mode,
        request=_session_request(snapshot),
    )
    # ⑤ 注入同一输入快照并启动
    initial_state = build_initial_state(req, snapshot)
    if source_warnings:
        initial_state["source_set_warnings"] = source_warnings
    if spec_reused:
        initial_state["spec_reused"] = True
    if contract:
        initial_state["contract"] = contract
    SESSIONS[thread_id] = session
    # 立即落盘一条 running 记录, 保证"未跑到 interrupt 就关闭"的会话也能在历史列表被看到并续跑
    _persist_session(session)
    worker = threading.Thread(target=_run_session, args=(session, initial_state), daemon=True)
    # 登记后台线程: shutdown()/delete_session 必须先等它退出再关检查点连接 (见 shutdown)
    session.worker_thread = worker
    worker.start()
    # 回传项目/问题 ID 与问题契约: 前端据此展示研究路径并调用对象级接口
    return {
        "thread_id": thread_id,
        "session_id": session_id,
        "mode": mode,
        "run_id": session.run_id,
        "project_id": snapshot["project_id"],
        "problem_id": snapshot["problem_id"],
        "contract": initial_state.get("contract"),
        "spec_reused": bool(initial_state.get("spec_reused")),
        "resumed": bool(req.resume),
    }


@app.get("/api/sessions/{thread_id}/events")
async def session_events(thread_id: str, last_event_id: int = 0):
    """SSE 事件流 (计划书 F2 / 合并计划 §3.3 G15)。

    三条不可让步的规则 (每一条都对应一个实测缺陷):

    1. **每个订阅者有自己的游标**, 事件从**持久日志**按游标读 —— 不是从共享队列里
       `get()`。共享队列只能被一个读者取走一条, 两个页面同时订阅就是互相抢事件
       (旧实现的症状: 一个页面收到的 task 事件在另一个页面里凭空消失)。
    2. **`connected` / 心跳 / 合成终止帧不占业务序号**: 它们的 SSE `id:` 沿用当前
       游标 (或不带 id), 否则客户端记下的最后一个 id 会比真实事件序号大 1, 下一条
       真实事件就被它当成"重复"丢掉。
    3. **缺口如实报告**: 游标早于日志保留窗口时发 `event_log_truncated`, 让前端
       重新加载投影, 而不是假装补齐。
    """
    session = SESSIONS.get(thread_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    def _frame(event: dict, seq: int = 0) -> str:
        payload = {k: v for k, v in event.items() if k != "_seq"}
        # `seq<=0` = 非业务帧: 不写 `id:`, 客户端的游标因此保持不变。
        head = f"id: {seq}\n" if seq and seq > 0 else ""
        return head + f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def gen():
        log = session.event_stream
        # 订阅者自己的游标: 起点是客户端给的 last_event_id。
        try:
            cursor = int(last_event_id or 0)
        except (TypeError, ValueError):
            cursor = 0
        with session.event_lock:
            pending, truncated = log.replay(cursor)
            latest = log.snapshot_seq()
        if truncated:
            # 请求的游标早于日志里最早的记录 —— 那部分已经被裁掉。此时**不能**
            # 假装补齐: 如实告知客户端"日志截断", 让它重新加载投影 (前端把未知事件
            # 类型当作 resync 信号, 见 events/session-events.ts)。
            yield _frame({"type": "event_log_truncated",
                          "message": replay_gap_note(True)}, latest)
        for event in pending:
            yield _frame(event, int(event.get("_seq", 0) or 0))
            cursor = max(cursor, int(event.get("_seq", 0) or 0))
            if event.get("type") in ("done", "stopped", "error"):
                return
        if not pending:
            # 会话已结束且客户端 (重)连接 → 立即补发终止帧, 避免空转。
            # 这些是**非业务帧**: 不占序号 (见本函数的规则 2)。
            if session.status == "done" and session.final_state is not None:
                yield _frame({"type": "done",
                              "state": _final_summary(session.final_state)}, 0)
                return
            if session.status in ("stopped", "error"):
                kind = "stopped" if session.status == "stopped" else "error"
                yield _frame({"type": kind, "message": session.error or ""}, 0)
                return
        yield _frame({"type": "connected",
                      "cursor": cursor,
                      "event_seq": session.event_seq}, 0)
        while True:
            if not await asyncio.to_thread(log.wait_for, cursor, 0.5):
                yield ": keep-alive\n\n"
                # 断线/停止之后仍然继续等: "断开连接"不等于"研究停止"; 只有会话
                # 进入终态且已交付到游标才结束。
                continue
            with session.event_lock:
                fresh, gap = log.replay(cursor)
            if gap:
                yield _frame({"type": "event_log_truncated",
                              "message": replay_gap_note(True)}, session.event_seq)
                with session.event_lock:
                    fresh, _ = log.replay(session.event_seq)
            for event in fresh:
                seq = int(event.get("_seq", 0) or 0)
                yield _frame(event, seq)
                cursor = max(cursor, seq)
                if event.get("type") in ("done", "stopped", "error"):
                    return
            if session.status in ("done", "stopped", "error") and not fresh:
                kind = {"done": "done", "stopped": "stopped",
                        "error": "error"}[session.status]
                payload = ({"type": kind, "state": _final_summary(session.final_state)}
                           if kind == "done" else
                           {"type": kind, "message": session.error or ""})
                yield _frame(payload, 0)
                return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/sessions/{thread_id}/state")
def session_state(thread_id: str):
    """会话当前状态 (计划书 F2 状态补偿): 重连后据此校正页面, 不依赖事件是否到齐。"""
    session = SESSIONS.get(thread_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return {
        "thread_id": thread_id,
        "session_id": session.session_id,
        "status": session.status,
        "mode": session.mode,
        "project_id": session.request.get("project_id", ""),
        "problem_id": session.request.get("problem_id", ""),
        "run_id": session.run_id,
        "event_seq": session.event_seq,
        "pending_interrupt_id": session.pending_interrupt_id,
        "answered_interrupts": list(session.answered_interrupts),
        "error": session.error or "",
        "final_state": (_final_summary(session.final_state)
                        if session.final_state else None),
    }


@app.post("/api/sessions/{thread_id}/respond")
def respond(thread_id: str, req: RespondRequest):
    """提交暂停点响应 (计划书 F2)。

    约束:
    - 只接受**等待态**会话: 运行中/已结束的响应会被拒绝 (避免把回答送错对象);
    - 同一 interrupt 只接受一次 (幂等键 = interrupt_id), 重复提交返回 409 而不是
      再喂一次响应 —— 否则重试会把同一回答施加两遍。
    """
    session = SESSIONS.get(thread_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.status != "waiting":
        raise HTTPException(409, f"会话当前状态为 {session.status}, 不接受响应")
    if req.interrupt_id and session.pending_interrupt_id \
            and req.interrupt_id != session.pending_interrupt_id:
        raise HTTPException(409, {
            "message": "响应对应的暂停点已过期",
            "expected": session.pending_interrupt_id,
            "received": req.interrupt_id,
        })
    if req.interrupt_id and req.interrupt_id in session.answered_interrupts:
        return {"ok": True, "duplicate": True,
                "detail": "该暂停点已回答过, 未重复施加"}
    session.messages.append({"role": "user", "text": req.response})
    session.responses.put(req.response)
    _persist_session(session)
    return {"ok": True, "interrupt_id": req.interrupt_id or session.pending_interrupt_id}


@app.post("/api/sessions/{thread_id}/stop")
def stop_session(thread_id: str):
    session = SESSIONS.get(thread_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    # 停止语义封装在会话里 (置停止标志 + 投递停止哨兵解除 interrupt 阻塞)。
    # 端点不接触私有哨兵, 也就不可能塞错对象。
    session.request_stop()
    return {"ok": True}


@app.post("/api/sessions/{session_id}/resume")
def resume_session(session_id: str):
    """断点续跑: 从历史会话记录重建 Session, 用同一 thread_id + 持久化 checkpointer
    从上次 checkpoint 继续 (若停在 interrupt 则重新提示用户输入)。"""
    record = load_conversation(session_id)
    if record is None:
        raise HTTPException(404, "会话不存在")
    thread_id = record.get("thread_id", "")
    if not thread_id:
        raise HTTPException(400, "会话记录缺少 thread_id, 无法恢复")

    # 已有活跃会话 (内存中仍在跑/等待) → 直接返回, 前端连 SSE 即可
    existing = SESSIONS.get(thread_id)
    if existing is not None:
        return {"thread_id": thread_id, "session_id": session_id, "status": existing.status}

    checkpointer, conn = _make_checkpointer(session_id)
    stored_request = dict(record.get("request") or {})
    # 旧会话属于已退役的形式化研究入口 (落盘记录里带 `mode="theory"`): 那条路径已删除,
    # 它的 checkpoint 里是旧图的节点, 用团队图续跑只会得到一张读不懂的状态。**如实拒绝**
    # 并给出可执行的下一步, 而不是"看起来在续跑"。
    if str(stored_request.get("mode") or "") == "theory":
        raise HTTPException(409, {
            "message": ("该会话由已退役的形式化研究入口创建, 无法用团队引擎续跑: "
                        "请以同一 project_id/problem_id 重新启动一次研究"),
            "session_id": session_id, "retired_engine": "theory",
            "retryable": True,
        })
    session = _make_session(
        thread_id,
        topic=record.get("topic", ""),
        session_id=session_id,
        checkpointer=checkpointer,
        checkpoint_conn=conn,
        run_id=record.get("run_id", ""),
        mode=_resolve_engine(),
        # G03: 续跑也必须**先**把落盘请求快照交给会话, 再建图 —— 团队装配从
        # `session.request` 读身份与资料, 先建图会让续跑的团队拿到空身份。
        request=stored_request,
    )
    session.created_at = record.get("created_at", session.created_at)
    # 恢复历史对话, 避免续跑落盘时用空 messages 覆盖历史记录
    session.messages = list(record.get("messages", []))
    SESSIONS[thread_id] = session

    # 没有 checkpoint 也能续: 团队应用把落盘请求快照重新走一遍画像/计划 (G16 的
    # `resume` 语义)。因此这里不再需要"有 checkpoint 才敢续"的判断, 也不需要构造
    # 引擎专属的初始状态 —— 输入快照本身就在 `session.request` 里。
    initial_state = None

    worker = threading.Thread(target=_run_session, args=(session, initial_state), daemon=True)
    session.worker_thread = worker  # 续跑同样要登记, 关闭连接前需等它退出
    worker.start()
    return {"thread_id": thread_id, "session_id": session_id, "status": session.status}


@app.get("/favicon.ico")
def favicon():
    """浏览器总会请求 /favicon.ico: 给一个图标而不是刷 404 日志。"""
    return _favicon_response()


@app.get("/favicon.svg")
def favicon_svg():
    """页面模板用 `<link rel="icon" href="/favicon.svg">` 引用 svg 图标。

    之前只登记了 `.ico` 路由, 于是每次打开页面都会多一条
    `GET /favicon.svg 404` 的服务端日志 (图标其实就在构建产物里)。
    """
    return _favicon_response()


def _favicon_response():
    """图标来源: 优先构建产物, 再退回源码 public 目录 (未构建时也能显示)。"""
    for icon in (WEB_DIST / "favicon.svg", WEB_DIR / "public" / "favicon.svg"):
        if icon.is_file():
            return FileResponse(str(icon), media_type="image/svg+xml",
                                headers={"Cache-Control": "no-cache"})
    return Response(status_code=204)


@app.post("/api/uploads")
def upload_attachments(
    kind: str = Form("literature"),
    project_id: str = Form(""),
    problem_id: str = Form(""),
    topic: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    """上传附件: kind=problem 补充问题说明, kind=literature 人工补充文献。

    逐文件返回结果: 单个文件失败不影响其它文件; 解析失败保留原件并如实报错。
    """
    from src.utils import uploads

    if kind not in ("problem", "literature"):
        raise HTTPException(400, f"未知的上传用途: {kind}")
    if not files:
        raise HTTPException(400, "没有收到文件")
    if kind == "literature" and not topic.strip():
        raise HTTPException(400, "补充文献需要指定资料库主题")

    results = []
    # R7: 用上传声明的大小在**读取之前**拒绝, 并限制文件数与单请求总量
    declared = [uploads.declared_size(item) for item in files]
    declared_total = sum(size for size in declared if size)
    batch_error = uploads.check_batch(len(files), declared_total)
    if batch_error:
        raise HTTPException(413, batch_error)
    # 声明大小可以撒谎 (缺失/OA 客户端不填), 因此请求级总量还要按**实际**字节数再记一次
    budget = uploads.ByteBudget(limit=uploads.MAX_REQUEST_BYTES)
    for index, (item, size) in enumerate(zip(files, declared)):
        name = item.filename or "upload"
        # 声明大小缺失 (None) 不能当成 0: 那是"空文件", 而缺失只能交给流式上限兜底
        if size is not None:
            early = uploads.check(name, size)
            if early:
                results.append({"ok": False, "filename": name, "error": early})
                continue
        if budget.remaining() <= 0:
            # 预算已用尽: 不再逐个"再拒一次", 剩下的文件一次性如实告知
            results.append({"ok": False, "filename": name,
                            "error": str(uploads.UploadRequestTooLarge(
                                budget.limit, budget.used))})
            skipped = [f.filename or "upload" for f in files[index + 1:]]
            if skipped:
                results.append({"ok": False, "filename": "(其余文件)",
                                "error": "本次请求已达到总量上限, 未处理剩余文件: "
                                         + ", ".join(skipped)})
            break
        try:
            # R7: 分块流式落盘, 暂存路径随后交给保存逻辑接管
            with uploads.staged_upload(item, declared_size=size, budget=budget) as staged:
                if kind == "problem":
                    results.append(uploads.save_problem_attachment(
                        project_id.strip(), problem_id.strip(), name, staged.path))
                else:
                    results.append(uploads.save_literature(topic.strip(), name, staged.path))
        except uploads.UploadRequestTooLarge as e:
            # 请求级超限: 其余文件不再尝试, 明确告知本次上传被拒
            results.append({"ok": False, "filename": name, "error": str(e)})
            skipped = [f.filename or "upload" for f in files[index + 1:]]
            results.append({"ok": False, "filename": "(其余文件)",
                            "error": "本次请求已达到总量上限, 未处理剩余文件"
                                     + (f": {', '.join(skipped)}" if skipped else "")})
            break
        except uploads.UploadTooLarge as e:
            results.append({"ok": False, "filename": name, "error": str(e)})
    ok = sum(1 for r in results if r.get("ok"))
    return {"kind": kind, "ok": ok, "failed": len(results) - ok, "results": results}


@app.get("/api/uploads")
def list_attachments(project_id: str = "", topic: str = "", kind: str = "",
                     problem_id: str = ""):
    """列出附件登记 (R1: 按用途分别过滤后合并, 不做两个互斥字段的交集)。"""
    from src.utils import uploads

    if kind and kind not in uploads.KINDS:
        raise HTTPException(400, f"未知的上传用途: {kind}")
    return {"attachments": uploads.list_for(project_id=project_id, topic=topic,
                                            kind=kind, problem_id=problem_id)}


@app.delete("/api/uploads/{attachment_id}")
def delete_attachment(attachment_id: str, project_id: str = "", topic: str = ""):
    from src.utils import uploads

    if not uploads.delete(attachment_id, project_id=project_id, topic=topic,
                          remove_file=True):
        raise HTTPException(404, f"附件不存在或不属于该项目/主题: {attachment_id}")
    return {"deleted": attachment_id}


@app.get("/api/research/{project_id}/state")
def research_state(project_id: str, problem_id: str = ""):
    """科研工作台数据: 问题/结论状态表/未决义务/证据定位/实验规格/路线/门槛。

    只读接口, 不触发研究动作。同项目多问题时必须显式给出 `problem_id`,
    否则返回 409 与可选问题清单 (F0-2)。
    """
    return _research_state(project_id, problem_id)


@app.get("/api/research/{project_id}/problems")
def research_problems(project_id: str):
    """项目下的研究问题清单 (供前端切换, 不猜测当前问题)。"""
    from src.research.store import ResearchStore, default_db_path

    if not default_db_path(project_id).exists():
        raise HTTPException(404, "该项目不存在")
    store = ResearchStore(project_id)
    try:
        return {"project_id": project_id, "problems": problem_index(store)}
    finally:
        store.close()


@app.post("/api/research/{project_id}/feedback")
def research_feedback(project_id: str, req: RespondRequest, problem_id: str = ""):
    """把自然语言研究意见转成**对象级动作**并施加 (计划书 §5.1)。

    反馈必须落到具体 assumption_id / claim_id / step_id; 无法确定对象时不猜,
    返回 `needs_clarification` 与澄清问题, 研究状态保持不变。
    作用对象限定在当前研究问题内 —— 不得改到同项目的另一个问题。

    **只有团队路径**: 反馈必须落到具体对象上, 而"落到对象上"要有一条**运行中的研究**
    来承接 (需求 → 主控派工 → 唯一提交口)。旧引擎的 `submit_feedback` 已随引擎退役
    删除, 因此没有团队运行状态的问题会得到**可恢复**的 409 与下一步说明, 而不是
    "看起来处理了"。
    """
    from src.research.store import ResearchStore, default_db_path

    if not default_db_path(project_id).exists():
        raise HTTPException(404, "该项目下没有研究问题规格")
    store = ResearchStore(project_id)
    try:
        try:
            resolved, spec = resolve_problem(store, problem_id or req.problem_id)
        except ProblemNotFound as e:
            raise HTTPException(404, str(e)) from e
        except ProblemAmbiguous as e:
            raise HTTPException(409, {"message": str(e), "problems": e.problems}) from e

        from src.research.feedback import find_latest_team_run

        team_state = find_latest_team_run(store, resolved)
        if not team_state:
            raise HTTPException(409, {
                "message": ("该问题还没有团队运行状态, 反馈无法落到对象上: "
                            "请先以同一 project_id/problem_id 启动一次团队研究"),
                "problem_id": resolved, "retryable": True,
                "how_to_fix": "POST /api/sessions (同一 project_id 与 problem_id)",
            })
        outcome = _team_feedback(store, spec, resolved, team_state, req, project_id)
    finally:
        store.close()
    status = 200 if outcome.get("ok") else 400
    return JSONResponse(content=outcome, status_code=status)


def _team_feedback(store, spec, problem_id: str, team_state: dict,
                   req: RespondRequest, project_id: str) -> dict:
    """团队路径的反馈处理: 需求 → 主控派工 → 唯一提交口落盘。"""
    from src.graph.research_graph import TeamRun
    from src.research.feedback import feedback_need_for

    need, info = feedback_need_for(project_id=project_id, problem_id=problem_id,
                                   text=req.response,
                                   target_object_id=req.object_id, store=store,
                                   scope=req.scope)
    if need is None:
        return info
    with TeamRun(project_id=project_id, problem_id=problem_id,
                 run_id=str(team_state.get("run_id", "") or ""),
                 request=str(team_state.get("request", "") or spec.original_request),
                 source_set_ids=list(team_state.get("source_set_ids") or []),
                 source_policy=str(team_state.get("source_policy", "user_kb")),
                 attachments=list(team_state.get("attachments") or []),
                 attachment_text=str(team_state.get("attachment_text", "") or ""),
                 max_rounds=int(team_state.get("max_rounds", 12) or 12)) as team:
        result = team.submit_feedback(
            statement=need.statement, owner=need.owner,
            target_ref=(need.blocked_refs[0] if need.blocked_refs else None),
            why=need.why, acceptance=list(need.acceptance), kind=need.kind,
            feedback_id=req.feedback_id, hints=need.hints)
        if result.get("ok") and not result.get("duplicate"):
            from src.graph.team_session import ExportError, TeamSession

            try:
                package = TeamSession(team).export(run_id=team.run_id)
                result["package_dir"] = str(package) if package else ""
            except ExportError as error:
                # 研究意见已施加，不能把“导出失败”伪装成“反馈未处理”。
                result["export_error"] = str(error)
    result.update({k: v for k, v in info.items() if k != "ok"})
    return result


@app.post("/api/research/fork")
def fork_research(req: ForkRequest):
    """fork: 从指定快照派生新的研究问题 (明确哪些结论被导入、哪些需重算)。

    与 resume 的区别: resume 继续**同一**问题的未完成动作; fork 新建问题,
    只把指定结论作为待重验前提导入, **不复制**任何验证记录。

    F0-5: 派生必须在同一步里创建新问题的 `ResearchSpec`, 并返回可导航的
    `problem_id`; 只导入命题而不建规格的话, 新问题在工作台里根本打不开。
    """
    project_id = (req.project_id or "").strip()
    if not project_id:
        raise HTTPException(400, "缺少 project_id")
    from src.research.forking import fork_from_snapshot
    from src.research.store import ResearchStore, default_db_path

    if not default_db_path(project_id).exists():
        raise HTTPException(404, "该项目下没有研究问题规格")
    store = ResearchStore(project_id)
    try:
        try:
            spec_id, spec = resolve_problem(store, req.problem_id)
        except ProblemNotFound as e:
            raise HTTPException(404, str(e)) from e
        except ProblemAmbiguous as e:
            raise HTTPException(409, {"message": str(e), "problems": e.problems}) from e
        # 派生只需要**存储 + 输入规格**: 它是用户可见功能, 不该因为引擎退役而消失,
        # 因此不再建 `TheoryEngine` (§5.5)。旧引擎的路线记账由引擎自己的同名方法追加。
        outcome = fork_from_snapshot(
            store, spec, source_snapshot_id=req.snapshot_id,
            claim_ids=req.claim_ids or None, new_problem_id=req.new_problem_id)
    finally:
        store.close()
    if not outcome.get("ok"):
        raise HTTPException(400, outcome.get("reason", "派生失败"))
    new_problem_id = outcome.get("new_problem_id", "")
    # 派生后必须能直接打开新问题 (而不是 404)
    check = ResearchStore(project_id)
    try:
        navigable = bool(new_problem_id) and check.get("spec", new_problem_id) is not None
    finally:
        check.close()
    return {"project_id": project_id, "spec_id": spec_id,
            "problem_id": new_problem_id, "navigable": navigable, **outcome}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话: 连同对话记录 / 检查点 / 产出 / 检索缓存 / 向量库一并删除,
    便于按会话管理项目进度。"""
    record = load_conversation(session_id)
    if record is None:
        raise HTTPException(404, "会话不存在")
    thread_id = record.get("thread_id", "")
    run_id = record.get("run_id", "")
    topic = record.get("topic", "")

    removed = []
    # 1. 停止并从内存移除活跃会话
    if thread_id and thread_id in SESSIONS:
        sess = SESSIONS.pop(thread_id)
        sess.request_stop()
        # 先关检查点连接, 否则第 3 步的 unlink 在 Windows 上会因句柄未释放而失败
        sess.shutdown()
    # 2. 删除对话记录
    if delete_conversation(session_id):
        removed.append(f"data/conversations/{session_id}.json")
    # 3. 删除检查点 (每会话一个 SQLite, 含 WAL/SHM)
    closed = True
    try:
        for suffix in ("", "-shm", "-wal"):
            ckpt = CHECKPOINT_DIR / f"{session_id}.sqlite{suffix}"
            if ckpt.exists():
                ckpt.unlink()
        removed.append(f"data/checkpoints/{session_id}.sqlite")
    except Exception as e:  # noqa: BLE001 - 句柄未释放时必须如实报告, 不能假装删掉了
        closed = False
        removed.append(f"data/checkpoints/{session_id}.sqlite 删除失败: {e}")
    # 4. 删除产出文件夹 outputs/{run_id}/
    if run_id:
        out_dir = safe_join(output_dir(), run_id)
        try:
            # 只允许删除 outputs/ 之下、且非 outputs/ 本身的目标 (拒绝越界与同前缀兄弟目录)
            if (out_dir is not None and out_dir.exists()
                    and out_dir != output_dir().resolve()):
                shutil.rmtree(out_dir)
                removed.append(f"outputs/{run_id}")
        except Exception:
            pass
    # 5. 删除检索缓存 (data/pipeline_cache/{topic}.json)
    if topic:
        try:
            from src.utils.pipeline_cache import cache_dir, resolve_cache_topic

            resolved = resolve_cache_topic(topic)
            if resolved:
                cache_path = cache_dir() / f"{sanitize_filename(resolved)}.json"
                if cache_path.exists():
                    cache_path.unlink()
                    removed.append(f"data/pipeline_cache/{cache_path.name}")
        except Exception:
            pass
    # 6. 共享检索资源 (向量库 / 主题缓存): **只有确认没有其他会话再用**才清。
    #    早期实现在这里直接清空三个全局 collection —— 多项目共用实例时, 删掉会话 A
    #    会把会话 B 的检索资料一起毁掉 (计划书 P0-4: 数据损伤)。
    #    判定依据: 除本会话外, 是否还有会话引用同一 project_id **或同一主题**
    #    (collection 是按主题命名的共享实例, 跨项目同主题同样共享)。
    owning_project = str(record.get("project_id", "")
                         or (record.get("request") or {}).get("project_id", "") or "")
    shared_removed, shared_kept = _cleanup_shared_retrieval_resources(
        session_id, owning_project, topic)
    removed.extend(shared_removed)
    return {"ok": True, "removed": removed, "checkpoint_removed": closed,
            "kept_shared": shared_kept}


def _has_other_session_using(project_id: str, exclude_session_id: str) -> bool:
    """是否还有**其他**会话引用同一 project_id (共享资料的存活判据)。"""
    return bool(_other_referrers(exclude_session_id, project_id=project_id, topic=""))


def _other_referrers(exclude_session_id: str, *, project_id: str = "",
                     topic: str = "") -> list[str]:
    """还有哪些会话引用同一项目 / 同一主题 (返回 `session_id: 依据` 可读列表)。

    为什么主题也算依据: Chroma collection 是**按主题**命名的共享实例
    (`CHROMA_CONFIG`, 与 project_id 无关)。两个不同项目研究同一主题时共用同一批
    collection —— 早期只按 `project_id` 判定"是否还有别人在用", 于是删掉项目 A 会把
    项目 B 的同主题检索资料一起清空 (合并计划 §7.4 明确要求跨项目共享库也要保住)。
    """
    referrers: list[str] = []
    try:
        for item in list_conversations():
            other = str(item.get("session_id", ""))
            if not other or other == exclude_session_id:
                continue
            other_project = str(item.get("project_id", "") or "")
            other_topic = str(item.get("topic", "") or "")
            same_project = bool(project_id) and other_project == project_id
            same_topic = bool(topic) and other_topic == topic
            if same_project or same_topic:
                why = "同一项目" if same_project else "同一主题"
                referrers.append(f"{other} ({why})")
    except Exception:  # noqa: BLE001
        # 读不到会话清单时按"可能还有别人"处理: 宁可少删, 不可误删共享资料
        return [f"(无法读取会话清单, 按仍有引用处理) {project_id or topic}"]
    return referrers


def _cleanup_shared_retrieval_resources(session_id: str, project_id: str,
                                        topic: str = ""):
    """按所有权清理共享检索资源。返回 `(已删列表, 保留列表)`。

    只删除**属于该会话且无其他会话引用**的资源; 有共享引用时明确记入"保留", 让调用方
    (与人) 看到"这次没有删掉共享库", 而不是静默清空。

    两条依据 (合并计划 §7.4 / M4):
    - `project_id`: 同一项目还有别的会话;
    - `topic`: Chroma collection 按主题命名, 与 project_id 无关 —— 跨项目同主题也共享。
    """
    removed: list[str] = []
    kept: list[str] = []
    referrers = _other_referrers(session_id, project_id=project_id, topic=topic)
    if referrers:
        kept.append(
            "data/chroma (向量库): 仍有其他会话引用同一批资料, 已保留 —— "
            + "、".join(referrers[:5]))
        return removed, kept
    try:
        from src.config import CHROMA_CONFIG
        from src.rag.vector_store import clear_collection

        for name in (CHROMA_CONFIG["collection_name"],
                     CHROMA_CONFIG["fulltext_collection"],
                     CHROMA_CONFIG["wiki_collection"]):
            try:
                clear_collection(name)
            except Exception:
                pass
        removed.append("data/chroma (向量库)")
    except Exception:
        pass
    return removed, kept


@app.get("/api/conversations")
def conversations():
    """列出所有历史会话 (元信息), 供前端历史回看列表。"""
    return {"conversations": list_conversations()}


@app.get("/api/conversations/{session_id}")
def get_conversation(session_id: str):
    """读取单条历史会话 (含完整消息)。"""
    record = load_conversation(session_id)
    if record is None:
        raise HTTPException(404, "会话不存在")
    return record


@app.delete("/api/cache")
def clear_cache():
    """清除已有缓存 (检索缓存 / 向量库 / 检查点 / 会话记忆), 便于转换研究主题。

    输出产物 (outputs/) 保留。会话记忆一并清除, 避免记忆指向已删除的缓存。"""
    removed = []
    # 检索缓存: 按新接口清空 (兼容扁平旧格式与按 run 分片的新格式), 而不是只 rmtree 目录
    try:
        from src.utils.pipeline_cache import clear_all_caches

        purged = clear_all_caches()
        if purged:
            removed.append(f"data/pipeline_cache ({len(purged)} 项)")
    except Exception:  # noqa: BLE001 - 清理失败不掩盖其它清理结果
        pass
    for dir_name in ("chroma",):
        p = DATA_DIR / dir_name
        if p.exists():
            shutil.rmtree(p)
            removed.append(f"data/{dir_name}")
    ckpt = DATA_DIR / "pipeline_checkpoints.sqlite"
    if ckpt.exists():
        try:
            ckpt.unlink()
            removed.append("data/pipeline_checkpoints.sqlite")
        except Exception:
            pass
    if CHECKPOINT_DIR.exists():
        try:
            shutil.rmtree(CHECKPOINT_DIR)
            removed.append("data/checkpoints")
        except Exception:
            pass
    mem = DATA_DIR / "session_memory.json"
    if mem.exists():
        try:
            mem.unlink()
            removed.append("data/session_memory.json")
        except Exception:
            pass
    # M4: 会话记忆现在按会话分片 (data/session_memory/<session_id>.json), 一并清除 ——
    # 否则"清除缓存"后旧的按会话记忆仍会让"继续撰写"沿用已清空的上下文。
    mem_dir = DATA_DIR / "session_memory"
    if mem_dir.exists():
        try:
            shutil.rmtree(mem_dir)
            removed.append("data/session_memory")
        except Exception:
            pass
    return {"ok": True, "removed": removed}


@app.get("/api/contexts")
def list_contexts():
    """列出所有缓存上下文 (主题 + 进度), 用于多上下文切换。"""
    from src.utils.pipeline_cache import list_contexts as _list_contexts

    return {"contexts": _list_contexts()}


@app.get("/api/contexts/{topic}/notes")
def get_context_notes(topic: str):
    """读取某个缓存上下文的文献综述笔记 (Markdown), 供切换上下文时预览。"""
    from src.utils.pipeline_cache import load_retrieval_cache_raw, resolve_cache_topic

    resolved = resolve_cache_topic(topic)
    if not resolved:
        raise HTTPException(404, "上下文不存在")
    data = load_retrieval_cache_raw(resolved)
    if data is None:
        raise HTTPException(404, "上下文不存在")
    return {
        "topic": data.get("topic") or resolved,
        "notes": data.get("literature_review_notes", ""),
        "stages": data.get("stages", []),
        "refs": len(data.get("verified_references", []) or []),
    }


def _artifact_metadata(root: Path) -> dict:
    """交付目录的归属信息 (project/problem/run + 交付级别)。

    F3: 文件清单必须能按当前 run/问题过滤, 而不是把全部 outputs/ 递归列出。
    归属来自交付包里的 `manifest.json` / `research_spec.json`。
    """
    meta: dict = {}
    manifest = root / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                meta.update({k: data.get(k) for k in
                             ("project_id", "problem_id", "snapshot_id",
                              "delivery_level", "gate_passed")})
                meta["run_id"] = data.get("run_id") or root.name
        except Exception:  # noqa: BLE001 - 坏 manifest 不应让文件页 500
            meta["manifest_error"] = True
    spec = root / "research_spec.json"
    if spec.is_file() and not meta.get("problem_id"):
        try:
            data = json.loads(spec.read_text(encoding="utf-8"))
            meta.setdefault("project_id", data.get("project_id", ""))
            meta["problem_id"] = data.get("problem_id", "")
        except Exception:  # noqa: BLE001
            pass
    meta.setdefault("project_id", "")
    meta.setdefault("problem_id", "")
    meta.setdefault("run_id", root.name)
    return meta


def _package_roots() -> dict[str, dict]:
    """outputs/research/<project>/<snapshot>/ 交付包目录 → 归属信息。"""
    out: dict[str, dict] = {}
    root = output_dir()
    research = root / "research"
    if not research.exists():
        return out
    for project_dir in research.iterdir():
        if not project_dir.is_dir():
            continue
        for snapshot_dir in project_dir.iterdir():
            if not snapshot_dir.is_dir():
                continue
            meta = _artifact_metadata(snapshot_dir)
            meta.setdefault("project_id", project_dir.name)
            out[snapshot_dir.relative_to(root).as_posix()] = meta
    return out


def _artifact_owner(rel_name: str, roots: dict[str, dict]) -> dict:
    """按最长前缀匹配把文件归属到交付包 (没有交付包则为空)。"""
    best = ""
    for prefix in roots:
        if rel_name == prefix or rel_name.startswith(prefix + "/"):
            if len(prefix) > len(best):
                best = prefix
    if not best:
        return {"run_id": rel_name.split("/")[0] if "/" in rel_name else ""}
    return dict(roots[best], package_dir=best)


@app.get("/api/artifacts")
def list_artifacts(project_id: str = "", problem_id: str = "", run_id: str = "",
                   bind: bool = True):
    """输出文件清单 (F3)。

    - 默认给每个文件附上归属 (`project_id/problem_id/run_id/delivery_level`);
    - 传入 `project_id`/`problem_id`/`run_id` 时只返回**属于该问题/该次运行**的文件,
      不再把全部 outputs/ 混在一起;
    - 无法归属的文件 (例如尚未打包的中间产物) 只在未过滤时返回, 并用
      `unattributed: true` 标出, 不冒充当前问题的交付物。
    """
    root = output_dir()
    if not root.exists():
        return {"files": [], "roots": {}}
    roots = _package_roots() if bind else {}
    files = []
    seen_roots: set[str] = set()
    for f in sorted(root.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        owner = _artifact_owner(rel, roots)
        entry = {"name": rel, "size": f.stat().st_size, "mtime": f.stat().st_mtime,
                 **owner}
        if not owner.get("package_dir"):
            entry["unattributed"] = True
        if project_id and entry.get("project_id") != project_id:
            continue
        if problem_id and entry.get("problem_id") != problem_id:
            continue
        if run_id and entry.get("run_id") != run_id:
            continue
        # 只统计**被返回文件**所属的交付包, 否则过滤后仍会报告别的问题的包
        if entry.get("package_dir"):
            seen_roots.add(entry["package_dir"])
        files.append(entry)
    return {"files": files, "roots": {k: roots[k] for k in sorted(seen_roots)}}


@app.get("/api/artifacts/download")
def download_artifact(name: str):
    path = safe_join(output_dir(), name)
    if path is None or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(path))


@app.get("/api/artifacts/{name:path}")
def get_artifact(name: str):
    path = safe_join(output_dir(), name)
    if path is None or not path.is_file():
        raise HTTPException(404, "文件不存在")
    if path.suffix.lower() in (".md", ".txt", ".tex", ".log", ".json"):
        try:
            return {"name": name, "content": path.read_text(encoding="utf-8", errors="replace")}
        except Exception as e:
            return {"name": name, "error": str(e)}
    return {"name": name, "binary": True, "url": f"/api/artifacts/download?name={name}"}


def main():
    import os
    import sys as _sys
    import time
    import webbrowser

    import uvicorn

    ensure_utf8_console()

    # M4 / 用户决策: 旧检索缓存按**主题**存放, 与运行/项目无关 (跨项目同主题会互相
    # 复用资料)。缓存随时可以重新检索得到, 因此不做迁移 —— 启动时直接清除一次。
    try:
        from src.utils.pipeline_cache import clear_all_caches

        purged = clear_all_caches()
        if purged:
            print(f"  已清除旧检索缓存 {len(purged)} 项 (按主题的旧格式不再保留)")
    except Exception as e:  # noqa: BLE001 - 清理失败不得阻止启动
        print(f"  [warning] 旧检索缓存清理失败: {e}")

    no_browser = "--no-browser" in _sys.argv or os.getenv("AIR_NO_BROWSER", "0") == "1"

    print("=" * 60)
    print("  AIR智能体研究系统 — Web 界面")
    if no_browser:
        print("  请手动打开浏览器访问: http://127.0.0.1:8000")
    else:
        print("  浏览器将自动打开: http://127.0.0.1:8000")
    print("  关闭本窗口或按 Ctrl+C 停止服务")
    print("=" * 60)

    if not no_browser:
        def _open_browser():
            time.sleep(2)
            try:
                webbrowser.open("http://127.0.0.1:8000")
            except Exception:
                pass

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
