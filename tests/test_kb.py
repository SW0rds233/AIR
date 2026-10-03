from __future__ import annotations

"""主题文献知识底座 (KB) 测试。

重点: 人工中文文献在无 embedding 时也能解析、抽卡、检索; 人工+机器去重合并。
"""

import json

from src.research.schemas import SourcePolicy

SAMPLE = """摘要

本文研究深度学习方法在射频指纹识别中的应用，重点分析特征提取与分类识别两个环节。
射频指纹识别利用发射机硬件差异带来的信号特征，实现设备身份认证，是无线安全领域的重要方向。

1 引言

随着无线通信设备的快速增长，射频指纹识别在设备接入认证、异常终端检测等场景中受到广泛关注。
传统方法依赖人工设计的瞬态与稳态特征，在低信噪比和信道变化下鲁棒性不足，因此需要更强的特征学习手段。

定义1：射频指纹是指由发射机硬件制造公差导致的、可用于区分设备的稳定信号特征。

定理1：在信噪比足够高且信道条件一致时，不同发射机的指纹特征在特征空间中可分。

结果表明，所提方法在多个公开数据集上准确率提升 5%，并在低信噪比条件下保持稳定。
本文方法在特征提取阶段使用深度网络，在分类阶段使用度量学习，整体框架具有良好的可扩展性。

参考文献

[1] 张三. 射频指纹识别研究. 电子学报, 2020.
[2] 李四. 无线设备身份认证方法. 通信学报, 2021.
"""


def _write_topic(tmp_path, monkeypatch, topic="RF测试"):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    from src.kb.ingest import ensure_topic

    tdir = ensure_topic(topic)
    (tdir / "manual" / "深度学习方法研究_2020.txt").write_text(SAMPLE, encoding="utf-8")
    meta = {
        "深度学习方法研究_2020.txt": {
            "title": "深度学习方法研究", "authors": "张三, 李四", "year": "2020",
            "venue": "电子学报", "doi": "10.1000/rf.2020.1",
        }
    }
    (tdir / "manual" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return topic


def test_manual_ingest_cards_and_keyword_search(tmp_path, monkeypatch):
    from src.kb.ingest import ingest_manual
    from src.kb.retrieve import search, stats
    from src.kb.store import KBStore

    topic = _write_topic(tmp_path, monkeypatch)
    report = ingest_manual(topic, embed=False)
    assert len(report["ingested"]) == 1
    doc = report["ingested"][0]
    assert doc["quality"] == "ok"
    assert doc["cards"] >= 3  # 定义/定理/结果

    s = stats(topic)
    assert s["documents"] == 1 and s["manual"] == 1 and s["with_fulltext"] == 1
    assert s["by_card_type"].get("definition") == 1
    assert s["by_card_type"].get("theorem") == 1

    store = KBStore(topic)
    cards = store.get_cards()
    locators = {c["card_type"]: c["locator"] for c in cards}
    assert "定理" in locators["theorem"]
    assert locators["theorem"].startswith("p")

    hits = search(topic, "指纹 可分", use_vector=False)
    assert hits
    cards = store.get_cards(card_types=["theorem"])
    assert cards and "可分" in cards[0]["text"]

    found = search(topic, "准确率 提升", use_vector=False)
    assert any(h.get("kind") in ("doc", "card") for h in found)


def test_keyword_search_card_type_filter(tmp_path, monkeypatch):
    from src.kb.ingest import ingest_manual
    from src.kb.store import KBStore

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)
    store = KBStore(topic)
    only_def = store.search_cards("指纹", card_types=["definition"])
    assert only_def and all(c["card_type"] == "definition" for c in only_def)
    only_thm = store.search_cards("指纹", card_types=["theorem"])
    assert all(c["card_type"] == "theorem" for c in only_thm)


# --------------------------------------------------------------------------
# 单一 KB 召回路径: CLI 适配层不得绕过范围过滤与融合 (计划书 §6.1)
# --------------------------------------------------------------------------
def test_retrieve_adapter_returns_locators_and_channels(tmp_path, monkeypatch):
    from src.kb.ingest import ingest_manual
    from src.kb.retrieve import describe, search, search_detailed

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)

    hits = search(topic, "指纹 可分", use_vector=False)
    assert hits, "适配层必须能取到命中"
    for hit in hits:
        # 旧实现只返回 title/text, 没有可回原文的定位信息
        assert "doc_id" in hit and "locator" in hit
        assert hit["channel"] in ("card", "keyword", "vector")
        assert "char_start" in hit and "page" in hit
    assert any(h["kind"] == "card" and h["locator"] for h in hits)
    # RRF 融合分必须真实产生并按融合分降序 (旧实现只返回原始 score)
    assert all(h["rrf_score"] > 0 for h in hits)
    scores = [h["rrf_score"] for h in hits]
    assert scores == sorted(scores, reverse=True)

    detail = search_detailed(topic, "指纹 可分", use_vector=False)
    assert detail["hits"] and detail["searched"] is True
    assert detail["fusion"] == "rrf"
    assert detail["channels"], "必须报告实际运行的召回通道"
    assert set(detail["channels"]) <= {"card", "keyword", "vector"}
    assert detail["channel_counts"]

    info = describe(topic)
    assert info["store_available"] is True
    assert info["channels"]["keyword"] is True
    assert info["fusion"] == "rrf"


def test_retrieve_adapter_cannot_bypass_manual_only(tmp_path, monkeypatch):
    """旧实现直接查 store, 不受范围约束; 适配层必须与 KnowledgeService 一致。"""
    from src.kb.ingest import ingest_manual
    from src.kb.retrieve import search
    from src.kb.service import KnowledgeService, RetrievalRequest
    from src.kb.store import KBStore

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)

    unconstrained = search(topic, "指纹 可分", use_vector=False)
    constrained = search(topic, "指纹 可分", use_vector=False, manual_only=True)
    service = KnowledgeService(topic)
    service_outcome = service.search(RetrievalRequest(query="指纹 可分",
                                                      manual_only=True, max_results=8))
    assert [r.excerpt for r in service_outcome.refs] == [h["text"] for h in constrained], \
        "适配层与 KnowledgeService 必须给出同一结果 (不能各有一套过滤)"
    # 人工导入的资料本身是 manual_asserted, 因此这里应当仍能命中;
    # 关键是不能出现"适配层返回了 service 过滤掉的条目"
    assert len(constrained) <= len(unconstrained)
    assert KBStore(topic).count_documents() == 1


def test_retrieve_adapter_respects_language_and_year_filters(tmp_path, monkeypatch):
    from src.kb.ingest import ingest_manual
    from src.kb.retrieve import search

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)

    # 年份范围不含 2020 → 必须被过滤掉 (旧实现没有年份参数, 不可能过滤)
    assert search(topic, "指纹 可分", use_vector=False, time_range="2015-2018") == []
    assert search(topic, "指纹 可分", use_vector=False, time_range="2019-2021")


def test_manual_machine_dedup_by_doi(tmp_path, monkeypatch):
    from src.kb.ingest import ingest_machine, ingest_manual
    from src.kb.store import KBStore

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)
    ingest_machine(topic, [{
        "title": "深度学习方法研究", "authors": "张三, 李四", "year": "2020",
        "venue": "电子学报", "doi": "10.1000/rf.2020.1", "abstract": "机器检索摘要",
        "api_source": "openalex",
    }], embed=False)
    store = KBStore(topic)
    docs = store.list_documents()
    assert len(docs) == 1  # 去重合并为同一 doc
    # 人工字段优先 + 两条来源
    conn = store._conn
    prov = conn.execute("SELECT origin FROM provenance WHERE doc_id=?", (docs[0]["doc_id"],)).fetchall()
    origins = {r["origin"] for r in prov}
    assert {"manual", "machine"} <= origins


def test_parse_sections_and_references(tmp_path):
    from src.kb.parse import parse_document

    path = tmp_path / "paper.md"
    path.write_text(SAMPLE, encoding="utf-8")
    parsed = parse_document(str(path))
    assert parsed.parse_quality == "ok"
    headings = [s.heading for s in parsed.sections]
    assert any("引言" in h for h in headings)
    assert any("参考文献" in h for h in headings)
    assert parsed.references and "射频指纹" in parsed.references[0]


def test_bridge_evidence_and_novelty(tmp_path, monkeypatch):
    from src.kb.bridge import novelty_lookup, retrieve_evidence
    from src.kb.ingest import ingest_manual
    from src.kb.service import KnowledgeService
    from src.research.schemas import Claim, SupportKindOfEvidence

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)
    service = KnowledgeService(topic)

    claim = Claim(statement="射频指纹 深度学习")
    evidence = retrieve_evidence(service, claim, k=5)
    assert evidence
    assert evidence[0].credibility.value in ("high", "medium")
    assert evidence[0].title
    # 召回只是候选: 不得默认建立支持关系 (计划书 §6.2)
    assert all(e.support != SupportKindOfEvidence.supports for e in evidence)
    # 每个来源都必须可定位 (计划书 §6.1)
    assert all(e.source_id for e in evidence)
    assert all(e.location for e in evidence)

    lookup = novelty_lookup(service)
    rows = lookup(Claim(statement="深度学习方法用于射频指纹识别"))
    assert rows
    # 差异未分析 → 保持待比较, 不得宣称可能不同
    assert all(r.difference == "" for r in rows)


def test_probe_does_not_create_empty_kb(tmp_path, monkeypatch):
    """为命题探测"有没有可用资料"不得创建空知识底座 (计划书 §9.3 无副作用探测)。"""
    from src import config
    from src.kb.service import KnowledgeService

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    probe = KnowledgeService("从未建过的主题", create_if_missing=False)
    assert probe.available is False
    assert not (tmp_path / "data" / "kb").exists(), "探测不得创建目录或空库"

    # 显式创建 (例如导入资料) 时仍然照常工作
    created = KnowledgeService("从未建过的主题")
    assert created.available is True
    assert created.has_content is False
    assert (tmp_path / "data" / "kb").exists()


def test_bridge_meta_reports_recall_channels(tmp_path, monkeypatch):
    """检索元信息必须报告实际运行的通道与越范围剔除数 (计划书 §9.3)。"""
    from src.kb.bridge import retrieve_evidence_detailed
    from src.kb.ingest import ingest_manual
    from src.kb.service import KnowledgeService
    from src.research.schemas import Claim

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)
    service = KnowledgeService(topic)

    items, meta = retrieve_evidence_detailed(service, Claim(statement="射频指纹 深度学习"), k=5)
    assert items and meta["searched"] is True
    assert meta["refs"] == len(items)
    assert isinstance(meta["recall_channels"], list)
    assert isinstance(meta["channel_counts"], dict)
    assert isinstance(meta["dropped_out_of_scope"], int)
    # 关键词/卡片通道可用, 至少应有一条通道真的跑过
    assert meta["recall_channels"], "必须报告实际运行的召回通道"
    assert set(meta["recall_channels"]) <= {"card", "keyword", "vector"}


def test_kb_search_returns_locatable_refs(tmp_path, monkeypatch):
    """统一检索接口: 所有召回分支都返回带定位的引用对象。"""
    from src.kb.ingest import ingest_manual
    from src.kb.service import KnowledgeService, RetrievalRequest

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)
    service = KnowledgeService(topic)

    outcome = service.search(RetrievalRequest(query="射频指纹 特征提取", max_results=5))
    assert outcome.searched and not outcome.failures
    assert outcome.refs
    assert any(r.locator for r in outcome.refs)

    card_ref = next((r for r in outcome.refs if r.kind == "card"), None)
    if card_ref is not None:
        assert card_ref.source_id and card_ref.locator
        resolved = service.resolve(card_ref)
        assert resolved["found"] is True
        assert resolved["existence_verified"] is True
        read = service.read(card_ref)
        assert read["text"]
        assert not read["failure"]


def test_kb_search_failure_is_not_no_results(tmp_path, monkeypatch):
    """检索失败与"无相关文献"必须分开记录 (计划书 §6.1-6)。"""
    from src.kb.service import KnowledgeService, RetrievalRequest

    _write_topic(tmp_path, monkeypatch)
    service = KnowledgeService("不存在的主题")
    service._store = None
    service._store_error = "模拟不可用"
    outcome = service.search(RetrievalRequest(query="任意"))
    assert outcome.empty
    assert outcome.failures
    assert outcome.searched is False


def test_scope_filter_covers_vector_and_card_branches(tmp_path, monkeypatch):
    """范围/权限过滤必须覆盖**每条**召回路径 (计划书 §9.4)。

    回归: 早期实现只对关键词与卡片分支做范围过滤, 向量命中直接 append,
    导致 `manual_only=True` 时仍会返回非人工资料。
    """
    from src.kb.ingest import ingest_machine, ingest_manual
    from src.kb.service import KnowledgeService, RetrievalRequest, SourceRef
    from src.kb.store import KBStore

    topic = _write_topic(tmp_path, monkeypatch)
    ingest_manual(topic, embed=False)
    ingest_machine(topic, [{
        "title": "A machine-only survey of RF fingerprinting", "year": "2021",
        "venue": "IEEE Trans", "doi": "10.1/machine", "abstract": "射频指纹 深度学习",
        "api_source": "crossref", "language": "en",
    }], embed=False)
    store = KBStore(topic)
    machine_docs = [d for d in store.list_documents() if "machine-only" in d["title"]]
    assert machine_docs, "机器资料应已入库"
    machine_doc_id = machine_docs[0]["doc_id"]
    store.close()

    service = KnowledgeService(topic)
    # 用受控向量命中模拟"向量分支返回了越范围/不可回查的片段"
    machine_hit = SourceRef(source_id=machine_doc_id,
                            title="A machine-only survey of RF fingerprinting",
                            excerpt="射频指纹 深度学习", kind="chunk", score=9.9)
    no_record = SourceRef(source_id="", title="无法回查权威记录的片段",
                          excerpt="射频指纹", kind="chunk", score=9.8)
    monkeypatch.setattr(service, "_vector_hits", lambda q, k: [machine_hit, no_record])

    # 不限范围: 能回查到权威记录的向量命中保留; 回查不到的剔除
    loose = service.search(RetrievalRequest(query="射频指纹", max_results=10))
    assert any(r.source_id == machine_doc_id for r in loose.refs)
    assert not any(r.title.startswith("无法回查") for r in loose.refs)

    # manual_only: 向量分支的机器资料必须被挡掉, 并计入 dropped_out_of_scope
    strict = service.search(RetrievalRequest(query="射频指纹", max_results=10,
                                             manual_only=True))
    assert not any(r.source_id == machine_doc_id for r in strict.refs)
    assert strict.dropped_out_of_scope >= 1

    # 年份范围: 把区间限定在未来, 该机器资料必须被挡掉
    future = service.search(RetrievalRequest(query="射频指纹", max_results=10,
                                             time_range="2030-2035"))
    assert not any(r.source_id == machine_doc_id for r in future.refs)

    # 语言范围: 请求 zh 时英文机器资料被挡掉
    zh = service.search(RetrievalRequest(query="射频指纹", max_results=10, language="zh"))
    assert not any(r.source_id == machine_doc_id for r in zh.refs)


def test_year_and_language_helpers():
    from src.kb.store import _language_matches, _year_in_range

    assert _year_in_range("2020", "2019-2026")
    assert not _year_in_range("2020", "2021-2026")
    assert _year_in_range("2020", "-2026")
    assert _year_in_range("2020", "2019-")
    assert _year_in_range("", "2021-2026")          # 无年份不过滤
    assert _year_in_range("约2020年", "2019-2026")   # 从文本里抽 4 位年份
    assert not _year_in_range("2020", "2018,2021")

    assert _language_matches("", "en")              # 未标注不过滤
    assert _language_matches("en", "en")
    assert _language_matches("chinese", "zh")
    assert _language_matches("英文", "en")
    assert not _language_matches("en", "zh")


def test_chunk_records_real_page_and_char_range(tmp_path, monkeypatch):
    """跨页片段必须记录真实起止页与字符范围, 不得沿用整节首页码 (§6.1-3)。"""
    from src.kb.ingest import _chunks_from_sections
    from src.kb.parse import segment_sections

    # 单节跨 3 页、正文足够长, 会被切成多个 chunk
    body = "。".join(f"第{i}句关于射频指纹稳定性与可分性的论述" for i in range(120))
    pages = [(4, body[:600]), (5, body[600:1200]), (6, body[1200:])]
    sections = segment_sections(pages, doc_id="doc-x")
    assert len(sections) == 1, "构造的正文应只形成一个节"
    section = sections[0]
    assert section.page == 4, section
    assert section.page_end == 6, "节应记录跨页范围"

    chunks = _chunks_from_sections("doc-x", sections)
    assert len(chunks) > 1, "长正文应被切成多块"

    pages_seen = {c.page for c in chunks}
    assert len(pages_seen) > 1, f"不同片段应落在不同页, 实际全为 {pages_seen}"
    assert max(pages_seen) >= 5
    # 每个片段都有精确字符范围, 且随位置单调前进
    assert all(c.char_start >= 0 and c.char_end > c.char_start for c in chunks)
    starts = [c.char_start for c in chunks]
    assert starts == sorted(starts), "片段字符范围应随顺序前进"
    # 页号是估算, 必须标注
    assert all(c.page_estimated for c in chunks), "跨页节的页号应标注为估算"
    assert "(页码为估算)" in chunks[0].locator()
    assert "chars" in chunks[0].locator()


def test_single_page_section_is_not_marked_estimated():
    from src.kb.ingest import _chunks_from_sections
    from src.kb.schema import Section

    section = Section(doc_id="d", index=0, heading="引言", page=3, page_end=3,
                      text="甲" * 1200)
    chunks = _chunks_from_sections("d", [section])
    assert chunks and all(c.page == 3 for c in chunks)
    assert not any(c.page_estimated for c in chunks)
    assert chunks[0].locator().startswith("p3")


def test_service_read_uses_stored_chunk_locator(tmp_path, monkeypatch):
    """read() 必须使用入库时记录的字符范围与起止页, 不另算也不丢字段。"""
    import json

    from src.kb.ingest import ingest_manual
    from src.kb.service import KnowledgeService, SourceRef
    from src.kb.store import KBStore

    _write_topic(tmp_path, monkeypatch)
    # 写一篇足够长的正文副本, 保证产生多个带真实字符范围的片段
    from src.kb.store import topic_dir

    tdir = topic_dir("RF测试")
    body = "。".join(f"第{i}句关于射频指纹稳定性与可分性的论述" for i in range(80))
    (tdir / "manual" / "长文_2020.txt").write_text("摘要\n" + body + "\n", encoding="utf-8")
    meta_path = tdir / "manual" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["长文_2020.txt"] = {"title": "长文", "year": "2020", "venue": "电子学报"}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    ingest_manual("RF测试", embed=False)
    store = KBStore("RF测试")
    doc = next(d for d in store.list_documents() if d["title"] == "长文")
    stored = store.get_chunks(doc["doc_id"])
    sample = next(c for c in stored if c["char_start"] > 0)
    assert sample["char_end"] > sample["char_start"]

    service = KnowledgeService("RF测试")
    read = service.read(SourceRef(source_id=doc["doc_id"],
                                  chunk_id=f"{doc['doc_id']}#{sample['idx']}",
                                  title="长文", excerpt="射频指纹"))
    assert read["text"]
    assert "chars" in read["locator"], read["locator"]
    assert str(sample["char_start"]) in read["locator"]

def test_ingest_machine_metadata_only(tmp_path, monkeypatch):
    from src import config
    from src.kb.ingest import ingest_machine
    from src.kb.store import KBStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    report = ingest_machine("机器主题", [{
        "title": "A survey of RF fingerprinting", "authors": "Doe J", "year": "2021",
        "venue": "IEEE Trans", "doi": "10.1/abc", "abstract": "survey",
        "api_source": "crossref",
    }], embed=False)
    assert len(report["ingested"]) == 1
    store = KBStore("机器主题")
    doc = store.list_documents()[0]
    assert doc["title"] == "A survey of RF fingerprinting"
    assert doc["has_fulltext"] is False
    assert store.get_cards() == []


# ----------------------------------------------------------------------
# 理论模式的检索也要"下载全文 → 入库": 只有元数据时拿不到可定位引文
# ----------------------------------------------------------------------

THESIS_TEXT = """摘要

本文研究射影平面与组合设计的关系。

1 引言

组合设计的存在性判定依赖经典必要条件。

定义 1 (2-设计) 一个 2-(v,k,λ) 设计由 v 个点与 b 个区组构成。

定理 2 (Bruck-Ryser-Chowla) 若 n 阶射影平面存在且 n 恒等于 1, 2 (mod 4),
则 n 必为两个整数平方之和。

定理 3 (Fisher 不等式) 任何 2-(v,k,λ) 设计都满足 b 大于等于 v。
"""


def _write_fulltext_paper(tmp_path, title="Bruck-Ryser-Chowla theorem"):
    """写一篇可解析的"全文" (txt 也走 parse_document 的同一路径)。"""
    paper_dir = tmp_path / "pdfs"
    paper_dir.mkdir(parents=True, exist_ok=True)
    path = paper_dir / "theorem_source.txt"
    path.write_text(THESIS_TEXT, encoding="utf-8")
    return {"title": title, "year": "2026", "doi": "10.4230/lipics.itp.2026.19",
            "url": "https://doi.org/10.4230/lipics.itp.2026.19",
            "api_source": "OpenAlex", "abstract": "formalization of BRC",
            "pdf_path": str(path)}


def test_harvest_downloads_fulltext_and_ingests_cards(tmp_path, monkeypatch):
    """`_harvest_external` 必须先取全文再入库, 使定理卡片与可定位原文可用。

    实测背景: 理论模式的检索原先只调 `ingest_machine(..., embed=False)` 且不带
    `pdf_path`, 于是库里只有元数据 —— 引用守门拿不到可核对引文
    (`quote_is_locatable` 需要原文), 出现"检索到了却一条也引不了"。
    综述模式早就有 `pdf_ingestion` 节点做这件事, 这里复用同一套下载工具。
    """
    from src import config
    from src.kb import bridge
    from src.kb.service import KnowledgeService
    from src.kb.store import KBStore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    topic = "理论检索主题"
    paper = _write_fulltext_paper(tmp_path)
    fetched: list[dict] = []

    def fake_download(papers, *, topic, per_query, coverage):
        # 记录入库前的论文数, 并回填 pdf_path (模拟真实下载成功)
        fetched.extend(papers)
        coverage.fulltext_available = 1
        return [paper]

    monkeypatch.setattr(bridge, "_download_fulltext_for", fake_download)

    coverage = bridge.RetrievalCoverage(policy=SourcePolicy.autonomous)
    bridge._harvest_external(["nonexistence projective plane"], SourcePolicy.autonomous,
                             coverage, topic=topic, per_query=3,
                             search_fn=lambda q, n: [{"title": paper["title"],
                                                      "doi": paper["doi"],
                                                      "url": paper["url"],
                                                      "api_source": "OpenAlex"}],
                             ingest=True)
    assert coverage.executed is True
    assert fetched, "入库前必须先经过全文下载步骤"
    assert coverage.ingested == 1

    store = KBStore(topic)
    doc = store.list_documents()[0]
    assert doc["has_fulltext"] is True, "全文必须真的入库"
    cards = store.get_cards(card_types=["theorem"])
    assert cards, "必须抽出定理卡片"
    assert any(c["locator"].startswith("p") for c in cards), \
        [c["locator"] for c in cards]
    assert any("Bruck" in c["text"] or "射影平面" in c["text"] for c in cards)

    # 知识服务必须能召回带定位的原文片段 (引用守门要的正是这个)
    from src.kb.service import RetrievalRequest

    service = KnowledgeService(topic)
    outcome = service.search(RetrievalRequest(query="射影平面 必要条件", max_results=5))
    assert outcome.refs, "入库后必须能检索到带定位的来源"
    assert any(getattr(ref, "locator", "") for ref in outcome.refs), \
        [getattr(ref, "locator", "") for ref in outcome.refs]


def test_download_fulltext_skips_when_no_embedding(tmp_path, monkeypatch):
    """无向量化能力时不下载全文 (下载了也检索不到), 并如实记录原因。"""
    from src.kb import bridge

    coverage = bridge.RetrievalCoverage(policy=SourcePolicy.autonomous)
    papers = [{"title": "x", "url": "https://doi.org/10.1/x"}]
    monkeypatch.setattr("src.rag.vector_store.embedding_available", lambda: False)

    result = bridge._download_fulltext_for(papers, topic="t", per_query=2,
                                          coverage=coverage)
    assert result == papers, "应退回元数据入库"
    assert any("向量化不可用" in note for note in coverage.uncovered)


def test_download_fulltext_records_boundary_when_nothing_downloaded(tmp_path, monkeypatch):
    """命中但没有开放获取全文 -> 如实写入覆盖边界, 不静默当作"查过了"。"""
    from src.kb import bridge

    coverage = bridge.RetrievalCoverage(policy=SourcePolicy.autonomous)
    papers = [{"title": "no oa copy", "url": "https://doi.org/10.1/y"}]
    monkeypatch.setattr("src.rag.vector_store.embedding_available", lambda: True)
    monkeypatch.setattr("src.tools.pdf_fetcher.download_pdfs_for_papers",
                        lambda items, limit=10: list(items))

    result = bridge._download_fulltext_for(papers, topic="t", per_query=2,
                                          coverage=coverage)
    assert result == papers
    assert coverage.fulltext_available == 0
    assert any("未取得全文" in note for note in coverage.uncovered)
