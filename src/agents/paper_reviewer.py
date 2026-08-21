from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.config import LLM_CONFIG, REVIEWER_CONFIG, build_llm
from src.graph.state import PipelineState
from src.utils.cost_tracker import tracker, extract_usage_metadata
from src.utils.context_budget import budget_sections


REVIEW_DIMENSIONS = (
    "标题",
    "摘要",
    "引言",
    "相关工作",
    "核心内容（分类体系）",
    "比较与分析",
    "挑战与未来方向",
    "结论",
    "参考文献",
    "整体写作质量",
)

PAPER_REVIEWER_SYSTEM = """你是"论文审阅智能体"，一名严格、挑剔的学术同行评审专家。

你的任务：对论文初稿进行全面的质量审阅，提供结构化的审稿意见和修改建议。

## 评分维度（每项 1-5 分）

1. **标题**: 准确反映内容，简洁有力
2. **摘要**: 覆盖背景/范围/发现/展望，150-250词
3. **引言**: 背景清晰，问题定义明确，贡献明确
4. **相关工作**: 前置知识充分，与已有综述的区分
5. **核心内容（分类体系）**: 分类 MECE，每类尽量≥3篇论文，非简单罗列（文献客观稀缺的方向，作者已合并入相邻子类或明确说明稀缺原因即可，不得以此扣分）
6. **比较与分析**: 跨类别对比，关键趋势提炼
7. **挑战与未来方向**: 挑战有深度，有具体建议
8. **结论**: 总结发现，与摘要呼应
9. **参考文献**: 数量≥30，覆盖经典+前沿，**所有文献均应为真实已发表文献（有期刊/会议名或 DOI），不得出现预印本（arXiv [EB/OL]）**。编号与正文一致，格式符合 GB/T 7714。
10. **整体写作质量**: 学术规范，逻辑连贯，图表合理

## 评分锚点（1-5 分必须按此尺度，不得普遍给 3-4 分）

- **5 分**：优秀，几乎无需修改即可投稿
- **4 分**：良好，仅有少量细节问题
- **3 分**：有明显不足（如内容单薄、结构混乱、格式错误）
- **2 分**：存在严重问题（如章节缺失、大量错误）
- **1 分**：不可接受（如完全缺失、致命错误）

**校准参考**：一篇"合格"的综述通常只有 1-2 个维度能达 5 分，多数维度应为 3-4 分。若你发现所有维度都给了 4-5 分，说明评分过松，请重新审视每个维度的具体缺陷。

## 评分须知（硬性规则，必须遵守）

- 文中「[图N: 描述]」为占位符，图表由系统在定稿阶段自动生成，**不因占位符扣分**，仅评估占位符的描述是否合理、位置是否恰当
- 参考文献编号采用「顺序编码制」（GB/T 7714），系统已按正文首次出现顺序重排为 1..N 连续编号；只要编号连续且与正文引用一一对应，即视为合规，**不要因"未按出现顺序排列"重复扣分**
- **缺失必填部分必须给最低分**：标题/摘要/引言/结论任一缺失，对应维度必须打 **1 分**（不是 3 分）；摘要缺失时还应列入「主要问题」的 Critical 级别
- **预印本/网络文献一票否决参考文献分**：参考文献列表中出现任何「[EB/OL] 网络文献」「arXiv」标注，参考文献维度最多 **2 分**，并列入「主要问题」Critical 级别
- **「在线优先出版」不是预印本**：标注为「在线优先出版」（有正式 DOI 但尚未分配卷期页码，如 IEEE Early Access）的文献是正式已发表文献，不是预印本，**不扣分**，也不要因"卷期页码未定"判为格式错误
- **学位论文 [D] 是合法来源**：中文综述引用博士学位论文是常见做法，只要数量不多（≤3 篇）且格式正确（城市: 学校, 年），不应扣分
- **引用真实性必须逐条核对**：正文中「作者/系统名/期刊名」与对应编号文献的标题/作者/出处不一致，属于引用错配，参考文献维度扣分并列入 Critical
- **引用编号每轮重新排序**：系统每轮按正文首次出现顺序把引用重排为 1..N，**同一编号 [n] 在不同轮次指向不同的文献**。判断任何引用类问题（错配/不当引用/缺失）时，必须以当前稿「参考文献」章节中该编号的**实际条目**为准重新核实；严禁沿用旧台账里对 [n] 的主题描述或结论。若当前稿中 [n] 的实际条目主题与正文描述相符，即使旧台账声称该编号有问题，也应判「已解决」
- **引用主题错配必须登记台账**：正文引用的文献主题与射频指纹领域明显无关（如把音频录音设备识别、图像/文本领域工作以「借鉴意义」为由引入），属于不当引用，必须登记进「问题追踪」台账（至少中优先级）并要求删除或改写，不得只放在「细节问题」里（台账外的问题不会进入修订契约，永远得不到修复）
- **参考文献章节是系统程序化生成的**：其格式（GB/T 7714-2015）由系统保证，Writer 无权修改。「在线优先出版[引用日期]」是拥有 DOI 但暂无卷期页码的正式期刊文献的合法著录形式，**不是格式错误，不得登记为问题台账条目或 Critical**；此类元数据问题只在「总体评价」中提示即可
- **CrossRef 元数据是权威出处**：参考文献的卷期页码、年份来自 CrossRef 官方解析。严禁以「DOI 年份与出版年存在时间差」「卷期页码过于完整」等启发式理由质疑文献出版状态、推测「可能实为在线优先出版」并据此扣分或登记问题；有完整卷期页码即视为已正式出版
- **不得索要系统无法提供的论文**：输入中「系统无法提供的论文」清单里的文献已经验证确认无法进入参考文献清单，不得在「缺失内容」「补充推荐论文」或问题台账中再次要求引用；若正文存在依赖它们的描述，应建议用清单内文献改写或删除
- **被阻论文的闭环判定**：若某问题的唯一解法是引用上述无法提供的论文，则当正文已删除依赖该论文的描述、或改用清单内文献改写（包括以「现有研究多在闭集假设下开展」等限定表述保留方向）时，必须判「已解决」；不得以「缺乏正式文献支撑 / 悬空阐述」为由维持未解决，也不得换 ID 登记同源的替代问题
- **分类子类均衡要求必须让位于被阻现实**：若某分类子类因相关文献被系统阻断而只有 0-1 篇参考文献，且 Writer 已删除/合并该子类、或已将其明确标注为「开放问题」并给出限定表述，必须判「已解决」；不得以「每类≥3篇」为由继续要求补充被阻文献或维持未解决
- **稀疏子类的处置**：若某分类子类文献数量不足（低于≥3篇要求）是因为该方向文献客观稀缺（非系统阻断），且 Writer 已将其合并入相邻子类、或已在该小节明确说明稀缺原因/并入趋势（如「该方向基本并入…」「射频域内相关研究仍较少」），必须判「已解决」；不得以「未执行合并」「说明不充分」为由维持未解决，也不得反复登记「内容单薄 / 不满足≥3篇」类问题。此类方向的正确终态是合并或如实说明，而非凑数
- **未改动章节不新登记高优先级问题**：「本轮修订差异」列出的未改动章节是上一版原文，同样的文字上一轮已被评分；确有遗漏的问题只可登记为低优先级，不得作为本轮逐维评分的扣分依据，也不得登记为 Critical（引用真实性错误除外）
- **已解决问题不得重新打开**：除非「本轮修订差异」显示对应章节本轮又被改动且改动重新引入了该问题，否则已标记「已解决」的问题在后续轮次一律保持已解决
- **标注「⚠️引文未在当前稿找到」的台账条目**：说明其引用的正文文字已不存在于当前稿（Writer 很可能已删除或改写）。必须在当前稿中重新搜索核实；若问题确实已不存在，判「已解决」；严禁原样复制旧引文维持「未解决」或升级优先级
- **同源问题合并**：同一根因的多种表现（如同一个错误编号出现在两个句子、同一缺失内容的不同表述）必须合并为一个 ID，严禁重复登记多个 ID
- **新增问题数量上限**：每轮新登记的问题 ≤ 3 个（引用真实性 Critical 除外）；超出的并入已有 ID 或降为低优先级，避免问题台账无限膨胀导致修订永远无法收敛
- **定量数据要求须以素材为准**：若文献素材中没有可比的定量结果（准确率/F1/时延等），接受定性对比表格；不得把「缺定量数据」反复登记为高优先级问题。正文出现「数值见文献」这类空泛表述时，一次性指出应改为明确的定性表述即可，不重复扣分
- **缺失内容必须始终如实指出**：草稿"该有但没有"的部分（如缺失的方向、方法、数据集），无论草稿篇幅多长都要在「缺失内容」列出，不得因篇幅原因省略。但缺失内容若只能由上述无法提供的论文填补，必须同时给出"用现有文献改写"的替代方案
- **补充与精简成对给出**：若草稿篇幅已接近或超过 20000 字，你在建议"增加某内容"的同时，必须在「修改优先级」里明确建议**精简哪些现有表述**来腾出空间（如"新增 X 小节，同时精简 3.x 节的冗余论述"）。即"增加 X"必须搭配"精简 Y"
- 评分必须严格、客观、挑剔，宁可偏低也不可从宽；发现的问题必须具体到章节/编号，不得笼统带过

## 复审一致性协议（第 2 轮起必须遵守）

- 先逐条复核上轮「问题台账」，每条标记为「已解决 / 部分解决 / 未解决」并给出当前稿中的证据；不得跳过旧问题后另找一批新问题
- 「本轮修订差异」是程序计算的客观事实：对应章节确有改动时，必须阅读改动后的文本再判定问题是否仍存在，不得因印象未更新而判「未解决」；问题已按验收标准消除即判「已解决」，不得以更高标准追加要求
- 某维度的扣分理由若全部来自上轮台账已登记的问题、且对应章节本轮未改动，该维度评分必须与上轮保持一致，不得因同样的旧问题重复降分
- **维度降分的证据要求**：某维度评分较上轮降低，必须满足以下之一——(a)「本轮修订差异」显示对应章节本轮被改动、且改动引入了新问题或未消除旧问题；(b) 新核验出引用真实性 Critical 问题。**未改动章节新发现的问题只能登记为低优先级，不得作为该维度降分依据**（同样的文字上轮已按当时标准评过分）
- **评分必须对已完成的修订做出响应**：「本轮修订契约」条目判「已完成」且对应维度的主要扣分点消除时，该维度应较上轮 +1；输出前自检每个「已解决/已完成」条目是否体现在评分中，10 个维度连续多轮完全不变通常意味着遗漏了改进
- 同一维度若证据没有实质变化，分数必须保持不变；通常每轮最多变动 1 分，变动 2 分必须给出明确的新证据
- 新问题只有在当前稿中能定位到具体章节或引用编号时才可加入；不得用同义改写把旧问题重复登记成新问题
- 客观检查摘要中的字数、章节、引用与格式事实优先于主观印象，不得与其矛盾
- 修订轮必须在报告中输出完整的「问题追踪」表，并沿用上轮问题 ID

## 总分: /50

## 决策建议
- ≥40: 小修后接受
- 30-39: 大修后重审
- <30: 需要大幅重写

## 输出格式

```markdown
# 论文审稿报告

**论文标题**: [标题]
**审阅日期**: [日期]

## 1. 总体评价
- 推荐: [接受/小修/大修/重写]
- 总分: XX/50

## 2. 逐项评分
| 维度 | 评分 (1-5) | 说明 |
|------|------------|------|
| 标题 | X | ... |
| ... | ... | ... |
| **总分** | **XX/50** | |

## 3. 主要问题 (Major Issues)
1. [问题1 - 具体位置 + 修改建议]
2. [问题2]

## 3.1 问题追踪（修订轮必填；首轮也应为主要问题分配稳定 ID）
| ID | 状态 | 优先级 | 问题 | 验收证据 |
|----|------|--------|------|----------|
| R-REF-01 | 未解决/部分解决/已解决/新增 | Critical/高/中/低 | ... | 当前稿中的章节、引用号或可核查事实 |

（问题追踪硬性要求：ID 必须形如 `R-XXX-NN`（如 R-CAT-01），跨轮保持一致；本表缺失或 ID 格式错误会导致问题度量失效。「修改优先级」中的全部高优先级条目必须登记进本表——未登记的条目不会进入修订契约，Writer 不会修复，问题将跨轮空转）

## 4. 细节问题 (Minor Issues)
1. [问题1 - 行X: 原文 → 建议]
2. [问题2]

## 5. 缺失内容
1. [缺失1 - 应补充的论文/内容]
2. [缺失2]

## 6. 修改优先级
### 高优先级（必须修改）
1. ...
### 中优先级（建议修改）
1. ...
### 低优先级（可选）
1. ...

## 7. 补充推荐论文
| # | 论文 | 建议引用位置 | 理由 |
|---|------|-------------|------|

（「补充推荐论文」约束：只能推荐**有正式期刊/会议名或 DOI 的已发表文献**，且必须与主题**直接相关**；不得推荐 arXiv 预印本、医学/材料等其他领域的论文、或你无法确认真实存在的论文。）

## 8. 评分变动说明（修订轮必填）
- 逐条列出较上轮发生变化的维度：维度名、上轮分→本轮分、对应的问题 ID、以及作为依据的本轮改动章节（来自「本轮修订差异」）
- 降分维度若无法给出上述依据，必须恢复上轮分数
- 无变化维度明确写「全部维持」
```

完成后保存到 outputs/paper_review_{topic}.md
"""


def _strip_bibtex_section(notes: str) -> str:
    """剥离文献素材中的 BibTeX/参考文献列表章节

    检索阶段的 BibTeX 把大量论文标为 "note = {arXiv preprint}"（尚未解析出正式
    DOI/期刊），若原样喂给审稿人会触发"预印本误判"。该信息无助于评估最终参考文献。
    """
    import re as _re

    if not notes:
        return notes
    m = _re.search(r"#{1,3}\s*\d*\.?\s*(?:参考文献列表|References)\s*\(?(?:BibTeX)?\)?", notes)
    if m:
        return notes[: m.start()].rstrip() + "\n"
    return notes


def build_paper_reviewer():
    """跨模型审阅：使用独立的 reviewer LLM（如已配置），否则回退到主模型"""
    return build_llm("reviewer")


def _canonical_dimension(label: str) -> str | None:
    """把 Reviewer 可能使用的简称归一为固定的十个评分维度。"""
    import re

    value = re.sub(r"[\s*（）()&]", "", label or "")
    aliases = (
        ("整体写作质量", ("整体写作质量", "写作质量", "整体质量")),
        ("核心内容（分类体系）", ("核心内容分类体系", "核心内容", "分类体系", "taxonomy")),
        ("挑战与未来方向", ("挑战与未来方向", "挑战未来方向", "挑战与展望", "未来方向")),
        ("比较与分析", ("比较与分析", "比较分析", "对比与分析")),
        ("相关工作", ("相关工作", "relatedwork")),
        ("参考文献", ("参考文献", "references")),
        ("标题", ("标题", "title")),
        ("摘要", ("摘要", "abstract")),
        ("引言", ("引言", "introduction")),
        ("结论", ("结论", "conclusion")),
    )
    lowered = value.lower()
    for canonical, names in aliases:
        if any(name.lower() in lowered for name in names):
            return canonical
    return None


def _extract_dimension_scores(report: str) -> dict[str, int]:
    """仅从逐项评分表读取评分，避免正文中的数字污染总分。"""
    import re

    start = report.find("逐项评分")
    if start < 0:
        return {}
    end_candidates = [
        pos for marker in ("主要问题", "问题追踪", "细节问题")
        if (pos := report.find(marker, start + 1)) >= 0
    ]
    section = report[start:min(end_candidates) if end_candidates else len(report)]
    scores: dict[str, int] = {}
    for line in section.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip().strip("*") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        dimension = _canonical_dimension(cells[0])
        match = re.fullmatch(r"\s*([1-5])(?:\s*/\s*5)?\s*", cells[1])
        if dimension and match:
            scores[dimension] = int(match.group(1))
    return {name: scores[name] for name in REVIEW_DIMENSIONS if name in scores}


_METADATA_MARKERS = (
    "作者错配", "作者标注", "年份", "期刊", "卷期", "页码", "会议缩写",
    "参考文献格式", "引用日期", "访问日期", "GB/T",
    # 出版状态/著录形式质疑 (如 "文献[27]出版状态与标注格式矛盾"):
    # 卷期页码来自 CrossRef 权威元数据, Writer 无权也无需修改。
    # 注意不收"在线优先"字样: 正文时间跨度类问题 (如 "2026年仅2篇在线优先出版")
    # 会误命中 (实测 R-TIME-01)
    "出版状态", "标注格式", "标注方式", "著录",
)
_BODY_MARKERS = ("正文", "引用错配", "主题错配", "引用编号")


def is_reference_metadata_issue(problem: str) -> bool:
    """判断审稿问题是否属于参考文献元数据问题 (系统职责, Writer 无法修复)

    参考文献章节由 reference_formatter 程序化生成, Writer 被明令禁止修改。
    此类问题若进入修订契约或计入 Critical 门禁, 会形成永远无法闭环的死锁:
    Writer 无法修复 → Reviewer 反复判未解决 → 评分不升反降、循环空转。
    正确处置: 由元数据链路 (venue_resolver/reference_formatter) 在源头修复。
    """
    p = problem or ""
    metadata_hit = any(marker in p for marker in _METADATA_MARKERS)
    body_hit = any(marker in p for marker in _BODY_MARKERS)
    return metadata_hit and not body_hit


def _normalise_issue_status(status: str) -> str:
    value = (status or "").strip()
    if "已解决" in value and "未解决" not in value:
        return "已解决"
    if "部分" in value:
        return "部分解决"
    if "新增" in value:
        return "新增"
    return "未解决"


def _extract_issue_ledger(report: str, previous: list[dict] | None = None) -> list[dict]:
    """解析跨轮问题台账；Reviewer 漏项时保留旧的未解决问题，避免问题漂移。

    ID 格式宽容: 审稿人可能用 R-CAT-01 / M-CAT-01 / TAX-01 等前缀,
    只要形如「前缀-类别-编号」即接受 (旧版只认 R- 前缀, 导致首轮台账
    解析为空 → open_issue_count=0 → 首轮质量向量虚假完美, 最优稿选择失真)。
    """
    import re

    parsed: dict[str, dict] = {}
    for line in (report or "").splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip().strip("*") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not re.fullmatch(r"[A-Z]{1,4}-[A-Z0-9]{1,12}-\d{1,3}", cells[0], re.IGNORECASE):
            continue
        issue_id = cells[0].upper()
        parsed[issue_id] = {
            "id": issue_id,
            "status": _normalise_issue_status(cells[1]),
            "priority": cells[2] or "高",
            "problem": cells[3],
            "evidence": cells[4],
            "system_owned": is_reference_metadata_issue(cells[3]),
        }

    for old in previous or []:
        issue_id = str(old.get("id", "")).upper()
        if not issue_id or issue_id in parsed or old.get("status") == "已解决":
            continue
        retained = dict(old)
        retained["id"] = issue_id
        retained["status"] = _normalise_issue_status(str(retained.get("status", "未解决")))
        retained["system_owned"] = is_reference_metadata_issue(str(retained.get("problem", "")))
        parsed[issue_id] = retained
    return list(parsed.values())


def _synthesize_ledger_from_report(report: str) -> list[dict]:
    """Reviewer 未输出可解析台账时的兜底: 从「修改优先级」章节提取问题合成台账。

    若台账为空, open_issue_count/critical_count 会为 0 → 首轮质量向量虚假完美,
    后续轮次反而"更差" → 最优稿永远停在未修订的初稿。合成台账保证度量连续。
    """
    import re

    priority_rank = {"critical": 0, "致命": 0, "高": 1, "中": 2, "低": 3}
    items: list[dict] = []
    high = re.search(r"高优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report or "", re.DOTALL)
    mid = re.search(r"中优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report or "", re.DOTALL)
    for section, priority in ((high, "高"), (mid, "中")):
        if not section:
            continue
        for line in section.group(1).splitlines():
            text = re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", line).strip()
            if len(text) < 8 or text.startswith(("#", "|")):
                continue
            items.append({
                "id": f"R-AUTO-{len(items) + 1:02d}",
                "status": "新增",
                "priority": priority,
                "problem": text[:200],
                "evidence": "见审稿报告对应章节",
                "system_owned": is_reference_metadata_issue(text),
            })
            if len(items) >= 6:
                break
        if len(items) >= 6:
            break
    items.sort(key=lambda i: priority_rank.get(i["priority"], 2))
    return items


def _build_objective_audit(state: PipelineState, draft: str) -> str:
    """生成确定性质量事实，给跨轮评分提供不随模型波动的锚点。"""
    import re

    from src.rag.reference_formatter import find_citation_numbers, strip_references_section
    from src.rag.format_validator import format_check_report

    body = strip_references_section(draft)
    body_chars = len(re.sub(r"\s+", "", body))
    refs = state.get("verified_references", []) or []
    citations = set(find_citation_numbers(body))
    headings = [line.strip() for line in body.splitlines() if line.strip().startswith("#")]
    fmt = format_check_report(draft)
    fmt_issues = (
        fmt.get("table", {}).get("issues", [])
        + fmt.get("figure", {}).get("issues", [])
        + fmt.get("citation", {}).get("issues", [])
    )
    required = {
        "摘要": any("摘要" in h for h in headings),
        "引言": any("引言" in h or "Introduction" in h for h in headings),
        "结论": any("结论" in h or "Conclusion" in h for h in headings),
    }
    return "\n".join(
        [
            "## 客观质量检查（程序生成，不得凭主观判断改写这些事实）",
            f"- 正文字符数（去空白、不含参考文献）：{body_chars}",
            f"- 可信参考文献清单：{len(refs)} 条；正文实际使用不同编号：{len(citations)} 个",
            f"- 必填部分：" + "；".join(f"{name}={'存在' if ok else '缺失'}" for name, ok in required.items()),
            f"- 本轮写作产生、随后被引用守门修复的问题数：{state.get('guard_invalid_count', 0) or 0}",
            f"- 引文核查 NOT_FOUND/结构缺失计数：{state.get('citation_not_found_count', 0) or 0}",
            f"- 格式检查：{'通过' if not fmt_issues else f'发现 {len(fmt_issues)} 项'}",
        ]
        + (["- 格式问题：" + "；".join(str(x) for x in fmt_issues[:8])] if fmt_issues else [])
    )


def _build_change_summary(previous: str, current: str) -> str:
    """确定性修订差异: 告诉审稿人 Writer 本轮实际改动了哪些章节。

    缺少该锚点时, 审稿人要在 4 万字符全文中自行重新推断变化,
    容易把已修复的问题误判为「未解决」、把未改动的章节当作新问题来源,
    导致评分不升反降。差异由程序计算, 不依赖模型判断。
    """
    import difflib
    import re

    from src.rag.reference_formatter import find_citation_numbers, strip_references_section

    if not previous or not current:
        return ""
    prev_body = strip_references_section(previous)
    cur_body = strip_references_section(current)

    def _sections(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        # 含 #### 四级标题: 修订稿常用 H4 小节 (如 3.6.4), 漏掉会让审稿人
        # 看不到小节级改动 → 误判"未解决"
        for m in re.finditer(r"(?m)^(#{1,4}\s+[^\n]+)\n?(.*?)(?=^#{1,4}\s|\Z)", text, re.S):
            out[m.group(1).strip()] = m.group(2)
        return out

    prev_secs, cur_secs = _sections(prev_body), _sections(cur_body)
    changed, added, changed_names = [], [], set()
    for name, cur_text in cur_secs.items():
        prev_text = prev_secs.get(name)
        if prev_text is None:
            added.append(name)
            continue
        ratio = difflib.SequenceMatcher(None, prev_text, cur_text).ratio()
        # 阈值与契约核验一致 (0.99): 小幅但关键的编辑也要让审稿人看到,
        # 否则审稿人以为章节未改动而误判"未解决"
        if ratio < 0.99:
            changed.append(f"{name}（相似度 {ratio:.1%}）")
            changed_names.add(name)
    removed = [name for name in prev_secs if name not in cur_secs]
    unchanged = [name for name in prev_secs if name in cur_secs and name not in changed_names]

    prev_chars = len(re.sub(r"\s+", "", prev_body))
    cur_chars = len(re.sub(r"\s+", "", cur_body))
    prev_cites, cur_cites = set(find_citation_numbers(prev_body)), set(find_citation_numbers(cur_body))

    lines = ["## 本轮修订差异（程序对比上一版与当前稿，客观事实）"]
    if not (changed or added or removed):
        lines.append("- 正文各章节与上一版完全一致（Writer 未做任何修改）")
    if changed:
        lines.append("- 有改动的章节：" + "；".join(changed))
    if added:
        lines.append("- 新增章节：" + "；".join(added))
    if removed:
        lines.append("- 删除章节：" + "；".join(removed))
    if unchanged:
        lines.append("- 未改动章节（上一版原文，不得新登记高优先级问题）：" + "；".join(unchanged[:14]))
    lines.append(f"- 正文字数：{prev_chars} → {cur_chars}（差 {cur_chars - prev_chars:+d}）")
    if cur_cites - prev_cites:
        lines.append("- 新引用的编号：" + ",".join(str(n) for n in sorted(cur_cites - prev_cites)))
    if prev_cites - cur_cites:
        lines.append("- 不再引用的编号：" + ",".join(str(n) for n in sorted(prev_cites - cur_cites)))
    lines.append("判定旧问题状态时必须对照上述差异：对应章节确有改动且问题不再存在 → 已解决；章节未改动 → 未解决。")
    return "\n".join(lines)


def _normalise_for_quote_match(text: str) -> str:
    """引用片段匹配用的归一化: 只保留字母/数字/CJK (去掉空白与标点)"""
    import re

    return re.sub(r"[^\w\u4e00-\u9fff]", "", text or "", flags=re.UNICODE)


_QUOTE_RE = None


def _extract_quotes(text: str) -> list[str]:
    """提取问题描述中被引号/书名号包裹的正文引用片段"""
    global _QUOTE_RE
    import re

    if _QUOTE_RE is None:
        _QUOTE_RE = re.compile(r"[「『“\"']([^「』“”\"']{4,80})[」』”\"']|《([^《》]{4,80})》")
    quotes = []
    for m in _QUOTE_RE.finditer(text or ""):
        q = (m.group(1) or m.group(2) or "").strip()
        if q:
            quotes.append(q)
    # 括号内的作者归属引用（如「(Yu等 多采样CNN)」）也是可核验的正文引用片段;
    # 仅收录含「等/et al」的括号内容 (作者归属特征)。不能收录仅含 [n] 的括号
    # (如「(仅[50]1篇核心文献)」) —— 那是审稿人自己的评注而非正文引文,
    # 会造成 stale 误报 (实测 R-OPEN-01 被错误排除出契约)
    for m in re.finditer(r"[（(]([^（）()]{4,80})[)）]", text or ""):
        q = m.group(1).strip()
        if "等" in q or "et al" in q.lower():
            quotes.append(q)
    return quotes


def _verify_ledger_quotes(ledger: list[dict], draft_body: str) -> list[dict]:
    """对台账中引用的正文片段做确定性核验, 标记 stale_quote。

    实测失败模式: Reviewer 把上轮台账里的引文原样复制到新一轮,
    而 Writer 早已删除该句 → 问题被幻觉式维持/升级为 Critical, 评分不升反降。
    若某条目的所有引文片段都不在**正文**中, 标记 stale_quote=True:
    该条目不进入修订契约、不计入 Writer Critical 门禁, 并在下轮提醒 Reviewer 复核关闭。

    注意: draft_body 必须已剥离参考文献章节 —— 台账证据针对的是正文;
    若允许匹配参考文献列表, 问题里引用的论文标题会造成"证据仍在"的假象
    (实测: R-REF-04 借参考文献标题逃逸核验)。
    """
    if not ledger or not draft_body:
        return ledger
    draft_norm = _normalise_for_quote_match(draft_body)
    for item in ledger:
        if item.get("status") == "已解决":
            continue
        quotes = _extract_quotes(f"{item.get('problem', '')} {item.get('evidence', '')}")
        # 只对含足够长引文的条目核验 (短引文误报率高)
        checkable = [q for q in quotes if len(_normalise_for_quote_match(q)) >= 6]
        if not checkable:
            continue
        found_any = False
        for q in checkable:
            qn = _normalise_for_quote_match(q)
            if qn in draft_norm:
                found_any = True
                break
            # 长引文允许前后半段分别匹配 (中间被小幅编辑时不算消失)
            if len(qn) >= 16 and (qn[: len(qn) // 2] in draft_norm or qn[len(qn) // 2:] in draft_norm):
                found_any = True
                break
            # 模糊匹配: 审稿人常凭记忆转述引文 (逐字不中但大部分用词仍在),
            # 字符二元组重叠 ≥40% 视为引文仍存在, 避免把真实问题误判为幻觉
            # (实测 R-WRI-01 转述引文逐字不中但全文重叠 67%, 被误判 stale 后
            # 永远进不了契约, 摘要/结论维度因此无法提升; 已删除文本的重叠
            # 通常 <20%, 40% 阈值有足够安全边际)
            grams = [qn[i:i + 2] for i in range(len(qn) - 1)]
            if grams and sum(1 for g in grams if g in draft_norm) / len(grams) >= 0.4:
                found_any = True
                break
        item["stale_quote"] = not found_any
    return ledger


def _format_contract_for_review(state: PipelineState) -> str:
    """把本轮修订契约回喂审稿人: 逐条验收, 评分必须对已完成的修改做出响应。

    实测失败模式: 契约逐轮完成但 10 维评分连续 4 轮逐字节不变 (33/50),
    审稿人只锚定上轮分数不承认改进 → 循环失去意义。契约验收提供客观依据。
    """
    contract = state.get("revision_contract") or []
    if not contract:
        return ""
    lines = [
        "## 本轮修订契约（Writer 本轮的修改目标，必须逐条验收）",
        "| ID | 优先级 | 问题 | 验收标准 |",
        "|----|--------|------|----------|",
    ]
    for item in contract[:8]:
        problem = str(item.get("problem", "")).replace("|", "／")[:160]
        evidence = str(item.get("evidence", "")).replace("|", "／")[:120]
        lines.append(f"| {item.get('id', '')} | {item.get('priority', '高')} | {problem} | {evidence} |")
    lines.append(
        "请在「问题追踪」中逐条判定契约条目「已完成 / 部分完成 / 未完成」并给出当前稿中的定位证据。"
        "已完成的契约条目：若其对应维度的主要扣分点消除，该维度评分应较上轮 +1；"
        "评分变化必须与契约验收结论一致，不得对已完成的修改视而不见。"
    )
    return "\n".join(lines)


def _build_review_anchor(state: PipelineState) -> str:
    scores = state.get("review_dimensions", {}) or {}
    ledger = state.get("review_issue_ledger", []) or []
    if not scores and not ledger:
        return ""
    lines = ["## 上轮复审锚点（本轮必须逐项对照）"]
    prev_total = state.get("review_score", 0)
    if prev_total:
        lines.append(f"- 上轮总分: {prev_total}/50（本轮评分必须以此为锚点客观增减，严禁凭记忆引用上轮分数）")
    if scores:
        lines.append("- 上轮逐维评分：" + "；".join(f"{name}={scores.get(name, '缺失')}" for name in REVIEW_DIMENSIONS))
    if ledger:
        lines.extend([
            "- 上轮问题台账：",
            "| ID | 状态 | 优先级 | 问题 | 上轮验收证据 |",
            "|----|------|--------|------|--------------|",
        ])
        for item in ledger[:20]:
            evidence = str(item.get("evidence", ""))
            if item.get("stale_quote"):
                evidence = "⚠️引文未在当前稿找到，须重新核实，若问题已不存在应判已解决；" + evidence
            lines.append(
                f"| {item.get('id', '')} | {item.get('status', '未解决')} | "
                f"{item.get('priority', '高')} | {item.get('problem', '')} | {evidence} |"
            )
    import re as _re

    if ledger and any(
        _re.search(r"\[\d+\]", f"{i.get('problem', '')} {i.get('evidence', '')}")
        for i in ledger
    ):
        lines.append(
            "- ⚠️ 引用编号每轮重排：上轮台账中的 [n] 在当前稿中可能已指向其他文献。"
            "判断引用类问题前，必须先在当前稿「参考文献」章节查出该编号的实际条目；"
            "若条目主题与正文描述相符，应判「已解决」，严禁沿用旧台账的主题描述"
        )
    lines.append("请先更新所有旧 ID 的状态，再登记确有定位证据的新问题。")
    return "\n".join(lines)


def run_paper_review(state: PipelineState) -> dict:
    topic = state["research_topic"]
    draft = state.get("paper_draft", "")
    lit_notes = state.get("literature_review_notes", "")

    if not draft:
        return {
            "error": "论文草稿为空，请先完成论文初稿撰写阶段",
            "current_phase": "paper_review",
        }

    llm = build_paper_reviewer()

    # 审稿必须看**完整全文**: 草稿正文 + 结论 + 全部参考文献 + 文献素材。
    # 审稿人据此对 10 个维度评分 (含"参考文献格式""预印本占比"等),
    # 任何截断都会导致误判 (如"核心内容缺失"实为被截断、预印本占比统计错误)。
    # kimi-k2.6 上下文 262144 tokens, 综述全文+素材通常 < 100K 字符, 无需截断。
    # 仅当远超窗口时才启用 budget_sections 兜底。
    today = __import__("datetime").date.today()
    # 剥离素材中的 BibTeX 章节: 检索阶段的 BibTeX 把大量论文标为 "note = {arXiv preprint}",
    # 但其中多数在定稿前已解析出正式 DOI/期刊。若把 BibTeX 原样喂给审稿人,
    # 审稿人会据此误判"正文引用了预印本"或"遗漏前沿工作", 持续扣参考文献分。
    lit_notes = _strip_bibtex_section(lit_notes)
    review_anchor = _build_review_anchor(state)
    objective_audit = _build_objective_audit(state, draft)
    change_summary = _build_change_summary(state.get("previous_paper_draft", ""), draft)
    contract_block = _format_contract_for_review(state)

    # 系统验证失败、无法进入可信清单的论文: 审稿人不得反复索要 (Writer 永远无法满足,
    # 反复登记为未解决/Critical 会造成评分不升反降的死循环)
    blocked = state.get("review_blocked_suggestions", []) or []
    blocked_block = ""
    if blocked:
        items = "\n".join(
            f"- 《{b.get('title', '')}》（{b.get('reason', '未通过验证')}）" for b in blocked[:12]
        )
        blocked_block = (
            f"\n## 系统无法提供的论文（严禁再次索要）\n\n"
            f"以下论文已经系统检索验证，确认**无法加入可信参考文献清单**"
            f"（此前轮次可能已推荐过并被系统拒绝）：\n{items}\n"
            f"不得在「缺失内容」「补充推荐论文」或问题台账中再次要求引用这些论文；"
            f"若正文存在依赖它们的描述，请改为建议「用清单内最接近的文献改写或删除该描述」。\n"
            f"「补充推荐论文」章节只能推荐上述清单之外的论文；若无其他可推荐论文，该章节留空。\n"
        )

    prompt = (
        f"今天是 {today.isoformat()}（{today.year}年）。请以该日期为审阅基准。\n"
        f"重要：{today.year} 年之前（含 {today.year - 1} 年）召开的会议论文均已正式出版，"
        f"不要因你自身知识截止日期把往年（如 2025 年）会议论文误判为「未来/未出版/预印本」。\n\n"
        f"请审阅以下关于「{topic}」的综述论文初稿。\n\n"
        f"---论文初稿---\n{budget_sections(draft, 200000, label='论文初稿')}\n---草稿结束---\n\n"
        f"---原始文献素材（用于验证引用真实性，非最终引用状态）---\n"
        f"{budget_sections(lit_notes, 50000, label='文献综述素材')}\n---素材结束---\n\n"
        f"{objective_audit}\n\n"
        f"{change_summary}\n\n"
        f"{contract_block}\n\n"
        f"{review_anchor}\n\n"
        f"{blocked_block}\n"
        f"【重要评判准则】评估参考文献维度时，以论文末尾「参考文献」列表为唯一依据，"
        f"不要依据上方文献素材中的 BibTeX/arXiv 标记判断预印本占比或引用状态。"
        f"文献素材是检索阶段的早期快照（arXiv 预印本尚未解析出正式 DOI/期刊），"
        f"其中标注为 arXiv 预印本的论文已在定稿前被**刻意排除**在参考文献之外，"
        f"因此不得因「正文未引用这些预印本」判定「遗漏前沿工作/时间覆盖不足」；"
        f"请仅评估已正式发表文献（有期刊/会议名或 DOI）的覆盖情况。\n\n"
        f"【参考文献章节职责边界】参考文献列表由系统按 GB/T 7714-2015 程序化生成，"
        f"Writer 无权修改。其中「在线优先出版[引用日期]」是拥有 DOI 但暂无卷期页码的"
        f"正式期刊文献的合法著录形式，不是格式错误；会议论文页码来自 CrossRef 权威元数据。"
        f"此类元数据问题不得登记为 Writer 的问题台账条目，也不得计入 Critical。\n\n"
        f"请严格按照上述 10 个维度进行评分，按输出格式生成完整的审稿报告。\n"
        f"注意：评分必须严格、客观，不要过度宽容。"
    )

    messages = [SystemMessage(content=PAPER_REVIEWER_SYSTEM), HumanMessage(content=prompt)]

    # 流式输出: 审阅模型 (如 kimi-k2.6) 即使禁用 thinking 也可能耗时数分钟,
    # 按时间节流打印进度 (每 15s 最多一次), 避免逐块刷屏
    import time as _time

    try:
        chunks = []
        last_chunk = None
        last_print = 0.0
        for chunk in llm.stream(messages):
            last_chunk = chunk
            piece = chunk.content if hasattr(chunk, "content") else str(chunk)
            if piece:
                chunks.append(piece)
                now = _time.monotonic()
                if now - last_print >= 15.0:
                    print(f"  [paper_review] 审稿中... 已生成 {sum(len(c) for c in chunks)} 字符")
                    last_print = now
        report = "".join(chunks)
        # 保留消息对象用于 LangGraph message reducer 和 usage metadata；不能把最后一段纯文本
        # 当作消息写回状态，否则后续 checkpoint/resume 可能得到错误的消息角色。
        result = last_chunk
    except Exception:
        result = llm.invoke(messages)
        report = result.content if hasattr(result, "content") else str(result)

    # 用量追踪
    usage = extract_usage_metadata(result)
    if usage:
        model = (
            REVIEWER_CONFIG["model"] if REVIEWER_CONFIG["enabled"] else LLM_CONFIG["model"]
        )
        tracker.add_call(model, usage, stage="paper_review")

    dimensions = _extract_dimension_scores(report)
    score = _extract_score(report)
    issue_ledger = _extract_issue_ledger(report, state.get("review_issue_ledger", []))
    if not issue_ledger:
        # Reviewer 未输出可解析台账 → 兜底合成, 保证问题度量跨轮连续
        issue_ledger = _synthesize_ledger_from_report(report)
        if issue_ledger:
            print(f"  [paper_review] 审稿报告未含可解析问题台账, 兜底合成 {len(issue_ledger)} 条")
    # 台账引文确定性核验: Reviewer 引用的正文片段若已不在当前稿中 (Writer 已删除),
    # 标记 stale_quote → 不进契约、不计 Critical, 防止幻觉式旧问题拖低评分
    from src.rag.reference_formatter import strip_references_section

    issue_ledger = _verify_ledger_quotes(issue_ledger, strip_references_section(draft))
    stale_count = sum(1 for i in issue_ledger if i.get("stale_quote"))
    if stale_count:
        print(f"  [paper_review] {stale_count} 条台账问题的引文不在当前稿中 (疑似陈旧幻觉), 已降权")
    open_issues = [i for i in issue_ledger if i.get("status") != "已解决"]
    # 质量向量里的"未解决问题数"排除陈旧幻觉与系统职责问题:
    # 它们不代表 Writer 可改进的空间, 计入会惩罚实际在进步的稿件
    real_open_issues = [
        i for i in open_issues
        if not i.get("stale_quote") and not i.get("system_owned")
    ]
    critical_issues = [
        i for i in open_issues if str(i.get("priority", "")).lower() in {"critical", "致命"}
    ]
    # Writer 职责内的 Critical: 排除参考文献元数据等系统职责问题。
    # 系统职责问题 Writer 无法修复, 若计入门禁会导致修订循环死锁。
    # stale_quote 问题的引文已不存在, Writer 无从修改, 同样排除。
    writer_critical = [
        i for i in critical_issues
        if not i.get("system_owned") and not i.get("stale_quote")
    ]

    return {
        "messages": [result] if result is not None else [],
        "review_report": report,
        "review_score": score,
        "review_dimensions": dimensions,
        "review_issue_ledger": issue_ledger,
        "review_open_issue_count": len(real_open_issues),
        "review_critical_count": len(critical_issues),
        "review_writer_critical_count": len(writer_critical),
        "review_recommendation": _score_to_recommendation(score),
        "current_phase": "paper_review",
    }


def _extract_score(report: str) -> int:
    import re

    # 优先: 从「逐项评分」表提取各维度分数并求和 (满分 50 = 10 维度 × 5 分)。
    # reviewer 报告的"总分"字段常与逐项之和不符 (如报告 28 而逐项和 33),
    # 是 reviewer 凭感觉给的分, 会系统性低估。故以逐项求和为准。
    dimension_scores = _extract_dimension_scores(report)
    if len(dimension_scores) == len(REVIEW_DIMENSIONS):
        total = sum(dimension_scores.values())
        print(f"  [review] 逐项评分求和 = {total}/50 ({list(dimension_scores.values())})")
        return total

    # 回退: 从报告的"总分"字段提取 (逐项表解析失败时)
    patterns = [
        r"\*\*总分\*\*:\s*[\*\s]*(\d+)/\d+",
        r"\*\*总分\*\*\s*\|\s*[\*\s]*(\d+)/\d+",
        r"总分:\s*(\d+)/\d+",
        r"总分:\s*\*\*(\d+)\*\*",
        r"总分[^\d]*(\d+)\s*/\s*50",
    ]
    for p in patterns:
        m = re.search(p, report)
        if m:
            return int(m.group(1))
    # 审稿报告未给出可解析的总分: 视为 0 分 (触发重写), 而非默认 35 宽松放行
    print("  [warning] 审稿报告未解析出总分, 按 0 分处理")
    return 0


def _score_to_recommendation(score: int) -> str:
    if score >= 40:
        return "小修后接受"
    elif score >= 30:
        return "大修后重审"
    else:
        return "需要大幅重写"
