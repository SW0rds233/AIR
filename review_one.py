"""单篇 Markdown 审稿脚本

用法:
    python review_one.py <paper.md> [主题]

读取一个 .md 论文文件, 调用项目现成的 Reviewer 智能体 (src.agents.paper_reviewer)
评审后返回 10 维评分、总分与审稿报告。

示例:
    python review_one.py outputs\\draft_paper_射频指纹_xxx.md
    python review_one.py my_paper.md "射频指纹识别技术综述"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 保证从项目根目录运行也能找到 src 包
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_draft(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"[错误] 文件不存在: {p}")
    if p.suffix.lower() != ".md":
        raise SystemExit(f"[错误] 仅支持 .md 文件, 收到: {p.suffix}")
    return p.read_text(encoding="utf-8")


def _guess_topic(draft: str, given: str | None) -> str:
    if given and given.strip():
        return given.strip()
    m = re.search(r"(?m)^#\s+(.+)$", draft)
    if m:
        return m.group(1).strip()
    return "未命名主题"


def run_review(path: str, topic: str | None = None) -> None:
    from src.agents.paper_reviewer import run_paper_review, REVIEW_DIMENSIONS

    draft = _load_draft(path)
    clean_topic = _guess_topic(draft, topic)

    # 构造 Reviewer 所需的最小 state (只做评审, 无修订上下文)
    state = {
        "research_topic": clean_topic,
        "paper_draft": draft,
        "literature_review_notes": "",
        # 以下字段留空, Reviewer 退化为"初稿评审"模式 (无上轮锚点/无差异对比)
        "review_dimensions": {},
        "review_issue_ledger": [],
        "review_blocked_suggestions": [],
        "previous_paper_draft": None,
        "revision_contract": [],
    }

    print(f"[begin] 开始审稿: {Path(path).name} (主题: {clean_topic})")
    print(f"[begin] 稿件长度: {len(draft)} 字符, Reviewer 模型见 .env 的 REVIEWER_MODEL")
    print("  正在生成审稿报告 (可能耗时 1-3 分钟)...")

    result = run_paper_review(state)

    print("\n================ 评分结果 ================")
    print(f"论文: {clean_topic}")
    total = result.get("review_score", 0)
    dims = result.get("review_dimensions", {})
    for name in REVIEW_DIMENSIONS:
        score = dims.get(name, "?")
        print(f"  {name}: {score}/5")
    print(f"  总分: {total}/50  ({total * 2}/100)")
    print(f"  建议: {result.get('review_recommendation', 'N/A')}")
    open_issues = result.get("review_open_issue_count", 0)
    critical = result.get("review_critical_count", 0)
    print(f"  开放问题: {open_issues}  |  Critical: {critical}")
    print("==========================================")

    report = result.get("review_report", "")
    if report:
        from src.utils.file_utils import save_file, sanitize_filename, get_timestamp

        ts = get_timestamp()
        fname = f"standalone_review_{sanitize_filename(clean_topic)}_{ts}.md"
        saved = save_file(report, fname)
        print(f"\n[ok] 审稿报告已保存: {saved}")
    else:
        print("\n[警告] 未生成审稿报告文本")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit("\n[用法] python review_one.py <paper.md> [主题]")

    md_path = sys.argv[1]
    topic_arg = sys.argv[2] if len(sys.argv) > 2 else None
    run_review(md_path, topic_arg)
