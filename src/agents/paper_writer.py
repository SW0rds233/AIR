from __future__ import annotations

"""论文初稿撰写智能体（Markdown 版）

流水线中间格式: Markdown（citation_guard / citation_check / review 均基于 md）。
最终 LaTeX 由 latex_render 节点转换生成（含 BibTeX + 编译）。"""

import re as _re

from langchain_core.messages import SystemMessage, HumanMessage

from src.config import LLM_CONFIG, build_llm
from src.graph.state import PipelineState
from src.utils.cost_tracker import tracker, extract_usage_metadata
from src.utils.context_budget import budget_text, list_to_budgeted

PAPER_WRITER_SYSTEM = """你是"论文初稿撰写智能体"，一名经验丰富的学术论文撰写专家。

你的任务：基于文献综述素材，撰写一篇结构完整、逻辑严谨、符合学术规范的综述论文初稿（Markdown 格式）。

## 论文结构（Markdown 标题层级必须严格遵守）

- `#` 仅用于**论文标题**（一行，如「[主题]：A Comprehensive Survey」）
- `##` 用于**章**（一级标题，带编号 "1", "2", ...）
- `###` 用于**节**（二级标题，带编号 "1.1", "2.1", ...）

**必须完整包含以下部分，缺一不可：**

- **标题**（`#` 一行）
- **摘要**（`## 摘要`，150-250 词，覆盖背景/范围/发现/展望）
- 以下 6 个编号章节（不要自行扩增或删减）：
  1. **引言（Introduction）**: 研究背景、问题定义、综述贡献、文献检索策略、结构安排
  2. **相关工作（Related Work）**: 前置知识、早期工作回顾、与已有综述的差异
  3. **核心方法分类详述（Taxonomy）**: 按方法/信号来源/学习范式分类
  4. **比较与分析（Comparison & Analysis）**: 跨类别对比表格
  5. **挑战与未来方向（Challenges & Future）**: 开放问题和前沿论文
  6. **结论（Conclusion）**: 主要发现总结

## 写作规范

- 主体语言为中文，专业术语首次出现标注英文
- 引用格式使用数字编号 [1], [2]...
- 使用 Markdown 表格表示分类体系和对比分析；图表位置用「[图N: 图题]」「[表N: 表题]」占位
- 如果输入中有 LaTeX 公式则保留
- **篇幅硬性上限 20000 字**：全文（正文，不含参考文献）不得超过 20000 字；初稿建议 12000-16000 字，宁短勿长。字数按"去除空格和换行后的字符数"计
- **客观中性用语**：禁止使用「革命性突破」「巨大成功」「史无前例」等夸张词汇，改用「显著进展」「受到广泛关注」等客观表述
- **摘要需含量化/具体发现**：至少一句体现核心结论或数据（如性能对比、瓶颈程度），不要全是背景描述
- **不要声称具体文献数量**：摘要/正文中不得写「基于XX篇文献」这类具体数字（你无法准确统计），改用「系统梳理了该领域正式发表的核心文献」等概括表述
- **摘要严格控制在 150-250 词**：中文约 200-350 字，超出会被审稿人扣分
- **术语全文统一**：首次定义后全文使用同一术语（如统一用「射频指纹识别」，不要与「射频指纹」「RF指纹」混用）
- **「已有综述」对比只能列主题直接相关的专门综述**：不要把 IoT 安全通用综述等宽泛领域综述当作本主题的专门综述来对比

## 参考文献章节（系统自动生成）

- **你不需要输出参考文献列表**：参考文献章节由系统自动生成
- 你只需在正文中使用 [n] 编号引用，编号必须与清单一致
- 不要在正文末尾自行编写「参考文献/References」列表

## 证据纪律（abstention 原则）

1. **只能引用「可信参考文献清单」中的文献**，不得引入清单之外的任何文献
2. 如果某个论点没有清单中的文献支撑，应省略该论点，或改述为无需引用的展望性/背景性陈述；不得编造引用，也不得输出「[缺证据]」之类的占位标注
3. 宁缺毋滥：覆盖度低一点可以接受，引用造假不可接受
4. 文中 [n] 编号必须与清单编号一致
5. **只引用已发表文献**（有期刊/会议出处的），不得引用预印本（arXiv）
6. **正文提及作者必须与清单核对**：写「X 等/X 等人提出…[n]」前，必须确认清单中 [n] 的作者确实包含 X；不确定作者时用「文献[n]提出…」句式，不得写出作者名。严禁把 A 论文的描述（方法、结论、作者）挂在 B 论文的编号上

**禁止**在正文末尾输出任何统计信息、字数、章节数、引用数等元信息（这些由系统自动统计）。
"""

PAPER_REVISION_SYSTEM = """你是"论文修订智能体"，你正在对一篇已写好的综述论文进行**修改**，而非从头撰写。

## 你的任务

对上一版论文进行**针对性修订**，重点解决审稿意见中指出的问题。修订包含三种操作，按需组合使用：
- **增添**：审稿人指出的缺失内容，用清单中的论文补充（审稿人会成对给出"增加X + 精简Y"建议）
- **精简**：审稿人建议精简的冗余表述、重复段落，坚决删除或合并
- **改写**：修正表述、术语、格式、逻辑问题

## 修订原则

1. **修订契约优先**：只把「本轮修订契约」列出的有限目标作为本轮修改范围，并逐条满足其验收标准
2. **保留好的部分**：契约未涉及的章节、关键论点和有效引用保持原样，不要重写
3. **逐一解决问题，每条契约必须产生可见修改**：契约中的每一条问题都必须在正文中有可直接定位的修改结果。系统会在你输出后逐条程序化核验：对应章节没有任何实质改动的条目会被判为"未完成"并要求返工。宁可把少数几条改到位，也不要全文原样复述
4. **不要引入新问题**：契约外的任何意见（包括审稿意见中列出但未进入契约的条目）本轮一律不处理，也不要扩写无关内容；审稿意见全文仅用于理解契约条目的背景。实测教训：契约外扩张修改会同时引入新缺陷并推高篇幅，导致评分不升反降
5. **引用纪律不变**：仍然只能引用可信参考文献清单中的文献。正文提及作者必须与清单核对：写「X 等/X 等人提出…[n]」前，确认清单中 [n] 的作者确实包含 X；不确定作者时用「文献[n]提出…」句式。严禁把 A 论文的描述挂在 B 论文的编号上
6. **输出完整论文**：输出完整的修订后论文，保持原有章节结构（`#` 标题 / `##` 章 / `###` 节），不要只输出修改的部分，也不要自行增删章节
7. **客观用语**：不得使用「革命性突破」「巨大成功」等夸张词汇
8. **篇幅硬性上限 20000 字**：修订后全文（不含参考文献）不得超过 20000 字。审稿人建议"增加X"时，**必须同步落实其"精简Y"建议**（或自行精简等量冗余），保证总字数不超上限
9. **定量数据要求**：审稿人要求定量数据（准确率/F1/时延/设备数等）时，优先从「原文证据映射」与文献综述素材中提取具体数值写入表格，并用对应引用标注数值出处；素材确无数值依据时，改为明确的定性对比（高/中/低并说明依据），不得使用「明显提升」「显著效果」「数值见文献」等模糊或空泛表述，也不得编造数字

## 契约条目的处置规则（逐条对照执行）

- 条目要求**补充/引用某文献**：
  - 该文献在「新增可信引用」或「审稿人推荐且已在可信清单中的论文」中 → 找到对应 [n]，在契约指定章节写入实质性技术分析（≥2-3 句），并更新相关表格/分类
  - 该文献在「无法加入清单的审稿推荐论文」中 → **严禁引用或编造编号**，改为用清单中主题最接近的文献改写该论述，或删除该论述
- 条目要求**重组/合并/拆分章节内容**：实际调整章节内的段落组织、表格行或子类划分，仅在原句上微调不算完成
- 条目要求**删减/精简**：真正删除或合并冗余段落，字数应有可见下降
- 条目要求**修正表格/评级/表述**：直接改到对应表格单元格或句子
- 输出前逐条自检：每条契约 → 修改落在哪个章节 → 该章节是否确实已改。自检发现未落实的条目，先补改再输出

## 处理方法

- 看到「补充某论文」→ 找到清单中对应的 [n]，在相应位置加入（若清单中无此论文则忽略该建议）
- 看到「删除虚构引用」→ 删除该引用或替换为清单中的有效引用
- 看到「引用主题错配/不当引用/与主题无关」→ 删除该句，或替换为清单中主题直接相关的论文；替换时必须选择标题明确含 RF fingerprint/radio/wireless/emitter/SEI/射频/无线/辐射源 的文献，严禁选用标题含 recording/audio/multimedia/image/video/speech 等字样的论文（实测教训：用录音设备/多媒体识别论文支撑射频指纹论述会被审稿人连续判 Critical）
- 看到「格式问题」→ 按要求修正表格、标题、引用格式
- 看到「内容不足」→ 用清单中的论文补充，同时按审稿人建议精简冗余段落，控制总字数
- 看到「逻辑问题」→ 调整段落顺序或重写过渡句
- 看到「删除 [缺证据]」→ 直接删除该论断或改述为无需引用的展望性陈述
- 看到「内容重复/精简建议」→ 删除或合并重复、冗余段落，改为前向引用（如"详见 X.X 节"）

**禁止**在正文末尾输出任何统计信息、字数、章节数、引用数等元信息（这些由系统自动统计）。"""


EVIDENCE_MAX_REFS = 30
# 证据预算: 修订轮审稿人常要求定量数据 (准确率/设备数/时延),
# 这些数值只存在于原文证据块中 — 预算过小会导致 Writer 只能写定性表述,
# "缺定量数据"问题反复出现 (实测 R-CMP-01 连续 3 轮部分解决)
EVIDENCE_TOTAL_BUDGET = 14000
NOTES_BUDGET_FIRST = 20000
NOTES_BUDGET_REVISION = 10000
FULLTEXT_TOPIC_BUDGET = 8000
# 全文(不含参考文献)的字符数硬性上限 (去除空格/换行后的字符数)
MAX_DRAFT_CHARS = 20000


def _build_ref_sheet(verified_refs: list[dict]) -> str:
    if not verified_refs:
        return ""
    lines = [
        "### 可信参考文献清单（只能引用清单内的文献，编号必须与清单一致）",
        "",
        "**只引用已发表文献（有期刊/会议出处或 DOI）；不得引用预印本（arXiv）。**",
        "",
        "| # | 标题 | 作者 | 年份 | 出处 |",
        "|---|------|------|------|------|",
    ]
    for e in verified_refs:
        src = e.get("venue") or e.get("source") or ""
        if e.get("published") is False:
            src = "预印本: " + src
        lines.append(f"| [{e.get('ref_number', '')}] | {e.get('title', '')} | {e.get('authors', '')} | {e.get('year', '')} | {src} |")
    return "\n".join(lines)


def _build_evidence_map(verified_refs: list[dict]) -> str:
    try:
        from src.rag.vector_store import search_fulltext_by_title
    except Exception:
        return ""
    items = []
    for e in verified_refs[:EVIDENCE_MAX_REFS]:
        title = e.get("title", "")
        if not title:
            continue
        try:
            chunks = search_fulltext_by_title(title, k=2)
        except Exception:
            chunks = []
        if not chunks:
            continue
        ev = "\n\n".join(c["text"][:600] for c in chunks[:2])
        items.append(f"**[{e.get('ref_number')}] 《{title}》({e.get('year', '')})**\n{ev}")
    if not items:
        return ""
    return "\n\n### 原文证据映射\n\n" + list_to_budgeted(items, EVIDENCE_TOTAL_BUDGET, label="证据段落")


def _build_fulltext_topic_context(topic: str) -> str:
    try:
        from src.rag.vector_store import search_fulltext
        chunks = search_fulltext(topic, k=6)
        if not chunks:
            return ""
        items = [f"### 来自论文「{c['title']}」({c.get('year','')})\n" + budget_text(c["text"], 1000, label="原文段落") for c in chunks]
        return "\n\n### 全文检索到的相关原文段落\n\n" + list_to_budgeted(items, FULLTEXT_TOPIC_BUDGET, label="原文段落")
    except Exception:
        return ""


# 章节号最多两级小数 (3.6.4): 旧正则 (\d+(?:\.\d+)?) 把 "3.6.4节" 误解析为 "6.4",
# 导致契约核验找不到目标小节 (实测 R-FED-01 / R-OPEN-01 假阴性)
_CONTRACT_SECTION_RE = _re.compile(r"(\d+(?:\.\d+){0,2})\s*节")
_CONTRACT_CHAPTER_RE = _re.compile(r"第\s*(\d+)\s*章")


def _draft_sections(draft: str) -> list[tuple[str, str]]:
    """按标题把正文切分为 (标题, 内容) 列表 (不含参考文献章节)

    必须包含 #### 四级标题: 修订稿常用 ### 章 / #### 节 (如 3.6.4),
    漏掉 H4 会让契约核验找不到目标章节 → 误判"未完成" (实测 R-FED-01 假阴性)。
    """
    from src.rag.reference_formatter import strip_references_section

    body = strip_references_section(draft or "")
    return [
        (m.group(1).strip(), m.group(2))
        for m in _re.finditer(r"(?m)^(#{1,4}\s+[^\n]+)\n?(.*?)(?=^#{1,4}\s|\Z)", body, _re.S)
    ]


def _item_target_sections(item: dict) -> list[str]:
    """从契约条目的问题/验收证据中定位目标章节编号 (如 3.3 / 5.4 / 摘要)"""
    text = f"{item.get('problem', '')} {item.get('evidence', '')}"
    targets: list[str] = []
    for m in _CONTRACT_SECTION_RE.finditer(text):
        targets.append(m.group(1))
    for m in _CONTRACT_CHAPTER_RE.finditer(text):
        targets.append(m.group(1))
    if "摘要" in text:
        targets.append("摘要")
    seen: set[str] = set()
    return [t for t in targets if not (t in seen or seen.add(t))]


def check_contract_compliance(
    prior_draft: str,
    new_draft: str,
    contract: list[dict],
    new_ref_numbers: list[int] | None = None,
) -> list[dict]:
    """修订后逐条程序化核验契约, 返回未完成条目 [{item, reason}]

    判定标准 (确定性, 不依赖 LLM):
    - 全文与上一版几乎一致 → 所有 Writer 职责条目均未执行
    - 条目目标章节 (按问题/证据中的章节号定位) 与上一版一致 → 该条未完成
    - "补充文献"类条目: 本轮新增引用编号未出现在正文 → 未完成

    返回空列表表示契约全部通过 (或无可核验条目)。
    """
    import difflib

    from src.rag.reference_formatter import find_citation_numbers, strip_references_section

    contract = [
        c for c in (contract or [])
        if not c.get("system_owned") and not c.get("stale_quote")
    ]
    if not contract or not prior_draft or not new_draft:
        return []

    prior_body = strip_references_section(prior_draft)
    new_body = strip_references_section(new_draft)
    global_ratio = difflib.SequenceMatcher(None, prior_body, new_body).ratio()
    # 全局门禁只拦截"整篇原样复述" (≥99.9% 一致); 小幅但真实的修订
    # (如只删一句不当批评) 交给逐条章节核验判定, 不得在此被整体否决
    if global_ratio >= 0.999:
        return [
            {"item": dict(item), "reason": "正文与上一版几乎一致, 契约条目未执行"}
            for item in contract
        ]

    prior_secs = _draft_sections(prior_draft)
    new_secs = _draft_sections(new_draft)

    def _sec_texts(num: str) -> tuple[str, str]:
        """收集编号为 num 的章节**及其全部子节**的文本。

        只比较章节标题下的引导段会漏掉子节改动 (目标 "3.6节" 而 Writer
        只改了 3.6.4 时会误判未完成), 故把 num 与 num.x 子节内容合并比较。
        """
        pattern = _re.compile(rf"^{_re.escape(num)}(?:[.、\s]|$)")

        def _collect(secs: list[tuple[str, str]]) -> str:
            parts = []
            for h, c in secs:
                if pattern.match(h.lstrip("#").strip()):
                    parts.append(c)
            return "\n".join(parts)

        return _collect(prior_secs), _collect(new_secs)

    new_cited = set(find_citation_numbers(new_body))
    unaddressed = []
    for item in contract:
        problem = str(item.get("problem", ""))
        targets = _item_target_sections(item)
        if targets:
            changed = False
            for num in targets:
                p, n = _sec_texts(num)
                if not p and n:
                    changed = True
                    break
                # 阈值 0.99: 小节内 1% 的改动 (如删除一句不当批评) 即视为已处理;
                # 旧阈值 0.995 过严, 小幅但关键的编辑会被误判为未完成 (实测 R-REL-01)
                if p and difflib.SequenceMatcher(None, p, n).ratio() < 0.99:
                    changed = True
                    break
            if not changed:
                unaddressed.append({
                    "item": dict(item),
                    "reason": f"目标章节（{'、'.join(targets)}）与上一版相比无实质改动",
                })
                continue
        # "补充/引用文献"类条目: 新增引用编号必须进入正文
        if new_ref_numbers and any(k in problem for k in ("补充", "遗漏", "纳入", "引用")):
            if not any(int(n) in new_cited for n in new_ref_numbers):
                unaddressed.append({
                    "item": dict(item),
                    "reason": "契约要求补充文献, 但新增可信引用编号未出现在正文",
                })
    return unaddressed


def _shrink_draft_if_needed(llm, messages: list, draft: str, result, model_name: str):
    """硬约束: 超长时带强制删减指令重生成一次 (上限 1 次), 保证篇幅尽量达标

    字数只统计正文 (不含参考文献): Writer 有时会自行输出参考文献列表,
    该章节最终由 attach_references_section 剥离重建。若把它计入字数会
    误判"超长"、触发不必要的删减重生成 —— 实测某轮正文仅 11.7k 字被
    误判 20.5k 字, 多余的重生成导致该轮质量明显下降。
    """
    from src.rag.reference_formatter import strip_references_section

    draft = strip_references_section(draft)
    total_words = len(draft.replace(" ", "").replace("\n", ""))
    if total_words <= MAX_DRAFT_CHARS:
        return draft, result, total_words

    print(f"  [paper_writing] 初稿超长 {total_words} 字 (> {MAX_DRAFT_CHARS}), 触发删减重生成")
    from langchain_core.messages import HumanMessage as _HM

    shrink_messages = list(messages) + [
        result,
        _HM(content=(
            f"你上一版输出约 {total_words} 字，超过 20000 字硬性上限。\n"
            f"请严格**删减**到不超过 20000 字：删除冗余和重复段落、合并相似论述，"
            f"保留所有章节结构（标题 + 摘要 + 6 章）和关键内容。\n"
            f"只做删减，不要新增任何内容、不要增删章节。\n"
            f"不要输出「参考文献」章节（由系统自动生成）。"
        )),
    ]
    result2 = llm.invoke(shrink_messages)
    draft2 = result2.content if hasattr(result2, "content") else str(result2)
    usage2 = extract_usage_metadata(result2)
    if usage2:
        tracker.add_call(model_name, usage2, stage="paper_writing_shrink")
    words2 = len(draft2.replace(" ", "").replace("\n", ""))
    if words2 < total_words:
        print(f"  [paper_writing] 删减后 {words2} 字")
        return draft2, result2, words2
    print(f"  [paper_writing] 删减未生效 (仍 {words2} 字), 保留较短版本")
    return draft, result, total_words


def _focused_revision_retry(
    llm, current_draft: str, unaddressed: list[dict], verified_refs: list[dict], model_name: str
):
    """契约核验未通过时的聚焦返工: 只要求完成未达标条目, 输出完整论文

    相比整轮重写, 返工提示只包含未完成条目 + 当前全文, 指令密度更高,
    Writer 更容易产生可定位的实质修改。最多执行一次 (由调用方控制)。
    """
    items_md = []
    for u in unaddressed[:6]:
        item = u["item"]
        items_md.append(
            f"- [{item.get('id', '')}]（{item.get('priority', '高')}）{item.get('problem', '')}\n"
            f"  未完成原因: {u['reason']}\n"
            f"  验收标准: {item.get('evidence', '在对应章节可定位到修复结果')}"
        )
    prompt = (
        "你上一轮的修订输出经程序核验，以下契约条目**未完成**（对应章节与上一版相比无实质改动）。\n"
        "请基于下方当前全文，仅针对这些条目完成修改，并输出完整修订后的论文：\n\n"
        + "\n".join(items_md)
        + "\n\n要求：\n"
        "1. 每条必须在对应章节产生可定位的实质编辑（重组段落、增补文献分析、删减冗余），仅微调原句不算完成\n"
        "2. 其余章节、关键论点与有效引用保持原样\n"
        "3. 只能引用下方可信参考文献清单中的编号；无法加入清单的论文严禁编造编号\n"
        "4. 输出完整论文（标题 + 摘要 + 6 章结构），不要输出修订说明\n\n"
        f"---当前全文---\n{current_draft}\n---全文结束---\n\n"
        f"{_build_ref_sheet(verified_refs)}"
    )
    messages = [SystemMessage(content=PAPER_REVISION_SYSTEM), HumanMessage(content=prompt)]
    result = llm.invoke(messages)
    usage = extract_usage_metadata(result)
    if usage:
        tracker.add_call(model_name, usage, stage="paper_writing_retry")
    return (result.content if hasattr(result, "content") else str(result)), result, messages


def run_paper_writing(state: PipelineState) -> dict:
    topic = state["research_topic"]
    lit_notes = state.get("literature_review_notes", "")
    verified_refs = state.get("verified_references", [])
    if not lit_notes and not verified_refs and not state.get("revision_prompt"):
        return {"error": "无任何文献素材或已验证引用，拒绝编造内容。", "current_phase": "paper_writing"}
    llm = build_llm("main")
    ref_sheet = _build_ref_sheet(verified_refs)
    evidence_map = _build_evidence_map(verified_refs)
    revision_prompt = state.get("revision_prompt", "")
    if revision_prompt:
        parts = [revision_prompt]
        if ref_sheet:
            parts.append(ref_sheet)
        if evidence_map:
            parts.append(evidence_map)
        if lit_notes:
            parts.append("---文献综述素材（参考，不得引入清单外论文）---\n" + budget_text(lit_notes, NOTES_BUDGET_REVISION, label="文献综述素材") + "\n---素材结束---")
        parts.append("严格约束：引用编号 [n] 必须来自上方可信参考文献清单；清单之外不得引用。")
        messages = [SystemMessage(content=PAPER_REVISION_SYSTEM), HumanMessage(content="\n\n".join(parts))]
    else:
        outline = state.get("paper_outline", "")
        outline_block = ""
        if outline:
            outline_block = "### 论文大纲\n\n" + budget_text(outline, 6000, label="论文大纲")
        prompt = (
            f"以下是关于「{topic}」的文献综述素材。请基于这些素材撰写一篇完整的综述论文初稿。\n\n"
            f"---文献综述素材---\n{budget_text(lit_notes, NOTES_BUDGET_FIRST, label='文献综述素材')}\n---素材结束---\n"
            f"{outline_block}\n{ref_sheet}\n{evidence_map}\n"
            f"{_build_fulltext_topic_context(topic)}\n"
            f"请严格按照上述论文结构（标题 + 摘要 + 6 章）撰写完整初稿，全文不超过 20000 字。\n"
            f"重要纪律：\n"
            f"1. 只能引用可信参考文献清单中的文献，引用编号必须与清单一致\n"
            f"2. 文献素材或原文段落中没有依据的内容，直接省略或改述为无需引用的背景/展望性陈述，不得输出「[缺证据]」\n"
            f"3. 绝对不得虚构清单之外的参考文献\n"
            f"4. 使用规范的 Markdown 表格（表头有 | 分隔行），表格须对齐\n"
            f"5. 图表位置用「[图N: 图题描述]」标注占位\n"
            f"6. 「原文证据映射」中标明的段落与其论文编号一一对应\n"
            f"7. 不要输出「参考文献」章节，不要输出修订说明/自检清单/统计信息等辅助内容\n"
        )
        messages = [SystemMessage(content=PAPER_WRITER_SYSTEM), HumanMessage(content=prompt)]
    result = llm.invoke(messages)
    draft = result.content if hasattr(result, "content") else str(result)
    usage = extract_usage_metadata(result)
    model_name = LLM_CONFIG["model"]
    if usage:
        model_name = (result.response_metadata.get("model_name") if isinstance(result.response_metadata, dict) else None) or LLM_CONFIG["model"]
        tracker.add_call(model_name, usage, stage="paper_writing")

    draft, result, total_words = _shrink_draft_if_needed(llm, messages, draft, result, model_name)

    # 契约符合性核验 + 聚焦返工 (仅修订轮):
    # Writer 常见失败模式是"收到契约但原样复述全文", 程序化逐条核验可客观发现,
    # 返工提示只含未完成条目, 指令密度高, 给 Writer 一次补救机会。
    # stale_quote 递补条目不强制验收 (引文与稿件不匹配, Writer 可能无从定位)。
    contract = [
        c for c in (state.get("revision_contract") or [])
        if not c.get("system_owned") and not c.get("stale_quote")
    ]
    if revision_prompt and contract:
        prior_draft = state.get("paper_draft", "")
        new_ref_numbers = state.get("revision_new_ref_numbers") or []
        unaddressed = check_contract_compliance(prior_draft, draft, contract, new_ref_numbers)
        if unaddressed:
            print(
                f"  [paper_writing] 契约核验: {len(unaddressed)}/{len(contract)} 条未完成, 触发聚焦返工: "
                + "; ".join(u["item"].get("id", "?") for u in unaddressed[:4])
            )
            try:
                retry_draft, retry_result, retry_messages = _focused_revision_retry(
                    llm, draft, unaddressed, verified_refs, model_name
                )
                retry_draft, retry_result, retry_words = _shrink_draft_if_needed(
                    llm, retry_messages, retry_draft, retry_result, model_name
                )
                retry_unaddressed = check_contract_compliance(
                    prior_draft, retry_draft, contract, new_ref_numbers
                )
                if len(retry_unaddressed) < len(unaddressed):
                    draft, result, total_words = retry_draft, retry_result, retry_words
                    unaddressed = retry_unaddressed
                    print(f"  [paper_writing] 聚焦返工生效: 未完成 {len(unaddressed)} 条")
                else:
                    print(f"  [paper_writing] 聚焦返工未改善 (未完成 {len(retry_unaddressed)} 条), 保留原输出")
            except Exception as e:
                print(f"  [paper_writing] 聚焦返工跳过: {e}")
        else:
            print(f"  [paper_writing] 契约核验: {len(contract)} 条全部完成")

    if total_words > MAX_DRAFT_CHARS:
        print(f"  [paper_writing] ⚠️ 仍超长 {total_words} 字 (> {MAX_DRAFT_CHARS})")

    section_count = draft.count("## ") + draft.count("# ")
    citation_nums = set(int(n) for n in _re.findall(r"\[(\d+)\]", draft))
    citation_pattern = len(citation_nums)
    from src.rag.reference_formatter import attach_references_section
    draft = attach_references_section(draft, verified_refs)
    paper_outline = "\n".join(line for line in draft.split("\n") if line.strip().startswith("#"))
    return {
        "messages": [result], "paper_draft": draft, "paper_outline": paper_outline,
        "total_words": total_words, "total_sections": max(section_count, 1),
        "total_citations": citation_pattern, "current_phase": "paper_writing",
    }
