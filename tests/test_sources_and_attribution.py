from __future__ import annotations

"""资料源绑定与逐命题证据归属的失败用例 (计划书 §3 R0 / R3)。

反向安全测试:
- 绑定一个不存在的资料库必须在**确认研究之前**被拒绝, 不得静默退回"无知识库";
- 选了 A 资料库时引用只能来自 A;
- 同一篇文献只支持 A 时, B 命题的证据缺口必须仍然存在;
- 同一篇文献分别支持 A、反对 B 时, 必须保存两条**不同的**关系。
"""

import json

import pytest

SAMPLE = """摘要
本文研究射频指纹识别方法。

定义
指纹可分性指不同设备的特征分布可分离。

定理 1
在信噪比足够高时, 指纹特征可分。
"""


def _make_kb(tmp_path, monkeypatch, topic, *, text=SAMPLE, year="2020", filename="doc.txt"):
    from src import config
    from src.kb.ingest import ensure_topic, ingest_manual

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    tdir = ensure_topic(topic)
    manual = tdir / "manual"
    manual.mkdir(parents=True, exist_ok=True)
    (manual / filename).write_text(text, encoding="utf-8")
    (manual / "meta.json").write_text(json.dumps({
        filename: {"title": f"{topic} 材料", "authors": "张三", "year": year,
                   "venue": "电子学报", "doi": f"10.1000/{topic}"},
    }, ensure_ascii=False), encoding="utf-8")
    ingest_manual(topic, embed=False)
    return topic


# --------------------------------------------------------------------------
# R0: 资料源列举与绑定校验
# --------------------------------------------------------------------------
def test_list_source_sets_reports_scope_and_index(tmp_path, monkeypatch):
    from src.kb.sources import list_source_sets

    _make_kb(tmp_path, monkeypatch, "源A")
    sources = {s.source_set_id: s for s in list_source_sets()}
    assert "源A" in sources
    info = sources["源A"]
    assert info.readable is True
    assert info.documents == 1 and info.cards >= 1
    assert info.indexed is False, "未建向量索引时必须如实报告"
    assert "未建索引" in info.describe()
    assert info.year_range == "2020"


def test_list_source_sets_does_not_create_empty_kb(tmp_path, monkeypatch):
    from src import config
    from src.kb.sources import list_source_sets

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    assert list_source_sets() == []
    assert not (tmp_path / "data" / "kb").exists(), "枚举不得创建目录或空库"


def test_validate_binding_rejects_missing_and_empty(tmp_path, monkeypatch):
    from src.kb.sources import validate_binding

    _make_kb(tmp_path, monkeypatch, "源B")
    missing = validate_binding("不存在的库")
    assert missing["ok"] is False and "不存在" in missing["reason"]

    # 目录存在但没有 kb.sqlite → 明确不可用 (不是静默"没有资料")
    from src import config

    (config.DATA_DIR / "kb" / "空库").mkdir(parents=True, exist_ok=True)
    empty_dir = validate_binding("空库")
    assert empty_dir["ok"] is False and "没有 kb.sqlite" in empty_dir["reason"]

    ok = validate_binding("源B")
    assert ok["ok"] is True
    assert any("未建向量索引" in w for w in ok["warnings"])


def test_resolve_topic_prefers_explicit_binding(tmp_path, monkeypatch):
    from src.kb.sources import resolve_topic, resolve_topic_with_binding
    from src.research.schemas import ResearchSpec

    _make_kb(tmp_path, monkeypatch, "源C")
    # 显式绑定优先于按主题名匹配
    spec = ResearchSpec(project_id="p", domain="另一个名字", source_set_id="源C")
    assert resolve_topic(spec) == "源C"
    topic, note = resolve_topic_with_binding(spec)
    assert topic == "源C" and "已绑定资料源 源C" in note

    # 绑定不可用 → 不返回主题, 并给出可展示的原因 (不静默降级)
    bad = ResearchSpec(project_id="p", domain="同名库", source_set_id="不存在")
    topic2, note2 = resolve_topic_with_binding(bad)
    assert topic2 == "" and "不可用" in note2


def test_start_session_rejects_unusable_source_set(tmp_path, monkeypatch):
    """计划书 R0 验收: 资料源不可用要在**确认研究之前**告知用户。"""
    from fastapi.testclient import TestClient

    from src import config, server

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    client = TestClient(server.app)
    r = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "src1", "problem_id": "p1",
        "request": "对所有实数 x: x**2 >= 0", "source_set_id": "从来没有的库",
    })
    assert r.status_code == 409, r.text[:300]
    detail = r.json()["detail"]
    assert detail["source_set_id"] == "从来没有的库"
    assert "不可用" in detail["message"]


def test_start_session_records_usable_binding(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src import config, server

    topic = _make_kb(tmp_path, monkeypatch, "源D")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    client = TestClient(server.app)
    r = client.post("/api/sessions", json={
        "mode": "theory", "project_id": "src2", "problem_id": "p1",
        "request": "对所有实数 x: x**2 >= 0", "source_set_id": topic,
    })
    assert r.status_code == 200, r.text[:300]
    tid = r.json()["thread_id"]

    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_SPEC, ResearchStore

    store = ResearchStore("src2", db_path=config.DATA_DIR / "research" / "src2.sqlite")
    spec = ResearchSpec.model_validate(store.get(KIND_SPEC, "p1"))
    store.close()
    assert spec.source_set_id == topic
    client.post(f"/api/sessions/{tid}/stop")
    server.SESSIONS.pop(tid, None)


def test_research_uses_bound_source_set_only(tmp_path, monkeypatch):
    """选了 A 资料库时, 检索只能命中 A 的资料。"""
    from src.kb.service import KnowledgeService
    from src.kb.sources import resolve_topic_with_binding
    from src.research.schemas import ResearchSpec

    _make_kb(tmp_path, monkeypatch, "源E", text=SAMPLE)
    _make_kb(tmp_path, monkeypatch, "源F",
             text="摘要\n完全不相关的内容。\n\n定义\n另一些定义。\n", filename="other.txt")

    spec = ResearchSpec(project_id="p", source_set_id="源E")
    topic, _note = resolve_topic_with_binding(spec)
    service = KnowledgeService(topic, create_if_missing=False)
    outcome = service.search("指纹 可分", limit=8)
    assert outcome.refs, "绑定资料库应当能检索到内容"
    titles = {r.title for r in outcome.refs}
    assert titles == {"源E 材料"}, f"只能引用绑定资料库的内容: {titles}"


# --------------------------------------------------------------------------
# R3: 逐命题证据归属
# --------------------------------------------------------------------------
def _two_claim_engine(tmp_path, pid, *, with_knowledge=False):
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ResearchSpec
    from src.research.store import ResearchStore
    from src.verification.runner import VerificationRunner

    spec = ResearchSpec(project_id=pid, problem_statement="对所有实数 x: x**2 >= 0",
                        domain="")
    store = ResearchStore(pid, db_path=tmp_path / f"{pid}.sqlite")
    knowledge = None
    if with_knowledge:
        # 证据缺口只在知识底座可用时产生 (没有底座就没有"该去检索"的缺口)
        from src.kb.ingest import ensure_topic, ingest_manual
        from src.kb.service import KnowledgeService

        ensure_topic(f"{pid}-kb")
        tdir = ensure_topic(f"{pid}-kb") / "manual"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "doc.txt").write_text(SAMPLE, encoding="utf-8")
        ingest_manual(f"{pid}-kb", embed=False)
        knowledge = KnowledgeService(f"{pid}-kb", create_if_missing=False)
    engine = TheoryEngine(spec, store, runner=VerificationRunner(inproc=True),
                          budget=ResearchBudget(max_actions=8), knowledge=knowledge)
    assert engine.bootstrap()
    return engine, store


def _extra_claim(engine, statement="额外命题"):
    from src.research.schemas import Claim, ClaimStatus

    claim = Claim(statement=statement, lhs="y", rhs="0", relation=">=",
                  variables=["y"], variable_domains={"y": "real"},
                  problem_id=engine.spec.problem_id, status=ClaimStatus.proposed)
    engine.store.put("claim", claim.id, claim.model_dump(mode="json"))
    return engine._get_claim(claim.id)


def _evidence(engine, claim, *, eid, support, excerpt="材料"):
    from src.research.schemas import SourceEvidence, SupportKindOfEvidence

    item = SourceEvidence(id=eid, title=f"文献 {eid}", source_id="doc-1",
                          claim_id=claim.id, excerpt=excerpt, source_kind="journal")
    item.support = SupportKindOfEvidence(support)
    engine.store.put("evidence", item.id, item.model_dump(mode="json"))
    return engine._evidence()[-1] if False else item


def test_evidence_for_claim_is_per_claim(tmp_path):
    """A 命题的证据不得进入 B 命题的证据集合。"""
    from src.research.schemas import SupportKindOfEvidence

    engine, store = _two_claim_engine(tmp_path, "r3a")
    claim_a = engine._claims()[0]
    claim_b = _extra_claim(engine)
    _evidence(engine, claim_a, eid="ev-a", support="supports")

    assert {e.id for e in engine.evidence_for_claim(claim_a)} == {"ev-a"}
    assert engine.evidence_for_claim(claim_b) == [], "B 不应共享 A 的证据"

    # 未归属的证据既不属于 A 也不属于 B
    orphan = _evidence(engine, claim_b, eid="ev-x", support="insufficient")
    orphan = orphan.model_copy(update={"claim_id": ""})
    store.put("evidence", orphan.id, orphan.model_dump(mode="json"))
    assert "ev-x" not in {e.id for e in engine.evidence_for_claim(claim_a)}
    assert "ev-x" not in {e.id for e in engine.evidence_for_claim(claim_b)}
    assert SupportKindOfEvidence.insufficient in {e.support for e in engine._evidence()}
    store.close()


def test_single_paper_supporting_a_leaves_b_gap(tmp_path):
    """计划书 R3 验收: 单篇文献只支持 A, B 必须保持缺证据。"""
    engine, store = _two_claim_engine(tmp_path, "r3b", with_knowledge=True)
    claim_a = engine._claims()[0]
    claim_b = _extra_claim(engine, "B 命题")
    _evidence(engine, claim_a, eid="ev-a", support="supports")

    gaps = engine._gaps(engine._claims(), engine._obligations())
    per_claim = {}
    for gap in gaps:
        if gap.gap_type.value == "missing_evidence":
            per_claim.setdefault(gap.target_ref.id, []).append(gap.statement)

    # A 已被支持 → 不应再因"没有已判定支持的证据"产生证据缺口
    assert not any("尚未检索" in s or "未判定支持关系" in s
                   for s in per_claim.get(claim_a.id, [])), per_claim
    # B 没有自己的证据 → 必须仍有证据缺口
    assert claim_b.id in per_claim, "B 的证据缺口不得被 A 的证据顶掉"
    store.close()


def test_same_paper_can_support_a_and_contradict_b(tmp_path):
    """同一文献分别支持 A、反对 B → 必须保存两条不同的关系。"""
    from src.research.schemas import SupportKindOfEvidence

    engine, store = _two_claim_engine(tmp_path, "r3c")
    claim_a = engine._claims()[0]
    claim_b = _extra_claim(engine, "B 命题")

    engine.link_evidence(claim_a, _evidence(
        engine, claim_a, eid="ev-shared", support="supports"), note="条件满足")
    engine.link_evidence(claim_b, _evidence(
        engine, claim_b, eid="ev-shared", support="contradicts"), note="条件不满足")

    links = engine._evidence_links()
    assert len(links) == 2, links
    by_claim = {link.claim_ref.id: link for link in links}
    assert by_claim[claim_a.id].relation == SupportKindOfEvidence.supports
    assert by_claim[claim_b.id].relation == SupportKindOfEvidence.contradicts
    # 关系绑定各自命题版本
    assert by_claim[claim_a.id].claim_ref.version == claim_a.version
    assert by_claim[claim_b.id].condition_notes != by_claim[claim_a.id].condition_notes
    store.close()


def test_links_expire_with_claim_version(tmp_path):
    """命题换代后旧关系不再为新一代命题记账。"""
    engine, store = _two_claim_engine(tmp_path, "r3d")
    claim = engine._claims()[0]
    engine.link_evidence(claim, _evidence(engine, claim, eid="ev-v1", support="supports"))
    assert len(engine.evidence_links_for(claim)) == 1

    bumped = claim.model_copy(update={"version": claim.version + 1})
    assert engine.evidence_links_for(bumped) == [], "旧版本关系不得沿用到新版本"
    store.close()


def test_attach_evidence_stamps_claim_and_link(tmp_path):
    """挂接证据时必须写入归属 (claim_id + 版本化 EvidenceLink)。"""
    from src.research.schemas import SourceEvidence

    engine, store = _two_claim_engine(tmp_path, "r3e")
    claim_a = engine._claims()[0]
    claim_b = _extra_claim(engine, "B 命题")
    item = SourceEvidence(id="ev-at", title="文献", excerpt="材料", source_id="doc-1")

    ids = engine.attach_evidence(claim_a.id, [item])
    assert ids == ["ev-at"]
    stored = next(e for e in engine._evidence() if e.id == "ev-at")
    assert stored.claim_id == claim_a.id
    assert engine.evidence_for_claim(claim_b) == []
    assert {link.source_ref.id for link in engine.evidence_links_for(claim_a)} == {"ev-at"}
    store.close()


def test_interpret_evidence_does_not_fall_back_to_first_claim(tmp_path):
    """对象不是本问题的命题时必须跳过, 不得改判到第一条命题。"""
    from src.research.schemas import ActionType, ResearchAction

    engine, store = _two_claim_engine(tmp_path, "r3f")
    claim_a = engine._claims()[0]
    _evidence(engine, claim_a, eid="ev-a", support="insufficient")

    ok = engine._act_interpret_evidence(ResearchAction(
        action_type=ActionType.interpret_evidence, object_id="clm-不存在"))
    assert ok is False
    assert any("不是本问题的命题" in n for n in engine._notes)
    # 未判定证据保持不变 (没有被动过)
    assert [e.support.value for e in engine.evidence_for_claim(claim_a)] == ["insufficient"]
    store.close()


def test_interpret_evidence_only_judges_own_evidence(tmp_path):
    from src.research.schemas import ActionType, ResearchAction

    engine, store = _two_claim_engine(tmp_path, "r3g")
    claim_a = engine._claims()[0]
    claim_b = _extra_claim(engine, "B 命题")
    _evidence(engine, claim_a, eid="ev-a", support="insufficient",
              excerpt="指纹 可分 特征 在高信噪比下可分")
    _evidence(engine, claim_b, eid="ev-b", support="insufficient",
              excerpt="完全无关的内容")

    ok = engine._act_interpret_evidence(ResearchAction(
        action_type=ActionType.interpret_evidence, object_id=claim_a.id))
    assert ok is True
    judged = {e.id: e for e in engine._evidence()}
    links = {link.source_ref.id for link in engine._evidence_links()}
    assert "ev-a" in links, "本命题的证据必须形成关系记录"
    assert "ev-b" not in links, "不得顺手判定别的命题的证据"
    assert judged["ev-b"].support.value == "insufficient"
    store.close()


def test_two_problems_in_one_project_do_not_share_sources_or_facts(tmp_path, monkeypatch):
    """发布前回归门槛 1/2 (计划书 §5): A 不得出现在仅授权 B 的研究里。

    同一项目下两个研究问题各自绑定不同资料源; 两个问题都真实跑完研究循环后:
    - 每个问题的规格只记录自己的资料源;
    - 研究成果里不得出现另一个资料源的定位 (source_id / 递归检查);
    - 导出交付物的 manifest 与工作台状态都按各自的问题/运行身份归属。
    """
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src.graph import theory_pipeline
    from src.research.schemas import ResearchSpec
    from src.research.store import KIND_SPEC, ResearchStore

    marker_a = "ALPHA-KB-7f31"
    marker_b = "BETA-KB-9c02"
    _make_kb(tmp_path, monkeypatch, "源A", text=f"摘要\n{marker_a} 的内容。\n", filename="a.txt")
    _make_kb(tmp_path, monkeypatch, "源B", text=f"摘要\n{marker_b} 的内容。\n", filename="b.txt")

    runs = {}
    for problem_id, source in (("pA", "源A"), ("pB", "源B")):
        # 资料源绑定由规格承载 (与 Web 入口一致): 先落盘规格, 再按 resume 语义研究,
        # 这样问题与资料源的绑定不会被"重新解析请求"覆盖。
        from src.research.question_planner import build_spec_from_input

        bound = build_spec_from_input(request="对所有实数 x: x**2 >= 0", topic=source,
                                      project_id="ab", problem_id=problem_id)
        bound.source_set_id = source
        store0 = ResearchStore("ab", db_path=config.DATA_DIR / "research" / "ab.sqlite")
        try:
            store0.put(KIND_SPEC, problem_id, bound.model_dump(mode="json"))
        finally:
            store0.close()
        final = theory_pipeline.run_theory_pipeline(
            project_id="ab", problem_id=problem_id, resume=True, max_actions=8)
        assert final.get("snapshot_id"), problem_id
        runs[problem_id] = final

    store = ResearchStore("ab", db_path=config.DATA_DIR / "research" / "ab.sqlite")
    try:
        specs = {pid: ResearchSpec.model_validate(store.get(KIND_SPEC, pid))
                 for pid in ("pA", "pB")}
        assert specs["pA"].source_set_id == "源A"
        assert specs["pB"].source_set_id == "源B"
        # 同一项目两个问题的运行身份必须不同 (各自的产物/账本可分别恢复)
        runs_a = {s["run_id"] for s in store.list_snapshots(problem_id="pA")}
        runs_b = {s["run_id"] for s in store.list_snapshots(problem_id="pB")}
        assert runs_a and runs_b and runs_a.isdisjoint(runs_b)
    finally:
        store.close()

    # 研究成果里不得泄漏另一个资料源的内容
    blobs = {}
    for pid, final in runs.items():
        parts = []
        for key in ("manuscript_path", "package_dir"):
            path = final.get(key)
            if not path:
                continue
            from pathlib import Path

            root = Path(path)
            files = [root] if root.is_file() else sorted(root.rglob("*"))
            for item in files:
                if item.is_file():
                    parts.append(item.read_text(encoding="utf-8", errors="ignore"))
        blobs[pid] = "\n".join(parts)
    assert marker_a not in blobs["pB"], "A 资料不得出现在仅授权 B 的研究交付物里"
    assert marker_b not in blobs["pA"], "B 资料不得出现在仅授权 A 的研究交付物里"

    # 工作台状态: 各自只看到自己的资料源与身份
    from src import server

    for problem_id, source, other in (("pA", "源A", marker_b), ("pB", "源B", marker_a)):
        state = server._research_state("ab", problem_id)
        assert state["problem_id"] == problem_id
        assert state["run_id"] == runs[problem_id]["run_id"]
        assert other not in json.dumps(state, ensure_ascii=False)


@pytest.mark.parametrize("kind", ["csv", "sqlite"])
def test_study_plan_carries_table_and_where(tmp_path, monkeypatch, kind):
    """StudyPlan 必须能把结构化数据源的表名/过滤条件传到只读适配器。"""
    from src.kb.adapters import readonly_data as rd
    from src.research.schemas import ProofObligation, StudyPlan
    from src.research.theorist import _check_for

    monkeypatch.setattr(rd, "authorized_roots", lambda: [tmp_path.resolve()])
    if kind == "csv":
        path = tmp_path / "t.csv"
        path.write_text("g,t,y\nT,pre,1\n", encoding="utf-8")
        table = ""
    else:
        import sqlite3

        path = tmp_path / "t.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE panel (g TEXT, t TEXT, y REAL)")
        conn.execute("INSERT INTO panel VALUES ('T','pre',1.0)")
        conn.commit()
        conn.close()
        table = "panel"

    from src.research.schemas import Claim, ClaimType

    study = StudyPlan(design="did", treatment="A", outcome="Y", data_ref=str(path),
                      data_table=table, group_col="g", time_col="t", outcome_col="y",
                      treated_label="T", control_label="C", pre_label="pre",
                      post_label="post", data_source_kind="real")
    claim = Claim(statement="A 影响 Y", claim_type=ClaimType.causal, study=study)
    obligation = ProofObligation(statement="估计效应", kind="estimate_effect",
                                 acceptance_method="stats", claim_id=claim.id)
    check = _check_for(obligation, claim, {"stats": True})
    assert check is not None
    assert check.tool == "stats"
    assert check.arguments.get("table") == table or table == ""
    assert "where" in check.arguments
    # 端到端: 该 check 的实参确实能被只读适配器读取
    rows, reason, meta = rd.load_rows(str(path), table=table)
    assert reason == "" and rows and meta["readonly"] is True
