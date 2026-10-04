from __future__ import annotations

"""会话注册表与持久化边界 (合并计划 §8: `src/sessions/store.py`)。

职责只有三件, 都很小但都有真实约束:

1. **注册表** `SESSIONS`: thread_id → 活跃会话。端点、删除、批关闭都从这里取;
2. **落盘**: 会话记录写入 `data/conversations/<session_id>.json` (失败不中断会话);
3. **批关闭** `shutdown_sessions()`: 先给**所有**会话发停止信号, 再逐个等线程退出。

为什么"先全部发信号"是硬要求: 逐个"发信号→等待"会让线程串行收敛, 多会话时白等
多个超时。这一点由 `tests/test_session_lifecycle.py` 的时序用例固定。

本模块不 import FastAPI, 也不 import `server`。
"""

from src.sessions.controller import Session
from src.utils.conversation_store import save_conversation

__all__ = [
    "SESSIONS",
    "final_summary",
    "persist_session",
    "session_record",
    "shutdown_sessions",
]


#: 活跃会话注册表 (thread_id → Session)。**只有一份**, 端点与批关闭共用。
SESSIONS: dict[str, Session] = {}


def final_summary(final: dict) -> dict:
    """把图终态压成交付摘要 (落盘与会话状态接口都用它)。

    字段集合是前端契约 (工作台/历史列表据此显示产物与门槛结论), 增删要同步前端。
    """
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
        # 理论研究模式
        "package_dir": final.get("package_dir", ""),
        "manuscript_path": final.get("manuscript_path", ""),
        "tex_path": final.get("tex_path", ""),
        "gate_passed": final.get("gate_passed", None),
        "gate_report": final.get("gate_report", ""),
        "delivery_level": final.get("delivery_level", ""),
        "snapshot_id": final.get("snapshot_id", ""),
        "project_id": final.get("project_id", ""),
        "problem_id": final.get("problem_id", ""),
        "needs_clarification": final.get("needs_clarification", False),
        # 资源用量与停止原因 (计划书 §9.3)
        "usage": final.get("usage", {}),
        "stopped_reason": final.get("stopped_reason", ""),
        # 团队会话引擎 (合并计划 §15.3): 摘要与交付包由团队侧给出, 这里如实透传 ——
        # 界面才能按同一套字段显示"跑了几轮 / 派了哪些任务 / 包在哪里"。
        "engine": final.get("engine", ""),
        "summary": final.get("summary", {}),
    }


def session_record(session: Session) -> dict:
    """构造会话记录 (供落盘)。"""
    return {
        "session_id": session.session_id,
        "thread_id": session.thread_id,
        "topic": session.topic,
        "run_id": session.run_id,
        "created_at": session.created_at,
        "status": session.status,
        "summary": final_summary(session.final_state) if session.final_state else {},
        "messages": list(session.messages),
        "request": dict(session.request),
    }


def persist_session(session: Session) -> None:
    """把会话记录写入 data/conversations/ (失败不中断会话)。"""
    try:
        save_conversation(session.session_id, session_record(session))
    except Exception:  # noqa: BLE001 - 落盘失败不得影响研究本身
        pass


def shutdown_sessions() -> int:
    """关闭所有活跃会话的检查点连接并清空注册表 (进程退出 / 测试清理用)。

    返回仍然关闭失败的会话数。研究结论已落在 SQLite 研究库里, 关连接不会丢结论;
    未关闭的句柄只会让检查点文件暂时无法删除 —— 这一点必须如实返回, 不能假装成功。

    先给**所有**会话发停止信号 (`Session.request_stop()`, 同时解除 interrupt 阻塞),
    再逐个等线程退出: 逐个"发信号→等待"会让线程串行收敛, 多会话时白等多个超时。
    """
    sessions: list[Session] = [
        session for session in (SESSIONS.pop(t, None) for t in list(SESSIONS))
        if session is not None
    ]
    for session in sessions:
        session.request_stop()
    failed = 0
    for session in sessions:
        if not session.shutdown():
            failed += 1
    return failed
