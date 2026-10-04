# 统一智能体系统重构 · 待完成任务与交接说明

> **权威来源**：`AI for Research(AIR)智能体团队合并计划.md`（2026-10-04 重构版，405 行）。
> 本文件只做两件事：**记录契约与已验证的事实**，以及**把剩余工作切成可直接执行的切片**。
> 需求与验收口径以计划书为准；本文件不新增要求。

**当前基线**：`master`，最后一次提交 `1514d4b`（**尚未推送到 GitHub**，见 §6）。
**离线全量回归**：`pytest -q` → **1082 passed, 1 skipped**；
`npx tsc --noEmit` → 0 error；`npx vitest run` → 227 passed；真实 Chromium → 63 passed；
`ruff check --select F,E9 src/ tests/` → All checks passed。

---

## 1. 一句话现状

**已具备"唯一团队"的骨架 + 安全/状态底座；尚未完成"由这套团队真正承担科研"。**
具体地：团队入口已接模型、主控已真的调用模型规划、派工依赖已修正、越权候选不再落库、
资料授权按策略求交、工具契约与真实适配器对齐、导出不再丢字段、运行身份全链路绑定。
**但形式化研究仍绕过团队进入 `TheoryEngine`** —— 这正是 G01/G06 未闭合的后果。

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

### R1 余项（安全/状态底座收尾，建议紧接着做）

| 缺口 | 现状（已核实） | 要做什么 | 出口门槛 |
|---|---|---|---|
| **G08 / P0** | `TaskStore.commit_result()` **已具备** read-set 检查与同事务提交（`src/research/task_store.py`），但 `graph/research_graph.py::_persist_result()` 走的是 `task_store.finish()` 后**逐条** `projection.register()` —— 事务能力没被用上 | 让团队走 `commit_result`：把"终态 + 接受的候选写入 + 事件 + 检查点"放同一事务；`accepted_writes` 直接来自 `finalize()` 之后的 `proposed_changes`（越权候选已被隔离，天然接上） | 注入中断（工具完成/对象未提交、提交中断、同结果重放）三种情形都有用例；重放不产生第二个对象 |
| **G12 / P1** | `TeamSession.export()` 未传完整研究/出版门槛；`_missing_roles()` 把 partial 也算满足；`TeamApp.stream()` 吞导出异常 | 区分"依赖已终止"与"验收已通过"；所有出口走同一 finalize；导出失败返回**可恢复错误**而不是静默空包 | partial 依赖不满足验收；导出异常可见且可重试 |
| **G13 / P1** | 常规入口未注入 `RunBudget`；`BudgetedLLM.__getattr__` 直接透传 `bind_tools`（返回的模型可能绕开 invoke 记账） | 统一 `invoke`/`bind_tools` 包装；即时计数 + 全局预留/结算/释放 + 取消传播 | 多轮模型替身 + 查询计数器：次数/tokens/成本归属正确，超限即停 |

### R2 团队接管研究（**关键路径**，G06 → G01）

| 缺口 | 现状（已核实） | 要做什么 |
|---|---|---|
| **G03 / P0**（先做） | `server.py::start_session()` 先 `_make_session()`（已建 `TeamRun`）后填 `session.request`/`run_id`；`TeamApp.stream()` 不消费初始字典里的研究身份与资料；附件未进团队 | 顺序改为：规范化输入与文件 → 校验资料授权 → **分配身份** → 创建 run/session → 注入同一输入快照 → 启动。HTTP 返回的 ID 必须等于任务/SQLite/事件/包里的 ID |
| **G06 / P0**（核心） | `ReasoningAgent` 的内核路径把计划/推导压成 claim 候选，**未**提交完整义务/推导/验证对象；`_run_tool/_act_check_step/_reconcile_claim` 仍在旧引擎 | 把"候选/义务 → 工具核验 → 结构化记录 → 状态归并 → 依赖失效 → 下一任务"接进**同一任务协议**。核验不是第二个规划引擎：工具结果经 `finalize` 落 `VerificationRecord`，义务闭合走 `reasoning_kernel.obligation_disposition_for`（判定层唯一写入） |
| **G01 / P0**（最后） | `server._resolve_engine()` + `DEFAULT_ENGINE`/`SURVEY_ENGINE_ENABLED` + `main.py --mode`/`_run_theory_mode()` + `graph/theory_pipeline.py` + `research/loop.py` | 删除引擎选择，所有科研请求进同一 `TeamRun`；删除 `research/loop.py`、`graph/theory_pipeline.py`、`_run_theory_mode` 与全部 `TheoryEngine` 实例化点；`is_survey_request()` 中为选引擎服务的部分删除（任务画像由主控统一生成） |
| 旧 Agent 退场（计划 §5.3） | 7 个旧 Agent 约 2407 行 + 旧 `PipelineState` 约 2564 行 | 按 §5.3 表：D1 的**先抽取有效能力**（引用核实→Evidence 工具、引用守门→`publication/checks`、审阅定位→`ReviewIssueStore`、PDF 入库对照 `kb/ingest`）再删旧入口 |

**G01 的删除清单（不可颠倒）**：先让团队覆盖已支持任务 → 逐组抽取旧引擎能力 → 删对应旧分支 → 最后删文件。**不允许**为了"测试全绿"先删断言。

### R3 唯一论文/审阅

| 缺口 | 现状 | 要做什么 |
|---|---|---|
| **G17 / P1** | `publication.schemas.Manuscript` 与 `rag.theory_render.Manuscript` **并存**，`WritingAgent` 双向转换；`theory_pipeline.py` 写 `manuscript_md` 后又 `build_publication_paper()` 重组正文 | 收敛为唯一 `WritingPacket → Manuscript IR → 多格式渲染`，预览/Markdown/PDF 同源同版；迁出证书/公式/排版函数后删除旧 IR、转换层与第二个正文生成器 |
| **G18 / P1** | `FigureAgent` 默认 `OUTPUT_DIR/figures` 按 kind-purpose 命名（多 run 可能覆盖）；校验问题记录后仍渲染；`input_versions` 为空；公式直接 `parse_expr` | 唯一 `FigureSpec`/产物服务按 run/task/artifact version 分目录；先校验来源/模型域/单位/权限再渲染；表达式白名单解析 + 受限执行，拒绝任意 Python |
| 计划 §5.5 D1 项 | `theory_writer`、`publication_paper.build_publication_paper`、`rag/{theory_render,publication_render,latex_render}`、`package._traceability` 自证、`publication_evidence`、`figure_generator`/`figure_llm` | 按 §5.5 表逐条抽取 → 归入 `publication/render_*`、`publication/checks`、`tools/figures` |

### R4 会话/前端收敛

| 缺口 | 现状 | 要做什么 |
|---|---|---|
| **G15 / P1** | `session_events()` 回放后仍 `session.events.get()`，多页面争抢同一队列；`connected` 用 `current_seq+1` 可能占用下一业务 ID | 持久事件日志 + 每订阅者游标/通知；`connected`/heartbeat 不占业务 seq；投影带 revision/event_seq，过期游标先补快照 |
| **G16 / P1** | `TeamRun` 每次创建空 `TeamLoopState`；`recovery_view()` 只是查询，未重建 brief/plan/results/open_needs/dispatched；`TaskStore.create()` 同键复用不返回旧 identity | 持久化统一 `RunState`、任务租约/attempt、结果与工具收据；明确 start/resume/fork；已提交动作不重跑 |
| **G19 / P1** | 前端三份状态并存：`air-global.ts` 的 `research`、`app.ts` 的 `currentResearch` 与 ID 镜像、`team-controller.ts` 的 reducer store；`syncSelectionFromLegacy()` 表明新 store 不是唯一权威 | 保留组件与 reducer，取消兼容同步；全部控制器经同一 store/action/selector（计划 §8.1）；删 `legacy-adapter`/`syncSelectionFromLegacy` |
| **G03 的前端部分** | —— | 计划 §8.4 的 8 个验收场景（快速切 A/B 迟到响应、同 run 两页面 SSE、断网重连、附件注入、stale 显示、多格式同源、错误/可访问性） |

### R5 清仓与科研验收

- 计划 §5.6：`protocol.py` 旧角色映射只留一次性迁移识别；删 `StartRequest.mode`、旧 `stages/stage_index`、survey/theory UI 标签；逐项确认 `--skip-retrieval`/`--max-revisions` 去向；取消固定 2019-2026 检索窗。
- 改写这些**待替换**的测试（计划 §5.6.3，**必须在删除 `TheoryEngine` 的同一提交里做**）：
  - `test_engine_boundary_contract.py`（保留双引擎的说明与断言）
  - `test_engine_retirement.py::test_both_engines_can_be_built` / `covers_both_remaining_engines`
  - `test_reasoning_kernel.py` 的 engine/team 双入口用例
- `test_refactor_features`/`test_new_features`/`test_relevance_cache`/`test_format_figures`/`test_figure_llm` **混有有效能力测试**：先迁行为用例，再删旧实现断言，**不整文件盲删**。
- README 的 `main_chat`/`gui`/模式切换说明失效；`pyproject` 项目名仍是 `langgraph-survey-pipeline`。
- 若唯一运行时不再用 LangGraph/checkpointer，再按扫描结果移除依赖。
- 最后按计划 §10 验收矩阵跑**离线/已知解回归 + 未见同类任务泛化**，记录输入/模型/工具/预算/版本/失败类型。

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

工作区干净（`git status --short` 为空），无未提交改动。

---

## 7. 建议的下一步执行顺序（给下个对话）

1. **推分支**（§6），确认 CI/远端状态。
2. **G03**（身份与输入绑定）：它是 G06 的前置 —— 团队现在可能拿到空的 problem/run 身份与不到位的附件。
3. **G06**（团队形式化闭环）：让团队自己走完"义务 → 工具核验 → 结构化记录 → 状态归并 → 依赖失效"。用 `problem2.md` 作为验收题（离线应产出完整论文包且 `llm_calls=0`）。
4. **G08/G12/G13**（提交事务、交付依据、用量边界）：与 G06 同一批做，因为都要经 `commit_result`。
5. **G01**（删 `TheoryEngine`）：必须**最后**做，且在同一提交里替换 §3 R5 列出的那些双引擎断言。
6. **G17/G18 → G15/G16/G19 → R5 清仓**。

**每片结束时要做的固定动作**：更新《计划推进情况.md》（新增一节，写清"改了什么/证据/仍未闭合/没做的事"）
与合并计划对应章节；跑 `pytest -q` + `ruff` + 前端三项；提交并推送。
