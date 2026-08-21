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
    unfiltered_papers: List[PaperSummary]
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
    manual_papers: List[dict]  # 人工导入的中文文献 (data/manual_pdfs/)

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
    review_dimensions: dict  # 固定 10 维逐项评分，作为下一轮复审锚点
    review_issue_ledger: List[dict]  # 跨轮稳定问题 ID / 状态 / 验收证据
    review_open_issue_count: int
    review_critical_count: int
    review_writer_critical_count: int  # 仅统计 Writer 职责内的 Critical (排除参考文献元数据等系统职责问题)
    review_blocked_suggestions: List[dict]  # 审稿推荐但系统验证失败的论文 (不得引用, 需改写/删除)
    review_quality_key: List[int]  # 硬门禁、评分、问题数构成的可比较质量向量
    review_recommendation: str
    review_report_path: str

    revision_count: int

    # 修订指令（increment_revision 写入，paper_writer 读取）
    revision_prompt: str
    revision_contract: List[dict]  # 本轮修订契约条目，paper_writer 写完后逐条核验
    revision_new_ref_numbers: List[int]  # 本轮因审稿建议新增的可信引用编号（核验"补充文献"类条目用）
    max_revisions: int
    revision_history: str  # 跨轮累积的审稿要点摘要，供后续修订参考
    previous_paper_draft: str  # 本轮修订前的稿件，供审稿人做确定性差异对比

    # 收敛检测: 跟踪历史最优稿与评分, 连续多轮无提升则提前终止
    best_score: int
    best_draft: str
    best_verified_refs: List[dict]  # 与 best_draft 编号配套的参考文献快照 (回退时同步恢复)
    best_quality_key: List[int]
    stagnation_count: int  # 连续评分无提升的轮数

    current_phase: str
    error: Optional[str]

    # 跳过检索阶段 (复用 data/pipeline_cache 的检索产物, 从 paper_writing 直接开始)
    skip_retrieval: bool
