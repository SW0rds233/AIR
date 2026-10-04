from __future__ import annotations

"""可恢复的团队运行状态 (合并计划 §3.3 G16)。

审计缺口原文: "`TeamRun` 每次创建空 `TeamLoopState`; `recovery_view()` 只是查询,
未重建 brief/plan/results/open_needs/dispatched; `TaskStore.create()` 同键复用不返回
旧 task identity。"

这里固定的是**恢复的实质**, 不是"能不能读回几行数据库":

1. 落盘的运行状态必须能**重建主控循环** (画像 + 计划 + 已交回成果 + 派工历史);
2. 进程被杀后 `resume()` 继续跑, **已提交的动作不重跑** —— 判据是"这些 task 的
   agent run 没有新增", 而不是"任务记录条数没变";
3. 已收尾的运行 `resume()` **不重跑**, 并按落盘时记下的状态返回 (不能把"已完成"
   显示成"未跑过");
4. 收尾原因/等级/用量在恢复后仍然如实 (不是恢复成一个空壳)。
"""

import time

import pytest

REQUEST = "判断参数为 2-(211,15,1) 的设计是否存在"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _team(run_id: str, *, max_rounds: int):
    from src.graph.research_graph import TeamRun

    return TeamRun(project_id="proj-resume", problem_id="p1", run_id=run_id,
                   request=REQUEST, source_policy="user_kb",
                   max_rounds=max_rounds)


def test_loop_state_is_persisted_and_restorable(isolated):
    """画像/计划/派工历史必须落盘并可重建 (只落任务行是不够的)。"""
    team = _team("run-state", max_rounds=2)
    try:
        team.prepare()
        assert team.loop.brief is not None and team.loop.plan is not None
        plan_id = team.loop.plan.plan_id
        brief_id = team.loop.brief.brief_id
    finally:
        team.close()

    again = _team("run-state", max_rounds=2)
    try:
        view = again.recovery_view()
        assert view["loop_state_restorable"] is True
        restored = again._load_loop_state()
        assert restored is not None
        # 复用**同一**画像与计划身份 (重新画像会换掉任务身份与依赖边)
        assert restored.brief.brief_id == brief_id
        assert restored.plan.plan_id == plan_id
        assert restored.plan.edges == again.loop.plan.edges if again.loop.plan else True
    finally:
        again.close()


def test_resume_after_a_kill_does_not_redo_committed_work(isolated):
    """模拟进程被杀: 逐步驱动 N 轮后丢弃对象, 再开一个同 run 的实例继续。"""
    team = _team("run-killed", max_rounds=8)
    try:
        team.prepare()
        for _ in range(2):
            if not team.step():
                break
        committed = {tid: r.agent_run_id for tid, r in team.loop.results.items()}
        runs_before = {r["run_id"]: r["task_id"] for r in team.task_store.runs()}
        dispatched_before = set(team.loop.dispatched)
        assert committed, "两轮之后应当已有交回的成果"
    finally:
        # 直接丢弃对象 = 进程被杀 (没有 _finish, 没有收尾落盘)
        team.close()

    again = _team("run-killed", max_rounds=8)
    try:
        view = again.recovery_view()
        assert view["loop_state_restorable"] is True
        assert view["resumable"] is True, view
        outcome = again.resume()
        # 1) 已提交的动作没有重跑: 那些 task 的 agent run 还是原来那一次
        kept = {tid: again.loop.results[tid].agent_run_id
                for tid in committed if tid in again.loop.results}
        for tid, run_id in committed.items():
            if tid in kept:
                assert kept[tid] == run_id, f"任务 {tid} 在恢复后被重跑 (换了 agent run)"
        # 2) 恢复也**没有为已提交的任务新开一次执行记录** (attempt 不重复计费)
        runs_after = {r["run_id"]: str(r.get("task_id", ""))
                      for r in again.task_store.runs()}
        assert set(runs_before) <= set(runs_after), "恢复丢掉了已有的执行记录"
        committed_task_ids = set(committed)
        duplicated = [rid for rid in set(runs_after) - set(runs_before)
                      if runs_after[rid] in committed_task_ids]
        assert not duplicated, f"已提交的任务被重新执行: {duplicated}"
        # 2) 已派发过的"角色+目标"仍在派工历史里 (否则同一件事会被再派一次)
        assert dispatched_before <= set(again.loop.dispatched)
        # 3) 恢复后确实继续推进 (不是停在原地)
        assert outcome.rounds >= 2
        assert outcome.status, outcome
        # 4) 没有被重新画像/重新计划
        assert again.loop.plan is not None
    finally:
        again.close()


def test_finished_run_is_not_rerun_and_reports_its_recorded_state(isolated):
    """已收尾的运行 `resume()` 不得重跑, 且状态/等级按**落盘时记下的**返回。"""
    team = _team("run-finished", max_rounds=2)
    try:
        first = team.run()
        status_before = first.status
        level_before = first.delivery.get("level")
        tasks_before = len(first.results)
        assert status_before == "partial", status_before   # 轮次上限收尾
    finally:
        team.close()

    again = _team("run-finished", max_rounds=8)
    try:
        view = again.recovery_view()
        assert view["resumable"] is False, "已收尾的运行不应被当成可继续"
        resumed = again.resume()
        assert resumed.status == status_before, (
            f"恢复把收尾状态改成了 {resumed.status!r} (应为 {status_before!r})")
        assert resumed.delivery.get("level") == level_before
        assert len(resumed.results) == tasks_before
        # 收尾原因如实保留 (不是"达到轮次上限"这类默认话术覆盖它)
        assert resumed.stop_reason
    finally:
        again.close()


def test_missing_run_state_is_reported_and_falls_back_to_a_fresh_run(isolated):
    """没有落盘状态时如实报告, 然后按**新运行**跑 (不假装恢复成功)。"""
    team = _team("run-missing", max_rounds=2)
    try:
        assert team._load_loop_state() is None
        outcome = team.resume()
        assert any(e["kind"] == "run_state_missing" for e in team.runtime.events), \
            team.runtime.events
        assert outcome.status in ("completed", "partial", "waiting_user", "stopped")
    finally:
        team.close()


def test_resume_uses_the_persisted_round_budget(isolated):
    """恢复后的轮次不是从零重数 (否则一次续跑又能跑满全部轮次)。"""
    team = _team("run-rounds", max_rounds=2)
    try:
        team.prepare()
        team.step()
        rounds_before = team.loop.rounds
    finally:
        team.close()
    again = _team("run-rounds", max_rounds=2)
    try:
        again.resume()
        assert again.loop.rounds >= rounds_before
        # 上限仍是 2: 恢复不该把已经用掉的轮次"还回来"
        assert again.loop.rounds <= 2, again.loop.rounds
        assert again.loop.finished
    finally:
        again.close()
        time.sleep(0.05)
