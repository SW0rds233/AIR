# AIR智能体研究系统

基于 **LangGraph** 多智能体 + **RAG (ChromaDB)** 的学术综述论文自动撰写系统（对话式协作）。

流程：`自然语言计划 → 文献检索 → PDF 摄入 → 引用预验证 → 大纲 → 初稿 → 引用守门/核查 → 跨模型审稿 → 修订循环 → LaTeX 出稿`

另含 **`theory` 理论研究模式**：`研究方向/明确问题 → 研究对象形式化 → 缺口驱动的研究循环（定向检索/建模/推导/反例）→ 受限工具核验 → 两道交付门槛 → 研究交付包`。

## 功能特性

- **对话式协作**：主控 Supervisor 理解自然语言意图，生成任务计划（主题/关键词/子主题 + 任务范围），确认后动态调度工作智能体
- **多源文献检索**：arXiv + Semantic Scholar + OpenAlex（可选 CNKI / 万方），LLM 筛选与综述
- **引用防幻觉全流程**：预验证并过滤预印本 → 写作守门 → CrossRef/OpenAlex/arXiv 交叉验证 → GB/T 7714 格式化
- **撰写-审稿-修订闭环**：跨模型审稿（10 维 50 分制）、跨轮问题台账、有限修订契约、收敛检测与历史最优稿回退
- **LaTeX 渲染**：Markdown → ctexart → xelatex 编译 PDF；自动生成分类树 / 时间线 / 趋势图等
- **Web 界面**：历史会话回看、断点续跑、删除会话、产物按对话隔离（`outputs/{run_id}/`）；高级选项可在 `survey` / `theory` 之间切换；**理论研究模式下对话输入框下方常驻附件入口**（选择文件 → 选用途「补充问题说明／补充文献」→ 上传，已上传列表可移除）
- **科研工作台**：理论模式下「科研工作台」标签页显示研究问题、进度统计、当前动作与未决缺口、研究过程事件、结论状态表、证明义务、证据与原文定位、验证记录、实验/仿真建议、研究路线与失败原因、新颖性审查，并提供对象级反馈与快照派生；理论暂停点可直接**点选候选路线**
- **成本追踪**：分阶段 Token 用量与费用统计

### 理论研究模式（`--mode theory`）

- **研究对象可追溯**：`ResearchSpec / Assumption / Definition / ResearchModel / Claim / ProofObligation / ProofAttempt / VerificationRecord / EvidenceLink / ResearchRoute / ResearchGap`，全部带稳定 ID 与版本；版本只增不减，历史不可覆盖
- **结论状态由规则计算**：`supported` 必须同时满足「全部必要义务已关闭 + 存在与原命题对齐的有效验证记录」，并在 `support_kind / coverage / validation_status` 三个维度上分别标注；局部步骤验证不会升级为整体结论
- **缺口驱动的研究循环**：控制器按「当前缺什么」选择动作（定向检索 / 读原文 / 判定证据关系 / 建模 / 证明规划 / 工具核验 / 找反例 / 新颖性比较 / 换路 / 生成实验规格 / 部分交付）；同输入动作不重复执行，缺新鲜方法时真实换路并保留失败原因
- **知识闭环**：`KnowledgeService` 统一 `search / read / resolve`，所有召回分支都返回可回到原文的定位；检索命中不建立支持关系，需显式判定支持/反对/部分/背景/不足
- **受限验证**：SymPy / Z3 / stats / Lean 子进程执行，白名单表达式、超时与资源限制；`unknown / timeout / unsupported / unavailable` 一律保持未决，绝不当成通过或“命题为假”
- **两道交付门槛**：研究有效性门槛（依赖闭合、无循环、反例已处理、验证未过期、因果结论需识别设计+CI+范围+混淆+证据）+ 论文表达门槛（结论↔正文映射、条件不得删去、未执行实验不得写成结果），并给出三种交付级别（研究备忘录 / 条件性研究报告 / 论文草稿）
- **判定类问题的命题化**：题面含计数/设计约束（如“v 个对象、每块 k 个、每对共现 λ 次”）时，自动抽取参数并套用**经典必要条件**（计数整除 / Fisher 不等式 / Bruck–Ryser–Chowla）判定存在性；判定必须落成**命题 + 必要性义务 + 可复核证书**（每条记录所用定理、定理陈述、输入数值与结论），**仅“存在一条被违反的必要条件”才允许关闭义务**；“必要条件全满足但存在性未定”一律保持未决、不升级交付等级。代码与提示词里不含任何具体题目或领域常量（领域词表放在 `evals/cases/<用例>/retrieval_terms.md`）
- **实验/仿真建议**：`ExperimentSpec` 生成 + 规格校验，只到 `spec_validated`；未执行不得作为证据
- **运行操作三分离**：`start` / `resume` / `fork`（`POST /api/research/fork` 从快照派生新问题，明确标注哪些结论需重算、不复用旧验证）
- **对象级反馈闭环**：`POST /api/research/{project_id}/feedback` 把自然语言意见转成对象级动作（修订假设/弱化命题/要求复核/定向检索）；无法确定作用对象时返回 `needs_clarification` 与澄清问题，**不猜**、不改动研究状态


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

**理论模式（研究 → 出版级论文）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `THEORY_LLM` | `0` = 离线（不调用任何模型、不联网检索，只走规则层与受限工具） | `1` |
| `THEORY_PROPOSER` | `0` = 停用语义提议，退回确定性打分 | `1` |
| `THEORY_PROPOSER_MAX_CALLS` | 单次运行提议调用上限 | `20` |
| `THEORY_LONG_FORM` | `1` = 启用撰写层长文，作为**附录 B** 追加（不替代正文判定链，需要主模型可用） | `0` |

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

### 4. 构建前端（新克隆的仓库必须做一次）

Web 界面已迁到 Vite + TypeScript（计划书 §2 F4）。**构建产物 `src/web/dist/` 不入库**，
而 `src/server.py` 优先提供它；缺少时页面会退回源码模板（引用 `/src/main.ts`），
浏览器里没有任何脚本、按钮全部没反应。所以首次运行前需要构建一次：

```bash
cd src/web
npm install
npm run build      # 产出 dist/ 并把 JS/CSS 发布到 src/web/assets/
```

- 双击 `start.bat` 会自动判断：产物**缺失或比源码旧**（改了 `src/web/src/**`、样式或 `vite.config.ts` 却忘了构建）时自动重新构建；构建失败会明确报错并停止，不会启动一个页面打不开的服务；
- 只想改后端：`start.bat --no-build` 跳过构建检查（最快）；
- 改前端要即时生效：`start.bat dev` = 后端 8000 + vite 开发服务器 5173（`/api` 由 vite 反向代理到后端）；
- 服务端只提供构建产物，缺产物时首页返回 **503 + 构建指引**（不再返回引用 `/src/main.ts` 的源码模板）；
- `setup_env.bat` 的第 [6/6] 步同样会构建（本机没有 npm 时只提示，不报错）。

### 5. 运行

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

**理论研究模式：**

```bash
# 明确问题（直接形式化并研究）
python -m src.main --mode theory "对所有实数 x: x**2 >= 0"

# 研究方向（先给出候选路线，确认后研究）
python -m src.main --mode theory --request "研究信道变化如何影响射频指纹可分性"
```

产物写入 `outputs/research/{project_id}/{snapshot_id}/`：`research_spec.json`、`claims.json`、
`obligations.json`、`models.json`、`evidence.json`、`verification/`、`experiments/`、`proofs/`、
`decisions.jsonl`、`novelty_review.md`、`unresolved.md`、`manuscript.md`、`paper.tex`、
`delivery_gate.md`、`manifest.json`。

要让理论模式能查到资料，先把文献放进主题知识底座（`data/kb/{主题}/manual/` + `meta.json`），
或在 `ResearchSpec.domain` 中给出主题名；底座为空时检索类动作不会被暴露。

**理论模式的只读接口**（供前端/脚本读取研究状态，不会改动任何结论）：

| 接口 | 作用 |
|---|---|
| `GET /api/research/{project_id}/state` | 科研工作台数据：问题规格、结论状态表、义务、验证记录、证据定位、实验规格、路线、事件、预算 |
| `POST /api/research/{project_id}/feedback` | 对象级反馈；歧义时返回 `needs_clarification` 且不改动状态 |
| `POST /api/research/fork` | 从快照派生新问题（明确哪些结论需重算、不复用旧验证） |

统计/分析类工具读取 CSV 时，`data_ref` 只允许指向 `data/` 或 `outputs/` 之下；
需要额外放行只读数据目录时用 `DATA_READ_ROOTS`（分号分隔的绝对路径）。


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

**12. 页面能打开，但按钮全都没反应 / 样式全丢**
说明服务端退回提供了**未构建**的源码模板（缺少 `src/web/dist/index.html`）。执行
`cd src/web && npm install && npm run build`（见「快速开始 4. 构建前端」），
然后刷新页面。构建完成后 `src/server.py` 会优先提供 `dist/index.html` 与哈希化的
`/assets/*`；`GET /` 的响应里若看到 `/src/main.ts` 就是没构建。

## 项目结构

```
AIR/
├── src/
│   ├── main.py / main_chat.py / server.py / gui.py   # CLI / 对话 CLI / Web / tkinter 入口
│   ├── config.py            # 全局配置 (.env 加载/默认值)
│   ├── agents/              # 文献查阅、撰写、审阅、大纲、引用预验证/守门/核查、PDF 摄入、理论写作
│   ├── graph/               # LangGraph 流水线 (Supervisor 编排 / 修订循环 / 理论研究图)
│   ├── research/            # 理论研究内核 (对象模型/存储/缺口/协调/路线/推导/反例/验收/交付包)
│   ├── verification/        # 受限验证执行器 (sympy / z3 / stats / lean, 子进程 + 超时)
│   ├── experiments/         # 实验/仿真规格生成与校验 (只到 spec_validated)
│   ├── kb/                  # 主题文献知识底座 (统一 search/read/resolve + 卡片 + 混合检索)
│   ├── rag/                 # 向量库、图表、LaTeX、引用格式化、相关性过滤
│   ├── tools/               # 学术搜索、引用验证、出处解析、PDF 下载、中文文献源
│   ├── utils/               # HTTP 容错、成本追踪、检索缓存、会话记录等
│   └── web/                 # 前端 (Vite + TypeScript)
│       ├── index.html       #   源码模板 (入口, 由 Vite 构建)
│       ├── src/             #   页面逻辑 (main/app/current-research/research-api/markdown/views/styles)
│       ├── assets/          #   构建出的 JS/CSS (入库, server.py 从这里提供)
│       └── dist/            #   构建出的页面 (不入库, 新克隆需 npm run build, 见「快速开始」)
├── tests/                   # 离线测试 (含可信状态/知识闭环/自主性反向安全用例)
├── data/                    # 运行时数据 (chroma/pdfs/conversations/checkpoints/kb/research)
├── outputs/                 # 产物 (综述按 run_id 隔离; 理论按 project/snapshot 隔离)
├── setup_env.bat            # 一键搭建环境
├── start.bat                # 一键启动 Web 界面
├── pyproject.toml
└── requirements.txt
```

## 能力边界（务必留意）

- 理论模式的结论只在**声明的模型与假设**下成立，不承担与真实系统相符的实证验证；
- 工具通过不等于科学真理：形式化工具只检查提交给它的编码，**陈述是否忠实于原问题仍需人工确认**；
- 多智能体一致同意不能替代数学证明；「没检索到等价结果」只表示在所检索范围内未发现；
- 未执行的实验/仿真规格不是证据；报告与论文稿在门槛未通过时只作为研究备忘录/条件性报告导出。


## 许可

MIT License
