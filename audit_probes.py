"""审计探针 (对照计划书 §12): 离线、内存库、不联网、不调用模型。

用途: 逐条复现 §3 里的 P0/P1 发现, 并在修复后**重新运行**看它是否真的变了。
判据要能区分三态: 已修复 / 仍未修复 / 已不适用 (探针本身的前提变了)。

本脚本只读探针, 不是生产代码。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("AIR_ROOT", Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from src.agents.protocol import AgentTask, AgentResult, ChangeProposal, grant_for  # noqa: E402
from src.agents.runtime import AgentRuntime  # noqa: E402
from src.agents.supervisor import SupervisorAgent  # noqa: E402
from src.graph.research_graph import TeamRun  # noqa: E402
from src.graph.team_session import _evidence_of, _model_of  # noqa: E402
from src.research.projection import TeamProjection  # noqa: E402
from src.research.store import ResearchStore  # noqa: E402
from src.research.task_store import TaskStore  # noqa: E402
from src.verification import stats_adapter, sympy_adapter, z3_adapter  # noqa: E402

facts: dict = {}

# --- G10: 工具/适配器契约 ---
# 判据: 工具声明的操作必须真实存在于适配器; 旧代码传的 `check` 不存在。
from src.agents.tools import VERIFICATION_TOOLS  # noqa: E402

facts["G10_tool_contract"] = {
    "declared_operations": {tool: operation
                            for tool, (_adapter, operation) in VERIFICATION_TOOLS.items()},
    "sympy_check_supported": "check" in sympy_adapter.OPERATIONS,
    "z3_check_supported": "check" in z3_adapter.OPERATIONS,
    "verdict": ("FIXED (tools no longer pass the nonexistent 'check')"
                if all(operation != "check"
                       for _a, operation in VERIFICATION_TOOLS.values())
                else "STILL BROKEN"),
}

store = ResearchStore("audit", db_path=":memory:")
try:
    # --- G09: user_kb 不得外搜 ---
    task = AgentTask(agent="evidence", objective="read only", source_policy="user_kb",
                     source_set_ids=["kb-authorized"])
    grant = grant_for(task)
    facts["G09_user_kb_grants_external_search"] = grant.allows_read("tools:search")
    facts["G09_source_set_scope_enforced"] = (
        grant.allows_source_set("kb-authorized"), grant.allows_source_set("kb-other"))

    # --- G07: 越权候选不得落库 ---
    result = AgentResult(task_id=task.task_id, agent="evidence", outcome="completed",
                         summary="candidate", proposed_changes=[ChangeProposal(
                             kind="claim", object_id="audit-claim",
                             payload={"statement": "unverified", "status": "supported"})])
    runtime = AgentRuntime()
    violations_before = runtime.validate_result(task, result, grant)
    result = runtime.finalize(task, result, grant)
    TeamProjection(store).register(task, result)
    stored_claim = store.get("claim", "audit-claim")
    facts["G07_rejected_candidate"] = {
        "violations": violations_before,
        "quarantined": [item["object_id"] for item in result.rejected_changes],
        "pending_registration": [p.object_id for p in result.proposed_changes],
        "in_store": stored_claim is not None,
        "verdict": "FIXED (not persisted)" if stored_claim is None else "STILL BROKEN",
    }

    # --- G11: 导出映射 ---
    row = _evidence_of({"id": "e1", "title": "source", "locator": "page 9",
                        "relation": "supports", "version": 7})
    facts["G11_evidence_export_mapping"] = {
        "location": row.location,
        "support": row.support.value,
        "version": row.version,
        "verdict": ("FIXED" if row.location == "page 9"
                    and row.support.value == "supports" and row.version == 7
                    else "STILL BROKEN"),
    }
    try:
        model = _model_of({"id": "m1", "name": "M", "natural_language": "n"})
        facts["G11_model_export"] = {"class": type(model).__name__, "verdict": "FIXED"}
    except Exception as exc:  # noqa: BLE001
        facts["G11_model_export"] = {"error": f"{type(exc).__name__}: {exc}",
                                     "verdict": "STILL BROKEN"}

    # --- G04: 主控是否调用模型 ---
    class SpyLLM:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("No network in audit")

    spy = SpyLLM()
    supervisor = SupervisorAgent(llm=spy)
    brief = supervisor.brief("证明所有正数 x 满足给定不等式，并参考文献写成论文",
                             project_id="audit")
    plan = supervisor.plan(brief)
    decision = supervisor.decide(brief=brief, plan=plan)
    facts["G04_G14_supervisor"] = {
        "llm_calls": spy.calls,
        "first_roles": [t.agent for t in decision.tasks],
        "task_run_ids": sorted({str(t.get("run_id", "")) for t in plan.tasks}),
        "reasoning_dependencies": [t.get("depends_on") for t in plan.tasks
                                   if t.get("agent") == "reasoning"],
        "verdict_G04": "STILL BROKEN (0 model calls)" if spy.calls == 0 else "FIXED",
        "verdict_G14": ("STILL BROKEN (empty run_id)"
                        if any(not str(t.get("run_id", "")) for t in plan.tasks) else "FIXED"),
    }

    # --- G05: 推理是否先于证据 ---
    facts["G05_dispatch_order"] = {
        "first_roles": [t.agent for t in decision.tasks],
        "evidence_dependencies": [t.get("depends_on") for t in plan.tasks
                                  if t.get("agent") == "evidence"],
    }

    # --- G02: 默认团队是否有模型 ---
    team = TeamRun(project_id="audit", run_id="audit-run", request="研究问题",
                   task_store=TaskStore("audit", store))
    facts["G02_default_team_llm_available"] = team.runtime.llm_available()
finally:
    store.close()

print(json.dumps(facts, ensure_ascii=False, indent=2))


