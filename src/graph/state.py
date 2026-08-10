from __future__ import annotations

from typing import TypedDict, Annotated, List, Optional
from operator import add

from langgraph.graph.message import add_messages


class PaperSummary(TypedDict, total=False):
    title: str
    authors: str
    year: str
    source: str
    abstract: str
    citations: int
    url: str
    bibtex: str


class PaperDetail(TypedDict, total=False):
    title: str
    authors: str
    year: str
    source: str
    abstract: str
    citations: int
    url: str
    bibtex: str
    full_text: str
    key_findings: str
    methodology: str
    limitations: str


class PipelineState(TypedDict, total=False):
    messages: Annotated[List, add_messages]

    research_topic: str
    topic_keywords: List[str]
    sub_topics: List[str]
    time_range: Optional[str]

    search_queries: List[str]
    search_results: List[PaperSummary]
    retrieved_papers: Annotated[List[PaperSummary], add]
    detailed_papers: List[PaperDetail]

    literature_review_notes: str
    literature_notes_path: str

    # 预写作阶段：已验证引用清单
    verified_references: List[dict]
    verified_references_path: str
    citation_precheck_report: str
    citation_precheck_verified: int
    citation_precheck_not_found: int

    # PDF 全文摄入
    ingested_papers: List[dict]
    ingestion_report: str

    paper_draft: str
    paper_outline: str
    draft_path: str
    total_words: int
    total_sections: int
    total_citations: int

    # 引用守门
    guard_report: dict
    guard_invalid_count: int

    # 引文核查
    citation_report: dict
    citation_report_path: str
    hallucinated_refs: List[str]
    citation_verified_count: int
    citation_not_found_count: int
    citation_total: int
    ref_section_missing: bool

    # 证据账本
    evidence_ledger: str
    evidence_ledger_path: str

    # 成本/用量追踪
    usage: dict
    total_cost: float
    cost_report_path: str
    figure_paths: List[str]

    # 格式检查
    format_report: dict
    format_report_path: str

    review_report: str
    review_score: int
    review_recommendation: str
    review_report_path: str

    revision_count: int

    # 修订指令（increment_revision 写入，paper_writer 读取）
    revision_prompt: str
    max_revisions: int

    current_phase: str
    error: Optional[str]
