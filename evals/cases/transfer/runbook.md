# 盲测运行手册：迁移题（`transfer` · 钙钛矿太阳能电池湿度稳定性）

本手册规定**怎么跑这一次盲测**，使结果可与 `rf-fingerprint` 对照。
评审口径见 `evals/rubric.md`；结论对照见封存的 `expected_notes.md`（评审前不得打开给系统看）。

## 0. 运行前自检

- [ ] **封存答案未泄漏**：对以下位置全文检索 `expected_notes.md` 的判定语句
      （"可逆"、"阈值不是普适"、"机制可以并存"）以及阈值数值，确认未出现在
      输入请求、问题描述、`domain_terms.md`、`retrieval_terms.md`、资料库文件中。
- [ ] **领域词表已就位**：`src/rag/domain_terms/perovskite-stability.md` 存在，
      且主题文本能匹配到它的 `domain`/`aliases`（自检见 §3）。
- [ ] **双语查询已规划**：中文专名、英文全称与词表已登记的 `PSC` 分开；
      词表中的英文同义名称不能被拼成必须同时命中的单条查询。
- [ ] **无射频残留**：确认本次输入与本用例材料中不出现射频/指纹/信道术语。
- [ ] **运行预算已记录**：团队默认轮次上限与实际调用量写入运行记录；CLI 不接受旧引擎的 `--max-actions`。
- [ ] **运行身份已记录**：`project_id` / `problem_id` / `run_id` 写入 `runs/<日期>/`。
- [ ] **资料场景已声明**：本次是"有绑定资料库"还是"自主检索"，
      两者结论强度不可混谈（见 §2）。

## 1. 两种资料场景

| 场景 | 绑定 | 适用 |
|---|---|---|
| S1 有资料库 | `source_policy=user_kb`/`both`，`source_set_id` 非空，资料 A/B 已入库并有版本 | 检验"用没用对资料"（rubric §1.2） |
| S2 自主检索 | `source_policy=both`（允许自主外搜），无预建资料库 | 检验检索覆盖与领域词扩展；**结论强度天然更弱**，不得据此判"资料 A/B 隔离" |

**两种场景都必须记录**，且 S2 的结果不得用来宣称 rubric §1.2 通过。

自备 PDF 应通过工作台的“文献附件”上传到指定资料库主题（服务端 `POST /api/uploads`，
`kind=literature`，`topic=<资料库主题>`），或从另一个已授权目录显式导入。
`data/pdfs/` 是自动下载缓存，路径导入会拒绝它；不要把自备 PDF 放进缓存来绕过来源登记。

## 2. 命名与归档

- 运行产物留在 `outputs/research/<project>/snap-*/`（不入版本库）；
- 本目录只放**摘要与指针**：`runs/<YYYY-MM-DD>/run.md`，内容为
  `project_id / problem_id / run_id / 快照目录 / 场景 / 预算 / 花费`。
- 失败登记追加到 `failures.md`（只追加，不删除；判"非缺陷"必须写理由）。

## 3. 领域词表自检（可复制执行）

```powershell
.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,r'.');from src.rag.relevance_filter import resolve_domain,load_terms;
t=resolve_domain('钙钛矿太阳能电池湿度稳定性');print('domain=',t.domain);print('in_domain=',len(t.in_domain));print('strong=',len(t.strong_in_domain));print('confusables=',len(t.off_domain_confusables))"
```

期望：`domain=perovskite-stability`，三个计数均非 0。若返回空词表，说明主题文本
未匹配到本用例词表，检索相关性过滤将退化为"不做领域假设"。

检索式静态自检（只规划，不访问外部文献库）：

```powershell
.venv\Scripts\python.exe -c "from src.research.query_planner import plan_queries; print([(q.angle, q.text) for q in plan_queries(goal='研究钙钛矿太阳能电池的湿度稳定性')])"
```

应分别出现中文术语、`perovskite solar cell`、带领域上下文的 `PSC`，并将
`perovskite photovoltaic` 作为独立备选查询。实际运行仍需检查
`RetrievalCoverage.queries`，以及外部来源清单的相关性、年份、被引数和全文可得性；
较新或高被引只决定候选优先级，不证明其支持本题结论。

## 4. 运行方式

```powershell
# CLI
# 将「资料源ID」替换为实际已入库的 source_set_id
.venv\Scripts\python.exe -m src.main --request "结合我选定的资料库，说明湿度如何导致钙钛矿太阳能电池性能衰减，并给出理论结论与可区分竞争解释的仿真建议。" `
  --project-id transfer-eval --problem-id p1 --source-set-id "资料源ID" --source-policy user_kb

# 或以编程方式 (本次评估所用)
# run_team_session(输入请求, project_id=..., problem_id="problem", source_policy="both")
```

## 5. 产物清单（送审）

与 `rubric.md` §0 一致：`manifest.json`、`research_spec.json`、`claims.json`、
`manuscript.md`/`.tex`/`.pdf`、`evidence.json` + `evidence_links.json`、
`obligations.json` + `verification/`、`novelty_review.md`、`unresolved.md`、
`writing_gaps.json`、`experiment_specs/`。

## 6. 反向检查（必做）

与 `rf-fingerprint` 产物对照：

- 检索产出中不得复用射频专属术语（`射频`/`指纹`/`信道`/`specific emitter` 等）；
- 正文中不得复用组合设计与数学类措辞（`组合设计`/`区组`/`射影平面`/`(v,k,λ)` 等）；
- 数值与单位必须来自本用例 `domain_terms.md`（RH/°C/小时/PCE），不得出现射频单位（dBm/Hz/IQ）。

任一命中即记入 `failures.md`。
