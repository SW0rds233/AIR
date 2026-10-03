from __future__ import annotations

r"""现场缺陷回归: 写作缺口回流后无法消解 → 运行以"有一条未关闭义务"收尾。

现场事件流 (`#11` 之后停止): `synthesize_results` 让结论变成 supported (支持方式
`theorem_application`), 收尾时写作阶段报出缺口"结论没有可展示的推导步骤", 回流成
一条 `writing_missing_argument_chain` **阻塞义务**, 此后什么也没发生 —— 没有成文,
交付级别掉到"条件性研究报告"。

三个独立成因, 各有一条用例:
1. 缺口判定只看 `attempts.steps`, 不认设计证书里的判定链 → **误报缺口**;
2. 理论图 `theory_finalize → END`, 回流出的义务**永远无人处理**;
3. `manifest` 的三个门槛字段互相顶替, 出现 `gate_passed=false` 而
   `delivery_gate_passed=true` 的自相矛盾。
"""



from src.research.argument import writing_gaps
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    ResearchSnapshot,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)

CERTIFICATE = {
    "design": {"v": 211, "k": 15, "lam": 1},
    "counts": {"r": 15, "b": 211, "symmetric": True, "order": 14},
    "evidence": [{"condition": "Bruck–Ryser–Chowla 必要条件", "result": False,
                  "theorem": "Bruck–Ryser–Chowla 定理 (射影平面)",
                  "inputs": {"n": 14}}],
}


def _snapshot(with_certificate: bool) -> ResearchSnapshot:
    claim = Claim(id="clm-1", statement="2-(211,15,1) 设计不存在", status=ClaimStatus.supported,
                  support_kind=SupportKind.theorem_application, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified, design_v=211, design_k=15,
                  design_lambda=1, design_b=211, design_r=15, design_verdict="nonexistent")
    arguments = {"design_report": dict(CERTIFICATE)} if with_certificate else {}
    record = VerificationRecord(id="ver-1", claim_id="clm-1", tool="design_necessity",
                               status="passed", validation_status=ValidationStatus.verified,
                               scope=Coverage.target, arguments=arguments)
    return ResearchSnapshot(project_id="gap", problem_id="p1", claims=[claim],
                            verifications=[record])


# ----------------------------------------------------------------------
# 成因 1: 有证书判定链就不算"没有推导步骤"
# ----------------------------------------------------------------------

def test_certificate_chain_counts_as_argument_chain():
    """有设计证书 (逐条反查定理/输入/结论) 的结论不得被判定为缺少推导步骤。"""
    gaps = writing_gaps(_snapshot(with_certificate=True))
    kinds = {gap.kind for gap in gaps}
    assert "missing_argument_chain" not in kinds, kinds
    assert not [g for g in gaps if g.severity == "blocking"], gaps


def test_without_certificate_or_steps_the_gap_is_still_reported():
    """反向保护: 真的没有推导链时**必须**仍然报缺口, 否则这道检查就失效了。"""
    gaps = writing_gaps(_snapshot(with_certificate=False))
    kinds = {gap.kind for gap in gaps}
    assert "missing_argument_chain" in kinds, kinds
    assert [g for g in gaps if g.severity == "blocking"], gaps


# ----------------------------------------------------------------------
# 成因 2: 回流出的义务必须能被真正处理
# ----------------------------------------------------------------------

def test_engine_resume_after_reflow_clears_completion(tmp_path):
    """`resume_after_reflow()` 只解除完成态, 让 `step()` 能继续。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.store import ResearchStore

    spec = _spec()
    store = ResearchStore("gapresume", db_path=tmp_path / "g.sqlite")
    engine = TheoryEngine(spec, store=store, budget=ResearchBudget(max_actions=6))
    engine.bootstrap()
    engine._done = True
    engine.resume_after_reflow()
    assert engine.done is False
    assert any("重新进入研究循环" in note for note in engine.notes)
    # 解除完成态之后必须真的能派发动作 (否则等于没恢复)
    engine.resume_after_reflow
    info = engine.step()
    assert info and info.get("action"), info
    store.close()


def test_graph_routes_back_to_research_loop_when_gap_reopened():
    """收尾不再是终点: 有回流阻塞义务时必须回研究循环。"""
    from src.graph import theory_pipeline

    reopened = {"feedback_reopened": True, "finalize_rounds": 1}
    assert theory_pipeline._route_after_finalize(reopened) == "step"
    # 回流周期内每完成一步就回收尾复核, 不能一直做动作而不交付
    assert theory_pipeline._route_after_step(reopened) == "finalize"
    # 没有回流时保持原行为: 收尾即结束
    assert theory_pipeline._route_after_finalize({}) == "end"
    # 澄清收尾不得进入研究循环
    assert theory_pipeline._route_after_finalize(
        {"feedback_reopened": True, "needs_clarification": True}) == "end"
    # 上限保护: 超过上限必须结束, 不能死循环
    limit = theory_pipeline._refinalize_limit()
    assert theory_pipeline._route_after_finalize(
        {"feedback_reopened": True, "finalize_rounds": limit + 1}) == "end"


def test_finalize_defers_export_until_the_gap_is_resolved(tmp_path, monkeypatch):
    """缺口未消解时**不导出交付包** —— 否则包里会留下义务未关闭时的旧稿。

    做法: 让引擎的 `writing_gap_feedback` 恒返回 True (等价于"收尾时回流出阻塞义务"),
    其余流程全部真实执行 —— 因此这条用例同时验证了节点在回流分支上的早退行为。
    """
    from src import config
    from src.graph import theory_pipeline
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.store import ResearchStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")

    spec = _spec()
    store = ResearchStore("gapdefer", db_path=tmp_path / "d.sqlite")
    engine = TheoryEngine(spec, store=store, budget=ResearchBudget(max_actions=6))
    engine.bootstrap()
    engine.writing_gap_feedback = lambda snapshot=None: True
    monkeypatch.setattr(theory_pipeline, "_engine_from_state", lambda state: engine)

    out = theory_pipeline.theory_finalize_node({"project_id": "gapdefer", "problem_id": "p1",
                                                "topic": "t", "request": "r"})
    assert out["feedback_reopened"] is True
    assert out["finalize_rounds"] == 1
    assert "package_dir" not in out, "缺口未消解就导出了交付包"
    produced = list((tmp_path / "out").rglob("manifest.json")) if (tmp_path / "out").exists() else []
    assert not produced, f"缺口未消解却留下了交付包: {produced}"
    store.close()


def test_unresolvable_gap_still_terminates_within_the_limit(tmp_path, monkeypatch):
    """缺口始终消不掉时, 重入次数触顶必须收尾 —— 不能变成死循环。

    这是回边的安全阀: 回流本身有意义 (义务通常能真被消解), 但"永远消不掉"的情形必须
    有上限, 且最终仍要产出交付包 (降级但不空手)。
    """
    from src import config
    from src.graph import theory_pipeline

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    monkeypatch.setenv("THEORY_REFINALIZE_MAX", "1")

    seen: set[int] = set()
    original = theory_pipeline._engine_from_state

    def _wrap(state):
        engine = original(state)
        if id(engine) not in seen:
            seen.add(id(engine))
            engine.writing_gap_feedback = lambda snapshot=None: True
        return engine

    monkeypatch.setattr(theory_pipeline, "_engine_from_state", _wrap)
    final = theory_pipeline.run_theory_pipeline(
        request="对所有实数 x: x**2 >= 0", topic="t", project_id="gaplimit",
        problem_id="p1", max_actions=8, max_tool_calls=8)

    assert final.get("package_dir"), "触顶后仍必须交付 (降级但不空手)"
    assert final.get("current_phase") == "theory_finalize"


# ----------------------------------------------------------------------
# 成因 3: manifest 三道门槛不得互相顶替
# ----------------------------------------------------------------------

def test_manifest_keeps_the_three_gates_independent(tmp_path):
    from src.graph.theory_pipeline import _refresh_manifest
    from src.research.acceptance import GateResult

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "manifest.json").write_text('{"delivery_level": "条件性研究报告"}',
                                           encoding="utf-8")
    publication = GateResult(passed=True)
    _refresh_manifest(package, "条件性研究报告", publication, "ok",
                      theory_passed=False, delivery_passed=True)
    import json

    payload = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert payload["gate_passed"] is False, "研究门槛未过不得写成 true"
    assert payload["delivery_gate_passed"] is True
    assert payload["publication_gate"]["complete"] is True
    assert payload["delivery_level"] == "条件性研究报告"


def _spec():
    from src.research.schemas import ResearchSpec

    return ResearchSpec(project_id="gapresume", problem_id="p1",
                        problem_statement="对所有实数 x: x**2 >= 0")
