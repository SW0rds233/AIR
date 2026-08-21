"""流水线逻辑测试：评分阈值换算 / 修订指令传递 / max_revisions 生效（离线测试）

运行:
    python tests/test_pipeline_logic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph.pipeline import (
    _build_revision_contract,
    _review_quality_key,
    _synchronize_figure_placeholders,
    increment_revision,
    should_continue_review,
)
from src.agents.paper_reviewer import (
    _extract_dimension_scores,
    _extract_issue_ledger,
    _extract_score,
)


def _state(score=45, rev=0, hallucinated=None, not_found=0, max_rev=3):
    return {
        "review_score": score,
        "revision_count": rev,
        "hallucinated_refs": hallucinated or [],
        "citation_not_found_count": not_found,
        "max_revisions": max_rev,
        "error": None,
    }


def test_high_score_no_revision():
    """45/50 (≈90/100) 高分且无虚构引用 → 结束"""
    assert should_continue_review(_state(score=45)) == "end"


def test_low_score_triggers_revision():
    """30/50 (≈60/100) 低分 → 修订"""
    assert should_continue_review(_state(score=30)) == "paper_writing"


def test_acceptance_boundary_matches_40_of_50():
    assert should_continue_review(_state(score=39)) == "paper_writing"
    assert should_continue_review(_state(score=40)) == "end"


def test_high_score_with_hallucination():
    """高分但有虚构引用 → 修订"""
    assert should_continue_review(_state(score=45, hallucinated=["fake"], not_found=1)) == "paper_writing"


def test_high_score_with_open_critical_issue():
    state = _state(score=45)
    state["review_critical_count"] = 1
    assert should_continue_review(state) == "paper_writing"


def test_max_revisions_respected():
    """达到 max_revisions → 结束"""
    assert should_continue_review(_state(score=30, rev=1, max_rev=2)) == "paper_writing"
    assert should_continue_review(_state(score=30, rev=2, max_rev=2)) == "end"


def test_error_ends():
    """有错误 → 结束"""
    s = _state(score=30)
    s["error"] = "boom"
    assert should_continue_review(s) == "end"


def test_increment_revision_writes_prompt():
    """修订指令包含审稿意见/引文核查/旧版论文"""
    state = {
        "research_topic": "测试",
        "review_report": "审稿意见: 引言不够清晰",
        "citation_report": {"report_md": "引文核查: 1条虚构"},
        "paper_draft": "旧版论文内容",
        "revision_count": 0,
    }
    r = increment_revision(state)
    assert r["revision_count"] == 1
    assert "revision_prompt" in r
    assert "审稿意见" in r["revision_prompt"]
    assert "引文核查" in r["revision_prompt"]
    assert "旧版论文" in r["revision_prompt"]


def test_dimension_scores_are_fixed_and_summed():
    names = ["标题", "摘要", "引言", "相关工作", "核心内容（分类体系）", "比较与分析",
             "挑战与未来方向", "结论", "参考文献", "整体写作质量"]
    rows = "\n".join(f"| {name} | {4 if i < 5 else 3} | 证据 |" for i, name in enumerate(names))
    report = f"## 2. 逐项评分\n| 维度 | 评分 (1-5) | 说明 |\n|---|---|---|\n{rows}\n## 3. 主要问题"
    scores = _extract_dimension_scores(report)
    assert list(scores) == names
    assert _extract_score(report) == 35


def test_partial_dimension_table_does_not_invent_missing_scores():
    report = "## 2. 逐项评分\n| 标题 | 4 | 好 |\n| 摘要 | 3 | 尚可 |\n## 3. 主要问题\n总分: 31/50"
    assert len(_extract_dimension_scores(report)) == 2
    assert _extract_score(report) == 31


def test_issue_ledger_retains_omitted_open_issue():
    previous = [{"id": "R-REF-01", "status": "未解决", "priority": "Critical",
                 "problem": "引用错配", "evidence": "检查[2]"}]
    report = "| R-TERM-01 | 新增 | 中 | 术语不一致 | 3.1节统一术语 |"
    ledger = _extract_issue_ledger(report, previous)
    assert {item["id"] for item in ledger} == {"R-REF-01", "R-TERM-01"}
    assert next(item for item in ledger if item["id"] == "R-REF-01")["status"] == "未解决"


def test_revision_contract_prioritises_and_caps_scope():
    state = {"review_issue_ledger": [
        {"id": "R-LOW", "status": "未解决", "priority": "低", "problem": "低优先级", "evidence": "e"},
        {"id": "R-HIGH", "status": "未解决", "priority": "高", "problem": "高优先级", "evidence": "e"},
        {"id": "R-DONE", "status": "已解决", "priority": "Critical", "problem": "已修复", "evidence": "e"},
    ]}
    contract = _build_revision_contract(state, limit=1)
    assert [item["id"] for item in contract] == ["R-HIGH"]


def test_revision_contract_excludes_reference_metadata_issues():
    """参考文献元数据是系统职责 (Writer 无权改参考文献章节), 不得进入修订契约,
    否则 Writer 永远无法验收 → 审稿反复判未解决 → 评分不升反降"""
    state = {"review_issue_ledger": [
        {"id": "R-REF", "status": "未解决", "priority": "Critical",
         "problem": "参考文献格式不规范，在线优先出版标注错误", "evidence": "参考文献[23]"},
        {"id": "R-CAT", "status": "未解决", "priority": "Critical",
         "problem": "分类体系MECE原则违反", "evidence": "3.3节仅一篇文献"},
    ]}
    contract = _build_revision_contract(state)
    assert [item["id"] for item in contract] == ["R-CAT"]


def test_format_revision_contract_marks_metadata_as_record_only():
    """退化路径提取的元数据条目仍要标注'仅记录', 防止 Writer 误改正文"""
    from src.graph.pipeline import _format_revision_contract

    rendered = _format_revision_contract([{
        "id": "R-FALLBACK-01", "status": "未解决", "priority": "高",
        "problem": "参考文献作者错配", "evidence": "参考文献[10]",
    }])
    assert "仅记录，禁止改正文" in rendered
    assert "由系统元数据链路处理" in rendered


def test_system_owned_critical_does_not_force_revision():
    """仅剩参考文献元数据类 Critical 时不得再触发修订 (Writer 无法修复, 死锁)"""
    state = _state(score=45)
    state["review_critical_count"] = 1
    state["review_writer_critical_count"] = 0
    assert should_continue_review(state) == "end"


def test_writer_critical_still_forces_revision():
    state = _state(score=45)
    state["review_writer_critical_count"] = 1
    assert should_continue_review(state) == "paper_writing"


def test_blocked_and_existing_refs_are_fed_back_to_writer():
    """审稿推荐但无法验证的论文 → 明确告知 Writer 改写/删除;
    已在清单中的 → 给出编号供直接引用 (否则'补充文献'类契约永远无法完成)"""
    import src.graph.pipeline as pl

    orig = pl._resolve_review_suggestions
    pl._resolve_review_suggestions = lambda report, topic, refs, prev_blocked=None: (
        [],
        [{"title": "Deep Open Set Identification for RF Devices", "reason": "仅为预印本"}],
        [{"title": "已在清单中的论文", "ref_number": 7}],
    )
    try:
        state = {
            "research_topic": "测试",
            "review_report": "审稿意见: 遗漏开集识别文献",
            "paper_draft": "旧版论文内容",
            "revision_count": 0,
            "verified_references": [],
        }
        r = increment_revision(state)
        assert "Deep Open Set Identification" in r["revision_prompt"]
        assert "严禁引用" in r["revision_prompt"]
        assert "[7]" in r["revision_prompt"]
        assert r["review_blocked_suggestions"][0]["title"].startswith("Deep Open Set")
        assert r["previous_paper_draft"] == "旧版论文内容"
        assert r["revision_new_ref_numbers"] == []
    finally:
        pl._resolve_review_suggestions = orig


def test_compliance_check_detects_unchanged_draft():
    """Writer 原样复述全文 (常见失败模式) → 所有契约条目判未完成"""
    from src.agents.paper_writer import check_contract_compliance

    filler = "射频指纹识别是物理层安全的重要支撑技术，近年来受到广泛关注。" * 6
    prior = (
        f"# 标题\n\n## 摘要\n{filler}\n\n## 1 引言\n{filler}\n\n"
        f"## 3 核心方法\n\n### 3.3 深度识别架构\n复值网络子类仅[38]一篇文献。\n\n"
        f"## 6 结论\n{filler}"
    )
    contract = [{"id": "R-CAT-02", "status": "未解决", "priority": "Critical",
                 "problem": "分类体系MECE违反，3.3节复值网络子类文献失衡",
                 "evidence": "3.3节仅[38]一篇"}]
    unaddressed = check_contract_compliance(prior, prior, contract)
    assert len(unaddressed) == 1

    revised = prior.replace(
        "复值网络子类仅[38]一篇文献。",
        "复值网络已并入端到端卷积网络子类，补充[38][39][40]三篇文献对比分析。",
    )
    assert check_contract_compliance(prior, revised, contract) == []


def test_compliance_requires_new_refs_cited_for_add_items():
    """'补充文献'类条目: 目标章节有改动但新增引用编号未进正文 → 仍判未完成"""
    from src.agents.paper_writer import check_contract_compliance

    filler = "射频指纹识别是物理层安全的重要支撑技术，近年来受到广泛关注。" * 6
    prior = (
        f"# 标题\n\n## 摘要\n{filler}\n\n## 5 挑战\n\n### 5.4 开放世界设备识别\n"
        f"开集识别缺乏文献支撑。\n\n## 6 结论\n{filler}"
    )
    contract = [{"id": "R-MISS-04", "status": "未解决", "priority": "高",
                 "problem": "遗漏开集识别关键文献，应补充至5.4节",
                 "evidence": "5.4节未引用"}]
    # 章节有改动但未引用新增文献 [59]
    revised = prior.replace("开集识别缺乏文献支撑。", "开集识别问题亟待解决，现有研究多在闭集假设下开展[8]。")
    unaddressed = check_contract_compliance(prior, revised, contract, new_ref_numbers=[59])
    assert len(unaddressed) == 1
    # 引用了新增文献 → 完成
    revised2 = prior.replace("开集识别缺乏文献支撑。", "Reus-Muns等人提出开集射频指纹方法[59]。")
    assert check_contract_compliance(prior, revised2, contract, new_ref_numbers=[59]) == []


def test_contract_items_with_blocked_papers_get_explicit_action():
    """涉及被阻论文的契约条目必须附上删除/改写处置方式,
    否则 Writer 保留方向但不引用 → Reviewer 判悬空 → 问题每轮复活 (死循环)"""
    from src.graph.pipeline import _augment_contract_with_blocked

    blocked = [{"title": "Deep Open Set Identification for RF Devices", "reason": "预印本"}]
    contract = [
        {"id": "R-REF-01", "priority": "高",
         "problem": "遗漏开集识别关键文献",
         "evidence": "《Deep Open Set Identification for RF Devices》未引用"},
        {"id": "R-TAB-05", "priority": "高", "problem": "表7逻辑矛盾", "evidence": "4.1节"},
    ]
    out = _augment_contract_with_blocked(contract, blocked)
    assert "严禁引用或编造编号" in out[0]["problem"]
    assert "不得悬空" in out[0]["problem"]
    # 无关条目不受影响
    assert "处置方式" not in out[1]["problem"]


def test_change_summary_lists_unchanged_sections():
    """差异摘要必须显式列出未改动章节 (审稿人据此不得对其新登记高优先级问题)"""
    from src.agents.paper_reviewer import _build_change_summary

    filler = "射频指纹识别是物理层安全的重要支撑技术。" * 5
    prev = f"# 标题\n\n## 1 引言\n{filler}\n\n## 5 挑战\n旧内容甲。\n\n## 6 结论\n{filler}"
    cur = prev.replace("旧内容甲。", "新内容乙，表述已更新。")
    summary = _build_change_summary(prev, cur)
    assert "未改动章节" in summary
    assert "1 引言" in summary.split("未改动章节")[1]
    assert "5 挑战" in summary.split("有改动的章节")[1]


def test_stale_quote_issues_are_downweighted():
    """台账引文已不在当前稿中 (疑似幻觉) → stale_quote:
    不占契约优先席位、不做强制验收; 但契约有空位时递补, 避免把
    审稿人转述的真实问题也一并排除 (实测 R-WRI-01/02 永远修不掉)"""
    from src.agents.paper_reviewer import _verify_ledger_quotes

    draft = "# 标题\n\n## 5 挑战\n\n### 5.3 开集识别\n现有研究多在闭集假设下开展[8]。\n"
    ledger = [
        {"id": "R-REF-05", "status": "未解决", "priority": "Critical",
         "problem": "3.4节两句话同时指向[51]",
         "evidence": "3.4节「Zeng等则较早探索了基于深度表示学习的端到端设备识别方法[51]」错误"},
        {"id": "R-KEEP", "status": "未解决", "priority": "高",
         "problem": "5.3节阐述不足", "evidence": "5.3节「现有研究多在闭集假设下开展」"},
        {"id": "R-NOQUOTE", "status": "未解决", "priority": "中",
         "problem": "表格过多", "evidence": "全文共7个表格"},
    ]
    out = _verify_ledger_quotes(ledger, draft)
    by_id = {i["id"]: i for i in out}
    assert by_id["R-REF-05"]["stale_quote"] is True
    assert not by_id["R-KEEP"].get("stale_quote")
    assert "stale_quote" not in by_id["R-NOQUOTE"]

    contract = _build_revision_contract({"review_issue_ledger": out})
    # fresh 条目优先, stale 条目带标记递补在最后
    assert [c["id"] for c in contract] == ["R-KEEP", "R-NOQUOTE", "R-REF-05"]
    assert contract[2].get("stale_quote") is True
    # 契约核验跳过 stale 递补条目 (未改动稿件时, 未达标名单不含 stale 条目)
    from src.agents.paper_writer import check_contract_compliance

    unaddressed = check_contract_compliance(draft, draft, contract)
    assert all(u["item"]["id"] != "R-REF-05" for u in unaddressed)


def test_paraphrased_quote_not_marked_stale():
    """审稿人凭记忆转述引文 (逐字不中但大部分用词仍在) 不得判 stale。
    实测 R-WRI-01: 转述引文逐字不中但二元组重叠 67%, 旧逻辑误判 stale
    导致摘要问题永远进不了契约"""
    from src.agents.paper_reviewer import _verify_ledger_quotes

    draft = (
        "# 标题\n\n## 摘要\n实验研究表明深度架构选择会影响识别性能。\n\n"
        "## 3 核心方法\n\n### 3.3 深度架构\n不同深度架构的性能差异显著，"
        "在多个数据集上表现出明显差别。\n"
    )
    ledger = [{"id": "R-WRI-01", "status": "未解决", "priority": "低",
               "problem": "摘要「大规模实验显示不同深度架构性能差异显著」缺乏正文数据支撑",
               "evidence": "摘要章节"}]
    out = _verify_ledger_quotes(ledger, draft)
    assert not out[0].get("stale_quote")


def test_ledger_parser_accepts_non_r_prefix_ids():
    """审稿人可能用 M-CAT-01 等前缀; 旧版只认 R- 导致首轮台账为空,
    open_issue_count=0 → 首轮质量向量虚假完美 → 最优稿永远停在未修订初稿"""
    report = (
        "## 3.1 问题追踪\n"
        "| M-CAT-01 | 新增 | 高 | 分类体系MECE完备性不足 | 当前稿表1 |\n"
        "| R-REF-02 | 新增 | 中 | 术语不一致 | 1.2节 |\n"
    )
    ledger = _extract_issue_ledger(report, None)
    assert {i["id"] for i in ledger} == {"M-CAT-01", "R-REF-02"}


def test_ledger_synthesized_when_reviewer_omits_table():
    """Reviewer 未输出台账 → 从修改优先级兜底合成, 保证问题度量跨轮连续"""
    from src.agents.paper_reviewer import _synthesize_ledger_from_report

    report = (
        "## 6. 修改优先级\n"
        "### 高优先级（必须修改）\n"
        "1. 分类体系遗漏开集识别子类，MECE完备性不足\n"
        "2. 摘要超过250词上限，需删减冗余表述\n"
        "### 中优先级（建议修改）\n"
        "3. 表7缺少定量数据支撑，建议补充或改为定性表述\n"
    )
    ledger = _synthesize_ledger_from_report(report)
    assert len(ledger) == 3
    assert ledger[0]["id"] == "R-AUTO-01"
    assert ledger[0]["priority"] == "高"


def test_time_span_issue_not_misclassified_as_metadata():
    """「2026年仅2篇在线优先出版」是正文时间表述问题 (Writer 职责),
    不得因含'在线优先出版'字样被误判为参考文献元数据问题 (实测 R-TIME-01 被错误排除)"""
    from src.agents.paper_reviewer import is_reference_metadata_issue

    assert not is_reference_metadata_issue("「2007-2026年」时间跨度夸大，2026年仅2篇在线优先出版")
    # 真正的元数据问题仍要识别
    assert is_reference_metadata_issue("参考文献格式不规范，缺引用日期")


def test_paren_annotation_with_refnum_not_treated_as_quote():
    """「(仅[50]1篇核心文献)」是审稿人评注而非正文引文, 不得用于 stale 判定
    (实测 R-OPEN-01 因此被错误标记 stale 并排除出契约)"""
    from src.agents.paper_reviewer import _verify_ledger_quotes

    draft = "# 标题\n\n#### 3.4.4 开集识别\n正式发表的方法学文献十分有限[50]。\n"
    ledger = [{"id": "R-OPEN-01", "status": "未解决", "priority": "中",
               "problem": "3.4.4节开集识别文献不足（仅[50]1篇核心文献）",
               "evidence": "当前稿3.4.4节；需补充或调整分类"}]
    out = _verify_ledger_quotes(ledger, draft)
    assert not out[0].get("stale_quote")


def test_compliance_check_finds_h4_subsections():
    """修订稿用 #### 四级小节 (如 3.6.4) 时, 契约核验必须能定位到小节改动
    (实测 R-FED-01: Writer 已改 3.6.4 但旧版只切到 ### → 误判未完成)"""
    from src.agents.paper_writer import check_contract_compliance

    filler = "射频指纹识别是物理层安全的重要支撑技术。" * 8
    prior = (
        f"# 标题\n\n## 摘要\n{filler}\n\n## 3 核心方法\n\n### 3.6 系统层\n\n"
        f"#### 3.6.4 联邦学习\n[61]被归类为联邦学习框架。\n\n## 6 结论\n{filler}"
    )
    contract = [{"id": "R-FED-01", "status": "未解决", "priority": "低",
                 "problem": "3.6.4节[61]归类联邦学习牵强，实为边缘推理框架",
                 "evidence": "3.6.4节表6"}]
    revised = prior.replace(
        "[61]被归类为联邦学习框架。",
        "[61]明确标注为边缘射频指纹识别框架，归类为边缘计算部署。",
    )
    assert check_contract_compliance(prior, revised, contract) == []


def test_small_section_edit_counts_as_addressed():
    """小幅但关键的编辑 (如删除一句不当批评) 必须算作已处理,
    旧阈值 0.995 过严会误判未完成 (实测 R-REL-01 空转返工)"""
    from src.agents.paper_writer import check_contract_compliance

    filler = "射频指纹识别是物理层安全的重要支撑技术，近年来受到广泛关注。" * 10
    prior = (
        f"# 标题\n\n## 2 相关工作\n\n### 2.3 已有综述对比\n{filler}"
        f"文献[10]组织分散，未成体系。{filler}\n\n## 6 结论\n{filler}"
    )
    contract = [{"id": "R-REL-01", "status": "未解决", "priority": "低",
                 "problem": "2.3节对已有综述的批评有失公允", "evidence": "2.3节"}]
    revised = prior.replace("文献[10]组织分散，未成体系。", "文献[10]侧重技术实现层面。")
    assert check_contract_compliance(prior, revised, contract) == []


def test_prev_blocked_suggestions_skip_research():
    """此前轮次已验证无法加入清单的论文不再重复检索
    (审稿人常无视规则反复推荐同一批预印本, 重复检索纯浪费)"""
    import src.graph.pipeline as pl

    orig_extract = pl._extract_paper_suggestions
    pl._extract_paper_suggestions = lambda report: ["Deep Open Set Identification for RF Devices"]
    try:
        new_refs, blocked, hits = pl._resolve_review_suggestions(
            "## 7. 补充推荐论文\n内容", "测试", [],
            prev_blocked=[{"title": "Deep Open Set Identification for RF Devices", "reason": "预印本"}],
        )
        assert new_refs == [] and hits == []
        assert len(blocked) == 1
        assert "此前轮次已验证" in blocked[0]["reason"]
    finally:
        pl._extract_paper_suggestions = orig_extract


def test_word_count_excludes_references_section():
    """字数只统计正文: Writer 自行输出参考文献列表时不得误判超长。
    实测案例: 正文 11.7k 字连同参考文献被计为 20.5k 字, 触发不必要的
    删减重生成, 导致该轮质量下降 (37→32→29 的诱因之一)"""
    from src.agents.paper_writer import _shrink_draft_if_needed, MAX_DRAFT_CHARS

    body = "# 标题\n\n## 摘要\n" + "正文内容。" * 100  # 短正文
    refs = "\n\n## 参考文献\n\n" + "\n".join(
        f"[{i}] AUTHOR A B. Paper title number {i} on RF fingerprinting[J]. Some Journal, 2020, 1(2): 1-10."
        for i in range(1, 500)
    )
    draft = body + refs
    assert len(draft.replace(" ", "").replace("\n", "")) > MAX_DRAFT_CHARS  # 含参考文献确实超长
    # llm=None: 若误触发删减重生成会抛异常 → 测试失败
    out_draft, _, words = _shrink_draft_if_needed(None, [], draft, object(), "model")
    assert words <= MAX_DRAFT_CHARS
    assert "## 参考文献" not in out_draft  # 参考文献章节被剥离 (末端会重建)


def test_contract_items_for_sparse_subclasses_get_merge_hint():
    """文献稀缺类条目附上合并/声明稀缺处置方式,
    防止 Writer 硬凑引用引入不当文献 (实测: 音频域文献被以'借鉴意义'引入)"""
    from src.graph.pipeline import _augment_contract_sparse

    contract = [
        {"id": "R-CNT-01", "priority": "中",
         "problem": "3.3.4节自编码器仅1篇文献[49]，内容单薄", "evidence": "3.3.4节"},
        {"id": "R-CAT-01", "priority": "高",
         "problem": "3.2节瞬态信号方法文献不足", "evidence": "3.2节"},
        {"id": "R-OTHER", "priority": "高", "problem": "表7缺定量数据", "evidence": "4.3节"},
    ]
    out = _augment_contract_sparse(contract)
    assert "并入主题相邻的子类" in out[0]["problem"]
    assert "严禁为凑数编造引用" in out[0]["problem"]
    # 「文献不足」类条目同样附上处置方式 (实测 R-CAT-01 因关键词缺失漏掉)
    assert "处置方式" in out[1]["problem"]
    assert "处置方式" not in out[2]["problem"]


def test_quality_key_prefers_hard_gate_then_score():
    clean = _state(score=35)
    dirty = _state(score=45, hallucinated=["fake"], not_found=1)
    assert _review_quality_key(clean) > _review_quality_key(dirty)


def test_generated_figures_get_semantic_placeholders():
    draft = "# 标题\n\n## 1 引言\n内容\n\n## 2 相关工作\n内容\n\n## 3 核心方法\n内容\n\n## 4 比较与分析\n内容\n\n## 6 结论\n结束"
    synced = _synchronize_figure_placeholders(
        draft, ["taxonomy_0.png", "timeline_1.png", "trend_2.png", "method_comp_3.png"]
    )
    assert all(f"[图{i}:" in synced for i in range(1, 5))
    assert synced.index("[图1:") > synced.index("## 3 核心方法")
    assert synced.index("[图4:") > synced.index("## 4 比较与分析")


if __name__ == "__main__":
    tests = [
        test_high_score_no_revision,
        test_low_score_triggers_revision,
        test_acceptance_boundary_matches_40_of_50,
        test_high_score_with_hallucination,
        test_high_score_with_open_critical_issue,
        test_max_revisions_respected,
        test_error_ends,
        test_increment_revision_writes_prompt,
        test_dimension_scores_are_fixed_and_summed,
        test_partial_dimension_table_does_not_invent_missing_scores,
        test_issue_ledger_retains_omitted_open_issue,
        test_revision_contract_prioritises_and_caps_scope,
        test_revision_contract_excludes_reference_metadata_issues,
        test_format_revision_contract_marks_metadata_as_record_only,
        test_system_owned_critical_does_not_force_revision,
        test_writer_critical_still_forces_revision,
        test_blocked_and_existing_refs_are_fed_back_to_writer,
        test_compliance_check_detects_unchanged_draft,
        test_compliance_requires_new_refs_cited_for_add_items,
        test_contract_items_with_blocked_papers_get_explicit_action,
        test_change_summary_lists_unchanged_sections,
        test_stale_quote_issues_are_downweighted,
        test_paraphrased_quote_not_marked_stale,
        test_ledger_parser_accepts_non_r_prefix_ids,
        test_ledger_synthesized_when_reviewer_omits_table,
        test_time_span_issue_not_misclassified_as_metadata,
        test_paren_annotation_with_refnum_not_treated_as_quote,
        test_compliance_check_finds_h4_subsections,
        test_small_section_edit_counts_as_addressed,
        test_prev_blocked_suggestions_skip_research,
        test_word_count_excludes_references_section,
        test_contract_items_for_sparse_subclasses_get_merge_hint,
        test_quality_key_prefers_hard_gate_then_score,
        test_generated_figures_get_semantic_placeholders,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
