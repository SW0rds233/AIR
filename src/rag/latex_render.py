from __future__ import annotations

"""LaTeX 渲染器: Markdown 初稿 → ctexart LaTeX 源文件 + xelatex 编译 PDF

管道位置: format_check 之后, finalize 之前。
保持 writer 输出 md（guard/citation_check/review 不破坏），
最后一步由本模块做格式转换——

借鉴 AI-Scientist-v2: 生成 .tex + 编译循环；
借鉴 AI-Research-SKILLs: 10 conference LaTeX 模板结构；
参考文献: BibTeX (cite key = ref{n}), \bibliographystyle{gbt7714-numerical}。
"""

import logging
import re as _re
from pathlib import Path

from src.utils.file_utils import get_timestamp

logger = logging.getLogger(__name__)

CITE_RANGE_RE = _re.compile(r"\[(\d+)-(\d+)\]")
CITE_LIST_RE = _re.compile(r"\[([\d,\s]+)\]")
FIG_PLACEHOLDER_RE = _re.compile(r"\[图\s*(\d+)\s*[:：]\s*(.+?)\]")

LATEX_PREAMBLE = r"""\documentclass[UTF8,a4paper,12pt]{ctexart}
\usepackage[hmargin=1.2in,vmargin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{hyperref}
\hypersetup{colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue}

\title{__TITLE__}
\author{AI Survey Pipeline}
\date{__DATE__}

\begin{document}
\maketitle
"""

LATEX_POSTAMBLE = r"""
\end{document}
"""


def _md_to_latex_inline(md_text: str) -> str:
    """Markdown 行内格式 → LaTeX"""
    # **bold** → \textbf{bold}
    text = _re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", md_text)
    # *italic* → \textit{italic}
    text = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\\textit{\1}", text)
    # `code` → \texttt{code}
    text = _re.sub(r"`([^`]+)`", r"\\texttt{\1}", text)
    # 数学公式 $...$ 保留
    return text


def _md_to_latex_body(md_text: str, fig_paths: list[str] | None = None) -> str:
    """Markdown 正文 → LaTeX body

    fig_paths: outputs/figures/ 下的 PNG 路径列表（[图N] → 第 N-1 个文件）
    """
    figs = fig_paths or []

    lines = md_text.split("\n")
    output = []
    in_table = False
    table_lines = []

    for line in lines:
        stripped = line.strip()

        # --- 图片占位: [图N: caption] → figure 环境 ---
        fm = FIG_PLACEHOLDER_RE.match(stripped) or FIG_PLACEHOLDER_RE.search(stripped)
        if fm:
            n = int(fm.group(1))
            cap = fm.group(2).strip()
            if fig_paths and n - 1 < len(fig_paths):
                fig_file = fig_paths[n - 1]
            else:
                fig_file = f"figures/fig_{n}.png"
            rel = fig_file.replace("\\", "/").replace("outputs/", "")
            from pathlib import Path as _Path
            full = _Path("outputs") / rel
            if full.exists():
                output.append(r"\begin{figure}[htbp]")
                output.append(r"\centering")
                output.append(f'\\includegraphics[width=0.95\\textwidth]{{{rel}}}')
                output.append(f"\\caption{{{cap}}}")
                output.append(f"\\label{{fig:{n}}}")
                output.append(r"\end{figure}")
            else:
                # 图片缺失: 文字占位（full pipeline 中 finalize 先于 latex_render 执行, 图片已就位）
                output.append(f"\\textbf{{[图{n}: {cap}]}}")
            continue

        # --- 表格: | ... | 开头 → 收集后用 pandoc 转换 ---
        if stripped.startswith("|") and "|" in stripped[1:]:
            if not _re.match(r"^\|[\s\-:|]+\|?$", stripped):
                table_lines.append(line)
            else:
                table_lines.append(line)  # 分隔行
            in_table = True
            continue
        elif in_table:
            # 表格结束 → 用 pandoc 转换
            if table_lines:
                tab = _convert_table_pandoc("\n".join(table_lines))
                output.append(tab)
                table_lines = []
            in_table = False

        # --- 引用: [n] / [n,m] / [n-m] → \cite{ref{n}} ---
        line2 = _re.sub(r"-(\d+)\]", lambda m: "," + ",".join(str(i) for i in range(0, 0)), line)
        # 分段处理: 先 [n-m] 范围
        def _expand_range(m):
            a, b = int(m.group(1)), int(m.group(2))
            return "[" + ",".join(str(i) for i in range(a, b + 1)) + "]"
        line2 = CITE_RANGE_RE.sub(_expand_range, stripped)
        # 再 [n,m,...]
        line2 = CITE_LIST_RE.sub(lambda m: "\\cite{" + _re.sub(r"\s+", "", "ref" + m.group(1).replace(",", ",ref")) + "}", line2)

        # --- 标题: # → \section{} ---
        if _re.match(r"^#\s", line2):
            line2 = _re.sub(r"^#\s+", r"\\section{", line2) + "}"
        elif _re.match(r"^##\s", line2):
            line2 = _re.sub(r"^##\s+", r"\\subsection{", line2) + "}"
        elif _re.match(r"^###\s", line2):
            line2 = _re.sub(r"^###\s+", r"\\subsubsection{", line2) + "}"

        # 行内格式
        line2 = _md_to_latex_inline(line2)

        if not stripped:
            output.append("")
        else:
            output.append(line2)

    # 未闭合的表格
    if table_lines:
        output.append(_convert_table_pandoc("\n".join(table_lines)))

    return "\n".join(output)


def _convert_table_pandoc(md_table: str) -> str:
    """将 Markdown 表格转 LaTeX tabular (simple, 不用 pandoc 的 minipage/cell 格式)"""
    rows = [r.strip() for r in md_table.split("\n") if r.strip() and not _re.match(r"^\|[\s\-:|]+\|?$", r)]
    if len(rows) < 2:
        return ""
    ncols = rows[0].count("|") - 1
    if ncols <= 0:
        return ""

    def _clean(cell: str) -> str:
        return cell.strip().replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")

    lines = [r"\begin{table}[htbp]", r"\centering", f"\\begin{{tabular}}{{{'l' * ncols}}}", r"\toprule"]
    for i, row in enumerate(rows):
        cells = [_clean(c) for c in row.split("|")[1:-1]]
        sep = " & "
        lines.append("    " + sep.join(cells) + r" \\")
        if i == 0:
            lines.append(r"\midrule")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


def render_latex(draft_md: str, topic: str, verified_refs: list[dict],
                 fig_paths: list[str] | None = None) -> str:
    r"""Markdown 初稿 → 完整 .tex 源文件

    参考文献以 \begin{thebibliography} 确定性嵌入 .tex 本身——
    不依赖 bibtex 和 .bib 外部文件（消除 bibtex 文件名/样式兼容性）。
    """
    from src.rag.reference_formatter import strip_references_section
    from datetime import datetime

    body_md = strip_references_section(draft_md)
    body_tex = _md_to_latex_body(body_md, fig_paths)

    preamble = (LATEX_PREAMBLE
                .replace("__TITLE__", topic)
                .replace("__DATE__", datetime.now().strftime("%Y-%m-%d")))
    biblio = _build_thebibliography(verified_refs)
    return preamble + "\n" + body_tex + "\n" + biblio + "\n" + LATEX_POSTAMBLE


def _build_thebibliography(verified_refs: list[dict]) -> str:
    """从可信清单生成 \begin{thebibliography}...\end{thebibliography}

    每条 [n] 的 \cite{refN} 等价于 \bibitem{refN} 的标签。[1], [2], ...
    格式: GB/T 7714-2015 风格 —— 作者. 题名[J/C/EB/OL]. 出处, 年. DOI.
    """
    from src.rag.reference_formatter import format_gbt7714_entry

    if not verified_refs:
        return r"\begin{thebibliography}{99}" + "\n" + r"\end{thebibliography}"

    max_n = max(r.get("ref_number", 0) for r in verified_refs)
    lines = [f"\\begin{{thebibliography}}{{{max_n}}}"]
    for ref in verified_refs:
        n = ref.get("ref_number", "")
        entry_text = format_gbt7714_entry(ref)
        # 输出: [1] AUTHOR. Title[J]. Venue, Year. DOI.
        # 映射到: \bibitem{ref1} AUTHOR. Title[J]. Venue, Year. DOI.
        body = entry_text.split("]", 1)[-1].strip() if "]" in entry_text else entry_text
        lines.append(f"\\bibitem{{ref{n}}} {body}")
    lines.append(r"\end{thebibliography}")
    return "\n".join(lines)
