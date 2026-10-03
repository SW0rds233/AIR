from __future__ import annotations

r"""阶段 4 (方案 v2 M4): 把理论研究成果接入既有撰写子图, 产出期刊量级长文。

设计原则 (与方案 §3 防火墙一致)
-------------------------------
1. **桥接而不是合并状态**: 既有撰写层 (`src/agents/paper_writer.py`,
   `src/graph/pipeline.py::build_writing_graph`) 面向 `PipelineState` (60+ 字段),
   理论侧是 `TheoryState` (34 字段且被契约测试锁住)。本模块只把理论成果**映射**成
   撰写层需要的最小输入, 不改任何一张状态定义。
2. **理论骨架是不可改写段**: 判定链与证书作为"素材"注入, 但**不要求撰写层复述**;
   撰写层产出只作为**附录**追加, 与阶段 3 的期刊式稿件分栏存放。
   因此长文永远不会覆盖已冻结的结论 —— 引用只能改变表述, 不能改变结论。
3. **失败可降级**: 没有模型可用 (离线) 或撰写层报错时, 保留阶段 3 稿件并如实记原因,
   **不阻断交付**。
"""

import re
from dataclasses import dataclass, field

from src.rag import reference_list as refmod
from src.research.schemas import ResearchSnapshot


@dataclass
class LongFormResult:
    """长文写作结果。`draft` 为空表示这一次没有产出 (原因见 `note`)。"""

    draft: str = ""
    note: str = ""
    usage: dict = field(default_factory=dict)

    @property
    def produced(self) -> bool:
        return bool(self.draft.strip())


def build_writing_inputs(snapshot: ResearchSnapshot, topic: str,
                         references: refmod.ReferenceList | None = None,
                         question: str = "") -> dict:
    """冻结快照 → 撰写层最小输入 (`PipelineState` 的子集)。

    逐一说明字段来源, 便于审查"撰写层到底看到了什么":
    - `research_topic`: 论文题目;
    - `verified_references`: 参考文献表 (**带 `ref_number`**, 编号与正文 `[n]` 同一套);
    - `literature_review_notes`: 理论素材 —— 命题陈述 + 判定链 + 证书摘要 + 假设/局限。
      这些是**可直接核对的事实**, 不是待检索的文献笔记;
    - `paper_outline`: 六章骨架 (与阶段 3 的期刊式稿件同构, 保证两栏不冲突);
    - `skip_retrieval`: True —— 素材已就绪, 不允许撰写层自行检索 (否则引用无法追溯)。
    """
    references = references or refmod.ReferenceList()
    claim_notes = _evidence_notes(snapshot)
    outline = _outline(topic, snapshot, question)
    return {
        "research_topic": topic or snapshot.problem_id,
        "topic_keywords": _keywords(snapshot),
        "verified_references": [_as_ref_dict(item) for item in references.references],
        "literature_review_notes": claim_notes,
        "paper_outline": outline,
        "skip_retrieval": True,
        "revision_count": 0,
        "max_revisions": 0,
    }


def _as_ref_dict(item: refmod.Reference) -> dict:
    """参考文献 → 撰写层的引用清单条目。

    `ref_number` 必须来自 `Reference.key` (ref1 → 1): 撰写层按 `[n]` 引用,
    编号与阶段 3 的正文引用表**同一套**, 两栏的 `[1]` 才不会指向不同文献。
    """
    number = item.key.replace("ref", "")
    return {
        "ref_number": number,
        "title": item.title,
        "authors": item.authors,
        "year": item.year,
        "venue": item.venue,
        "doi": item.doi,
        "url": item.url,
        "published": bool(item.venue or item.doi),
        "source_id": item.source_id,
    }


def _keywords(snapshot: ResearchSnapshot) -> list[str]:
    words: list[str] = []
    for claim in snapshot.claims:
        if claim.design_v and claim.design_k and claim.design_lambda:
            words.append(f"2-({claim.design_v},{claim.design_k},{claim.design_lambda})")
            break
    words.extend(["necessary conditions", "machine-checkable certificate"])
    return words


def _outline(topic: str, snapshot: ResearchSnapshot, question: str) -> str:
    supported = [c for c in snapshot.claims if c.status.value == "supported"]
    lines = [
        f"# {topic or snapshot.problem_id}",
        "",
        "摘要 (200-300 字, 覆盖问题/方法/结论/边界)",
        "关键词 (3-6 个)",
        "",
        "1 引言: 问题背景与本文要判定的问题",
        "2 问题与形式化: 定义、假设、符号表与适用条件",
        "3 主要结果: 逐条给出命题及其判定链 (结论必须与素材逐字一致)",
        "4 与已有工作比较: 只使用可信参考文献清单内的文献, 标明关系判定",
        "5 讨论与局限: 适用边界、未决项、未关闭义务",
        "6 结论",
        "附录 A 验证记录与判定链 (原样保留, 不得改写)",
    ]
    if question:
        lines.insert(2, f"研究问题: {question[:200]}")
    if supported:
        lines.append("")
        lines.append(f"必须逐字保留的既定结论 ({len(supported)} 条):")
        for claim in supported[:5]:
            lines.append(f"- {claim.id}: {claim.statement}")
    return "\n".join(lines)


def _evidence_notes(snapshot: ResearchSnapshot) -> str:
    """理论素材: 命题 + 判定链 + 证书摘要 + 假设/局限 (只读快照, 不新造内容)。"""
    from src.research.design_feasibility import certificate_of, render_certificate_chain

    blocks: list[str] = []
    for claim in snapshot.claims:
        blocks.append(f"### 结论 {claim.id} (状态 {claim.status.value})\n{claim.statement}")
    for assumption in snapshot.assumptions:
        blocks.append(f"### 假设 {assumption.id}\n{assumption.statement}")
    for record in snapshot.verifications:
        if record.stale:
            continue
        detail = (f"### 验证记录 [{record.tool}] 命题 {record.claim_id}@v{record.claim_version}\n"
                  f"验证状态 {record.validation_status.value}; 覆盖范围 {record.scope.value}; "
                  f"证书 {record.certificate or '-'}")
        certificate = certificate_of(record)
        if certificate:
            detail += "\n\n判定链 (可逐条反查定理与输入):\n" + render_certificate_chain(certificate)
        blocks.append(detail)
    open_obligations = [o for o in snapshot.obligations if o.status.value != "closed"]
    if open_obligations:
        blocks.append("### 未关闭义务 (写作时必须如实保留)\n" + "\n".join(
            f"- {o.id} ({o.status.value}): {o.statement}" for o in open_obligations))
    return "\n\n".join(blocks)


def write_long_form(snapshot: ResearchSnapshot, topic: str,
                    references: refmod.ReferenceList | None = None,
                    question: str = "",
                    writer=None) -> LongFormResult:
    """调用既有撰写层产出长文初稿。**只读快照**, 不回写任何研究对象。

    `writer` 可注入 (测试/替换实现); 默认使用 `paper_writer.run_paper_writing`。
    任何异常都转成 `note`, 不向上抛 —— 长文是增强项, 不能让研究交付失败。
    """
    import os

    if os.getenv("THEORY_LONG_FORM", "0") != "1":
        return LongFormResult(note="长文写作未启用 (THEORY_LONG_FORM=0)")
    inputs = build_writing_inputs(snapshot, topic, references, question)
    if not inputs["verified_references"] and not inputs["literature_review_notes"]:
        return LongFormResult(note="缺少素材 (既无参考文献也无理论判定链), 拒绝编造内容")
    if writer is None:
        try:
            from src.agents.paper_writer import run_paper_writing as writer
        except Exception as e:  # noqa: BLE001
            return LongFormResult(note=f"撰写层不可用 ({type(e).__name__}: {e})")
    try:
        outcome = writer(dict(inputs))
    except Exception as e:  # noqa: BLE001 - 长文失败不得中断交付
        return LongFormResult(note=f"长文写作失败 ({type(e).__name__}: {e})")
    if not isinstance(outcome, dict):
        return LongFormResult(note="撰写层返回了非预期结果")
    if outcome.get("error"):
        return LongFormResult(note=f"撰写层报告错误: {outcome['error']}")
    draft = str(outcome.get("paper_draft") or "")
    if not draft.strip():
        return LongFormResult(note="撰写层未产出稿件")
    return LongFormResult(draft=draft, usage=dict(outcome.get("usage") or {}))


# ----------------------------------------------------------------------
# 组装: 阶段 3 稿件 + 长文附录
# ----------------------------------------------------------------------

def append_long_form_appendix(markdown: str, draft: str, note: str = "") -> str:
    """把长文作为**附录**追加在期刊式稿件之后。

    为什么不合并成一篇: 阶段 3 稿件的每一条结论都能回到冻结快照 (可反查),
    而长文是生成性正文。两者混排会让"哪一句可核对"变得不可判定, 因此分栏保留,
    并由附录标题明确标注"长文初稿, 不替代第 3 节的判定链"。
    """
    if not (draft or "").strip():
        return markdown
    body = draft.strip()
    appendix = [
        "",
        "---",
        "",
        "## 附录 B 长文初稿 (撰写层产出, 不替代正文判定链)",
        "",
        "> 本附录由撰写层基于冻结快照与参考文献清单生成。**研究结论以第 3 节与附录 A 为准**;",
        "> 本附录中的表述若与第 3 节冲突, 以第 3 节为准。",
        "",
    ]
    if note:
        appendix.append(f"> 生成说明: {note}")
        appendix.append("")
    appendix.append(body)
    return (markdown.rstrip() + "\n" + "\n".join(appendix) + "\n")


_LATEX_SPECIAL_ESCAPE = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def draft_to_latex_section(draft: str, label: str = "长文初稿") -> str:
    """长文 Markdown → LaTeX 附录 (逐字符转义, 不解析 Markdown 结构)。

    生成性正文可能含任意 LaTeX 敏感字符 (下划线、百分号、`$`), 因此这里**一律转义**,
    不保留任何"看着像公式"的片段 —— 附录是说明性文字, 不是判定链; 宁可朴素, 不可
    因为一个裸 `$` 让整篇 `.tex` 编译失败。
    """
    lines = [f"\\section*{{附录 B {label}}}",
             "\\addcontentsline{toc}{section}{附录 B " + label + "}", ""]
    escape = _LATEX_SPECIAL_ESCAPE
    for raw in (draft or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            lines.append("")
            continue
        text = "".join(escape.get(ch, ch) for ch in line)
        heading = re.match(r"^#{1,6}\s*(.+)$", line)
        if heading:
            title = "".join(escape.get(ch, ch) for ch in heading.group(1))
            lines.append("\\subsection*{" + title + "}")
        else:
            lines.append(text)
    return "\n".join(lines) + "\n"


def append_latex_appendix(tex: str, draft: str, label: str = "长文初稿") -> str:
    """把长文附录插进 `\\end{document}` 之前。"""
    if not (draft or "").strip():
        return tex
    section = draft_to_latex_section(draft, label)
    marker = "\\end{document}"
    if marker not in tex:
        return tex + "\n" + section
    return tex.replace(marker, section + "\n" + marker)


__all__ = ["LongFormResult", "append_latex_appendix", "append_long_form_appendix",
           "build_writing_inputs", "draft_to_latex_section", "write_long_form"]
