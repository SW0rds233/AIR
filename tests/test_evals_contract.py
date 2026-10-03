from __future__ import annotations

"""领域验收材料的交付契约 (计划书 §3 / §5 门槛 6)。

计划书要求 `evals/cases` 保存首个领域的真实资料与已知解/反例, `evals/rubric.md`
保存领域人审表。这两类**人工**材料无法由自动化测试代替, 但它们的存在性与结构
可以检查 —— 否则"门槛 6 未完成"会一直只是一句口头声明。

反向安全测试:
- 送审材料的最低面必须齐 (身份、规格、论证链、验证、证据、对照、建议、未决、用量);
- 判定维度必须覆盖计划书点名的能力点 (模型忠实度、引用、差异、建议判据、条件性交付);
- 用例必须声明"评审前不得提供的材料" —— 否则无法检验系统的判断力;
- 封存答案不得混进被研究的问题描述。

I-1 脚手架补充 (下一轮执行计划 I-1 P0-1):
- 盲测运行手册 (`runbook.md`) 必须覆盖两种资料场景 (绑定资料库 / 授权自主检索) 的
  CLI+Web 等价路径、运行身份命名、归档清单、`runs/` 归档约定与运行前自检;
- 失败案例台账 (`failures.md`) 的列名必须与 `rubric.md` §3 逐字一致, 且写明
  "判定为非缺陷必须写理由""历史记录只追加不删除";
- 领域术语表 (`domain_terms.md`) 必须是留给领域专家的**空白**模板, 不得被运行时模型代填;
- 第二个任务类型 (`transfer`) 必须存在且把领域留成待人工选定 —— 防射频关键词硬编码;
- 防泄漏: 封存答案里已填写的结论不得出现在 runbook / case.md 的输入模板里。
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EVALS = REPO_ROOT / "evals"

RUBRIC_DIMENSIONS = ("研究意图", "资料与来源", "模型忠实度", "论证与验证",
                     "引用与差异", "仿真/实验建议", "写作缺口回流", "交付诚实度")

REQUIRED_MATERIALS = ("manifest.json", "research_spec.json", "claims.json",
                      "manuscript.md", "evidence_links.json", "novelty_review.md",
                      "experiment_specs", "unresolved.md", "delivery_gate.md")

RF_CASE = EVALS / "cases" / "rf-fingerprint"
RUNBOOK = RF_CASE / "runbook.md"
FAILURE_LEDGER = RF_CASE / "failures.md"
DOMAIN_TERMS = RF_CASE / "domain_terms.md"
SEALED_NOTES = RF_CASE / "expected_notes.md"
TRANSFER_CASE = EVALS / "cases" / "transfer" / "case.md"
CD_RUNBOOK = EVALS / "cases" / "combinatorial-design" / "runbook.md"
CD_FAILURE_LEDGER = EVALS / "cases" / "combinatorial-design" / "failures.md"
FRONTEND_BUILD_CHECK = REPO_ROOT / "scripts" / "frontend_needs_build.ps1"

# 计划书 §5 固定题: 输入模板的正对照 (防止"输入段落是空的"这类平凡通过)
FIXED_INPUT = "结合我选定的资料库，分析信道变化对射频指纹可分性的影响，并给出理论结论与仿真建议"

# 归档清单 (执行计划 I-1): 缺一项即材料不全
ARCHIVE_ITEMS = ("输入请求", "资料集合", "版本/hash", "检索覆盖记录", "研究日志",
                 "模型与舍弃理由", "论证链", "交付报告", "manifest.json",
                 "费用与停止原因", "封存答案")

# 失败台账的列名必须与 rubric §3 的表头逐字一致 (列名漂移会让历史记录对不上)
LEDGER_HEADER = "| 编号 | 日期 | 维度 | 现象（含文件/行位置） | 期望 | 处理 | 状态 |"
LEDGER_STATES = ("待处理", "已修复(测试名)", "判定为非缺陷(理由)")

# 术语表必须为领域专家留出的关键变量与字段 (执行计划 I-3 (d))
TERM_VARIABLES = ("信道", "噪声", "设备特征", "可分性指标")
TERM_FIELDS = ("术语", "符号", "含义", "单位", "取值范围",
               "机制关系（与其它变量的作用路径）", "来源与定位（文献+页/节）", "备注")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _blocks(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题行切块, 返回 [(标题行, 正文), ...] (标题行保留 # 前缀)。"""
    blocks: list[list] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            blocks.append([line.strip(), []])
        else:
            if not blocks:
                blocks.append(["", []])
            blocks[-1][1].append(line)
    return [(head, "\n".join(body)) for head, body in blocks]


def _block(text: str, *keywords: str) -> str:
    """取标题中同时含全部 keywords 的那一节的正文。"""
    for head, body in _blocks(text):
        if all(keyword in head for keyword in keywords):
            return body
    raise AssertionError(f"缺少标题含 {keywords} 的小节")


def _filled_sealed_values(notes: str) -> list[str]:
    """封存答案里**已填写**的字段值 (冒号后非空且够长)。

    模板留空时不产生断言对象 —— 领域专家一旦填入结论, 防泄漏断言立刻生效。
    """
    values = []
    for line in notes.splitlines():
        match = re.match(r"^[-*]\s*[^:：]{1,40}[：:]\s*(\S.{7,})$", line.strip())
        if match:
            values.append(match.group(1).strip())
    return values


def test_rubric_exists_and_covers_all_dimensions():
    rubric = EVALS / "rubric.md"
    assert rubric.is_file(), "计划书 §5 门槛 6 的人审表必须存在"
    text = _read(rubric)
    for dimension in RUBRIC_DIMENSIONS:
        assert dimension in text, f"人审表缺少维度: {dimension}"
    # 判定口径必须是三档且要求写明理由, 不能只有"通过/不通过"
    for verdict in ("通过", "需修改", "不通过"):
        assert verdict in text, verdict
    assert "理由" in text and "签字" in text
    # 失败案例必须留痕且不得删除
    assert "失败案例" in text and "不得删除" in text
    # 必须说清"自动化测试通过不等于本表通过"
    assert "不等于" in text


def test_rubric_lists_every_required_deliverable():
    text = _read(EVALS / "rubric.md")
    for material in REQUIRED_MATERIALS:
        assert material in text, f"送审材料清单缺少 {material}"
    # 评审前必须准备的三样材料 (计划书 §5)
    for prepared in ("两篇条件不同的资料", "已知解析特例", "反例"):
        assert prepared in text, prepared


@pytest.mark.parametrize("case_id", ["rf-fingerprint"])
def test_acceptance_case_is_prepared(case_id):
    """每个领域用例都要有输入、封存答案与评审目录约定。"""
    case_dir = EVALS / "cases" / case_id
    assert case_dir.is_dir(), f"缺少用例 {case_id}"
    case_text = _read(case_dir / "case.md")
    notes = case_dir / "expected_notes.md"
    assert notes.is_file(), "已知解/反例必须事先写下来并封存"

    # 输入必须是计划书 §5 固定题的实质内容 (而不是占位)
    assert "射频指纹可分性" in case_text
    # 评审前不得提供的材料必须显式声明
    assert "不得提供给系统" in case_text or "不得交给系统" in case_text
    # 期望的能力边界与"必须拒绝的说法"都要写
    assert "必须拒绝" in case_text
    assert "条件性" in case_text or "条件性研究报告" in case_text
    # 封存答案不得只留空标题: 至少要有表格与待填项
    notes_text = _read(notes)
    assert "评审前不得交给系统" in notes_text
    assert "|" in notes_text, "封存答案需要可填写的对照表"


def test_evals_readme_documents_layout_and_rules():
    readme = EVALS / "README.md"
    assert readme.is_file()
    text = _read(readme)
    assert "cases/" in text and "rubric.md" in text
    assert "不得提供给系统" in text or "不得交给系统" in text
    assert "回归锚点" in text


def test_sealed_answers_are_not_part_of_the_research_input():
    """封存答案不得出现在研究输入里 (否则用例失去意义)。"""
    notes = _read(EVALS / "cases" / "rf-fingerprint" / "expected_notes.md")
    # 封存文件本身必须仍在"待填写"状态: 没有具体数值/结论被写死
    assert not re.search(r"\d+\s*dB", notes), "封存答案里不应出现具体结论数值"
    r = re.search(r"case-id\*\*: `([^`]+)`", notes)
    assert r is None, "封存答案不应声明自己是用例定义 (定义在 case.md)"


# --------------------------------------------------------------------------
# I-1 工程脚手架: 盲测运行手册 / 失败台账 / 领域术语表 / 迁移用例 / 防泄漏
# --------------------------------------------------------------------------


def test_runbook_covers_two_source_scenarios_with_two_entrypoints():
    """两种资料场景各一条运行路径, 且每条都有 CLI 与 Web 两条等价入口。"""
    runbook = _read(RUNBOOK)
    # 场景 ①: 用户提供资料库, 绑定 source_set_id
    assert "用户提供资料库" in runbook and "source_set_id" in runbook
    # 场景 ②: 只给研究方向 + 授权自主检索
    assert "授权自主检索" in runbook
    scenarios = [body for head, body in _blocks(runbook)
                 if head.startswith("###") and "场景" in head]
    assert len(scenarios) == 2, "运行手册必须给出两种资料场景各一条运行路径"
    for body in scenarios:
        assert "CLI" in body and "Web" in body, "每个场景都要有 CLI 与 Web 两条等价入口"


def test_runbook_lists_every_archive_item():
    runbook = _read(RUNBOOK)
    for item in ARCHIVE_ITEMS:
        assert item in runbook, f"归档清单缺少 {item}"
    # 封存答案只以指针形式归档, 且必须写明评审前不得进入运行时输入
    assert "评审前不得进入任何运行时输入" in runbook


def test_runbook_documents_runs_directory_and_big_file_location():
    runbook = _read(RUNBOOK)
    assert "runs/<YYYY-MM-DD>" in runbook, "归档目录约定必须给出 runs/<YYYY-MM-DD>/"
    # 大文件留在交付包目录, runs/ 只放摘要与指针
    assert "outputs/research/" in runbook
    assert "指针" in runbook


def test_runbook_has_preflight_self_check():
    preflight = _block(_read(RUNBOOK), "运行前自检")
    for item in ("封存", "入库", "预算", "身份"):
        assert item in preflight, f"运行前自检缺少 {item} 项"


def test_runbook_documents_naming_and_same_identity_rule():
    text = _read(RUNBOOK)
    naming = _block(text, "命名约定")
    for field in ("project_id", "problem_id", "run_id", "branch_id"):
        assert field in naming, f"命名约定缺少 {field} 的取值规则"
    # 同一问题必须同一身份, 且续跑沿用同一 run_id
    assert "同一问题必须同一身份" in text
    assert "沿用同一 `run_id`" in naming or "沿用同一 `run_id`" in text


def test_combinatorial_runbook_covers_preflight_and_criteria():
    """组合设计用例的运行手册必须含: 运行前自检、判据映射、失败台账与归档约定。

    (计划书 §6.1 要求的"离线冒烟 / 限额 LLM / 判据映射 / 失败记录方式"四项。)
    """
    text = _read(CD_RUNBOOK)
    preflight = _block(text, "运行前自检")
    for item in ("封存", "预算", "身份", "归档目录"):
        assert item in preflight, f"运行前自检缺少 {item} 项"
    for section in ("离线冒烟", "限额", "判据映射", "归档清单", "失败记录",
                    "runs/<YYYY-MM-DD>"):
        assert section in text, f"运行手册缺少 {section}"
    # 判据映射必须对着计划书的发布门槛 1–5 逐条给依据
    # (该节含子标题, 因此按"所有标题含关键词的块"合并取正文, 而不是只取第一块)
    mapping = "\n".join(body for head, body in _blocks(text) if "判据映射" in head)
    assert mapping.strip(), "运行手册缺少判据映射小节"
    for gate in ("1.", "2.", "3.", "4.", "5."):
        assert gate in mapping, f"判据映射缺少发布门槛 {gate}"
    failure = _read(CD_FAILURE_LEDGER)
    assert "| 编号 | 日期 | 维度 | 现象（含文件/行位置） | 期望 | 处理 | 状态 |" in failure
    for state in LEDGER_STATES:
        assert state in failure, f"失败台账状态取值缺少 {state}"
    assert "只追加不删除" in failure and "必须写理由" in failure


def test_runbook_requires_frontend_artifacts_to_match_sources():
    """Web 入口的运行前自检必须检查"前端产物与源码同版本"。

    后端不编译前端 (只从 `src/web/dist/` 提供页面), 因此产物过期或残留旧 bundle 时
    跑的是旧前端配新后端 —— 2026-10-03 的 `/favicon.svg` 与草稿身份工作台 404 就是
    这个组合造成的 (见 failures.md CD-001/CD-002)。
    """
    assert FRONTEND_BUILD_CHECK.is_file(), "缺少产物过期检测脚本"
    script = _read(FRONTEND_BUILD_CHECK)
    assert "src\\web\\dist\\index.html" in script, "检测脚本必须比对构建产物与源码时间"
    assert "src\\web\\src" in script

    preflight = _block(_read(CD_RUNBOOK), "运行前自检")
    assert "前端产物与源码同版本" in preflight, "运行前自检缺少前端产物版本项"
    section = _block(_read(CD_RUNBOOK), "前端产物与源码同版本")
    for needle in ("frontend_needs_build.ps1", "npm run build", "dist/assets",
                   "favicon.svg"):
        assert needle in section, f"前端产物检查说明缺少 {needle}"
    # 明确写出"不重启服务但必须刷新浏览器", 否则修完还会看到旧行为
    assert "刷新" in section


def _heading_level(line: str) -> int:
    """Markdown 标题的层级; 不是标题返回 0。

    必须把代码块里的 `# 注释` 排除掉 —— PowerShell 注释与 Markdown 标题同形,
    否则"取到下一个同级标题"会停在第 1 行代码注释上 (实测: 说明里的命令全被截掉)。
    """
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return 0
    marks = len(stripped) - len(stripped.lstrip("#"))
    rest = stripped[marks:]
    if not rest.startswith(" ") and rest:
        return 0                     # `#注释` 不是标题
    text = rest.strip()
    # 代码注释常见形态: "# 1) ..." / "# 2) ..." —— 数字加右括号视为注释
    match = re.match(r"^(\d+)[)、.]", text)
    if match:
        return 0
    return marks


def _section_text(text: str, *keywords: str) -> str:
    """从标题含全部 keywords 的那一节起, 取到下一个**同级或更高级**标题之前的正文。

    与 `_block` 的区别: 这里不把内容里的代码块当成终点 —— 检查"说明里有没有提到某条
    命令"时, 截断会漏掉代码块之后的内容。
    """
    lines = text.splitlines()
    start = None
    level = 0
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            continue
        heading = line.lstrip().lstrip("#").strip()
        if all(keyword in heading for keyword in keywords):
            start = index + 1
            level = _heading_level(line)
            break
    if start is None:
        raise AssertionError(f"缺少标题含 {keywords} 的小节")
    body: list[str] = []
    for line in lines[start:]:
        current = _heading_level(line)
        if current and current <= level:
            break
        body.append(line)
    return "\n".join(body)


def test_runbook_requires_no_leftover_service_before_start():
    """运行前自检必须检查"没有遗留服务占用端口"。

    实测教训 (2026-10-03): 调试时起的 8 个服务在"终止包装进程"后**仍在监听**
    (uvicorn 子进程没被杀掉, 且 log_level=warning 不报错)。换端口重跑会让请求落到
    旧代码上, 结果无法归因 —— 已修好的缺陷会被误判为仍然存在。
    """
    runbook = _read(CD_RUNBOOK)
    preflight = _block(runbook, "运行前自检")
    assert "没有遗留服务占用端口" in preflight, "运行前自检缺少遗留服务检查项"
    section = _section_text(runbook, "没有遗留服务占用端口")
    for needle in ("Get-NetTCPConnection", "OwningProcess", "Stop-Process -Name python",
                   "换端口", "收尾"):
        assert needle in section, f"遗留服务检查说明缺少 {needle}"


def test_failure_ledger_matches_rubric_section_three():
    rubric = _read(EVALS / "rubric.md")
    ledger = _read(FAILURE_LEDGER)
    # 列名必须与 rubric §3 逐字一致, 否则历史记录与判定表对不上
    assert LEDGER_HEADER in rubric
    assert LEDGER_HEADER in ledger, "失败台账的列名必须与 rubric §3 一致"
    assert "| R-001 |" in ledger, "失败台账要留一行可填写的编号占位"
    for state in LEDGER_STATES:
        assert state in ledger, f"状态取值说明缺少 {state}"
    # 两条纪律: 判定为非缺陷必须写理由; 历史记录只追加不删除
    assert "判定为非缺陷" in ledger and "必须写理由" in ledger
    assert "只追加不删除" in ledger


def test_domain_terms_template_is_blank_for_experts():
    """术语表是留给领域专家的空白模板, 关键字段不得被运行时模型代填。"""
    text = _read(DOMAIN_TERMS)
    assert "不得由运行时模型代填" in text
    for variable in TERM_VARIABLES:
        block = _block(text, variable)
        for field in TERM_FIELDS:
            pattern = rf"^- {re.escape(field)}：[ \t]*$"
            assert re.search(pattern, block, re.MULTILINE), f"{variable} 缺少留空字段: {field}"
    # 关键字段全部为空 (只要有一个被填, 就说明不再是空白模板)
    filled = re.findall(
        r"^- (?:术语|符号|含义|单位|取值范围|机制关系（与其它变量的作用路径）|"
        r"来源与定位（文献\+页/节）|备注)：[ \t]*(\S.*)$", text, re.MULTILINE)
    assert not filled, f"术语表字段必须留空待专家填写, 却已填: {filled[:2]}"


def test_second_domain_case_is_a_template_for_manual_selection():
    """第二个任务类型用于检验系统是否只对射频关键词硬编码。"""
    assert TRANSFER_CASE.is_file(), "需要第二个任务类型的用例模板 (防领域关键词硬编码)"
    text = _read(TRANSFER_CASE)
    assert "case-id**: `transfer`" in text
    # 领域由人工选定, 不得预填结论
    assert re.search(r"\*\*领域\*\*: TODO", text), "迁移用例的领域必须留 TODO 占位"
    assert "由人工选定" in text and "不得预填" in text
    # 结构与 rf-fingerprint 的 case.md 对齐
    for section in ("## 1. 输入", "评审前必须人工准备的材料", "期望的能力边界",
                    "必须拒绝", "条件性", "判定方式", "TODO"):
        assert section in text, f"迁移用例缺少 {section}"
    assert "不得提供给系统" in text or "不得交给系统" in text


def test_sealed_answers_do_not_leak_into_input_templates():
    """封存答案的关键结论不得出现在 runbook / case.md 的输入模板里。"""
    notes = _read(SEALED_NOTES)
    assert "评审前不得交给系统" in notes, "封存答案必须显式声明评审前不得交给系统"

    runbook = _read(RUNBOOK)
    runbook_input = _block(runbook, "输入请求")
    case_input = _block(_read(RF_CASE / "case.md"), "输入")
    # 正对照: 输入段落确实是固定题, 而不是空段落 (否则下面的断言会平凡通过)
    assert FIXED_INPUT in runbook_input
    assert FIXED_INPUT in case_input

    # 封存答案里一旦填入结论/数值, 就不得出现在任何输入模板中
    for value in _filled_sealed_values(notes):
        assert value not in runbook_input, f"封存结论泄漏进 runbook 输入段落: {value}"
        assert value not in case_input, f"封存结论泄漏进 case.md 输入段落: {value}"

    # 封存答案里点名的"过强说法"不得成为输入模板的一部分
    for hint in ("必然降低可分性",):
        assert hint not in runbook_input, f"答案关键句泄漏进 runbook 输入段落: {hint}"
        assert hint not in case_input, f"答案关键句泄漏进 case.md 输入段落: {hint}"

    # 运行手册必须写明封存答案不进运行时输入
    assert "封存" in runbook and "运行时输入" in runbook

