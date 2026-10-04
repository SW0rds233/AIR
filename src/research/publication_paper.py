from __future__ import annotations

"""期刊式论文的**出版层** (方案 v2 §5 阶段 3)。

与 `theory_writer.build_manuscript` 的分工
------------------------------------------
- `build_manuscript` (既有): 把冻结快照如实展开成"研究稿" —— 它回答"研究做了什么、
  每条结论怎么验证的", 是**研究记录**;
- 本模块: 把同一份冻结快照组织成**一篇论文** —— 题名/作者占位/摘要/关键词/中图分类号/
  英文摘要/六章正文/参考文献/附录, 并保证"正文引用 ↔ 文献表"双向一致、结论可反查。

三条不可违反的约束 (方案 v2 §3 防火墙)
--------------------------------------
1. **结论原文照搬**: 命题陈述直接取自冻结快照, 本模块不做改写、不做"文献支持下的重述";
2. **摘要不得引入新论断**: 摘要由快照字段组装 (题面 + 结论 + 适用条件 + 局限), 未决项
   如实写明 —— 它不调用模型自由发挥, 因此不可能出现"摘要比结论更强"的情况;
3. **引用只在相关工作与讨论层**: 正文引用编号由 `reference_list` 统一分配, 缺文献时
   如实写检索范围, 不伪造引用、不省略参考文献章节。
"""

import re

from src.rag import reference_list as refmod
from src.rag.theory_render import Block, Manuscript
from src.research.schemas import (
    Claim,
    ClaimStatus,
    ObligationStatus,
    ResearchSnapshot,
    ResearchSpec,
    RetrievalCoverage,
)

# P0-3 / §7.3: 文稿结构与领域措辞由**任务画像**决定, 不再写死领域模板。
from src.research.task_profile import TaskProfile, profile_for_deliverables

# 作者/单位占位: 必须带"待作者补充"标注, 否则出版门槛会判为未标注占位符
AUTHOR_PLACEHOLDER = "作者姓名（待作者补充）"
AFFILIATION_PLACEHOLDER = "作者单位（待作者补充，含城市与邮编）"
CORRESPONDING_PLACEHOLDER = "通信作者（待作者补充，含邮箱）"
CLC_PLACEHOLDER = "中图分类号：待作者补充"

_SUPPORT_CN = {
    "theorem_application": "具名定理应用",
    "symbolic_derivation": "符号推导",
    "counterexample": "反例",
    "literature": "文献依据",
    "experiment": "实验/仿真",
    "definitional": "按定义",
    "assumption": "假设",
    "unsupported": "无支持",
}



def coverage_sentence(coverage) -> str:
    """把既有的 `RetrievalCoverage` (schemas) 如实转成一句可读的覆盖说明。

    不修改 schema: 覆盖记录是研究对象的字段, 呈现方式属于出版层。
    """
    if coverage is None:
        return ("本次运行没有检索覆盖记录（离线或未接入检索源），"
                "因此本节的已有工作比较不构成“无先例”的判断依据。")
    if not getattr(coverage, "executed", False):
        return ("本次运行未执行外部文献检索（离线或未接入检索源），"
                "因此本节的已有工作比较不构成“无先例”的判断依据。")
    parts = [f"本次检索覆盖检索引擎：{'、'.join(getattr(coverage, 'engines', []) or []) or '未记录'}"]
    queries = list(getattr(coverage, "queries", []) or [])
    if queries:
        parts.append("检索式：" + "；".join(queries[:5]))
    parts.append(f"命中 {getattr(coverage, 'hits', 0)} 条，入库 {getattr(coverage, 'ingested', 0)} 条")
    uncovered = list(getattr(coverage, "uncovered", []) or [])
    if uncovered:
        parts.append("未覆盖范围：" + "、".join(uncovered[:5]))
    failures = list(getattr(coverage, "failures", []) or [])
    if failures:
        parts.append("检索失败：" + "、".join(failures[:3]))
    if getattr(coverage, "scope_note", ""):
        parts.append(str(coverage.scope_note))
    return "。".join(parts) + "。"


def build_publication_paper(snapshot: ResearchSnapshot, topic: str = "",
                            references: refmod.ReferenceList | None = None,
                            coverage: RetrievalCoverage | None = None,
                            delivery_level: str = "",
                            spec: ResearchSpec | None = None,
                            related_evidence: list | None = None,
                            profile: TaskProfile | None = None
                            ) -> tuple[Manuscript, list[str]]:
    """冻结快照 + 研究规格 + 文献表 → 期刊式论文稿件。

    返回 `(稿件, 悬空引用键列表)`。悬空引用**不静默丢弃**: 它写进正文并在
    `publication_checks` 里被挡下。

    为什么需要 `spec`: **题面在规格里, 不在快照里** (`ResearchSnapshot` 只有结论与
    验证记录)。论文的题名/引言/问题陈述必须来自规格的 `problem_statement` / `direction`
    / `original_request` —— 从快照字段猜题面会写出与用户问题不符的引言。

    为什么需要 `related_evidence`: 出版层检索到的证据**不在冻结快照里**, 而第 4 节
    要逐条给出关系判定。不传它就会出现"参考文献表有 N 条、第 4 节却写'未发现可比较的
    工作'"的自相矛盾 (实测)。

    为什么需要 `profile` (合并计划 §7.3 / P0-3): 文稿的章节骨架与领域措辞必须由**任务
    画像**决定。不传时按快照里是否真的有设计参数降级推断 (兼容旧调用点), 但那时
    "章节骨架"退回默认的问题报告骨架, 不会硬套期刊六章。
    """
    references = references or refmod.ReferenceList()
    if coverage is None:
        coverage = getattr(spec, "coverage", None) or RetrievalCoverage()
    missing_citations: list[str] = []
    question = _problem_text(spec, topic)
    if profile is None:
        profile = profile_for_deliverables(
            _deliverables_from_spec(spec),
            has_design_parameters=bool(_design_params(snapshot)))

    supported = [c for c in snapshot.claims if c.status == ClaimStatus.supported]
    refuted = [c for c in snapshot.claims if c.status == ClaimStatus.refuted]
    unresolved = [c for c in snapshot.claims
                  if c.status in (ClaimStatus.blocked, ClaimStatus.proposed,
                                  ClaimStatus.in_progress)]
    open_obligations = [o for o in snapshot.obligations
                        if o.status in (ObligationStatus.open, ObligationStatus.blocked)]

    topic = topic or snapshot.problem_id
    manuscript = Manuscript(title=_title(topic, snapshot))
    add = manuscript.blocks.append

    # ---- 题名页 ----
    # 用专门 kind 而不是 prose: LaTeX 侧作者块已在 `\author{}` 里, 再输出一遍会与
    # 页首重复 (实测 PDF 里出现过两行"作者单位（待作者补充）")。
    add(Block("titlepage", AUTHOR_PLACEHOLDER))
    add(Block("titlepage", AFFILIATION_PLACEHOLDER))
    add(Block("titlepage", CORRESPONDING_PLACEHOLDER))

    # ---- 中文摘要 / 关键词 / 分类号 ----
    add(Block("abstract", _abstract(question, supported, refuted, unresolved,
                                    open_obligations, profile)))
    add(Block("keywords", _keywords(snapshot, supported, profile)))
    add(Block("prose", CLC_PLACEHOLDER))

    # ---- 英文题名与摘要 ----
    # 用 titlepage kind: 它不是编号章节 (实测被编成"1 英文题名与摘要", 把后续章节
    # 整体推移成 2/3/4…, 与正文自带的"1 引言"打架)。
    add(Block("titlepage", _english_title(supported, refuted)))
    add(Block("titlepage", _english_abstract(supported, refuted, unresolved)))
    add(Block("titlepage", "Key words: " + _english_keywords(supported)))

    # ---- 1 引言 ----
    add(Block("heading", "1 引言"))
    add(Block("prose", _introduction(question, references, coverage, snapshot,
                                     profile)))

    # ---- 2 问题与形式化 ----
    add(Block("heading", "2 问题与形式化"))
    # P0-3: 第 2 节也必须按画像写。抽取前这里**无条件**说"把问题形式化为参数 v、k、λ
    # 的 2-设计存在性判定" —— 与整篇稿件同样的领域硬编码, 只是藏在第二节。
    if profile.allows_design_vocabulary():
        add(Block("prose", "本节把问题形式化为参数 $v$、$k$、$\\lambda$ 的 2-设计存在性判定："
                           "把题面给出的每条计数约束写成对 $(v,k,\\lambda)$ 的代数条件，"
                           "再逐条核验经典必要条件。完整的题面陈述与研究对象见第 1 节。"))
    elif not profile.allows_formal_vocabulary():
        add(Block("prose", "本节明确研究对象、依据来源与适用条件：每条结论都附可定位的"
                           "依据或明确的未决说明。完整的题面陈述见第 1 节。"))
    else:
        add(Block("prose", "本节把题面给出的对象、量词与约束写成可核验的形式，"
                           "并声明结论成立的适用条件。完整的题面陈述见第 1 节。"))
    for item in snapshot.definitions:
        text = getattr(item, "statement", "") or getattr(item, "natural_language", "")
        add(Block("prose", f"- 定义 {getattr(item, 'id', '')}：{text}"))
    for model in snapshot.models:
        add(Block("prose",
                  f"- 模型 {model.id}（{'已选' if model.selected else '候选'}）："
                  f"{model.natural_language or model.name}；"
                  f"编码 {model.formal_encoding or '-'}；适用范围 "
                  f"{model.verified_scope or '未声明'}"))
    if snapshot.assumptions:
        for asm in snapshot.assumptions:
            state = "已接受" if asm.accepted else "已修订/放弃"
            add(Block("prose", f"- 假设 {asm.id}（{state}）：{asm.statement}"))
    else:
        add(Block("prose", "本文不引入额外假设：结论在显式声明的变量域与量词下成立。"))
    add(Block("prose", _symbol_table(snapshot)))

    # ---- 3 主要结果 ----
    add(Block("heading", "3 主要结果"))
    if supported:
        for claim in supported:
            add(Block(_claim_kind(claim), _claim_statement(claim), label=claim.id,
                      title=claim.id))
            manuscript.writing_map[claim.id] = f"{_claim_kind(claim)}-{claim.id}"
            # 设计/计数类结论: 用 `certificate` 块占位, 由 LaTeX 渲染器排成
            # 公式块 + 分条 + 汇总表 (见 `render_certificate_latex`)。
            # 其他结论仍用文字证明块。
            certificate = _certificate_of_claim(snapshot, claim)
            if certificate:
                # 正文用结构化证书排版; Markdown 侧给出判定链原文 (逐条可反查)
                from src.research.design_feasibility import render_certificate_chain

                add(Block("certificate", render_certificate_chain(certificate),
                          label=claim.id))
            else:
                add(Block("proof", _proof_text(claim, snapshot)))
            add(Block("prose", _claim_footer(claim)))
    else:
        add(Block("prose", "本次运行未得到已确定结论。"))
    for claim in refuted:
        witness = _counterexample(snapshot, claim)
        add(Block("proposition", f"原命题为假：{_claim_statement(claim)}",
                  label=claim.id, title=claim.id))
        add(Block("proof", (f"反例（满足声明前提）：取 {witness}，原命题不成立。"
                            if witness else
                            "该否定性结论缺少可回代的反例见证，需补充后复核。")))
        manuscript.writing_map[claim.id] = f"proposition-{claim.id}"

    # ---- 4 与已有工作比较 ----
    add(Block("heading", "4 与已有工作比较"))
    for block in _related_work(snapshot, references, coverage, missing_citations,
                              related_evidence):
        add(block)

    # ---- 5 讨论与局限 ----
    add(Block("heading", "5 讨论与局限"))
    add(Block("prose", _discussion(supported, unresolved, open_obligations)))
    for spec in snapshot.experiment_specs or []:
        status = str(spec.get("execution_status", "proposed"))
        add(Block("prose",
                  f"- 实验/仿真建议 {spec.get('id')} [{status}]：目的 "
                  f"{spec.get('purpose')}；检验 {spec.get('claim_id')}；判据 "
                  f"{str(spec.get('decision_rule', ''))[:120]}；"
                  + ("已执行" if status in ("executed", "analyzed")
                     else "尚未执行，不得作为已验证证据")))

    # ---- 6 结论 ----
    add(Block("heading", "6 结论"))
    add(Block("prose", _conclusion(supported, refuted, unresolved)))

    # ---- 参考文献 (章节必须在位, 即便为空) ----
    # 用专门 kind 而不是 heading: LaTeX 侧参考文献由 `thebibliography` 环境承担,
    # 多一个 `\section{参考文献}` 会在正文里留下一个空章节 (实测 PDF 里出现过
    # `\#\# 参考文献` 的转义残留)。
    add(Block("references", references.render_markdown(_no_reference_reason(coverage))
              .removeprefix("## 参考文献").lstrip()))

    # ---- 附录 ----
    add(Block("heading", "附录 A 验证记录与判定链"))
    # P0-3: 附录前言同样按画像写。抽取前它**无条件**说"逐条必要条件核验与证书指纹",
    # 对非设计类研究是错的领域描述。
    if profile.allows_design_vocabulary():
        add(Block("prose",
                  "本附录给出完整的判定对象、逐条必要条件核验与证书指纹。"
                  "附录内容由冻结快照原样导出，**不得改写**；第 3 节的结论以本附录为准。"))
    elif not profile.allows_formal_vocabulary():
        add(Block("prose",
                  "本附录给出每条结论所依据的来源定位与完整的验证记录。"
                  "附录内容由冻结快照原样导出，**不得改写**；正文的结论以本附录为准。"))
    else:
        add(Block("prose",
                  "本附录给出完整的判定对象、逐条条件核验与证书指纹。"
                  "附录内容由冻结快照原样导出，**不得改写**；正文的结论以本附录为准。"))
    for record in snapshot.verifications:
        if record.stale:
            continue
        add(Block("prose", _verification_line(record)))

    if missing_citations:
        add(Block("prose", "引用完整性问题（需回到检索/写作阶段修正）："
                           + "、".join(sorted(set(missing_citations)))))
    if delivery_level:
        add(Block("prose", f"（本次交付级别：{delivery_level}）"))
    return manuscript, missing_citations


# ----------------------------------------------------------------------
# 各节内容 (全部由快照字段组装, 不做自由发挥)
# ----------------------------------------------------------------------

def _title(topic: str, snapshot: ResearchSnapshot) -> str:
    for claim in snapshot.claims:
        if claim.status in (ClaimStatus.supported, ClaimStatus.refuted) \
                and claim.design_v and claim.design_verdict:
            return (f"{claim.design_v}-{claim.design_k}-{claim.design_lambda} 设计的"
                    f"{_verdict_cn(claim.design_verdict)}性判定")
    return f"关于「{topic}」的理论研究"


def _verdict_cn(verdict: str) -> str:
    return {"nonexistent": "不存在", "exists": "存在",
            "necessary_met": "必要条件满足但未定"}.get(verdict, verdict)


def _abstract(question: str, supported: list[Claim],
              refuted: list[Claim], unresolved: list[Claim],
              open_obligations: list, profile: TaskProfile | None = None) -> str:
    """中文摘要：问题 → 方法 → 结论 → 边界。只组装, 不新造论断。

    P0-3 现场缺陷 (抽取前): 方法句**无条件**写"把问题形式化为可机检的计数/组合结构，
    逐条核验经典必要条件", 结尾**无条件**写"本文结论为纯数学判定，不含实验或仿真数据"。
    对机理/案例/数据类研究这两句都是假陈述 —— 现在由任务画像决定措辞, 且只在确实
    纯形式化时才写最后那句。
    """
    parts: list[str] = []
    head = _first_sentence(question, 160)
    if head:
        parts.append(f"本文研究的问题：{head}。")
    if profile is not None and profile.allows_design_vocabulary():
        parts.append("方法：把问题形式化为可机检的计数/组合结构，逐条核验经典必要条件"
                     "（计数恒等式、整除条件与具名定理的适用前提）；所有判定均由受限工具"
                     "重算并由规则层验收，判定链与证书随文给出。")
    elif profile is not None and not profile.allows_formal_vocabulary():
        parts.append("方法：先明确研究对象、依据来源与适用条件，再逐条给出可定位的依据；"
                     "结论强度与未决部分如实标注，不以叙述代替依据。")
    else:
        parts.append("方法：先形式化研究对象、量词与约束，再逐条核验所依据的条件；"
                     "所有判定均由受限工具重算并由规则层验收，判定链与证书随文给出。")
    if supported:
        parts.append("主要结论：" + "；".join(
            _claim_statement(c)[:120] for c in supported[:2]) + "。")
    if refuted:
        parts.append("同时以严格反例否定了：" + "；".join(
            _claim_statement(c)[:80] for c in refuted[:2]) + "。")
    parts.append(f"共 {len(supported)} 条命题在声明条件下成立，{len(refuted)} 条被反例否定，"
                 f"{len(unresolved)} 条未决。")
    if open_obligations:
        parts.append(f"仍有 {len(open_obligations)} 项义务未关闭，"
                     "相应表述在正文中标为条件性结论。")
    # 只在**纯形式化**研究里才允许说"不含实验或仿真数据"; 否则这句话是假的
    if profile is None or profile.is_purely_formal():
        parts.append("本文结论为纯数学判定，不含实验或仿真数据。")
    elif profile.has_empirical_scope:
        parts.append("涉及经验/数据部分的结论仅为**方案与依据整理**，"
                     "未执行的实验或仿真不构成证据。")
    return "".join(parts)


def _keywords(snapshot: ResearchSnapshot, supported: list[Claim],
              profile: TaskProfile | None = None) -> str:
    """关键词: 只用可出版的概念词, 且**按任务画像**决定领域词。

    **不写内部工具名** (如 `design_necessity`): 那是系统实现细节, 出现在期刊关键词里
    是明显的表错。概念词从验证证书的判定链里识别 (如"射影平面""Bruck–Ryser–Chowla")。

    P0-3 现场缺陷: 抽取前这里**无条件**追加 `"组合设计存在性"`、`"必要条件核验"` ——
    于是一篇关于信道可分性的报告在关键词里声称自己是组合设计研究。现在设计论词只在
    `profile.allows_design_vocabulary()` (快照里真的有声明的设计参数) 时出现。
    """
    words: list[str] = []
    # 领域词闸门: 设计论词只在**确认存在设计参数**时出现。
    # 抽取前 `2-(v,k,λ)设计`、`射影平面`、`Bruck–Ryser–Chowla 定理`、`组合设计存在性`
    # 这四类词都可能出现在与设计无关的稿件里 (前两类按快照猜, 后两类无条件追加)。
    design_ok = (profile.allows_design_vocabulary() if profile is not None
                 else bool(_design_params(snapshot)))
    if design_ok:
        for claim in snapshot.claims:
            if claim.design_v and claim.design_k and claim.design_lambda:
                words.append(
                    f"2-({claim.design_v},{claim.design_k},{claim.design_lambda})设计")
                break
        cert_text = " ".join(str(r.certificate or "") + str(r.raw_output or "")
                             for r in snapshot.verifications)
        if "射影平面" in cert_text:
            words.append("射影平面")
        if "Bruck" in cert_text or "BRC" in cert_text:
            words.append("Bruck–Ryser–Chowla 定理")
        words.extend(["组合设计存在性", "必要条件核验"])
    # 方法类词按**形式化范围**给: 经验类研究写"机器可复核判定链"同样是表错
    if profile is None or profile.allows_formal_vocabulary():
        words.append("机器可复核判定链")
    unique: list[str] = []
    for word in words:
        if word and word not in unique:
            unique.append(word)
    return "关键词：" + "；".join(unique[:6])


def _english_title(supported: list[Claim], refuted: list[Claim]) -> str:
    for claim in supported + refuted:
        if claim.design_v and claim.design_k and claim.design_lambda:
            verdict = {"nonexistent": "Nonexistence", "exists": "Existence"}.get(
                claim.design_verdict, "Feasibility")
            return (f"Title: {verdict} of a 2-({claim.design_v},{claim.design_k},"
                    f"{claim.design_lambda}) Design")
    return "Title: A Machine-Checkable Theoretical Study"


def _english_abstract(supported: list[Claim], refuted: list[Claim],
                      unresolved: list[Claim],
                      profile: TaskProfile | None = None) -> str:
    """英文摘要: 与中文摘要同一份事实, 措辞按任务画像选。

    抽取前这里**无条件**写 "counting/combinatorial structure" 与 "classical necessary
    conditions" —— 非设计类研究因此得到一段与正文不符的英文摘要。
    """
    bits: list[str] = []
    if profile is not None and profile.allows_design_vocabulary():
        bits.append("Abstract: We formalize the stated problem as a machine-checkable "
                    "counting/combinatorial structure and verify the classical necessary "
                    "conditions one by one, including named theorems and their hypotheses.")
    elif profile is not None and not profile.allows_formal_vocabulary():
        bits.append("Abstract: We state the research question, make the sources and the "
                    "applicable conditions explicit, and report each conclusion together "
                    "with its locatable basis; unresolved parts are stated as such.")
    else:
        bits.append("Abstract: We formalize the objects, quantifiers and constraints of "
                    "the stated problem and check the conditions each conclusion relies "
                    "on; all checks are recomputed by restricted tools.")
    if supported:
        bits.append("Main result: the stated design does not exist under the declared "
                    "parameters." if any(c.design_verdict == "nonexistent" for c in supported)
                    else "Main result: " + "; ".join(_claim_statement(c)[:140]
                                                     for c in supported[:2]) + ".")
    if refuted:
        bits.append(f"{len(refuted)} stated proposition(s) are refuted by explicit "
                    "counterexamples satisfying the declared hypotheses.")
    bits.append(f"{len(supported)} proposition(s) are supported, {len(refuted)} refuted, "
                f"{len(unresolved)} unresolved. All conclusions are pure-mathematical; "
                "no experimental data are involved.")
    return " ".join(bits)


def _english_keywords(supported: list[Claim]) -> str:
    words = ["combinatorial design existence", "necessary conditions",
             "machine-checkable certificate"]
    for claim in supported:
        if claim.design_v:
            words.insert(0, f"2-({claim.design_v},{claim.design_k},{claim.design_lambda}) design")
            break
    return "; ".join(words[:5])


def _introduction(question: str,
                  references: refmod.ReferenceList,
                  coverage: RetrievalCoverage,
                  snapshot: ResearchSnapshot | None = None,
                  profile: TaskProfile | None = None) -> str:
    """引言: 研究背景 → 本文工作 → 主要结论 → 全文组织。

    **不再复述题面**: 题面属于"问题与形式化"一节; 引言要说明"为什么做、做了什么、
    结论是什么、后文怎么安排"。此前引言只有一段题面引文, 与第 2 节重复, 读者也看不出
    本文的贡献 (现场自查: "第 1 节没有引言")。
    """
    parts: list[str] = []
    design = _design_params(snapshot)
    # P0-3: 引言必须按任务画像写。抽取前这里**无条件**以"组合设计的存在性判定是组合
    # 数学中的经典问题…"开头, 并在无设计参数时仍声称"本文采用必要条件路线" ——
    # 通用入口因此输出了组合设计专用内容。
    if design and (profile is None or profile.allows_design_vocabulary()):
        parts.append(
            "组合设计的存在性判定是组合数学中的经典问题：计数关系自洽并不蕴含设计存在，"
            "真正起决定作用的是若干经典必要条件（计数恒等式、Fisher 不等式、"
            "Bruck–Ryser–Chowla 定理（Bruck–Ryser–Chowla，1949/1950）等）。对给定参数而言，"
            "逐条核验这些条件并给出可复核的判定链，比穷举关联矩阵更可靠，也更适合机器验证。")
    elif profile is not None and not profile.allows_formal_vocabulary():
        parts.append(
            "本文的研究问题需要把结论建立在**可定位的依据**之上：所依据的来源、"
            "假设与判定过程都随文给出，未决部分如实保留，不以结论语气代替依据。")
    else:
        parts.append(
            "本文的研究问题需要把结论建立在**可复核的判定**之上：先形式化问题的对象、"
            "域与约束，再逐条核验所依据的条件；所有判定均由受限工具重算并由规则层验收。")
    if design:
        v, k, lam = design
        parts.append(
            f"本文研究参数为 $v={v}$、$k={k}$、$\\lambda={lam}$ 的 2-设计的存在性。"
            f"该规模下穷举不可行（关联矩阵为 ${v}\\times{v}$），因此本文采用必要条件路线："
            "先形式化计数约束，再逐条核验必要条件，所有判定均由受限工具重算并由规则层验收。")
    elif question:
        parts.append("本文研究的原始问题陈述如下；形式化与适用条件见下一节。")
    if question:
        # 题面在**引言**里出现一次 (论文需要自带问题陈述); 第 2 节只做形式化, 不重复
        quoted = "\n".join("> " + line.strip()
                           for line in question.splitlines() if line.strip())
        parts.append("本文研究的原始问题陈述如下：\n\n" + quoted)
    summary = _conclusion_sentence(snapshot)
    if summary:
        parts.append(summary)
    parts.append(
        "本文其余部分组织如下：第 2 节给出问题的形式化、假设与符号约定；第 3 节给出"
        "主要结果与判定链；第 4 节比较已有工作；第 5 节讨论适用范围与局限；"
        "第 6 节总结全文。完整的判定对象、逐条核验与证书指纹见附录 A。")
    parts.append(coverage_sentence(coverage))
    if references.references:
        parts.append(f"本文引用的已核文献共 {len(references.references)} 条，"
                     "其支持关系在检索阶段逐条判定（见参考文献与检索覆盖记录）。")
    return "\n\n".join(part for part in parts if part)


def _design_params(snapshot: ResearchSnapshot | None) -> tuple | None:
    """(v, k, λ) —— 仅取冻结快照里显式声明的参数。"""
    if snapshot is None:
        return None
    for claim in snapshot.claims:
        if claim.design_v and claim.design_k and claim.design_lambda:
            return claim.design_v, claim.design_k, claim.design_lambda
    return None


def _deliverables_from_spec(spec: ResearchSpec | None) -> list[str]:
    """规格里声明的交付形态 (供没有 `ResearchBrief` 的旧调用点推断画像)。

    规格 (`ResearchSpec`) 不直接带交付形态, 因此按"有没有契约/成功条件"保守推断:
    有明确契约的当理论结论, 否则当问题报告。**不猜领域** —— 领域由设计参数是否存在决定。
    """
    if spec is None:
        return ["problem_report"]
    contract = getattr(spec, "contract", None)
    if contract is not None and getattr(contract, "success_criteria", None):
        return ["theoretical_conclusion"]
    return ["problem_report"]


def _conclusion_sentence(snapshot: ResearchSnapshot | None) -> str:
    """由结论状态生成的"本文结论"一句 (只做事实陈述, 不新造论断)。"""
    if snapshot is None or not snapshot.claims:
        return ""
    supported = [c for c in snapshot.claims if c.status == ClaimStatus.supported]
    refuted = [c for c in snapshot.claims if c.status == ClaimStatus.refuted]
    if supported and any(c.design_verdict == "nonexistent" for c in supported):
        return ("本文的主要结论是：该参数的设计**不存在**，判定依据是一条被违反的经典"
                "必要条件；判定链的每一步都可回到冻结快照的验证记录。")
    if supported:
        return (f"本文给出 {len(supported)} 条在声明条件下成立的命题，"
                "其判定链与验证记录随文给出。")
    if refuted:
        return f"本文以严格反例否定了 {len(refuted)} 条命题。"
    return ""


def _problem_text(spec: ResearchSpec | None, topic: str) -> str:
    """题面: 优先规格的明确问题陈述, 退回方向/原始请求/主题。

    规范化是必须的: 用户粘贴的是 Markdown (带 `# 标题`、`- 列表项`、`$公式$`),
    直接塞进摘要/引言会读成"本文研究以下问题：# Problem 2：…… - 每轮恰好选择……"。
    这里只做**排版级**规范化 (去标记、压空白), 不改动任何语义或数字。
    """
    sources = [getattr(spec, "problem_statement", ""), getattr(spec, "direction", ""),
               getattr(spec, "original_request", ""), topic]
    for source in sources:
        text = _normalize_question(str(source or ""))
        if text:
            return text
    return "（未记录题面）"


# 题面里常见的 LaTeX 数学命令 -> Unicode: 排版级规范化, 不改变数学含义。
# 必要性来自实测: 用户题面里的 `$211\times211$` 去掉 `$` 后剩下裸 `\times`,
# xelatex 会报 "Missing $ inserted" 并整篇不输出页面。
_MATH_COMMAND_MAP = {
    r"\times": "×", r"\cdot": "·", r"\leq": "≤", r"\le": "≤", r"\geq": "≥",
    r"\ge": "≥", r"\neq": "≠", r"\ne": "≠", r"\approx": "≈", r"\infty": "∞",
    r"\pm": "±", r"\in": "∈", r"\subset": "⊂", r"\cup": "∪", r"\cap": "∩",
    r"\lambda": "λ", r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\theta": "θ", r"\mu": "μ", r"\sigma": "σ", r"\pi": "π",
}


def _latex_math_to_unicode(line: str) -> str:
    for command, replacement in _MATH_COMMAND_MAP.items():
        line = line.replace(command, replacement)
    return line


def _normalize_question(text: str) -> str:
    """按**段落**保留换行: 题面常是"标题 + 正文", 压成一行会读成
    "211 个实验节点的无重复配对计划某大型实验有 211 个节点"。

    另外去掉**外部题号** (如 `Problem 2：`): 它是采集来源的编号, 写进论文会让读者
    以为本文是某题集的一部分 (现场自查发现"问题陈述：Problem 2：…"语气像采集脚本)。
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)          # Markdown 标题标记
        line = re.sub(r"^[-*+]\s+", "", line)            # 列表项标记
        line = re.sub(r"^\d+[.、)]\s*", "", line)        # 有序列表标记
        line = _strip_external_numbering(line)
        line = line.replace("$", "")                     # 行内公式定界符 (内容保留)
        line = _latex_math_to_unicode(line)              # 裸数学命令 -> Unicode
        current.append(re.sub(r"\s+", " ", line).strip())
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(item for item in paragraphs if item)


def _strip_external_numbering(line: str) -> str:
    """去掉"Problem 2："这类外部题号 (只影响题号本身, 不动题面内容)。"""
    return re.sub(r"^\s*(?:Problem|问题|题目|Task|Exercise)\s*\d+\s*[:：、.]?\s*",
                  "", line, flags=re.IGNORECASE)


def _first_sentence(text: str, limit: int) -> str:
    """取题面的第一个句子用于摘要 (摘要不铺陈整段题面, 但不得改变其含义)。

    两个必须处理的细节:
    - 中文标题常以全角冒号分隔 ("Problem 2：211 个实验节点的无重复配对计划"),
      句末判定必须包含 `：` 与换行, 否则"第一句"会跨过标题一直吃到正文;
    - 取到的是**完整句**, 摘要里再拼"。"会出现"…计划 2 . 方法："这种中间带句号的
      断裂 (实测 PDF), 因此这里交回**不带句末标点**的片段。
    """
    clean = re.sub(r"[ \t\u3000]+", " ", (text or "")).strip()
    if not clean:
        return ""
    match = re.search(r"[。；;!?！？：:\n]", clean)
    head = clean[:match.end()] if match else clean
    head = re.sub(r"\s+", " ", head).strip()
    if len(head) > limit:
        head = head[:limit].rstrip("。；;!?：:") + "……"
    return head.rstrip("。；;!?：:。！？")


def _symbol_table(snapshot: ResearchSnapshot) -> str:
    claim = next((c for c in snapshot.claims if c.design_v), None)
    if claim is None:
        return "符号说明：见各命题中的变量声明。"
    rows = [("v", claim.design_v, "实验节点（区组）总数"),
            ("k", claim.design_k, "每轮联合测试的节点数"),
            ("λ", claim.design_lambda, "任意两节点恰好共同参加的次数"),
            ("b", claim.design_b, "轮数（区组数）"),
            ("r", claim.design_r, "每个节点参加的次数")]
    lines = ["| 符号 | 取值 | 含义 |", "|---|---|---|"]
    lines.extend(f"| {name} | {value} | {meaning} |"
                 for name, value, meaning in rows if value is not None)
    return "\n".join(lines)


def _claim_kind(claim: Claim) -> str:
    return "theorem" if claim.design_verdict == "nonexistent" else "proposition"


def _claim_statement(claim: Claim) -> str:
    """结论陈述**原文照搬**冻结快照 (方案 v2 §3 防火墙第 1 条)。"""
    return claim.statement


def _proof_text(claim: Claim, snapshot: ResearchSnapshot) -> str:
    from src.agents.theory_writer import _proof_or_plan

    return _proof_or_plan(claim, snapshot)


def _certificate_of_claim(snapshot: ResearchSnapshot, claim: Claim) -> dict:
    """取该结论的设计判定证书 (没有则空 dict)。

    有了它, 证明正文就能排成公式与表格; 没有它 (符号推导、反例等) 仍走文字证明块。
    """
    from src.research.design_feasibility import certificate_of

    for record in snapshot.verifications:
        if record.claim_id == claim.id and not record.stale:
            certificate = certificate_of(record)
            if certificate:
                return certificate
    return {}


def _claim_footer(claim: Claim) -> str:
    support = _SUPPORT_CN.get(claim.support_kind.value, claim.support_kind.value)
    domains = claim.variable_domains or {}
    condition = ("，适用条件 " + ", ".join(f"{k}∈{v}" for k, v in domains.items())
                 if domains else "")
    return (f"（结论 {claim.id}；支持方式 {support}；验证等级 {claim.assurance.value}；"
            f"覆盖范围 {claim.coverage.value}{condition}）")


def _counterexample(snapshot: ResearchSnapshot, claim: Claim) -> str:
    for record in snapshot.verifications:
        if record.claim_id == claim.id and record.counterexample:
            return record.counterexample
    return ""


def _related_work(snapshot: ResearchSnapshot, references: refmod.ReferenceList,
                  coverage: RetrievalCoverage,
                  missing: list[str],
                  related_evidence: list | None = None) -> list[Block]:
    """第 4 节：逐条给出"相同/包含/条件更强或更弱/无法比较"的判断。

    证据来源有两处, 都必须用:
    - 冻结快照 `snapshot.evidence` (研究循环召回并判定过);
    - **出版层检索结果** (`related_evidence`) —— 它不在快照里, 只用快照会出现
      "参考文献表有 N 条、本节却写'未发现可比较的工作'" (实测缺陷)。
    """
    blocks: list[Block] = []
    if not references.references:
        blocks.append(Block("prose", _no_reference_reason(coverage)))
        for record in snapshot.novelty or []:
            blocks.append(Block(
                "prose",
                f"- 结论 {record.claim_id} 的新颖性状态 {record.status.value}："
                f"{record.conclusion or record.bounded_statement}；检索日期 "
                f"{record.search_date or '-'}；覆盖源 "
                f"{'、'.join(record.covered_sources) or '-'}"))
        return blocks
    by_claim: dict[str, list] = {}
    for item in list(snapshot.evidence) + list(related_evidence or []):
        by_claim.setdefault(item.claim_id or "", []).append(item)
    for claim in snapshot.claims:
        if claim.status not in (ClaimStatus.supported, ClaimStatus.refuted):
            continue
        blocks.append(Block("prose", f"结论 {claim.id}：「{claim.statement[:80]}」"))
        items = by_claim.get(claim.id) or []
        if not items:
            blocks.append(Block("prose", "- 在所检索范围内未发现可比较的工作。"))
            continue
        for item in items:
            key = _reference_key_for(item, references)
            # 引用**用占位符**而不是预先算好的 `[n]`: 两种渲染器各自解析 ——
            # Markdown 出 `[n]`, LaTeX 出 `\upcite{refN}`。这里若直接写 `[1]`,
            # LaTeX 会把它当可选参数吃掉 (实测 PDF 里参考文献条目丢了序号)。
            citation = refmod.citation_marker(key) if key else ""
            blocks.append(Block(
                "prose",
                f"- {citation} {item.title or item.literature_id}：关系判定 "
                f"{_relation_cn(getattr(item.support, 'value', ''))}；依据 "
                f"{item.support_reason or '未记录'}；定位 {item.location or '未定位'}；"
                f"可信度 {item.credibility.value}"))
    return blocks


def _reference_key_for(evidence, references: refmod.ReferenceList) -> str:
    target = getattr(evidence, "id", "")
    for reference in references.references:
        if reference.source_id and reference.source_id == target:
            return reference.key
    return ""


def _relation_cn(value: str) -> str:
    return {"supports": "支持（不构成替代证明）",
            "partially_supports": "部分支持",
            "background": "背景（不含本文结论）",
            "refutes": "与本文结论冲突（须复核）",
            "insufficient": "无法比较"}.get(value, "无法比较")


def _no_reference_reason(coverage: RetrievalCoverage) -> str:
    if not coverage.executed:
        return ("本次运行未执行外部文献检索，参考文献表为空。按本项目规则，"
                "未检索不等于“无先例”，本文因此不宣称原创性，"
                "相关工作比较留待接入检索后补充。")
    return ("已执行检索，但在覆盖来源内未发现可引用的等价工作；参考文献表为空，"
            "具体覆盖范围与检索式见检索覆盖记录。")


def _discussion(supported: list[Claim], unresolved: list[Claim],
                open_obligations: list) -> str:
    lines: list[str] = []
    if supported:
        lines.append("本文结论的适用范围由第 2 节的变量域与量词声明限定，结论只在声明条件下"
                     "成立；判定链中的每一步都可回到冻结快照的验证记录。")
    if unresolved:
        lines.append("未决结论：" + "；".join(
            f"{c.id}（{c.status.value}）{c.statement[:80]}" for c in unresolved[:6]) + "。")
    if open_obligations:
        lines.append("未关闭义务：" + "；".join(
            f"{o.id}（{o.status.value}）{o.statement[:60]}" for o in open_obligations[:6])
            + "。")
    if not unresolved and not open_obligations:
        lines.append("本次运行没有遗留的未决结论或未关闭义务。")
    lines.append("局限：本文不进行实验或仿真验证；文献覆盖范围以检索覆盖记录为准，"
                 "未覆盖范围内是否存在等价结果未知。")
    return "".join(lines)


def _conclusion(supported: list[Claim], refuted: list[Claim],
                unresolved: list[Claim]) -> str:
    return (f"本文共给出 {len(supported)} 条在声明条件下成立的命题、{len(refuted)} 条被严格"
            f"反例否定的命题、{len(unresolved)} 条未决命题。所有命题的判定链、证书与验证"
            "等级见附录 A。")


def _verification_line(record) -> str:
    from src.research.design_feasibility import certificate_of, render_certificate_chain

    detail = (f"- [{record.tool}] 命题 {record.claim_id}@v{record.claim_version}；验证状态 "
              f"{record.validation_status.value}；覆盖范围 {record.scope.value}；证书 "
              f"{record.certificate or '-'}")
    certificate = certificate_of(record)
    if certificate:
        detail += "\n\n" + render_certificate_chain(certificate)
    return detail


__all__ = ["AFFILIATION_PLACEHOLDER", "AUTHOR_PLACEHOLDER", "CLC_PLACEHOLDER",
           "CORRESPONDING_PLACEHOLDER", "build_publication_paper", "coverage_sentence"]








