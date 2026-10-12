# 报告 01：`proj-mv4x8ywydolg`「星网→临海渔业」未完成运行审查（2026-10-12）

| 项 | 值 |
|---|---|
| 运行 | `proj-mv4x8ywydolg` / `problem` / run `请你研究星网系统的建设会给中国临海渔业产_20261012_152132_1f7a` |
| 会话 | Web 会话 `6e119c3b29474e998cc1ef83b975fdae`（1459 条消息） |
| 输入 | 「请你研究星网系统的建设会给中国临海渔业产业带来怎样的影响？进行研究并最终撰写成文。」 |
| 授权 | `source_policy=both`；**`source_set_id=星网_星链`**（规范 id）；`bound_source_set_id=星网_星链` |
| 时段 | 15:21:32 开始 → **17:03:18 被停止**（约 102 分钟）；会话 `status=stopped` |
| 完成度 | **未导出**：无 `snap-*`、无 `manifest.json`、无 `.tex`/PDF、无交付包 |
| 规模（store） | evidence **147**、model 11、claim 7、obligation 8、verification 8、validation_plan 1、manuscript **7**、review_issue 24、**figure 0** |
| 评审 | **score 28 / threshold 80**，`decision=major_revision`，`accepted=False`，fatal 5 条 |
| 审查日期 | 2026-10-12 |

---

## 0. 一句话结论

**上一轮的两个修复在真实运行中都生效了**（资料库按规范 id 绑定成功、13→26 篇全文进入证据、稿件不再出现修订口吻与虚构图号、7 节大纲正是新写作链路的结构），但这次运行**暴露了一个更深的问题**：建模层把**通用教科书方法学**（Schaefer 剩余产量、Hawkins–Simon/Neumann 级数、序贯可忽略性、递归线性结构方程）当成研究的核心模型，于是 8 条义务**全部**是数学恒等式/等式条件，**7 条 blocked、核验 7/8 stale、命题 4/7 blocked**，没有任何 settled 结论。证据角色自己就报出了根因：「**授权语料是卫星通信/星座技术的专业语料，不含渔业生物经济学、投入产出数学、因果推断识别理论的文献**」。运行跑了 102 分钟、产出 7 个稿件版本、评审分仍是 28/80，最后由你手动停止，**没有任何可交付物**。

---

## 1. 完成度与产物清单

`progress/`（提案与过程快照）：

| 产物 | 大小 | 时间 | 说明 |
|---|---|---|---|
| `brief.json` / `plan.json` / `research_spec.json` | 5.5 / 17.3 / 3.4 KB | 15:21:46 | 任务书、计划、规格 |
| `review-report.json` | 16.9 KB | 16:50:52 | 独立评审（首份） |
| `evidence.json` | 312 KB | 17:03:17 | 提案流（含 `_proposal_id` 等字段） |
| `model.json` | 98.5 KB | 17:03:17 | 模型 11 条 |
| `claim.json` / `obligation.json` / `verification.json` | 32.1 / 10.0 / 13.8 KB | 17:03:17 | 命题 7 / 义务 8 / 核验 8 |
| `validation_plan.json` | 14.0 KB | 17:03:17 | 验证方案 1 条（含竞争假说） |
| `review_issue.json` | 42.7 KB | 17:03:17 | 评审意见 24 条（累计） |
| `manuscript.json` / `manuscript-draft.md` | **949 KB** / 60.7 KB | 17:03:17 | **7 个稿件版本** / 最新稿 Markdown |
| `progress.json` | 19.8 KB | 17:03:17 | 计数与最近任务 |

**缺失（未完成态）**：`snap-*` 快照、`manifest.json`、`manuscript.tex`、PDF、`unresolved.md`、交付包 —— 即**没有进入提交/编译/导出阶段**，因此**没有成本记录**（用量只在 manifest 里）。

`progress.json` 的 `note` 自己写明：**「过程快照；科研对象以 ResearchStore 为准，稿件尚未完成最终审阅」**。

时间线里有一个显著的空档：**15:21:46 → 16:50:52（89 分钟）** 之间没有任何 progress 文件更新，随后 17:03:17 一次性刷新全部 —— 说明过程记录是**按轮次批量落盘**的，中途看不到增量；这段时间正是 evidence/model/claim/obligation 反复提案与核验的阶段。

---

## 2. 已确证生效的修复（与上一轮 `proj-mv3omgh65ukz` 对照）

| 项 | 上一轮 | 本轮 | 结论 |
|---|---|---|---|
| 资料库绑定 | `source_set_id=""`（未绑定） | **`星网_星链`**（规范 id）+ `bound_source_set_id=星网_星链` | **库名规范化修复生效** ✓ |
| 证据里的全文 | fulltext **4** 条 | fulltext **34** 条，其中 **26 条来自 `星网_星链`** | **绑定带来真实全文** ✓ |
| 稿件修订口吻 | `.tex` 含"上一版"16 / "本版"12 | **上一版 0 / 本版 0 / 修订版 0 / 审阅意见 0** | **PR #4 审计与大纲守卫生效** ✓ |
| 图号 | 正文引用**未登记**的图 1–4 | 正文**无任何图号**（图数为 0） | **未登记图号审计生效** ✓ |
| 稿件结构 | 单次整篇 JSON，后部章节易丢 | **7 节结构**：引言 / 相关研究与缺口 / 研究设计与资料基础 / **3.1 模型、假设与识别方法** / **候选机制与可检验命题** / 讨论与局限 / 结论 | **PR #4 分节链路生效**，且第五节正确用了"无已确定命题"分支（未写"研究结果"）✓ |

即：上一轮报告里的 P-1（查错库）、P-3（渲染/写作构造）、以及 PR #4 的写作链路，**在本轮真实运行中都被验证为有效**。

---

## 3. 本轮发现的问题

### P-1【严重】建模层脱离授权语料，把通用教科书方法学当成研究核心

**证据**：

- 8 条义务的**种类全是数学**：`prove_identity` **4** 条 + `equality_condition` **4** 条；状态 **7 blocked + 1 closed**。
  例：`latest_task.objective = "义务未关闭: 核验恒等: q·E_MSY·N_MSY = r·K/4"`；会话里另见「核验恒等: `r·N*·(1 − N*/K) − q·E·N* = 0`」「核验恒等: `(I − A)⁻¹ = Σ_{k=0}^{∞} A^k`」。
- 7 条命题中 **4 条 blocked**，全部是通用结论：Schaefer/Logistic 均衡 `N* = K·(1 − q·E/r)`、Hawkins–Simon/Neumann 级数、递归线性结构方程的总效应分解、MSY 点 `E_MSY = r/(2q)、N_MSY = K/2、H_MSY = r·K/4`。
- 核验 8 条：**unsupported 6 / passed 1 / unknown 1**，且 **stale 7 条**（命题版本推进使核验过期）。
- **证据角色自己给出了根因**（`progress.json` 最近任务摘要，逐字）：

  > 「在授权语料 (topic=星网_星链, policy=both) 中执行了 12 次查询。**关键观察：授权语料是卫星通信/星座技术的专业语料，不含渔业生物经济学、投入产出数学、因果推断识别理论的文献。** 且相当一部分命中是关键词假阳性（例如 "Schaefer…MSY" 查询返回的是激光通信综述的参考文献列表、"投入产出 Hawkins–Simon" 查询返回的是 NTN 透明转发/多普勒信号片段），这些命中与检索意图无关，不作为来源登记。」

**影响**：义务账本被"证明教科书恒等式"占满 → 循环把预算花在无法从授权语料获得依据的数学核验上 → 102 分钟不收敛、**零 settled 结论**、无导出。

**可能原因**：建模角色的提示词允许自由选择形式化框架，但**没有任何环节要求"该框架在本研究的授权语料中有出处"**；而 `prove_identity`/`equality_condition` 义务又会对任何自造模型自动生成，形成"自造模型 → 自造义务 → 无法核验 → 阻塞"的闭环。

**建议**：
1. 建模层加一条硬约束：模型所依赖的框架/定理必须**在授权语料中有可定位出处**，否则只能标为"方法学假设（无本研究来源）"且**不生成 `prove_identity` 义务**；
2. 对"教材级恒等式"不生成核验义务（它们不是本研究的发现）；
3. 建模前先做一次"语料—问题匹配度"判断：语料覆盖不到问题的核心概念时，**先如实降级**（研究备忘录 + 数据需求），而不是继续构造形式化体系。

### P-2【中】没有收敛信号，也没有出口

- 7 个稿件版本、评审分 **28/80** 且 `accepted=False`，运行由用户手动停止；
- 会话事件计数显示提案 churn 很高：**`change_proposed` 873 条**（文本全部相同、无 payload，属事件类型）、`tool_run` 149、`llm_call` 143、主控轮次 **15**；
- `progress.json` 无"停滞判定"或"预算即将耗尽"的记录，也**没有任何自动降级导出的痕迹**。

**影响**：用户要在 100 分钟后自己判断"它不会好了"，而系统本可以在连续 N 轮评审分不升 / 同一义务连续 blocked 时**主动停止并如实导出研究备忘录**。

**建议**：加停滞检测（连续 K 轮评审分与 blocked 义务集合都不变 → 停止；或按预算消耗比例），并以"研究备忘录"完成导出，同时在未决项里写清"哪些义务为什么无法关闭"。

### P-3【中】引用一致性：声明"不引用"却在正文引用并列入文献表

评审 blocking 意见（`issue-3e43ff236f48`，category=citation）：

> 「参考文献[11][12][13]标注为2026年出版，但当前核验语境下无法确认其出版状态，**正文已声明不引用却仍在参考文献表中列出**」

**我独立复核的结果（在真实稿件里复现）**：

- 稿件中确有声明：「对出版状态尚未确认的条目，**亦不作为论证依据**」「本节**不引用任何估计值**」；
- 但 **[11][12][13] 同时出现在正文的引用锚点里**，例如
  「…而非已测得效应**[1][11][12][7]**」「…并明确养殖排放压力的度量口径**[4][6][3][10][13]**」；
- 参考文献表共 13 条，其中 **3 条含 2026**，来源为 `Hans Journal of Agricultural Sciences`、`Advances in Social Sciences`、`Open Journal of Fisheries Research`（汉斯系期刊），**表内没有任何状态标注**。

**即：问题不是"列了没引用的文献"，而是"声明不作为依据的条目却在正文里当了引用锚点、且文献表未标注出版状态待核"** —— 评审的判断成立，其验收判据（删除 / 移入"待补读文献"附录 / 明确标注"不作为论证依据"）也可执行。

### P-4【中】写作层对核验状态的表述越界

- 稿件一处写「**已知的是**模型内部的关系及其前提：…」，同段结尾又写「…后两者的**核验尚未闭合**」；全文"已知"6 次、"待核验"2 次、"核验尚未闭合"2 次、"已核验"0 次。
- 而核验记录是 **unsupported 6 / passed 1 / unknown 1**。

评审 blocking 意见要求「将'已知'层级严格限定为已完成核验的命题…并在正文中明确标注各命题的核验状态（passed/unknown/unsupported/blocked）」。

**性质**：判定层没错（它把命题标成 blocked），是**写作层措辞把"未核验"写成了"已知"**。建议在写作审计里增加一条零 LLM 检查：正文出现"已知/已证实/已核验"时必须能指向 `status=supported` 的命题或 `passed` 的核验记录，否则触发修稿需求。

### P-5【中】证据仍以摘要级为主，且无一条被判定为支持

- `content_level`：**abstract 107 / fulltext 34 / metadata 6**；
- `locator` 形态以 `abstract chars 0-600` 为主（75 条）；
- `relation`：**insufficient 140 / background 7** —— **没有任何一条被判为 supports**；
- `credibility`：high 109 / medium 27 / low 11（来源本身可信度高，但不能支撑命题）。

即使绑定了 41 篇全文的语料，**证据层仍无一条能支撑结论** —— 这与 P-1 互为因果：问题需要的证据类型（渔业生物经济学/因果识别）不在语料里，而语料擅长的（星座技术）又支撑不了这些命题。

### P-6【轻】图表仍为 0

`figure` 计数 0，评审仅给了一条 **minor** 意见：「全文未包含任何图或表，但多次提及'框架''通道''分解'等空间结构概念，缺乏可视化辅助影响可读性」。本轮未见配图需求派发（运行被停止前可能尚未进入配图轮次）。**连续两次运行 figure 均为 0**，值得确认 Figure 角色在无显式配图需求时是否真的会被派发。

### P-7【观察】分级字段只在导出阶段产生（不是本轮缺陷）

`ResearchStore` 里本轮与上一轮**都是** `evidence_grade=None` / `support=None`（全量），只有 `relation` 有值 —— 说明 `evidence_grade`/`support` 是**快照/导出阶段**才补齐的字段，不是 store 字段。因此本轮"看不到分级"是**未导出**的必然结果，而非回归；但也意味着：**运行一旦中途停止，就完全没有分级结果可查**，只剩 `relation`。

### P-8【观察】规格仍未澄清就开跑

`research_spec.json`：`domain=""`、`confirmed=False`、`contract.basis="缺少可判定的问题类型信号"`、`contract.clarification=""`。与上一轮同一模式：**问题类型信号缺失时系统不请求澄清，直接按 `scenario` 开跑**。

---

## 4. 智能体协作评估

**正常的部分**：

- 派发与留痕齐全：`brief.json` 拆出 **11 个子问题**（evidence ×4、modeling ×2、writing ×2、validation ×1、review ×1、reasoning ×1），每个子问题都有 owner 与验收标准；
- **证据角色的自我诊断非常诚实且有价值**（P-1 引用的那段），它明确区分了"命中"与"支持"，并把关键词假阳性排除在外，没有把命中当支持；
- **评审角色继续高质量工作**：28/80、`major_revision`、2 blocking + 5 fatal，每条都带 `suggested_owner`、`acceptance`、`locator`；`category_scores` = science **0** / fidelity 55 / citation **0** / readability 60 / figure 30 / completeness 50；
- **写作层有真实产出的正面内容**：`gap` 2 条都是**否定性发现** ——「提升监测与信息能力（提高有效努力 E）**必然**提高可持续产量或改善资源状况」**不成立**；「星网建设提高渔业产业结果（总效应为正）在结构模型内**不成立**，因为许可参数下总效应符号可正可负」。`validation_plan` 给出两条**竞争假说**（H1 生产效率假说 vs H2 监管强化假说）—— 这是切题、可检验的研究内容。

**有问题的部分**：

| 问题 | 证据 |
|---|---|
| **建模层与语料脱节**（P-1） | 8/8 义务为数学、7 blocked；证据角色明确指出语料不含所需学科 |
| **无收敛与出口**（P-2） | 7 个稿件版本、评审分不变、873 条提案事件、需人工停止 |
| **写作层表述越界**（P-4） | "已知"与"核验尚未闭合"同段并存 |
| **引用一致性**（P-3） | 声明不作为依据的条目仍是正文引用锚点 |
| **图表零产出**（P-6） | 两次运行 figure 均为 0 |

---

## 5. 产出质量评估

- **稿件**：最新版 8 个标题（7 节 + 参考文献），24,466 字符；结构完整、领域词汇（渔业/养殖/监测/资源）到位；
- **强项**：正确地把结论限定为"条件性代数结论与识别条件"，并明说「**相应恒等式的核验尚未闭合**，故这里给出的是条件性代数结论与识别条件，而不是已确证的因果效应」—— **科学表述的克制是对的**；
- **弱项**：把通用教材结论抬到"已知"层（P-4）、把出版状态未定的条目当引用锚点（P-3）；
- **总体**：**未达到任何交付门槛**（评审 28/80 `major_revision`；且因为未导出，交付门槛都没机会跑）。评审分与上一轮（26/80）几乎持平 —— 即使修好了 P-1 之外的链路问题，**只要建模层与语料脱节，分数就上不去**。

---

## 6. 可能原因汇总

| 现象 | 直接原因 | 更深一层 |
|---|---|---|
| 8 条义务全是数学、7 条 blocked | 建模层选了 Schaefer / Neumann / 序贯可忽略性 / 递归 SEM | 缺"框架必须在本研究语料中有出处"的约束；`prove_identity` 义务对自造模型自动生成 |
| 102 分钟不收敛、无导出 | 循环持续在无法闭合的义务上提案（873 条提案事件、7 个稿件版本） | 没有停滞检测与自动降级导出 |
| 证据 0 条 supports | 语料擅长的主题与问题所需主题不重合 | 语料—问题匹配度未在建模前评估 |
| 评审 citation 0 分 | 未核验出版状态的条目被当引用锚点 | 引用服务的边界只写在正文声明里，未落到文献表与审计 |
| "已知"与"未核验"并存 | 写作层措辞未绑定核验状态 | 缺"断言层级 ↔ 核验状态"的零 LLM 审计 |
| figure 0 | 未见派发配图需求 | Figure 角色的触发条件不明确 |

---

## 7. 建议（按优先级）

| # | 建议 | 对应问题 |
|---|---|---|
| 1 | **建模锚定语料**：框架/定理须有授权语料出处；教材级内容不生成核验义务；语料与问题核心概念不匹配时**先降级** | P-1 |
| 2 | **停滞检测与自动导出**：连续 K 轮评审分不升或 blocked 义务集合不变 → 停止并导出"研究备忘录"，未决项写明原因 | P-2 |
| 3 | **新增零 LLM 审计**：① 正文"已知/已证实/已核验"必须指向 `supported` 命题或 `passed` 核验；② 参考文献表中出版状态未确认的条目须标注或移入附录 | P-4 / P-3 |
| 4 | **语料—问题匹配度评估**：建模前判断授权语料是否覆盖问题核心概念，并据此给出"可研究/需补料"的结论 | P-1 / P-5 |
| 5 | 明确 Figure 角色的触发条件（何时必须配图、何时只是 warning） | P-6 |
| 6 | 问题类型信号缺失时**请求澄清**而不是默认 `scenario` | P-8 |

---

## 8. 边界：已核实 / 未验证 / 未做 / 未评估

**已核实（有产物或实测支持）**

- 运行在 17:03:18 被停止（会话末条为 `[stopped] 已停止`），**未导出**：无 `snap-*`/manifest/tex/PDF
- 绑定为规范 id `星网_星链`（spec + 会话两处一致），证据里 26 条全文来自该库
- 稿件修订口吻与图号均为 **0**；7 节结构与 PR #4 大纲一致
- store 与 progress 的全部计数、义务/核验/命题状态、评审分数与维度得分、评审意见逐条内容
- 参考文献表 13 条、其中 3 条含 2026；[11][12][13] 在正文出现且正文有"不作为依据"的声明
- 证据角色的语料不匹配诊断（逐字引用）

**未验证**

- **无法给出成本/用量**：未导出 manifest，store 中也没有用量表
- 交付等级与编译结果**未知**（未到该阶段）；`figure` 未产出是否因"被停止前未轮到"**无法确认**
- 873 条 `change_proposed` 中"提案 → 被取代"的具体比例未逐一追踪（仅知文本全部相同、无 payload）
- 评审意见是否在停止前的最后一轮被处理过（`manuscript` 7 个版本与评审记录的先后对应关系未逐一比对）

**未做**

- 未修改任何代码或数据（本报告只做审查）
- 未重跑该运行；未用本机 `xelatex` 编译（没有 `.tex` 可编译）

**未评估**

- 与该课题领域内的"合格研究报告"基线相比的绝对质量（无人工基线可比）
- 其余历史运行（本报告只覆盖 `proj-mv4x8ywydolg` 本次未完成运行）

---

## 9. 复现命令

```powershell
cd E:\AIR

# 运行身份与停止状态
.venv\Scripts\python.exe -c "import json;r=json.load(open(r'data/conversations/6e119c3b29474e998cc1ef83b975fdae/conversation.json',encoding='utf-8'));print(r['status'], r['request']['source_set_id'], r['bound_source_set_id'], len(r['messages']));print(r['messages'][-1])"

# 规格（未澄清）
.venv\Scripts\python.exe -c "import json;print(json.load(open(r'outputs/research/proj-mv4x8ywydolg/请你研究星网系统的建设会给中国临海渔业产_20261012_152132_1f7a/progress/research_spec.json',encoding='utf-8')))"

# store 侧计数与义务/核验状态
.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,r'E:\AIR');from src.research.store import ResearchStore;from collections import Counter;s=ResearchStore('proj-mv4x8ywydolg',db_path=r'E:\AIR\data\research\proj-mv4x8ywydolg.sqlite');[print(k,len(s.list_latest(k)),dict(Counter(str(r.get('status')) for r in s.list_latest(k)))) for k in ('evidence','claim','model','obligation','verification','manuscript','figure')]"

# 证据来源与全文比例
.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,r'E:\AIR');from src.research.store import ResearchStore;from collections import Counter;s=ResearchStore('proj-mv4x8ywydolg',db_path=r'E:\AIR\data\research\proj-mv4x8ywydolg.sqlite');ev=s.list_latest('evidence');print(dict(Counter(str(r.get('content_level')) for r in ev)));print(dict(Counter(str(r.get('source_set_id')) for r in ev)));print(dict(Counter(str(r.get('relation')) for r in ev)))"

# 评审分数与 blocking
.venv\Scripts\python.exe -c "import json;d=json.load(open(r'outputs/research/proj-mv4x8ywydolg/请你研究星网系统的建设会给中国临海渔业产_20261012_152132_1f7a/progress/review-report.json',encoding='utf-8'));print(d['score'],d['threshold'],d['decision'],d['accepted']);print(d['category_scores']);[print(i['severity'],i['summary'][:90]) for i in d['issues']]"

# 稿件: 修订口吻与图号审计
.venv\Scripts\python.exe -c "from pathlib import Path;p=Path(r'outputs/research/proj-mv4x8ywydolg/请你研究星网系统的建设会给中国临海渔业产_20261012_152132_1f7a/progress/manuscript-draft.md');t=p.read_text(encoding='utf-8');print({k:t.count(k) for k in ('上一版','本版','修订版','审阅意见')});import re;print('图号',sorted({int(m.group(1)) for m in re.finditer(r'图\s*(\d+)',t)}))"
```

---

**报告完。** 要点：**上一轮的修复已在本轮真实运行中验证生效**（库绑定/全文入库/写作链路/审计），但**建模层与授权语料脱节**导致 8 条数学义务全部无法关闭、102 分钟不收敛、无导出 —— 这是目前最值得先修的一项；其次是**停滞检测与自动降级导出**，它能让这类运行以"研究备忘录"体面收场，而不是等你手动停止。
