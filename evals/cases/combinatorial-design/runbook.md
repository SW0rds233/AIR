# 盲测运行手册：组合设计存在性（`combinatorial-design`）

- **对应用例**: `evals/cases/combinatorial-design/case.md`（211 节点无重复配对计划）
- **人工审表**: `evals/rubric.md`
- **封存答案**: `evals/cases/combinatorial-design/expected_notes.md`
  （**评审前不得进入任何运行时输入**，也不得出现在 `--request` / `--topic` / 附件里）
- **本用例的性质**: 这是一道**有确定数学答案**的问题。它的价值在于同时检验
  ①系统能否不靠穷举给出严格否定结论；②系统能否区分"计数必要条件满足"与"存在"；
  ③同一套逻辑换参数后是否仍然成立（泛化，而不是背答案）。
- **目标**: 每次运行都可复核、可回归 —— 归档必须能回答"系统凭什么下这个结论、
  花了多少钱、失败在哪一步"。
- **领域词表**: 本题是纯数学题，`src/rag/relevance_filter.py` 的领域过滤对本题
  **不生效**（`evals/cases/combinatorial-design/` 下没有 `retrieval_terms.md`）；
  判不出领域时系统不做任何领域假设。若本题走自主检索，检索覆盖记录仍必须如实
  记录命中与未覆盖范围（"库内查不到"不等于"领域内不存在"）。

---

## 0. 运行前自检（逐项打勾后才可启动）

- [ ] **封存答案未出现在输入**：`expected_notes.md` 仍在封存状态；输入请求、问题描述、
      附件、`research_spec.json`、资料库文件里都没有它的结论、定理名或参数分解。
      启动前对 `evals/cases/combinatorial-design/` 做一次全文检索确认。
- [ ] **输入与 `case.md` 逐字一致**（或其等价复述）：**禁止**追加任何预判方向、
      已知结论（如"BRC"/"射影平面"/"不存在"）或提示。
- [ ] **离线基线已复跑**（§1）：`THEORY_LLM=0`、`THEORY_PROPOSER=0` 下交付等级为
      `论文草稿`、`gate_passed=True`、`usage` 全零。基线不成立时**不得**进入 §2。
- [ ] **预算上限已显式给出**（§2.1 的 token/费用区间与硬上限），并已获授权。
- [ ] **运行身份已确定**（§3）：`project_id` / `problem_id` / `run_id` / `branch_id`。
- [ ] **前端产物与源码同版本**（§0.1）：后端只提供 `src/web/dist/`，不编译 —— 产物过期
      或残留旧 bundle 时，跑的是**上一版前端**配**本版后端**，会出现已修复的 404 复现
      （CD-001/CD-002 的现场之一）。Web 入口跑之前必须过这一项。
- [ ] **没有遗留服务占用端口**（§0.2）：上一轮调试/验证起的服务必须真的退出；换端口而不查
      会让"这一版代码"与"旧进程"混在一起，跑出来的结果无法归因。
- [ ] **归档目录已就绪**：`evals/cases/combinatorial-design/runs/<YYYY-MM-DD>/` 已建；
      大文件留在 `outputs/research/<project_id>/<snapshot_id>/`（见 §5）。

任一项不满足即**不得启动**；先在 `failures.md` 登记，补齐后再跑。

### 0.1 前端产物与源码同版本（Web 入口的硬前置）

后端**不编译前端**：`src/server.py` 只从 `src/web/dist/` 提供页面与资源。因此
"源码改了但没重建" 会让浏览器里跑的是旧逻辑，表现为**已修复的缺陷在现场复现**。
2026-10-03 实测过两次：`/favicon.svg` 与草稿身份的工作台 404 都是"旧 bundle + 新后端"
的组合（见 `failures.md` CD-001 / CD-002）。

自查顺序：先跑 `scripts/frontend_needs_build.ps1`（输出 `1` = 需要重建，`0` = 最新），
需要时执行 `npm run build`，再核对 `dist/assets/` 只剩一个当前 bundle。
**重建后不必重启服务**（`/` 与 `/assets/*` 每次读盘），但**浏览器必须刷新** ——
否则缓存的旧 bundle 会让你以为缺陷还在。

```powershell
# 1 表示需要重建, 0 表示产物是最新的
.\scripts\frontend_needs_build.ps1

# 需要重建时:
cd src\web; npm run build; cd ..\..
# 并确认 assets/ 下只剩一个当前 bundle (残留旧 js 会让人误判跑的是哪一版)
Get-ChildItem src\web\dist\assets -Name
```

判定与处置：

| 检查 | 期望 | 不满足时的处置 |
|---|---|---|
| `frontend_needs_build.ps1` | 输出 `0` | 先 `npm run build` 再继续；构建失败即**不得**启动 Web 入口 |
| `dist/assets/` 内容 | 只有 **1 个** `.js` 与 **1 个** `.css` | 删除残留旧 bundle 并重建（旧文件虽可能未被引用，但会让"跑的是哪一版"无法判断） |
| `dist/index.html` 的引用 | 指向上面那个 `.js` / `.css` | 重建；引用与文件不一致说明产物被手工改过 |
| `GET /favicon.svg`、`/favicon.ico` | 都是 200（`image/svg+xml`） | 见 `failures.md` CD-001：路由或产物缺失，先补齐再跑 |

**改完之后不必重启服务**（`/` 与 `/assets/*` 每次都会重新读盘），但**浏览器要刷新**，
因为旧 bundle 会被缓存。CLI 入口（§2.2 的 `--mode theory`）不提供服务端页面，
不受这一项影响 —— 它只走 Python 逻辑。

### 0.2 没有遗留服务占用端口（换端口不等于换了进程）

**为什么单列一项**：调试/验证时反复起服务、每次换个端口，是很容易"以为已经收尾"的做法。
2026-10-03 实测：8 个诊断服务（8017–8024）**全部仍在监听** —— 终止包装进程并没有终止
被它启动的 uvicorn 子进程，而它们 `log_level=warning`，既不报错也不刷日志。
后果是"这一版代码"与"上一个进程"混在一起：请求可能落到旧代码上，
跑出来的结果**无法归因**，排查时会把已修好的缺陷当成仍然存在。

```powershell
# 1) 有没有遗留服务 (本用例约定端口 8000 / 8017-8024)
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 8000,8017..8024 } |
  Select-Object LocalAddress, LocalPort, OwningProcess

# 2) 按 PID 确认它确实是我们的服务, 再终止 (不要用 Stop-Process -Name python 一把杀)
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 8000,8017..8024 } |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Get-Process -Id $_ | Select-Object Id, ProcessName, StartTime }
```

判定与处置：

| 检查 | 期望 | 不满足时的处置 |
|---|---|---|
| 本用例端口上的监听 | **0 个** | 按 PID 逐个确认后终止；**换端口重跑不算解决**（旧进程仍在写 `data/`、`outputs/`） |
| `Get-Process python` 数量 | 与本次要起的服务数一致 | 有不明进程时先看 `StartTime` 与命令行，再决定是否终止 |
| 终止方式 | 按**监听端口反查 PID** 精确终止 | 禁止 `Stop-Process -Name python -Force`（会误伤用户自己的进程/其它工具） |

**收尾时也要做这一项**：服务必须真的退出，否则下一轮"运行前自检"会撞上同一批遗留进程，
并且它们可能已经往 `data/research/`、`data/uploads/` 里写了本轮的库与登记。
清理时要**只删能确认是本次调试产生的**项目（按 `project_id` 精确匹配），
保留用户自己的项目与附件登记。

---

## 1. 离线冒烟（不花钱，必须先过）

### 1.1 端到端（CLI，与 `case.md` 同一输入）

Windows（PowerShell）。`THEORY_LLM=0` 强制离线：只走规则层与受限工具，不调用任何模型。

```powershell
$env:THEORY_LLM = "0"; $env:THEORY_PROPOSER = "0"; $env:PYTHONIOENCODING = "utf-8"
$case = Get-Content "evals\cases\combinatorial-design\case.md" -Raw -Encoding utf8
.venv\Scripts\python.exe -m src.main --mode theory --request $case `
  --project-id cdeval --problem-id p1 --source-policy autonomous `
  --max-actions 20 --max-tool-calls 20
```

Web 等价入口（产物落在同一身份与同一交付包目录下）：

```powershell
.venv\Scripts\python.exe -m uvicorn src.server:app --port 8000
# 打开 http://127.0.0.1:8000/ → 模式选"理论研究" → 资料范围选"自主检索"
# → 把 case.md 的正文整段贴入请求 → 提交
```

### 1.2 离线验收判据（逐项核对，缺一项即基线不成立）

| # | 判据 | 期望 |
|---|---|---|
| 1 | 结论 | 明确判断该排班**不存在**；不得出现"计数吻合所以可以生成" |
| 2 | 依据 | 正文出现 **Bruck–Ryser–Chowla**，并说明 $b=v\Rightarrow$ 对称设计 $ \Rightarrow$ 14 阶射影平面 |
| 3 | 判定链 | `manuscript.md` 的"证明与验证"给出**逐条判定链**：定理名 + 定理陈述 + 输入数值 + 本次结论；每条能反查到 `verification/` 的证书 |
| 4 | 未穷举 | 不得把穷举/数值检查当成证明；证书只由计数关系与经典必要性定理构成 |
| 5 | 门槛与等级 | `delivery_gate.md` 显示两道门槛**通过**，`delivery_level = 论文草稿` |
| 6 | 未判定项 | 未实现/不适用的必要条件（例如非对称设计的 BRC 不适用）在正文与证书里如实列出 |
| 7 | 费用 | `manifest.json` 的 `usage` 为 `cost_usd=0.0 / llm_calls=0 / tokens=0` |
| 8 | 可复现性 | `manifest.json` 的 `input_snapshot` 记录了本次请求、资料授权策略与预算；`reproducible` 字段与实际一致 |
| 9 | 日志契约 | `manifest.json` / `log_anomalies` 无"未登记事件类型""缺必需键"异常 |

### 1.3 泛化回归（同一次跑批必做，防止把结论写死在某组参数上）

当前仓库没有旧版 `tests/test_design_feasibility.py`。先运行现有组件级回归，
再按下表逐组检查真实输出；此步骤尚无端到端自动化替代：

```powershell
$env:THEORY_LLM="0"; $env:THEORY_PROPOSER="0"
.venv\Scripts\python.exe -m pytest tests/test_latest_run.py tests/test_current_run_integrity.py -q
```

| 参数 | 期望结论 | 理由 |
|---|---|---|
| 2-(211,15,1)（本题） | **不存在** | $n=k-1=14\equiv 2\pmod 4$，14 不是两平方和 → BRC 排除 |
| 2-(43,7,1) | **不存在** | $n=6\equiv 2\pmod 4$，6 不是两平方和 |
| 2-(7,3,1)（Fano） | **必要条件满足、存在性未定**；义务**不得**关闭 | BRC 不排除 $n=2$；系统**不得**输出"不存在"，也不得输出"存在" |
| 非计数题（如"分析信道变化对可分性的影响"） | 行为不变（澄清/未决报告） | 抽不出计数约束时不得编造参数 |

### 1.4 若离线基线失败

按 §6 登记到 `evals/cases/combinatorial-design/failures.md`，**不得**通过改测试期望、
放宽判据或润色论文来掩盖；离线基线不成立时，§2 的限额真实测试的结论无效。

---

## 2. 限额真实测试（唯一花钱的步骤）

### 2.1 先给预估区间与硬上限（**运行前**填写并获授权）

预估值必须**先写下来**再运行，事后与实际值对照 —— 否则无法判断预算机制是否有效。

| 项 | 预估区间 | 硬上限 | 依据 |
|---|---|---|---|
| 模型调用上限 `THEORY_PROPOSER_MAX_CALLS` | — | 20 | 默认值；提议器每次只挑一个动作 |
| 研究动作 `--max-actions` | — | 40 | 离线实测用 3 个动作即闭环，真实运行主要被提议/检索放大 |
| 工具调用 `--max-tool-calls` | — | 60 | 同上 |
| 输入 tokens | — | — | 由提示词规模 × 调用次数估计 |
| 输出 tokens | — | — | 由候选/提议/推导步骤的期望长度估计 |
| **费用上限（USD）** | — | — | 按 `--model` 单价 × token 上限换算，**必须**显式填写 |

先在离线跑一遍记录**动作数/tool 调用数**（当前为 3 / 0），再用它乘上"每个动作的
期望模型调用次数×提示词长度"给出上界；不要用"应该不贵"代替上界。

**2026-10-03 实测参照**（本题、`deepseek-v4-flash`、提议器开启、3 个动作闭环）：

| 项 | 实测值 |
|---|---|
| 模型调用 | 3 次（全部来自提议器 `stage=proposal`；定理判定与义务关闭走规则层，不调用模型） |
| tokens | 输入 ≈ 2.7k，输出 ≈ 5.4k（合计 ≈ 8.1k） |
| 墙钟 | ≈ 27–32 s |
| 费用 | 闲时 ≈ **\$0.0036**／峰时 ≈ \$0.0073（按官方 flash 单价换算，见 §2.3 的费用口径） |

同题重跑的第二次（`cdreal2`）为 3 次调用 / 9.2k tokens / 31.5 s，费用同量级。
**注意**：本题是"规则层能闭环"的短运行；换成需要检索、建模、逐条推导的领域题
（如 rf-fingerprint），模型调用次数会随动作数与 evidence 判定显著放大，§2.1 的预估
必须按那条路径重算，不能套用本表。

### 2.2 运行命令（保留离线开关一致，只开真实模型）

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:THEORY_LLM = "1"            # 允许协调者/理论研究者使用真实模型
$env:THEORY_PROPOSER = "1"       # 允许结构化动作提议
$env:THEORY_PROPOSER_MAX_CALLS = "20"   # §2.1 的硬上限
$case = Get-Content "evals\cases\combinatorial-design\case.md" -Raw -Encoding utf8
.venv\Scripts\python.exe -m src.main --mode theory --request $case `
  --project-id cdeval --problem-id p1 --source-policy autonomous `
  --max-actions 40 --max-tool-calls 60 --resume
```

- **同一问题必须复用同一身份**：`--project-id cdeval --problem-id p1`；改题才换 `problem_id`。
- 续跑沿用同一 `run_id`（由入口生成并写入 `manifest.json`）。
- 触顶即停：预算耗尽时引擎输出部分结果并在 `delivery_gate.md` 写明停止原因，
  **不得**把部分结果当作已完成研究。

### 2.3 运行后核对（必须逐项记录）

| 项 | 来源 | 期望 |
|---|---|---|
| 实际 token / 费用 | `manifest.json` → `usage`（`tokens` / `cost_usd` / `llm_calls`） | 与 §2.1 的预估区间对照；**超出预估区间要在归档摘要里写明原因** |
| 费用口径 | 逐次调用明细（`runtime.usage_events`）+ 服务商**官方定价页** | 见下：`cost_usd` 是系统估算，必须按官方单价复核 |
| 预算上限 | `manifest.json` → `budget_limits` | 与命令行一致，未被静默放宽 |
| 停止原因 | `delivery_gate.md` / `manifest.json` | 正常结束或明确写出触顶原因 |
| 结论是否仍成立 | `claims.json` + `manuscript.md` | 逐项对照 §1.2 判据 1–6 |

**费用口径（重要）**：`manifest.usage.cost_usd` 是**系统按内置价目表估算**的值。
价目表未收录的模型会退回兜底单价（`0.50/1.50` 每百万 token），此时估算值可能明显
偏离实际。归档时必须：

1. 从 `runtime.usage_events` 取 `(model, input_tokens, output_tokens)` 逐次明细；
2. 用服务商官方定价页的单价重算（DeepSeek 峰时为 UTC 周一至周五 01:00–04:00 与
   06:00–10:00，其余时段半价；缓存命中价另计，`usage` 只给总量时按"全部未命中"作上界）；
3. 两个数字都写进归档摘要，并注明差价原因（价目表版本 / 兜底单价 / 峰谷时段）。
| 真实模型有没有"改题" | `claims.json` / `research_spec.json` | 命题陈述必须仍是"该参数的设计是否存在"，不得被换成一个更好证的弱命题 |
| 提议与拒绝理由 | `decisions.jsonl` / 事件流 | 能被逐步解释"为什么做这个动作" |

**关键判定**：真实模型路径下结论**只能更强或更诚实**，不能更弱 ——
若出现"因为模型给的候选好看就把命题换成更弱的版本"，按 §6 记为失败并保留样例。

---

## 3. 命名约定（同一问题必须同一身份）

| 字段 | 取值规则 | 本用例取值 | 权威来源 |
|---|---|---|---|
| `project_id` | 用例级固定前缀；盲测期间不得改名 | `cdeval` | `manifest.json` |
| `problem_id` | 一个研究问题一个 id；**改题 = 新 id** | `p1` | `research_spec.json` / `manifest.json` |
| `run_id` | 一次运行一个 id，由入口生成 | 以 `manifest.json` 实际值为准 | `manifest.json`（与动作账本、产物目录同名） |
| `branch_id` | 问题内的研究路线分支，无路线时为 `no-route` | 以动作账本为准 | 动作账本 / 快照 |

1. **同一问题必须同一身份**：补资料、重跑、复述问题都沿用同一组值。
2. **续跑沿用同一 `run_id`**：账本、`outputs/research/<project_id>/<snapshot_id>/`、
   `manifest.json` 三处必须是同一个值；对不上直接记为失败。
3. 身份缺失或冲突时以 `manifest.json` 为准，并在归档摘要里注明差异与处理。

---

## 4. 归档清单（逐项，缺一项即材料不全）

| # | 归档项 | 来源文件 / 位置 | 检查 |
|---|---|---|---|
| 1 | **输入请求** | `research_spec.json` 的原始请求 + `manifest.json` 的 `input_snapshot` | 与 `case.md` 一致，无附加预判/答案提示 |
| 2 | **输入快照与可复现性** | `manifest.json` → `input_snapshot`（`request`/`source_policy`/`budget`/`reproducible`） | `reproducible=False` 的运行**不得**被当作可完整复现 |
| 3 | **研究日志** | `decisions.jsonl` + 事件流（`log_anomalies`） | 动作、工具调用、异常可回溯；无未登记事件类型 |
| 4 | **结论与状态** | `claims.json` | 每条结论有状态/支持方式/覆盖范围；`design_*` 参数与命题陈述一致 |
| 5 | **义务与证书** | `obligations.json` + `verification/` | 未关闭义务不得写成结论；证书含定理名、定理陈述、输入与结论 |
| 6 | **判定链** | `manuscript.md` 的"证明与验证"段 | 逐条可反查；未穷举；未判定项如实列出 |
| 7 | **交付报告** | `manuscript.md` / `paper.tex` / `research_report.md` / `unresolved.md` | 交付等级与实际完成度一致 |
| 8 | **`manifest.json`** | `outputs/research/<project_id>/<snapshot_id>/manifest.json` | `project_id`/`problem_id`/`run_id`/`branch_id`、`tool_versions`、`usage`、`budget_limits`、`delivery_level` 齐全 |
| 9 | **费用与停止原因** | `manifest.json` 的 `usage` + `delivery_gate.md` | 实际花费与 §2.1 预估**分开**列出；停止原因显式写出 |
| 10 | **封存答案的存放位置** | `evals/cases/combinatorial-design/expected_notes.md` | 归档只写指针与"已封存"状态，**不复制内容**进摘要或输入 |
| 11 | **判据映射结果** | 本文件 §6 的判据映射表 + `evals/rubric.md` §2 各维度 | 逐项写"通过/需修改/不通过 + 具体理由与文件位置" |

---

## 5. 归档目录约定

```
evals/cases/combinatorial-design/runs/<YYYY-MM-DD>/
└─ <run_id>.md              # 摘要 + 指针；不放产物本体
```

摘要必须含：`run_id`、输入请求（逐字）、`usage`（实际 vs 预估）、交付等级、停止原因、
§1.2 判据 1–9 的逐项结论、以及指向交付包的相对路径。

大文件一律留在 `outputs/research/<project_id>/<snapshot_id>/`。
`runs/` 下的摘要与失败台账**只追加**，不覆盖历史；同一天多次运行按 `<run_id>` 分文件。

---

## 6. 判据映射（对照计划书发布门槛 1–5）与失败记录

### 6.1 判据映射

| 计划书发布门槛 | 本用例怎么检验 | 判定依据 |
|---|---|---|
| 1. 三种入口可从 Web 发起同一流程；资料权限与附件身份在恢复/导出后不变 | Web 与 CLI 用同一身份跑同一输入，产物落在同一交付包目录；`manifest.json` 的 `input_snapshot` 与恢复后的规格一致 | `manifest.json`、`research_spec.json`、§3 身份表 |
| 2. 核心变量/假设/模型关系/推导/反例/引用由独立人员逐项审过 | 由组合设计方向独立人员核对：BRC 的余数类与两平方和判定、$v=k(k-1)+1\Rightarrow$ 射影平面、计数关系；并记录核对理由与位置 | `evals/rubric.md` §2.4；本文件 §1.2 判据 2–4 |
| 3. 至少一条路线能因证据或反例改变模型/命题；否则诚实给出未决原因 | §1.3 泛化回归：Fano 参数下必须**不**输出"不存在"且保持未决；2-(43,7,1) 必须输出"不存在" | 逐组运行的 `claims.json`；当前缺端到端自动化用例 |
| 4. 已知等价已有工作不称原创；缺全文不称无先例；求解器 `unknown` 不称已证；建议不称结果 | 结论只声明"存在性判定"，不宣称原创；未判定项（非对称设计的 BRC 不适用）如实列出；无"已知结果"检索时正文显示"尚未进行已有工作比较" | `manuscript.md`（已有工作/适用范围与局限段）+ `novelty_review.md` |
| 5. 第二个不同任务类型的题目暴露的失败被记录并回归 | 本题结论**不得**被推广到所有 $(v,k,\lambda)$；用第二个领域/任务类型的题目（见 `evals/cases/transfer/`）跑同一套流程并登记失败 | `evals/cases/transfer/` + `failures.md` |

**评分约定的两项（需在 `expected_notes.md` §5 一并裁决）**：

1. **"引用 BRC 即算合格"还是要求系统自行推导 BRC？** —— 当前实现是"具名定理 +
   机器重算的参数证书"（`support_kind=theorem_application`）：证书逐条记录所用定理、
   定理陈述、本次输入数值与结论，并**另行判定定理的适用前提**（λ=1、v=k(k-1)+1、
   r=k=b ⇒ 等价于 14 阶射影平面）。系统**不**自行证明 BRC 本身。
   2026-10-03 的真实运行已按此口径产出"不存在 + 严谨判定链"（见 §8 归档摘要）。
   若裁决要求自行推导 BRC，则本题按 `不通过` 记，并需要新增更强的符号/形式化能力。
2. **"必要条件满足但存在性未定"的未决报告是否算部分通过？** —— 当前实现按
   "可选结论之一"处理：Fano 参数下如实未决、义务保持未关闭、交付等级不升级；
   本题（211/15/1）不涉及该分支。

两项裁决都必须写进复核记录；裁决前本用例的结论只能作为"待裁决"归档。

### 6.2 失败记录

失败台账：`evals/cases/combinatorial-design/failures.md`（结构同 `evals/rubric.md` §3）。

| 编号 | 日期 | 维度 | 现象（含文件/行位置） | 期望 | 处理 | 状态 |
|---|---|---|---|---|---|---|
| CD-001 | | | | | | 待处理 |

处理状态取值：`待处理 / 已修复(测试名) / 判定为非缺陷(理由)`。
**不允许**把"判定为非缺陷"留空理由，历史记录**不得删除**（只允许追加处理说明）；
失败样例必须保留，不得用改期望或润色论文掩盖。

---

## 7. 与 `rubric.md` 的对应

| 环节 | 依据 |
|---|---|
| 材料是否齐 | `evals/rubric.md` §0 送审材料 |
| 维度判定 | `evals/rubric.md` §2.1–§2.8（逐项写理由与文件位置） |
| 失败登记 | `evals/cases/combinatorial-design/failures.md`（`rubric.md` §3 表结构） |
| 封存答案 | `evals/cases/combinatorial-design/expected_notes.md`（评审后对照） |
| 离线自动化回归 | 当前可运行 `tests/test_latest_run.py`、`tests/test_current_run_integrity.py`；旧版 `test_design_feasibility.py`、`test_delivery_level.py`、`test_theory_mode.py` 不在当前仓库，相关端到端覆盖需补齐 |

---

## 8. 归档摘要（2026-10-03 真实运行）

按 §5 约定归档；本次是**规则层闭环**的短运行，费用与动作数不能外推到其他题型。

| 项 | 值 |
|---|---|
| `run_id` / `project_id` / `problem_id` | 见 `outputs/research/cdreal2/snap-fdb539f5/manifest.json` |
| 输入请求 | 与 `case.md` 逐字一致（`input_snapshot.request` 可逐字比对） |
| 交付等级 / 门槛 | 论文草稿 / 通过 |
| 结论 | **不存在**；判定链 5 步（计数整除 → 声明值一致 → Fisher → 射影平面等价 → BRC）；证书 `sha256=9089eb5c…` |
| 模型调用 | 3 次，全部 `stage=proposal`（`deepseek-v4-flash`） |
| tokens | 输入 2,681 / 输出 6,541 / 合计 9,222 |
| 墙钟 | 31.5 s |
| 费用（系统估算 `usage.cost_usd`） | \$0.008653（当时价目表未收录该模型，按兜底单价 0.50/1.50 估算） |
| 费用（官方单价复核） | 闲时 \$0.0042 / 峰时 \$0.0085（DeepSeek flash 0.15-0.30 / 0.60-1.20，缓存命中价未计 ⇒ 上界） |
| 附件 / 可复现性 | 无附件；`input_snapshot.reproducible = true`（校验结论） |
| 结论可复现性 | 与离线运行**同一证书 sha256**（离线为 `22135c2b…` 且当时缺"射影平面等价"这一步；加入该步后两次真实运行均为 `9089eb5c…`），说明真实模型未改变判定结果 |
| 失败记录 | 本次无结论性失败；提议被拒 2 次（`on_failure` 里写了结论性断言）已按规则记入事件流，不构成失败样例 |
