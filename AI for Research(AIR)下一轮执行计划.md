# AIR 下一轮更改执行计划（对照 2026-10-01 计划书）

编制日期：**2026-10-01**（v2，取代 2026-09-30 版）
依据：`AI for Research(AIR)计划书.md`（2026-10-01 版：§1 交付目标与端到端契约、§2 工作包 P0-1…P2、§3 接口表、§4 人工必须完成与检查的工作、§5 迭代安排）
基线：`AI for Research(AIR)执行进度.md`（2026-09-24 版；`pytest` 631 passed）＋ 本次读码核对（源码自 9-24 23:00 后无改动，故 09-30 版计划中的代码锚点仍全部有效）

> 口径（沿用计划书 §6）：计划书更新不涉及研究内核代码修改；"存在实现与自动化用例"不等于领域问题被解决。
> 本文件只回答：**每个工作包改哪些文件、改成什么、用什么证明改对了、谁必须人工参与。**

---

## 0. 相比 09-26 版，目标升级在哪（执行计划必须跟着改的地方）

| # | 变化 | 09-26 版 | 10-01 版 | 对执行计划的影响 |
|---|---|---|---|---|
| D1 | 版本目标 | "在限定领域内能提出、核验并诚实交付理论研究结果" | 在真实领域问题上跑通**完整闭环**，并**成文**（论文草稿或条件性报告），再用另一类问题检验可迁移性 | 迭代 IV 的"成文"从收尾动作升级为一等交付物；每轮迭代都要有可运行研究产物 |
| D2 | 自主性定义 | 研究动作/路线能自行调整 | **系统自主选择后续研究动作**：人不再指定检索词、模型或证明路线 | 新增"自主检索闭环"工作包（见 §2 的 I-3），它是本版最大新增工程量 |
| D3 | 端到端契约 | 无 | 新增 7 阶段契约表（理解问题→发现审读资料→构造模型→理论推进→判断结果→提出验证建议→成文），每阶段写明"必须自主完成/可审查对象/何时停下" | 契约表成为**验收主干**：§6 用它反查每个工作包的产出 |
| D4 | P0-1 场景 | 一次真实运行 | **两种场景**：①用户提供资料库 ②只给方向并授权自主检索；人不写检索词、不指定应引用的论断 | 场景 ② 目前**走不通**（见 §1.1），必须新建能力 |
| D5 | §3 接口表 | 无 kb 行 | 新增 `kb/service.py + kb/bridge.py`：`search/read/resolve → 有覆盖范围和原文定位的研究资料`，隐藏"用户资料与自主检索的合并、去重、全文可得性、检索失败原因" | 检索覆盖记录与全文可得性成为**接口承诺**，不再是内部细节 |
| D6 | §3 接口表 | 无 | `experiments/planner.py + validator.py → propose_validation(gap, models)`；`theory_writer + package + reporting` 合并为"冻结快照 → 正文/投影/清单" | P1-3 与成文的职责收拢 |
| D7 | 人审 | 仅在门槛 6 提"专家签字" | 新增 6 个时间点的人工作表（负责人/要做什么/AIR 该提供什么/未完成怎么处理）＋ **只有 4 类停机点**需要人工中断 | §7 按该表重写；明确"人审不是每个动作的前置" |
| D8 | 迭代产物 | 交付门槛 | 每迭代都要有**可运行研究产物**（I 首轮失败报告；II 竞争模型+推导图；III 差异关系+新路线；IV 冻结快照+稿件+建议规格） | 迭代验收加"产物"列 |

---

## 1. 本次读码核对的关键事实

### 1.1 关键发现：**场景 ②「只给方向 + 授权自主检索」当前根本走不通**

不是"能力薄弱"，是**缺失**。证据链（当前工作区实测行号）：

| 环节 | 现状 | 位置 |
|---|---|---|
| 启动请求 | 只有 `source_set_id` / `source_set_kind(kb/files/dataset)`，**没有"授权自主检索"这类策略字段** | `src/server.py:126-127` |
| 绑定校验 | 资料源为空/不可读 → `ok=False`，启动直接 **409 拒绝** | `src/kb/sources.py:182-201`、`src/server.py:877-887` |
| 知识装配 | 无绑定主题 → `_knowledge_for()` 返回 `None` → `knowledge_available=False` | `src/graph/theory_pipeline.py:35-59`、`src/research/loop.py:396` |
| 动作暴露 | `has_knowledge` 为假时检索类动作**不进入候选集合** | `src/research/action_registry.py:47` |
| 外部检索 | 唯一路径把 `claim.statement`（或 treatment+outcome）当**单一查询词**，且只用于证据/新颖性候选 | `src/research/evidence.py:216-222`、`src/research/retrieval.py:26-32` |
| 覆盖记录 | `_DEFAULT_SOURCES` 硬编码；`SearchOutcome.covered` 只是"命中 ref 的 source_id 集合"，不是"检索了哪些库/哪些检索式/何时/未覆盖什么" | `src/research/retrieval.py:48`、`src/kb/service.py:134-155` |
| 全文入库 | `ingest_machine()` 已存在，但**只有 KB 命令行会调用**，研究循环里没有任何自动摄入 | `src/kb/ingest.py:231`、`src/kb/__main__.py:29-33` |

结论：计划书 §1 契约第 2 行（"在授权范围内自行设计检索式、检索和阅读全文；并入用户提供的资料库；
区分原始结果、转引、摘要和缺失全文"）在当前代码里**几乎全是新增工程**，并且它是 P0-1 场景 ②、
P1-1 新颖性边界、P1-3 交付清单的共同前置。故本计划把 **I-3 自主检索闭环**单列为迭代 I 的关键路径，
而不是把它当成 P0-1 的一个小分支。

### 1.2 其余锚点：与 09-30 版计划一致，无需改动

已复核仍成立：P0-2 `question_planner.py:120-142`；P0-3 `modeling.py:231-285 / 298-301 / 363-373`；
P0-4 `evidence.py:296-307 / 137-144`、`argument.py:273-296`；P1-1 `novelty_compare.py:181-259`、`theory_pipeline.py:62`；
P1-2 `proposal.py:191-208`、`coordinator.py:334-372`；P1-3 `experiments/planner.py:55-70`、`package.py:71-93`；
P1-4 `kb/parse.py:32-42`、`utils/external_data.py:26-104`；P2 `app.ts:16`、`server.py:66-68`。

### 1.3 端到端契约 7 阶段 → 现状与工作包映射

| 契约阶段 | 现状 | 缺口工作包 |
|---|---|---|
| 理解问题 | 有关系列候选，但"影响"默认落因果 | I-2（P0-2） |
| 发现与审读资料 | **无自主检索、无覆盖记录、无自动入库、无全文可得性状态** | **I-3（新增）** |
| 构造模型 | 双模板机制，编码回填命题表达式 | II-1（P0-3） |
| 理论推进 | 有工具核验与反例，但论证归属可推测、空引文可成强支持 | II-2（P0-4）、III-2（P1-2） |
| 判断结果 | 有状态规则与新颖性记录，但比较偏词面 | III-1（P1-1） |
| 提出验证建议 | 有草案/校验，但含跨题固定句 | IV-1（P1-3） |
| 成文 | 有写作与交付包，但正文未强制沿论证链、清单缺资料/提示版本 | IV-1（P1-3）、IV-3（P2） |

---

## 2. 迭代 I（计划书 4–7 日；本计划实测 **7–12 日**，见 §8 估算冲突）

产物（计划书 §5 要求）：**经确认的研究题 + 授权资料与封存材料 + 首轮失败报告**。

### I-1 P0-1 领域验收基线（工程 2 日 + 人工 2–3 日）

| 事项 | 交付 |
|---|---|
| 工程：盲测运行口径 | 新增 `evals/cases/rf-fingerprint/runbook.md`：CLI/Web 两条等价路径、`project/problem/run` 命名、归档清单（输入、选用资料与版本、检索覆盖记录、研究日志、模型、论证链、报告、`manifest.json`、费用与停止原因）。**两种场景各一条**：①绑定用户资料库 ②只给方向 + 授权自主检索（依赖 I-3） |
| 工程：归档约定 | `evals/cases/rf-fingerprint/runs/<YYYY-MM-DD>/`：只放指针与摘要（run_id、资料集合 hash、工具版本、`metrics`、`log_anomalies`、交付等级），大文件留在 `outputs/research/…` |
| 工程：失败案例 | `evals/cases/rf-fingerprint/failures.md`（按 `evals/rubric.md` §3 表结构）——**首轮失败报告**是本迭代产物 |
| 工程：防泄漏契约 | 扩展 `tests/test_evals_contract.py`：runbook 字段齐全、失败案例表结构完整、封存答案关键结论不出现在任何运行时输入模板 |
| 工程：迁移题 | 第二领域 `evals/cases/<other>/case.md`（防射频关键词硬编码） |
| 人工 | 资料 A/B（合法可用、条件有差异）＋ 解析特例 ＋ 反例；填并**封存** `expected_notes.md` |

### I-2 P0-2 问题契约（2–3 日）

| 文件 | 改动 |
|---|---|
| `src/research/schemas.py` | 新增 `ProblemContract`：`goal / task_kind(mechanism·formal_proof·empirical_causal·scenario)`、`objects+units`、`scope`、`available_sources`、`allowed_methods`、`forbidden_substitutions`、`success_criteria`、`stop_criteria`、`frozen_version`；挂到 `ResearchSpec.contract`。改题 = `version+1` 或新 `problem_id` |
| `src/research/question_planner.py` | 新增 `formulate(request, source_summary) → FormulationResult{contract, paths[1..3], clarification}`；`paths` 每条写"能产生的结论类型 + 所缺条件"；`_applied_candidate()` 仅在契约为经验因果时才产出 `causal/observational` |
| `src/research/problem_formulator.py` | 量词/对象/方法类型判断集中到契约层；规则回退必须写进 `contract.basis`（看得出是规则还是模型定的） |
| `src/graph/theory_pipeline.py` | 入口改 `formulate()` → 冻结契约 → 再建 `ResearchSpec`；澄清**只问一个**最影响路线的问题 |
| `src/server.py` | start 响应与工作台 `state` 暴露 `contract`/`paths`；`_existing_spec_conflict()` 增加"契约不一致"分支 |

**测试**：新增 `tests/test_research_contract.py` —— 同一句"X 对 Y 的影响"在（理论模型 / 观测数据 / 只有文献）
三种输入下走不同路径；缺数据不得自称已估计因果；机理问题不得被缩成"待收集数据"；确认后契约冻结。

### I-3 自主检索闭环（**新增，关键路径，3–5 日**）

> 目标：让 §1 契约第 2 行成立 —— 系统在授权范围内自己设计检索式、检索、读全文、入库、
> 合并用户资料，并留下**可审查的覆盖记录**。

| # | 文件 | 改动 |
|---|---|---|
| a | `src/research/schemas.py` | 新增 `SourcePolicy`（`user_kb / autonomous / both`）与 `RetrievalCoverage`（`queries[]`、`engines[]`、`date_from/to`、`hits/ingested`、`fulltext_available`、`abstract_only`、`uncovered[]`、`failures[]`、`scope_note`）；挂到 `ResearchSpec` 与 `NoveltyRecord` |
| b | `src/server.py:126-127,877-887` | `StartRequest` 增 `source_policy`；`autonomous` 模式下**空资料库不再是 409**（改为允许从零检索，但把"未绑定资料库"写进契约与报告） |
| c | `src/research/query_planner.py`（新增；若 `question_planner` 过大则独立成模块） | `plan_queries(contract, gap, terminology) → list[Query]`：术语变体（中/英/缩写）、机制导向 vs 结果导向、引用追踪（被引/参考文献）、多轮扩展；每条查询记录"为什么这样问" |
| d | `evals/cases/rf-fingerprint/domain_terms.md`（人工确认） | 首个领域术语表：信道、噪声、设备特征、可分性指标的含义/单位/范围/机制关系 —— 供契约与检索式规划引用（**不放进 `references/`**：该目录被 `.gitignore` 忽略） |
| e | `src/kb/service.py` + `src/kb/bridge.py` | 落实 §3 新接口：`search/read/resolve` 返回**带覆盖范围与原文定位的研究资料**；对外隐藏"用户资料与自主检索的合并、去重、全文可得性、检索失败原因" |
| f | `src/kb/ingest.py:231` + 新调用点 | 把 `ingest_machine()` 接进研究循环：检索命中 → 自动入库（去重、来源标记）→ 建立可用于 `read` 的全文/片段；缺失全文单独标记，不冒充已读 |
| g | `src/research/evidence.py:216-222`、`src/research/retrieval.py:26-48` | 去掉"单一查询词 + 硬编码 `_DEFAULT_SOURCES`"：改为消费 `plan_queries()` 的多条查询，并把每次检索写入 `RetrievalCoverage` |
| h | `src/research/loop.py`（`_act_retrieve_targeted` 等） | `autonomous` 模式下检索动作可用（解除 `has_knowledge` 单一门槛）；记录"检索失败原因"与"未覆盖范围"，失败与无命中继续走不同路线（与 III-2 衔接） |
| i | `src/research/reporting.py` + 工作台 | 展示检索覆盖记录与来源质量状态（原始结果 / 转引 / 仅摘要 / 缺失全文），供 §7 第 2 行人工核对 |

**测试**
- `tests/test_source_policy.py`（新）：`autonomous` 模式下无资料库可启动；`user_kb` 模式仍严格按绑定资料（不越权）；
- `tests/test_retrieval_coverage.py`（新）：覆盖记录含查询/库/日期/命中/全文可得性/未覆盖；无命中 ≠ 不存在（限定语）；
- 合并去重：同一篇同时来自用户资料库与自主检索 → 只算一次来源，但保留两条来源路径；
- 可达性冒烟：只给方向 + 授权自主检索 → 能产出"资料清单 + 覆盖记录 + 至少一条可读全文定位"，即使结论是"未决"。

**验收（本迭代产物之一）**：首轮失败报告（含资料覆盖不足、检索无命中、全文缺失等真实失败项），
而不是一份"看起来完整"的报告。

---

## 3. 迭代 II（8–12 日）：模型合成 + 引文/论证硬约束

产物：**有来源的竞争模型 + 逐步推导图**。

### II-1 P0-3 用资料机制真正构建模型（4–6 日）

| 文件 | 改动 |
|---|---|
| `src/research/modeling.py` | 新接口 `synthesize_models(contract, anchored_results) → ModelComparison`；`candidate_mechanisms()` 从**带定位的原文结果**抽机制，要求结构性差异（不同作用路径或边界条件）；`mdl-empirical/mdl-simplified` 降级为无资料兜底且必须 `origin=template` + 进 `weak`，不得参与"选中" |
| 同上 | `_encoding()`（298-301）不得再回填命题既有表达式冒充模型编码：编码来自原文结果标注，或标 `proposed`（AIR 新假设） |
| 同上 | 无可靠来源 → `selected=""`，只输出待检假设；成本项不得压过"无来源" |
| 同上 | 冲突判定去掉措辞匹配（363-373）：两候选须在**相同条件**下给出不同可检验预测，否则如实报告"无法形成竞争模型" |
| `src/research/schemas.py` | `Mechanism`/`ResearchModel` 增 `anchored_results: list[ObjectRef]` 与 `assumption_origin` 明细 |

**测试**（扩展 `tests/test_research_modeling.py`）：差异不是"有条件/无条件"措辞；删去关键资料 → 模型与结论降级；无来源时不选"最佳"。

### II-2 P0-4 引文与论证关系（4–6 日）

| 文件 | 改动 |
|---|---|
| `src/research/evidence.py` | ① 强支持/反对必须有可精确定位的引文（`quote` + `char_start/char_end` + `page/location` + `text_hash` 校验），否则降为待审；② 删除 `support_evidence = excerpt[:300]` 摘要兜底；③ `assess_support()` 的"处理与结果词同时出现即 supports"降为 `partially_supports`（`reviewer="rule"` 永不出强支持）；④ `EvidenceLink` 必须比较前提与适用域（`condition_match/condition_notes`） |
| `src/research/argument.py:273-296` | 删除"同命题同版本兜底"，改显式 `ProofStep.obligation_ref → VerificationRecord.obligation_id`；缺链按缺口呈现 |
| `src/research/schemas.py` | `ProofStep` 增 `obligation_ref`；`VerificationRecord` 增 `checked_formula / premises / tool_input_digest / output_digest / expired` |
| 迁移 | 老快照隐式关联统一标 `link_status="unconfirmed"`，不暗中补边；迁移脚本 + 回归测试 |
| `src/research/acceptance.py` | 门槛加两条：强结论必须有可定位引文；论证链每跳必须有显式验证输入或标未决 |

**测试**：删一条引文 / 改原文前提 / 调换两步验证记录 → 链失效并显示缺口；反例须核验满足前提；局部核验不得让整链闭合。

---

## 4. 迭代 III（7–11 日）：已有工作比较 + 失败驱动路线修订

产物：**可解释的差异关系 + 失败后的新路线**。

### III-1 P1-1 有界命题比较（3–5 日）

| 文件 | 改动 |
|---|---|
| `src/research/novelty_compare.py` | 新接口 `compare_prior(claim, source_result)`：从已读全文抽结构化前提/结论/方法/适用域/定位；可形式化的子命题调 `verification/` 判蕴含方向；不可形式化的由独立审阅者给候选关系与理由；保留 `not_comparable` |
| `src/research/schemas.py` | `NoveltyRecord` 承载 I-3 的 `RetrievalCoverage`（不另建一套）；`comparison.bounded` 语义固定为"有界表述" |
| `src/graph/theory_pipeline.py:62` | **区分"用户授权的研究证据库"与"用于新颖性调查的检索范围"**，禁止把 kb 覆盖范围说成整个领域 |
| `src/research/reporting.py`/`package.py` | 渲染层强制限定语："在所检索范围内尚未发现等价结果"；无差异分析时禁止出现"首次/原创" |

**测试**：新增 `tests/test_novelty_scope.py` —— 同结论不同条件 / 已知等价 / 缺全文三组对照；题名相似不得判等价。

### III-2 P1-2 失败驱动的路线修订（4–6 日）

| 文件 | 改动 |
|---|---|
| `src/research/coordinator.py:334-372` | `decide(context, outcomes, budget)`：附"预期信息增益 + 成本上限"解释；失败类型化（资料未找到 / 模型被反例否定 / 形式化失败 / 工具 unknown / 预算耗尽）各自映射不同候选动作 |
| `src/research/loop.py`（`record_failure` 调用点） | 失败后必须产出**新条件、新子命题或不同机制**之一，或给停止理由；只换工具名/措辞 → 记 `no_progress` 并走向 `stop_with_report` |
| `src/research/schemas.py` | `ResearchRoute` 增 `target_gap / expected_observation / action_ref / actual_outcome / next_judgement` |
| `src/research/proposal.py` | 提议器增 `expected_information_gain`（仅日志，不参与结论升级）；仍只在动作注册表内调度 |

**测试**：新增 `tests/test_route_revision.py` —— 冲突资料 / 模型反例 / 验证 `unknown` 三者引出不同路线；日志能答"为何现在做、学到什么、为何停"。

---

## 5. 迭代 IV（7–10 日）：建议、成文、信任边界、浏览器验收

产物：**冻结快照 + 论文草稿/条件性报告 + 未执行的建议规格**。

### IV-1 P1-3 建议与正文继承真实模型（3–4 日）

| 文件 | 改动 |
|---|---|
| `src/experiments/planner.py` + `validator.py` | 落实 §3 新接口 `propose_validation(gap, models) → 未执行的实施建议或缺项`；删除跨题固定句（55-70）；每份建议必须写全：待区分的两个解释/待核验假设、变量与单位、模型/求解方式、参数范围及依据、基线与对照、误差与灵敏度检查、预期输出格式、事前判据、所需数据/设备与估算资源、失败与停止条件；缺任一项 → 保持草案 |
| `src/research/package.py:71-93` | `manifest.json` 增：资料集合版本与文件 hash、查询与原文定位、**模型与提示版本**、工具配置、预算使用；敏感原始数据不外发（只放 hash 与定位） |
| `src/agents/theory_writer.py` | 正文按显式论证链写：原问题、相关工作、定义与假设、模型、命题/引理、逐步推导、反例、结果、限制、建议；主结论仅局部核验 → 交付等级不得升级；**每条核心论断能从正文回到冻结快照的命题/证明/证据**，不由写作模型自由补足 |

**测试**：同领域两竞争模型 → 设计不同且判据不同（扩展 `tests/test_theory_mode.py`）；清单契约测试；
未执行建议不得出现"已验证"；正文论断 → 快照对象可反查（新增 `tests/test_manuscript_traceability.py`）。

### IV-2 P1-4 可见文本与外部指令信任边界（2–3 日）

| 文件 | 改动 |
|---|---|
| `src/kb/parse.py:32-42` | 新接口 `read_source(ref) → list[AnchoredSpan]`：用 `page.get_text("dict")` 带出字号/颜色/坐标/是否页外；`ParsedDoc` 增可见性元数据 |
| `src/utils/external_data.py` | 增 `visibility_flags`（近似背景色、极小字号、页外、被覆盖）→ 标"需核对"；**保留原文**，但默认不作为问题要求、研究行动或强证据 |
| `src/research/reporting.py` + 工作台 | 显示"需核对片段"定位；提供页面渲染与抽取文本对照 |

**测试**：合成 PDF 用例（近似白字、极小字、页外字、正常白底黑字）+ 指令注入用例；浅色图注不得因单一阈值被无条件删除。

### IV-3 P2 前端与交付形态（3–4 日）

| 文件 | 改动 |
|---|---|
| `src/web/src/app.ts` | 逐段移除 `@ts-nocheck`；为**会话事件 / 研究状态 / 反馈 / 交付物**定义单一类型契约（`src/web/src/types/`） |
| `src/server.py:66-68` | 生产缺 `src/web/dist/index.html` → 明确构建提示或拒绝启动，**不再返回引用 `/src/main.ts` 的源码模板**；保留开发服务器专用入口（环境变量开关） |
| `tests/browser/`（新增） | 引入 Playwright：完整路径（选资料→提问→确认候选→查看命题/原文/验证→反馈→导出）+ 错误恢复路径（断线重连 / 缺 dist 提示）+ 小窗口 + 键盘 |

**验收**：新检出按 README 构建后 Web 与 CLI 看到**同一问题快照**；生产静态资源不依赖开发服务器。

---

## 6. 端到端契约 → 交付物对照（验收主干）

| 契约阶段 | AIR 必须自主完成 | 本计划产出 | 反查证据 |
|---|---|---|---|
| 理解问题 | 区分四类问题；定量词/对象/适用域/成功判据 | `ProblemContract` + `paths` | `tests/test_research_contract.py` |
| 发现与审读资料 | 自设检索式、检索、读全文、并入用户库、区分原始/转引/摘要/缺失全文 | `RetrievalCoverage` + 来源质量状态 | `tests/test_retrieval_coverage.py` |
| 构造模型 | 抽变量/机制/方程/假设/边界；提可区分候选 | 竞争模型 + 舍弃理由 | `test_research_modeling.py` |
| 理论推进 | 子命题/义务/工具/反例；失败后改路线 | 论证图 + 验证输入输出 + 路线日志 | `test_research_argument.py`、`test_route_revision.py` |
| 判断结果 | 对照已有工作、梳理条件与反例、校准强度 | 已证/条件成立/被否定/未决 + 限定范围 | `test_novelty_scope.py` |
| 提出验证建议 | 判断哪些命题需要仿真/实验；设计可区分方案 | 未执行规格（含 §5 IV-1 的 11 项要素） | `test_theory_mode.py` 扩展 |
| 成文 | 冻结快照 → 稿件；逐条映射论断与来源 | 论文草稿/条件性报告 + 可复核清单 | `test_manuscript_traceability.py` |

---

## 7. 人工必须完成与检查的工作（严格对齐计划书 §4）

**原则**：人审**不是**每个研究动作的前置。只有 4 类停机点需要人工中断：① 问题被实质性改写；
② 资料/费用权限扩大；③ 领域含义无法自动确定；④ 最终对外声明。其余合法动作由 AIR 在预算内自行推进。

| 时间点 / 负责人 | 人工要做什么 | AIR 应提供什么（避免把研究劳动转嫁给人） | 未完成时的处理 | 记录位置 |
|---|---|---|---|---|
| **启动前**；课题负责人 | 选定首个目标领域与实际问题；定范围、成果期望、资料/数据库使用权限、预算与禁止触碰的资源；**可提供资料库，但不必手工整理每条引用**；回答真正会改变题意的澄清 | 问题复述、可能的研究路径、缺失输入、预算预估 | 核心对象不清或无资料使用权 → **暂停**，不自行猜测或越权检索 | 工作台 `contract` + 启动确认记录 |
| **盲测前**；领域专家 | 准备/核实少量代表性资料与封存的解析特例、反例；检查资料版本与入库质量；**不把封存答案喂给 AIR** | 自动摄入与索引授权资料、自主继续检索、展示解析质量与来源定位 | 材料未齐 → 只做工程回归，**不得宣称领域验收** | `expected_notes.md`（封存）+ `runs/<date>/` |
| **模型与结论形成后**；领域专家 | 检查变量定义、机制关系、边界条件、被忽略因素、结论的领域解释；确认是否真正回答原问题 | 模型来源、条件差异、被舍弃的解释、反例、无法判断项 | 模型不忠实或强度过高 → **退回研究循环**，不得只改论文措辞 | `evals/rubric.md` §2.3–§2.4 + `reviews/<date>.md` |
| **推导完成后**；数学/方法专家 | 独立抽查核心推导、形式化编码与原问题的等价性、求解器前提、反例有效性；检查已有工作比较关键条目 | 逐步证明图、工具输入/输出、原文链接、条件与版本 | 核验不足 → 标条件性/未决，**不盖"已证明"** | `reviews/<date>.md` + `manifest.json` |
| **方案交付前**；实验/仿真领域人员 | 评估建议可实施性、参数与资源是否现实、判据能否区分解释；决定未来是否授权执行 | 未执行的完整规格 + 缺项清单，**不生成虚构结果** | 规格缺项 → 保持草案；本版本不执行实验/仿真 | `experiment_specs/` + `delivery_gate.md` |
| **对外提交前**；课题负责人/作者 | 审阅最终文本、引用、署名、合规；确认交付等级；决定是否投稿 | 与冻结快照一致的稿件、可复核清单、限制说明 | 未签字产物只作内部研究草稿，**不称已发表成果** | `reviews/<date>.md` 签字表（`rubric.md` §4） |

**失败案例台账**（`failures.md`）：每条"不通过"留维度、现象与文件/行定位、期望、处理、状态；
"判定为非缺陷"必须写理由；历史记录**只追加不删除**。

**工程侧可代做（减少人工负担）**：建 `data/kb/rf-A|rf-B/manual/` 与 `meta.json` 模板（按文件名登记
authors/year/venue/volume/issue/pages/doi），人只放 PDF 与书目字段，随后执行
`python -m src.kb ingest --topic "rf-A"` 并以 `stats` 核对；起草 `runbook.md`、`failures.md`、
`reviews/` 模板、第二领域 case 骨架、`domain_terms.md` 空白结构；生成归档摘要（run_id、资料 hash、
覆盖记录、工具版本、费用、停止原因）；录入评审结论。**签字与领域真值判定必须人工。**

---

## 8. 依赖、并行与估算

**强顺序**：I-1（资料与封存答案）→ I-2（契约）→ I-3（自主检索）→ II/III → IV。
理由：没有真实资料与冻结契约，P0-3/P0-4 只能在玩具题上自证；没有 I-3，契约第 2 行与 P0-1 场景 ② 无法成立。

**可并行**：II 与 III 可在独立样例上并行开发，但都必须过同一领域盲测回归；IV-3（前端）可由前端开发并行推进。

**外部依赖（不计入工程日）**：资料合法可用性与整理、领域专家评审与签字、模型调用费用与网络、
第二任务类型题目设计、Playwright 浏览器二进制下载、如需出 PDF 则需 `xelatex`。

**估算冲突（需你决定）**：计划书 §5 给迭代 I 为 4–7 日，但其 P0-1 已包含"只给方向 + 授权自主检索"场景，
而该能力经核对属于**缺失**（§1.1），我单列 I-3 为 3–5 日。因此：

- **方案 A（推荐）**：迭代 I 按 **7–12 日** 计，总估算变为 **29–45 日**；
- **方案 B**：拆为 I-a（4–7 日：资料/契约）与 I-b（3–5 日：自主检索，与迭代 II 并行），总估算仍落在 26–40 日框架内。

| 迭代 | 工作与产物 | 门槛 | 估算 |
|---|---|---|---|
| I | P0-1 基线 + P0-2 契约 + **I-3 自主检索**；产出经确认的研究题、授权资料、首轮失败报告 | 问题未被偷换；两种资料场景都能启动；封存材料与授权由人完成 | 7–12 日（见上） |
| II | P0-3 + P0-4；产出有来源的竞争模型与逐步推导图 | 空引文/错配验证不支撑强结论；专家核对变量、条件、核心推导 | 8–12 日 |
| III | P1-1 + P1-2；产出可解释的差异关系与失败后新路线 | 等价/条件不同/不可比可区分；反例、资料冲突、unknown 后动作不同 | 7–11 日 |
| IV | P1-3 + P1-4 + P2；产出冻结快照、稿件/条件性报告、建议规格 | 无隐藏文字改题；真浏览器完成任务；专家审核结论与建议，负责人签字 | 7–10 日 |

**每轮纪律**：失败案例追加记录，不用改测试期望掩盖研究错误；迭代结束时更新
`AI for Research(AIR)执行进度.md`（写"命令 + 结果"，不写"应该能"）。

---

## 9. 发布判据（逐条对应计划书 §5）

1. 基础代数回归题仍正确处理等号条件、严格不等式反例与 `unknown`，但**不代表领域研究验收**；
2. 射频指纹盲测每条核心结论可追溯到明确问题、模型条件、原文或验证输入；领域专家给出"通过/需修改/不通过"及证据位置；
3. 两种竞争机制至少形成一条真正可区分的推导或仿真建议；缺必要信息时如实停在草案；
4. 已知等价研究不被写成原创；覆盖不充分不被写成"没有先例"；隐藏指令不改变研究行为；
5. 新检出环境可构建前端并通过真实浏览器流程；Web、CLI 与导出包引用同一问题与快照；未构建页面有明确错误提示；
6. 第二个任务类型的迁移用例不依赖射频关键词；不成功则保留失败记录与能力边界。

## 10. 明确不做（计划书 §1 边界）

- 不自动运行仿真或实际实验、不训练新模型、不操作仪器、不自动投稿；
- 只产出**可由人实施**的仿真/实验建议，并标注"未执行"；
- 不承诺任意领域都能得出新定理；交付等级仍由现有门槛决定；
- 不声称普遍原创；不把未执行建议写成实验结果；
- 不用玩具测试或测试数量替代领域正确性结论；
- 不为通过验收修改测试期望或删除失败记录。
