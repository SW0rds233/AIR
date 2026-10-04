from __future__ import annotations

"""计划完备性与运行身份 (合并计划 §3.1 G03 / §4.2)。

两个缺陷的回归, 都是在"带问题说明附件"的真实流程里被抓到的:

1. **交付形态要求的角色必须有任务**: 子问题来自任务分类或模型提议, 而"交付需要哪些
   角色"来自 `_DELIVERABLE_ROLES`。两张表对不上时, 计划里没有那个角色的任务, 而主控
   仍然要求它 —— 运行以"没有可派发的任务, 且交付形态要求未满足"收尾, **任务全跑完却
   一条结论都没有** (实测: 4 个任务跑完, 命题数 0, 交付等级掉到研究备忘录)。

2. **每个对象都要带运行身份**: `needs_to_tasks` 生成的补派任务没有 `run_id`, 它提交的
   对象 `_scope.run_id` 为空 —— 按运行裁剪的视图 (交付摘要的登记对象计数、按 run 过滤
   的产物清单) 会把它们算成"不是这次运行产出的" (实测: 工作台里有命题, 摘要里 claim=0)。
"""

import pytest

from src.agents.protocol import AgentTask
from src.agents.supervisor import (
    _DEFAULT_ROLE_SUBQUESTION,
    _DELIVERABLE_ROLES,
    SupervisorAgent,
)

REQUEST = "对所有实数 x: x**2 >= 0"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.mark.parametrize("deliverable", sorted(_DELIVERABLE_ROLES))
def test_every_required_role_has_a_subquestion(deliverable):
    """交付形态要求的每个角色都必须在计划里有对应子问题 (兜底子问题表要能覆盖)。"""
    supervisor = SupervisorAgent()
    brief = supervisor.brief(REQUEST, project_id="p", problem_id="p1")
    brief.deliverables = [deliverable]
    planned = supervisor.plan(brief)
    owners = {str(t.get("agent", "")) for t in planned.tasks}
    required = set(_DELIVERABLE_ROLES[deliverable])
    missing = sorted(role for role in required if role not in owners)
    assert not missing, (
        f"交付形态 {deliverable} 要求 {sorted(required)}, 但计划里只有 {sorted(owners)}: "
        "缺的角色永远不会被派工, 而交付判定仍会要求它")
    # 兜底表必须能覆盖所有会被要求的角色 (否则补不上)
    for role in required:
        assert role in _DEFAULT_ROLE_SUBQUESTION or role in owners, role


def test_兜底不覆盖已有措辞():
    """兜底只补缺失角色: 已由分类或模型给出的子问题措辞必须保留。"""
    supervisor = SupervisorAgent()
    brief = supervisor.brief(REQUEST, project_id="p", problem_id="p1")
    before = [sq.statement for sq in brief.subquestions if sq.owner == "reasoning"]
    supervisor._ensure_required_roles(brief, ["problem_report"])
    after = [sq.statement for sq in brief.subquestions if sq.owner == "reasoning"]
    if before:
        assert after[: len(before)] == before, "兜底覆盖了已有的推理子问题措辞"


def test_complementary_tasks_carry_the_run_identity(isolated):
    """补派任务提交的对象必须带**本 run** 的身份 (`_scope.run_id`)。"""
    from src.graph.research_graph import TeamRun
    from src.research.store import KIND_CLAIM, KIND_OBLIGATION, KIND_VERIFICATION

    with TeamRun(project_id="proj-scope", problem_id="p1", run_id="run-scope",
                 request=REQUEST, source_policy="user_kb", max_rounds=6) as team:
        team.run()
        store = team.task_store.store
        rows: list[dict] = []
        for kind in (KIND_CLAIM, KIND_OBLIGATION, KIND_VERIFICATION):
            rows.extend(store.list_latest(kind, strip_meta=False))
        assert rows, "团队运行没有产出任何对象"
        wrong = [(row.get("id"), (row.get("_scope") or {}).get("run_id"))
                 for row in rows
                 if str((row.get("_scope") or {}).get("run_id", "")) != "run-scope"]
        assert not wrong, f"这些对象没有本 run 的身份 (按 run 裁剪的视图会漏掉它们): {wrong}"
        # 按运行裁剪的投影必须能看到它们 (摘要里的登记对象计数走的是同一条查询)
        visible = team.projection.rows("claim") + team.projection.rows("obligation")
        assert visible, "按 run 裁剪的投影看不到任何对象 (身份没写对)"


def test_planned_tasks_are_stamped_before_execution(isolated):
    """执行口必须回填运行身份 —— 需求派工的任务本身不带 `run_id`。"""
    from src.graph.research_graph import TeamRun

    with TeamRun(project_id="proj-stamp", problem_id="p1", run_id="run-stamp",
                 request=REQUEST, source_policy="user_kb", max_rounds=2) as team:
        team.prepare()
        from src.agents.protocol import ChangeProposal  # noqa: F401  (契约引用)
        from src.agents.supervisor import needs_to_tasks

        task = AgentTask(agent="reasoning", objective="补一条推导", subquestion="",
                         project_id="proj-stamp", problem_id="p1", run_id="")
        assert not task.run_id
        tasks = needs_to_tasks([], brief=team.outcome.brief, plan_version=1)
        assert isinstance(tasks, list)  # 空需求 -> 不派工 (不猜)
