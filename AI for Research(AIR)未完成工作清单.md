# 统一智能体系统重构 · 待完成任务与交接说明

> **权威来源**：`AI for Research(AIR)智能体团队合并计划.md`（2026-10-04 重构版，405 行）。
> 本文件只做两件事：**记录契约与已验证的事实**，以及**把剩余工作切成可直接执行的切片**。
> 需求与验收口径以计划书为准；本文件不新增要求。

**当前基线**：`master`，最后一次提交 `3523c7c`（**尚未推送到 GitHub**，见 §6）。
**离线全量回归**：`pytest -q` → **1155 passed, 1 skipped**（2026-10-04 第八轮后）；
`npx tsc --noEmit` → 0 error；`npx vitest run` → 256 passed（20 files）；真实 Chromium → 9 passed；
`ruff check --select F,E9 src/ tests/` → All checks passed；
**真实模型端到端**（`problem2.md`，`THEORY_LLM` 未设）→ `status=completed`、`llm_calls=3`、
tokens 43,929、成本 $0.0336、交付包含 `manuscript.md` + `manuscript.tex` + `manuscript.pdf`。

---

## 0. 已完成（第三轮后更新，不要再当待办）

| 缺口 | 状态 | 落地位置与回归 |
|---|---|---|
| **G03** 身份与输入绑定 | **完成** | `server._normalized_input()` 唯一输入快照；`_make_session(request=)` 建图前落位；`_build_team_app` 把附件与资料范围交给团队。回归：`test_team_wiring_and_identity.py`（2 项） |
| **G06** 团队形式化闭环 | **完成** | `research/verification_service.py`（工具/规则/反例/独立审查四分派）、`research/formulation.py`、`ReasoningAgent.formal_closure()`、`reasoning_kernel.claim_state_for`（唯一状态判据）。回归：`test_research_commit_and_closure.py`（22 项） |
| **G08** 事务与 read-set | **完成** | `research/commit.py::ResearchCommitService`（唯一提交口）。回归：同上（过期不合入/重放不重复/越权在提交口被拒） |
| **G12** 交付依据与导出 | **完成** | `research/delivery.py::assess_delivery()`；`_finish` 按门槛降级；`export()` 抛 `ExportError`；轮次上限也走同一 finalize。回归：同上 |
| **G15** 事件多订阅 | **完成** | `EventLog.wait_for` + SSE 每订阅者游标读持久日志；非业务帧不占序号。回归：`tests/test_sse_subscribers.py`（3 项，真并发双连接） |
| **G16** 可恢复运行状态 | **完成** | `KIND_TEAM_RUN` 落盘 + `TeamRun.save_state/resume`；已提交动作不重跑。回归：`tests/test_team_run_state.py`（5 项） |
| **G17** 唯一文稿 IR（出版层） | **完成** | `publication/render_latex.py`（只吃唯一 IR）+ `publication/compiler.py`（编译实现迁入，`rag/latex_compiler` 转发）；交付包含 `.tex`/`.pdf`，等级按编译事实定。回归：`test_research_commit_and_closure.py` 的 2 项 + `test_team_session.py` |
| **G18** 图表产物隔离 | **完成** | `rag/figure_formula.py`（AST 白名单）+ `agents/figures.py`（唯一校验式服务，`figures/<run>/<task>/v<n>/`）+ 自由脚本路径删除。回归：`tests/test_figure_artifact_service.py`（26 项） |
| **G19** 前端单一状态 | **完成** | `state/research-store.ts` 唯一可写；`current-research.ts` 只读投影；兼容同步删除。回归：`src/web/tests/single-source-of-truth.test.ts` + 真实 Chromium |
| §5.4 反例搜索 | **完成** | `verification_service.check_counterexample()` + `refute` 义务（`required=False`）+ 有理数探测网格。回归：`test_team_decides_provable_and_refutable_claims`（可证明/可反驳各一例） |
| §6.1 证据归属 | **完成** | `EvidenceLink` 候选（证据角色与推理角色都能写）+ `KIND_MAP`/`OBJECT_KINDS`/`WRITABLE_KINDS` 补齐。回归：`test_evidence_is_bound_to_the_claim_version` |

---

## 1. 一句话现状

**团队已经能自己走完形式化研究并交付完整论文包（含 PDF），剩下的是一条"能力抽取 → 删除旧引擎"的收尾路径。**
团队侧已具备：唯一提交口、唯一文稿 IR 与出版层、确定性状态归并、工具/规则/反例核验、
证据归属、可恢复运行状态、多订阅者事件流、图表产物隔离。
**仍绕不过团队的是旧引擎里还没抽出来的那几项能力**（见 §3 R2 的表），抽完即可执行 G01 的删除。

---

## 2. 契约：改动时必须保持的既有保证

这些是已经**有回归用例**、改动时不能回退的契约（改动前先看对应测试）：

| 契约 | 位置 | 测试 |
|---|---|---|
| 判定层零 LLM、且是**唯一**研究状态写入点 | `research/kernel`、`research/loop.py` | `test_reasoning_kernel.py::test_engine_path_and_team_path_share_the_kernel` / `test_model_proposal_does_not_write_state` |
| 智能体只能提交**候选**，不得自报科学等级 | `agents/runtime.py` | `test_agent_runtime.py` |
| 越权候选只进审计记录，**绝不进权威对象库** | `AgentRuntime.finalize` → `AgentResult.rejected_changes` | `test_rejected_candidate_never_reaches_authoritative_storage` |
| 资料授权 = 角色能力 ∩ 用户授权 ∩ 任务范围 | `agents/protocol.grant_for` + `SOURCE_POLICY_SCOPES` | `test_search_capability_requires_declared_source_policy` |
| 主文只有一条生产路径（含离线降级） | `agents/writing.write_main_manuscript` | `test_main_text_writing_agent.py` |
| 审阅只可降级、不可升级 | `acceptance.py` + `downgrade_only_gate` | `test_agent_runtime.py::test_review_gate_only_allows_downgrade` |
| 运行身份全链路绑定（禁 `not run_id` 放行） | `TeamRun.prepare` + `projection.object_rows` | `test_team_wiring_and_identity.py` |
| 交付包导出不得静默漏行 | `graph/team_session._snapshot_from_store` | `test_snapshot_export_mapping.py` |
| 端点表 ↔ 后端路由/方法一致 | `web/src/api/research-client.ts` + `team_api.py` | `tests/test_web_api_contract.py` |
| 人工只审最终产出，系统内**无**签字/审批 | `evals/rubric.md` | `tests/test_evals_contract.py` |

**期望答案不得进入 Agent 上下文**（`evals/` 的 expected 只在评测侧可见）。

---

## 3. 剩余切片（按依赖顺序，各自可独立提交）

每片都要求：**先写可失败的回归**（计划 §9 的出口门槛），再改实现，最后删旧路径。

### R1 余项（安全/状态底座收尾）

| 缺口 | 现状（已核实） | 要做什么 | 出口门槛 |
|---|---|---|---|
| **G08 / P0（已完成）** | 见 §0（唯一提交口） | —— | —— |
| **G12 / P1（已完成）** | 见 §0（交付门槛决定等级） | —— | —— |
| **G13 / P1（按要求暂缓）** | 常规入口未注入 `RunBudget`；`BudgetedLLM.__getattr__` 直接透传 `bind_tools`（返回的模型可能绕开 invoke 记账） | 统一 `invoke`/`bind_tools` 包装；即时计数 + 全局预留/结算/释放 + 取消传播 | 多轮模型替身 + 查询计数器：次数/tokens/成本归属正确，超限即停。**用户已明确本轮不考虑预算上限**，但它是"真实模型自主运行"的前置，建议在 G01 之后补 |

### R2 团队接管研究（**关键路径**：能力抽取 → G01）

| 缺口 | 现状（已核实） | 要做什么 |
|---|---|---|
| **G03 / P0（已完成）** | 见 §0；第六轮又补三处：工作台 `run_id` 与 HTTP 一致、补派任务的对象带运行身份、交付清单记问题附件 | —— |
| **G06 / P0（已完成）** | 见 §0 | —— |
| **G01 / P0（运行时已收敛，文件待删）** | **入口已无引擎选择**：`_resolve_engine()` 不收参数、`StartRequest.mode`/`max_actions`/`max_tool_calls` 删除、`_build_app_for_mode` 只有团队一张图、`build_theory_initial_state`/`_research_progress` 删除、CLI `--mode`/`_run_theory_mode` 删除、反馈端点的引擎分支删除、`server.py` 里 `TheoryEngine` 实例化为 **0 处**。**文件本身仍在**：`research/loop.py`、`graph/theory_pipeline.py`、`graph/theory_state.py`、`rag/{theory_render,publication_render,latex_render}`、`publication_paper.py`、`theory_writer.py`、7 个旧 Agent 与旧 `PipelineState` —— 运行时不可达，只在测试里作为库被调用 | 同一提交里：删这些文件 → 迁移 `tests/test_theory_mode.py`（约 1000 行）等仍直接调它们的用例 → 清理只服务旧图的 `unified_state.py` 字段 → 重跑全量 |
| 旧 Agent 退场（计划 §5.3） | **七件套已删除**（第八轮，约 2000 行）：`citation_checker`/`citation_guard`/`citation_prechecker` → `publication/{citation_checks,evidence_ledger}`（逐字迁出，用例改指新家）；`literature_reviewer` → `AgentRuntime.run`（`test_agent_runtime.py` 11 项，强于原 3 项）；`outline_generator` → `writing.py`；`paper_reviewer` → `review.py`（含"引用编号每轮重排"规则的迁移）；`pdf_ingestor` → `kb/ingest`。保留了 `ROLE_ALIASES` 旧名映射与旧节点名分支（一次性迁移识别） | —— |

**G01 的删除清单（不可颠倒）**：先让团队覆盖已支持任务 → 逐组抽取旧引擎能力（**已完成**：只读检视、反馈、派生、新颖性排序口径、事件摘要、预算对象）→ 删对应旧分支（**已完成**：服务端入口、CLI、反馈）→ 最后删文件。
`tests/test_engine_retirement.py::test_extracted_services_do_not_depend_on_the_engine` 是这条的**可执行准入判据**（AST 扫描 11 个已抽出的模块，出现反向依赖即失败）。

### R3 唯一论文/审阅

| 缺口 | 现状 | 要做什么 |
|---|---|---|
| **G17 / P1（已闭合）** | **同源同版已成立**：交付的 Markdown 与 `.tex`/`.pdf` 都从唯一 IR (`publication.schemas.Manuscript`) 出来（`write_main_manuscript` 返回唯一 IR + `render_markdown`）；块锚点改为确定性（同一快照两次渲染逐字相同）。**仅剩旧 IR 的模块本身**：`rag/{theory_render,publication_render,latex_render}`、`publication_paper.build_publication_paper`、`agents/theory_writer.py` —— 随 G01 的文件删除一并清掉（顺序见《计划推进情况.md》§10.12 第三节） | 见 §10.12 的删除顺序：先让 `writing.manuscript_from_snapshot` 直接产唯一 IR 的块，再删 `theory_writer`/`theory_render` |
| **G18 / P1（已完成）** | 见 §0 | —— |
| 计划 §5.5 D1 项 | `theory_writer`、`publication_paper.build_publication_paper`、`rag/{theory_render,publication_render,latex_render}`、`package._traceability` 自证、`publication_evidence`、`figure_generator`/`figure_llm` | 按 §5.5 表逐条抽取 → 归入 `publication/render_*`、`publication/checks`、`tools/figures`（`figure_*` 已完成） |

### R4 会话/前端收敛

| 缺口 | 现状 | 要做什么 |
|---|---|---|
| **G15 / P1（已完成）** | 见 §0 | —— |
| **G16 / P1（已完成）** | 见 §0 | —— |
| **G19 / P1（已完成）** | 见 §0 | —— |
| **G03 的前端部分** | 事件游标仍有两套 key 空间：`session-stream` 按 **thread**，store 的 `cursorBySession` 按 **session**（且只被当前未接入的 `applyEvent` 使用）；`app.ts::handleEvent` 仍直接消费 SSE 载荷 | 计划 §8.4 的 8 个验收场景（快速切 A/B 迟到响应、同 run 两页面 SSE、断网重连、附件注入、stale 显示、多格式同源、错误/可访问性）；统一 key 空间与持久游标 |

### R2 剩余的旧引擎能力（G01 的前置，按依赖顺序）

| 能力 | 现状 | 要做什么 |
|---|---|---|
| 工作台只读投影 | **已完成**：`research/inspection.py::StoreInspection`（只读检视层）+ `reporting.workbench_projection` 只收已算好的值；`server._research_state` 不再建引擎，团队运行也写 `ResearchSpec` 因此可在工作台浏览 | —— |
| 用户反馈 → 对象级修订 | **团队路径已完成**：`research/feedback.py` + `TeamRun.submit_feedback()`（意见 → 跨职责需求 → 主控派工 → 唯一提交口）；`/api/research/{p}/feedback` 在**该问题已有团队运行状态**时走团队。没有团队状态的旧问题仍走引擎 | G01 时删掉引擎分支（连同 `TheoryEngine.submit_feedback`） |
| 派生（fork） | **已完成**：`research/forking.py::fork_from_snapshot(store, spec, …, on_claim=)`；HTTP 端点不再建引擎；引擎同名方法转发并用自己的 `on_claim` 登记研究路线 | —— |
| 新颖性对照 | **已完成（团队路径）**：`research/novelty_service.py` + 证据角色产出确定性 id 记录 + `snapshot_from_store` 收集 novelty（交付包按它写"新颖性未确认"）；`user_kb` 绝不外搜，`autonomous` 才走外部对照 | —— |
| 快照冻结/收尾 | 团队走 `export_package`；`_freeze_snapshot`/`metrics`/`writing_gap_feedback` 只在引擎 | 确认团队路径已覆盖（写作缺口回流已通过 `followup_needs` 走通），再删引擎分支 |
| 缺口排序口径 | **已完成**：`research/obligations.py`（`OBLIGATION_PRIORITY`/`obligation_kind`/`obligation_claim`），`loop.py` 转发 | —— |
| **删除准入判据** | **已有**：`tests/test_engine_retirement.py::test_extracted_services_do_not_depend_on_the_engine` —— AST 扫描 11 个已抽出的模块，出现 `src.research.loop` 依赖即失败 | 删引擎前先让这条保持绿 |

### R5 清仓与科研验收

- 计划 §5.6：`protocol.py` 旧角色映射只留**一次性迁移识别**（已确认；第七轮删旧 Agent 时保留了它）；
  **已删**：`StartRequest.mode`/`max_actions`/`max_tool_calls`（第六轮）、`--max-revisions`/`--skip-retrieval`/`MAX_REVISIONS`（第十轮，删前先确认"全库无读者"）、`--mode` 与理论 CLI 分支、旧 `stages/stage_index`、`graph/state.py::PipelineState`（第九轮）；
  **已清**：README 的 `main_chat`/`gui`/模式切换/已删开关说明 + `pyproject` 项目名（第十轮 → `air-research-team`）。
  **仍未做**：取消固定 2019-2026 检索窗；LangGraph/checkpointer 依赖盘点（唯一运行时是否还需要它）。
- 改写这些**待替换**的测试（计划 §5.6.3，**必须在删除 `TheoryEngine` 的同一提交里做**）：
  - `test_engine_boundary_contract.py`（保留双引擎的说明与断言）
  - `test_engine_retirement.py::test_both_engines_can_be_built` / `covers_both_remaining_engines` / CLI `--mode` 默认值那条
  - `test_reasoning_kernel.py` 的 engine/team 双入口用例
- `test_refactor_features`/`test_new_features`/`test_relevance_cache`/`test_format_figures`/`test_figure_llm` **混有有效能力测试**：先迁行为用例，再删旧实现断言，**不整文件盲删**。
- README 的 `main_chat`/`gui`/模式切换说明失效；`pyproject` 项目名仍是 `langgraph-survey-pipeline`。
- 若唯一运行时不再用 LangGraph/checkpointer，再按扫描结果移除依赖。
- 最后按计划 §10 验收矩阵跑**离线/已知解回归 + 未见同类任务泛化**，记录输入/模型/工具/预算/版本/失败类型。
  （§10 的"数学/逻辑：可证明/可反驳/unsupported/缺条件各一例"已有回归：`test_team_decides_provable_and_refutable_claims`）

---

## 4. 可复用的验证资产（都已入库）

| 资产 | 用途 |
|---|---|
| `audit_probes.py` | 逐条打印 §3 缺口当前状态（`FIXED`/`STILL BROKEN`）。离线、内存库、不联网、不调模型。**改判据时注意**：它必须走真实入口（G02/G14 曾因绕过装配层而误报） |
| `audit_sources.py` | 只读 AST 清单：源码 hash 比较、入口可达性、测试引用。注意它假定 `AIR/` 与 `AIR_review_*/` 布局，在本仓库要改路径 |
| `tests/test_engine_boundary_contract.py` | 判定层写入口清单 + 旧撰写路径防回流 |
| `tests/test_web_api_contract.py` | 前端端点表 ↔ 后端真实路由/方法 |
| `tests/test_web_asset_publish.py` | 产物原子发布与引用一致性 |

**离线跑测试的固定环境**：`$env:THEORY_LLM='0'`（显式离线）、`--basetemp=<唯一目录>`（并行时必须）、解释器 `.\.venv\Scripts\python.exe`。

---

## 5. 已知坑（踩过的，别再踩）

1. **测试可能"只断言了其中一句"就通过**：拆分 `views/paper.ts` 时我改坏过状态文案与缺锚点记录，21 项用例全绿。移动代码后要专门核对用户可见文案。
2. **守卫可能恒真或大面积假阳性**：我做过"所有模块必须从入口可达"的守卫，第一版恒真（放死模块也通过），第二版把 `src.config` 这类在用模块报成孤儿 —— **已删除**。不可靠的守卫比没有守卫更糟。
3. **探针/测试绕开真实入口会给出错结论**（G02/G14 就是这样误报的）。
4. **`.pytest_tmp*` 会被 `git add -A` 暂存**（`.gitignore` 已补 `.pytest_tmp*/`）。
5. **`KIND_MAP` 把 source/card/case/dataset 归入 `evidence` 存储族**是有意的；候选种类保留在 `_candidate_kind`（别把它当成冗余字段删掉）。
6. **`_snapshot_from_store` 的裸 `except` 曾吞掉 `ImportError`**，导致"模型一个都没进包"完全无声；现在跳过必须留痕。新增映射时保持这个约定。
7. Windows + `core.autocrlf` 会产生大量 LF/CRLF 警告，不影响内容。

---

## 6. 立即要做的一件事

**`master` 领先 `origin/master` 1 个提交（`1514d4b`），推送因本机无法连接 GitHub 而失败**（`Test-NetConnection github.com:443` → False）。网络恢复后：

```powershell
cd E:\AIR
git push origin master
```

工作区**有大量未提交改动**（2026-10-04 第三轮后共 59 项：27 个源文件、12 个新文件、若干测试），
尚未提交也尚未推送。**下一轮第一件事应当是提交并推送这批改动**（按切片分几个提交更清楚）。

---

## 7. 建议的下一步执行顺序（给下个对话）

> **先读 `交接文档-统一多智能体科研系统.md`** —— 它把"现状 / 验收证据 / 复现命令 / 下一步 / 环境 / 踩过的坑"
> 收在一处；本文件是**权威待办**，那份是**入口**。

1. **提交**工作区改动（§6）：当前 111 项改动全部未提交（29 新增 / 10 删除）；先分组提交再做批量迁移。
2. **G01 的唯一前置：迁移 36 个仍由 `theory_pipeline`/`TheoryEngine` 驱动的用例**。
   批次见 `交接文档` §3.1：① 纯判定层服务类（直接调已抽出的服务）→ ② 交付/出版类（走团队 + 交付包）
   → ③ 端到端与"方向→候选确认"类（`test_theory_mode` 等最后处理）。
   准入判据已就位并须保持绿：`tests/test_engine_retirement.py::test_extracted_services_do_not_depend_on_the_engine`
   （AST 扫描 11 个已抽出的模块）。
3. **删除旧引擎整簇**（约 6750 行）：`research/loop.py`、`graph/theory_pipeline.py`、
   `graph/theory_state.py`、`agents/theory_writer.py`、`research/publication_paper.py`、
   `rag/{theory_render,publication_render,latex_render}`；同一提交里改写双引擎断言
   （`test_engine_boundary_contract.py`、`test_engine_retirement.py`、`test_reasoning_kernel.py`、
   `test_store_inspection.py`）。**不允许**为了全绿先删断言。
4. **G13（用量边界）** —— 用户已明确暂缓，但它是真实模型自主长跑的前置，建议紧随 G01。
5. **§8.4 前端 8 个验收场景**；**R5 剩余**：取消固定 2019–2026 检索窗、LangGraph/checkpointer 依赖盘点。

**每片结束时的固定动作**：更新《计划推进情况.md》（新增一节：改了什么/证据/仍未闭合/没做的事）
与合并计划对应章节；跑 `pytest -q`（`THEORY_LLM=0` + 唯一 `--basetemp`）+ `ruff` + 前端三项。
