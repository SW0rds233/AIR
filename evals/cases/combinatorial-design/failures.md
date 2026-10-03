# 失败案例与缺陷台账：组合设计存在性（`combinatorial-design`）

> 依据 `evals/rubric.md` §3。本文件登记两类记录:
> 1. **真实使用/运行中发现的缺陷**（下表, `CD-NNN`）—— 含打开前端页面产生的 404 这类
>    使用体验缺陷, 因为它们同样影响"系统是否可以开始真实测试"的判断;
> 2. **评审失败项**（数学/方法专家在 `rubric.md` §2 判 `不通过` 的条目）。
>
> 运行手册见 `runbook.md`；维度定义见 `evals/rubric.md` §2。
> 编号递增、不复用、不重排；历史记录只追加不删除。

## 1. 缺陷与失败记录

| 编号 | 日期 | 维度 | 现象（含文件/行位置） | 期望 | 处理 | 状态 |
|---|---|---|---|---|---|
| CD-001 | 2026-10-03 | 交付诚实度 | 服务端日志每次打开页面出现 `GET /favicon.svg HTTP/1.1" 404` | 页面声明的图标必须可访问（`link rel="icon" href="/favicon.svg"` 就在模板里） | 只登记了 `/favicon.ico` 路由，未登记 `/favicon.svg`；新增 `favicon_svg()` 并抽出 `_favicon_response()`（优先构建产物、退回 `src/web/public`）。回归：`tests/test_browser_web_flow.py::test_favicon_routes_are_served`、`src/web/tests/e2e/draft_identity.mjs` 第 4 步 | 已修复(test_favicon_routes_are_served) |
| CD-002 | 2026-10-03 | 研究意图 | 切到理论模式后出现 `GET /api/research/proj-muroooh1pyw4/state?problem_id=p1 404` ×3，界面显示"读取工作台失败 (404): 该项目下没有研究问题规格" | 草稿身份（尚未启动研究）不应探测工作台；应显示空态说明而不是报错 | 切模式时会生成一个**草稿**项目 id（R2: 让附件在稳定身份下上传），而刷新工作台的判据是"项目 id 与状态里的不同"，草稿也会写进状态 → 判据被填平。改为显式记录草稿 id（`draftUnboundProjectId`）：只有草稿自己不查，换成手填/历史会话 id 一律放行。回归：`tests/test_browser_web_flow.py::test_draft_identity_does_not_probe_workbench`（含反向保护：手填真实项目仍须查询） | 已修复(test_draft_identity_does_not_probe_workbench) |
| CD-003 | 2026-10-03 | 研究意图 | 人工运行 `Problem 2` 后的系统输出：事件流中控制器连续 16 轮提议 `clarify_problem`（分别指向命题与不同义务），每轮都被判"有进展"，运行"卡在澄清步骤"直至预算耗尽 | 澄清是**终态判断**：一旦判定需要澄清就必须停下来把问题交给用户，不得反复提议同一动作 | `ActionType.clarify_problem` 此前只作为普通动作派发（`lambda a: True` = 有进展），于是循环永不终止。改为在 `step()` 里直接终态收尾（置 `needs_clarification`、`done`、写入澄清理由），并让 `run()` 的两个循环条件都排除澄清态，防止收尾重开再推进。回归：`tests/test_theory_mode.py::test_clarify_decision_stops_the_loop_instead_of_repeating`（固定提议器，断言 ≤2 个动作）、`test_clarify_by_controller_ends_the_graph_with_a_memo` | 已修复(test_clarify_decision_stops_the_loop_instead_of_repeating) |
| CD-004 | 2026-10-03 | 研究意图 | 题面（`problem2.md`，含 v/k/λ 与 b/r）作为**「补充问题说明」附件**上传、输入框只写一句"研究此问题…"时，系统报"缺少可检验对象"并请求澄清；而同一题面直接粘进输入框则正常给出结论 | 附件里的科学约束必须参与形式化；用户不该为了"把题面交给系统"而被迫改用另一种入口 | 四个连锁缺陷：(1) `uploads.problem_text()` 全局**零调用** —— 附件正文算进 `state["attachment_candidates"]` 后再无读者；(2) `TheoryState` 未声明 `attachment_ids`，该键根本不进图状态，`_engine_from_state` 读不到；(3) `start_session` 落盘的 `session.request` 漏了 `attachment_ids`（续跑/回看无法复现"题面在附件里"）；(4) 题面能被精确形式化时仍先要求用户选"研究方向候选"。修复：引擎新增 `attachment_ids` 与 `attachment_text()`（读取失败/归属不合规时记 note，不静默当作"用户没给"）；`bootstrap` 把附件正文并入形式化来源，并在能精确形式化时**直接进入判定、不生成候选**；`TheoryState` 声明该键；`session.request` 记录附件/资料源字段。回归：`tests/test_design_feasibility.py::test_problem_text_supplied_as_attachment_is_used_for_formulation`（端到端，含"负向验证：去掉附件读取该用例失败"） | 已修复(test_problem_text_supplied_as_attachment_is_used_for_formulation) |
| CD-005 | 2026-10-03 | 写作缺口回流 | 真实测试（题面走附件、请求"研究附件中的问题…"）产出 `delivery_level=条件性研究报告`：事件流 `#11 写作缺口回流: 1 条新义务` 之后停止，遗留一条 `writing_missing_argument_chain / required=True -> open`（"结论 clm-* 没有可展示的推导步骤"），未见成文；同一份 `manifest.json` 自相矛盾（`gate_passed=false` 而 `delivery_gate_passed=true`） | 写作缺口只有在**真的缺推导**时才可回流为阻塞义务；回流出的阻塞义务必须被真正消解，不得只登记不处理；交付物内三道门槛字段必须各自独立、自洽 | 三处独立成因：(1) `argument.writing_gaps` 判定"没有可展示的推导步骤"只看 `attempts.steps`，不认设计证书里的判定链（`certificate_of` → 定理/条件/输入/结论逐条可反查），于是对计数/设计类结论**误报**缺口 → 新增 `_has_certificate_chain()`，有证书链即不算缺口（反向用例保证真缺推导时仍报）；(2) 理论图 `theory_finalize → END`，而"缺口回流→重开研究循环"只存在于 `loop.run()` 的内层循环里，图路径逐步驱动**不经过**它 → 新增 `_route_after_finalize` 回边 + `theory_step_node` 重入时调用 `engine.resume_after_reflow()` 解除持久化的 `done`（否则 `step()` 立刻返回、什么也不做）+ 收尾在缺口未消解时**不导出交付包** + `THEORY_REFINALIZE_MAX`（默认 3）防死循环；(3) `_refresh_manifest` 把 `delivery_gate_passed` 写成出版门槛结论，覆盖了真实的研究/表达门槛 → 三个字段各自独立并接受调用方传入的判定。回归：`tests/test_writing_gap_recovery.py`（7 项）；真实模型同一路径复跑 4 次均为"完整论文 + PDF、无未关闭义务" | 已修复(test_certificate_chain_counts_as_argument_chain / test_graph_routes_back_to_research_loop_when_gap_reopened / test_manifest_keeps_the_three_gates_independent) |
| CD-006 | 2026-10-03 | 引用与差异 | 真实运行产出 `.tex`/`.pdf`，但（a）排版粗糙（每节打成"1 1 引言"、英文摘要被编成编号章节、"参考文献"打印两遍、作者/单位重复、参考文献条目丢序号、判定链挤成一整段）、（b）**完全没有文献检索与引用**（覆盖记录 `origins=not_executed`、`references.json` 为空、第 4 节写"未检索不等于无先例"） | 出版级交付应：章节编号唯一、引用编号与文献表一致且可见、参考文献章节与文献表相邻；授权允许自主检索时**必须真的检索**（本地无库也要走外部），检索到相关工作要能引用，且不得声称其支持本文结论 | 13 处成因（引用侧 6 + 排版侧 7）：(1) "本地无资料库"直接放弃检索 → 改为按授权策略决定，`autonomous`/`both` 无本地库也走外部检索；(2) `search_all_sources` 是 `StructuredTool` 不能直接调 → 经 `.func` 取原始实现，多源失败退单源 arXiv；(3) 外部命中无页/节定位被全部丢弃 → 用 arXiv ID 作稳定定位；(4) 中文命题 vs 英文来源零词面重合 → 术语对照 + 命中只升 `background` 且要求 ≥2 命中；(5) 检索证据未传给论文生成（参考文献 11 条却写"未发现可比较的工作"）→ 证据随结果传给第 4 节；(6) 检索成功却记 `executed=false`；(7) 稿件自带编号 + LaTeX 自动编号 → `\setcounter{secnumdepth}{-2}`；(8) 英文摘要用 `titlepage` kind 不参与编号；(9) `thebibliography` 自带标题 → 去掉多余的 `\section*`；(10) 作者块只在 `\author{}`；(11) 引用统一 `\upcite{refN}`（`[1]` 会被当可选参数）；(12) 多行块按行渲染并分流列表/引文/表格；(13) `compile_publication` 用 `resolve()`（相对路径导致 PDF 落进套娃目录，"编译成功却报没有 PDF"）。回归：`tests/test_publication_layout.py`（8）、`test_publication_evidence.py`（14） | 已修复(test_publication_layout.py / test_arxiv_link_counts_as_locator / test_empty_library_falls_back_to_external_search_when_allowed) | 真实测试（题面走附件、请求"研究附件中的问题…"）产出 `delivery_level=条件性研究报告`：事件流 `#11 写作缺口回流: 1 条新义务` 之后停止，遗留一条 `writing_missing_argument_chain / required=True -> open`（"结论 clm-* 没有可展示的推导步骤"），未见成文；同一份 `manifest.json` 自相矛盾（`gate_passed=false` 而 `delivery_gate_passed=true`） | 写作缺口只有在**真的缺推导**时才可回流为阻塞义务；回流出的阻塞义务必须被真正消解，不得只登记不处理；交付物内三道门槛字段必须各自独立、自洽 | 三处独立成因：(1) `argument.writing_gaps` 判定"没有可展示的推导步骤"只看 `attempts.steps`，不认设计证书里的判定链（`certificate_of` → 定理/条件/输入/结论逐条可反查），于是对计数/设计类结论**误报**缺口 → 新增 `_has_certificate_chain()`，有证书链即不算缺口（反向用例保证真缺推导时仍报）；(2) 理论图 `theory_finalize → END`，而"缺口回流→重开研究循环"只存在于 `loop.run()` 的内层循环里，图路径逐步驱动**不经过**它 → 新增 `_route_after_finalize` 回边 + `theory_step_node` 重入时调用 `engine.resume_after_reflow()` 解除持久化的 `done`（否则 `step()` 立刻返回、什么也不做）+ 收尾在缺口未消解时**不导出交付包** + `THEORY_REFINALIZE_MAX`（默认 3）防死循环；(3) `_refresh_manifest` 把 `delivery_gate_passed` 写成出版门槛结论，覆盖了真实的研究/表达门槛 → 三个字段各自独立并接受调用方传入的判定。回归：`tests/test_writing_gap_recovery.py`（7 项）；真实模型同一路径复跑 4 次均为"完整论文 + PDF、无未关闭义务" | 已修复(test_certificate_chain_counts_as_argument_chain / test_graph_routes_back_to_research_loop_when_gap_reopened / test_manifest_keeps_the_three_gates_independent) |


- **编号**：`CD-NNN`，递增、不复用、不重排。
- **日期**：发现日期，`YYYY-MM-DD`。
- **维度**：取值必须是 `rubric.md` §2 的维度之一 —— 研究意图 / 资料与来源 / 模型忠实度 /
  论证与验证 / 引用与差异 / 仿真或实验建议 / 写作缺口回流 / 交付诚实度。
- **现象（含文件/行位置）**：写清是哪个文件、哪次运行（`run_id`），以及可复核的观察。
- **状态**：取值见 §2。

## 2. 状态取值

`待处理 / 已修复(测试名) / 判定为非缺陷(理由)`

## 3. 纪律（必须遵守）

1. **`判定为非缺陷` 必须写理由**：括号内理由不得为空，并须给出依据（对应 rubric 维度 +
   文件位置 + 为什么不算缺陷）。留空理由的记录视为"未处理"。
2. **历史记录只追加不删除**：已登记的编号、日期、现象不得修改或删除；修复与复议通过追加
   "处理/状态"说明完成。编号不得回收，行不得整行删除。
3. 每条 `rubric.md` 的 `不通过` 项都必须有一条编号记录，不得只写在评审记录正文里。
4. 同一现象在后续轮次复现时，**新增一行**并引用原编号，而不是覆盖原记录。
5. 本用例的失败样例必须保留：**不得**用改测试期望、放宽判据或润色论文来消除失败记录。

## 4. 备注（不算缺陷，但必须记录）

- 同一时期的 `GET /api/sessions/<id>/events`、`/api/sessions/<id>/state` 报错属
  **会话已结束/会话不存在**的正常探测，不计入本表。
- CD-001 与 CD-002 的**共同近因**是 `src/web/dist/` 里的旧 bundle 配新后端：
  后端不编译前端（只从 `dist/` 提供页面），源码改了但没重建时，浏览器里跑的是旧逻辑。
  因此运行手册 §0.1 已把"前端产物与源码同版本"列为 Web 入口的**硬前置**，并加了
  契约测试（`test_runbook_requires_frontend_artifacts_to_match_sources`）防止这条要求
  从手册里消失。
- 修复 CD-002 过程中曾出现两次**修复不完整**的实现（先只看 `runId`、再只比较
  `currentResearch.projectId`），两次都被同一条浏览器回归用例挡下（用例断言的是
  "没有真的发出请求"，不是界面文案）。教训：**判据必须是"这个项目建立过运行"这一事实，
  而不是另一个恰好相等的间接字段** —— 后者会随后续赋值被填平。

