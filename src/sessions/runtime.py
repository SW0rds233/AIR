from __future__ import annotations

"""会话运行线程的归属资源 (合并计划 §7.4 / M4: "按 run/project/source grant 绑定……
移除多线程替换全局 stdout 的日志归属方式")。

三件进程级资源在这里按**线程**归属, 而不是"谁最后设置谁生效":

1. **日志**: `_StdoutBridge` 把研究线程的 `print` 桥接成该会话的 SSE `log` 事件。
   全局只挂一个 `_StdoutDispatcher`, 由它按 `threading.get_ident()` 查表派发。
   早期实现直接 `sys.stdout = _StdoutBridge(...)`, 于是后启动的会话会把先启动的桥
   当成"原始 stdout"包起来 (A 的日志转发给 B), 而且先结束的会话"恢复"时会把这个桥
   重新装回全局 —— 后结束的会话日志静默消失。
2. **用量**: 会话级 `UsageTracker` 绑定 (每个 run 的成本报告不再包含历史累计)。
3. **运行身份**: 会话记忆/检索缓存/图表目录按 run 归属 (`run_scope`)。

本模块不 import FastAPI, 也不 import `server`。
"""

import sys
import threading
from typing import Any

__all__ = [
    "STOP",
    "StdoutBridge",
    "bind_stdout_bridge",
    "unbind_stdout_bridge",
]

#: 停止哨兵: 会话的 `responses` 队列收到它就表示"停止, 不要当作答复"。
#: 必须是**同一个对象**才能用 `is` 判定, 因此全进程只有这一份 (由
#: `Session.request_stop()` 投递, 研究线程用 `is STOP` 比较)。
STOP = object()


class StdoutBridge:
    """把某个会话研究线程的 print 输出桥接为 SSE `log` 事件, 同时保留控制台输出。

    子智能体 (文献检索/PDF下载/引用核查等) 的进度 print 原本只进服务端控制台, 通过
    本桥接器逐行转发给前端, 让用户能实时看到子智能体调用过程。

    **归属**: 这个对象属于**一个** session, 不直接挂到 `sys.stdout` 上 —— 实际转发由
    `_StdoutDispatcher` 按线程查表决定 (见模块 docstring)。
    """

    def __init__(self, session: Any, orig):
        self.session = session
        self.orig = orig
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, s: str):
        try:
            self.orig.write(s)
            self.orig.flush()
        except Exception:  # noqa: BLE001 - 控制台写失败不影响事件转发
            pass
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip()
                if line:
                    self.session.emit({"type": "log", "text": line})

    def flush(self):
        """把缓冲区里的**无换行**残行也发出去。

        线程解绑时调用: `print("x")` 正常带换行, 但最后一次写如果没有换行符 (或用
        `sys.stdout.write` 直接写), 残行会留在缓冲里 —— 会话结束时必须如实发出, 而
        不是静默丢掉本次运行的最后一句话。
        """
        try:
            self.orig.flush()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            rest, self._buf = self._buf.strip(), ""
        if rest:
            self.session.emit({"type": "log", "text": rest})

    def __getattr__(self, name):
        return getattr(self.orig, name)


#: 真实 stdout (进程启动时的那个), 任何桥接器都不得把它替换掉。
_TRUE_STDOUT: Any = None
#: 线程 -> 该线程正在运行的会话的桥接器。同一线程只会有一个会话。
_STDOUT_BRIDGES: dict[int, StdoutBridge] = {}
_STDOUT_LOCK = threading.Lock()
#: 线程 -> 该线程已进入的"运行身份作用域" (会话记忆/缓存/图表的归属依据)。
_RUN_SCOPE_STACKS: dict[int, Any] = {}
_RUN_SCOPE_LOCK = threading.Lock()


class _StdoutDispatcher:
    """按**线程**把 print 输出派发给对应会话的桥接器。

    这样每个会话的日志归属由"哪个线程在跑它"决定, 而不是由"谁最后设置了
    `sys.stdout`"决定 —— 后者在多会话并发时会把日志串到别的会话身上。
    """

    def __init__(self, orig):
        self.orig = orig

    def write(self, s: str):
        bridge = _STDOUT_BRIDGES.get(threading.get_ident())
        if bridge is not None:
            bridge.write(s)          # 桥接器自己会写原始 stdout 并发事件
            return
        try:
            self.orig.write(s)
        except Exception:  # noqa: BLE001
            pass

    def flush(self):
        try:
            self.orig.flush()
        except Exception:  # noqa: BLE001
            pass

    def __getattr__(self, name):
        return getattr(self.orig, name)


def _install_stdout_dispatcher() -> Any:
    """安装一次全局派发器, 返回**真实** stdout (用作桥接器的 `orig`)。

    这里必须认得出"当前 stdout 是上一个会话留下的桥接器"这种情况: 早期实现直接
    `sys.stdout = StdoutBridge(...)`, 于是后启动的会话会把先启动的桥接器当成原始
    stdout 包进去 —— 事件被转发到错误的会话, 而且先结束的会话"恢复"时会把这个桥
    重新装回全局。派发器始终挂在全局, 桥接器只按线程查表。
    """
    global _TRUE_STDOUT
    with _STDOUT_LOCK:
        current = sys.stdout
        if isinstance(current, _StdoutDispatcher):
            _TRUE_STDOUT = current.orig
            return _TRUE_STDOUT
        if isinstance(current, StdoutBridge):
            # 历史遗留状态 (例如旧代码路径装上的桥): 剥掉它, 取它背后的真实 stdout
            _TRUE_STDOUT = current.orig
        else:
            _TRUE_STDOUT = current
        sys.stdout = _StdoutDispatcher(_TRUE_STDOUT)
        return _TRUE_STDOUT


def bind_stdout_bridge(session: Any) -> StdoutBridge:
    """把当前线程绑定到该会话的桥接器 (不替换全局 stdout)。

    同时绑定**会话级用量累计器**与**运行身份作用域**: 日志归属、用量归属与会话记忆/
    缓存的归属在这里一起确定, 都按"哪个线程在跑这个会话"判定, 不再共用进程级资源
。
    """
    true_stdout = _install_stdout_dispatcher()
    bridge = StdoutBridge(session, true_stdout)
    with _STDOUT_LOCK:
        _STDOUT_BRIDGES[threading.get_ident()] = bridge
    from src.utils.cost_tracker import bind_session_tracker

    bind_session_tracker()
    from src.utils.run_scope import run_scope_bound

    request = getattr(session, "request", None) or {}
    scope = run_scope_bound(
        session_id=getattr(session, "session_id", ""),
        run_id=getattr(session, "run_id", ""),
        project_id=str(request.get("project_id", "") or ""),
        topic=getattr(session, "topic", ""),
    )
    scope.__enter__()
    with _RUN_SCOPE_LOCK:
        _RUN_SCOPE_STACKS[threading.get_ident()] = scope
    return bridge


def unbind_stdout_bridge() -> None:
    """解绑当前线程的桥接器、用量累计器与运行身份作用域。"""
    with _STDOUT_LOCK:
        bridge = _STDOUT_BRIDGES.pop(threading.get_ident(), None)
    if bridge is not None:
        # 残行必须发出: 否则本次运行的最后一句话会静默丢掉
        bridge.flush()
    with _RUN_SCOPE_LOCK:
        scope = _RUN_SCOPE_STACKS.pop(threading.get_ident(), None)
    if scope is not None:
        scope.__exit__(None, None, None)
    from src.utils.cost_tracker import unbind_session_tracker

    unbind_session_tracker()
