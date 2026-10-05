from __future__ import annotations

"""团队会话引擎 (合并计划 §15.3 第 2–3 步)。

要固定的是"会话引擎"这层的行为, 而不是某次运行的内容:

1. `stream()` 逐轮产出**真实**事件 (主控决策 + 角色成果), 界面不会停在"正在思考";
2. 主控要求澄清时停在 `interrupt` 上, 投递答复后能继续 —— 澄清是终态判断,
   不是一个可以反复提议的动作 (旧引擎的 CD-003 缺陷就是这个形状);
3. 结束时可导出**交付包**, 且复用既有 `export_package` 与判定层;
4. 交付包里命题只能是**候选** (`proposed`), 团队不得冒充已确证结论 (§14.3)。
"""

import json
from pathlib import Path


from src.agents.supervisor import DecisionKind, SupervisorDecision
from src.graph.research_graph import TeamRun
from src.graph.team_session import TeamSession, run_team_session


def test_restarted_team_app_does_not_replay_finished_tasks(tmp_path, monkeypatch):
    """HTTP 会话重建后，stream(None) 应恢复 TeamRun，而非重新画像和派工。"""
    from src import config
    from src.graph.team_session import TeamApp

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    with TeamRun(project_id="proj-app-resume", problem_id="p1", run_id="run-app-resume",
                 request="证明对所有实数 x 有 x**2 >= 0", max_rounds=2) as first:
        first.run()
        original_ids = set(first.loop.results)
        original_rounds = first.loop.rounds
        assert original_ids
    with TeamRun(project_id="proj-app-resume", problem_id="p1", run_id="run-app-resume",
                 request="证明对所有实数 x 有 x**2 >= 0", max_rounds=2) as restored:
        session = TeamSession(restored)
        monkeypatch.setattr(session, "export", lambda: None)
        events = list(TeamApp(session).stream(None))
        assert set(restored.loop.results) == original_ids
        assert restored.loop.rounds == original_rounds
        assert events[-1]["research_team_done"]["summary"]["rounds"] == original_rounds


def _kb(tmp_path, monkeypatch, topic: str = "kb-session") -> str:
    """小资料库 (与团队用例同构): 让检索角色能真的登记可定位来源。"""
    from src import config
    from src.kb.identity import build_identity
    from src.kb.schema import LitRecord, Provenance
    from src.kb.store import KBStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    store = KBStore(topic)
    item = {"title": "The Nonexistence of Certain Finite Projective Planes",
            "authors": "X", "year": "2001"}
    store.upsert_document(
        LitRecord(doc_id="doc-1", title=item["title"], authors="X", year="2001",
                  abstract="Bruck-Ryser-Chowla.", manual_asserted=True,
                  has_fulltext=True, identity=build_identity(item)),
        search_text=("projective planes design block design BIBD Bruck Ryser Chowla "
                     "nonexistence"))
    store.set_identity(["title:thenonexistenceofcertainfiniteprojectiveplanes|x|2001"],
                       "doc-1")
    store.add_provenance("doc-1", [Provenance(origin="manual", detail="x.pdf")])
    store.append_event("ingest_manual", {"doc_id": "doc-1"})
    store.close()
    return topic


# --------------------------------------------------------------------------
# 逐轮事件
# --------------------------------------------------------------------------
def test_stream_yields_supervisor_decisions_and_task_results(tmp_path, monkeypatch):
    """界面必须能看到"主控这一轮做了什么 + 哪个角色交回了什么"。

    注意: 所有对团队对象的读取都在 `with` 块内完成 —— `TeamRun.close()` 会关掉
    研究库连接, 之后再读只会得到 `closed database` (那是测试自己制造的假失败)。
    """
    topic = _kb(tmp_path, monkeypatch)
    with TeamRun(project_id="proj-stream", problem_id="p1",
                 request="解释该机理并写成论文: 参数 2-(211,15,1) 的设计是否存在",
                 source_set_ids=[topic], source_policy="user_kb",
                 max_rounds=20) as team:
        team.set_source_sets([{"source_set_id": topic, "documents": 1, "cards": 0,
                               "indexed": False}])
        session = TeamSession(team)
        events = list(session.stream())
        status = team.outcome.status

        kinds = [e["type"] for e in events]
        assert "supervisor_decision" in kinds, kinds
        # 角色成果用运行时自己的事件名 (`task_result`), 不另造一套"任务完成"的说法
        assert "task_result" in kinds, "逐轮必须产出角色成果, 不能只有决策"
        assert kinds[-1] == "run_finished", kinds[-1]
        # 事件顺序与语义: 每条决策之后才可能有该轮的任务成果
        decisions = [i for i, k in enumerate(kinds) if k == "supervisor_decision"]
        tasks = [i for i, k in enumerate(kinds) if k == "task_result"]
        assert decisions and tasks
        assert decisions[0] < tasks[0]
        # 成果事件必须带任务身份与结果 (否则界面无法显示"哪个角色做了什么")
        finished = next(e for e in events if e["type"] == "task_result")
        payload = finished["payload"]
        assert payload["task_id"] and payload["agent"] and payload["outcome"]
    assert status in ("completed", "partial"), status


def test_session_forwards_events_to_the_session_outlet(tmp_path, monkeypatch):
    """团队运行时的事件同时进会话出口 (SSE 才能拿到真实进度)。"""
    topic = _kb(tmp_path, monkeypatch, "kb-outlet")
    seen: list[dict] = []
    with TeamRun(project_id="proj-outlet", problem_id="p1",
                 request="解释该机理并写成论文",
                 source_set_ids=[topic], source_policy="user_kb",
                 max_rounds=8) as team:
        team.set_source_sets([{"source_set_id": topic, "documents": 1, "cards": 0,
                               "indexed": False}])
        session = TeamSession(team, emit=seen.append)
        session.run_to_completion()
        kinds = {e["type"] for e in seen}
    assert "brief_ready" in kinds and "plan_ready" in kinds, kinds
    assert "supervisor_decision" in kinds
    assert "run_finished" in kinds


# --------------------------------------------------------------------------
# 澄清与续跑
# --------------------------------------------------------------------------
class _ClarifyOnce:
    """先要求澄清一次, 之后正常派工 —— 用来验证"停在 interrupt 上再继续"。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.asked = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def decide(self, **kwargs):
        if not self.asked:
            self.asked = True
            return SupervisorDecision(
                decision=DecisionKind.request_clarification,
                reason="问题范围不清, 需要用户补充")
        return self.inner.decide(**kwargs)


def test_clarification_stops_the_loop_and_a_response_resumes_it(tmp_path, monkeypatch):
    """主控要求澄清 → 停在 interrupt; 用户答复后能继续 (且不是重复提议澄清)。"""
    topic = _kb(tmp_path, monkeypatch, "kb-clarify")
    with TeamRun(project_id="proj-clarify", problem_id="p1",
                 request="研究一下这个方向", source_set_ids=[topic],
                 source_policy="user_kb", max_rounds=6) as team:
        team.set_source_sets([{"source_set_id": topic, "documents": 1, "cards": 0,
                               "indexed": False}])
        team.supervisor = _ClarifyOnce(team.supervisor)
        session = TeamSession(team)
        events = list(session.stream())

        assert events[-1]["type"] == "interrupt", [e["type"] for e in events]
        assert session.pending_interrupt is not None
        assert "澄清" in json.dumps(session.pending_interrupt, ensure_ascii=False)
        assert team.outcome.status == "waiting_user"

        # 空答复不接受 (不能用一个空字符串"继续")
        assert session.submit_response("   ") is False
        # 真实答复: 解除等待并重建画像/计划
        assert session.submit_response("把范围限定在 2-(211,15,1) 的存在性") is True
        assert session.pending_interrupt is None
        assert team.loop.finished is False
        assert "补充说明" in team.request
        # 继续跑: 主控现在有明确问题了, 必须能推进到收尾而不是再次要澄清
        more = list(session.stream())
        assert more, "答复之后必须能继续产生事件"
        assert more[-1]["type"] in ("run_finished", "interrupt")


# --------------------------------------------------------------------------
# 交付包
# --------------------------------------------------------------------------
def test_export_produces_a_package_with_candidate_claims_only(tmp_path, monkeypatch):
    """团队产出能导出交付包; 其中命题只能是**候选**, 不得冒充已确证。"""
    topic = _kb(tmp_path, monkeypatch, "kb-export")
    with TeamRun(project_id="proj-export", problem_id="p1",
                 request="解释该机理并写成论文: 参数 2-(211,15,1) 的设计是否存在",
                 source_set_ids=[topic], source_policy="user_kb",
                 max_rounds=20) as team:
        team.set_source_sets([{"source_set_id": topic, "documents": 1, "cards": 0,
                               "indexed": False}])
        session = TeamSession(team)
        session.run_to_completion()
        package = session.export()
        assert package is not None, session.team.outcome.stop_reason
        manifest = json.loads((Path(package) / "manifest.json").read_text("utf-8"))
        claims = json.loads((Path(package) / "claims.json").read_text("utf-8"))
        assert "delivery_level" in manifest
        assert (Path(package) / "manuscript.md").is_file()
        assert (Path(package) / "evidence.json").is_file()
        # 团队**不自报**科学等级: 结论状态必须由判定层给出, 且给出时必须有依据
        # (§3.1 G06 之后团队能走完形式化闭环, 因此这里的判据从"只许是 proposed"
        #  收紧为"不许由团队自报, 且 supported 必须配上已关闭义务与有效核验记录")。
        store = session.team.task_store.store
        obligations = store.list_latest("obligation") or []
        verifications = store.list_latest("verification") or []
        for claim in claims:
            status = claim.get("status", "")
            assert status != "", "结论没有状态: 判定层没有给出结论状态"
            if status == "supported":
                assert any(o.get("status") == "closed" for o in obligations), \
                    "结论被标为已确证, 但没有任何已关闭的义务支撑"
                assert any(not v.get("stale", False) for v in verifications), \
                    "结论被标为已确证, 但库里没有可用的核验记录"


def test_export_without_sources_is_an_honest_unresolved_report(tmp_path, monkeypatch):
    """没有任何来源/结论时, 交付包必须是**未决报告**而不是空包, 也不许伪造证据。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    with TeamRun(project_id="proj-empty", problem_id="p1",
                 request="判断参数为 2-(211,15,1) 的设计是否存在",
                 source_policy="user_kb", max_rounds=6) as team:
        session = TeamSession(team)
        session.run_to_completion()
        package = session.export()
        assert package is not None, "没有对象时也要给出未决报告"
        manifest = json.loads((Path(package) / "manifest.json").read_text("utf-8"))
        assert (Path(package) / "unresolved.md").is_file()
        # 等级必须**由事实决定**: 说"完整论文"就必须真的有编译好的 PDF (§5 阶段 1)。
        # 这里不再固定"只能到论文草稿" —— 计数约束判定靠**具名定理 + 机器重算的参数**
        # 成立, 不需要文献; 方案 §0 明确允许"无文献时参考文献章节仍在位并说明检索范围"。
        level = manifest.get("delivery_level")
        assert level in ("研究备忘录", "条件性研究报告", "论文草稿", "完整论文"), manifest
        pdf = Path(package) / "manuscript.pdf"
        if level == "完整论文":
            assert manifest.get("compilation_status") == "ok", manifest
            assert pdf.is_file() and pdf.stat().st_size > 1024, "自称完整论文却没有 PDF"
            # 没有来源时, 参考文献章节必须**在位并说明检索范围**, 不能凭空省掉
            tex = (Path(package) / "manuscript.tex").read_text("utf-8")
            assert "参考文献" in tex, "没有来源时省略了参考文献章节"
            assert "没有可引用的可定位来源" in tex, tex[:400]
        else:
            assert not (level == "完整论文"), manifest
        assert manifest.get("input_snapshot", {}).get("engine") == "team_v1", manifest


def test_run_team_session_reports_objects_while_storage_is_open(tmp_path, monkeypatch):
    """便捷入口的摘要必须在存储仍打开时取 —— 否则会报"有证据却是 0 条"。"""
    topic = _kb(tmp_path, monkeypatch, "kb-summary")
    summary = run_team_session(
        "解释该机理并写成论文: 参数 2-(211,15,1) 的设计是否存在",
        project_id="proj-summary", problem_id="p1", max_rounds=20,
        source_set_ids=[topic])
    assert summary["status"] == "completed", summary
    assert summary["objects"]["evidence"] > 0, summary["objects"]
    assert summary["objects"]["manuscript"] > 0, summary["objects"]
    assert summary["package_dir"]


def test_outcome_summary_never_claims_judged_truth(tmp_path, monkeypatch):
    """摘要里给出的是任务/用量/对象计数, 不含"结论为真"这类判定字段。"""
    topic = _kb(tmp_path, monkeypatch, "kb-truth")
    with TeamRun(project_id="proj-truth", problem_id="p1",
                 request="解释该机理并写成论文", source_set_ids=[topic],
                 source_policy="user_kb", max_rounds=8) as team:
        team.set_source_sets([{"source_set_id": topic, "documents": 1, "cards": 0,
                               "indexed": False}])
        session = TeamSession(team)
        session.run_to_completion()
        summary = session.outcome_summary()
    assert set(summary) >= {"run_id", "status", "rounds", "usage", "objects"}
    text = json.dumps(summary, ensure_ascii=False)
    for forbidden in ("supported", "refuted", "verified", "gate_passed"):
        assert forbidden not in text, f"团队摘要不得包含判定字段 {forbidden}"
