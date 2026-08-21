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
TABLE_PLACEHOLDER_RE = _re.compile(r"\[表\s*\d+\s*[:：]\s*.*\]")

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


def _escape_latex(text: str) -> str:
    """转义 LaTeX 特殊字符（保持数学公式 $...$ 与 LaTeX 命令不变）

    只转义普通文本中危险且常见的字符: _ & % #。
    - 下划线 _ 未转义会触发 "Missing $ inserted" (如 "Raw_I_Q")
    - 反斜杠 \\ 和花括号 {} 是命令结构, 不能转义, 否则破坏 \\textbf{} 等
    - 数学公式 $...$ 内部不转义 (如 $x_i$)
    """
    # 先保护数学公式 $...$
    math_blocks = []
    def _protect(m):
        math_blocks.append(m.group(0))
        return f"\x00MATH{len(math_blocks) - 1}\x00"
    text = _re.sub(r"\$[^$]*\$", _protect, text)

    for ch in ("_", "&", "%", "#"):
        text = text.replace(ch, "\\" + ch)

    # 恢复数学公式
    for i, blk in enumerate(math_blocks):
        text = text.replace(f"\x00MATH{i}\x00", blk)
    return text


def _md_to_latex_inline(md_text: str) -> str:
    """Markdown 行内格式 → LaTeX"""
    # **bold** → \textbf{bold}
    text = _re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", md_text)
    # *italic* → \textit{italic}
    text = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\\textit{\1}", text)
    # `code` → \texttt{code}
    text = _re.sub(r"`([^`]+)`", r"\\texttt{\1}", text)
    # 数学公式 $...$ 保留，其余 LaTeX 特殊字符转义
    text = _escape_latex(text)
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
            from pathlib import Path as _Path

            # fig_paths 可能是绝对路径或相对路径; 统一解析为绝对路径后,
            # 再换算成相对 outputs/ 的路径 (figures/xxx.png), 供 \includegraphics 使用
            full = _Path(fig_file).resolve()
            try:
                rel = full.relative_to(_Path("outputs").resolve()).as_posix()
            except ValueError:
                rel = full.name
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

        # --- 表格标题占位: [表N: caption] → 跳过 (表格内容紧随其后) ---
        if TABLE_PLACEHOLDER_RE.match(stripped):
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
        # 分段处理: 先 [n-m] 范围
        def _expand_range(m):
            a, b = int(m.group(1)), int(m.group(2))
            return "[" + ",".join(str(i) for i in range(a, b + 1)) + "]"
        line2 = CITE_RANGE_RE.sub(_expand_range, stripped)
        # 再 [n,m,...]
        line2 = CITE_LIST_RE.sub(lambda m: "\\cite{" + _re.sub(r"\s+", "", "ref" + m.group(1).replace(",", ",ref")) + "}", line2)

        # --- 标题层级: 论文标题 (#) 已在 render_latex 提取为 \title 并从正文移除,
        #     故正文 ## → \section, ### → \subsection, #### → \subsubsection。
        #     writer 的标题里带手动编号 ("1 引言"/"1.1 背景"), 而 LaTeX 会自动编号,
        #     必须剥离手动编号, 否则出现 "2 1 引言" 重复编号 ---
        if _re.match(r"^####\s", line2):
            line2 = _re.sub(r"^####\s+", "", line2)
            line2 = _re.sub(r"^\d+(\.\d+)*\s+", "", line2)
            line2 = r"\subsubsection{" + line2 + "}"
        elif _re.match(r"^###\s", line2):
            line2 = _re.sub(r"^###\s+", "", line2)
            line2 = _re.sub(r"^\d+(\.\d+)*\s+", "", line2)
            line2 = r"\subsection{" + line2 + "}"
        elif _re.match(r"^##\s", line2):
            line2 = _re.sub(r"^##\s+", "", line2)
            line2 = _re.sub(r"^\d+(\.\d+)*\s+", "", line2)
            line2 = r"\section{" + line2 + "}"
        elif _re.match(r"^#\s", line2):
            line2 = _re.sub(r"^#\s+", "", line2)
            line2 = _re.sub(r"^\d+(\.\d+)*\s+", "", line2)
            line2 = r"\section{" + line2 + "}"

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
        row_tex = sep.join(cells)
        # 行首为 [n] (引用列) 时, LaTeX 会把 \\ 或 \toprule/\midrule 后的 [..]
        # 解析为竖向间距/线宽可选参数 → "Illegal unit of measure" 编译中断,
        # 引用无法完成第二遍解析 → PDF 中全部显示 [?]。用 \relax 阻断。
        if row_tex.lstrip().startswith("["):
            row_tex = r"\relax " + row_tex
        lines.append("    " + row_tex + r" \\")
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
    from src.rag.reference_formatter import (
        strip_references_section,
        strip_evidence_markers,
        _strip_writer_statistics,
    )
    from datetime import datetime

    # 参考文献必须从草稿自身的参考文献章节构建: 该章节与正文引用编号
    # 严格一致 (renumber_citations 保证)。若改用 state 的 verified_refs,
    # 在 format_check 回退历史最优稿后编号会与正文错位 → \cite 找不到
    # \bibitem → PDF 中引用显示为 [?] (实测故障)。
    biblio = _build_thebibliography_from_draft(draft_md)

    body_md = strip_references_section(draft_md)
    body_md = strip_evidence_markers(body_md)
    body_md = _strip_writer_statistics(body_md)

    # 提取论文标题 (正文第一个 # 标题行) 用于 \title, 并从正文中移除该行
    title = topic
    tm = _re.search(r"^#\s+(.+)$", body_md, _re.MULTILINE)
    if tm:
        title = tm.group(1).strip()
        body_md = body_md[: tm.start()] + body_md[tm.end():]

    body_tex = _md_to_latex_body(body_md, fig_paths)

    preamble = (LATEX_PREAMBLE
                .replace("__TITLE__", _escape_latex(title))
                .replace("__DATE__", datetime.now().strftime("%Y-%m-%d")))
    if not biblio:
        # 草稿无参考文献章节时的兜底 (如初稿被剥离)
        biblio = _build_thebibliography(verified_refs)
    return preamble + "\n" + body_tex + "\n" + biblio + "\n" + LATEX_POSTAMBLE


def _build_thebibliography_from_draft(draft_md: str) -> str:
    r"""从草稿内嵌的参考文献章节生成 thebibliography。

    草稿正文引用 [n] 与该章节条目 [n] 由 renumber_citations 保证一一对应,
    据此构建 \bibitem{refn} 可确保 \cite 与 \bibitem 永远匹配。
    """
    m = _re.search(r"^#{1,3}\s*(?:参考文献|References)\s*$", draft_md or "", _re.M | _re.I)
    if not m:
        return ""
    entries: list[tuple[int, str]] = []
    for line in draft_md[m.end():].splitlines():
        mm = _re.match(r"^\[(\d+)\]\s*(.+)$", line.strip())
        if mm:
            entries.append((int(mm.group(1)), mm.group(2).strip()))
    if not entries:
        return ""
    max_n = max(n for n, _ in entries)
    lines = [f"\\begin{{thebibliography}}{{{max_n}}}"]
    for n, body in sorted(entries):
        # 条目里可能含下划线 (如 Raw_I_Q), 未转义会触发 "Missing $ inserted"
        lines.append(f"\\bibitem{{ref{n}}} {_escape_latex(body)}")
    lines.append(r"\end{thebibliography}")
    return "\n".join(lines)


def _build_thebibliography(verified_refs: list[dict]) -> str:
    r"""从可信清单生成 \begin{thebibliography}...\end{thebibliography}

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
        # 标题/作者里可能含下划线 (如 Raw_I_Q), 未转义会触发 "Missing $ inserted"
        body = _escape_latex(body)
        lines.append(f"\\bibitem{{ref{n}}} {body}")
    lines.append(r"\end{thebibliography}")
    return "\n".join(lines)
