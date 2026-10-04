from __future__ import annotations

"""会话控制器: `Session` 的身份、状态机、事件出口与关闭时序 (合并计划 §8 / M4)。

为什么单独成模块
----------------
`server.py` 里的 `Session` 同时承担"会话状态"与"研究线程管理"。会话状态本身是**纯
状态 + 线程原语**, 不依赖 FastAPI, 因此可以独立测试; 而端点只剩装配与注入。

这个模块**不 import FastAPI**: 会话状态本身是纯状态 + 线程原语。图应用 (`app`)
由调用方注入 (`server._make_session` 决定"模式 → 哪张图"); 只有当调用方完全没有
注入时, 才回落到缺省建图 (`_build_app()`, 与拆分前一致) —— 那条回落路径按名称取
入口层的工厂, 因此不在这里固化第二份"模式 → 图"的决策。

不可让步的边界 (真实事故记忆, 改动前请先读)
------------------------------------------
1. **关闭时序**: 后台研究线程仍在使用 `checkpoint_conn` (sqlite, `check_same_thread=False`)
   时 close, CPython 的 sqlite3 会访问已释放句柄 —— 实测触发 `0xC0000005`
   (STATUS_ACCESS_VIOLATION) 崩掉整个进程, 崩点在 C 层, `except Exception` 拦不住。
   因此 `shutdown()` 先 join, 线程仍存活就**不关**并如实返回 False。
2. **日志归属按线程**: 每个会话的研究线程持有自己的 `_StdoutBridge`
   (`src/sessions/runtime.py`), 不替换全局 `sys.stdout`。`shutdown()` 因此必须能
   在任意调用线程里安全调用, 并且**不能让本线程的绑定泄漏到别的会话**。
3. **事件序号唯一**: `event_seq`/`event_log`/`event_lock` 只是 `EventLog` 的只读视图,
   不得再出现第二份序号。
"""

import queue
import threading
import uuid
from datetime import datetime
from typing import Any

from src.sessions.events import EventLog, accepts_message, event_to_message

__all__ = ["EVENT_LOG_LIMIT", "Session", "SessionStatus"]

#: 事件重放日志的长度上限 (与 `events.DEFAULT_LOG_LIMIT` 同源, 不各写一份)。
EVENT_LOG_LIMIT = 500

#: 会话状态机取值 (running / waiting / done / stopped / error)。
SessionStatus = str


#: `Session(app=...)` 未显式给出时表示"按缺省规则构建图"。
#: 与"显式传 None"区分开: 显式 None 表示这个会话不跑图 (离线/测试替身)。
_UNSET = object()


def _langfuse_handler():
    """可选的链路追踪回调 (取不到就返回 None, 不影响会话)。"""
    try:
        from src.graph.node_progress import get_langfuse_handler

        return get_langfuse_handler()
    except Exception:  # noqa: BLE001 - 追踪不可用不得影响会话构建
        return None


class Session:
    """一个研究会话: 身份 + 状态机 + 事件出口 + 关闭时序。

    构造签名是**公共契约**: `server.Session` 的调用方 (端点、测试) 依赖它, 属性名
    同样不得改名。

    `app` 缺省 (`_UNSET`) 时按 `mode` 构建图, 与拆分前逐字一致 —— 直接
    `Session("t")` 必须仍然可用。入口层会显式注入 `app`
    (`server._make_session`), 于是"哪张图服务这个会话"的决策留在入口, 而不是
    分散进会话状态机; 显式传 `app=None` 表示这个会话不跑图。
    """

    def __init__(self, thread_id: str, topic: str = "", session_id: str | None = None,
                 checkpointer=None, checkpoint_conn=None, run_id: str = "",
                 mode: str = "survey", app: Any = _UNSET):
        from langgraph.checkpoint.memory import MemorySaver

        self.thread_id = thread_id
        self.session_id = session_id or uuid.uuid4().hex
        self.topic = topic
        self.run_id = run_id  # outputs/{run_id}/ 产物子目录
        self.mode = mode or "survey"
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.checkpointer = checkpointer or MemorySaver()
        self.checkpoint_conn = checkpoint_conn
        self.app = self._build_app() if app is _UNSET else app
        self.config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        lf = _langfuse_handler()
        if lf:
            self.config["callbacks"] = [lf]
        self.events: queue.Queue = queue.Queue()
        self.responses: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.status: SessionStatus = "running"
        self.final_state: dict | None = None
        self.error: str | None = None
        self.messages: list[dict] = []
        self.request: dict = {}
        # F2: 当前等待中的 interrupt 稳定 ID 与已回答记录 (幂等 + 只接受等待态响应)
        self.pending_interrupt_id: str = ""
        self.answered_interrupts: list[str] = []
        # F2: 事件日志 (可重放) —— SSE 是单消费者队列, 断线或多标签页会丢事件;
        # 这里保留带序号的日志, 重连时按 last_event_id 回放缺失部分。
        # 下面这几个属性是**视图**, 读写都落到同一个 EventLog, 避免"两份序号各自走"。
        self.event_stream = EventLog(sink=self.events, limit=EVENT_LOG_LIMIT)
        # 跑这一会话的后台线程 (start 时登记): 关闭检查点连接前必须等它退出,
        # 否则会在"线程还在用 sqlite 连接"时 close, Windows 上直接崩进程。
        self.worker_thread: threading.Thread | None = None

    def _build_app(self):
        """缺省建图: 按 `mode` 取对应引擎 (与拆分前逐字一致)。

        按**名称**在调用时取工厂, 因此 `monkeypatch.setattr(server, "build_pipeline",
        stub)` 对直接构造的会话仍然生效 (既有测试靠这个手法注入假图)。

        注意入口层 (`server._make_session`) 一律显式注入 `app`; 这条回落只为
        "直接 `Session(...)` 仍可用"这条公共契约存在。
        """
        from src import server as _server

        return _server._build_app_for_mode(self.checkpointer, self.mode, session=self)

    # ------------------------------------------------------------------
    # 事件出口
    # ------------------------------------------------------------------
    @property
    def event_seq(self) -> int:
        """当前事件序号 (只读视图; 序号由 EventLog 分配)。"""
        return self.event_stream.seq

    @property
    def event_log(self) -> list[dict]:
        """可重放事件日志 (只读视图)。"""
        return self.event_stream.records

    @property
    def event_lock(self):
        """与事件日志同一把锁 (调用方需要"读日志 + 读序号"原子时使用)。"""
        return self.event_stream.lock

    def emit(self, event: dict):
        """登记一个事件: 序号 → 实时队列 → 可重放日志 → (按需) 会话历史。"""
        record = self.event_stream.append(event)
        message = event_to_message(event)
        if message is None:
            return record        # 已进日志/队列, 但不占会话历史 (例如 connected 心跳)
        # 断点续跑时 checkpoint 会重新抛出与历史最后一条相同的 interrupt,
        # 去重避免历史记录里出现重复的中断卡片 (SSE 事件照常推送, 只影响落盘)。
        if accepts_message(self.messages, message):
            self.messages.append(message)
        return record

    # ------------------------------------------------------------------
    # 停止请求
    # ------------------------------------------------------------------
    def request_stop(self) -> None:
        """请求停止: 置停止标志, 并解除可能正在阻塞的 interrupt 等待。

        为什么是方法而不是让调用方直接 `responses.put(...)`: 解除阻塞需要一个
        **同一个**哨兵对象, 该哨兵属于本模块。调用方 (端点/注册表) 只需要表达
        "请停止", 不该接触私有哨兵, 也就不可能塞错对象。
        """
        from src.sessions.runtime import STOP

        self.stop_flag.set()
        try:
            self.responses.put(STOP)
        except Exception:  # noqa: BLE001 - 队列已满/已关闭都不影响停止标志
            pass

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def shutdown(self, timeout: float = 10.0) -> bool:
        """关闭会话持有的检查点连接 (幂等)。

        为什么必须有这一步: 每个会话一个 SQLite 检查点, 连接不关闭时在 Windows 上
        会**一直持有文件句柄** —— 删除会话时 `unlink` 静默失败 (留下无法删除的文件),
        测试的临时目录清理也会报 `WinError 32`。这里显式关闭, 并让调用方知道结果。

        为什么要先等后台线程: `checkpoint_conn` 是 `check_same_thread=False` 的 sqlite3
        连接, 后台研究线程仍在 `app.stream`/落盘中用它。在另一个线程还在使用时 close,
        CPython 的 sqlite3 会访问已释放的句柄 —— 实测直接触发
        `0xC0000005` (STATUS_ACCESS_VIOLATION) 崩掉整个进程, 而且崩点在 C 层,
        Python 的 `except Exception` 拦不住。因此这里先 join, 只在线程确实退出后才关;
        线程还在跑就**不关**并如实返回 False (宁可有句柄残留, 也不崩进程)。
        """
        thread = self.worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        if thread is not None and thread.is_alive():
            self.error = self.error or "研究线程仍在运行, 未关闭检查点连接"
            return False
        conn, self.checkpoint_conn = self.checkpoint_conn, None
        if conn is None:
            return True
        try:
            conn.close()
            return True
        except Exception as e:  # noqa: BLE001 - 关闭失败不应掩盖删除结果
            self.error = self.error or f"检查点连接关闭失败: {e}"
            return False
