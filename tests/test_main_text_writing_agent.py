from __future__ import annotations

"""M3: 主文统一由 WritingAgent 形成 (合并计划 §7.3 "退役双正文主路径")。

这里检查四件在合并后必须成立的事:

1. **唯一入口**: `run_theory_writing` 走 `agents.writing.write_main_manuscript`,
   自己不再组装正文 (否则"双正文"只是从两个文件变成两段代码);
2. **离线逐字不变**: 无 LLM 时由冻结快照确定性起草, 输出与旧渲染器一致 ——
   合并不得以"可复现性下降"为代价;
3. **降级是一次性的**: 模型输出不可解析/调用失败时退到确定性起草, 并把原因写进
   note, 而不是抛错中断交付;
4. **两条路径共用同一份实现**: 替换 `write_main_manuscript` 会同时改变两个入口的
   行为 (差异测试, 不靠阅读代码判断)。
"""

import pytest

from src.agents import theory_writer, writing
from src.agents.writing import (
    manuscript_from_snapshot,
    to_publication_manuscript,
    write_main_manuscript,
)
from src.research.schemas import (
    Claim,
    ClaimStatus,
    Coverage,
    ResearchSnapshot,
    SupportKind,
    ValidationStatus,
    VerificationRecord,
)


def _snapshot() -> ResearchSnapshot:
    """一个与 problem2 同构的设计类快照 (结论可用, 带证书)。"""
    claim = Claim(id="clm-1", statement="2-(211,15,1) 设计不存在",
                  status=ClaimStatus.supported, support_kind=SupportKind.theorem_application,
                  coverage=Coverage.target, validation_status=ValidationStatus.verified,
                  design_v=211, design_k=15, design_lambda=1, design_b=211, design_r=15,
                  design_verdict="nonexistent")
    record = VerificationRecord(
        id="v1", claim_id="clm-1", tool="design_necessity", status="passed",
        validation_status=ValidationStatus.verified, certificate="deadbeef",
        arguments={"design_report": {
            "design": {"v": 211, "k": 15, "lam": 1},
            "counts": {"r": 15, "b": 211, "symmetric": True, "order": 14},
            "evidence": [{"condition": "Bruck–Ryser–Chowla 必要条件", "result": False,
                          "theorem": "Bruck–Ryser–Chowla 定理 (射影平面)",
                          "inputs": {"n": 14}}],
        }})
    return ResearchSnapshot(project_id="lf", problem_id="p1", claims=[claim],
                            verifications=[record])


# ----------------------------------------------------------------------
# 唯一入口
# ----------------------------------------------------------------------
def test_run_theory_writing_delegates_to_the_single_entry(monkeypatch):
    """替换唯一入口即改变理论主文 —— 证明它没有第二份实现。"""
    seen: list[str] = []

    def fake_write(snapshot, topic="", delivery_level="", **kwargs):
        seen.append(topic)
        return snapshot_manuscript(), "# 替换入口后的正文\n", {"clm-1": "a"}, "test"

    def snapshot_manuscript():
        return manuscript_from_snapshot(_snapshot(), "Problem 2")[0]

    monkeypatch.setattr(writing, "write_main_manuscript", fake_write)
    markdown, writing_map = theory_writer.run_theory_writing(_snapshot(), "Problem 2")
    assert seen == ["Problem 2"]
    assert markdown == "# 替换入口后的正文\n"
    assert writing_map == {"clm-1": "a"}


def test_theory_writer_no_longer_builds_the_main_text_itself():
    """静态判据: `run_theory_writing` 不得直接调 build_manuscript/render_markdown。

    否则"主文统一"就退化成两处组装正文, 而两处迟早会漂移。
    """
    import inspect

    source = inspect.getsource(theory_writer.run_theory_writing)
    assert "write_main_manuscript" in source
    assert "build_manuscript(" not in source, "主文必须由写作智能体形成"
    assert "render_markdown(" not in source, "主文必须由写作智能体形成"


# ----------------------------------------------------------------------
# 离线逐字不变 + 降级原因
# ----------------------------------------------------------------------
def test_offline_main_text_is_the_deterministic_unified_render():
    """离线下主文必须**就是**唯一 IR 的确定性渲染 (逐字一致, 且可复现)。

    这条契约曾经是"与旧快照渲染器逐字一致" —— 那时 Markdown 走旧 IR、`.tex/.pdf` 走唯一
    IR, 同一次运行的两份产物来自两种表示, 只能靠人工比对。现在两者同源 (§3.3 G17),
    因此断言改成:**主文 == 唯一 IR 的渲染**, 且逐次调用完全一致 (确定性)。
    """
    from src.agents.writing import render_markdown
    from src.publication.schemas import Manuscript

    snapshot = _snapshot()
    first_manuscript, first_md, writing_map, note = write_main_manuscript(snapshot,
                                                                         "Problem 2")
    _second_manuscript, second_md, _map2, _note2 = write_main_manuscript(snapshot,
                                                                       "Problem 2")
    # 1) 确定性: 同一输入两次渲染逐字相同
    assert first_md == second_md, "确定性起草出现了不确定的差异"
    # 2) 同源: 主文就是唯一 IR 的渲染 (Markdown 与 LaTeX/PDF 用同一份稿件)
    assert isinstance(first_manuscript, Manuscript), type(first_manuscript)
    assert first_md == render_markdown(first_manuscript)
    # 3) 可追溯: 正文里必须出现块锚点, 否则"从论断回到快照对象"只能靠猜
    assert "<!-- block:" in first_md, first_md[:200]
    assert first_manuscript.sections, "稿件必须分段"
    assert writing_map, "写作映射不能为空 (正文要能回到快照对象)"
    assert "确定性起草" in note


def test_failed_llm_draft_falls_back_and_reports_the_reason():
    """模型输出不可解析时必须降级并写明原因, 不能中断交付。"""
    class _Runtime:
        def llm_available(self) -> bool:
            return True

    from src.agents.protocol import AgentTask, ContextPack

    task = AgentTask(task_id="t1", agent="writing", objective="写正文",
                     project_id="lf", problem_id="p1", run_id="r1")
    context = ContextPack(request="问题", objects={})
    # AgentRuntime 由协议构造; 这里直接给一个"会失败"的替身
    manuscript, markdown, _, note = write_main_manuscript(
        _snapshot(), "Problem 2", task=task, context=context,
        runtime=_Runtime())  # type: ignore[arg-type]
    assert "确定性起草" in note
    assert "降级原因" in note, note
    assert markdown


def test_publication_blocks_keep_every_claim_labelled():
    """出版块表示必须保留"结论文本 -> 命题 id"的引用 (追溯视图依赖它)。"""
    snapshot = _snapshot()
    legacy, _ = manuscript_from_snapshot(snapshot, "Problem 2")
    publication = to_publication_manuscript(legacy, snapshot)
    claim_blocks = [b for b in publication.all_blocks()
                    if any(k == "claim" for k in b.ref_kinds)]
    assert claim_blocks, "结论块必须带 claim 引用"
    assert {ref.id for b in claim_blocks for ref, k in zip(b.refs, b.ref_kinds)
            if k == "claim"} == {"clm-1"}


def test_main_text_note_is_returned_for_the_delivery_package():
    """交付包要能说明"主文是谁写的" —— 否则读者无法判断可复现性。"""
    for kwargs in ({}, ):
        _, _, _, note = write_main_manuscript(_snapshot(), "Problem 2", **kwargs)
        assert note.strip()
        assert "确定性起草" in note


def test_problem2_offline_main_text_has_no_llm_call(tmp_path, monkeypatch):
    """以 `problem2.md` 为题的离线整链: 主文由写作智能体产出, 但**不调用模型**。

    这是合并后必须成立的一条: "文章撰写交给 WritingAgent" 不能以"离线也要烧钱/
    不可复现"为代价 —— 离线时写作智能体走确定性起草, 用量保持全零, 证书不变。
    """
    import json
    from pathlib import Path

    case = Path(__file__).resolve().parents[1] / "problem2.md"
    if not case.is_file():
        pytest.skip("缺少 problem2 用例")

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from tests.test_design_feasibility import _run_case

    final = _run_case(tmp_path, monkeypatch, case.read_text(encoding="utf-8"), "p2agent")
    package = Path(final["package_dir"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    # 主文仍然逐字来自快照渲染 (离线可复现), 且没有任何模型调用
    manuscript = (package / "manuscript.md").read_text(encoding="utf-8")
    assert "Bruck–Ryser–Chowla" in manuscript
    assert manifest["usage"]["llm_calls"] == 0, manifest["usage"]
    assert manifest["usage"]["cost_usd"] == 0.0
    # 可反查性检查必须说明它核对的是**正文本体**, 不是"自我一致"
    trace = manifest.get("manuscript_traceability", {})
    assert trace.get("checked") in ("markdown", "snapshot_only"), trace
    assert (package / "publication.pdf").is_file()
