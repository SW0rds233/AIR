# 计划推进情况

> **接手请先读 `交接文档-统一多智能体科研系统.md`**（现状 / 验收证据 / 复现命令 / 下一步 / 环境 / 坑）。
> 本文件是**逐轮记录**；权威待办在《未完成工作清单.md》。

## 10.16 第十一轮（2026-10-04：交接文档 + 本轮结束状态）

本轮不改变任何行为，只做**交接**：新增 `交接文档-统一多智能体科研系统.md`，并把它的入口链接
写进本文件与《未完成工作清单.md》的"下一步"一节。文档里逐条列出：验收口径对照、复现全部证据的
命令、已完成的 10 项缺口（含实现位置）、未完成的四块（G01 文件删除的批次方案 / G13 / §8.4 /
R5 剩余）、环境与开关、以及**本次会话踩过的 14 个坑**（对象归属读 `_scope`、`run_id` 唯一性、
冻结快照、交付角色必须有计划任务、依赖终止后的下游、提交口重建载荷丢元数据、候选种类登记、
删旧模块前先确认新家覆盖、Windows 引号地狱等）。

**自检**：交接文档里提到的 27 个 `.py` 路径已用脚本逐个确认存在（脚本用完即删）。

## 10.15 上一轮（2026-10-04 第十轮：R5 清仓 —— 文档与"没人读的旋钮"）

| 项 | 处置 | 判据/证据 |
|---|---|---|
| `pyproject.toml` 项目名 | `langgraph-survey-pipeline` → **`air-research-team`**，描述改为"统一的多智能体学术科研系统（一个主控 + 七类角色）" | 与 README 首段、`/api/team/roles` 的定位一致 |
| README 的失效说明 | 删除/改写：`--mode theory` 用法、"survey / theory 切换"、`main_chat` / `gui.py` 入口、"理论模式"小节标题、`THEORY_LONG_FORM` 开关行、`pipeline_cache`+`--skip-retrieval` 段落；"系统正在收敛"改为"已收敛为团队"，并把判定层/门槛/受限核验描述为**所有研究共用**（不再是某个模式的能力） | `README.md` 里已无 `--mode` / `main_chat` / `gui.py` / `survey`（除"没有综述/理论模式之分"这句正面陈述）/ `THEORY_LONG_FORM` |
| `--max-revisions` / `--skip-retrieval` / `MAX_REVISIONS` | **退役**（CLI、`StartRequest`、输入快照、初始状态、README 参数表一并删除） | 先确认**没有读者**：全库 grep 这两个名字，除了"透传赋值"再无使用点（修订循环已随旧流水线删除；检索与否由资料授权策略 + 覆盖记录决定）。留着会让使用者以为"设了就生效"。旧会话记录里的同名键由 Pydantic 忽略（`test_server.py` 的续跑用例仍用旧记录快照覆盖这条） |
| 用例同步 | `test_build_initial_state` 改为断言**这两个键不存在**（判据从"透传生效"改成"拨盘已删"，更强）；`test_build_initial_state_carries_the_request_text` 保留"请求原样进入状态"的语义 | 35 passed（入口/会话相关文件） |

### 仍未闭合

1. **G01 的文件删除**：真正的前置是"把 35 个用例文件从 `theory_pipeline` 迁走"（批次见 §10.14）。
2. **G13 用量边界**；**§8.4 前端 8 个验收场景**；R5 剩余项（LangGraph 依赖盘点、固定
   2019–2026 检索窗）。
3. 工作区累计 100+ 项改动**仍未提交**。

## 10.14 上一轮（2026-10-04 第九轮：R5 清仓 —— 旧综述图状态退役）

> 本轮是"小刀口"：`src/graph/state.py`（`PipelineState`，127 行）的最后几个使用者是上一轮
> 删除的引用守门/预检旧 Agent。删之前用 **AST** 全库确认"代码里没有任何引用"
> （只剩注释与文档字符串），再删并把退役理由写进 `test_engine_retirement.py` 的清单表。

| 判据 | 结果 |
|---|---|
| AST 扫描 `src/` + `tests/`：`graph.state` import / `PipelineState` 名字 | **0 处**（排除注释与字符串） |
| 删除后受影响的用例 | `test_engine_retirement` / `test_agent_protocol` / `test_agent_runtime` / `test_new_features` → 75 passed |
| 文档同步 | `graph/unified_state.py` 的"与旧状态的关系"改写为"`PipelineState` 已随流水线删除、能力去向"；`test_engine_retirement.py` 的退役表新增一行 |

### 仍未闭合（含一处**顺序修正**）

1. G01 剩余删除步骤：**第 1 步（`theory_writer`/`theory_render`）实际被第 5 步挡住** ——
   `theory_pipeline.py` 与 `publication_paper.py` 也 import 它们，而 `theory_pipeline` 有
   **35 个用例文件**在驱动。因此真正的前置是"**把测试从 `theory_pipeline` 上迁走**"：
   先迁行为用例（判据换成真实组件：推理角色 + `AgentRuntime` + 唯一提交口，或已抽出的
   判定层服务如 `VerificationService`/`research.obligations`），再一次性删掉整簇文件。
   建议的迁移批次（按依赖清晰度）：① 纯判定层服务类用例（`test_design_feasibility` 的
   服务部分、`test_research_invariants`、`test_research_derivation`）→ 直接调服务；
   ② 交付/出版类（`test_delivery_level`、`test_publication_*`）→ 走团队 + 交付包；
   ③ 端到端与"方向→候选确认"类（`test_theory_mode`、`test_research_identity`）→ 最后处理。
2. **G13 用量边界**、**§8.4 前端 8 个验收场景**、**R5 其余清仓项**（README/`pyproject` 名称、
   LangGraph 依赖盘点、`--skip-retrieval`/`--max-revisions` 去向、固定检索窗）。

## 10.13 上一轮（2026-10-04 第八轮：**旧 Agent 七件套退役** —— 先迁能力，再删实现）

> 上表第 3 步。判据始终是"**语义有没有减少**"：先把还在提供的能力搬进唯一层、把行为断言
> 换成活的守护对象，再删旧模块。本轮删掉 **7 个旧 Agent（约 2000 行）**。

### 一、先迁能力（新增两个通用件）

| 新家 | 内容（逐字迁出，未改判定口径） |
|---|---|
| `publication/citation_checks.py`（236 行） | `check_citation_semantics`（引用与论述是否真的对应）、`auto_fix_citations`（只删无效编号 + 重编号，不生成新引用）、`validate_draft_citations` 及其私有 helper |
| `publication/evidence_ledger.py`（86 行） | `build_evidence_ledger`（证据台账）、`build_verified_reference_sheet`（写作可用的引用清单）、`verify_reference_list`（候选文献逐篇验证 + 撤稿剔除） |

迁移是**逐字搬运**（用脚本按函数真实边界抽取，避免手抄改错），只改模块头与 import。
`server`/`writing`/`package` 等生产路径此时并未使用它们（旧 Agent 已无生产使用者），
因此这次搬迁的直接价值是：**删旧 Agent 不再删掉这层语义检查**。

### 二、把"血的教训"迁到活的守护对象

`paper_reviewer` 的提示词里有一条来自真实事故的规则：**引用编号每轮重排**，沿用旧台账对
`[n]` 的主题描述会让同一个问题被连续 4 轮误判成 Critical 未解决。删模块前把这条规则
写进**真正在跑**的 `agents/review.py::ReviewAgent.SYSTEM`，并把断言迁到它上面
（`test_relevance_cache.py::test_review_prompt_warns_about_renumbered_citations`）。
同类迁移：`test_agent_prompts_carry_no_hardcoded_domain_vocabulary` 的守护对象由
`PAPER_REVIEWER_SYSTEM` 换成 `ReviewAgent.SYSTEM`。

### 三、删除清单与"语义去向"

| 删除 | 行数 | 生产使用者 | 语义去向（覆盖更强或等价） |
|---|---|---|---|
| `agents/citation_checker.py` | 160 | 无 | `publication/evidence_ledger.build_evidence_ledger`（用例迁到新家） |
| `agents/citation_guard.py` | 277 | 无 | `publication/citation_checks`（5 条语义用例迁到新家，断言未减） |
| `agents/citation_prechecker.py` | 237 | 无 | `publication/evidence_ledger.verify_reference_list`（撤稿剔除用例迁到新家） |
| `agents/literature_reviewer.py` | 367 | 无 | 工具循环 → `AgentRuntime.run`；`tests/test_agent_runtime.py` **11 项**覆盖（执行工具并回喂／未授权能力／未授权读范围／未知工具与异常区分／上限／取消／事件记账），强于原 3 项 |
| `agents/outline_generator.py` | 98 | 无 | `agents/writing.py`（合并计划 §15.2 早已合并；无用例引用） |
| `agents/paper_reviewer.py` | 692 | 无 | `agents/review.py` + `research/critic`；提示词规则与无领域硬编码断言已迁 |
| `agents/pdf_ingestor.py` | 196 | 无 | `kb/ingest`；无用例引用 |

保留（有意）：`protocol.ROLE_ALIASES` 里的旧角色名映射（**一次性迁移识别**，计划 §5.6 允许）、
`graph/node_progress.py` 的旧节点名分支（历史 checkpoint 里仍是旧名）。
删除的是"旧实现的断言"，不是行为判据 —— 每个模块的语义去向都记在上表里。

### 四、验证基线

| 命令 | 结果 |
|---|---|
| `pytest -q`（离线全量） | 见 §10.13 结尾 |
| 三个混装测试文件 | 44 passed（重新指向新家后；其中引用类用例会跑真实网络校验，约 2 分钟） |
| `ruff check --select F,E9 src/ tests/` | All checks passed |

### 五、仍未闭合

1. G01 剩余删除步骤（顺序见 §10.12 第三节）：`theory_writer`/`theory_render` →
   `publication_paper`/`theory_state`/`publication_render`/`latex_render` →
   **最后** `theory_pipeline` + `loop`（35 个用例文件靠它驱动，须先把驱动换成真实组件）。
2. **G13 用量边界**、**§8.4 前端 8 个验收场景**、**R5 清仓**。

## 10.12 上一轮（2026-10-04 第七轮：**G17 收官** —— Markdown 与 LaTeX/PDF 同一份稿件）

> 上一轮把"选引擎"从运行时删掉之后，本轮处理**唯一文稿 IR 的最后一处裂缝**：交付的
> Markdown 来自旧快照 IR，而 `.tex`/`.pdf` 来自唯一 IR —— 同一次运行的两份产物来自两种
> 表示，"同源同版"只能靠人工比对。现在两者都从 `publication.schemas.Manuscript` 出来。

### 一、改了什么

| 位置 | 改动 |
|---|---|
| `agents/writing.py::write_main_manuscript` | 两条路径（模型起草 / 离线确定性起草）都先把稿件转成**唯一 IR**（`to_publication_manuscript`），再用 `render_markdown` 渲染 —— 与 `.tex`/`.pdf` 同一份稿件。删除只服务旧 IR 的 `_legacy_render` |
| `agents/writing.py::to_publication_manuscript` | **块锚点改为确定性**（按文档序 `blk-<位置>`）。此前 `Block.block_id` 默认随机：同一份冻结快照渲染两次会得到**不同的正文锚点** —— 离线交付不可复现（同一输入两次产物不同），版本之间也无法按锚点对齐。这个缺陷是本轮新写的"确定性"断言抓出来的 |

### 二、可验证的结果

| 判据 | 结果 |
|---|---|
| Markdown 与唯一 IR 同源 | `write_main_manuscript(...)` 返回的稿件是 `publication.schemas.Manuscript`，且 `markdown == render_markdown(manuscript)` |
| 确定性 | 同一快照连续渲染两次**逐字相同**（含锚点）；断言写进 `test_main_text_writing_agent.py` |
| 可追溯 | 正文含 `<!-- block:… -->` 锚点，`manuscript_traceability.checked == "markdown"` 仍成立 |
| 交付等级 | `problem2.md` 端到端仍为 **完整论文**，`manuscript.pdf` 落盘（`test_unified_entry_http.py`） |

### 三、G01 文件删除的**顺序**（剩余工作的执行清单）

删除必须自下而上，每一步都要在同一提交里迁移掉该模块的最后一批使用者：

| 步 | 目标 | 先决条件（谁还在用） |
|---|---|---|
| 1 | `agents/theory_writer.py` + `rag/theory_render.py` | `writing.manuscript_from_snapshot` 仍用它把快照渲染成**旧 IR** —— 需改为直接由快照构造唯一 IR 的块（`publication.schemas`），此后这两个模块对生产路径无使用者 |
| 2 | `research/publication_paper.py`（680 行） | 仅 `theory_pipeline` 与它自己的用例（`tests/test_publication_paper.py`）使用；用例的行为断言并入 `publication/render_latex` 的用例后删除 |
| 3 | 旧 Agent 七件套（`citation_checker`/`citation_guard`/`citation_prechecker`/`literature_reviewer`/`outline_generator`/`paper_reviewer`/`pdf_ingestor`，约 2000 行） | 生产路径**已无使用者**（只有 `test_new_features`/`test_refactor_features`/`test_relevance_cache` 引用）→ 按 §5.3 D1 先确认新家覆盖（引用守门 → `publication/checks`、审阅定位 → `ReviewIssueStore`、PDF 入库 → `kb/ingest`），再迁行为用例、删旧断言 |
| 4 | `graph/theory_state.py`、`rag/{theory_render,publication_render,latex_render}` | 随第 1–2 步的使用者一起消失 |
| 5 | `graph/theory_pipeline.py` + `research/loop.py`（3326 行） | 最后一步：这两个模块是**测试驱动形式化研究的入口**（35 个用例文件）。要先把它们驱动的行为用例迁到真实路径（推理角色 + 唯一提交口），再删 |

第 5 步是最大的一块：`tests/test_theory_mode.py`（约 1000 行、40 处引用）以及
`test_research_*` 系列目前都通过 `theory_pipeline.run_theory_pipeline` 驱动判定层。
迁移口径是**把驱动换成真实组件**（`ReasoningAgent` + `AgentRuntime` + 唯一提交口），
不是"再造一个小引擎"（计划 §5.6 明确禁止）。

### 四、仍未闭合

1. 上表第 1–5 步（G01 的文件删除）。
2. **G13 用量边界**（用户已明确本轮不考虑，但是真实模型自主运行的前置）。
3. **§8.4 前端 8 个验收场景**、**R5 清仓**（README/`pyproject` 名称/LangGraph 依赖盘点、
   `--skip-retrieval`/`--max-revisions` 去向、固定 2019-2026 检索窗）。

## 10.11 上一轮（2026-10-04 第六轮：**G01 入口收敛** —— 只剩一张图）

> 本轮把"选引擎"从**运行系统里彻底删除**：所有研究请求（综述型、形式化、方向型）都进
> 同一张团队图。旧引擎的文件本身还在（`research/loop.py`、`graph/theory_pipeline.py` 等），
> 但**运行时已经不可达**，只在测试里作为库被直接调用；下一轮按 §3 R5 的清单删除文件并
> 改写剩余的引擎行为用例。

### 一、改了什么

| 位置 | 改动 |
|---|---|
| `src/server.py` | `DEFAULT_ENGINE`/`SURVEY_ENGINE_ENABLED`/`_resolve_engine(mode, request)`/`_build_app_for_mode` 的理论分支/`from ... theory_pipeline import build_theory_pipeline` **全部删除**；新增 `TEAM_ENGINE` 常量与不收参数的 `_resolve_engine()`；`StartRequest.mode` 删除（旧客户端传了会被 Pydantic 忽略，不会报错也不会换引擎）；`build_theory_initial_state`、`_research_progress`（**为了显示进度又建了一个引擎**）删除；反馈端点的引擎分支删除 |
| `src/server.py::start_session` | 顺序改为：规范化输入 → 校验资料授权 → **落规格并判冲突** → 建 run/session → 注入同一快照 → 启动。新增 `_ensure_problem_spec()`：把"同 ID 改题必须 409"从旧引擎入口**搬到统一入口**（团队运行此前会静默沿用旧规格） |
| `src/server.py::resume_session` | 旧引擎创建的会话无法用团队图续跑 → 如实返回 409 + 可执行下一步，而不是"看起来在续跑" |
| `src/main.py` | 删除 `--mode`、`_run_theory_mode`、`--candidate`/`--max-actions`/`--max-tool-calls`；**`--source-policy` 此前被硬编码忽略**，现在真的传下去 |
| 测试 | 建图桩从 `server.build_theory_pipeline` 迁到唯一入口 `server._build_team_app`（会话生命周期/流式/interrupt 语义不变）；`test_engine_boundary_contract`/`test_engine_retirement`/`test_unified_entry_*` 里"选择点"断言改写为**断言它不存在**（更多不是更少）；`tests/test_theory_state_contract.py` 删除，语义去向记入退役表（团队用 dataclass 状态，缺字段直接报错而不是静默变默认值） |

### 三、被既有断言抓出来的**真实缺陷**（都已修）

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 1 | **R6 隔离对团队运行失效** | 命题载荷里没有 `problem_id`（归属只记在 `_scope`），而所有按问题过滤的读法把"空"当成"归属不明"→ **同项目两个问题互相看得到对方的结论**（实测两个问题的 id 集合完全相同） | `inspection.claims()` 增加 `_scope` 回退 + fail-closed；`projection.object_payload_for()` 从**运行身份**同步 `problem_id`（覆盖候选自填值，否则智能体能用字段把对象挂到别人的问题上） |
| 2 | **工作台里 `run_id` 恒为空** | 身份只读旧引擎的运行记录；团队运行不写它 → "HTTP 返回的 ID = 工作台 = 交付包"这条链在团队路径断掉（G03 的核心要求） | `inspection.identity()` 增加团队运行状态回退 |
| 3 | **团队运行从不冻结快照** | 交付包目录存在、界面显示"已冻结"，但对象库里没有快照 → `fork` 报"未找到可派生的快照" | `TeamSession.export()` 调 `research/snapshot.freeze_snapshot()`（冻结的唯一入口，`allow_replace` 保证重复导出幂等），失败抛 `ExportError`；写入点清单相应登记该模块 |
| 4 | **`final_state.snapshot_id` 为空** | 会话终态无法指向交付包里的同一份快照 | 由包目录/manifest 派生 |
| 5 | **`run_id` 会碰撞** | 主题 + 秒级时间戳：同一秒用同一主题启动两次 → **同一个 run_id**，而对象按 run 归属 → 两次运行混在一起 | 追加 4 位随机后缀 |
| 6 | **反馈不检查对象归属** | 可以改到同项目**另一个问题**的对象 | 定位时读 `_scope`/`problem_id`，不属于本问题则澄清拒绝 |
| 7 | `start_session` 里一行重复的 `build_initial_state` | 合同与 `spec_reused` 刚写就被覆盖（脚本化编辑的产物，被 e2e 断言抓到） | 删除重复行；并把 409 提前到**建会话之前**，不再留下悬挂会话 |
| 8 | `usage.cost_usd` 在 `llm_calls=0` 时报 `null` | 交付清单看起来"花了多少不知道"，而离线运行的花费是确定的 0 | `UsageRecord.to_dict()`：无模型调用 ⇒ 花费**已知为 0**（有调用但缺价目时仍保持 `null`） |
| 9 | **交付形态要求的角色可能没有任务** | 子问题来自任务分类/模型提议，而"需要哪些角色"来自 `_DELIVERABLE_ROLES`；两张表对不上时计划里没有该角色的任务，主控却在"没有可派发的任务, 且交付形态要求未满足"处收尾 → **任务全跑完、一条结论都没有**（实测：带"问题说明附件"运行，4 个任务跑完，命题数 0，等级掉到研究备忘录） | `SupervisorAgent._ensure_required_roles()`：在 `brief()` 与 `plan()` 各兜一次（幂等，只补缺失角色，不覆盖已有措辞），默认措辞表 `_DEFAULT_ROLE_SUBQUESTION` |
| 10 | **依赖键对不上会永久卡死下游** | `settled` 按"角色+目标"文本键匹配；目标文本里带附件正文/计数时键会变，于是**已经受阻返回**的前置永远"未 settled"，下游永不就绪 | `settled` 追加"**有成果行**的任务"（成果行是"已尝试过"的直接证据） |
| 11 | **补派任务提交的对象没有运行身份** | `needs_to_tasks` 生成的任务不带 `run_id` → 其对象 `_scope.run_id` 为空 → 按 run 裁剪的视图（交付摘要的登记对象计数、按 run 过滤的产物清单）把它们当成"不是这次运行产出的"（实测：工作台有命题，摘要 `claim: 0`） | `TeamRun._execute_one()` 在唯一执行口回填 `run_id`/`project_id`/`problem_id` |
| 12 | **义务对象丢元数据** | `commit._normalise_obligation` / `_apply_obligation_disposition` 会**重建**载荷，把唯一提交口刚写的 `_scope` 一起丢掉 → 义务没有运行身份 | 新增 `_keep_meta()`/`_scope_meta()` 并在两处重建后重新带上元数据 |
| 13 | **交付清单没记问题附件** | 团队导出的 `input_snapshot` 只有 request/资料源/策略 → 事后无法区分"用户交来的题面"与"检索到的证据文献"（旧路径记了，团队路径漏了） | `export()` 的输入快照补 `attachment_ids`/`attachments`/`problem_id`/`run_id` |

### 四、验证基线

| 命令 | 结果 |
|---|---|
| `pytest -q`（离线全量） | **1159 passed, 1 skipped**（起点 1082） |
| `ruff check --select F,E9 src/ tests/` | All checks passed |
| 离线 e2e（HTTP 全链路） | 4 passed：启动 → 事件流 → 工作台同一 run_id → 产物清单 → 对象级反馈 → 从快照派生 |
| **真实 Chromium 九项** | **9 passed**（含"附件上传 → 启动 → 工作台出现结论对象"整条链路） |
| 团队验收（`problem2.md`） | `mode=team`、交付等级 **完整论文**、`manuscript.pdf` 落盘、`usage.cost_usd == 0.0` |
| 新增回归 | `tests/test_team_planning_and_scope.py`（10 项：交付形态角色全覆盖 + 兜底不覆盖已有措辞 + 补派对象带 run 身份 + 计划任务执行前回填身份） |

### 五、仍未闭合

1. **删除引擎文件**：`research/loop.py`、`graph/theory_pipeline.py`、`graph/theory_state.py`、
   `rag/{theory_render,publication_render,latex_render}`、`publication_paper.py`、
   `theory_writer.py`、7 个旧 Agent 与旧 `PipelineState` 仍在（**运行时已不可达**，只在测试里
   作为库被直接调用）。删除时要在同一提交里处理 `tests/test_theory_mode.py` 等仍直接调它们的用例。
2. **G13 用量边界**、**§8.4 前端 8 个验收场景**、**R5 清仓**（README/`pyproject`/LangGraph 依赖盘点）。

## 10.10 上一轮（2026-10-04 第五轮：新颖性对照回到团队 + 排序口径抽出 + G01 准入判据）

> 继续按 §9 的 R2（"抽取旧引擎能力"）推进。**仍未删除 `TheoryEngine`**，但本轮把"能不能删"变成了**可执行的判据**（见第三节第二条）。

### 一、本轮的切片

| 切片 | 问题（已核实） | 本轮处置 | 回归 |
|---|---|---|---|
| **新颖性对照在团队路径上缺失** | `_act_compare_novelty` + `novelty.assess` 只在引擎里；团队没有新颖性对象，于是交付包 `_limitation()` 只能落到 `not snapshot.novelty` 分支 —— **"没比过"和"比过没问题"在交付物里长得一样** | 新增 `research/novelty_service.py`：`novelty_lookup_for(service, source_policy=)` 选对照来源（本地库优先；**只有授权外搜时才走外部检索**），`assess_novelty_for()` 落记录；注册 `novelty` 为可写对象种类（`OBJECT_KINDS`/`WRITABLE_KINDS`/`KIND_MAP`）；证据角色产出**确定性 id**（`nov-<claim>-v<version>`）的记录；`snapshot_from_store` 收集 novelty（交付包/出版层据此写局限） | 新增 `tests/test_team_novelty.py`（7 项：`user_kb` 绝不外搜／`autonomous` 才外搜且声明覆盖范围／无来源也落 `unchecked` 并写原因／有本地库时记 `local_kb` 与库名／团队运行留下记录且快照与 `_limitation()` 都能看到"新颖性未确认"／种类已登记／受阻结果仍可携带候选） |
| **"受阻"被当成"什么都没产出"** | `AgentBase.blocked()` 不接受 `changes` —— 调用方一传就 `TypeError`，整条证据任务 `failed`，而卡片上只显示"成果不符合契约"（症状离原因很远） | `blocked(..., changes=)` 接受候选：检索受阻时"新颖性未对照"这条记录仍要落盘 | `test_blocked_results_may_still_carry_candidates` |
| **排序口径长在引擎里** | `OBLIGATION_PRIORITY` / `_obligation_kind` / `_claim_of` 都在 `loop.py`，只读检视层为了排缺口顺序必须 import 引擎 —— 引擎退役时工作台会跟着消失 | 新增 `research/obligations.py`（唯一口径，含"为什么是这个顺序"的说明），`loop.py` 转发，`inspection.py` 改从这里取 | 既有缺口/工作台用例（`test_store_inspection` 的差异测试逐条比对缺口顺序） |
| **"可以删引擎了吗"没有判据** | 只能靠人工读代码判断还有哪些能力挂在引擎上 | `tests/test_engine_retirement.py::test_extracted_services_do_not_depend_on_the_engine`：用 AST 扫描 **11 个已抽出的服务/只读模块**，任何一个出现 `src.research.loop` 的 import 即失败 | 该用例本身（它在有人把引擎依赖写回去时立刻变红） |

### 二、可验证的结果

| 场景 | 结果 |
|---|---|
| 离线团队运行（`user_kb`、无资料库） | 落 `nov-clm-pre-v2`（确定性 id）`status=unchecked`、结论"未接入已有工作检索, 无法判定新颖性"；`snapshot.novelty` 有 1 条；`_limitation(snapshot)` 含**"新颖性未确认"** |
| 授权边界 | `novelty_lookup_for(None, source_policy="user_kb")` → `(None, {})`（不联网）；`"autonomous"` → 外部 lookup 且声明覆盖 `arXiv/Semantic Scholar/OpenAlex` |
| 已抽出的能力不再依赖引擎 | AST 扫描 11 个模块 → 0 处 `src.research.loop` 依赖（`inspection`/`reporting`/`budget`/`obligations`/`feedback`/`forking`/`novelty_service`/`snapshot`/`delivery`/`commit`/`verification_service`） |

### 三、验证基线

| 命令 | 结果 |
|---|---|
| `pytest -q`（离线全量） | **1154 passed, 1 skipped**（起点 1082） |
| `ruff check --select F,E9 src/ tests/` | All checks passed |
| `npx tsc --noEmit` / `npx vitest run` | 0 error / 256 passed（20 files） |
| `pytest tests/test_browser_web_flow.py`（真实 Chromium） | 9 passed |

### 四、仍未闭合

1. **G01 / P0 的最后一步**：`server.py` 只剩 1 处 `TheoryEngine`（无团队状态的旧问题的反馈迁移开关）；CLI `--mode theory` / `_run_theory_mode` / `_resolve_engine` / `DEFAULT_ENGINE` / `SURVEY_ENGINE_ENABLED` / `StartRequest.mode` 仍在；`research/loop.py`、`graph/theory_pipeline.py`、`rag/{theory_render,publication_render,latex_render}`、`publication_paper.py`、`theory_writer.py`、7 个旧 Agent 与旧 `PipelineState` 仍在。删它们要在**同一提交**里改写 §3 R5 列出的双引擎断言。
2. **G13 用量边界**（用户已明确本轮不考虑，但是真实模型自主运行的前置）。
3. **§8.4 前端 8 个验收场景**、**R5 清仓**（README/`pyproject` 名称/LangGraph 依赖盘点）。

## 10.9 上一轮（2026-10-04 第四轮：只读检视层去引擎化 + 反馈/派生回到团队 + 团队运行进入工作台）

> 继续按 §9 的 R2（"抽取旧引擎能力"这一组）推进。**仍未删除 `TheoryEngine`**（原因见第四条）。

### 一、本轮的切片

| 切片 | 问题（已核实） | 本轮处置 | 回归 |
|---|---|---|---|
| **工作台只读端点依赖研究引擎** | `server._research_state` 建 `TheoryEngine(spec, store, …)` 才读得出对象；`reporting.workbench_projection(engine=…)` 依赖 `engine.metrics/model_selection/model_comparison/_gaps/event_digest/decisions/budget`。一个**只读**端点因此依赖了"能执行研究动作"的组件 —— 引擎退役时工作台会被迫跟着重写 | 新增 `research/inspection.py::StoreInspection`：只读检视层（对象读取、逐命题证据归属、运行身份/用量/预算、事件摘要、指标、模型选择/比较、缺口），**不写任何对象、不执行任何动作**；`reporting.workbench_projection` 改为只接受**已算好的值**（不再收 engine）；`server._research_state` 改用检视层 | 新增 `tests/test_store_inspection.py`（14 项，**差异测试**：同一份存储逐项比对检视层与引擎的 id/版本/事件摘要/缺口/模型选择/身份/预算）；**决定性用例** `test_workbench_endpoint_never_constructs_a_research_engine`（把 `TheoryEngine` 打成一构造就抛错，端点仍须 200 并返回正确内容） |
| **团队运行在工作台里"没有研究问题"** | 团队路径从不写 `ResearchSpec`，而工作台/问题列表/交付清单都按 `problem_id` 找规格 —— 同一个项目在团队模式下只读端点直接 404，对象明明在库里 | `TeamRun.prepare()` 落盘输入规格（`_persist_spec`，**不覆盖已有规格**：它是冻结的输入契约），并落 `spec_persisted` 事件 | `test_team_run_is_browsable_in_the_research_workbench`（团队跑完后工作台 200、能看到结论/义务/事件，且结论状态是判定层给的 `supported`）；`test_a_second_team_run_does_not_overwrite_the_frozen_spec` |
| **摘要/预算/分类各写一份** | ① 事件摘要在 `loop._summarize_event`（110 行）里；② `ResearchBudget` 长在 `loop.py`，只读侧要用它就只能 import 引擎；③ `_claim_category` **有两份且已分叉**：内核只回 `causal/empirical/scenario/normative/formal`，引擎还回 `monotonicity/identity/inequality` —— 而团队建模角色用的是内核那份，能力声明因此说"未知问题类型 formal: 需先澄清类型再研究"（内核文档还声称有一条一致性断言，**实际不存在**） | ① 事件摘要逐字迁到 `reporting.summarize_event`，引擎转发；② `ResearchBudget` 迁到 `research/budget.py`，引擎再导出；③ `_claim_category` 完整规则收进内核，`loop._claim_category` 转发 | 摘要迁移由既有 `test_research_logging.py`（15 项，含 digest 逐字断言）与差异测试把守；`test_claim_category_has_one_implementation` 补齐**那条不存在的**断言（9 个样本逐项比对 + 三态期望） |
| **用户反馈 → 对象级修订** | 该能力只在 `TheoryEngine.submit_feedback` 里，于是 `/api/research/{p}/feedback` 必须建引擎；团队路径没有任何入口 | 新增 `research/feedback.py`（定位对象 → 选承接角色 → 交回主控）与 `TeamRun.submit_feedback()`：意见变成**跨职责需求**，由主控转成 `AgentTask`，成果仍经唯一提交口落盘；**不猜对象**（没给 id 就返回澄清 + 候选项，状态零改动）；已收尾的运行允许继续，并给这次意见**自己的轮次额度**（否则 `rounds >= max_rounds` 会立刻收尾，需求根本派不出去 —— 实测踩到） | 新增 `tests/test_team_feedback.py`（7 项：无对象澄清且不改对象／指定对象则**目标对象换版**而非新建／已收尾能继续且真的派工／未知对象拒绝／候选项只列本问题／HTTP 端点两条路径／需求种类与角色一致） |
| **派生（fork）** | 同上：只在 `TheoryEngine.fork_from_snapshot` 里，派生接口必须建引擎 —— 而派生是**用户可见功能**（从快照派生新问题、不复制验证记录） | 新增 `research/forking.py::fork_from_snapshot(store, spec, …, on_claim=)`：复制命题并**重置状态** + 同一步创建新问题规格与血缘；引擎的同名方法改为转发（它额外用 `on_claim` 登记自己的研究路线），HTTP 端点不再建引擎 | `test_workbench_api.py::test_fork_endpoint_does_not_need_a_research_engine`（把引擎打成一构造就抛错，派生仍须 200 且新问题可打开、不复用验证记录）；既有 fork 用例（`test_research_autonomy` / `test_workbench_api` / `test_e2e_http`）全部保持通过 |

### 二、可验证的结果

| 场景 | 结果 |
|---|---|
| 团队运行 → 工作台 | `GET /api/research/proj-team-wb/state?problem_id=p1` → **200**，`claims[0].status == "supported"`（判定层给出）、义务与事件都在、`budget.max_actions == 40` |
| 只读路径不构造引擎 | 把 `src.research.loop.TheoryEngine` 换成"一构造就抛 AssertionError"后，端点仍返回 200 且内容正确 |
| 检视层与引擎一致性 | 对象 id/版本、`event_digest`（逐字）、`metrics`（events/counts/anomalies/actions/tool_calls）、`gaps`（类型/目标/陈述逐条）、`model_selection`、`model_comparison`、`identity`、`budget` 全部相同 |
| 检视层真的只读 | 调用全部读方法后，对象版本索引与事件条数**一个都没变** |
| 反馈 → 修订 | HTTP：无对象 → **400 + 澄清 + 4 个候选**；指定对象 → **200**、`owner=reasoning`、`rounds_used=3`，目标命题 **v1 → v2**（修订而非新建） |
| 派生不建引擎 | 把引擎打成一构造就抛错后 `POST /api/research/fork` → **200**、新问题可打开、`reused_verifications == []` |

### 三、验证基线

| 命令 | 结果 |
|---|---|
| `pytest -q`（离线全量） | **1146 passed, 1 skipped**（起点 1082） |
| `ruff check --select F,E9 src/ tests/` | All checks passed |
| `npx tsc --noEmit` / `npx vitest run` | 0 error / 256 passed（20 files） |
| `pytest tests/test_browser_web_flow.py`（真实 Chromium） | 9 passed |

### 四、仍未闭合

1. **G01 / P0 仍未做**：`server.py` 只剩 **1 处** `TheoryEngine` 实例化
   —— `/api/research/{p}/feedback` 对**没有团队运行状态**的问题仍走引擎（迁移开关）；
   另有 CLI `--mode theory` 与 `_resolve_engine`/`DEFAULT_ENGINE`/`SURVEY_ENGINE_ENABLED`、
   `StartRequest.mode`。`research/loop.py` 与 `graph/theory_pipeline.py` 的文件本身也还在。
   本轮把**只读**、**反馈（有团队状态时）**、**派生**三处从引擎里拿了出来。
2. **新颖性对照**仍未在团队里实现（内核函数已有）。
3. **G13 用量边界**未做（用户已明确本轮不考虑，但它是真实模型自主运行的前置）。
4. **R5 清仓**未做；§8.4 前端 8 个验收场景未做。
5. `research/inspection.py` 仍从 `loop` 取三样排序口径（`OBLIGATION_PRIORITY`、
   `_obligation_kind`、`_claim_of`）；`feedback.py` 的候选项分组也依赖存储 kind 名。
   **引擎退役时必须把它们一起搬走**，否则工作台会跟着引擎消失。

## 10.8 上一轮（2026-10-04 第三轮：唯一出版层 + 可反驳路径 + 多订阅者事件流）

> 继续按 §9 的 R2/R3/R4 推进。**仍未删除 `TheoryEngine`**（原因见 10.7 与本节第四条）。

### 一、本轮的四个切片（都有可失败回归）

| 切片 | 问题（已核实） | 本轮处置 | 回归 |
|---|---|---|---|
| **G17 出版层（唯一渲染）** | 交付包**只有 Markdown**：`rag/{theory_render,publication_render,latex_render}` 三套排版入口各自解析一种表示，"预览/Markdown/PDF 同源同版"只能对其中一条成立；交付等级因此永远封顶在"论文草稿" | 新增 `publication/render_latex.py`（**只接受唯一文稿 IR** 的 LaTeX 渲染器：转义/前言/表格/`thebibliography`；`\cite{refN}` 只对参考文献表里真有的条目发，杜绝自造悬空引用）与 `publication/compiler.py`（编译实现从 `rag/latex_compiler.py` 迁来，旧模块改为**转发**，无第二份实现）；`rag/latex_render.py` 的前言与转义改为从 `publication.render_latex` 取用；`TeamSession.export()` 渲染 `.tex` →**真编译**→ 用编译事实**重评**交付等级并回写 manifest | 新增 `tests/test_research_commit_and_closure.py::test_latex_render_uses_the_same_ir_and_has_no_dangling_references` / `::test_team_delivery_writes_markdown_latex_and_pdf_from_one_source`；`test_team_session.py` 的等级断言从"只能是草稿"收紧为"说完整论文就必须真有编译好的 PDF" |
| **可反驳路径（§5.4 反例搜索）** | 团队没有反例搜索：命题**不成立**时只会得到 unsupported，结论停在"受阻"——验收矩阵要求的"可证明/可反驳各一例"在团队里走不到 | `verification_service` 增 `check_counterexample()`（判定用内核 `counterexample_verdict_for`：只有**可回代反例**才算反驳；没找到反例只记有限测试证据）；`ReasoningAgent` 对可编码命题补一条 `refute` 义务（`required=False`）；`claim_state_for` 改为**反例不受 `required` 过滤** | `test_research_commit_and_closure.py::test_refutation_is_not_filtered_by_required_flag` / `::test_unclosed_counterexample_search_does_not_block_a_proven_claim` / `::test_team_decides_provable_and_refutable_claims`（`x**2>=0` → supported；`x**2>=x` → refuted 且带机器反例） |
| **G15 多订阅者事件流** | `session_events()` 回放后仍 `session.events.get()` —— **两个页面互相抢事件**；`connected`/合成终止帧用 `current_seq+1`，**占用了下一条业务序号**（实测：日志最大 6，帧却发 `id: 7`），于是下一条真实事件被客户端当成重复丢掉 | `EventLog` 增条件变量与 `wait_for(cursor)`；SSE 改为**每订阅者自带游标读持久日志**（不再消费共享队列）；`connected`/心跳/合成终止帧**不带业务 id**；缺口如实发 `event_log_truncated`；断线后继续等待（"断开连接"不等于"研究停止"） | 新增 `tests/test_sse_subscribers.py`（3 项，**真并发**双 SSE 连接：两端事件集合相同、带 id 的帧必须落在真实日志序号内、从中间游标只收到之后的事件） |
| **顺带修掉的形式化缺陷（3 个，都导致"可判定题变成不可判定"）** | ① `extract_design_params()` 不认标准记号 `t-(v,k,λ)`；② 量词段后的"变量列表+连接词"没剥掉，`lhs` 解析成 `"x 都有 x**2"`；③ 题面尾部的指令（"并给出严格证明"）留在右端，`rhs` 变成 `"0, 并给出严格"` | 分别补上记号抽取（`2-(211,15,1)` 与"设计/区组+`(v,k,λ)`"两种写法）、连接词切分（只在连接词后仍有内容时才切）、尾部指令正则（允许"并/请/再 + 给出/说明 + 严格/完整 + 证明/反例/…"）；另把反例探测网格从**纯整数**扩到有理数（`x**2>=x` 的反例是 `x=1/2`，整数网格上该命题处处成立） | 上述可反驳/可证明用例全部走这条链路；`test_research_derivation` / `test_reasoning_kernel` / `test_theory_mode` 等 128 项回归不变 |

### 二、本轮的端到端事实

| 场景 | 结果 |
|---|---|
| 离线 `problem2.md`（团队路径） | `status=completed`、交付等级 **完整论文**、`manuscript.md` + `manuscript.tex` + **`manuscript.pdf`（66 KB，xelatex 编译 ok）**、`llm_calls=0` |
| 离线"可证明" | `x**2 >= 0` → 命题 **supported**（`symbolic_check`、义务 closed、核验记录 passed） |
| 离线"可反驳" | `x**2 >= x` → 命题 **refuted**（义务 refuted、机器反例 `{"x": "1/2"}`、记录 failed） |
| 真实模型（上一轮已验） | `llm_calls=3`、成本 $0.0336、19 个交付文件；本轮出版层使其同样能产出 `.tex`/`.pdf` |

### 三、验证基线（本轮实测）

| 命令 | 结果 |
|---|---|
| `pytest -q`（离线全量） | **1118 passed, 1 skipped**（起点 1082；本轮新增 3 + 4 + 3 项用例） |
| `ruff check --select F,E9 src/ tests/` | All checks passed |
| `npx tsc --noEmit` / `npx vitest run` | 0 error / 256 passed（20 files） |
| `pytest tests/test_browser_web_flow.py`（真实 Chromium） | 9 passed |

### 四、仍未闭合

1. **G01 / P0 仍未做**（同上轮）：引擎里仍有一批团队没有的能力 —— 证据挂接到指定命题
   （`EvidenceLink`）、新颖性对照、快照冻结/续跑/**派生（fork）**、用户反馈→对象级修订、
   `writing_gap_feedback`、对象级指标。本轮把**出版层**与**反例搜索**从清单里划掉了，
   剩下的抽取完才能删文件。
2. **G13 / P1 未做**（按要求暂不考虑预算上限）。
3. **G16 / P1 未做**：`TeamRun` 每次创建空 `TeamLoopState`，`recovery_view()` 只是查询，
   没有重建 brief/plan/results/open_needs/dispatched；`TaskStore.create()` 同键复用不返回旧 identity。
4. **R5 清仓未做**：`pyproject` 项目名、README 的 `main_chat`/`gui`/模式切换、
   `protocol.py` 旧角色映射、`StartRequest.mode`、`--skip-retrieval`/`--max-revisions` 去向。
5. **真实模型的引用纪律**：实测模型仍会在正文写 `[1][2][3]`（无来源可引），出版门槛
   如实拦下并把等级降为"论文草稿"；这说明"渲染时只按来源清单分配编号"这条下游约束
   还需要更强的实现（当前只做到"标出并降级"，没有改写模型文本）。

## 10.7 上一轮（2026-10-04 第二轮：团队形式化闭环 + 唯一提交口 + 交付门槛 + 图表/前端收敛）

> 依据《智能体团队合并计划》§9 的 R1（G08/G12/G13）与 R2 前段（G03/G06），外加 R3 的
> G18 与 R4 的 G19。**本轮没有删除 `TheoryEngine`**（原因见"四、仍未闭合"第一条）。

### 一、本轮修掉的问题（都有可失败回归）

| 缺口 | 问题（审计复现） | 本轮处置 | 回归 |
|---|---|---|---|
| **G03 / P0** | 请求绑定时机错误：`start_session()` 先 `_make_session()`（已建 `TeamRun`）后填 `session.request`/`run_id`；`TeamApp.stream()` 不消费初始字典里的研究身份与资料；附件未进团队 | 入口改成固定顺序：**规范化输入与文件 → 校验资料授权 → 分配身份 → 创建 run/session → 注入同一输入快照 → 启动**。新增 `_normalized_input()` 作为**唯一**输入快照（`initial_state`/`session.request`/团队装配都取自它）；`_make_session(..., request=)` 在建图**之前**落位请求；`_build_team_app()` 现在把附件清单与附件正文一并交给团队 | `test_team_wiring_and_identity.py::test_http_identity_reaches_the_team_and_its_research_store`（一路核对到团队持有的研究库）/ `::test_team_entry_consumes_attachments_and_source_policy`；`test_session_entry_contract.py` 的旧断言"团队拿不到 project/problem"已按新契约改写 |
| **G06 / P0** | 团队形式化路径未闭环：内核路径把计划/推导压成一条 claim 候选，未提交义务/推导/验证对象；`KIND_MAP` 里根本没有 `obligation`/`verification`，团队写的义务被登记层当"未登记种类"整批丢掉 | 新增 `research/verification_service.py`（**核验服务**：工具核验 / 规则核验 / 独立审查三分派，产出 `VerificationRecord` + 义务处置）与 `research/formulation.py`（**形式化服务**，从旧引擎按职责抽出）；`ReasoningAgent.formal_closure()` 走完"命题 → 义务 → 工具核验 → 结构化记录 → 状态归并"，全部作为**候选**提交；`KIND_MAP` 补齐 `obligation`/`verification`/`gap`；命题状态判据抽到 `reasoning_kernel.claim_state_for`（引擎与团队共用同一份，差异测试把守） | 新增 `tests/test_research_commit_and_closure.py`（14 项，含状态归并三态与"候选不得自报等级"）；`test_team_run.py::test_math_request_uses_the_formal_derivation_kernel` 收紧为"必须提交义务候选" |
| **G08 / P0** | 事务与 read-set 未接入：`TaskStore.commit_result()` 已有能力，团队走 `finish()` 后逐条 `put()` | 新增 `research/commit.py::ResearchCommitService` —— **唯一提交口**：校验（角色可写种类）→ read-set 检查 → 丢弃候选自报的科学等级 → 按核验记录重算义务处置 → 按内核判据重算命题状态 → **一个事务**提交任务终态/对象/事件/幂等收据。`TeamRun._persist_result()` 改走它 | `test_research_commit_and_closure.py` 的"过期不合入""重放不产生第二个对象""越权种类在提交口被拒"；另修一个**真实缺陷**：`submit_step` 给同一步的每条事件都挂同一个幂等键，第二条就撞唯一索引并整步回滚 |
| **G12 / P1** | 角色跑过即被当成交付依据：`_missing_roles()` 把 partial 也算满足；导出未传研究/出版门槛；`TeamApp.stream()` 吞导出异常；达到轮次上限也不走统一收尾 | 新增 `research/delivery.py::assess_delivery()`（研究有效性 + 论文表达 + 出版完备三道门槛，`classify_deliverable` 定级）；`TeamRun._finish()` 在交付时按门槛复核，未过一律 `completed → partial` 并把理由写进未决报告与 `stop_reason`；`TeamSession.export()` 抛 `ExportError`（可恢复）；`TeamApp.stream()` 如实发出 `research_team_export_failed`；轮次上限出口也走同一 finalize；`_DELIVERABLE_ROLES` 与"要不要派写作/审阅"改由同一张表决定（补上"要求写作却从不派工"） | `test_research_commit_and_closure.py::test_export_failure_is_a_recoverable_error_not_an_empty_package` / `::test_run_status_completed_equals_delivery_gate_passed`；另修 `delivery_gate` 一个**真实缺陷**：0 条命题 + 只有骨架正文时仍报"论文草稿" |
| **G18 / P1** | 图表路径未隔离（多 run 互相覆盖）、校验问题记录后仍渲染、`input_versions` 为空、公式直接 `parse_expr`（实测可执行任意 Python） | 见下"三、并行切片" | `tests/test_figure_artifact_service.py`（26 项） |
| **G19 / P1** | 前端三份状态并存（`air-global.research` / `app.currentResearch` + ID 镜像 / `team-controller` 的 reducer store），`syncSelectionFromLegacy()` 表明新 store 不是唯一权威 | 见下"三、并行切片" | `src/web/tests/single-source-of-truth.test.ts` + `tests/test_browser_web_flow.py` 新增真实 Chromium 用例 |

**顺带修掉的真实缺陷（都因此前"跑不到"而从未暴露）**：

1. `AgentBase.partial()` 不收 `needs` 参数，而写作角色在"有未决项"时必传它 ——
   结果是**只要稿件有未决项，写作任务就整条 `failed`**，交付判定只看到
   "交付形态要求未满足"，真正原因埋在 `failure_reason` 里；
2. `TeamRun` 建 `SupervisorAgent(llm=None)`：G04 把"主控真调模型"修进了
   `SupervisorAgent.brief()`，但**装配层一直没把模型交给主控** ——
   角色模型被解析出来了却从未使用（这正是审计用 `llm_calls == 0` 判断的那条；
   实测修复前 `llm_calls=0` 而 `tool_calls=38`）。现由 `bootstrap.role_llm_factory()`
   统一解析并计入运行用量（主控用量并入 `outcome.usage`）；
3. 达到轮次上限时 `step()` 直接返回 `False` 而不 `_finish()` —— 运行停在
   `status="queued"`、没有停止原因、没有交付评估（`TeamRun.run()` 有兜底，
   `TeamSession.run_to_completion()` 没有，于是 CLI 路径就是这种状态）；
4. 快照的引用闭包缺 `verification`/`attempt`/`evidence_link`：交付门槛因此报
   "结论缺少有效验证记录"，而记录就在库里（实测 claims=1, verifications=0）；
5. `writing_map` 在团队路径上从未计算，导致"结论明明写进了正文"仍被判
   "主要结论未映射到正文位置"。
6. `extract_design_params()` 只认叙述性措辞（"每轮恰好…"），不认标准参数记号
   `t-(v,k,λ)`：一个**有唯一确定答案**的判定题因此被当成研究方向并停在未决。

### 二、验证基线（本轮实测）

| 命令 | 结果 |
|---|---|
| `pytest -q`（离线全量） | **1108 passed, 1 skipped**（本轮起点 1082） |
| `ruff check --select F,E9 src/ tests/` | All checks passed |
| `npx tsc --noEmit` | 0 error |
| `npx vitest run` | **256 passed / 20 files**（起点 227 / 19） |
| `pytest tests/test_browser_web_flow.py`（真实 Chromium） | **9 passed** |
| `audit_probes.py` | G02/G04/G05/G07/G09/G10/G11/G14 全部 `FIXED` |
| **真实模型端到端（离线之外）** | `problem2.md` 走团队：`status=completed`、交付等级**论文草稿**、`llm_calls=3`、tokens 43,929、成本 $0.0336、模型 `deepseek-v4-flash`、19 个交付文件 |
| 离线 `problem2.md` | 完整论文包、`llm_calls=0`、命题状态由判定层从 `design_necessity` 证书算出（**2-(211,15,1) 不存在**，引 Bruck–Ryser–Chowla） |

### 三、并行切片（G18 / G19，各自独立完成并验证）

- **G18 图表产物服务**：新增 `rag/figure_formula.py`（AST 白名单解析 + 受限求值，
  指数上限，无 `eval`/内建函数）；`agents/figures.py` 成为唯一校验式 FigureSpec 服务，
  目录按 `figures/<run>/<task>/v<n>/` 分层，校验（来源登记/单位/模型域/权限）**先于**渲染，
  拒绝时不落盘并返回结构化失败；`rag/figure_llm.py` 的**自由脚本路径整段删除**
  （旧 `parse_expr` 路径实测可执行 `open(...)` 写文件）；`input_versions` 与真实
  `ArtifactRef`（含 sha256）补齐。
- **G19 前端单一状态**：`state/research-store.ts` 成为唯一可写状态（进程级单例 +
  reducer + selector），`current-research.ts` 降为只读冻结投影，`window.AIR.research`
  改成无 setter 的 getter；`syncSelectionFromLegacy`/`legacyResearch`/`app.currentResearch`
  与 6 个 ID 镜像/`workbenchData` 等全部删除；新增真实 Chromium 用例核对
  `#projid` = `window.AIR.research.projectId` = 团队 store 的 `selection.projectId`。

### 四、仍未闭合（**不得当成已完成**）

1. **G01 / P0 未做（有意）**：本轮**没有**删除 `TheoryEngine`。计划 §9 的顺序要求
   "先让团队覆盖已支持任务 → 逐组抽取旧引擎能力 → 删对应旧分支 → 最后删文件"，
   而引擎里仍有一批团队尚未具备的能力：证据挂接到指定命题（`attach_evidence` +
   `EvidenceLink`）、模型提议/比较与选择、新颖性对照、验证方案生成、反例搜索、
   快照冻结/续跑/派生（`submit_feedback`/`fork_from_snapshot`）、LaTeX/PDF 出版层。
   现在删文件会**净损失**这些能力，因此下一步是在团队路径上逐个补齐后再删
   （`research/loop.py`、`graph/theory_pipeline.py`、`main.py::_run_theory_mode()`、
   `server._resolve_engine()`/`DEFAULT_ENGINE`/`SURVEY_ENGINE_ENABLED`、
   `StartRequest.mode`）。
2. **G17 / P1 部分闭合**：稿件 IR 层已统一（`finalise_manuscript()` 在 IR 层补齐摘要/
   关键词/参考文献章节/论断锚点，模型起草与确定性起草走同一套结构契约；确定性路径
   与模型路径都已能产出"论文草稿"）。**未做**：`rag/{theory_render,publication_render,
   latex_render}` 与 `TheoryEngine` 的出版层仍未迁到 `publication/render_*`，
   因此交付包目前**只有 Markdown**，没有 `.tex`/`.pdf`（交付等级因此封顶在"论文草稿"）。
3. **G13 / P1 未做**（按要求本轮不考虑预算上限）：常规入口仍未注入 `RunBudget`，
   `BudgetedLLM.__getattr__` 仍直接透传 `bind_tools`。
4. **G15 / G16 未做**：事件多订阅（`session-stream` 的游标按 thread、store 的
   `cursorBySession` 按 session，两套 key 空间）与"任务落盘 ≠ 恢复团队"
   （`TeamRun` 每次创建空 `TeamLoopState`，`recovery_view()` 只是查询）。
5. **R5 清仓未做**：`pyproject` 项目名仍是 `langgraph-survey-pipeline`；README 仍写
   `main_chat`/`gui`/模式切换；`protocol.py` 旧角色映射、`StartRequest.mode`、
   `--skip-retrieval`/`--max-revisions` 的处置未逐项确认。
6. **真实模型的引用纪律**：实测模型仍会在正文里写 `[1][2][3]`（无来源可引），
   出版门槛如实拦下并把等级降为"论文草稿"。这是**系统按设计拦住**，但也说明
   写作提示词的引用约束还需要更强的下游校验（例如渲染时只按来源清单分配编号）。

## 10.6 上一轮（按 2026-10-04 重构版合并计划执行：安全/状态底座 +

> 新计划（重构版，撤销"长期保留两种研究引擎"）落地记录。**本轮只做 §9 的 R0/R1 切片**：
> 先把"越权/状态/工具契约"这类**正确性与权限**问题修掉，再谈让团队接管研究（R2）。
> 依据：`audit_probes.py` / `audit_sources.py`（审查方放在仓库根的两个只读探针）。

### 一、本轮修掉的问题（都有可失败回归）

| 缺口 | 问题（审计复现） | 本轮处置 | 回归 |
|---|---|---|---|
| **G07 / P0** | 校验拒绝**不阻止落库**：`finalize()` 只把结果降为 partial，候选仍留在 `proposed_changes` 里，投影照样登记 —— 一个 evidence 角色提交的 claim 被判违规后，库里仍出现 `status=supported` | `finalize()` 改为**候选级隔离**：违规候选移入新增的 `AgentResult.rejected_changes`（带理由与载荷字段摘要），只有合规候选才可能被登记。结果仍降级为 partial（不把违规那次显示成干净完成） | `test_agent_runtime.py::test_rejected_candidate_never_reaches_authoritative_storage`（直接查库断言越权对象不在） |
| **G09 / P0** | 授权只按角色，不按本次资料策略：`grant_for()` 未与 `source_policy` 求交 —— `user_kb` 任务照样拿到 `tools:search`（"只用本地库"却能联网外搜） | 新增 `SOURCE_POLICY_SCOPES`（策略 → 允许的外部取数能力）；`grant_for()` 按 **角色能力 ∩ 用户授权 ∩ 任务范围**签发，并把 `source_set_ids` / `allowed_paths` 写进令牌，附 `allows_source_set()` / `allows_path()`（路径做规范化前缀比较，`../` 逃逸被拒）。未声明策略按**最保守**处理 | `test_agent_runtime.py::test_search_capability_requires_declared_source_policy` 等 |
| **G10 / P0** | 数学工具契约错误：工具传 `operation="check"`，而 SymPy/Z3 的 `OPERATIONS` 里没有 `check` → 每次返回 `unsupported` 却像"执行过"；统计工具传 `column` 而适配器读 `outcome_col` | 工具改用**真实操作**（`prove_identity` / `prove_inequality` / `check_satisfiable` / `describe`）；参数按适配器实际读取的名字传（`lhs`/`rhs`/`relation`/`assumptions`、`outcome_col`、Z3 变量声明用映射）；关系符自动拆分（`"x+1 == x+2"` → lhs/rhs）；返回值保留状态、证书与输入 hash，并区分"不可用/不支持"与"证伪" | `test_agent_tools_contract.py`（13，**不 mock 适配器**：静态核对操作/参数名 + 真实跑真命题/矛盾约束） |
| **G11 / P0** | 导出数据失真/缺失：`_model_of()` import 一个**不存在**的 `ModelRecord`（异常被裸 `except` 吞掉，模型一个都没进包）；`_evidence_of()` 丢 `locator→location`、`relation→support`，并 `pop` 掉 `version` → 定位为空、支持关系退化成 insufficient、版本回到 1 | 新增显式字段差异表与支持关系映射；`version` 保留；`_model_of` 改用真实类 `ResearchModel`；快照汇集**引用闭包**（新增 obligation/assumption/definition/validation_plan）；单条坏数据仍可跳过但**必须留下**"哪一条、为什么"（写入缺口列表 + 事件），存储读取失败不再被当成"该类为空" | `test_snapshot_export_mapping.py`（14） |
| **G14 / P0** | 跨运行混用：`plan()` 产出的任务 `run_id` 全为空；`team_projection()` 用 `not t.get("run_id")` 放行；对象查询读项目全部最新对象 —— 同项目两次运行互相串 | 运行身份由 `TeamRun.prepare()` **统一注入**计划的每条任务；投影端点只接受 `run_id == 本次运行` 的任务；对象写入时记录 `_scope{project,problem,run}`，`object_rows(run_id=...)` 按运行裁剪，`rows()` 默认裁剪、`project_rows()` 才是项目级视图 | `test_team_wiring_and_identity.py`（10） |
| **G05 / P0** | 推理先于证据：首轮顺序 `reasoning, reasoning, evidence`，且推理任务依赖为空 —— 等于**凭记忆推导** | 依赖表新增 `reasoning: (modeling, evidence)`：需要依据的推理必须等建模与证据就绪。**例外**（计划明确允许）：全部推理子问题都是 `existence_proof`（纯自足证明）时不加证据依赖，省下一轮无谓检索；判据保守 —— 只要有一条需要依据就整体等待 | `test_dispatch_dependencies.py`（8，含"证据先于推理可运行"与例外/保守两侧） |
| **G04 / P1** | 主控仍是规则分类器：注入 spy 模型后 `llm_calls == 0`（保存了 llm 却从不调用） | `SupervisorAgent.brief()` 新增模型提议路径：模型可用时**真的调用**并采用它给的子问题；输出**必须过校验**（未登记的类型/角色丢弃或按类型归位，主控不得成为 owner，条数上限 8）；不可用/不合规时退回规则展开并**如实标记降级**（`unknown_fields`），不让"规则跑通"看起来像"模型理解过任务" | `test_supervisor_model_planning.py`（11，含模型抛错/坏 JSON/越权角色/任务爆炸/降级标记） |
| **G02 / P0** | 团队入口**没有模型工厂** → `llm_available()` 恒为 False，主控与七类角色全退化成规则模板却照样产出"完整"交付包 | `server._build_team_app()` 注入 `_team_llm_factory`（角色 → 模型配置键：主控 coordinator / 审阅 reviewer / 推理 theorist / 证据 cheap…）；`THEORY_LLM=0` 仍是显式离线开关。**顺带修掉一个真实缺陷**：`llm_available()` 原先只判断"工厂是否存在"，而工厂可返回 None（离线），于是角色以为模型可用、调用抛异常再退回确定性实现——症状被掩盖成"跑通了"；现在按角色**实测并缓存**，取模型失败落 `llm_unavailable` 事件 | `test_team_wiring_and_identity.py`（含"工厂失败必须可见"与"离线开关被尊重"） |

另修一处**顺带发现**的类型丢失：投影登记时 `source`/`card`/`case`/`dataset` 都归入 `evidence`
存储族（这是有意的，为了查询与版本化只有一处实现），但**候选自己的种类**此前彻底消失。
现在保留为 `_candidate_kind`（族用于存储，该字段用于语义），符合 §6.1"不能把 case/dataset
无区别映成 evidence 丢类型"。

### 二、仍未闭合（**不得当成已完成**）

`audit_probes.py` 现在逐条打印状态（**G02/G04/G05/G07/G09/G10/G11/G14 均为 `FIXED`**）。
剩余项按计划 §9 属于后续切片：

| 缺口 | 现状 | 计划归属 |
|---|---|---|
| **G01 / P0** | 仍是双引擎：`server._resolve_engine()` + `DEFAULT_ENGINE`/`SURVEY_ENGINE_ENABLED` + `main.py --mode` / `_run_theory_mode()` | R2：所有科研请求进同一 TeamRun，删除引擎选择与分流测试 |
| **G06 / P0** | 团队形式化路径未闭环：内核路径把计划/推导压成 claim 候选，未提交完整义务/推导/验证对象（`research_graph._persist_result()` 只登记投影） | R2：**这是删除 TheoryEngine 的前置** —— 团队必须能自己走完"义务→工具核验→结构化记录→状态归并→依赖失效" |
| **G08 / P0** | 事务与 read-set 未接入：`TaskStore.commit_result()` 已具备能力，但团队走 `finish()` 后逐条 `put()` | R1 |
| **G12 / P1** | 角色跑过即被当成交付依据（partial 也算满足；导出异常被吞） | R1/R3 |
| **G13 / P1** | 用量边界未接通：常规入口未注入 `RunBudget`；`bind_tools` 可能绕开记账 | R1 |
| **G15–G19 / P1** | 事件多订阅、任务落盘≠恢复团队、双 Manuscript/IR、图表路径未隔离、前端多份状态 | R4/R3 |

**本轮明确没有做的事**（避免误解）：没有删除 `TheoryEngine`，没有改前端状态层，
没有跑真实模型任务。`test_engine_boundary_contract.py` 里"保留双引擎"的断言**保留原样**，
等真正删除 `TheoryEngine` 的那个提交里一并替换（计划 §5.6.3）。

**探针本身也修过两处**（否则会给错结论）：G14 原先直接调 `supervisor.plan()`，绕过了
注入身份的运行时，于是永远报"空 run_id"；G02 原先检查裸 `TeamRun`（本来就没有模型，
模型由装配层注入）。两处都改成走**真实入口**后再判。

### 三、验证基线

| 命令 | 结果 |
|---|---|
| `pytest -q`（离线全量） | **1082 passed, 1 skipped**（本轮新增：工具契约 13 + 导出映射 14 + 装配/身份 10 + 派工依赖 8 + 主控模型 11） |
| `npx tsc --noEmit` / `npx vitest run` / 真实 Chromium | 0 error / 227 passed / 63 passed |
| `ruff check --select F,E9 src/ tests/` | All checks passed |
| `audit_probes.py` | G02/G04/G05/G07/G09/G10/G11/G14 = `FIXED`；其余见上表 |
| `ruff check --select F,E9 src/ tests/` | All checks passed |

## 文档索引（哪份文档说了算）

| 文档 | 作用 | 权威性 |
|---|---|---|
| `AI for Research(AIR)计划书.md` | 2026-10-02 版计划书：P0-1…P2、S1–S4、发布门槛 1–5 | **需求与验收的唯一依据** |
| `AI for Research(AIR)执行进度.md` | 逐次迭代的改动、证据与命令 | 过程与证据的权威记录 |
| `AI for Research(AIR)未完成工作清单.md` | 迁移用：还没做完的事 + 已核实的接入点 | 待办的权威 |
| `AI for Research(AIR)S1命题化交接.md` | S1 的接入点交接（已完成，留作过程记录） | 已被实现覆盖 |
| 本文件 | 面向"看板"的推进情况汇总 | 结论摘要，细节以执行进度为准 |
| `AI for Research(AIR)智能体团队合并计划.md` | 目标架构、功能归属、M0–M5/F0–F5、验收矩阵 | 合并实施的主计划 |
| `audit_probes.py` / `audit_sources.py` | 审查方的两个**只读探针**（离线、内存库、不联网）。`audit_probes.py` 逐条打印 §3 缺口的当前状态（`FIXED` / `STILL BROKEN`） | 缺口状态的机器可查依据；不是生产代码 |

## 10.5 本轮推进情况（彻底合并重构：主文统一给 WritingAgent + Session 拆分 + 统一入口 + §13 前端 + 追溯视图）

依据《智能体团队合并计划》继续实施。**本轮不复跑付费真实任务**；所有证据均为离线命令。

### 一、按阶段完成的工作包

| 阶段 | 状态 | 关键交付 | 证据 |
|---|---|---|---|
| **§13 前端「从本机路径添加资料」** | **完成** | 新建 `web/src/contracts/library.ts`（契约 + 逐行路径解析 + 幂等键）、`library-controller.ts`（**先预览再确认**流程：未扫描不得导入、部分失败不报成功、扫描失败不产生"可导入"假状态）、`views/library.ts`（预览清单按 ok/跳过/拒绝/已截断分组、资料工作区按来源类型分组并显示失效文件）；`index.html` 增路径入口与「资料库」标签页；`styles/team.css` 增样式 | `tests/library.test.ts`（19）、`tests/e2e/library_paths.mjs` + `test_library_path_import_in_real_browser`（真实 Chromium） |
| **契约按域分组 + 长文附录路径删除（第 7 轮）** | **完成** | ① `contracts.ts`(215 行) 拆成 `contracts/{session,research,publication}.ts` + `index.ts` 分组入口（`library.ts` 本就在自己的域里）：会话身份/SSE、问题形式化与工作台行、交付包清单三域各自演进，`contracts.ts` 已删除；manifest 读取判据（文本/二进制/错误三种返回形态 + 按 basename 挑条目）从 `app.ts` 内联代码提为 `pickManifestEntry`/`readManifestBody` 并补 10 项用例。② 删除**默认关闭的长文附录路径**：`src/agents/paper_writer.py`(458)、`src/research/writing_bridge.py`(251) 与 `tests/test_writing_bridge.py`(196) 共 **905 行**；`THEORY_LONG_FORM` 未设时这条路径唯一效果是往 notes 写一句"长文附录未生成"，交付物里从未出现它生成的附录。`protocol.py` 的旧名映射与测试同步收敛（`paper_writer`/`writing_bridge` 不再映射到 writing） | Python **1018 passed**（-14 长文用例 +1 重指向的提示词用例）；前端 **222 passed / 18 files**（+10 manifest 判据） |
| **追溯视图拆分（第 8 轮）** | **完成** | `views/paper.ts`(340 行) → `views/paper/{types,traceability,figure-gallery,numbering}.ts` + `views/paper.ts` 分组入口（§9.6 点名的三块各成一模块；公共类型与转义放 `types.ts`，避免三个子视图互相反向依赖）。**拆分过程中真的改坏过两处用户可见的东西**（状态文案 `可反查`→`已追溯`、缺失锚点那条记录把 anchor 丢成空串），当时的用例只断言了其中一句所以两处都"通过"；发现后已逐字还原，并新增 `paper-wording-parity.test.ts`（5 项）把**真的被改坏过的**文案与标记写成窄红线 | `web/tests/paper-wording-parity.test.ts`（5，含"缺锚点记录必须保留 anchor"）；原有 `paper.test.ts`(13)/`paper-tab.test.ts`(8) 不变 |
| **M5 契约闭环（第 6 轮）** | **完成** | 新增两条**跨前后端**的回归：①`tests/test_web_api_contract.py`——前端 `ENDPOINTS` 端点表里的每个路径都必须在**后端真实路由**里存在（含 `include_router` 进来的 `/api/team/*` 与 `/api/library/*`；FastAPI 0.141 把子路由保留为 `_IncludedRouter`，必须递归展开才看得到），且模板代入参数后的 URL 形状必须匹配；②客户端把"非 GET 的方法"显式写进 `VERBS` 表（不再靠测试去猜调用点），测试断言表里每个方法都被后端接受，并**反查调用点**确保没有"发了 POST 却没登记"的端点 | `tests/test_web_api_contract.py`（9）、`tests/test_web_asset_publish.py`（4） |
| **两种研究形态定稿（第 9 轮）** | **完成（有意选择）** | 把"为什么保留两支引擎"从"看起来没合并完"定稿成**设计选择**（合并计划 §15.2.2）：判定层必须零 LLM 且是唯一状态写入点，`AgentTask` 只提交候选不做判断；两者已共用研究对象存储、判定层与推理内核、会话与事件层、写作入口、交付包导出与门槛、预算计量。设计说明写在 `_resolve_engine` 的决策点旁边，并有 6 条用例把守 | `tests/test_engine_boundary_contract.py`（5，含写入口清单 + 旧撰写路径防回流 + 决策点说明存在性） |
| **M5 产物原子发布（第 6 轮）** | **完成** | `vite.config.ts` 的发布插件改为**先写新、后删旧**：同代码先落 `.tmp` 再原子改名，旧哈希产物在新页面就位后才清理。原实现"先删旧再拷新"有一个真实窗口——用户正好在这期间刷新，`index.html` 会引用一个不存在的脚本/CSS（表现为"样式没了/按钮全不响应"） | `tests/test_web_asset_publish.py`（4：引用产物都存在且非空 / 无死产物 / 发布顺序只能是先写后删 / 落盘产物可独立部署） |
| **M5 取数入口收敛（第 5 轮完成）** | **完成** | `web/src/api/research-client.ts`：**唯一** HTTP 出口 + `ENDPOINTS` 端点表（URL 只在这里出现）；三条语义写死: **自动重试只限幂等请求**（GET 与带幂等键的变更，否则一次超时重试可能把同一动作施加两遍）、所有请求接受 `AbortSignal`、4xx 把后端 `detail` 如实抛出；支持 multipart 表单（不手写 Content-Type，边界交给浏览器）。**全库只剩这一个 `fetch`**（`grep` 核对）：`app.ts`/`session-controller`/`team-controller`/`library-controller`/`intake`/`session-stream` 全部切过来；`team-controller`/`library-controller` 的私有端点表改为引用共享端点表；SSE 端点也取自端点表（`EventSource` 本身仍必须用它）。旧的 `research-api.ts` 被取代并**删除** | `web/tests/research-client.test.ts`（14，含两条防回流断言：六个模块不得出现 `fetch(`，端点字符串只能在 `api/` 层——注释不计） |
| **团队会话引擎** | **完成** | `TeamRun.prepare()`/`step()` + 显式 `TeamLoopState`（主控循环可**逐步驱动**，`run()` 与逐轮走同一实现）；新建 `src/graph/team_session.py`（`TeamSession`：逐轮事件、澄清 interrupt、`submit_response()` 续跑、交付包导出；`TeamApp`：适配会话驱动的 `stream/get_state`）；新建 `src/research/intake.py::is_survey_request()`（保守判据，形式化意图优先）；`server._resolve_engine()` 把**综述型请求**分流到团队引擎、形式化请求仍走理论引擎 | `tests/test_team_session.py`（7）、`tests/test_team_run.py::test_stepwise_loop_matches_a_single_run`（差异测试）、`tests/test_unified_entry_survey_gap.py`（3） |
| **M4 全局资源归属** | **完成** | **stdout**: 不再替换全局 `sys.stdout`，改为按线程派发的 `_StdoutDispatcher` + 桥接器注册表（此前后启动的会话会把先启动的桥接器当成"原始 stdout"包起来 → A 的日志转发给 B，且断开一个会话会让另一个彻底丢日志）；**用量**: `cost_tracker` 增会话级 `ContextVar` 绑定与 `tracker` 转发代理（每个 run 的成本报告不再包含历史累计）；**图表目录**: `figure_generator` 增 `figure_scope_bound(run_id)`，图表落到 `figures/<run>/`（文件名只是局部序号，共用目录会互相覆盖）；`latex_render` 增 `\graphicspath` 并把图表路径解析改为按**调用时** `OUTPUT_DIR` | `tests/test_resource_ownership.py`（4）、`test_publication_layout.py::test_graphicspath_declared_so_run_scoped_figures_are_found` |
| **M4 会话事件模块** | **完成** | `src/sessions/events.py`（`EventLog`：全序序号、有界可重放日志、按游标 `replay()` 并**如实报告缺口**、事件→会话消息映射与连续重复 interrupt 去重）；SSE 在日志截断时增发 `event_log_truncated`（前端按未知事件 → resync 重新加载投影） | `tests/test_session_events.py`（9） |
| **M4 Session 拆分** | **完成** | `server.py` **2055 → 1548 行**；`Session` 与停止语义 → `src/sessions/controller.py`，按线程日志桥 + 会话级用量 + 运行身份作用域 + 停止哨兵 → `src/sessions/runtime.py`，会话注册表与持久化 → `src/sessions/store.py`；`server.py` 只保留端点与装配（`_make_session()` 是唯一装配入口），**全库各实现只有一处**（已 grep 核对 `class Session`/`StdoutBridge`/`persist_session`/`shutdown_sessions`/`final_summary`）。公共 API 保持：`server.Session`/`server.SESSIONS`/`server.shutdown_sessions()`/`server._persist_session`/`server._run_session`/`server._STOP` 等名字仍可用 | `tests/test_session_lifecycle.py`（15，关闭时序/并发/停止收尾；重构前后同一命令均 48 passed，即行为零变化） |
| **M2 剩余：判定层边界** | **完成（研究侧）** | `reasoning_kernel` 增**纯映射** `obligation_disposition_for(result, obligation) -> ObligationDisposition`：把"工具结果 → 义务处置"（关闭/反驳/未决、是否解除记录 stale、是否写路线失败、估计类失败的独立语义）从 `loop._act_check_step` 抽出，**不写状态**；引擎改为"执行工具 → 调内核 → 落盘 + 领域化收尾"，判定层仍是唯一状态写入点 | `test_reasoning_kernel.py` 新增 5 项（含与引擎 `_to_validation_status` 的**差异测试**） |
| **M3 主文统一（退役双正文）** | **完成** | `agents/writing.py` 增**唯一正文生产入口** `write_main_manuscript()`：有 LLM/运行时 → WritingAgent 起草（逐段带依据对象 id，块 id 写入正文作追溯锚点）；离线、预算耗尽、调用失败或输出不可解析 → 由**冻结快照**确定性起草，输出与旧渲染器**逐字一致**，降级原因写进 note。`theory_writer.run_theory_writing()` 不再自己组装正文，改为委托该入口；`theory_pipeline` 通过 `_writing_agent_inputs()` 注入 `AgentTask`/`ContextPack`/`AgentRuntime`（预算与取消在写作这步同样生效）。`package._traceability` 增 `checked` 字段区分"核对正文本体"与"仅核对快照" | `tests/test_main_text_writing_agent.py`（7，含**差异测试**：替换唯一入口即改变理论主文；离线逐字一致；降级原因；以 `problem2.md` 为题的离线整链 `llm_calls=0` 且 PDF 产出） |
| **M4 旧检索缓存清除（用户决策）** | **完成** | `pipeline_cache.clear_all_caches()`：清空按主题的旧缓存与按 run 分片的新缓存，逐条报告；服务器启动时执行一次（旧格式**不迁移、不保留**）。同时把缓存目录解析改为 `cache_dir()`（**调用时**读 `DATA_DIR`），修掉"改了数据根后清理仍指向旧目录"的真实缺陷 | `tests/test_server.py::test_clear_cache_endpoint`（判据改为"缓存确实为空"，并补齐 `config.DATA_DIR` 隔离） |
| **人工复核改为"只看最终产出"（用户决策）** | **完成** | `evals/rubric.md` 从"领域人审表（含签字栏）"改写为**交付物复核清单**：复核对象是交付包里的文件（§0 列出来源），系统**不读取**该清单、也不因未勾选而拒绝交付；删除签字表与"送审前另行准备封存材料"的前置要求。期望答案仍不得进入子智能体上下文（防泄漏，与审批无关） | `tests/test_evals_contract.py`（17） |
| **M5 前端拆分（`app.ts` 只装配）** | **完成** | `app.ts` **2137 → 994 行**；新增 `session-controller.ts`、`features/intake/controller.ts`、`events/session-stream.ts`、`views/research-overview.ts`、`views/interrupt-cards.ts`、`views/project-navigation.ts`、`views/research-input.ts`。硬约束保持: 无内联事件处理器（全部 `data-action` + 文档级委托）、`window.AIR`/`AIRTeam`/`AIRLibrary` 兼容转发保留、唯一状态仍是 `window.AIR.research`、未加 `@ts-nocheck` | `research-overview.test.ts`(16)、`session-controller.test.ts`(13)、`session-stream.test.ts`(8)、`intake-controller.test.ts`(7)、`paper-tab.test.ts`(8) |
| **M5 论文/公式/图表追溯（F3–F5）** | **完成** | `views/paper.ts`(312)：三类追溯状态、三个门槛各自独立、来源索引、图表归属、"没有编号数据就不自己编号"；`index.html` 增「论文与追溯」标签页；`refreshPaperTrace()` 读交付物清单 + `manifest.json`，任一步失败**追加原因并保留已加载内容** | `paper.test.ts`(13)、`paper-tab.test.ts`(8)、`e2e/web_flow.mjs` 面板步骤 |
| **M5 契约类型去重** | **完成** | `RunMode`/`RunStatus` 统一到 `contracts.ts`（`current-research.ts` 与 `state/research-store.ts` 不再各写一份）；`views/paper.ts` 不另立平行 manifest 契约 | `npx tsc --noEmit` |
| **M5 统一入口（无模式）** | **完成（本轮）** | `StartRequest.mode` 默认值改为空串，`start_session` 用 `DEFAULT_ENGINE = "theory"` 决定引擎；界面不再提供模式选择（前端迁移另见下）。旧客户端显式传 `mode` 仍可用，但不再是用户可见的选择 | `tests/test_unified_entry_http.py`（`problem2.md` 题面走真实 HTTP 入口：不带 mode 启动、事件序号递增可回放、交付包为完整论文且 `llm_calls=0`、追溯口径明确）、`test_survey_mode.py` 契约用例改写 |

### 二、本轮同时落地的行为改动

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 同时跑两个会话时 A 的日志转发给 B；断开一个会话后另一个的日志**静默消失** | `_run_session` 全局替换 `sys.stdout`，后启动者把先启动者的桥接器当"原始 stdout"包起来 | 全局只挂一个按线程派发的 dispatcher；桥接器按线程查表；解绑只解本线程 |
| 2 | 每个 run 的成本报告都是**历史累计** | `cost_tracker.tracker` 是进程级单例，从不复位 | 会话线程绑定时创建独立 `UsageTracker`（ContextVar，派生线程继承） |
| 3 | 两个会话的图表会互相覆盖 | 图表文件名是局部序号（`taxonomy_0.png`），目录是所有运行共用的 `outputs/figures/` | 图表目录按 run 分层；`\graphicspath` 声明相对/绝对 figures 目录 |
| 4 | 嵌套折叠 `<details>` 里的资料入口"存在但控件用不了" | 嵌套折叠默认收起时子控件不可见（Playwright 与用户都点不到） | 改为**不折叠**的区块（并把这条写进模板注释，避免再犯） |
| 5 | 图表路径写死 `Path("outputs")` 解析 | `config.OUTPUT_DIR` 改了或被测试隔离时不生效，图被判"不存在"后静默跳过 | 按调用时的 `OUTPUT_DIR` 解析 |
| 6 | `tests/test_reasoning_kernel.py` 用了 `pytest.skip` 但没导入 `pytest`（F821） | 静态检查发现（运行时恰好被间接导入掩盖） | 显式导入 |

### 三、验证基线（离线）

| 命令 | 结果 |
|---|---|
| `pytest -q`（全量、离线） | **1023 passed, 1 skipped**（演进：第 1 轮起点 1030 → 第 3 轮删 101 项旧 stage 用例 = 1019（+8 防回流）→ 第 6 轮 +13 契约 = 1032 → 第 7 轮 −14 长文用例 +1 重指向 = 1018 → 第 9 轮 +5 引擎边界 = 1023） |
| `pytest tests/test_browser_web_flow.py -q` | **8 passed**（真实 Chromium；含 §13 路径导入用例） |
| `pytest tests/test_browser_web_flow.py tests/test_web_frontend.py tests/test_workbench_api.py -q` | **50 passed** |
| `cd src/web && npx tsc --noEmit` | 0 error |
| `cd src/web && npx vitest run` | **227 passed / 19 files**（起点 124 / 11 files） |
| `cd src/web && npm run build` | 成功（`dist/assets/index-JNK61YzL.js` + `index-HZ3oLGQN.css`，已复制到 `src/web/assets/`） |
| 离线实跑（真实 Chromium，**统一入口，不带 `mode`**） | 页面加载 → 提交请求 → **工作台出现结论对象** → 反馈控件 → `data-action` 委托生效 → 论文与追溯面板 → 窄屏 → 键盘，全程无 mode |
| 离线实跑（真实 Chromium，§13 路径导入） | 入口**始终可见**且不在折叠高级选项内 → 未扫描时「确认导入」禁用 → 预览列出拒绝项 → 导入"新增 1 / 拒绝 1"且**未报成功** → 原目录内容与 mtime 未变 → 资料工作区归入"本机路径导入"且**不泄露绝对路径** → 解除登记未删除原文件 |
| 离线整链（`problem2.md` 走真实 HTTP 入口） | 不带 `mode` 启动 → 完整论文交付包 → `llm_calls=0` / `cost_usd=0.0` → 事件序号递增可回放 → 追溯检查口径为 `markdown` |

新增用例：M2 判定层 5 + M4 资源归属 5 + 会话事件 9 + 排版契约 1 + 跨项目共享库 1 + 主文统一 7 + 会话生命周期 15 + 统一入口 1 + 综述分流 3 + 团队会话 7 + 逐轮等价 1 + 引擎退役 8 + 过渡契约迁移 5 + 前后端端点契约 9 + 产物发布契约 4 = **81**（Python）；
§13 路径导入 19 + 追溯视图 13 + 前端拆分 44 + 追溯面板接入 8 + 取数入口 14 = **98**（TS）。
（前端 vitest 由 124 → 212；Python 见下方边界说明：删除了 101 项覆盖已退役 stage 机制的旧用例。）

> 跑批注意：`pyproject.toml` 的 `addopts = --basetemp=.pytest_tmp` 是**全工作区共享**的，
> 并行跑 pytest 必须各自带独立 `--basetemp`，否则会互相删临时目录并产生假红。

### 四、诚实边界（不要当成已完成）

- **旧综述图已删除（本轮完成，§15.2.1）**：`src/graph/pipeline.py`(2166 行)、
  `src/gui.py`(428 行)、`src/main_chat.py`(190 行) 与 **101 项**覆盖已退役 stage 机制的
  旧用例一并删除；通用件抽为 `src/graph/node_progress.py`；`test_survey_mode.py` 的
  5 项**引擎无关**会话契约迁移为 `tests/test_session_entry_contract.py`（走团队引擎）。
  语义去向逐条对照与防回流用例见 `tests/test_engine_retirement.py`(8 项)。
  **Python 用例数因此从 1114 降到 1019**：-101 旧机制用例 +8 防回流 +其余；这是删除
  机制的必然结果，每条删除项的语义都有新守护点，不是"删断言换绿灯"。
- **两种研究形态是定稿的设计选择，不是未完成的合并**（详见合并计划 §15.2.2）：
  形式化请求走理论引擎（命题/义务/核验/门槛），综述型请求走团队会话引擎；界面不暴露
  这个选择。**为什么不合并成一支**：判定层必须零 LLM 且是唯一状态写入点，而
  `AgentTask` 的契约是"只提交候选、不做判断"——把理论引擎的动作改造成任务，要么多一层
  无意义转发，要么把判定权交给智能体。两者**已经共用**真正该共用的部分（研究对象存储、
  判定层与推理内核、会话与事件层、写作入口、交付包导出与门槛、预算计量）。
  这一点由 6 条用例把守（内核共用 / 内核不写状态 / 写入口清单 / 旧撰写路径不得回来 /
  设计说明在决策点 / 分流对形式化请求保守），见 `tests/test_engine_boundary_contract.py`。
  **诚实边界**：写入口清单只在代码写出写入口（`ResearchStore.put(` 等）时生效；
  经注入句柄只调 `.put(...)` 的写入它看不见——真正的保证来自"内核用例 + 本清单"的组合。
- **顺带修掉一个真实缺陷**：续跑原来只看落盘 `request.mode`，而旧记录的 request 没有该
  字段 —— 会让一个**形式化研究会话在续跑时被换成团队引擎**（实测续跑后等的是团队事件，
  收不到原来的 interrupt）。现在续跑与启动共用同一条引擎判据。
- **旧代码的发现方式（重要经验，第 7 轮）**：删除 `paper_writer`/`writing_bridge` 之前，
  我先用 **AST 依赖图 + 可达性**算"从真实入口走不到谁"，再 `grep` 逐个人工核对调用者。
  这条路只走通了一半：
  - **有用的一半**：可达性确实指出了 `paper_writer`（唯一 import 者 `writing_bridge`）
    与 `writing_bridge`（唯一调用者是 `theory_pipeline` 里那条默认关闭的长文附录分支）。
  - **踩坑的一半**：我一度把它做成正式用例（"所有模块都必须从入口可达"），**两次都写坏了**
    —— 先是"包可达 ⇒ 子模块全可达"让判据失去区分度（放一个 `src/zz_dead_probe.py` 进去
    照样通过），改成严格版后又把 `src.config`、`src.research.intake` 这类真实在用的模块
    报成孤儿（函数内的 `from x import y` 让静态边解析不可靠）。
  - **处置**：把这个不可靠的守卫**删掉**，而不是留着一条只会让人忽略的红线 ——
    一个总是误报（或总是通过）的守卫比没有守卫更糟，它给人"已经守住"的错觉。
  - 结论：死代码清理目前**靠人工核对调用者**；可复用的做法是"用可达性分析得到候选，
    再逐个核对 import 与调用点"。这条经验留在这里，避免下次又去造一个不可靠的守卫。
- **M5 仍未迁出的部分（整理项，不是功能缺口）**：追溯视图仍以 `views/paper.ts` 一个模块
  落地，未再细分 `paper-workspace` / `figure-gallery` / `review-panel`。
  **契约已按域分组**（`contracts/{session,research,publication,library}.ts` + `index.ts`）；
  **取数入口已收敛**（全库只有 `api/research-client.ts` 里一处 `fetch`）；
  **产物发布已原子化**，并有跨前后端的端点/方法契约把守。
- **长文附录路径已删除（第 7 轮）**：`writing_bridge.write_long_form → paper_writer`
  与 `THEORY_LONG_FORM` 开关一并移除（共 905 行）。它在默认配置下唯一效果是往 notes 里
  写一句"长文附录未生成"；正文早已统一由 WritingAgent 承担。
- **`pipeline_cache` 的归属仍未按 run 分片**：旧按主题缓存已按要求**直接清除、不再迁移**；
  但新建缓存仍以主题为键（跨项目同主题共用），删除时按"项目或主题"判存活（不会误删）。
- **人工复核**（不是系统门槛，用户已决策）：`evals/rubric.md` 已改为**交付物复核清单**
  —— 人只看系统的最终产出，系统里**不再有"专家签字"环节**，也不再有"送审前另行准备
  封存材料"的前置要求；D1/D2 不再是评分约定，而是由复核人按清单在产出上判定。
- **限额真实测试**：本轮未跑付费任务（按要求）。

### 五、下一步

**目标五项已全部落地**（M3 / M4 / M5 / problem2 端到端 / 回归绿 + 签字移出系统），
两种研究形态也已**定稿为有意选择**（合并计划 §15.2.2）。剩下的是可选项：

1. **`publication/{inputs,rendering,gates}.py` 未按计划书拆**：能力都在
   （`publication/schemas.py` + `research/publication_paper.py` + `rag/latex_*`），
   只是模块边界与计划书里画的图不完全一致 —— 整理项，不影响行为。
2. **`pipeline_cache` 按 run 分片**：当前仍是主题键（跨项目同主题共用），
   删除时已按"项目或主题"判存活（不会误删）。
3. **限额真实测试**：按 runbook 先填 token/费用预估与硬上限，授权后运行并对照 `manifest.usage`。
4. **人工复核**：交付包产出后由人按 `evals/rubric.md`（交付物复核清单）逐条判定；
   系统内不再登记审批状态，也不要求送审前准备额外材料。

---

## 10.4 上一轮推进情况（智能体团队合并: M0 + M1 + M4 核心 + §13 路径导入）

依据《智能体团队合并计划》实施。**本轮不复跑付费真实任务**；所有证据均为离线命令。

### 一、按阶段完成的工作包

| 阶段 | 状态 | 关键交付 | 证据 |
|---|---|---|---|
| **M0 基线与契约** | **完成** | 新建 `src/agents/protocol.py`（`AgentTask`/`AgentResult`/`ResearchNeed`/`ChangeProposal`/`ArtifactRef`/`TaskBudget`/`RunBudget`/`UsageRecord`/`CapabilityGrant`/`ContextPack`/`AgentRun`）；`registry.py` 注册 8 个角色并**探测真实可用能力** | `tests/test_agent_protocol.py`（23） |
| **M1 最小团队闭环** | **完成** | 新建 `agents/runtime.py`（有界工具循环/权限闸门/预算/取消）、`agents/supervisor.py`（画像+计划+结构化决策+复盘）、7 个功能角色、`graph/research_graph.py`（团队外层循环）、`research/projection.py`（候选登记，不做判定） | `tests/test_agent_runtime.py`（27）、`test_agent_supervisor.py`（31）、`test_team_run.py`（26） |
| **M4 任务持久化与一致性** | **核心完成** | 新建 `research/task_store.py`（任务生命周期/幂等续跑/read-set 事务检查/恢复视图）、`graph/unified_state.py`（小状态：只存身份与引用） | `test_team_run.py` 内 M4 用例 + `read_set` 冲突拒绝 |
| **§13 路径构建人工文献库** | **完成** | 新建 `kb/path_import.py`（授权根/敏感拒绝/不复制/幂等去重/stale 检测/逐条报告）；`SourceSet` 增 `origin`/`imported_files`/`stale_files`；`GET/POST/DELETE /api/library/*` 与 `GET/POST /api/team/*` | `tests/test_path_import.py`（19）、`test_team_api.py`（9） |
| **M3 文稿中间表示与任务画像** | **完成（研究侧）** | 新建 `publication/schemas.py`（`WritingPacket`/`Manuscript`/`Block`，稳定 block ID + 引用追溯 + stale 判定 + 渲染期编号）与 `research/task_profile.py`（`TaskProfile`：章节骨架与领域措辞的**唯一依据**）；写作角色产出并登记稿件；**P0-3 领域硬编码退役**：`publication_paper` 的关键词/摘要/引言/第 2 节/附录前言与 `writing_bridge` 的关键词/章节骨架全部改为按画像决定 —— 抽取前它们**无条件**输出"组合设计/2-设计/必要条件的判定链"，让一篇信道可分性的报告声称自己在做组合设计判定 | `tests/test_task_profile.py`（14：块级 + **渲染后**双重回归 + 设计类反向保护）、`test_writing_bridge.py` 新增画像用例 |
| **M2 动作抽取** | **完成（研究侧）** | 新建 `research/reasoning_kernel.py`，把 `loop.py` 里的**推理/建模/验证方案**计算抽成纯函数（返回 `writes`/`events`/`idempotency_key`，**不写状态**）：证明计划、推导步骤 + 反方审查、设计证书判定链、反例判定映射、新颖性对照、模型提案、验证方案。引擎侧 `_act_plan_proof`/`_act_derive_step`/`_derive_design_steps`/`_act_seek_counterexample`/`_act_compare_novelty`/`_act_propose_model`/`_act_design_experiment` 全部改为"定位对象 → 调内核 → 落盘"；角色侧 `ReasoningAgent`/`ModelingAgent`/`ValidationPlanningAgent` 在对应策略下**直接调内核** —— 团队路径与旧引擎路径**共用同一份实现** | `tests/test_reasoning_kernel.py`（27，含差异测试与三条反向保护）、`test_team_run.py` 新增 3 项 |
| **M5 前端切换** | **部分完成** | 新建 `web/src/state/research-store.ts`（唯一状态入口: selection/entities/transport/ui）、`events/session-events.ts`（会话级游标 + 去重 + 缺口重同步 + 有上限退避）、`views/team-board.ts`（团队状态条/任务表/角色能力/审阅面板）、`team-controller.ts`（只读接入 + 迟到响应丢弃）、`styles/team.css`；`app.ts` 只在工作台容器前端插入团队区块并把旧状态**单向**映射进 selection | `research-store.test.ts`（22）、`session-events.test.ts`（19）、`team-board.test.ts`（17）、`test_team_board_in_real_browser`（真实 Chromium） |

### 二、修掉的真实缺陷（都由离线实跑暴露, 不是静态审阅）

团队闭环第一版在离线实跑中**连续踩到四种循环/卡死**, 逐个定位并加回归:

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 同一句需求被派 10–17 次直到轮次上限 | 幂等键含 `plan_version`, 升版即变新任务; 需求措辞里的计数也在变 | 幂等键**去掉计划版本**; 需求去重用 `need.assignment_key`（只由需求类型+角色决定） |
| 2 | 补检索成功, 写作却永不派发, 以 "wait" 收尾 | `replan()` 克隆计划时换了任务 id, 依赖边指向已不存在的任务 | `replan()` 保持任务身份不变; 补派任务用 `adopt()` **并入**计划 |
| 3 | 补派成功后交付判定仍不通过 | 等价完成只按 task_id 判, 补派任务换了 id 就不算数 | 依赖与交付判定都按"角色+目标"键算等价完成 |
| 4 | 已失败的前置永久阻塞下游 | `ready()` 要求依赖全部"完成" | 引入 `settled`: **已尝试过**的任务不再阻塞下游, 但同一件事不重复派 |
| 5 | 检索命中 0 条、研究卡住 | 检索式取自**子任务措辞**（"收集支撑或反驳候选结论的可定位来源"）, 而不是研究问题 | 检索目标优先取 `brief.main_question`/原始请求 |

### 三、验证基线（离线）

| 命令 | 结果 |
|---|---|
| `pytest -q`（全量、离线） | **1030 passed, 1 skipped**（本轮起点 876） |
| `pytest tests/test_browser_web_flow.py` | **7 passed**（真实 Chromium；含新增团队工作台用例） |
| `cd src/web && npx tsc --noEmit` | 0 error |
| `cd src/web && npx vitest run` | **124 passed**（本轮起点 73） |
| `node tests/js/bundle_security.test.js` | 全部通过 |
| 离线实跑（真实 KB, 一条自然语言任务） | `检索 → 综合 → 写作 → 审阅` 全通, `status=completed`, 5 轮, 登记 1 条可定位来源 + 1 条命题 + 1 份稿件 |
| 离线实跑（数学题走 M2 内核） | `reasoning completed KERNEL`: 策略 direct (rules), 3 步推导, **反方审查 8 项**, 并交出结构化候选 |

新增用例: M0 契约 23 + 运行时 27 + 主控 31 + 团队闭环 29 + 路径导入 19 + 团队/资料库接口 9 + 推理内核 27 + 任务画像 14 = **179**（Python）；
前端 store 22 + 事件 19 + 团队视图 17 = **58**（TS）。

> 说明: 无 LLM 时七类角色走**确定性实现** —— 检索走 `kb/bridge.gather_sources`, 综合按已登记证据关系生成候选, 写作渲染已登记结果并保留完整追溯。**判定层仍为零 LLM**（`verification/**`、`acceptance.py` 未新增任何 LLM 调用）。

### 四、前端本轮落地范围（诚实边界）

**已做**: 状态只有一份且四片归属明确; 连接状态与运行状态分开（离线不表示后台已停止）;
会话级事件游标 + `seq` 去重 + 缺口重同步; 未知事件不静默跳过; 有上限退避并支持取消重连;
团队状态条/任务表/角色真实能力/审阅面板; 团队视图接在后端投影接口上（只读, 不启动任务）;
迟到响应按加载代号丢弃; 窄屏纵向堆叠。

**未做（不要当成已完成）**: `app.ts` 尚未拆成 `session-controller.ts` / `features/intake/` /
`views/*`（仍是 2000+ 行）; 旧 `currentResearch` 仍在写路径上（只做了单向同步）;
`contracts.ts` 与 `current-research.ts` 的类型去重未做; `@ts-nocheck` 未移除;
论文/公式/图表追溯视图与产物级联调（F3–F5）未做; `src/web/dist` 与 `assets` 的原子发布未做。

### 五、下一步

1. **M2 剩余（判定层边界, 需谨慎）**: `_act_check_step` 的核心是"工具结果 → 义务状态"
   的状态机, 它与 `_run_tool`/`_save_verification`/`_reconcile_claim` 同属**判定层**,
   按 §14.3 要求保持零 LLM 且是唯一状态写入点。若要继续抽, 应抽"结果 → 义务处置"的
   **纯映射**（与 `counterexample_verdict_for` 同一形状）, 而不是把状态写入搬进角色。
2. **M5 收尾**: 把 `app.ts` 按 §9.5 的表拆成 `session-controller.ts` / `features/intake/` / `views/*`; 统一 `contracts/` 类型; 移除旧 mode 分叉与 `@ts-nocheck`; 论文/公式/图表追溯视图（F3–F5）。
3. **M3 收尾**: 退役"双正文"主路径（主文统一由 WritingAgent 形成, 确定性证书留附录）。
4. **前端 §13**: "从本机路径添加资料"的预览清单与资料工作区分组（后端 `scan`/`import` 已就绪）。
5. **M4 剩余**: 会话事件模块拆分与全局资源归属修复（全局 stdout、用量、共享记忆/缓存、图表目录、跨项目库删除）。
6. **人工门槛**（自动化替代不了）: `evals/cases/*/expected_notes.md` 专家签字; D1/D2 决策。
7. **限额真实测试**: 按 runbook 先填 token/费用预估与硬上限, 授权后运行并对照 `manifest.usage` 汇报。

---

## 10.3 上一轮推进情况（S1 命题化 + R6/R7 收尾 + 去领域硬编码）

### 一、本轮完成的工作包

| 工作包 | 状态 | 关键交付 | 证据 |
|---|---|---|---|
| §1 S1 命题化 | **完成** | 判定成为"命题 + 必要性义务 + 可复核证书"；`acceptance_method=design_necessity` **仅对 `nonexistent` 关闭义务**；题面能精确形式化时按明确问题处理（不再误判为研究方向、也不再要求先选候选）；附件题面同样参与形式化；正文附证书判定链；成功判据落进规格 | `tests/test_design_feasibility.py`（20）、`tests/test_delivery_level.py` |
| **出版级交付（方案 v2 M1–M4）** | **完成** | 四级交付等级（新增 **完整论文**）；期刊式稿件（摘要/关键词/中图分类号/英文摘要/六章/参考文献/附录）；`publication.tex` **真实编译为 `publication.pdf`**（4 页、无缺字）；**检索 + 引用守门**（命中≠支持，无可定位出处不引用）；**撰写层长文作附录 B**（只读快照，不覆盖判定链）；`publication_checklist.md` 人审清单；`problem2` 离线达"完整论文"且**证书 sha256 不变** | `tests/test_publication_paper.py`（13）、`test_publication_evidence.py`（12）、`test_writing_bridge.py`（13）、`test_problem2_offline_reaches_complete_paper_with_certificate`、方案 §11–12 |
| 图状态契约 | **完成** | `tests/test_theory_state_contract.py`：入口写入的每个状态键必须在 `TheoryState` 声明、类型一致，且 `input_snapshot` / `attachment_ids` 等回归键必须真的到达引擎 —— 把两次同类"静默丢数据"的根因固定成不变量 | `tests/test_theory_state_contract.py`（4） |
| §2 删除领域硬编码 | **完成** | 射频词表移入 `evals/cases/rf-fingerprint/retrieval_terms.md`；代码只留 `mentions()` 等领域无关原语；提示词里的领域词清理；判不出领域时**不做任何领域假设** | `tests/test_relevance_cache.py`（9） |
| §3 R6 收尾 | **完成** | `src/research/input_snapshot.py` 逐份附件重算 sha256；不一致**拒绝续跑**（可显式降级）；入口无关的启动快照；`TheoryState` 缺 `input_snapshot` 声明的真 bug 修复 | `tests/test_input_snapshot.py`（13） |
| §4 R7 收尾 | **完成** | `staged_upload()` 1 MiB 分块流式落盘 + 失败必清理；请求级上限按**实际**字节数；`copy_stream_to_bytes` 删除 | `tests/test_uploads.py`（23） |
| §5 精简与命名统一 | **完成** | 领域谓词收敛到同一实现并保留兼容入口；工作台投影已统一在 `reporting.workbench_projection`（核对结论：无重复实现）；注释只留"为什么" | 同上 |
| §6.1 评测 runbook | **完成** | `evals/cases/combinatorial-design/runbook.md`（离线冒烟 + 限额真实测试 + 判据映射 + 归档清单）与 `failures.md` | `tests/test_evals_contract.py` |

### 二、S1 的实际效果（离线、零成本）

`problem2.md`（211 节点无重复配对计划）现在端到端产出：

- **1 条命题**：`(v=211, k=15, λ=1, r=15, b=211)` 的 2-设计**不存在**；
- **1 条必要性义务**（`design_necessity`）被**规则证书关闭**，证书含 4 条判定：
  计数整除 → 声明值一致 → Fisher 不等式 → **Bruck–Ryser–Chowla**（n=14≡2 mod 4 非两平方和）；
- 两道门槛**通过**，`delivery_level = 论文草稿`，正文含"判定链（可逐条反查定理与输入）"；
- `manifest.usage`：`actions=3 / tool_calls=0 / tokens=0 / cost_usd=0.0 / llm_calls=0`；
- `manifest.input_snapshot.reproducible = true`（来自实际校验，不是声明）。

**泛化回归**（同一套逻辑，代码里无题目常量）：2-(43,7,1) 输出"不存在"；
2-(7,3,1)（Fano）只给"必要条件满足、存在性未定"，**义务保持未关闭**、交付等级不升级；
"分析信道变化对可分性的影响"不生成设计命题、行为不变。

### 三、验证基线

| 命令 | 结果 |
|---|---|
| `pytest -q`（全量、离线） | **876 passed**，约 125s |
| `cd src/web && npx tsc --noEmit` | 0 error |
| `cd src/web && npx vitest run` | 73 passed |
| `node tests/js/bundle_security.test.js` | 全部通过 |
| `pytest tests/test_browser_web_flow.py` | 6 passed（真实 Chromium：单页流程/整链/草稿身份/图标/附件上传/静态守卫） |
| CLI 离线实跑 `--mode theory` on `case.md` | 论文草稿 / 门槛通过 / usage 全零 / reproducible=true |
| 真实 LLM 实跑 ×2（`deepseek-v4-flash`） | 论文草稿 / 门槛通过 / 3 次调用 / 8.1k 与 9.2k tokens / 闲时合计 ≈ \$0.0080 |

> 跑批注意：`pyproject.toml` 的 `addopts = --basetemp=.pytest_tmp` 是**全工作区共享**的，
> 并行跑 pytest 必须各自带独立 `--basetemp`，否则会互相删临时目录并产生假红。

### 三之二、前端 404 缺陷（现场报告 → 已修复）

打开页面时报的四个 404 是两个缺陷，均已修复并加了回归（台账见
`evals/cases/combinatorial-design/failures.md` CD-001 / CD-002）：

| 编号 | 现象 | 根因 | 修复与回归 |
|---|---|---|---|
| CD-001 | `GET /favicon.svg 404` | 模板引用 `/favicon.svg`，后端只登记了 `/favicon.ico` 路由 | 新增 `/favicon.svg`；`test_favicon_routes_are_served` |
| CD-002 | `GET /api/research/proj-<草稿>/state 404` ×3，"读取工作台失败 (404)" | 切理论模式会生成草稿项目 id（让附件有稳定身份），而"要不要刷新工作台"的判据是"项目 id 与状态里的不同"——草稿也写进状态，判据被填平 | 显式记录草稿 id，只有它自己不查；`test_draft_identity_does_not_probe_workbench`（含反向保护：手填真实项目仍须查询） |

**共同近因**：`src/web/dist/` 里的旧 bundle 配新后端（后端不编译前端）。因此运行手册
§0.1 把"前端产物与源码同版本"列为 Web 入口硬前置，并加契约测试防止这条要求从手册消失；
`dist/assets/` 里残留的旧 bundle 也已清理（原先同时存在两个 js，容易误判跑的是哪一版）。

### 三之三、附件上传入口提到主界面（可用性）

原先附件入口藏在折叠的「高级选项」且只在理论模式显示 —— 现场表现为"没有上传入口"。
现已移到**对话输入区正下方**（`#inputbar` 之下的 `#attachrow`），切到理论模式即出现：
`选择文件`（原生控件改为 label 按钮样式）· `用途`（补充问题说明／补充文献）· `上传` ·
已上传列表。用途与语义不变，仅位置提前；切到理论模式时会自动拉取已上传列表。

同时补上了这条链路的**首个浏览器级回归** `src/web/tests/e2e/web_upload.mjs`
（`test_attachment_upload_entry_in_real_browser`）：综述模式隐藏 → 理论模式可见 →
入口不在高级选项内 → 上传成功 → 列表出现 → **启动请求携带 `attachment_ids`** →
交付包 `input_snapshot` 里逐份附件带 `sha256` → 改动项目 ID 时**启动前如实拦下**
（R3 归属校验的用户可见面）。写这条用例时发现并修掉一个会**静默丢附件**的坑：
上传后改项目 ID，服务端会拒绝这些附件而界面不报错，用户以为附件生效了。

### 三之四、现场缺陷：控制器反复提议澄清（CD-003）

人工运行 `Problem 2` 后的系统输出显示：事件流里控制器连续 **16 轮**提议 `clarify_problem`
（分别指向命题与不同义务），每轮都被判"有进展"，运行"卡在澄清步骤"直到预算耗尽。

原因：`ActionType.clarify_problem` 此前只当普通动作派发（`lambda a: True` = 有进展），
于是"判定需要澄清"这件事**不会结束循环**，控制器可以一直重复提议同一个动作。
澄清是终态判断，不是可重复动作。修复：

- `step()` 里遇到 `clarify_problem` 直接终态收尾（置 `needs_clarification` + `done`，
  并把"为什么请求澄清"写进 notes）；
- `run()` 的两个循环条件都排除澄清态（含"写作缺口重开"那条），防止重开后再推进；
- 图侧不再把这个标志回写状态 —— 它在引擎里是"粘住"的，回写会让一次中途澄清永久
  降级最终交付等级（实测：验收题的 `gate_passed` 被误判为 False，已按此修正）。

回归：`test_clarify_decision_stops_the_loop_instead_of_repeating`（固定提议器连续提议澄清，
断言 ≤2 个动作即收尾）与 `test_clarify_by_controller_ends_the_graph_with_a_memo`
（端到端：门槛不通过、交付等级为研究备忘录）。真实模型复跑同一路径：5 个动作即收尾，
`needs_clarification=True`（此前会跑满 40 个动作）。

**顺带确认的另一处**：同一输入下若模型不提议澄清，循环会走"换路"路线；`no_progress`
护栏会在用尽 3 条路线后停下并输出未决报告（实测 19 个动作停止，不是烧光预算）——
这一条与 CD-003 是不同机制，已一并核对。

原先附件入口藏在折叠的「高级选项」且只在理论模式显示 —— 现场表现为"没有上传入口"。
现已移到**对话输入区正下方**（`#inputbar` 之下的 `#attachrow`），切到理论模式即出现：
`选择文件`（原生控件改为 label 按钮样式）· `用途`（补充问题说明／补充文献）· `上传` ·
已上传列表。用途与语义不变，仅位置提前；切到理论模式时会自动拉取已上传列表。

同时补上了这条链路的**首个浏览器级回归** `src/web/tests/e2e/web_upload.mjs`
（`test_attachment_upload_entry_in_real_browser`）：综述模式隐藏 → 理论模式可见 →
入口不在高级选项内 → 上传成功 → 列表出现 → **启动请求携带 `attachment_ids`** →
交付包 `input_snapshot` 里逐份附件带 `sha256` → 改动项目 ID 时**启动前如实拦下**
（R3 归属校验的用户可见面）。写这条用例时发现并修掉一个会**静默丢附件**的坑：
上传后改项目 ID，服务端会拒绝这些附件而界面不报错，用户以为附件生效了。

### 三之五、现场缺陷：附件里的题面从未被读取（CD-004）

用户的真实入口是**把 `problem2.md` 作为「补充问题说明」附件上传**，输入框只写一句
"研究此问题，输出明确的结论，并撰写成文"。系统却报"缺少可检验对象"并请求澄清；
而同一题面直接粘进输入框就能正常给出"不存在"。四个连锁缺陷：

1. `uploads.problem_text()` 全局**零调用** —— 附件正文被算进 `state["attachment_candidates"]`
   后再无任何读者，研究循环完全看不到它；
2. `TheoryState` 未声明 `attachment_ids`，该键**根本不进图状态**，`_engine_from_state` 读不到；
3. `start_session` 落盘的 `session.request` 漏了 `attachment_ids`，续跑/历史回看无法复现
   "题面在附件里"这一事实；
4. 即使题面能被精确形式化，流程仍先要求用户选"研究方向候选"（多此一举的一步）。

修复：引擎新增 `attachment_ids` 参数与 `attachment_text()`（读取失败或归属校验不通过时
记 note，不静默当作"用户没给"）；`bootstrap` 把附件正文并入形式化来源，且**能精确形式化
就直接进入判定、不生成候选**；`TheoryState` 声明该键；`session.request` 记录附件与资料源字段。

端到端验证（真实 HTTP 入口，离线模型）：上传 `problem2.md` 作为附件 + 一句话请求 →
会话 `done` → 交付包 **论文草稿 / 门槛通过**，命题为"…的 2-设计不存在"、
`design_verdict=nonexistent`、`input_snapshot.attachments` 记录 `sha256`、`reproducible=True`。

回归：`test_problem_text_supplied_as_attachment_is_used_for_formulation`
（含负向验证：把附件读取断开后该用例失败）。

### 三之六、现场缺陷：写作缺口回流后无人消解（CD-005）

真实测试（题面走附件）在事件流 `#11 写作缺口回流: 1 条新义务` 之后停止：遗留一条
`writing_missing_argument_chain / required=True -> open`，交付级别掉到"条件性研究报告"，
且同一份 `manifest.json` 自相矛盾（`gate_passed=false` 而 `delivery_gate_passed=true`）。
三处独立成因（详见 `evals/cases/combinatorial-design/failures.md` CD-005）：

1. **误报缺口**：缺口判定只看 `attempts.steps`，不认设计证书里的判定链 —— 计数/设计类
   结论的推导链存放在 `VerificationRecord.arguments["design_report"]`（定理 → 条件 →
   输入 → 结论逐条可反查），比 steps 更可核查。已改为"有证书链即不算缺口"，
   并加反向用例保证真缺推导时仍然报。
2. **回边缺失**：图是 `theory_finalize → END`，而"缺口回流 → 重开研究循环"只存在于
   `loop.run()` 的内层循环 —— 图/Web 逐步驱动**不经过**它，回流出的义务只被登记、
   永远无人处理。已加 `_route_after_finalize` 回边、`engine.resume_after_reflow()`
   （解除持久化的 `done`，否则 `step()` 立刻返回）、缺口未消解时**不导出交付包**、
   以及 `THEORY_REFINALIZE_MAX`（默认 3）防死循环。
3. **manifest 自相矛盾**：`_refresh_manifest` 把 `delivery_gate_passed` 写成出版门槛
   结论，覆盖了真实的研究/表达门槛。已让三个字段各自独立。

回归：`tests/test_writing_gap_recovery.py`（7 项，含"不可消解的缺口必须在上限内收尾"
的防死循环用例）；真实模型同一路径复跑 4 次均为"完整论文 + PDF、无未关闭义务"。

### 三之七、现场反馈：出版格式粗糙 + 没有文献引用（CD-006）

真实运行产出了 `.tex`/`.pdf`，但（a）格式粗糙、（b）没有文献检索与引用。逐项查证后
定位到 **13 个真实缺陷**（其中 5 个直接导致"有论文但没有引用"）。

**引用与检索（b）**

1. **本地无资料库时直接放弃检索** —— 而外部检索（arXiv/OpenAlex）本可用且网络可达。
   改为按资料授权策略决定：`autonomous`/`both` 时无本地库也走外部检索；`user_kb` 且库空
   时如实说明"如需检索请把资料范围改为自主检索"。
2. **`search_all_sources` 是 `StructuredTool`，不能直接调用**（实测
   `TypeError: 'StructuredTool' object is not callable`）→ 经 `.func` 取原始实现，
   多源失败时退到单源 arXiv。
3. **外部命中无页/节定位 → 每条都被判"无可定位出处"而全部丢弃**（"检索到了却一条也引不了"）
   → 用 arXiv ID 作为稳定定位。
4. **中文命题 vs 英文来源零词面重合** → 真正的相关工作被判"原文未出现命题的主题词"。
   新增术语对照（射影平面/projective plane 等），命中只升到 `background`（背景相关工作，
   不声称支持），并要求 ≥2 个术语命中，避免 "polypill design" 这类泛词误判。
5. **检索到的证据没有传给论文生成** → 出现"参考文献 11 条、第 4 节却写未发现可比较的工作"
   的自相矛盾 → 证据随检索结果一并传给第 4 节。
6. **检索成功却记成 `executed=false`** → 覆盖记录一边写命中 18 条、一边写未执行。

**排版（a）**

7. 每一节打成"1 1 引言"（稿件自带编号 + LaTeX 自动编号叠加）→ 用
   `\setcounter{secnumdepth}{-2}` 关掉 LaTeX 自动编号（稿件自带编号）。
8. 英文摘要被编成"1 英文题名与摘要"，把后续章节整体推移。
9. "参考文献"打印两遍（`thebibliography` 自带标题 + 又加了 `\section*`）。
10. 作者/单位在页首出现两行（`\author{}` 与正文块重复）。
11. 参考文献条目丢序号（`[1]` 被 LaTeX 当可选参数吃掉）→ 引用统一走 `\upcite{refN}`。
12. 判定链五条挤成一整段、无法阅读 → 多行块按行渲染（段落/无序列表/编号列表/引文/表格分流）。
13. **PDF 落进套娃目录**：`package_dir` 是相对路径，而编译器按当前工作目录解析，产物跑到
    `out/research/<pid>/<snap>/out/research/...` —— 表现为"编译其实成功、门槛却报没有 PDF"。

**数学排版（c）**：用户反馈"数学推导的符号、公式等应当更标准……而不是大量文字和公式推导的堆叠"。
证书本身是结构化的（`design` / `counts` / `evidence[]`），附录据此渲染**真正的行间公式**
（`equation*`，含分式）、**逐条件条目**（定理 / 输入 / 结论）与 `booktabs` **必要条件汇总表**，
不再把推导堆成一整段文字。改完逐条核对**编译日志**，查出并修掉三处真实 LaTeX 错误：
数学模式里写中文字符（缺字）、双反斜杠写出 `lamdbda = 1` / `cdotr`、`\cdot` 吞掉后一个字母
（`v\cdotr`）**直接中断整次编译**。引用在正文里是数字，参考文献区紧邻条目列表。

**回归与验证**：新增 `tests/test_publication_layout.py`（8 项排版契约）；
`test_publication_evidence.py` 扩到 14 项（含 arXiv 定位、外部检索回退、空库回退）；
真实网络复跑：命中 18 条 → 可引用 3 条 → 第 4 节逐条关系判定 + 5 页 PDF
（摘要/英文摘要/六章/参考文献/附录齐备，无重复标题、无缺序号）。

### 三之八、现场缺陷：历史会话被草稿项目顶掉（工作台 0 次 state 请求）

现象：顶部「历史会话」点开一条旧对话，工作台显示"还没有研究记录"，服务端日志里
`/api/research/*/state` **一次都没有**被请求。

根因：前端 `switchToConversation()`（`src/web/src/app.ts`）在 `#projid` 字段还空着的时候就调用了
`onModeChange()`，后者按 R2 行为生成一个**草稿**项目 id 并覆盖历史 id；工作台随后查的是这个
草稿项目，历史记录自然读不出来。

修复：先绑定运行、把历史项目 id 与问题 id 写进表单，**再**调 `onModeChange()`，之后**再写一次**
（`onModeChange` 自己也会写这两个字段，不重写就会被顶掉）；只有确实存在真实项目 id 时才刷新工作台。

回归：新增浏览器级用例 `src/web/tests/e2e/history_view.mjs` —— 断言日志出现历史标记、工作台渲染出
记录、`/api/research/*/state` 确实被请求过、且全程无 4xx/5xx。真实 Chromium 实测：
**3 次 state 请求**、工作台内容约 **3.2k 字符**。

### 三之九、正文数学排版（遗留 ① 已完成）

上一轮只把附录证书排成了公式，正文第 3 节仍是同一份证书的**逐字文本**，读起来就是
"文字与符号堆叠"。本轮补齐：

1. **正文证明块改用结构化证书排版**：`publication_paper` 对设计类结论发出
   `certificate` 块（`label=claim_id`），LaTeX 渲染器按 `claim_id` 取回证书，排出
   公式块（`equation*` + 分式）、逐条必要条件（定理/输入/结论）、必要条件汇总表 ——
   与附录完全一致；没有证书的结论仍走文字证明块。
2. **正文行内公式统一进数学模式**：新增 `wrap_inline_math()`，把段落里的纯文本公式
   （`v=211`、`r(k-1)=λ(v-1)`、`b ≥ v`）包成 `$...$`。判据刻意严格：片段必须含关系符、
   不含汉字、不含逗号、不含 `^`/`mod`；已在 `$...$` 内的内容跳过；紧邻的圆括号留在
   数学模式**外**（数学模式里的 `(` 会被排成定界符，与中文标点不一致）。

途中修掉的两个真实 LaTeX 事故（都由编译日志发现）：

- `\cdot` 把后一个字母并进命令名（`v·r` → `\cdotr`）→ 报 Undefined control sequence
  并**中断整篇编译**；所有符号命令统一补 `{}`（`\lambda{}`、`\cdot{}`…）。
- 括号被卷进数学模式：输出过 `($v=211$, …, $b=211)$` 这种左右不配对的排版 →
  前导 `(` 只在括号不平衡时才当标点，末尾多余的 `)` 一律留在数学模式外。

回归：`tests/test_publication_layout.py` 扩到 17 项，新增"所有 `\命令` 必须在已知命令
白名单内"这条判据 —— 它能精确抓到"符号替换出错"（`\geqv`、`\lambda`），比按位置匹配
可靠。全量 `pytest -q` → **854 passed**（离线）。

### 三之十、产出美观度与可读性自查（第二轮）

逐页查看生成 PDF 后发现的**结构性问题**与修法（问题按严重度排列）：

| # | 问题（实测证据） | 修法 |
|---|---|---|
| 1 | 第 1 节"引言"没有引言：正文只有"本文研究以下问题："加一段题面引文，无背景/贡献/结构 | 重写 `_introduction`：研究背景 → 本文工作 → 主要结论 → 全文组织，再附原题面引文 |
| 2 | 章节重复：第 1 节后半段与第 2 节重复 | 第 2 节改为"一句话点明问题 + 形式化落脚点"，整段题面只在引言出现一次 |
| 3 | 同一份证明出现两遍（§3 与附录 A 逐字重复） | 正文改用**紧凑证书**（判定对象 + 被违反条件 + 指向附录），完整推导只在附录出现一次 |
| 4 | 英文摘要标题是模板话 "Title and Abstract in English" | 改为 `\section*{Abstract (English)}` |
| 5 | 题名页印生成日期"2026 年 10 月 3 日" | 去掉 `\date{}`（期刊论文不印生成日期） |
| 6 | 作者/单位三行占位堆叠 | 姓名 + `\thanks{}` 脚注（通信作者含邮箱），只出现一次 |
| 7 | **页眉写着错误的节名**：第 1 节内容顶着"2 问题与形式化"的页眉 | 页眉改用稳定短标题（论文题名），不再取"最后一个 `\section` 名" |
| 8 | 正文里 `**不存在**` 印出两个星号 | 新增 `_md_emphasis`：`**x**` -> `\textbf{x}` |
| 9 | `n mod 4` 被排成 `nmod4`（斜体黏连） | 输入值里的 `mod` 渲染为 `\bmod` |
| 10 | 符号表左对齐、列松散 | 表格居中 + `booktabs` |

其中 3 处是**会破坏编译**的真实缺陷（都由编译日志定位）：

- `\textbf{...}` 多一个闭合花括号 -> "Too many }'s" 中断编译；
- 数学符号替换的顺序错误：早期实现先在整串上做"裸命令 -> Unicode"，于是
  `$\lambda=1$` 里本来该留在数学模式内的 `\lambda` 被换成 `λ`，又被当成裸符号补 `$`，
  排成 `\$$\lambda$=1\$` 这种垃圾；现在改为**按区域处理**（先切出保留区，再在区外
  规范化与转义，最后区外残留符号转数学模式），并对非数学片段做两次规范化以处理
  规范化自身插入的 `$...$`；
- `wrap_inline_math` 会把已存在的数学区域重新拼接：`$211\times211$` 被拆成
  `$211\times{}$` + `211`，丢掉外层定界符，LaTeX 在正文里看到裸 `\times` 报错；
  现在正文渲染对已含 `$` 的行不再二次包装。

排版契约测试扩到 17 项，新增：行内公式包装、中文绝不进数学模式、无空数学模式 `$$`、
命令白名单（能精确抓到 `\geqv` / `\\lambda` 这类替换事故）。

全量 `pytest -q` -> **854 passed**（离线）；生成物 5 页、编译 0 错误 0 缺字。

### 三之十一、按新计划书落地首轮 (P0 三项 + 检索质量)

依据 `AI for Research(AIR)计划书.md`，本轮完成其中三项 P0 与一项实测发现：

| 计划书编号 | 问题 | 处置 | 回归 |
|---|---|---|---|
| P0-1 | 自主检索可被整个研究循环跳过 | **两处** `knowledge_available` 一票否决已解除：`_gaps()` 不再以本地库为空为由不产生证据缺口；`_retrieval_requests()` 改用 `retrieval_available` | `test_no_local_kb_still_produces_evidence_gap_and_retrieval_requests`、`test_user_kb_policy_without_local_kb_produces_no_retrieval_request` |
| P0-1 追加 | 已定论命题永不检查文献依据 | `_gaps()` 只跳过**已有文献依据**的已定论命题。实测依据：真实运行中命题 `status=supported`/`validation_status=verified` 但**证据数为 0**，按 status 跳过会导致它永不检索 | 真实运行 `liveauto2` 出现连续 3 次 `retrieve_targeted`（此前为 0） |
| P0-4 | 删会话清空共享向量库 | `delete_session()` 增加所有权判据：还有会话引用同一 `project_id` 时**保留**共享资源，并在响应里以 `kept_shared` 明示；`list_conversations()` 暴露 `project_id` | `test_delete_session_keeps_shared_vector_store_for_other_sessions` |
| — | DOI/OpenAlex 命中拿不到定位，永远无法引用 | 新增 `_stable_locator()`（arXiv ID > DOI > OpenAlex ID）取代只认 arXiv 的实现；`_normalize_doi()` 归一 `doi:`/`https://doi.org/` 前缀并拒绝无效 DOI | `test_stable_locator_accepts_doi_and_openalex_like_arxiv`、`test_stable_locator_rejects_non_doi_and_empty` |
| — | OpenAlex 命中"只有标题"，无引文 | 取 `abstract_inverted_index` 并还原成摘要；`select` 补 `id` 使 `openalex_id` 不再恒为空（`kb/identity.py` 的去重键之一） | `test_openalex_abstract_is_reconstructed_from_inverted_index`、`test_openalex_hit_with_doi_and_abstract_becomes_citable` |
| — | 中文关键词在英文库里等于噪声 | `query_planner` 在术语表为空时回退到领域术语对照；跨语言检索式**意图词 + 领域名词短语**配对、泛词整类剔除、同义意图词只留一个 | `test_chinese_goal_without_terminology_gets_english_core_terms` 等 4 项 |

**实测数据 (真实联网 + 真实模型)**：`source_policy=autonomous`、无本地库。修复前该场景
检索被跳过；修复后 `liveauto2` 在研究循环内连续执行 3 次 `retrieve_targeted`，命中 7 条
候选证据，且**判定层如实把 7 条全部标为 `insufficient`**（返回的是 "The Nonexistence of
Character Traits"、"Nonexistence theorems for traversable wormholes" 等无关论文）——
即"检索到了但一条也不引用"，符合"不得靠标题相似声称支持"的防火墙要求。

**已知未做**：中文标注术语（如 `2-设计`）在英文库里没有可用的具体英文锚点，检索式会退化
到 `nonexistence` 这类泛意图词。检索层的召回质量有限，目前依靠判定层的严格过滤兜底；
要真正改善需要领域词表或按 AI 领域的受控词表做查询扩展。

全量 `pytest -q` -> **866 passed**（离线，较上轮 854 增 12 项）。

### 三之十二、P0-2 后半段与两处审计缺陷

接上一轮 (三之十一)。本轮把"检索影响研究"这条链闭合，并修掉两处**审计缺陷**
(不影响结论正确性，但让交付包无法复核)。

**1. P0-2 后半段：相反来源使命题失效重验**

新增 `TheoryEngine._literature_impact_recheck()`，挂在 `_dispatch()` 里
"产生/判定证据的动作"成功之后 (`retrieve_targeted` / `read_source` / `extract_result` /
`interpret_evidence` / `seek_counterexample` / `compare_prior_work`)。处置与既有的
`revise_assumption` **同一套机制**，不另造：命题写回 `in_progress` 并落盘**新版本** →
现有验证记录**保留但标记 stale** (不删除，保住审计线索) → 相关义务重开 →
由中央状态规则 `_reconcile_claim` 重新裁决 → 记录"哪条来源改变了哪条结论"。
幂等：同一来源只触发一次 (判据是命题 notes 里已含该来源 id)。

为什么必须做: 检索此前发生在 `engine.finalize()` **之后** (`gather_publication_evidence`)，
判成 `contradicts` 的来源只留在证据表里，冻结的命题与验证记录毫发无损 —— 这就是计划书说的
"发表前检索无法纠正先前推导"。

**2. 审计缺陷 A：缺口不落盘**

`_gaps()` 只做**内存**计算，而 `_freeze_snapshot()` 从存储读 `KIND_GAP` → 决策日志里明明
有 `missing_evidence` 缺口，交付包的 `gaps.json` 却是 **0 条**。新增 `_persist_gaps()`，
在每步决策前与收尾前各落盘一次；实验规格 (id 以 `exp-` 开头) 不被覆盖。
实测：`gaps.json` 由 0 条变为 **14 条**。

**3. 审计缺陷 B：覆盖记录被空覆盖顶掉**

检索改到研究阶段后，出版层因"研究循环已产生证据"跳过检索并返回一份**空覆盖**；导出时
直接采用它，于是交付包报 `executed=false / 命中 0`，而实际入库了证据。新增
`engine_spec_coverage()`：优先取研究循环累积在 `spec.coverage` 上的记录 (并标注
`origin=research_loop`)，仅在研究循环确实没检索过时才用出版层记录。
实测：`retrieval_coverage.json` 由 `not_executed / 命中 0` 变为
`research_loop / 命中 24 / 入库 12`。

**真实运行验证 (`livefinal2`, 联网 + 真实模型)**

决策序列 (8 步)：`retrieve_targeted → interpret_evidence → retrieve_targeted →
derive_step → check_step → read_source → compare_prior_work → synthesize_results`。
即：研究阶段先检索、再解释证据、发现还缺 BRC 定理的原文支撑就**再检索一次**、
然后才进入推导与核验。判定层仍把 7 条候选证据全部标为 `insufficient` (返回的同类论文
不含该定理陈述)，未产生任何假引用。

全量 `pytest -q` -> **873 passed**（离线；本轮 +2 项，累计较计划书基线 854 增 19 项）。

### 三之十三、复用综述模式的"下载全文 → 入库"（用户建议）

用户指出：综述撰写链早就有"把检索到的文献下载到本地并入库供使用"的操作，问为何不照用。
核查确认**确实可以照用，而且工具完全现成**：

| 现成资产 | 位置 | 作用 |
|---|---|---|
| `pdf_ingestion` 节点 | `src/graph/pipeline.py` (综述模式正式阶段) | 下载 + 解析 + 入库 |
| `download_pdfs_for_papers` | `src/tools/pdf_fetcher.py` | 按 arXiv/DOI 取 PDF，逐篇限流 |
| `ingest_pdf_fulltext` | `src/agents/pdf_ingestor.py` | 解析全文、分块、写向量库 |
| `ingest_machine` | `src/kb/ingest.py` | **只要 `pdf_path` 存在**，就自动 parse → sections → chunks → `extract_cards` → 向量化 |
| `extract_cards` | `src/kb/cards.py` | 抽 `定理/Theorem` 等卡片，定位形如 `p7 / Theorem 10` |

理论模式的 `_harvest_external()` 只调 `ingest_machine(topic, papers, embed=False)` 且
**不带 `pdf_path`** → 库里只有元数据 → 引用守门拿不到可核对引文
(`quote_is_locatable` 需要原文) → "检索到了却一条也引不了"。

修法：在 `_harvest_external()` 入库**之前**插入 `_download_fulltext_for()`，复用
`download_pdfs_for_papers`；规模上限取 `per_query` 与 `PDF_DOWNLOAD_LIMIT` 的较小值
(下载是逐篇限流的重活)。三种边界都如实记录：向量化不可用则跳过下载、命中但无开放获取
则记"只能按摘要核对"、下载异常不阻断元数据入库。

**真实运行验证 (联网，arXiv)**

```
命中 4 条 → 下载 2 篇 (Ternary codes, biplanes… 9 页 / Symmetries of biplanes 24 页)
入库后: 文档 has_fulltext=True、页数正确; 卡片 47 张 (定理 8 / 引理 27 / 推论 5 …)
定理卡片带页级定位:
  [p7 / Theorem 10] A quasi-3 design with parameters 2-(267,57,12) … does not exist.
  [p7 / Theorem 11] A quasi-3 design with parameters 2-(149,37,9) … does not exist.
  [p1 / Theorem 1.1] Let D be a biplane with parameters (v,k,2), where k ∈ {13,16} …
```

这正是"引用已有定理时找到对应文献"所需要的可核对出处 —— 不再是只有标题和摘要。

回归：`tests/test_kb.py` 新增 3 项（全文入库后出定理卡片且带定位、无向量化时跳过下载、
命中但无开放获取时记录覆盖边界）。全量 `pytest -q` -> **876 passed**（较上轮 873 增 3）。

### 三之十四、代码与测试精简（用户建议）

用项目自己在 `pyproject.toml` 里声明的 `ruff` 做依据（此前只声明未安装，本轮装上 0.16.7），
逐类处理，**每步之后跑全量回归**。

**1. 修掉 3 个真实缺陷（静态检查发现，非风格问题）**

| 缺陷 | 位置 | 影响 |
|---|---|---|
| `Path` 未导入 | `src/rag/vector_store.py` `persist_directory()` | 该函数**运行时必然 NameError**；它是向量库落盘目录的唯一入口，实测 `persist_directory()` 直接抛错 |
| 类型注解引用未导入的名字 | `src/research/publication_evidence.py` `_locatable_snippet(claim: Claim)` | `typing.get_type_hints()` 抛 NameError；任何做运行时类型检查的工具都会失败 |
| `except Exception as e` 但未使用 / 局部变量遮蔽 `dataclasses.field` / 死变量 `figs` | `utils/http_client.py`、`research/design_feasibility.py`、`rag/latex_render.py` | 遮蔽 `field` 会在该作用域内静默拿到字符串而非 dataclass 字段 |

**2. 合并逐字重复的实现（不另造新抽象，收敛到已有规范实现）**

| 重复 | 处置 |
|---|---|
| `_arxiv_id_from_url` 在 `rag/reference_formatter.py` 与 `tools/venue_resolver.py` 各一份，**逐字相同** | 统一到 `tools/pdf_fetcher.extract_arxiv_id`（它本来就是功能超集：还支持旧式 `cs.CL/0011004`）；新增 `arxiv_id_or_empty()` 保持调用方原来的字符串契约 |
| `_loads_json_object` 在 `research/derivation.py` 与 `research/proposal.py` 各一份，**归一化后完全一致**（仅差一行 docstring） | 收敛到新模块 `src/utils/json_text.py`；两份容错解析规则变成一份真相源 |
| `_rewrite` 闭包在 `rag/reference_formatter.py` 内出现两次，**逐字相同** | 提为模块级纯函数 `_rewrite_citation_group(match, mapping)` |

**3. 删除死代码**：`publication_render._math_key()` 与 `_render_input_value()` —— 前几轮重构的
遗留物，全库（含脚本/前端/文档）零引用。**明确不删**的候选：`@app.get/@app.post` 装饰的
FastAPI 路由（按名搜索查不到，但由框架调用），以及 `head_with_retry` / `reset_circuit_breaker`
/ `close_client` 等公共 HTTP 工具（属对外可用 API）。

**4. 机械清理**：未使用导入 43 项、导入排序 67 项、`f-string` 无占位符 21 项等共 163 项自动修复；
`F`/`E9` 类（未定义名、未使用变量、遮蔽、语法类）**全部清零**。

**5. 测试侧：核查后判定"不该合并"**（避免为凑指标做有害合并）

- 834 个测试函数体**无一完全相同**（无复制粘贴式重复）；
- `_claim` 7 处、`_snapshot` 6 处、`_evidence` 5 处虽同名，但逐条核对后确认是**各测试自己的
  夹具**：代数命题 / 因果命题 / 定义式命题，带证书 / 不带证书，带证据 / 不带证据，断言文本
  也不同。合并成一个带默认参数的工厂会降低每个测试的自解释性，因此**保留不动**。

**结果**：`src` 36,244 行（-2 行但消除 3 处重复实现与 2 个死函数）、`tests` 14,890 行；
全量 `pytest -q` -> **876 passed**，与精简前一致（未因精简丢失任何断言）。

### 四、下一步

1. **人工门槛**（自动化替代不了）：`evals/cases/combinatorial-design/expected_notes.md`
   数学方向核对签字（BRC 应用 + D1/D2 两项评分约定）；rf-fingerprint 材料 A/B 与专家签字；
   第二领域（`evals/cases/transfer/`）题目选定。
2. **限额 LLM 真实测试**：按 runbook §2.1 先填 token/费用预估与硬上限，授权后运行，
   用 manifest 的 `usage` 与 `budget_limits` 对照汇报实际花费（首次已跑：两次合计闲时 ≈ \$0.0080）。
3. 首次真实运行的失败样例归档（只追加、不删除）。
4. 待决策历史项：D1（引用 BRC 是否算合格）、D2（未决报告是否算部分通过）、
   D4（`src/web/dist/` 是否入库）；D3 已随流式落盘实现而消失。

---

# 附录·历史归档（并入自已删除的旧文档）

> 说明：原 `执行进度.md`、`下一轮执行计划.md`、`完整论文格式补全方案.md`、
> `S1命题化交接.md`、`上传附件实现计划.md`、`未完成工作清单.md` 已按"只保留
> 计划书/执行进度/合并计划"的要求删除。**其中仍有后续价值的内容并入本节**，
> 避免历史记录丢失；被删文件的全文可在 git 历史 `c794d97` 中取回。

## A. 历史缺陷修复记录（2026-09-24 一轮复核）

| # | 缺陷 | 处置 |
|---|---|---|
| 1 | `Session.shutdown()` 在后台线程仍用连接时关闭 SQLite → 进程崩溃 | 改为先置停止标志并等待退出，超时则如实返回失败而不崩进程 |
| 2 | `tests/test_survey_mode.py` 三处与契约不符（上一轮新增但未记录） | 按契约修正测试 |
| 3 | 测试隔离不完整，测试把会话/检查点写回仓库 | 补隔离（`DATA_DIR`/`OUTPUT_DIR` 调用时读取） |
| 4 | 图表/向量库绕过配置写死仓库路径 | 改为按调用时配置解析路径 |
| 5 | `test_workbench_api.py` 用 `SESSIONS.pop()` 代替 `shutdown()` | 改用 `shutdown_sessions()` |
| 6 | 新克隆仓库拿不到前端构建产物 | `setup_env.bat` / `start.bat` 自动构建；缺产物时服务端返回 503 并给出构建指引 |

## B. 已知边界（不得当作已完成）

- **综述模式没有 `project_id/problem_id`**：身份是 `thread_id/session_id/run_id`，
  与理论模式不同构。**统一两者必须先改契约与前端**，不能给综述造一个假 project
  —— 这一条直接约束「智能体团队合并计划」的 P1（统一状态）阶段。
- 逐命题事件（`derive_step` / `verification_recorded` 等）不带 `problem_id`，
  按问题聚合依赖命题归属；只有"每问题一次"的生命周期事件与 `log_anomaly` 强隔离。
- `resume` 的"真续跑"只在**同一**检查点存储上成立；换新内存检查点会明确报告
  "从头开始"，并沿用落盘的运行身份。
- 写作缺口的 `blocking/advisory` 划分依据是"当前研究能力能否自行消解"；
  能人工消除的一律 `advisory`，由验收门槛独立拦下。
- 关闭会话时后台线程 10s 内未退出则**不**关闭检查点连接（宁可有句柄残留也不崩进程），
  此时 Windows 上该检查点文件可能暂时删不掉。
- **本机 `.venv` 是从别处搬来的**（`site-packages` 内记录的路径仍是旧的
  `E:\SearchAgents\AIR\.venv`），能跑通全套但不干净；正式发布前建议重建。
- `app.ts` 仍有 `@ts-nocheck`：视图纯函数已抽出并类型化，余下 DOM 操作与请求编排未类型化。
- `start.bat` / `setup_env.bat` 只做过静态审阅，未端到端跑过批处理。

## C. 仍未完成 / 待人工

| 项 | 说明 |
|---|---|
| 发布门槛 6 的领域人审 | 表与材料已就绪，**签字待人工**：专家按 `evals/rubric.md` 逐项判定 |
| R2 / R4 的领域级验收 | 自动化已覆盖"不越界、状态一致、模型有候选与舍弃理由"；"模型忠实于领域、论证在领域内成立"属人工评审 |
| 浏览器级验收 | `test_e2e_http.py` 走真实 HTTP 但不驱动真实浏览器；视觉与键盘可用性仍需人工 |
| 三类评估样例 | 组合设计（就绪）/ 另一类数学题（缺）/ 需经验验证题（缺） |
| rf-fingerprint 材料 A/B 与 `expected_notes.md` 封存 | 待人工；**不得提供给系统** |
| 第二领域题目 | `evals/cases/transfer/case.md` 仍是 TODO，用于迁移性验证 |
| 限额 LLM 真实测试 | 唯一花钱的步骤：先填预估与硬上限，授权后运行并对照 `manifest.json` 的 `usage` 报告实际花费 |
| 首次真实运行的失败记录归档 | 失败样例必须保留，不得改期望或润色掩盖 |
| **测试仍会在仓库留下 `data/chroma/chroma.sqlite3`** | 全量运行结束后出现（空库：0 集合 / 0 嵌入），单文件运行不出现，推测来自图线程晚于夹具落盘。已加会话级兜底 + `atexit` 清理并如实报告，但**本次实测中 `atexit` 仍未拦住该写入，写入者尚未定位**。该文件被 `.gitignore` 覆盖（第 21 行 `data/chroma/`），不会污染提交，仅占约 184 KB |

## D. 待决策的历史项

| 编号 | 问题 | 现状 | 影响 |
|---|---|---|---|
| D1 | 评分标准：引用 BRC 即算合格，还是要求系统自行推导 BRC？ | 现为"具名定理 + 参数证书"（`theorem_application`），不含 BRC 自行证明 | 决定验收门槛高低 |
| D2 | "必要条件满足但存在性未定"的未决报告是否算部分通过？ | 按"可选结论之一"处理（如实未决、义务不关闭、等级不升级） | 决定能力边界如何计分 |
| D4 | `src/web/dist/` 是否入库？ | **已定**：不入库，由 `setup_env.bat`/`start.bat` 自动构建；缺产物返回 503 并给指引 | 新克隆需先跑构建脚本 |

## E. 本轮清理记录（2026-10-03）

- **代码入库**：`c794d97` 把长期未跟踪的全部源码纳入版本库
  （233 文件 / +53,517 行），含 `src/web/src` 前端源码与 `tests/`、`evals/`。
- **过滤内容**：`.env`（真实密钥，确认从未入库）、`data/uploads/`（用户上传附件）、
  运行时数据与产物；`.gitignore` 增补 `.ruff_cache/`、`.mypy_cache/`、`.pt_*/`、`data/uploads/`。
- **数据清理**（见下）：按用户要求彻底清空会话数据与研究成果，
  工作区回到"只有代码与文档"的状态。

