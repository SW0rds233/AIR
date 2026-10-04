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
    # 直接调 plan() 时不会带运行身份 —— 身份由**运行时**(TeamRun)注入。为了测到真实
    # 行为, 这里经 TeamRun 走一遍 (G14 的判据是"计划里有没有 run_id")。
    from src.graph.research_graph import TeamRun as _TeamRun  # noqa: E402

    team_probe = _TeamRun(project_id="audit", problem_id="p1", run_id="audit-run",
                          request="证明所有正数 x 满足给定不等式，并参考文献写成论文",
                          supervisor=supervisor,
                          task_store=TaskStore("audit", store))
    team_probe.prepare()
    plan = team_probe.loop.plan
    decision = supervisor.decide(brief=team_probe.loop.brief, plan=plan)
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
    roles_by_id = {str(t.get("task_id", "")): str(t.get("agent", ""))
                   for t in plan.tasks}
    facts["G05_dispatch_order"] = {
        "first_roles": [t.agent for t in decision.tasks],
        "reasoning_depends_on_roles": sorted({
            roles_by_id.get(str(dep), dep)
            for t in plan.tasks if t.get("agent") == "reasoning"
            for dep in (t.get("depends_on") or [])}),
    }

    # --- G02: **HTTP 装配**出来的团队是否有角色模型 ---
    # 注意: 裸 `TeamRun(...)` 本来就没有模型 —— 模型由装配层注入 (这是有意的分工)。
    # 因此判据是"从入口构建出来的团队有没有模型", 不是"直接 new 一个有没有"。
    from src import server as _server  # noqa: E402

    class _ProbeSession:
        request = {"project_id": "audit", "problem_id": "p1", "request": "研究问题",
                   "source_policy": "user_kb"}
        run_id = "audit-run"
        session_id = "audit-session"
        topic = "audit"

        def emit(self, *_a, **_k):
            pass

    team = _server._build_team_app(_ProbeSession()).session.team
    facts["G02_team_entry_has_model_factory"] = {
        "factory_wired": team.runtime._llm_factory is not None,
        # 本探针在离线约定下运行 (THEORY_LLM=0), 所以能力应为 False; 关键是**工厂已接线**
        "llm_available_offline": team.runtime.llm_available(),
        "run_id_wired": team.run_id,
        "verdict": ("FIXED (entry injects a role model factory)"
                    if team.runtime._llm_factory is not None else "STILL BROKEN"),
    }
finally:
    store.close()

print(json.dumps(facts, ensure_ascii=False, indent=2))


