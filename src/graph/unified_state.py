from __future__ import annotations

"""统一研究状态: 只保存身份、任务与引用 (合并计划 §6.1 / §8)。

为什么状态必须"小"
------------------
合并计划 §6.1: "LangGraph 状态保存身份、计划版本、任务 ID/状态、对象版本与产物指针;
全文、论文正文、代码和大批检索结果**不平铺进入图状态**。"

这不是风格问题: 初版方案的"信封+载荷"如果让载荷复制一份可写的 claims/evidence,
而 SQLite 再保存另一份, 就会出现两个真相源 —— 同一条结论在两边版本不一致时,
判定层读哪一个都不对。因此这里只放**引用**。

与旧状态的关系 (§7.4 / R5 清仓)
-------------------------------
旧综述流水线的状态 (`graph/state.py::PipelineState`) **已随该流水线删除** —— 它最后的使用者
是引用守门/预检的旧 Agent, 那些能力已迁到 `publication/citation_checks` 与
`publication/evidence_ledger`。旧理论图的状态 (`graph/theory_state.py::TheoryState`) 仍在,
只为尚未退役的形式化流水线服务, 与本模块无关。旧 checkpoint 保持可读, 不强制原地转换。
"""

from typing import Any, TypedDict

__all__ = [
    "TASK_STATUS_KEYS",
    "UnifiedState",
    "initial_unified_state",
    "slim_state",
    "state_identity",
]


class UnifiedState(TypedDict, total=False):
    """统一研究状态的键 (合并计划 §6.2 的身份链)。

    身份关系: `project_id -> problem_id -> run_id -> branch_id/task_id/agent_run_id`。
    `session_id/thread_id` 管理用户交互与 checkpoint, 必须映射到明确 run。
    """

    # ---- 身份 ----
    project_id: str
    problem_id: str
    run_id: str
    branch_id: str
    session_id: str
    thread_id: str

    # ---- 入口输入 (只保存引用与摘要, 不复制正文) ----
    request: str
    attachment_ids: list[str]
    attachment_refs: list[dict]
    source_set_ids: list[str]
    source_policy: str
    autonomous_retrieval: bool
    budget: dict

    # ---- 计划与任务 (引用与状态, 不放大载荷) ----
    engine_version: str          # team_v1 / legacy
    brief_ref: dict
    plan_id: str
    plan_version: int
    task_ids: list[str]
    task_status: dict[str, str]
    running_task_ids: list[str]
    open_need_ids: list[str]

    # ---- 研究对象与产物指针 ----
    object_versions: dict[str, int]
    artifact_refs: list[dict]
    manuscript_ref: dict
    snapshot_id: str

    # ---- 交付与事件 ----
    delivery_level: str
    gate_passed: bool
    gate_report: str
    unresolved: list[str]
    event_seq: int
    needs_clarification: bool
    clarification: str
    current_phase: str
    error: str | None


#: 任务状态在状态里的存放方式: 只存 `task_id -> status`, 不存任务全文。
TASK_STATUS_KEYS: tuple[str, ...] = (
    "queued", "running", "waiting", "completed", "partial", "failed", "cancelled",
)


def initial_unified_state(*, project_id: str, problem_id: str, run_id: str,
                          request: str, session_id: str = "", thread_id: str = "",
                          source_set_ids: list[str] | None = None,
                          source_policy: str = "user_kb",
                          autonomous_retrieval: bool = False,
                          budget: dict[str, Any] | None = None,
                          engine_version: str = "team_v1") -> UnifiedState:
    """构造统一状态的初始值 (入口写入, 后续节点只增量更新)。"""
    return UnifiedState(
        project_id=project_id,
        problem_id=problem_id,
        run_id=run_id,
        budget=dict(budget or {}),
        session_id=session_id or "",
        thread_id=thread_id or "",
        request=request,
        attachment_ids=[],
        attachment_refs=[],
        source_set_ids=list(source_set_ids or []),
        source_policy=source_policy,
        autonomous_retrieval=bool(autonomous_retrieval),
        engine_version=engine_version,
        brief_ref={},
        plan_id="",
        plan_version=0,
        task_ids=[],
        task_status={},
        running_task_ids=[],
        open_need_ids=[],
        object_versions={},
        artifact_refs=[],
        manuscript_ref={},
        snapshot_id="",
        delivery_level="",
        gate_passed=False,
        gate_report="",
        unresolved=[],
        event_seq=0,
        needs_clarification=False,
        clarification="",
        current_phase="intake",
        error=None,
    )


def state_identity(state: dict[str, Any]) -> dict[str, str]:
    """取出身份链 (供事件归属与存储定位)。"""
    return {
        "project_id": str(state.get("project_id", "") or ""),
        "problem_id": str(state.get("problem_id", "") or ""),
        "run_id": str(state.get("run_id", "") or ""),
        "branch_id": str(state.get("branch_id", "") or ""),
        "session_id": str(state.get("session_id", "") or ""),
        "thread_id": str(state.get("thread_id", "") or ""),
    }


#: 绝对不允许进入图状态的键: 正文/大块载荷。违者被 `slim_state` 拦下并如实报出。
_FORBIDDEN_STATE_KEYS = (
    "manuscript_text", "full_text", "paper_text", "draft", "draft_text",
    "notes_text", "retrieved_papers", "literature_review_notes", "all_chunks",
    "chat_history", "messages",
)


def slim_state(state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """去掉不该进入图状态的载荷, 返回 `(精简后的状态, 被去掉的键)`。

    **如实报告被去掉的键**: 调用方据此知道有内容没有进状态, 而不是静默丢弃
    (与项目里"未声明的键静默丢失"两次踩坑的教训一致)。
    """
    removed: list[str] = []
    slim = dict(state)
    for key in _FORBIDDEN_STATE_KEYS:
        if slim.get(key):
            removed.append(key)
            slim.pop(key, None)
    for key, value in list(slim.items()):
        if isinstance(value, str) and len(value) > 20000:
            removed.append(key)
            slim[key] = value[:20000] + "\n...(状态内文本已截断, 正文请在产物中读取)"
    return slim, removed
