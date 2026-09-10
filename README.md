# AIR智能体研究系统

基于 **LangGraph** 多智能体 + **RAG (ChromaDB)** 的学术综述论文自动撰写系统（对话式协作）。

流程：`自然语言计划 → 文献检索 → PDF 摄入 → 引用预验证 → 大纲 → 初稿 → 引用守门/核查 → 跨模型审稿 → 修订循环 → LaTeX 出稿`

## 功能特性

- **对话式协作**：主控 Supervisor 理解自然语言意图，生成任务计划（主题/关键词/子主题 + 任务范围），确认后动态调度工作智能体
- **多源文献检索**：arXiv + Semantic Scholar + OpenAlex（可选 CNKI / 万方），LLM 筛选与综述
- **引用防幻觉全流程**：预验证并过滤预印本 → 写作守门 → CrossRef/OpenAlex/arXiv 交叉验证 → GB/T 7714 格式化
- **撰写-审稿-修订闭环**：跨模型审稿（10 维 50 分制）、跨轮问题台账、有限修订契约、收敛检测与历史最优稿回退
- **LaTeX 渲染**：Markdown → ctexart → xelatex 编译 PDF；自动生成分类树 / 时间线 / 趋势图等
- **Web 界面**：历史会话回看、断点续跑、删除会话、产物按对话隔离（`outputs/{run_id}/`）
- **成本追踪**：分阶段 Token 用量与费用统计

## 快速开始

### 1. 获取代码

```bash
git clone https://github.com/SW0rds233/AIR.git
cd AIR
```

已有仓库时更新：`git pull`

### 2. 一键搭建环境（Windows 推荐）

双击 `setup_env.bat`，或在项目根目录执行：

```bat
setup_env.bat
```

自动完成：检测 Python（≥3.10）→ 创建 `.venv` → 安装依赖 → 生成 `.env` → 验证关键依赖。可重复执行，已存在的 `.venv` / `.env` 会自动跳过。

国内网络下载慢或超时，使用清华 PyPI 镜像：

```bat
setup_env.bat --mirror
```

<details>
<summary>手动搭建（macOS / Linux 或自定义环境）</summary>

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```
</details>

### 3. 配置环境变量（.env）

`.env` 由 `python-dotenv` 自动加载，已被 `.gitignore` 忽略，不会提交到版本库；未配置项使用 `src/config.py` 默认值。完整注释见 `.env.example`。

**必填（主模型）**

| 变量 | 说明 | 示例 |
|------|------|------|
| `OPENAI_API_KEY` | 主模型 API Key | `sk-xxx` |
| `OPENAI_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `OPENAI_MODEL` | 主模型名称 | `deepseek-chat` |

**RAG 向量检索（建议配置）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `EMBEDDING_MODEL` | embedding 模型；DeepSeek 等无 embedding 的服务留空即可（RAG 自动降级，主流程仍可运行）；推荐硅基流动 `BAAI/bge-large-zh-v1.5` | `text-embedding-3-small` |
| `EMBEDDING_BASE_URL` | embedding 接口地址（如 `https://api.siliconflow.cn/v1`） | 回退主模型 |
| `EMBEDDING_API_KEY` | embedding API Key | 回退主模型 |
| `SKIP_EMBEDDING` | `1` = 跳过向量化入库（调试提速，不影响写作/审阅） | `0` |
| `PDF_FULLTEXT_MAX_CHARS` | 单篇论文全文摄入最大字符数 | `20000` |

**跨模型审阅（推荐开启）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `REVIEWER_MODEL` / `REVIEWER_API_KEY` / `REVIEWER_BASE_URL` | 独立审阅模型，避免与撰写模型共享认知盲区；留空复用主模型 | 空 |
| `REVIEWER_TEMPERATURE` | 审阅温度 | `0.1` |

**廉价模型分层（推荐开启）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CHEAP_MODEL` / `CHEAP_BASE_URL` / `CHEAP_API_KEY` | 子查询生成、相关性打分等轻量任务；留空回退主模型 | 空 |

**检索与流程**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ARXIV_SEARCH_MAX_RESULTS` / `SEMANTIC_SCHOLAR_MAX_RESULTS` | 单次检索结果上限 | `50` / `50` |
| `MAX_REVISIONS` | 最大修订轮次 | `3` |
| `REVIEW_ACCEPT_THRESHOLD` | 审稿通过阈值（百分制，`80` 对应 50 分制 `40`） | `80` |
| `STAGNATION_LIMIT` | 连续 N 轮评分无提升则提前终止修订 | `2` |
| `CROSSREF_EMAIL` | CrossRef 礼貌池联系邮箱 | 空 |
| `PDF_DOWNLOAD_LIMIT` | PDF 下载篇数上限（调试可调低至 5~10 提速） | `30` |
| `OPENALEX_API_KEY` | OpenAlex API Key（注册后额度更高，避免 429） | 空 |

**可选数据源与追踪**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CNKI_API_KEY` / `WANFANG_API_KEY` | 中文文献源（CNKI / 万方开放平台） | 空 |
| `GROBID_BASE_URL` | GROBID 结构化 PDF 解析服务；留空回退 PyMuPDF | 空 |
| `LANGFUSE_API_KEY` / `LANGFUSE_HOST` | Langfuse LLM 调用追踪 | 空 / `https://cloud.langfuse.com` |

**网络容错与超时**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `HTTP_CONNECT_TIMEOUT` / `HTTP_READ_TIMEOUT` / `HTTP_WRITE_TIMEOUT` | 连接 / 读取 / 发送超时（秒） | `10` / `30` / `30` |
| `HTTP_MAX_RETRIES` | 最大重试次数（3 = 首次 + 2 次重试） | `3` |
| `HTTP_BACKOFF_BASE` / `HTTP_429_BACKOFF_BASE` | 网络错误 / 429 限流退避基值（秒） | `2` / `4` |
| `HTTP_CIRCUIT_BREAKER_THRESHOLD` / `HTTP_CIRCUIT_BREAKER_COOLDOWN` | 断路器：连续失败 N 次后暂停 M 秒 | `5` / `60` |
| `LLM_TIMEOUT` / `LLM_MAX_RETRIES` | LLM 单次调用超时（秒）/ 重试次数 | `900` / `2` |

### 4. 运行

**命令行：**

```bash
# 直接指定主题/关键词/子主题
python -m src.main "研究主题" --keywords K1 K2 --subtopics S1 S2

# 自然语言入口（建议带上英文术语/缩写，便于英文库检索）
python -m src.main --request "我想研究射频指纹识别(RF fingerprinting, RFFI)的特征提取与对抗攻击"
```

**Web 界面（对话式协作，推荐）：**

```bash
python -m src.server          # 或双击 start.bat
# 浏览器访问 http://127.0.0.1:8000
```

在研究计划、大纲、初稿、每轮审稿后暂停等待确认或提意见；顶部「历史会话」支持回看与断点续跑。命令行版对话入口：`python -m src.main_chat --request "..."`。

## 主要参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `topic` | 研究主题（用 `--request` 时可不填） | — |
| `--request, -r` | 自然语言研究描述，Planner 自动提取主题/关键词/子主题 | — |
| `--keywords, -k` | 核心关键词列表 | — |
| `--subtopics, -s` | 子主题列表 | — |
| `--time-range` | 时间范围 | `2019-2026` |
| `--max-revisions` | 最大修改轮次 | `3` |
| `--resume` | 断点续跑 | 否 |
| `--skip-retrieval` | 跳过检索/摄入/预验证，复用 `data/pipeline_cache` 检索产物 | 否 |

首次完整运行会把检索产物写入 `data/pipeline_cache/{主题}.json`；之后加 `--skip-retrieval` 可跳过前三个阶段，从大纲开始重跑，大幅缩短重复测试时间（缓存不足时自动回退完整检索）。

## 常见问题（FAQ）

**1. 提示「未检测到 Python 3.10 或更高版本」**
安装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选 "Add python.exe to PATH"，重开终端后重试。

**2. pip 安装依赖慢 / 超时失败**
使用 `setup_env.bat --mirror`，或手动执行：
`.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

**3. 启动报 API Key 无效 / 401**
确认 `.env` 位于项目根目录且填写了 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`；`BASE_URL` 通常需以 `/v1` 结尾；修改后需重启程序。

**4. RAG / 向量检索不可用**
DeepSeek 等不提供 embedding API 时 RAG 自动降级（主流程不受影响）。要启用完整 RAG，配置硅基流动：`EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5`、`EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1`、`EMBEDDING_API_KEY=sk-xxx`。

**5. 运行太慢 / 只想调试流程**
`.env` 调低 `PDF_DOWNLOAD_LIMIT`（如 10）、设 `SKIP_EMBEDDING=1`，或加 `--skip-retrieval` 复用已有检索缓存。

**6. 检索返回 429 / 频繁限流**
配置 `OPENALEX_API_KEY`（OpenAlex 免费额度按日计，UTC 午夜重置）；调大 `HTTP_429_BACKOFF_BASE`、`HTTP_CIRCUIT_BREAKER_COOLDOWN`；稍后重试。

**7. LLM 调用超时**
推理模型审稿/写作耗时长，调大 `LLM_TIMEOUT`（默认 900s）；网络波动会自动重试 `LLM_MAX_RETRIES` 次。

**8. 中断后如何继续**
Web 界面顶部「历史会话」→ 未完成会话点「继续」；CLI 加 `--resume`。停在暂停点的会话会重新提示输入。

**9. 没有生成 PDF，只有 .tex**
需本机安装 LaTeX（MiKTeX / TeX Live）并提供 `xelatex`；首次编译拉取 ctexart 等包可能超时，重试或更换网络。

**10. 8000 端口被占用**
换端口启动：`uvicorn src.server:app --host 127.0.0.1 --port 8001`。

**11. 控制台中文乱码**
程序入口已自动修复编码；若仍乱码，先执行 `chcp 65001`，或改用 Windows Terminal 运行。

## 项目结构

```
AIR/
├── src/
│   ├── main.py / main_chat.py / server.py / gui.py   # CLI / 对话 CLI / Web / tkinter 入口
│   ├── config.py            # 全局配置 (.env 加载/默认值)
│   ├── agents/              # 文献查阅、撰写、审阅、大纲、引用预验证/守门/核查、PDF 摄入
│   ├── graph/               # LangGraph 流水线 (Supervisor 编排 / 修订循环)
│   ├── rag/                 # 向量库、图表、LaTeX、引用格式化、相关性过滤
│   ├── tools/               # 学术搜索、引用验证、出处解析、PDF 下载、中文文献源
│   ├── utils/               # HTTP 容错、成本追踪、检索缓存、会话记录等
│   └── web/index.html       # Web 前端 (原生 HTML + JS)
├── tests/                   # 离线测试 (python tests/xxx.py)
├── data/                    # 运行时数据 (chroma/pdfs/conversations/checkpoints/pipeline_cache)
├── outputs/                 # 产物 (按 run_id 隔离)
├── setup_env.bat            # 一键搭建环境
├── start.bat                # 一键启动 Web 界面
├── pyproject.toml
└── requirements.txt
```

## 许可

MIT License
