from __future__ import annotations

"""理论研究模式状态 (P0)。

与综述模式 ``PipelineState`` 隔离; 只保存对象 ID、版本、任务队列与产物指针,
详细证明与文献全文放在独立存储 (SQLite) 中。旧检查点不强制原地转换。
"""

from typing import TypedDict


class TheoryState(TypedDict, total=False):
    project_id: str
    problem_id: str
    topic: str
    request: str
    mode: str
    # R6: 统一身份 —— run_id 是本次运行的权威标识 (动作账本/产物目录/manifest/快照
    # 必须是同一个值); branch_id 是问题内的研究路线分支。
    run_id: str
    branch_id: str

    budget_max_actions: int
    budget_max_tool_calls: int

    step_index: int
    action: dict
    done: bool
    bootstrapped: bool
    needs_clarification: bool
    needs_confirmation: bool
    candidates: list[dict]
    selected_candidate: int
    # 稳定候选 ID (计划书 F1-4): 确认路线按 ID 而非数组序号
    selected_candidate_id: str
    interactive: bool
    notes: list[str]

    # 用户反馈 (计划书 §5.1): 自然语言意见 → 对象级动作
    human_feedback: str
    feedback_result: dict

    # 研究过程事件摘要 (供 Web 科研工作台显示"当前在做哪一步/为什么")
    decision_log: list[dict]

    snapshot_id: str
    gate_passed: bool
    gate_report: str
    delivery_level: str      # 研究备忘录 / 条件性研究报告 / 论文草稿
    package_dir: str
    manuscript_path: str
    tex_path: str

    # R6: 不可变启动输入快照 (附件/资料集/策略/预算)。必须在这里声明 ——
    # 未声明的键不会进入图状态, 于是收尾节点看不到启动输入, manifest 只能写空快照。
    input_snapshot: dict
    # R2/R4: 问题说明附件 id 与**候选要求文本**。同样必须声明: 未声明时
    # `_engine_from_state` 读不到附件, 附件正文就进不了形式化 —— 现场两次踩同一个坑
    # (第一次是 input_snapshot, 第二次是 attachment_ids)。契约测试见
    # `tests/test_theory_state_contract.py`。
    attachment_ids: list[str]
    attachment_candidates: str
    attachment_rejected: list[str]
    # 资料授权与绑定: 入口写入, 引擎/交付包读取 (未声明同样会被图丢掉)
    source_policy: str
    source_set_id: str
    # 资料源类型 (kb/files/dataset): `theory_pipeline` 用 `state.get("source_set_kind")`
    # 决定交付清单里的资料集描述。未声明时图状态里不会保留它, 于是清单只能写默认 `kb`
    # —— 与 input_snapshot/attachment_ids 同一类"未声明即静默丢失"的坑,
    # 由 `tests/test_theory_state_contract.py` 的契约用例把守。
    source_set_kind: str
    source_set_warnings: list[str]
    # 已落盘的问题契约 (续跑时复用, 不重新判定研究类型)
    contract: dict
    spec_reused: bool
    # 写作缺口回流: 收尾时若回流了阻塞义务, 图必须回到研究循环消解它。
    # 这两个键**必须声明**, 否则路由读不到它们, 回边永远不会触发 ——
    # 与 `input_snapshot` / `attachment_ids` 同一类"未声明即静默丢失"的坑。
    feedback_reopened: bool
    finalize_rounds: int

    current_phase: str
    error: str | None


