from __future__ import annotations

"""论文格式规范校验器

功能:
1. Markdown 表格规范检查 (表头/分隔行/列数一致性)
2. 引用格式检查 (文中 [n] 与参考文献对应)
3. 图表编号检查 (图1/表1 编号连续)
4. BibTeX 补全 (为缺失 bibtex 的文献生成)
5. 中文参考文献格式转换 (GB/T 7714 风格)
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def check_markdown_tables(draft: str) -> dict:
    """检查 Markdown 表格规范性

    规则:
    - 表头行后必须有分隔行 (|---|)
    - 每行列数必须一致
    - 表格必须被空行包围 (前后空行)
    """
    lines = draft.split("\n")
    issues = []
    tables_found = 0

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 表头行: 以 | 开头或包含 | 的非分隔行
        if line.startswith("|") and "|" in line[1:]:
            # 检查是否是分隔行
            is_separator = bool(re.match(r"^\|[\s\-:|]+\|?$", line))
            if not is_separator:
                tables_found += 1
                cols = line.count("|") - (0 if line.endswith("|") else 1)
                # 下一行必须是分隔行
                if i + 1 >= len(lines):
                    issues.append(f"表格 {tables_found}: 表头后缺少分隔行")
                    i += 1
                    continue
                next_line = lines[i + 1].strip()
                if not re.match(r"^\|[\s\-:|]+\|?$", next_line):
                    issues.append(f"表格 {tables_found}: 表头后缺少分隔行 (|---|)")
                else:
                    next_cols = next_line.count("|") - (0 if next_line.endswith("|") else 1)
                    if next_cols != cols:
                        issues.append(
                            f"表格 {tables_found}: 分隔行列数 ({next_cols}) 与表头列数 ({cols}) 不一致"
                        )
                    # 检查数据行列数
                    j = i + 2
                    while j < len(lines) and lines[j].strip().startswith("|"):
                        data_cols = lines[j].count("|") - (0 if lines[j].strip().endswith("|") else 1)
                        if data_cols != cols:
                            issues.append(
                                f"表格 {tables_found} 第 {j - i} 行: 列数 {data_cols} ≠ 表头 {cols}"
                            )
                        j += 1
                # 检查表格前是否有空行
                if i > 0 and lines[i - 1].strip() and not lines[i - 1].strip().startswith("|"):
                    issues.append(f"表格 {tables_found}: 表头前缺少空行")
                # 跳过表格体
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith("|"):
                    j += 1
                if j < len(lines) and lines[j].strip():
                    issues.append(f"表格 {tables_found}: 表格后缺少空行")
                i = j
                continue
        i += 1

    return {
        "tables_found": tables_found,
        "issues": issues,
        "ok": len(issues) == 0,
    }


def check_figure_numbering(draft: str) -> dict:
    """检查图表编号是否连续 (图1, 图2, ... / 表1, 表2, ...)"""
    issues = []

    # 检查图编号
    fig_nums = [int(n) for n in re.findall(r"图\s*(\d+)", draft)]
    if fig_nums:
        expected = list(range(1, max(fig_nums) + 1))
        missing = [n for n in expected if n not in fig_nums]
        if missing:
            issues.append(f"图编号缺失: {missing}")

    # 检查表编号
    tbl_nums = [int(n) for n in re.findall(r"表\s*(\d+)", draft)]
    if tbl_nums:
        expected = list(range(1, max(tbl_nums) + 1))
        missing = [n for n in expected if n not in tbl_nums]
        if missing:
            issues.append(f"表编号缺失: {missing}")

    return {
        "figures": len(fig_nums),
        "tables": len(tbl_nums),
        "issues": issues,
        "ok": len(issues) == 0,
    }


def check_citation_format(draft: str) -> dict:
    """检查引用格式 (文中 [n] 编号连续, 无 [0], 无空引用 [])"""
    issues = []
    all_nums = [int(n) for n in re.findall(r"\[(\d+)\]", draft)]

    if 0 in all_nums:
        issues.append("存在引用编号 [0]，应为从 1 开始")

    if re.search(r"\[\s*\]", draft):
        issues.append("存在空引用 [ ]")

    # 检查编号连续性 (从1开始)
    if all_nums:
        max_n = max(all_nums)
        missing = [n for n in range(1, max_n + 1) if n not in all_nums]
        if missing:
            issues.append(f"引用编号不连续，缺失: {missing}")

    return {
        "total_citations": len(all_nums),
        "max_citation_num": max(all_nums) if all_nums else 0,
        "issues": issues,
        "ok": len(issues) == 0,
    }


def generate_bibtex(title: str, authors: str = "", year: str = "", url: str = "") -> str:
    """为缺失 BibTeX 的文献生成条目"""
    key_parts = []
    if authors:
        first_author = authors.split(",")[0].strip().split()[-1]
        key_parts.append(first_author)
    else:
        key_parts.append("unknown")
    if year:
        key_parts.append(year)
    key = "_".join(key_parts).lower()

    entry = f"@article{{{key},\n"
    entry += f"  title = {{{title}}},\n"
    if authors:
        entry += f"  author = {{{authors}}},\n"
    if year:
        entry += f"  year = {{{year}}},\n"
    if url:
        entry += f"  url = {{{url}}},\n"
    entry += "}"
    return entry


def convert_to_gbt7714(bibtex_entry: str) -> str:
    """将 BibTeX 条目转换为 GB/T 7714 中文参考文献格式

    示例: "VASWANI A, SHAZEER N, PARMAR N, et al. Attention is all you need[C].
           Advances in Neural Information Processing Systems. 2017: 5998-6008."
    """
    title = re.search(r"title\s*=\s*[{]([^}]+)[}]", bibtex_entry, re.I)
    author = re.search(r"author\s*=\s*[{]([^}]+)[}]", bibtex_entry, re.I)
    year = re.search(r"year\s*=\s*[{]?(\d{4})", bibtex_entry, re.I)

    # 作者名: "Vaswani, Ashish and Shazeer, Noam" → "VASWANI A, SHAZEER N"
    author_str = ""
    if author:
        parts = author.group(1).split(" and ")
        formatted = []
        for p in parts:
            p = p.strip()
            if "," in p:
                surname, given = p.split(",", 1)
                initials = "".join(w[0] for w in given.strip().split() if w)
                formatted.append(f"{surname.strip().upper()} {initials}")
            else:
                words = p.split()
                if len(words) >= 2:
                    formatted.append(f"{words[-1].upper()} {''.join(w[0] for w in words[:-1])}")
                else:
                    formatted.append(words[0].upper())
        author_str = ", ".join(formatted[:3])
        if len(parts) > 3:
            author_str += ", et al"

    title_str = title.group(1) if title else ""
    year_str = year.group(1) if year else ""

    result = f"{author_str}. {title_str}. {year_str}."
    return result.strip()


def format_check_report(draft: str) -> dict:
    """综合格式检查，输出报告"""
    table_result = check_markdown_tables(draft)
    fig_result = check_figure_numbering(draft)
    cite_result = check_citation_format(draft)

    lines = [
        "# 论文格式规范检查报告",
        "",
        "## 1. Markdown 表格",
        f"- 表格数量: {table_result['tables_found']}",
        f"- 状态: {'✅ 通过' if table_result['ok'] else '❌ 发现问题'}",
    ]
    for issue in table_result["issues"]:
        lines.append(f"  - ⚠️ {issue}")

    lines.extend([
        "",
        "## 2. 图表编号",
        f"- 图: {fig_result['figures']} 处 | 表: {fig_result['tables']} 处",
        f"- 状态: {'✅ 通过' if fig_result['ok'] else '❌ 发现问题'}",
    ])
    for issue in fig_result["issues"]:
        lines.append(f"  - ⚠️ {issue}")

    lines.extend([
        "",
        "## 3. 引用格式",
        f"- 文中引用总数: {cite_result['total_citations']}",
        f"- 最大引用编号: {cite_result['max_citation_num']}",
        f"- 状态: {'✅ 通过' if cite_result['ok'] else '❌ 发现问题'}",
    ])
    for issue in cite_result["issues"]:
        lines.append(f"  - ⚠️ {issue}")

    all_ok = table_result["ok"] and fig_result["ok"] and cite_result["ok"]
    lines.append("")
    lines.append(f"## 总体结论: {'✅ 格式规范' if all_ok else '❌ 存在格式问题，建议修正后定稿'}")

    return {
        "table": table_result,
        "figure": fig_result,
        "citation": cite_result,
        "all_ok": all_ok,
        "report_md": "\n".join(lines),
    }
