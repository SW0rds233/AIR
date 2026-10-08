# 盲测运行手册：射频指纹可分性（`rf-fingerprint`）

- **对应用例**: `evals/cases/rf-fingerprint/case.md`
- **人工审表**: `evals/rubric.md`
- **固定输入**（计划书 §5）："结合我选定的资料库，分析信道变化对射频指纹可分性的影响，
  并给出理论结论与仿真建议。"
- **封存材料**: `evals/cases/rf-fingerprint/expected_notes.md`（评审前不得进入任何运行时输入）
- **目标**: 让每次盲测都可复核、可回归 —— 归档包必须能回答"用了哪一版资料、检索覆盖到哪、
  模型为什么这样选、花了多少、为什么停"。

---

## 0. 运行前自检（逐项打勾后才可启动）

- [ ] **封存答案未出现在输入**：`expected_notes.md` 仍在封存状态；输入请求、问题描述、
      资料库文件、`domain_terms.md`、`research_spec.json` 中都没有它的解析结果、反例构造
      或结论方向提示。启动前对 `evals/cases/rf-fingerprint/` 做一次全文检索确认。
- [ ] **资料均已入库并有版本**：每份资料都有来源版本与 hash，`source_set_id` 已绑定，
      文档数与索引状态可查；仅有摘要或全文缺失的资料单独标记，不得冒充已读全文。
- [ ] **预算上限已设置**：`--max-actions`、`--max-tool-calls` 与费用上限已显式给出；
      触发上限时的停止原因与交付降级规则（改为条件性交付）已知。
- [ ] **运行身份已确定**：`project_id` / `problem_id` / `run_id` / `branch_id` 按 §2 取值，
      同一问题必须复用同一身份。
- [ ] **归档目录已就绪**：`evals/cases/rf-fingerprint/runs/<YYYY-MM-DD>/` 已建，
      大文件留在 `outputs/research/...`（见 §5）。

任一项不满足即**不得启动**；先在 `failures.md` 登记，补齐后再跑。

---

## 1. 输入请求

两种资料场景共用**同一句**输入 —— 差异只在资料授权与绑定方式，不在问题描述：

```text
结合我选定的资料库，分析信道变化对射频指纹可分性的影响，并给出理论结论与仿真建议。
```

输入请求只允许是上面这一句（或经确认的等价复述）。**禁止**在输入请求、`--topic`
或问题描述里追加任何预判方向、已知结果、反例提示，或 `expected_notes.md` 的内容。
输入模板本身也不得携带评分线索：不写"期望系统找到/否定什么"。

---

## 2. 命名约定（同一问题必须同一身份）

| 字段 | 取值规则 | 示例 | 权威来源 |
|---|---|---|---|
| `project_id` | 用例级固定前缀，一个验收项目一个 id；盲测期间不得改名 | `rf-eval` | `manifest.json` |
| `problem_id` | 一个研究问题一个 id；**改题 = 新 id**（或契约 `version+1`），不得复用旧 id 掩盖改题 | `p1` | `research_spec.json` / `manifest.json` |
| `run_id` | 一次运行一个 id，由入口生成（CLI/Web 同名规则）；形如 `<problem_id>_<YYYYMMDD_HHMMSS>` 或 `<problem_id>_<8位十六进制>`，以 `manifest.json` 实际值为准 | `p1_20261001_140302` | `manifest.json`（与动作账本、产物目录同名） |
| `branch_id` | 问题内的研究路线分支，由循环按所选路线写入；无路线时为 `no-route` | `route-2` | 动作账本 / 快照 |

规则：

1. **同一问题必须同一身份**：`project_id` / `problem_id` 一经确认，复述问题、补资料、
   重跑都沿用同一组值；只有"确实是另一个研究问题"才新建 `problem_id`。
2. **续跑沿用同一 `run_id`**：账本、`outputs/<run_id>/` 产物目录、`manifest.json` 三处
   必须是同一个值；续跑另起 `run_id` 会使三者对不上，直接记为失败并在 `failures.md` 登记。
3. `run_id` 缺失或冲突时，以 `manifest.json` 为准，并在归档摘要里注明差异与处理。

---

## 3. 两条运行路径（CLI 与 Web 等价）

两条路径的产物必须落在同一身份与同一交付包目录下；入口不同不改变归档清单（§4）。

### 3.1 场景 A：用户提供资料库（绑定 `source_set_id`）

前提：资料 A / 资料 B（条件不同）已入库并有版本；`source_set_id` 指向该资料集合。

CLI（命令写法与 `case.md` §1 一致）：

```powershell
.venv\Scripts\python.exe -m src.main --mode theory --project-id rf-eval --problem-id p1 `
  --topic "信道变化对射频指纹可分性的影响" --max-actions 40 --max-tool-calls 60
```

启动后必须核对交付包里的 `research_spec.json`：`source_set_id`、文档数、每份来源的
版本/hash。若 CLI 尚未暴露显式 `--source-set-id`，以规格文件里的绑定记录为准（显式
策略字段随 I-3 的 `SourcePolicy` 落地），并在归档摘要里写明本次绑定是从哪条入口设置的。

Web：

```powershell
.venv\Scripts\python.exe -m uvicorn src.server:app --port 8000
# 打开 http://127.0.0.1:8000/ → 模式选"理论研究" → 绑定资料库（source_set_id）→ 提交 §1 输入
```

验收要点：报告引用的证据**只能来自绑定的资料集合**；只授权资料 B 的命题缺口不得用
资料 A 的证据满足。

### 3.2 场景 B：只给研究方向 + 授权自主检索

前提：启动时只给方向，不绑定资料库；授权范围（检索引擎、时间窗、语言）与"未绑定
资料库"这一事实必须写进契约与报告。

CLI：

```powershell
.venv\Scripts\python.exe -m src.main --mode theory --project-id rf-eval --problem-id p1 `
  --topic "信道变化对射频指纹可分性的影响" --max-actions 40 --max-tool-calls 60
```

（CLI 不绑定资料库即进入自主检索场景；策略字段落地后以 `--source-policy autonomous`
一类的显式参数为准。）

Web：

```powershell
.venv\Scripts\python.exe -m uvicorn src.server:app --port 8000
# 打开 http://127.0.0.1:8000/ → 模式选"理论研究" → **不选资料库** → 提交 §1 输入
```

必须留痕：检索覆盖记录（查询式、引擎、时间窗、命中、入库、全文可得性、未覆盖范围、
失败原因）。无命中不等于不存在，只能作有界表述。

双语检索核对：运行时词表是 `src/rag/domain_terms/rf-fingerprint.md`。
`RetrievalCoverage.queries` 应包含中文专名、独立的 `RF fingerprinting` 与
`radio frequency fingerprinting` 查询，以及带领域上下文的 `RFFI` 查询；
不得把两个英文同义名称堆成一个必须同时匹配的查询。检查实际入选文献的主题相关性、
出版年份、被引数及可读深度；较新或高被引不代表结论已被证实。

依赖标注：本场景的 `source_policy` 与 `RetrievalCoverage` 字段随 I-3 落实。I-1 期间该
路径只在契约与归档口径上成立，**不得**据此宣称已通过盲测。

---

## 4. 归档清单（逐项，缺一项即材料不全）

| # | 归档项 | 来源文件 / 位置 | 检查 |
|---|---|---|---|
| 1 | **输入请求** | `research_spec.json` 的原始请求 + 运行摘要 | 与 §1 逐字一致，无附加预判 |
| 2 | **资料集合与其版本/hash** | `source_set_id`、文档数、每份来源版本与 hash | 绑定的资料与报告引用一致，无越权引用 |
| 3 | **检索覆盖记录** | `RetrievalCoverage`（queries / engines / date range / hits / ingested / fulltext_available / abstract_only / uncovered / failures / scope_note） | 无命中、仅摘要、失败原因如实记录（I-3） |
| 4 | **研究日志** | `decisions.jsonl` + 存储层 `log_anomalies` | 动作、工具调用与异常可回溯 |
| 5 | **模型与舍弃理由** | `models.json` + 决策日志 | ≥2 个候选机制，给出舍弃理由（问题忠实度 / 来源条件 / 可验证性 / 成本） |
| 6 | **论证链** | `manuscript.md` "证明与验证" 段 + `obligations.json` + `verification/` | 关键步骤可逐步复核；局部验证不得写成整体证明 |
| 7 | **交付报告** | `manuscript.md` / `paper.tex` / `novelty_review.md` / `unresolved.md` | 交付等级与实际完成度一致 |
| 8 | **`manifest.json`** | `outputs/research/<project_id>/<snapshot_id>/manifest.json` | `project_id`/`problem_id`/`run_id`/`branch_id`、`tool_versions`、`usage`、`delivery_level` 齐全 |
| 9 | **费用与停止原因** | `manifest.json` 的 `usage` + `delivery_gate.md` | 实际花费与预估分开；停止原因（预算 / 动作上限 / 阻塞）显式写出 |
| 10 | **封存答案的存放位置** | `evals/cases/rf-fingerprint/expected_notes.md`（**评审结束前不得进入运行时输入**） | 归档只写指针与"已封存"状态，不复制其内容进摘要或输入 |

---

## 5. 归档目录约定

```
evals/cases/rf-fingerprint/runs/<YYYY-MM-DD>/
└─ <run_id>.md              # 摘要 + 指针；不放产物本体
```

摘要必须含：`run_id`、资料集合 hash、工具版本、`metrics`、`log_anomalies`、交付等级、
停止原因、以及指向交付包的相对路径。

大文件一律留在 `outputs/research/<project_id>/<snapshot_id>/`（交付包：正文、证据、
验证记录、实验建议、`manifest.json`）。`runs/` 只写摘录与指针。

`runs/` 下的摘要与失败台账**只追加**，不覆盖历史；同一天多次运行按 `<run_id>` 分文件。

---

## 6. 与 `rubric.md` 的对应

| 环节 | 依据 |
|---|---|
| 材料是否齐 | `rubric.md` §0 送审材料 |
| 维度判定 | `rubric.md` §2.1–§2.8（逐项写理由与文件位置） |
| 失败登记 | `evals/cases/rf-fingerprint/failures.md`（`rubric.md` §3 表结构） |
| 术语、量纲、机制关系 | `evals/cases/rf-fingerprint/domain_terms.md`（由领域专家填写） |
| 封存答案 | `evals/cases/rf-fingerprint/expected_notes.md`（评审后对照） |
