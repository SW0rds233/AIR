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


def _engine(tmp_path, pid: str = "log1", db_path=None):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id=pid, problem_id="p1",
                        problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore(pid, db_path=db_path or (tmp_path / f"{pid}.sqlite"))
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    return engine, store


def test_missing_required_key_is_recorded_not_silently_dropped(tmp_path):
    """少键的事件仍在库里, 但必须同时留下可查询的契约异常。"""
    engine, store = _engine(tmp_path, "log2")
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

        metrics = engine.metrics()
        assert any("obligation_closed" in item for item in metrics["anomalies"])
    finally:
        store.close()


def test_unregistered_event_type_is_recorded(tmp_path):
    engine, store = _engine(tmp_path, "log3")
    try:
        store.append_event("brand_new_event", {"whatever": 1})
        anomalies = store.log_anomalies()
        assert [a["kind"] for a in anomalies] == ["brand_new_event"]
        assert "未登记" in anomalies[0]["missing"]
        assert "brand_new_event" in engine.metrics()["events"]
    finally:
        store.close()


def test_well_formed_event_has_no_anomaly(tmp_path):
    engine, store = _engine(tmp_path, "log4")
    try:
        store.append_event("obligation_closed",
                           {"claim_id": "c", "obligation_id": "o", "tool": "sympy"})
        assert store.log_anomalies() == []
        assert engine.metrics()["anomalies"] == []
    finally:
        store.close()


def test_log_anomaly_does_not_recurse(tmp_path):
    _, store = _engine(tmp_path, "log5")
    try:
        store.append_event("log_anomaly", {"kind": "x", "missing": "y", "reason": "z"})
        kinds = [e["type"] for e in store.events()]
        assert kinds.count("log_anomaly") == 1, kinds
    finally:
        store.close()


def test_metrics_aggregate_a_real_run(tmp_path):
    """指标必须来自真实研究过程, 且与动作账本/用量一致。"""
    engine, store = _engine(tmp_path, "log6")
    try:
        result = engine.run()
        metrics = engine.metrics()

        assert metrics["events"].get("run_start") == 1
        assert metrics["events"].get("formulated") == 1
        assert metrics["actions"].get("succeeded", 0) >= 1
        assert metrics["counts"]["claims"] == len(result.snapshot.claims)
        assert metrics["counts"]["obligations"] == len(result.snapshot.obligations)
        assert metrics["counts"]["verifications"] == len(result.snapshot.verifications)
        assert metrics["usage"]["actions"] == result.usage["actions"]
        assert metrics["identity"]["problem_id"] == "p1"
        assert metrics["identity"]["run_id"] == engine.run_id
        assert metrics["stopped_reason"] == ""
        assert metrics["anomalies"] == []
        # 提议计数即使为 0 也必须存在 (契约字段不能缺)
        assert set(metrics["proposals"]) == {"used", "rejected", "failed", "skipped"}
    finally:
        store.close()


def test_metrics_are_isolated_per_problem(tmp_path):
    engine_a, store = _engine(tmp_path, "log7")
    try:
        engine_a.run()
        from src.research.loop import ResearchBudget, TheoryEngine
        from src.research.schemas import ResearchSpec

        # 问题 B 使用**自己的** problem_id: 同项目多问题时对象必须按问题隔离
        spec_b = ResearchSpec(project_id="log7", problem_id="p2",
                              problem_statement="对所有实数 x: x**2 >= 0")
        engine_b = TheoryEngine(spec_b, store, budget=ResearchBudget(max_actions=4))
        engine_b.bootstrap()

        metrics_a = engine_a.metrics()
        metrics_b = engine_b.metrics()
        assert metrics_a["events"].get("run_start") == 1
        assert metrics_b["events"].get("run_start") == 1, metrics_b["events"]
        assert metrics_a["counts"]["claims"] >= 1
        assert metrics_a["counts"]["obligations"] >= 1
        # B 只统计自己的对象 (A 的结论/义务不得被算进来)
        assert metrics_b["counts"]["claims"] == len(engine_b._claims())
        assert {c.problem_id for c in engine_b._claims()} == {"p2"}
        assert metrics_a["identity"]["problem_id"] == "p1"
        assert metrics_b["identity"]["problem_id"] == "p2"
        # 事件摘要也按问题过滤: A 的过程不串到 B
        assert all("p2" not in item["detail"] for item in engine_a.event_digest())
    finally:
        store.close()


def test_event_digest_only_shows_surface_events(tmp_path):
    from src.research.logging_schema import SURFACE_EVENT_KINDS

    engine, store = _engine(tmp_path, "log8")
    try:
        engine.run()
        digest = engine.event_digest()
        assert digest, "研究跑完后应当有可展示的过程事件"
        assert {item["kind"] for item in digest} <= set(SURFACE_EVENT_KINDS)
        # 每个条目都要有可读说明, 不能只回显事件名
        for item in digest:
            assert item["detail"] and item["detail"] != item["kind"], item
    finally:
        store.close()


def test_workbench_state_exposes_metrics_and_anomalies(tmp_path, monkeypatch):
    """工作台必须能看到指标与日志契约异常 (否则"少字段"只会在界面上表现为 0)。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server
    from src.research.store import default_db_path

    # 与工作台读取的是**同一个库路径** (conftest 会把研究库重定向到隔离目录)
    engine, store = _engine(tmp_path, "log9", db_path=default_db_path("log9"))
    try:
        engine.run()
        store.append_event("obligation_closed", {"claim_id": "clm-x"})
    finally:
        store.close()

    payload = server._research_state("log9", "p1")
    assert payload["metrics"]["counts"]["claims"] >= 1
    assert payload["metrics"]["identity"]["problem_id"] == "p1"
    assert payload["log_anomalies"], "工作台必须能看到日志契约异常"
    assert payload["log_anomalies"][0]["kind"] == "obligation_closed"


@pytest.mark.parametrize("kind", ["run_start", "formulated", "capability_declared",
                                  "writing_gaps_fed_back"])
def test_problem_scoped_events_carry_problem_identity(tmp_path, kind):
    """按问题隔离日志的前提: 这些"每问题一次"的事件必须带 problem_id。

    事件表只按 project 记录; 没有归属字段时多问题项目无法按问题聚合
    (`metrics_from_store` 只能保守地保留, 于是 A 的计数会串到 B)。
    逐命题事件靠命题归属判断, 不在目录里强制。
    """
    from src.research.logging_schema import EVENT_SPECS

    assert "problem_id" in EVENT_SPECS[kind].required, kind
    if kind == "writing_gaps_fed_back":
        return       # 只在出现写作缺口时发出, 由下面的目录约束与真实用例覆盖
    engine, store = _engine(tmp_path, f"log-{kind}")
    try:
        engine.run()
        seen = [e["payload"] for e in store.events() if e["type"] == kind]
        assert seen, f"真实运行没有产生 {kind}"
        assert any(p.get("problem_id") == "p1" for p in seen), seen
    finally:
        store.close()
