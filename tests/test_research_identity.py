from __future__ import annotations

"""问题级运行与交付身份 (计划书 §3 R6)。

反向安全测试:
- `run_id` / `branch_id` 必须**持久化**: 续跑不能生成新的运行身份, 否则同一次研究
  在动作账本、runtime、快照和产物目录里会变成多次运行;
- 快照自带 project/problem/run/branch —— 权威身份来自对象本身, 而不是调用方
  "取第一个规格"再拼;
- 交付 manifest 与快照使用同一个身份; 目录名不决定归属;
- 旧库 (`snapshots` 没有身份列) 打开时补列并**按已有 JSON 回填**, 缺失的保持为空
  (不猜归属), 不破坏历史数据。
"""

import sqlite3

import pytest


def _engine(tmp_path, pid: str, *, run_id: str = "", problem_id: str = "p1"):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id=pid, problem_id=problem_id,
                        problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore(pid, db_path=tmp_path / f"{pid}.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=15), run_id=run_id)
    return engine, store, spec


def test_run_identity_persists_across_resume(tmp_path):
    """续跑必须沿用同一个 run_id/branch_id, 否则账本与产物对不上。"""
    engine, _, _ = _engine(tmp_path, "rid1")
    assert engine.bootstrap()
    action_taken = engine.step()
    assert action_taken  # 确实执行了动作
    engine.save_runtime()
    run_id, branch_id = engine.run_id, engine.branch_id
    assert run_id
    engine.store.close()

    resumed, store2, _ = _engine(tmp_path, "rid1")
    # 未加载前是新的内存身份; load_runtime 后必须换成落盘的那个
    assert resumed.run_id != run_id
    resumed.load_runtime()
    assert resumed.run_id == run_id
    assert resumed.branch_id == branch_id

    # 动作账本里的身份与实际运行一致 (同一 run/branch)
    entries = store2.list_actions(resumed.spec.problem_id)
    assert entries
    assert {e.get("run_id") for e in entries} == {run_id}
    assert {e.get("branch_id") for e in entries} == {branch_id}
    store2.close()


def test_snapshot_carries_problem_run_branch_and_columns(tmp_path):
    engine, store, spec = _engine(tmp_path, "rid2", run_id="run-explicit")
    result = engine.run()
    snapshot = result.snapshot

    assert snapshot.problem_id == spec.problem_id
    assert snapshot.run_id == "run-explicit"
    assert snapshot.branch_id
    assert result.gate.passed

    rows = store._conn.execute(
        "SELECT problem_id, run_id, branch_id FROM snapshots WHERE snapshot_id=?",
        (snapshot.snapshot_id,)).fetchall()
    assert rows == [(spec.problem_id, "run-explicit", snapshot.branch_id)]

    listed = store.list_snapshots(problem_id=spec.problem_id)
    assert [s["snapshot_id"] for s in listed] == [snapshot.snapshot_id]
    assert listed[0]["run_id"] == "run-explicit"
    # 过滤到别的问题/运行时不得返回该快照 (fail-closed)
    assert store.list_snapshots(problem_id="prob-other") == []
    assert store.list_snapshots(problem_id=spec.problem_id, run_id="run-other") == []
    store.close()


def test_explicit_run_id_is_adopted(tmp_path):
    engine, store, _ = _engine(tmp_path, "rid3", run_id="run-from-session")
    assert engine.run_id == "run-from-session"
    assert engine.identity()["run_id"] == "run-from-session"
    assert engine.identity()["problem_id"] == engine.spec.problem_id
    store.close()


def test_legacy_snapshot_table_is_migrated(tmp_path):
    """旧库没有身份列: 打开时补列, 并从 JSON 回填真实身份。"""
    from src.research.schemas import ResearchSnapshot
    from src.research.store import ResearchStore

    # 库文件放在隔离研究目录下 (conftest 会把非隔离路径的研究库重定向到这里)
    db = tmp_path / "data" / "research" / "legacy.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE snapshots (
            project_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, snapshot_id)
        );
        """)
    good = ResearchSnapshot(project_id="legacy", problem_id="p1", run_id="run-old",
                            branch_id="route-old")
    partial = ResearchSnapshot(project_id="legacy", problem_id="p1")
    conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                 ("legacy", good.snapshot_id, good.model_dump_json(), good.created_at))
    conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                 ("legacy", partial.snapshot_id, partial.model_dump_json(),
                  partial.created_at))
    conn.commit()
    conn.close()

    store = ResearchStore("legacy", db_path=db)
    raw = sqlite3.connect(str(db))
    columns = {r[1] for r in raw.execute("PRAGMA table_info(snapshots)")}
    assert {"problem_id", "run_id", "branch_id"} <= columns
    by_id = {r[0]: (r[1], r[2], r[3]) for r in raw.execute(
        "SELECT snapshot_id, problem_id, run_id, branch_id FROM snapshots")}
    assert set(by_id) == {good.snapshot_id, partial.snapshot_id}, by_id
    assert by_id[good.snapshot_id] == ("p1", "run-old", "route-old")
    # 旧 JSON 里没有的身份字段保持为空 (NULL), 不猜测
    assert by_id[partial.snapshot_id] == ("p1", None, None)

    # 历史数据未被改写: 内容仍可读回
    loaded = store.load_snapshot(good.snapshot_id)
    assert loaded is not None and loaded.run_id == "run-old"
    store.close()
    raw.close()
    # 迁移可重复执行 (幂等)
    store2 = ResearchStore("legacy", db_path=db)
    assert store2.list_snapshots(problem_id="p1")[0]["snapshot_id"] == partial.snapshot_id
    store2.close()


def test_workbench_state_reports_identity_and_scopes_experiments(tmp_path, monkeypatch):
    """工作台状态必须给出统一身份, 且实验建议按当前问题过滤 (R6)。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_CLAIM, KIND_GAP, KIND_SPEC

    engine, store, spec = _engine(tmp_path, "wb-identity", run_id="run-wb")
    engine.run()
    # 同项目的**第二个研究问题**: 它自己的规格与命题
    other_spec = ResearchSpec(project_id="wb-identity", problem_id="p-other",
                              problem_statement="另一个问题")
    store.put(KIND_SPEC, "p-other", other_spec.model_dump(mode="json"))
    store.put(KIND_CLAIM, "clm-other", {
        "id": "clm-other", "statement": "另一个问题的结论", "problem_id": "p-other",
        "status": "proposed"})
    # 属于本问题的实验建议 + 属于别的问题的实验建议
    store.put(KIND_GAP, "exp-mine", {
        "id": "exp-mine", "claim_id": engine._claims()[0].id, "title": "我的建议",
        "decision_rule": "r", "execution_status": "proposed"})
    store.put(KIND_GAP, "exp-foreign", {
        "id": "exp-foreign", "claim_id": "clm-other", "title": "别人的建议",
        "decision_rule": "r", "execution_status": "proposed"})
    store.close()

    payload = server._research_state("wb-identity", spec.problem_id)
    assert payload["run_id"] == "run-wb"
    assert payload["branch_id"]
    assert [s["run_id"] for s in payload["snapshots"]] == ["run-wb"]
    assert [e["id"] for e in payload["experiments"]] == ["exp-mine"]
    assert {p["problem_id"] for p in payload["problems"]} == {spec.problem_id, "p-other"}

    # 切到另一个问题时: 不返回第一个问题的建议 (两个问题互不串数据)
    other = server._research_state("wb-identity", "p-other")
    assert [e["id"] for e in other["experiments"]] == ["exp-foreign"]
    assert [c["id"] for c in other["claims"]] == ["clm-other"]


def test_two_problems_in_one_project_run_and_resume_independently(tmp_path, monkeypatch):
    """计划书 R6 验收: 同项目两个问题并行后**分别**恢复, 预算/身份互不影响。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from langgraph.checkpoint.memory import MemorySaver

    from src.graph import theory_pipeline

    app = theory_pipeline.build_theory_pipeline(saver := MemorySaver())
    base = {"project_id": "parallel", "interactive": False, "checkpointer": saver}
    runs = {}
    for problem_id, request in (("pA", "对所有实数 x: x**2 >= 0"),
                                ("pB", "对所有实数 x: 1/x >= 0")):
        runs[problem_id] = theory_pipeline.run_theory_pipeline(
            request=request, problem_id=problem_id, max_actions=2, **base)
        assert runs[problem_id]["run_id"]

    assert runs["pA"]["run_id"] != runs["pB"]["run_id"]
    assert runs["pA"]["snapshot_id"] != runs["pB"]["snapshot_id"]

    # pB 的检查点必须独立存在: 恢复 pB 时不得借用 pA 的状态
    saved_b = theory_pipeline._checkpoint_state(
        app, {"configurable": {"thread_id": "theory:parallel:pB"}})
    assert saved_b.get("problem_id") == "pB"
    assert saved_b.get("run_id") == runs["pB"]["run_id"]

    def _progress(problem_id):
        from src.research.store import KIND_RUNTIME, ResearchStore

        store = ResearchStore("parallel",
                              db_path=config.DATA_DIR / "research" / "parallel.sqlite")
        try:
            data = store.get(KIND_RUNTIME, problem_id, strip_meta=False) or {}
            return data.get("run_id"), data.get("actions")
        finally:
            store.close()

    run_a, actions_a = _progress("pA")
    # 换一个进程级检查点再"续跑 pB": 没有检查点时必须如实说明是重新开始,
    # 但**运行身份沿用落盘的那个** (不能因为换了入口就变成另一次运行)
    fresh_saver = MemorySaver()
    resumed = theory_pipeline.run_theory_pipeline(
        problem_id="pB", resume=True, max_actions=40, project_id="parallel",
        interactive=False, checkpointer=fresh_saver)
    assert resumed.get("resume_note"), "无检查点时必须说明不是真正续跑"
    assert resumed["run_id"] == runs["pB"]["run_id"], "续跑不得换运行身份"
    assert _progress("pA") == (run_a, actions_a), "运行 pB 不得改动 pA 的运行状态"


@pytest.mark.parametrize("with_checkpoint", [False, True])
def test_cli_resume_reports_what_it_actually_did(tmp_path, monkeypatch, with_checkpoint):
    """`resume` 必须区分"真从检查点继续"和"从头开始", 并显式说明 (R6)。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from langgraph.checkpoint.memory import MemorySaver

    from src.graph import theory_pipeline

    checkpointer = MemorySaver() if with_checkpoint else None
    kwargs = {"request": "对所有实数 x: x**2 >= 0", "project_id": "res-cli",
              "problem_id": "p-res", "max_actions": 6, "interactive": False,
              "checkpointer": checkpointer}
    first = theory_pipeline.run_theory_pipeline(**kwargs)
    assert first.get("snapshot_id"), "第一次运行应产出快照"
    assert first.get("run_id")
    assert first.get("resumed") is False

    second = theory_pipeline.run_theory_pipeline(resume=True, **kwargs)
    # 已收尾的运行不得被静默重跑: 要么明确报告"已收尾", 要么真的从检查点继续
    assert second.get("snapshot_id")
    if second.get("resumed"):
        assert second["run_id"] == first["run_id"]
    else:
        assert second.get("resume_note"), "未继续时必须说明原因"
        assert second["run_id"] == first["run_id"]
