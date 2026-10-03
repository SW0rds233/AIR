#!/usr/bin/env python
"""AIR (AIResearch) — 多智能体科研综述论文撰写系统

技术路线：LangGraph 多智能体 + RAG (ChromaDB)
参考项目：AI-Researcher, gpt-researcher, OpenAI4S, AI-Scientist-v2

用法:
    python -m src.main "研究主题" [--keywords K1 K2 ...] [--subtopics S1 S2 ...]

示例:
    python -m src.main "基于大语言模型的对话系统综述" --keywords "LLM,对话系统,多轮对话" --subtopics "意图识别,响应生成,对话管理"
"""

from __future__ import annotations

import argparse
import sys

from src.config import MAX_REVISIONS
from src.graph.pipeline import run_pipeline
from src.utils.console import ensure_utf8_console


def _run_theory_mode(args) -> int:
    """理论研究模式: 运行研究循环并导出研究交付包。"""
    from src.graph.theory_pipeline import run_theory_pipeline
    from src.research.question_planner import build_spec_from_input

    request = args.request or args.topic
    project_id = args.project_id
    selected = args.candidate
    source_set_id = (getattr(args, "source_set_id", "") or "").strip()
    source_set_kind = getattr(args, "source_set_kind", "kb") or "kb"

    # 绑定资料源必须在研究之前校验: 不可用时明确告知, 不静默退回"无资料"
    if source_set_id:
        from src.kb.sources import validate_binding

        check = validate_binding(source_set_id)
        if not check["ok"]:
            print(f"  [错误] 资料源不可用: {check['reason']}")
            return 1
        for warning in check["warnings"]:
            print(f"  [提示] {warning}")

    # 方向输入且未指定主路线: 先展示候选问题, 由用户确认后再研究
    if selected is None:
        from src.kb.sources import build_source_summary

        spec = build_spec_from_input(
            request=request, topic=args.topic, project_id=project_id,
            problem_id=args.problem_id,
            source_summary=build_source_summary(
                source_set_id=source_set_id, source_set_kind=source_set_kind,
                request=request or args.topic or ""))
        if source_set_id:
            spec.source_set_id = source_set_id
            spec.source_set_kind = source_set_kind
        if spec.direction:
            from src.research.loop import ResearchBudget, TheoryEngine
            from src.research.store import KIND_SPEC, ResearchStore

            project_id = spec.project_id
            store = ResearchStore(project_id)
            store.put(KIND_SPEC, args.problem_id, spec.model_dump(mode="json"))
            try:
                from src.config import build_llm

                role_llm = build_llm("coordinator")
            except Exception:
                role_llm = None
            engine = TheoryEngine(spec, store, llm=role_llm, budget=ResearchBudget(
                max_actions=args.max_actions, max_tool_calls=args.max_tool_calls))
            candidates = engine.plan_candidates()
            if not candidates:
                print("  无法从研究方向生成可检验候选问题, 需要澄清 (请给出更具体的关系或表达式)")
                return 1
            print("  已生成候选研究路线, 请选择主路线:")
            for idx, c in enumerate(candidates):
                mark = " (推荐)" if c.recommended else ""
                print(f"    [{idx}]{mark} [{c.category}] {c.statement}")
                if c.verifiability:
                    print(f"         可核验性: {c.verifiability}")
                if c.difference:
                    print(f"         与已有差异: {c.difference}")
            try:
                answer = input("  请输入序号 (回车采用推荐路线): ").strip()
            except EOFError:
                answer = ""
            try:
                selected = int(answer)
            except ValueError:
                selected = next((i for i, c in enumerate(candidates) if c.recommended), 0)
            print(f"  已选择路线 [{selected}]")

    print("启动理论流水线: 问题形式化 → 研究调度 → 推导/证明 → 工具核验 → 交付门槛")
    print("-" * 60)
    final = run_theory_pipeline(
        request=request,
        topic=args.topic or args.request,
        project_id=project_id,
        problem_id=args.problem_id,
        max_actions=args.max_actions,
        max_tool_calls=args.max_tool_calls,
        resume=args.resume,
        selected_candidate=selected,
        source_set_id=source_set_id,
        source_set_kind=source_set_kind,
        source_policy=getattr(args, "source_policy", "user_kb") or "user_kb",
    )
    print("=" * 60)
    if final.get("needs_clarification"):
        print("  需要澄清: 无法从输入可靠形式化研究问题")
        for note in final.get("notes", []) or []:
            print(f"  - {note}")
        print("=" * 60)
        return 1
    print("  理论研究完成")
    print(f"  研究包目录:   {final.get('package_dir', '')}")
    print(f"  研究稿(md):   {final.get('manuscript_path', '')}")
    print(f"  论文(tex):    {final.get('tex_path', '')}")
    print(f"  快照ID:       {final.get('snapshot_id', '')}")
    print(f"  交付级别:     {final.get('delivery_level', '')}")
    print(f"  交付门槛:     {'通过' if final.get('gate_passed') else '未通过'}")
    gate_report = final.get("gate_report", "")
    if gate_report:
        print("-" * 60)
        print(gate_report)
    print("=" * 60)
    return 0


def main():
    ensure_utf8_console()  # Windows 终端编码修复
    parser = argparse.ArgumentParser(
        description="LangGraph + RAG 多智能体综述论文撰写系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.main "基于大语言模型的对话系统综述"
  python -m src.main "图像分割方法综述" --keywords "图像分割,语义分割,实例分割" --subtopics "FCN,U-Net,Transformer"
  python -m src.main "机器学习可解释性综述" --max-revisions 3
        """,
    )
    parser.add_argument("topic", nargs="?", default="", help="研究主题（综述论文主题; 用 --request 时可不填）")
    parser.add_argument("--request", "-r", help="自然语言研究描述 (无需精确主题/关键词, Planner 自动提取)")
    parser.add_argument("--keywords", "-k", nargs="+", help="核心关键词列表")
    parser.add_argument("--subtopics", "-s", nargs="+", help="子主题列表")
    parser.add_argument("--time-range", default="2019-2026", help="时间范围 (默认: 2019-2026)")
    parser.add_argument("--max-revisions", type=int, default=MAX_REVISIONS,
                        help=f"最大修改轮次 (默认: {MAX_REVISIONS})")
    parser.add_argument("--resume", action="store_true",
                        help="断点续跑: 恢复同一主题上次中断的会话")
    parser.add_argument("--skip-retrieval", action="store_true",
                        help="跳过文献检索/摄入/预验证, 复用 data/pipeline_cache 的检索产物; 大纲/草稿/审稿循环仍重新生成")
    parser.add_argument("--mode", choices=["survey", "theory"], default="survey",
                        help="运行模式: survey=综述写作 (默认), theory=理论研究循环")
    parser.add_argument("--project-id", default="", help="理论研究项目 ID (默认由主题生成)")
    parser.add_argument("--problem-id", default="problem", help="理论研究问题 ID")
    parser.add_argument("--source-set-id", default="",
                        help="理论模式绑定的资料源 ID (不填则不绑定任何资料库)")
    parser.add_argument("--source-set-kind", default="kb", choices=["kb", "files", "dataset"],
                        help="资料源类型: kb=文献知识底座, dataset=结构化数据")
    parser.add_argument("--source-policy", default="user_kb",
                        choices=["user_kb", "autonomous", "both"],
                        help="资料授权: user_kb=只用授权资料库; autonomous=自主检索; both=两者合并")
    parser.add_argument("--max-actions", type=int, default=40, help="理论模式最大研究动作数")
    parser.add_argument("--max-tool-calls", type=int, default=60, help="理论模式最大工具调用数")
    parser.add_argument("--candidate", type=int, default=None,
                        help="理论模式: 直接指定候选路线序号 (方向输入), 省略则交互确认")

    args = parser.parse_args()

    if not args.topic and not args.request:
        parser.error("请提供研究主题，或用 --request 输入自然语言描述")

    print("=" * 60)
    print("  AIR (AIResearch) — 多智能体科研综述论文撰写系统")
    print("=" * 60)
    if args.request:
        print(f"  研究描述: {args.request}")
    else:
        print(f"  研究主题: {args.topic}")
    if args.keywords:
        print(f"  关键词:   {', '.join(args.keywords)}")
    if args.subtopics:
        print(f"  子主题:   {', '.join(args.subtopics)}")
    print(f"  时间范围: {args.time_range}")
    print(f"  最大修改: {args.max_revisions} 轮")
    if args.resume:
        print("  模式:     断点续跑")
    if args.skip_retrieval:
        print("  模式:     跳过检索 (复用 data/pipeline_cache)")
    print("=" * 60)
    print()

    if args.mode == "theory":
        return _run_theory_mode(args)

    if args.skip_retrieval:
        print("启动流水线: 初稿撰写 -> 论文审阅 -> [修改循环] (跳过检索阶段)")
    else:
        print("启动流水线: 文献查阅 -> 初稿撰写 -> 论文审阅 -> [修改循环]")
    print("-" * 60)

    try:
        final_state = run_pipeline(
            topic=args.topic,
            keywords=args.keywords,
            sub_topics=args.subtopics,
            time_range=args.time_range,
            max_revisions=args.max_revisions,
            resume=args.resume,
            skip_retrieval=args.skip_retrieval,
            request=args.request,
        )
    except KeyboardInterrupt:
        print("\n\n流水线已中断。")
        sys.exit(1)
    except Exception as e:
        print(f"\n流水线执行出错: {e}")
        sys.exit(1)

    print("-" * 60)
    print()
    print("=" * 60)
    print("  流水线执行完成")
    print("=" * 60)

    if final_state.get("error"):
        print(f"  错误: {final_state['error']}")
        sys.exit(1)

    review_path = final_state.get("review_report_path", "")
    draft_path = final_state.get("draft_path", "")
    tex_path = final_state.get("paper_tex_path", "")
    notes_path = final_state.get("literature_notes_path", "")

    print(f"  文献综述素材: {notes_path}")
    print(f"  论文初稿(md): {draft_path}")
    if tex_path:
        print(f"  论文初稿(tex):{tex_path}")
    print(f"  审稿报告:     {review_path}")
    print(f"  审稿评分:     {final_state.get('review_score', 'N/A')}/50")
    print(f"  审稿建议:     {final_state.get('review_recommendation', 'N/A')}")
    print(f"  修改轮次:     {final_state.get('revision_count', 0)}")
    print(f"  总字数:       {final_state.get('total_words', 'N/A')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
