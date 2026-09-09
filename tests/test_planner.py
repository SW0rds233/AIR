"""Planner (自然语言→结构化指令) 测试：提取 / 计划确认 / 路由 (离线测试)

运行:
    python tests/test_planner.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import src.graph.pipeline as pl
from src.graph.state import PipelineState


def test_route_after_plan():
    assert pl.route_after_plan({"plan_correction": "x"}) == "research_planner"
    assert pl.route_after_plan({"plan_correction": ""}) == "supervisor"


def test_route_supervisor():
    assert pl.route_supervisor({"supervisor_next": "research_planner"}) == "research_planner"
    assert pl.route_supervisor({"supervisor_next": "literature_review"}) == "literature_review"
    assert pl.route_supervisor({"supervisor_next": "outline_generation"}) == "outline_generation"
    assert pl.route_supervisor({}) == "end"


def test_supervisor_dispatches_full_pipeline_in_order():
    """完整综述: 计划确认后依次派发 research → write → end"""
    s = {"plan_confirmed": True, "stages": ["research", "write"], "stage_index": 0}
    out = pl.supervisor_node(s)
    assert out["supervisor_next"] == "literature_review"
    assert out["stage_index"] == 1

    s2 = {"plan_confirmed": True, "stages": ["research", "write"], "stage_index": 1}
    out2 = pl.supervisor_node(s2)
    assert out2["supervisor_next"] == "outline_generation"
    assert out2["stage_index"] == 2

    s3 = {"plan_confirmed": True, "stages": ["research", "write"], "stage_index": 2}
    assert pl.supervisor_node(s3)["supervisor_next"] == "end"


def test_supervisor_partial_task_research_only():
    """局部任务: 只要检索+总结 → research 之后直接结束"""
    s = {"plan_confirmed": True, "stages": ["research"], "stage_index": 0}
    assert pl.supervisor_node(s)["supervisor_next"] == "literature_review"
    s2 = {"plan_confirmed": True, "stages": ["research"], "stage_index": 1}
    assert pl.supervisor_node(s2)["supervisor_next"] == "end"


def test_supervisor_skip_retrieval_goes_straight_to_write():
    """skip-retrieval 命中缓存 → 跳过 research, 直接派发 write"""
    s = {"plan_confirmed": True, "stages": ["research", "write"], "stage_index": 0, "skip_retrieval": True}
    out = pl.supervisor_node(s)
    assert out["supervisor_next"] == "outline_generation"
    assert out["stage_index"] == 2


def test_supervisor_requires_plan_first():
    assert pl.supervisor_node({"plan_confirmed": False})["supervisor_next"] == "research_planner"


def test_guess_stages():
    assert pl._guess_stages("请帮我写一篇射频指纹识别的综述论文") == ["research", "write"]
    assert pl._guess_stages("帮我检索并总结相关文献") == ["research"]
    assert pl._guess_stages("只要做个文献调研") == ["research"]
    assert pl._guess_stages("随便研究一下") == ["research", "write"]


def test_split_cn_en():
    assert pl._split_cn_en("射频指纹提取方法 (RF fingerprint extraction methods)") == (
        "射频指纹提取方法", "RF fingerprint extraction methods")
    assert pl._split_cn_en("射频指纹识别算法（deep learning-based RF fingerprinting）") == (
        "射频指纹识别算法", "deep learning-based RF fingerprinting")
    assert pl._split_cn_en("普通子主题") == ("普通子主题", "")


def test_normalize_subtopics():
    subs = [
        "射频指纹提取方法 (RF fingerprint extraction methods)",
        "普通子主题",
        "深度学习识别 (deep learning classification)",
    ]
    kws = ["射频指纹"]
    cn, kw = pl._normalize_subtopics(subs, kws)
    assert cn == ["射频指纹提取方法", "普通子主题", "深度学习识别"]
    assert "RF fingerprint extraction methods" in kw
    assert "deep learning classification" in kw
    assert "射频指纹" in kw


def test_research_planner_splits_cn_en_subtopics_into_keywords():
    """端到端: 子主题「中文（英文）」→ 中文名保留, 英文确定性并入关键词 (进入检索)"""
    orig = pl._extract_research_plan
    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "射频指纹识别",
        "keywords": ["射频指纹", "RF fingerprinting"],
        "sub_topics": [
            "射频指纹提取方法 (RF fingerprint extraction methods)",
            "对抗攻击 (adversarial attacks)",
        ],
        "time_range": "",
        "stages": ["research"],
    }
    try:
        out = pl.research_planner_node({"research_request": "研究射频指纹", "skip_retrieval": False})
        # 子主题只留中文名
        assert out["sub_topics"] == ["射频指纹提取方法", "对抗攻击"]
        # 括号里的英文确定性并入关键词
        assert "RF fingerprint extraction methods" in out["topic_keywords"]
        assert "adversarial attacks" in out["topic_keywords"]
    finally:
        pl._extract_research_plan = orig


def test_detect_use_cache():
    assert pl._detect_use_cache("相关的检索工作已经完成，请你先查看相关缓存") is True
    assert pl._detect_use_cache("直接用缓存生成报告") is True
    assert pl._detect_use_cache("我想开展关于射频指纹的研究") is False
    assert pl._detect_use_cache("结合已有的数据和报告，撰写综述") is True
    assert pl._detect_use_cache("结合已收集到的信息，撰写综述") is True


def test_guess_stages_continuation_write_only():
    """已有数据/报告 + 撰写 → 只跑 write, 跳过 research"""
    assert pl._guess_stages("结合已有的数据和报告，撰写综述") == ["write"]
    assert pl._guess_stages("写一篇射频指纹识别的综述论文") == ["research", "write"]


def test_guess_stages_report_is_not_write():
    """「检索…生成报告」≠ 撰写论文 → 只跑 research"""
    assert pl._guess_stages("检索相关文献并生成报告") == ["research"]
    assert pl._guess_stages("检索文献并生成综述总结") == ["research"]


def test_guess_stages_negation():
    """「不需要撰写完整论文」→ 只跑 research"""
    assert pl._guess_stages("不需要撰写完整论文") == ["research"]
    assert pl._guess_stages("检索文献并生成报告，不需要撰写完整论文") == ["research"]


def test_guess_topic_strips_quotes():
    """LLM 失败回退时, 主题不带引号"""
    from types import SimpleNamespace

    import src.config as cfg

    orig = cfg.build_llm

    def fake_llm(key="main"):
        raise Exception("402 Insufficient Balance")

    cfg.build_llm = fake_llm
    try:
        plan = pl._extract_research_plan("我希望开展关于“射频指纹”的主题研究，请检索相关文献并生成报告")
        assert plan["topic"] == "射频指纹"
        assert plan["stages"] == ["research"]
    finally:
        cfg.build_llm = orig


def test_research_planner_continuation_skips_research():
    """「结合已有数据…撰写综述」→ stages=['write'] 且命中缓存跳过检索"""
    import tempfile
    from pathlib import Path

    import src.utils.pipeline_cache as pc
    import src.utils.session_memory as sm

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic
    orig_latest = pc.latest_cache_topic
    orig_load = pc.load_retrieval_cache
    orig_save = pl.save_file
    orig_mem = sm.MEMORY_FILE

    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "结合已有数据和报告撰写综述", "keywords": [], "sub_topics": [],
        "time_range": "", "stages": ["research", "write"], "reuse_previous": True,
    }
    sm.MEMORY_FILE = Path(tempfile.mkdtemp()) / "mem.json"  # 无记忆 → 走最近缓存
    pc.resolve_cache_topic = lambda t: ("射频指纹识别技术研究" if t == "射频指纹识别技术研究" else None)
    pc.latest_cache_topic = lambda: "射频指纹识别技术研究"
    pc.load_retrieval_cache = lambda topic: {
        "topic": "射频指纹识别技术研究",
        "literature_review_notes": "笔记",
        "verified_references": [{"ref_number": i} for i in range(25)],
        "retrieved_papers": [], "unfiltered_papers": [],
    }
    pl.save_file = lambda content, name: name
    try:
        out = pl.research_planner_node({"research_request": "结合已有的数据和报告，撰写综述", "skip_retrieval": False})
        assert out["stages"] == ["write"]
        assert out["skip_retrieval"] is True
        assert out["research_topic"] == "射频指纹识别技术研究"  # 沿用上下文主题
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve
        pc.latest_cache_topic = orig_latest
        pc.load_retrieval_cache = orig_load
        pl.save_file = orig_save
        sm.MEMORY_FILE = orig_mem


def test_resolve_cache_topic_fuzzy():
    """主题字符串不完全一致时, 模糊匹配已有缓存"""
    import tempfile
    from pathlib import Path

    import src.utils.pipeline_cache as pc

    orig_dir = pc.CACHE_DIR
    tmp = Path(tempfile.mkdtemp())
    pc.CACHE_DIR = tmp
    try:
        refs = [{"ref_number": i} for i in range(25)]
        pc.save_retrieval_cache("射频指纹识别研究", "notes", refs)
        pc.save_retrieval_cache("图像分割", "notes", refs)
        assert pc.resolve_cache_topic("射频指纹识别研究") == "射频指纹识别研究"  # 精确
        assert pc.resolve_cache_topic("射频指纹识别研究综述") == "射频指纹识别研究"  # 模糊
        assert pc.resolve_cache_topic("完全不相关的主题") is None
    finally:
        pc.CACHE_DIR = orig_dir


def test_research_planner_detects_use_cache_and_loads():
    """意图含"检索已完成/用缓存" → 自动命中缓存并跳过检索"""
    import src.utils.pipeline_cache as pc

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic
    orig_load = pc.load_retrieval_cache
    orig_save = pl.save_file

    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "射频指纹识别研究综述", "keywords": [], "sub_topics": [],
        "time_range": "", "stages": ["research"], "reuse_previous": True,
    }
    pc.resolve_cache_topic = lambda topic: "射频指纹识别研究"
    pc.load_retrieval_cache = lambda topic: {
        "literature_review_notes": "缓存笔记",
        "verified_references": [{"ref_number": i} for i in range(25)],
        "retrieved_papers": [], "unfiltered_papers": [],
    }
    pl.save_file = lambda content, name: name
    try:
        state = {"research_request": "检索已完成，查看缓存生成报告", "skip_retrieval": False}
        out = pl.research_planner_node(state)
        assert out["skip_retrieval"] is True
        assert out["literature_review_notes"] == "缓存笔记"
        assert out["literature_notes_path"]
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve
        pc.load_retrieval_cache = orig_load
        pl.save_file = orig_save


def test_extract_research_plan_outputs_reuse_previous():
    """LLM 结构化输出 reuse_previous 字段 (替代正则主判断)"""
    from types import SimpleNamespace

    import src.config as cfg

    orig = cfg.build_llm

    def fake_llm(key="main"):
        return SimpleNamespace(invoke=lambda msgs: SimpleNamespace(
            content='{"topic": "", "keywords": [], "sub_topics": [], "time_range": "", '
                   '"stages": ["write"], "reuse_previous": true, "needs_clarification": false}'
        ))

    cfg.build_llm = fake_llm
    try:
        plan = pl._extract_research_plan("结合已有的数据和报告，撰写综述")
        assert plan["reuse_previous"] is True
        assert plan["stages"] == ["write"]
    finally:
        cfg.build_llm = orig


def test_session_memory_save_load():
    import tempfile
    from pathlib import Path

    import src.utils.session_memory as sm

    orig = sm.MEMORY_FILE
    sm.MEMORY_FILE = Path(tempfile.mkdtemp()) / "mem.json"
    try:
        sm.save_session_memory("射频指纹识别技术研究", ["research"])
        mem = sm.load_session_memory()
        assert mem["topic"] == "射频指纹识别技术研究"
        assert mem["stages"] == ["research"]
    finally:
        sm.MEMORY_FILE = orig


def test_research_planner_continuation_falls_back_to_full_research_when_no_cache():
    """复用缓存失败(缓存被清空) → 恢复完整流程 research+write, 而不是空跑 write"""
    import tempfile
    from pathlib import Path

    import src.utils.pipeline_cache as pc
    import src.utils.session_memory as sm

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic
    orig_latest = pc.latest_cache_topic
    orig_mem = sm.MEMORY_FILE

    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "撰写论文", "keywords": [], "sub_topics": [],
        "time_range": "", "stages": ["write"], "reuse_previous": True,
    }
    pc.resolve_cache_topic = lambda t: None
    pc.latest_cache_topic = lambda: None
    sm.MEMORY_FILE = Path(tempfile.mkdtemp()) / "mem.json"  # 无记忆
    try:
        out = pl.research_planner_node({"research_request": "请你在已收集文献和数据的基础上，撰写论文", "skip_retrieval": False})
        assert out["skip_retrieval"] is False
        assert out["stages"] == ["research", "write"]  # 恢复完整流程
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve
        pc.latest_cache_topic = orig_latest
        sm.MEMORY_FILE = orig_mem


def test_research_planner_uses_session_memory_for_continuation():
    """「已有数据撰写」且未指明主题 → 优先沿用会话记忆主题"""
    import tempfile
    from pathlib import Path

    import src.utils.pipeline_cache as pc
    import src.utils.session_memory as sm

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic
    orig_load = pc.load_retrieval_cache
    orig_save = pl.save_file
    orig_mem = sm.MEMORY_FILE

    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "撰写综述", "keywords": [], "sub_topics": [],
        "time_range": "", "stages": ["research", "write"], "reuse_previous": True,
    }
    sm.MEMORY_FILE = Path(tempfile.mkdtemp()) / "mem.json"
    sm.save_session_memory("射频指纹识别技术研究", ["research"])
    pc.resolve_cache_topic = lambda t: ("射频指纹识别技术研究" if t == "射频指纹识别技术研究" else None)
    pc.load_retrieval_cache = lambda topic: {
        "topic": "射频指纹识别技术研究",
        "literature_review_notes": "笔记",
        "verified_references": [{"ref_number": i} for i in range(25)],
        "retrieved_papers": [], "unfiltered_papers": [],
    }
    pl.save_file = lambda content, name: name
    try:
        out = pl.research_planner_node({"research_request": "结合已有的数据和报告，撰写综述", "skip_retrieval": False})
        assert out["research_topic"] == "射频指纹识别技术研究"
        assert out["skip_retrieval"] is True
        assert out["stages"] == ["write"]
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve
        pc.load_retrieval_cache = orig_load
        pl.save_file = orig_save
        sm.MEMORY_FILE = orig_mem


def test_is_garbage_topic():
    assert pl._is_garbage_topic("") is True
    assert pl._is_garbage_topic("你帮我开展研究", "请你帮我开展研究") is True  # 与指令高度相似
    assert pl._is_garbage_topic("帮我研究", "帮我研究") is True
    assert pl._is_garbage_topic("请帮我写一篇论文") is True  # 指令性客套开头
    assert pl._is_garbage_topic("射频指纹识别", "请你帮我开展关于射频指纹识别的研究") is False
    assert pl._is_garbage_topic("研究图像分割") is False


def test_extract_research_plan_needs_clarification_keeps_topic_empty():
    """LLM 返回 needs_clarification=true (topic 空) → 保持空主题, 不回退正则"""
    from types import SimpleNamespace

    import src.config as cfg

    orig = cfg.build_llm

    def fake_llm(key="main"):
        return SimpleNamespace(invoke=lambda msgs: SimpleNamespace(
            content='{"topic": "", "keywords": [], "sub_topics": [], "time_range": "", '
                   '"stages": [], "reuse_previous": false, "needs_clarification": true}'
        ))

    cfg.build_llm = fake_llm
    try:
        plan = pl._extract_research_plan("请你帮我开展研究")
        assert plan["topic"] == ""  # 不回退成垃圾主题
        assert plan["needs_clarification"] is True
    finally:
        cfg.build_llm = orig


def test_research_planner_clarification_roundtrip():
    """主题无法确定时反问澄清: interrupt → 用户输入主题 → 主题被采用"""
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    import src.utils.pipeline_cache as pc

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic

    def fake_extract(request, topic_hint=""):
        # 初始指令无主题 → 返回垃圾主题触发澄清; 澄清回答 → 直接当作主题
        if request == "帮我研究":
            return {"topic": "帮我研究", "keywords": [], "sub_topics": [],
                    "time_range": "", "stages": ["research", "write"], "reuse_previous": False}
        return {"topic": request, "keywords": [], "sub_topics": [],
                "time_range": "", "stages": ["research", "write"], "reuse_previous": False}

    pl._extract_research_plan = fake_extract
    pc.resolve_cache_topic = lambda t: None
    try:
        g = StateGraph(pl.PipelineState)
        g.add_node("planner", pl.research_planner_node)
        g.set_entry_point("planner")
        g.add_edge("planner", END)
        app = g.compile(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t-clarify"}}
        state = {"research_request": "帮我研究", "interactive": True, "skip_retrieval": False}
        events = list(app.stream(state, cfg))
        assert "__interrupt__" in events[-1]
        assert events[-1]["__interrupt__"][0].value["type"] == "clarify"
        for _ in app.stream(Command(resume="射频指纹识别"), cfg):
            pass
        final = app.get_state(cfg).values
        assert final["research_topic"] == "射频指纹识别"
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve


def test_research_planner_no_clarification_when_not_interactive():
    """非交互模式下不反问, 主题直接兜底"""
    import src.utils.pipeline_cache as pc

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic
    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "帮我研究", "keywords": [], "sub_topics": [],
        "time_range": "", "stages": ["research", "write"], "reuse_previous": False,
    }
    pc.resolve_cache_topic = lambda t: None
    try:
        out = pl.research_planner_node({"research_request": "帮我研究", "interactive": False, "skip_retrieval": False})
        assert out["research_topic"] == "帮我研究"  # 不反问, 用请求兜底
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve


def test_research_planner_explicit_topic_priority():
    """显式主题 (上下文切换) 优先于 LLM 提取的主题, 不被覆盖"""
    import tempfile
    from pathlib import Path

    import src.utils.pipeline_cache as pc
    import src.utils.session_memory as sm

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic
    orig_mem = sm.MEMORY_FILE

    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "垃圾主题", "keywords": [], "sub_topics": [],
        "time_range": "", "stages": ["write"], "reuse_previous": True,
    }
    pc.resolve_cache_topic = lambda t: None
    sm.MEMORY_FILE = Path(tempfile.mkdtemp()) / "mem.json"
    try:
        state = {
            "research_request": "继续撰写",
            "research_topic": "射频指纹识别技术研究",  # 显式主题 (上下文切换)
            "skip_retrieval": False,
        }
        out = pl.research_planner_node(state)
        assert out["research_topic"] == "射频指纹识别技术研究"  # 显式主题保留
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve
        sm.MEMORY_FILE = orig_mem


def test_list_contexts():
    """list_contexts 返回缓存上下文列表 (主题 + 进度)"""
    import tempfile
    from pathlib import Path

    import src.utils.pipeline_cache as pc

    orig_dir = pc.CACHE_DIR
    tmp = Path(tempfile.mkdtemp())
    pc.CACHE_DIR = tmp
    try:
        refs = [{"ref_number": i} for i in range(25)]
        pc.save_retrieval_cache("主题A", "笔记A", refs, stages=["research"])
        pc.save_retrieval_cache("主题B", "笔记B", refs, stages=["research", "write"])
        ctxs = pc.list_contexts()
        topics = {c["topic"]: c for c in ctxs}
        assert "主题A" in topics and "主题B" in topics
        assert topics["主题A"]["stages"] == ["research"]
        assert topics["主题B"]["stages"] == ["research", "write"]
        assert topics["主题A"]["refs"] == 25
    finally:
        pc.CACHE_DIR = orig_dir


def test_citation_precheck_saves_cache_with_result_refs():
    """citation_precheck 从 run_citation_precheck 的 result 取 verified_references 存缓存"""
    import src.utils.pipeline_cache as pc

    orig_run = pl.run_citation_precheck
    orig_save = pl.save_file
    orig_save_cache = pc.save_retrieval_cache
    captured = {}

    def fake_precheck(state):
        return {"citation_precheck_report": "报告",
                "verified_references": [{"ref_number": 1, "title": "T", "doi": "10.1/x"}]}

    pl.run_citation_precheck = fake_precheck
    pl.save_file = lambda content, name: name

    def fake_save_cache(**kwargs):
        captured.update(kwargs)
        return "path"

    pc.save_retrieval_cache = fake_save_cache
    try:
        state = {"research_topic": "测试", "literature_review_notes": "笔记"}
        pl.citation_precheck_node(state)
        # verified_references 必须来自 result (1 篇), 而非空的输入 state (0 篇)
        assert len(captured["verified_references"]) == 1
    finally:
        pl.run_citation_precheck = orig_run
        pl.save_file = orig_save
        pc.save_retrieval_cache = orig_save_cache


def test_guess_stages_figures():
    assert pl._guess_stages("重新生成图片") == ["figures"]
    assert pl._guess_stages("重新生成图表") == ["figures"]
    assert pl._guess_stages("生成插图") == ["figures"]


def test_supervisor_dispatches_figures():
    assert pl.supervisor_node({"plan_confirmed": True, "stages": ["figures"], "stage_index": 0})["supervisor_next"] == "regenerate_figures"


def test_research_planner_figures_reuses_context():
    """「重新生成图片」→ figures 动作 + 复用上下文主题 (不反问)"""
    import tempfile
    from pathlib import Path

    import src.utils.pipeline_cache as pc
    import src.utils.session_memory as sm

    orig_extract = pl._extract_research_plan
    orig_resolve = pc.resolve_cache_topic
    orig_load = pc.load_retrieval_cache
    orig_mem = sm.MEMORY_FILE
    orig_save = pl.save_file

    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "重新生成图片", "keywords": [], "sub_topics": [],
        "time_range": "", "stages": ["figures"], "reuse_previous": False,
    }
    sm.MEMORY_FILE = Path(tempfile.mkdtemp()) / "mem.json"
    sm.save_session_memory("射频指纹识别技术研究", ["research"])
    pc.resolve_cache_topic = lambda t: ("射频指纹识别技术研究" if t == "射频指纹识别技术研究" else None)
    pc.load_retrieval_cache = lambda topic: {
        "topic": "射频指纹识别技术研究",
        "literature_review_notes": "笔记",
        "verified_references": [{"ref_number": i} for i in range(25)],
        "retrieved_papers": [], "unfiltered_papers": [],
    }
    pl.save_file = lambda content, name: name
    try:
        out = pl.research_planner_node({"research_request": "重新生成图片", "interactive": False, "skip_retrieval": False})
        assert out["stages"] == ["figures"]
        assert out["research_topic"] == "射频指纹识别技术研究"  # 复用上下文主题
        assert out["skip_retrieval"] is True  # 命中缓存
    finally:
        pl._extract_research_plan = orig_extract
        pc.resolve_cache_topic = orig_resolve
        pc.load_retrieval_cache = orig_load
        sm.MEMORY_FILE = orig_mem
        pl.save_file = orig_save


def test_format_plan_summary():
    s = pl._format_plan_summary("射频指纹识别", ["RF fingerprinting", "RFFI"], ["特征提取"], ["research", "write"])
    assert "射频指纹识别" in s
    assert "RF fingerprinting" in s
    assert "特征提取" in s
    assert "撰写完整综述论文" in s


def test_research_planner_extracts_from_request():
    orig = pl._extract_research_plan
    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "射频指纹识别",
        "keywords": ["RF fingerprinting", "RFFI"],
        "sub_topics": ["特征提取", "分类识别"],
        "time_range": "",
    }
    try:
        state = {"research_request": "我想研究射频指纹识别", "skip_retrieval": False}
        out = pl.research_planner_node(state)
        assert out["research_topic"] == "射频指纹识别"
        assert out["topic_keywords"] == ["RF fingerprinting", "RFFI"]
        assert out["sub_topics"] == ["特征提取", "分类识别"]
    finally:
        pl._extract_research_plan = orig


def test_research_planner_legacy_precise_path():
    """无自然语言请求时, 沿用精确填写的主题/关键词"""
    state = {
        "research_request": "",
        "research_topic": "图像分割",
        "topic_keywords": ["语义分割"],
        "sub_topics": ["U-Net"],
        "skip_retrieval": False,
    }
    out = pl.research_planner_node(state)
    assert out["research_topic"] == "图像分割"
    assert out["topic_keywords"] == ["语义分割"]
    assert out["sub_topics"] == ["U-Net"]


def test_confirm_plan_confirm_clears_correction():
    g = StateGraph(PipelineState)
    g.add_node("confirm", pl.human_confirm_plan_node)
    g.set_entry_point("confirm")
    g.add_edge("confirm", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-plan-ok"}}
    state = {"interactive": True, "research_topic": "T", "topic_keywords": ["k"], "sub_topics": ["s"]}
    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1]
    for _ in app.stream(Command(resume=""), cfg):
        pass
    final = app.get_state(cfg).values
    assert final.get("plan_correction", "") == ""


def test_confirm_plan_correction_loops_back():
    g = StateGraph(PipelineState)
    g.add_node("confirm", pl.human_confirm_plan_node)
    g.set_entry_point("confirm")
    g.add_edge("confirm", END)
    app = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-plan-fix"}}
    state = {"interactive": True, "research_topic": "T", "topic_keywords": ["k"], "sub_topics": ["s"]}
    events = list(app.stream(state, cfg))
    assert "__interrupt__" in events[-1]
    for _ in app.stream(Command(resume="把子主题改成联邦学习、边缘计算"), cfg):
        pass
    final = app.get_state(cfg).values
    assert "联邦学习" in final["plan_correction"]
    assert final["plan_iteration"] == 1


def test_confirm_plan_not_interactive_auto_confirms():
    assert pl.human_confirm_plan_node({"interactive": False}) == {"plan_confirmed": True}


def test_start_node_requires_topic_or_request():
    assert "error" in pl.start_node({"research_topic": "", "research_request": ""})
    assert pl.start_node({"research_topic": "T"}) == {"current_phase": "start"}
    assert pl.start_node({"research_request": "R"}) == {"current_phase": "start"}


def test_extract_research_plan_falls_back_on_invalid_llm():
    """LLM 返回非 JSON / 无效结果时, 规则兜底提取核心主题 (而不是把整段描述当主题)"""
    from types import SimpleNamespace

    import src.config as cfg

    orig = cfg.build_llm

    def fake_llm(key="main"):
        return SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content="这是一段没有 JSON 的普通回复"))

    cfg.build_llm = fake_llm
    try:
        plan = pl._extract_research_plan(
            "我希望开展关于射频指纹的主题研究，请你根据我的意图生成研究计划，组合关键词和子主题"
        )
        assert plan["topic"] == "射频指纹"
    finally:
        cfg.build_llm = orig


def test_supervisor_loop_end_to_end_order():
    """监督者循环端到端: start→supervisor→planner→confirm→supervisor→research→supervisor→write→supervisor→end"""
    from langgraph.graph import StateGraph, END

    visited = []

    def fake_research(state):
        visited.append("research")
        return {"literature_review_notes": "notes", "verified_references": [{"ref_number": 1}]}

    def fake_write(state):
        visited.append("write")
        return {"paper_draft": "draft"}

    orig = pl._extract_research_plan
    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "射频指纹", "keywords": ["RFFI"], "sub_topics": [],
        "time_range": "", "stages": ["research", "write"],
    }
    try:
        g = StateGraph(pl.PipelineState)
        g.add_node("start", lambda s: {"current_phase": "start"})
        g.add_node("supervisor", pl.supervisor_node)
        g.add_node("research_planner", pl.research_planner_node)
        g.add_node("human_confirm_plan", pl.human_confirm_plan_node)
        g.add_node("literature_review", fake_research)
        g.add_node("outline_generation", fake_write)
        g.set_entry_point("start")
        g.add_edge("start", "supervisor")
        g.add_conditional_edges("supervisor", pl.route_supervisor, {
            "research_planner": "research_planner",
            "literature_review": "literature_review",
            "outline_generation": "outline_generation",
            "end": END,
        })
        g.add_edge("research_planner", "human_confirm_plan")
        g.add_conditional_edges("human_confirm_plan", pl.route_after_plan, {
            "research_planner": "research_planner",
            "supervisor": "supervisor",
        })
        g.add_edge("literature_review", "supervisor")
        g.add_edge("outline_generation", "supervisor")
        app = g.compile()

        result = app.invoke({"research_request": "我想研究射频指纹", "interactive": False})
        assert visited == ["research", "write"]
        assert result["research_topic"] == "射频指纹"
    finally:
        pl._extract_research_plan = orig


def test_supervisor_loop_partial_task_stops_after_research():
    """局部任务: 只跑 research, 不写论文"""
    from langgraph.graph import StateGraph, END

    visited = []

    def fake_research(state):
        visited.append("research")
        return {"literature_review_notes": "notes"}

    def fake_write(state):
        visited.append("write")
        return {}

    orig = pl._extract_research_plan
    pl._extract_research_plan = lambda request, topic_hint="": {
        "topic": "射频指纹", "keywords": [], "sub_topics": [], "time_range": "", "stages": ["research"],
    }
    try:
        g = StateGraph(pl.PipelineState)
        g.add_node("start", lambda s: {"current_phase": "start"})
        g.add_node("supervisor", pl.supervisor_node)
        g.add_node("research_planner", pl.research_planner_node)
        g.add_node("human_confirm_plan", pl.human_confirm_plan_node)
        g.add_node("literature_review", fake_research)
        g.add_node("outline_generation", fake_write)
        g.set_entry_point("start")
        g.add_edge("start", "supervisor")
        g.add_conditional_edges("supervisor", pl.route_supervisor, {
            "research_planner": "research_planner",
            "literature_review": "literature_review",
            "outline_generation": "outline_generation",
            "end": END,
        })
        g.add_edge("research_planner", "human_confirm_plan")
        g.add_conditional_edges("human_confirm_plan", pl.route_after_plan, {
            "research_planner": "research_planner",
            "supervisor": "supervisor",
        })
        g.add_edge("literature_review", "supervisor")
        g.add_edge("outline_generation", "supervisor")
        app = g.compile()

        app.invoke({"research_request": "帮我检索并总结文献", "interactive": False})
        assert visited == ["research"]  # 不写论文
    finally:
        pl._extract_research_plan = orig


def test_extract_research_plan_rejects_full_request_as_topic():
    """LLM 把整段输入原样当 topic 返回时, 也要回退规则提取"""
    from types import SimpleNamespace

    import src.config as cfg

    orig = cfg.build_llm
    request = "我希望开展关于射频指纹的主题研究，请你根据我的意图生成研究计划"

    def fake_llm(key="main"):
        return SimpleNamespace(invoke=lambda msgs: SimpleNamespace(
            content='{"topic": "' + request + '", "keywords": [], "sub_topics": [], "time_range": ""}'
        ))

    cfg.build_llm = fake_llm
    try:
        plan = pl._extract_research_plan(request)
        assert plan["topic"] == "射频指纹"
    finally:
        cfg.build_llm = orig


if __name__ == "__main__":
    from src.utils.console import ensure_utf8_console

    ensure_utf8_console()
    tests = [
        test_route_after_plan,
        test_route_supervisor,
        test_supervisor_dispatches_full_pipeline_in_order,
        test_supervisor_partial_task_research_only,
        test_supervisor_skip_retrieval_goes_straight_to_write,
        test_supervisor_requires_plan_first,
        test_guess_stages,
        test_split_cn_en,
        test_normalize_subtopics,
        test_research_planner_splits_cn_en_subtopics_into_keywords,
        test_detect_use_cache,
        test_guess_stages_continuation_write_only,
        test_guess_stages_report_is_not_write,
        test_guess_stages_negation,
        test_guess_stages_figures,
        test_supervisor_dispatches_figures,
        test_research_planner_figures_reuses_context,
        test_guess_topic_strips_quotes,
        test_research_planner_continuation_skips_research,
        test_extract_research_plan_outputs_reuse_previous,
        test_session_memory_save_load,
        test_research_planner_uses_session_memory_for_continuation,
        test_research_planner_continuation_falls_back_to_full_research_when_no_cache,
        test_research_planner_clarification_roundtrip,
        test_research_planner_no_clarification_when_not_interactive,
        test_extract_research_plan_needs_clarification_keeps_topic_empty,
        test_is_garbage_topic,
        test_research_planner_explicit_topic_priority,
        test_list_contexts,
        test_citation_precheck_saves_cache_with_result_refs,
        test_resolve_cache_topic_fuzzy,
        test_research_planner_detects_use_cache_and_loads,
        test_format_plan_summary,
        test_research_planner_extracts_from_request,
        test_research_planner_legacy_precise_path,
        test_confirm_plan_confirm_clears_correction,
        test_confirm_plan_correction_loops_back,
        test_confirm_plan_not_interactive_auto_confirms,
        test_start_node_requires_topic_or_request,
        test_extract_research_plan_falls_back_on_invalid_llm,
        test_extract_research_plan_rejects_full_request_as_topic,
        test_supervisor_loop_end_to_end_order,
        test_supervisor_loop_partial_task_stops_after_research,
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
