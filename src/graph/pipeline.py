from __future__ import annotations

import json
from typing import Literal, Optional
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from src.graph.state import PipelineState
from src.agents.literature_reviewer import run_literature_review
from src.agents.paper_writer import run_paper_writing
from src.agents.paper_reviewer import run_paper_review
from src.agents.pdf_ingestor import run_pdf_ingestion
from src.agents.citation_checker import run_citation_check
from src.agents.citation_prechecker import run_citation_precheck
from src.agents.outline_generator import run_outline_generation
from src.rag.format_validator import format_check_report
from src.config import MAX_REVISIONS, REVIEW_ACCEPT_THRESHOLD, OUTPUT_DIR, LANGFUSE_CONFIG
from src.utils.console import ensure_utf8_console
from src.utils.file_utils import save_file, sanitize_filename, get_timestamp


def _get_langfuse_handler():
    """可选 Langfuse 追踪：配置了 Langfuse key 时启用

    参考: deer-flow 的 LangSmith/Langfuse tracing 集成
    兼容多种配置方式:
    - 新版: LANGFUSE_API_KEY (langfuse >= 4.x)
    - 旧版: LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY
    - host: LANGFUSE_HOST 或 LANGFUSE_BASE_URL
    """
    import os
    import logging as _logging

    # langfuse 4.x 装饰器在 client 未初始化时打印
    # "No Langfuse client ... has been initialized" 噪音 (不影响追踪),
    # 过滤该行避免刷屏
    _lf_logger = _logging.getLogger("langfuse")
    _lf_logger.addFilter(lambda r: "No Langfuse client" not in r.getMessage())

    api_key = os.getenv("LANGFUSE_API_KEY", "")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_HOST", "") or os.getenv("LANGFUSE_BASE_URL", "")

    if not (api_key or (public_key and secret_key)):
        return None
    try:
        # langfuse 4.x 通过环境变量初始化 client (SDK 内部 os.getenv 读取)
        # .env 文件由 dotenv 加载, 需显式注入进程环境变量
        import os as _os

        if api_key:
            _os.environ.setdefault("LANGFUSE_API_KEY", api_key)
            _os.environ.setdefault("LANGFUSE_PUBLIC_KEY", api_key)
        if secret_key:
            _os.environ.setdefault("LANGFUSE_SECRET_KEY", secret_key)
        if public_key and not _os.environ.get("LANGFUSE_PUBLIC_KEY"):
            _os.environ.setdefault("LANGFUSE_PUBLIC_KEY", public_key)
        if host:
            _os.environ.setdefault("LANGFUSE_HOST", host)

        # langfuse 4.x: 必须先初始化全局 client (get_client), 否则 handler 报
        # "No Langfuse client ... has been initialized"
        from langfuse import get_client as _get_lf_client

        _get_lf_client(public_key=public_key or api_key)

        from langfuse.langchain import CallbackHandler

        if public_key:
            return CallbackHandler(public_key=public_key)
        return CallbackHandler(public_key=api_key)
    except ImportError:
        print("  [warning] langfuse 未安装，跳过追踪 (pip install langfuse)")
        return None
    except Exception as e:
        print(f"  [warning] langfuse 初始化失败 ({e})，跳过追踪")
        return None


PhaseName = Literal[
    "start",
    "literature_review",
    "pdf_ingestion",
    "citation_precheck",
    "paper_writing",
    "citation_check",
    "paper_review",
]


def start_node(state: PipelineState) -> dict:
    topic = state.get("research_topic", "")
    if not topic:
        return {"error": "research_topic is required", "current_phase": "start"}
    return {"current_phase": "start"}


def literature_review_node(state: PipelineState) -> dict:
    result = run_literature_review(state)
    notes = result.get("literature_review_notes", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    notes_path = None
    if notes:
        filename = f"literature_review_notes_{safe_name}_{ts}.md"
        notes_path = save_file(notes, filename)
        result["literature_notes_path"] = notes_path
    return result


def paper_writing_node(state: PipelineState) -> dict:
    result = run_paper_writing(state)
    draft = result.get("paper_draft", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if draft and "error" not in result:
        filename = f"draft_paper_{safe_name}_{ts}.md"
        draft_path = save_file(draft, filename)
        result["draft_path"] = draft_path
    return result


def pdf_ingestion_node(state: PipelineState) -> dict:
    result = run_pdf_ingestion(state)
    report = result.get("ingestion_report", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report:
        filename = f"pdf_ingestion_report_{safe_name}_{ts}.md"
        save_file(report, filename)
    return result


def citation_guard_node(state: PipelineState) -> dict:
    """引用守门: 写作后校验引用编号是否在可信清单内"""
    from src.agents.citation_guard import run_citation_guard

    result = run_citation_guard(state)
    report = result.get("guard_report", {})
    report_md = report.get("report_md", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report_md:
        filename = f"citation_guard_{safe_name}_{ts}.md"
        save_file(report_md, filename)
        result["guard_report_path"] = filename
    return result


def citation_check_node(state: PipelineState) -> dict:
    result = run_citation_check(state)
    report = result.get("citation_report", {})
    report_md = report.get("report_md", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report_md:
        filename = f"citation_report_{safe_name}_{ts}.md"
        save_file(report_md, filename)
        result["citation_report_path"] = filename

    # 保存证据账本
    ledger = result.get("evidence_ledger", "")
    if ledger:
        filename = f"evidence_ledger_{safe_name}_{ts}.md"
        save_file(ledger, filename)
        result["evidence_ledger_path"] = filename
    return result


def citation_precheck_node(state: PipelineState) -> dict:
    result = run_citation_precheck(state)
    report = result.get("citation_precheck_report", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report:
        filename = f"citation_precheck_{safe_name}_{ts}.md"
        save_file(report, filename)
        result["verified_references_path"] = filename
    return result


def outline_generation_node(state: PipelineState) -> dict:
    """STORM 式：写作前生成论文大纲，保证结构清晰"""
    result = run_outline_generation(state)
    outline = result.get("paper_outline", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if outline and "error" not in result:
        filename = f"paper_outline_{safe_name}_{ts}.md"
        save_file(outline, filename)
        result["outline_path"] = filename
    return result


def format_check_node(state: PipelineState) -> dict:
    """格式规范检查：表格对齐/图表编号/引用格式"""
    draft = state.get("paper_draft", "")
    if not draft:
        return {"current_phase": "format_check", "format_report": {}}

    report = format_check_report(draft)
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    filename = f"format_report_{safe_name}_{ts}.md"
    save_file(report["report_md"], filename)
    return {
        "current_phase": "format_check",
        "format_report": report,
        "format_report_path": filename,
    }


def latex_render_node(state: PipelineState) -> dict:
    """LaTeX 渲染节点：Markdown 初稿 → ctexart .tex + xelatex 编译 PDF"""
    draft = state.get("paper_draft", "")
    topic = state["research_topic"]
    verified_refs = state.get("verified_references", [])
    fig_paths = state.get("figure_paths", [])

    if not draft:
        return {"current_phase": "latex_render"}

    try:
        from src.rag.latex_render import render_latex

        tex_content = render_latex(draft, topic, verified_refs, fig_paths)
        safe_name = sanitize_filename(topic)
        ts = get_timestamp()
        tex_path = save_file(tex_content, f"paper_{safe_name}_{ts}.tex")

        # 编译 PDF
        pdf_ok = False
        try:
            from src.rag.latex_compiler import compile_latex, cleanup_aux_files

            pdf_ok, log = compile_latex(str(tex_path))
            if pdf_ok:
                cleanup_aux_files(str(tex_path))
            else:
                print(f"  [latex_render] 编译警告: {log[:200]}")
        except Exception as e:
            print(f"  [latex_render] 编译跳过: {e}")

        return {
            "current_phase": "latex_render",
            "paper_tex_path": str(tex_path),
            "tex_compiled": pdf_ok,
        }
    except Exception as e:
        print(f"  [latex_render] 渲染失败: {e}")
        return {"current_phase": "latex_render"}


def finalize_node(state: PipelineState) -> dict:
    """最终节点：生成图表 + 成本/用量报告"""
    result: dict = {}

    # 生成综述图表（分类体系/时间线/趋势/方法对比, 借鉴 AI-Scientist 12 图上限）
    try:
        from src.rag.figure_generator import generate_figures_from_notes

        topic = state.get("research_topic", "")
        lit_notes = state.get("literature_review_notes", "")
        verified_refs = state.get("verified_references", [])
        fig_paths = generate_figures_from_notes(topic, lit_notes, verified_refs)
        if fig_paths:
            result["figure_paths"] = fig_paths
            print(f"  [finalize] 图表生成: {len(fig_paths)} 张 ({', '.join(Path(p).name for p in fig_paths)})")
    except Exception as e:
        print(f"  [finalize] 图表生成跳过: {e}")

    # 成本/用量报告
    try:
        from src.utils.cost_tracker import tracker

        report = tracker.summary_md()
        if report:
            ts = get_timestamp()
            filename = f"cost_report_{ts}.md"
            save_file(report, filename)
            summary = tracker.summary()
            result["usage"] = summary
            result["total_cost"] = summary["total_cost_usd"]
            result["cost_report_path"] = filename
    except Exception:
        pass

    result.setdefault("usage", {})
    result.setdefault("total_cost", 0.0)
    return result


def paper_review_node(state: PipelineState) -> dict:
    result = run_paper_review(state)
    report = result.get("review_report", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report and "error" not in result:
        filename = f"paper_review_{safe_name}_{ts}.md"
        review_path = save_file(report, filename)
        result["review_report_path"] = review_path
    return result


def should_continue_review(state: PipelineState) -> Literal["paper_writing", "end"]:
    score = state.get("review_score", 0)
    revision_count = state.get("revision_count", 0)
    error = state.get("error")
    hallucinated = state.get("hallucinated_refs", []) or []
    not_found = state.get("citation_not_found_count", 0) or 0
    guard_invalid = state.get("guard_invalid_count", 0) or 0
    max_revisions = state.get("max_revisions", MAX_REVISIONS)

    if error:
        return "end"

    # review_score 是 50 分制，需换算为 100 分制与 REVIEW_ACCEPT_THRESHOLD 比较
    score_100 = score * 2
    needs_fix = (
        score_100 < REVIEW_ACCEPT_THRESHOLD
        or not_found > 0
        or len(hallucinated) > 0
        or guard_invalid > 0  # 引用守门: 存在越界引用编号
    )

    if needs_fix and revision_count < max_revisions:
        return "paper_writing"

    return "end"


def increment_revision(state: PipelineState) -> dict:
    count = state.get("revision_count", 0) + 1
    review_report = state.get("review_report", "")
    citation_report = state.get("citation_report", {})
    topic = state["research_topic"]
    prior_draft = state.get("paper_draft", "")

    from src.utils.context_budget import budget_text

    revision_prompt = (
        f"请根据以下审稿意见对论文进行修订。这是第 {count} 轮修订。\n\n"
        f"---审稿意见---\n{budget_text(review_report, 20000, label='审稿意见')}\n---意见结束---\n"
    )

    if citation_report:
        revision_prompt += (
            f"\n---引文核查报告（必须修复其中 NOT_FOUND 的虚构引用）---\n"
            f"{budget_text(citation_report.get('report_md', ''), 8000, label='引文核查报告')}"
            f"\n---引文核查结束---\n"
        )

    # 附上上一版论文，要求在此基础上有针对性地修改，而非从头重写
    revision_prompt += (
        f"\n---上一版论文（在其基础上修改，保留符合要求的章节）---\n"
        f"{budget_text(prior_draft, 20000, label='上一版论文')}\n---上一版结束---\n"
    )

    revision_prompt += "\n请重点解决'高优先级'问题，删除或替换虚构引用，逐一修改后输出完整修订版。"

    return {
        "revision_count": count,
        "revision_prompt": revision_prompt,  # 供 paper_writer 读取
        "messages": [
            {
                "role": "user",
                "content": revision_prompt,
            }
        ],
        "current_phase": "paper_writing",
    }


def build_pipeline(checkpointer=None, persist: bool = False) -> StateGraph:
    """构建流水线图

    persist=True 时使用 SQLite 持久化 checkpointer (断点续跑, 借鉴 HKUDS)
    """
    graph = StateGraph(PipelineState)

    graph.add_node("start", start_node)
    graph.add_node("literature_review", literature_review_node)
    graph.add_node("pdf_ingestion", pdf_ingestion_node)
    graph.add_node("citation_precheck", citation_precheck_node)
    graph.add_node("outline_generation", outline_generation_node)
    graph.add_node("paper_writing", paper_writing_node)
    graph.add_node("citation_guard", citation_guard_node)
    graph.add_node("citation_check", citation_check_node)
    graph.add_node("paper_review", paper_review_node)
    graph.add_node("increment_revision", increment_revision)
    graph.add_node("format_check", format_check_node)
    graph.add_node("latex_render", latex_render_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("start")

    # 主流水线（STORM 式：预验证引用 → 大纲 → 写作 → 引用守门）
    graph.add_edge("start", "literature_review")
    graph.add_edge("literature_review", "pdf_ingestion")
    graph.add_edge("pdf_ingestion", "citation_precheck")
    graph.add_edge("citation_precheck", "outline_generation")
    graph.add_edge("outline_generation", "paper_writing")
    graph.add_edge("paper_writing", "citation_guard")
    graph.add_edge("citation_guard", "citation_check")
    graph.add_edge("citation_check", "paper_review")

    # 审阅 → 修订循环 或 收尾
    graph.add_conditional_edges(
        "paper_review",
        should_continue_review,
        {
            "paper_writing": "increment_revision",
            "end": "format_check",
        },
    )
    graph.add_edge("increment_revision", "paper_writing")
    graph.add_edge("format_check", "finalize")
    graph.add_edge("finalize", "latex_render")
    graph.add_edge("latex_render", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)


def _get_persistent_checkpointer():
    """SQLite 持久化 checkpointer (断点续跑用)"""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from src.config import DATA_DIR

        db_path = DATA_DIR / "pipeline_checkpoints.sqlite"
        conn = __import__("sqlite3").connect(str(db_path), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception as e:
        print(f"  [warning] SQLite checkpointer 不可用: {e}")
        return MemorySaver()


def run_pipeline(
    topic: str,
    keywords: list[str] = None,
    sub_topics: list[str] = None,
    time_range: str = "2019-2026",
    max_revisions: int = None,
    checkpointer = None,
    resume: bool = False,
) -> dict:
    ensure_utf8_console()  # Windows 终端编码修复

    if checkpointer is None:
        checkpointer = _get_persistent_checkpointer() if resume else MemorySaver()
    app = build_pipeline(checkpointer)

    thread_id = sanitize_filename(topic)

    if resume:
        # 断点续跑: 用同一 thread_id 恢复之前的状态 (借鉴 HKUDS 缓存续跑)
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = app.get_state(config)
            if snapshot and snapshot.values.get("research_topic"):
                print(f"  [resume] 恢复会话 {thread_id} (阶段: {snapshot.values.get('current_phase', 'unknown')})")
                for event in app.stream(None, config):
                    for node_name, node_state in event.items():
                        print(f"  [{node_name}] 完成")
                try:
                    return app.get_state(config).values
                except Exception:
                    return {}
            print(f"  [resume] 未找到会话 {thread_id}, 从头开始")
        except Exception as e:
            print(f"  [warning] 续跑失败, 从头开始: {e}")

    initial_state: PipelineState = {
        "messages": [],
        "research_topic": topic,
        "topic_keywords": keywords or [],
        "sub_topics": sub_topics or [],
        "time_range": time_range,
        "revision_count": 0,
        "max_revisions": max_revisions if max_revisions is not None else MAX_REVISIONS,
        "retrieved_papers": [],
        "current_phase": "start",
    }

    config = {"configurable": {"thread_id": thread_id}}

    langfuse_handler = _get_langfuse_handler()
    if langfuse_handler:
        config["callbacks"] = [langfuse_handler]
        print("  [langfuse] LLM 追踪已启用")

    final_state = None
    for event in app.stream(initial_state, config):
        for node_name, node_state in event.items():
            if node_name == "paper_review":
                score = node_state.get("review_score", "N/A")
                print(f"  [{node_name}] 审稿评分: {score}/50")
            elif node_name == "paper_writing":
                words = node_state.get("total_words", "N/A")
                print(f"  [{node_name}] 初稿字数: {words}")
            elif node_name == "literature_review":
                papers = len(node_state.get("retrieved_papers", []))
                print(f"  [{node_name}] 检索到: {papers} 篇论文")
            elif node_name == "pdf_ingestion":
                ingested = len(node_state.get("ingested_papers", []))
                print(f"  [{node_name}] 全文入库: {ingested} 篇论文")
            elif node_name == "citation_precheck":
                verified = node_state.get("citation_precheck_verified", 0)
                not_found = node_state.get("citation_precheck_not_found", 0)
                print(f"  [{node_name}] 引用预验证: 可信 {verified}, 未通过 {not_found}")
            elif node_name == "outline_generation":
                outline = node_state.get("paper_outline", "")
                print(f"  [{node_name}] 大纲生成: {len(outline)} 字符")
            elif node_name == "citation_guard":
                invalid = node_state.get("guard_invalid_count", 0)
                print(f"  [{node_name}] 引用守门: {'✅ 全部在可信清单内' if invalid == 0 else f'⚠️ {invalid} 个越界引用'}")
            elif node_name == "format_check":
                report = node_state.get("format_report", {})
                ok = report.get("all_ok", False)
                print(f"  [{node_name}] 格式检查: {'✅ 通过' if ok else '❌ 发现问题'}")
                if not ok:
                    tbl = report.get("table", {}).get("issues", [])
                    fig = report.get("figure", {}).get("issues", [])
                    cit = report.get("citation", {}).get("issues", [])
                    for i in (tbl + fig + cit)[:5]:
                        print(f"    ⚠️ {i}")
            elif node_name == "citation_check":
                verified = node_state.get("citation_verified_count", 0)
                not_found = node_state.get("citation_not_found_count", 0)
                total = node_state.get("citation_total", 0)
                print(f"  [{node_name}] 引用核查: {total} 条, 已验证 {verified}, 未找到 {not_found}")
            elif node_name == "finalize":
                cost = node_state.get("total_cost", 0)
                print(f"  [{node_name}] 成本统计: ${cost}")
            elif node_name == "latex_render":
                ok = node_state.get("tex_compiled", False)
                print(f"  [{node_name}] LaTeX: {'✅ PDF 已生成' if ok else '⚠ 仅 .tex (编译失败/跳过)'}")
            else:
                print(f"  [{node_name}] 完成")

    # 用 checkpointer 的完整最终状态作为返回值 (langgraph 1.x 中
    # stream 事件只含当前节点更新, 直接返回最后事件会丢失 review_score 等字段)
    try:
        final_state = app.get_state(config).values
    except Exception:
        final_state = None

    return final_state if final_state else {}
