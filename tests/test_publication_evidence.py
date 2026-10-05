from __future__ import annotations

"""阶段 2 (方案 v2 M3): 出版层证据获取与引用守门测试。

三条不变量最容易被写坏, 因此各有用例:
1. **命中 != 支持**: 检索命中只证明文献存在; 没有可定位引文时不得声称支持;
2. **离线不联网**: `THEORY_LLM=0` 时不执行检索, 覆盖记录如实标注原因;
3. **不丢已有证据 + 重新编号**: 合并快照证据与检索结果时, 引用编号必须连续唯一
   (否则正文 `[1]` 指向哪一条不可预期)。
"""

from types import SimpleNamespace

from src.rag import reference_list as refmod
from src.research.publication_evidence import gather_publication_evidence, should_search
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    ResearchSnapshot,
    SourcePolicy,
    SupportKind,
    SupportKindOfEvidence,
    ValidationStatus,
)


def _snapshot(with_evidence: bool = False) -> ResearchSnapshot:
    claim = Claim(id="clm-1", statement="2-(211,15,1) 设计不存在", status=ClaimStatus.supported,
                  support_kind=SupportKind.theorem_application,
                  coverage=Coverage.target, validation_status=ValidationStatus.verified,
                  design_v=211, design_k=15, design_lambda=1, design_b=211, design_r=15,
                  design_verdict="nonexistent")
    snapshot = ResearchSnapshot(project_id="pev", problem_id="p1", claims=[claim])
    if with_evidence:
        from src.research.schemas import SourceEvidence

        snapshot.evidence = [SourceEvidence(id="ev-1", title="既有证据", claim_id="clm-1")]
    return snapshot


def _engine(knowledge=None, policy: SourcePolicy = SourcePolicy.user_kb):
    return SimpleNamespace(spec=SimpleNamespace(source_policy=policy), knowledge=knowledge)


class _FakeKnowledge:
    """最小可用知识底座: 记录检索式并返回固定命中。"""

    def __init__(self, refs, usable: bool = True, has_content: bool = True):
        self._refs = refs
        self.usable = usable
        self.has_content = has_content
        self.queries: list[str] = []

    def search(self, query, limit=8):
        self.queries.append(query)
        return SimpleNamespace(refs=list(self._refs))


def _ref(title: str, excerpt: str = "", locator: str = "", page: int = 0, doc_id: str = "doc-1",
         url: str = ""):
    return SimpleNamespace(source_id=doc_id, title=title, excerpt=excerpt, locator=locator,
                           page=page, year="1990", doi="", url=url, file_hash="",
                           doc_id=doc_id)


def _external_hit(title: str, abstract: str = "", url: str = "") -> dict:
    """外部检索 (arXiv/OpenAlex) 的命中形态: 纯字典, 无定位字段。"""
    return {"title": title, "abstract": abstract, "authors": "A. Author", "year": "1990",
            "url": url, "source": "arXiv", "doi": ""}


# ----------------------------------------------------------------------
# 是否检索
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# 覆盖记录: 交付包必须反映**研究阶段**的真实检索
# ----------------------------------------------------------------------

def test_offline_mode_never_searches(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "0")
    needed, reason = should_search(_snapshot(), _engine(_FakeKnowledge([])), SourcePolicy.user_kb)
    assert needed is False
    assert "离线" in reason


def test_search_skipped_when_policy_is_user_kb_and_library_empty(monkeypatch):
    """授权"只用用户资料库"而资料库为空时不联网 —— 但必须说明怎么才能检索。"""
    monkeypatch.setenv("THEORY_LLM", "1")
    needed, reason = should_search(_snapshot(), _engine(_FakeKnowledge([], usable=False)),
                                   SourcePolicy.user_kb)
    assert needed is False
    assert "资料库为空" in reason
    assert "自主检索" in reason, "必须告诉用户怎么开启检索, 否则用户只会看到'没有引用'"


def test_empty_library_falls_back_to_external_search_when_allowed(monkeypatch):
    """授权允许自主检索时, **本地没有资料库也要走外部检索**。

    现场反馈: 论文只有结论没有引用。根因之一就是此前"没有本地资料库"直接放弃检索,
    而外部检索 (arXiv/OpenAlex) 本来就是自主检索的正经来源。
    """
    monkeypatch.setenv("THEORY_LLM", "1")
    needed, reason = should_search(_snapshot(), _engine(_FakeKnowledge([], usable=False)),
                                   SourcePolicy.autonomous)
    assert needed is True
    assert "外部检索" in reason


def test_search_skipped_when_evidence_already_present(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "1")
    needed, reason = should_search(_snapshot(with_evidence=True),
                                   _engine(_FakeKnowledge([_ref("x")])), SourcePolicy.user_kb)
    assert needed is False
    assert "已产生证据" in reason


def test_offline_bundle_reports_why_and_keeps_references_empty(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "0")
    bundle = gather_publication_evidence(_snapshot(), _engine(_FakeKnowledge([_ref("x")])))
    assert bundle.executed is False
    assert bundle.references.references == []
    assert "离线" in bundle.coverage.scope_note
    assert any("未执行检索" in note for note in bundle.notes)


# ----------------------------------------------------------------------
# 命中 != 支持
# ----------------------------------------------------------------------

def test_hit_without_location_is_never_citable(monkeypatch):
    """命中主题词但**既无页/节定位、也无稳定外部标识** -> 不得进参考文献表。

    规则层 `assess_support` 会因"主题词出现"给部分支持, 但那条判定不看原文出处;
    没有定位就无法核对, 因此必须降为待审 (禁止靠标题相似声称支持)。
    """
    monkeypatch.setenv("THEORY_LLM", "1")
    knowledge = _FakeKnowledge([_ref("2-(211,15,1) 设计不存在: 计数关系虽自洽",
                                     excerpt="2-(211,15,1) 设计不存在: 计数关系虽自洽, "
                                             "但存在一条被违反的经典必要条件")])
    bundle = gather_publication_evidence(_snapshot(), _engine(knowledge))
    assert bundle.executed is True
    assert bundle.coverage.hits >= 1
    assert bundle.coverage.ingested == 0, bundle.notes
    assert bundle.references.references == []
    assert "未找到可引用" in bundle.coverage.scope_note


def test_arxiv_link_counts_as_locator(monkeypatch):
    """外部检索命中没有页/节定位, 但 **arXiv 链接是稳定标识**, 必须能作为定位。

    不这样做就会出现"检索到了却一条也引不了" —— 引用守门要求可定位出处,
    而外部结果的 `locator`/`page` 天然为空。
    """
    monkeypatch.setenv("THEORY_LLM", "1")
    monkeypatch.setattr("src.tools.search_tools.search_all_sources",
                        lambda query, max_results=6: [
                            _external_hit(
                                "2-(211,15,1) 设计不存在: 计数关系虽自洽",
                                "2-(211,15,1) 设计不存在: 计数关系虽自洽, "
                                "但存在一条被违反的经典必要条件",
                                url="http://arxiv.org/abs/2001.11974v3")])
    bundle = gather_publication_evidence(
        _snapshot(), _engine(None, policy=SourcePolicy.autonomous))
    assert bundle.executed is True
    assert "external" in bundle.coverage.origin
    assert bundle.coverage.ingested == 1, bundle.notes
    item = bundle.references.references[0]
    assert item.source_id or item.title
    assert "arXiv:2001.11974" in (item.location or ""), item.location


def test_hit_with_locator_becomes_citable(monkeypatch):
    """定位齐全 -> 可引用, 但规则层最多给"部分支持"。"""
    monkeypatch.setenv("THEORY_LLM", "1")
    knowledge = _FakeKnowledge([_ref("2-(211,15,1) 设计不存在: 计数关系虽自洽",
                                     excerpt="2-(211,15,1) 设计不存在: 计数关系虽自洽",
                                     locator="pp. 12-18", page=12)])
    bundle = gather_publication_evidence(_snapshot(), _engine(knowledge))
    assert bundle.coverage.ingested >= 1, bundle.notes
    assert len(bundle.references.references) == 1
    item = bundle.references.references[0]
    assert item.support in ("partially_supports", "supports")
    # 引用键必须从 ref1 开始, 供正文 `[[REF:ref1]]` 映射
    assert item.key == "ref1"


def test_unrelated_hit_is_recorded_as_not_ingested(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "1")
    knowledge = _FakeKnowledge([_ref("完全无关的标题", excerpt="与本题无关的内容",
                                     locator="p. 1", page=1)])
    bundle = gather_publication_evidence(_snapshot(), _engine(knowledge))
    assert bundle.coverage.ingested == 0
    assert bundle.coverage.hits >= 1


def test_duplicate_hits_are_deduplicated_and_counted(monkeypatch):
    """同一命中的重复只能算一次; 命中数是"式 × 结果"的原始条数, 去重单独统计。"""
    monkeypatch.setenv("THEORY_LLM", "1")
    excerpt = "2-(211,15,1) 设计不存在: 计数关系虽自洽"
    same = "2-(211,15,1) 设计不存在"
    knowledge = _FakeKnowledge([_ref(same, excerpt, "p. 1", 1),
                                _ref(same, excerpt, "p. 1", 1)])
    bundle = gather_publication_evidence(_snapshot(), _engine(knowledge))
    assert bundle.coverage.hits == 4, "两条检索式 × 每次 2 条命中"
    assert bundle.coverage.duplicates == 3
    assert len(bundle.references.references) == 1


def test_retrieval_failure_is_recorded_not_raised(monkeypatch):
    """检索抛异常时必须记进覆盖记录并继续收尾, 不能中断研究交付。"""
    monkeypatch.setenv("THEORY_LLM", "1")

    class _Boom(_FakeKnowledge):
        def search(self, query, limit=8):
            raise RuntimeError("检索后端 500")

    bundle = gather_publication_evidence(_snapshot(), _engine(_Boom([])))
    assert bundle.executed is True
    assert any("检索后端 500" in item for item in bundle.coverage.failures)
    assert any("检索失败" in note for note in bundle.notes)


# ----------------------------------------------------------------------
# 合并与重新编号
# ----------------------------------------------------------------------

def test_merge_renumbers_references_to_keep_ids_unique(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "1")
    existing = refmod.ReferenceList(references=[
        refmod.Reference(key="ref1", title="快照里的文献", source_id="s-1")])
    excerpt = "2-(211,15,1) 设计不存在: 计数关系虽自洽"
    knowledge = _FakeKnowledge([_ref("2-(211,15,1) 设计不存在", excerpt, "p. 3", 3,
                                     doc_id="s-2")])
    bundle = gather_publication_evidence(_snapshot(), _engine(knowledge), references=existing)
    keys = [item.key for item in bundle.references.references]
    assert keys == ["ref1", "ref2"], keys
    assert len(set(keys)) == len(keys)


def test_queries_are_derived_from_declared_parameters(monkeypatch):
    monkeypatch.setenv("THEORY_LLM", "1")
    knowledge = _FakeKnowledge([])
    gather_publication_evidence(_snapshot(), _engine(knowledge))
    joined = " ".join(knowledge.queries)
    assert "2-(211,15,1)" in joined, knowledge.queries
    assert "projective plane of order 14" in joined, knowledge.queries


# ----------------------------------------------------------------------
# 外部检索命中的定位与引文 (DOI / OpenAlex / arXiv)
# ----------------------------------------------------------------------

def test_stable_locator_accepts_doi_and_openalex_like_arxiv():
    """DOI / OpenAlex ID 必须与 arXiv ID 等价地充当"可定位出处"。

    实测背景: 早期实现只认 arXiv URL, 而 OpenAlex 返回的全是 DOI 链接
    (`https://doi.org/10.4230/lipics.itp.2026.19`), 于是 OpenAlex 命中的定位**恒为空**,
    被判"无可定位出处"而无法引用 —— 包括"引用某个具名定理"时找到的定理原始出处。
    """
    from src.research.publication_evidence import _stable_locator

    assert _stable_locator("http://arxiv.org/abs/2001.11974") == "arXiv:2001.11974"
    assert (_stable_locator("https://doi.org/10.4230/lipics.itp.2026.19")
            == "doi:10.4230/lipics.itp.2026.19")
    # 前缀/大小写归一
    assert _stable_locator("doi:10.1007/BF00151357") == "doi:10.1007/bf00151357"
    assert _stable_locator("", "https://doi.org/10.1016/j.disc.2007.03.016") \
        == "doi:10.1016/j.disc.2007.03.016"
    # arXiv 优先于 DOI (同一篇预印本两者都有时, arXiv 是更稳定的定位)
    assert _stable_locator("https://arxiv.org/abs/2001.11974", "10.1/x") \
        == "arXiv:2001.11974"
    # OpenAlex ID 兜底, 且能从 URL 里取
    assert _stable_locator("", "", "W7168605630") == "openalex:W7168605630"
    assert _stable_locator("https://openalex.org/works/W1980559176") \
        == "openalex:W1980559176"


def test_stable_locator_rejects_non_doi_and_empty():
    """普通 URL / 无效 DOI 不得被当成 DOI 著录 (会产出假定位)。"""
    from src.research.publication_evidence import _normalize_doi, _stable_locator

    assert _normalize_doi("https://example.com/paper.pdf") == ""
    assert _normalize_doi("10.1234") == ""          # 缺 `/`
    assert _normalize_doi("") == ""
    assert _stable_locator("https://example.com/x") == ""
    assert _stable_locator("") == ""


def test_openalex_abstract_is_reconstructed_from_inverted_index():
    """OpenAlex 的摘要倒排索引必须还原成正文 —— 否则命中"只有标题", 无法引用。"""
    from src.tools.search_tools import _openalex_abstract

    inverted = {"We": [0], "present": [1], "a": [2], "formalization": [3],
                "of": [4], "the": [5], "Bruck-Ryser-Chowla": [6], "theorem": [7]}
    assert _openalex_abstract(inverted) == \
        "We present a formalization of the Bruck-Ryser-Chowla theorem"
    # 空/异常输入不得抛错
    assert _openalex_abstract(None) == ""
    assert _openalex_abstract({}) == ""
    assert _openalex_abstract({"word": []}) == ""


def test_openalex_hit_with_doi_and_abstract_becomes_citable():
    """端到端: OpenAlex 形态的命中 -> 可定位 + 有引文 -> 可进参考文献表。

    这是"引用已有定理时最好找到对应文献"的落地点: 定理原始出处经 OpenAlex 命中后,
    必须能作为**背景相关工作**被引用 (不得声称支持本文结论), 且带 DOI 定位。
    """
    from src.rag.reference_list import build_reference_list
    from src.research.publication_evidence import _judge_support, _to_evidence
    from src.tools.search_tools import _openalex_abstract

    snapshot = _snapshot()
    hit = {
        "title": "Formalizing the Bruck-Ryser-Chowla Theorem",
        "authors": "Eric Jonathan Wang, Elif Üsküplü",
        "year": "2026",
        "venue": "DROPS",
        "api_source": "OpenAlex",
        "doi": "https://doi.org/10.4230/lipics.itp.2026.19",
        "url": "https://doi.org/10.4230/lipics.itp.2026.19",
        "openalex_id": "W7168605630",
        "abstract": _openalex_abstract(
            {"The": [0], "Bruck-Ryser-Chowla": [1], "theorem": [2],
             "for": [3], "projective": [4], "planes": [5]}),
    }
    judged = _judge_support(snapshot, _to_evidence(hit, snapshot))
    assert judged.location == "doi:10.4230/lipics.itp.2026.19", judged.location
    assert judged.excerpt, "有摘要时 excerpt 不应为空"
    assert judged.support == SupportKindOfEvidence.background, judged.support_reason

    references = build_reference_list([judged])
    assert len(references) == 1, "背景相关工作必须能进参考文献表"
    assert references.references[0].location.startswith("doi:")


def test_openalex_hit_without_abstract_stays_uncitable():
    """无摘要的命中不得凭空变成可引用 (如实保持不可引用并给出原因)。"""
    from src.research.publication_evidence import _judge_support, _to_evidence

    snapshot = _snapshot()
    hit = {
        "title": "Finite nets. II. Uniqueness and imbedding",
        "api_source": "OpenAlex",
        "doi": "https://doi.org/10.2140/pjm.1963.13.421",
        "url": "https://doi.org/10.2140/pjm.1963.13.421",
        "openalex_id": "W1980559176",
        "abstract": "",
    }
    judged = _judge_support(snapshot, _to_evidence(hit, snapshot))
    # 定位有了 (DOI), 但没有可核对引文 -> 仍不可引用, 且**必须给出原因**
    assert judged.location == "doi:10.2140/pjm.1963.13.421"
    assert judged.support == SupportKindOfEvidence.insufficient, judged.support_reason
    assert (judged.support_reason or "").strip(), "不可引用时必须记录原因"
    # 也不得因此进入参考文献表
    from src.rag.reference_list import build_reference_list

    assert len(build_reference_list([judged])) == 0


# ----------------------------------------------------------------------
# 端到端 (进程内, 不起服务): 真实 KB 对象 → 参考文献 → 正文引用 → PDF
# ----------------------------------------------------------------------

