from __future__ import annotations

"""问题类型能力矩阵与领域模型选择的失败用例 (计划书 §5.2 / §7.2)。

反向安全测试:
- 未登记的问题类型不得被当作"可符号核验"的命题 (fail-closed);
- 需要数据/设计的类型在缺前置条件时必须请求澄清, 而不是给出结论;
- 规范性结论不得被系统自动判定成立;
- 选中模型是**显式动作**: 必须写 `selected` 与具体版本, 且不改变结论状态。
"""


import pytest


# --------------------------------------------------------------------------
# 能力声明
# --------------------------------------------------------------------------
def test_unknown_category_is_not_downgraded_to_definitional():
    from src.research.capability import claim_type_for, declare_capability
    from src.research.schemas import ClaimType

    decl = declare_capability("quantum-vibes")
    assert decl.known is False
    assert decl.action == "clarify"
    assert decl.questions, "未知类型必须给出澄清问题"
    # 未登记类型按"需独立审查"处理, 绝不退化成可符号核验
    assert claim_type_for("quantum-vibes") == ClaimType.normative
    assert claim_type_for("") == ClaimType.normative


def test_causal_and_predictive_require_data_and_design():
    from src.research.capability import declare_capability

    for category in ("causal", "associational", "predictive", "descriptive", "scenario"):
        missing = declare_capability(category, available={"stats": True})
        assert missing.action == "clarify", category
        assert missing.questions, category

        ready = declare_capability(category, available={"stats": True},
                                   has_data=True, has_design=True)
        assert ready.action == "proceed", category

    # 只有数据没有设计仍然不够 (无法区分识别条件)
    partial = declare_capability("causal", available={"stats": True}, has_data=True)
    assert partial.action == "clarify"


def test_missing_backend_is_reported_not_ignored():
    from src.research.capability import declare_capability

    decl = declare_capability("inequality", available={})
    # inequality 需要符号/求解器后端; 全部不可用时必须显式说明
    assert decl.required_backends
    assert decl.missing_backends
    assert any("后端" in r for r in decl.reasons)


def test_normative_claim_never_self_certified():
    from src.research.capability import declare_capability
    from src.research.schemas import ClaimType

    decl = declare_capability("normative", available={"stats": True},
                              has_data=True, has_design=True)
    assert decl.claim_type == ClaimType.normative
    assert decl.action == "clarify", "规范性结论必须由独立审查确认"
    assert decl.questions


def test_category_mapping_matches_claim_types():
    from src.research.capability import CATEGORY_TO_CLAIM_TYPE, claim_type_for
    from src.research.schemas import ClaimType

    assert claim_type_for("causal") == ClaimType.causal
    assert claim_type_for("applied") == ClaimType.causal
    assert claim_type_for("predictive") == ClaimType.predictive
    assert claim_type_for("descriptive") == ClaimType.descriptive
    assert claim_type_for("monotonicity") == ClaimType.definitional
    assert CATEGORY_TO_CLAIM_TYPE["associational"] == ClaimType.associational


def test_non_definitional_claim_gets_data_and_scope_obligations():
    """描述/预测/规范类命题不得"没有义务" (那等于无需证据即可交付)。"""
    from src.research.problem_formulator import _obligations_for
    from src.research.schemas import Claim, ClaimType

    for claim_type in (ClaimType.descriptive, ClaimType.predictive,
                       ClaimType.scenario, ClaimType.normative,
                       ClaimType.associational):
        claim = Claim(statement="某类结论", claim_type=claim_type)
        obligations = _obligations_for(claim, False, {"stats": True, "sympy": True})
        kinds = {o.kind for o in obligations}
        assert obligations, claim_type
        assert "scope_check" in kinds, claim_type
        assert "evidence_support" in kinds, claim_type
        # 非定义型命题不得只给出"核验不等式"的义务
        assert "prove_inequality" not in kinds, claim_type


def test_engine_bootstrap_declares_capability(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="cap1", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("cap1", db_path=tmp_path / "cap1.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=8))
    assert engine.bootstrap()
    events = [e for e in store.events() if e["type"] == "capability_declared"]
    assert events, "能力声明必须写入事件流 (可审计)"
    record = events[-1]["payload"]
    assert record["category"] in ("inequality", "identity", "monotonicity", "definitional")
    assert record["action"] in ("proceed", "clarify")
    store.close()


# --------------------------------------------------------------------------
# 领域模型选择
# --------------------------------------------------------------------------
def _fake_model(model_id="mdl-1", *, version=1, refs=2, complete=True):
    from src.research.schemas import ResearchModel

    return ResearchModel(
        id=model_id, version=version, name=f"模型 {model_id}",
        natural_language="自然语言描述" if complete else "",
        formal_encoding="x >= 0" if complete else "",
        fidelity="忠实度说明" if complete else "",
        boundaries="适用边界" if complete else "",
        source_refs=[f"ev-{i}" for i in range(refs)],
    )


def test_select_model_writes_selected_and_version_only():
    from src.research.capability import select_model
    from src.research.schemas import Claim, ClaimStatus

    claim = Claim(statement="x^2 >= 0")
    weak = _fake_model("mdl-weak", refs=1)
    strong = _fake_model("mdl-strong", refs=3)
    before = claim.status
    outcome = select_model(None, claim, [weak, strong])

    assert outcome["ok"] is True
    assert outcome["selected"] == "mdl-strong", "应选来源最完整的候选"
    assert outcome["version"] == 1
    assert strong.selected is True
    assert weak.selected is False, "其余候选必须取消选中"
    assert claim.model_ref is not None and claim.model_ref.id == "mdl-strong"
    # 选中模型不得改变结论状态
    assert claim.status == before == ClaimStatus.proposed


def test_select_model_without_candidates_fails_loudly():
    from src.research.capability import select_model
    from src.research.schemas import Claim

    outcome = select_model(None, Claim(statement="p"), [])
    assert outcome["ok"] is False
    assert "reason" in outcome
    assert outcome.get("selected", "") == ""


def test_engine_propose_model_marks_selected(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import (
        ActionType,
        ResearchAction,
        ResearchSpec,
        SourceEvidence,
    )
    from src.research.store import KIND_EVIDENCE, KIND_MODEL, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="mds", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("mds", db_path=tmp_path / "mds.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=8))
    assert engine.bootstrap()
    claim = engine._claims()[0]
    # P0-3: 竞争机制来自结构不同的**带定位原文结果**, 而不是套两个模板
    for item in (
        SourceEvidence(id="ev-1", title="文献 A", location="p.1", source_id="doc-1",
                       excerpt="噪声使特征分布重叠, 可分性下降", claim_id=claim.id,
                       source_kind="journal"),
        SourceEvidence(id="ev-2", title="文献 B", location="p.7", source_id="doc-2",
                       excerpt="信道均衡通过抑制漂移使可分性提升", claim_id=claim.id,
                       source_kind="journal"),
    ):
        store.put(KIND_EVIDENCE, item.id, item.model_dump(mode="json"))

    ok = engine._act_propose_model(ResearchAction(action_type=ActionType.propose_model,
                                                  object_id=claim.id))
    assert ok is True
    models = store.list_latest(KIND_MODEL)
    assert len(models) >= 2, models
    selected = [m for m in models if m["selected"]]
    assert len(selected) == 1, "必须恰好选中一个候选"
    assert selected[0]["version"] == 1

    selection = engine.model_selection(claim.id)
    # list_latest 的顺序不保证, 因此按"恰好一个被选中"判断, 而不是看第一个
    assert sum(1 for m in selection["models"] if m["selected"]) == 1
    assert selection["claims"][claim.id]["model_ref"] == {
        "id": selected[0]["id"], "version": 1}
    assert selection["claims"][claim.id]["selected_state"] == "selected"
    store.close()


def test_definitional_claim_does_not_report_missing_model(tmp_path):
    """纯形式化命题不需要领域模型: 不得虚报"缺模型选择" (避免噪声告警)。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ClaimType, ResearchSpec
    from src.research.store import KIND_CLAIM, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="nomdl", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("nomdl", db_path=tmp_path / "nomdl.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=8))
    assert engine.bootstrap()
    claim = engine._claims()[0]
    assert claim.claim_type == ClaimType.definitional
    entry = engine.model_selection(claim.id)["claims"][claim.id]
    assert entry["model_required"] is False
    assert entry["selected_state"] == "not_required"

    # 应用/数据类命题才需要模型: 缺模型必须显式标为 missing_selection
    causal = claim.model_copy(update={"claim_type": ClaimType.causal})
    store.put(KIND_CLAIM, causal.id, causal.model_dump(mode="json"))
    entry2 = engine.model_selection(causal.id)["claims"][causal.id]
    assert entry2["model_required"] is True
    assert entry2["selected_state"] == "missing_selection"
    store.close()


def test_engine_select_model_persists_and_audits(tmp_path):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import (
        ActionType,
        Origin,
        ResearchAction,
        ResearchSpec,
        SourceEvidence,
    )
    from src.research.store import KIND_EVIDENCE, KIND_MODEL, ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id="mds2", problem_statement="对所有实数 x: x**2 >= 0")
    store = ResearchStore("mds2", db_path=tmp_path / "mds2.sqlite")
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=8))
    assert engine.bootstrap()
    claim = engine._claims()[0]
    for i in (1, 2):
        store.put(KIND_EVIDENCE, f"ev-{i}",
                  SourceEvidence(id=f"ev-{i}", source_ref=f"doc-{i}", locator="p1",
                                 excerpt=f"材料 {i}", claim_id=claim.id,
                                 origin=Origin.external).model_dump(mode="json"))
    engine._act_propose_model(ResearchAction(action_type=ActionType.propose_model,
                                             object_id=claim.id))
    first = store.list_latest(KIND_MODEL)[0]["id"]

    # 手工再加一个候选, 显式选中它: 第一个必须被取消选中
    from src.research.schemas import ResearchModel
    rival = ResearchModel(id="mdl-rival", name="竞争模型", natural_language="nl",
                          formal_encoding="x >= 0")
    store.put(KIND_MODEL, rival.id, rival.model_dump(mode="json"))

    outcome = engine.select_model_for_claim(claim.id, "mdl-rival")
    assert outcome["ok"] is True
    assert outcome["selected"] == "mdl-rival"
    latest = {m["id"]: m for m in store.list_latest(KIND_MODEL)}
    assert latest["mdl-rival"]["selected"] is True
    assert latest[first]["selected"] is False, "同一命题下只能有一个被选中模型"
    assert any(e["type"] == "model_selected" for e in store.events())
    store.close()


@pytest.mark.parametrize("category,expected", [
    ("causal", "因果识别模型"), ("predictive", "预测模型"),
    ("scenario", "情景/仿真模型"), ("normative", "规范论证模型"),
    ("descriptive", "描述模型"), ("associational", "关联模型"),
])
def test_candidate_scheme_per_claim_type(category, expected):
    from src.research.capability import candidate_scheme, claim_type_for
    from src.research.schemas import Claim

    claim = Claim(statement="p", claim_type=claim_type_for(category))
    name, mechanism, fidelity = candidate_scheme(claim)
    assert name == expected
    assert mechanism and fidelity
