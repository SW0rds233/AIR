# 下一步实现交接：把设计可行性判定变成研究产物（S1 命题化）

> 背景：`design_feasibility.py`（通用必要条件判定）与题面抽取已实现并通过测试；
> 判定结果目前只写进引擎笔记，**尚未生成命题/义务**，因此 `problem2.md` 仍停在
> "请求澄清 → 研究备忘录"。本文件记录已核实的接入点，供继续实现，避免重复排查。

## 已实现（可复用，勿重写）

| 能力 | 位置 | 说明 |
|---|---|---|
| 通用必要条件判定 | `src/research/design_feasibility.py` | `check(DesignParams(...))` → 三态（nonexistent / necessary_met / insufficient）+ 逐条证据 + 未实现限制 |
| 题面抽取 | 同上 | `extract_design_params(text)` / `feasibility_from_text(text)`；中英双语、多措辞，抽不到返回 `None` |
| 判定进入运行笔记 | `src/research/loop.py` bootstrap 分支（`form = formulate(self.spec, self.available)` 之后、`if not form.claims:` 内） | 已把 `report.describe()` 追加到 `self._notes` |
| 交付语义护栏 | `src/graph/theory_pipeline.py` finalize | 澄清/零命题 → `gate_passed=False`、`delivery_level=研究备忘录` |

## 待实现的接入点（已核实）

1. **命题/义务的来源不在 `question_planner.formulate`**
   - `src/research/question_planner.py:431` 的 `formulate(...)` 返回的是 **`ProblemContract`**（P0-2），不是命题集合。
   - `loop.py` 里 `form = formulate(self.spec, self.available)` 用的是**另一个** `formulate`（返回对象含
     `.claims/.obligations/.assumptions/.definitions/.unknown_fields/.notes`）。实现前先确认它的 import 来源。
2. **持久化路径现成可用**：`loop._persist_formulation(form)` 会把
   `form.assumptions/definitions/claims/obligations` 按 `KIND_*` 写入并 append 一步（事件 `formulated`），
   并为每条 claim 补 `problem_id`。因此只要在调用它之前往 `form` 里补对象即可，无需新写持久化。
3. **义务需要验收方式**：`loop.py` 中规则型义务由 `_evaluate_rule_obligation`（事件
   `rule_obligation_evaluated`）按 `obligation.acceptance_method` 分派。设计类命题应新增一种规则验收
   （例如 `acceptance_method="design_necessity"`），把 `check()` 的每条证据映射为可复核的证书，
   **`nonexistent` 才算关闭**；`necessary_met` 必须保持未关闭（存在性未定）。

## 实现要求（来自计划书，勿违反）

- **不得**为具体题目写规则或常量：所有数值一律来自 `extract_design_params`；
- 生成命题时必须带**来源要求**：BRC 这一步要记录"引用了哪条经典必要性定理"，而不是空口断言；
- 抽不出计数约束（`feasibility_from_text → None`）时**保持**现有"请求澄清"行为；
- 判定为 `necessary_met` 时**不得**输出"存在"结论，只能给"必要条件满足、存在性未定"并保留未决义务；
- 交付等级仍由门槛决定，不允许因为有了判定笔记就升级。

## 建议的验收

- `problem2.md`（离线）：产生 ≥1 条命题 + 对应的必要性义务，义务由规则验收关闭，
  门槛通过、交付等级达到论文草稿，且正文引用该判定链；
- 同一套逻辑用**不同参数**（如 Fano 2-(7,3,1)、2-(43,7,1)）验证：前者不得输出"不存在"，后者必须输出；
- 反向用例：抽不到参数的题面（如"分析信道变化对可分性的影响"）行为不变（澄清）；
- 全量 `pytest -q` 保持全绿（当前基线 **737 passed / 47s**）。
