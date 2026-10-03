from __future__ import annotations

"""2-(v,k,λ) 可行性判定: 通用必要条件, 不含任何具体题目的特例。

同时覆盖 S1 命题化: 判定必须变成**命题 + 必要性义务**, 由规则证书验收关闭,
未决 (necessary_met) 时不得关闭、不得升级交付等级。
"""

import json
from pathlib import Path

import pytest

from src.research.design_feasibility import (
    DesignParams,
    check,
    is_sum_of_two_squares,
)

CASE = (Path(__file__).resolve().parents[1] / "evals" / "cases"
        / "combinatorial-design" / "case.md")


def test_sum_of_two_squares_rule():
    for n in (0, 1, 2, 4, 5, 8, 9, 10, 13, 25):
        assert is_sum_of_two_squares(n), n
    for n in (3, 6, 7, 11, 12, 14, 15, 21, 28):
        assert not is_sum_of_two_squares(n), n


def test_projective_plane_of_order_14_is_excluded():
    """211 点/每块 15/每对一次: 计数自洽, 但 14 阶射影平面不存在。"""
    report = check(DesignParams(v=211, k=15, lam=1))
    assert report.r == 15 and report.b == 211
    assert report.symmetric and report.order == 14
    assert report.verdict == "nonexistent"
    assert any("Bruck–Ryser–Chowla" in v for v in report.violations)
    assert "14" in report.describe()


def test_order_6_plane_excluded_and_order_2_3_planes_not_excluded():
    assert check(DesignParams(v=43, k=7, lam=1)).verdict == "nonexistent"   # n=6
     # n=2 (Fano) 与 n=3: BRC 不排除
    fano = check(DesignParams(v=7, k=3, lam=1))
    assert fano.verdict == "necessary_met" and fano.order == 2
    order3 = check(DesignParams(v=13, k=4, lam=1))
    assert order3.verdict == "necessary_met" and order3.order == 3
    assert any("不适用" in item for item in order3.limitations)


def test_divisibility_and_fisher_violations():
    bad_r = check(DesignParams(v=8, k=4, lam=1))          # r=7/3
    assert bad_r.verdict == "nonexistent"
    assert any("非整数" in v for v in bad_r.violations)

    bad_b = check(DesignParams(v=16, k=6, lam=1))         # r=3, b=8 < 16
    assert bad_b.verdict == "nonexistent"
    assert any("Fisher" in v for v in bad_b.violations)

    inconsistent = check(DesignParams(v=211, k=15, lam=1, b=200))
    assert inconsistent.verdict == "nonexistent"


def test_even_v_symmetric_design_needs_square_k_minus_lambda():
    # 2-(16,6,2): v=16 偶, k-λ=4 是平方数 → 必要条件通过
    ok = check(DesignParams(v=16, k=6, lam=2))
    assert ok.symmetric and ok.verdict == "necessary_met", ok.describe()
    # 2-(22,7,2): 计数与对称性都成立, 但 k-λ=5 非平方数 → BRC 排除
    bad = check(DesignParams(v=22, k=7, lam=2))
    assert bad.symmetric and bad.r == 7 and bad.b == 22
    assert bad.verdict == "nonexistent"
    assert any("平方数" in item for item in bad.violations), bad.describe()


def test_general_case_reports_limitations_instead_of_overclaiming():
    """非射影平面的一般参数: 不得声称"必要条件全部检查过"。"""
    report = check(DesignParams(v=211, k=15, lam=2))
    assert report.symmetric is False and report.verdict == "necessary_met"
    assert any("需要显式构造" in item for item in report.limitations)
    # 奇 v 且 λ>1 的非对称设计: BRC 不适用这一点必须明说, 不得冒充已检查
    odd = check(DesignParams(v=11, k=5, lam=2))
    assert odd.verdict == "necessary_met"
    assert any("不适用" in item or "未实现" in item for item in odd.limitations)


def test_invalid_parameters_are_insufficient_not_guessed():
    assert check(DesignParams(v=15, k=15, lam=1)).verdict == "nonexistent"
    assert check(DesignParams(v=0, k=0, lam=0)).verdict == "insufficient"


def test_extraction_is_generic_not_tuned_to_one_phrasing():
    """同一套词表要能吃下多种说法与不同参数 (含英文), 不针对某一题。"""
    from src.research.design_feasibility import extract_design_params, feasibility_from_text

    chinese = ("某实验有 211 个节点。管理员希望安排恰好 211 轮联合测试: 每轮恰好选择 15 个节点;"
               " 每个节点恰好参加 15 轮; 任意两个不同节点恰好共同参加一轮测试。")
    params = extract_design_params(chinese)
    assert params is not None
    assert (params.v, params.k, params.lam) == (211, 15, 1)
    assert feasibility_from_text(chinese).verdict == "nonexistent"

    # 不同参数、不同措辞 (Fano: 7 点 / 每块 3 个 / 每点 3 块 / 每对一次)
    other = ("设有 7 个对象, 每个区组包含 3 个对象; 每个对象出现在 3 组中;"
             " 任意两个对象恰好共同出现在一组。")
    second = extract_design_params(other)
    assert second is not None and second.v == 7 and second.k == 3
    assert feasibility_from_text(other).verdict == "necessary_met"

    english = ("There are 43 points. Each line contains 7 points. Each point occurs in 7 lines."
               " Any two points occur together in exactly 1 line.")
    third = extract_design_params(english)
    assert third is not None and third.v == 43 and third.k == 7

    # 抽不出计数约束时不得编造参数
    assert extract_design_params("请分析信道变化对可分性的影响") is None
    assert feasibility_from_text("讨论一下这个方向值不值得做") is None


# ---------------------------------------------------------------------------
# 出处要求: 判定必须记录"引用了哪条经典必要性定理", 不得空口断言
# ---------------------------------------------------------------------------


def test_every_check_carries_theorem_provenance():
    report = check(DesignParams(v=211, k=15, lam=1))
    assert report.sources(), "必须记录引用了哪些必要性定理"
    assert any("Bruck–Ryser–Chowla" in s for s in report.sources())
    for item in report.checks:
        assert item.theorem_cn and item.statement and item.citation, item
        assert item.inputs, "每条判定都必须记录输入数值"
        assert item.conclusion, "每条判定都必须给出本条件下的结论"
    violated = [c for c in report.checks if not c.result]
    assert violated and "Bruck–Ryser–Chowla" in violated[0].theorem_cn
    # 未判定的必要条件必须如实报出, 不得冒充"已全部检查"
    assert all(item.condition and item.reason for item in report.unchecked)


def test_certificate_is_deterministic_and_recomputable():
    first = check(DesignParams(v=211, k=15, lam=1))
    second = check(DesignParams(v=211, k=15, lam=1))
    assert first.certificate_digest() == second.certificate_digest()
    assert first.certificate_dict()["design"]["v"] == 211
    # 换参数必须换证书 (否则"换参数沿用旧证书"可以伪造结论)
    assert (check(DesignParams(v=43, k=7, lam=1)).certificate_digest()
            != first.certificate_digest())
    payload = json.loads(first.certificate_json())
    assert payload["verdict"] == "nonexistent"
    assert payload["counts"] == {"r": 15, "b": 211, "symmetric": True, "order": 14}


def test_certificate_records_why_the_theorem_applies():
    """BRC 的适用前提 (等价于射影平面) 必须自己也是一条可核验判定。

    否则"因为这是射影平面问题, 所以用 BRC"只停留在自然语言里, 复核者无法逐项确认
    (v = k(k-1)+1、λ=1、r=k=b 三个前提)。
    """
    report = check(DesignParams(v=211, k=15, lam=1))
    keys = [c.key for c in report.checks]
    assert "projective_plane_equivalence" in keys, keys
    plane = next(c for c in report.checks if c.key == "projective_plane_equivalence")
    assert plane.result is True
    assert plane.inputs["v"] == plane.inputs["k(k-1)+1"] == 211
    assert "射影平面" in plane.conclusion and "14 阶" in plane.conclusion
    # 非射影平面参数不得声称已套用射影平面形式的 BRC (2-(16,6,2): b=v 但 λ≠1)
    other = check(DesignParams(v=16, k=6, lam=2))
    assert "projective_plane_equivalence" not in [c.key for c in other.checks]
    assert "brc_projective_plane" not in [c.key for c in other.checks]
    assert "brc_even_order" in [c.key for c in other.checks]


# ---------------------------------------------------------------------------
# S1: 判定 → 命题 + 义务 (通用逻辑, 不含题目常量)
# ---------------------------------------------------------------------------


def test_formulation_from_text_produces_claim_and_obligation():
    from src.research.design_feasibility import (
        ACCEPTANCE_METHOD,
        formulate_from_text,
    )

    text = ("某大型实验有 211 个节点。管理员希望安排恰好 211 轮联合测试: "
            "每轮恰好选择 15 个节点; 每个节点恰好参加 15 轮; "
            "任意两个不同节点恰好共同参加一轮测试。")
    form = formulate_from_text(text)
    assert form is not None
    claim = form.claims[0]
    assert claim.design_v == 211 and claim.design_k == 15 and claim.design_lambda == 1
    assert claim.design_verdict == "nonexistent"
    assert "不存在" in claim.statement
    # 命题陈述必须与证书结论一致, 且记录引用的定理
    assert "定义" in claim.notes or "定理" in claim.notes
    obligation = form.obligations[0]
    assert obligation.acceptance_method == ACCEPTANCE_METHOD
    assert obligation.claim_id == claim.id
    assert obligation.local_context, "义务必须带上可复核证书"


def test_formulation_is_absent_when_counts_cannot_be_extracted():
    from src.research.design_feasibility import formulate_from_text

    assert formulate_from_text("请分析信道变化对可分性的影响") is None


# ---------------------------------------------------------------------------
# 端到端 (离线): 交付等级与门槛由证书决定
# ---------------------------------------------------------------------------


def _run_case(tmp_path, monkeypatch, request: str, project_id: str) -> dict:
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")

    from src.graph import theory_pipeline

    return theory_pipeline.run_theory_pipeline(
        request=request, topic=request[:20], project_id=project_id,
        problem_id="design", max_actions=20, max_tool_calls=20)


@pytest.mark.skipif(not CASE.is_file(), reason="缺少组合设计用例")
def test_problem2_offline_reaches_complete_paper_with_certificate(tmp_path, monkeypatch):
    """problem2 (离线): 命题 + 必要性义务 + 规则验收, 门槛通过, 达**完整论文** (含 PDF)。

    方案 v2 的里程碑 M1+M2: 交付物必须是出版级 `.tex` 与可读 `.pdf`,
    且证书摘要不因出版层而改变。
    """
    final = _run_case(tmp_path, monkeypatch, CASE.read_text(encoding="utf-8"), "p2paper")

    assert final["needs_clarification"] is False
    assert final["delivery_level"] == "完整论文", final.get("notes")
    assert final["gate_passed"] is True
    package = Path(final["package_dir"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    claims = json.loads((package / "claims.json").read_text(encoding="utf-8"))
    obligations = json.loads((package / "obligations.json").read_text(encoding="utf-8"))
    assert claims and obligations
    assert claims[0]["design_verdict"] == "nonexistent"
    assert obligations[0]["acceptance_method"] == "design_necessity"
    assert obligations[0]["status"] == "closed"
    # 正文必须引用该判定链 (定理名 + 输入结论), 而不是只给一句结论
    manuscript = (package / "manuscript.md").read_text(encoding="utf-8")
    assert "Bruck–Ryser–Chowla" in manuscript
    assert "判定链" in manuscript
    assert "n = k-1=14" in manuscript or "n=14" in manuscript
    # 出版层: 期刊式稿件 + 可编译的 .tex + 真实 PDF (M1/M2 的硬要求)
    assert (package / "publication.md").is_file()
    assert (package / "publication.tex").is_file()
    assert (package / "publication.pdf").is_file(), final.get("notes")
    assert (package / "references.json").is_file()
    publication_md = (package / "publication.md").read_text(encoding="utf-8")
    for section in ("摘要", "关键词", "参考文献", "Bruck–Ryser–Chowla"):
        assert section in publication_md, f"出版稿件缺少 {section}"
    # 离线运行不得产生任何外部费用
    assert manifest["usage"]["cost_usd"] == 0.0
    assert manifest["usage"]["llm_calls"] == 0
    # 成功判据必须落进规格: "不得推广到其他参数" 必须可核查, 而不是只留在评审者记忆里
    spec = json.loads((package / "research_spec.json").read_text(encoding="utf-8"))
    conditions = spec["success_conditions"]
    assert any("不得推广到其他" in c for c in conditions), conditions
    assert any("必须写出本次输入数值" in c or "v=211" in c for c in conditions), conditions


def test_confirmed_candidate_does_not_hide_the_precise_problem_text(tmp_path, monkeypatch):
    """题面里的精确约束不得被"已确认的候选路线"顶掉 (现场缺陷回归)。

    现场路径: 题面含方向措辞 ("管理员希望安排…"), 系统先按研究方向生成候选,
    `confirm_candidate` 把候选择入 `spec.questions`; 此后 `formulate` 只返回候选命题,
    题面的计数约束再也参与不上 → 运行以"请求澄清"收尾 (还被旧缺陷卡在澄清循环里)。

    这里直接造出"候选已确认 + 题面含精确计数约束"的研究规格 (恢复历史会话、或
    分类被后来的规则改动影响的规格都会长成这个样子), 断言仍然以题面为准。
    """
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.question_planner import build_spec_from_input
    from src.research.schemas import ClaimQuestion, ClaimStatus
    from src.research.store import ResearchStore

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    text = CASE.read_text(encoding="utf-8")
    spec = build_spec_from_input(request=text, project_id="cand2design")
    assert spec.problem_statement, "该题面本身可被精确形式化"
    # 造出"用户已确认候选"的状态 (方向输入路径会写进 questions + confirmed)
    spec.questions = [ClaimQuestion(
        statement="需要补全研究对象、变量与数据来源后才有可检验命题",
        category="scenario", recommended=True)]
    spec.candidates = list(spec.questions)
    spec.confirmed = True
    spec.selected_candidate_id = "cand-fixed"

    store = ResearchStore("cand2design", db_path=tmp_path / "c2d.sqlite")
    engine = TheoryEngine(spec, store=store, budget=ResearchBudget(max_actions=12))
    assert engine.bootstrap() is True
    result = engine.run()

    # 关键: 仍然以题面的计数约束为准, 给出"不存在", 而不是请求澄清
    assert result.needs_clarification is False
    claim = result.snapshot.claims[0]
    assert claim.design_verdict == "nonexistent"
    assert claim.status == ClaimStatus.supported
    assert "不存在" in claim.statement
    assert result.gate.passed
    # 已确认的候选不被静默丢弃: 作为来源记录在案
    assert any("仅作来源记录" in n for n in result.notes), result.notes
    store.close()


def test_problem_text_supplied_as_attachment_is_used_for_formulation(tmp_path, monkeypatch):
    """现场缺陷回归: 题面作为**问题说明附件**交上来时, 附件正文必须参与形式化。

    用户的真实路径: 输入框只写一句"研究此问题, 输出明确的结论, 并撰写成文",
    把 `problem2.md` 作为"补充问题说明"附件上传。此前附件的解析文本被写进图状态
    (`attachment_candidates`) 后**没有任何读取者**(`uploads.problem_text()` 全局零调用),
    于是引擎只看到那句自由请求 → 判"缺少可检验对象" → 请求澄清 (并被澄清循环卡死)。
    这里断言: 附件正文进入形式化, 产出"不存在"的命题与论文草稿。
    """
    from src.graph import theory_pipeline
    from src.research.store import ResearchStore
    from src.utils import uploads

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")

    # 按真实上传路径登记附件 (内容用 problem2.md 的题面)
    staging = tmp_path / "staged.md"
    staging.write_text(CASE.read_text(encoding="utf-8"), encoding="utf-8")
    saved = uploads.save_problem_attachment("attachproj", "p1", "problem2.md", staging)
    assert saved["ok"] is True, saved
    attachment_id = saved["attachment"]["attachment_id"]

    final = theory_pipeline.run_theory_pipeline(
        request="研究此问题，输出明确的结论，并撰写成文。", topic="",
        project_id="attachproj", problem_id="p1",
        input_snapshot={
            "request": "研究此问题，输出明确的结论，并撰写成文。", "topic": "",
            "source_set_id": "", "source_set_kind": "kb", "source_policy": "user_kb",
            "attachment_ids": [attachment_id],
            "attachments": [{"attachment_id": attachment_id,
                             "filename": saved["attachment"]["filename"],
                             "sha256": saved["attachment"]["sha256"]}],
            "budget": {"max_actions": 12, "max_tool_calls": 12},
        },
        max_actions=12, max_tool_calls=12)

    assert final.get("needs_clarification") is False, final.get("notes")
    assert final.get("delivery_level") in ("论文草稿", "完整论文"), final.get("notes")
    store = ResearchStore("attachproj",
                          db_path=config.DATA_DIR / "research" / "attachproj.sqlite")
    claims = [__import__("src.research.schemas", fromlist=["Claim"]).Claim.model_validate(d)
              for d in store.list_latest("claim")]
    claims = [c for c in claims if c.problem_id == "p1"]
    assert claims and claims[0].design_verdict == "nonexistent", claims
    assert any("形式化使用问题说明附件" in n for n in (final.get("notes") or [])), \
        final.get("notes")
    store.close()


def test_fano_necessary_met_never_claims_nonexistence(tmp_path, monkeypatch):
    """泛化回归: 2-(7,3,1) 的必要条件满足 → 不得输出"不存在", 义务保持未关闭。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ClaimStatus, ObligationStatus, ResearchSpec
    from src.research.store import ResearchStore

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    text = ("设有 7 个对象, 每个区组包含 3 个对象; 每个对象出现在 3 组中; "
            "任意两个对象恰好共同出现在一组。")
    spec = ResearchSpec(project_id="fano", problem_id="design", problem_statement=text,
                        original_request=text)
    store = ResearchStore("fano", db_path=tmp_path / "fano.sqlite")
    result = TheoryEngine(spec, store, budget=ResearchBudget(max_actions=20)).run()

    claim = result.snapshot.claims[0]
    assert claim.design_verdict == "necessary_met"
    assert claim.status != ClaimStatus.supported
    assert "不存在" not in claim.statement
    assert "未定" in claim.statement
    obligation = result.snapshot.obligations[0]
    assert obligation.status != ObligationStatus.closed
    assert not result.gate.passed
    store.close()


def test_second_projective_plane_case_is_nonexistent(tmp_path, monkeypatch):
    """泛化回归: 2-(43,7,1) (n=6, 落在 BRC 覆盖的余数类) 必须输出"不存在"。"""
    from src.research.loop import ResearchBudget, TheoryEngine
    from src.research.schemas import ClaimStatus, ObligationStatus, ResearchSpec
    from src.research.store import ResearchStore

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    text = ("There are 43 points. Each line contains 7 points. Each point occurs in 7 lines. "
            "Any two points occur together in exactly 1 line.")
    spec = ResearchSpec(project_id="plane43", problem_id="design", problem_statement=text,
                        original_request=text)
    store = ResearchStore("plane43", db_path=tmp_path / "plane43.sqlite")
    result = TheoryEngine(spec, store, budget=ResearchBudget(max_actions=20)).run()

    claim = result.snapshot.claims[0]
    assert claim.design_verdict == "nonexistent"
    assert claim.status == ClaimStatus.supported
    assert "不存在" in claim.statement
    assert result.snapshot.obligations[0].status == ObligationStatus.closed
    assert result.gate.passed
    store.close()


def test_reverse_case_keeps_clarification_behaviour(tmp_path, monkeypatch):
    """反向用例: 抽不出计数约束的题面行为不变 (不生成设计命题, 走原有澄清/候选路径)。"""
    from src.research.design_feasibility import feasibility_from_text, formulate_from_text

    direction = "分析信道变化对可分性的影响"
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src.graph import theory_pipeline

    final = theory_pipeline.run_theory_pipeline(
        request=direction, topic=direction, project_id="channel", problem_id="design",
        max_actions=20, max_tool_calls=20)

    # 不编造计数参数 → 不产生任何设计命题/必要性义务
    assert feasibility_from_text(direction) is None
    assert formulate_from_text(direction) is None
    claims = json.loads((Path(final["package_dir"]) / "claims.json")
                        .read_text(encoding="utf-8"))
    obligations = json.loads((Path(final["package_dir"]) / "obligations.json")
                             .read_text(encoding="utf-8"))
    assert not [c for c in claims if c.get("design_v") is not None]
    assert not [o for o in obligations
                if o.get("acceptance_method") == "design_necessity"]
    # 研究方向输入在离线规则路径下仍按原语义收尾 (澄清/未决报告, 不是论文草稿)
    assert final["delivery_level"] != "论文草稿"
    assert final["gate_passed"] is False


def test_statement_swap_across_parameters_is_rejected():
    """对齐检查: 换一组设计参数后不得沿用旧证书 (陈述偷换)。"""
    from src.research.acceptance import _aligned
    from src.research.schemas import Claim, VerificationRecord

    record = VerificationRecord(
        tool="design_necessity", claim_id="c1", validation_status="verified",
        arguments={"design_v": 211, "design_k": 15, "design_lambda": 1,
                   "design_b": 211, "design_r": 15, "design_verdict": "nonexistent"})
    same = Claim(statement="s", design_v=211, design_k=15, design_lambda=1,
                 design_b=211, design_r=15, design_verdict="nonexistent")
    assert _aligned(record, same) is True
    swapped = same.model_copy(update={"design_v": 43, "design_k": 7, "design_b": 43,
                                      "design_r": 7, "design_lambda": 1})
    assert _aligned(record, swapped) is False
    # 参数相同但结论被改动 → 同样拒绝
    flipped = same.model_copy(update={"design_verdict": "necessary_met"})
    assert _aligned(record, flipped) is False
    # 命题带设计参数但记录里没有对应输入 → 拒绝 (不得用别的记录充当证书)
    bare = VerificationRecord(tool="sympy", claim_id="c1", arguments={})
    assert _aligned(bare, same) is False

