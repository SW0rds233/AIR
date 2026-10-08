# 运行产出评估报告：proj-muwia7ognn2l

| 项 | 内容 |
| --- | --- |
| 运行 | `proj-muwia7ognn2l` / `snap-95179b4a`（2026-10-06 18:45） |
| 题面 | 668 条长度 667、两两 Hamming 距离 334 的二进制序列是否存在（附件 `problem3.md`） |
| 规模 | 103 次 LLM 调用、266 次工具调用、1,195,376 token、**$0.959** |
| 产出 | 12 页 PDF（220 KB）+ Markdown（30,915 字节）+ **7 命题** + 32 证据 + **10 验证记录** + 5 证明文件 + 5 模型 |
| 交付判定 | `gate/theory_gate/delivery_gate/publication_gate` 仍为 False，但 **`delivery_level: 条件性研究报告`**（首次脱离"研究备忘录"） |
| 关键突破 | **`compilation_status: ok`、`pdf: "manuscript.pdf"`**（首次 PDF 被正式接受为交付物） |
| 收尾 | 11 轮、20 个任务结果、`stop_reason: 交付形态所需的角色成果齐备`、`status: partial` |

---

## 0. 一句话结论

**这是迄今最接近成功的一轮。** 检索链稳定工作（10 个证据任务、命中 14–60 条、入库 1–9 条、可引用 6–14 条），推理层真的提出了 7 条命题 + 2 条反例候选，形式化闭环产出了 5 份证明文件与 10 条验证记录 —— 其中 **5 条 `verified`（通过）、1 条 `counterexample_found`（找到反例）**，是历轮中第一次出现"真正被核验过的结论"。

交付等级首次升到 **条件性研究报告**，PDF 也被正式接受。

**但它仍然被三件"窄"事挡住，而且没有一件是研究能力问题**：

| # | 阻断项 | 性质 |
| --- | --- | --- |
| 1 | `dot(u,v)` 不在 sympy 白名单里 → 4 条义务 blocked → 主命题 blocked | 工具能力缺口 |
| 2 | 4 条验证记录被判"过期"（正文引用 `clm-…@1`，命题已升到 v2） | 版本引用不一致 |
| 3 | 措辞守卫误判：把"**没有**排除…的严格证明"当成"自述已完成证明" | 假阳性 |

---

## 1. 本轮的实质进展

### 1.1 命题层：第一次出现 supported / refuted

| 命题 | 类型 | 状态 | 验证状态 | 内容摘要 |
| --- | --- | --- | --- | --- |
| `clm-0de71d64524d` | definitional | **supported** | **verified** | 668 = 4·167、167 ≡ 3 (mod 4)，故 668 不被平凡整除障碍排除 |
| `clm-3d5ce939fbfe` | descriptive | **refuted** | **counterexample_found** | Plotkin 型约束 (M−1)d ≤ nM/2 对 (668,668,334) 有余量 334（原文表述被反例推翻） |
| `clm-2fc7a95cae15` | descriptive | blocked | unknown | **主命题**：等距码存在 ⟺ 668 阶 Hadamard 矩阵存在 |
| `clm-df9046d4a54c` | definitional | proposed | unknown | 证书格式：HHᵀ = 668·I₆₆₈，可在精确整数算术下判定 |
| `clm-7c232d88bf39` | descriptive | proposed | unknown | 若存在长度 ℓ=333 的 Legendre 对，则存在 668 阶 Hadamard 矩阵 |
| `clm-a956ddb37e4a` | descriptive | proposed | unknown | 668 阶模 Hadamard 矩阵已被构造（m=128、500；167 ≡ 7 mod 32 → 64-modular 族） |
| `clm-35efc2b2de75` | descriptive | proposed | unknown | 现有障碍性结果都是"结构化子族"障碍，不构成非存在性证明 |

历轮对比：上一轮 61 条命题**全部** `proposed/unsupported`；本轮 7 条中有 1 条 supported+verified、1 条 refuted+counterexample。

### 1.2 义务与验证：第一次真正闭合

**义务 10 条**：`closed` 5、`blocked` 4、`refuted` 1。

**验证记录 10 条**：

| 结果 | 条数 | 说明 |
| --- | --- | --- |
| `passed` / `verified` | **5** | sympy 实际跑通并通过 |
| `failed` / `counterexample_found` | **1** | 真的找到了反例（`clm-3d5ce939fbfe`） |
| `stale` / `unsupported` | 4 | 主命题的 4 条，因命题升版而失效 |

**5 份证明文件**（`proofs/`，首次非空），每份 3 步：`考察差 D = …` → `化简 D 并判定其符号` → 结论，规则为 `definition` + `algebraic_simplification`。其中三份是**纯算术**：

- `D = (668) − (4*167)` → 通过；
- `D = (667*334) − (668*668/2)` → **失败并给出反例**（这正是把 `clm-3d5ce939fbfe` 判为 refuted 的依据）；
- `D = (dot(u,v)) − (668 − 2*334)` → **被白名单拦下**（见 §2.1）。

### 1.3 检索链稳定

10 个证据任务全部 `completed`，均为"命中 → 入库 → 可引用"三级都有数：

```
命中 31 / 入库 9 / 可引用 9      命中 47 / 入库 6 / 可引用 14
命中 60 / 入库 6 / 可引用 13     命中 21 / 入库 1 / 可引用 6
命中 28 / 入库 3 / 可引用 11     命中 42 / 入库 1 / 可引用 6
命中 14 / 入库 1 / 可引用 8      命中 35 / 入库 3 / 可引用 7
命中 28 / 入库 2 / 可引用 12
```

### 1.4 渲染：历轮最干净

| 指标 | 值 |
| --- | --- |
| 页数 | 12 |
| `!` 致命错误 | **0** |
| `Missing character` | **0** |
| `Overfull \hbox` | **0** |
| `compilation_status` | **ok** |
| `pdf` | **manuscript.pdf** ✓ |

### 1.5 其它改进

- **模型终于进快照**：`models.json` 5 个（上一轮任务自述"提出 2 个候选模型"而快照为空）；
- **研究类型正确**：`research_type=formal_proof`、`task_kind=formal_proof`、`problem_statement` 411 字符的真实题面；
- **上一轮的"引述题面被误判"已修**：`src/publication/claims.py:11` 的 `asserts_completed_proof` 新增了 `_REQUIREMENT` 守卫（`须/需/应/必须/要求/请`），上一轮那个假阳性不再出现；
- **写作缺口记录质量高**（`writing_gaps.json` 11 条），例如：
  > 题面序列族与 668 阶 Hadamard 矩阵之间的等价已在正文逐行给出……**但尚未作为独立命题获得形式化核验记录**。
  > 建模记录登记的 668 阶模 Hadamard 矩阵数据集（m = 128、500）是否实际满足**非对角精确内积 0**（而非仅满足模 m 同余）尚未检查。

---

## 2. 仍然存在的问题

### P0-1 `dot(u,v)` 不在 sympy 白名单，主命题因此受阻

4 条 blocked 义务的失败原因完全一致（`unresolved.md`）：

```
- obl-4919c2ec → clm-2fc7a95cae15 v1 (blocked): 核验恒等: dot(u,v) = 668 - 2*334
  — 只允许调用白名单数学函数
- obl-37373488 → clm-2fc7a95cae15 v1 (blocked): 求等号成立条件 — 只允许调用白名单数学函数
（v2 同样两条）
```

对应实现 `src/verification/sympy_adapter.py`：

```python
_FUNCS = {"sqrt","exp","log","Abs","sin","cos",…,"factorial","binomial"}   # :20-24  没有 dot
_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                  ast.Name, ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div,
                  ast.Pow, ast.Mod, ast.UAdd, ast.USub, ast.Load, ast.Tuple)  # :26-30
...
if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
    raise _UnsafeExpression("只允许调用白名单数学函数")                        # :55-57
```

**问题在于**：`⟨u,v⟩ = n − 2d` 恰恰是本题归约的核心等式，而推理层选择用 `dot(u,v)` 表达它 —— 唯一需要的函数正好不在白名单里。于是 5 条纯算术义务全部通过、唯一涉及内积的那条永远受阻，主命题 `clm-2fc7a95cae15` 因此 `blocked`。

**候选修法**：(a) 支持有限维内积（把 `dot` 实现为 Σ 乘积，或加入 `_FUNCS` 并限制为有限维向量）；(b) 在推理层把 `dot(u,v)` 展开为逐坐标求和后再交给 sympy。两条都比放宽整个白名单安全。

### P0-2 4 条验证记录被判"过期"：正文引用命题 v1，而命题已升到 v2

`theory_gate_reasons` 首条：`存在 4 条被本次交付引用的过期验证记录, 需重算`。

对应 `unresolved.md` 中 30 条同族条目：

```
- 正文块 blk-5f8e3f1c 提及未登记的对象 clm-2fc7a95cae15@1
- 正文块 blk-516afde9 提及未登记的对象 clm-2fc7a95cae15@1
- 正文块 blk-638776ca 提及未登记的对象 clm-df9046d4a54c@1
- 正文块 blk-0446f94f 提及未登记的对象 clm-2fc7a95cae15@1
（共 30 条，为本轮 unresolved 的第二大类）
```

即：正文块绑定的是 `clm-…@1`，而命题在推理过程中升到了 v2，于是这些核验记录全部 `stale: true`。**32 条证据里真正被交付引用的部分核验记录反而失效了。**

### P0-3 措辞守卫假阳性：把"没有…的严格证明"读成"自述已完成证明"

`publication_gate_reasons` 唯一一条：

```
未定/未核验结论在正文中被写成已完成证明: clm-df9046d4a54c
```

定位到正文块 `blk-845888c7`（该块确实引用 `clm-df9046d4a54c`），文本是：

> 因此，在本轮可核验来源范围内，题面序列的存在性**仍未决**：既没有 668 阶 Hadamard 矩阵的显式精确证书，也**没有排除全部 668 阶 ±1 矩阵的严格证明**。判断该子问题所需的可核验证书只有一种格式：……

**这句话恰恰是在否认自己完成了证明**，却被判为"自述已完成证明"。判据（`src/publication/claims.py`）：

```python
_PROOF_ASSERTION = re.compile(r"我们证明|已(?:经)?(?:完成|完整)?证明|已被完整证明|完整证明了|严格证明|证明了|归约成立|必定成立|必然成立|\bqed\b|…", re.I)
_NEGATION = re.compile(r"(?:尚未|未能|不能|不得|不声称|并非|不是|不代表|没有).{0,10}$", re.I)
_REQUIREMENT = re.compile(r"(?:须|需|应|必须|要求|请)\s*(?:给出|提供|完成)?\s*$")
```

`_NEGATION` 要求否定词出现在匹配点**前 10 个字符以内**；而这里的 `没有` 距 `严格证明` 有约 20 个字符（`没有` + `排除全部 668 阶 ±1 矩阵的`），窗口太窄 → 漏检。

这是**同一族假阳性的第二次出现**：上一轮是"引述题面要求"（已被 `_REQUIREMENT` 修掉），这一轮是"较长距离的否定"（窗口太窄）。建议把否定判定从"前 10 字符"改为**子句级**：先按 `，、；` 再切一层，在匹配点所在的子句内查找否定词。

### P1-1 `research_spec` 仍部分为空

`research_type=formal_proof` ✓、`problem_statement` 411 字符 ✓，但：`domain=''`、`objects=0`、`variables=0`、`confirmed=False`、`frozen_version` 空。

### P1-2 `unresolved.md` 仍偏噪

260 行，分类：`other` 78、`block_refs_unregistered` 30、`low_relevance` 27、`derivation` 20、`clause` 10、`more_sources` 5、`obligation` 4、`manuscript_revision` 2、`empirical_support` 1、`model_condition` 1。

其中 27 条"低相关候选未纳入"仍是纯计数噪声；30 条"未登记对象"是 P0-2 的重复表述。

### P2 其余

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| P2-1 | 本地 KB 对部分主题仍不可用 | `unresolved.md`：`本地 KB 不可用（'modular Hadamard'、'equidistant codes' 资料库为空），无法回原文核验定理假设与证明对象`；可引用证据仅摘要级 |
| P2-2 | 审核发现问题 12 个（阻断 1） | `science 7, citation 2, readability 1, completeness 2` |
| P2-3 | 一个重要的**建模参数**疑问未被处置 | `unresolved.md`：等价性依赖"M = 668 恰等于 n"这一选择；若"668 个控制器"实际给的是**码长**而非**码字数**，等价链不成立，**需要澄清建模参数**。这是全轮最有价值的一条科学质疑，但没有转成人工澄清 |
| P2-4 | 空产物 | `figures/`、`evidence/`、`experiment_specs/` 仍空；`definitions/assumptions/gaps/routes.json` 均 2 字节；全文无图 |
| P2-5 | 写作三稿但均 `partial` | `8 节/6523 字`、`8 节/6688 字`、`12 节/11522 字; 4 处待核查` |

---

## 3. 与上一轮（proj-muwck6gx296p）的对照

| 指标 | 上一轮 | 本轮 |
| --- | --- | --- |
| `compilation_status` | failed（Overfull 46pt） | **ok** ✓ |
| `pdf` 字段 | `''` | **`manuscript.pdf`** ✓ |
| 交付等级 | 研究备忘录 | **条件性研究报告** ✓ |
| PDF 页数 / 致命错误 / 缺字形 / Overfull | 13 / 0 / 0 / 3 | 12 / **0 / 0 / 0** ✓ |
| 命题 | 1（内容是附件哈希） | **7（真实数学内容）** ✓ |
| supported + verified 命题 | 0 | **1** ✓ |
| refuted + 反例 | 0 | **1** ✓ |
| 验证记录 | 3（全 stale） | **10（5 verified / 1 反例 / 4 stale）** ✓ |
| 义务 | 3（全 blocked） | **10（5 closed / 4 blocked / 1 refuted）** ✓ |
| `proofs/` | 空 | **5 份** ✓ |
| 模型进快照 | 0（任务自述 2） | **5** ✓ |
| LLM 调用 / 成本 | 61 / $0.608 | 103 / $0.959 |

---

## 4. 建议处置顺序

| 顺序 | 动作 | 落点 | 预期效果 |
| --- | --- | --- | --- |
| 1 | **支持有限维内积**：把 `dot` 实现为有限维 Σ 乘积后加入白名单，或在推理层把 `dot(u,v)` 展开为逐坐标求和 | `verification/sympy_adapter.py:20-24`、推理层 | 直接解开 4 条 blocked 义务 → 主命题可核验 → theory/delivery 门槛有望通过 |
| 2 | **修否定判定的窗口**：`_NEGATION` 改子句级（按 `，、` 再切一层，在匹配点所在子句内找否定词），或把 `.{0,10}$` 放宽到可覆盖"没有…的"这类结构 | `publication/claims.py:11` | 消除第二次出现的同类假阳性 |
| 3 | **正文块与命题版本对齐**：正文引用 `@1` 而命题已升 v2 → 要么写回版本，要么把已 stale 的核验重新绑定到当前版本 | 写作/提交层 | 消除 30 条"未登记对象"与 4 条 stale 记录 |
| 4 | **把关键建模质疑转成人工澄清**：如"668 是码字数还是码长" | supervisor 澄清机制 | 这类问题一旦错了整篇作废，值得请求确认 |
| 5 | `research_spec` 的 `domain` / `objects` / `variables` 在团队路径下落值 | `graph/research_graph.py` | 消除长期残留 |
| 6 | `unresolved.md` 去掉纯计数噪声、合并同族条目 | `research/reporting.py` | 260 行 → 可读 |

---

## 附录 A：复现命令

```powershell
$snap = "E:\AIR\outputs\research\proj-muwia7ognn2l\snap-95179b4a"

# 1) 交付判定
$m = Get-Content "$snap\manifest.json" -Raw | ConvertFrom-Json
$m | Select-Object gate_passed, theory_gate_passed, delivery_gate_passed,
      publication_gate_passed, delivery_level, compilation_status, pdf
$m.publication_gate_reasons; $m.theory_gate_reasons; $m.delivery_gate_reasons

# 2) 命题 / 义务 / 验证
Get-Content "$snap\claims.json" -Raw | ConvertFrom-Json |
  Select-Object id, claim_type, status, evidence_grade, validation_status
Get-Content "$snap\obligations.json" -Raw | ConvertFrom-Json |
  Group-Object status, kind | Select-Object Count, Name
Get-ChildItem "$snap\verification" -File | ForEach-Object {
  (Get-Content $_.FullName -Raw | ConvertFrom-Json) |
    Select-Object tool, stale, status, validation_status }
Get-ChildItem "$snap\proofs" -File | Select-Object Name, Length

# 3) 三处阻断的确切原因
Select-String -Path "$snap\unresolved.md" -Pattern "白名单数学函数|过期验证|未登记的对象"
Select-String -Path "E:\AIR\src\verification\sympy_adapter.py" -Pattern "_FUNCS" -Context 0,5
Select-String -Path "E:\AIR\src\publication\claims.py"     -Pattern "_NEGATION" -Context 0,2

# 4) 渲染
& pdfinfo "$snap\manuscript.pdf" | Select-String Pages
Select-String -Path "$snap\manuscript.log" -Pattern "^!|Overfull|Missing character"
```

## 附录 B：本报告的边界

- **已核实**：§1、§2 的全部数字取自 `manifest.json`、`claims.json`、`obligations.json`、`verification/*.json`、`proofs/*.json`、`unresolved.md`、`writing_gaps.json`、`manuscript.log/.md`、`decisions.jsonl` 与运行库 `team_run.results`；§2 的代码判据引自 `verification/sympy_adapter.py:20-30,55-57` 与 `publication/claims.py:1-20`；`blk-845888c7` 的定位与"越权=True"是对运行库中的稿件 IR 实际执行 `asserts_completed_proof` 得到的。
- **未做**：本报告**未改动任何代码**；§4 的 6 条建议均未实施。
- **未评估**：本轮研究的**数学正确性**（"等距码 ⟺ 668 阶 Hadamard 矩阵"的双向构造是否严密）。本轮首次有了 `verified` 记录，但它们覆盖的是**算术恒等式**（668 = 4·167、667·334 ≤ 668²/2）与**反例**，主等价命题 `clm-2fc7a95cae15` 本身仍 `blocked`、其 4 条核验记录 `stale`。
- 全部统计在只读副本或原文件读取下完成，**未改动**冻结快照与运行库。
