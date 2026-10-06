from __future__ import annotations

"""Render the canonical manuscript IR using the same citation order as Markdown.

Narrative belongs to WritingAgent; this module handles escaping and layout only.
"""

import re
from typing import Any

from src.publication.schemas import Block, Manuscript, RefKind, assign_render_numbers

__all__ = [
    "LATEX_POSTAMBLE",
    "LATEX_PREAMBLE",
    "escape_latex",
    "escape_math",
    "render_latex",
]

#: Unicode → LaTeX 映射 (只列**实际会被正文带进来**的字符)。
#:
#: 原则: 数学符号进数学模式 (`$...$`), 这样既能排版也不再缺字形; 不放进数学模式的
#: 字符 (如中文标点) 一律不动。刻意**不**映射 `\\`、`{`、`}`、`$` —— 它们是命令结构,
#: 正文里的既有命令 (如 `\\textbf{}`) 必须原样保留。
#:
#: 没有这张表时实测后果: 正文里的 `λ`/`≡`/`×`/`−`/`∈` 直接落到 ctexart 的拉丁字体上,
#: 日志报 `Missing character: There is no ∈ in font [lmroman12-regular]`, PDF 出现空洞。
_UNICODE_TO_LATEX: dict[str, str] = {
    # 运算与关系
    "×": r"$\times$", "÷": r"$\div$", "±": r"$\pm$", "∓": r"$\mp$",
    "≤": r"$\leq$", "≥": r"$\geq$", "≠": r"$\neq$", "≡": r"$\equiv$",
    "≈": r"$\approx$", "≃": r"$\simeq$", "∼": r"$\sim$", "∝": r"$\propto$",
    "·": r"$\cdot$", "−": r"$-$", "∞": r"$\infty$", "√": r"$\surd$",
    "∑": r"$\sum$", "∏": r"$\prod$", "∫": r"$\int$", "∂": r"$\partial$",
    # 集合与逻辑
    "∈": r"$\in$", "∉": r"$\notin$", "⊂": r"$\subset$", "⊆": r"$\subseteq$",
    "⊃": r"$\supset$", "∪": r"$\cup$", "∩": r"$\cap$", "∅": r"$\emptyset$",
    "∀": r"$\forall$", "∃": r"$\exists$", "¬": r"$\neg$", "∧": r"$\wedge$",
    "∨": r"$\vee$", "⇒": r"$\Rightarrow$", "⇔": r"$\Leftrightarrow$",
    "→": r"$\to$", "←": r"$\leftarrow$", "↔": r"$\leftrightarrow$",
    # 常见希腊字母 (论文里最常出现的那些)
    "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$", "δ": r"$\delta$",
    "ε": r"$\epsilon$", "ζ": r"$\zeta$", "η": r"$\eta$", "θ": r"$\theta$",
    "κ": r"$\kappa$", "λ": r"$\lambda$", "μ": r"$\mu$", "ν": r"$\nu$",
    "ξ": r"$\xi$", "π": r"$\pi$", "ρ": r"$\rho$", "σ": r"$\sigma$",
    "τ": r"$\tau$", "φ": r"$\phi$", "χ": r"$\chi$", "ψ": r"$\psi$",
    "ω": r"$\omega$", "Γ": r"$\Gamma$", "Δ": r"$\Delta$", "Θ": r"$\Theta$",
    "Λ": r"$\Lambda$", "Ξ": r"$\Xi$", "Π": r"$\Pi$", "Σ": r"$\Sigma$",
    "Φ": r"$\Phi$", "Ψ": r"$\Psi$", "Ω": r"$\Omega$",
    # 数论/线性代数里常见、但 ctexart 拉丁字体同样没有字形的符号
    # (实测: `ℓ` 会报 `Missing character: There is no ℓ`, PDF 留下空洞)
    "ℓ": r"$\ell$", "ℝ": r"$\mathbb{R}$", "ℕ": r"$\mathbb{N}$",
    "ℤ": r"$\mathbb{Z}$", "ℚ": r"$\mathbb{Q}$", "ℂ": r"$\mathbb{C}$",
    "∥": r"$\parallel$", "⊥": r"$\perp$", "≅": r"$\cong$",
    "≪": r"$\ll$", "≫": r"$\gg$", "≺": r"$\prec$", "≻": r"$\succ$",
    "⟨": r"$\langle$", "⟩": r"$\rangle$", "⋯": r"$\cdots$",
    "∘": r"$\circ$", "⊗": r"$\otimes$", "⊕": r"$\oplus$", "⋆": r"$\star$",
    # 实测 (proj-muvx3j7ima1z): `↦`/`ᵀ`/`⌊`/`⌋` 同样缺字形
    "↦": r"$\mapsto$", "ᵀ": r"$^{\mathrm{T}}$", "⟹": r"$\Longrightarrow$",
    "⌊": r"$\lfloor$", "⌋": r"$\rfloor$", "⌈": r"$\lceil$", "⌉": r"$\rceil$",
    "≔": r"$:=$", "≢": r"$\not\equiv$",
    "′": r"$^{\prime}$", "″": r"$^{\prime\prime}$",
    # 上下标数字/字母: 模型常直接写 `10⁸`、`x₁`, 拉丁字体里同样没有这些字形
    "⁰": r"$^{0}$", "¹": r"$^{1}$", "²": r"$^{2}$", "³": r"$^{3}$",
    "⁴": r"$^{4}$", "⁵": r"$^{5}$", "⁶": r"$^{6}$", "⁷": r"$^{7}$",
    "⁸": r"$^{8}$", "⁹": r"$^{9}$", "ⁿ": r"$^{n}$",
    "₀": r"$_{0}$", "₁": r"$_{1}$", "₂": r"$_{2}$", "₃": r"$_{3}$",
    "₄": r"$_{4}$", "₅": r"$_{5}$", "₆": r"$_{6}$", "₇": r"$_{7}$",
    "₈": r"$_{8}$", "₉": r"$_{9}$",
}

#: 同一张表的**数学模式**版本: 去掉外层 `$`, 只保留命令本身。
#:
#: 公式体 (`Block.math`) 已经处在 `equation` 里, 再插入 `$` 会破坏数学模式
#: (`$-$` 落在公式里 → `Missing $ inserted`)。`−` 这类映射到单字符的条目
#: 去掉 `$` 后正好是合法的数学符号。
_UNICODE_TO_LATEX_MATH: dict[str, str] = {
    char: latex.strip("$") for char, latex in _UNICODE_TO_LATEX.items()
}

#: 公式体里的全角标点: 数学模式下这些字形不存在 (日志里的
#: `Missing character: There is no ， in font ...`), 换成 ASCII 等价物并补一个间距。
_CJK_PUNCT_IN_MATH: dict[str, str] = {
    "，": r",\ ", "。": r".\ ", "；": r";\ ", "：": r":\ ",
    "、": r",\ ", "（": "(", "）": ")", "％": r"\%",
}

#: 正文里"底数^指数"的形状: `M^T`、`14^105`、`x^{-1}`、`2^{10}`、`Σ^{166}`。
#: 底数只吃单个 ASCII 字母/数字或单个希腊字母 —— 多字符底数按最后一个字符切分
#: (视觉结果相同: `14^{105}` 与 `1` + `4^{105}` 排版一致), 但不会误吞中文或命令。
_INLINE_POWER_RE = re.compile(
    r"([A-Za-z0-9\u0370-\u03ff])\^(\{[^{}]*\}|-?[A-Za-z0-9]+)")

#: 集合字面量作底数的形状: `{0,1}^667`、`{0,1}^n` (实测就是这一处 `^` 让整篇编译
#: 中断), 以及模型自己写好的 `\{0,1\}^667`。内容限死在数字/字母/逗号/空白/正负号
#: (含 U+2212 减号), 并用 lookbehind 排除 `\textbf{…}^2` 这类命令参数被误当成集合。
_INLINE_SET_POWER_RE = re.compile(
    r"(?<![A-Za-z\\])((?:\\?\{)[0-9A-Za-z,;\s+\-\u2212]*(?:\\?\}))\^"
    r"(\{[^{}]*\}|-?[A-Za-z0-9]+)")

#: 正文里"单字母_下标"的形状: `c_i`、`I_667`、`d_H`、`H_{1j}`、`Σ_{t=0}`。
#:
#: 转义成 `\_` 只是能编译 —— PDF 里显示的是**字面下划线**而不是真下标, 一篇满是
#: `c\_i`、`J\_667` 的论文读起来很糟。这里把它还原成行内数学。
#:
#: 边界刻意收得很紧, 只吃**单个字母/数字**作底数, 且
#: - lookbehind 排除 `ab_c` / `existence_proof` 这类**多字母标识符** (否则蛇形命名会被
#:   当成下标, 变成 `existence_{proof}`);
#: - lookahead 排除 `a_bcde` 这类下标超过 3 个字符的形状 (保守放弃, 保持 `\_`);
#: - 只接受 1-3 个字母数字或一个花括号组作下标。
_INLINE_SUBSCRIPT_RE = re.compile(
    r"(?<![A-Za-z0-9\\])([A-Za-z0-9\u0370-\u03ff])_"
    r"(\{[^{}]*\}|[A-Za-z0-9]{1,3})(?![A-Za-z0-9_])")

#: 同时带下标与上标的形状: `Σ_{t=0}^{166}`、`x_{i}^{2}`。
#: 必须排在单独的上标/下标规则**之前**, 否则会被拆成两半 (`Σ_{t=0}` + 认不出的 `^{166}`)。
_INLINE_SUBSUP_RE = re.compile(
    r"(?<![A-Za-z0-9\\])([A-Za-z0-9\u0370-\u03ff])_"
    r"(\{[^{}]*\}|[A-Za-z0-9]{1,3})\^(\{[^{}]*\}|-?[A-Za-z0-9]+)")

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
% 中英混排里 `(v,k,λ)=(211,15,1)` 这类不可断行的片段会把行挤出右边界
% (实测 Overfull \hbox 1.05pt)。emergencystretch 允许在实在找不到断点时多拉伸
% 一点间距, 这是 LaTeX 为此设计的机制; 不设时编译器会把这类溢出判为交付失败。
\emergencystretch=2em

\title{__TITLE__}
\author{AI Research Team}
\date{__DATE__}

\begin{document}
\maketitle
"""

LATEX_POSTAMBLE = r"""
\end{document}
"""


def _escape_active(text: str, char: str) -> str:
    r"""把 `char` 转义成 `\char`, 但**跳过已经转义过的** `\char`。

    为什么必须跳过: 模型经常自己就在公式/正文里写 `\#`(基数)、`\%`、`\&`。无条件再转
    一次会得到 `\\#` —— `\\` 是换行, 后面的 `#` 就成了"宏参数字符"。实测
    (proj-muvx3j7ima1z): 公式里写 `\#\{i:s_i(k)=+1\}`, 二次转义后 xelatex 报
    ``! You can't use `macro parameter character #' in display math mode.``,
    编译在错误处停下, 30KB 的稿件只写出 **3 页** PDF。
    """
    return re.sub(r"(?<!\\)" + re.escape(char), lambda _match: "\\" + char, text)


def _math_body(chunk: str) -> str:
    r"""把片段里的 Unicode 数学符号换成**数学模式命令**形式 (`λ`→`\lambda`)。

    行内数学片段是自己造出来的, 而生成时 Unicode 还没被替换 (转换要排在它前面, 否则
    形状会被切碎)。不在片段内部再转一次, `{+1,−1}^{668}` 的 `−`(U+2212) 就会带着
    Unicode 落进数学模式, 日志报 `Missing character: There is no − in font cmmi12`。
    """
    for char, latex in _UNICODE_TO_LATEX_MATH.items():
        if char in chunk:
            chunk = chunk.replace(char, latex)
    return chunk


def escape_latex(text: str) -> str:
    """转义普通文本里的 LaTeX 特殊字符 (**保持**数学公式与已有命令)。

    只转义 `_ & % #`: `\\` 与 `{}` 是命令结构, 转义它们会破坏 `\\textbf{}`;
    `$...$` 内部是数学, 不转义 (否则 `x_i` 会被写成 `x\\_i`)。

    **另加两类必须处理的字符** (离线交付实测: 生成的正文里带 `2^1 × 7`、`λ`、`∈`,
    于是 xelatex 报 `! Missing $ inserted.` → **一页都写不出来, 没有 PDF**):

    - `^` 与 `~`: 在文本模式下是活动字符。`X^Y` 先尽量转成行内数学 `$X^{Y}$`
      (论文里的 `M^T`、`{0,1}^667` 都是这个形状), 剩余无法识别的 `^` 退化为
      `\\textasciicircum{}` —— 只是难看, 但**不会**再让整篇编译失败;
    - `X_y` 形状的**下标**同样还原成 `$X_{y}$`; 认不出来的 `_` 仍然转义成 `\\_`
      (标识符/文件名/DOI 里的下划线必须原样保留);
    - 常见 Unicode 数学/希腊字符: ctexart 的拉丁字体里没有这些字形, 映射到数学模式
      后既不再缺字, 语义也不变。

    这里是**唯一**的转义入口; 公式体走 `escape_math`, 两者共用同一张字符表。
    """
    math_blocks: list[str] = []

    def _protect(match: "re.Match[str]") -> str:
        math_blocks.append(match.group(0))
        return f"\x00MATH{len(math_blocks) - 1}\x00"

    # 原有的 $...$ 先摘出来, 下面所有替换都不碰数学模式内部
    text = re.sub(r"\$[^$]*\$", _protect, text or "")
    # Keep a relational expression together instead of emitting '$-$' per symbol.
    plain_math = re.compile(r"[A-Za-z0-9\u0370-\u03ff_{}()\[\].=+*/^<>!−×±≤≥≠ᵀ′″]+")

    def _protect_expression(match: re.Match) -> str:
        value = match.group(0)
        stack = []
        for char in value:
            if char in "{([":
                stack.append(char)
            elif char in "})]":
                if not stack or stack.pop() != {"}": "{", ")": "(", "]": "["}[char]:
                    return value
        if stack or re.search(r"[A-Za-z]{4,}_", value) or re.match(r"[A-Za-z]{4,}=", value):
            return value
        if (re.search(r"[=<>≤≥≠]", value)
                and re.search(r"[_^\u0370-\u03ff−×±≤≥≠ᵀ′″]", value)):
            math_blocks.append("$" + _math_body(value) + "$")
            return f"\x00MATH{len(math_blocks) - 1}\x00"
        return value

    text = plain_math.sub(_protect_expression, text)
    # `_` **先不转义**: 下面的下标还原要能看到原始的 `X_y`, 写成 `X\_y` 就认不出来了
    for ch in ("&", "%", "#"):
        text = _escape_active(text, ch)
    # 文本模式下的活动字符。先把可识别的上标/下标转成行内数学并**摘出来**, 否则下面
    # 的兜底替换会把刚生成的 `^{...}`/`_{...}` 一起改掉 (实测产出
    # `$M\textasciicircum{}{T}$`, 并在数学模式里报 "Command \textasciicircum invalid
    # in math mode")。
    #
    # 顺序要求: 这一步必须**早于** Unicode 映射。实测 (proj-muvx3j7ima1z)
    # `{+1,−1}^668` 里的 `−` 会先被映射成 `$-$`, `Σ_{t=0}^{166}` 的 `Σ` 先变成
    # `$\Sigma$` —— 两者都会把待识别的形状切碎, 于是 `^` 只能退化成脱字符。
    inline: list[str] = []

    def _snippet(latex: str) -> str:
        inline.append(latex)
        return f"\x00POW{len(inline) - 1}\x00"

    def _exponent(raw: str) -> str:
        # 已经带花括号的指数不要再套一层 (`y^{10}` 不该变成 `y^{{10}}`);
        # 花括号里的内容同样要过 `_math_body` (`^{668×668}` → `^{668\times668}`)
        body = _math_body(raw.strip("{}")) if raw.startswith("{") else _math_body(raw)
        return "{" + body + "}"

    def _inline_power(match: "re.Match[str]") -> str:
        return _snippet(f"${_math_body(match.group(1))}^{_exponent(match.group(2))}$")

    def _inline_set_power(match: "re.Match[str]") -> str:
        # 集合的花括号在数学模式里会被吃掉, 必须写成 `\{…\}`;
        # 模型自己写好的 `\{…\}` 与裸 `{…}` 归一到同一种写法。
        base = re.sub(r"[\\{}]", "", match.group(1)).strip()
        return _snippet(rf"$\{{{_math_body(base)}\}}^{_exponent(match.group(2))}$")

    def _inline_subscript(match: "re.Match[str]") -> str:
        base = _math_body(match.group(1))
        return _snippet(f"${base}_{{{match.group(2).strip('{}')}}}$")

    def _inline_subsup(match: "re.Match[str]") -> str:
        base = _math_body(match.group(1))
        return _snippet(f"${base}_{{{match.group(2).strip('{}')}}}"
                        f"^{_exponent(match.group(3))}$")

    text = _INLINE_SET_POWER_RE.sub(_inline_set_power, text)
    text = _INLINE_SUBSUP_RE.sub(_inline_subsup, text)
    text = _INLINE_POWER_RE.sub(_inline_power, text)
    text = _INLINE_SUBSCRIPT_RE.sub(_inline_subscript, text)
    # 剩下的 `^`/`~` 不能再留给 LaTeX: 文本模式下的 `^` 会让整篇编译中断。
    text = text.replace("^", r"\textasciicircum{}").replace("~", r"\textasciitilde{}")
    # 没被还原成下标的 `_` 仍然必须转义 (标识符/路径/DOI 里的下划线)
    text = _escape_active(text, "_")
    # Unicode → LaTeX: 数学符号与希腊字母进数学模式 (字体里没有这些字形)。
    # 放在最后: 它新造的 `$...$` (例如 `ᵀ`→`$^{\mathrm{T}}$`) 含 `^`, 而上面所有会
    # 改写 `^`/`_` 的步骤都已经过去了, 因此不会再被打到。
    for char, latex in _UNICODE_TO_LATEX.items():
        if char in text:
            text = text.replace(char, latex)
    for index, snippet in enumerate(inline):
        text = text.replace(f"\x00POW{index}\x00", snippet)
    for index, block in enumerate(math_blocks):
        text = text.replace(f"\x00MATH{index}\x00", block)
    return text


def escape_math(text: str) -> str:
    """转义**公式体** (`Block.math`, 会原样放进 `equation` 环境)。

    与 `escape_latex` 的区别: 这里已经在数学模式里, 因此

    - Unicode 符号用**不带 `$`** 的命令形式 (`λ`→`\\lambda`, `×`→`\\times`);
    - `^`/`_` 是合法语法, 不动;
    - 全角标点在数学模式里没有字形, 换成 ASCII 等价物。

    不做这一步的后果实测: 公式里带 `λ`/`，`/`−` 时日志报
    `Missing character: There is no λ in font cmmi12`, 公式出现空洞。
    """
    body = text or ""
    for char, latex in _UNICODE_TO_LATEX_MATH.items():
        if char in body:
            body = body.replace(char, latex)
    for char, latex in _CJK_PUNCT_IN_MATH.items():
        if char in body:
            body = body.replace(char, latex)
    # `%` 是注释符, `#` 是宏参数符, 在公式里都必须转义 —— 但**已经转义过的不能再转**
    # (见 `_escape_active`: `\#` 二次转义成 `\\#` 会让 display math 直接报错)
    body = _escape_active(_escape_active(body, "%"), "#")
    return body


def _display_math(value: str) -> str:
    """Split long top-level formula groups; never split an existing TeX environment."""
    body = escape_math(value)
    if re.search(r"\\(?:begin|end)\{|\\\\", body):
        return body
    rows: list[str] = []
    start, depth = 0, 0
    index = 0
    while index < len(body):
        char = body[index]
        if char in "{([" and (index == 0 or body[index - 1] != "\\"):
            depth += 1
        elif char in "})]" and (index == 0 or body[index - 1] != "\\"):
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            rows.append(body[start:index].strip())
            start = index + 1
        elif depth == 0 and len(body) > 110 and body.startswith(r"\qquad", index):
            rows.append(body[start:index].strip())
            index += len(r"\qquad")
            start = index
            continue
        index += 1
    rows.append(body[start:].strip())
    if len(rows) > 1 and all(rows):
        return r"\begin{aligned}" + r" \\ ".join("&" + row for row in rows) + r"\end{aligned}"
    return body


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
    labeled_claims: set[str] = set()
    if author:
        lines[0] = lines[0].replace("AI Research Team", escape_latex(author))

    if (manuscript.abstract or "").strip():
        lines += [r"\begin{abstract}", escape_latex(manuscript.abstract),
                  r"\end{abstract}", ""]
    if manuscript.keywords:
        lines += [r"\noindent\textbf{关键词：} "
                  + "；".join(escape_latex(value) for value in manuscript.keywords), ""]

    for section in manuscript.sections:
        # Manuscript 的章节名可带作者输入的编号；LaTeX 自己编号，不能出现「1 1 结果」。
        # 参考文献由下方统一生成，否则模型/离线稿件自带的该章节会再打印一次。
        if section.role == "references" or "参考文献" in (section.heading or ""):
            continue
        heading = escape_latex(_section_heading(section.heading or ""))
        command = {1: "section", 2: "subsection", 3: "subsubsection"}.get(section.level, "section")
        lines.append(f"\\{command}{{{heading}}}" if heading else rf"\{command}*{{}}")
        lines.append("")
        for block in section.blocks:
            lines.extend(_render_block(block, citations, figures or {}, labeled_claims))
        lines.append("")

    lines.append(_bibliography(citations, labels))
    lines.append(LATEX_POSTAMBLE)
    return "\n".join(line for line in lines if line is not None)


def _section_heading(value: str) -> str:
    return re.sub(r"^\s*(?:\d+(?:\.\d+)*[.、]?\s+|[一二三四五六七八九十]+[、.]\s*)",
                  "", value).strip()


def _render_block(block: Block, citations: dict[str, int],
                  figures: dict[str, str],
                  labeled_claims: set[str] | None = None) -> list[str]:
    """一个块 → LaTeX 行 (公式/表格/图引用/正文各按角色排版)。"""
    out: list[str] = []
    text = escape_latex(block.text or "")
    # 结论块生成 LaTeX 锚点；同一结论的重复块只保留第一次定义。
    claim_labels = [f"claim:{ref.id}" for kind, ref in zip(block.ref_kinds, block.refs)
                    if kind == RefKind.claim.value and ref.id]
    if block.role.value == "claim" and claim_labels:
        label = claim_labels[0]
        if labeled_claims is None or label not in labeled_claims:
            out.append(f"\\label{{{label}}}")
            if labeled_claims is not None:
                labeled_claims.add(label)
    if block.math:
        # 公式体**必须**先过 escape_math: 模型常直接写 Unicode (λ/≡/×/−/，),
        # 原样落进 equation 会缺字形, 落到文本模式还会中断整篇编译。
        out += ["", f"\\begin{{equation}}{_display_math(block.math)}\\end{{equation}}", ""]
    marks = [f"\\cite{{ref{citations[ref.id]}}}"
             for kind, ref in zip(block.ref_kinds, block.refs)
             if kind == RefKind.source.value and ref.id in citations]
    from src.publication.references import citation_suffix
    if text:
        out.append(citation_suffix(text, "".join(marks)))
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
        entry = labels.get(object_id) or "书目信息未登记"
        lines.append(f"\\bibitem{{ref{number}}} {escape_latex(str(entry))}")
    lines.append(r"\end{thebibliography}")
    return "\n".join(lines)
