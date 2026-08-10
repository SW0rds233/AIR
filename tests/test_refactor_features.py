"""重构功能测试：上下文预算 / 去重归一化 / 引用守门自动修复 / 撤稿排除 / DOI 存在性 / agent 循环

运行:
    python tests/test_refactor_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch, MagicMock

from src.utils.context_budget import budget_text, list_to_budgeted
from src.tools.search_tools import _norm_title, _dedup_key, merge_papers
from src.agents.citation_guard import auto_fix_citations, validate_draft_citations
from src.agents.citation_prechecker import verify_reference_list
from src.tools.citation_verifier import _doi_exists_via_crossref


# ---------- context_budget ----------

def test_budget_text_short_passthrough():
    assert budget_text("短文本", 100) == "短文本"


def test_budget_text_long_has_marker():
    text = "A" * 1000
    out = budget_text(text, 200, label="素材")
    assert len(out) < 400
    assert "省略" in out
    assert "不得虚构" in out
    assert out.startswith("A" * 100)      # 保留头部
    assert out.endswith("A" * 100)        # 保留尾部


def test_budget_text_none():
    assert budget_text(None, 100) == ""


def test_list_to_budgeted_drops_and_reports():
    items = [f"item-{i}:" + "x" * 200 for i in range(10)]
    out = list_to_budgeted(items, 500, label="论文条目")
    assert "另有" in out
    assert "未包含在本上下文中" in out
    assert "不得虚构" in out


def test_list_to_budgeted_fits():
    out = list_to_budgeted(["short", "ok"], 1000)
    assert "省略" not in out
    assert "short" in out


# ---------- 去重归一化 ----------

def test_clean_query_underscore_to_space():
    """下划线必须替换为空格而非删除（否则词拼接导致 arXiv/S2 0 命中）"""
    from src.tools.search_tools import _clean_query
    assert _clean_query("RF_fingerprinting") == "RF fingerprinting"
    assert _clean_query("device_identification 射频指纹") == "device identification 射频指纹"
    assert _clean_query("**RF fingerprinting**") == "RF fingerprinting"
    assert _clean_query("") == ""


def test_norm_title():
    assert _norm_title("RF Fingerprinting") == "rffingerprinting"
    assert _norm_title("RF_fingerprinting") == "rffingerprinting"
    # "A Survey" 中的 "a" 保留（真实单词，非分隔符）
    assert _norm_title("RF-Fingerprinting: A Survey") == "rffingerprintingasurvey"


def test_dedup_key_doi_preferred():
    a = {"title": "Paper X: Different Format", "doi": "10.1000/xyz"}
    b = {"title": "Paper X", "doi": "10.1000/xyz"}
    assert _dedup_key(a) == _dedup_key(b) == "doi:10.1000/xyz"


def test_dedup_key_title_variants():
    a = {"title": "RF Fingerprinting"}
    b = {"title": "RF_fingerprinting"}
    assert _dedup_key(a) == _dedup_key(b)


def test_merge_papers_dedup_by_doi():
    existing = [{"title": "Paper A", "doi": "10.1/a"}]
    found = [
        {"title": "Paper A Variant", "doi": "10.1/a"},   # DOI 重复 → 合并
        {"title": "Paper B", "doi": ""},                  # 新论文 → 加入
        {"title": "Paper C (same title)", "doi": ""},     # 新论文 → 加入
        {"error": "OpenAlex search failed"},              # error 条目 → 跳过
        {"doi": "10.2/c"},                                # 缺 title → 跳过
    ]
    merged = merge_papers(existing, found)
    assert len(merged) == 3
    assert merged[0]["title"] == "Paper A"
    assert "Paper B" in [p["title"] for p in merged]


# ---------- 引用守门自动修复 ----------

def test_auto_fix_replace_and_remove():
    verified = [
        {
            "ref_number": 1,
            "title": "Deep Learning for RF Fingerprinting",
            "abstract": "提出基于深度学习的射频指纹识别方法，用于无线设备认证。",
        },
        {
            "ref_number": 2,
            "title": "Wireless Device Authentication Survey",
            "abstract": "无线设备身份认证综述。",
        },
    ]
    draft = (
        "深度学习射频指纹方法 [99] 在无线设备认证中表现优异。\n"
        "完全无关的句子 [88] 这里没有任何清单关联词。"
    )
    fix = auto_fix_citations(draft, verified, [99, 88])
    # [99] 上下文含 深度学习/射频指纹/无线设备认证 → 应替换为 [1]
    assert "[1]" in fix["repaired_draft"]
    assert any(r["from"] == 99 and r["to"] == 1 for r in fix["replacements"])
    # [88] 上下文与清单无关 → 应删除标记
    assert "[88]" not in fix["repaired_draft"]
    assert 88 in fix["removals"]


def test_auto_fix_no_invalid():
    draft = "引用 [1] 和 [2]"
    fix = auto_fix_citations(draft, [{"ref_number": 1, "title": "A"}, {"ref_number": 2, "title": "B"}], [])
    assert fix["repaired_draft"] == draft
    assert fix["replacements"] == []
    assert fix["removals"] == []


def test_validate_draft_citations_with_fix():
    verified = [
        {
            "ref_number": 1,
            "title": "BERT: Pre-training of Deep Bidirectional Transformers",
            "abstract": "提出预训练模型 BERT，在自然语言理解任务上取得突破。",
        }
    ]
    draft = "预训练模型 [7] 是基础。"
    result = validate_draft_citations(draft, verified)
    assert result["invalid_citations"] == [7]
    assert "[1]" in result["fix"]["repaired_draft"]


# ---------- 撤稿论文排除 ----------

def test_precheck_excludes_retracted():
    papers = [
        {"title": "Normal Paper", "authors": "A", "year": "2020", "api_source": "arXiv"},
        {"title": "Retracted Paper", "authors": "B", "year": "2021", "api_source": "Semantic Scholar", "retracted": True},
    ]
    result = verify_reference_list(papers, limit=5)
    assert len(result["verified"]) == 1
    assert result["verified"][0]["title"] == "Normal Paper"
    assert len(result["retracted"]) == 1
    assert "已撤稿" in result["report_md"]


# ---------- DOI 存在性判定（三值语义） ----------

@patch("src.tools.citation_verifier.httpx.get")
def test_doi_exists_true(mock_get):
    mock_get.return_value.status_code = 200
    assert _doi_exists_via_crossref("10.1000/xyz") is True


@patch("src.tools.citation_verifier.httpx.get")
def test_doi_exists_false(mock_get):
    mock_get.return_value.status_code = 404
    assert _doi_exists_via_crossref("10.1000/fake") is False


@patch("src.tools.citation_verifier.httpx.get")
def test_doi_exists_unknown_on_error(mock_get):
    mock_get.side_effect = Exception("timeout")
    # None ≠ False: 网络失败不得把论文误判为伪造
    assert _doi_exists_via_crossref("10.1000/xyz") is None


@patch("src.tools.citation_verifier.httpx.get")
def test_doi_exists_none_on_empty(mock_get):
    assert _doi_exists_via_crossref("") is None
    mock_get.assert_not_called()


# ---------- literature_reviewer agent 循环 ----------

def test_format_papers_for_tool():
    from src.agents.literature_reviewer import _format_papers_for_tool

    papers = [
        {"title": "Paper One", "year": "2023", "source": "arXiv", "citations": 5, "abstract": "摘要内容"},
    ]
    out = _format_papers_for_tool(papers)
    assert "Paper One" in out
    assert "2023" in out
    assert "摘要" in out


def test_agent_loop_executes_tools_and_finishes():
    """LLM 第一轮返回 1 个工具调用，第二轮返回最终文本 → notes 应为最终文本"""
    from src.agents.literature_reviewer import _run_agent_loop, _TOOL_MAP

    first = MagicMock()
    first.content = ""
    first.response_metadata = {}
    first.tool_calls = [
        {"name": "search_all_sources", "args": {"query": "rf fingerprinting"}, "id": "call_1"}
    ]
    second = MagicMock()
    second.content = "最终综述素材"
    second.response_metadata = {}
    second.tool_calls = []

    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = [first, second]

    # 打桩 search_all_sources.func, 避免真实网络
    original = _TOOL_MAP["search_all_sources"]
    fake_tool = MagicMock()
    fake_tool.func = lambda query, max_results=10: [
        {"title": "RF Fingerprinting Survey", "year": "2023", "source": "arXiv",
         "citations": 3, "abstract": "A survey of RF fingerprinting."}
    ]
    _TOOL_MAP["search_all_sources"] = fake_tool
    try:
        all_papers: list[dict] = []
        notes = _run_agent_loop(fake_llm, [], all_papers)
    finally:
        _TOOL_MAP["search_all_sources"] = original

    assert notes == "最终综述素材"
    assert len(all_papers) == 1
    assert all_papers[0]["title"] == "RF Fingerprinting Survey"


def test_agent_loop_feeds_error_back():
    """工具抛异常 → 以 [Tool Call Error] 文本作为 tool 消息回喂"""
    from src.agents.literature_reviewer import _run_agent_loop, _TOOL_MAP
    from langchain_core.messages import ToolMessage

    first = MagicMock()
    first.content = ""
    first.response_metadata = {}
    first.tool_calls = [
        {"name": "openalex_search", "args": {"query": "q"}, "id": "call_x"}
    ]
    second = MagicMock()
    second.content = "done"
    second.response_metadata = {}
    second.tool_calls = []

    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = [first, second]

    def boom(query, max_results=10):
        raise RuntimeError("API down")

    original = _TOOL_MAP["openalex_search"]
    fake_tool = MagicMock()
    fake_tool.func = boom
    _TOOL_MAP["openalex_search"] = fake_tool
    try:
        messages = [MagicMock(), MagicMock()]  # system + human
        _run_agent_loop(fake_llm, messages, [])
    finally:
        _TOOL_MAP["openalex_search"] = original

    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert tool_msgs, "工具结果必须以 ToolMessage 回喂"
    assert "[Tool Call Error]" in tool_msgs[0].content
    assert "API down" in tool_msgs[0].content


# ---------- 中文分块自适应 + 向量库降级入库 ----------

def test_chunk_text_cjk_adaptive():
    """中文为主时块大小自动降低, 且任何块不超 chunk_size+100 硬上限"""
    from src.rag.chunker import chunk_text

    cn_text = ("射频指纹识别是无线设备安全认证的关键技术。" * 60)  # 纯中文 ~600 字符
    chunks = chunk_text(cn_text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 500 + 100 for c in chunks), "块长度超过硬上限"
    # 中文自适应: 块显著小于 500 字符上限
    assert max(len(c) for c in chunks) <= 450


def test_chunk_text_english_unchanged():
    """英文文本不触发自适应降级"""
    from src.rag.chunker import chunk_text

    en_text = "RF fingerprinting is a key technology for wireless security. " * 30
    chunks = chunk_text(en_text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    assert max(len(c) for c in chunks) > 450  # 未降级


def test_add_documents_resilient_batch_split():
    """批量入库失败 → 对半拆; 单条超长 → 截断重试"""
    from src.rag.vector_store import _add_documents_resilient
    from langchain_core.documents import Document

    class FlakyStore:
        def __init__(self):
            self.added = []
            self.calls = 0

        def add_documents(self, docs):
            self.calls += 1
            # 模拟: 任何含超长文本(>400字符)的批量请求失败
            if any(len(d.page_content) > 400 for d in docs):
                raise ValueError("400 code 20015: parameter invalid")
            self.added.extend(docs)

    store = FlakyStore()
    docs = [Document(page_content="好" * 500, metadata={"title": f"p{i}"}) for i in range(4)]
    ok, fail = _add_documents_resilient(store, docs)
    assert ok == 4  # 长块被截断后仍入库
    assert fail == 0
    # 递归拆分确实发生了
    assert store.calls >= 2


def test_add_documents_resilient_single_short_failure():
    """单条短块仍失败 → 计入失败, 不抛异常"""
    from src.rag.vector_store import _add_documents_resilient
    from langchain_core.documents import Document

    class AlwaysFail:
        def add_documents(self, docs):
            raise ValueError("boom")

    store = AlwaysFail()
    docs = [Document(page_content="short", metadata={"title": "p"})]
    ok, fail = _add_documents_resilient(store, docs)
    assert ok == 0
    assert fail == 1


if __name__ == "__main__":
    tests = [
        test_budget_text_short_passthrough,
        test_budget_text_long_has_marker,
        test_budget_text_none,
        test_list_to_budgeted_drops_and_reports,
        test_list_to_budgeted_fits,
        test_clean_query_underscore_to_space,
        test_norm_title,
        test_dedup_key_doi_preferred,
        test_dedup_key_title_variants,
        test_merge_papers_dedup_by_doi,
        test_auto_fix_replace_and_remove,
        test_auto_fix_no_invalid,
        test_validate_draft_citations_with_fix,
        test_precheck_excludes_retracted,
        test_doi_exists_true,
        test_doi_exists_false,
        test_doi_exists_unknown_on_error,
        test_doi_exists_none_on_empty,
        test_format_papers_for_tool,
        test_agent_loop_executes_tools_and_finishes,
        test_agent_loop_feeds_error_back,
        test_chunk_text_cjk_adaptive,
        test_chunk_text_english_unchanged,
        test_add_documents_resilient_batch_split,
        test_add_documents_resilient_single_short_failure,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"FAIL: {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
