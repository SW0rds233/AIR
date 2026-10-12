"""Writer regressions reproduced from the Starlink maritime manuscript."""

from src.agents.writing import WritingAgent
from src.agents.protocol import AgentTask, TaskBudget, UsageRecord
from src.publication.schemas import Block, BlockRole, Manuscript, Section, WritingPacket


def test_writer_audit_rejects_revision_changelog_and_phantom_figure():
    packet = WritingPacket(main_question="中国星网对航海业的影响")
    manuscript = Manuscript(
        title="星网与航海业",
        abstract="研究问题、方法、发现与边界。",
        sections=[
            Section(heading="引言", role="introduction", blocks=[
                Block(role=BlockRole.background,
                      text="本版针对上一版被指出的问题作了九类修改。图1给出路径网络。"),
            ]),
            Section(heading="结论", role="conclusion", blocks=[
                Block(role=BlockRole.limitation, text="参数不足，当前不能估计净效应。"),
            ]),
        ],
    )
    problems = WritingAgent().audit_manuscript(manuscript, packet)
    assert any("修订过程" in problem for problem in problems)
    assert any("未登记图" in problem for problem in problems)


def test_writer_outline_marks_unverified_work_as_research_framework():
    from src.agents.writing import build_paper_outline

    packet = WritingPacket(
        main_question="中国星网对航海业的影响",
        claims=[{"id": "clm-1", "status": "proposed", "statement": "影响可能存在"}],
        models=[{"id": "model-1", "name": "候选路径"}],
    )
    outline = build_paper_outline(packet)
    headings = [row["heading"] for row in outline]
    roles = [row["role"] for row in outline]
    assert roles[0] == "introduction" and roles[-1] == "conclusion"
    assert "method" in roles and "discussion" in roles
    assert any("文献" in heading or "相关研究" in heading for heading in headings)
    assert not any("实证结果" in heading or "已证实结果" in heading for heading in headings)
    assert build_paper_outline(WritingPacket())[4]["heading"] == "证据现状与未决问题"
    from src.agents.writing import _abstract_of

    mixed = WritingPacket(claims=[
        {"status": "supported", "statement": "命题甲成立"},
        {"status": "refuted", "statement": "命题乙不成立"},
    ])
    summary = _abstract_of(mixed)
    assert "1 条获支持" in summary and "1 条命题已被反例否决" in summary


def test_writer_drafts_sections_from_registered_materials_and_targeted_review():
    import json
    import re
    from types import SimpleNamespace

    prompts: list[str] = []

    class FakeLLM:
        def invoke(self, messages):
            prompt = messages[-1].content
            prompts.append(prompt)
            match = re.search(r"只写第 (\d+) 个章节", prompt)
            if match:
                index = int(match.group(1))
                refs = ["doc-1"] if index == 2 else ["clm-1"] if index == 5 else []
                return SimpleNamespace(content=json.dumps({"blocks": [{
                    "role": "evidence" if index == 2 else "claim" if index == 5 else "reasoning",
                    "text": "依据已登记材料说明研究问题与证据边界。",
                    "ref_ids": refs, "needs_check": index == 5,
                    "review_issue_ids": ["issue-1", "invented-issue"] if index == 5 else [],
                }], "unresolved": ["需要核实覆盖参数"] if index == 5 else []}, ensure_ascii=False))
            if "据实际正文生成准确" in prompt:
                return SimpleNamespace(content=json.dumps({
                    "title": "星网建设与海事通信：研究框架",
                    "abstract": "本文研究星网建设对航海业的潜在影响。通过分析已登记文献与候选模型，本文提出可检验的传导机制，并区分现有证据、候选推断与尚未取得的数据。现阶段缺少直接观测，故不估计净效应或影响方向。",
                    "keywords": ["星网", "航海业", "海事通信"],
                }, ensure_ascii=False))
            return SimpleNamespace(content="{}")

    class FakeRuntime:
        def llm(self, task, usage, stage=""):
            return FakeLLM()

    packet = WritingPacket(
        main_question="中国星网建设会如何影响航海业",
        claims=[{"id": "clm-1", "status": "proposed", "statement": "覆盖可能改善"}],
        sources=[{"id": "doc-1", "source_id": "doc-1", "title": "Maritime networks",
                  "publication_status": "published", "publication_verified_by": "crossref",
                  "publication_type": "journal-article", "authors": "A Researcher",
                  "venue": "Marine Networks",
                  "year": "2024", "content_level": "abstract", "locator": "abstract",
                  "excerpt": "Maritime networks face coverage limitations."}],
        prior_manuscript={"sections": [{"heading": "4 候选机制", "role": "result",
            "blocks": [{"text": "本版针对上一版修订。", "refs": [{"id": "clm-1"}]}]}]},
        review_issues=[{"id": "issue-1", "category": "science", "summary": "说明覆盖前提",
                        "affected_refs": [{"id": "clm-1", "version": 1}],
                        "acceptance": ["前提明确"]}],
    )
    manuscript, note = WritingAgent()._draft_with_llm(
        AgentTask(agent="writing", objective="修订论文"), FakeRuntime(), UsageRecord(), packet)
    assert manuscript is not None and not note
    assert len(manuscript.sections) == 7
    assert manuscript.sections[1].blocks[0].refs[0].id == "doc-1"
    assert "Maritime networks" in prompts[2]
    assert manuscript.sections[4].blocks[0].refs[0].id == "clm-1"
    assert manuscript.sections[4].blocks[0].review_issue_ids == ["issue-1"]
    assert any("覆盖参数" in gap["detail"] for gap in manuscript.gaps)
    assert "说明覆盖前提" in prompts[5]
    assert "说明覆盖前提" not in prompts[1]
    assert "本版针对上一版修订" not in manuscript.sections[4].blocks[0].text
    from src.agents.writing import finalise_manuscript, render_markdown

    finalise_manuscript(manuscript, packet)
    markdown = render_markdown(manuscript)
    assert "## 2 相关研究与研究缺口" in markdown
    assert "[1]" in markdown and "## 参考文献" in markdown
    assert not any("修订过程" in finding or "未登记图" in finding
                   for finding in WritingAgent().audit_manuscript(manuscript, packet))


def test_source_ranking_favors_readable_relevant_recent_research():
    from src.agents.writer_plan import linked_source_ids, rank_sources

    def source(source_id, year, level, cited):
        return {"source_id": source_id, "title": source_id, "year": year,
                "content_level": level, "citation_count": cited,
                "publication_status": "published", "publication_verified_by": "crossref",
                "publication_type": "journal-article", "authors": "A Researcher",
                "venue": "Marine Networks",
                "relevance_reason": "与研究问题直接相关"}

    ranked = rank_sources([
        source("old-metadata", "2008", "metadata", 0),
        source("recent-full", "2025", "full_text", 80),
        source("recent-abstract", "2024", "abstract", 10),
    ])
    assert [row["source_id"] for row in ranked][:2] == ["recent-full", "recent-abstract"]
    packet = WritingPacket(claims=[{"support_refs": ["old-metadata"]}])
    linked = linked_source_ids(packet)
    assert rank_sources([
        source("recent-full", "2025", "full_text", 80),
        source("old-metadata", "2008", "metadata", 0),
    ], priority_ids=linked)[0]["source_id"] == "old-metadata"


def test_writer_reads_only_registered_fulltext(monkeypatch):
    from src.agents import tools
    from src.agents.writing import _enrich_registered_sources

    calls = []
    monkeypatch.setattr(tools, "_read_source", lambda source_id, **kwargs:
                        calls.append((source_id, kwargs["topic"])) or "已授权库中的原文片段")
    rows = _enrich_registered_sources([
        {"source_id": "doc-1", "source_set_id": "selected-library", "content_level": "full_text"},
        {"source_id": "doc-2", "source_set_id": "selected-library", "content_level": "abstract"},
        {"source_id": "doc-3", "source_set_id": "", "content_level": "full_text"},
    ])
    assert calls == [("doc-1", "selected-library")]
    assert rows[0]["_writer_excerpt"] == "已授权库中的原文片段"
    assert "_writer_excerpt" not in rows[1] and "_writer_excerpt" not in rows[2]


def test_writer_with_one_call_budget_keeps_the_complete_outline():
    import json
    from types import SimpleNamespace

    from src.agents.writing import build_paper_outline

    packet = WritingPacket(main_question="海事卫星通信研究")
    outline = build_paper_outline(packet)
    calls = []

    class LLM:
        def invoke(self, messages):
            calls.append(messages[-1].content)
            return SimpleNamespace(content=json.dumps({
                "title": "海事卫星通信研究", "abstract": "如实说明研究设计与边界。",
                "sections": [{**plan, "blocks": [{"role": "limitation", "text": "缺少直接观测。"}]}
                             for plan in outline],
            }, ensure_ascii=False))

    class Runtime:
        def llm(self, task, usage, stage=""):
            return LLM()

    task = AgentTask(agent="writing", objective="形成研究论文",
                     budget=TaskBudget(max_llm_calls=1))
    manuscript, note = WritingAgent()._draft_with_llm(task, Runtime(), UsageRecord(), packet)
    assert manuscript is not None and not note
    assert len(manuscript.sections) == len(outline)
    assert len(calls) == 1
