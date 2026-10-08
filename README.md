# AIR智能体研究系统

基于 **LangGraph** 多智能体 + **RAG (ChromaDB)** 的学术研究系统（对话式协作）。

**只有一个入口**: 用户给出自然语言研究请求, 主控（Supervisor）画像并派工给七类角色
（检索 / 建模 / 推理 / 验证 / 写作 / 配图 / 审阅）, 结束后导出交付包。
没有"综述/理论模式"之分 —— 形式化研究能力（命题 / 义务 / 推导 / 反例 / 受限工具核验）
是团队里**推理与验证角色**的职责, 由同一张图统一调度。

## 功能特性

- **对话式协作**：主控理解自然语言请求，产出研究画像（子问题 / 交付形态 / 授权与能力）与版本化计划，随后**动态派工**给角色 —— 不是固定阶段流水线
- **多源文献检索**：arXiv + Semantic Scholar + OpenAlex（可选 CNKI / 万方），命中不等于支持，须显式判定支持/反对/部分/背景/不足
- **引用可核对**：候选清单逐篇验证、撤稿剔除、写作时只允许引用已核实清单，草稿引用与论述是否对应由 `publication/citation_checks` 判定
- **判定层零 LLM**：结论状态由规则计算（`supported` 必须"必要义务全闭合 + 存在与原命题对齐的有效验证记录"），局部验证不升级为整体结论
- **受限工具核验**：SymPy / Z3 / stats / Lean 子进程执行（白名单表达式、超时与资源限制），`unknown / timeout / unsupported` 一律保持未决
- **多格式同源交付**：唯一文稿 IR → Markdown / LaTeX / PDF（`xelatex`）三份产物同一份稿件；等级按**编译事实**与门槛结论判定，不以"看起来有 PDF"代替
- **Web 界面**：历史会话回看、断点续跑、删除会话、产物按研究问题隔离（`outputs/research/{project_id}/{snapshot_id}/`）；对话输入框下方常驻附件入口（选择文件 → 选用途「补充问题说明／补充文献」→ 上传，已上传列表可移除）
- **科研工作台**：研究问题、进度统计、当前动作与未决缺口、研究过程事件、结论状态表、证明义务、证据与原文定位、验证记录、实验/仿真建议、研究路线与失败原因、新颖性审查，并提供对象级反馈与快照派生
- **成本追踪**：分阶段 Token 用量与费用统计（无模型调用时花费如实记为已知的 0）

### 智能体团队（合并计划 M1）

系统已按 [`docs/合并计划.md`](docs/合并计划.md) 收敛为**一个主控 + 七类功能子智能体**的团队：
旧的综述 stage 流水线、旧引擎选择、旧引用守门/预检 Agent 与旧图状态都已退役
（旧实现已删除，统一团队为唯一研究执行路径）。

- **统一入口**：用户只给自然语言（可带附件、资料库与授权策略），**不需要选模式**；主控产出 `ResearchBrief`（分开记录子问题类型 / 交付形态 / 授权与能力三个维度，未知字段保留为未知）与版本化 `TeamPlan`
- **动态派工而非线性阶段**：每轮主控给出一种结构化决策 —— `dispatch` / `request_clarification` / `wait` / `deliver` / `stop_with_report`；子智能体受阻时提 `ResearchNeed`（缺来源 / 缺模型条件 / 缺验证…），由主控转成新任务，**不允许子智能体互相派工**
- **角色与真实能力**：`GET /api/team/roles` 返回八个角色的职责、交付物，并**探测**能力是否真的可用（无 embedding 时如实报告"只能关键词+卡片召回"，不承诺语义检索）
- **统一运行时**：有界工具循环、按任务记账、协作式取消、结果契约校验（角色只能提交本职责内的对象种类，越界即降级为 `partial`）；审阅**只可降级、不可升级**由运行时闸门实际拦住，不靠提示词
- **任务持久化与一致性**：任务生命周期落盘、幂等续跑、`read-set` 版本检查（依据 v2 产出而对象已到 v3 时**拒绝合入**并记录过期）；图状态只保存身份与引用，正文留在产物里
- **团队工作台（前端）**：`ResearchStore` 一份状态（selection / entities / transport / ui 各有归属）；连接状态与运行状态分开显示（**离线不等于后台已停止**）；会话级事件游标、按 `seq` 去重、缺口要求重新同步而不是把页面停在"完成"；团队状态条按角色聚合任务数，失败/受阻原因直接可见
- **按用户指定路径建库**：`POST /api/library/scan`（只预览）→ `POST /api/library/import`（只读引用，**不复制**用户文件）；未授权路径与 `.env`/`*.key` 等敏感目标一律拒绝并给原因；原件被移动后标记 stale
- **同领域共享资料库**：领域识别后先查询已入库资料，检索查询与 PDF 身份可复用；已确认同领域的任务共用资料库，相关性筛选阻止跨领域文献误入研究。
- **审阅与过程可见**：审阅阈值在每次请求中设置（百分制，默认 80），原则性学术问题直接打回；每轮任务的计划、研究对象、稿件草稿与审阅意见逐步保存于 `outputs/research/<project_id>/<run_id>/progress/`，可在当前运行的文件页查看。

离线（无 LLM）时团队走**确定性实现**：检索走 KB/外部检索并留下覆盖记录、综合按已登记证据关系生成候选、写作渲染已登记结果并保留完整追溯；判定层仍为零 LLM。

### 研究判定层与交付门槛（**所有**研究共用, 无一例外）

- **研究对象可追溯**：`ResearchSpec / Assumption / Definition / ResearchModel / Claim / ProofObligation / ProofAttempt / VerificationRecord / EvidenceLink / ResearchRoute / ResearchGap`，全部带稳定 ID 与版本；版本只增不减，历史不可覆盖
- **结论状态由规则计算**：`supported` 必须同时满足「全部必要义务已关闭 + 存在与原命题对齐的有效验证记录」，并在 `support_kind / coverage / validation_status` 三个维度上分别标注；局部步骤验证不会升级为整体结论
- **缺口驱动的研究循环**：控制器按「当前缺什么」选择动作（定向检索 / 读原文 / 判定证据关系 / 建模 / 证明规划 / 工具核验 / 找反例 / 新颖性比较 / 换路 / 生成实验规格 / 部分交付）；同输入动作不重复执行，缺新鲜方法时真实换路并保留失败原因
- **知识闭环**：`KnowledgeService` 统一 `search / read / resolve`，所有召回分支都返回可回到原文的定位；检索命中不建立支持关系，需显式判定支持/反对/部分/背景/不足
- **受限验证**：SymPy / Z3 / stats / Lean 子进程执行，白名单表达式、超时与资源限制；`unknown / timeout / unsupported / unavailable` 一律保持未决，绝不当成通过或“命题为假”
- **两道交付门槛**：研究有效性门槛（依赖闭合、无循环、反例已处理、验证未过期、因果结论需识别设计+CI+范围+混淆+证据）+ 论文表达门槛（结论↔正文映射、条件不得删去、未执行实验不得写成结果），并给出三种交付级别（研究备忘录 / 条件性研究报告 / 论文草稿）
- **判定类问题的命题化**：题面含计数/设计约束（如“v 个对象、每块 k 个、每对共现 λ 次”）时，自动抽取参数并套用**经典必要条件**（计数整除 / Fisher 不等式 / Bruck–Ryser–Chowla）判定存在性；判定必须落成**命题 + 必要性义务 + 可复核证书**（每条记录所用定理、定理陈述、输入数值与结论），**仅“存在一条被违反的必要条件”才允许关闭义务**；“必要条件全满足但存在性未定”一律保持未决、不升级交付等级。检索词表放在 `src/rag/domain_terms/`；设计存在性判定器仍含相应领域规则，形式化入口和约束忠实度检查按题目结构判定。
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

**研究判定层与工具核验（所有研究共用）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `THEORY_LLM` | `0` = 离线（不调用任何模型、不联网检索，只走规则层与受限工具） | `1` |
| `THEORY_PROPOSER` | `0` = 停用语义提议，退回确定性打分 | `1` |
| `THEORY_PROPOSER_MAX_CALLS` | 单次运行提议调用上限 | `20` |

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

仓库包含 `tests/` 与 `evals/`；本机缓存不入库。`src/web/dist/` 不入库，新克隆仓库需按下述步骤构建前端。

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
# 直接指定主题
python -m src.main "研究主题"

# 自然语言入口（建议带上英文术语/缩写，便于英文库检索）
python -m src.main --request "我想研究射频指纹识别(RF fingerprinting, RFFI)的特征提取与对抗攻击"
```

**Web 界面（对话式协作，推荐）：**

```bash
python -m src.server          # 或双击 start.bat
# 浏览器访问 http://127.0.0.1:8000
```

研究过程中可在任意时刻查看「科研工作台」（问题、进度、未决缺口、结论状态、义务与
验证记录）；顶部「历史会话」支持回看与断点续跑。

**命令行（同一个入口, 没有模式开关）：**

```bash
# 明确问题（直接研究）
python -m src.main "对所有实数 x: x**2 >= 0"

# 自然语言描述 + 资料授权（user_kb=只用授权资料库 / autonomous=授权自主检索）
python -m src.main --request "研究信道变化如何影响射频指纹可分性" --source-policy autonomous
```

产物写入 `outputs/research/{project_id}/{snapshot_id}/`：`research_spec.json`、`claims.json`、
`obligations.json`、`models.json`、`evidence.json`、`verification/`、`experiments/`、`proofs/`、
`decisions.jsonl`、`novelty_review.md`、`unresolved.md`、`manuscript.md`、`manuscript.tex`、
`delivery_gate.md`、`manifest.json`。仅当本机安装 `xelatex` 且编译成功时才有 `manuscript.pdf`。

若使用 `user_kb`，需先绑定并导入资料库；默认 `both` 会同时允许自主检索与已绑定的资料库。
离线模式或外部检索不可用时，系统应如实标出缺乏可定位来源，而不是虚构参考文献。

**研究状态的只读接口**（供前端/脚本读取，不会改动任何结论）：

| 接口 | 作用 |
|---|---|
| `GET /api/research/{project_id}/state` | 科研工作台数据：问题规格、结论状态表、义务、验证记录、证据定位、实验规格、路线、事件、预算 |
| `POST /api/research/{project_id}/feedback` | 对象级反馈；歧义时返回 `needs_clarification` 且不改动状态 |
| `POST /api/research/fork` | 从快照派生新问题（明确哪些结论需重算、不复用旧验证） |

统计/分析类工具读取 CSV 时，`data_ref` 只允许指向 `data/` 或 `outputs/` 之下；
需要额外放行只读数据目录时用 `DATA_READ_ROOTS`（分号分隔的绝对路径）。

**按本机路径建人工文献库（合并计划 §13）**：用户可直接给出文件或文件夹路径，系统扫描后
**只登记引用**（不复制、不改名、不修改原文件），随后供检索/推理/引用使用。

| 接口 | 作用 |
|---|---|
| `POST /api/library/scan` | 只扫描不导入，返回逐条状态（`ok/skipped/denied/unreadable`）与截断提示，供**先预览再确认** |
| `POST /api/library/import` | 按请求导入；返回逐条结果（新增/合并/幂等命中/跳过/拒绝/失败）；支持幂等键 |
| `GET /api/library/{source_set_id}` | 库的来源类型、文件数、hash 清单与失效文件（只回显文件名，不外发绝对路径） |
| `DELETE /api/library/{source_set_id}` | 只解除**该库**登记；**不删除用户原文件**，也不清空共享向量库 |

安全规则（程序强制，不靠提示词）：只接受位于授权根（`data/`、`outputs/` 或
`DATA_READ_ROOTS` 显式放行项）之下的路径；`.env`、`*.pem/*.key/*.pfx`、`.ssh/`、`.git/`、
`.aws/`、`credentials*`、`node_modules/`、`.venv/` 即使在被授权根内也一律拒绝；符号链接
越界即拒绝；默认上限 500 文件 / 2 GB，超限标记 `truncated` 而不是静默截断。

```bash
# 先预览
curl -X POST http://127.0.0.1:8000/api/library/scan \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["D:/papers", "D:/papers/extra/brc1949.pdf"], "label": "我的文献"}'
# 确认后导入
curl -X POST http://127.0.0.1:8000/api/library/import \
  -H 'Content-Type: application/json' \
  -d '{"paths": ["D:/papers"], "topic": "我的文献", "idempotency_key": "batch-1"}'
```
（上例中的 `D:/papers` 需由维护者在 `DATA_READ_ROOTS` 中显式放行。）


## 主要参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `topic` | 研究主题（用 `--request` 时可不填） | — |
| `--request, -r` | 自然语言研究描述，Planner 自动提取主题/关键词/子主题 | — |
| `--project-id` / `--problem-id` | 研究对象标识 | `proj-team` / `problem` |
| `--source-set-id` | 已授权资料源 ID | 不绑定 |
| `--source-policy` | `user_kb`、`autonomous` 或 `both` | `both` |


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
`.env` 调低 `PDF_DOWNLOAD_LIMIT`（如 10）、设 `SKIP_EMBEDDING=1`。

**6. 检索返回 429 / 频繁限流**
配置 `OPENALEX_API_KEY`（OpenAlex 免费额度按日计，UTC 午夜重置）；调大 `HTTP_429_BACKOFF_BASE`、`HTTP_CIRCUIT_BREAKER_COOLDOWN`；稍后重试。

**7. LLM 调用超时**
推理模型审稿/写作耗时长，调大 `LLM_TIMEOUT`（默认 900s）；网络波动会自动重试 `LLM_MAX_RETRIES` 次。

**8. 中断后如何继续**
Web 界面顶部「历史会话」→ 未完成会话点「继续」。CLI 是一次性启动入口，目前不提供续跑参数；请勿把再次调用 CLI 当作恢复旧会话。

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
│   ├── main.py / server.py   # CLI（唯一入口）/ Web 服务与前端 API
│   ├── config.py            # 全局配置 (.env 加载/默认值)
│   ├── agents/              # 团队角色与运行时（旧综述 Agent 与旧引用守门/预检已退役,
│   │                        #   通用件在 publication/ 下: citation_checks / evidence_ledger）
│   │   ├── protocol.py      #   团队契约: AgentTask/AgentResult/ResearchNeed/权限令牌/预算
│   │   ├── registry.py      #   角色注册 + **真实可用能力探测**
│   │   ├── runtime.py       #   统一运行时: 有界工具循环/记账/取消/结果校验
│   │   ├── supervisor.py    #   主控: 任务画像/团队计划/结构化决策/复盘
│   │   ├── evidence|modeling|reasoning|validation_planning|writing|figures|review.py
│   │   └── base.py / tools.py   # 角色公共设施与受控工具集
│   ├── graph/               # LangGraph 流水线 (Supervisor 编排 / 修订循环 / 理论研究图)
│   │   ├── research_graph.py #   团队外层循环 (动态派工, 非固定阶段)
│   │   └── unified_state.py  #   统一状态: 只保存身份、任务与引用
│   ├── research/            # 理论研究内核 (对象模型/存储/缺口/协调/路线/推导/反例/验收/交付包)
│   │   ├── task_store.py    #   任务生命周期 + 幂等续跑 + read-set 事务检查
│   │   └── projection.py    #   团队候选登记 (只登记, 不做判定)
│   ├── publication/         # 文稿中间表示 (WritingPacket/Manuscript/Block, 渲染期编号)
│   ├── team_api.py          # 团队与资料库 HTTP 接口 (/api/team/*, /api/library/*)
│   ├── verification/        # 受限验证执行器 (sympy / z3 / stats / lean, 子进程 + 超时)
│   ├── experiments/         # 实验/仿真规格生成与校验 (只到 spec_validated)
│   ├── kb/                  # 主题文献知识底座 (统一 search/read/resolve + 卡片 + 混合检索)
│   │   └── path_import.py   #   按用户给定本机路径建库 (授权根/敏感拒绝/不复制/stale)
│   ├── rag/                 # 向量库、图表、LaTeX、引用格式化、相关性过滤
│   ├── tools/               # 学术搜索、引用验证、出处解析、PDF 下载、中文文献源
│   ├── utils/               # HTTP 容错、成本追踪、检索缓存、会话记录等
│   └── web/                 # 前端 (Vite + TypeScript)
│       ├── index.html       #   源码模板 (入口, 由 Vite 构建)
│       ├── src/             #   页面逻辑 (main/app/current-research/research-api/markdown/views/styles)
│       │   ├── state/       #   唯一状态入口 research-store.ts (selection/entities/transport/ui)
│       │   ├── events/      #   session-events.ts (会话级游标/去重/重连策略)
│       │   ├── views/       #   纯函数视图 (含 team-board.ts 团队工作台)
│       │   └── team-controller.ts  # 团队视图接入 (只读, 单向同步旧状态)
│       ├── assets/          #   构建出的 JS/CSS (入库, server.py 从这里提供)
│       └── dist/            #   构建出的页面 (不入库, 新克隆需 npm run build, 见「快速开始」)
├── docs/                    # 项目文档 (进度.md / 计划书.md / 合并计划.md)
├── evals/                   # 验收用例 (cases/) 与审查报告归档 (reports/)
├── data/                    # 运行时数据 (chroma/pdfs/conversations/checkpoints/kb/research)
├── outputs/                 # 统一团队产物 (研究包按 project/snapshot 隔离)
├── setup_env.bat            # 一键搭建环境
├── start.bat                # 一键启动 Web 界面
├── pyproject.toml
└── requirements.txt
```

## 能力边界（务必留意）

- 形式化结论只在**声明的模型与假设**下成立，不承担与真实系统相符的实证验证；
- 工具通过不等于科学真理：形式化工具只检查提交给它的编码，**陈述是否忠实于原问题仍需人工确认**；
- 多智能体一致同意不能替代数学证明；「没检索到等价结果」只表示在所检索范围内未发现；
- 未执行的实验/仿真规格不是证据；报告与论文稿在门槛未通过时只作为研究备忘录/条件性报告导出。
- **重构已收敛为一套团队**：主控与七类功能子智能体共用研究对象、提交边界、会话和唯一文稿 IR；旧理论引擎与旧排版链已删除。真实模型、可定位文献、浏览器及 PDF 的实物验收仍需按 [`docs/进度.md`](docs/进度.md) §5 执行。
- 团队闭环第一版**串行执行**（按合并计划 §10 要求，资源隔离完成前不打开全队并行）；没有 LLM 时角色走确定性实现，"任务完成"不等于"研究完成"。


## 许可

MIT License
