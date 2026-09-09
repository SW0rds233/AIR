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

from src.utils.console import ensure_utf8_console
from src.graph.pipeline import run_pipeline
from src.config import MAX_REVISIONS


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
        print(f"  模式:     断点续跑")
    if args.skip_retrieval:
        print(f"  模式:     跳过检索 (复用 data/pipeline_cache)")
    print("=" * 60)
    print()

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
