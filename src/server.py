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
import queue
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.utils.console import ensure_utf8_console
from src.utils.conversation_store import save_conversation, list_conversations, load_conversation, delete_conversation
from src.utils.file_utils import sanitize_filename
from src.config import MAX_REVISIONS, OUTPUT_DIR, DATA_DIR
from src.graph.pipeline import build_pipeline, _get_langfuse_handler, _describe_node

_STOP = object()

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"
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


class StartRequest(BaseModel):
    topic: str = ""
    request: str = ""
    keywords: list[str] = []
    subtopics: list[str] = []
    time_range: str = "2019-2026"
    max_revisions: int | None = None
    skip_retrieval: bool = False


class RespondRequest(BaseModel):
    response: str


class Session:
    def __init__(self, thread_id: str, topic: str = "", session_id: str | None = None,
                 checkpointer=None, checkpoint_conn=None, run_id: str = ""):
        self.thread_id = thread_id
        self.session_id = session_id or uuid.uuid4().hex
        self.topic = topic
        self.run_id = run_id  # outputs/{run_id}/ 产物子目录
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.checkpointer = checkpointer or MemorySaver()
        self.checkpoint_conn = checkpoint_conn
        self.app = build_pipeline(self.checkpointer)
        self.config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        lf = _get_langfuse_handler()
        if lf:
            self.config["callbacks"] = [lf]
        self.events: queue.Queue = queue.Queue()
        self.responses: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.status = "running"  # running / waiting / done / stopped / error
        self.final_state: dict | None = None
        self.error: str | None = None
        self.messages: list[dict] = []
        self.request: dict = {}

    def emit(self, event: dict):
        self.events.put(event)
        msg = _event_to_message(event)
        if msg is None:
            return
        # 断点续跑时 checkpoint 会重新抛出与历史最后一条相同的 interrupt,
        # 去重避免历史记录里出现重复的中断卡片 (SSE 事件照常推送, 只影响落盘)。
        if msg.get("role") == "interrupt" and self.messages and self.messages[-1].get("role") == "interrupt":
            if self.messages[-1].get("title") == msg.get("title"):
                return
        self.messages.append(msg)


def _event_to_message(event: dict) -> dict | None:
    """把 SSE 事件映射为会话消息记录 (供历史回看与落盘)。"""
    t = event.get("type")
    if t in ("node", "log"):
        return {"role": "node", "text": event.get("text", "")}
    if t == "interrupt":
        p = event.get("payload") or {}
        return {
            "role": "interrupt",
            "title": p.get("title", ""),
            "hint": p.get("hint", ""),
            "content": p.get("content", ""),
        }
    if t == "done":
        return {"role": "done", "text": "完成"}
    if t == "stopped":
        return {"role": "stopped", "text": "已停止"}
    if t == "error":
        return {"role": "error", "text": event.get("message", "")}
    return None


def _session_record(session: Session) -> dict:
    """构造会话记录 (供落盘)。"""
    return {
        "session_id": session.session_id,
        "thread_id": session.thread_id,
        "topic": session.topic,
        "run_id": session.run_id,
        "created_at": session.created_at,
        "status": session.status,
        "summary": _final_summary(session.final_state) if session.final_state else {},
        "messages": list(session.messages),
        "request": dict(session.request),
    }


def _persist_session(session: Session):
    """把会话记录写入 data/conversations/ (失败不中断会话)。"""
    try:
        save_conversation(session.session_id, _session_record(session))
    except Exception:
        pass


SESSIONS: dict[str, Session] = {}


def _final_summary(final: dict) -> dict:
    return {
        "draft_path": final.get("draft_path", ""),
        "paper_tex_path": final.get("paper_tex_path", ""),
        "review_report_path": final.get("review_report_path", ""),
        "literature_notes_path": final.get("literature_notes_path", ""),
        "figure_count": len(final.get("figure_paths", []) or []),
        "review_score": final.get("review_score", "N/A"),
        "revision_count": final.get("revision_count", 0),
        "total_cost": final.get("total_cost", 0),
        "error": final.get("error", ""),
    }


def build_initial_state(req: StartRequest) -> dict:
    # 自然语言请求与检索缓存加载均由入口 research_planner_node 处理,
    # 此处仅透传原始输入 (主题可能为空, 待 Planner 提取)。
    # run_id 用作 outputs/{run_id}/ 产物子目录名, 使不同对话产物隔离、便于查看;
    # 存进 state 后经 checkpoint 持久化, 断点续跑也能沿用同一目录。
    base = sanitize_filename((req.topic or req.request).strip())[:20].strip("_") or "research"
    run_id = f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return {
        "messages": [],
        "research_topic": req.topic.strip(),
        "research_request": req.request.strip(),
        "topic_keywords": req.keywords or [],
        "sub_topics": req.subtopics or [],
        "time_range": req.time_range,
        "run_id": run_id,
        "revision_count": 0,
        "max_revisions": req.max_revisions if req.max_revisions is not None else MAX_REVISIONS,
        "retrieved_papers": [],
        "current_phase": "start",
        "skip_retrieval": bool(req.skip_retrieval),
        "interactive": True,
    }


class _StdoutBridge:
    """把后台图线程的 print 输出桥接为 SSE 'log' 事件, 同时保留控制台输出。

    子智能体 (文献检索/PDF下载/引用核查等) 的进度 print 原本只进服务端控制台,
    通过本桥接器逐行转发给前端, 让用户能实时看到子智能体调用过程。
    """

    def __init__(self, session: "Session", orig):
        self.session = session
        self.orig = orig
        self._buf = ""

    def write(self, s: str):
        try:
            self.orig.write(s)
            self.orig.flush()
        except Exception:
            pass
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self.session.emit({"type": "log", "text": line})

    def flush(self):
        try:
            self.orig.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.orig, name)


def _run_session(session: Session, initial_state: dict | None = None):
    """在后台线程运行流水线, 事件经 queue → SSE 推给前端。

    initial_state=None 时表示断点续跑: 用同一 thread_id + 持久化 checkpointer 从
    上次 checkpoint 继续 (stream(None, config))。若上次停在 interrupt, stream 会
    重新抛出 __interrupt__, 进入下方等待用户响应的分支。
    """
    import sys

    inputs: Any = initial_state
    orig_stdout = sys.stdout
    sys.stdout = _StdoutBridge(session, orig_stdout)
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
                    session.emit({"type": "interrupt", "payload": intr.value})
                    _persist_session(session)
                    response = session.responses.get()  # 阻塞等待用户响应
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
                        topic = ((node_state or {}).get("research_topic") or "").strip()
                        if topic and topic != session.topic:
                            session.topic = topic
                            _persist_session(session)
                        session.emit({
                            "type": "node",
                            "name": node_name,
                            "text": _describe_node(node_name, node_state),
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
        sys.stdout = orig_stdout


app = FastAPI(title="AIR智能体研究系统")


@app.get("/")
def index():
    if not INDEX_HTML.exists():
        raise HTTPException(404, "前端页面不存在: src/web/index.html")
    return HTMLResponse(
        INDEX_HTML.read_text(encoding="utf-8-sig"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/api/sessions")
def start_session(req: StartRequest):
    if not (req.topic and req.topic.strip()) and not (req.request and req.request.strip()):
        raise HTTPException(400, "研究主题或自然语言描述至少填写一项")
    thread_id = sanitize_filename((req.topic or req.request).strip()) + "_" + uuid.uuid4().hex[:6]
    session_id = uuid.uuid4().hex
    checkpointer, conn = _make_checkpointer(session_id)
    session = Session(
        thread_id,
        topic=(req.topic or req.request).strip(),
        session_id=session_id,
        checkpointer=checkpointer,
        checkpoint_conn=conn,
    )
    session.request = {
        "topic": req.topic,
        "request": req.request,
        "keywords": req.keywords or [],
        "subtopics": req.subtopics or [],
        "time_range": req.time_range,
        "max_revisions": req.max_revisions,
        "skip_retrieval": req.skip_retrieval,
    }
    SESSIONS[thread_id] = session
    initial_state = build_initial_state(req)
    session.run_id = initial_state.get("run_id", "")
    # 立即落盘一条 running 记录, 保证"未跑到 interrupt 就关闭"的会话也能在历史列表被看到并续跑
    _persist_session(session)
    threading.Thread(target=_run_session, args=(session, initial_state), daemon=True).start()
    return {"thread_id": thread_id, "session_id": session_id}


@app.get("/api/sessions/{thread_id}/events")
async def session_events(thread_id: str):
    session = SESSIONS.get(thread_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    async def gen():
        # 会话已结束且客户端 (重)连接 → 立即补发终止事件, 避免空转
        if session.status == "done" and session.final_state is not None:
            yield f"data: {json.dumps({'type': 'done', 'state': _final_summary(session.final_state)}, ensure_ascii=False)}\n\n"
            return
        if session.status in ("stopped", "error"):
            kind = "stopped" if session.status == "stopped" else "error"
            yield f"data: {json.dumps({'type': kind, 'message': session.error or ''}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"
        while True:
            try:
                event = await asyncio.to_thread(session.events.get, True, 0.5)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in ("done", "stopped", "error"):
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{thread_id}/respond")
def respond(thread_id: str, req: RespondRequest):
    session = SESSIONS.get(thread_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    session.messages.append({"role": "user", "text": req.response})
    session.responses.put(req.response)
    _persist_session(session)
    return {"ok": True}


@app.post("/api/sessions/{thread_id}/stop")
def stop_session(thread_id: str):
    session = SESSIONS.get(thread_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    session.stop_flag.set()
    session.responses.put(_STOP)  # 若正阻塞在 interrupt, 解除等待
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
    session = Session(
        thread_id,
        topic=record.get("topic", ""),
        session_id=session_id,
        checkpointer=checkpointer,
        checkpoint_conn=conn,
        run_id=record.get("run_id", ""),
    )
    session.request = dict(record.get("request") or {})
    session.created_at = record.get("created_at", session.created_at)
    # 恢复历史对话, 避免续跑落盘时用空 messages 覆盖历史记录
    session.messages = list(record.get("messages", []))
    SESSIONS[thread_id] = session

    # 判断是否有 checkpoint 可续跑 (无 checkpoint 说明尚未执行任何节点就关闭)
    has_checkpoint = False
    try:
        snap = session.app.get_state(session.config)
        has_checkpoint = bool(snap.values or snap.next)
    except Exception:
        has_checkpoint = False

    initial_state = None
    if not has_checkpoint:
        req = session.request
        if req.get("topic") or req.get("request"):
            try:
                initial_state = build_initial_state(StartRequest(**req))
            except Exception:
                initial_state = None
        if initial_state is None:
            raise HTTPException(400, "无 checkpoint 且缺少启动参数, 无法恢复")

    threading.Thread(target=_run_session, args=(session, initial_state), daemon=True).start()
    return {"thread_id": thread_id, "session_id": session_id, "status": session.status}


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
        sess.stop_flag.set()
        try:
            sess.responses.put(_STOP)
        except Exception:
            pass
    # 2. 删除对话记录
    if delete_conversation(session_id):
        removed.append(f"data/conversations/{session_id}.json")
    # 3. 删除检查点 (每会话一个 SQLite, 含 WAL/SHM)
    try:
        for suffix in ("", "-shm", "-wal"):
            ckpt = CHECKPOINT_DIR / f"{session_id}.sqlite{suffix}"
            if ckpt.exists():
                ckpt.unlink()
        removed.append(f"data/checkpoints/{session_id}.sqlite")
    except Exception:
        pass
    # 4. 删除产出文件夹 outputs/{run_id}/
    if run_id:
        out_dir = (OUTPUT_DIR / run_id).resolve()
        try:
            if out_dir.exists() and str(out_dir).startswith(str(OUTPUT_DIR.resolve())):
                shutil.rmtree(out_dir)
                removed.append(f"outputs/{run_id}")
        except Exception:
            pass
    # 5. 删除检索缓存 (data/pipeline_cache/{topic}.json)
    if topic:
        try:
            from src.utils.pipeline_cache import resolve_cache_topic, CACHE_DIR

            resolved = resolve_cache_topic(topic)
            if resolved:
                cache_path = CACHE_DIR / f"{sanitize_filename(resolved)}.json"
                if cache_path.exists():
                    cache_path.unlink()
                    removed.append(f"data/pipeline_cache/{cache_path.name}")
        except Exception:
            pass
    # 6. 清空向量库 (RAG 检索数据, 全局共享无法按主题精确删除, 单项目场景下清空)
    try:
        from src.rag.vector_store import clear_collection
        from src.config import CHROMA_CONFIG

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
    return {"ok": True, "removed": removed}


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
    for dir_name in ("pipeline_cache", "chroma"):
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


@app.get("/api/artifacts")
def list_artifacts():
    if not OUTPUT_DIR.exists():
        return {"files": []}
    files = []
    # 递归列出所有文件, name 用相对 outputs/ 的路径 (含 run_id 子目录), 便于前端按对话分组查看
    for f in sorted(OUTPUT_DIR.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.is_file():
            rel = f.relative_to(OUTPUT_DIR).as_posix()
            files.append({"name": rel, "size": f.stat().st_size, "mtime": f.stat().st_mtime})
    return {"files": files}


@app.get("/api/artifacts/download")
def download_artifact(name: str):
    root = OUTPUT_DIR.resolve()
    path = (OUTPUT_DIR / name).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(path))


@app.get("/api/artifacts/{name:path}")
def get_artifact(name: str):
    root = OUTPUT_DIR.resolve()
    path = (OUTPUT_DIR / name).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
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
