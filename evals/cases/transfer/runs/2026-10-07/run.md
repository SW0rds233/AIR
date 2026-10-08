# 运行记录：2026-10-07（迁移题首轮盲测）

| 项 | 值 |
|---|---|
| case-id | `transfer`（钙钛矿太阳能电池湿度稳定性） |
| project_id | `transfer-eval` |
| problem_id | `problem` |
| run_id | `run-af28be7a` |
| 快照目录 | `outputs/research/transfer-eval/snap-bc72b66d`（大文件留在 outputs，不入版本库） |
| 场景 | **S2 自主检索**（`source_policy=both`，未绑定人工资料库；资料 A/B 未入库） |
| 预算 | `max_rounds=16`，`RESEARCH_MAX_COST_USD=0.8` |
| 实际花费 | **$0.825**（LLM 109 次、工具 250 次、1,133,244 token） |
| 轮次 / 收尾 | 11 轮；`stop_reason` = 交付形态齐备 \| 交付门槛未通过 |
| 交付等级 | **条件性研究报告** |
| 门槛 | `publication_gate_passed=True`；`theory_gate/delivery_gate=False` |

## 产物规模

| 项 | 值 |
|---|---|
| 命题 | 8（`descriptive` 4 / `definitional` 2 / `associational` 1 / `predictive` 1） |
| 证据 | 130 篇来源（约 25 篇 fulltext） |
| 验证记录 | 8 |
| 模型候选 | 12 |
| 义务 | 16（`closed` 1 / `blocked` 6 / `open` 8 / `refuted` 1） |
| 正文 | 21 章 86 段 / 17,638 字；PDF 正常编译（`Overfull 0`、`Missing character 0`） |
| 检索式 | 29 条（中英混排，领域词齐备） |

## 输入（`case.md` §1，逐字）

```text
结合我选定的资料库，说明湿度如何导致钙钛矿太阳能电池性能衰减，并给出理论结论与可区分竞争解释的仿真建议。
```

## 关键观察（详见 `../failures.md`）

1. **跨领域污染（R-001/R-002，严重）**：本轮 KB 在建库时把 `data/pdfs/` **整体导入**
   （`path_refs.json`，`batch-20261006_233346`），使上一轮 Hadamard/等距码语料成为本领域"证据"，
   并导致建模层**选中了一个数论定理**作为机制模型。
2. **契约退化（R-003）**：`research_spec` 仍为 `scenario` 模板，未识别机制类问题。
3. **暴露时长缺失（R-004）**：结论只绑定湿度与温度，缺第三维。
4. **术语扩展空转（R-005）**：`terminology_variants` 返回空；领域词靠 LLM 工具循环补齐。
5. **正面**：跨领域措辞泄漏为 0；无越权措辞；仿真建议给出可区分判据；
   `writing_gaps.json` 独立指认了"低湿度可逆水合反例窗口"与"关联而非因果份额"。

## 复现

```powershell
# 见 ../runbook.md §4；本次以编程方式调用 run_team_session(..., source_policy="both", max_rounds=16)
# 领域词表自检: ../runbook.md §3
```
