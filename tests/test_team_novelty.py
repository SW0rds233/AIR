from __future__ import annotations

"""新颖性对照在团队路径上必须留下记录 (合并计划 §5.4 / G01 的前置)。

为什么这条比"能跑"更重要
------------------------
交付包与出版层按**新颖性记录**决定能不能宣称原创 (`package._limitation`):
没有记录或状态 `unchecked` 时必须如实写成"在受限检索范围内整理, 新颖性未确认"。
因此"没比过"这件事本身必须落成对象 —— 省略它会让成果看起来没有新颖性问题,
这正是最容易被无声放过的一类错误。

同时把**授权边界**固定住: `user_kb` 的任务不得为了做新颖性对照去联网检索。
"""

import pytest

from src.research.novelty_service import assess_novelty_for, novelty_lookup_for
from src.research.schemas import Claim

REQUEST = "请证明对所有实数 x 都有 x**2 >= 0"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_user_kb_policy_never_searches_externally():
    """`user_kb` 的任务只允许用已绑定资料库: 新颖性对照不是绕过授权的入口。"""
    lookup, scope = novelty_lookup_for(None, source_policy="user_kb")
    assert lookup is None and scope == {}, (lookup, scope)
    lookup, scope = novelty_lookup_for(None, source_policy="")
    assert lookup is None, "空策略同样不得外搜"


def test_autonomous_policy_uses_external_lookup_with_declared_scope():
    """授权自主检索时才走外部对照, 并说明检索覆盖了哪些范围。"""
    lookup, scope = novelty_lookup_for(None, source_policy="autonomous")
    assert lookup is not None
    assert scope["kind"] == "external"
    assert scope["covered"], "必须说明检索范围 (否则无法判断'没找到'意味着什么)"


def test_no_lookup_still_records_unchecked_with_a_reason():
    """没有对照来源时**照常落记录**, 并写明为什么 —— 不能静默省略。"""
    record, scope = assess_novelty_for(Claim(id="c1", statement="x**2 >= 0"),
                                       source_policy="user_kb")
    assert record.status.value == "unchecked"
    assert "未接入" in record.conclusion
    assert scope == {}


class _FakeKb:
    """最小知识底座替身: 只实现 `novelty_lookup` 需要的那部分。"""

    usable = True
    topic = "demo-kb"

    def search(self, request):  # noqa: ANN001, ARG002
        class _Outcome:
            refs: list = []
        return _Outcome()


def test_local_kb_is_preferred_and_recorded_in_the_scope():
    """有可用知识底座时用本地库对照, 并把"比的是哪个库"写进记录。"""
    record, scope = assess_novelty_for(Claim(id="c1", statement="x**2 >= 0"),
                                       service=_FakeKb(), source_policy="user_kb")
    assert scope.get("kind") == "local_kb", scope
    assert scope.get("source_set_id") == "demo-kb"


def test_team_run_writes_a_novelty_record_and_it_reaches_the_snapshot(isolated):
    """团队运行必须留下新颖性记录, 且快照/交付包能看到它 (含"未确认"的局限)。"""
    from src.graph.research_graph import TeamRun
    from src.research.snapshot import snapshot_from_store

    with TeamRun(project_id="proj-nov-t", problem_id="p1", run_id="run-nov",
                 request=REQUEST, source_policy="user_kb", max_rounds=6) as team:
        team.prepare()
        store = team.task_store.store
        store.put("claim", "clm-pre", {
            "id": "clm-pre", "statement": "x**2 >= 0", "problem_id": "p1",
            "_scope": {"project_id": "proj-nov-t", "problem_id": "p1",
                       "run_id": "run-nov"}})
        team.run()
        records = store.list_latest("novelty")
        assert records, "团队路径没有留下任何新颖性记录"
        # 确定性 id: 同一条命题的**同一版本**只有一条记录 (重复对照不该堆出多个对象)。
        # 版本号取命题当时的版本 —— 推理角色可能已经把它推进到 v2, 因此不断言具体版本。
        ids = {row["id"] for row in records}
        assert len(ids) == 1, ids
        assert next(iter(ids)).startswith("nov-clm-pre-v"), ids
        assert all(row["status"] == "unchecked" for row in records)
        snapshot = snapshot_from_store(store, project_id="proj-nov-t",
                                       problem_id="p1", run_id="run-nov")
    assert snapshot.novelty, "快照没有收集新颖性记录 —— 交付包会显示成'没有新颖性问题'"
    # 交付包按它写局限: 未确认时必须如实说明, 不得包装成原创
    from src.research.package import _limitation

    assert "新颖性未确认" in _limitation(snapshot)


def test_novelty_is_registered_as_a_writable_object_kind():
    """`novelty` 必须在封闭的对象种类集合里, 否则构造候选时整条任务被判失败。"""
    from src.agents.protocol import OBJECT_KINDS, writable_kinds

    assert "novelty" in OBJECT_KINDS
    assert "novelty" in writable_kinds("evidence")
    from src.research.projection import KIND_MAP

    assert KIND_MAP["novelty"] == "novelty"


def test_blocked_results_may_still_carry_candidates():
    """受阻**不等于没有产出**: 检索受阻时"新颖性未对照"仍要落盘。

    这个入参此前不存在, 于是调用方一传 `changes` 整条任务就 `failed`, 而卡片上只写
    "成果不符合契约" —— 症状离原因很远。
    """
    from src.agents.evidence import EvidenceAgent
    from src.agents.protocol import AgentTask

    agent = EvidenceAgent()
    assert callable(agent._novelty_records), "证据角色必须能产出新颖性候选"
    task = AgentTask(task_id="t1", agent="evidence", objective="o",
                     project_id="p", problem_id="p1", run_id="r1")
    result = agent.blocked(task, "没有可用资料源",
                           changes=[_dummy_proposal()])
    assert result.outcome.value == "blocked"
    assert [c.kind for c in result.proposed_changes] == ["novelty"]
    assert result.failure_reason == "没有可用资料源"


def _dummy_proposal():
    from src.agents.protocol import ChangeProposal

    return ChangeProposal(kind="novelty", payload={"id": "nov-x", "claim_id": "clm-x"},
                          object_id="nov-x", rationale="受阻时的对照记录")
