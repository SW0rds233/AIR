# AIR 未完成工作清单（迁移用）

更新日期：2026-10-03 · 依据：2026-10-02 版计划书 §3–§6、本会话实测记录
用途：**迁移到下一对话**。本文件只列"还没做完的事"和"已核实的接入点"。

> **2026-10-03 会话更新**：§1（S1 命题化）、§2（删除领域硬编码）、§3（R6 快照校验）、
> §4（R7 分块流式落盘）、§5（精简与命名统一）、§6.1（runbook）**已完成**并跑完全量回归。
> 结论与证据见各节的"✅ 已完成"块；仍未完成的项集中在 §6.2 之后的**人工/环境依赖**与
> **决策项（D1/D2 仍未裁决）**。

---

## 0. 已验证基线（先跑一遍确认没漂）

| 命令 | 期望结果 |
|---|---|
| `pytest -q` | **876 passed**，约 125s（离线默认） |
| `cd src/web && npx tsc --noEmit` | 0 error |
| `cd src/web && npx vitest run` | 73 passed |
| `node tests/js/bundle_security.test.js` | 全部通过 |
| `pytest tests/test_browser_web_flow.py` | 5 passed（真实 Chromium：单页流程/整链/草稿身份/图标/静态守卫） |
| `.\scripts\frontend_needs_build.ps1` | 输出 `0`（前端产物与源码同版本；`1` = 需要 `npm run build`） |

运行政策：测试与默认开发**一律离线**（`THEORY_LLM=0`、`THEORY_PROPOSER=0`），
`usage` 复核 `cost_usd=0.0 / llm_calls=0 / tokens=0`。真实模型仅在你明确授权时使用。
**并行跑 pytest 时必须带独立 `--basetemp`**（`pyproject.toml` 的 `addopts`
是全工作区共享的，多个进程同时跑会互相删临时目录、产生假红）。

---

## 1. ✅ 已完成：S1 命题化（判定 → 命题/义务 → 验证 → 门槛）

**交付**：`problem2.md` 离线端到端达到**论文草稿**、门槛通过、`usage` 全零。

- 判定引擎 `src/research/design_feasibility.py` 重构为**带出处**的结构化判定：
  每条 `NecessityCheck` 记录 `key / 定理名 / 判定条件 / 定理陈述 / 输入 / 结论 / 出处`，
  未实现或不适用者进 `UncheckedEntry`（不再有"看起来已检查完"的字符串 limitations）；
  新增 `certificate_dict()/certificate_json()/certificate_digest()` 作为可复核证书。
- 命题化入口 `formulate_from_text(text)`：题面 → 1 条命题 + 1 条必要性义务，
  参数全部来自 `extract_design_params`，**代码里没有任何具体题目常量**。
- `loop.bootstrap`：`formulate` 无命题时改走命题化（原来是只写笔记 + 请求澄清）；
  抽不出计数约束时行为不变（仍请求澄清）。
- 新验收方式 `acceptance_method="design_necessity"`（`loop._evaluate_design_necessity`）：
  用**命题记录里的参数重算**判定，写验证记录（`tool=design_necessity`，含定理陈述与输入）；
  **仅 `nonexistent` 关闭义务**，`necessary_met` 保持未关闭并记录"还缺哪一步"。
- 防偷换：`Claim` 新增 `design_v/k/lambda/b/r/verdict`，`acceptance._aligned` 要求
  验证记录的判定输入与命题记录逐项一致（换参数/改结论 ⇒ 对齐失败）。
- 新增支持方式 `SupportKind.theorem_application`（具名定理 + 机器重算参数）：
  门槛要求验证记录里必须能反查到定理陈述与参数证书。
- 正文：`theory_writer` 对这类命题**不再附通用代数论证链**（那会凭空显示"差式比较"等
  与结论无关的步骤），改附证书判定链；`derive_step` 也按证书生成步骤、不跑反查清单。
- 题面分类修正：能精确形式化（计数约束抽取成功）的题面按**明确问题**处理，
  不再因"希望/要求"等方向措辞被当成研究方向、先让用户选候选路线。

**回归**（`tests/test_design_feasibility.py`，17 项全绿）：

| 用例 | 期望 |
|---|---|
| 2-(211,15,1)（problem2） | 不存在；义务关闭；门槛通过；交付等级**论文草稿**；正文含 BRC 判定链 |
| 2-(43,7,1) | 不存在；义务关闭；门槛通过 |
| 2-(7,3,1)（Fano） | **必要条件满足、存在性未定**；义务**不得**关闭；不输出"不存在" |
| "分析信道变化对可分性的影响" | 不生成设计命题，行为不变 |
| 参数/结论偷换 | `_aligned` 拒绝（同参数不同结论也拒绝） |

成功判据（"不得推广到其他 (v,k,λ)" 等）落进 `research_spec.json` 的
`success_conditions`，供人工评审逐条对照（`theory_pipeline._record_design_success_conditions`）。

---

## 2. ✅ 已完成：删除"针对某一类特殊问题的专门构造"

`src/rag/relevance_filter.py` 的射频领域知识已**全部移入数据文件**：

- 新数据文件 `evals/cases/rf-fingerprint/retrieval_terms.md`：
  `domain / aliases / in_domain / strong_in_domain / off_domain_confusables /
  non_technical_venues` 六个键，中英变体都可写；新增领域 = 新增一份文件，**不改代码**。
- 代码只留与领域无关的原语：`mentions(terms, text)`（英文按词边界、中文按子串）、
  `mention_spans`、`_has_nearby`（强领域词与混淆词就近共现才算"领域内边缘工作"）、
  `parse_terms`、`load_terms`、`resolve_domain`（主题匹配别名）、
  `domain_terms`（显式 > 主题 > 语料识别）、`is_off_domain` / `has_domain_signal`。
- **判不出领域时不做任何领域假设**（返回空词表 → 过滤失效），不再有"只对一个领域有效"的保护。
- 调用点全部改为数据驱动：`graph/pipeline._resolve_review_suggestions`（领域门 + 跨域门）、
  `utils/pipeline_cache`（历史缓存兜底）。
- 顺带清掉两处同类硬编码：`subquery_generator.SUBQUERY_SYSTEM` 的示例词表、
  `paper_writer`/`paper_reviewer` 提示词里的领域词表（改为"判据只能是本次输入的研究主题 +
  文献标题，提示词里的领域词表不是判据"）。
- 回归 `tests/test_relevance_cache.py`（9 项，含"领域词汇必须来自数据"与
  "提示词不得含领域硬编码"两项新增断言）。

---

## 3. ✅ 已完成：R6 收尾（续跑消费输入快照 + 附件校验）

- 新增 `src/research/input_snapshot.py`：`verify_input_snapshot(snapshot) → SnapshotCheck`，
  逐份附件**读文件重算 sha256**，结合登记表与路径归属，给出
  `ok / missing / hash_mismatch / unregistered / outside_root` 五态与具体原因。
- `theory_pipeline`：`resume=True` 且校验不通过 → **拒绝续跑**（`InputSnapshotRejected`，
  进入图之前抛出，不产出新交付包，同时落审计事件）；显式 `allow_degraded_resume=True`
  才降级并在返回值/manifest 写明原因。
- 关键附带修复：`TheoryState` 未声明 `input_snapshot`，该键根本进不了图状态（manifest 一直为空）；
  另一个是续跑时用"本次重建的空附件快照"顶掉了检查点里那一份。
- **入口无关**：CLI/直接调用也自建启动快照（`_entry_input_snapshot`），
  "能不能说这次输入可复现"不再取决于从哪个入口进来；资料库内容未做 hash 这一点在 `note` 写明。
- manifest 新增 `input_snapshot.verification`（逐份状态 + 原因 + basis + checked_at），
  `reproducible` 一律由**实际校验**决定。
- 回归 `tests/test_input_snapshot.py`（13 项）：一致 / 内容被改 / 文件被删 / 登记被删 /
  路径越界 / 声明不可复现 / 端到端续跑拒绝与降级。

---

## 4. ✅ 已完成：R7 收尾（上传分块流式落盘）

- `src/utils/uploads.py`：`staged_upload()` 上下文管理器（1 MiB 分块、"读→写→边算 sha256"），
  声明值超限时**在落盘前**拒绝，超限/读写出错/调用方失败**一律清理暂存文件**；
  保存函数改收暂存路径并接管（`_adopt` / `_discard`）；`copy_stream_to_bytes` 已删除。
- 请求级上限按**实际字节数**统计（`ByteBudget`）：声明大小可以撒谎，
  用尽时其余文件一次性如实告知（`UploadRequestTooLarge` → "未处理剩余文件"）。
- `server.py` 上传端点：`declared_size()` 区分 `None` 与 `0`（缺失不再被误判为空文件）。
- 报错文本低于 1 MB 时显示 KB（原先 8 KiB 会显示成 "0.0 MB"）。
- 回归 `tests/test_uploads.py`（23 项）。

---

## 5. ✅ 已完成：代码精简与命名统一

- `relevance_filter` 命名统一：`mentions()` / `topic_terms()` 语义（`domain_terms` 解析），
  `has_domain_signal` / `has_off_domain_signal` / `is_off_domain` 收敛到同一实现（兼容入口保留）；
  模块常量词表全部删除。
- 工作台投影的 payload 打包**已统一**在 `src/research/reporting.py::workbench_projection`，
  `server._research_state` 只负责开库/取数/传参（核对结论：无重复实现，未再改动）。
- 冗余注释清理：删掉复述"是什么"的注释，保留"为什么"。

---

## 6. 评测与真实测试准备

### 6.1 ✅ 已完成：`evals/cases/combinatorial-design/runbook.md`

已包含：运行前自检、**离线冒烟**（CLI + Web 两条等价入口、9 条验收判据、泛化回归表）、
**限额真实测试**（预估区间与硬上限必须先填写、触顶停止与停止原因、运行后逐项核对）、
命名与身份约定、11 项归档清单、`runs/<YYYY-MM-DD>/` 约定、**判据映射（计划书发布门槛 1–5）**、
失败记录纪律。同时新建 `evals/cases/combinatorial-design/failures.md`（列名与 `rubric.md` §3 逐字一致）。

### 6.2 仍未完成（人工/环境依赖，自动化替代不了）

1. `evals/cases/combinatorial-design/expected_notes.md` **需数学/组合设计方向独立人员核对签字**：
   BRC 应用（余数类、两平方和判定、$v=k(k-1)+1\Rightarrow$ 射影平面）、
   以及"引用 BRC 是否算合格""未决报告是否算部分通过"两项评分约定。**真实测试时不得提供给系统**。
2. rf-fingerprint 材料 A/B、封存的 `expected_notes.md`（当前为空）与 `evals/rubric.md` §4 专家签字。
3. 第二领域题目的选择（`evals/cases/transfer/case.md` 仍是 TODO），用于迁移性验证（发布门槛 5）。
4. **首次真实运行后的失败记录归档**（失败样例必须保留，不得用改期望或润色掩盖）。
5. **限额 LLM 真实测试**（唯一花钱的步骤）：先按 runbook §2.1 填预估区间与硬上限，经授权后运行，
   跑完用 `manifest.json` 的 `usage` 与 `budget_limits` 对照，报告实际花费。

### 6.3 仍未完成（本轮已知功能缺口）

1. ~~**正文行内的数学排版仍部分为纯文本**~~ **已完成**（见 `AI for Research(AIR)计划推进情况.md`
   三之九）：正文第 3 节的证明块与附录一样改用结构化证书排版（行间公式 / 逐条件条目 /
   `booktabs` 汇总表），段落里的纯文本公式也统一包进 `$...$`（`wrap_inline_math`）。
2. **真实网络检索取决于用户选的资料范围**：只有资料范围选「自主检索」或「两者合并」时才会走
   外部 arXiv/OpenAlex 检索；选「只阅读我上传的文献」且资料库为空时**按设计不检索**
   （界面提示可改为自主检索）。
3. **arXiv 是词面索引，召回经典文献能力弱**：题面参数与经典论文标题用词不重合时几乎检不到，
   例如 1949 年的 Bruck–Ryser–Chowla 原文；这类相关工作目前只能靠模型自带知识或人工补文献。

---

## 7. 待你决策的历史项

| 编号 | 问题 | 现状 | 影响 |
|---|---|---|---|
| D1 | 评分标准：引用 BRC 即算合格，还是要求系统自行推导 BRC？ | 当前实现为"具名定理 + 参数证书"（`theorem_application`），不含 BRC 自行证明 | 决定 S1 验收门槛高低；若要求自行推导则本题按不通过记，需要更强的符号/形式化能力 |
| D2 | "必要条件满足但存在性未定"的未决报告是否算**部分通过**？ | 当前按"可选结论之一"处理（Fano 参数下如实未决、义务不关闭、等级不升级） | 决定能力边界如何计分 |
| D3 | R7 是否接受"有界拒绝"作为本版边界（不做流式落盘）？ | **已实现流式落盘**，该项不再需要决策 | — |
| D4 | `src/web/dist/` 是否入库？ | 未变 | 影响新克隆是否开箱可用 |

---

## 8. 本会话已交付（供对照，勿重复实现）

- S1 命题化全链（§1）与全部回归；
- 领域硬编码 → 数据文件（§2）与提示词领域词清理；
- R6 输入快照校验 + 入口无关快照（§3）；R7 分块流式落盘 + 请求级实际字节上限（§4）；
- `evals/cases/combinatorial-design/runbook.md` + `failures.md`（§6.1）；
- 成功判据落进 `research_spec.json`（"不得推广到其他参数"可核查）。

**真实测试（2026-10-03 已完成首次）**：`deepseek-v4-flash` 实跑两次，均为
**论文草稿 / 门槛通过**；每次 3 次模型调用（全部来自提议器 `stage=proposal`），
8.1k 与 9.2k tokens；按官方单价两次合计 **闲时 ≈ \$0.0080**（峰时 ≈ \$0.0159）。
同时修掉三处真实运行/使用暴露的问题：成本估算用的价目表未收录该模型（高估约 2.5 倍）、
交付物标题被整段请求文本撑爆（新增 `derive_topic`）、以及前端两个 404 缺陷
（见 `evals/cases/combinatorial-design/failures.md` CD-001/CD-002，运行手册 §0.1 已把
"前端产物与源码同版本"列为 Web 入口硬前置）。

4. **成文结构仍偏薄**：本次交付只有 1 条命题，第 4 节（与已有工作比较）在离线无检索时
   只能是"未执行检索"的说明，第 6 节结论两句话。这不是排版问题，而是**研究内容量**问题：
   要拿到像样的期刊论文，需要真实模型跑批产出多条命题与真实文献比较。
5. **作者信息仍为占位**：`作者姓名（待作者补充）`，缺机构、邮箱、基金号；这些只能由用户提供。

6. **中文标注术语的英文检索锚点缺失**：`2-设计` 只能映射到泛词 `design`，跨语言检索式会退化
   成 `nonexistence` 这类泛意图词，实测返回 "The Nonexistence of Character Traits" 等无关
   论文。判定层会把它们全部标为 `insufficient` 并拒绝引用（不会产生假引用），但召回质量差；
   要改善需要领域受控词表或查询扩展。
7. ~~**`gaps.json` 落盘为空**~~ **已修复**（三之十二）：新增 `_persist_gaps()`，实测由 0 条变 14 条。

8. ~~**定理级引用缺全文定位**~~ **已修复**（三之十三）：理论模式检索现在会下载全文入库，
   入库即产出带页级定位的定理卡片（实测 `p7 / Theorem 10`）。仍需核实的是
   **引用守门的二次判定**：有了定理卡片后，`_judge_support` 是否真能把相关定理升为可引用
   （而不是仍停在 `insufficient`），这需要一次联网端到端运行确认。
