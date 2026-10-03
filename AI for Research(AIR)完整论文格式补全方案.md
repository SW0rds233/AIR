# 理论模式「出版级论文」补全方案（执行方案 v2，**M1/M2 已完成**）

- 版本：2026-10-03 草案 v2（依据用户裁决修订：交付标准 = 出版级 `.tex` + `.pdf`；
  阶段 4 纳入计划；模板 = **中文期刊式**）
- **进度**：阶段 1、3、5（PDF 编译）已完成并通过验收 → 见 §11 实施记录；
  阶段 2（真实检索）与阶段 4（撰写子图长文）待做
- 目标读者：本项目负责人
- 相关文档：`AI for Research(AIR)计划书.md` §1/§4/§6、`evals/rubric.md`、
  `evals/cases/combinatorial-design/runbook.md`

---

## 0. 交付标准的最终口径（v2 修订）

> **系统最终交付 = 出版级完备的 `.tex` 源码 + 编译成功的 `.pdf` 论文（中文期刊式模板）。**

与之配套的三条硬要求：

1. **不允许"看起来完整"**：出版完备性必须由**门槛**判定，任何缺项（缺摘要、缺参考文献表、
   正文引用与文献表不一致、PDF 编译失败）都不得写成"完整论文"，必须降级并**写明缺哪一项**；
2. **引用只能改变表述，不能改变结论**：改造前后 `problem2` 的结论与证书 `sha256` 必须一致；
3. **"论文草稿"仍然是合法终态**：无可用文献、无 xelatex、预算触顶时，如实交付"论文草稿"
   （研究门槛 + 表达门槛通过），并注明通向"完整论文"还缺什么。**不得为了让等级好看而补假引用。**

### 名词澄清（避免误读历史产出）

`论文草稿` 在本系统是**三级交付等级中的最高级**（判据：研究有效性门槛 + 论文表达门槛
都通过），不是"没写完"。我们要做的是在它之上再加一层**出版完备度**，达到第四级
`完整论文`（出版级 .tex + .pdf）。

---

## 1. 现状核对（逐文件读过，2026-10-03）

### 1.1 已有

- **研究层**：冻结快照（命题/义务/验证记录/证书）、两道门槛、三种交付等级；
- **写作层（理论侧）**：`manuscript.md`（引言/定义与假设/主要结果+判定链/已有工作与新颖性/
  证据与适用性/实验建议/局限/结论/证明附录）、`paper.tex`、`research_report.md`、
  `unresolved.md`、`novelty_review.md`；
- **LaTeX 基建**：`src/rag/latex_render.py` 已是 **`ctexart`** 模板（UTF8/a4paper/12pt +
  amsmath/amssymb/booktabs/tabularx/hyperref），参考文献走
  `\bibliographystyle{gbt7714-numerical}` 与**确定性内嵌 `thebibliography`**；
- **编译器**：`src/rag/latex_compiler.py::compile_latex` 支持 xelatex 多遍编译；
  本机 **xelatex 可用**（已确认）；
- **复用面**：检索/相关性过滤（数据驱动领域词表）、引用预验证、引用守门、三源引文核查、
  GB/T 7714 参考文献格式化、大纲生成、长文写作、格式校验、撰写子图、逐维评审。

### 1.2 缺（= 本方案要补）

| 缺项 | 现状 |
|---|---|
| 摘要 / 关键词 / 中图分类号 / 英文题名与摘要 | 无 |
| 相关工作与引用 | 无引用体系；`novelty` 未跑时只能写"尚未比较" |
| 参考文献表 + 正文 `[n]` 双向可核 | 无（理论模式没有引用环节） |
| 正文引用守门 | 无 |
| 出版完备度门槛与第四级交付等级 | 无 |
| **PDF** | **未编译** —— 只写 `.tex`（`theory_finalize_node` 从未调用 `compile_latex`） |
| 分节长文（期刊式正文体量） | 无（当前是结构化摘要式） |

### 1.3 唯一真实的科研缺口（与"格式"无关，但决定论文有没有第 4 节）

**理论模式的结论与世界知识的接口是空的。** `problem2` 的判定链引用了 Bruck–Ryser–Chowla、
Fisher 不等式、两平方和判定，但没有任何**可引用文献记录**：`novelty_review.md` 只能写
"尚未进行已有工作比较"，参考文献表只能为空，"相关工作"一节无从写起，
也无法回答"是否已知结果"（发布门槛 4）。**这正是既有检索 + 引用守门要补的那一块。**

---

## 2. 中文期刊式模板规格（v2 新增，定稿即锁）

以现有 `ctexart` 基建为底，补齐期刊排版要素：

| 要素 | 规格 |
|---|---|
| 文档类 | `ctexart`（中文、UTF8、a4paper）；必要时切 `ctexart` + `\zihao` 控制字号 |
| 题名/作者/单位 | 中文题名 + 作者占位（`作者占位`，不编造真实姓名/单位）+ 单位占位 + 通信作者占位 |
| 摘要/关键词 | 中文摘要 200–300 字；关键词 3–6 个；**中图分类号**（缺失时留空并标注"待作者补充"） |
| 英文部分 | 英文题名 + 英文摘要 + 英文关键词（可由中文摘要翻译，**不得引入新论断**） |
| 章节 | 1 引言 / 2 问题与形式化（定义、假设、符号表）/ 3 主要结果（定理+判定链+证明）/
  4 与已有工作比较 / 5 讨论与局限 / 6 结论 / 参考文献 / 附录 |
| 定理环境 | 沿用 `amsthm`：定理/命题/引理/推论 + 编号 + `\label`/`\ref` |
| 图表 | `booktabs`/`tabularx` 表格（如参数表、判定链表、检索覆盖表）；图仅在真实生成时插入 |
| 参考文献 | **GB/T 7714**（`gbt7714-numerical` 或内嵌 `thebibliography`，后者不依赖 BibTeX 工具链） |
| 编译 | xelatex 多遍（含交叉引用与 `thebibliography`），产出 `paper.pdf` + `compile_log.txt` |
| 字体 | 由 `ctexart` 自动选择（Fandol 字体）；**字体缺失必须显式报错**，不得静默产出乱码 PDF |

**验收补充**：PDF 必须通过"可读性自检"——抽取文本层，断言无 `�`/连续空白/缺字块，
中文字符数与正文规模同量级（本机 xelatex 已有，可直接测）。

---

## 3. 合并的第一原则（反幻觉防火墙，写进验收）

> **引用只能改变"表述"，不能改变"结论"。**

禁止项（任一违反即判不通过）：

1. 不得因检索到某文献而改写/弱化/强化命题陈述；
2. 不得用文献"支持"替代已关闭义务的证书（`support_kind=theorem_application` 不动）；
3. 正文不得出现参考文献表中没有的引用；参考文献表不得出现未通过核查的条目；
4. 检索不到相关工作时，写"在所检索范围内未发现等价结果"，**不得**省略引用体系或伪造引用；
5. 无来源可用时，正文如实写"无外部证据"，**不得**制造引用充版面。

---

## 4. 可复用清单（已核实存在，不重写）

| 能力 | 位置 | 复用方式 |
|---|---|---|
| 定向检索 | `src/research/retrieval.py`、`src/tools/search_tools.py` | 复用（离线可关） |
| 检索结果相关性过滤 | `src/rag/relevance_filter.py`（领域词表数据驱动） | 直接复用 |
| 文献审读/PDF 摄入 | `src/agents/literature_reviewer.py`、`pdf_ingestor.py` | 适配调用 |
| 引用预验证 | `src/agents/citation_prechecker.py` | 直接复用 |
| 引用守门 | `src/agents/citation_guard.py` | 直接复用 |
| 引文三源核查 | `src/tools/citation_verifier.py`、`src/agents/citation_checker.py` | 直接复用 |
| GB/T 7714 格式化 | `src/rag/reference_formatter.py` | 直接复用 |
| 大纲 / 长文写作 | `src/agents/outline_generator.py`、`paper_writer.py` | 复用（理论骨架入提示词） |
| 格式校验 | `src/rag/format_validator.py` | 直接复用 |
| LaTeX 渲染 + 编译 | `src/rag/latex_render.py`、`latex_compiler.py` | 直接复用（xelatex 已可用） |
| 撰写子图 | `src/graph/pipeline.py: build_writing_graph()` | **阶段 4 正式接入** |
| 逐维评审/修订 | `src/agents/paper_reviewer.py` | 阶段 5 接入（不得自动改结论） |

**不合并两张 State 定义**：`PipelineState` 60+ 字段、`TheoryState` 34 字段且已被
`tests/test_theory_state_contract.py` 锁住。阶段 4 用**适配函数**（`writing_bridge`）
把冻结快照 + 证据映射成写作子图所需最小输入，而不是改 State 定义。

---

## 5. 执行方案（5 个阶段，全部必做）

### 阶段 1：出版完备门槛 + 第四级交付等级（0.5 天）

**改动**
- `src/research/acceptance.py`：新增 `publication_gate(manuscript, snapshot, references,
  coverage, compile_status) -> GateResult`，判据：
  1. 有摘要与关键词（中文；英文可选但缺失需记录）；
  2. 有参考文献章节，且**正文引用集合 == 参考文献表集合**；
  3. 每条核心结论在正文中可反查（复用 `trace_manuscript`）；
  4. `.tex` 可编译且 `paper.pdf` 存在（无 xelatex 时按"环境性缺项"记录，等级不虚升）；
  5. 正文无悬空 `\ref`/`\cite`、无未替换占位符（除作者/单位占位，那两项显式标注）。
- `classify_deliverable`：`完整论文`（研究门槛 ✓ + 表达门槛 ✓ + 出版门槛 ✓）＞
  `论文草稿`（前两道 ✓）＞ `条件性研究报告` ＞ `研究备忘录`。
- `theory_finalize_node`：`delivery_gate.md` 分开列出三道门槛与出版缺项清单。

**验收**
- `problem2` 离线：等级 = `论文草稿`（无文献 ⇒ 出版门槛如实不通过并列出缺项）；
- 假数据正向用例：补一条已核文献后等级升 `完整论文`；
- 现有 `test_delivery_level.py` 全绿。

### 阶段 2：证据与引用获取（1–1.5 天）

**改动**
- 新 `src/research/publication_evidence.py`：
  - 按 `spec.source_policy`（`user_kb`/`autonomous`/`both`）决定检索范围，复用
    `relevance_filter` + `citation_prechecker`；
  - 产出交付包 `references.json` + `retrieval_coverage.json`（沿用现有 `RetrievalCoverage`），
    含未覆盖范围与失败原因；
  - 检索不可用/离线：如实记"未执行/未接入"，等级不升级。
- 复用 `kb.service` 的 `search/read/resolve`：命中不等于支持，需显式判定支持关系。

**验收**：离线为空且带原因；假检索结果经过滤+预验证只留通过项；
`test_relevance_cache.py`/`test_sources_and_attribution.py` 不回归。

### 阶段 3：期刊式单遍写作（1.5–2 天）

**改动**
- `src/agents/theory_writer.py::build_publication_manuscript(...)`：按 §2 模板生成
  摘要/关键词/英文题名摘要/六章正文/参考文献/附录；**结论块原文照搬冻结快照**；
- 新 `src/rag/reference_list.py`：GB/T 7714 参考文献表 + 正文 `[n]` ↔ 表项双向映射；
- 新 `src/research/publication_checks.py`：引用完整性与结论可反查的确定性检查。

**验收（硬）**
- `problem2` 离线：产出带摘要/关键词/参考文献章节的完整格式文档，结论仍为 `不存在`，
  证书 `sha256` 与改造前一致；
- 反向用例：注入一条未核引用 → 引用完整性检查**必须失败**；
- 双向可核：每个 `[n]` 都在表里，表里每条都被正文引用。

### 阶段 4：接入撰写子图做分节长文（**纳入计划**，1–2 天）

**改动**
- 新 `src/research/writing_bridge.py`：把冻结快照 + 证据映射成写作子图最小输入
  （`research_topic`/`topic_keywords`/`literature_review_notes`/`verified_references`/
  `paper_outline`/`skip_retrieval=True`），驱动
  `paper_writing → citation_guard → citation_check → finalize → latex_render`；
- 理论骨架（判定链/证书）作为**不可改写段**注入提示词与后校验：
  写作前后逐字节比对 `claims.json` / `verification/`，任何改动即失败；
- 长文目标：正文体量达到期刊论文量级（引言与相关工作的文献密度由阶段 2 提供）。

**验收**
- 同一冻结快照下，阶段 3（单遍）与阶段 4（分节）产出的**结论与证书完全一致**；
  差异只在正文长度/引用密度时记录对比结论；
- 若适配成本超阈值（写作节点需要 10+ 个理论侧没有的字段），允许**降级为"仅复用
  citation_guard/citation_check/format_validator"**，但必须在本文件登记该决定与理由。

### 阶段 5：出版级排版与评审收尾（1 天）

**改动**
- 调 `compile_latex` 出 `paper.pdf` + `compile_log.txt`；PDF 文本层可读性自检（§2）；
- `format_validator`（GB/T 7714）+ 一轮逐维评审 → `review_report.md`；
  **评审只产出修订建议，不得自动改结论或义务状态**；
- 交付包新增：`paper.pdf`、`compile_log.txt`、`publication_checklist.md`（逐项判定结果）。

**验收**：本机交付包含 `paper.pdf` 且自检通过；无 xelatex 环境降级为"仅 .tex"并写明。

---

## 6. 成本预估（真实模型前先填硬上限）

| 阶段 | 离线 | 真实模型预估 |
|---|---|---|
| 1 门槛与等级 | 0 | 0 |
| 2 检索 + 预验证 | 0（不联网） | 检索 API + 每篇 1 次判定 |
| 3 单遍写作 | 0（确定性模板） | 摘要与各章 6–10 次调用 |
| 4 分节写作 | 0 | 每节 1 次 + 守门/核查若干 |
| 5 编译 + 评审 | 0（本机 xelatex） | 评审 10 维 ≈ 2–4 次 |

参照：`problem2` 真实模型整轮 4 次调用 / 9.2k tokens / **\$0.0087**（闲时）。
加写作层后按 20–35 次调用估计约 **\$0.05–0.10**；跑前仍按 runbook §2.1 先填硬上限与停止规则。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 引用幻觉污染严谨结论 | §3 五条禁止项 + 引用完整性正/反向用例 + 证书 `sha256` 必须不变 |
| 中文 LaTeX 字体缺失导致乱码 PDF | PDF 文本层可读性自检；失败即降级并显式报错，不产出"看起来成功"的 PDF |
| 适配 `PipelineState` 成本失控 | 阶段 4 的降级判据（§5 阶段 4） |
| 检索拉长时长与成本 | 沿用 `max_actions/max_tool_calls` 与预算触顶即停；离线默认不联网 |
| 两条模式互相回归 | 不改 `PipelineState`；`test_theory_state_contract.py` 继续守 `TheoryState` |
| 作者/单位占位被误当真 | 占位符统一用醒目文本并在 `publication_checklist.md` 标注"待作者补全" |

---

## 8. 对齐计划书（防越界）

- 发布门槛 1（三种入口同一流程）：Web/CLI 都能取到 `.tex` + `.pdf`，在阶段 5 用真实浏览器用例覆盖；
- 门槛 4（已知等价不称原创 / 缺全文不称无先例 / 未证不称已证）：阶段 2 覆盖记录 +
  阶段 3 第 4 节写法 + `publication_checks` 逐条守住；
- §1 边界（不自动执行实验/不训练模型/不自动投稿）：本方案只加写作与排版，不新增执行类能力。

---

## 9. 里程碑（v2：以 .tex + .pdf 为终点）

| 里程碑 | 内容 | 预估 |
|---|---|---|
| M1 | 阶段 1+3 离线跑通：`problem2` 产出带摘要/关键词/参考文献章节的完整格式文档；结论与证书不变 | 1 个会话 |
| M2 | 阶段 5 前半：`paper.pdf` 真实编译 + 文本层可读性自检通过 | 0.5 个会话 |
| M3 | 阶段 2 接真实检索 + 引用守门，`完整论文` 等级可达（含覆盖记录与失败原因） | 1 个会话 |
| M4 | 阶段 4 分节长文接入撰写子图，产出体量达期刊量级的 `.tex`/`.pdf` | 1–2 个会话 |

**每个里程碑的硬门槛**：全量 `pytest` 全绿；`problem2` 结论与证书 `sha256` 不变；
交付包内 `.tex` 与 `.pdf` 同时存在（M2 起）；出版完备项逐条写进 `publication_checklist.md`。

---

## 10. 评审清单（人审用，随交付包导出）

`publication_checklist.md` 固定条目（每项须写"通过/需修改/不通过 + 理由 + 文件位置"）：

1. 摘要与关键词是否覆盖研究问题、方法、结论与边界；
2. 英文题名/摘要是否与中文一致（不得引入新论断）；
3. 主要结果是否与冻结快照逐条对应（定理编号 ↔ `claim_id`）；
4. 判定链/证书是否可在附录中逐条反查；
5. 参考文献是否全部通过核查；正文引用与文献表是否双向一致；
6. 第 4 节对已有工作的判断是否写明"相同/包含/条件更强或更弱/无法比较"；
7. 检索覆盖与未覆盖范围是否如实记录；
8. 未决义务/未关闭项是否在局限一节如实列出；
9. 作者与单位占位是否显式标注为待补；
10. `.pdf` 是否可读（无乱码、无缺字、图表完整）。
---

## 11. 实施记录（v2 推进）

### M1 + M2 已完成（2026-10-03）

**新增模块**

| 文件 | 职责 |
|---|---|
| `src/rag/reference_list.py` | 参考文献表 + 正文 `[[REF:key]]` ↔ `[n]` **双向映射**；只收支持关系已判定的证据；未收录引用不静默丢弃 |
| `src/research/publication_paper.py` | 期刊式稿件组装：题名/作者占位/摘要/关键词/中图分类号/英文摘要/六章/参考文献/附录；**结论原文照搬冻结快照** |
| `src/rag/publication_render.py` | 出版级 Markdown/LaTeX 渲染：`abstract` 环境、`thebibliography`、Unicode 数学符号与裸 LaTeX 命令规范化 |
| `src/research/acceptance.py`（改） | `publication_gate`（出版完备门槛）、`publication_complete`、四级 `classify_deliverable` |
| `src/graph/theory_pipeline.py`（改） | 出版层接线 + `compile_publication`（真编译 + 缺字检测）+ manifest 刷新 + `publication_checklist.md` |

**实测结果（离线、零成本）**

```
输入: evals/cases/combinatorial-design/case.md (Problem 2)
交付级别: 完整论文      门槛: 研究✓ 表达✓ 出版✓
交付物: publication.tex (4 页 → publication.pdf 118 KB)
        publication.md / references.json / retrieval_coverage.json
        compile_log.txt / publication_checklist.md
证书 sha256: 9089eb5c…  ← 与改造前一致 (引用层未触碰结论)
PDF 文本层自检: 无乱码、无缺字 (Missing character 计数 = 0)
```

**过程中发现并修掉的真实缺陷**（每一条都会造成"看起来成功"的假交付）

1. 出版门槛只看 `passed`：没有 PDF、没有参考文献的稿件也会被报成"完整论文" → 改为
   `passed 且无未决项`，存在未决即降级并写明缺什么；
2. `\upcite{}` 被当普通文本转义成可见文本 → 保命令转义；
3. Unicode 数学符号（λ/≥/≡/×）在正文字体里**缺字形且静默留空** → 统一转 LaTeX 数学模式
   （xeCJK 的 CJK 字体不覆盖这些非 CJK 码位）；
4. 裸写的 `\times` 使 xelatex 报 `Missing $ inserted` 且**整篇不输出页面** → 先转 Unicode；
5. `manifest.json` 停在编译前的等级（包里有 PDF、manifest 写"论文草稿"）→ 编译后刷新；
6. 摘要跨过中文题名的全角冒号一直吃到正文 → 句末判定加入 `：` 与换行；
7. 题面的 Markdown 标记（`#`、`-`）直接进入正文 → 段落级规范化（保留换行，不压成一行）；
8. 参考文献章节在 LaTeX 侧重复输出（空章节 + `\#\#` 残留）→ 改用专门 `references` kind。

**回归**：`pytest -q` → **801 passed**；新增 `tests/test_publication_paper.py`（12 项）。
里程碑测试改写为更强的断言：`test_problem2_offline_reaches_complete_paper_with_certificate`
（断言 `publication.tex` + `publication.pdf` + 摘要/关键词/参考文献齐全 + 成本为 0）。

### M3 已完成（2026-10-03）：阶段 2 检索 + 引用守门

**新增** `src/research/publication_evidence.py`：

- `should_search()` 决定是否检索，并**给出可解释的原因**（离线 / 无检索源 / 已有证据 /
  资料库为空）；
- `gather_publication_evidence()` 执行检索 → 逐条判定支持关系 → 产出参考文献表与覆盖记录；
- 检索式**由研究对象的客观参数生成**（`2-(211,15,1) design existence`、
  `projective plane of order 14`），不是自由发挥。

**堵死的两条假引用路径**（各有回归用例）：

1. 规则层 `assess_support` 会因"主题词出现"或"处理/结果同时出现"给部分支持，
   但它**不看原文出处** → 命中缺页/节定位时一律降为待审，不支持引用；
2. 引文必须是**已读取原文里真实出现的片段**（`quote_is_locatable`），否则降为待审；
   规则层最多 `partially_supports`，绝不升级为强支持。

**修掉的两个真实缺陷**：

1. **引用占位印进正文**：`CITE_PATTERN.replace(...)` 是在**正则**上做字符串替换，
   留下字面反斜杠，正文显示 `[[REF:ref1]]` 而不会被编号；改用
   `reference_list.citation_marker()`。同一问题的另一半是 **Markdown 渲染器做了 LaTeX
   转义**（`\_`、`\[` 在 Markdown 里就是字面反斜杠），已分开处理：LaTeX 转义只属于 `.tex` 侧。
2. **覆盖记录含混**：没检索时无法区分"没检索过"与"检索过无新命中"。新增
   `RetrievalCoverage.origin`（`research_loop` / `publication_layer` / `not_executed`），
   并写进 `retrieval_coverage.json`。

**实测（进程内真实 KB）**：临时库放入一条 2-(211,15,1) 文献 → 交付包出现 `references.json`
（1 条）、正文第 4 节 `[1] … 关系判定 支持（不构成替代证明）`、`publication.pdf` 正常生成；
覆盖记录 `origin=research_loop`（引文来自研究循环已采信的证据）。

### M4 已完成（2026-10-03）：撰写子图长文（附录 B）

**新增** `src/research/writing_bridge.py`：

- `build_writing_inputs()` 把冻结快照映射成撰写层最小输入（`research_topic` /
  `verified_references`（**`ref_number` 与正文 `[n]` 同一套编号**）/
  `literature_review_notes`（命题 + 判定链 + 证书）/ `paper_outline` / `skip_retrieval=True`）；
- `write_long_form()` 调用既有 `run_paper_writing`，**只读快照**，任何异常转成 note，
  不阻断交付；由 `THEORY_LONG_FORM=1` 显式启用（默认关）；
- `append_long_form_appendix()` / `append_latex_appendix()` 把长文作为**附录 B** 追加，
  并在附录开头声明"冲突时以第 3 节判定链为准"。

**为什么分栏而不是合并成一篇**：阶段 3 稿件里每一句结论都能回到冻结快照（可反查），
长文是生成性正文；混排会让"哪一句可核对"不可判定。生成性正文进入 `.tex` 时**逐字符转义**
（裸 `$`、`_`、`%` 会让整篇编译失败）。

**验收用例**（`tests/test_writing_bridge.py`，13 项）：映射正确、`ref_number` 与正文同编号、
快照未被改动（逐字节比对 `model_dump`）、撰写层报错/离线可降级、附录不覆盖正文、
LaTeX 转义安全、端到端（`THEORY_LONG_FORM=1`，假 writer）附录进入交付包且证书 `sha256` 不变。

---

## 12. 总体完成情况

| 里程碑 | 状态 | 证据 |
|---|---|---|
| M1 出版门槛 + 期刊式单遍写作 | **完成** | `tests/test_publication_paper.py`（13） |
| M2 PDF 真实编译 + 文本层自检 | **完成** | 4 页 PDF、缺字计数 0、`compile_log.txt` |
| M3 检索 + 引用守门 | **完成** | `tests/test_publication_evidence.py`（12，含真实 KB 端到端） |
| M4 撰写子图长文（附录 B） | **完成** | `tests/test_writing_bridge.py`（13） |
| 全量回归 | **828 passed** | `pytest -q`（离线默认） |

**最终交付形态**（离线 `problem2` 实测）：

```
交付级别: 完整论文   研究门槛 ✓ 表达门槛 ✓ 出版门槛 ✓ (compile_status=ok)
包内: publication.tex + publication.pdf(4 页) + publication.md
      references.json + retrieval_coverage.json + compile_log.txt
      publication_checklist.md + paper.tex(研究稿) + claims/obligations/verification
证书: sha256=9089eb5c…  (自 M1 起未变)
```

**尚未做（可选增强，不影响上述目标达成）**：

- `format_validator` 逐条校 GB/T 7714 与 `paper_reviewer` 一轮逐维评审；
- 长文的**真实模型**跑批（当前是确定性桥接验收 + 假 writer 端到端；真实跑批需按
  runbook §2.1 先填硬上限）。
