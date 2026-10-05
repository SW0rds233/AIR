from __future__ import annotations

"""唯一文稿 IR → LaTeX (合并计划 §5.5 / §3.3 G17)。

为什么要新写一个渲染器
----------------------
合并前有**三套**排版入口: 综述侧 `rag/latex_render.render_latex(markdown, …)`、
理论侧 `rag/theory_render.render_latex(旧 Manuscript)`、以及出版侧
`rag/publication_render`。它们各自解析一种表示, 于是"预览 / Markdown / PDF 同源同版"
只能对其中一条路径成立 —— 这正是 G17 记录的问题。

这个渲染器只接受**唯一**的文稿 IR (`publication.schemas.Manuscript`), 因此
Markdown 渲染器与本渲染器读的是同一份块、同一份引用、同一份版本。它**不生成叙事**:
块的文本来自写作智能体或已登记对象, 这里只做"转义 + 排版 + 编号分配"。

编号 (§6.3): 引用/图表/公式编号只在渲染时分配 (`assign_render_numbers`), 因此
重排不会改变来源身份。参考文献表用 `thebibliography` + `\bibitem{refN}` (不需要
bibtex), 与 Markdown 渲染器的 `[n]` 行一一对应 —— 同一渲染编号, 两种格式。
"""

import re
from typing import Any

from src.publication.schemas import Block, Manuscript, RefKind, assign_render_numbers

__all__ = [
    "LATEX_POSTAMBLE",
    "LATEX_PREAMBLE",
    "escape_latex",
    "render_latex",
]

#: 通用中文论文前言 (ctexart + xelatex)。这里是**唯一**定义, 综述侧的 Markdown
#: 渲染器从本模块取用, 避免两处 preamble 漂移。
LATEX_PREAMBLE = r"""\documentclass[UTF8,a4paper,12pt]{ctexart}
\usepackage[hmargin=1.2in,vmargin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{tabularx}
\usepackage{hyperref}
\hypersetup{colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue}
% 可自动换行的 X 列 (左对齐), 用于宽表格撑满 \textwidth 而不越界
\newcolumntype{L}{>{\raggedright\arraybackslash}X}

\title{__TITLE__}
\author{AI Research Team}
\date{__DATE__}

\begin{document}
\maketitle
"""

LATEX_POSTAMBLE = r"""
\end{document}
"""


def escape_latex(text: str) -> str:
    """转义普通文本里的 LaTeX 特殊字符 (**保持**数学公式与已有命令)。

    只转义 `_ & % #`: `\\` 与 `{}` 是命令结构, 转义它们会破坏 `\\textbf{}`;
    `$...$` 内部是数学, 不转义 (否则 `x_i` 会被写成 `x\\_i`)。
    """
    math_blocks: list[str] = []

    def _protect(match: "re.Match[str]") -> str:
        math_blocks.append(match.group(0))
        return f"\x00MATH{len(math_blocks) - 1}\x00"

    text = re.sub(r"\$[^$]*\$", _protect, text or "")
    for ch in ("_", "&", "%", "#"):
        text = text.replace(ch, "\\" + ch)
    for index, block in enumerate(math_blocks):
        text = text.replace(f"\x00MATH{index}\x00", block)
    return text


def render_latex(manuscript: Manuscript, *, author: str = "AI Research Team",
                 date: str = "", references: dict[str, str] | None = None,
                 figures: dict[str, str] | None = None) -> str:
    """把唯一文稿 IR 渲染为可编译的 `.tex`。

    只输出**能编译**的构造: `\\ref{}` 只指向本文件里真的 `\\label{}` 过的锚点,
    `\\cite{}` 只用于参考文献表里真的存在的条目 —— 悬空引用在出版门槛里是硬失败,
    渲染器不能自己制造它。

    `references`: `{来源对象 id: 条目文本}`。编号由 `assign_render_numbers` 按正文
    首次引用顺序分配, 这个映射只提供条目的**文字**; 缺条目时退回对象 id, 不编造作者
    与年份。
    """
    numbers = assign_render_numbers(manuscript)
    citations: dict[str, int] = numbers["citations"]
    labels = dict(references or {})
    title = escape_latex(manuscript.title or "研究报告")
    lines: list[str] = [
        LATEX_PREAMBLE.replace("__TITLE__", title)
                      .replace("__DATE__", date or r"\today"),
    ]
    if author:
        lines[0] = lines[0].replace("AI Research Team", escape_latex(author))

    if (manuscript.abstract or "").strip():
        lines += [r"\begin{abstract}", escape_latex(manuscript.abstract),
                  r"\end{abstract}", ""]

    for section in manuscript.sections:
        # Manuscript 的章节名可带作者输入的编号；LaTeX 自己编号，不能出现「1 1 结果」。
        # 参考文献由下方统一生成，否则模型/离线稿件自带的该章节会再打印一次。
        if section.role == "references" or "参考文献" in (section.heading or ""):
            continue
        heading = escape_latex(_section_heading(section.heading or ""))
        lines.append(f"\\section{{{heading}}}" if heading else r"\section*{}")
        lines.append("")
        for block in section.blocks:
            lines.extend(_render_block(block, citations, figures or {}))
        lines.append("")

    lines.append(_bibliography(citations, labels))
    lines.append(LATEX_POSTAMBLE)
    return "\n".join(line for line in lines if line is not None)


def _section_heading(value: str) -> str:
    return re.sub(r"^\s*(?:\d+[.、]?\s+|[一二三四五六七八九十]+[、.]\s*)",
                  "", value).strip()


def _render_block(block: Block, citations: dict[str, int],
                  figures: dict[str, str]) -> list[str]:
    """一个块 → LaTeX 行 (公式/表格/图引用/正文各按角色排版)。"""
    out: list[str] = []
    text = escape_latex(block.text or "")
    # 结论块给一个稳定锚点: 正文里按 claim id 反查, LaTeX 里按 label 反查。
    claim_labels = [f"claim:{ref.id}" for kind, ref in zip(block.ref_kinds, block.refs)
                    if kind == RefKind.claim.value and ref.id]
    if block.role.value == "claim" and claim_labels:
        out.append(f"\\label{{{claim_labels[0]}}}")
    if block.math:
        out += ["", f"\\begin{{equation}}{block.math}\\end{{equation}}", ""]
    if text:
        out.append(text)
        out.append("")
    marks = [f"\\cite{{ref{citations[ref.id]}}}"
             for kind, ref in zip(block.ref_kinds, block.refs)
             if kind == RefKind.source.value and ref.id in citations]
    if marks:
        out.append("依据: " + " ".join(marks))
        out.append("")
    if block.role.value == "table" and block.data:
        out.extend(_render_table(block.data))
    for kind, ref in zip(block.ref_kinds, block.refs):
        if kind == RefKind.figure.value and ref.id:
            path = figures.get(ref.id, "")
            # 路径只能由交付层验证后传入；缺文件时正文如实注明，不伪装成有图。
            if re.fullmatch(r"figures/[A-Za-z0-9_-]+\.png", path):
                out.extend([r"\begin{figure}[htbp]", r"\centering",
                            f"\\includegraphics[width=0.85\\textwidth]{{{path}}}",
                            f"\\caption{{{escape_latex(block.heading or ref.id)}}}",
                            r"\end{figure}", ""])
            else:
                out.append(f"图 {escape_latex(ref.id)} 不可用。")
    return out


def _render_table(data: dict[str, Any]) -> list[str]:
    """结构化的表格块 → `tabular` (列数由表头决定, 不猜)。"""
    header = [str(h) for h in (data.get("header") or [])]
    rows = data.get("rows") or []
    if not header:
        return []
    columns = " ".join("L" if i == 0 else "L" for i in range(len(header)))
    out = [r"\begin{table}[htbp]", r"\centering",
           f"\\begin{{tabularx}}{{\\textwidth}}{{{columns}}}", r"\toprule",
           " & ".join(escape_latex(h) for h in header) + r" \\", r"\midrule"]
    for row in rows[:50]:
        cells = [escape_latex(str(c)) for c in (row or [])][: len(header)]
        cells += [""] * (len(header) - len(cells))
        out.append(" & ".join(cells) + r" \\")
    out += [r"\bottomrule", r"\end{tabularx}", r"\end{table}", ""]
    return out


def _bibliography(citations: dict[str, int], labels: dict[str, str]) -> str:
    """参考文献表: 按**渲染编号**列出, 与正文 `\\cite{refN}` 一一对应。

    没有已登记来源时仍然输出这一节并说明检索范围 —— 缺这一节会被出版门槛判为
    "不完整", 而"没有来源"是事实, 必须写出来而不是省略。
    """
    if not citations:
        return ("\\section*{参考文献}\n"
                "本次运行没有可引用的可定位来源。检索范围与授权情况见交付清单; "
                "未命中不等于相关文献不存在。\n")
    lines = [r"\section*{参考文献}", r"\begin{thebibliography}{99}"]
    for object_id, number in sorted(citations.items(), key=lambda item: item[1]):
        entry = labels.get(object_id) or object_id
        lines.append(f"\\bibitem{{ref{number}}} {escape_latex(str(entry))}")
    lines.append(r"\end{thebibliography}")
    return "\n".join(lines)
