from __future__ import annotations

"""按运行、项目和资料授权范围维护运行身份作用域。"""

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
