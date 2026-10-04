from __future__ import annotations

"""运行身份作用域 (合并计划 §7.4 / M4: "按 run/project/source grant 绑定……禁止依靠全局
'最近一次'续研")。

问题
----
会话记忆、检索缓存、图表目录、用量这些资源此前都是**进程级单例**: 谁最后写谁生效。
于是一个会话在跑的时候, 另一个会话"最近一次研究"的主题、图表目录或费用会被串起来。
研究线程本来就只有一个明确的运行身份, 因此把它绑定到**当前上下文**是最直接的归属
来源 —— 这也是 `server._run_session` 在其工作线程里能做、且不需要改所有调用点的事。

与 `cost_tracker` 的做法一致: 用 `ContextVar` 而不是全局变量, 新建线程会继承父线程
的上下文, 因此会话内部再派生的 worker 线程仍然归属同一运行。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

__all__ = ["RunScope", "current_run_scope", "run_scope_bound"]


@dataclass(frozen=True)
class RunScope:
    """一次运行的归属身份 (缺失一律为空字符串, 不编造)。"""

    session_id: str = ""
    run_id: str = ""
    project_id: str = ""
    topic: str = ""

    @property
    def bound(self) -> bool:
        return bool(self.session_id or self.run_id or self.project_id or self.topic)

    def key(self) -> str:
        """稳定标识: 优先会话, 其次运行 (用于按归属存取的资源)。"""
        return self.session_id or self.run_id or self.project_id or ""


_ACTIVE_SCOPE: ContextVar[RunScope | None] = ContextVar("air_run_scope", default=None)


def current_run_scope() -> RunScope:
    """当前上下文绑定的运行身份 (未绑定时返回空身份, 不抛错)。"""
    return _ACTIVE_SCOPE.get() or RunScope()


@contextmanager
def run_scope_bound(session_id: str = "", run_id: str = "", project_id: str = "",
                    topic: str = ""):
    """在 `with` 块内把运行身份绑定给当前上下文。"""
    scope = RunScope(session_id=str(session_id or ""), run_id=str(run_id or ""),
                     project_id=str(project_id or ""), topic=str(topic or ""))
    token = _ACTIVE_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _ACTIVE_SCOPE.reset(token)
