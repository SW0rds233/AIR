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
from src.config import MAX_REVISIONS, REVIEW_ACCEPT_THRESHOLD, STAGNATION_LIMIT, OUTPUT_DIR, LANGFUSE_CONFIG
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

# 修订时回喂 Writer 的上一版论文预算: 必须容纳 20000 字上限的完整正文
# (含空白/标记约 2x), 截断会导致 Writer 无法对中段章节执行结构性修改
PRIOR_DRAFT_BUDGET = 60000


def start_node(state: PipelineState) -> dict:
    topic = state.get("research_topic", "")
    if not topic:
        return {"error": "research_topic is required", "current_phase": "start"}
    return {"current_phase": "start"}


def route_after_start(state: PipelineState) -> Literal["outline_generation", "literature_review"]:
    """skip-retrieval 模式: 跳过文献检索/摄入/预验证, 从大纲生成开始 (大纲/草稿/审阅循环全部重跑)"""
    if state.get("skip_retrieval"):
        return "outline_generation"
    return "literature_review"


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
    import time as _time

    mode = "修订" if state.get("revision_prompt") else "初稿"
    print(f"  [paper_writing] 开始{mode}撰写 (预计 2-10 分钟)...")
    _t0 = _time.monotonic()
    result = run_paper_writing(state)
    print(f"  [paper_writing] {mode}完成, 耗时 {_time.monotonic() - _t0:.0f}s")
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

    # 引用重编号: 按正文首次出现顺序 1..N, 且同步重排 verified_refs。
    # 此前只在 format_check (循环结束后) 重排, 导致审稿人看到的是 ref_number 顺序,
    # 每轮都扣"编号与正文不一致/参考文献未按出现顺序"分。
    # 现在在审稿前完成重排, 并持久化重排后的 refs 供下一轮修订使用。
    try:
        from src.rag.reference_formatter import renumber_draft_and_refs

        draft = state.get("paper_draft", "")
        verified_refs = list(state.get("verified_references", []))
        new_draft, new_refs = renumber_draft_and_refs(draft, verified_refs)
        if new_draft != draft:
            result["paper_draft"] = new_draft
            print("  [citation_check] 引用已重编号 (按首次出现顺序 1..N)")
            # 用重编号后的稿子覆盖 paper_writing 保存的初稿文件,
            # 避免用户看到编号带空档 (如缺 [2][7]) 的旧版草稿
            try:
                draft_path = state.get("draft_path", "")
                if draft_path:
                    from pathlib import Path as _P
                    _P(draft_path).write_text(new_draft, encoding="utf-8")
            except Exception:
                pass
        if new_refs != verified_refs:
            result["verified_references"] = new_refs
    except Exception as e:
        print(f"  [citation_check] 引用重编号跳过: {e}")

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

    # 保存检索产物到 data/pipeline_cache, 供 --skip-retrieval 复用 (跳过检索阶段)
    try:
        from src.utils.pipeline_cache import save_retrieval_cache

        save_retrieval_cache(
            topic=topic,
            literature_review_notes=state.get("literature_review_notes", ""),
            verified_references=state.get("verified_references", []),
            paper_outline=outline,
            retrieved_papers=list(state.get("retrieved_papers", [])),
            unfiltered_papers=list(state.get("unfiltered_papers", [])),
            keywords=state.get("topic_keywords", []),
            sub_topics=state.get("sub_topics", []),
            time_range=state.get("time_range", ""),
        )
    except Exception:
        pass

    return result


def format_check_node(state: PipelineState) -> dict:
    """格式规范检查：表格对齐/图表编号/引用格式"""
    draft = state.get("paper_draft", "")
    if not draft:
        return {"current_phase": "format_check", "format_report": {}}

    # 收敛检测: 按“硬门禁 → 评分 → 未解决问题数”的质量向量回退，
    # 避免仅凭波动的单次总分选中仍含引用/结构硬伤的稿件。
    best_draft = state.get("best_draft", "")
    best_score = state.get("best_score", 0)
    cur_score = state.get("review_score", 0)
    best_key = state.get("best_quality_key", []) or []
    cur_key = state.get("review_quality_key", []) or []
    should_restore = best_key > cur_key if best_key and cur_key else best_score > cur_score
    restored_refs = None
    if best_draft and should_restore:
        draft = best_draft
        print(f"  [format_check] 回退到历史最优稿 (质量向量 {best_key} > {cur_key})")
        # 同步回退与该稿编号配套的参考文献清单: 后续 latex 渲染/引用核对
        # 若沿用最新轮编号会与回退稿正文错位 → PDF 引用显示 [?] 或张冠李戴
        best_refs = state.get("best_verified_refs", []) or []
        if best_refs:
            restored_refs = list(best_refs)

    # 引用重编号: 按正文首次出现顺序 1..N (引文验证已在前面阶段完成, 此处安全)
    try:
        from src.rag.reference_formatter import renumber_citations

        renumbered = renumber_citations(draft)
        if renumbered != draft:
            draft = renumbered
            print("  [format_check] 引用已重编号 (按首次出现顺序 1..N)")
    except Exception as e:
        print(f"  [format_check] 引用重编号跳过: {e}")

    report = format_check_report(draft)
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    filename = f"format_report_{safe_name}_{ts}.md"
    save_file(report["report_md"], filename)
    out = {
        "current_phase": "format_check",
        "paper_draft": draft,
        "format_report": report,
        "format_report_path": filename,
    }
    if restored_refs is not None:
        out["verified_references"] = restored_refs
    return out


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

        # 编译 PDF (xelatex 双遍, 每遍上限 180s; 打印进度避免静默等待)
        pdf_ok = False
        try:
            from src.rag.latex_compiler import compile_latex, cleanup_aux_files

            print("  [latex_render] 编译 PDF (xelatex 双遍, 最多约 6 分钟)...")
            pdf_ok, log = compile_latex(str(tex_path))
            if pdf_ok:
                cleanup_aux_files(str(tex_path))
            else:
                # 落盘编译日志: 瞬时失败 (杀毒锁文件/MiKTeX 更新提示) 与真实
                # LaTeX 错误的区分必须靠日志, 仅打印 200 字不足以定位
                try:
                    log_path = Path(str(tex_path) + ".compile.log")
                    log_path.write_text(log or "", encoding="utf-8")
                    print(f"  [latex_render] 编译警告: {str(log)[:200]} (完整日志: {log_path.name})")
                except Exception:
                    print(f"  [latex_render] 编译警告: {str(log)[:200]}")
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


def _synchronize_figure_placeholders(draft: str, figure_paths: list[str]) -> str:
    """为每张实际生成的图补齐正文占位符，并尽量放入语义对应章节。"""
    import re

    if not draft or not figure_paths:
        return draft
    existing = {int(n) for n in re.findall(r"\[图\s*(\d+)\s*[:：]", draft)}
    mappings = (
        ("taxonomy", "3", "分类体系"),
        ("timeline", "2", "研究发展脉络"),
        ("trend", "2", "文献出版趋势与出处分布"),
        ("method_comp", "4", "主要方法多维对比"),
        ("comparison", "4", "主要方法多维对比"),
    )

    def insert_in_section(text: str, section_num: str, block: str) -> str:
        heading = re.search(rf"(?m)^##\s*{section_num}(?:[.、\s]|$).*", text)
        if not heading:
            # 结构异常时仍保证图片被正文引用：放在结论前，找不到则置于正文末尾。
            conclusion = re.search(r"(?m)^##\s*6(?:[.、\s]|$).*", text)
            pos = conclusion.start() if conclusion else len(text)
        else:
            next_heading = re.search(r"(?m)^##\s+", text[heading.end():])
            pos = heading.end() + next_heading.start() if next_heading else len(text)
        return text[:pos].rstrip() + "\n\n" + block + "\n\n" + text[pos:].lstrip()

    synced = draft
    for index, path in enumerate(figure_paths, start=1):
        if index in existing:
            continue
        name = Path(path).stem.lower()
        section_num, caption = "4", f"{Path(path).stem} 图示"
        for marker, target, title in mappings:
            if marker in name:
                section_num, caption = target, title
                break
        synced = insert_in_section(synced, section_num, f"[图{index}: {caption}]")
    return synced


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
            synced_draft = _synchronize_figure_placeholders(
                state.get("paper_draft", ""), fig_paths
            )
            if synced_draft != state.get("paper_draft", ""):
                result["paper_draft"] = synced_draft
                print("  [finalize] 已为全部成图补齐正文占位符")
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


def _review_quality_key(state: PipelineState, result: dict | None = None) -> list[int]:
    """构造稳定的稿件质量排序键。

    首先要求引用硬门禁全部通过，其次比较固定量表总分；同分时，未解决的
    Critical/普通问题越少越好。该排序同时用于停滞判断和历史最优稿选择。
    """
    data = result or state
    hard_defects = (
        int(state.get("citation_not_found_count", 0) or 0)
        + len(state.get("hallucinated_refs", []) or [])
        + int(state.get("guard_invalid_count", 0) or 0)
    )
    score = int(data.get("review_score", state.get("review_score", 0)) or 0)
    # 优先用 Writer 职责内的 Critical 数: 参考文献元数据等系统职责问题
    # 不应拉低稿件质量排序 (Writer 无法修复, 属系统链路责任)
    critical = data.get("review_writer_critical_count", state.get("review_writer_critical_count"))
    if critical is None:
        critical = data.get("review_critical_count", state.get("review_critical_count", 0))
    critical = int(critical or 0)
    open_issues = int(data.get("review_open_issue_count", state.get("review_open_issue_count", 0)) or 0)
    return [1 if hard_defects == 0 else 0, score, -critical, -open_issues, -hard_defects]


def paper_review_node(state: PipelineState) -> dict:
    import time as _time

    draft_len = len(state.get("paper_draft", ""))
    print(f"  [paper_review] 开始审稿 (初稿 {draft_len} 字符, 预计 2-10 分钟)...")
    _t0 = _time.monotonic()
    try:
        result = run_paper_review(state)
    except Exception as e:
        print(f"  [paper_review] 审稿节点异常: {e}, 跳过审稿继续收尾")
        return {
            "current_phase": "paper_review",
            "error": f"paper_review crashed: {e}",
            "review_score": state.get("review_score", 0),
            "review_report": "## 审稿报告\n\n审稿节点异常，跳过本轮审稿。",
        }
    print(f"  [paper_review] 审稿完成, 耗时 {_time.monotonic() - _t0:.0f}s, 评分 {result.get('review_score', 'N/A')}/50")
    report = result.get("review_report", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report and "error" not in result:
        filename = f"paper_review_{safe_name}_{ts}.md"
        review_path = save_file(report, filename)
        result["review_report_path"] = review_path

    # 收敛检测: 用稳定质量向量而非单一总分判断进步。
    score = result.get("review_score", 0)
    best_score = state.get("best_score", 0)
    best_draft = state.get("best_draft", "")
    stagnation = state.get("stagnation_count", 0)
    current_draft = state.get("paper_draft", "")
    current_key = _review_quality_key(state, result)
    previous_key = state.get("review_quality_key", []) or []
    best_key = state.get("best_quality_key", []) or []
    result["review_quality_key"] = current_key

    if not best_key or current_key > best_key:
        result["best_score"] = score
        result["best_draft"] = current_draft
        result["best_quality_key"] = current_key
        # 与最优稿编号配套的参考文献快照: 回退最优稿时必须同步恢复,
        # 否则后续用最新轮 verified_references 渲染会让 \cite 与 \bibitem 错位 → PDF 引用变 [?]
        result["best_verified_refs"] = list(state.get("verified_references", []))
        print(f"  [paper_review] 质量提升 {best_key or '[初稿]'} → {current_key}, 更新最优稿")
    else:
        result["best_score"] = best_score
        result["best_draft"] = best_draft or current_draft
        result["best_quality_key"] = best_key
        result["best_verified_refs"] = state.get("best_verified_refs", []) or list(state.get("verified_references", []))

    if not previous_key or current_key > previous_key:
        result["stagnation_count"] = 0
        print(f"  [paper_review] 相比上轮有可测量进步: {previous_key or '[首次]'} → {current_key}")
    else:
        result["stagnation_count"] = stagnation + 1
        print(f"  [paper_review] 相比上轮未提升 ({current_key} ≤ {previous_key}), 连续 {stagnation + 1} 轮")
    return result


def should_continue_review(state: PipelineState) -> Literal["paper_writing", "end"]:
    score = state.get("review_score", 0)
    revision_count = state.get("revision_count", 0)
    error = state.get("error")
    hallucinated = state.get("hallucinated_refs", []) or []
    not_found = state.get("citation_not_found_count", 0) or 0
    guard_invalid = state.get("guard_invalid_count", 0) or 0
    # 门禁只看 Writer 职责内的 Critical: 参考文献元数据等系统职责问题
    # Writer 无法修复, 计入会造成"永远有 Critical → 永远修订"的死锁
    review_critical = state.get(
        "review_writer_critical_count", state.get("review_critical_count", 0)
    ) or 0
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
        or review_critical > 0  # Reviewer 台账仍有 Writer 可修复的未关闭 Critical 问题
    )

    # 收敛检测: 连续 STAGNATION_LIMIT 轮评分无提升 → 提前终止 (避免无意义地跑满轮次)
    if state.get("stagnation_count", 0) >= STAGNATION_LIMIT:
        print(f"  [review] 连续 {STAGNATION_LIMIT} 轮评分无提升, 提前终止修订循环")
        return "end"

    if needs_fix and revision_count < max_revisions:
        return "paper_writing"

    return "end"


def _summarize_review(report: str, score: int, round_num: int) -> str:
    """从审稿报告中提取关键问题摘要，用于跨轮累积"""
    if not report:
        return ""
    import re as _re
    lines = []
    # 提取 高优先级 + 中优先级 条目
    high = _re.search(r"高优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report, _re.DOTALL)
    mid = _re.search(r"中优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report, _re.DOTALL)
    if high:
        items = [l.strip() for l in high.group(1).split("\n") if l.strip() and l.strip()[0].isdigit()]
        lines.extend(items[:4])
    if mid:
        items = [l.strip() for l in mid.group(1).split("\n") if l.strip() and l.strip()[0].isdigit()]
        lines.extend(items[:2])
    if not lines:
        return ""
    summary = f"### 第 {round_num} 轮审稿要点 (评分 {score}/50)\n"
    for item in lines:
        summary += f"- {item[:200]}\n"
    return summary + "\n"


def _resolve_review_suggestions(
    review_report: str,
    topic: str,
    existing_refs: list[dict],
    prev_blocked: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """从审稿意见中提取论文建议，搜索验证后返回三类结果:

    (new_refs, blocked, existing_hits)
    - new_refs: 验证通过、可新增进引用清单的论文
    - blocked: 审稿人推荐但无法进入清单的论文 [{title, reason}]
      (Writer 不得引用, 必须在正文中改写或删除相关描述;
      同时回喂 Reviewer, 避免其反复要求引用而无法闭环)
    - existing_hits: 审稿人推荐且**已在清单中**的论文 [{title, ref_number}]
      (Writer 可直接引用, 满足"补充某文献"类审稿意见)

    审稿人常建议 "请引用 Brik (2008)" 等，Writer 无法凭空创造引用。
    本函数在修改前自动补齐这些论文到引用清单。
    prev_blocked 中已验证不可用的论文直接跳过检索 (审稿人常无视
    "不得再索要"规则反复推荐同一批预印本, 重复检索纯浪费)。
    """
    if not review_report:
        return [], [], []

    suggestions = _extract_paper_suggestions(review_report)
    if not suggestions:
        return [], [], []

    from src.tools.search_tools import search_all_sources
    from src.tools.citation_verifier import CitationRecord
    from src.rag.reference_formatter import is_published_ref
    from src.rag.relevance_filter import has_domain_signal

    search_fn = search_all_sources.func if hasattr(search_all_sources, "func") else search_all_sources
    existing_titles = {_norm_title(r.get("title", "")) for r in existing_refs}
    existing_dois = {(r.get("doi") or "").strip().lower() for r in existing_refs if (r.get("doi") or "").strip()}
    next_num = max([r.get("ref_number", 0) for r in existing_refs], default=0)

    # 快速 DOI 检查: 检索结果自带 DOI 时无需三源验证
    try:
        from src.tools.chinese_sources import _doi_resolves
    except ImportError:
        _doi_resolves = lambda doi: False

    def _find_existing(suggestion: str) -> dict | None:
        """建议论文已在可信清单中 → 返回对应条目 (Writer 可直接引用)"""
        for r in existing_refs:
            if _title_sim(suggestion, r.get("title", "")) >= 0.85:
                return r
        return None

    new_refs = []
    blocked = []
    existing_hits = []
    prev_blocked_fps = _blocked_fingerprints(prev_blocked or [])
    for suggestion in suggestions[:8]:
        # 此前轮次已验证无法加入清单的论文: 不再重复检索, 直接维持 blocked
        if any(fp in _norm_title(suggestion) for fp in prev_blocked_fps):
            blocked.append({"title": suggestion, "reason": "此前轮次已验证无法加入清单"})
            continue
        # 已在清单中的论文: 记录编号供 Writer 直接引用 (满足"补充某文献"类意见)
        hit = _find_existing(suggestion)
        if hit is not None:
            existing_hits.append({"title": hit.get("title", suggestion), "ref_number": hit.get("ref_number")})
            continue
        try:
            results = search_fn(suggestion, 5)
        except Exception:
            results = []
        # 完整标题检索失败时, 用冒号前的主标题重试 (长标题检索常无结果)
        if not results and ":" in suggestion:
            try:
                results = search_fn(suggestion.split(":", 1)[0].strip(), 5)
            except Exception:
                results = []
        if not results:
            blocked.append({"title": suggestion, "reason": "检索无结果, 无法验证真实性"})
            continue

        # 取标题相似度最高的结果
        best = max(results, key=lambda p: _title_sim(suggestion, p.get("title", "")))
        if _title_sim(suggestion, best.get("title", "")) < 0.5:
            blocked.append({"title": suggestion, "reason": "检索结果与推荐标题不匹配"})
            continue

        # 去重: best 的归一化标题或 DOI 已在清单中 → 记为可直接引用 (避免重复编号)
        if _norm_title(best.get("title", "")) in existing_titles or (
            best.get("doi") or ""
        ).strip().lower() in existing_dois:
            dup = _find_existing(best.get("title", ""))
            if dup is not None:
                existing_hits.append({"title": dup.get("title", ""), "ref_number": dup.get("ref_number")})
            continue

        # 快速验证: 结果自带 DOI 且可解析 → 直接可信
        # 否则回退三源验证（仅个别论文，不会拖慢流水线）
        doi = (best.get("doi") or "").strip()
        if doi and _doi_resolves(doi):
            best["verified"] = True
        else:
            from src.tools.citation_verifier import verify_single_citation

            record = CitationRecord(ref_number=0, title=best.get("title", ""))
            vr = verify_single_citation(record)
            if vr.status == "NOT_FOUND":
                blocked.append({"title": suggestion, "reason": "三源交叉验证未找到, 疑似不存在"})
                continue
            best["verified"] = True

        # 用户要求: 最终参考文献只含真实已发表文献, 预印本不进入清单。
        # 但 arXiv 预印本可能已正式发表 (如 FLAME → IEEE Internet of Things Journal),
        # 先尝试标题检索 CrossRef 升级为正式版本, 再判定是否跳过。
        if not is_published_ref(best):
            from src.tools.venue_resolver import upgrade_arxiv_to_published

            if upgrade_arxiv_to_published(best):
                print(f"  [引用扩充] 预印本已解析为正式发表: {best.get('title','')[:60]}")
        if not is_published_ref(best):
            print(f"  [引用扩充] 跳过预印本 (无正式 DOI/期刊): {best.get('title','')[:60]}")
            blocked.append({"title": suggestion, "reason": "仅为预印本, 无正式发表出处"})
            continue

        # 主题相关性门: 审稿建议补录的论文标题必须含无线/RF 领域特征词,
        # 否则 (审稿人提及的架构名/方向名检索到的无关论文) 不进入清单
        if not has_domain_signal(best.get("title", "") or ""):
            print(f"  [引用扩充] 跳过无关论文 (无领域信号): {best.get('title','')[:60]}")
            blocked.append({"title": suggestion, "reason": "与主题领域不相关"})
            continue

        # 跨域负向门: 音频/图像/多媒体等领域论文不得进入清单
        from src.rag.relevance_filter import has_off_domain_signal

        if has_off_domain_signal(best.get("title", "") or ""):
            print(f"  [引用扩充] 跳过跨域论文 (音频/多媒体等): {best.get('title','')[:60]}")
            blocked.append({"title": suggestion, "reason": "跨域论文 (非射频/无线领域)"})
            continue

        # 用 CrossRef 补全/修正出处、卷期页码、年份 (审稿人常报"年份错误/卷期页缺失")
        # 不再只对"无出处"的论文补全: 有 DOI 的论文无论出处是否已有, 都补缺失字段并修正年份
        if (best.get("doi") or "").strip():
            from src.tools.venue_resolver import enrich_paper_from_doi

            enrich_paper_from_doi(best)

        next_num += 1
        best["ref_number"] = next_num
        new_refs.append(best)
        existing_titles.add(_norm_title(best.get("title", "")))
        if (best.get("doi") or "").strip():
            existing_dois.add((best.get("doi") or "").strip().lower())
        print(f"  [引用扩充] 审稿建议 → 已验证: [{next_num}] {best.get('title','')[:60]}")

    if blocked:
        print(f"  [引用扩充] {len(blocked)} 篇审稿推荐论文无法加入清单: "
              + "; ".join(b["title"][:40] for b in blocked[:5]))
    return new_refs, blocked, existing_hits


def _extract_paper_suggestions(review_report: str) -> list[str]:
    """用廉价 LLM 从审稿意见的「补充推荐论文」章节提取论文建议（标题）

    仅从第 7 节「补充推荐论文」提取: 审稿意见其他章节 (如「缺失内容」) 会提及
    "Swin Transformer" 等架构/方向名, 若一并提取会被当成引用建议, 检索到完全无关的
    论文 (如 CVPR 视频 Swin Transformer) 混入引用清单。
    """
    import re as _re
    # 定位「补充推荐论文」章节 (到下一个 ## 或文末为止)
    m = _re.search(r"补充推荐论文\s*\n(.*?)(?=\n##\s|\Z)", review_report, _re.DOTALL)
    if not m or not m.group(1).strip():
        return []
    section7 = m.group(1)[:3000]

    def _clean_line(line: str) -> str:
        """把 LLM 输出行清洗为论文标题 (兼容表格行/编号前缀)"""
        line = line.strip()
        if not line:
            return ""
        # 廉价模型常原样回显表格行 "| 1 | 标题 | 位置 | 理由 |" → 取论文列
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = [c for c in cells if c and not set(c) <= {"-", ":", " "}]
            if len(cells) >= 2:
                line = cells[1]
            else:
                return ""
        # 去掉编号/列表前缀与加粗标记
        line = _re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", line)
        return line.strip("* ").strip()

    try:
        from src.config import build_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = build_llm("cheap")
        prompt = (
            "从以下审稿人推荐的论文中提取每篇的完整论文标题。\n"
            "每行输出一个论文标题，只输出标题本身，不要表格、编号、架构名、方向名或解释。\n\n"
            f"{section7}"
        )
        result = llm.invoke([
            SystemMessage(content="你是文献信息提取助手。只输出提取的论文标题，每行一个。"),
            HumanMessage(content=prompt),
        ])
        text = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        print(f"  [warning] 论文建议提取 LLM 调用失败, 回退表格解析: {e}")
        text = section7

    suggestions = []
    for raw in text.split("\n"):
        title = _clean_line(raw)
        # 过滤明显不是论文标题的行 (太短 / 标题行 / 代码围栏 / 表头)
        if len(title) <= 15 or title.startswith(("#", "```")):
            continue
        if title.lower() in {"论文", "paper", "标题", "title"}:
            continue
        suggestions.append(title)
    return suggestions[:10]


def _norm_title(title: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _title_sim(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, _norm_title(a), _norm_title(b)).ratio()


def _strip_suggestions_section(report: str) -> str:
    """移除审稿报告中的「补充推荐论文」章节

    该章节的论文建议可能尚未验证、未加入引用清单 (如 ORACLE、Chen COMST 综述等),
    若直接回喂 Writer, Writer 会把它们写进正文并挂上错误编号 → 引用错配。
    该章节已由 _resolve_review_suggestions 搜索验证, 结果以「新增可信引用」块给出。
    """
    import re as _re
    m = _re.search(r"#{1,3}\s*\d*\.?\s*补充推荐论文", report)
    if m:
        return report[: m.start()].rstrip() + "\n"
    return report


def _extract_actionable_sections(report: str) -> str:
    """从审稿报告中提取**可操作**部分 (主要问题/细节问题/缺失内容/修改优先级),
    剥离冗长的「总体评价」「逐项评分」表。

    修订提示若包含整份审稿报告 (含每维度 100+ 字的说明), Writer 会被大量
    非操作信息淹没, 倾向扩写而非精准修复, 导致评分多轮不变。只喂可操作部分,
    Writer 才能逐条解决具体问题。
    """
    import re as _re
    start = _re.search(r"#{1,3}\s*\d*\.?\s*主要问题", report)
    if not start:
        return report
    end = _re.search(r"#{1,3}\s*\d*\.?\s*补充推荐论文", report)
    return report[start.start(): (end.start() if end else len(report))].strip()


def _build_revision_contract(state: PipelineState, limit: int = 4) -> list[dict]:
    """把开放问题收敛为本轮有限、可验收的修改契约。

    Writer 每轮只处理最多 ``limit`` 个最高优先级问题，防止自由文本审稿意见
    触发整篇重写并引入新的回退。若 Reviewer 未输出问题台账，则从高优先级段落
    退化提取，保证旧模型仍可工作。
    """
    import re as _re

    from src.agents.paper_reviewer import is_reference_metadata_issue

    priority_rank = {"critical": 0, "致命": 0, "高": 1, "中": 2, "低": 3}
    ledger = [
        dict(item) for item in (state.get("review_issue_ledger", []) or [])
        if item.get("status") != "已解决" and item.get("problem")
    ]
    # 系统职责问题 (参考文献元数据) 不进入 Writer 契约: Writer 无权修改参考文献章节,
    # 留在契约里只会消耗注意力且永远无法验收 (评分不升反降的死锁来源之一)。
    ledger = [
        item for item in ledger
        if not (item.get("system_owned") or is_reference_metadata_issue(str(item.get("problem", ""))))
    ]
    # stale 条目 (引文与当前稿不匹配, 疑似审稿人转述/幻觉) 不占优先席位,
    # 防止幻觉问题浪费修订轮次; 但契约有空位时递补进入, 避免真实问题
    # (审稿人转述引文导致误判 stale) 永远得不到修复 —— 递补条目带 stale_quote
    # 标记, 契约核验对其不做强制验收 (Writer 找不到对应文字时允许不改)。
    fresh = [i for i in ledger if not i.get("stale_quote")]
    stale = [i for i in ledger if i.get("stale_quote")]
    _by_priority = lambda item: priority_rank.get(str(item.get("priority", "高")).lower(), 2)
    fresh.sort(key=_by_priority)
    stale.sort(key=_by_priority)
    contract = fresh[:limit] + stale[: max(0, limit - len(fresh))]
    if contract:
        return contract

    report = state.get("review_report", "") or ""
    high = _re.search(r"高优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report, _re.DOTALL)
    source = high.group(1) if high else _extract_actionable_sections(report)
    items = []
    for line in source.splitlines():
        text = _re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", line).strip()
        if len(text) < 8 or text.startswith(("#", "|")):
            continue
        items.append({
            "id": f"R-FALLBACK-{len(items) + 1:02d}",
            "status": "未解决",
            "priority": "高",
            "problem": text,
            "evidence": "修订后在对应章节可直接定位到修改结果",
        })
        if len(items) >= limit:
            break
    return items


def _blocked_fingerprints(blocked: list[dict]) -> list[str]:
    """被阻论文的归一化标题指纹 (用于判断契约条目是否涉及被阻论文)"""
    fps = []
    for b in blocked or []:
        norm = _norm_title(b.get("title", ""))
        if len(norm) >= 12:
            fps.append(norm[:20])
        elif len(norm) >= 8:
            fps.append(norm)
    return fps


def _augment_contract_with_blocked(contract: list[dict], blocked: list[dict]) -> list[dict]:
    """为涉及被阻论文的契约条目显式给出唯一可行的完成方式 (删除/改写)。

    否则 Writer 倾向保留方向但不引用 → Reviewer 判「悬空阐述」→ 问题每轮复活,
    形成永远无法闭环的循环 (实测: R-REF-01 → R-REF-03 → R-REF-01 反复横跳)。
    """
    fps = _blocked_fingerprints(blocked)
    if not fps or not contract:
        return contract
    for item in contract:
        text = _norm_title(f"{item.get('problem', '')} {item.get('evidence', '')}")
        if any(fp in text for fp in fps):
            item["problem"] = (
                str(item.get("problem", ""))
                + "【处置方式：所涉论文无法加入可信清单，严禁引用或编造编号——"
                "必须删除依赖该论文的正文描述，或改用清单中主题最接近的文献改写；"
                "方向可保留，但须以限定表述收尾（如「现有研究多在闭集假设下开展」），不得悬空；"
                "若整个分类子类因此没有任何可用文献，应删除该子类或并入相邻子类，不得保留 0 文献的空子类】"
            )
    return contract


_SPARSE_KEYWORDS = ("仅1篇", "仅一篇", "仅2篇", "仅两篇", "单篇", "内容单薄", "文献单薄", "文献不足", "文献严重不足", "篇文献，内容")


def _augment_contract_sparse(contract: list[dict]) -> list[dict]:
    """文献稀缺类契约条目: 给出合并/声明稀缺的处置方式。

    否则 Writer 倾向保留单薄子类或硬凑引用 (引入不当引用, 如把音频域
    文献以"借鉴意义"引入射频综述), 审稿人反复判"内容单薄"无法闭环。
    """
    for item in contract:
        problem = str(item.get("problem", ""))
        if "处置方式" in problem:
            continue
        if any(k in problem for k in _SPARSE_KEYWORDS):
            item["problem"] = problem + (
                "【处置方式：该方向文献客观稀缺——首选把该子类内容并入主题相邻的子类并删除原子类标题；"
                "次选把该子类精简为1-2句「开放问题」说明并如实陈述稀缺原因。"
                "不得保留只有0-1篇文献的独立子类，严禁为凑数编造引用或引入主题无关的文献】"
            )
    return contract


def _format_revision_contract(items: list[dict]) -> str:
    if not items:
        return "- 未解析到结构化问题；按下方审稿意见修复可明确定位的高优先级问题。"
    lines = [
        "| ID | 优先级 | 修改边界 | 本轮必须解决的问题 | 验收标准 |",
        "|----|--------|----------|--------------------|----------|",
    ]
    from src.agents.paper_reviewer import is_reference_metadata_issue

    for item in items:
        problem = str(item.get("problem", ""))
        evidence = str(item.get("evidence", "") or "在对应章节给出可定位的修复证据")
        metadata_only = bool(item.get("system_owned")) or is_reference_metadata_issue(problem)
        boundary = "仅改正文" if not metadata_only else "仅记录，禁止改正文"
        if metadata_only:
            problem = f"参考文献元数据问题：{problem}（由系统元数据链路处理）"
        lines.append(
            f"| {item.get('id', '')} | {item.get('priority', '高')} | {boundary} | "
            f"{problem.replace('|', '／')} | {evidence.replace('|', '／')} |"
        )
    return "\n".join(lines)


def increment_revision(state: PipelineState) -> dict:
    count = state.get("revision_count", 0) + 1
    review_report = state.get("review_report", "")
    citation_report = state.get("citation_report", {})
    topic = state["research_topic"]
    prior_draft = state.get("paper_draft", "")
    score = state.get("review_score", 0)
    verified_refs = list(state.get("verified_references", []))

    from src.utils.context_budget import budget_text, budget_sections

    def _strip_refs(s: str) -> str:
        """移除参考文献章节 (修订时 Writer 已单独收到 ref_sheet, 无需重复)"""
        from src.rag.reference_formatter import strip_references_section

        return strip_references_section(s)

    # 跨轮累积的被拒论文清单: 先加载历史, 供 _resolve_review_suggestions
    # 跳过重复检索 (审稿人常反复推荐同一批无法加入清单的预印本)
    prev_blocked = list(state.get("review_blocked_suggestions", []) or [])

    # 从审稿意见中提取论文建议，搜索验证后补入引用清单;
    # 无法补入的 (blocked) 与已在清单的 (existing_hits) 显式回喂 Writer,
    # 否则 "补充某文献" 类契约条目 Writer 永远无法完成 → 审稿人反复判未解决 → 评分下降
    new_refs, blocked, existing_hits = _resolve_review_suggestions(
        review_report, topic, verified_refs, prev_blocked
    )
    verified_refs.extend(new_refs)

    # 合并本轮新被拒论文 (去重), 供 Writer 规避 + 下一轮 Reviewer 停止索要
    seen_titles = {_norm_title(b.get("title", "")) for b in prev_blocked}
    for b in blocked:
        if _norm_title(b.get("title", "")) not in seen_titles:
            prev_blocked.append(b)
            seen_titles.add(_norm_title(b.get("title", "")))

    # 累积审稿历史
    prev_history = state.get("revision_history", "")
    this_summary = _summarize_review(review_report, score, count)
    revision_contract = _build_revision_contract(state)
    # 涉及被阻论文的契约条目 → 显式附上删除/改写处置方式 (否则 Writer 无法闭环)
    revision_contract = _augment_contract_with_blocked(revision_contract, prev_blocked)
    # 文献稀缺类条目 → 附上合并/声明稀缺处置方式 (防止硬凑引用引入不当文献)
    revision_contract = _augment_contract_sparse(revision_contract)

    revision_prompt = (
        f"请根据以下审稿意见对论文进行修订。这是第 {count} 轮修订，当前评分 {score}/50。\n\n"
    )

    if prev_history:
        revision_prompt += (
            f"## 历史审稿要点（此前各轮的核心问题，检查是否已修复）\n\n{prev_history}\n\n"
        )

    revision_prompt += (
        f"## 本轮修订契约（优先且仅聚焦这些目标）\n\n"
        f"{_format_revision_contract(revision_contract)}\n\n"
        f"先满足表中验收标准；不得以整篇重写代替局部修订。\n\n"
        f"## 本轮审稿意见（用于理解契约，不得自行扩张修改范围）\n\n"
        f"{budget_text(_extract_actionable_sections(review_report), 8000, label='审稿意见')}\n"
    )

    if citation_report:
        revision_prompt += (
            f"\n## 引文核查报告（NOT_FOUND 的引用必须删除或替换为清单中的有效引用）\n\n"
            f"{budget_text(citation_report.get('report_md', ''), 8000, label='引文核查报告')}\n"
        )

    # 引用语义错配 (守门节点已删除错误引用标记): 要求 Writer 删除/改写对应的虚构正文描述
    guard_report = state.get("guard_report", {}) or {}
    sem_mismatches = guard_report.get("semantic_mismatches", []) or []
    if sem_mismatches:
        mm_block = "\n".join(
            f"- 正文「{m.get('context', '')[:70]}」中引用 [{m.get('num')}] 与作者"
            f"「{m.get('surname')}」不匹配（引用标记已删除）→ 请删除或改写该句的论文描述"
            for m in sem_mismatches[:10]
        )
        revision_prompt += (
            f"\n## 引用语义错配（必须删除或改写对应的正文描述）\n\n{mm_block}\n"
        )

    # 篇幅回喂: 上一版已超 20000 字时明确告知 Writer 必须删减, 否则修订每轮越改越长
    prior_body = _strip_refs(prior_draft)
    prior_chars = len(prior_body.replace(" ", "").replace("\n", ""))
    if prior_chars > 20000:
        revision_prompt += (
            f"\n## ⚠️ 篇幅警告（必须遵守）\n\n"
            f"上一版正文约 {prior_chars} 字，已超过 20000 字上限。本轮修订**必须删减**：\n"
            f"- 合并内容重复的段落（如挑战章节与结论的重复表述）\n"
            f"- 删除与审稿意见无关的赘述\n"
            f"- 修订后全文（不含参考文献）必须 ≤ 20000 字，宁可删减不可扩写\n"
        )

    # 上一版论文必须**完整**回喂: 截断会让 Writer 看不到中段章节,
    # 只能凭记忆复述 → 倾向原样复制、无法执行结构性修改 (契约落空的直接原因之一)
    revision_prompt += (
        f"\n## 上一版论文（在此基础上修改，保留审稿意见未涉及的章节）\n\n"
        f"{budget_sections(prior_body, PRIOR_DRAFT_BUDGET, label='上一版论文')}\n"
    )

    # 格式问题回喂: format_check 原本只在循环结束后运行一次,
    # "表编号缺失/表格不规范" 等问题从不反馈给 Writer, 每轮重复出现。
    # 这里提前对上一版草稿做格式检查, 把问题写入修订提示。
    try:
        from src.rag.format_validator import format_check_report

        fmt = format_check_report(prior_draft)
        fmt_issues = (
            fmt.get("table", {}).get("issues", [])
            + fmt.get("figure", {}).get("issues", [])
            + fmt.get("citation", {}).get("issues", [])
        )
        if fmt_issues:
            fmt_block = "\n".join(f"- {i}" for i in fmt_issues[:10])
            revision_prompt += (
                f"\n## 格式问题（必须在本轮修订中修复）\n\n{fmt_block}\n"
            )
    except Exception:
        pass

    if new_refs:
        new_ref_str = "\n".join(
            f"[{r['ref_number']}] {r.get('title','')} ({r.get('year','')}) [{r.get('venue', r.get('source',''))}]"
            for r in new_refs
        )
        revision_prompt += (
            f"\n## 新增可信引用（已根据审稿意见搜索验证，可直接使用）\n\n{new_ref_str}\n"
        )

    if existing_hits:
        hit_str = "\n".join(
            f"- [{h.get('ref_number')}] {h.get('title', '')}" for h in existing_hits[:10]
        )
        revision_prompt += (
            f"\n## 审稿人推荐且已在可信清单中的论文（审稿意见要求补充时, 直接引用这些编号）\n\n{hit_str}\n"
        )

    if prev_blocked:
        blocked_str = "\n".join(
            f"- 《{b.get('title', '')}》（{b.get('reason', '未通过验证')}）"
            for b in prev_blocked[:10]
        )
        revision_prompt += (
            f"\n## 无法加入清单的审稿推荐论文（严禁引用或虚构其编号）\n\n"
            f"{blocked_str}\n\n"
            f"这些论文未通过真实性/发表状态验证。审稿意见中涉及它们的内容，"
            f"一律改为用清单中主题最接近的文献支撑，或删除相关描述，"
            f"不得为其编造引用编号。\n"
        )

    revision_prompt += (
        f"\n## 修订要求\n\n"
        f"1. 逐条完成「本轮修订契约」，修改后应能按每条验收标准定位证据\n"
        f"2. 历史问题只做回归检查；不要把已解决问题重新改写，也不要处理契约外的低优先级意见\n"
        f"3. 删除或替换引文核查报告中标记为 NOT_FOUND 的虚构引用\n"
        f"4. 审稿意见未提及问题的章节保持原样，不要重写\n"
        f"5. 输出完整修订版论文（标题 + 摘要 + 6 章结构），不要只输出修改部分\n"
        f"6. 只能引用「可信参考文献清单」和「新增可信引用」中的文献；"
        f"审稿意见中提到的但未出现在上述清单里的论文，一律删除相关正文描述，不得写入\n"
        f"7. 修订后全文（不含参考文献）必须不超过 20000 字；如已超限，优先删减而非扩写\n"
        f"8. 保持未涉及章节的标题、关键论点和有效引用不变，避免修好一处又破坏另一处\n"
        f"9. 先在上一版中定位契约所指的章节、段落或引用号，再做最小编辑；不要凭审稿意见另起炉灶\n"
        f"10. 仅修改契约中标为‘仅改正文’的问题；‘仅记录，禁止改正文’的参考文献元数据问题，不要改写作者归属、引用句或新增引用，避免制造正文—参考文献错配\n"
        f"11. 输出前逐项复核契约目标、契约外章节和原有有效引用；不要输出修订说明或检查清单\n"
    )

    return {
        "revision_count": count,
        "revision_prompt": revision_prompt,
        "revision_contract": revision_contract,
        "revision_new_ref_numbers": [r.get("ref_number") for r in new_refs if r.get("ref_number")],
        "revision_history": prev_history + this_summary,
        "verified_references": verified_refs,
        "review_blocked_suggestions": prev_blocked,
        # 审稿人需要确定性差异对比来判断旧问题是否已解决 (否则易误判"未解决")
        "previous_paper_draft": prior_draft,
        "messages": [{"role": "user", "content": revision_prompt}],
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
    # skip-retrieval 模式: start → outline_generation (跳过检索/摄入/预验证,
    # 大纲及之后的草稿/审阅循环全部重新生成)
    graph.add_conditional_edges(
        "start",
        route_after_start,
        {
            "outline_generation": "outline_generation",
            "literature_review": "literature_review",
        },
    )
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
    skip_retrieval: bool = False,
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

    # skip-retrieval 模式: 扫描 data/pipeline_cache/{topic}.json,
    # 命中且满足条件则加载检索产物, 跳过检索直接进入撰写/审稿循环
    cached = None
    if skip_retrieval:
        try:
            from src.utils.pipeline_cache import load_retrieval_cache

            cached = load_retrieval_cache(topic)
        except Exception as e:
            print(f"  [warning] 检索缓存加载失败: {e}")
            cached = None
        if cached:
            print(f"  [skip-retrieval] 命中检索缓存 (已验证引用 {len(cached.get('verified_references', []))} 篇), "
                  f"跳过检索/摄入/预验证, 从大纲生成开始重新撰写")
        else:
            print("  [skip-retrieval] 未命中可用缓存, 回退完整检索")

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
        "skip_retrieval": bool(cached),
    }

    if cached:
        # 只加载检索产物 (文献素材 + 已验证引用), 大纲/草稿等撰写阶段产出不加载,
        # 由 outline_generation → paper_writing 循环重新生成
        initial_state["literature_review_notes"] = cached.get("literature_review_notes", "")
        initial_state["verified_references"] = cached.get("verified_references", [])
        initial_state["retrieved_papers"] = cached.get("retrieved_papers", [])
        initial_state["unfiltered_papers"] = cached.get("unfiltered_papers", [])

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
