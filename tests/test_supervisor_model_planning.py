from __future__ import annotations

"""主控**真的调用模型**的回归 (§3.1 G04)。

审计复现: 给 `SupervisorAgent` 注入 spy 模型后, 理解/规划/首轮决策的调用数仍为 **0**
—— 主控保存了 `llm` 却从不调用, 于是"理解任务"退化成关键词分类: 同义改写、否定条件、
跨领域说法都会走样, 而且**看起来像是模型理解过的**。

本文件固定三件事:
1. 有可用的模型时, `brief()` 必须真的调用它, 并用它给的子问题;
2. 模型的输出**必须被校验** —— 未登记的类型/角色不得进入计划 (否则会在派工时才炸);
3. 模型不可用或输出不合规时**退回规则展开**, 并如实标记这是一次降级 ——
   不能让"规则跑通"看起来像"模型理解过任务"。
"""

from src.agents.supervisor import (
    SUBQUESTION_KINDS,
    SupervisorAgent,
)


class _Spy:
    """模型替身: 记录调用次数, 返回预设 JSON。"""

    def __init__(self, body: str) -> None:
        self.calls = 0
        self.body = body

    def invoke(self, *_args, **_kwargs):
        self.calls += 1

        class _Response:
            content = self.body

        return _Response()


def _payload(items: list[dict]) -> str:
    import json

    return json.dumps({"subquestions": items}, ensure_ascii=False)


# --------------------------------------------------------------------------
# 1. 有模型就要用
# --------------------------------------------------------------------------
def test_brief_calls_the_model_and_uses_its_subquestions():
    spy = _Spy(_payload([{"statement": "核对定理的适用条件",
                          "kind": "literature_synthesis", "owner": "evidence",
                          "needs": ["定位原文", "标出适用域"]}]))
    brief = SupervisorAgent(llm=spy).brief(
        "证明所有正数 x 满足给定不等式，并参考文献写成论文", project_id="p")
    assert spy.calls >= 1, "主控有模型却没有调用它 (G04 的核心症状)"
    statements = [sub.statement for sub in brief.subquestions]
    assert "核对定理的适用条件" in statements, statements
    assert "模型提议" in brief.basis, "没有记下子问题来自模型"


def test_model_proposed_needs_become_acceptance_criteria():
    """模型给的完成判据要被带进子问题 (后续派工用它做 acceptance_criteria)。"""
    spy = _Spy(_payload([{"statement": "给出机理解释", "kind": "mechanism",
                          "owner": "reasoning", "needs": ["逐条列出假设"]}]))
    brief = SupervisorAgent(llm=spy).brief("机理分析", project_id="p")
    target = next(s for s in brief.subquestions if s.statement == "给出机理解释")
    assert "逐条列出假设" in target.needs


# --------------------------------------------------------------------------
# 2. 模型输出必须被校验
# --------------------------------------------------------------------------
def test_unknown_kind_is_discarded():
    spy = _Spy(_payload([
        {"statement": "非法类型", "kind": "nonsense", "owner": "evidence"},
        {"statement": "合法机制", "kind": "mechanism", "owner": "reasoning"},
    ]))
    brief = SupervisorAgent(llm=spy).brief("分析", project_id="p")
    statements = [sub.statement for sub in brief.subquestions]
    assert "非法类型" not in statements, "未登记的子问题类型进入了计划"
    assert "合法机制" in statements


def test_unknown_owner_falls_back_to_the_kind_owner_and_is_reported():
    """角色名不认识时用该类型登记的承接者, 并在 basis 里如实写出替换。"""
    spy = _Spy(_payload([{"statement": "说不上谁做", "kind": "mechanism",
                          "owner": "hacker"}]))
    brief = SupervisorAgent(llm=spy).brief("分析", project_id="p")
    target = next((s for s in brief.subquestions if s.statement == "说不上谁做"), None)
    assert target is not None
    assert target.owner == "reasoning", target.owner
    assert "角色已按类型归位" in brief.basis, brief.basis


def test_supervisor_role_is_never_assigned_as_an_owner():
    """主控不承接派工任务: 把 owner 写成 supervisor 的条目必须被丢弃。"""
    spy = _Spy(_payload([
        {"statement": "让主控自己做", "kind": "mechanism", "owner": "supervisor"},
        {"statement": "合法机制", "kind": "mechanism", "owner": "reasoning"},
    ]))
    brief = SupervisorAgent(llm=spy).brief("分析", project_id="p")
    owners = {sub.owner for sub in brief.subquestions}
    assert "supervisor" not in owners
    assert "让主控自己做" not in {sub.statement for sub in brief.subquestions}


def test_every_kept_subquestion_has_a_registered_kind_and_owner():
    spy = _Spy(_payload([
        {"statement": "a", "kind": "mechanism", "owner": "reasoning"},
        {"statement": "b", "kind": "nonsense", "owner": "reasoning"},
        {"statement": "c", "kind": "validation_plan", "owner": "hacker"},
    ]))
    brief = SupervisorAgent(llm=spy).brief("分析", project_id="p")
    for sub in brief.subquestions:
        assert sub.kind in SUBQUESTION_KINDS, sub.kind
        assert sub.owner and sub.owner != "supervisor", sub.owner


def test_model_cannot_explode_the_task_count():
    """模型把任务拆得过碎时要有上限 (否则预算会被摊薄)。"""
    spy = _Spy(_payload([{"statement": f"子问题 {i}", "kind": "mechanism",
                          "owner": "reasoning"} for i in range(50)]))
    brief = SupervisorAgent(llm=spy).brief("分析", project_id="p")
    proposed = [s for s in brief.subquestions if s.statement.startswith("子问题 ")]
    assert len(proposed) <= 8, f"模型提议没有被限流: {len(proposed)}"


# --------------------------------------------------------------------------
# 3. 降级必须可见
# --------------------------------------------------------------------------
def test_unparsable_model_output_falls_back_and_is_marked():
    spy = _Spy("这不是 JSON, 只是闲聊")
    brief = SupervisorAgent(llm=spy).brief("机理分析", project_id="p")
    assert spy.calls >= 1
    assert brief.subquestions, "降级后没有产出任何子问题"
    assert "subquestions_from_model" in brief.unknown_fields, (
        "有模型却没走上模型, 却没标出这是降级")


def test_model_failure_does_not_break_the_brief():
    class _Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("模型不可用")

    brief = SupervisorAgent(llm=_Boom()).brief("机理分析", project_id="p")
    assert brief.subquestions, "模型抛错后主控没有降级"
    assert "subquestions_from_model" in brief.unknown_fields


def test_no_model_is_not_reported_as_a_degradation():
    """离线本来就该走规则 —— 不该被标成"降级"(否则这个标记就失去意义)。"""
    brief = SupervisorAgent(llm=None).brief("机理分析", project_id="p")
    assert brief.subquestions
    assert "subquestions_from_model" not in brief.unknown_fields


def test_offline_brief_still_classifies_by_rules():
    """规则展开仍是有效降级路径: 类型判定与角色归属保持确定。"""
    brief = SupervisorAgent(llm=None).brief(
        "证明所有正数 x 满足给定不等式，并参考文献写成论文", project_id="p")
    owners = {sub.owner for sub in brief.subquestions}
    assert owners <= {"evidence", "modeling", "reasoning", "validation",
                      "writing", "figures", "review"}
    assert "reasoning" in owners
