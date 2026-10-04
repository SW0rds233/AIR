from __future__ import annotations

"""统一智能体协议的行为用例 (合并计划 §5 / §10.1 第 1 项)。

覆盖三类**接口结果**: 正常完成 / 受阻 / 版本过期 —— 这是 M0 的退出条件。
另加权限令牌、预算预留与身份归一的不变量, 因为这些是"模型自造令牌就绕过约束"
这类缺陷的唯一防线 (不能靠提示词)。

约定: 本文件不调用任何 LLM, 也不写仓库数据目录。
"""

import pytest
from pydantic import ValidationError

from src.agents.protocol import (
    AGENT_CAPABILITIES,
    GRANT_ISSUER,
    AgentResult,
    AgentRun,
    AgentTask,
    ArtifactRef,
    CapabilityGrant,
    ChangeProposal,
    ContextPack,
    GrantError,
    ResearchNeed,
    ReviewIssueRef,
    RunBudget,
    TaskBudget,
    TaskOutcome,
    TaskStatus,
    UsageRecord,
    canonical_object_kind,
    grant_for,
    idempotency_key_for,
    role_of,
)


def _task(**over) -> AgentTask:
    data = {
        "agent": "evidence",
        "objective": "检索 2-(211,15,1) 设计不存在性的原始文献并给出可定位引文",
        "subquestion": "该不存在性是否有已发表定理支撑",
        "expected_gain": "得到可引用的定理陈述与出处",
        "project_id": "proj-1",
        "problem_id": "prob-1",
        "run_id": "run-1",
    }
    data.update(over)
    return AgentTask(**data)


# --------------------------------------------------------------------------
# 正常完成
# --------------------------------------------------------------------------
def test_completed_result_carries_evidence_and_no_reason():
    task = _task()
    result = AgentResult(
        task_id=task.task_id, agent=task.agent, agent_run_id="ar-1",
        outcome="completed", summary="检索到 2 篇可定位文献",
        input_versions={task.task_id: 1, "claim-1": 3},
        artifact_refs=[ArtifactRef(artifact_id="art-1", kind="json",
                                   uri="research/proj-1/evidence.json", bytes=120)],
        proposed_changes=[ChangeProposal(
            kind="evidence", object_id="", payload={"claim_id": "claim-1"},
            input_versions={"claim-1": 3})],
        verification_refs=[],
        usage=UsageRecord(llm_calls=2, tool_calls=3, input_tokens=900,
                          output_tokens=300, cost_usd=0.0012, models=["m1"]),
    )
    assert result.outcome == TaskOutcome.completed
    assert result.is_success is True
    assert result.failure_reason == ""
    assert result.usage.total_tokens == 1200
    body = result.to_dict()
    assert body["outcome"] == "completed"
    assert body["artifact_refs"][0]["kind"] == "json"
    assert body["usage"]["total_tokens"] == 1200


# --------------------------------------------------------------------------
# 受阻
# --------------------------------------------------------------------------
def test_blocked_result_must_state_a_reason_and_a_need():
    """受阻但说不出缺什么时, 主控无从补派工 —— 必须显式可见。"""
    silent = AgentResult(task_id="t1", agent="reasoning", outcome="blocked")
    assert silent.failure_reason          # 自动补上原因, 不是空串
    assert any("followup_needs" in u for u in silent.unresolved)

    stated = AgentResult(
        task_id="t1", agent="reasoning", outcome="blocked",
        failure_reason="引理 L 缺适用条件 (需要 k ≡ 1 mod 4)",
        followup_needs=[ResearchNeed(
            kind="clause", statement="查引理 L 的适用条件",
            why="推导第 3 步依赖该条件",
            acceptance=["给出定理编号与原文定位"],
            blocked_refs=[], blocking=False,
            raised_by="ar-9")],
    )
    assert stated.followup_needs[0].owner == "evidence"
    assert stated.failure_reason.startswith("引理 L")
    assert stated.unresolved == []


def test_other_blocked_owners_follow_the_need_kind():
    from src.agents.protocol import NEED_OWNER

    for kind, owner in NEED_OWNER.items():
        need = ResearchNeed(kind=kind, statement="x")
        assert need.owner == owner


def test_review_issue_severity_decides_blocking():
    blocking = ReviewIssueRef(issue_id="i1", severity="blocking", category="science",
                              summary="结论与第 3 节的推导不一致")
    assert blocking.blocking is True
    soft = ReviewIssueRef(issue_id="i2", severity="minor", summary="措辞")
    assert soft.blocking is False
    with pytest.raises(ValidationError):
        ReviewIssueRef(issue_id="i3", suggested_owner="hacker")


def test_review_role_cannot_escalate_only_downgrade():
    """审阅只能提出意见: 能力表里没有写状态的能力, 也不得改结论。"""
    spec = AGENT_CAPABILITIES["review"]
    assert spec["tools"] == ("propose_review",)
    assert spec["may_change_conclusion"] is False
    grant = grant_for(_task(agent="review"))
    assert grant.allows_tool("propose_review") is True
    # 审阅不能提交命题或推导
    assert grant.allows_tool("propose_claim") is False
    assert grant.allows_tool("propose_derivation") is False


# --------------------------------------------------------------------------
# 版本过期 (read-set 一致性)
# --------------------------------------------------------------------------
def test_change_proposal_declares_expected_revision_and_read_set():
    proposal = ChangeProposal(
        kind="claim", object_id="claim-7", expected_revision=2,
        payload={"statement": "该 2-设计不存在"},
        input_versions={"claim-7": 2, "evidence-3": 1},
    )
    assert proposal.kind == "claim"
    assert proposal.expected_revision == 2
    assert proposal.input_versions["claim-7"] == 2
    # 依据 v2 产出、对象已到 v3 -> 调用方据此判过期 (运行时检查, 不是模型自述)
    assert proposal.expected_revision != 3


def test_idempotency_key_is_stable_and_content_sensitive():
    """同一依据 + 同一内容必须得到同一键 (重连/重试不重复付费调用)。"""
    first = ChangeProposal(kind="claim", object_id="c1", expected_revision=1,
                           payload={"statement": "x"}, )
    second = ChangeProposal(kind="claim", object_id="c1", expected_revision=1,
                            payload={"statement": "x"})
    third = ChangeProposal(kind="claim", object_id="c1", expected_revision=1,
                           payload={"statement": "y"})
    assert first.idempotency_key("t1") == second.idempotency_key("t1")
    assert first.idempotency_key("t1") != third.idempotency_key("t1")
    # 换了依据版本 -> 不同键 (目标变了就是新提交)
    bumped = ChangeProposal(kind="claim", object_id="c1", expected_revision=2,
                            payload={"statement": "x"})
    assert bumped.idempotency_key("t1") != first.idempotency_key("t1")


def test_task_idempotency_key_normalizes_whitespace_but_not_wording():
    same = idempotency_key_for("p", "q", 1, "evidence", "检索  文献\n并定位")
    same2 = idempotency_key_for("p", "q", 1, "evidence", "检索 文献 并定位")
    other = idempotency_key_for("p", "q", 1, "evidence", "检索文献")
    assert same == same2
    assert same != other
    # 计划升版**不**改变键: 升版不等于要做新东西。把版本算进键会让同一句需求每升一版
    # 就变成新任务, 主控于是反复派工直到预算耗尽 (实测过这种循环)。
    assert idempotency_key_for("p", "q", 2, "evidence", "检索 文献 并定位") == same
    # 角色不同则是不同的任务
    assert idempotency_key_for("p", "q", 1, "writing", "检索 文献 并定位") != same


# --------------------------------------------------------------------------
# 权限令牌
# --------------------------------------------------------------------------
def test_forged_grant_is_invalid_and_grants_nothing():
    forged = CapabilityGrant(
        grant_id="grant-fake", task_id="t", agent="review",
        tools=["propose_review"], may_submit=True,
        issued_by="model", issued_at="2026-01-01T00:00:00+00:00",
    )
    assert forged.is_valid() is False
    assert forged.allows_tool("propose_review") is False
    with pytest.raises(GrantError):
        forged.require_tool("propose_review")


def test_runtime_issued_grant_matches_role_scope():
    task = _task(agent="writing")
    grant = grant_for(task)
    assert grant.issued_by == GRANT_ISSUER
    assert grant.is_valid() is True
    assert grant.allows_tool("propose_manuscript") is True
    assert grant.allows_tool("propose_claim") is False
    assert grant.allows_read("tools:latex") is True
    assert grant.may_change_conclusion is False   # 结论等级由中央规则产生


def test_grant_rejects_unregistered_scope_and_tool():
    with pytest.raises(ValidationError):
        CapabilityGrant(grant_id="g", task_id="t", agent="evidence",
                        read_scopes=["root_filesystem"], issued_by=GRANT_ISSUER,
                        issued_at="now")
    with pytest.raises(ValidationError):
        CapabilityGrant(grant_id="g", task_id="t", agent="evidence",
                        tools=["write_state_directly"], may_submit=True,
                        issued_by=GRANT_ISSUER, issued_at="now")


def test_grant_requires_a_write_capability_to_submit():
    with pytest.raises(ValidationError):
        CapabilityGrant(grant_id="g", task_id="t", agent="evidence",
                        may_submit=True, tools=[], issued_by=GRANT_ISSUER,
                        issued_at="now")


def test_every_role_can_be_granted_and_supervisor_takes_no_task():
    from src.agents.protocol import AGENT_ROLES, WORKER_ROLES

    for role in WORKER_ROLES:
        grant = grant_for(_task(agent=role))
        assert grant.is_valid() is True
    assert set(WORKER_ROLES) | {"supervisor"} == set(AGENT_ROLES)
    with pytest.raises(ValidationError):
        # 主控只派发, 不承接派工
        _task(agent="supervisor")


# --------------------------------------------------------------------------
# 预算
# --------------------------------------------------------------------------
def test_budget_reservation_marks_reserved_and_detects_overrun():
    budget = TaskBudget(max_llm_calls=3, max_tokens=1000, max_cost_usd=0.01)
    reserved = budget.reserve()
    assert reserved.reserved is True and reserved.reserved_at
    # 原对象不被就地改写 (预留返回副本)
    assert budget.reserved is False

    usage = UsageRecord(llm_calls=2, tool_calls=1, input_tokens=700,
                        output_tokens=400, cost_usd=0.004)
    assert reserved.exceeded_by(usage) == ["tokens 1100/1000"]
    assert reserved.fraction_used(usage)["tokens"] == pytest.approx(1.1)


def test_run_budget_allocation_is_visible_and_bounded():
    run = RunBudget(total=TaskBudget(max_llm_calls=5, max_tokens=5000,
                                     max_cost_usd=0.02))
    grant, note = run.allocate(TaskBudget(max_llm_calls=3, max_tokens=4000,
                                          max_cost_usd=0.05), "task-1")
    assert note                      # 被裁剪过就必须说明
    assert grant.max_llm_calls == 3
    assert grant.max_cost_usd == pytest.approx(0.02)
    assert grant.reserved is True
    assert run.allocations == ["task-1"]

    # 第二次分配只剩下全局余额, 不会"每个任务都以为有全额"
    second, note2 = run.allocate(TaskBudget(max_llm_calls=4, max_tokens=5000),
                                 "task-2")
    assert second.max_llm_calls == 2
    assert second.max_tokens == 1000
    assert note2


def test_usage_merge_keeps_unknown_distinct_from_zero():
    partial = UsageRecord(llm_calls=1, unknown_parts=["网关未返回 usage"])
    assert partial.cost_usd is None            # 未知不是 0
    merged = partial.merge(UsageRecord(llm_calls=2, cost_usd=0.002))
    assert merged.llm_calls == 3
    assert merged.cost_usd == pytest.approx(0.002)
    assert merged.unknown_parts == ["网关未返回 usage"]


# --------------------------------------------------------------------------
# 身份与对象种类
# --------------------------------------------------------------------------
def test_role_normalization_accepts_legacy_and_camel_case():
    assert role_of("EvidenceAgent") == "evidence"
    assert role_of("evidence_agent") == "evidence"
    assert role_of("literature_reviewer") == "evidence"
    # 已退役的撰写模块名不再映射 (合并计划 §15.2 清理): 名字表只保留**在用的**别名,
    # 否则"这个名字属于哪个角色"会长期给读代码的人错误印象。
    assert role_of("paper_writer") == ""
    assert role_of("writing_bridge") == ""
    assert role_of("PaperReviewer") == "review"
    assert role_of("hack_the_planet") == ""
    assert role_of("") == ""


def test_object_kind_is_closed_but_accepts_documented_aliases():
    assert canonical_object_kind("claim") == "claim"
    assert canonical_object_kind("proof_obligation") == "obligation"
    assert canonical_object_kind("experiment_spec") == "validation_plan"
    assert canonical_object_kind("nonsense") == ""
    with pytest.raises(ValidationError):
        ChangeProposal(kind="nonsense", payload={})


def test_artifact_ref_rejects_absolute_paths():
    ok = ArtifactRef(artifact_id="a", uri="research/proj/paper.pdf")
    assert ok.uri == "research/proj/paper.pdf"
    for bad in ("E:/papers/x.pdf", r"C:\papers\x.pdf", "//host/share/x.pdf"):
        with pytest.raises(ValidationError):
            ArtifactRef(artifact_id="a", uri=bad)


def test_context_pack_only_carries_summaries_not_full_text():
    pack = ContextPack(
        task_id="t1", agent="evidence", grant=grant_for(_task()),
        objects={"claim": [{"id": "claim-1", "version": 2, "statement": "s",
                            "status": "proposed"}]},
        sources=[{"source_set_id": "kb-x", "documents": 12}],
        request="研究该问题并给出结论",
        gaps=[{"id": "gap-1", "type": "missing_evidence"}],
    )
    assert pack.scoped_objects("claim")[0]["version"] == 2
    body = pack.to_dict()
    assert body["grant"]["valid"] is True
    assert "text" not in body["objects"]["claim"][0]


# --------------------------------------------------------------------------
# 任务与运行
# --------------------------------------------------------------------------
def test_task_requires_worker_role_and_objective():
    with pytest.raises(ValidationError):
        _task(objective="   ")
    with pytest.raises(ValidationError):
        AgentTask(agent="ghost", objective="x")


def test_agent_run_records_identity_fields_for_audit():
    run = AgentRun(task_id="t1", agent="evidence", attempt=2,
                   status=TaskStatus.running, prompt_version="evidence/v1",
                   models=["m1"], tool_runs=["tr-1"], input_versions={"claim-1": 2})
    assert run.attempt == 2
    body = run.to_dict()
    for key in ("task_id", "agent", "attempt", "prompt_version", "models",
                "tool_runs", "input_versions", "output_hash"):
        assert key in body
    assert body["status"] == "running"


def test_task_status_retry_uses_same_task_new_attempt():
    task = _task()
    first = AgentRun(task_id=task.task_id, agent=task.agent, attempt=1,
                     status=TaskStatus.failed, error="timeout")
    retry = AgentRun(task_id=task.task_id, agent=task.agent, attempt=2,
                     status=TaskStatus.running)
    assert first.task_id == retry.task_id
    assert retry.agent_run_id != first.agent_run_id
    assert retry.attempt == first.attempt + 1
