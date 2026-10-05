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
