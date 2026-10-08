# 失败案例台账：迁移题（`transfer` · 钙钛矿太阳能电池湿度稳定性）

> 依据 `evals/rubric.md` §3。记录**首次真实运行**（2026-10-07）暴露的失败项。
> 运行身份：`project=transfer-eval` / `run=run-af28be7a` / `snapshot=snap-bc72b66d`
> （场景 S2：自主检索，未绑定人工资料库；资料 A/B 尚未入库，见 `case.md` §5）

## 1. 记录

| 编号 | 日期 | 维度 | 现象（含文件/行位置） | 期望 | 处理 | 状态 |
|---|---|---|---|---|---|---|
| R-001 | 2026-10-07 | 资料与来源 | `manifest.json` `source_set.documents` 共 130 篇，其中 **11 篇属上一轮数学领域**（Hadamard / Legendre 对 / 等距码 / 编码论 / 模形式），**6 篇为 `fulltext`**（如 `2026_Kulhandjian_..._32-modular_Hadamard_matrices...`、`2017_Polyanskii_On_almost-equidistant_sets`、`2012_Gérard_Construction_of_Hadamard_states_by_pseudo-differential_calculus`）。证据：`data/kb/research-transfer-eval-problem/path_refs.json` 显示这些 PDF 在**建库时**被批量导入（`batch_id=batch-20261006_233346`，`imported_at=20261006_233347`，源目录 `data/pdfs/`） | 新领域的 KB 只能包含本领域来源；上一轮领域的全文不得进入 | 待处理 | 待处理 |
| R-002 | 2026-10-07 | 模型忠实度 | `models.json` 12 个候选中**至少 6 个是数学论文原文片段**：`5 Difference Sets ... construction of Hadamard matrices`、`arXiv:2607.20765v1 [math.CO] ... Theorem 1. Let H ≤(Z/333Z)× ... Legendre`、`\|c′′\| × X γ ... ∈Γ∞\Γ`、`l′ . We remark that in an odd cycle`、`X j∈J1,...,r tj = n −`；且**被选中的**（`selected: True`）是 `CONSTRUCTIVE VERIFICATION OF THEOREM 7 VIA THE ÁLVAREZ ET AL. PSEUDOCOCYCLE FORMULA` | 候选机制必须来自本领域资料，并给出舍弃理由 | 待处理（根因同 R-001） | 待处理 |
| R-003 | 2026-10-07 | 研究意图 | `research_spec.json`：`research_type=scenario`、`contract.task_kind=scenario`、`allowed_methods=['参数化情景推导','条件比对','灵敏度分析']`、`domain=''`、`objects=0`、`variables=0`；而同一轮 `claims.json` 已有 8 条带 `variable_domains` 的机制类命题 | 契约应识别为机制/条件类问题，并把变量域写入 spec | 待处理 | 待处理 |
| R-004 | 2026-10-07 | 研究意图 | 结论绑定湿度（`RH` 12 次、`相对湿度` 18 次）与温度（31 次），但**暴露时长维度缺失**：`时长` 0 次、`暴露` 0 次、`小时` 1 次。本用例 `domain_terms.md` §2 规定湿度+温度+时长构成条件三元组 | 条件三元组齐全，或明确声明为何未限定 | 待处理 | 待处理 |
| R-005 | 2026-10-07 | 资料与来源 | `terminology_variants('…钙钛矿太阳能电池性能衰减…')` 返回 **`[]`**（零领域词）。本轮 29 条检索式中的领域词（`CH3NH3PbI3 humid air degradation hydration PbI2`、`relative humidity perovskite solar cell efficiency degradation recovery annealing` 等）**全部来自 LLM 工具循环**，而非术语表；对照：相关性过滤已数据驱动（新增 `src/rag/domain_terms/perovskite-stability.md` 即生效，`is_off_domain` 对地学同名词判定正确） | 跨语言检索术语扩展同样可扩展/数据驱动，或至少不返回空 | 2026-10-08：运行时词表增加 `translation:`/`query_core:`；`plan_queries` 与 `EvidenceAgent` 将中文、英文全称、缩写分别规划，`choose_queries` 保留双语覆盖。当前 `terminology_variants('研究钙钛矿太阳能电池性能衰减')` 已返回英文全称与 `PSC`；离线回归见 `tests/test_shared_research_quality.py::test_perovskite_request_gets_mechanism_contract_and_shared_field` 与 `tests/test_multilingual_retrieval.py`；真实 S2 重跑待做 | 已修复(test_partial_term_proposal_keeps_registered_field_translations) |
| R-006 | 2026-10-07 | 论证与验证 | `obligations.json` 16 条：`closed` 1、`blocked` 6、`open` 8、`refuted` 1；`theory_gate_reasons` 报"存在 6 条被本次交付引用的**过期**验证记录"；`delivery_gate_reasons` 报 6 条"未关闭义务的结论被写入正文" | 未关闭义务对应的结论不得以已成立口径写入正文；过期记录需重算 | 待处理 | 待处理 |
| R-007 | 2026-10-07 | 资料与来源 | 本次为场景 S2（未绑定资料库），`case.md` §2 的资料 A/B 尚未入库，因此 rubric §1.2 的"资料 A/B 隔离"与"结论只引用绑定来源"**无法评估** | 补齐资料 A/B 并绑定 `source_set_id` 后重跑 | 判定为非缺陷(用例材料尚未入库，属用例待办 `case.md` §5；非系统行为) | 待准备材料 |

## 2. 首轮通过项（不登记为失败，供回归对照）

- **跨领域泄漏（case §4 / runbook §6）**：Markdown/LaTeX 中**无**组合设计或数学类措辞；
  唯一 `指纹` 命中是 `clm-17cc459915ba` 的"两模型给出**符号可区分的指纹**"（"signature"比喻，
  本领域合法用法）。`(v,k,λ)` 的唯一命中在 `.tex` 导言区**注释**里（渲染器自带说明），非正文。
- **必须拒绝的说法（case §3.2）**：`仿真已验证` / `已验证` / `原创发现` / `无条件` / `必然导致`
  在正文中均为 **0 次**；`novelty_review.md` 全部为 `unchecked`，并写明"不得据此宣称创新"。
- **仿真建议（rubric §1.6）**：validation 任务产出"6 个竞争解释 / 9 个判据"；
  `clm-17cc459915ba` 给出符号可区分的判别预测（A 通道：ΔJ_sc/J_sc 降幅 > ΔV_oc/V_oc 且与吸收边移动相关；
  B 通道：ΔV_oc/V_oc 更大且与 TRPL 推得的 ln(N_t2/N_t1)·(nkT/q) 一致）。
- **写作缺口回流（rubric §1.7）**：`writing_gaps.json` 12 条，且**独立指出了封存答案中的关键点**
  （"效率对相对湿度单调不增存在**反例窗口**（低湿度区间的缺陷钝化或可逆水合自修复）"；
  "湿度在总衰减中的份额未知……本文对湿度效应的陈述是**关联性命题，不是因果份额**"）。
- **渲染**：`compilation_status=ok`、`Overfull 0`、`Missing character 0`；交付等级 `条件性研究报告`
  （与本用例 `case.md` §3.3 的期望一致）。
