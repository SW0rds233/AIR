from __future__ import annotations

"""统一事件键与可聚合指标 (研究日志契约)。

反向安全测试:
- 事件契约目录必须**覆盖源码里全部静态发出的事件类型** —— 新增事件类型而忘记
  登记时, 这条测试会失败, 而不是等到界面读不到字段才发现;
- 缺必需键 / 未登记类型时: 事件照旧入库 (它是证据), 但必须**记录**一条
  `log_anomaly`, 不得静默;
- 指标聚合只读日志与账本, 不改动任何研究状态; 按问题隔离。
"""

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 静态发出事件的位置 (事件名以字面量出现)
_EMIT_SOURCES = (
    "src/research/loop.py",
    "src/research/proposal.py",
    "src/kb/ingest.py",
)


def _literal_emissions(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "events" \
                and isinstance(node.value, ast.List):
            for elt in node.value.elts:
                if (isinstance(elt, ast.Tuple) and len(elt.elts) == 2
                        and isinstance(elt.elts[0], ast.Constant)):
                    out.add(str(elt.elts[0].value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append_event" and node.args
                and isinstance(node.args[0], ast.Constant)):
            out.add(str(node.args[0].value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_emit" and node.args
                and isinstance(node.args[1], ast.Dict)):
            for key, value in zip(node.args[1].keys, node.args[1].values):
                if isinstance(key, ast.Constant) and key.value == "type" \
                        and isinstance(value, ast.Constant):
                    out.add(str(value.value))
    return out


def test_event_catalog_covers_all_literal_emissions():
    """目录必须登记源码里所有字面量事件类型 (否则测试失败, 而不是界面上少字段)。"""
    from src.research.logging_schema import EVENT_SPECS

    emitted: set[str] = set()
    for rel in _EMIT_SOURCES:
        emitted |= _literal_emissions(REPO_ROOT / rel)
    assert emitted, "没解析到任何事件类型, 说明扫描逻辑失效"
    unknown = sorted(emitted - set(EVENT_SPECS))
    assert unknown == [], f"这些事件类型未登记进 logging_schema.EVENT_SPECS: {unknown}"


def test_event_catalog_entries_are_well_formed():
    from src.research.logging_schema import EVENT_SPECS, SURFACE_EVENT_KINDS

    assert len(EVENT_SPECS) >= 30
    for kind, spec in EVENT_SPECS.items():
        assert spec.kind == kind
        assert spec.summary, f"{kind} 缺少用途说明"
        assert len(set(spec.required)) == len(spec.required), f"{kind} 必需键重复"
    assert "log_anomaly" in EVENT_SPECS
    assert set(SURFACE_EVENT_KINDS) <= set(EVENT_SPECS)


def test_validate_event_reports_missing_keys():
    """目录的公共校验入口 (供工具/脚本在写入前自查)。"""
    from src.research.logging_schema import is_registered, validate_event

    assert validate_event("obligation_closed",
                          {"claim_id": "c", "obligation_id": "o", "tool": "sympy"}) == []
    assert set(validate_event("obligation_closed", {"claim_id": "c"})) == {
        "obligation_id", "tool"}
    assert validate_event("brand_new_event", {}) == []
    assert is_registered("run_start") is True
    assert is_registered("brand_new_event") is False


def _view(tmp_path, pid: str = "log1", db_path=None):
    """只读检视层 + 存储 (事件/指标类判据不需要研究引擎)。"""
    from src.research.inspection import StoreInspection
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore

    spec = ResearchSpec(project_id=pid, problem_id="p1",
                        problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore(pid, db_path=db_path or (tmp_path / f"{pid}.sqlite"))
    return StoreInspection(store, spec), store


def test_missing_required_key_is_recorded_not_silently_dropped(tmp_path):
    """少键的事件仍在库里, 但必须同时留下可查询的契约异常。"""
    view, store = _view(tmp_path, "log2")
    try:
        assert store.append_event("obligation_closed", {"claim_id": "clm-x"}) is True
        kinds = [e["type"] for e in store.events()]
        assert "obligation_closed" in kinds, "事件本身不得被丢弃"
        anomalies = store.log_anomalies()
        assert len(anomalies) == 1, anomalies
        anomaly = anomalies[0]
        assert anomaly["kind"] == "obligation_closed"
        assert "obligation_id" in anomaly["missing"] and "tool" in anomaly["missing"]
        assert anomaly["reason"]

        metrics = view.metrics(view.claims(), problem_id="p1")
        assert any("obligation_closed" in item for item in metrics["anomalies"])
    finally:
        store.close()


def test_unregistered_event_type_is_recorded(tmp_path):
    view, store = _view(tmp_path, "log3")
    try:
        store.append_event("brand_new_event", {"whatever": 1})
        anomalies = store.log_anomalies()
        assert [a["kind"] for a in anomalies] == ["brand_new_event"]
        assert "未登记" in anomalies[0]["missing"]
        assert "brand_new_event" in view.metrics(view.claims(), problem_id="p1")["events"]
    finally:
        store.close()


def test_well_formed_event_has_no_anomaly(tmp_path):
    view, store = _view(tmp_path, "log4")
    try:
        store.append_event("obligation_closed",
                           {"claim_id": "c", "obligation_id": "o", "tool": "sympy"})
        assert store.log_anomalies() == []
        assert view.metrics(view.claims(), problem_id="p1")["anomalies"] == []
    finally:
        store.close()


def test_log_anomaly_does_not_recurse(tmp_path):
    _, store = _view(tmp_path, "log5")
    try:
        store.append_event("log_anomaly", {"kind": "x", "missing": "y", "reason": "z"})
        kinds = [e["type"] for e in store.events()]
        assert kinds.count("log_anomaly") == 1, kinds
    finally:
        store.close()


# ----------------------------------------------------------------------
# 真实运行的指标与事件摘要 (驱动团队; 旧引擎已不在运行路径上)
# ----------------------------------------------------------------------
def _team_facts(tmp_path, pid: str = "log6", *, problem_id: str = "p1",
                max_rounds: int = 6) -> dict:
    """跑一次真实团队运行, 返回断言需要的**事实快照**。

    为什么不在运行后留着 store: 团队运行结束时存储会被关闭 (它拥有这个库), 所以这里
    在运行期间把指标/摘要/快照/异常一次取出 —— 断言用的仍是同一批数据。
    """
    from src.graph.research_graph import TeamRun
    from src.research.inspection import StoreInspection
    from src.research.schemas import ResearchSpec
    from src.research.snapshot import snapshot_from_store

    with TeamRun(project_id=pid, problem_id=problem_id,
                 run_id=f"run-{pid}-{problem_id}",
                 request="对所有实数 x: x**2 >= 0", source_policy="user_kb",
                 max_rounds=max_rounds) as team:
        team.run()
        store = team.task_store.store
        spec = ResearchSpec.model_validate(store.get("spec", problem_id))
        view = StoreInspection(store, spec)
        claims = view.claims()
        return {
            "run_id": team.run_id,
            "metrics": view.metrics(claims, problem_id=problem_id),
            "digest": view.event_digest(claims, problem_id=problem_id),
            "claim_ids": [c.id for c in claims],
            "snapshot": snapshot_from_store(store, project_id=pid,
                                            problem_id=problem_id,
                                            run_id=team.run_id),
            "anomalies": store.log_anomalies(),
        }


def test_metrics_aggregate_a_real_run(tmp_path):
    """指标必须来自真实研究过程, 且与冻结快照、运行身份一致。"""
    facts = _team_facts(tmp_path, "log6")
    metrics = facts["metrics"]
    snapshot = facts["snapshot"]

    # 团队的**真实**事件契约 (提交与派工各至少一次)
    assert metrics["events"].get("commit_result", 0) >= 1, metrics["events"]
    assert metrics["events"].get("task_finished", 0) >= 1, metrics["events"]
    # 计数与冻结快照同源
    assert metrics["counts"]["claims"] == len(snapshot.claims)
    assert metrics["counts"]["obligations"] == len(snapshot.obligations)
    assert metrics["counts"]["verifications"] == len(snapshot.verifications)
    assert metrics["identity"]["problem_id"] == "p1"
    assert metrics["identity"]["run_id"] == facts["run_id"], "指标必须挂在本 run 上"
    assert metrics["anomalies"] == []
    assert set(metrics["proposals"]) == {"used", "rejected", "failed", "skipped"}


def test_metrics_are_isolated_per_problem(tmp_path):
    """同项目两个问题: 各自的计数与事件摘要只能看到自己的对象。"""
    facts_a = _team_facts(tmp_path, "log7", problem_id="p1")
    facts_b = _team_facts(tmp_path, "log7", problem_id="p2")
    metrics_a, metrics_b = facts_a["metrics"], facts_b["metrics"]

    assert metrics_a["counts"]["claims"] >= 1
    assert metrics_a["counts"]["obligations"] >= 1
    assert metrics_b["counts"]["claims"] == len(facts_b["snapshot"].claims)
    assert metrics_b["counts"]["claims"] == len(facts_b["claim_ids"])
    assert metrics_a["identity"]["problem_id"] == "p1"
    assert metrics_b["identity"]["problem_id"] == "p2"
    # 事件摘要也按问题过滤: A 的过程不串到 B
    assert all("p2" not in item["detail"] for item in facts_a["digest"])


def test_event_digest_only_shows_surface_events(tmp_path):
    from src.research.logging_schema import SURFACE_EVENT_KINDS

    facts = _team_facts(tmp_path, "log8")
    digest = facts["digest"]
    assert digest, "研究跑完后应当有可展示的过程事件"
    assert {item["kind"] for item in digest} <= set(SURFACE_EVENT_KINDS)
    # 每个条目都要有可读说明, 不能只回显事件名
    for item in digest:
        assert item["detail"] and item["detail"] != item["kind"], item


def test_workbench_state_exposes_metrics_and_anomalies(tmp_path):
    """工作台必须能看到指标与日志契约异常 (否则"少字段"只会在界面上表现为 0)。"""
    from src import server
    from src.graph.research_graph import TeamRun

    # 与工作台读取的是**同一个库路径** (conftest 会把研究库重定向到隔离目录)
    with TeamRun(project_id="log9", problem_id="p1", run_id="run-log9-p1",
                 request="对所有实数 x: x**2 >= 0", source_policy="user_kb",
                 max_rounds=6) as team:
        team.run()
        store = team.task_store.store
        store.append_event("obligation_closed", {"claim_id": "clm-x"})

    payload = server._research_state("log9", "p1")
    assert payload["metrics"]["counts"]["claims"] >= 1
    assert payload["metrics"]["identity"]["problem_id"] == "p1"
    assert payload["log_anomalies"], "工作台必须能看到日志契约异常"
    assert payload["log_anomalies"][0]["kind"] == "obligation_closed"


@pytest.mark.parametrize("kind", ["run_start", "formulated", "capability_declared",
                                  "writing_gaps_fed_back"])
def test_problem_scoped_events_carry_problem_identity(tmp_path, kind):
    """按问题隔离日志的前提: 目录里"每问题一次"的事件必须声明 `problem_id`。

    事件表只按 project 记录; 没有归属字段时多问题项目无法按问题聚合
    (`metrics_from_store` 只能保守地保留, 于是 A 的计数会串到 B)。

    这条判据查的是**事件契约目录** (`EVENT_SPECS`), 与谁发出事件无关 —— 旧引擎的
    `run_start`/`formulated`/`capability_declared` 仍在目录里 (历史事件要可读),
    团队发出的是自己那批 (见 `test_metrics_aggregate_a_real_run`)。
    """
    from src.research.logging_schema import EVENT_SPECS

    assert "problem_id" in EVENT_SPECS[kind].required, kind
    facts = _team_facts(tmp_path, f"log-{kind}", max_rounds=4)
    assert facts["metrics"]["anomalies"] == [], "真实运行不应产生契约异常"
