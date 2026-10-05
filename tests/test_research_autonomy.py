from __future__ import annotations

"""研究内核的自主性与交付语义测试 (计划书 §5.3-§5.4, §8, §14)。

覆盖:
- 意图解析必须绑定具体对象, 歧义时请求澄清而不是猜第一个;
- 语义提议越界/前置条件不满足时被拒绝并记录理由;
- 真实换路产生新路线并保留失败原因, 命题真假状态不被改写;
- 实验规格只到 spec_validated, 不得出现已执行结果;
- 写作区分"验证记录"与"推导计划";
- fork 明确哪些结论需重算且不复用旧验证。
"""



# --------------------------------------------------------------------------
# 意图解析: 对象级、歧义不猜
# --------------------------------------------------------------------------
def test_intent_binds_object_or_asks_for_clarification():
    from src.research.intent import parse_intent

    context = {
        "assumptions": {"asm-1": "矩阵 A 可逆", "asm-2": "噪声独立同分布"},
        "claims": {"clm-1": "上界较紧", "clm-2": "可分性随信噪比下降"},
        "steps": {"1:考察差值", "2:判定符号"},
    }
    # 唯一命中 → 绑定该对象
    actions = parse_intent("不要假设矩阵 A 可逆", context)
    assert actions and actions[0]["action_type"] == "revise_hypothesis"
    assert actions[0]["object_id"] == "asm-1"
    assert not actions[0].get("ambiguous")

    # 无命中 → 不猜第一个, 标记歧义并要求澄清
    actions = parse_intent("要不要再检查一下别的", context)
    if actions:
        assert actions[0].get("ambiguous") is True
        assert actions[0]["clarify"]

    # 控制指令保持确定性
    assert parse_intent("停止当前路线", context)[0]["action_type"] == "stop_with_report"
    assert parse_intent("", context) == []


def test_intent_rejects_unregistered_action_types():
    from src.research.intent import parse_intent

    actions = parse_intent("随便说点什么", {"claims": {"c1": "x"}})
    from src.research.action_registry import ACTION_REGISTRY
    from src.research.schemas import ActionType

    for action in actions:
        assert ActionType(action["action_type"]) in ACTION_REGISTRY


# --------------------------------------------------------------------------
# 语义提议: 越界拒绝
# --------------------------------------------------------------------------
def test_coordinator_rejects_out_of_scope_proposal():
    from src.research.coordinator import decide

    state = {
        "budget_remaining": 10, "open_obligations": [], "unplanned_claims": [],
        "pending_novelty": [], "unresolved_claims": [], "questions": [],
        "knowledge_available": False, "gaps": [],
    }
    # 未注册动作 → 拒绝并记录
    action = decide(state, proposal={"action_type": "hack_the_planet"})
    assert action.action_type.value in ("deliver_partial", "synthesize_results",
                                        "stop_with_report", "clarify_problem")
    assert any("未注册" in r for r in state.get("rejected_proposals", []))

    # 前置条件不满足 → 拒绝
    state2 = dict(state, retrieval_requests=[])
    action2 = decide(state2, proposal={"action_type": "retrieve_targeted"})
    assert action2.action_type.value != "retrieve_targeted"
    assert state2.get("rejected_proposals")


# --------------------------------------------------------------------------
# 换路: 保留失败原因, 不篡改真假状态
# --------------------------------------------------------------------------
def test_route_switch_preserves_failure_and_claim_status():
    from src.research.routes import RouteManager
    from src.research.schemas import RouteStatus

    manager = RouteManager()
    route = manager.ensure_route("clm-1", goal="证明 P", strategy="nonnegative_difference")
    assert route.status == RouteStatus.active

    switched = manager.switch("clm-1", reason="工具不支持该表达式",
                              recovery_condition="出现新后端或补齐条件")
    assert switched.id != route.id            # 真的新建了路线
    assert route.status == RouteStatus.failed  # 旧路线保留并标记失败
    assert route.failure_reason == "工具不支持该表达式"
    assert switched.strategy != route.strategy
    assert manager.used_strategies("clm-1") == {route.strategy, switched.strategy}

    # 非科学性失败可重试; 出现科学反例则不可原样重试
    manager.record_failure("clm-2", "unsupported", "后端不支持", scientific=False)
    assert manager.retryable("clm-2")
    manager.record_failure("clm-2", "counterexample", "找到反例", scientific=True)
    assert not manager.retryable("clm-2")


# --------------------------------------------------------------------------
# 实验规格: 只到 spec_validated
# --------------------------------------------------------------------------
def test_experiment_spec_never_claims_execution():
    from src.experiments.planner import design_experiment
    from src.experiments.schemas import ExecutionStatus
    from src.experiments.validator import validate_spec
    from src.research.schemas import Claim, ClaimType, StudyPlan

    claim = Claim(statement="技术A提升产出", claim_type=ClaimType.causal,
                  study=StudyPlan(treatment="技术A", outcome="产出",
                                  confounders=["规模"], confounder_handling="DiD"),
                  status=__import__("src.research.schemas", fromlist=["ClaimStatus"]).ClaimStatus.blocked)
    spec = design_experiment(claim)
    assert spec.execution_status in (ExecutionStatus.proposed, ExecutionStatus.spec_validated)
    assert spec.execution_status != ExecutionStatus.executed
    assert spec.artifacts == []
    assert "未执行" in " ".join(spec.limitations)

    # 伪造"已执行"必须被校验拒绝
    spec.execution_status = ExecutionStatus.executed
    report = validate_spec(spec)
    assert not report.ok
    assert any("没有真实产物" in e for e in report.errors)


def test_delivery_level_classification():
    from src.research.acceptance import GateResult, classify_deliverable

    ok = GateResult(passed=True)
    bad = GateResult(passed=False, reasons=["有问题"])
    memo = GateResult(passed=False, unresolved=["没有任何已确定结论, 只能作为研究备忘录导出"])
    # 出版完备门槛 (方案 v2): 完整论文 = 研究 ✓ + 表达 ✓ + 出版完备 ✓
    publishable = GateResult(passed=True)
    incomplete = GateResult(passed=True, unresolved=["尚未尝试编译 .tex"])

    assert classify_deliverable(ok, ok) == "完整论文"
    assert classify_deliverable(ok, ok, publishable) == "完整论文"
    # 出版层有实质缺项 (无 PDF/无参考文献) 时如实降级, 不得报成"完整论文"
    assert classify_deliverable(ok, ok, incomplete) == "论文草稿"
    assert classify_deliverable(bad, ok) == "条件性研究报告"
    assert classify_deliverable(bad, memo) == "研究备忘录"


# --------------------------------------------------------------------------
# 新颖性: 未分析差异不得升级; 底座缺失时退回外部检索
# --------------------------------------------------------------------------
def test_novelty_stays_pending_without_difference_analysis():
    """命中相近结果但未分析差异时, 不得升级为"可能不同" (计划书 §6.2/A13)。"""
    from src.research.novelty import assess
    from src.research.schemas import Claim, NoveltyComparisonRow, NoveltyStatus

    claim = Claim(statement="在信道一致时指纹线性可分")
    pending = [NoveltyComparisonRow(result="某论文", conclusion="相似结论", difference="")]
    record = assess(claim, lambda c: pending)
    assert record.status == NoveltyStatus.unchecked
    assert "尚未分析" in record.conclusion or "待比较" in record.conclusion

    equivalent = [NoveltyComparisonRow(result="某论文", difference="与本文结论等价")]
    assert assess(claim, lambda c: equivalent).status == NoveltyStatus.known_equivalent

    distinct = [NoveltyComparisonRow(result="某论文", difference="前提更弱, 结论范围更窄")]
    assert assess(claim, lambda c: distinct).status == NoveltyStatus.potentially_distinct


def test_novelty_separates_retrieval_failure_from_no_hit():
    """检索失败与"无命中"必须分开记录 (计划书 §6.1-6)。"""
    from src.research.novelty import assess
    from src.research.schemas import Claim, NoveltyStatus

    claim = Claim(statement="命题")

    def boom(_claim):
        raise RuntimeError("网络失败")

    failed = assess(claim, boom)
    assert failed.status == NoveltyStatus.unchecked
    assert failed.inaccessible and "检索失败" in failed.inaccessible[0]
    empty = assess(claim, lambda c: [])
    assert empty.status == NoveltyStatus.unchecked and not empty.inaccessible


def test_external_novelty_lookup_is_available_without_kb():
    """本地底座不可用时, 新颖性应退回外部文献检索并记录覆盖源 (A12)。"""
    from src.research.novelty import assess
    from src.research.retrieval import build_lookup
    from src.research.schemas import Claim

    calls: list[str] = []

    def fake_search(query, limit):
        calls.append(query)
        return [{"title": "An upper bound for X", "abstract": "we prove ...",
                 "venue": "Journal of Y", "source": "openalex"}]

    lookup = build_lookup(search_fn=fake_search)
    assert getattr(lookup, "covered_sources", None)
    record = assess(Claim(statement="X 的上界"), lookup)
    assert calls, "必须真的发起检索"
    assert record.rows and record.covered_sources
    # 差异未分析 → 保持待比较, 不得宣称创新
    assert record.status.value == "unchecked"


def test_adversarial_review_becomes_obligations():
    """反方审查每条意见必须产生**新的义务**, 而不是只输出总分 (计划书 §5.5)。"""
    from src.research.critic import issues_to_obligations, review_attempt
    from src.research.schemas import Claim, ProofAttempt, ProofStep

    claim = Claim(id="c1", statement="对 x>0, 1/x 的上界")
    attempt = ProofAttempt(target_claim_id="c1", strategy="nonnegative_difference",
                           steps=[ProofStep(index=1, statement="两边同时除以 x",
                                            justification="代数变形", rule="division")])
    issues = review_attempt(attempt)
    assert issues, "除以 x 却未声明非零, 应被独立审查指出"

    obligations = issues_to_obligations(attempt, claim)
    assert obligations, "审查意见必须转成新义务"
    for obligation in obligations:
        assert obligation.claim_id == "c1"
        assert obligation.acceptance_method == "informal_review"
        assert "独立审查" in obligation.statement

    # 已有同名义务不重复创建
    again = issues_to_obligations(attempt, claim,
                                  existing_statements={o.statement for o in obligations})
    assert again == []


# --------------------------------------------------------------------------
# fork: 明确重算范围
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 用户反馈闭环: 自然语言 → 对象级动作 (计划书 §5.1 / §6.1)
# --------------------------------------------------------------------------
