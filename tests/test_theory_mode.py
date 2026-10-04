from __future__ import annotations

"""理论研究模式 (theory mode) 测试。

覆盖《下一步执行方案》第 13 节要求的验收情形: 真命题、假命题、缺失前提、
循环证明、矛盾假设、工具未决、陈述偷换、Lean 留洞、重复已有结果、
论文遗漏前提、修改上游假设、恢复/重试。
"""

import tempfile
from pathlib import Path

import pytest

ACCEPTANCE_TEXT = (
    "判断对所有实数 x、y，是否有 x² + y² ≥ 2xy；给出证明和等号条件。"
    "再判断是否可以把 ≥ 换成 >。"
)

_CACHE: dict = {}


def _acceptance_result():
    """运行一次最小闭环示例并缓存 (子进程验证开销较大)。"""
    if "result" not in _CACHE:
        from src.research.loop import ResearchBudget, TheoryEngine
        from src.research.schemas import ResearchSpec
        from src.research.store import ResearchStore

        tmp = Path(tempfile.mkdtemp()) / "acc.sqlite"
        spec = ResearchSpec(project_id="acc", problem_statement=ACCEPTANCE_TEXT)
        store = ResearchStore("acc", db_path=tmp)
        engine = TheoryEngine(spec, store, budget=ResearchBudget(max_actions=20))
        _CACHE["result"] = engine.run()
        _CACHE["engine"] = engine
        _CACHE["store"] = store
    return _CACHE["result"]


# --------------------------------------------------------------------------
# 依赖图 / 存储 / schema
# --------------------------------------------------------------------------
def test_dependency_graph_rejects_cycle_and_propagates_stale():
    from src.research.dependency_graph import CyclicDependencyError, DependencyGraph

    graph = DependencyGraph([("asm", "L1"), ("L1", "L2"), ("L2", "T")])
    assert graph.find_cycle() is None
    assert graph.stale_closure("asm") == {"L1", "L2", "T"}
    assert graph.dependents_of("L1", transitive=True) == {"L2", "T"}
    with pytest.raises(CyclicDependencyError):
        graph.add_edge("T", "asm")
    with pytest.raises(CyclicDependencyError):
        graph.add_edge("L2", "L2")


def test_resume_reuses_persisted_spec(tmp_path, monkeypatch):
    """resume 必须复用已落盘规格, 不重新生成候选 (计划书 §9.1)。"""
    from src import config
    from src.graph import theory_pipeline
    from src.research.store import KIND_SPEC, ResearchStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")

    request = "研究在噪声强度影响下误码率的变化"
    first = theory_pipeline.run_theory_pipeline(request=request, topic="方向",
                                                project_id="res1", problem_id="p1")
    candidates_first = first.get("candidates") or []
    assert candidates_first, "首次运行应生成候选"
    assert first.get("checkpoint_persistent") is True, "检查点应默认持久化"

    # 显式指定 db_path: 默认路径会落到仓库 data/, 污染工作区并遮蔽隔离问题
    store = ResearchStore("res1", db_path=config.DATA_DIR / "research" / "res1.sqlite")
    spec_v1 = store.get(KIND_SPEC, "p1")
    assert spec_v1, "规格应已落盘"

    # 模拟"用户已确认路线"后再 resume
    spec_v1["confirmed"] = True
    spec_v1["selected_candidate_id"] = "cand-fixed"
    spec_v1["candidates"] = [dict(candidates_first[0], candidate_id="cand-fixed")]
    store.put(KIND_SPEC, "p1", spec_v1)

    theory_pipeline.run_theory_pipeline(
        request="完全不相关的另一个方向", topic="另一个主题",
        project_id="res1", problem_id="p1", resume=True)
    spec_after = store.get(KIND_SPEC, "p1")
    assert spec_after["confirmed"] is True, "resume 不得重置已确认状态"
    assert spec_after["selected_candidate_id"] == "cand-fixed"
    assert spec_after["problem_statement"] == spec_v1["problem_statement"], \
        "resume 不得用新输入覆盖已落盘规格"
    store.close()


def test_checkpoint_degradation_is_explicit(monkeypatch):
    """检查点降级必须显式告知, 不能静默冒充可跨进程恢复 (§9.1)。"""
    from langgraph.checkpoint.memory import MemorySaver

    from src.graph import theory_pipeline

    def boom(*_a, **_kw):
        raise RuntimeError("模拟 sqlite 不可用")

    monkeypatch.setattr("langgraph.checkpoint.sqlite.SqliteSaver", boom)
    checkpointer, note = theory_pipeline._get_persistent_checkpointer()
    assert isinstance(checkpointer, MemorySaver)
    assert note and "降级" in note and "无法跨进程恢复" in note


def test_store_versioning_idempotency_and_revision(tmp_path):
    from src.research.store import KIND_CLAIM, ResearchStore, RevisionConflict, VersionConflict

    store = ResearchStore("s1", db_path=tmp_path / "s1.sqlite")
    assert store.put(KIND_CLAIM, "c1", {"v": 1}) == 1
    assert store.put(KIND_CLAIM, "c1", {"v": 2}) == 2
    assert store.get(KIND_CLAIM, "c1")["v"] == 2
    assert store.get(KIND_CLAIM, "c1", version=1)["v"] == 1
    assert len(store.list_versions(KIND_CLAIM, "c1")) == 2
    with pytest.raises(RevisionConflict):
        store.put(KIND_CLAIM, "c1", {"v": 3}, expected_revision=1)
    # 研究历史不可覆盖: 显式指定已存在的历史版本会被拒绝
    with pytest.raises(VersionConflict):
        store.put(KIND_CLAIM, "c1", {"v": 9}, version=1)
    # 同内容重复写入幂等
    assert store.put(KIND_CLAIM, "c1", {"v": 2}) == 2
    assert store.append_event("run_start", {"project_id": "s1", "problem_id": "p1"},
                              idempotency_key="k1") is True
    assert store.append_event("run_start", {"project_id": "s1", "problem_id": "p1"},
                              idempotency_key="k1") is False
    assert len(store.events()) == 1
    store.close()


def test_store_atomic_step_and_tool_ledger(tmp_path):
    """原子研究步骤提交 + 工具运行账本 (计划书 §9.2)。"""
    from src.research.store import (
        KIND_CLAIM,
        KIND_OBLIGATION,
        ResearchStore,
        StepAlreadyApplied,
    )

    store = ResearchStore("s2", db_path=tmp_path / "s2.sqlite")
    versions = store.submit_step(
        writes=[(KIND_CLAIM, "c1", {"v": 1}), (KIND_OBLIGATION, "o1", {"v": 1})],
        events=[("formulated", {"claims": 1, "obligations": 1, "problem_id": "p1"})],
        idempotency_key="step-1")
    assert versions == {"c1": 1, "o1": 1}
    assert len(store.events()) == 1
    # 幂等: 同一步骤不重复执行
    with pytest.raises(StepAlreadyApplied):
        store.submit_step(writes=[(KIND_CLAIM, "c1", {"v": 2})],
                          events=[("formulated", {"claims": 2, "obligations": 2,
                                                  "problem_id": "p1"})],
                          idempotency_key="step-1")
    assert store.get(KIND_CLAIM, "c1")["v"] == 1

    # 工具账本: 待执行登记 → 幂等键命中不再执行 → 结果未知可被识别
    assert store.begin_tool_run("r1", "sympy", "simplify", {"expr": "x"},
                                idempotency_key="t1") is True
    assert store.begin_tool_run("r1", "sympy", "simplify", {"expr": "x"},
                                idempotency_key="t1") is False
    assert [r["run_id"] for r in store.unresolved_tool_runs()] == ["r1"]
    store.finish_tool_run("r1", "passed", {"ok": True})
    assert store.unresolved_tool_runs() == []
    assert store.get_tool_run("r1")["status"] == "passed"
    store.close()


def test_list_latest_exposes_object_ids(tmp_path):
    """`list_latest` 默认只返回业务数据; 取主键必须用 version_index / strip_meta。

    回归: 早期实现两者都拿不到 obj_id, 导致按对象查询 (反馈/派生接口) 永远 404。
    """
    from src.research.store import KIND_OBLIGATION, KIND_SPEC, ResearchStore

    store = ResearchStore("ids", db_path=tmp_path / "ids.sqlite")
    store.put(KIND_SPEC, "prob-1", {"problem_id": "prob-1", "statement": "x"})
    store.put(KIND_OBLIGATION, "obl-1", {"statement": "y"})

    assert store.version_index(KIND_SPEC) == {"prob-1": 1}
    assert "obj_id" not in store.list_latest(KIND_SPEC)[0]
    with_meta = store.list_latest(KIND_OBLIGATION, strip_meta=False)[0]
    assert with_meta["__obj_id__"] == "obl-1"
    assert with_meta["__version__"] == 1
    # 用 version_index 取主键 → 能按 id 取回对象
    for obj_id in store.version_index(KIND_SPEC):
        assert store.get(KIND_SPEC, obj_id)["problem_id"] == "prob-1"
    store.close()


def test_claim_step_scope_cannot_claim_full_assurance():
    from src.research.schemas import Assurance, Claim, ClaimStatus, VerificationScope

    with pytest.raises(ValueError):
        Claim(
            statement="local only",
            status=ClaimStatus.supported,
            assurance=Assurance.symbolic_checked,
            verification_scope=VerificationScope.step,
        )


# --------------------------------------------------------------------------
# 验证适配器
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def runner():
    from src.verification.runner import VerificationRunner

    return VerificationRunner(inproc=True)


def test_sympy_acceptance_behaviour(runner):
    base = dict(lhs="x**2 + y**2", rhs="2*x*y", variables=["x", "y"],
                assumptions={"x": "real", "y": "real"})
    ge = runner.sympy("prove_inequality", {**base, "relation": ">="})
    assert ge.status.value == "passed"
    gt = runner.sympy("prove_inequality", {**base, "relation": ">"})
    assert gt.status.value == "failed"
    assert gt.counterexample == {"x": 0, "y": 0}
    eq = runner.sympy("equality_condition", base)
    assert eq.status.value == "passed"
    assert "x = y" in eq.certificate


def test_sympy_complex_domain_unsupported(runner):
    result = runner.sympy("prove_inequality", dict(
        lhs="x**2 + y**2", rhs="2*x*y", relation=">=", variables=["x", "y"],
        assumptions={"x": "complex", "y": "complex"}))
    assert result.status.value == "unsupported"


def test_sympy_rejects_unsafe_expression(runner):
    result = runner.sympy("simplify", {"expr": "__import__('os').system('echo hi')", "variables": []})
    assert result.status.value == "unsupported"


def test_z3_implication_and_inconsistency(runner):
    ok = runner.z3("check_implication", dict(
        premises=["x >= 1"], conclusion="x >= 0", variables={"x": "Real"}))
    assert ok.status.value == "passed"
    bad = runner.z3("check_implication", dict(
        premises=["x >= 1"], conclusion="x >= 2", variables={"x": "Real"}))
    assert bad.status.value == "failed"
    assert bad.counterexample.get("x") == 1
    incon = runner.z3("check_implication", dict(
        premises=["x >= 1", "x <= 0"], conclusion="x == 5", variables={"x": "Real"}))
    assert incon.status.value == "unknown"
    assert "unsatisfiable" in incon.detail
    sat = runner.z3("check_satisfiable", dict(
        constraints=["x >= 1", "x <= 0"], variables={"x": "Real"}))
    assert sat.status.value == "failed"


def test_lean_rejects_sorry_without_toolchain(runner):
    result = runner.run("lean", "check_declaration", {"source": "theorem t : True := by sorry"})
    assert result.status.value == "failed"
    assert "sorry" in result.detail


# --------------------------------------------------------------------------
# 问题形式化 / 意图 / 新颖性 / 抽取 / 排版
# --------------------------------------------------------------------------
def test_problem_formulator_parses_acceptance():
    from src.research.problem_formulator import parse_questions

    questions = parse_questions(ACCEPTANCE_TEXT)
    assert len(questions) == 2
    assert questions[0].relation.value == ">="
    assert questions[0].lhs == "x**2 + y**2"
    assert questions[0].rhs == "2*x*y"
    assert questions[0].variables == ["x", "y"]
    assert questions[0].variable_domains == {"x": "real", "y": "real"}
    assert questions[0].wants_equality_condition is True
    assert questions[1].relation.value == ">"


def test_problem_formulator_unknown_returns_clarification():
    from src.research.problem_formulator import formulate
    from src.research.schemas import ResearchSpec

    form = formulate(ResearchSpec(project_id="x", problem_statement="帮我研究一下这个东西"))
    assert not form.claims
    assert "questions" in form.unknown_fields


def test_intent_parsing_maps_to_objects():
    from src.research.intent import parse_intent

    context = {"assumptions": {"asm-1": "矩阵 M 可逆"}, "claims": {"clm-1": "上界"}}
    actions = parse_intent("不要假设矩阵可逆", context)
    assert actions[0]["action_type"] == "revise_hypothesis"
    assert actions[0]["object_id"] == "asm-1"
    assert parse_intent("停止当前路线，但保留引理", context)[0]["action_type"] == "stop_with_report"
    assert parse_intent("如果这个命题不成立，给反例", context)[0]["action_type"] == "seek_counterexample"
    assert parse_intent("", context) == []


def test_novelty_known_equivalent_and_unchecked():
    from src.research.novelty import assess
    from src.research.schemas import Claim, NoveltyComparisonRow

    claim = Claim(statement="x^2 + y^2 >= 2*x*y")
    unchecked = assess(claim)
    assert unchecked.status.value == "unchecked"
    assert "尚未发现" in unchecked.bounded_statement
    record = assess(claim, lambda c: [NoveltyComparisonRow(result="AM-GM", difference="等价")])
    assert record.status.value == "known_equivalent"


def test_theorem_extractor_cards():
    from src.rag.theorem_extractor import extract_source_evidence

    text = (
        "Section 2\n"
        "Theorem 2.1: For all real x, x^2 >= 0.\n"
        "Proof. Squares are nonnegative.\n"
        "Lemma 2.2: The sum of nonnegative numbers is nonnegative.\n"
    )
    cards = extract_source_evidence(text, literature_id="lit-1", title="T")
    assert len(cards) == 2
    assert "x^2 >= 0" in cards[0].excerpt
    assert cards[0].location.startswith("line")


def test_theory_render_structured_output():
    from src.rag.theory_render import Block, Manuscript, render_latex, render_markdown

    ms = Manuscript(title="Demo", blocks=[
        Block("heading", "主要结果"),
        Block("theorem", "x^2 \\ge 0", label="thm1", title="非负性"),
        Block("proof", "因为平方非负。"),
    ])
    md = render_markdown(ms)
    assert "定理 1" in md and "证明" in md
    tex = render_latex(ms)
    assert "\\begin{theorem}" in tex and "\\begin{proof}" in tex


# --------------------------------------------------------------------------
# 交付门槛
# --------------------------------------------------------------------------
def test_acceptance_alignment_blocks_statement_swap():
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        SupportKind,
        ValidationStatus,
        VerificationRecord,
        VerificationScope,
    )

    claim = Claim(id="c1", statement="x >= 0", lhs="x", rhs="0", relation=">=",
                  variables=["x"], status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked, verification_scope=VerificationScope.target)
    swapped = VerificationRecord(claim_id="c1", tool="sympy", status="passed",
                                 scope=VerificationScope.target,
                                 validation_status=ValidationStatus.verified,
                                 arguments={"lhs": "x**2", "rhs": "0", "relation": ">=",
                                            "variables": ["x"]})
    gate = theory_validity_gate([claim], [], [swapped], [])
    assert not gate.passed
    assert any("偷换" in r for r in gate.reasons)


def test_acceptance_cycle_and_missing_proof():
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        SupportKind,
        ValidationStatus,
        VerificationScope,
    )

    claim = Claim(id="c1", statement="p", status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked, verification_scope=VerificationScope.target)
    gate = theory_validity_gate([claim], [], [], [("c1", "c1")])
    assert not gate.passed
    assert any("循环" in r or "非法依赖" in r for r in gate.reasons)


def test_delivery_gate_requires_claim_mapping():
    from src.research.acceptance import delivery_gate
    from src.research.schemas import (
        Assurance,
        Claim,
        ClaimStatus,
        Coverage,
        ResearchSnapshot,
        SupportKind,
        ValidationStatus,
        VerificationScope,
    )

    claim = Claim(id="c1", statement="p", status=ClaimStatus.supported,
                  support_kind=SupportKind.symbolic_check, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified,
                  assurance=Assurance.symbolic_checked, verification_scope=VerificationScope.target)
    snapshot = ResearchSnapshot(project_id="p", claims=[claim], writing_map={})
    gate = delivery_gate("x" * 300, snapshot)
    assert not gate.passed
    assert any("未映射" in r for r in gate.reasons)
    snapshot.writing_map = {"c1": "theorem-1"}
    assert delivery_gate("x" * 300, snapshot).passed


# --------------------------------------------------------------------------
# 研究循环端到端
# --------------------------------------------------------------------------
def test_engine_minimal_closed_loop():
    result = _acceptance_result()
    statuses = {(c.relation.value, c.status.value) for c in result.snapshot.claims}
    assert (">=", "supported") in statuses
    assert (">", "refuted") in statuses
    assert result.gate.passed
    # 反例必须满足前提 (x,y 实数) 且违反结论
    ce = [v for v in result.snapshot.verifications if v.counterexample]
    assert ce and ce[0].counterexample == {"x": 0, "y": 0}
    # 真命题的验证等级
    supported = [c for c in result.snapshot.claims if c.status.value == "supported"]
    assert all(c.assurance.value == "symbolic_checked" for c in supported)
    # 决策记录可审查
    assert any(d["action"] == "check_step" for d in result.decisions)


def test_clarify_decision_stops_the_loop_instead_of_repeating(tmp_path):
    """控制器判定"需要澄清"时必须收尾, 不得反复提议澄清直到预算耗尽。

    现场缺陷 (一次人工运行的事件流): 控制器连续 16 轮提议 `clarify_problem`
    (分别指向命题与不同义务), 每轮都被判"有进展", 于是 40 个动作的预算全部烧在
    同一个未完成的澄清步骤上, 界面看起来"卡住"。澄清是**终态判断**, 不是可重复的
    普通动作 —— 一旦决定需要澄清, 就必须把问题交给用户。
    """
    from src.research.loop import ActionType, ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore

    spec = ResearchSpec(project_id="clr", problem_id="p1",
                        problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("clr", db_path=tmp_path / "clr.sqlite")

    def always_clarify(_state):
        return {"action_type": ActionType.clarify_problem.value,
                "object_id": "", "why_now": "研究对象未被指定",
                "uncertainty_reduced": "明确研究问题与范围"}

    engine = TheoryEngine(spec, store=store, llm=object(),
                          budget=ResearchBudget(max_actions=40, max_tool_calls=40))
    engine.proposer = always_clarify
    result = engine.run()

    assert result.needs_clarification is True, "澄清必须是终态"
    assert engine.done is True
    assert result.usage["actions"] <= 2, (
        f"澄清后仍继续循环: 消耗 {result.usage['actions']} 个动作")
    assert result.usage["actions"] < 40, "预算是被澄清烧光的"
    assert any(d["action"] == "clarify_problem" for d in result.decisions)
    assert any("澄清" in n for n in result.notes)
    # 澄清请求不是研究结论: 门槛不得通过
    assert result.gate.passed is False
    store.close()


def test_clarify_by_controller_ends_the_graph_with_a_memo(tmp_path, monkeypatch):
    """端到端: 控制器澄清 → 图收尾 → 交付等级为研究备忘录 (不得报成论文草稿)。"""
    from src import config
    from src.graph import theory_pipeline

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")

    original = theory_pipeline._attach_proposer

    def clarify_proposer(engine, store):
        engine.proposer = lambda _state: {
            "action_type": "clarify_problem", "object_id": "",
            "why_now": "缺少可检验对象", "uncertainty_reduced": "明确研究问题"}

    monkeypatch.setattr(theory_pipeline, "_attach_proposer", clarify_proposer)
    try:
        final = theory_pipeline.run_theory_pipeline(
            request="对所有实数 x: x**2 >= 0", topic="clarify", project_id="clrgraph",
            problem_id="p1", max_actions=20, max_tool_calls=20)
    finally:
        monkeypatch.setattr(theory_pipeline, "_attach_proposer", original)

    assert final.get("needs_clarification") is True
    assert final.get("delivery_level") == "研究备忘录"
    assert final.get("gate_passed") is False
    assert (final.get("usage") or {}).get("actions", 0) <= 3, final.get("usage")


def test_engine_unknown_tool_stays_undecided(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.schemas import VerificationResult, VerificationStatus

    class UnknownRunner:
        def run(self, tool, operation, arguments, timeout=None):
            return VerificationResult(tool=tool, status=VerificationStatus.unknown, detail="求解器超时")

    spec = ResearchSpec(project_id="u1", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("u1", db_path=tmp_path / "u1.sqlite")
    engine = TheoryEngine(spec, store, runner=UnknownRunner(),
                          budget=ResearchBudget(max_actions=10))
    result = engine.run()
    assert all(c.status.value != "supported" for c in result.snapshot.claims)
    assert not result.gate.passed
    assert any("未决" in n for n in result.notes)


def test_engine_missing_premise_stays_blocked(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    # 1/x >= 0 在 x 不排除 0 时无法判定 → 定位到缺口, 不冒充通过
    spec = ResearchSpec(project_id="m1", problem_statement="对所有实数 x: 1/x >= 0")
    store = ResearchStore("m1", db_path=tmp_path / "m1.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    result = engine.run()
    assert not any(c.status.value == "supported" for c in result.snapshot.claims)
    assert any(o.status.value in ("blocked", "refuted") for o in result.snapshot.obligations)


def test_engine_complex_variant_blocked(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="cx1", problem_statement="对所有复数 x、y: x**2 + y**2 >= 2*x*y")
    store = ResearchStore("cx1", db_path=tmp_path / "cx.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    result = engine.run()
    assert not result.gate.passed
    blocked = [c for c in result.snapshot.claims if c.status.value == "blocked"]
    assert blocked


def test_revise_assumption_propagates_staleness(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_ASSUMPTION, KIND_VERIFICATION, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="r1", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("r1", db_path=tmp_path / "r1.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    engine.run()
    asm = store.list_latest(KIND_ASSUMPTION)[0]
    affected = engine.revise_assumption(asm["id"], reason="用户质疑变量域")
    assert affected
    stale = [v for v in store.list_latest(KIND_VERIFICATION) if v.get("stale")]
    assert stale
    assert store.get(KIND_ASSUMPTION, asm["id"])["accepted"] is False


def test_engine_resume_preserves_budget_and_claims(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_CLAIM, KIND_RUNTIME, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="res1", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("res1", db_path=tmp_path / "res1.sqlite")
    runner = VerificationRunner(inproc=True)
    first = TheoryEngine(spec, store, runner=runner, budget=ResearchBudget(max_actions=10)).run()
    claims_after_first = len(store.list_latest(KIND_CLAIM))
    runtime = store.get(KIND_RUNTIME, spec.problem_id)
    assert runtime and runtime["actions"] > 0

    # 重建引擎 (模拟断点恢复): 不应重置预算, 也不重复接纳命题
    resumed = TheoryEngine(spec, store, runner=runner, budget=ResearchBudget(max_actions=10))
    resumed.load_runtime()
    assert resumed._actions == runtime["actions"]
    assert resumed.done is True
    resumed.run()
    assert len(store.list_latest(KIND_CLAIM)) == claims_after_first
    assert first.gate.passed


def test_theory_pipeline_graph_export(tmp_path, monkeypatch):
    from src import config
    from src.graph import theory_pipeline
    from src.research import package as package_mod

    # 隔离到临时目录, 避免污染仓库 data/ 与 outputs/
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")

    exported = {}
    real_export = package_mod.export_package

    def fake_export(snapshot, spec, decisions, md, tex=None, **kwargs):
        kwargs.pop("base_dir", None)
        path = real_export(snapshot, spec, decisions, md, tex,
                           base_dir=tmp_path / snapshot.snapshot_id, **kwargs)
        exported["path"] = path
        return path

    monkeypatch.setattr(theory_pipeline, "export_package", fake_export)
    final = theory_pipeline.run_theory_pipeline(
        request=ACCEPTANCE_TEXT, topic="acceptance", project_id="graph1", problem_id="p9",
    )
    assert final.get("gate_passed") is True
    assert "path" in exported
    assert (exported["path"] / "manuscript.md").exists()
    assert (exported["path"] / "paper.tex").exists()
    assert (exported["path"] / "manifest.json").exists()


# --------------------------------------------------------------------------
# 方向输入 → 候选问题 → 确认 → 单调性研究
# --------------------------------------------------------------------------
def test_parse_direction_extracts_variables():
    from src.research.question_planner import parse_direction

    for text, x, y in [
        ("我想要研究在温度影响下电阻的变化", "温度", "电阻"),
        ("随着温度增大，电阻如何变化", "温度", "电阻"),
        ("温度对电阻的影响", "温度", "电阻"),
        ("how does temperature affect resistance", "temperature", "resistance"),
    ]:
        analysis = parse_direction(text)
        assert analysis.matched
        assert analysis.independent == x
        assert analysis.dependent == y


def test_build_spec_distinguishes_direction_and_problem():
    from src.research.question_planner import build_spec_from_input

    direction_spec = build_spec_from_input("研究在温度影响下电阻如何变化", project_id="d1")
    assert direction_spec.direction
    assert not direction_spec.problem_statement

    problem_spec = build_spec_from_input("对所有实数 x、y: x^2 + y^2 >= 2xy", project_id="d2")
    assert problem_spec.problem_statement
    assert not problem_spec.direction


def test_direction_candidates_require_confirmation(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.question_planner import build_spec_from_input
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = build_spec_from_input("研究在噪声强度影响下误码率的变化", project_id="dc1")
    store = ResearchStore("dc1", db_path=tmp_path / "dc1.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    first = engine.run()
    assert first.needs_confirmation
    assert first.candidates and first.candidates[0]["category"] == "monotonicity"
    assert not first.snapshot.claims  # 未确认前不进入研究

    assert engine.confirm_candidate(0) is True
    second = engine.run()
    # 规则候选缺少显式表达式 → 请求澄清而不是臆造结论
    assert second.needs_clarification
    assert any("显式表达式" in n or "无法可靠形式化" in n for n in second.notes)


def test_llm_direction_candidate_runs_monotonicity(tmp_path):
    from types import SimpleNamespace

    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.question_planner import build_spec_from_input
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    payload = (
        '[{"statement":"电阻关于温度单调非减","category":"monotonicity",'
        '"expr":"x**2","wrt":"x","direction":"nondecreasing","dependent":"电阻",'
        '"independent":"温度","variables":["x"],"variable_domains":{"x":"positive"},'
        '"known_results":"未知","difference":"待比较","verifiability":"可符号核验",'
        '"difficulty":"低","recommended":true,"rationale":"示例"}]'
    )
    fake_llm = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=payload))
    spec = build_spec_from_input("研究在温度影响下电阻的变化", project_id="dc2")
    store = ResearchStore("dc2", db_path=tmp_path / "dc2.sqlite")
    engine = TheoryEngine(spec, store, llm=fake_llm, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=10))
    assert engine.run().needs_confirmation
    assert engine.confirm_candidate(0)
    result = engine.run()
    supported = [c for c in result.snapshot.claims if c.status.value == "supported"]
    assert supported and supported[0].direction == "nondecreasing"
    assert result.gate.passed


def test_monotonicity_supported_and_refuted(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ClaimQuestion, ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    def run(pid, question):
        spec = ResearchSpec(project_id=pid, questions=[question], confirmed=True)
        store = ResearchStore(pid, db_path=tmp_path / f"{pid}.sqlite")
        return TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                            budget=ResearchBudget(max_actions=10)).run()

    good = ClaimQuestion(statement="x² 关于 x 单调非减 (x>0)", category="monotonicity",
                         expr="x**2", wrt="x", direction="nondecreasing",
                         variables=["x"], variable_domains={"x": "positive"})
    result = run("mono-ok", good)
    assert any(c.status.value == "supported" for c in result.snapshot.claims)
    assert result.gate.passed

    bad = ClaimQuestion(statement="-x 关于 x 单调非减", category="monotonicity",
                        expr="-x", wrt="x", direction="nondecreasing",
                        variables=["x"], variable_domains={"x": "real"})
    result2 = run("mono-bad", bad)
    assert any(c.status.value == "refuted" for c in result2.snapshot.claims)


# --------------------------------------------------------------------------
# 因果影响分析 (应用型) 垂直切片
# --------------------------------------------------------------------------
def _did_rows(true_effect: float):
    import random

    random.seed(7)
    rows = []
    for _ in range(25):
        rows.append({"g": "C", "t": "pre", "y": 10 + random.gauss(0, 0.5)})
        rows.append({"g": "C", "t": "post", "y": 11 + random.gauss(0, 0.5)})
        rows.append({"g": "T", "t": "pre", "y": 10 + random.gauss(0, 0.5)})
        rows.append({"g": "T", "t": "post", "y": 11 + true_effect + random.gauss(0, 0.5)})
    return rows


def _did_study(rows, **overrides):
    from src.research.schemas import StudyDesign, StudyPlan

    base = dict(
        design=StudyDesign.did, treatment="技术A", outcome="行业产出",
        population="制造业", region="华东", period="2015-2023",
        counterfactual="未采用技术A", confounders=["企业规模", "地区经济"],
        confounder_handling="DiD 平行趋势 + 地区固定效应",
        # 计划书 §7.4: 因果结论需独立声明识别假设/设计可行性/测量与缺失/误差结构
        identification_assumptions=["平行趋势", "无预期效应", "SUTVA 无干扰"],
        design_feasibility="存在受处理组与对照组、政策前后各 4 期观测",
        measurement_notes="产出按不变价增加值计量, 单位: 亿元",
        missing_data_handling="缺失率 <1%, 按企业-年度插值 (近似 MAR)",
        error_structure="按地区聚类稳健标准误",
        data_source_kind="real",
        group_col="g", time_col="t", outcome_col="y",
        treated_label="T", control_label="C", pre_label="pre", post_label="post",
        rows=rows,
    )
    base.update(overrides)
    return StudyPlan(**base)


def test_stats_did_and_mean_difference(runner):
    did = runner.run("stats", "difference_in_differences", {
        "rows": _did_rows(2.0), "group_col": "g", "time_col": "t", "outcome_col": "y",
        "treated_label": "T", "control_label": "C", "pre_label": "pre", "post_label": "post"})
    assert did.status.value == "passed"
    assert abs(did.values["estimate"] - 2.0) < 0.5
    assert did.values["ci_low"] < 2.0 < did.values["ci_high"]

    md = runner.run("stats", "mean_difference", {
        "rows": _did_rows(2.0), "group_col": "t", "outcome_col": "y",
        "treated_label": "post", "control_label": "pre"})
    assert md.status.value == "passed"
    assert "estimate" in md.values

    no_data = runner.run("stats", "difference_in_differences", {"group_col": "g"})
    assert no_data.status.value == "unsupported"


def _causal_engine(tmp_path, pid, study):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ClaimQuestion, ClaimType, ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    question = ClaimQuestion(statement="技术A对行业产出的影响", category="applied",
                             claim_type=ClaimType.causal, study=study, recommended=True)
    spec = ResearchSpec(project_id=pid, questions=[question], confirmed=True)
    store = ResearchStore(pid, db_path=tmp_path / f"{pid}.sqlite")
    return TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                        budget=ResearchBudget(max_actions=20)).run()


def test_causal_claim_with_data_passes_gate(tmp_path):
    result = _causal_engine(tmp_path, "causal-ok", _did_study(_did_rows(2.0)))
    supported = [c for c in result.snapshot.claims if c.status.value == "supported"]
    assert supported and supported[0].claim_type.value == "causal"
    assert supported[0].assurance.value == "empirical_estimated"
    assert result.gate.passed, result.gate.render()
    # 结论携带不确定性区间
    assert supported[0].effect_estimate.get("ci_low") is not None
    # 写稿应包含效应量与 CI
    from src.agents.theory_writer import run_theory_writing

    md, writing_map = run_theory_writing(result.snapshot, "技术A影响")
    assert "95% CI" in md and supported[0].id in writing_map


def test_causal_claim_without_data_stays_undecided(tmp_path):
    result = _causal_engine(tmp_path, "causal-nodata", _did_study([]))
    assert not any(c.status.value == "supported" for c in result.snapshot.claims)
    assert not result.gate.passed


def test_causal_claim_null_effect_not_supported(tmp_path):
    # 真实效应为 0 → CI 包含 0 → 不得声称因果成立
    result = _causal_engine(tmp_path, "causal-null", _did_study(_did_rows(0.0)))
    assert not any(c.status.value == "supported" for c in result.snapshot.claims)


def test_causal_gate_blocks_missing_scope_and_confounders(tmp_path):
    study = _did_study(_did_rows(2.0), confounders=[], confounder_handling="",
                       population="", region="", period="")
    # 缺少范围/混淆声明时, 规则义务无法关闭 → 门槛不通过
    result = _causal_engine(tmp_path, "causal-scope", study)
    assert not result.gate.passed
    kinds = {o.kind: o.status.value for o in result.snapshot.obligations}
    assert kinds.get("scope_check") == "blocked"
    assert kinds.get("control_confound") == "blocked"


def test_causal_requires_identification_declarations(tmp_path):
    """计划书 §7.4: 声明设计 + 填了混淆说明 + 区间不跨零 不足以建立因果结论。

    识别假设 / 设计可行性 / 测量与缺失 / 误差结构必须各自成为独立义务,
    缺任一项都不得把结论升级为 supported。
    """
    # measurement_and_missing 义务要求"测量方案"与"缺失机制"同时声明, 二者缺一即 blocked
    cases = [
        ("identification_assumptions", {"identification_assumptions": []}),
        ("design_feasibility", {"design_feasibility": ""}),
        ("measurement_and_missing", {"measurement_notes": ""}),
        ("measurement_and_missing", {"missing_data_handling": ""}),
        ("error_structure", {"error_structure": ""}),
    ]
    for index, (kind, overrides) in enumerate(cases):
        study = _did_study(_did_rows(2.0), **overrides)
        result = _causal_engine(tmp_path, f"causal-miss{index}", study)
        assert not any(c.status.value == "supported" for c in result.snapshot.claims), overrides
        kinds = {o.kind: o.status.value for o in result.snapshot.obligations}
        assert kinds.get(kind) == "blocked", (overrides, kinds)
        assert not result.gate.passed, overrides


def test_causal_gate_rejects_placeholder_data(tmp_path):
    """§7.4: 不得用占位/合成数据产出'现实因果结论'。"""
    study = _did_study(_did_rows(2.0), data_source_kind="placeholder",
                       data_source_note="演示用随机数据")
    result = _causal_engine(tmp_path, "causal-placeholder", study)
    assert not result.gate.passed
    assert any("placeholder" in r or "占位" in r or "演示" in r for r in result.gate.reasons), \
        result.gate.render()


def test_causal_declarations_are_independent_obligations(tmp_path):
    """四项声明义务必须真实出现在义务表里 (而不是只写在门槛里)。"""
    result = _causal_engine(tmp_path, "causal-kinds", _did_study(_did_rows(2.0)))
    kinds = {o.kind for o in result.snapshot.obligations}
    for expected in ("identification_assumptions", "design_feasibility",
                     "measurement_and_missing", "error_structure"):
        assert expected in kinds, (expected, kinds)


def test_effect_direction_and_contract_decide_candidates():
    from src.research.question_planner import parse_effect_direction

    for text, t, o in [
        ("研究某AI技术应用导致制造业就业的变化", "某AI技术应用", "制造业就业"),
        ("某技术对金融行业的影响", "某技术", "金融行业"),
        ("impact of automation on employment", "automation", "employment"),
    ]:
        analysis = parse_effect_direction(text)
        assert analysis.matched
        assert analysis.treatment == t
        assert analysis.outcome == o

    from src.research.question_planner import build_spec_from_input, generate_candidates
    from src.research.schemas import SourceSummary

    # P0-2: 没声明数据 → 不做经验因果估计, 只给条件性情景候选
    without_data = build_spec_from_input("研究某AI技术应用导致制造业就业的变化",
                                        project_id="ap1")
    assert without_data.contract.task_kind.value == "scenario"
    scenario = generate_candidates(without_data)
    assert scenario and scenario[0].claim_type.value == "scenario"

    # 声明了观测数据 → 才产出因果候选, 且不编造数据行
    with_data = build_spec_from_input("研究某AI技术应用导致制造业就业的变化",
                                      project_id="ap2",
                                      source_summary=SourceSummary(has_observational_data=True))
    causal = generate_candidates(with_data)
    assert causal and causal[0].claim_type.value == "causal"
    assert causal[0].category == "applied"
    assert causal[0].study.treatment
    assert not causal[0].study.rows and not causal[0].study.data_ref


def test_evidence_tiering_contradiction_and_grade():
    from src.research.evidence import (
        classify_source,
        detect_contradictions,
        grade_evidence,
        independent_supporters,
    )
    from src.research.schemas import EvidenceGrade, SourceKind, SupportKindOfEvidence

    journal = classify_source({"title": "AI and manufacturing output",
                               "doi": "10.1/x", "venue": "Journal of Economics",
                               "abstract": "We find a 5% increase in output."})
    assert journal.source_kind == SourceKind.journal
    assert journal.peer_reviewed and journal.credibility.value == "high"
    # 命中只代表"来源存在", 不建立支持关系 (计划书 §6.2)
    assert journal.content_supports is False
    assert journal.support == SupportKindOfEvidence.insufficient

    preprint = classify_source({"title": "AI and jobs", "venue": "arXiv preprint",
                                "abstract": "effects unclear"})
    assert preprint.source_kind == SourceKind.preprint
    assert not preprint.peer_reviewed

    pos = classify_source({"title": "AI raises output", "doi": "10.2/a",
                           "abstract": "increase growth"})
    neg = classify_source({"title": "AI lowers output", "doi": "10.3/b",
                           "abstract": "we find a decrease in output"})
    # 支持关系必须显式判定, 且方向由命题决定
    pos.support = SupportKindOfEvidence.supports
    neg.support = SupportKindOfEvidence.supports
    detect_contradictions([pos, neg])
    assert pos.contradicts and neg.contradicts

    assert grade_evidence([]) == EvidenceGrade.unsupported
    # 未判定支持关系时不得升级
    unjudged = [classify_source({"title": "AI raises output", "doi": "10.9/z"})]
    assert grade_evidence(unjudged) == EvidenceGrade.anecdotal
    assert grade_evidence([pos]) == EvidenceGrade.single_source
    assert grade_evidence([pos, neg]) == EvidenceGrade.converging
    # 多篇转引同一原始记录只算一个独立来源
    dup = classify_source({"title": "AI raises output (preprint)",
                           "original_record_hash": pos.independence_key})
    dup.support = SupportKindOfEvidence.supports
    assert len(independent_supporters([pos, dup])) == 1
    assert grade_evidence([pos, dup]) == EvidenceGrade.single_source


def test_gather_evidence_and_attach(tmp_path):
    from src.research.evidence import assess_support, gather_evidence
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import Claim, ClaimType, ResearchSpec, StudyPlan
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    def fake_search(query, limit):
        return [{"title": "技术A 对行业产出的影响", "doi": "10.9/z",
                 "venue": "Journal X", "abstract": "increase in 行业产出"}]

    claim = Claim(statement="技术A影响行业产出", claim_type=ClaimType.causal,
                  study=StudyPlan(treatment="技术A", outcome="行业产出"))
    evidence = gather_evidence(claim, search_fn=fake_search)
    # 规则判定可以给出部分/支持关系, 但必须带判定理由与可定位来源
    assert evidence
    assert evidence[0].excerpt
    assert evidence[0].support_reason
    assert evidence[0].original_record_hash

    from src.research.store import KIND_CLAIM

    spec = ResearchSpec(project_id="ev1")
    store = ResearchStore("ev1", db_path=tmp_path / "ev1.sqlite")
    store.put(KIND_CLAIM, claim.id, claim.model_dump(mode="json"))
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=5))
    # 不判定支持关系时不得升级证据分级 (召回 ≠ 支持)
    ids = engine.attach_evidence(claim.id, evidence, judge=False)
    assert ids
    assert engine._get_claim(claim.id).evidence_grade.value == "unsupported"
    # 显式判定后才允许升级
    engine.attach_evidence(claim.id, [assess_support(claim, evidence[0])], judge=True)
    assert engine._get_claim(claim.id).evidence_grade.value in ("single_source", "converging")


def test_server_startrequest_theory_fields():
    from src.server import StartRequest, build_theory_initial_state

    req = StartRequest(request="对所有实数 x: x**2 >= 0", mode="theory", project_id="pj")
    state = build_theory_initial_state(req)
    assert state["mode"] == "theory"
    assert state["project_id"] == "pj"
    assert state["budget_max_actions"] == 40
    # 统一入口 (合并计划 §3 / M5): 留空不再等于"综述", 由服务端按默认引擎决定
    from src.server import DEFAULT_ENGINE

    assert StartRequest(topic="t").mode == ""
    assert DEFAULT_ENGINE == "theory"
