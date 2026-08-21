# AIR — AIResearch

基于 **LangGraph** 多智能体 + **RAG (ChromaDB)** 的学术综述论文自动撰写系统。

借鉴 [AI-Researcher](https://github.com/RyannDaGreat/AI-Researcher)、[OpenAI4S](https://github.com/ranpox/OpenAI4S)、[gpt-researcher](https://github.com/assafelovic/gpt-researcher) 等项目，构建**撰写 → 审稿 → 修改**的完整闭环流水线。

## 功能特性

- **文献查阅**：多源检索（arXiv + Semantic Scholar + OpenAlex），LLM 筛选与综述
- **引用预验证（STORM）**：写作前逐条验证引用真实性，杜绝幻觉引用
- **论文大纲生成**：结构化大纲保证论文逻辑清晰
- **初稿撰写**：基于文献笔记与原文证据映射，Markdown 格式输出，遵守证据纪律
- **引用守门 + 核查**：写作后交叉验证所有引用编号与 CrossRef/OpenAlex/arXiv 数据库
- **跨模型审阅**：独立 Reviewer 模型评分（10 维 50 分制），避免认知框架盲区
- **修订循环**：审稿 → 构建修订指令 → 重写，自动迭代直到达标
- **LaTeX 渲染**：Markdown → ctexart → xelatex 编译 PDF
- **图表生成**：分类体系树、时间线、趋势图、方法对比图
- **成本追踪**：分阶段 Token 用量与费用统计

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key
```

**必需配置：**
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` — 主模型
- `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` — RAG 向量检索（如 DeepSeek 不支持 embedding，推荐硅基流动 `BAAI/bge-large-zh-v1.5`）

**可选配置：**
- `REVIEWER_MODEL` — 跨模型审阅（推荐开启，避免撰写/审阅共享认知盲区）
- `CHEAP_MODEL` — 廉价模型分层（子查询、相关性打分等轻量任务降本）
- `GROBID_BASE_URL` — PDF 结构化解析
- `LANGFUSE_*` — LLM 调用追踪

### 3. 运行

```bash
python -m src.main "研究主题" --keywords K1 K2 --subtopics S1 S2
```

**示例：**

```bash
python -m src.main "射频指纹识别技术综述" \
  --keywords 射频指纹 RF指纹 设备识别 物理层安全 深度学习 \
  --subtopics 特征提取 分类识别 对抗攻击 轻量化模型
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `topic` | 研究主题（必填） | — |
| `--keywords, -k` | 核心关键词列表 | — |
| `--subtopics, -s` | 子主题列表 | — |
| `--time-range` | 时间范围 | `2019-2026` |
| `--max-revisions` | 最大修改轮次 | `3` |
| `--resume` | 断点续跑 | 否 |
| `--skip-retrieval` | 跳过文献检索/摄入/预验证阶段，复用 `data/pipeline_cache` 的检索产物（大纲、草稿、审稿循环仍重新生成） | 否 |

**快速测试（跳过检索，复用上次检索结果）：**

```bash
python -m src.main "射频指纹识别技术综述" --skip-retrieval
```

首次完整运行会在检索阶段把检索产物（文献综述素材、已验证引用清单）写入 `data/pipeline_cache/{主题}.json`；之后加 `--skip-retrieval` 即可跳过 `literature_review` / `pdf_ingestion` / `citation_precheck` 三个阶段，从 `outline_generation` 开始重新生成大纲、草稿并跑审稿循环，大幅缩短重复测试时间（检索部分不重跑）。缓存不满足条件（引用数不足）时自动回退完整检索。

## 流水线流程

```
start
  → literature_review     (多源检索 + 文献综述)
  → pdf_ingestion         (PDF下载 + 解析 + 向量入库)
  → citation_precheck     (引用预验证)
  → outline_generation    (论文大纲生成)
  → paper_writing         (初稿撰写)
  → citation_guard        (引用编号守门)
  → citation_check        (引文交叉验证)
  → paper_review          (跨模型审稿，50分制)
       │
       ├── [评分<40 或 存在虚构引用] → increment_revision → paper_writing (修订循环)
       │
       └── [评分≥40 且 无虚构引用] → format_check → finalize → latex_render → END
```

## 项目结构

```
AIR/
├── src/
│   ├── main.py              # CLI 入口 (python -m src.main)
│   ├── gui.py               # tkinter 图形界面 (python -m src.gui)
│   ├── config.py            # 全局配置 (模型/超时/断路器/阈值等)
│   ├── agents/              # 智能体
│   │   ├── literature_reviewer.py    # 文献查阅 (多源检索 + 子查询 + 中文补充 + 综述素材)
│   │   ├── paper_writer.py          # 论文撰写/修订 (证据纪律 + 字数硬约束)
│   │   ├── paper_reviewer.py        # 论文审阅 (10维评分 + 严格扣分规则)
│   │   ├── outline_generator.py     # 大纲生成
│   │   ├── citation_checker.py      # 引文核查 (程序化 + LLM 意见)
│   │   ├── citation_prechecker.py   # 引用预验证 (STORM 式, 预印本过滤 + 去重)
│   │   ├── citation_guard.py        # 引用守门 (越界修复 + 语义错配检测)
│   │   └── pdf_ingestor.py          # PDF 下载 + 解析 + 向量入库
│   ├── graph/               # LangGraph 流水线
│   │   ├── pipeline.py      # 图定义/编排/修订循环/收敛检测
│   │   └── state.py         # 状态类型
│   ├── rag/                 # RAG 与格式化
│   │   ├── vector_store.py          # ChromaDB 向量库 (embedding 降级/深度上限)
│   │   ├── figure_generator.py      # 图表生成 (规则版)
│   │   ├── figure_llm.py            # 图表生成 (LLM 代码 + 审阅修正循环)
│   │   ├── figure_style.py          # 图表样式指南
│   │   ├── latex_render.py          # Markdown → LaTeX (标题/层级/图表/引用)
│   │   ├── latex_compiler.py        # xelatex 编译
│   │   ├── reference_formatter.py   # GB/T 7714 引用格式 + 重编号 + 预印本判定
│   │   ├── format_validator.py      # 格式校验 (表格/图表/引用)
│   │   ├── manual_pdfs.py           # 人工导入 PDF (data/manual_pdfs/)
│   │   ├── relevance_filter.py      # 相关性过滤 (规则 + LLM 打分 + 领域信号)
│   │   ├── chunker.py               # 文本分块 (CJK 自适应)
│   │   ├── paper_parser.py          # PDF 解析 (GROBID / PyMuPDF)
│   │   └── subquery_generator.py    # 子查询生成
│   ├── tools/               # 工具
│   │   ├── search_tools.py          # 学术搜索 (arXiv/S2/OpenAlex + 去重)
│   │   ├── citation_verifier.py     # 引用验证 (CrossRef/OpenAlex/arXiv)
│   │   ├── venue_resolver.py        # 出处解析 (CrossRef 卷期页/年份补全)
│   │   ├── pdf_fetcher.py           # PDF 下载 (arXiv/OA + 断路器等待)
│   │   └── chinese_sources.py       # 中文文献 (CNKI/万方)
│   └── utils/               # 工具库
│       ├── cost_tracker.py          # 成本追踪
│       ├── context_budget.py        # 上下文预算控制
│       ├── http_client.py           # HTTP (重试 + 429退避 + 断路器)
│       ├── pipeline_cache.py        # 检索缓存 (--skip-retrieval)
│       ├── console.py               # Windows 终端编码修复
│       └── file_utils.py            # 文件工具
├── tests/                   # 离线测试 (python tests/xxx.py)
├── data/                    # 数据目录 (chroma/ pdfs/ manual_pdfs/ pipeline_cache/)
├── outputs/                 # 输出目录 (草稿/审稿报告/图表/LaTeX)
├── references/              # 参考项目源码 (调研用)
├── pyproject.toml
└── requirements.txt
```

## 核心设计要点

### 引用全生命周期（防幻觉的核心）

```
检索 → citation_precheck(预验证+预印本过滤+去重) → paper_writing(仅引用清单内文献)
     → citation_guard(越界编号自动修复 + 引用-正文语义错配检测)
     → citation_check(CrossRef/OpenAlex/arXiv 交叉验证)
     → renumber(按正文首次出现顺序 1..N 重编号)
     → reference_formatter(GB/T 7714 确定性格式化)
```

关键机制：
- **预印本过滤**：拒绝 arXiv(`10.48550`)/TechRxiv(`10.36227`)/SSRN(`10.2139`)/OSF 等预印本 DOI，保证参考文献全部为正式发表文献。
- **去重**：按 DOI **或**归一化标题去重，避免 arXiv 版与正式版被判为两篇（导致重复编号）。
- **多编号引用**：`[2,7]` 这类引用在重编号/覆盖率检查中逐个展开（此前 `\[(\d+)\]` 正则漏掉了多编号引用，导致"编号与正文不一致"）。
- **早期访问标注**：无卷期的期刊文献统一标注「在线优先出版」，避免渲染成残缺的 `, : 1-1`。
- **CrossRef 补全**：审稿建议补录的论文，用 CrossRef 回填出处/卷/期/页码，并修正年份（如 2010→2012）。

### 撰写-审稿-修订闭环

- **10 维 50 分制审稿**（标题/摘要/引言/相关工作/核心内容/比较/挑战/结论/参考文献/写作质量）。
- **总分按逐项求和**（reviewer 报告的"总分"常凭感觉给、与逐项之和不符，程序按逐项求和为准）。
- **严格扣分规则**：缺失必填章节=1分；预印本=参考文献≤2分；引用错配=Critical。
- **收敛检测**：连续 2 轮评分无提升则提前终止，最终产出回退到历史最优稿。
- **固定量表复审锚点**：后续轮次继承上一轮 10 维评分；无新证据时维度分数必须保持，避免每轮更换评价重点。
- **跨轮问题台账**：每个主要问题使用稳定 ID，Reviewer 必须先报告旧问题的已解决/部分解决/未解决状态，遗漏的开放问题由程序保留。
- **有限修订契约**：每轮最多处理 4 个最高优先级问题并附验收标准，避免 Writer 因长篇自由意见整稿重写。
- **质量向量判优**：历史最优稿按“引用硬门禁 → 总分 → Critical/开放问题数”选择，而非只比较易波动的总分。
- **字数硬约束**：全文 ≤20000 字；超长时带强制删减指令重生成一次。
- **修订=增添+精简+改写**：reviewer "增加X"必须成对给出"精简Y"。

### 检索缓存（--skip-retrieval）

检索产物（文献素材 + 已验证引用）写入 `data/pipeline_cache/{主题}.json`，跳过检索/摄入/预验证，从大纲开始重跑撰写/审稿循环，大幅缩短重复测试时间。

## 当前状态与后续改进

以下条目同时记录本轮已落地的改进和仍需继续解决的问题：

### 1. Writer 内容质量（LLM 能力天花板，非代码 bug）
- **引用-正文语义错配（内容级）**：正文可能把文献归到错误类别、或给出与实际内容不符的描述（如把 IoT 安全通用综述当作 RFF 专门综述对比）。现有 `citation_guard` 的语义错配检测**只覆盖"作者名"匹配**（`Xxx等人`），覆盖不到"内容归属/描述"层面的错配。
- **MECE 分类边界、章节重复、术语一致性**：仅靠提示词约束，收敛有限，需要更强的内容校验或更强的模型。

### 2. 审稿人评分波动（本轮已做结构性改进）
已加入固定 10 维评分锚点、稳定问题 ID 台账、客观质量检查摘要和每轮最多 4 项的修订契约。Reviewer 必须先复核旧问题再新增问题；Writer 只做契约内局部修订；停滞与最优稿选择使用复合质量向量。默认验收阈值也已统一为 `80/100 = 40/50`。这些机制能显著降低“打地鼠”和总分随机波动，但最终内容上限仍受所选模型能力影响，建议用同一缓存样本做多次端到端 A/B 评测。

### 3. 越界引用（writer 幻觉）
writer 每轮可能引用 1 个不存在的编号，靠 `citation_guard` 自动替换/删除兜底，**未从源头根治**（属于 writer 未严格遵守清单编号纪律）。

### 4. 引用扩充的重复/无关建议
审稿人可能**重复建议**已被拒绝的预印本（每轮重新搜索+拒绝，浪费 API 调用），或**幻觉出无关论文**（如医学、微谐振器）。已通过提示词约束"只推荐已发表且直接相关的文献"，但依赖 LLM 遵守，不可靠。

### 5. 中文文献元数据不全
CrossRef 对中文期刊 DOI 覆盖不全，部分中文文献（学位论文、早期期刊）缺 DOI/卷期页，导致 GB/T 7714 著录不完整（如学位论文无 DOI、部分期刊无卷期）。

### 6. 图表占位符与成图不一致（已修复）
`finalize` 会按实际生成的图片列表补齐缺失的 `[图N: 图题]`，并依据 taxonomy/timeline/trend/comparison 类型插入到核心方法、相关工作或比较分析章节，确保每张成图都被正文引用。

### 7. LaTeX 编译环境依赖
需本机安装 xelatex/MiKTeX；首次运行拉取 ctexart 等包可能超时。无 LaTeX 环境时只能产出 `.tex`，无法编译 PDF。

## 许可

MIT License
