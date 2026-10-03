# AIR 计划书执行进度

更新日期：**2026-09-24**（上一版：2026-09-23）
对照文件：`AI for Research(AIR)计划书.md`（2026-09-23 版，正文 §1–§6）

本文件只记录"计划书里的哪些工作包**已经做完、用什么证据证明**"，不重复计划书正文。
计划书本体保持"修改前的检查结论"原样，不改成"已完成" —— 否则下一轮无法再从计划书
看出当初要修的是什么。

约定：只有**自动化证据**（测试 / 构建 / 真实服务冒烟）才算完成；只写了代码不算。

> 本版新增：① 上一轮（9-24 13:03）未写进本文档的 `tests/test_survey_mode.py`；
> ② 本轮（9-24 晚）复核时发现并修掉的 6 个真实缺陷（含一个会崩进程的缺陷）；
> ③ 清理记录。**"本轮复验"列**标明该条证据是这次重新跑过的，还是沿用上一轮记录。

---

## 0. 本轮复核结论速览

| 项 | 结论 |
|---|---|
| 计划书 §2 前端问题 F0–F4 | 5/5 工作包有自动化证据；F4 的"交付形态"本轮补齐（见 §2 修复 6） |
| 计划书 §3 科研能力 R0–R6 | 7/7 有自动化证据（单元 + 图入口 + 真实 HTTP）；领域级验收仍需人工 |
| 计划书 §4 结构调整 | 已按建议落地（`reporting.py` / `modeling.py` / `readonly_data.py` / `views/*` / `styles/*`） |
| 计划书 §5 门槛 1–5 | 5/5 有自动化证据 |
| 计划书 §5 门槛 6（领域人审） | **未完成（需人工签字）**：材料齐备 `evals/rubric.md` + `evals/cases/rf-fingerprint/` |
| 本轮验证 | `pytest` **631 passed**（8m41s）；前端 `vitest` **62 passed**；`tsc --noEmit` 干净；`node tests/js/bundle_security.test.js` **11 项全过**；`vite build` 通过且哈希与入库产物一致 |
| 本轮修复 | 6 项修复 + 1 项顺带修复：1 个崩进程（`0xC0000005`）、1 个使整套测试挂死（SSE 长连接读法错误）、2 个测试写回仓库（隔离漏项 / 硬编码路径绕过配置）、1 处线程清理方式错误、1 个交付形态缺口（`dist/` 不入库且无构建步骤）、1 个"靠真实运行缓存伪通过"的用例 |
| 清理 | 删除约 36 MB 无用缓存/测试残留/旧产物/被取代文档；`references/`（第三方克隆，93 MB）按你的选择保留 |

---

## 1. 计划书条目逐项对照

### 1.1 §2 前端（F0–F4）

| 计划书要求 | 现状 | 证据 | 本轮复验 |
|---|---|---|---|
| F0-1 `researchStateUrl` 用 `URL/searchParams`，无 `problem_id` 不产生 404 | 已完成 | `test_workbench_api.py::test_frontend_does_not_concat_query_with_ampersand` | ✅ 全套通过 |
| F0-2 `problem_id` 精确定位规格；多问题不擅自取第一个 | 已完成 | `test_state_requires_problem_id_when_project_has_many`、`test_state_scopes_objects_to_the_requested_problem` | ✅ |
| F0-3 `objects` 计数契约由服务端统一给出（`reporting.objects_counts`） | 已完成 | `test_state_objects_counts_are_complete`、`test_research_reporting.py::test_count_contract_is_complete_and_uses_one_source` | ✅ |
| F0-4 同 ID 改题冲突 + 继续/新建选择 | 已完成 | `test_start_same_problem_different_request_conflicts` | ✅ |
| F0-5 fork 原子创建可导航新问题 | 已完成 | `test_fork_creates_navigable_new_problem` | ✅ |
| F1 当前研究状态对象（会话/模式/项目/问题/run 统一加载与清空） | 已完成 | `src/web/src/current-research.ts` + `test_frontend_has_single_state_object_and_resume_semantics`、Vitest `current-research.test.ts`(13) | ✅ |
| F1-4 候选问题按稳定 `candidate_id` 确认 | 已完成 | `test_candidate_confirmation_uses_stable_id`、`test_confirm_candidate_rejects_unknown_id` | ✅ |
| F1-5 反馈对象选择器 + 无法定位时澄清 | 已完成 | `test_feedback_object_selector_binds_target`、`test_state_payload_exposes_feedback_targets` | ✅ |
| F2 事件序号/回放/多标签页/状态补偿 | 已完成 | `test_session_event_log_is_replayable`、`test_e2e_http.py::test_reconnect_replays_events_and_state_compensates` | ✅ |
| F2 `respondWith` 先校验响应再改状态、幂等 | 已完成 | `test_respond_requires_waiting_state_and_is_idempotent`、`test_feedback_failure_paths_are_explicit` | ✅ |
| F2 工作台读取绑定 project/problem + 序号，旧请求不覆盖新画面 | 已完成 | `src/web/src/app.ts`（请求序号 + 取消旧请求） | ✅ |
| F3 Markdown 本地消毒渲染 + 协议白名单（不再 `innerHTML`） | 已完成 | `src/web/src/markdown.ts`、`test_markdown_module_uses_dom_api_only`、Vitest `markdown.test.ts`(9)、`tests/js/bundle_security.test.js` | ✅ 11 项 |
| F3 CSP 真实下发、内联脚本迁出 | 已完成 | `test_index_response_sets_csp`、`test_page_logic_moved_out_of_inline_script` | ✅ |
| F3 窄屏布局 / 表格横向滚动 / 详情导航 / 可键盘操作标签 | 已完成 | `base.css`(`@media max-width:1000px`)、`test_frontend_binds_artifacts_and_has_detail_nav`、`test_styles_are_split_out_of_the_template` | ✅ |
| F3 文件清单绑定 project/problem/run | 已完成 | `test_artifacts_filtered_by_problem`、`test_artifacts_owner_metadata`、`test_artifacts_unknown_problem_returns_empty` | ✅ |
| F4 Vite + TS 组织、`views/*` 三段模块、可构建可单测 | 已完成 | `src/web/{package.json,tsconfig.json,vite.config.ts}`、`views/{research-workbench,conversation,artifacts}.ts` + 29 项 Vitest | ✅ 62 项 |
| **F4 交付形态：新克隆仓库如何拿到构建产物** | **本轮补齐** | 见 §2 修复 6：README「快速开始 4. 构建前端」+ FAQ 12；`start.bat` 缺产物时自动构建；`setup_env.bat` 新增 [6/6] | ✅ 文档/脚本 |

### 1.2 §3 科研能力（R0–R6）

| 计划书要求 | 现状 | 证据 | 本轮复验 |
|---|---|---|---|
| R0 资料源显式绑定 + 只读 SQLite/CSV 适配器 | 已完成 | `src/kb/sources.py`、`src/kb/adapters/readonly_data.py`、`tests/test_readonly_data.py`、`test_sources_and_attribution.py` | ✅ |
| R0 选择 A 库时引用只来自 A；缺失/解析失败/无命中状态不同 | 已完成 | `test_two_problems_in_one_project_do_not_share_sources_or_facts`、`test_single_paper_supporting_a_leaves_b_gap` | ✅ |
| R1 正式图入口注入语义提议器；非法提议被拒；停用 LLM 可降级 | 已完成 | `src/graph/theory_pipeline.py::_attach_proposer`、`tests/test_research_proposal.py`、`test_research_autonomy.py` | ✅ |
| R2 领域建模：≥2 候选机制、量纲/边界/未覆盖因素、冲突预测→可区分检验 | 已完成（自动化部分） | `src/research/modeling.py`、`tests/test_research_modeling.py`(10) | ✅ |
| R3 逐命题证据归属（`EvidenceLink` 为准，不从全局证据推断） | 已完成 | `test_evidence_refs_are_attributed_per_claim`、`test_single_paper_supporting_a_leaves_b_gap` | ✅ |
| R4 可审查论证链 + 逐项新颖性差异 + 写作缺口回流 | 已完成 | `src/research/argument.py`、`novelty_compare.py`、`tests/test_research_argument.py`、`test_research_review_quality.py` | ✅ |
| R5 由缺口生成仿真建议；草案与"信息足够可执行"分离 | 已完成 | `src/experiments/{planner,validator}.py`、`tests/test_theory_mode.py` | ✅ |
| R6 统一 project/problem/run/branch 身份 + 旧库迁移 + start/resume/fork 分离 | 已完成 | `tests/test_research_identity.py`(8)、`test_legacy_snapshot_table_is_migrated`、`test_cli_resume_reports_what_it_actually_did` | ✅ |
| 统一事件键与可聚合指标（含工作台可见日志异常） | 已完成 | `src/research/logging_schema.py`、`tests/test_research_logging.py` | ✅ |
| §4 `research/reporting.py` 成为只读投影深模块 | 已完成 | `src/research/reporting.py`、`tests/test_research_reporting.py`(21) | ✅ |

### 1.3 §5 发布前最小回归门槛

| 门槛 | 现状 | 证据 | 本轮复验 |
|---|---|---|---|
| 1. 请求 URL/工作台字段/多问题/派生/会话切换/重连/反馈失败均有覆盖 | 已完成 | `test_workbench_api.py`、`test_web_frontend.py`、`test_e2e_http.py`、`test_survey_mode.py` | ✅ 631 项 |
| 2. A 资料不出现在仅授权 B 的研究里；A 的证据不满足 B 的缺口 | 已完成 | `test_two_problems_in_one_project_do_not_share_sources_or_facts`、`test_single_paper_supporting_a_leaves_b_gap` | ✅ |
| 3. 正式图路径真实使用提议器；非法提议被拒；失败路线不无限重复 | 已完成 | `test_research_proposal.py`、`test_research_autonomy.py` | ✅ |
| 4. unknown/编码错误/未检索不得升级为确定结论或创新声明 | 已完成 | `tests/test_research_invariants.py` | ✅ |
| 5. 规格关键字段未完成时保持草案；未执行时无"已验证"表述 | 已完成 | `tests/test_theory_mode.py`、`test_research_review_quality.py` | ✅ |
| 6. 领域专家复核端到端题（研究意图/模型忠实度/论证/引用/差异/建议） | **未完成（需人工签字）** | 材料与表格已就绪：`evals/rubric.md`、`evals/cases/rf-fingerprint/{case.md,expected_notes.md}`；自动化不能替代签字 | ⏳ 待人工 |

---

## 2. 本轮（2026-09-24）复核发现并修复的缺陷

这 6 项此前**没有任何文档记录**，其中 2 项会让"自动化证据"本身失效
（整套测试挂死 / 崩进程），另 2 项让"测试不写回工作区"的约定名存实亡。

### 修复 1：`Session.shutdown()` 在后台线程仍在使用连接时关闭 SQLite → 进程崩溃

- **现象**：`tests/test_survey_mode.py` 跑到一半整个 pytest 进程消失，退出码 `-1073741819`
  （`0xC0000005`，STATUS_ACCESS_VIOLATION），faulthandler 栈指向
  `server.py:201 conn.close()` ← `shutdown_sessions()` ← 用例 teardown。
- **根因**：检查点连接是 `check_same_thread=False` 的 sqlite3 连接，后台研究线程仍在
  `app.stream` / 落盘中使用它；`shutdown_sessions()` 只投了 `_STOP` 就立刻 `close()`，
  没有等线程退出。在 C 层访问已释放句柄，Python 的 `except Exception` 拦不住。
- **修复**：`Session` 登记 `worker_thread`（start 与 resume 两处都登记）；
  `shutdown()` 先 `join(timeout=10s)`，**只有线程确实退出才关连接**，否则不关并如实
  返回 `False`；`shutdown_sessions()` 改为"先给全部会话发停止信号，再逐个等待"。
- **验证**：`tests/test_survey_mode.py` 由"崩溃/挂死"变为 **6 passed in 2.5s**；
  全套 631 项不再出现访问违例。

### 修复 2：`tests/test_survey_mode.py`（上一轮 13:03 新增，未进进度文档）三处与契约不符

| 用例 | 原写法 | 实际契约 | 处理 |
|---|---|---|---|
| 启动身份 | `assert body["project_id"]` | 综述会话的身份是 `thread_id/session_id/run_id`；`project_id/problem_id` 是理论研究模式的对象范围，前端也只在 theory 模式发送 | 改为断言运行身份 + 显式断言 `project_id == ""`（不要求服务端凭空造） |
| 事件回放 | `client.get(/events).text` 直接读 | `/events` 是 SSE 长连接：**会话未收尾时该请求永不返回**（TestClient 传输层会一直等 ASGI app 结束），整套测试因此挂死 | 改为先 `stop` 收尾再读完整流（与 `test_e2e_http.py` 一致） |
| 未知资料源 | 期望 `404` | `/api/sources/{id}` 返回 `200 + bindable=false + reason`（前端要拿 reason 展示；start 时不可用才 409） | 改为断言 200 + 不可绑定 + 有原因；`/api/contexts/{不存在}/notes` 仍断言 404 |

- **验证**：`pytest tests/test_survey_mode.py -v` → 6 passed。

### 修复 3：测试隔离不完整，测试把会话/检查点写回仓库

- **现象**：一次全套测试后 `data/checkpoints/` 多出 6 个 SQLite、`data/conversations/`
  多出记录、`data/chroma/chroma.sqlite3` 被改写 —— 而 `tests/conftest.py` 的文档字符串
  明确声称"全部数据目录重定向到临时目录"。
- **根因**：`CHECKPOINT_DIR`（`src/server.py`）、`FIGURE_DIR`（`src/rag/figure_llm.py`）、
  `PDF_DIR/_OA_CACHE_FILE`（`src/tools/pdf_fetcher.py`）等常量在**导入期**就已求值，
  而 `_DATA_BOUND_MODULES` 里没有这些模块；另外 `_guarded_init` 里用了未导入的 `Path`，
  `NameError` 被 `except Exception` 吞掉，守卫实际从未生效。
- **修复**：`conftest.py` 顶部 `from pathlib import Path`；补齐模块清单；新增 `_remap()`，
  凡落在原 `DATA_DIR`/`OUTPUT_DIR` 之下的模块级 `Path` 常量一律重指到临时目录
  （不逐个硬编码常量名，新增常量自动覆盖）。
- **验证**：清空 `data/` 后跑全套 631 项，`data/conversations`、`data/checkpoints`、
  `outputs/figures`、`data/chroma` 的**文件数与 mtime 全部不变**。

### 修复 4：图表/向量库绕过配置写死仓库路径

- `src/rag/figure_generator._ensure_figure_dir()` 直接拼
  `Path(__file__)…/"outputs"/"figures"` → 改 `OUTPUT_DIR` 无效，测试图（`test_*.png`）
  直接落进仓库。改为**调用时**读 `config.OUTPUT_DIR`（与 `server.py::output_dir()` 同一原则）。
- `src/rag/vector_store.get_vector_store()` 用导入期字符串化的
  `CHROMA_CONFIG["persist_directory"]` → 测试仍写仓库 `data/chroma/`。新增
  `persist_directory()`，调用时按 `config.DATA_DIR` + `CHROMA_PERSIST_REL` 重算。
- **验证**：全套跑完 `outputs/figures/test_*.png` 不再出现在仓库；`data/chroma` mtime 不变。

### 修复 5：`test_workbench_api.py` 用 `SESSIONS.pop()` 代替 shutdown

- 该用例注释已承认"残留线程会在夹具恢复 DATA_DIR 之后继续写数据，把文件落回仓库"，
  但处理方式是直接从 `SESSIONS` 里 pop —— 这反而让线程**再也无法被 join**。
  改为 `server_module().shutdown_sessions()`（先停、再等线程退出、最后关连接）。

### 修复 6（交付形态）：新克隆仓库拿不到前端构建产物

- **现象**：`server.py::_index_page_path()` 优先提供 `src/web/dist/index.html`，但
  `src/web/dist/` 被 `.gitignore` 忽略；新克隆只提交了 `src/web/assets/`。
  于是直接 `python -m src.server` 会退回**源码模板**（引用 `/src/main.ts`），
  页面能打开但没有任何脚本、按钮全无反应 —— README 里也没有构建步骤。
- **修复**（按你的选择，保持 dist 不入库）：
  - README 新增「快速开始 4. 构建前端（新克隆的仓库必须做一次）」+ FAQ 12（如何判断没构建）；
  - `start.bat`：缺 `src\web\dist\index.html` 时自动 `npm install && npm run build`，失败/无 npm 时明确警告；
  - `setup_env.bat`：新增第 [6/6] 步构建前端（无 npm 只提示，不中断环境搭建）；
  - README「项目结构」修正前端条目（Vite + TS，`assets/` 入库、`dist/` 不入库）。
- **验证**：`npx vite build` 成功（190 ms），产物哈希与入库的
  `index-BUWrOaZt.js` / `index-DIBHm5xq.css` **完全一致**（说明入库产物与源码同步），
  `git status -- src/web` 无变化。

### 顺带修复：一个"靠真实运行缓存伪装成离线测试"的用例

`tests/test_references_figures.py::test_resolve_venue_arxiv_journal_ref` 的假 arXiv XML
缺 `<atom:id>`，而解析器按真实 API 行为用 `<entry><id>` 与请求的 `id_list` 对齐 →
整条 entry 被当作"无该记录"。它此前之所以"通过"，是因为仓库里
`data/venue_cache.json` 恰好缓存过 `arxiv:2105.04492`（真实运行留下的）。补齐 `<id>` 后
离线即可通过 —— 这正是"测试读写真实运行数据会造成伪通过"的样本。

---

## 3. 仍未完成 / 待人工

| 项 | 说明 |
|---|---|
| §5 门槛 6 领域人审 | **表与材料已就绪，签字待人工**：领域专家按 `evals/rubric.md` 对 `evals/cases/rf-fingerprint/` 逐项判定并填写 `expected_notes.md` |
| R2 / R4 的**领域级**验收 | 自动化已覆盖"不越界、状态一致、模型有候选与舍弃理由"；"模型忠实于领域、论证在领域内成立、建议真能区分两种解释"属人工评审（同门槛 6） |
| 浏览器级验收（CSS/布局/键盘交互/断网后加载） | `test_e2e_http.py` 走真实 HTTP 但不驱动真实浏览器；视觉与键盘可用性仍需人工/浏览器测试 |
| `app.ts` 剩余部分 | 视图纯函数已抽出并类型化；余下 DOM 操作与请求编排仍是 `@ts-nocheck` |
| `start.bat` / `setup_env.bat` 的本轮改动 | 只做静态审阅（执行会真的启动服务/装依赖），未端到端跑过批处理 |

## 4. 已知边界（不得当作已完成）

- **综述（survey）模式没有 `project_id/problem_id`**：身份是 `thread_id/session_id/run_id`。
  计划书 F1 的"当前研究状态"对两种模式不是同构的（theory 才有 project/problem）。若下一版
  要把两者统一，需要先改契约与前端，不能靠给 survey 造一个假 project。
- 逐命题事件（`derive_step` / `verification_recorded` 等）不带 `problem_id`，按问题的指标
  聚合依赖命题归属；只有"每问题一次"的生命周期事件与 `log_anomaly` 是强隔离的。
- `resume` 的"真续跑"只在**同一**检查点存储上成立；换用新的内存检查点时会明确报告
  "从头开始"，并沿用落盘的运行身份。
- 写作缺口的 `blocking/advisory` 划分依据是"当前研究能力能否自行消解"；能人工消除的
  缺口一律 `advisory`，由验收门槛独立拦下，不重复阻塞。
- 关闭会话时若后台研究线程在 10s 内没退出，`shutdown()` **不会**关闭检查点连接
  （宁可有句柄残留，也不崩进程），并如实返回失败；此时 Windows 上该检查点文件可能暂时
  删不掉。
- 本机 `.venv` 是从别处搬来的（`site-packages` 内记录的路径仍是旧的
  `E:\SearchAgents\AIR\.venv`），能跑通全套但不算干净；正式发布前建议重建。

---

## 5. 本轮验证记录

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q` | **631 passed**，1 warning，521s（8m41s） |
| 同上，在**清空 data/ 与 outputs/ 之后**再跑一遍 | **631 passed**，548s（9m07s）；跑完仓库数据目录仍为空（见下） |
| `cd src/web; npx vitest run` | **62 passed**（5 文件：current-research 13 / research-workbench 13 / views 16 / markdown 9 / research-api 11） |
| `cd src/web; npx tsc --noEmit` | 干净（退出码 0） |
| `node tests/js/bundle_security.test.js` | **11 项全过**（含页面初始化与全部内联处理器可解析） |
| `cd src/web; npx vite build` | 成功，190 ms；哈希与入库产物一致 |
| 真实服务冒烟（uvicorn，上一轮） | 首页/CSS/JS/CSP/目录穿越/404 符合预期，页面无内联 script/style |

### 清理后复验（`data/`、`outputs/` 清空状态）

在 `data/chroma`、`data/pipeline_cache`、`data/conversations`、`data/checkpoints`、
`data/research`、`data/venue_cache.json` 与 `outputs/` **全部清空**之后重跑整套：

```
.venv\Scripts\python.exe -m pytest -q     →  631 passed, 1 warning in 547.80s (0:09:07)
```

且跑完后 `data/`（除保留的 `manual_pdfs/`、`pdfs/`）与 `outputs/` **仍为空**，
`data/chroma`、`outputs/figures` 未被重新创建 —— 即：测试不再依赖、也不再写入
仓库里的真实运行数据，"测试不写回工作区"这条约定本轮才真正成立。
（唯一副作用是 Python 的 `__pycache__` 字节码缓存，已清理。）

---

## 6. 本次清理记录（2026-09-24）

保留：`references/`（第三方克隆，93 MB，你的选择）、`data/manual_pdfs/`（7 篇射频指纹
PDF + `meta.json`）、`data/pdfs/`（3 篇下载 PDF）、`src/web/assets/`、`src/web/dist/`、
`.env`、`.idea/`、`.venv/`。

| 类别 | 删除内容 | 说明 |
|---|---|---|
| 验证过程临时文件 | `.pytest_tmp/`(16.6 MB)、`.pytest_tmp2/`(11.1 MB)、`.pytest_cache/`、`.ruff_cache/`、`src/**/__pycache__`、`tests/**/__pycache__`、各类探针目录与日志 | 本轮验证产生；`__pycache__` 共 12 个目录 4.6 MB |
| 测试/冒烟残留 | `data/checkpoints/*`(20 个)、`data/research/cf1.sqlite`（玩具题 `x**2>=0`，无快照） | 由 test_survey_mode / 手动冒烟产生 |
| 会话与缓存 | `data/conversations/*`(374 条)、`data/chroma/`(9.2 MB)、`data/pipeline_cache/`、`data/venue_cache.json`、`data/oa_cache.json`、`data/session_memory.json`、`data/theory_checkpoints.sqlite`、`data/pipeline_checkpoints.sqlite-wal/-shm`(4.96 MB，**主库早已不存在，属孤儿文件**) | 清空后 Web 界面「历史会话」为空；下次综述运行需重新检索 + embedding |
| 旧产物 | `outputs/` 全部内容（2026-09-10 那次射频指纹综述交付 8.1 MB、`figures/` 及测试图 `test_*.png`） | 含被测试覆盖过的 `heatmap_0.png` |
| 被取代文档 | `下一步执行方案.md`(9-16)、`PROJECT_IMPROVEMENT_REPORT.md`(8-20)、`科研Agent技术依据核查.md`(9-21) | 已被 2026-09-23 计划书与本进度文档取代；合计 55 KB |

合计释放约 **36 MB**。`timeline.png` 上一轮已从磁盘删除（git 索引中仍是 `D`，提交后生效）。

### 复验：清空后仍应全绿

清空 `data/`、`outputs/` 后再跑 `.venv\Scripts\python.exe -m pytest -q` 的结果见下。

---

## 7. 新计划书（2026-10-01）执行记录

依据：`AI for Research(AIR)计划书.md`（2026-10-01 版）+ `AI for Research(AIR)下一轮执行计划.md`（v2）。
本轮完成迭代 I 的 I-1（工程脚手架）与 I-2（问题契约）；I-3（自主检索闭环）未开始。

### 7.1 I-2 问题契约（P0-2）—— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 契约对象 | `src/research/schemas.py` | 新增 `TaskKind`（mechanism/formal_proof/empirical_causal/scenario）、`SourceSummary`、`ResearchPath`、`ProblemContract`；`ResearchSpec.contract` |
| 契约判定 | `src/research/question_planner.py` | 新增 `formulate(request, topic, source_summary)`：先判研究类型（依据写进 `basis`），给出 1–3 条研究路径（每条含"能产生什么/还缺什么"），判定不了只问**一个**澄清问题 |
| 候选按契约分派 | 同上 | `generate_candidates()` 按契约产出：无数据不产出经验因果候选；要求机理的问题不被降级为"待收集数据"；`contract` 为空（老规格）保持历史行为 |
| 资料摘要 | `src/kb/sources.py` | 新增 `build_source_summary()`：把资料源规模/全文数 + 题目里的模型/数据措辞整理成 `formulate()` 的输入 |
| 入口接线 | `src/server.py`、`src/graph/theory_pipeline.py`、`src/main.py` | 启动与 CLI 都传资料摘要；启动响应与工作台 `spec.contract` 暴露契约；`_existing_spec_conflict()` 新增"同一请求但研究类型变了"冲突分支 |
| 契约冻结 | `src/research/loop.py` | 用户确认候选时把 `contract.frozen_version` 置为规格版本；事件 `candidate_confirmed` 带 `contract_kind` |
| CLI 绑定资料源 | `src/main.py` | 新增 `--source-set-id` / `--source-set-kind`，并在研究前校验绑定（不可用直接报错退出） |
| 命名/注释清理 | `question_planner.py`、`sources.py` | `AppliedAnalysis`→`EffectDirection`、`parse_applied_direction`→`parse_effect_direction`、`_applied_candidate`→`_causal_candidate`；模块 docstring 与冗余注释精简 |

证据：`tests/test_research_contract.py`（9 项）——同一句"影响"在给定理论模型/给定观测数据/只给文献
三种输入下分别得到 mechanism / empirical_causal / scenario 且推荐路径不同；缺数据不产出因果候选；
声明数据才产出因果候选且不编造 `rows/data_ref`；机理问题不被降级；无材料时只问一个问题；
显式命题走 formal_proof；确认后契约冻结；同请求换研究类型触发冲突；启动响应与工作台都能读到契约。
`tests/test_theory_mode.py::test_effect_direction_and_contract_decide_candidates` 已按新语义更新
（原用例断言"缺数据也生成因果候选"，与 P0-2 目标冲突）。

### 7.2 I-1 盲测基线工程脚手架（P0-1 工程部分）—— 已完成

| 交付 | 文件 |
|---|---|
| 运行手册（两种资料场景 × CLI/Web、命名约定、10 项归档清单、`runs/<日期>/` 约定、运行前自检） | `evals/cases/rf-fingerprint/runbook.md` |
| 失败案例台账（表头与 `rubric.md` §3 逐字一致；只追加不删除） | `evals/cases/rf-fingerprint/failures.md` |
| 领域术语表模板（信道/噪声/设备特征/可分性指标，字段全空，禁止模型代填） | `evals/cases/rf-fingerprint/domain_terms.md` |
| 迁移用例模板（第二领域，领域待人工选定） | `evals/cases/transfer/case.md` |
| 契约测试（含"封存答案不得出现在输入模板"的防泄漏断言，经变异测试验证非平凡） | `tests/test_evals_contract.py`（5 → 14 项） |
| 目录约定同步 | `evals/README.md` |

仍缺（人工）：资料 A/B 全文与封存答案（`expected_notes.md` 仍为空）、第二领域选定、专家签字。

### 7.3 本轮验证记录

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q`（全量） | **666 passed**，1 warning，550s |
| 对比上一轮基线 | 631 → 666（+9 契约、+9 evals 契约、+5 检索式规划、+6 覆盖记录、+6 授权策略；无回归） |
| `tests/test_research_contract.py` | 9 passed |
| `tests/test_evals_contract.py` | 14 passed |
| `tests/test_query_planner.py` | 5 passed |
| `tests/test_retrieval_coverage.py` | 6 passed |
| `tests/test_source_policy.py` | 6 passed |
| 测试是否写回仓库 | 全量跑完后 `data/`、`outputs/` 12 分钟内零写入 |

### 7.4 I-3 自主检索闭环（P0-1 场景 ② / §1 契约第 2 行）—— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 授权策略与覆盖记录 | `src/research/schemas.py` | `SourcePolicy`（user_kb/autonomous/both）、`RetrievalCoverage`（`executed` 区分"检索失败"与"无命中"；检索式/引擎/时间/命中/入库/去重/全文可得/仅摘要/未覆盖/失败，`absorb()` 多轮累加）；`ResearchSpec.source_policy/coverage` |
| 检索式规划 | `src/research/query_planner.py`（新增） | `plan_queries()`：按研究类型排列结果/机理/定义/方法/边界/反例六个角度，每条带"为什么这样问"；术语表变体生成跨语言检索式；无可分解对象时退回整句目标 |
| 采集与合并 | `src/kb/bridge.py` | `gather_sources()`：按策略外搜 → **自动入库**（`ingest_machine`，跨检索式按 DOI/标题去重）→ 与用户资料库走同一条检索路径 → 按 DOI/hash/标题记录键去重合并 → 统计全文/仅摘要 → 写覆盖记录；没有问题契约时沿用"处理+结果"旧口径（保持既有资料库行为） |
| 循环接线 | `src/research/loop.py`、`action_registry.py` | 新增 `retrieval_available`（有资料库或授权自主检索即可开展检索动作）；`_act_retrieve_targeted` 改为消费规划后的多条检索式；入库后自动重新装配知识服务（后续 `read_source` 可读）；覆盖记录随规格落盘并发 `retrieval_coverage` 事件；检索失败与无命中走**不同**路线（`retrieval_failed` / `no_retrieval_hit`） |
| 入口接线 | `src/server.py`、`src/graph/theory_pipeline.py`、`src/main.py` | `StartRequest.source_policy`；CLI `--source-policy`；无预建资料库 + autonomous 不再被拒；工作台 `spec.source_policy/coverage` 可见 |
| 事件登记 | `src/research/logging_schema.py` | 新增 `retrieval_coverage` 必需键（契约测试会校验事件类型是否登记） |

证据：`tests/test_query_planner.py`(5)、`tests/test_retrieval_coverage.py`(6)、`tests/test_source_policy.py`(6) ——
覆盖"user_kb 不外搜 / autonomous 无库可启动 / 多检索式外搜并入库 / 跨检索式去重 / 检索失败与无命中分开 /
入库后同一动作内即可取到带定位证据 / 未授权时不产生覆盖记录"。

### 7.5 迭代 I 剩余（全部需人工，见计划书 §4）

1. 资料 A/B 全文与封存答案（`evals/cases/rf-fingerprint/expected_notes.md` 仍为空）；
2. 第二领域选定（`evals/cases/transfer/case.md` 仍是模板）；
3. 首轮真实运行与失败报告（`runbook.md` 两种场景已就绪，`failures.md` 待填）；
4. 领域专家签字（`evals/rubric.md` §4）。

工程侧可继续的是迭代 II（P0-3 模型合成、P0-4 引文与论证硬约束），见
`AI for Research(AIR)下一轮执行计划.md` §3。

---

## 8. 迭代 II 执行记录（2026-10-01 起）

### 8.1 P0-3 用资料机制真正构建模型 —— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 原文锚点 | `src/research/modeling.py` | 新增 `AnchoredResult` + `build_anchors(claim, evidence)`：**只有带定位（页/节）且有原文片段**的证据才算锚点；锚点带作用路径、条件、噪声、方向 (sign)、形式化片段与来源 |
| 模型合成 | 同上 | 新增 `synthesize_models(contract, anchored_results)`（计划书 §3 接口）：按**结构签名**（机制动词 + 机制因子 + 条件 + 对象）分组，不同结构才算不同机制；无锚点 → 只给"待检假设"且 `selected=""`；只有一条结构 → 如实写 `note="无法形成竞争模型"`，不硬凑对照 |
| 机制内容 | 同上 | 关系/条件/噪声/形式化片段/假设全部来自原文锚点并标注 `[文献] 定位`；预测由**原文方向 + 条件**生成（"在…下, 结果随处理增大而下降"），可机器比较 |
| 冲突判定 | 同上 | 删除按措辞判冲突的 `_contradict`；改为"同一对象下方向相反且条件相容"（`sign` + `_conditions_compatible`），冲突记录带 `basis` |
| 选择规则 | 同上 | `_select()`：只在**有来源且有可观测预测**的候选里选；否则 `selected=""` + 说明。删除模板双机制（`mdl-empirical`/`mdl-simplified`）与 `_relation_text`/`_encoding` 回填命题表达式 |
| 接线 | `src/research/loop.py` | `_act_propose_model` / `model_comparison` 传入 `spec.contract` |

证据：`tests/test_research_modeling.py`（12 项）——同一机制的两种表述只算一条（不因改写句子造出假竞争）；
路径/条件不同才形成两条候选；**有原文但无定位 → 不算锚点**；无锚点只给待检假设且不选中；
方向相反的带定位结果 → 冲突 + 可区分检验；无方向冲突 → 条件必要性检验。
`tests/test_research_capability.py`、`tests/test_research_review_quality.py` 已按新语义更新用例输入。

### 8.2 P0-4 引文与论证关系硬约束 —— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 引文可定位 | `src/research/evidence.py` | 新增 `quote_is_locatable()`（引文非空足够长 + 有页/节 + 真的出现在原文片段里）与 `mark_pending()`（降为待审并保留原判定供审查） |
| 强关系收紧 | 同上 | LLM 判定：`supports/partially_supports/contradicts` 必须给出**可定位引文**，空引文或定位失效 → `insufficient` 待审；`support_evidence` 不再回退摘要开头 |
| 规则判定降级 | 同上 | `assess_support()` 不再产出强 `supports`（最多 `partially_supports`），方向相反记为"待核反例候选"，且不再把摘要开头当依据 |
| 显式对象引用 | `src/research/schemas.py` | `ProofStep.obligation_ref`；`VerificationRecord.obligation_ref` + `link_status` + `checked_formula` / `premises` / `output_digest` |
| 移除猜测归属 | `src/research/argument.py` | `_match_record` 不再回退"同一命题同版本的验证记录"；显式引用 → `confirmed`，旧数据隐式关联 → `unconfirmed`；论证链对"关联待确认"和"有义务却对不上验证输入"的步骤显式写入 caveats |
| 交付门槛 | `src/research/acceptance.py` | 以 `textual_support/informal_argument` 支持的强结论必须有一条**带引文且带页/节**的证据，否则门槛拦下 |

证据：`tests/test_evidence_locator.py`（10 项）——规则判定不出强支持；空引文/引文不在原文/缺页节定位
三种情况都降为待审且保留原判定；有引文+定位才接受；**删掉引文后同一判定不再算支持**；
改掉原文前提后引文对不上 → 降为待审；交付门槛对"无定位引文的强结论"报错。
`tests/test_research_argument.py` 新增 2 项：旧数据隐式关联标 `unconfirmed` 并进 caveats；
无引用无痕迹的记录**不被静默挂到步骤上**。

### 8.3 本轮验证记录

| 命令 | 结果 |
|---|---|
| `pytest -q`（全量，含 P0-3/P0-4 改动） | **679 passed**，1 warning，540s（对比迭代 I 的 666：+12 建模、+10 引文、+3 论证-接线；无回归） |
| `tests/test_research_modeling.py` | 12 passed |
| `tests/test_evidence_locator.py` | 10 passed |
| `tests/test_research_argument.py` | 25 passed |

### 8.4 迭代 II 剩余 / 下一步

- P0-4 的"**反例必须核验满足命题前提**"目前仍由既有义务/验收规则承担，尚未单独收紧（迭代 III 一并处理）；
- 迭代 III：P1-1 有界命题比较（`compare_prior` + 检索范围与证据库分离）、P1-2 失败驱动的路线修订
  （失败类型化、`no_progress` 判定、路线闭环字段）。

---

## 9. 迭代 III 执行记录（P1-1 已完成）

### 9.1 P1-1 从词面匹配升级为有界的命题比较 —— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 新接口 | `src/research/novelty_compare.py` | `compare_prior(claim, source_result)`：接受 `SourceEvidence`（已读原文）或 `NoveltyComparisonRow`；`compare_with_row` 保留为内部逐维比较 |
| 结构化抽取 | 同上 | `_fill_from_evidence()`：从**已读原文**抽前提（条件句）、结论（首句/结果句）、方法（仿真/实验/推导/测量…）、适用域与**定位**（页/节）；抽不到就留空并判 `not_comparable`，不用标题相似度凑数 |
| 形式化辅助 | 同上 | `_formal_implication()`：两边都是同一比较符的闭式表达式时，用 sympy 判断蕴含方向（相等→`same`，可证非负→更强/更弱）；**证不出来就返回空并保持不可比**，不猜 |
| 域口径统一 | 同上 | `_domain_tokens()`：把 `x∈real` 与中文"实数域/任意复数"归一为 `dom:real` 等，使条件比较在中英混排原文上成立（此前中文原文一律判"不可比"） |
| 定位落库 | `src/research/schemas.py` | `NoveltyComparisonRow.locator` |
| 范围分离 | `src/research/schemas.py`、`novelty.py` | `NoveltyRecord.evidence_scope`（**授权研究证据库**）与 `retrieval_scope`（本次**新颖性检索范围**：通道/覆盖/未覆盖）；`render_novelty()` 两行分开写，并禁止"首次/原创发现"字样 |
| 接线 | `src/research/loop.py` | `_act_compare_novelty()` 把 `spec.source_set_id/domain` 作为证据库、把 `spec.coverage`（检索式/未覆盖）作为检索范围写入记录 |

证据：`tests/test_novelty_scope.py`（7 项）——已知等价→`equivalent`；同结论不同条件→条件关系而非等价；
缺全文→`not_comparable` 且写明缺什么；**题名相似永不判等价**；带定位原文→等价并附定位；
授权证据库与检索范围分别出现在记录与渲染中；未接入检索时保持 `unchecked` 且有界表述。

### 9.2 本轮验证记录

| 命令 | 结果 |
|---|---|
| `pytest -q`（全量，含 P1-1） | **686 passed**，1 warning，552s |
| `pytest -q`（全量，含 P1-1 + P1-2） | **694 passed**，1 warning，625s |
| `pytest -q`（全量，含 P1-1/P1-2/P1-3） | **700 passed**，1 warning，608s |
| `pytest -q`（全量，含 P1-1…P1-4） | **706 passed**，1 warning，678s |
| `pytest -q`（全量，含 P1-1…P1-4 + P2 浏览器/离线） | **713 passed**，1 warning，**44s**（+7：浏览器流程与静态守卫、离线隔离、缺产物报错；测试默认离线后 678s → 44s） |
| `pytest -q`（全量，P2 收官：去 `@ts-nocheck` + 事件委托） | **713 passed**，1 warning，44s（无回归） |
| `pytest -q`（全量，P2 整链浏览器验收） | **714 passed**，1 warning，47s |
| `pytest tests/test_browser_web_flow.py` | 3 passed（单页流程 + 整链流程 + 静态守卫） |
| `cd src/web && npx tsc --noEmit` | **0 error**（`app.ts` 已无 `@ts-nocheck`） |
| `pytest tests/test_browser_web_flow.py` | 2 passed（真实 Chromium 走完页面流程） |
| `cd src/web && npx vitest run` | 67 passed（5 → 6 个文件，新增契约测试） |
| `cd src/web && npx tsc --noEmit` | 0 error |
| `node tests/js/bundle_security.test.js` | 全部通过 |
| `tests/test_novelty_scope.py` | 7 passed |
| `tests/test_route_revision.py` | 8 passed |
| `tests/test_manuscript_traceability.py` | 6 passed |
| `tests/test_research_review_quality.py` | 16 passed |
| `tests/test_theory_mode.py` 组合连跑 2 次 | 79 passed / 79 passed（拒绝动作不再空转） |

### 9.3 迭代 III 剩余

- P1-2 失败驱动的路线修订（见下节，已完成）。

### 9.4 P1-2 从"下一动作提议"扩展到失败驱动的研究路线修订 —— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 失败类型 | `src/research/schemas.py` | 新增 `FailureKind`（source_missing / model_refuted / formalization_failed / tool_unknown / budget_exhausted）；`ResearchRoute` 增加闭环字段 `target_gap / expected_observation / action_ref / actual_outcome / next_judgement`、`failure_kind`、`substantive_change` |
| 分类与映射 | `src/research/routes.py` | `classify_failure()` 把 20 余处既有自由文本失败类型（no_retrieval_hit / counterexample / encoding_mismatch / unknown / timeout / route_exhausted …）归入五类；`NEXT_ACTIONS` 给出每类允许的下一动作：资料缺失→扩大检索/读原文；模型被反例否定→换机制/改假设/换路；形式化失败→先修编码；工具未知→换后端；预算耗尽→部分交付/停止 |
| 实质变化闸门 | 同上 | `RouteManager.revise()`：必须给出新条件、新子命题或不同机制，否则记 `no_progress` 并返回**停止理由**；唯一例外是"工具未知"时换一个**没试过**的后端（`tried()` 改为按归类后的失败类型比较，避免同一后端反复重试） |
| 路线闭环 | 同上 | `record_failure()` 把"实际结果"与"下一判断"写回当前路线；`revise()` 写"预期可观察结果"与"本次新在哪里" |
| 引擎接线 | `src/research/loop.py` | `_switch_change()` 按"新机制 > 新条件 > 新子命题 > 换后端"组装实质变化；换路被拒时记 `_stopped_reason` 并如实说明无法继续；状态新增 `failure_next_actions` / `failure_judgement` |
| 排序与成本 | `src/research/coordinator.py` | 失败类型映射的动作获得最高优先级并写明"按失败类型选择下一动作(依据)"；昂贵动作（cost ≥ 3 或逼近预算）在理由中附"预期信息增益 + 成本上限"并设终止条件 |

证据：`tests/test_route_revision.py`（8 项）——五类失败映射到互不相同的下一动作；
失败记录带类型与下一动作、路线闭环字段被写回；**只换工具名/同一后端 → 拒绝并给停止理由**；
给出不同机制 → 生成新路线且策略按失败类型选择；未试过的后端算实质变化；路线用尽 → 停止并说明；
失败类型提升对应动作排序并带解释；昂贵动作理由含信息增益与成本上限。

---

## 10. 迭代 IV 执行记录（P1-3 已完成）

### 10.1 P1-3 让建议与成文继承真实模型 —— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 模型导出的建议 | `src/experiments/planner.py` | `design_experiment(..., model=…)`：被选模型的**变量与单位、方程、边界、机制**进入规格（单位优先于命题文本识别）；模型进入假设与替代解释；规格记录 `model_ref/model_source` |
| 生成闸门 | `src/research/loop.py` | 只对**有竞争预测或未闭义务**的对象生成建议；否则拒绝执行并**记住该拒绝**（`_refused` + `_dismissed_actions`），避免同一预算里反复空转 |
| 必备要素清单 | `src/experiments/validator.py` | `REQUIRED_ELEMENTS`（计划书逐项 10 条）+ `missing_elements()`；缺项写入规格 `missing_elements`；**有缺项时不得标记为可执行规格**（校验直接报错） |
| 可复核清单 | `src/research/package.py` | manifest 新增 `source_set`（资料集合 id/策略/检索式/未覆盖/**文件 hash 与原文定位**）、`model_config`（各角色模型 + 提示词版本状态，未版本化时如实写明）、`budget_limits`、`manuscript_traceability` |
| 正文可反查 | `src/agents/theory_writer.py` | `trace_manuscript()`：核心论断是否有映射、锚点是否出现在正文、命题段落是否有对象标签；`writing_map` 与正文不一致时如实报出 |

证据：`tests/test_manuscript_traceability.py`（6 项）——规格由被选模型导出（方程/单位/边界/机制）；
两个竞争模型产出不同设计、不同判据、不同 `model_ref`；缺关键要素时维持草案且**不得**升级为
`spec_validated`；要素清单覆盖计划书 10 项；manifest 带四块可复核清单；正文缺映射/锚点不一致时被标出。

### 10.2 本轮顺带修掉的缺陷

- **拒绝的动作会被反复挑选**：`_act_design_experiment` 新增闸门后返回 False 却不留痕，协调者会在同一预算里
  重复选中它（实测导致 `test_theory_pipeline_graph_export` 偶发因预算耗尽而门槛不通过）。
  新增 `_refused`（按 对象+路线 记录）并并入 `_dismissed_actions()`；连跑两次该组合用例均 79 passed。

### 10.3 P1-4 可见文本与外部指令的信任边界 —— 已完成

| 改动 | 文件 | 说明 |
|---|---|---|
| 片段级样式元数据 | `src/kb/parse.py` | 新增 `Span`（文本/页码/bbox/字号/颜色/异常标记）与 `flag_span()`：**接近背景色**（相对亮度 ≥ 0.85）、**极小字**（< 4pt）、**页外**（超出页面边界）、**被图形覆盖**（bbox 落在填充矩形内）；`read_pdf_spans()` 用 `page.get_text("dict")` + `get_drawings()` 提取 |
| 读取接口 | 同上 | `read_source(path) → SourceReading{text, spans, flags, trust}`（计划书 §3 接口）；异常片段**保留原文**并带页码与坐标定位，`trust=needs_review` |
| 异常清单入库 | `src/kb/schema.py`、`ingest.py`、`service.py` | `LitRecord.visibility_flags` 随文档落库；`resolve()` 返回该字段 |
| 证据降级标记 | `src/kb/bridge.py` | 来自含异常片段文档的候选证据，`notes` 写明"需核对…不作为强证据"（带定位） |
| 定界说明 | `src/utils/external_data.py` | `VISIBILITY_LABEL` / `describe_visibility()` / `mark_spans()`；`_GUARD_NOTE` 明确异常片段不得作为问题要求、研究行动或强证据；注入扫描照旧记录不执行 |
| 工作台提示 | `src/research/reporting.py` | 证据投影新增 `notes` 与 `needs_review` 字段 |

证据：`tests/test_visibility_trust.py`（6 项，**合成 PDF**，用内置中文字体）——正常白底黑字无标记；
近白字/极小字/页外字分别被标记且**可定位**（页码 + 坐标）；**50% 灰的图注不被误标**（验收要求：
浅色图注不得因单一颜色阈值被无条件删除）；隐藏片段里的注入文本被扫描记录、原文保留、
`mark_spans` 给出"保留原文但不作为问题要求/行动/强证据"的处理说明；
入库→检索→证据链路上异常标记一路带到证据备注。

---

## 11. 迭代 IV 执行记录（P2 前端与交付形态）

### 11.1 用真实浏览器驱动页面, 抓到两个"页面看着正常其实不能用"的缺陷

引入 **Playwright + Chromium**（`src/web/tests/e2e/web_flow.mjs`，由 `tests/test_browser_web_flow.py`
在隔离数据目录下起 uvicorn 后驱动）。首跑即失败，暴露出此前所有静态测试都看不到的问题：

| 缺陷 | 现象 | 修复 |
|---|---|---|
| **严格 CSP 拦掉内联事件处理器** | 浏览器报 `Executing inline event handler violates ... 'script-src 'self''`；页面能加载、`window.AIR` 也在，但**所有按钮与模式切换全部无效** | `src/web/index.html` 去掉全部 17 处 `on*=` 属性；改为在打包模块 `app.ts` 的 `bindStaticHandlers()` 里 `addEventListener` 显式绑定（含三个标签页的 click/keydown）；新增模板回归测试 |
| **理论测试真实调用 LLM** | `THEORY_PROPOSER=0` 只关提议器，流水线仍把真实 LLM 注入引擎用于推导 → 同一条代数验收题**偶发失败（约 1/5）**、单次约 50s、且真的计费 | 新增 `THEORY_LLM=0` 离线开关（`_role_llm()` 返回 None）；`tests/conftest.py` 默认置 0；离线后同一题 **6/6 稳定通过、6 次共 9s**；`tests/test_llm_offline_guard.py` 固定该边界 |

浏览器验收覆盖（`test_web_flow_in_real_browser`，通过时输出逐步 PASS）：页面加载且打包脚本真的执行
（`window.AIR` 存在）→ 展开高级选项、切到理论研究模式 → 提交代数问题 → **科研工作台出现结论对象** →
对象级反馈控件存在 → 窄屏（420px）布局切换为纵向且启动按钮仍可见 → 键盘 Tab 焦点在标签页间移动 →
全程无 JS 报错、无 4xx 资源。

### 11.2 其余 P2 改动

| 改动 | 文件 | 说明 |
|---|---|---|
| 缺构建产物的行为 | `src/server.py` | 缺 `src/web/dist/index.html` 且非开发模式时返回 **503 + 可操作构建提示**（`npm install` / `npm run build` / `AIR_WEB_DEV=1`），**不再**返回引用 `/src/main.ts` 的源码模板；开发模式（`AIR_WEB_DEV=1`）才回退模板 |
| 前后端契约集中定义 | `src/web/src/contracts.ts` | 会话事件、会话状态、问题契约、检索覆盖、工作台状态（含 P1-3 `missing_elements`/`model_ref`、P1-4 `notes`/`needs_review`）、对象级反馈、交付清单（含 `source_set`/`model_config`/`budget_limits`/`manuscript_traceability`） |
| 契约测试 | `src/web/tests/contracts.test.ts` | 用真实 payload fixture 走类型与字段检查（vitest 62 → **67**） |

### 11.3 未完成项（如实记录）

- ~~`app.ts` 的 `@ts-nocheck` 尚未移除~~ → **已完成，见 §12**。
- 浏览器验收暂未覆盖"选资料 → 确认候选 → 导出"整链（需要 LLM 与真实资料库），
  以及断线重连的浏览器级用例；当前覆盖的是**构建产物可运行 + 模式切换 + 提问 + 工作台
  + 工作台动作点击 + 窄屏 + 键盘**。

---

## 12. 迭代 IV 执行记录（P2 收官：类型化与事件委托）

### 12.1 `app.ts` 摘掉 `@ts-nocheck`（实测 369 → 0）

计划书 P2 要求"逐段移除 `@ts-nocheck`"。做法与证据：

| 步骤 | 结果 |
|---|---|
| 去掉 `@ts-nocheck` 实测基线 | **369 行** `tsc` 错误（TS2531 105 / TS2339 98 / TS7006 86 / TS18047 49 …） |
| 新增 `src/web/src/dom.ts` | 类型化 DOM 访问（取值/写值/显隐/占位/滚动/选项），元素缺失时安全跳过 |
| 机械迁移 | `$('id').value` 类读取 44 处、赋值 37 处、`style.*` 12 处改走封装；`$()` 调用从 159 处降到 **0** |
| 局部变量与内联调用 | 26 处 `const x = $('id')` 改为带元素类型的 `byId<T>('id')!`（与原行为一致：元素缺失本来就抛错） |
| 参数标注 | 箭头/函数共 191 处补上显式类型；仅**后端 JSON 载荷**保留显式 `any`（载荷形状由 `contracts.ts` 描述） |
| 逐条收尾 | 死状态 `lastResearch`、死函数 `clearCache`/`openHistory` 删除；`window.AIRMarkdown` 从 `unknown` 改为真实接口类型 |
| 最终 | **`tsc --noEmit` 0 error**，`@ts-nocheck` 已删除；vitest 67 passed；bundle 安全测试全通过 |

### 12.2 同一类 CSP 缺陷的第二半：动态生成的 `onclick` 同样是死的

模板修好后，浏览器仍会拦掉**运行时拼接的 HTML 里的内联处理器** —— 工作台的
"展开结论详情 / 施加反馈 / 从快照派生 / 刷新工作台 / 选择研究问题"按钮此前全是死的
（`app.ts` 里 5 处 `onclick="..."` 字符串拼接）。改为：

- 生成 `data-action="动作名" data-id="…"`；
- `PAGE_ACTIONS` 动作表 + `bindDelegatedActions()` 文档级事件委托统一分发
  （新增动作只改一处，模板与视图不再出现内联处理器）；
- 浏览器验收新增一步：**真的点击"展开结论详情"并断言工作台内容发生变化**。

证据：`tests/test_web_frontend.py` 增加"app.ts 不得生成内联处理器 + 必须存在
`data-action`/`bindDelegatedActions`"断言；`tests/js/bundle_security.test.js` 的处理器清单
改为委托动作清单；浏览器流程 7 步全 PASS（含新增的点击生效一步）。

### 12.3 整链浏览器验收：选资料 → 启动研究 → 交付物 → 断线重连

`src/web/tests/e2e/web_flow_full.mjs`（由 `tests/test_browser_web_flow.py::test_full_chain_in_real_browser`
驱动，pytest 侧用合成 PDF 建好资料集并在隔离目录起服务）覆盖：

| 步骤 | 断言 |
|---|---|
| 选资料 | `#sourceset` 下拉必须列出测试资料集，选中后 `#sourceinfo` 显示文档规模 |
| 启动研究 | 提交可离线判定的问题后，工作台出现结论对象 (`clm-`) |
| 交付物 | 切到 outputs 标签页必须列出交付包 (`manifest.json`/`manuscript`) 且含本次项目/快照标识 |
| 断线重连 | 拦掉 SSE 后启动新会话：**状态补偿接口必须被调用**（断线不得静默）；解拦后事件继续、工作台重新出现结论对象 |

`pytest -q`（全量，P2 收官）: **714 passed**, 47s；浏览器用例 3 项全通过。

**仍未覆盖**：候选路线确认的**点击**只在下拉/中断卡片渲染层面被单测覆盖 —— 离线规则路径对
可离线判定的问题不会发起 `theory_candidates` 中断（`needs_confirmation=False`），
需要真实 LLM 提议器才会出现多候选；该交互已由 vitest 与 `tests/test_theory_mode.py` 的假 LLM
用例覆盖，浏览器级点击留待人工资料/LLM 就绪后补测。

### 12.4 一键启动脚本更新（start.bat）

| 能力 | 说明 |
|---|---|
| 参数 | `start.bat`（生产）/`--no-build`（跳过构建）/`--no-browser`/`dev`（开发模式）/`--help` |
| 自动构建 | 产物**缺失或比源码旧**（`src/web/src/**`、样式、`vite.config.ts`）时自动 `npm install` + `npm run build`；新鲜度判断放在 [scripts/frontend_needs_build.ps1](scripts/frontend_needs_build.ps1)，避免批处理里转义 PowerShell 管道 |
| 失败即停 | npm 缺失、`npm install`/`npm run build` 失败、构建后仍无 `dist/index.html` → 明确报错并退出，**不再启动一个页面打不开的服务** |
| 开发模式 | `start.bat dev` = 后端 8000（`AIR_WEB_DEV=1`，提供源码模板）+ 独立窗口的 vite 5173；[vite.config.ts](src/web/vite.config.ts) 新增 `/api` 反向代理与 IPv4 绑定 |
| 脚本化调用 | 带参数调用时不等待按键（`AIR_NO_PAUSE`），便于 CI 与自动化 |
| 编码 | 仍是 GBK（`chcp 936`），中文提示在 cmd 窗口正常显示 |

实测证据：`start.bat --help` 退出码 0；`start.bat --no-browser` 在产物过期时自动重建并启动，
`GET /` 返回 **200 且引用打包产物**（不含 `/src/main.ts`）、`GET /api/sources` 返回 200 空列表；
重建后新鲜度检查返回 0；开发模式实测 `http://127.0.0.1:5173/` 返回 200 源码模板、
`http://127.0.0.1:5173/api/sources` 经代理返回 200。












