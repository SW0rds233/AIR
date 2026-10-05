#!/usr/bin/env python
"""AIR (AI for Research) — 统一多智能体科研系统

用法:
    python -m src.main "研究主题" [--source-policy both]

示例:
    python -m src.main --request "研究信道变化如何影响射频指纹可分性"
"""

from __future__ import annotations

import argparse
import sys

from src.utils.console import ensure_utf8_console


def main():
    ensure_utf8_console()  # Windows 终端编码修复
    parser = argparse.ArgumentParser(
        description="统一的多智能体学术科研系统 (一个主控 + 七类角色)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.main "对所有实数 x: x**2 >= 0"
  python -m src.main --request "研究信道变化如何影响射频指纹可分性" --source-policy autonomous
  python -m src.main "图像分割方法综述"
        """,
    )
    parser.add_argument("topic", nargs="?", default="", help="研究主题（用 --request 时可不填）")
    parser.add_argument("--request", "-r", help="自然语言研究描述 (无需精确主题/关键词, Planner 自动提取)")
    parser.add_argument("--project-id", default="", help="研究项目 ID (默认 proj-team)")
    parser.add_argument("--problem-id", default="problem", help="研究问题 ID")
    parser.add_argument("--source-set-id", default="",
                        help="绑定的资料源 ID (不填则不绑定任何资料库)")
    parser.add_argument("--source-policy", default="both",
                        choices=["user_kb", "autonomous", "both"],
                        help="资料授权: user_kb=只用授权资料库; autonomous=自主检索; both=两者合并")

    args = parser.parse_args()

    if not args.topic and not args.request:
        parser.error("请提供研究主题，或用 --request 输入自然语言描述")

    print("=" * 60)
    print("  AIR (AI for Research) — 统一多智能体科研系统")
    print("=" * 60)
    if args.request:
        print(f"  研究描述: {args.request}")
    else:
        print(f"  研究主题: {args.topic}")
    print("=" * 60)
    print()

    # 统一入口 (合并计划 §3 / §15.2 / G01): **没有模式选择** —— 所有研究请求都由
    # 团队会话引擎承担 (主控派工给检索/推理/写作/审图角色, 结束后导出交付包)。
    # 旧的形式化研究循环 (`--mode theory`) 已随引擎退役删除。
    from src.graph.team_session import run_team_session

    request = args.request or args.topic
    print("启动团队研究: 主控画像/计划 → 检索 → 综合 → 写作 → 配图 → 审阅 → 交付包")
    print("-" * 60)
    try:
        summary = run_team_session(
            request,
            project_id=args.project_id or "",
            problem_id=args.problem_id or "problem",
            source_set_ids=[getattr(args, "source_set_id", "") or ""],
            # `--source-policy` 此前被硬编码成 user_kb: 命令行给了选项却不生效,
            # 用户以为已授权自主检索, 实际仍只用授权资料库。
            source_policy=getattr(args, "source_policy", "both") or "both",
        )
    except KeyboardInterrupt:
        print("\n\n团队研究已中断。")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - CLI 出错要给可读信息
        print(f"\n团队研究执行出错: {e}")
        sys.exit(1)

    print("-" * 60)
    print("=" * 60)
    print("  团队研究完成")
    print("=" * 60)
    print(f"  状态:     {summary.get('status')} ({summary.get('rounds')} 轮, "
          f"{summary.get('tasks')} 个任务)")
    print(f"  停止原因: {summary.get('stop_reason')}")
    objects = summary.get("objects", {})
    print(f"  登记对象: 来源 {objects.get('evidence', 0)} / 结论候选 "
          f"{objects.get('claim', 0)} / 稿件 {objects.get('manuscript', 0)}")
    print(f"  交付包:   {summary.get('package_dir') or '(未产出)'}")
    if summary.get("unresolved"):
        print("  未决事项:")
        for item in summary["unresolved"][:6]:
            print(f"    - {item}")
    return 0


if __name__ == "__main__":
    main()
