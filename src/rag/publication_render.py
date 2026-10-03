from __future__ import annotations

r"""期刊式论文的 Markdown / LaTeX 渲染 (方案 v2 §2 模板规格)。

为什么不能直接用 `theory_render.render_latex`
---------------------------------------------
研究稿的渲染器会把**所有** prose 块按普通文本转义 (含已生成的 `\upcite{ref1}`、
判定链里的数学符号), 而出版级稿件必须:

1. 有 `abstract` 环境、关键词、中图分类号、作者/单位占位;
2. 保留生成器写入的 LaTeX 片段 (`\upcite{...}`、数学式、表格);
3. 参考文献用确定性内嵌 `thebibliography` (不依赖 BibTeX 工具链);
4. 中文期刊式版式 (ctexart + 定理环境 + GB/T 7714 著录)。

因此本模块只做"渲染", 不做任何研究结论的改写: 结论文本来自冻结快照,
引用编号来自 `reference_list` 的单一分配。
"""

import re

from src.rag.theory_render import Manuscript

_SPECIALS = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
             "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}

# Unicode 数学/希腊符号 -> LaTeX 数学模式。
# 必要性来自实测: 判定链与证书里带 λ、≥、≡ 等字符, 而正文字体 (Latin Modern Roman)
# 没有这些字形, xelatex 只打 "Missing character" 警告并**静默留空** —— 编译"成功"的
# PDF 里那些符号是缺的 (方案 v2 §2 明确要求不得产出这种"看起来成功"的 PDF)。
# 这些字符**不在 CJK 区**, 所以 xeCJK 的 CJK 字体会兜住汉字、兜不住它们。
_UNICODE_MATH = {
    "λ": r"$\lambda$", "μ": r"$\mu$", "σ": r"$\sigma$", "π": r"$\pi$",
    "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$", "δ": r"$\delta$",
    "ε": r"$\epsilon$", "θ": r"$\theta$", "Δ": r"$\Delta$", "Σ": r"$\Sigma$",
    "≥": r"$\geq$", "≤": r"$\leq$", "≠": r"$\neq$", "≈": r"$\approx$",
    "≡": r"$\equiv$", "∈": r"$\in$", "⊂": r"$\subset$", "⊆": r"$\subseteq$",
    "∪": r"$\cup$", "∩": r"$\cap$", "×": r"$\times$", "·": r"$\cdot$",
    "→": r"$\rightarrow$", "∞": r"$\infty$", "±": r"$\pm$", "∀": r"$\forall$",
    "∃": r"$\exists$", "∑": r"$\sum$", "√": r"$\sqrt{\ }$", "∼": r"$\sim$",
    "⊕": r"$\oplus$", "⊥": r"$\perp$",
}

# 裸写命令 -> Unicode (供上面 `_BARE_MATH_COMMAND_RE` 使用)
_UNICODE_MATH_COMMANDS = {
    "\\times": "×", "\\cdot": "·", "\\leq": "≤", "\\le": "≤", "\\geq": "≥",
    "\\ge": "≥", "\\neq": "≠", "\\ne": "≠", "\\approx": "≈", "\\infty": "∞",
    "\\pm": "±", "\\in": "∈", "\\subset": "⊂", "\\cup": "∪", "\\cap": "∩",
    "\\lambda": "λ", "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ",
    "\\epsilon": "ε", "\\theta": "θ", "\\mu": "μ", "\\sigma": "σ", "\\pi": "π",
}

_KIND_ENV = {"theorem": "theorem", "lemma": "lemma", "corollary": "corollary",
             "proposition": "proposition", "claim": "proposition"}

# 引用占位 `[[REF:key]]` -> LaTeX `\upcite{key}`
_CITE_RE = re.compile(r"\[\[REF:([A-Za-z0-9_\-]+)\]\]")


_UNICODE_MATH_RE = re.compile("|".join(re.escape(s) for s in _UNICODE_MATH))

# 文本里**裸写**的 LaTeX 数学命令 (用户题面、模型输出都可能出现) 也要兜住:
# `211\times211` 未加 `$` 时 xelatex 报 "Missing $ inserted" 且整篇不输出页面
# (实测)。先转成语义相同的 Unicode 符号, 再由上面的规则统一转回数学模式。
_BARE_MATH_COMMAND_RE = re.compile(
    "|".join(re.escape(c) for c in sorted(_UNICODE_MATH_COMMANDS, key=len, reverse=True)))


def escape_latex_keep_commands(text: str) -> str:
    r"""转义 LaTeX 特殊字符, 同时规范化数学模式与 Unicode 数学符号。

    处理**按区域**进行, 顺序不能颠倒:
    1. 先按 `$...$` / `$$...$$` 与 `\命令{}` / `\begin{}` 切出"原样保留区";
    2. 保留区**之外**才做两件事:
       - 裸写的数学命令 (`211\times211`) 先转成语义相同的 Unicode 符号 ——
         未加 `$` 时 xelatex 报 "Missing $ inserted" 且整篇不输出页面 (实测);
       - 其余字符按 `_SPECIALS` 转义。
    3. 保留区之内再用 Unicode 数学符号 -> LaTeX 命令补齐 (`λ` -> `$\lambda$`),
       并按需补 `{}` / `$` 定界符。

    **为什么先切区域再做替换**: 早期实现在整串上先做裸命令 -> Unicode, 于是
    `$\lambda=1$` 里的 `\lambda` (它本来就该留在数学模式里) 被换成 `λ`, 再被
    当成"裸符号"补上 `$`, 结果排成 `\\$$\lambda$=1\$` 这种垃圾 (实测)。
    """
    protected: list[str] = []

    def _keep(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00K{len(protected) - 1}\x00"

    # 保留区: 已有数学、生成器写入的命令与环境 (含 `\命令{...}` 形式)
    keep_pattern = ("|".join((
        r"\\\$[^$]*\\\$", r"\$\$[^$]+\$\$", r"\$[^$]+\$",
        r"\\[A-Za-z]+\{[^}]*\}", r"\\begin\{[^}]*\}", r"\\end\{[^}]*\}",
    )))
    work = re.sub(keep_pattern, _keep, text or "")

    # 保留区之外: 裸数学命令 -> Unicode (统一成文本符号, 便于下一步转成数学模式)
    work = _BARE_MATH_COMMAND_RE.sub(lambda m: _UNICODE_MATH_COMMANDS[m.group(0)], work)
    # 保留区之外: 其余字符转义
    work = "".join(_SPECIALS.get(ch, ch) for ch in work)

    # 还原保留区, 并把其间的 Unicode 数学符号转成数学模式
    for index, original in enumerate(protected):
        work = work.replace(f"\x00K{index}\x00", _normalize_math_symbols(original))
    # 保留区之外残留的 Unicode 数学符号也要转 (上一轮 sub 只处理了命令形式)
    return _normalize_math_symbols(work)


def _normalize_math_symbols(text: str) -> str:
    r"""把 Unicode 数学符号换成 LaTeX 数学模式, 并处理两处实测踩过的排版陷阱。

    - 符号后紧跟字母/数字/括号 (`≥v`、`·(x)`) 时命令后补 `{}`: 否则 LaTeX 把后续字母
      并进命令名 (`\geqv`) 报 Undefined control sequence 并中断整篇编译;
    - 符号夹在字母/数字之间 (`v·r`) 时同样补 `{}`, 排成 `v\cdot{}r` 才是"点乘 + r"。
    """
    def _replace(match: re.Match) -> str:
        token = match.group(0)
        symbol = _UNICODE_MATH[token]
        following = text[match.end():match.end() + 1]
        if following and (following.isalnum() or following in "([{"):
            symbol = _terminate_command(symbol)
        return symbol

    return _UNICODE_MATH_RE.sub(_replace, text)


def _terminate_command(symbol: str) -> str:
    r"""`$\geq$` -> `$\geq{}$`: 补终止花括号, 防止吞掉后一个字母。"""
    body = symbol.strip("$")
    if body.endswith("{}"):
        return symbol
    return "$" + body + "{}$"


# 夹在字母/数字之间的数学符号 (如 `n≡1`): 需要自带定界符, 否则替换后符号两侧断裂
_INLINE_SYMBOL_RE = re.compile(
    r"(?<=[A-Za-z0-9])(" + "|".join(re.escape(s) for s in _UNICODE_MATH) +
    r")(?=[A-Za-z0-9])")


# ----------------------------------------------------------------------
# LaTeX
# ----------------------------------------------------------------------

_PREAMBLE = r"""\documentclass[UTF8,a4paper,12pt]{ctexart}
\usepackage[hmargin=1.1in,vmargin=1in]{geometry}
\usepackage{amsmath,amssymb,amsthm}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{tabularx}
\usepackage{longtable}
\usepackage{hyperref}
\hypersetup{colorlinks=true,linkcolor=black,citecolor=black,urlcolor=blue}
\newcolumntype{L}{>{\raggedright\arraybackslash}X}
% 中文期刊常用: 上标引用编号
\newcommand{\upcite}[1]{\textsuperscript{\cite{#1}}}
\newtheorem{theorem}{定理}
\newtheorem{lemma}{引理}
\newtheorem{corollary}{推论}
\newtheorem{proposition}{命题}
\theoremstyle{definition}
\newtheorem{definition}{定义}
\renewcommand{\proofname}{证明}
% 章节编号由稿件**自带** ("1 引言"、"附录 A …"), 因此关掉 LaTeX 自动编号 ——
% 否则会打印成"1 1 引言"、"6 5 讨论与局限"(实测 PDF 每一节都是双编号)。
\setcounter{secnumdepth}{-2}
% 页眉用**稳定短标题**: LaTeX 默认取"最后一个 \section 名", 分页后会错位
% (实测: "2 问题与形式化"的页眉出现在第 1 节的内容上)。
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[C]{\small __RUNNINGHEAD__}
\fancyfoot[C]{\thepage}
\renewcommand{\headrulewidth}{0pt}

\title{__TITLE__}
% 作者块只出现一次: 姓名 + 单位, 通信作者与基金走 \thanks 脚注 (期刊惯例)
\author{__AUTHOR__\thanks{__CORRESPONDING__}}
\date{}

\begin{document}
\maketitle
"""


def render_publication_latex(manuscript: Manuscript, references=None,
                             date: str = "",
                             certificates: dict | None = None) -> tuple[str, list[str]]:
    r"""稿件 -> 出版级 `.tex`。返回 `(tex, 渲染警告)`。

    `certificates`: `claim_id -> 设计判定证书` (结构化)。给了它, 正文第 3 节排成紧凑
    判定块、附录排成完整公式推导 (含汇总表); 给不出就退回逐字排版。

    `date` 已不使用: 期刊论文不在题名页印生成日期 (现场自查发现)。
    """
    warnings: list[str] = []
    certificates = certificates or {}
    title = escape_latex_keep_commands(manuscript.title)
    author = "作者姓名（待作者补充）"
    corresponding = "通信作者（待作者补充，含邮箱）"
    # 页眉用中性短标题 (稿件的实际题名): 不取 `\section` 名, 避免分页错位
    running_head = title
    preamble = (_PREAMBLE.replace("__TITLE__", title)
                .replace("__AUTHOR__", author)
                .replace("__CORRESPONDING__", corresponding)
                .replace("__RUNNINGHEAD__", running_head))

    abstract_text = ""
    keywords = ""
    body: list[str] = []
    bibliography = ""
    bibitem_count = 0
    for block in manuscript.blocks:
        kind = block.kind
        if kind == "certificate":
            # 正文 (第 3 节): 紧凑版 —— 判定对象 + 判定依据 + 指向附录。
            # 完整推导只在附录出现一次, 避免同一份证明排两遍 (现场自查发现)。
            certificate = certificates.get(block.label or "")
            if certificate:
                body.append(render_certificate_latex(certificate, compact=True))
            continue
        if kind == "heading":
            if block.text == "摘要":
                continue
            if block.text == "参考文献":
                continue
            if block.text == "关键词":
                continue
            body.append("\\section{" + escape_latex_keep_commands(block.text) + "}")
        elif kind == "abstract":
            abstract_text = escape_latex_keep_commands(block.text)
        elif kind == "keywords":
            keywords = escape_latex_keep_commands(block.text)
        elif kind == "references":
            # 章节标题**不在这里**输出: `thebibliography` 紧跟在 `\end{document}` 之前,
            # 标题必须与它相邻, 否则会出现"空标题 + 附录 + 文献表"(实测 PDF)。
            continue
        elif kind == "prose":
            text = block.text or ""
            if text.startswith("中图分类号"):
                continue
            if _is_placeholder_line(text):
                continue
            certificate = _certificate_for(text, certificates)
            if certificate:
                # 结构化证明块 -> 数学排版 (公式块 + 分条 + 汇总表)
                body.append(render_certificate_latex(certificate))
                continue
            body.append(_render_prose(text))
        elif kind == "titlepage":
            # 作者/单位/英文题名与摘要: LaTeX 侧的版式由导言区与 `\author{}` 承担,
            # 这里只让 Markdown 保留它们, 不再往正文里塞一遍 (实测会重复)。
            continue
        elif kind in _KIND_ENV:
            env = _KIND_ENV[kind]
            label = block.label or block.title
            body.append(f"\\begin{{{env}}}[{escape_latex_keep_commands(label)}]\n"
                        + escape_latex_keep_commands(block.text)
                        + f"\n\\end{{{env}}}")
        elif kind == "proof":
            body.append("\\begin{proof}\n" + escape_latex_keep_commands(block.text)
                        + "\n\\end{proof}")
        elif kind == "equation":
            label = f"\\label{{{block.label}}}" if block.label else ""
            body.append(f"\\begin{{equation}}\n{block.text}\n{label}\n\\end{{equation}}")
        else:
            body.append(_render_prose(block.text or ""))

    if references is not None:
        bibliography, bibitem_count = _render_bibliography(references)
    # 正文里的引用占位 -> `\upcite{refN}`。放在最后统一处理: 稿件只写占位,
    # 由各渲染器决定形态 (Markdown 出 `[n]`, LaTeX 出上标引用)。
    body = [_CITE_RE.sub(lambda m: f"\\upcite{{{m.group(1)}}}", text) for text in body]

    parts = [preamble]
    if abstract_text:
        parts.append("\\begin{abstract}\n" + abstract_text + "\n\\end{abstract}")
    if keywords:
        parts.append("\\noindent\\textbf{" + keywords + "}")
    parts.append(_english_block(manuscript))
    parts.append("\n".join(body))
    if bibliography:
        # 标题由 `thebibliography` **自带** (ctexart 会排版"参考文献"标题),
        # 因此这里不再另出 `\section*`: 两处都出会打印两遍 (实测 PDF)。
        parts.append(bibliography)
    if bibitem_count == 0 and references is not None and len(references):
        warnings.append("参考文献表非空但没有生成 bibitem")
    parts.append("\\end{document}\n")
    return "\n\n".join(part for part in parts if part.strip()), warnings


def _is_placeholder_line(text: str) -> bool:
    return any(mark in text for mark in ("（待作者补充）", "中图分类号"))


def _md_emphasis(escaped: str) -> str:
    r"""Markdown 强调 (`**x**`) -> LaTeX `\textbf{x}`。

    为什么需要: 稿件的部分文字按 Markdown 生成 (如"该参数的设计**不存在**"),
    原样放进 LaTeX 会把两个星号印出来 (实测 PDF 里出现 "设计 ** 不存在 **")。
    """
    return re.sub(r"\*\*(.+?)\*\*", lambda m: "\\textbf{" + m.group(1) + "}", escaped)


def _certificate_for(text: str, certificates: dict) -> dict:
    """验证记录段落 -> 结构化证书。

    段落形如 `- [design_necessity] 命题 clm-xxx@v1；…`, 用命题 id 回查证书表。
    只有拿到结构化数据才能把 `r(k-1)=λ(v-1)` 排成公式 —— 逐字排版只会得到
    "符号与文字堆叠" (现场反馈: 可读性差)。
    """
    import re

    if not certificates:
        return {}
    match = re.search(r"命题\s+([A-Za-z0-9_\-]+)@", text or "")
    if not match:
        return {}
    return certificates.get(match.group(1)) or {}


def _english_block(manuscript: Manuscript) -> str:
    """英文题名与摘要: 用 `\\section*` (不编号) 承接 titlepage 块里的英文内容。

    只取 `titlepage` 块中**含英文**的部分, 作者/单位占位不再重复输出。
    """
    lines: list[str] = []
    for block in manuscript.blocks:
        if block.kind != "titlepage":
            continue
        text = (block.text or "").strip()
        if not text or _is_placeholder_line(text):
            continue
        if not any(ch.isascii() and ch.isalpha() for ch in text):
            continue
        lines.append(escape_latex_keep_commands(text))
    if not lines:
        return ""
    return ("\\section*{Abstract (English)}\n\n" + "\n\n".join(lines))


# 行内公式识别: 字母/数字/括号/运算符组成的片段, 且**必须含关系符**。
# 三条苛刻判据都是为了不误伤正文:
#   - 片段内不能含逗号 (中文列举 `(v=211, k=15, …)` 若被整段吞进数学模式, 逗号会被
#     排成数学标点、间距全变 —— 实测出现 `($v=211, k$=15, λ=1, …` 这种断裂);
#   - 必须含 `=`/`≥`/`≤` 等关系符 (只有运算符的片段容易是普通词);
#   - 不能有汉字。
_INLINE_START = r"[A-Za-z0-9\u03bb\u03bc\u03c3\u03c0]"
# 片段体内**明确列举**允许的字符 —— 不能用 `[^$]*` 那种"除了 $ 什么都收"的写法:
# 那样会跨过汉字把整句吞进数学模式 (实测 `r(k-1)=λ(v-1) 与 bk=vr` 被整段包起来,
# 数学模式里出现中文会缺字形)。还要禁止 `$` 以免跨过已有数学模式。
# **不含逗号**: 逗号是中文列举的分隔符, 留在数学模式里会被排成数学标点 ——
# 实测 `(v=211, k=15)` 排成 `($v=211, k$=$15, …)` 这种两侧括号与等号对不上的断裂。
_INLINE_BODY = (r"[A-Za-z0-9 \u00b7\u03bb\u03bc\u03c3\u03c0"
                r"()\[\]{}\-+/*.]")
_INLINE_REL = r"[=\u2265\u2264\u2260\u2248\u2261]"
_INLINE_MATH_RE = re.compile(
    r"(?<![\\$A-Za-z0-9_])"
    r"(\("
    r"?(" + _INLINE_START + _INLINE_BODY + r"*" + _INLINE_REL + _INLINE_BODY + r"*)\)?"
    r")"
    r"(?![A-Za-z0-9_$])")
# `mod` 是数学模式里的排版陷阱 (`\equiv{} 1, 2 (mod 4)` 会把 mod/4 排成变量斜体):
# 含 `mod` 的片段交给正文排版, 由 `_math_label` 那条路径处理。
_INLINE_MATH_EXCLUDE = ("^", "%", "°", "…", "mod")


def wrap_inline_math(text: str) -> str:
    r"""把正文里**纯文本形式的公式**包进数学模式 (`$...$`)。

    现场反馈: "数学推导的符号、公式等应当更标准, 格式更加明确, 突出数学语言,
    而不是大量文字和公式推导的堆叠"。此前只有附录证书排成了公式, 正文段落里的
    `v=211`、`r(k-1)=λ(v-1)`、`b ≥ v` 仍是普通文字, 读起来就是符号与文字混排。

    安全性来自判据: (1) 片段必须含关系符; (2) 片段内不能有汉字、逗号、`^`、`mod`;
    (3) 已在 `$...$` 内的内容跳过。命中后只做 Unicode 数学符号 -> LaTeX 命令的替换
    (命令后补 `{}`, 防止吞掉后一个字母), 并把紧邻的圆括号留在数学模式**外**。
    """
    if not text:
        return text
    pieces, _ = wrap_inline_math_pieces(text)
    out: list[str] = []
    for is_math, body in pieces:
        out.append("$" + body + "$" if is_math else body)
    return "".join(out)


def wrap_inline_math_pieces(text: str) -> tuple[list[tuple[bool, str]], int]:
    r"""`wrap_inline_math` 的分段版: 返回 `[(是否数学, 片段)]` 与数学片段个数。

    为什么需要它: "转义"与"包数学模式"两步会互相干扰 —— 占位符方案能让调用方先把
    非数学片段转义、再拼回已转义的数学片段, 避免 `$` 被二次处理 (实测出现过
    `$\lambda{}$` 嵌在 `$...$` 里, 以及 `\$` 被印出来)。
    """
    if not text:
        return [(False, "")], 0

    def _convert(segment: str) -> list[tuple[bool, str]]:
        output: list[tuple[bool, str]] = []
        cursor = 0
        for match in _INLINE_MATH_RE.finditer(segment):
            body = match.group(1)
            if any(ch in body for ch in _INLINE_MATH_EXCLUDE):
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in body):
                continue
            lead = ""
            core = body
            # 前导 `(` 只有在括号不平衡时才算正文标点 (`(v=211`); 平衡时属于公式
            if core.startswith("(") and core.count("(") > core.count(")"):
                lead, core = "(", core[1:]
            if not re.search(_INLINE_REL, core):
                continue
            core = core.rstrip(" ,.;:")
            while core.endswith(")") and core.count(")") > core.count("("):
                core = core[:-1]
            trailing = body[len(lead) + len(core):]
            for ch, latex in _MATH_SYMBOLS.items():
                core = core.replace(ch, latex)
            if match.start() > cursor:
                output.append((False, segment[cursor:match.start()]))
            if lead:
                output.append((False, lead))
            output.append((True, core))
            if trailing:
                output.append((False, trailing))
            cursor = match.end()
        if cursor < len(segment):
            output.append((False, segment[cursor:]))
        return output

    pieces: list[tuple[bool, str]] = []
    for part, is_math in _split_math_regions(text):
        if is_math:
            pieces.append((True, part.strip("$")))
        else:
            pieces.extend(_convert(part))
    return pieces, sum(1 for is_math, _ in pieces if is_math)


def _split_math_regions(text: str) -> list[tuple[str, bool]]:
    """按 `$...$` / `$$...$$` 切成 (片段, 是否已在数学模式)。"""
    parts: list[tuple[str, bool]] = []
    cursor = 0
    for match in re.finditer(r"\$\$[^$]+\$\$|\$[^$]+\$", text):
        if match.start() > cursor:
            parts.append((text[cursor:match.start()], False))
        parts.append((match.group(0), True))
        cursor = match.end()
    if cursor < len(text):
        parts.append((text[cursor:], False))
    return parts


def _render_prose(text: str, indent: bool = True) -> str:
    """段落/列表: Markdown 表格行转 tabularx, 其余按**行**渲染 (保留换行)。

    为什么要按行: 判定链、检索条目都是多行文本。整块塞进一个段落会挤成一团
    (实测 PDF: 五条必要条件判定链连成一大段, 无法阅读)。
    - `- ` 开头 -> `itemize`;
    - `n ` / `n.` 开头 -> `enumerate` (保留编号, 参考文献与判定链靠它指路);
    - 其余行 -> 独立段落 (**标准 LaTeX 缩进**), 不再用 `\\noindent` ——
      判定链的子行需要缩进才能与上一级区分开。
    """
    lines = (text or "").splitlines()
    out: list[str] = []
    table_rows: list[str] = []
    list_items: list[str] = []
    quote_lines: list[str] = []
    numbered_items: list[tuple[str, str]] = []

    def _flush_numbered() -> None:
        if not numbered_items:
            return
        out.append("\\begin{enumerate}")
        for _number, text in numbered_items:
            out.append("\\item " + text)
        out.append("\\end{enumerate}")
        numbered_items.clear()

    def _flush_quote() -> None:
        if not quote_lines:
            return
        out.append("\\begin{quote}")
        out.append(" \\\\\n".join(quote_lines))
        out.append("\\end{quote}")
        quote_lines.clear()

    def _flush_table() -> None:
        if not table_rows:
            return
        rows = [r for r in table_rows if not re.match(r"^\|[\s\-|:]+\|$", r.strip())]
        if rows:
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            width = max(len(r) for r in cells)
            out.append("\\begin{center}\\begin{tabular}{" + "l" * width + "}")
            out.append("\\toprule")
            for index, row in enumerate(cells):
                padded = row + [""] * (width - len(row))
                out.append(" & ".join(escape_latex_keep_commands(c) for c in padded)
                           + r" \\")
                if index == 0:
                    out.append("\\midrule")
            out.append("\\bottomrule\\end{tabular}\\end{center}")
        table_rows.clear()

    def _flush_list() -> None:
        if not list_items:
            return
        out.append("\\begin{itemize}")
        for item in list_items:
            out.append("\\item " + item)
        out.append("\\end{itemize}")
        list_items.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            _flush_list()
            _flush_quote()
            _flush_numbered()
            table_rows.append(line)
            continue
        _flush_table()
        if stripped.startswith(("- ", "* ")):
            _flush_quote()
            _flush_numbered()
            list_items.append(escape_latex_keep_commands(stripped[2:]))
            continue
        numbered = re.match(r"^(\d+)[.、)]\s+(.*)$", stripped)
        if numbered:
            # 编号列表 (`1 标题…`): 保留编号 —— 参考文献条目与判定链都靠它指路,
            # 当成普通段落会丢掉编号 (实测 PDF 里条目变成"Upper Bounds on …"没有序号)。
            _flush_quote()
            numbered_items.append((numbered.group(1),
                                   escape_latex_keep_commands(numbered.group(2))))
            continue
        if stripped.startswith("> "):
            _flush_list()
            _flush_numbered()
            quote_lines.append(escape_latex_keep_commands(stripped[2:]))
            continue
        _flush_table()
        _flush_list()
        _flush_quote()
        _flush_numbered()
        if not stripped:
            continue
        rendered = escape_latex_keep_commands(stripped)
        # Markdown 强调 -> LaTeX (`**x**` 若是原样输出, PDF 里会印出两个星号 —— 实测)
        rendered = _md_emphasis(rendered)
        # 正文里的纯文本公式 (`v=211`、`b ≥ v`) 统一包进数学模式。
        # **已有 `$...$` 的行不再处理**: `wrap_inline_math` 会把已存在的数学区域重新
        # 拼接 (实测 `$211\times211$` 被拆成 `$211\times{}$` + `211`, 丢掉了外层定界符,
        # 于是 LaTeX 在正文里看到裸 `\times` 并报错)。
        if "$" not in rendered:
            rendered = wrap_inline_math(rendered)
        out.append(rendered)
    _flush_table()
    _flush_list()
    _flush_quote()
    _flush_numbered()
    return "\n\n".join(out)


def render_certificate_latex(certificate: dict, compact: bool = False) -> str:
    r"""设计判定证书 -> **排版后的数学推导** (方案 v2 §2: 突出数学语言)。

    为什么不逐字转义那段判定链文本: 证书本身是结构化的 (`design` / `counts` /
    `evidence[]`, 每条含 theorem / condition / statement / inputs / result /
    conclusion), 逐字排版会把 `r(k-1)=λ(v-1)`、`b ≥ v` 这类**公式**当普通文字塞进段落,
    读起来就是一堆符号和文字的堆叠 (现场反馈: 可读性差)。

    `compact=True` 用于**正文** (第 3 节): 只给判定对象、计数关系与被违反的条件 ——
    完整推导放在附录, 避免同一份证明在正文与附录里逐字出现两遍 (现场自查发现)。

    排版组成: `align*`/`equation*` 公式块、逐条必要条件 (定理/输入/结论)、
    `booktabs` 汇总表、证书指纹。
    """
    design = certificate.get("design") or {}
    counts = certificate.get("counts") or {}
    evidence = list(certificate.get("evidence") or [])
    if not design and not evidence:
        return ""
    lines: list[str] = []

    # ---- 判定对象 (公式块) ----
    if design:
        v = _num(design.get("v"))
        k = _num(design.get("k"))
        lam = _num(design.get("lam"))
        b = _num(design.get("b"))
        r = _num(design.get("r"))
        if not compact:
            lines.append("\\subsection*{判定对象}")
        lines.append("\\begin{equation*}")
        lines.append(f"v = {v},\\quad k = {k},\\quad \\lambda = {lam},\\quad "
                     f"b = {b},\\quad r = {r}")
        lines.append("\\end{equation*}")
    if counts:
        lines.append("由计数恒等式 $r(k-1)=\\lambda(v-1)$ 与 $bk=vr$ 推出")
        lines.append("\\begin{equation*}")
        lines.append(f"r = \\frac{{\\lambda(v-1)}}{{k-1}} = {_num(counts.get('r'))},"
                     f"\\qquad b = \\frac{{vr}}{{k}} = {_num(counts.get('b'))}")
        lines.append("\\end{equation*}")
        tail = ("该设计为对称设计（$b = v$）"
                if counts.get("symmetric") else "该设计不是对称设计")
        if counts.get("order") is not None:
            tail += f"，等价于 ${_num(counts.get('order'))}$ 阶射影平面"
        lines.append(tail + "。")

    if compact:
        violated = [item for item in evidence if not item.get("result")]
        if not violated:
            violated = evidence[:1]
        if violated:
            lines.append("\\subsection*{判定依据}")
            for item in violated:
                # 注意 `\textbf{...}` 的闭合花括号: 早期写成 `+ "}（依据定理："` 会在
                # 文本末尾多出一个 `}` (实测 LaTeX 报 "Too many }'s" 并中断编译)。
                lines.append("\\noindent\\textbf{被违反的必要条件："
                             + _esc_math(item.get("condition", ""))
                             + "}（依据定理：" + _esc_math(item.get("theorem", ""))
                             + "）")
                statement = _esc_math(item.get("statement", ""))
                if statement:
                    lines.append(statement)
                inputs = _render_math_values(item.get("inputs") or {})
                if inputs:
                    lines.append("输入：" + inputs)
                conclusion = _esc_math(item.get("conclusion", ""))
                if conclusion:
                    lines.append("结论：" + conclusion)
        lines.append("完整的逐条核验、必要条件汇总与证书指纹见附录 A。")
        return "\n".join(lines)

    # ---- 逐条必要条件 ----
    if evidence:
        lines.append("\\subsection*{必要条件逐条核验}")
        lines.append("\\begin{itemize}")
        for item in evidence:
            result = "满足" if item.get("result") else "\\textbf{违反}"
            theorem = _esc_math(item.get("theorem", ""))
            condition = _esc_math(item.get("condition", ""))
            inputs = _render_math_values(item.get("inputs") or {})
            conclusion = _esc_math(item.get("conclusion", ""))
            statement = _esc_math(item.get("statement", ""))
            citation = _esc_math(item.get("citation", ""))
            head = f"\\item[{result}] \\textbf{{{condition}}}"
            if theorem:
                head += f"（依据定理：{theorem}"
                head += f"，{citation}）" if citation else "）"
            lines.append(head)
            # 分条内部用 `\\` 换行: 直接写多行会让后续行掉出 itemize
            if statement:
                lines.append("\\\\ " + statement)
            if inputs:
                lines.append("\\\\ 输入：" + inputs)
            if conclusion:
                lines.append("\\\\ 结论：" + conclusion)
        lines.append("\\end{itemize}")

    # ---- 汇总表 ----
    if evidence:
        lines.append("\\subsection*{必要条件汇总}")
        lines.append("\\begin{center}\\begin{tabular}{@{}llc@{}}")
        lines.append("\\toprule")
        lines.append("必要条件 & 依据定理 & 判定 \\\\")
        lines.append("\\midrule")
        for item in evidence:
            condition = escape_latex_keep_commands(
                str(item.get("condition", ""))[:40])
            theorem = escape_latex_keep_commands(str(item.get("theorem", ""))[:36])
            mark = "满足" if item.get("result") else "\\textbf{违反}"
            lines.append(f"{condition} & {theorem} & {mark} \\\\")
        lines.append("\\bottomrule\\end{tabular}\\end{center}")

    fingerprint = str(certificate.get("sha256", "") or "")
    if fingerprint:
        lines.append("\\noindent\\texttt{证书 sha256：" + escape_latex_keep_commands(fingerprint)
                     + "}")
    return "\n".join(lines)


# 数学符号 -> LaTeX 命令。命令后面一律补 `{}`: 否则 `v·r` 会拼成 `v\cdotr`
# (LaTeX 把后续字母并进命令名), 报 "Undefined control sequence" 并中断整篇编译 (实测)。
_MATH_SYMBOLS = {
    "λ": "\\lambda{}", "·": "\\cdot{}", "≥": "\\geq{}", "≤": "\\leq{}",
    "×": "\\times{}", "≠": "\\neq{}", "≈": "\\approx{}", "∈": "\\in{}",
    "≡": "\\equiv{}", "∞": "\\infty{}", "±": "\\pm{}",
}


def _esc_math(text) -> str:
    r"""字段文本 -> LaTeX: 先把裸命令规范化, 再按片段转义, 数学片段原样拼回。

    顺序由实测确定:
    1. **先**把裸写的数学命令转成 Unicode (`211\times211` -> `211×211`): 否则它们会
       被内联公式规则当成数学片段, 而在**未被识别为数学**的上下文里 LaTeX 命令会触发
       "Missing $ inserted" 并让整篇不输出页面 (实测);
    2. 再按片段切分 (`wrap_inline_math_pieces`): 非数学片段转义、数学片段原样保留;
    3. 最后把残留的 Unicode 符号转成数学模式 (`n≡1` -> `n$\equiv{}$1`)。
    """
    if not text:
        return ""
    raw = _BARE_MATH_COMMAND_RE.sub(
        lambda m: _UNICODE_MATH_COMMANDS[m.group(0)], str(text))
    pieces, _ = wrap_inline_math_pieces(raw)
    out: list[str] = []
    for is_math, body in pieces:
        if is_math:
            out.append("$" + body + "$")
        else:
            # 两次规范化: 第一次把 Unicode 符号转成数学模式 (`n≡1` -> `n$\equiv{}$1`),
            # 第二次处理这些新插入的 `$...$` —— 它们内部若仍有 Unicode 符号
            # (如 `$λ=1$`) 必须换成 LaTeX 命令, 否则正文字体缺字形 (实测)。
            out.append(escape_latex_keep_commands(_normalize_math_symbols(body)))
    return "".join(out)


def _math_label(key: str) -> str:
    r"""把含数学符号的**标签**整体放进数学模式 (`λ(v-1)` -> `$\lambda{}(v-1)$`)。"""
    text = str(key)
    if not text:
        return ""
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return escape_latex_keep_commands(text)
    converted = text
    for ch, latex in _MATH_SYMBOLS.items():
        converted = converted.replace(ch, latex)
    if converted == text and not text[0].isalpha():
        return escape_latex_keep_commands(text)
    return f"${converted}$"


def _render_math_values(pairs: dict) -> str:
    """`{k: v, …}` -> `$k = v,\\quad …$`, 且**整体**放进数学模式。

    为什么整串放进数学模式: 逐项 `$k = 15$`、`$k-1 = 14$` 之间用顿号分隔时, 顿号会落在
    数学模式外, 前后间距忽宽忽窄; 实测排版出现"`$k = 15$、`lambda`(v-1) = 210`"这种
    半数学半文本的断裂。整串一个数学模式, 标签用 `\\text{}` 或转义, 读起来才连贯。
    """
    if not pairs:
        return ""
    items: list[str] = []
    for key, value in pairs.items():
        label = _math_label(key)
        if label.startswith("$") and label.endswith("$"):
            body = label[1:-1]
        else:
            body = f"\\text{{{label}}}"
        # `mod` 在数学模式里是运算符, 必须写 `\bmod`: 直接写 mod 会被排成
        # "nmod4"(斜体变量连排, 实测 PDF)。同理补一个空格避免黏连。
        body = re.sub(r"\bmod\b", r"\\bmod", body)
        items.append(f"{body} = {_num(value)}")
    return "$" + ",\\quad ".join(items) + "$"


def _num(value) -> str:
    return str(value) if value is not None else "-"


def _render_bibliography(references) -> tuple[str, int]:
    r"""GB/T 7714 著录 -> 内嵌 `thebibliography` (确定性, 不需 bibtex)。"""
    items = list(getattr(references, "references", []) or [])
    if not items:
        return "", 0
    lines = [f"\\begin{{thebibliography}}{{{len(items)}}}"]
    for index, item in enumerate(items, start=1):
        text = getattr(item, "formatted", "") or getattr(item, "title", "")
        text = re.sub(r"^\[\d+\]\s*", "", text or "")
        key = getattr(item, "key", f"ref{index}") or f"ref{index}"
        lines.append(f"\\bibitem{{{key}}} " + escape_latex_keep_commands(text))
    lines.append("\\end{thebibliography}")
    return "\n".join(lines), len(items)


# ----------------------------------------------------------------------
# Markdown (供人类阅读与研究记录核对)
# ----------------------------------------------------------------------

def render_publication_markdown(manuscript: Manuscript) -> str:
    """稿件 -> Markdown。

    **不做 LaTeX 转义**: Markdown 里 `\\_`、`\\[`、`\\%` 是字面反斜杠, 会把正文读成
    "2-(211,15,1) 设计不存在" 这类乱码, 也会让引用占位 `[[REF:ref1]]` 变成
    `\\[\\[REF:ref1\\]\\]` 而无法被编号解析。LaTeX 转义只属于 `render_publication_latex`。
    """
    lines = [f"# {manuscript.title}", ""]
    counters: dict[str, int] = {}
    for block in manuscript.blocks:
        kind = block.kind
        if kind == "heading":
            lines.append(f"## {block.text}")
            lines.append("")
        elif kind == "abstract":
            # 结构化 kind 在 Markdown 侧仍要有小标题: 少了它, 读者(与出版门槛)
            # 就看不出哪一段是摘要 —— LaTeX 侧由 `abstract` 环境承担。
            lines.append("## 摘要")
            lines.append("")
            lines.append(block.text.strip())
            lines.append("")
        elif kind in ("keywords",):
            lines.append(block.text.strip())
            lines.append("")
        elif kind == "references":
            lines.append("## 参考文献")
            lines.append("")
            lines.append(block.text.strip())
            lines.append("")
        elif kind == "certificate":
            # Markdown 侧给出**判定链原文** (逐条可反查); 数学排版由 LaTeX 侧承担
            lines.append("")
            lines.append("*证明.*")
            lines.append("")
            for item in (block.text or "").splitlines():
                lines.append(item)
            lines.append("")
        elif kind in _KIND_ENV:
            counters[kind] = counters.get(kind, 0) + 1
            tag = {"theorem": "定理", "lemma": "引理", "corollary": "推论",
                   "proposition": "命题", "claim": "结果"}[kind]
            title = f"（{block.title}）" if block.title else ""
            lines.append(f"**{tag} {counters[kind]}**{title}：{block.text.strip()}")
            lines.append("")
        elif kind == "proof":
            lines.append(f"*证明.* {block.text.strip()}")
            lines.append("")
        elif kind == "equation":
            lines.extend(["$$", block.text.strip(), "$$", ""])
        else:
            lines.append(block.text.strip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["escape_latex_keep_commands", "render_publication_latex",
           "render_publication_markdown"]

