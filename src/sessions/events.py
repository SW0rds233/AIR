from __future__ import annotations

"""会话事件日志与游标 (合并计划 M4: "抽出会话/事件模块, 独立 SSE 游标")。

为什么单独成模块
---------------
`server.py` 里的 `Session` 此前既管研究运行, 又内联了事件序号、可重放日志、
"事件 → 会话消息"的映射和 500 条上限。这几件事其实是一段**纯逻辑**, 不依赖
FastAPI、不依赖图, 因此可以单独测:

- **序号与日志**: 每个事件带一个递增 `_seq`, 同时进实时队列与有界日志;
- **按游标重放**: 断线/多标签页重连时按 `last_event_id` 回放缺失事件 —— 只回放
  日志里还在的部分, 并如实报告是否发生了缺口 (旧记录已被裁掉), 不假装补齐;
- **落盘消息映射**: 事件转换成会话历史里的一行 (`role=node/interrupt/done/...`),
  并对"断点续跑时重复抛出的同一 interrupt"去重。

不变量
------
1. 序号是全序且连续的: 客户端按 `_seq` 去重与判断缺口才有意义;
2. 心跳/`connected` **不占业务事件序号** (它们只在 SSE 帧里, 不进日志) ——
   这一条由调用方保证: 它直接构造帧, 不经过 `append()`;
3. 日志有上限, 但**不得静默截断**: `replay()` 会返回 `truncated=True`。
"""

import threading
from collections.abc import Iterable
from typing import Any

__all__ = [
    "DEFAULT_LOG_LIMIT",
    "EventLog",
    "accepts_message",
    "event_to_message",
    "iter_types",
    "replay_gap_note",
]

#: 重放日志的长度上限: 用于补偿短时断线, 不是完整归档。
DEFAULT_LOG_LIMIT = 500


def event_to_message(event: dict[str, Any]) -> dict[str, Any] | None:
    """把 SSE 事件映射为会话消息记录 (供历史回看与落盘)。

    返回 `None` 表示该事件不进会话历史 (例如工具日志之外的内部事件)。
    """
    kind = event.get("type")
    if kind in ("node", "log"):
        return {"role": "node", "text": event.get("text", "")}
    if kind == "interrupt":
        payload = event.get("payload") or {}
        return {
            "role": "interrupt",
            "title": payload.get("title", ""),
            "hint": payload.get("hint", ""),
            "content": payload.get("content", ""),
        }
    if kind == "done":
        return {"role": "done", "text": "完成"}
    if kind == "stopped":
        return {"role": "stopped", "text": "已停止"}
    if kind == "error":
        return {"role": "error", "text": event.get("message", "")}
    return None


class EventLog:
    """一个会话的事件序号、实时队列与有界重放日志。

    线程安全: 研究线程写, SSE 协程读, 因此内部用一把可重入锁保护序号与日志。
    """

    def __init__(self, limit: int = DEFAULT_LOG_LIMIT, sink: Any = None):
        self.limit = max(1, int(limit))
        self.seq = 0
        self.records: list[dict[str, Any]] = []
        self.lock = threading.RLock()
        #: 实时出口 (通常是 `queue.Queue`); 为空表示只做日志。
        self.sink = sink

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """登记一个事件: 分配序号 → 进日志 (有界) → 进实时出口。"""
        with self.lock:
            self.seq += 1
            record = {**event, "_seq": self.seq}
            self.records.append(record)
            # 只保留最近 `limit` 条: 重放日志用于补偿短时断线, 不是完整归档
            if len(self.records) > self.limit:
                del self.records[:-self.limit]
        if self.sink is not None:
            self.sink.put(record)
        return record

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def snapshot_seq(self) -> int:
        """当前序号 (读快照; 与日志读取需在同一临界区内配合使用)。"""
        with self.lock:
            return self.seq

    def replay(self, last_event_id: int = 0) -> tuple[list[dict[str, Any]], bool]:
        """返回 `(缺失事件, 是否发生缺口)`。

        缺口判据: 请求的游标早于日志里**最早**保留的序号 —— 那部分已经被裁掉,
        如实返回 `True`, 让调用方决定重新加载投影而不是假装补齐。
        """
        try:
            cursor = int(last_event_id or 0)
        except (TypeError, ValueError):
            cursor = 0
        with self.lock:
            records = [e for e in self.records if int(e.get("_seq", 0) or 0) > cursor]
            truncated = bool(self.records) and cursor < int(
                self.records[0].get("_seq", 0) or 0) - 1
            return records, truncated

    def tail(self, count: int) -> list[dict[str, Any]]:
        """最近 `count` 条 (调试与用例用)。"""
        with self.lock:
            return list(self.records[-max(0, int(count)):])


def accepts_message(messages: list[dict[str, Any]],
                    message: dict[str, Any] | None) -> bool:
    """是否需要把 `message` 追加进会话历史 (False 表示重复, 丢弃)。

    断点续跑时 checkpoint 会重新抛出与历史最后一条相同的 interrupt, 去重避免
    历史里出现重复的中断卡片 (SSE 事件照常推送, 只影响落盘)。
    """
    if message is None:
        return False
    if (message.get("role") == "interrupt" and messages
            and messages[-1].get("role") == "interrupt"
            and messages[-1].get("title") == message.get("title")):
        return False
    return True


def replay_gap_note(truncated: bool) -> str:
    """缺口的人可读说明 (界面据此重新加载投影, 而不是继续按游标追)。"""
    return ("事件日志已截断, 缺失部分无法回放 —— 请重新加载研究投影"
            if truncated else "")


def iter_types(events: Iterable[dict[str, Any]]) -> list[str]:
    """事件类型序列 (用例与调试辅助)。"""
    return [str(e.get("type", "")) for e in events]
