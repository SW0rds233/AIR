# 论文写作方法与图表生成最佳实践（调研汇总）

本文档汇总了 GitHub 高星项目中的论文写作方法和图表生成方案，
供后续改进 AIR / CrewAI_SurveyTeam 时参考。

---

## 一、图表生成方法（重点）

### 1. PaperBanana (2.2k⭐) — 学术图表生成的 SOTA 方案
**项目**: https://github.com/llmsresearch/paperbanana

**核心思路**: 7-agent 流水线，无需人工干预生成发表级图表

```
Phase 0 输入优化: Context Enricher(整理方法文本) + Caption Sharpener(润色图题)
Phase 1 线性规划: Retriever(检索13个参考样例) → Planner(生成图描述) → Stylist(美学约束)
Phase 2 迭代精炼: Visualizer(渲染成图) → Critic(VLM评估) → 循环至满意
```

**可借鉴点**:
- **Stylist 用 NeurIPS 风格指南**（色板/布局/排版）约束生成 → 已借鉴为 `figure_style.py`
- **Critic 迭代循环**: VLM 评估成图并给出修改意见 → 我们已实现代码执行+错误修正循环（进一步可加 VLM 审图）
- **`paperbanana plot` 模式**: VLM 生成 matplotlib 代码而非图像生成模型 → **已实现** (`figure_llm.py`)
- venue 风格包 (neurips/icml/acl/ieee) → 可扩展
- `orchestrate`: 从全文自动规划整套图 → 可扩展

### 2. FigMirror (497⭐) — 参考图驱动的绘图
**项目**: https://github.com/VILA-Lab/FigMirror

**核心思路**: 以某篇论文的图为风格目标，Drawer-Reviewer 迭代直至风格一致

- **Drawer**: 生成 matplotlib 代码 + Grounded Measurement(像素级校验)
- **Reviewer**: 远景合成图 + 全分辨率对比，返回带边界框的结构化反馈
- **Aesthetic Lib**: 美学原则兜底

**可借鉴点**:
- 提供参考图作为风格目标（用户可指定目标论文的图）→ 后续可加
- 139 图参考画廊 → 可作参考样例库

### 3. tueplots (752⭐) — 期刊级 matplotlib 配置
**项目**: https://github.com/pnkraemer/tueplots

**核心思路**: 一行代码切换期刊风格配置（图宽/字体/字号）
- `tueplots.constants.beamer_moml` / `neurips` 等预设
- **已借鉴** 部分思路到 `figure_style.py`（单栏3.5in/双栏7in/300dpi）

### 4. 其它可视化库（如需要 3D/复杂图）
- pyvista (3.8k⭐): 3D 可视化
- pyqtgraph (4.4k⭐): 高性能科学绘图
- sane_tikz (429⭐): LLM 生成 TikZ 图（LaTeX 出版级）

---

## 二、论文写作方法（跨项目最佳实践）

### 1. STORM (30.8k⭐) — 两阶段写作
**项目**: https://github.com/stanford-oval/storm

**核心**: 预写阶段(收集引用+生成大纲) → 写作阶段(填充正文)
- **多视角提问**: 从不同角度问问题引导检索（视角引导提问）
- **模拟对话**: 模拟"作家-领域专家"对话来深化理解
- **已借鉴**: 大纲生成节点 (outline_generator.py)

### 2. PaperOrchestra (624⭐) — 5-agent 流水线
**项目**: https://github.com/Ar9av/PaperOrchestra
(基于 Google 论文 arXiv:2604.05018)

**核心**: Outline → Plotting → Literature Review → Section Writing → Content Refinement
- **并行**: plotting 与 literature review 并行执行
- **halt rules**: 内容精炼有严格的接受/回退规则
- **已借鉴**: 大纲先行思路

### 3. academic-research-skills (40.7k⭐) — 科研技能包
**项目**: https://github.com/Imbad0202/academic-research-skills

**核心**: 13-agent 深度研究 + 12-agent 论文写作 + 7-agent 审阅
- **integrity gates**: 强制完整性检查点（引用真实性）
- **Socratic 对话**: 引导式需求澄清
- **Style Calibration**: 学习用户写作风格
- **已借鉴**: 引文验证门控、跨模型审阅

### 4. OpenDraft (359⭐) — 19-agent 写作流水线
**项目**: https://github.com/federicodeponte/opendraft

**核心**: 研究→结构→写作→引文→润色→导出
- **引文先验证后入参考文献** → 已借鉴
- PDF/DOCX/LaTeX 多格式导出 → 可扩展

### 5. AI-Scientist (SakanaAI) / FAROS (3.1k⭐)
- AI-Scientist: 全自动研究（idea→实验→论文→评审）
- FAROS: blueprint 驱动的工作流运行时
- **可借鉴**: 论文评审模拟（已实现）、实验报告生成

### 6. ARIS (14.2k⭐) — 跨模型审阅
**项目**: https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep

**核心**: 主模型写作 + 另一模型审阅（避免认知盲区）
- **已借鉴**: REVIEWER_CONFIG 跨模型审阅
- 完整性取证 (integrity forensics): 检测造假模式 → 可扩展

---

## 三、当前项目已实现 vs 可进一步扩展

### 已实现
| 功能 | 借鉴来源 |
|------|---------|
| 大纲生成 (outline_generation 节点) | STORM / PaperOrchestra |
| 引文预验证 (citation_precheck) | OpenDraft / academic-research-skills |
| 引文核查 + 证据账本 | OpenDraft / research-proof |
| 跨模型审阅 | ARIS |
| LLM 图表代码生成 + 错误修正循环 | PaperBanana plot 模式 |
| 学术风格指南 (figure_style.py) | PaperBanana Stylist / tueplots |
| 格式校验 (表格/图表编号/引用) | 自研 |
| 成本追踪 | deer-flow / ARIS |

### 可进一步扩展（按优先级）
| 扩展 | 来源 | 说明 |
|------|------|------|
| VLM 审图循环 | PaperBanana Critic | 需要多模态模型 API，对成图二次评审 |
| 参考图风格模仿 | FigMirror | 用户提供参考图作风格目标 |
| LaTeX/PDF 导出 | OpenDraft | 论文定稿格式 |
| 多候选图生成+择优 | PaperBanana num-candidates | 生成 N 张取最佳 |
| Socratic 引导对话 | academic-research-skills | 写作前澄清需求 |
| 写作风格学习 | academic-research-skills | 学习用户已有论文风格 |
