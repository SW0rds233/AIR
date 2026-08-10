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
│   ├── main.py              # CLI 入口
│   ├── config.py            # 全局配置
│   ├── agents/              # 智能体
│   │   ├── literature_reviewer.py    # 文献查阅
│   │   ├── paper_writer.py          # 论文撰写
│   │   ├── paper_reviewer.py        # 论文审阅
│   │   ├── outline_generator.py     # 大纲生成
│   │   ├── citation_checker.py      # 引文核查
│   │   ├── citation_prechecker.py   # 引用预验证
│   │   ├── citation_guard.py        # 引用守门
│   │   └── pdf_ingestor.py          # PDF摄入
│   ├── graph/               # LangGraph 流水线
│   │   ├── pipeline.py      # 图定义与编排
│   │   └── state.py         # 状态类型
│   ├── rag/                 # RAG 与格式化
│   │   ├── vector_store.py          # ChromaDB 向量库
│   │   ├── figure_generator.py      # 图表生成
│   │   ├── latex_render.py          # LaTeX 渲染
│   │   ├── latex_compiler.py        # LaTeX 编译
│   │   ├── reference_formatter.py   # GB/T 7714 引用格式
│   │   └── format_validator.py      # 格式校验
│   ├── tools/               # 工具
│   │   ├── search_tools.py          # 学术搜索
│   │   ├── citation_verifier.py     # 引用验证
│   │   └── venue_resolver.py        # 会议/期刊名解析
│   └── utils/               # 工具库
│       ├── cost_tracker.py          # 成本追踪
│       ├── context_budget.py        # 上下文预算控制
│       └── file_utils.py            # 文件工具
├── tests/                   # 测试
├── data/                    # 数据目录
├── outputs/                 # 输出目录
├── pyproject.toml
└── requirements.txt
```

## 许可

MIT License
