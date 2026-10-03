# AIR 智能体团队合并计划（统一学术研究智能体系统）

编写日期：2026-10-03
基线提交：`c794d97 Bootstrap unified AIR research system baseline`（233 文件 / +53,517 行）
依据：当前源码实际结构（行号可核）、874 项离线测试、评估用例与已保存运行记录。

---

## 0. 一句话目标

把现在**两套并行、能力互补但不共享**的系统，合并为**一个统一的学术研究智能体系统**：
用户给出方向/问题/附件/授权资料范围，一个**智能体团队**分工完成
「检索 → 建模 → 推导 → 成文 → 配图 → 审阅」，产出**可追溯到证据与前提**的出版级论文草稿。

**不可让渡的底线**（合并中必须保持）：结论的**判定权不在智能体手里**。
智能体只能产出结构化对象与解释；结论等级由证书链、工具重算与中央状态规则决定。
引用与叙述只能改表述，**不得改结论**。

---

## 1. 现状：为什么必须合并

| 维度 | 综述模式（`src/graph/pipeline.py`） | 理论模式（`src/graph/theory_pipeline.py`） |
|---|---|---|
| 图节点 | 20 个（含 5 个人工确认点） | 5 个（bootstrap / confirm / step / feedback / finalize） |
| 状态字段 | `PipelineState` **86 字段** | `TheoryState` **43 字段** |
| 共享字段 | 仅 5 个：`current_phase`、`error`、`human_feedback`、`interactive`、`run_id` | 同上 |
| 智能体化 | 高：文献查阅（**绑工具 + 多轮闭环**）、大纲、初稿、审阅、图表（绘制/代码审阅/视觉审阅） | 低：**1 个 LLM 控制器** + 确定性写作器（`theory_writer.py` **零 LLM 调用**） |
| 推理能力 | **无**：不做建模、不做推导、无结论核验 | **强**：证书链、sympy/z3/lean 重算、四级交付、反幻觉防火墙 |
| 编排机制 | LangGraph 边 + 人工中断 | LangGraph 5 节点 + **3909 行控制器循环**（26 种动作） |
| 前端 | 工作台按"模式"分叉 | 同一前端、不同分支 |

**结论**：两者能力互补而形态割裂。用户看到的既不是"综述工具"也不是"定理证明器"，
而是一个**该有六个专职智能体、实际只有半个团队**的系统。

---

## 2. 目标形态：智能体团队 + 判定层

```
                    ┌──────────────────────────────────────────┐
   用户输入 ───────▶ │  统一入口: 方向/问题/附件/授权资料范围      │
   (自然语言/附件)   └──────────────────┬───────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────┐
                    │  会话与编排层 (单一 SessionController)     │
                    │  · 任务类型判定 (推导型/综述型/混合)        │
                    │  · 人工确认点 (方向 · 结论 · 投稿)          │
                    └──────────────────┬───────────────────────┘
                                       ▼
   ╔═══════════════════════════════════════════════════════════════════════╗
   ║  智能体团队 (每个智能体: 角色提示 + 受限工具集 + 结构化产物)            ║
   ║                                                                       ║
   ║  ① 检索智能体      ② 建模智能体      ③ 推导智能体                     ║
   ║   LiteratureAgent  ModelingAgent     DerivationAgent                  ║
   ║   · 多源检索        · 变量/量词/域     · 证明义务拆解                   ║
   ║   · 全文下载入库    · 假设显式化       · 步骤提议 (不可自证)            ║
   ║   · 来源卡+定位     · 已知结果锚定     · 反例搜索                       ║
   ║                                                                       ║
   ║  ④ 撰写智能体      ⑤ 配图智能体      ⑥ 审阅智能体                     ║
   ║   WritingAgent     FigureAgent       ReviewAgent                      ║
   ║   · 大纲→段落       · 图表规划         · 维度化评审                     ║
   ║   · 逐句回链        · 绘制/代码审阅/   · 一致性矛盾发现                  ║
   ║     命题ID+来源      视觉审阅          · 只可降级, 不可升级              ║
   ╚═══════════════════════════════════┬═══════════════════════════════════╝
                                       │ 只能提交"结构化对象"
                                       ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │  判定层 (确定性, 无 LLM)  ★ 不可让渡                                    │
   │  · 形式化核验 (sympy/z3/lean/stats)   · 证书链 (design_necessity)       │
   │  · 中央状态规则 reconcile → supported/refuted/blocked/in_progress       │
   │  · 引用守门 (可定位引文才可引用)      · 四级交付 classify_deliverable    │
   │  · 发表门槛 (章节/编号/引文/编译/一致性)                                 │
   └───────────────────────────────────────────────────────────────────────┘
                                       ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │  产物: 出版级 .tex/.pdf + 参考文献 + 图表 + 判定链 + 未决说明 + 人审清单  │
   └───────────────────────────────────────────────────────────────────────┘
```

### 2.1 六个智能体的职责与**能力边界**（这是合并的核心设计）

| # | 智能体 | 输入契约 | 输出契约 | 可用工具 | **禁止** |
|---|---|---|---|---|---|
| ① | `LiteratureAgent` | `LiteratureNeed`（术语/定义/已知结论/反例/最近结果） | `SourceCard[]`（含定位、可信度、`RetrievalAttempt` 记录） | 多源检索、全文下载、原文解析、卡片抽取 | 不得声明"支持结论"（只能给候选 + 引文片段） |
| ② | `ModelingAgent` | 契约 + 来源卡 | `ResearchModel`（变量域、量词、假设、符号表）+ `PremiseAssessment` | 读来源卡、查已登记定理 | 不得断言结论；假设变更必须**显式版本化** |
| ③ | `DerivationAgent` | 命题 + 义务 + 可用定理 | `ProofObligation[]` 拆解 + 步骤提议 | 调核验器（**只提交**，不判定）、反例搜索 | 不得自证；不得把"命名定理登记"说成原创 |
| ④ | `WritingAgent` | 冻结快照 + 参考文献 + 大纲 | `Manuscript`（块级，每段带命题 ID + 来源键） | 读快照、读文献、读图表清单 | **不得引入快照外的新事实**；不得升级结论强度 |
| ⑤ | `FigureAgent` | 快照 + 文稿（数据/结构） | `FigureSpec[]` + 产物图 | 绘图库、代码审阅、视觉审阅 | 不得凭空生成数据点 |
| ⑥ | `ReviewAgent` | 文稿 + 快照 + 门禁结果 | `ReviewIssue[]`（严重度、定位、建议） | 只读全文与证据 | **只可降级/提出质疑，不可升级结论** |

**判定层**（`src/verification/`、`research/acceptance.py`、`research/design_feasibility.py`、
`research/argument.py`、`research/loop.py` 的 `_reconcile_claim`）**保持无 LLM**，
继续作为唯一能改变 `status` / `validation_status` / 交付级别的代码路径。

---

## 3. 后端合并方案

### 3.1 统一状态与编排（合并的难点）

**问题**：`PipelineState`(86 字段) 与 `TheoryState`(43 字段) 只共享 5 个字段。
直接把两者塞进一个 dict 会得到一个 130 字段的"上帝状态"，无法维护。

**方案**：拆成"**共享信封 + 领域载荷**"，用 Pydantic 子模型分层，而不是平铺字段。

```python
class ResearchEnvelope(TypedDict):        # 两个模式共用的信封 (取代现在的 5 个重叠字段)
    session_id: str
    run_id: str
    task_kind: TaskKind                    # derivation | survey | hybrid
    phase: str
    human_feedback: list[dict]
    interactive: bool
    capabilities: CapabilityTable          # 本轮各智能体可用工具 (UI 据此隐藏不可执行动作)
    artifacts: ArtifactIndex               # 产物索引
    error: str

class UnifiedState(TypedDict):
    envelope: ResearchEnvelope
    literature: LiteratureSlice            # 检索智能体的产物 (取代综述的 retrieved_papers 等)
    modeling: ModelingSlice                # 契约/模型/假设/符号
    derivation: DerivationSlice            # 命题/义务/验证记录/证书
    writing: WritingSlice                  # 大纲/文稿/图表/参考文献
    review: ReviewSlice                    # 审阅意见/门禁结果
```

- **迁移策略**：新 `UnifiedState` 与既有两个 state **并存一个版本**；用适配器
  `to_legacy_survey()` / `to_legacy_theory()` 把 `UnifiedState` 投影回旧结构，
  使既有 874 项测试**在合并期间持续通过**（这是"可回溯"的保证）。
- 旧 state 在 Phase 4 删除。

### 3.2 统一图（单一 LangGraph）

```
start → intake ─┬─▶ literature ──▶ modeling ──▶ derivation ──▶ writing ──▶ figures ──▶ review ──▶ gates ──▶ finalize → end
                │        ▲              ▲             │            ▲                          │
                │        └──────────────┴─── 回写: 相反来源/前提变化 → 命题换代 ──────────────┘
                └─▶ human_confirm_scope (人工确认: 研究方向与授权范围)
                                        human_confirm_conclusions (人工确认: 主要结论)
                                                                    human_confirm_submission (人工确认: 投稿)
```

- 三个 `human_confirm_*` 用 LangGraph `interrupt` 实现（理论模式已有此机制）。
- 「回写」边复用已实现的 `_literature_impact_recheck()`：来源与前提变化 → 命题新版本 +
  旧验证 stale + 义务重开。**巡检式**（每步后检查）而非固定边，避免图爆炸。
- 综述模式缺推理环节：`literature` 之后**直接跳 `writing`** 的路径保留（纯综述任务不做建模/推导）。

### 3.3 单一会话与事件层（顺带修掉已知缺陷）

| 问题（已实测） | 合并中的处置 |
|---|---|
| SSE 回放后仍从共享队列抢占，多标签页争抢事件 | 每订阅者独立游标，只读 `event_log`（`seq > cursor`） |
| `connected`/终止帧用 `current_seq+1` 且不写日志 → 与真实事件**同号** | 所有帧统一从 `event_log` 分配 `seq`；心跳不占业务序号 |
| 前端 `lastEventId` 是**跨会话全局量** → 新会话前 N 条事件被丢弃 | 游标按 `(sessionId, threadId)` 维护；`openSession` 时重置 |
| `resumeConversation()` 只设 threadId，不设模式/项目/问题 → 新事件显示在上一个项目工作台 | 与 `switchToConversation` 合并为单一 `openSession(sessionId, intent: view\|continue)` |
| 删除会话清空共享向量库 | **已修**（按 `project_id` 所有权判定 + `kept_shared` 明示） |
| `durable=false`（内存 checkpointer）未在 UI 警示 | 会话记录加 `durable` 字段，前端显示"不可跨进程恢复" |

### 3.4 智能体协议（一次定义，六处复用）

新增 `src/agents/protocol.py`（**唯一**的智能体骨架，不新增平行框架）：

```python
@dataclass(frozen=True)
class AgentContract:
    name: str                 # literature | modeling | derivation | writing | figure | review
    prompt_version: str       # 提示词版本 (进审计日志, 可回溯"第几版提示产生了这条结论")
    tools: tuple[str, ...]    # 允许的工具名 (能力表由此生成)
    output_schema: type       # 结构化产物 (Pydantic)
    may_change_conclusion: bool = False   # 只有 False 才允许接入判定层

def run_agent(contract, inputs, llm, *, tool_runtime, budget) -> AgentRun:
    """统一执行: 组装提示 → 受限工具循环 → 结构化解析 → 审计留痕。

    复用已经在用的 `_run_agent_loop` (literature_reviewer.py) 的闭环实现,
    把"工具错误回灌让模型自我修正"的行为固定下来, 不再每处各写一遍。
    """
```

**审计**：每次运行写入 `agent_run` 事件：`agent`、`prompt_version`、`model`、
`tool_calls`、`tokens`、`cost`、`input_versions`、`output_hash`。
这使"某条结论是哪个智能体在哪一版提示下产出的"可回答。

---

## 4. 前端合并方案

### 4.1 信息架构：从"模式分叉"到"团队视角"

现在前端按"模式"（综述/理论）分叉。合并后改为**按团队与阶段**组织：

| 区域 | 现在 | 合并后 |
|---|---|---|
| 顶部 | 模式选择器 + 模式徽标 | **任务选择**（推导型/综述型/混合）+ **团队状态条**（6 个智能体的当前状态：待命/工作中/受阻/完成） |
| 主区 | 工作台（理论）/ 预览（综述）分叉 | **研究时间线**（单一）：检索来源与覆盖 → 当前模型/命题 → 关键缺口 → 下一动作及理由 |
| 侧栏 | 日志 + 产物 | **证据卡**（区分「找到标题 / 获得全文 / 已核对支持前提」三态）+ **结论与论文**（已证/被否定/条件性/未决 + 证书跳转） |
| 论文预览 | 只有文本 | 草稿状态 + **所依赖的研究快照版本** + 未通过的门槛逐条列出 |
| 会话列表 | 会话 + 恢复 | 会话 + 模式 + 项目 + 状态 + **恢复能力标记**（可跨进程恢复 / 仅内存），「查看」与「继续」分开 |
| 人工确认 | 5 个分散确认点 | 3 个统一确认点（方向 / 结论 / 投稿），带"为什么需要你确认"的说明与候选清单 |

### 4.2 新增/改造的 API（服务端，28 端点基础上调整）

```
新增
  GET  /api/team/{session_id}/status        # 六个智能体状态 + 当前阶段 + 下一步理由
  GET  /api/research/{project_id}/timeline  # 研究时间线 (来源/模型/命题/缺口/动作)
  GET  /api/research/{project_id}/capabilities  # 能力表 (前端据此隐藏不可执行动作)
  GET  /api/artifacts/{name}/gates          # 文档所依赖快照版本 + 门槛逐条结果
改造
  POST /api/sessions/{id}/events            # 独立游标; 帧统一从 event_log 分配 seq
  POST /api/sessions/{id}/open              # 统一 openSession(view|continue), 取代 resume/switch
保留兼容 (一个版本)
  GET  /api/sessions/{thread_id}/state, /api/research/*/state   # 旧前端仍可用
```

### 4.3 前端文件改动

| 文件 | 改动 |
|---|---|
| `src/web/src/app.ts`（主控制） | 拆出 session controller：单一 `openSession`、会话级游标、取消订阅；页面只展示已绑定状态 |
| `src/web/src/views/research-workbench.ts` | 改为团队视角：智能体状态条 + 时间线 + 结论列表 |
| `src/web/src/views/conversation.ts` | 会话列表区分"查看/继续" + 恢复能力标记 |
| `src/web/src/contracts.ts` | 新增 `TeamStatus`/`TimelineEntry`/`CapabilityTable`/`GateReport` 类型 |
| `src/web/src/current-research.ts` | 按 `(sessionId, threadId)` 维护游标与状态投影 |
| 新增 `views/team-status.ts`、`views/evidence-card.ts`、`views/paper-gate.ts` | 团队状态、证据三态、论文门槛 |
| `src/web/tests/`（13 个单测 + 5 个 e2e） | 补：双标签页同序事件、断线重连不丢中断、跨项目切换不串工作台、刷新后策略一致 |

---

## 5. 分阶段实施（每阶段可独立验收）

| 阶段 | 内容 | 交付物 | 验收（必过） | 人工参与 |
|---|---|---|---|---|
| **P1** 地基（约 3–4 日） | `AgentContract` + `run_agent` 协议；`UnifiedState`（信封+载荷）；**适配器**把 `UnifiedState` 投影回两个旧 state | 新模块 + 适配器；旧测试全绿 | 既有 **874 项测试全过**（这是安全网）；新增协议不变量测试 | — |
| **P2** 团队成型（约 5–7 日） | 六个智能体按契约改造：①检索接工具闭环（复用现有）②建模从规则升级为智能体+规则校验 ③推导智能体只提议、核验仍留判定层 ④撰写接真智能体（替换 `theory_writer` 模板，保留模板作为降级）⑤配图接入主链 ⑥审阅合并两套 | 六个智能体 + 各自契约测试 | 每个智能体的**禁止项**有负向测试（如撰写智能体不得引入快照外事实）；`insufficient` 证据仍不可引用 | 领域专家核对②③的关键推理 |
| **P3** 单一图与事件层（约 4–6 日） | 统一 20+5 节点为一张图；三个 `human_confirm`；SSE 独立游标；`openSession`；`durable` 标记 | 单一图 + 新会话层 | 双标签、断网、刷新、跨项目切换、终止状态；删 A 后 B 可用（已有回归） | 维护者定旧会话记录迁移策略 |
| **P4** 前端团队视角（约 4–6 日） | §4.1–4.3 全部改动 | 新工作台 | 窄屏/长公式/引文/断线/双标签/历史切换；对比度与键盘焦点 | 作者信息、投稿要求 |
| **P5** 清理与验收（约 3–5 日） | 删除旧 state/旧图/死代码；跑三类评估用例；真实盲测 | 清理后的单一系统 | 三类样例（组合设计 / 另一类数学题 / 需经验验证题）；跨领域禁词；删证据自动降级；PDF 仍可编译 | **独立审阅签字**（封存期望笔记） |

**规模**：约 19–28 个有效开发日（不含专家排期）。

### 5.1 明确的"不做"

- **不**让智能体获得改变 `status`/交付级别的能力（判定层零 LLM）。
- **不**为合并引入第二套编排框架（继续用 LangGraph）。
- **不**删除 `theory_writer` 模板渲染器 —— 它是 LLM 不可用时的确定性降级路径。
- **不**在 P1 之前改动任何现有行为（先建地基与适配器，保证随时可回退）。

### 5.2 风险与缓解

| 风险 | 缓解 |
|---|---|
| 130 字段"上帝状态"导致不可维护 | 信封+载荷分层；每片有独立 schema 与测试 |
| 合并期间功能回退 | 适配器双向投影 + 874 项测试作为安全网 + 每阶段独立提交 |
| 智能体互相背书（LLM 给 LLM 作证） | 契约中 `may_change_conclusion=False`；判定层无 LLM；审阅智能体**只可降级** |
| 撰写智能体引入快照外事实 | 文稿块级回链（命题 ID + 来源键）；门槛检查"未回链段落"直接失败 |
| 两套评估用例期望值被无意改变 | `evals/cases/*/expected_notes.md` 只作人审输入，**绝不进运行时**；P5 封存 |

---

## 6. 合并完成的判定标准（Definition of Done）

1. 只有**一套**状态、**一张**图、**一个**会话入口；旧 state 与旧图已删除。
2. 六个智能体各有契约、能力边界与负向测试；每个智能体的运行留痕可查（agent/prompt 版本/tokens/cost）。
3. 判定层仍无 LLM 调用（可用静态检查证明：`src/verification/**` 与 `acceptance.py` 无 `llm.invoke`）。
4. 三类评估样例通过；跨领域样例不出现组合设计专用术语（当前 P0-3 缺陷的回归）。
5. 前端在双标签、断网重连、刷新、跨项目切换、窄屏下行为正确。
6. 论文仍可编译出出版级 PDF；结论等级不低于合并前。
7. 全部未完成项如实记录在「未完成工作清单」，不夸大。

---

## 7. 与既有文档的关系

- 本计划**取代**「合并两套架构」部分的零散记录，成为后续主线。
- `AI for Research(AIR)计划书.md` 的 P0/P1 清单仍然有效；其中
  **P0-3（通用论文入口输出组合设计专用内容）** 在 P2 的撰写智能体中一并解决。
- 每一项实现进展追加到 `AI for Research(AIR)计划推进情况.md`，格式沿用既有章节。
