#!/usr/bin/env python
"""AIR 对话式协作入口 (第1步+第2步: 人工确认暂停点 + 意见解析执行)

在大纲生成后、初稿完成后、每轮审稿后暂停, 等待用户确认/决策/指令。
相比 `python -m src.main` 的一次性批处理, 本入口支持边跑边确认、按意见迭代。

用法:
    python -m src.main_chat "研究主题" [--keywords K1 K2] [--subtopics S1 S2] [--skip-retrieval]

各暂停点可选操作:
- 大纲: 回车/y=确认; 输入修改意见=按意见重新生成大纲; 含"子主题/关键词"=更新范围并重新检索; 含"轮次N"=改最大修订轮次; q=停止
- 初稿: 回车/y=送审; 输入修改意见=转为修订要求(首轮修订执行); q=停止
- 审稿: 回车/y=继续修订; 输入修改意见=转为修订要求并继续修订; f/结束=接受当前稿并定稿; q=停止
- q 停止后, 会话状态已写入 SQLite 检查点 (断点续跑能力将在后续步骤完善)。
"""

from __future__ import annotations

import argparse
import sys

from langgraph.types import Command

from src.utils.console import ensure_utf8_console
from src.utils.file_utils import sanitize_filename
from src.config import MAX_REVISIONS
from src.graph.pipeline import (
    build_pipeline,
    _get_persistent_checkpointer,
    _get_langfuse_handler,
    _print_node_progress,
    _is_quit,
)

PREVIEW_LIMIT = 3000


def _preview(text: str, limit: int = PREVIEW_LIMIT) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (已截断, 完整内容共 {len(text)} 字符)"


def _present_interrupt(payload: dict) -> str:
    """展示暂停点信息, 读取用户输入并返回 (由调用方决定 resume 或停止)。"""
    itype = payload.get("type", "")
    title = payload.get("title", "")
    content = payload.get("content", "")
    path = payload.get("path", "")
    hint = payload.get("hint", "")

    print()
    print("=" * 60)
    print(f"  [人工确认] {title}")
    print("=" * 60)

    if itype == "outline":
        # 大纲较短, 全文展示便于确认
        print(content or "(空)")
    else:
        print(_preview(content))
    if path:
        print(f"\n  (完整内容见: {path})")

    if itype == "review":
        print(f"  (第 {payload.get('revision_count', '')} 轮 / 最多 {payload.get('max_revisions', '')} 轮)")

    print("-" * 60)
    print(f"  {hint}")

    try:
        return input("  > ").strip()
    except EOFError:
        return "q"


def run_interactive(
    topic: str = "",
    keywords: list[str] | None = None,
    sub_topics: list[str] | None = None,
    time_range: str = "2019-2026",
    max_revisions: int | None = None,
    skip_retrieval: bool = False,
    request: str | None = None,
) -> dict:
    ensure_utf8_console()

    checkpointer = _get_persistent_checkpointer()
    app = build_pipeline(checkpointer)

    thread_id = sanitize_filename(topic or request or "research")
    config = {"configurable": {"thread_id": thread_id}}

    langfuse_handler = _get_langfuse_handler()
    if langfuse_handler:
        config["callbacks"] = [langfuse_handler]
        print("  [langfuse] LLM 追踪已启用")

    # 自然语言请求与检索缓存加载均由入口 research_planner_node 处理
    initial_state = {
        "messages": [],
        "research_topic": topic or "",
        "research_request": (request or "").strip(),
        "topic_keywords": keywords or [],
        "sub_topics": sub_topics or [],
        "time_range": time_range,
        "revision_count": 0,
        "max_revisions": max_revisions if max_revisions is not None else MAX_REVISIONS,
        "retrieved_papers": [],
        "current_phase": "start",
        "skip_retrieval": bool(skip_retrieval),
        "interactive": True,
    }

    print()
    print("=" * 60)
    print("  AIR 对话式协作模式 (自然语言入口 + 意见解析执行)")
    print("=" * 60)
    if request:
        print(f"  研究描述: {request}")
    else:
        print(f"  研究主题: {topic}")
        if keywords:
            print(f"  关键词:   {', '.join(keywords)}")
        if sub_topics:
            print(f"  子主题:   {', '.join(sub_topics)}")
    print(f"  时间范围: {time_range}")
    print(f"  最大修改: {max_revisions if max_revisions is not None else MAX_REVISIONS} 轮")
    print("  暂停点:   研究计划 / 大纲 / 初稿 / 每轮审稿")
    print("=" * 60)

    inputs = initial_state
    stopped = False
    while True:
        interrupted = False
        for event in app.stream(inputs, config):
            if "__interrupt__" in event:
                interrupted = True
                intr = event["__interrupt__"][0]
                response = _present_interrupt(intr.value)
                if _is_quit(response):
                    stopped = True
                    print("\n  已停止 (会话状态已保存到检查点)。")
                    break
                inputs = Command(resume=response)
            else:
                for node_name, node_state in event.items():
                    _print_node_progress(node_name, node_state)
        if stopped or not interrupted:
            break

    try:
        final_state = app.get_state(config).values
    except Exception:
        final_state = {}

    if not stopped:
        print()
        print("-" * 60)
        print("=" * 60)
        print("  流水线执行完成")
        print("=" * 60)
        if final_state.get("error"):
            print(f"  错误: {final_state['error']}")
        else:
            print(f"  论文初稿(md): {final_state.get('draft_path', '')}")
            tex = final_state.get("paper_tex_path", "")
            if tex:
                print(f"  论文初稿(tex):{tex}")
            print(f"  审稿报告:     {final_state.get('review_report_path', '')}")
            print(f"  审稿评分:     {final_state.get('review_score', 'N/A')}/50")
            print(f"  修改轮次:     {final_state.get('revision_count', 0)}")
        print("=" * 60)

    return final_state if final_state else {}


def main():
    ensure_utf8_console()
    parser = argparse.ArgumentParser(
        description="AIR 对话式协作模式 (自然语言入口 + 人工确认暂停点)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.main_chat --request "我想开展射频指纹识别(RF fingerprinting, RFFI)的研究, 涉及特征提取、分类识别、对抗攻击"
  python -m src.main_chat "射频指纹识别技术综述" --skip-retrieval
  python -m src.main_chat "基于大语言模型的对话系统综述" --keywords "LLM,对话系统" --subtopics "意图识别,响应生成"
        """,
    )
    parser.add_argument("topic", nargs="?", default="", help="研究主题（可选; 用 --request 输入自然语言描述时可不填）")
    parser.add_argument("--request", "-r", help="自然语言研究描述 (无需精确主题/关键词, Planner 自动提取)")
    parser.add_argument("--keywords", "-k", nargs="+", help="核心关键词列表")
    parser.add_argument("--subtopics", "-s", nargs="+", help="子主题列表")
    parser.add_argument("--time-range", default="2019-2026", help="时间范围 (默认: 2019-2026)")
    parser.add_argument("--max-revisions", type=int, default=MAX_REVISIONS,
                        help=f"最大修改轮次 (默认: {MAX_REVISIONS})")
    parser.add_argument("--skip-retrieval", action="store_true",
                        help="跳过文献检索/摄入/预验证, 复用 data/pipeline_cache 的检索产物")

    args = parser.parse_args()

    if not args.topic and not args.request:
        parser.error("请提供研究主题，或用 --request 输入自然语言描述")

    try:
        run_interactive(
            topic=args.topic,
            keywords=args.keywords,
            sub_topics=args.subtopics,
            time_range=args.time_range,
            max_revisions=args.max_revisions,
            skip_retrieval=args.skip_retrieval,
            request=args.request,
        )
    except KeyboardInterrupt:
        print("\n\n  已中断 (会话状态已保存到检查点)。")
        sys.exit(1)
    except Exception as e:
        print(f"\n  执行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
