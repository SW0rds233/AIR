from __future__ import annotations

"""结构化动作提议器的失败用例 (计划书 §3 R1)。

计划书指出的缺陷: 协调器已接受 `proposer`, 但正式图创建 `TheoryEngine` 时**没有
注入** proposer, 于是真实路径仍按缺口规则与固定打分选动作。本文件既测提议器本身
的校验, 也测**图入口是否真的在用提议** (这是计划书明确的验收方式)。

约定: 测试默认关闭提议器 (`conftest` 设 `THEORY_PROPOSER=0`), 因此这里显式打开
并注入假 LLM —— 否则测试会真实调用模型 API。
"""

import json
from types import SimpleNamespace


def _proposal(**over) -> str:
    data = {
        "action_type": "retrieve_targeted",
        "object_id": "",
        "target_gap": "missing_evidence",
        "why_now": "当前缺证据",
        "uncertainty_reduced": "文献中是否已有该结论",
        "on_failure": "改用反例检索并记录失败原因",
    }
    data.update(over)
    return json.dumps(data, ensure_ascii=False)


def _fake_llm(proposal_text: str, *, seen: list | None = None):
    """假 LLM: 只对"动作规划"提示词返回提议, 其余返回空 JSON (避免影响其他判定)。"""
    def _invoke(messages, *args, **kwargs):
        text = " ".join(str(getattr(m, "content", m)) for m in messages)
        if seen is not None:
            seen.append(text)
        if "动作规划助手" in text:
            return SimpleNamespace(content=proposal_text, response_metadata={},
                                   usage_metadata=None)
        return SimpleNamespace(content="{}", response_metadata={}, usage_metadata=None)
    return SimpleNamespace(invoke=_invoke)


# --------------------------------------------------------------------------
# 解析与校验
# --------------------------------------------------------------------------
def test_valid_proposal_requires_structured_fields():
    from src.research.proposal import parse_proposal

    good = parse_proposal(_proposal(), allowed_actions=["retrieve_targeted"])
    assert good.ok is True
    assert good.action_type == "retrieve_targeted"
    assert good.uncertainty_reduced and good.on_failure and good.why_now
    assert "提议 retrieve_targeted" in good.describe()

    # 缺任一结构化字段 → 无效
    for missing in ("why_now", "uncertainty_reduced", "on_failure"):
        data = json.loads(_proposal())
        data.pop(missing)
        bad = parse_proposal(json.dumps(data, ensure_ascii=False),
                             allowed_actions=["retrieve_targeted"])
        assert bad.ok is False, missing
        assert any(missing in r for r in bad.rejected)


def test_proposal_rejects_unknown_action_and_verdict_claims():
    from src.research.proposal import parse_proposal

    unknown = parse_proposal(_proposal(action_type="hack_the_planet"),
                            allowed_actions=["retrieve_targeted"])
    assert unknown.ok is False
    assert any("不在当前可派发清单内" in r for r in unknown.rejected)

    # 提议不得替验证器宣布结论
    verdict = parse_proposal(_proposal(why_now="该命题已被证明, 所以跳过"),
                             allowed_actions=["retrieve_targeted"])
    assert verdict.ok is False
    assert any("结论性断言" in r for r in verdict.rejected)

    assert parse_proposal("我觉得应该先检索一下").ok is False
    assert parse_proposal("").ok is False


def test_proposal_version_must_match_current():
    from src.research.proposal import parse_proposal

    known = {"clm-1": 3, "obl-1": 1}
    ok = parse_proposal(_proposal(action_type="check_step", object_id="obl-1@v1"),
                        allowed_actions=["check_step"], known_objects=known)
    assert ok.ok is True and ok.object_id == "obl-1" and ok.object_version == 1

    stale = parse_proposal(_proposal(action_type="check_step", object_id="clm-1@v2"),
                           allowed_actions=["check_step"], known_objects=known)
    assert stale.ok is False
    assert any("版本过期" in r for r in stale.rejected)

    foreign = parse_proposal(_proposal(action_type="check_step", object_id="clm-999@v1"),
                             allowed_actions=["check_step"], known_objects=known)
    assert foreign.ok is False
    assert any("不在当前研究范围内" in r for r in foreign.rejected)


def test_proposal_prompt_carries_required_context():
    """提示词必须包含可派发动作、可引用对象与上下文 (含冲突/失败路线)。"""
    from src.research.proposal import build_proposal_prompt

    prompt, scan = build_proposal_prompt(
        "# 研究目标\nX\n# 失败档案\n- 已失败路线",
        available_actions=["retrieve_targeted", "check_step"],
        known_objects={"clm-1": 2})
    assert "retrieve_targeted" in prompt and "check_step" in prompt
    assert "clm-1@v2" in prompt
    assert "失败档案" in prompt
    assert scan == ""
    # 上下文按外部资料定界
    assert "<<<EXTERNAL_DATA_BEGIN>>>" in prompt


def test_proposal_skips_llm_when_unavailable_but_falls_back_stably():
    """停用 LLM 时必须稳定降级为确定性排序, 且不产生任何动作。"""
    from src.research.proposal import make_proposer

    assert make_proposer(None)({"state": {}}) is None

    calls = {"n": 0}
    sink_events: list[dict] = []

    class Counting(SimpleNamespace):
        def invoke(self, *a, **k):
            calls["n"] += 1
            return SimpleNamespace(content=_proposal(), response_metadata={},
                                   usage_metadata=None)

    proposer = make_proposer(Counting(), event_sink=sink_events.append, max_calls=1)
    state = {"available_actions": ["retrieve_targeted"], "unresolved_claims": []}
    assert proposer(state) is not None
    assert proposer(state) is None, "超过上限后必须退回确定性排序"
    assert calls["n"] == 1
    assert any(e["type"] == "proposal_skipped" for e in sink_events)


def test_proposer_reports_llm_failure_instead_of_hiding_it():
    from src.research.proposal import make_proposer

    class Boom:
        def invoke(self, *a, **k):
            raise RuntimeError("402 Insufficient Balance")

    events: list[dict] = []
    proposer = make_proposer(Boom(), event_sink=events.append)
    assert proposer({"state": {"available_actions": ["retrieve_targeted"]}}) is None
    assert any(e["type"] == "proposal_failed" and "402" in e["reason"] for e in events)


def test_coordinator_rejects_stale_object_version_proposal():
    """协调者层面的版本校验: 基于过期对象的提议被拒并记录理由。

    这里刻意让被拒提议的动作在回退排序里不可用 (`check_step` 需要已注册的前置
    条件), 以便同时确认"拒绝后确实换了别的动作"。
    """
    from src.research.coordinator import decide

    state = {
        "budget_remaining": 10,
        "open_obligations": [{"id": "obl-1", "version": 2, "status": "open",
                              "kind": "prove_inequality", "claim_id": "clm-1"}],
        "unresolved_claims": [{"id": "clm-1", "version": 2}],
        "unplanned_claims": [], "pending_novelty": [], "undetermined_claims": [],
        "retryable_claims": [], "questions": [], "knowledge_available": False,
        "gaps": [], "dismissed_actions": [],
    }
    stale = decide(state, proposal={
        "action_type": "check_step", "object_id": "obl-1@v1",
        "why_now": "关闭义务", "uncertainty_reduced": "义务是否成立",
        "on_failure": "换路线"})
    assert any("版本过期" in r for r in state["rejected_proposals"])
    assert any(not a.get("accepted") for a in state["proposal_audit"])
    # 被拒后不得使用那个(过期)对象
    assert stale.object_id != "obl-1" or stale.action_type.value != "check_step"

    # 同一提议用当前版本 → 被采纳
    state2 = dict(state, rejected_proposals=[], proposal_audit=[])
    ok = decide(state2, proposal={
        "action_type": "check_step", "object_id": "obl-1@v2",
        "why_now": "关闭义务", "uncertainty_reduced": "义务是否成立",
        "on_failure": "换路线"})
    assert ok.action_type.value == "check_step"
    assert ok.object_id == "obl-1"
    assert ok.input_versions.get("obl-1") == 2
    assert any(a.get("accepted") for a in state2["proposal_audit"])


# --------------------------------------------------------------------------
# 图入口: 正式路径必须真的使用提议
# --------------------------------------------------------------------------
ACCEPTANCE_TEXT = "判断对所有实数 x: x**2 >= 0。"


def _run_graph(tmp_path, monkeypatch, proposal_text, pid):
    from src import config
    from src.graph import theory_pipeline

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_PROPOSER", "1")
    seen: list[str] = []
    monkeypatch.setattr(theory_pipeline, "_role_llm",
                        lambda: _fake_llm(proposal_text, seen=seen))
    final = theory_pipeline.run_theory_pipeline(
        request=ACCEPTANCE_TEXT, topic="prop", project_id=pid, problem_id="p1")
    return final, seen


def _claim_ref(pid: str) -> str:
    """取命题 id@version (供需要构造合法提议的用例引用当前对象)。"""
    from src.research.store import ResearchStore

    store = ResearchStore(pid)
    claims = store.list_latest("claim")
    store.close()
    return f"{claims[0]['id']}@v{claims[0].get('version', 1)}" if claims else ""


def test_graph_entry_uses_proposer_and_logs_decisions(tmp_path, monkeypatch):
    """计划书 R1 验收: 正式图入口实际使用结构化提议, 且提议结果进研究日志。"""
    from src.research.store import ResearchStore

    final, seen = _run_graph(tmp_path, monkeypatch, _proposal(), "propA")
    assert final.get("gate_passed") is not None
    assert seen, "正式路径必须调用提议器 (存在动作规划提示词)"

    store = ResearchStore("propA")
    events = store.events()
    store.close()
    kinds = [e["type"] for e in events]
    assert any(k.startswith("proposal") for k in kinds), \
        f"提议结果必须进研究日志: {sorted(set(kinds))}"
    # 提议器自身的事件或被采纳, 或说明了拒绝理由 —— 两者都算"真的在用提议"
    used = [e for e in events if e["type"] == "proposal_used"]
    rejected = [e for e in events if e["type"] == "proposal_rejected"]
    assert used or rejected, [e["type"] for e in events]


def test_graph_entry_records_rejected_proposal(tmp_path, monkeypatch):
    """非法提议必须被拒并**说明理由** (不能静默回退)。"""
    from src.research.store import ResearchStore

    _run_graph(tmp_path, monkeypatch, _proposal(action_type="hack_the_planet"), "propB")
    store = ResearchStore("propB")
    events = store.events()
    store.close()
    rejected = [e for e in events if e["type"] == "proposal_rejected"]
    reasons = " ".join(str(r) for e in rejected for r in (e["payload"].get("reasons") or []))
    assert reasons, f"被拒提议必须留下理由: {[e['type'] for e in events]}"
    assert "hack_the_planet" in reasons or "不在当前可派发清单内" in reasons


def test_proposer_choice_changes_selected_action(tmp_path, monkeypatch):
    """引擎层: 注入提议后, 选中的动作与理由来自提议 (而不是固定打分)。"""
    from src.graph import theory_pipeline
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    monkeypatch.setenv("THEORY_PROPOSER", "1")   # conftest 默认关闭, 这里显式打开
    spec = ResearchSpec(project_id="propC", problem_statement=ACCEPTANCE_TEXT)
    store = ResearchStore("propC", db_path=tmp_path / "propC.sqlite")
    llm = _fake_llm(_proposal(action_type="seek_counterexample"))
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          llm=llm, budget=ResearchBudget(max_actions=6))
    assert engine.bootstrap()
    claim = engine._claims()[0]
    # 提议必须绑定**当前版本**的对象 (过期版本会被拒)
    llm = _fake_llm(_proposal(action_type="seek_counterexample",
                              object_id=f"{claim.id}@v{claim.version}"))
    engine.llm = llm
    theory_pipeline._attach_proposer(engine, store)

    info = engine.step()
    assert info["action"] == "seek_counterexample", info
    assert "减少不确定性" in info["reason"], info["reason"]
    # 提议只决定"下一步做什么", 不改变命题状态
    assert engine._get_claim(claim.id).status.value in ("proposed", "in_progress",
                                                       "refuted", "supported")
    store.close()
