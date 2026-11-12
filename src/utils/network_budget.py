"""One task deadline and failure budget shared by LLM, HTTP and embedding calls.

Only remote reads run in daemon workers. A late response is discarded: callers
perform parsing, accounting and database writes after this boundary returns.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
import time
from typing import Callable, Any


class NetworkUnavailable(ConnectionError):
    pass


class TaskDeadlineExceeded(TimeoutError):
    pass


@dataclass
class NetworkState:
    failures: int = 0
    unavailable_until: float = 0
    workers: BoundedSemaphore = field(default_factory=lambda: BoundedSemaphore(3))


@dataclass
class NetworkBudget:
    seconds: float = 600
    task_id: str = ""
    emit: Callable[[str, dict], None] | None = None
    cancel: Callable[[], None] | None = None
    state: NetworkState = field(default_factory=NetworkState)
    max_failures: int = 3
    heartbeat_seconds: float = 30
    started: float = field(default_factory=time.monotonic)
    failures: int = 0
    backoff_used: float = 0
    retry_attempts: int = 0
    deferred: bool = False

    def remaining(self) -> float:
        return max(0., self.seconds - (time.monotonic() - self.started))

    def event(self, kind: str, **data: Any) -> None:
        if self.emit:
            self.emit(kind, {"task_id": self.task_id, **data})

    def check(self) -> None:
        if self.cancel:
            self.cancel()
        if self.remaining() <= 0:
            self.deferred = True
            raise TaskDeadlineExceeded("任务墙钟预算耗尽，保留已有成果，待恢复后补做")
        if self.failures >= self.max_failures or self.state.unavailable_until > time.monotonic():
            self.deferred = True
            raise NetworkUnavailable("网络暂不可用，已暂停本任务远程步骤，保留本地资料")


_current: ContextVar[NetworkBudget | None] = ContextVar("network_budget", default=None)
_in_call: ContextVar[bool] = ContextVar("inside_remote_read", default=False)


def current_budget() -> NetworkBudget | None:
    return _current.get()


@contextmanager
def task_network_budget(budget: NetworkBudget):
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


def network_allowed() -> bool:
    budget = current_budget()
    if budget is None:
        return True
    try:
        budget.check()
        return True
    except (NetworkUnavailable, TaskDeadlineExceeded):
        return False


def phase_event(phase: str, **data: Any) -> None:
    if budget := current_budget():
        budget.event(phase, **data)


def network_error(error: BaseException) -> bool:
    return isinstance(error, (ConnectionError, TimeoutError, OSError)) or any(
        name in type(error).__name__.lower() for name in ("connection", "connect", "timeout", "readerror"))


def network_call(fn: Callable[[], Any], *, timeout: float = 120, phase: str = "network") -> Any:
    budget = current_budget()
    if budget is None or _in_call.get():
        return fn()
    budget.check()
    if not budget.state.workers.acquire(blocking=False):
        raise NetworkUnavailable("先前网络请求尚未退出，暂不增加后台调用")
    result: Queue = Queue(maxsize=1)
    context = copy_context()
    def work():
        token = _in_call.set(True)
        try:
            try:
                result.put((True, fn()))
            except BaseException as error:
                result.put((False, error))
        finally:
            _in_call.reset(token)
            budget.state.workers.release()
    Thread(target=lambda: context.run(work), daemon=True, name="air-remote-read").start()
    deadline = time.monotonic() + min(max(.001, timeout), budget.remaining())
    heartbeat = time.monotonic() + budget.heartbeat_seconds
    try:
        while True:
            budget.check()
            remaining = min(deadline - time.monotonic(), budget.remaining())
            if remaining <= 0:
                raise TaskDeadlineExceeded(f"{phase} 等待超过时间预算，迟到结果不再采用")
            try:
                ok, value = result.get(timeout=min(remaining, 1., budget.heartbeat_seconds))
                if not ok:
                    raise value
                if budget.remaining() <= 0 or time.monotonic() >= deadline:
                    raise TaskDeadlineExceeded("任务墙钟预算耗尽，迟到结果不再采用")
                if budget.state.unavailable_until:
                    budget.event("network_recovered", phase=phase)
                budget.state.failures = 0
                budget.state.unavailable_until = 0
                return value
            except Empty:
                if time.monotonic() >= heartbeat:
                    budget.event("task_heartbeat", phase=phase, status="waiting_network",
                                 elapsed_seconds=round(time.monotonic() - budget.started, 1),
                                 remaining_seconds=round(budget.remaining(), 1))
                    heartbeat = time.monotonic() + budget.heartbeat_seconds
    except Exception as error:
        if network_error(error):
            budget.failures += 1
            budget.state.failures += 1
            budget.event("network_error", phase=phase, error_type=type(error).__name__,
                         failures=budget.failures)
            if budget.state.failures >= budget.max_failures or isinstance(error, TaskDeadlineExceeded):
                budget.deferred = True
                budget.state.unavailable_until = time.monotonic() + 60
                budget.event("network_unavailable", phase=phase, retry_after_seconds=60,
                             action="local_sources_only")
        raise


def retry_wait(seconds: float) -> None:
    budget = current_budget()
    if budget is None:
        time.sleep(seconds)
        return
    budget.check()
    if budget.retry_attempts >= 3:
        raise NetworkUnavailable("本任务共享重试次数已耗尽")
    wait = min(max(0., seconds), 30 - budget.backoff_used, budget.remaining())
    if wait <= 0:
        raise NetworkUnavailable("本任务重试等待预算已耗尽")
    budget.backoff_used += wait
    budget.retry_attempts += 1
    budget.event("network_retry", wait_seconds=wait)
    time.sleep(wait)
    budget.check()
