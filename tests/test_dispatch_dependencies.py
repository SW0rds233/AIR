from __future__ import annotations

"""派工依赖与顺序的回归 (§3.1 G05)。

审计复现: 首轮任务顺序是 `reasoning, reasoning, evidence`, 且推理任务的依赖为空 ——
主控先让模型推导, 再去查文献。对需要已有定理/数据/案例的问题, 这等于**凭记忆推导**。

判据 (计划 §3.1 G05):
- 需要依据的推理任务必须**依赖证据任务** (检索/阅读先发生);
- **纯自足证明**可以无文献启动 (计划明确允许的例外), 此时不强行加证据依赖;
- 写作/审阅的既有依赖不因此回退。
"""

import pytest

from src.agents.supervisor import (
    SELF_CONTAINED_SUBQUESTION_KINDS,
    SupervisorAgent,
    _all_reasoning_self_contained,
)


def _plan_for(request: str):
    supervisor = SupervisorAgent(llm=None)
    brief = supervisor.brief(request, project_id="p")
    return brief, supervisor.plan(brief)


def _roles_depended_on(plan, task: dict) -> list[str]:
    by_id = {record["task_id"]: record for record in plan.tasks}
    return sorted({str(by_id[dep]["agent"]) for dep in task.get("depends_on", [])
                   if dep in by_id})


# --------------------------------------------------------------------------
# 需要依据的问题: 推理必须等证据
# --------------------------------------------------------------------------
def test_reasoning_depends_on_evidence_when_the_problem_needs_sources():
    """含文献综合子问题的证明题: 推理任务必须依赖证据任务。"""
    _brief, plan = _plan_for("证明所有正数 x 满足给定不等式，并参考文献写成论文")
    reasoning = [t for t in plan.tasks if t["agent"] == "reasoning"]
    assert reasoning, "没有派推理任务"
    for task in reasoning:
        assert "evidence" in _roles_depended_on(plan, task), (
            f"推理任务没有等证据就绪: {task.get('objective')}")


def test_evidence_is_dispatched_before_reasoning():
    """可运行顺序上, 证据必须先于推理 (依赖边应让推理在证据之后 ready)。"""
    _brief, plan = _plan_for("证明所有正数 x 满足给定不等式，并参考文献写成论文")
    reasoning_ids = {t["task_id"] for t in plan.tasks if t["agent"] == "reasoning"}
    evidence_ids = {t["task_id"] for t in plan.tasks if t["agent"] == "evidence"}
    # 计划提供 ready() 计算当前可跑任务: 一开始不应有推理就绪
    ready = set(plan.ready(completed=(), running=()))
    assert not (ready & reasoning_ids), "推理在证据就绪前就可运行"
    assert ready & evidence_ids or not evidence_ids, "证据任务没有被优先派工"


# --------------------------------------------------------------------------
# 自足证明: 允许无文献启动
# --------------------------------------------------------------------------
def test_pure_existence_proof_is_not_forced_to_wait_for_evidence():
    """全部推理都落在自足证明上时, 不加证据依赖 (省下一轮无谓检索)。"""
    from src.agents.supervisor import ResearchBrief, SubQuestion

    brief = ResearchBrief(
        original_request="判断该设计是否存在", project_id="p",
        subquestions=[
            SubQuestion(statement="构造存在性证明", kind="existence_proof",
                        owner="reasoning"),
        ])
    assert _all_reasoning_self_contained(brief) is True
    supervisor = SupervisorAgent(llm=None)
    plan = supervisor.plan(brief)
    for task in plan.tasks:
        if task["agent"] == "reasoning":
            assert "evidence" not in _roles_depended_on(plan, task)


def test_one_evidence_needing_subquestion_is_enough_to_require_sources():
    """保守判据: 只要有一个推理子问题需要依据, 推理整体就要等证据。"""
    from src.agents.supervisor import ResearchBrief, SubQuestion

    brief = ResearchBrief(
        original_request="x", project_id="p",
        subquestions=[
            SubQuestion(statement="存在性", kind="existence_proof", owner="reasoning"),
            SubQuestion(statement="与已知结果对比", kind="literature_synthesis",
                        owner="reasoning"),
        ])
    assert _all_reasoning_self_contained(brief) is False


def test_self_contained_kinds_are_declared_explicitly():
    """例外清单必须显式且很窄 —— 放宽它等于允许凭记忆推导。"""
    assert SELF_CONTAINED_SUBQUESTION_KINDS == frozenset({"existence_proof"})


# --------------------------------------------------------------------------
# 既有依赖不得回退
# --------------------------------------------------------------------------
@pytest.mark.parametrize("role,needed", [
    ("writing", "reasoning"),
    ("writing", "evidence"),
    ("review", "writing"),
])
def test_existing_downstream_dependencies_are_preserved(role, needed):
    """写作/审阅的既有前置关系不能因为这次改动而丢失。"""
    _brief, plan = _plan_for("证明所有正数 x 满足给定不等式，并参考文献写成论文")
    tasks = [t for t in plan.tasks if t["agent"] == role]
    if not tasks:
        pytest.skip(f"该请求没有派 {role} 任务")
    for task in tasks:
        assert needed in _roles_depended_on(plan, task), (
            f"{role} 任务丢了 {needed} 前置: {task.get('objective')}")
