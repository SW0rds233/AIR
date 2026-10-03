from __future__ import annotations

"""理论稿件结构化对象与数学排版 (P3)。

保留 prose / equation / theorem / lemma / proof / citation 等结构化块,
再分别渲染 Markdown 与 LaTeX (amsthm 环境, 公式编号, label/ref)。
"""

from dataclasses import dataclass, field


@dataclass
class Block:
    kind: str  # heading / prose / equation / theorem / lemma / corollary / proposition / proof / citation
    text: str
    label: str = ""
    title: str = ""
    level: int = 2


@dataclass
class Manuscript:
    title: str
    blocks: list[Block] = field(default_factory=list)
    writing_map: dict[str, str] = field(default_factory=dict)


_KIND_HEADING = {
    "theorem": "定理",
    "lemma": "引理",
    "corollary": "推论",
    "proposition": "命题",
}


def render_markdown(manuscript: Manuscript) -> str:
    lines = [f"# {manuscript.title}", ""]
    counters: dict[str, int] = {}
    for block in manuscript.blocks:
        if block.kind == "heading":
            prefix = "#" * max(2, min(block.level, 4))
            lines.append(f"{prefix} {block.text}")
            lines.append("")
        elif block.kind == "prose":
            lines.append(block.text.strip())
            lines.append("")
        elif block.kind == "equation":
            label = f" ({block.label})" if block.label else ""
            lines.append("$$")
            lines.append(block.text.strip())
            lines.append("$$")
            if label:
                lines.append(f"式{label}")
            lines.append("")
        elif block.kind in _KIND_HEADING:
            counters[block.kind] = counters.get(block.kind, 0) + 1
            num = counters[block.kind]
            tag = _KIND_HEADING[block.kind]
            lines.append(f"**{tag} {num}**（{block.title or block.label}）: {block.text.strip()}")
            lines.append("")
        elif block.kind == "proof":
            lines.append(f"*证明.* {block.text.strip()}")
            lines.append("")
        elif block.kind == "citation":
            lines.append(block.text.strip())
            lines.append("")
        else:
            lines.append(block.text.strip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_latex(manuscript: Manuscript) -> str:
    preamble = (
        "\\documentclass[12pt]{ctexart}\n"
        "\\usepackage{amsmath,amssymb,amsthm}\n"
        "\\newtheorem{theorem}{定理}\n"
        "\\newtheorem{lemma}{引理}\n"
        "\\newtheorem{corollary}{推论}\n"
        "\\newtheorem{proposition}{命题}\n"
        "\\theoremstyle{definition}\n"
        "\\newtheorem{definition}{定义}\n"
        "\\title{" + _escape_latex(manuscript.title) + "}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
    )
    body: list[str] = []
    counters: dict[str, int] = {}
    for block in manuscript.blocks:
        if block.kind == "heading":
            body.append("\\section{" + _escape_latex(block.text) + "}")
        elif block.kind == "prose":
            body.append(_escape_latex(block.text))
        elif block.kind == "equation":
            label = f"\\label{{{block.label}}}" if block.label else ""
            body.append("\\begin{equation}\n" + block.text.strip() + "\n" + label + "\n\\end{equation}")
        elif block.kind in _KIND_HEADING:
            counters[block.kind] = counters.get(block.kind, 0) + 1
            env = block.kind
            # 定理/引理/命题正文由生成器保证为 LaTeX 安全的数学片段, 不再转义
            title = "\\textbf{" + _escape_latex(block.title) + "} " if block.title else ""
            body.append(f"\\begin{{{env}}}[{_escape_latex(block.label) or ''}]\n"
                        + title + block.text.strip() + f"\n\\end{{{env}}}")
        elif block.kind == "proof":
            body.append("\\begin{proof}\n" + block.text.strip() + "\n\\end{proof}")
        elif block.kind == "citation":
            body.append(_escape_latex(block.text))
        else:
            body.append(_escape_latex(block.text))
    return preamble + "\n\n".join(body) + "\n\\end{document}\n"


def _escape_latex(text: str) -> str:
    specials = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(specials.get(ch, ch) for ch in (text or ""))
