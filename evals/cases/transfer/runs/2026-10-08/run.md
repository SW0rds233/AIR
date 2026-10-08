# 运行记录：2026-10-08（迁移题第二轮 · 端到端复跑验证）

| 项 | 值 |
|---|---|
| case-id | `transfer`（钙钛矿太阳能电池湿度稳定性） |
| project_id | `transfer-eval2`（**全新项目**，以避开首轮被污染的知识库主题） |
| problem_id | `problem` |
| run_id | `run-232302a9` |
| 快照目录 | `outputs/research/transfer-eval2/snap-c42273b2`（大文件留在 outputs，不入版本库） |
| 场景 | **S2 自主检索**（`source_policy=both`，未绑定人工资料库；资料 A/B 仍未入库） |
| 预算 | `max_rounds=16`，`RESEARCH_MAX_COST_USD=0.8` |
| 实际花费 | **$1.292**（LLM 105 次、工具 280 次、1,856,180 token） |
| 轮次 | **15**（首轮 11） |
| 交付等级 | **条件性研究报告** |
| 门槛 | `publication_gate` / `theory_gate` / `delivery_gate` 均 False |
| 编译 | **failed**（`! Package amsmath Error: Multiple \tag.`）→ 无 PDF |

## 输入（`case.md` §1，逐字）

```text
结合我选定的资料库，说明湿度如何导致钙钛矿太阳能电池性能衰减，并给出理论结论与可区分竞争解释的仿真建议。
```

## 本轮目的与对照基线

验证两件在首轮（`transfer-eval` / `snap-bc72b66d`）之后才修好的能力：

1. **跨领域语料污染是否真的消除**（首轮 130 篇来源里 12 篇是上一轮 Hadamard/等距码论文，且另有 2 条是上一次运行的产出）；
2. **双语检索是否真的生效**（首轮 `terminology_variants` 返回空、领域词全靠 LLM 工具循环）。

**换新 project_id 是刻意的实验设计**：`research-transfer-eval-problem` 正是首轮被污染的库，复用它会把旧污染算到新代码头上。

## 关键结果

### 1. 跨领域污染：**已消除** ✓

| 指标 | 首轮 | 本轮 |
|---|---|---|
| 来源总数 | 130 | 61 |
| 属上一轮数学领域（Hadamard/Legendre/等距码/编码论…） | **12（全部 fulltext）** | **0** |
| 系统产物被当成来源（`manuscript` / `unresolved`） | **2** | **0** |
| 路径批量导入（`data/kb/<topic>/path_refs.json`） | 有（`batch-20261006_233346`） | **无该文件** |
| 属本领域（钙钛矿/光伏/缺陷复合） | 107 | 58 |

剩余 3 条"难判定"项经逐条核对均属本领域：`MAPbI3 hydrate in triple mesoscopic stack minimodules`（水合物，正是主题）、`薄膜太阳能电池缺陷成像方法应用研究进展`、`Cracking in polymer substrates for flexible electronic devices`（与稿件中应力-开裂路径相关）。

### 2. 双语检索：**已生效** ✓

本轮 17 条检索式（**每任务 6 条**，首轮 4 条）：

| 组成 | 条数 |
|---|---|
| 含中文专名（钙钛矿） | 4 |
| 含英文全称（perovskite） | 12 |
| 含已登记缩写 `PSC` | 1（`PSC perovskite humidity`） |
| 含 `MAPbI3` / `CH3NH3PbI3` | 2 |

样本：`湿度 相对湿度 钙钛矿太阳能电池 性能衰减 稳定性 机理 模型`、`perovskite solar cell humidity degradation`、`PSC perovskite humidity`、`钙钛矿太阳能电池 湿度 衰减`、`MAPbI3 hydrate monohydrate dihydrate reversible irreversible moisture degradation`。

### 3. 新问题：**LaTeX 编译失败，无 PDF**（R-008）

```
compilation_status  failed: LaTeX 编译报错, 输出可能被截断
publication_gate_reasons:
  - 存在未标注的占位符: 待定, 待定, 待定, 待定, 待定
  - LaTeX 编译失败
```

根因（见 `../failures.md` R-008）：模型自撰公式自带编号（`\tag{3.4}`…`\tag{3.13}`，共 5 处），而渲染器把该块包进自动编号的 `\begin{equation}` → `Multiple \tag`；`render_latex.py` 中**没有任何 `\tag` 处理逻辑**。首轮该现象为 0 次，本次未改 `publication/`，属**新触发**。

附带：失败编译留下的 `manuscript.pdf`（182,215 字节）仍在包内，而 `manifest.pdf` 为空（R-009）。

### 4. 结论受阻项：3 条**实质性**判定（正面信号）

`theory_gate_reasons` 里三条 blocked 结论都指出了"结论强度超过证书"的具体位置：

| 结论 | 系统给出的理由 |
|---|---|
| `clm-683626c580a4` | 分支概率 `p = k_r/(k_r+k_i)` 只是代数定义；要落在 [0,1] 须额外施加 `k_r>0, k_i>0`。变量域未指定（允许 `k_i<0`）时该"概率"可取负值 |
| `clm-b14e03ea20c9` | 侵入前沿只能写成标度关系 `x_f = sqrt(α·D_eff·t)`（α 为 O(1) 前置因子）；丢掉 α 的 `x_f² = D_eff·t` 一般不成立，故 `D_eff = x_f²/t` 的定量反推不被支撑 |
| `clm-49eb83319ed8` | `D_eff ≡ D_b + f·(D_gb − D_b)` 是定义/假设；写成 `D_eff = D_b + (2δ/g)·D_gb` 不是恒等式，两者相差 `f·D_b` |

即：系统拒绝让正文把"定义式"当成"可符号核验的恒等式"。这与 `rubric.md` §1.4「局部步骤验证没有被写成整体证明」一致。

### 5. 与首轮的指标对照

| 指标 | 首轮 | 本轮 |
|---|---|---|
| 来源 / 命题 / 验证 / 模型 | 130 / 8 / 8 / 12 | 61 / 4 / 8 / 6 |
| 每任务检索式 | 4 | 6 |
| 轮次 / 花费 | 11 / $0.825 | 15 / $1.292 |
| 等级 | 条件性研究报告 | 条件性研究报告 |
| 编译 | ok（有 PDF） | **failed（无 PDF）** |
| 跨领域污染 | 12 篇 + 2 条产物 | **0** |
| 双语检索 | 未生效 | **生效** |

## 复现

```powershell
# 见 ../runbook.md §4；本次以编程方式调用：
# run_team_session(<case.md §1 输入>, project_id="transfer-eval2", problem_id="problem",
#                  source_policy="both", max_rounds=16)
```

## 环境备注

本轮外部检索持续被限流：Semantic Scholar 连续 429 达 14 次、arXiv 亦出现 429（断路器多次开启）。检索召回质量受此影响，结论以"在所检索范围内"为界。
