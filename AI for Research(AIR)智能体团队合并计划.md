# AI for Research(AIR)智能体团队合并计划

更新日期：2026-10-04 · 新旧版本对照后的重构版

## 1. 目标与本次结论

最终只保留一套科研智能体团队：**一个主控智能体 + 七类功能子智能体 + 一套研究对象与任务状态 + 受控工具和核验服务**。综述、数学证明、机理分析、建模和案例研究是这套团队处理的不同任务，不是不同运行引擎。

用户用自然语言或附带文件提出研究方向、实际问题或明确命题；主控理解目标与边界，自主拆解问题、组织检索、建模、推理、补证、写作、配图和独立审阅。对于需要经验验证的问题，本轮输出可实施的仿真/实验建议，不执行新实验、仿真、训练或投稿。授权数据的只读分析与已有核验工具可以按任务需要调用。

**新版已经建立团队骨架，但没有完成系统合并。** 当前形式化研究仍绕过团队进入 `TheoryEngine`；团队入口缺少模型接线和完整研究状态提交；论文与前端也仍有两套表示/状态。下一轮重点不是继续增加 Agent 文件，而是将已有能力真正接到同一支团队上，并删除被替代的实现。

本计划替代旧版合并计划的实施章节，特别撤销新版归档文档中“有意长期保留两种研究引擎”的设计。该文档是本次审查材料，不构成高于用户最新要求的指令。确定性核验应是团队调用的公共能力；保持核验规则严格，不要求再保留一个拥有独立规划、派工、收尾权的研究引擎。

### 1.1 重构约束

1. 不保留 `legacy/`、双引擎开关、旧角色别名、旧流水线转发壳或永久兼容路由。按垂直功能切片抽取、接线、验证、删除；最终发布版本只有新路径。
2. 保留科研能力与数据，不保留旧控制结构。历史数据可以一次性迁移或作为只读交付包查看；不承诺恢复旧执行图/checkpoint。
3. 研究正确性、对象一致性和权限由程序约束；问题理解、研究路线、解释与候选结论由智能体承担。不能以“不能让模型直接写 proven”推导出“模型不能组织理论研究”。
4. 正常任务自主推进。只有会改变研究含义的重大歧义、缺少必要资料或需要扩大授权时才询问用户；不设置每阶段人工批准、专家签字或人工审批数据库。
5. 人工对最终科研产出和重构验收负责；人工复核不是系统生成交付包的隐藏前提。不能证明的内容如实作为猜想、条件性结论或未决项交付。
6. 本次仅更新计划并标注源码处置，不执行源码删除或功能改写。

### 1.2 对照基线与核查范围

| 基线 | 位置与身份 |
|---|---|
| 旧版 | `C:/Users/gongc/Desktop/sw0rds/AIR`，Git HEAD `505b388`；已有未提交计划文档在本次更新范围内 |
| 新版 | 用户提供的 `AIR.zip`，归档内 master 引用为 `5f098f58669839403183cf97ecbf55902f84b1df` |
| 新版审查副本 | `C:/Users/gongc/Desktop/sw0rds/AIR_review_0b9243d2/AIR`；未覆盖旧目录源码 |
| 归档校验 | SHA-256：`EA541DA5888B14988CFBE68C452BFA3D4B60E1F8C2A4D673B8D80B0385D79853` |

以下源码位置均以**新版审查副本**为准，除非明确标为目标目录。比较采用文件内容和真实调用关系，不只依据提交信息、文件名或进度文档。

源码清单口径：`src/` 下 `.py/.ts/.css/.html`，排除测试、构建产物、缓存和依赖目录。旧版 134 个、新版 184 个；新增 57、删除 7、修改 22。扫描解析了 144 个 Python 源模块，未发现 Python 语法解析错误。入口可达性扫描找到 20 个不可达候选，**不等于可以删除 20 个模块**：例如 `verification/_worker.py` 通过子进程 `-m` 启动，必须保留。

已运行不联网、不调用模型、使用内存数据库的最小复现；没有运行新版全量测试、真实科研任务、浏览器端到端测试或实际 PDF 编译。归档中的历史通过数量不作为本次复测结果。

## 2. 两版差异：已具备的基础不再重复立项

| 能力 | 旧版 → 新版的实质变化 | 下一轮处理 |
|---|---|---|
| 角色与协议 | 新增主控及七类角色、`AgentTask/AgentResult/ResearchNeed/CapabilityGrant`、工具循环与预算结构 | 继承骨架；修接线、权限和任务闭环，不重建一套协议 |
| 团队编排 | 新增 `research_graph.py`、`team_session.py`、任务存储与候选投影 | 选为唯一外层运行路径，接入原理论能力 |
| 旧综述主入口 | 已删除 `graph/pipeline.py`、`agents/paper_writer.py`、`research/writing_bridge.py`、`main_chat.py`、`gui.py` | 不再列为待实现/待删除；清理残留约定与文档 |
| 理论能力提取 | 新增 `reasoning_kernel.py`，证明计划、推导、模型、验证方案等已有部分抽取 | 保留有效函数；补齐核验落盘、义务闭合和失效传播后删除 `TheoryEngine` |
| 文稿 | 新增 `publication/schemas.py`、`WritingAgent`、`task_profile.py`，已移除旧长文附录入口 | 收敛文稿表示和正文生产，不再新增另一个写作智能体 |
| 资料接入 | 已有检索桥、用户资料库、只读数据适配器；新增本机路径扫描/导入及前端文献库 | 保留并统一任务授权、来源身份与产物归属 |
| 会话与资源 | 新增 `sessions/`、事件日志、线程归属日志、`run_scope` 等 | 继承模块，修实际消费与恢复逻辑；拆出文件不等于行为完成 |
| 前端 | 已有统一输入、团队板、论文/图表/引用视图、API 契约、会话控制器及 reducer | 保留组件与测试；完成单状态、单事件、单会话路径 |

旧计划里“自主检索在无本地库时直接跳过”“仍默认生成组合设计专用论文”“尚无团队协议/前端控制器”等历史描述，不应原样继续作为新版缺口。下文只保留新版仍未闭合的工作。

## 3. 影响目标实现的真实缺口和错误

P0：接通自主运行前必须处理的正确性/权限问题；P1：正式交付前必须完成的能力和一致性问题。“已复现”指本次最小离线复现；其余为源码路径核查，不冒充实际运行结论。

### 3.1 主控、入口和科研闭环

| 编号 | 发现与证据 | 下一轮修复与验收 |
|---|---|---|
| G01 / P0 | **实际仍是双引擎。** `server.py:161` 的 `_resolve_engine()`、建图分支、`main.py::_run_theory_mode()` 将非综述/显式 theory 任务交给 `theory_pipeline + TheoryEngine`。 | 所有科研请求创建同一 ResearchRun，进入 TeamRun。删除引擎选择函数、`DEFAULT_ENGINE/SURVEY_ENGINE_ENABLED`、`--mode` 和相关分流测试。形式化证明必须在同一团队内完成。 |
| G02 / P0 | **团队入口没有模型工厂。** `server.py::_build_team_app()`、`team_api.py::run_team()`、`team_session.py::run_team_session()` 均未注入 llm_factory；AgentRuntime 默认值是 None。已复现默认团队 `llm_available() == False`。 | 一个装配入口注入角色模型、工具、预算、权限。显式离线测试可规则退化，但生产配置缺失必须可见，不能宣称自主科研完成。模型替身验证 HTTP/CLI 的角色模型实际调用，再做受控真实模型测试。 |
| G03 / P0 | **请求绑定时机错误。** `server.py::start_session()` 先 `_make_session()`，后填写 session.request/run_id；前者已创建 TeamRun。`TeamApp.stream()` 不消费初始字典中的研究身份和资料。团队可能使用 session ID、自动 problem/run ID、默认资料策略；附件也未传入该团队。 | 改为：规范化输入与文件 → 校验资料授权 → 分配身份 → 创建 run/session → 注入同一输入快照 → 启动。HTTP 返回 ID 必须等于任务、SQLite、事件和包中的 ID；用附件题目、本地库和 autonomous 请求分别验证。 |
| G04 / P1 | **主控仍主要是规则分类器。** SupervisorAgent 保存 llm 后未调用；brief() 根据关键词展开固定类型子问题，decide() 按规则派工。注入 spy 模型后，理解/规划/首轮决策调用数仍为 0。 | 抽用 question_planner/proposal 的模型提议经验，实现模型驱动的 ResearchBrief、子问题 DAG 和复盘重规划；规则负责契约、权限、预算与循环保护。用同义改写、否定条件、跨领域及附件冲突任务验收。 |
| G05 / P0 | **推理前资料依赖仍不充分。** `_wire_default_dependencies()` 只约束写作/配图/审阅；证据任务优先级晚于研究子问题。已复现首轮顺序 `reasoning, reasoning, evidence`，推理依赖为空。EvidenceAgent 的 LLM 工具观察也未直接转成证据对象，而后又固定调用 gather_sources()。 | 按子问题缺口建依赖：依赖已有研究的建模/推导先检索、阅读；纯自足证明可无文献启动，但说明理由和新颖性限制。工具命中进入统一证据接入，保留模型选择的查询与原文；中途遇到引理/反例/冲突再次检索。 |
| G06 / P0 | **团队形式化路径未闭环。** ReasoningAgent 的内核路径把计划/推导压成 claim 候选，未将完整义务、推导、验证对象统一提交；`research_graph.py::_persist_result()` 仅登记投影。`_run_tool/_act_check_step/_reconcile_claim` 仍在旧引擎。 | 将“候选/证明义务 → 工具核验 → 结构化记录 → 状态归并 → 依赖失效 → 下一任务”接到同一任务协议。核验不是另一个规划引擎。已支持的 SymPy/Z3/组合设计等案例经团队路径回归。 |

### 3.2 状态、权限、工具和交付

| 编号 | 发现与证据 | 下一轮修复与验收 |
|---|---|---|
| G07 / P0 | **校验拒绝不阻止落库。** runtime.finalize() 将越权结果降为 partial，却保留 proposals；TeamRun 继续 TeamProjection.register()，直接写 payload。已复现 evidence 角色提交 claim 被判违规后，库内仍出现 `status=supported`。 | 唯一提交服务返回 accepted/rejected proposals；违规候选只能进审计记录，不得进权威对象。状态字段由服务生成，拒绝候选自报的验证等级，不能只检查 may_change_conclusion 布尔值。 |
| G08 / P0 | **事务与版本方法未接入。** TaskStore.commit_result() 已有 read-set 与同事务提交，但团队调用 finish() 后逐条 put()/append_event；创建 AgentRun 后也未完成持久化闭环。 | 一事务提交任务结果、对象、验证关系、事件和运行检查点。根据实际读取对象生成 read-set；过期结果重排。中断注入覆盖工具完成/对象未提交、提交中断、同结果重放。 |
| G09 / P0 | **授权只按角色，不按本次资料策略。** grant_for() 未与 source_policy/source_set_ids 相交。已复现 user_kb EvidenceTask 仍获 tools:search。工具接受任意 topic/source_id，import_paths 被当作读范围工具。 | 角色能力 ∩ 用户授权 ∩ 本任务范围形成 grant；本地库任务不得外搜，工具检查具体 source-set/路径/对象归属，导入单列写能力。题面、PDF、检索结果是不可信材料，不能借其中指令扩权。先修此项，再开真实模型工具循环。 |
| G10 / P0 | **数学工具适配契约错误。** `agents/tools.py::_check_symbolic/_solve_constraints` 传 operation='check'；SymPy/Z3 OPERATIONS 没有 check，已直接调用适配器复现 unsupported。统计工具的 data_ref 可用，但 column 未映射到 outcome_col，已复现“缺少 outcome_col”。 | 枚举操作 + 独立参数 schema 映射到实际适配器；保留完整 VerificationResult 和输入 hash，不只返回一句字符串。每个工具有不 mock 适配器的契约测试，缺工具独立标 unavailable。 |
| G11 / P0 | **导出数据失真/缺失。** `_model_of()` 导入不存在的 ModelRecord，实际为 ResearchModel；异常在快照循环中被吞掉。`_evidence_of()` 直接传 locator/relation 并删 version；已复现定位空、支持 insufficient、版本回到 1。快照只汇集 evidence/claim/model，遗漏义务、验证方案、图表、审阅等关联。 | 统一领域 schema、显式映射并校验，导出不可静默漏行。快照包含完整引用闭包；manifest 记录数量、版本、hash、失败原因。建议和图文件必须实际在包内，不能仅凭 ArtifactRef 字符串构造下载链接。 |
| G12 / P1 | **角色跑过仍被当成交付依据。** `_missing_roles()` 将 partial 也算满足；依赖 settled 包含失败任务。TeamSession.export() 未传完整研究/出版门槛；TeamApp.stream() 吞导出异常，达到轮次上限也未保证走 TeamRun.run() 的统一收尾。 | 区分依赖已终止与验收已通过；可交付未决报告，不可自动升级论文。检查目标、核心对象、blocking issue、版本、文件与停止原因；所有出口走同一 finalize，导出失败返回可恢复错误。 |
| G13 / P1 | **用量边界尚未接通。** 常规入口未注入 RunBudget；BudgetedLLM.__getattr__ 直接透传 bind_tools，其返回模型可能绕开 invoke 记账；确定性检索也未按每次查询走统一工具计量。 | 统一 invoke/bind_tools 包装、即时计数、全局预留/结算/释放与取消传播；子任务额度不得无限叠加。用多轮模型替身和查询计数器验证次数、tokens、成本归属及超限停止。 |

### 3.3 会话、成文与前端

| 编号 | 发现与证据 | 下一轮修复与验收 |
|---|---|---|
| G14 / P0 | **跨运行混用风险。** AgentTask 有 run_id，但 plan() 未赋值（已复现空）；team_projection() 把无 run_id 任务纳入任意运行。TeamProjection.rows() 读取项目全部 latest 对象，默认上下文不按 problem/run/依赖裁剪。 | 对象记录 project/problem/run、produced_by 和 lineage；默认只读当前问题与显式继承快照。禁止 not run_id 式放行。测试同项目两问题、同问题两运行隔离；跨运行复用必须显式引用。 |
| G15 / P1 | **事件模块拆出，多订阅问题仍在。** session_events() 回放后仍 `session.events.get()`，多个页面争抢队列；connected 使用未落日志的 current_seq+1，可能占下一业务 ID。 | 持久事件日志 + 每订阅者游标/通知，不用共享 queue 广播；connected/heartbeat 不占业务 seq。投影带 revision/event_seq；过期游标先补快照。真实双订阅比对完整序列。 |
| G16 / P1 | **任务落盘不等于恢复团队。** TeamRun 每次创建空 TeamLoopState；recovery_view() 只是查询，未重建 brief/plan/results/open_needs/dispatched。TaskStore.create() 同键复用不返回旧 task identity，调用方仍可能执行新 ID。 | 持久化统一 RunState、任务租约/attempt、结果与工具收据，明确 start/resume/fork。已提交动作不重跑；外部回执不确定时显式处理，不能承诺任意外部工具 exactly-once。 |
| G17 / P1 | **正文和文稿模型未统一。** publication.schemas.Manuscript 与 rag.theory_render.Manuscript 并存，WritingAgent 双向转换。theory_pipeline.py:504 写 manuscript_md 后，:564 又 build_publication_paper() 重组正文。转换丢 source/figure 引用、表格 data、部分块身份与版本。 | 唯一 WritingPacket → Manuscript IR → 多格式渲染，预览/Markdown/PDF 同源同版。渲染器不能生成研究叙事；迁出证书/公式/排版函数后删除旧 IR、转换层和第二正文生成器。 |
| G18 / P1 | **新图表路径未继承旧隔离。** FigureAgent 默认 OUTPUT_DIR/figures，按 kind-purpose 命名，多 run 可能覆盖；校验问题记录后仍渲染，input_versions 为空；公式直接 parse_expr，不能视为安全解释器。 | 唯一 FigureSpec/产物服务，按 run/task/artifact version 分目录；来源、模型域、单位、权限先校验再渲染。表达式白名单解析与受限执行，拒绝任意 Python；不支持/绘制失败可见。 |
| G19 / P1 | **前端仍同步多份状态。** air-global.ts 的 research、app.ts 的 currentResearch 与 ID 镜像、team-controller.ts 的 reducer store 并存；syncSelectionFromLegacy() 表明新 store 不是唯一权威。 | 保留组件和 reducer，取消兼容同步；全部控制器经同一 store/action/selector，见第 8 节。 |

## 4. 唯一团队：角色归属与复用边界

外层只允许以下控制结构。角色内部工具循环可以不同，但不能调用另一个完整研究总控。

```text
统一输入/API/CLI
  → 输入快照与授权 → SessionController → TeamRun（唯一运行循环）
                                      ↕
                                SupervisorAgent
                                      ↓ 派 AgentTask
     Evidence / Modeling / Reasoning / ValidationPlanning / Writing / Figure / Review
                                      ↓ AgentResult + 后续需求
                    ResearchCommitService + VerificationService
                                      ↓
                      版本化研究库 / 事件 / 不可变产物
                                      → 主控复盘 → 继续研究或交付
```

TeamRun 负责执行，不代替主控做领域推理；ResearchCommitService 是拟新增的权威提交边界，不是新智能体。七类角色按需参与，不要求每次调用七个角色，也不要求使用不同模型供应商。

| 唯一角色 | 职责与成果 | 当前复用基础 | 禁止继续承担的职责 |
|---|---|---|---|
| SupervisorAgent | 理解任务、路线、子问题/依赖、派工、验收、复盘与停止；ResearchBrief/TeamPlan | supervisor.py 协议/去重；question_planner、intent、proposal、coordinator 的有效提议/约束逻辑 | 不跑完整理论引擎、不硬选引擎、不以角色数量判成功 |
| EvidenceAgent | 文献/案例/数据检索、全文阅读、真实性、支持/反驳/适用性、查新材料；EvidenceBundle/CaseCard/DatasetCard | kb 的 bridge/service/retrieve/ingest/parse/identity/cards/sources/path_import；search_tools/pdf_fetcher/citation_verifier；research 的 evidence/retrieval/query_planner/novelty_compare | 不以标题相似证明结论，不在出版末尾偷偷补研究依据 |
| ModelingAgent | 实际问题建模，变量/单位/假设/定义/约束、竞争模型与选择理由 | agents/modeling、research/modeling、problem_formulator、reasoning_kernel.model_proposal_for；旧引擎模型比较方法 | 不擅改目标，不把未知条件补成事实 |
| ReasoningAgent | 理论推导、证明计划、引理、反例、替代假设与结论综合；Claim/DerivationStep/ProofAttempt | agents/reasoning、reasoning_kernel、theorist、derivation、argument、design_feasibility | 不自写核验通过；不把摘要当作完整推导依据 |
| ValidationPlanningAgent | 对待验证结论给可证伪的仿真/实验建议：变量、对照、参数、指标、统计判据、资源、风险和实现步骤 | validation_planning、reasoning_kernel.validation_plan_for、modeling 相关方案 | 本轮不运行仿真/实验，方案不是成功证据 |
| WritingAgent | 论证提纲、正文撰写/局部返修；唯一 Manuscript | writing.py、publication/schemas、task_profile；旧理论 writer 的证书与事实表达 | 不改研究结论，不造无依据事实，不把日志堆积当论文 |
| FigureAgent | 按论证需要选图，FigureSpec、文件、图注、来源；发现缺口交回主控 | figures.py；旧 figure_generator 的通用绘制、figure_style、figure_llm 质量检查 | 不凑图、不编数值、不执行任意生成脚本 |
| ReviewAgent | 独立查逻辑、前提、引用支持、可读性、图文一致性；ReviewIssue/定向返工 | review.py、research/critic/adversarial；旧 paper_reviewer/citation_* 有效检查 | 不只打总分、不直接升级科学等级；返工跑过不等于问题关闭 |

### 4.1 主控必须完成的动态闭环

主控读取原问题/附件、约束、当前对象/版本、未决问题、可用能力和预算。每轮输出结构化决定：派工、等待、补资料/换路线、必要澄清、交付或带未决报告停止；给简明理由、预计收益和验收条件，不要求展示模型内部隐式思考。

典型轨迹：用户问题 → 初步建模 → 发现需已有定理 → 检索并核对定理条件 → 补适用域 → 推导与核验 → 反例导致改命题/路线 → 写作 → 审阅发现缺前提 → 原角色修订 → 受影响稿件/图更新 → 复核交付。

任务图必须随成果变化；区分工具不可用、证据不足、反例、问题不清、预算耗尽；同一目标无新信息不得反复尝试。每个新增任务要对应具体缺口，关键词与固定 priority 仅作保守降级，不作正常主控实现。

## 5. 冗余清理清单：删除、抽取、复用

### 5.1 标记含义

- **D0：可独立清理删除。** 新版生产入口已不可达或能力已替代；同一提交清理 import、旧契约和仅验证旧实现的测试。
- **D1：抽取有效能力并接入后删除原文件/代码块。** 不是保留以防万一，必须有目标归属和删除验收。
- **K：保留复用。** 可改接口或移动，但不重写有效能力。
- **T：改写测试/文档。** 删除维护旧架构的断言，保留科研正确性/数据完整性行为测试。

删除前还需检查字符串加载、子进程、CLI、测试和打包引用。测试通过不能使一段未接生产的代码自动成为有效能力。

### 5.2 死入口与空架构

| 标记 | 文件/代码块 | 处置与依据 |
|---|---|---|
| D0 | agents/outline_generator.py | 无生产调用/直接测试引用；提纲归 WritingAgent，删除 PipelineState 节点，不留旧大纲 Agent |
| D0 | agents/literature_reviewer.py 的旧工具循环与 run_retrieval/run_notes_synthesis/run_literature_review | 无生产调用，检索桥与 ToolLoop 已替代；删除固定篇数/年份/笔记流程。中文来源、元数据工具按下表接入，不靠旧 Agent 激活 |
| D0 | utils/session_memory.py | 无生产 import；“最近一次研究”及兼容汇总不属于统一 run 状态。清理 server 专用旧清理分支/兼容测试，不顺便删除用户历史数据 |
| D0 | graph/node_progress.py 中 citation_precheck/outline_generation/citation_guard/旧 finalize/latex_render 节点映射 | 对应综述图已删除；理论节点映射随理论图退出，最终仅处理真实团队事件 |
| D1 | graph/unified_state.py | 无生产/测试接线，不能算统一状态已实现；需要的身份/引用字段并入持久 TeamRunState，删除平行空声明 |
| D0（成组） | graph/state.py::PipelineState | 调用者仅为旧 survey Agent；它们迁走纯能力后连文件删除，不单独删断依赖，不留兼容 TypedDict |

### 5.3 旧 Agent：保留能力，不保留重复角色

| 标记 | 原模块/可复用块 | 新归属 | 删除部分 |
|---|---|---|---|
| D1 | citation_prechecker.py::verify_reference_list/build_verified_reference_sheet；citation_verifier/venue_resolver | 来源核实工具给 EvidenceAgent，文献表给出版模块 | run_citation_precheck(PipelineState) 及旧按编号传状态的节点链 |
| D1 | citation_guard.py::validate_draft_citations/check_citation_semantics | publication/checks + ReviewAgent；相似度只筛可疑项 | run_citation_guard；auto_fix_citations 按相似度自动换引文不能原样保留为正确性修复 |
| D1 | citation_checker.py 的语义核查提示/账本呈现 | ReviewAgent + evidence-link 投影 | run_citation_check、旧 report schema、整个旧角色文件 |
| D1 | paper_reviewer.py::_extract_issue_ledger/_build_review_anchor/_build_change_summary 的定位/差异/问题归并思路 | ReviewIssueStore + ReviewAgent，使用稳定对象/块 ID | run_paper_review、总分阈值改稿循环、独立旧模型入口 |
| D1 | pdf_ingestor.py::ingest_pdf_fulltext 的定位/下载/容错 | 对照 kb/ingest、kb/parse、pdf_fetcher，只补缺失功能 | run_pdf_ingestion(PipelineState)，长期双 PDF 入库路径 |
| D1 | rag/subquery_generator.py + research/query_planner.py | EvidenceAgent 唯一缺口查询规划，可有不同任务策略 | 单独依赖 literature_reviewer 的服务与固定模板 |
| K（接线） | tools/chinese_sources.py、tools/venue_resolver.py | 有配置、授权、可用性检查的检索/元数据工具 | 不因暂不可达误删有效中文来源能力；不可用与零命中需区分 |
| D1 | utils/context_budget.py、research/context.py、agents/protocol.ContextPack | 唯一 ContextPack；摘要/裁剪算法合入任务上下文服务 | 第二个同名 ContextPack 与多套截断规则 |

七个旧 Agent（citation_checker/guard/prechecker、literature_reviewer、outline_generator、paper_reviewer、pdf_ingestor）约 2,407 行，加旧 PipelineState 约 2,564 行。这是待处置范围，不是净删除承诺：有效读取/检查能力需先提取。目标是消除旧入口与重复职责，不以删行数代替完成度。

### 5.4 旧理论引擎：拆解后整套退役

`research/loop.py` 共 3,875 行，不能整体复制进 ReasoningAgent，也不能作为其“内部引擎”留下。

| 原位置/能力组 | 复用目标 | 退出条件 |
|---|---|---|
| _dispatch、step/run、动作排序/重试/停止；proposal/coordinator/action_registry | 语义规划归 Supervisor；允许操作、预算、有效前置条件归运行时纯验证器 | TeamRun 覆盖已支持任务后，删旧 ActionType 派发主循环与第二动作注册表 |
| _act_plan_proof/_act_derive_step/_derive_design_steps | ReasoningAgent + reasoning_kernel/derivation/theorist | 提交真实义务/推导对象，团队测试覆盖旧科学案例后删 wrapper |
| _run_tool/_save_verification/_act_check_step/_act_seek_counterexample/_act_rule_obligation | VerificationService 类型化请求与结果提交；推理角色选核查目标 | 结果通过统一 commit 落盘，保守判定不变；删旧执行/写库副本 |
| _reconcile_claim/_record_aligned/_verification_closure/_save_claim/_save_obligation | ResearchCommitService + 核验状态规则 | 唯一服务维护状态/版本/依赖，Agent 无法绕过 |
| _act_propose_model/model_comparison/model_selection/select_model_for_claim | ModelingAgent + 领域模型服务 | 模型/理由/条件关联完整，UI/API 不再临时构建 TheoryEngine |
| _act_retrieve_targeted/_act_read_source/_act_extract_result/_act_interpret_evidence/_act_compare_novelty | EvidenceAgent + 唯一 KB/EvidenceService | 搜索/阅读/支持/查新使用同一 schema，删循环内第二检索派工 |
| _act_design_experiment/_distinguishing_for | ValidationPlanningAgent | 建议完整进入快照/包，状态明确未执行 |
| submit_feedback/revise_assumption/fork_from_snapshot/_literature_impact_recheck/_reopen_for_recheck | 修订/派生服务 + 依赖失效，主控响应缺口 | 反馈/修改/派生 API 不再建旧引擎，证明/稿件/图自动 stale |
| _freeze_snapshot/_finalize/writing_gap_feedback/metrics | SnapshotService、PublicationService、任务事件与后续需求 | 唯一快照/收尾/用量来源，删第二出口 |

完成切片后，**删除 research/loop.py、graph/theory_pipeline.py、main.py::_run_theory_mode() 和 server 全部 TheoryEngine 实例化点**。`research/intake.py::is_survey_request()` 为选引擎服务的部分一并删除，任务画像由主控统一生成。

`graph/team_session.py::TeamApp` 为适配旧图驱动模拟 stream/get_state/Command/resume。最终 SessionController 直接依赖 TeamRuntime 的 start/resume/cancel/events/snapshot 接口；删除 TeamApp 和 `_Interrupt/_TeamState` 兼容类。会话业务归 sessions/controller，不再形成一个总控。

### 5.5 写作、渲染与配图重复实现

| 标记 | 原模块/块 | 复用/删除要求 |
|---|---|---|
| K（唯一 IR） | publication/schemas.py | 补引用类型、稳定块 ID、版本和表格/图引用，唯一 Manuscript/Block |
| D1 | agents/theory_writer.py | 证书/条件/反例/事实块提取到 publication/facts 与 checks；删 run_theory_writing 和旧整文入口 |
| D1 | research/publication_paper.py::build_publication_paper | 保留题目清洗、条件表达、符号表/证书呈现；删第二正文生成器；章节由 WritingAgent + TaskProfile 决定 |
| D1 | rag/theory_render.py、rag/publication_render.py、不可达 rag/latex_render.py | 通用渲染、转义、公式/证书排版迁 publication/render_*；删旧 IR/多套入口；renderer 不生成叙事 |
| D1 | writing.py::to_publication_manuscript/_snapshot_manuscript_from_blocks/_legacy_render/manuscript_from_snapshot | 单 IR 后删往返转换和旧类型兜底；降级稿也直接生成同一 IR，不调旧 writer |
| D1 | research/package.py::_traceability | 对实际 Manuscript IR 校验快照引用，不重建一份稿件自证；导出统一归 publication |
| D1 | research/publication_evidence.py | 有效检索/比较归 EvidenceAgent；出版只查已有依据，缺文献产生 ResearchNeed 回团队 |
| D1 | rag/figure_generator.py、rag/figure_llm.py | 提取通用图种、风格、check_png_quality 等到 Figure 工具；删固定 RF 图、按笔记凑图、旧自治生成循环与 _execute_code 自由脚本路径 |
| K（迁移） | rag/figure_style.py、rag/format_validator.py、rag/latex_compiler.py | 归 figures/style、publication/checks、publication/compiler，补契约后复用 |

### 5.6 兼容契约、测试与文档一并清理

1. protocol.py 旧角色映射仅由一次性数据迁移识别；运行时只接受八个规范角色名。
2. 删除 StartRequest.mode、旧 stages/stage_index、survey/theory UI 标签；逐项确认 --skip-retrieval/--max-revisions 等参数用途，仍有业务意义的改成目标/预算/来源策略。固定 2019-2026 检索窗取消，范围由用户或有依据的规划给出。
3. 改写 test_engine_boundary_contract 中保留双引擎说明/保守分流断言，test_engine_retirement 的 covers_both_remaining_engines/both_engines_can_be_built，以及 test_reasoning_kernel 的 engine/team 双入口测试；替换为唯一团队行为测试。
4. test_refactor_features/test_new_features/test_relevance_cache/test_format_figures/test_figure_llm 混有有效能力测试：先迁行为用例，再删旧实现断言，不整文件盲删。
5. README 的 main_chat/gui、模式切换等说明失效，pyproject 项目名仍为 langgraph-survey-pipeline；统一更新启动说明、样例、结构图与包元数据。
6. 若唯一运行时已不使用 LangGraph/checkpointer，再按扫描结果移除依赖；不能因名字旧提前删除有效工具依赖。

用户资料库、研究库、附件和输出包不是本次可删冗余。源码清理与用户数据清理严格分开。

## 6. 统一领域与状态提交：还需补齐什么

### 6.1 一份事实模型

保持 `ResearchBrief → 子问题 → 模型/假设/命题 → 推导/证据/核验 → 稿件块/图 → 审阅问题 → 快照` 引用链。

| 对象组 | 关键字段与约束 |
|---|---|
| ResearchInput / ResearchBrief | 原请求、附件 ID/hash/用途、目标、未知条件、多标签研究类型、来源策略、交付规格、预算；明确约束不能被摘要覆盖 |
| AgentTask / AgentRun | project/problem/run/task/attempt、owner、依赖、acceptance、输入版本、授权、状态；run_id 不空 |
| SourceEvidence / CaseCard / DatasetCard | 身份、版本/hash、定位、摘录、授权、真实性；EvidenceLink 绑定命题版本和适用条件。不能把 case/dataset 无区别映成 evidence 丢类型 |
| ResearchModel / Claim / DerivationStep / Obligation | 定义、符号域、量词、假设、步骤前提、义务、反例；理论/经验/非形式化等级分开，自然语言不能自动升级严格证明 |
| VerificationRecord | 实际操作、参数、输入版本、工具版本、退出状态、结果、适用域、证书/hash；unknown/unavailable/timeout 不算通过 |
| Manuscript / Figure / ReviewIssue | 稳定 ID、输入版本、来源/论断引用、图文件 hash、定位到对象/块的问题、关闭依据 |
| ResearchSnapshot / DeliveryManifest | 不可变引用闭包、产物清单、核验/审阅状态、停止原因/未决；排版映射另有版本，不用 allow_replace 改冻结科学快照 |

### 6.2 唯一提交接口（目标接口，尚未实现）

`commit_result(task, result, execution_receipts, expected_run_revision)` 隐藏授权、冲突、验证和落盘细节，调用方不再拼 put/append_event：

1. 校验 task/run/agent、可写对象种类、资料权限和结果 schema。
2. 根据实际 read-set 核对当前版本/分支；冲突返回 stale_input，不接受旧依据成果。
3. 拒绝候选自报科学等级；核验收据来自执行服务。智能体可评价证据语义，但记录评估者/原文/不确定性，不能伪装确定性证明。
4. 计算 accepted/rejected、依赖边、状态与失效传播，拒绝理由可见。
5. 一个事务提交结果、对象、AgentRun、事件、检查点与幂等收据；失败回滚。
6. 产物先临时写入和校验 hash，再登记不可变 URI；外部执行与事务之间以持久收据衔接，不能发不存在的下载链接。

上下文服务只给相关、授权的对象引用/摘要，提供 read_object/read_section/read_source 读取全文。不能为省 token 永久裁去后半段证明/论文，也不能把全项目 latest 对象送给所有角色。

## 7. 推荐项目结构

下列为目标结构，不表示新服务已实现。先原位抽取接线、稳定后移动；**不要先搭空目录再用 wrapper 包旧系统**。

```text
src/
  bootstrap.py                 # 唯一装配：模型、权限、工具、存储、团队
  main.py                      # 薄 CLI，与 HTTP 调同一应用服务
  server.py                    # FastAPI 装配
  api/
    sessions.py  research.py  sources.py  artifacts.py  contracts.py
  agents/
    supervisor.py  evidence.py  modeling.py  reasoning.py
    validation_planning.py  writing.py  figures.py  review.py
    base.py  protocol.py  registry.py
  runtime/
    team.py                    # 由 research_graph.TeamRun 收敛，唯一外层循环
    state.py                   # 可恢复 RunState
    executor.py  tools.py  budget.py  context.py  policy.py
  research/
    schemas.py  store.py  task_store.py
    commit.py  verification_service.py  snapshot.py  revisions.py
    dependency_graph.py  acceptance.py
    modeling.py  reasoning_kernel.py  derivation.py  evidence.py
    domains/                   # 领域策略如组合设计，不是独立引擎
  kb/
    service.py  bridge.py  sources.py  path_import.py
    ingest.py  parse.py  retrieve.py  identity.py  cards.py  adapters/
  tools/
    search_tools.py  pdf_fetcher.py  citation_verifier.py  venue_resolver.py
    figures/                   # 声明式绘制、白名单表达式、风格、质量检查
  verification/
    runner.py  _worker.py  schemas.py  *_adapter.py
  publication/
    schemas.py  facts.py  checks.py  profiles.py
    render_markdown.py  render_latex.py  compiler.py  package.py
  sessions/
    controller.py  store.py  events.py  runtime.py
  utils/
    uploads.py  external_data.py  cost_tracker.py  run_scope.py ...
  web/src/
    app.ts                     # 只启动、挂载、路由装配
    api/  contracts/  state/  events/
    features/
      intake/ sessions/ research/ team/ library/ paper/ artifacts/
    components/  styles/  markdown.ts
tests/
  contracts/ runtime/ research/ publication/ sessions/ security/ e2e/
evals/
  tasks/ expected/ rubric.md    # 期望答案仅评测可见，不能进 Agent 上下文
```

rag 中有效的向量化、分块、解析、引用按职责迁 kb/publication/tools，不整目录删除。公共模型、ContextPack、Manuscript、RunState 各一套。迁库保留 hash、来源身份和审计记录。

## 8. 前端重构：复用组件，收敛状态和交互

无需为合并先换前端框架。已有 API client、reducer、会话/SSE 控制器、团队板、论文和文献库；下一轮重点是接通并移除中间兼容层。

### 8.1 单状态与单会话动作

1. state/research-store.ts 成为唯一可写状态：selection（project/problem/session/run）、entities（带版本）、transport（连接/游标）、ui（选择/展开/草稿）；服务端结论只读。
2. session-controller 的 applyResearch/reset 改为 store action，保留统一打开/恢复/派生逻辑。请求带 run key、load token、取消信号，迟到响应不入库。
3. 删除 air-global.ts 的全局可写 research、current-research.ts 平行状态模型、app.ts::currentResearch 和 ID 镜像；状态标签保留为纯 selector。
4. 删除 team-controller::syncSelectionFromLegacy 与 legacyResearch；团队/工作台/论文不各存工作对象，使用统一 entities/selectors。
5. app.ts 仅装配，workbenchData、wbRequestSeq、产物加载、反馈、对象详情归 features；不是把大文件拆散后继续依赖全局变量。

### 8.2 信息架构与科研可见性

- 统一输入：自然语言、问题/文献附件用途、来源范围和交付要求；明确只生成实验建议，不暴露引擎选择。
- 研究概览：目标、当前 ResearchBrief、未知条件、路线、阻塞、下一动作和停止原因，不只显示“思考中”。
- 团队页：任务目标、负责人、依赖、状态、输入/输出引用、工具/预算、返工原因；完成任务与验证结论分开显示。
- 研究对象页：问题/模型/假设/命题/推导/义务/反例关系，支持版本差异与失效影响；不把任务树和科学关系混成大日志。
- 论文页：唯一 Manuscript 版本，公式/表格/图/引文完整；论断回链 claim/推导/核验，引文回到定位原文；不能只给截断摘要。
- 文献库/产物页：复用路径预览、来源失效检查和下载；显示真实 hash/版本状态；缺图、PDF 未编译、旧正文引用失效可见。
- 审阅面板：科学性/引用/可读性/图表分类，定位 affected_ref/block_id，显示未决/返工/复核通过；不是仅打总分。

### 8.3 事件、恢复与多页面一致性

统一 EventEnvelope：event_id/seq/project_id/problem_id/run_id/task_id/type/schema_version/payload。session-events 校验、去重和补偿，session-stream 管连接；事件经 reducer 更新 store 后渲染，废除 SSE 回调直接改多个 DOM/全局状态的路径。

持久游标与快照水位对齐；“断开连接”不等于“研究停止”。新建、历史查看、恢复、派生共用 openSession 入口，但查看不启动、恢复同一 run、派生新 run 并显式继承。切换附件/源库不得悄改已启动 run 的授权。

### 8.4 前端验收与删除门槛

| 场景 | 必须验证 |
|---|---|
| 快速切 A/B，A 请求迟到 | 无 A 的标题/任务/稿件/图串入 B，旧请求取消或丢弃 |
| 同 run 两页面 | 两端得到完整任务/中断/完成事件，不互相抢事件 |
| 断网/重连/日志超窗/服务重启 | 快照游标一致，不重做任务，不把断网当完成 |
| 自然语言 + 精确问题附件 | 可查解释后的意图与附件 hash，不被文件提示改授权 |
| 修改假设或原文更新 | 论断、块和图显示 stale，旧 PDF 明确旧版 |
| 多格式/追溯 | 预览、Markdown、PDF 同 manuscript/snapshot/version，引用/公式/图号一致 |
| 错误/可访问性 | 权限/工具/编译失败有提示，键盘可用、小屏可读，不仅以颜色区分等级 |

保留既有 Vitest/Playwright 行为测试并迁契约，不为兼容旧全局变量再留状态链；补真实 HTTP + SSE 浏览器测试。

## 9. 下一轮实施顺序与提交切片

以下均为待完成工作。每个切片须有测试和删除清单，不能把删除旧系统无限后推。

| 切片 | 工作 | 出口门槛 |
|---|---|---|
| R0：冻结目标/入口契约 | 唯一 TeamRun，统一 ResearchInput/RunIdentity，旧行为用例/删除清单和数据迁移决策 | API/CLI 同应用服务，身份先绑定；为 G02/G03/G07/G09/G14 建可失败回归 |
| R1：安全/状态底座 | commit、read-set、授权、工具契约、任务/产物归属、预算；G07–G11/G13/G14 | 越权/过期拒写，本地任务不外搜；工具真实可调用；重放不重复；删除任意 payload 直接写权威对象路径 |
| R2：团队接管研究 | 接角色模型/主控规划，抽取旧引擎核验、模型、修订、冻结，接前置/中途检索 | 同团队跑综述/形式化/机理/反例修订；删除 TheoryEngine/theory_pipeline、双路选择与旧 survey Agent，不留后门 |
| R3：唯一论文/审阅 | 单 Manuscript、长文/局部返修、引文支持、图表、审阅 issue 闭环和导出 | 预览/Markdown/PDF 同源，定向返工有效；删第二 IR、旧 writer、正文重生成和自由绘图代码执行 |
| R4：会话/前端收敛 | 持久 RunState/SSE，start/resume/fork，单 store/事件和页面整理 | 多会话/多页/重启/断网/过期测试过；删 TeamApp 旧图壳、window.AIR 状态与同步镜像 |
| R5：清仓/科研验收 | 旧测试/配置/文档/依赖、迁移、干净安装、真实模型跨任务回归 | 无旧执行入口，一套团队/状态/文稿/会话；人工最终复核，列出未通过项 |

R1 权限/写入边界先于 R2 真实模型工具运行。前端可按 R0 契约推进，但不能绕过后端缺口。先保持 run 内角色串行；并行派工是隔离/预算/一致性验收后的优化，不是多智能体成立的前提。

第一批提交建议：

1. 将本次探针转正式回归测试，补请求身份和真实 SSE 双订阅，确保稳定检出问题。
2. 修非法结果隔离、字段映射、run/source scope、数学工具参数与提交事务。
3. 唯一装配：请求/附件/来源先解析再建 TeamRun，真实注入 LLM/预算。删除同步 `/api/team/run` 的独立启动逻辑，统一调用 session/run 服务；团队只读查询可保留。
4. 完整研究纵切：建模→定向检索→推导→核验→反例/义务反馈→提交，逐组删对应旧引擎分支，迁完删旧文件。
5. 单文稿/独立审阅纵切，迁有效引文/图表能力，删除旧角色。
6. 会话恢复/SSE/前端收尾，全量清仓。

排期参考：一名后端 + 一名前端、领域人员间歇参与，可按约 6–8 周准备；取决于有效测试质量、模型成本和领域覆盖。这是规划估计，不是交付保证；是否完成以出口门槛而非修改文件数判断。

## 10. 统一系统验收矩阵

| 测试组 | 输入与预期 |
|---|---|
| 入口一致 | HTTP/CLI 相同自然语言、附件、来源得到相同 ResearchInput 和唯一团队，无 mode 分流 |
| 真实角色 | 模型替身证明主控/派发角色实际调用；缺模型显式受限，不把零调用规则模板当成功 |
| 检索驱动 | 有文献前提先阅读，中途引理再检索，反向证据触发修改而非只加参考文献 |
| 数学/逻辑 | 可证明/可反驳/unsupported/缺条件各一例；查义务、步骤、记录、状态，不仅查文章存在 |
| 建模 | 比较可区分候选模型，理由可追溯；不足则说明不确定性，不补造数据 |
| 验证建议 | 含可证伪判据/实施细节，系统和文章都不宣称已经实验 |
| 非形式化 | 案例/综述使用证据等级和条件性表达，不硬造数学证书、不自升定理 |
| 权限/一致性 | 越权 proposal、伪造等级、旧 read-set、跨项目 source_id、附件提示注入、路径越界被阻止且有记录 |
| 恢复/成本 | 杀进程恢复不重复提交，不确定外部回执如实处理，工具循环 tokens/费用归本 run |
| 写作/审阅 | 摘要/引言/定理/讨论/结论一致，核心论断回链；预置缺前提/无依据引文能被发现并定向返工 |
| 文件/图表 | 同名图跨 run 不覆盖，单位/图注/来源一致；包文件、manifest、下载、hash 对应 |
| 前端 | 第 8.4 全场景，特别真实双页 SSE 和多 run 切换 |
| 干净安装/删除 | 没有旧模块仍可导入、CLI/服务/测试/构建；无引用已删除模块的脚本说明 |

固定离线/已知解回归，再用未见同类任务测泛化，记录输入、模型、工具、预算、版本、失败类型。期望答案不进上下文，模型自评不代替领域复核。

最终必须满足：一个主控注册和外层循环；一个权威提交口；一个 Manuscript/Block 与正文来源；一个前端 store/会话动作口。TheoryEngine、旧 PipelineState、旧角色文件、mode 分流、TeamApp 兼容壳退出生产源码，不能换名继续存在。可从干净目录安装运行，不依靠旧进程、旧构建或缓存补缺文件。

## 11. 人工需要完成和检查的工作

| 人工工作 | 内容 |
|---|---|
| 开发者：发布/数据 | 备份研究库/附件/输出，选择迁新对象或只读归档；失败不悄删资料。确认仍有价值的 CLI/插件入口后再删依赖 |
| 开发者：接口/测试 | 每个 D1 有复用落点并已接入；旧测试迁成行为测试，而非为全绿删正确性要求 |
| 领域人员：最终科研产出 | 原题与模型忠实度、假设合理性、量词/适用域、推导跳步、证据支持关系、实验建议可实施性 |
| 编辑/读者：最终文章 | 是否讲清问题—方法选择—关键推导—结果—局限；图表/参考文献/摘要结论一致，不堆日志 |
| 用户/开发者：授权配置 | 允许库/目录、联网、模型和费用上限；敏感外发/付费来源/扩大范围需明确选择 |

人工复核用于开发验收和科研责任判断，不成为运行中的强制审批表。系统应自主完成正常闭环并输出边界；人的科研责任不能被“系统显示完成”替代。

## 12. 本次核查材料与边界

审查目录保留两个只读分析脚本，不属于 AIR 生产系统：

- `../AIR_review_0b9243d2/audit_sources.py`：源码 hash 比较、AST 解析、入口 import 可达性与测试引用；--summary 输出计数。结合字符串/子进程检查，不能仅据静态不可达删除。
- `../AIR_review_0b9243d2/audit_probes.py`：内存数据库复现越权候选落库、user_kb 外搜 grant、证据字段/版本损失、ModelRecord 导入失败、主控未调用模型、推理先于证据、任务 run_id 为空、默认团队无 LLM，以及适配器操作/参数契约错误。不需 API key，不访问网络/用户研究库；不等于完成实际数学工具求解测试。

本轮只更新计划，未删除源码、迁移数据或宣称重构完成。结论是：**新版可作为重构底座继续复用，但“新增团队代码”与“完整科研任务由唯一团队可靠完成”之间，仍有上述接线、状态、权限、核验、成文和会话缺口。下一版以闭环接通和旧路径退出为交付目标，不再叠加另一层架构。**
