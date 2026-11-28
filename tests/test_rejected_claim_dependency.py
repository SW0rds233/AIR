import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.agents.reasoning import ReasoningAgent


def test_a_rejected_revision_keeps_its_target_and_model_dependency_during_repair(monkeypatch):
    agent = ReasoningAgent()
    target = {"id": "clm-target", "version": 1, "statement": "旧的行为解释", "claim_type": "descriptive"}
    candidate = {"id": "clm-target", "statement": "条件模型只刻画激励阈值", "claim_type": "descriptive",
                 "study": {"design": "theory"}, "model_ref": "mdl-detection@v0"}
    ctx = ContextPack(objects={"claim": [target], "model": [{"id": "mdl-detection", "version": 1}]})
    runtime = SimpleNamespace(llm_available=lambda *_: True)
    monkeypatch.setattr(agent, "tool_loop", lambda *a, **kw: (json.dumps({"claims": [candidate]}), []))
    def run(hints):
        return agent._run(AgentTask(agent="reasoning", objective="修订命题", hints=hints), ctx, runtime, UsageRecord(), [])
    bad = run({"repair_claim": True, "claim_id": target["id"]})
    assert not bad.proposed_changes
    hints = bad.followup_needs[0].hints
    assert hints["claim_id"] == target["id"] and hints["rejected_candidate"]["model_ref"] == "mdl-detection@v0"
    del candidate["model_ref"]
    assert not run(hints).proposed_changes
    candidate["model_ref"] = {"id": "unregistered", "version": 1}
    assert not run(hints).proposed_changes
    candidate["model_ref"] = "mdl-detection@v1"
    fixed, = [change for change in run(hints).proposed_changes if change.kind == "claim"]
    assert fixed.object_id == target["id"] and fixed.payload["version"] == 2
    assert fixed.payload["model_ref"] == {"id": "mdl-detection", "version": 1}

    candidate.update(variable_domains={"x": "概率区间"}, formal_spec={
        "variables": {"x": "Real"}, "premises": ["x >= 0", "x <= 1"], "conclusion": "x <= 1"})
    rejected = run({"repair_claim": True, "claim_id": target["id"]})
    retry, = rejected.followup_needs
    assert "概率区间" in retry.why
    del candidate["variable_domains"]
    assert not run(retry.hints).proposed_changes
