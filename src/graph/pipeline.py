from __future__ import annotations

import json
from typing import Literal, Optional
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from src.graph.state import PipelineState
from src.agents.literature_reviewer import run_literature_review
from src.agents.paper_writer import run_paper_writing
from src.agents.paper_reviewer import run_paper_review
from src.agents.pdf_ingestor import run_pdf_ingestion
from src.agents.citation_checker import run_citation_check
from src.agents.citation_prechecker import run_citation_precheck
from src.agents.outline_generator import run_outline_generation
from src.rag.format_validator import format_check_report
from src.config import MAX_REVISIONS, REVIEW_ACCEPT_THRESHOLD, STAGNATION_LIMIT, OUTPUT_DIR, LANGFUSE_CONFIG
from src.utils.console import ensure_utf8_console
from src.utils.file_utils import save_file, sanitize_filename, get_timestamp


def _get_langfuse_handler():
    """可选 Langfuse 追踪：配置了 Langfuse key 时启用

    参考: deer-flow 的 LangSmith/Langfuse tracing 集成
    兼容多种配置方式:
    - 新版: LANGFUSE_API_KEY (langfuse >= 4.x)
    - 旧版: LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY
    - host: LANGFUSE_HOST 或 LANGFUSE_BASE_URL
    """
    import os
    import logging as _logging

    # langfuse 4.x 装饰器在 client 未初始化时打印
    # "No Langfuse client ... has been initialized" 噪音 (不影响追踪),
    # 过滤该行避免刷屏
    _lf_logger = _logging.getLogger("langfuse")
    _lf_logger.addFilter(lambda r: "No Langfuse client" not in r.getMessage())

    api_key = os.getenv("LANGFUSE_API_KEY", "")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_HOST", "") or os.getenv("LANGFUSE_BASE_URL", "")

    if not (api_key or (public_key and secret_key)):
        return None
    try:
        # langfuse 4.x 通过环境变量初始化 client (SDK 内部 os.getenv 读取)
        # .env 文件由 dotenv 加载, 需显式注入进程环境变量
        import os as _os

        if api_key:
            _os.environ.setdefault("LANGFUSE_API_KEY", api_key)
            _os.environ.setdefault("LANGFUSE_PUBLIC_KEY", api_key)
        if secret_key:
            _os.environ.setdefault("LANGFUSE_SECRET_KEY", secret_key)
        if public_key and not _os.environ.get("LANGFUSE_PUBLIC_KEY"):
            _os.environ.setdefault("LANGFUSE_PUBLIC_KEY", public_key)
        if host:
            _os.environ.setdefault("LANGFUSE_HOST", host)

        # langfuse 4.x: 必须先初始化全局 client (get_client), 否则 handler 报
        # "No Langfuse client ... has been initialized"
        from langfuse import get_client as _get_lf_client

        _get_lf_client(public_key=public_key or api_key)

        from langfuse.langchain import CallbackHandler

        if public_key:
            return CallbackHandler(public_key=public_key)
        return CallbackHandler(public_key=api_key)
    except ImportError:
        print("  [warning] langfuse 未安装，跳过追踪 (pip install langfuse)")
        return None
    except Exception as e:
        print(f"  [warning] langfuse 初始化失败 ({e})，跳过追踪")
        return None


PhaseName = Literal[
    "start",
    "literature_review",
    "pdf_ingestion",
    "citation_precheck",
    "paper_writing",
    "citation_check",
    "paper_review",
]

# 修订时回喂 Writer 的上一版论文预算: 必须容纳 20000 字上限的完整正文
# (含空白/标记约 2x), 截断会导致 Writer 无法对中段章节执行结构性修改
PRIOR_DRAFT_BUDGET = 60000


def start_node(state: PipelineState) -> dict:
    topic = state.get("research_topic", "")
    request = state.get("research_request", "")
    if not topic and not request:
        return {"error": "research_topic 或 research_request 至少需要一个", "current_phase": "start"}
    return {"current_phase": "start"}


# 计划确认阶段重提取的最大次数 (防止"改了又改"死循环)
MAX_PLAN_ITERATIONS = 2


def _format_plan_summary(topic: str, keywords: list[str], sub_topics: list[str], stages: list[str] | None = None) -> str:
    lines = [f"## 研究计划\n\n- **主题**: {topic}"]
    if keywords:
        lines.append("- **关键词**: " + ", ".join(keywords))
    if sub_topics:
        lines.append("- **子主题**: " + ", ".join(sub_topics))
    if stages:
        scope = _format_stages(stages)
        lines.append("- **任务范围**: " + scope)
    lines.append("\n（确认无误后回车开始；如需调整，请说明）")
    return "\n".join(lines)


def _format_stages(stages: list[str]) -> str:
    labels = {
        "research": "检索文献 + 生成综述总结",
        "write": "撰写完整综述论文",
        "figures": "重新生成图表",
    }
    return " → ".join(labels.get(s, s) for s in stages)


def _guess_stages(text: str) -> list[str]:
    """规则兜底: 从描述中判断要执行哪些阶段/动作 (局部任务 vs 完整综述)。"""
    import re
    text = text or ""
    # 重新生成图表/图片 → figures 动作 (后处理, 复用已有上下文)
    if re.search(r"(重新)?(生成|绘制|制作|重画).{0,4}(图片|图表|插图|图件|示意图|配图|图)", text):
        return ["figures"]
    # 明确否定撰写 (不需要/不要/不用/无需/免去 写/撰写/成文/论文/综述) → 只检索+总结
    if re.search(r"(不|无需|不必|不用|不要|免去|免于).{0,6}(写|撰写|成文|成稿|论文|综述)", text):
        return ["research"]
    # "写/撰写/成文/成稿" + "论文/综述/文章" = 撰写 (注意: "报告/总结/大纲" 不算撰写)
    wants_write = re.search(r"(写|撰写|成文|成稿|输出)", text) and re.search(r"(论文|综述|文章)", text)
    has_data = _detect_use_cache(text)
    # 明确要求撰写 → 完整综述; 已有数据/报告则跳过检索直接写
    if wants_write:
        return ["write"] if has_data else ["research", "write"]
    # 明确"只要/仅/只想"检索或总结 → 局部任务
    if re.search(r"(只要|只需|仅|只想|就)(检索|查找|总结|调研|梳理)", text):
        return ["research"]
    # 只提到检索/总结/调研/生成报告, 未提撰写 → 局部任务 (检索+总结)
    if re.search(r"(检索|查找|调研|总结|综述笔记|文献综述|报告|汇报)", text) and not re.search(r"(写|撰写|论文)", text):
        return ["research"]
    return ["research", "write"]


def _detect_use_cache(text: str) -> bool:
    """规则判定: 用户是否表示"检索已完成/已有数据/用缓存" (从而跳过检索)。"""
    import re
    text = text or ""
    if re.search(r"检索.{0,8}(已|已经)?(完成|做完|结束|好了|过了)", text):
        return True
    if re.search(r"(查看|复用|使用|利用|用|读取|直接).{0,6}(缓存|cache)", text, re.IGNORECASE):
        return True
    if re.search(r"(已有|现成|有).{0,4}(缓存|cache)", text, re.IGNORECASE):
        return True
    # 已有数据/报告/材料 (如「结合已有的数据和报告」)
    if re.search(r"(已有|现有|现成|之前|前面|上面).{0,12}(数据|报告|材料|笔记|文献|资料|结果|总结)", text):
        return True
    if re.search(r"(结合|基于|利用|使用|沿用).{0,6}(已有|现有|之前|前面)", text):
        return True
    # 已收集/已搜集/已检索/已整理/已汇总 (到的信息/数据/文献)
    if re.search(r"(已|已经)(收集|搜集|检索|查找|整理|汇总|获取).{0,10}(信息|数据|文献|资料|笔记|结果|材料)", text):
        return True
    return False


def _split_cn_en(text: str) -> tuple[str, str]:
    """把「中文（英文）」/「中文 (English)」拆成 (中文, 英文)。

    用于子主题扁平化: 中文名保留在子主题, 英文检索词并入关键词。
    """
    import re
    text = (text or "").strip()
    m = re.match(r"^\s*(.+?)\s*[（(]\s*([A-Za-z][^（）()]*)\s*[)）]\s*$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return text, ""


def _normalize_subtopics(sub_topics: list[str], keywords: list[str]) -> tuple[list[str], list[str]]:
    """子主题扁平化: 拆出括号里的英文并入关键词, 子主题只保留中文名。

    - 子主题「射频指纹提取方法 (RF fingerprint extraction methods)」
      → 子主题「射频指纹提取方法」, 关键词追加「RF fingerprint extraction methods」
    - 无括号的子主题原样保留
    """
    cn_list: list[str] = []
    kw = list(keywords or [])
    seen = {k.lower() for k in kw}
    for s in sub_topics or []:
        cn, en = _split_cn_en(s)
        if cn:
            cn_list.append(cn)
        if en and en.lower() not in seen:
            kw.append(en)
            seen.add(en.lower())
    return cn_list, kw


def _strip_quotes(text: str) -> str:
    """去掉主题两端包裹的引号/括号 (含全角弯引号 “ ” ‘ ’、书名号等)。"""
    t = (text or "").strip()
    for ch in ('"', "'", "\u201c", "\u201d", "\u2018", "\u2019",
               "「", "」", "『", "』", "《", "》", "<", ">", "(", ")", "（", "）"):
        t = t.strip(ch)
    return t.strip("，。；;、 ,")


def _is_garbage_topic(topic: str, request: str = "") -> bool:
    """判断主题是否为「垃圾」——提取/兜底得到的是指令本身, 而非真实研究主题。

    用于触发反问澄清: 空 / 过长 / 与指令相同或高度相似 / 以指令性客套开头且很短。
    """
    topic = (topic or "").strip()
    if not topic or len(topic) > 50:
        return True
    req = (request or "").strip()
    if req and topic == req:
        return True
    if req:
        try:
            from difflib import SequenceMatcher

            if SequenceMatcher(None, topic, req).ratio() >= 0.6:
                return True
        except ImportError:
            pass
    import re

    # 以指令性客套/动词开头且很短 → 疑似指令 (如「你帮我开展研究」「请帮我写」)
    if re.match(r"^(帮我|请你|请|麻烦你|你|我们|开展|撰写|写|生成|检索|总结|梳理|进行|做|能否|可以)", topic) and len(topic) <= 12:
        return True
    return False


def _extract_research_plan(request: str, topic_hint: str = "") -> dict:
    """用廉价 LLM 从自然语言描述中提取结构化研究计划。

    返回 {"topic", "keywords", "sub_topics", "time_range", "stages"}。
    关键词必须包含用户给出的英文检索形式与缩写 (用于英文学术库检索)。
    LLM 返回无效/失败时, 用规则回退提取主题与任务范围 (避免把整段描述当主题)。
    """
    import re
    import json

    def _to_text(result) -> str:
        content = getattr(result, "content", None)
        if content is None:
            return str(result)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(getattr(c, "text", c)) for c in content)
        return str(content)

    def _guess_topic(text: str) -> str:
        """规则兜底: 从「关于 X 的 Y」「开展 X 研究」等句式提取核心主题。"""
        text = (text or "").strip()
        m = re.search(r"关于\s*(.{2,40}?)\s*的\s*(?:研究|综述|主题|方向|领域|课题|现状|进展)", text)
        if m:
            return _strip_quotes(m.group(1))
        m = re.search(r"(?:开展|进行|做|写)\s*(.{2,40}?)\s*(?:的)?\s*(?:研究|综述)", text)
        if m:
            return _strip_quotes(m.group(1))
        # 去掉客套/指令前缀, 取剩余前 40 字
        cleaned = re.sub(r"^(我希望|我想|我打算|请|请你|帮我|麻烦你|你)(?:帮我)?(?:开展|研究|写|生成|总结|综述|做|梳理)?", "", text)
        return _strip_quotes(cleaned[:40])

    raw_text = ""
    topic, keywords, sub_topics, time_range, stages = "", [], [], "", []
    reuse_previous, needs_clarification = False, False
    try:
        from src.config import build_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = build_llm("cheap")
        prompt = (
            "请从下面的用户研究描述中提取结构化研究计划。\n\n"
            "注意: 用户描述可能包含客套或指令性语句 (如「请你根据我的意图生成研究计划」),"
            " 请忽略这些客套, 只提取**实际的研究对象与任务范围**。\n\n"
            "要求：\n"
            "1. topic: 研究主题 (中文, 简洁, 去除冗余客套)。\n"
            "2. keywords: 核心关键词列表, **必须同时包含中文术语、其英文检索形式与缩写**"
            " (例如「射频指纹识别」要同时给出「RF fingerprinting」「RFFI」「radio frequency fingerprint identification」),"
            " 因为后续要在 arXiv/Semantic Scholar/OpenAlex 等英文库检索。\n"
            "3. sub_topics: 用户希望覆盖的研究方面/子主题列表, 每个用「中文名（英文检索词）」格式,"
            " 英文检索词便于后续在 arXiv/Semantic Scholar/OpenAlex 等英文库检索。\n"
            "4. time_range: 若用户提及年份范围则提取 (如 2019-2026), 否则留空字符串。\n"
            "5. stages: 用户希望执行的任务阶段/动作, 从以下取值:\n"
            "   - \"research\" = 检索文献并生成综述总结 (文献综述/调研)\n"
            "   - \"write\" = 撰写完整综述论文\n"
            "   - \"figures\" = 重新生成图表/图片 (对已有综述笔记/数据重新绘图)\n"
            "   若用户只要求检索/总结/调研而不写论文 → [\"research\"];\n"
            "   若要求写综述论文且未表示已有数据 → [\"research\", \"write\"];\n"
            "   若用户明确表示已有数据/报告/缓存/检索已完成, 只需撰写 → [\"write\"];\n"
            "   若用户要求重新生成图表/图片 → [\"figures\"], 且 reuse_previous=true (复用已有上下文)。\n"
            "6. reuse_previous: 布尔值, 用户是否表示要复用之前会话的成果"
            " (如「已有数据/报告/材料/缓存」「检索已完成」「结合之前的」「继续」),"
            " 即跳过重新检索、沿用已有数据。\n"
            "7. needs_clarification: 布尔值, 若用户意图模糊 (如「撰写综述」但未指明主题、"
            "且明显依赖上下文) 则为 true, 否则 false。\n\n"
            '输出 JSON: {"topic": "...", "keywords": [...], "sub_topics": [...], "time_range": "...", "stages": [...], "reuse_previous": false, "needs_clarification": false}\n'
            "只输出 JSON, 不要解释。\n\n"
            f"用户描述:\n{request}"
        )
        result = llm.invoke([
            SystemMessage(content="你是研究计划提取助手, 只输出 JSON。"),
            HumanMessage(content=prompt),
        ])
        raw_text = _to_text(result)
        m = re.search(r"\{.*\}", raw_text, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        topic = _strip_quotes(data.get("topic") or "")
        keywords = [str(x).strip() for x in (data.get("keywords") or []) if str(x).strip()]
        sub_topics = [str(x).strip() for x in (data.get("sub_topics") or []) if str(x).strip()]
        time_range = (data.get("time_range") or "").strip()
        raw_stages = data.get("stages") or []
        stages = [s for s in (str(x).strip() for x in raw_stages) if s in ("research", "write", "figures")]
        reuse_previous = bool(data.get("reuse_previous", False))
        needs_clarification = bool(data.get("needs_clarification", False))
    except Exception as e:
        print(f"  [planner] 计划提取 LLM 失败, 回退规则提取: {e}")

    # 主题校验
    if needs_clarification:
        # LLM 明确表示意图模糊、需要澄清 → 保持主题为空, 交由 planner 反问澄清 (不回退正则)
        topic = ""
    elif not topic or topic == request.strip() or len(topic) > 50:
        # LLM 未提取到有效主题 (空/等于整段输入/过长) → 规则兜底
        if raw_text:
            print(f"  [planner] LLM 未提取到有效主题, 回退规则提取")
        topic = _guess_topic(request) or topic_hint or request
    if not stages:
        stages = _guess_stages(request)
    # reuse_previous 正则兜底: LLM 漏判时仍能识别"已有数据/用缓存"
    if not reuse_previous:
        reuse_previous = _detect_use_cache(request)

    return {
        "topic": topic, "keywords": keywords, "sub_topics": sub_topics,
        "time_range": time_range, "stages": stages,
        "reuse_previous": reuse_previous, "needs_clarification": needs_clarification,
    }


def research_planner_node(state: PipelineState) -> dict:
    """入口 Planner: 解析自然语言意图 → 结构化指令 (主题/关键词/子主题/任务阶段)。

    同时在主题确定后处理 skip-retrieval 缓存加载 (原入口处逻辑迁移至此,
    因为自然语言路径下主题要等 Planner 提取后才已知)。
    """
    request = (state.get("research_request") or "").strip()
    correction = (state.get("plan_correction") or "").strip()
    # 用户显式指定的主题 (上下文切换/主题框), 优先级高于 LLM 从指令中提取的主题
    explicit_topic = state.get("research_topic", "").strip()
    topic = explicit_topic
    keywords = list(state.get("topic_keywords", []) or [])
    sub_topics = list(state.get("sub_topics", []) or [])
    time_range = state.get("time_range", "2019-2026")
    stages = list(state.get("stages", []) or [])
    reuse_previous = False

    if request:
        full = request if not correction else f"{request}\n用户修正意见: {correction}"
        print("  [planner] 解析自然语言研究意图...")
        plan = _extract_research_plan(full, topic_hint=topic)
        # 无显式主题时, 才采用 LLM 从指令提取的主题 (显式主题优先)
        if plan.get("topic") and not explicit_topic:
            topic = plan["topic"]
        if plan.get("keywords"):
            keywords = plan["keywords"]
        if plan.get("sub_topics"):
            sub_topics = plan["sub_topics"]
        if plan.get("time_range"):
            time_range = plan["time_range"]
        if plan.get("stages"):
            stages = plan["stages"]
        if plan.get("reuse_previous"):
            reuse_previous = plan["reuse_previous"]

    # 后处理类动作 (figures 等) 天然作用于当前上下文 → 复用已有成果 (不重新检索)
    if any(s in _POST_ACTIONS for s in stages):
        reuse_previous = True

    if not topic:
        topic = request

    # 扁平化: 子主题「中文（英文）」→ 拆出英文并入关键词, 子主题只留中文名
    # (覆盖 LLM 提取与用户手动填写两条路径, 保证英文检索词进入扁平关键词列表)
    sub_topics, keywords = _normalize_subtopics(sub_topics, keywords)

    # 复用之前成果 (reuse_previous) → 若无显式主题, 主题来自上下文 (会话记忆 → 最近缓存),
    # 而非指令本身 (指令如「结合收集到的信息撰写」不含真实主题, LLM 提取的常是垃圾主题)
    if reuse_previous and not explicit_topic:
        try:
            from src.utils.session_memory import load_session_memory
            from src.utils.pipeline_cache import latest_cache_topic

            mem = load_session_memory()
            mem_topic = (mem or {}).get("topic", "") if mem else ""
            ctx = mem_topic or latest_cache_topic()
            if ctx:
                topic = ctx
                print(f"  [planner] 复用之前成果, 沿用上下文主题「{topic}」")
            else:
                print("  [planner] 复用之前成果, 但无可用上下文 (缓存已清空且无会话记忆)")
        except Exception:
            pass

    # 主题仍未解决 (垃圾主题) 且为交互模式 → 反问澄清
    # (复用之前成果时优先从上下文解析, 无上下文可用才会走到这里)
    if state.get("interactive") and _is_garbage_topic(topic, request):
        answer = interrupt({
            "type": "clarify",
            "phase": "research_planner",
            "title": "需要澄清研究主题",
            "content": "我无法从你的描述中确定研究主题。请告诉我你想研究的具体主题。",
            "hint": "请输入研究主题或完整指令（如「射频指纹识别」）；q 停止",
        })
        answer = (answer or "").strip()
        if answer and not _is_quit(answer) and not _is_confirm(answer):
            try:
                plan2 = _extract_research_plan(answer)
                if plan2.get("topic"):
                    topic = plan2["topic"]
                if plan2.get("keywords"):
                    keywords = plan2["keywords"]
                if plan2.get("sub_topics"):
                    sub_topics = plan2["sub_topics"]
                if plan2.get("stages"):
                    stages = plan2["stages"]
                if plan2.get("reuse_previous"):
                    reuse_previous = plan2["reuse_previous"]
                if plan2.get("time_range"):
                    time_range = plan2["time_range"]
                sub_topics, keywords = _normalize_subtopics(sub_topics, keywords)
                print(f"  [planner] 澄清后重新提取: 主题「{topic}」, 任务范围 {_format_stages(stages)}")
            except Exception:
                topic = _strip_quotes(answer)
                print(f"  [planner] 澄清后主题: {topic}")

    # 尝试加载缓存 (skip-retrieval 或 reuse_previous), 确定能否跳过检索
    cached = None
    resolved = None
    if state.get("skip_retrieval") or reuse_previous:
        try:
            from src.utils.pipeline_cache import load_retrieval_cache, resolve_cache_topic

            resolved = resolve_cache_topic(topic)
            cached = load_retrieval_cache(resolved) if resolved else None
        except Exception:
            cached = None
            resolved = None

    cache_hit = cached is not None

    # 根据缓存命中情况确定最终任务范围 (先定 cache, 再定 stages, 避免日志前后矛盾)
    if reuse_previous and "write" in stages:
        if cache_hit:
            stages = ["write"]
            print("  [planner] 复用已有数据/报告, 跳过检索直接进入撰写")
        else:
            stages = ["research", "write"]
            print("  [planner] 无可用缓存可复用, 恢复为完整流程 (检索 + 撰写)")

    print(f"  [planner] 主题: {topic}")
    if keywords:
        print(f"  [planner] 关键词: {', '.join(keywords)}")
    if sub_topics:
        print(f"  [planner] 子主题: {', '.join(sub_topics)}")
    if stages:
        print(f"  [planner] 任务范围: {_format_stages(stages)}")

    out: dict = {
        "research_topic": topic,
        "topic_keywords": keywords,
        "sub_topics": sub_topics,
        "time_range": time_range,
        "stages": stages,
        "stage_index": 0,
        "plan_confirmed": False,
        "current_phase": "research_planner",
    }

    if cache_hit and cached and resolved:
        print(f"  [planner] 命中检索缓存「{resolved}」"
              f" (已验证引用 {len(cached.get('verified_references', []))} 篇), 跳过检索")
        out["literature_review_notes"] = cached.get("literature_review_notes", "")
        out["verified_references"] = cached.get("verified_references", [])
        out["retrieved_papers"] = cached.get("retrieved_papers", [])
        out["unfiltered_papers"] = cached.get("unfiltered_papers", [])
        out["skip_retrieval"] = True
        # 仅当"检索+总结"是唯一任务时, 才把综述笔记落盘 (它是该任务的产物);
        # 对 write/figures 等后处理动作, 笔记已在 outputs/ 中, 重存会产生重复文件与误导
        if stages == ["research"]:
            notes = cached.get("literature_review_notes", "")
            if notes:
                try:
                    safe_name = sanitize_filename(resolved or topic)
                    ts = get_timestamp()
                    out["literature_notes_path"] = save_file(
                        notes, f"literature_review_notes_{safe_name}_{ts}.md",
                        subdir=state.get("run_id"),
                    )
                except Exception:
                    pass
    else:
        out["skip_retrieval"] = False

    return out


def human_confirm_plan_node(state: PipelineState) -> dict:
    """计划确认暂停点: 展示提取结果 (主题/关键词/子主题/任务范围), 用户确认 / 修正 / 停止。"""
    if not state.get("interactive"):
        return {"plan_confirmed": True}
    topic = state.get("research_topic", "")
    keywords = state.get("topic_keywords", []) or []
    sub_topics = state.get("sub_topics", []) or []
    stages = state.get("stages", []) or []
    response = interrupt({
        "type": "plan",
        "phase": "research_planner",
        "title": "研究计划已生成，请确认",
        "content": _format_plan_summary(topic, keywords, sub_topics, stages),
        "topic": topic,
        "keywords": keywords,
        "subtopics": sub_topics,
        "stages": stages,
        "hint": "回车/y=确认并开始；输入修正意见=重新提取；q=停止",
    })
    response = (response or "").strip()
    if _is_confirm(response):
        return {"plan_correction": "", "plan_confirmed": True}
    if _is_quit(response):
        return {"plan_correction": "", "plan_confirmed": True}
    it = state.get("plan_iteration", 0) + 1
    if it > MAX_PLAN_ITERATIONS:
        print(f"  [planner] 计划已重提取 {MAX_PLAN_ITERATIONS} 次, 按当前计划继续")
        return {"plan_correction": "", "plan_confirmed": True}
    print("  [planner] 按修正意见重新提取研究计划")
    prev = state.get("plan_correction", "") or ""
    return {"plan_correction": f"{prev}\n{response}".strip(), "plan_iteration": it}


# 阶段名 → 该阶段第一个节点
_STAGE_ENTRY = {
    "research": "literature_review",
    "write": "outline_generation",
    "figures": "regenerate_figures",
}

# 后处理类动作: 天然作用于当前上下文 (复用已有成果), 不重新检索、不反问
_POST_ACTIONS = {"figures"}


def supervisor_node(state: PipelineState) -> dict:
    """主控 Supervisor: 根据任务计划 (stages) 决定下一步调用哪个智能体。

    工作智能体: research (检索+总结), write (撰写综述)。执行完一个阶段后回到
    supervisor, 由其派发下一阶段或结束。skip-retrieval 命中缓存时跳过 research。
    """
    if not state.get("plan_confirmed"):
        return {"supervisor_next": "research_planner"}

    stages = list(state.get("stages") or ["research", "write"])
    idx = int(state.get("stage_index", 0) or 0)

    # 跳过由 skip-retrieval 缓存覆盖的 research 阶段
    while idx < len(stages):
        if stages[idx] == "research" and state.get("skip_retrieval"):
            idx += 1
            continue
        break

    if idx >= len(stages):
        print("  [supervisor] 全部任务完成")
        # 保存会话记忆 (主题 + 已完成阶段), 供后续"继续撰写"等指令复用
        try:
            from src.utils.session_memory import save_session_memory

            save_session_memory(state.get("research_topic", ""), stages)
        except Exception:
            pass
        return {"supervisor_next": "end"}

    stage = stages[idx]
    nxt = _STAGE_ENTRY.get(stage, "end")
    print(f"  [supervisor] 调度: {stage} → {nxt}")
    return {"supervisor_next": nxt, "stage_index": idx + 1}


def route_supervisor(state: PipelineState) -> Literal["research_planner", "literature_review", "outline_generation", "regenerate_figures", "end"]:
    """supervisor 后去向。"""
    return state.get("supervisor_next", "end")


def route_after_plan(state: PipelineState) -> Literal["research_planner", "supervisor"]:
    """计划确认后去向: 有待重提取的修正 → 回 planner; 否则回 supervisor 派发执行。"""
    if state.get("plan_correction"):
        return "research_planner"
    return "supervisor"


def literature_review_node(state: PipelineState) -> dict:
    result = run_literature_review(state)
    notes = result.get("literature_review_notes", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    notes_path = None
    if notes:
        filename = f"literature_review_notes_{safe_name}_{ts}.md"
        notes_path = save_file(notes, filename, subdir=state.get("run_id"))
        result["literature_notes_path"] = notes_path
    return result


def paper_writing_node(state: PipelineState) -> dict:
    import time as _time

    mode = "修订" if state.get("revision_prompt") else "初稿"
    print(f"  [paper_writing] 开始{mode}撰写 (预计 2-10 分钟)...")
    _t0 = _time.monotonic()
    result = run_paper_writing(state)
    print(f"  [paper_writing] {mode}完成, 耗时 {_time.monotonic() - _t0:.0f}s")
    draft = result.get("paper_draft", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if draft and "error" not in result:
        filename = f"draft_paper_{safe_name}_{ts}.md"
        draft_path = save_file(draft, filename, subdir=state.get("run_id"))
        result["draft_path"] = draft_path
    return result


def pdf_ingestion_node(state: PipelineState) -> dict:
    result = run_pdf_ingestion(state)
    report = result.get("ingestion_report", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report:
        filename = f"pdf_ingestion_report_{safe_name}_{ts}.md"
        save_file(report, filename, subdir=state.get("run_id"))
    return result


def citation_guard_node(state: PipelineState) -> dict:
    """引用守门: 写作后校验引用编号是否在可信清单内"""
    from src.agents.citation_guard import run_citation_guard

    result = run_citation_guard(state)
    report = result.get("guard_report", {})
    report_md = report.get("report_md", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report_md:
        filename = f"citation_guard_{safe_name}_{ts}.md"
        save_file(report_md, filename, subdir=state.get("run_id"))
        result["guard_report_path"] = filename
    return result


def citation_check_node(state: PipelineState) -> dict:
    result = run_citation_check(state)
    report = result.get("citation_report", {})
    report_md = report.get("report_md", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report_md:
        filename = f"citation_report_{safe_name}_{ts}.md"
        save_file(report_md, filename, subdir=state.get("run_id"))
        result["citation_report_path"] = filename

    # 保存证据账本
    ledger = result.get("evidence_ledger", "")
    if ledger:
        filename = f"evidence_ledger_{safe_name}_{ts}.md"
        save_file(ledger, filename, subdir=state.get("run_id"))
        result["evidence_ledger_path"] = filename

    # 引用重编号: 按正文首次出现顺序 1..N, 且同步重排 verified_refs。
    # 此前只在 format_check (循环结束后) 重排, 导致审稿人看到的是 ref_number 顺序,
    # 每轮都扣"编号与正文不一致/参考文献未按出现顺序"分。
    # 现在在审稿前完成重排, 并持久化重排后的 refs 供下一轮修订使用。
    try:
        from src.rag.reference_formatter import renumber_draft_and_refs

        draft = state.get("paper_draft", "")
        verified_refs = list(state.get("verified_references", []))
        new_draft, new_refs = renumber_draft_and_refs(draft, verified_refs)
        if new_draft != draft:
            result["paper_draft"] = new_draft
            print("  [citation_check] 引用已重编号 (按首次出现顺序 1..N)")
            # 用重编号后的稿子覆盖 paper_writing 保存的初稿文件,
            # 避免用户看到编号带空档 (如缺 [2][7]) 的旧版草稿
            try:
                draft_path = state.get("draft_path", "")
                if draft_path:
                    from pathlib import Path as _P
                    _P(draft_path).write_text(new_draft, encoding="utf-8")
            except Exception:
                pass
        if new_refs != verified_refs:
            result["verified_references"] = new_refs
    except Exception as e:
        print(f"  [citation_check] 引用重编号跳过: {e}")

    return result


def citation_precheck_node(state: PipelineState) -> dict:
    result = run_citation_precheck(state)
    report = result.get("citation_precheck_report", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report:
        filename = f"citation_precheck_{safe_name}_{ts}.md"
        save_file(report, filename, subdir=state.get("run_id"))
        result["verified_references_path"] = filename

    # research 阶段结束即保存检索产物到缓存, 供"继续撰写"/多上下文切换复用。
    # (此前仅在 outline_generation 保存, 导致"只检索+总结"的会话不产生缓存)
    try:
        from src.utils.pipeline_cache import save_retrieval_cache

        # verified_references 由 run_citation_precheck 产出 (在 result 中), 而非输入 state
        verified_refs = result.get("verified_references", []) or state.get("verified_references", [])
        notes = state.get("literature_review_notes", "")
        if notes or verified_refs:
            save_retrieval_cache(
                topic=topic,
                literature_review_notes=notes,
                verified_references=verified_refs,
                paper_outline="",
                retrieved_papers=list(state.get("retrieved_papers", [])),
                unfiltered_papers=list(state.get("unfiltered_papers", [])),
                keywords=state.get("topic_keywords", []),
                sub_topics=state.get("sub_topics", []),
                time_range=state.get("time_range", ""),
                stages=["research"],
            )
    except Exception:
        pass

    return result


def outline_generation_node(state: PipelineState) -> dict:
    """STORM 式：写作前生成论文大纲，保证结构清晰"""
    import time as _time

    _t0 = _time.monotonic()
    print("  [outline_generation] 开始生成论文大纲 (预计 1-3 分钟)...")
    result = run_outline_generation(state)
    print(f"  [outline_generation] 大纲生成完成, 耗时 {_time.monotonic() - _t0:.0f}s")
    # 人工大纲意见已由 run_outline_generation 读取并写入提示, 此处消费后清空
    result["human_feedback"] = ""
    result["human_feedback_phase"] = ""
    outline = result.get("paper_outline", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if outline and "error" not in result:
        filename = f"paper_outline_{safe_name}_{ts}.md"
        save_file(outline, filename, subdir=state.get("run_id"))
        result["outline_path"] = filename

    # 把大纲增量写入缓存 (research 阶段已在 citation_precheck 保存过检索产物)
    try:
        from src.utils.pipeline_cache import update_retrieval_cache

        if outline:
            update_retrieval_cache(topic, paper_outline=outline)
    except Exception:
        pass

    return result


def format_check_node(state: PipelineState) -> dict:
    """格式规范检查：表格对齐/图表编号/引用格式"""
    draft = state.get("paper_draft", "")
    if not draft:
        return {"current_phase": "format_check", "format_report": {}}

    # 收敛检测: 按“硬门禁 → 评分 → 未解决问题数”的质量向量回退，
    # 避免仅凭波动的单次总分选中仍含引用/结构硬伤的稿件。
    best_draft = state.get("best_draft", "")
    best_score = state.get("best_score", 0)
    cur_score = state.get("review_score", 0)
    best_key = state.get("best_quality_key", []) or []
    cur_key = state.get("review_quality_key", []) or []
    should_restore = best_key > cur_key if best_key and cur_key else best_score > cur_score
    restored_refs = None
    if best_draft and should_restore:
        draft = best_draft
        print(f"  [format_check] 回退到历史最优稿 (质量向量 {best_key} > {cur_key})")
        # 同步回退与该稿编号配套的参考文献清单: 后续 latex 渲染/引用核对
        # 若沿用最新轮编号会与回退稿正文错位 → PDF 引用显示 [?] 或张冠李戴
        best_refs = state.get("best_verified_refs", []) or []
        if best_refs:
            restored_refs = list(best_refs)

    # 引用重编号: 按正文首次出现顺序 1..N (引文验证已在前面阶段完成, 此处安全)
    try:
        from src.rag.reference_formatter import renumber_citations

        renumbered = renumber_citations(draft)
        if renumbered != draft:
            draft = renumbered
            print("  [format_check] 引用已重编号 (按首次出现顺序 1..N)")
    except Exception as e:
        print(f"  [format_check] 引用重编号跳过: {e}")

    report = format_check_report(draft)
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    filename = f"format_report_{safe_name}_{ts}.md"
    save_file(report["report_md"], filename, subdir=state.get("run_id"))
    out = {
        "current_phase": "format_check",
        "paper_draft": draft,
        "format_report": report,
        "format_report_path": filename,
    }
    if restored_refs is not None:
        out["verified_references"] = restored_refs
    return out


def latex_render_node(state: PipelineState) -> dict:
    """LaTeX 渲染节点：Markdown 初稿 → ctexart .tex + xelatex 编译 PDF"""
    draft = state.get("paper_draft", "")
    topic = state["research_topic"]
    verified_refs = state.get("verified_references", [])
    fig_paths = state.get("figure_paths", [])

    if not draft:
        return {"current_phase": "latex_render"}

    try:
        from src.rag.latex_render import render_latex

        tex_content = render_latex(draft, topic, verified_refs, fig_paths)
        safe_name = sanitize_filename(topic)
        ts = get_timestamp()
        tex_path = save_file(tex_content, f"paper_{safe_name}_{ts}.tex")

        # 编译 PDF (xelatex 双遍, 每遍上限 180s; 打印进度避免静默等待)
        pdf_ok = False
        try:
            from src.rag.latex_compiler import compile_latex, cleanup_aux_files

            print("  [latex_render] 编译 PDF (xelatex 双遍, 最多约 6 分钟)...")
            pdf_ok, log = compile_latex(str(tex_path))
            if pdf_ok:
                cleanup_aux_files(str(tex_path))
            else:
                # 落盘编译日志: 瞬时失败 (杀毒锁文件/MiKTeX 更新提示) 与真实
                # LaTeX 错误的区分必须靠日志, 仅打印 200 字不足以定位
                try:
                    log_path = Path(str(tex_path) + ".compile.log")
                    log_path.write_text(log or "", encoding="utf-8")
                    print(f"  [latex_render] 编译警告: {str(log)[:200]} (完整日志: {log_path.name})")
                except Exception:
                    print(f"  [latex_render] 编译警告: {str(log)[:200]}")
        except Exception as e:
            print(f"  [latex_render] 编译跳过: {e}")

        return {
            "current_phase": "latex_render",
            "paper_tex_path": str(tex_path),
            "tex_compiled": pdf_ok,
        }
    except Exception as e:
        print(f"  [latex_render] 渲染失败: {e}")
        return {"current_phase": "latex_render"}


def _synchronize_figure_placeholders(draft: str, figure_paths: list[str]) -> str:
    """为每张实际生成的图补齐正文占位符，并尽量放入语义对应章节。"""
    import re

    if not draft or not figure_paths:
        return draft
    existing = {int(n) for n in re.findall(r"\[图\s*(\d+)\s*[:：]", draft)}
    mappings = (
        # 第1章(引言): 领域总览 + 文献检索概况(计量类图)
        ("framework", "1", "研究框架总览"),
        ("trend", "1", "文献出版趋势与出处分布"),
        ("heatmap", "1", "主题×年份文献分布"),
        # 第2章(相关工作/背景): 识别流程 + 研究发展脉络
        ("pipeline", "2", "识别流程"),
        ("timeline", "2", "研究发展脉络"),
        # 第3章(核心方法): 分类体系
        ("taxonomy", "3", "分类体系"),
        # 第4章(比较与分析): 方法对比
        ("method_comp", "4", "主要方法多维对比"),
        ("comparison", "4", "主要方法多维对比"),
    )

    def insert_in_section(text: str, section_num: str, block: str) -> str:
        heading = re.search(rf"(?m)^##\s*{section_num}(?:[.、\s]|$).*", text)
        if not heading:
            # 结构异常时仍保证图片被正文引用：放在结论前，找不到则置于正文末尾。
            conclusion = re.search(r"(?m)^##\s*6(?:[.、\s]|$).*", text)
            pos = conclusion.start() if conclusion else len(text)
        else:
            next_heading = re.search(r"(?m)^##\s+", text[heading.end():])
            pos = heading.end() + next_heading.start() if next_heading else len(text)
        return text[:pos].rstrip() + "\n\n" + block + "\n\n" + text[pos:].lstrip()

    synced = draft
    for index, path in enumerate(figure_paths, start=1):
        if index in existing:
            continue
        name = Path(path).stem.lower()
        section_num, caption = "4", f"{Path(path).stem} 图示"
        for marker, target, title in mappings:
            if marker in name:
                section_num, caption = target, title
                break
        synced = insert_in_section(synced, section_num, f"[图{index}: {caption}]")
    return synced


def regenerate_figures_node(state: PipelineState) -> dict:
    """figures 动作: 重新生成图表 (从综述笔记 + 已验证引用)。"""
    import time as _time

    topic = state.get("research_topic", "")
    lit_notes = state.get("literature_review_notes", "")
    verified_refs = state.get("verified_references", [])

    # 状态里没有素材时, 尝试从缓存加载 (planner 通常已加载)
    if not lit_notes and not verified_refs:
        try:
            from src.utils.pipeline_cache import load_retrieval_cache, resolve_cache_topic

            resolved = resolve_cache_topic(topic)
            cached = load_retrieval_cache(resolved) if resolved else None
            if cached:
                lit_notes = cached.get("literature_review_notes", "")
                verified_refs = cached.get("verified_references", [])
        except Exception:
            pass

    if not lit_notes and not verified_refs:
        return {"error": "无可用文献素材/已验证引用, 无法生成图表", "current_phase": "figures"}

    print(f"  [figures] 开始生成图表 (主题: {topic})...")
    _t0 = _time.monotonic()
    try:
        from src.rag.figure_generator import generate_figures_from_notes

        fig_paths = generate_figures_from_notes(topic, lit_notes, verified_refs)
        print(f"  [figures] 图表生成完成: {len(fig_paths)} 张, 耗时 {_time.monotonic() - _t0:.0f}s")
        result = {"figure_paths": fig_paths, "current_phase": "figures"}
        # 若有草稿, 同步图占位符
        draft = state.get("paper_draft", "")
        if draft and fig_paths:
            synced = _synchronize_figure_placeholders(draft, fig_paths)
            if synced != draft:
                result["paper_draft"] = synced
                print("  [figures] 已为成图补齐正文占位符")
        return result
    except Exception as e:
        print(f"  [figures] 图表生成失败: {e}")
        return {"error": f"图表生成失败: {e}", "current_phase": "figures"}


def finalize_node(state: PipelineState) -> dict:
    """最终节点：生成图表 + 成本/用量报告"""
    result: dict = {}

    # 论文已完成 → 更新缓存的进度为 research+write (供多上下文列表显示)
    try:
        from src.utils.pipeline_cache import update_retrieval_cache

        topic = state.get("research_topic", "")
        if topic:
            update_retrieval_cache(topic, stages=["research", "write"])
    except Exception:
        pass

    # 生成综述图表（分类体系/时间线/趋势/方法对比, 借鉴 AI-Scientist 12 图上限）
    try:
        from src.rag.figure_generator import generate_figures_from_notes

        topic = state.get("research_topic", "")
        lit_notes = state.get("literature_review_notes", "")
        verified_refs = state.get("verified_references", [])
        fig_paths = generate_figures_from_notes(topic, lit_notes, verified_refs)
        if fig_paths:
            result["figure_paths"] = fig_paths
            synced_draft = _synchronize_figure_placeholders(
                state.get("paper_draft", ""), fig_paths
            )
            if synced_draft != state.get("paper_draft", ""):
                result["paper_draft"] = synced_draft
                print("  [finalize] 已为全部成图补齐正文占位符")
            print(f"  [finalize] 图表生成: {len(fig_paths)} 张 ({', '.join(Path(p).name for p in fig_paths)})")
    except Exception as e:
        print(f"  [finalize] 图表生成跳过: {e}")

    # 成本/用量报告
    try:
        from src.utils.cost_tracker import tracker

        report = tracker.summary_md()
        if report:
            ts = get_timestamp()
            filename = f"cost_report_{ts}.md"
            save_file(report, filename, subdir=state.get("run_id"))
            summary = tracker.summary()
            result["usage"] = summary
            result["total_cost"] = summary["total_cost_usd"]
            result["cost_report_path"] = filename
    except Exception:
        pass

    result.setdefault("usage", {})
    result.setdefault("total_cost", 0.0)
    return result


def _review_quality_key(state: PipelineState, result: dict | None = None) -> list[int]:
    """构造稳定的稿件质量排序键。

    首先要求引用硬门禁全部通过，其次比较固定量表总分；同分时，未解决的
    Critical/普通问题越少越好。该排序同时用于停滞判断和历史最优稿选择。
    """
    data = result or state
    hard_defects = (
        int(state.get("citation_not_found_count", 0) or 0)
        + len(state.get("hallucinated_refs", []) or [])
        + int(state.get("guard_invalid_count", 0) or 0)
    )
    score = int(data.get("review_score", state.get("review_score", 0)) or 0)
    # 优先用 Writer 职责内的 Critical 数: 参考文献元数据等系统职责问题
    # 不应拉低稿件质量排序 (Writer 无法修复, 属系统链路责任)
    critical = data.get("review_writer_critical_count", state.get("review_writer_critical_count"))
    if critical is None:
        critical = data.get("review_critical_count", state.get("review_critical_count", 0))
    critical = int(critical or 0)
    open_issues = int(data.get("review_open_issue_count", state.get("review_open_issue_count", 0)) or 0)
    return [1 if hard_defects == 0 else 0, score, -critical, -open_issues, -hard_defects]


def paper_review_node(state: PipelineState) -> dict:
    import time as _time

    draft_len = len(state.get("paper_draft", ""))
    print(f"  [paper_review] 开始审稿 (初稿 {draft_len} 字符, 预计 2-10 分钟)...")
    _t0 = _time.monotonic()
    try:
        result = run_paper_review(state)
    except Exception as e:
        print(f"  [paper_review] 审稿节点异常: {e}, 跳过审稿继续收尾")
        return {
            "current_phase": "paper_review",
            "error": f"paper_review crashed: {e}",
            "review_score": state.get("review_score", 0),
            "review_report": "## 审稿报告\n\n审稿节点异常，跳过本轮审稿。",
        }
    print(f"  [paper_review] 审稿完成, 耗时 {_time.monotonic() - _t0:.0f}s, 评分 {result.get('review_score', 'N/A')}/50")
    report = result.get("review_report", "")
    topic = state["research_topic"]
    safe_name = sanitize_filename(topic)
    ts = get_timestamp()
    if report and "error" not in result:
        filename = f"paper_review_{safe_name}_{ts}.md"
        review_path = save_file(report, filename, subdir=state.get("run_id"))
        result["review_report_path"] = review_path

    # 收敛检测: 用稳定质量向量而非单一总分判断进步。
    score = result.get("review_score", 0)
    best_score = state.get("best_score", 0)
    best_draft = state.get("best_draft", "")
    stagnation = state.get("stagnation_count", 0)
    current_draft = state.get("paper_draft", "")
    current_key = _review_quality_key(state, result)
    previous_key = state.get("review_quality_key", []) or []
    best_key = state.get("best_quality_key", []) or []
    result["review_quality_key"] = current_key

    if not best_key or current_key > best_key:
        result["best_score"] = score
        result["best_draft"] = current_draft
        result["best_quality_key"] = current_key
        # 与最优稿编号配套的参考文献快照: 回退最优稿时必须同步恢复,
        # 否则后续用最新轮 verified_references 渲染会让 \cite 与 \bibitem 错位 → PDF 引用变 [?]
        result["best_verified_refs"] = list(state.get("verified_references", []))
        print(f"  [paper_review] 质量提升 {best_key or '[初稿]'} → {current_key}, 更新最优稿")
    else:
        result["best_score"] = best_score
        result["best_draft"] = best_draft or current_draft
        result["best_quality_key"] = best_key
        result["best_verified_refs"] = state.get("best_verified_refs", []) or list(state.get("verified_references", []))

    if not previous_key or current_key > previous_key:
        result["stagnation_count"] = 0
        print(f"  [paper_review] 相比上轮有可测量进步: {previous_key or '[首次]'} → {current_key}")
    else:
        result["stagnation_count"] = stagnation + 1
        print(f"  [paper_review] 相比上轮未提升 ({current_key} ≤ {previous_key}), 连续 {stagnation + 1} 轮")
    return result


# 对话式协作: 用户在各暂停点的输入分类 (集中定义, CLI 前端与节点共用)
_CONFIRM_WORDS = {"", "y", "yes", "ok", "okay", "c", "continue", "确认", "继续", "好", "可以", "同意", "是"}
_QUIT_WORDS = {"q", "quit", "exit", "stop", "n", "no", "abort", "取消", "停止", "退出", "放弃"}
_FINALIZE_WORDS = {"f", "finalize", "finish", "done", "end", "accept", "accept", "定稿", "接受", "结束", "直接定稿", "就这样"}

# 大纲反馈重生成的最大次数 (防止"改了又改"死循环)
MAX_OUTLINE_ITERATIONS = 3


def _is_confirm(response: str) -> bool:
    return (response or "").strip().lower() in _CONFIRM_WORDS


def _is_quit(response: str) -> bool:
    return (response or "").strip().lower() in _QUIT_WORDS


def _is_finalize(response: str) -> bool:
    return (response or "").strip().lower() in _FINALIZE_WORDS


def _parse_config_change(feedback: str) -> dict:
    """从用户意见解析配置修改 (目前支持: 最大修订轮次)。"""
    import re
    args = {}
    if re.search(r"轮次|revision|修订轮|修改轮|改.{0,3}轮|最多", feedback, re.IGNORECASE):
        m = re.search(r"(\d+)\s*轮|轮次?\s*[=:：为改设至]+\s*(\d+)", feedback)
        if m:
            args["max_revisions"] = int(m.group(1) or m.group(2))
        else:
            m2 = re.search(r"(\d+)", feedback)
            if m2:
                args["max_revisions"] = int(m2.group(1))
    return args


def _extract_scope(feedback: str) -> dict:
    """从范围修改意见提取新的 sub_topics / keywords (廉价 LLM + 逗号回退)。"""
    import re as _re

    def _split(t: str) -> list[str]:
        parts = _re.split(r"[,，、;；\n]+", t)
        return [p.strip() for p in parts if p.strip()]

    try:
        from src.config import build_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = build_llm("cheap")
        prompt = (
            "从下面的用户意见中提取他们想增加/修改的研究子主题和关键词。\n"
            '输出 JSON: {"sub_topics": [...], "keywords": [...]}，只输出 JSON 不要解释，'
            "未提及的项输出空数组。\n\n"
            f"用户意见: {feedback}"
        )
        result = llm.invoke([
            SystemMessage(content="你是结构化提取助手，只输出 JSON。"),
            HumanMessage(content=prompt),
        ])
        text = result.content if hasattr(result, "content") else str(result)
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        data = __import__("json").loads(m.group(0)) if m else {}
        return {
            "sub_topics": [str(x).strip() for x in (data.get("sub_topics") or []) if str(x).strip()],
            "keywords": [str(x).strip() for x in (data.get("keywords") or []) if str(x).strip()],
        }
    except Exception as e:
        print(f"  [human] 范围提取 LLM 失败, 回退逗号切分: {e}")
        return {"sub_topics": _split(feedback), "keywords": []}


def parse_human_intent(feedback: str, phase: str) -> dict:
    """把用户自然语言反馈解析为结构化动作。

    返回 {"kind": ..., "args": {...}}
    kind: none / regenerate_outline / revise / scope / config
    """
    import re
    feedback = (feedback or "").strip()
    if not feedback:
        return {"kind": "none"}

    if phase == "outline":
        # 范围修改: 明确提及子主题/关键词/主题
        if re.search(r"子主题|关键词|主题", feedback):
            return {"kind": "scope"}
        # 配置修改: 明确提及轮次等
        cfg = _parse_config_change(feedback)
        if cfg:
            return {"kind": "config", "args": cfg}
        # 默认: 按修改意见重新生成大纲
        return {"kind": "regenerate_outline"}

    # draft / review 阶段: 一律视为内容修订意见
    return {"kind": "revise"}


def _human_feedback_to_contract(feedback: str, phase: str, index: int) -> dict:
    """把用户内容意见包装成修订契约条目 (increment_revision 会优先执行)。"""
    return {
        "id": f"R-HUMAN-{index:02d}",
        "status": "未解决",
        "priority": "高",
        "problem": f"（用户人工要求）{feedback}",
        "evidence": f"用户在第 {phase} 阶段提出的人工意见",
        "human": True,
    }


def human_outline_node(state: PipelineState) -> dict:
    """大纲确认暂停点: 确认继续 / 按意见重新生成大纲 / 修改范围(重新检索) / 改轮次 / 停止。"""
    if not state.get("interactive"):
        return {}
    outline = state.get("paper_outline", "")
    response = interrupt({
        "type": "outline",
        "phase": "outline_generation",
        "title": "论文大纲已生成，请确认",
        "content": outline,
        "path": state.get("outline_path", ""),
        "hint": "回车/y=确认；输入修改意见=重新生成大纲；含'子主题/关键词'=重新检索；含'轮次N'=改修订轮次；q=停止",
    })
    response = (response or "").strip()
    if _is_confirm(response):
        return {"human_feedback": "", "human_feedback_phase": "", "human_outline_route": "paper_writing"}
    if _is_quit(response):
        # quit 由 CLI 前端在 resume 前拦截; 此处仅兜底
        return {"human_outline_route": "paper_writing"}

    intent = parse_human_intent(response, "outline")

    if intent["kind"] == "scope":
        scope = _extract_scope(response)
        out = {
            "human_feedback": "",
            "human_feedback_phase": "",
            "human_outline_route": "literature_review",
            "outline_iteration": 0,
            # 回到 supervisor 后需重新派发 write 阶段 (research 已重跑)
            "stage_index": 1,
        }
        if scope.get("sub_topics"):
            out["sub_topics"] = scope["sub_topics"]
            print(f"  [human] 更新子主题: {', '.join(scope['sub_topics'])} → 重新检索")
        if scope.get("keywords"):
            out["topic_keywords"] = scope["keywords"]
            print(f"  [human] 更新关键词: {', '.join(scope['keywords'])} → 重新检索")
        if not scope.get("sub_topics") and not scope.get("keywords"):
            # 没解析到具体词 → 退化为重新生成大纲
            print("  [human] 未解析到明确的关键词/子主题, 按修改意见重新生成大纲")
            return {
                "human_feedback": response,
                "human_feedback_phase": "outline",
                "human_outline_route": "outline_generation",
            }
        return out

    if intent["kind"] == "config":
        out = {"human_feedback": "", "human_feedback_phase": "", "human_outline_route": "paper_writing"}
        mr = intent["args"].get("max_revisions")
        if mr:
            out["max_revisions"] = int(mr)
            print(f"  [human] 最大修订轮次改为 {mr}")
        return out

    # regenerate_outline: 按意见重新生成大纲 (带次数上限)
    it = state.get("outline_iteration", 0) + 1
    if it > MAX_OUTLINE_ITERATIONS:
        print(f"  [human] 大纲已重生成 {MAX_OUTLINE_ITERATIONS} 次, 不再继续, 按当前大纲进入撰写")
        return {"human_feedback": "", "human_feedback_phase": "", "human_outline_route": "paper_writing"}
    print("  [human] 按你的意见重新生成大纲")
    return {
        "human_feedback": response,
        "human_feedback_phase": "outline",
        "human_outline_route": "outline_generation",
        "outline_iteration": it,
    }


def human_draft_node(state: PipelineState) -> dict:
    """初稿确认暂停点 (仅初稿; 修订轮次由审稿暂停点接管)。"""
    if not state.get("interactive"):
        return {}
    if state.get("revision_count", 0) > 0:
        return {}
    draft = state.get("paper_draft", "")
    response = interrupt({
        "type": "draft",
        "phase": "paper_writing",
        "title": "论文初稿已完成 (引用已守门/核查)",
        "content": draft,
        "path": state.get("draft_path", ""),
        "hint": "回车/y=送审；输入修改意见=转成修订要求(首轮修订执行)；q=停止",
    })
    response = (response or "").strip()
    if _is_confirm(response):
        return {"human_feedback": "", "human_feedback_phase": ""}
    if _is_quit(response):
        return {"human_feedback": ""}
    existing = list(state.get("human_revision_contract", []) or [])
    item = _human_feedback_to_contract(response, "draft", len(existing) + 1)
    print(f"  [human] 初稿意见已转为修订要求 [{item['id']}], 将在首轮修订中执行")
    return {"human_feedback": "", "human_feedback_phase": "draft", "human_revision_contract": existing + [item]}


def human_review_node(state: PipelineState) -> dict:
    """审稿暂停点: 继续修订(可附人工意见) / 接受定稿 / 停止。"""
    if not state.get("interactive"):
        return {"human_review_decision": ""}
    score = state.get("review_score", 0)
    report = state.get("review_report", "")
    revision_count = state.get("revision_count", 0)
    max_revisions = state.get("max_revisions", MAX_REVISIONS)
    response = interrupt({
        "type": "review",
        "phase": "paper_review",
        "title": f"第 {revision_count} 轮审稿完成 (评分 {score}/50)",
        "score": score,
        "revision_count": revision_count,
        "max_revisions": max_revisions,
        "content": report,
        "path": state.get("review_report_path", ""),
        "hint": "回车/y=继续修订；输入修改意见=转成修订要求并继续修订；f/结束=接受当前稿并定稿；q=停止",
    })
    response = (response or "").strip()
    if _is_finalize(response):
        # 定稿时若仍有引用硬伤, 明确警告 (引用可能渲染为 [?])
        hard = (
            int(state.get("citation_not_found_count", 0) or 0)
            + len(state.get("hallucinated_refs", []) or [])
            + int(state.get("guard_invalid_count", 0) or 0)
        )
        if hard:
            print(f"  [human] ⚠️ 当前稿仍有 {hard} 处引用硬伤, 定稿后 PDF 引用可能显示为 [?]")
        return {"human_review_decision": "finalize", "human_feedback": ""}
    if _is_confirm(response):
        return {"human_review_decision": "revise", "human_feedback": ""}
    if _is_quit(response):
        return {"human_review_decision": "revise", "human_feedback": ""}
    # 其余输入: 继续修订 + 附人工意见 (转为修订契约条目)
    existing = list(state.get("human_revision_contract", []) or [])
    item = _human_feedback_to_contract(response, "review", len(existing) + 1)
    print(f"  [human] 审稿阶段意见已转为修订要求 [{item['id']}], 将随本轮修订执行")
    return {
        "human_review_decision": "revise",
        "human_feedback": "",
        "human_revision_contract": existing + [item],
    }


def route_after_human_outline(state: PipelineState) -> str:
    """大纲暂停点后去向: 重新生成大纲 / 重新检索(改范围) / 进入撰写。"""
    return state.get("human_outline_route", "paper_writing")


def should_continue_review(state: PipelineState) -> Literal["paper_writing", "end"]:
    score = state.get("review_score", 0)
    revision_count = state.get("revision_count", 0)
    error = state.get("error")
    hallucinated = state.get("hallucinated_refs", []) or []
    not_found = state.get("citation_not_found_count", 0) or 0
    guard_invalid = state.get("guard_invalid_count", 0) or 0
    # 门禁只看 Writer 职责内的 Critical: 参考文献元数据等系统职责问题
    # Writer 无法修复, 计入会造成"永远有 Critical → 永远修订"的死锁
    review_critical = state.get(
        "review_writer_critical_count", state.get("review_critical_count", 0)
    ) or 0
    max_revisions = state.get("max_revisions", MAX_REVISIONS)

    # 人工决策优先: 用户在审稿暂停点选择"接受定稿" → 无论机器门禁如何都结束
    if state.get("human_review_decision") == "finalize":
        return "end"

    if error:
        return "end"

    # 有待处理的人工要求 → 必须修订执行 (即使机器门禁已达标/停滞, 人工指令优先于机器门禁)
    if state.get("human_revision_contract"):
        if revision_count < max_revisions:
            return "paper_writing"
        print("  [review] 已达最大修订轮次, 仍有未执行的人工要求, 结束")
        return "end"

    # review_score 是 50 分制，需换算为 100 分制与 REVIEW_ACCEPT_THRESHOLD 比较
    score_100 = score * 2
    needs_fix = (
        score_100 < REVIEW_ACCEPT_THRESHOLD
        or not_found > 0
        or len(hallucinated) > 0
        or guard_invalid > 0  # 引用守门: 存在越界引用编号
        or review_critical > 0  # Reviewer 台账仍有 Writer 可修复的未关闭 Critical 问题
    )

    # 收敛检测: 连续 STAGNATION_LIMIT 轮评分无提升 → 提前终止 (避免无意义地跑满轮次)
    if state.get("stagnation_count", 0) >= STAGNATION_LIMIT:
        print(f"  [review] 连续 {STAGNATION_LIMIT} 轮评分无提升, 提前终止修订循环")
        return "end"

    if needs_fix and revision_count < max_revisions:
        return "paper_writing"

    return "end"


def _summarize_review(report: str, score: int, round_num: int) -> str:
    """从审稿报告中提取关键问题摘要，用于跨轮累积"""
    if not report:
        return ""
    import re as _re
    lines = []
    # 提取 高优先级 + 中优先级 条目
    high = _re.search(r"高优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report, _re.DOTALL)
    mid = _re.search(r"中优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report, _re.DOTALL)
    if high:
        items = [l.strip() for l in high.group(1).split("\n") if l.strip() and l.strip()[0].isdigit()]
        lines.extend(items[:4])
    if mid:
        items = [l.strip() for l in mid.group(1).split("\n") if l.strip() and l.strip()[0].isdigit()]
        lines.extend(items[:2])
    if not lines:
        return ""
    summary = f"### 第 {round_num} 轮审稿要点 (评分 {score}/50)\n"
    for item in lines:
        summary += f"- {item[:200]}\n"
    return summary + "\n"


def _resolve_review_suggestions(
    review_report: str,
    topic: str,
    existing_refs: list[dict],
    prev_blocked: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """从审稿意见中提取论文建议，搜索验证后返回三类结果:

    (new_refs, blocked, existing_hits)
    - new_refs: 验证通过、可新增进引用清单的论文
    - blocked: 审稿人推荐但无法进入清单的论文 [{title, reason}]
      (Writer 不得引用, 必须在正文中改写或删除相关描述;
      同时回喂 Reviewer, 避免其反复要求引用而无法闭环)
    - existing_hits: 审稿人推荐且**已在清单中**的论文 [{title, ref_number}]
      (Writer 可直接引用, 满足"补充某文献"类审稿意见)

    审稿人常建议 "请引用 Brik (2008)" 等，Writer 无法凭空创造引用。
    本函数在修改前自动补齐这些论文到引用清单。
    prev_blocked 中已验证不可用的论文直接跳过检索 (审稿人常无视
    "不得再索要"规则反复推荐同一批预印本, 重复检索纯浪费)。
    """
    if not review_report:
        return [], [], []

    suggestions = _extract_paper_suggestions(review_report)
    if not suggestions:
        return [], [], []

    from src.tools.search_tools import search_all_sources
    from src.tools.citation_verifier import CitationRecord
    from src.rag.reference_formatter import is_published_ref
    from src.rag.relevance_filter import has_domain_signal

    search_fn = search_all_sources.func if hasattr(search_all_sources, "func") else search_all_sources
    existing_titles = {_norm_title(r.get("title", "")) for r in existing_refs}
    existing_dois = {(r.get("doi") or "").strip().lower() for r in existing_refs if (r.get("doi") or "").strip()}
    next_num = max([r.get("ref_number", 0) for r in existing_refs], default=0)

    # 快速 DOI 检查: 检索结果自带 DOI 时无需三源验证
    try:
        from src.tools.chinese_sources import _doi_resolves
    except ImportError:
        _doi_resolves = lambda doi: False

    def _find_existing(suggestion: str) -> dict | None:
        """建议论文已在可信清单中 → 返回对应条目 (Writer 可直接引用)"""
        for r in existing_refs:
            if _title_sim(suggestion, r.get("title", "")) >= 0.85:
                return r
        return None

    new_refs = []
    blocked = []
    existing_hits = []
    prev_blocked_fps = _blocked_fingerprints(prev_blocked or [])
    for suggestion in suggestions[:8]:
        # 此前轮次已验证无法加入清单的论文: 不再重复检索, 直接维持 blocked
        if any(fp in _norm_title(suggestion) for fp in prev_blocked_fps):
            blocked.append({"title": suggestion, "reason": "此前轮次已验证无法加入清单"})
            continue
        # 已在清单中的论文: 记录编号供 Writer 直接引用 (满足"补充某文献"类意见)
        hit = _find_existing(suggestion)
        if hit is not None:
            existing_hits.append({"title": hit.get("title", suggestion), "ref_number": hit.get("ref_number")})
            continue
        try:
            results = search_fn(suggestion, 5)
        except Exception:
            results = []
        # 完整标题检索失败时, 用冒号前的主标题重试 (长标题检索常无结果)
        if not results and ":" in suggestion:
            try:
                results = search_fn(suggestion.split(":", 1)[0].strip(), 5)
            except Exception:
                results = []
        if not results:
            blocked.append({"title": suggestion, "reason": "检索无结果, 无法验证真实性"})
            continue

        # 取标题相似度最高的结果
        best = max(results, key=lambda p: _title_sim(suggestion, p.get("title", "")))
        if _title_sim(suggestion, best.get("title", "")) < 0.5:
            blocked.append({"title": suggestion, "reason": "检索结果与推荐标题不匹配"})
            continue

        # 去重: best 的归一化标题或 DOI 已在清单中 → 记为可直接引用 (避免重复编号)
        if _norm_title(best.get("title", "")) in existing_titles or (
            best.get("doi") or ""
        ).strip().lower() in existing_dois:
            dup = _find_existing(best.get("title", ""))
            if dup is not None:
                existing_hits.append({"title": dup.get("title", ""), "ref_number": dup.get("ref_number")})
            continue

        # 快速验证: 结果自带 DOI 且可解析 → 直接可信
        # 否则回退三源验证（仅个别论文，不会拖慢流水线）
        doi = (best.get("doi") or "").strip()
        if doi and _doi_resolves(doi):
            best["verified"] = True
        else:
            from src.tools.citation_verifier import verify_single_citation

            record = CitationRecord(ref_number=0, title=best.get("title", ""))
            vr = verify_single_citation(record)
            if vr.status == "NOT_FOUND":
                blocked.append({"title": suggestion, "reason": "三源交叉验证未找到, 疑似不存在"})
                continue
            best["verified"] = True

        # 用户要求: 最终参考文献只含真实已发表文献, 预印本不进入清单。
        # 但 arXiv 预印本可能已正式发表 (如 FLAME → IEEE Internet of Things Journal),
        # 先尝试标题检索 CrossRef 升级为正式版本, 再判定是否跳过。
        if not is_published_ref(best):
            from src.tools.venue_resolver import upgrade_arxiv_to_published

            if upgrade_arxiv_to_published(best):
                print(f"  [引用扩充] 预印本已解析为正式发表: {best.get('title','')[:60]}")
        if not is_published_ref(best):
            print(f"  [引用扩充] 跳过预印本 (无正式 DOI/期刊): {best.get('title','')[:60]}")
            blocked.append({"title": suggestion, "reason": "仅为预印本, 无正式发表出处"})
            continue

        # 主题相关性门: 审稿建议补录的论文标题必须含无线/RF 领域特征词,
        # 否则 (审稿人提及的架构名/方向名检索到的无关论文) 不进入清单
        if not has_domain_signal(best.get("title", "") or ""):
            print(f"  [引用扩充] 跳过无关论文 (无领域信号): {best.get('title','')[:60]}")
            blocked.append({"title": suggestion, "reason": "与主题领域不相关"})
            continue

        # 跨域负向门: 音频/图像/多媒体等领域论文不得进入清单
        from src.rag.relevance_filter import has_off_domain_signal

        if has_off_domain_signal(best.get("title", "") or ""):
            print(f"  [引用扩充] 跳过跨域论文 (音频/多媒体等): {best.get('title','')[:60]}")
            blocked.append({"title": suggestion, "reason": "跨域论文 (非射频/无线领域)"})
            continue

        # 用 CrossRef 补全/修正出处、卷期页码、年份 (审稿人常报"年份错误/卷期页缺失")
        # 不再只对"无出处"的论文补全: 有 DOI 的论文无论出处是否已有, 都补缺失字段并修正年份
        if (best.get("doi") or "").strip():
            from src.tools.venue_resolver import enrich_paper_from_doi

            enrich_paper_from_doi(best)

        next_num += 1
        best["ref_number"] = next_num
        new_refs.append(best)
        existing_titles.add(_norm_title(best.get("title", "")))
        if (best.get("doi") or "").strip():
            existing_dois.add((best.get("doi") or "").strip().lower())
        print(f"  [引用扩充] 审稿建议 → 已验证: [{next_num}] {best.get('title','')[:60]}")

    if blocked:
        print(f"  [引用扩充] {len(blocked)} 篇审稿推荐论文无法加入清单: "
              + "; ".join(b["title"][:40] for b in blocked[:5]))
    return new_refs, blocked, existing_hits


def _extract_paper_suggestions(review_report: str) -> list[str]:
    """用廉价 LLM 从审稿意见的「补充推荐论文」章节提取论文建议（标题）

    仅从第 7 节「补充推荐论文」提取: 审稿意见其他章节 (如「缺失内容」) 会提及
    "Swin Transformer" 等架构/方向名, 若一并提取会被当成引用建议, 检索到完全无关的
    论文 (如 CVPR 视频 Swin Transformer) 混入引用清单。
    """
    import re as _re
    # 定位「补充推荐论文」章节 (到下一个 ## 或文末为止)
    m = _re.search(r"补充推荐论文\s*\n(.*?)(?=\n##\s|\Z)", review_report, _re.DOTALL)
    if not m or not m.group(1).strip():
        return []
    section7 = m.group(1)[:3000]

    def _clean_line(line: str) -> str:
        """把 LLM 输出行清洗为论文标题 (兼容表格行/编号前缀)"""
        line = line.strip()
        if not line:
            return ""
        # 廉价模型常原样回显表格行 "| 1 | 标题 | 位置 | 理由 |" → 取论文列
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = [c for c in cells if c and not set(c) <= {"-", ":", " "}]
            if len(cells) >= 2:
                line = cells[1]
            else:
                return ""
        # 去掉编号/列表前缀与加粗标记
        line = _re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", line)
        return line.strip("* ").strip()

    try:
        from src.config import build_llm
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = build_llm("cheap")
        prompt = (
            "从以下审稿人推荐的论文中提取每篇的完整论文标题。\n"
            "每行输出一个论文标题，只输出标题本身，不要表格、编号、架构名、方向名或解释。\n\n"
            f"{section7}"
        )
        result = llm.invoke([
            SystemMessage(content="你是文献信息提取助手。只输出提取的论文标题，每行一个。"),
            HumanMessage(content=prompt),
        ])
        text = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        print(f"  [warning] 论文建议提取 LLM 调用失败, 回退表格解析: {e}")
        text = section7

    suggestions = []
    for raw in text.split("\n"):
        title = _clean_line(raw)
        # 过滤明显不是论文标题的行 (太短 / 标题行 / 代码围栏 / 表头)
        if len(title) <= 15 or title.startswith(("#", "```")):
            continue
        if title.lower() in {"论文", "paper", "标题", "title"}:
            continue
        suggestions.append(title)
    return suggestions[:10]


def _norm_title(title: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _title_sim(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, _norm_title(a), _norm_title(b)).ratio()


def _strip_suggestions_section(report: str) -> str:
    """移除审稿报告中的「补充推荐论文」章节

    该章节的论文建议可能尚未验证、未加入引用清单 (如 ORACLE、Chen COMST 综述等),
    若直接回喂 Writer, Writer 会把它们写进正文并挂上错误编号 → 引用错配。
    该章节已由 _resolve_review_suggestions 搜索验证, 结果以「新增可信引用」块给出。
    """
    import re as _re
    m = _re.search(r"#{1,3}\s*\d*\.?\s*补充推荐论文", report)
    if m:
        return report[: m.start()].rstrip() + "\n"
    return report


def _extract_actionable_sections(report: str) -> str:
    """从审稿报告中提取**可操作**部分 (主要问题/细节问题/缺失内容/修改优先级),
    剥离冗长的「总体评价」「逐项评分」表。

    修订提示若包含整份审稿报告 (含每维度 100+ 字的说明), Writer 会被大量
    非操作信息淹没, 倾向扩写而非精准修复, 导致评分多轮不变。只喂可操作部分,
    Writer 才能逐条解决具体问题。
    """
    import re as _re
    start = _re.search(r"#{1,3}\s*\d*\.?\s*主要问题", report)
    if not start:
        return report
    end = _re.search(r"#{1,3}\s*\d*\.?\s*补充推荐论文", report)
    return report[start.start(): (end.start() if end else len(report))].strip()


def _build_revision_contract(state: PipelineState, limit: int = 4) -> list[dict]:
    """把开放问题收敛为本轮有限、可验收的修改契约。

    Writer 每轮只处理最多 ``limit`` 个最高优先级问题，防止自由文本审稿意见
    触发整篇重写并引入新的回退。若 Reviewer 未输出问题台账，则从高优先级段落
    退化提取，保证旧模型仍可工作。
    """
    import re as _re

    from src.agents.paper_reviewer import is_reference_metadata_issue

    priority_rank = {"critical": 0, "致命": 0, "高": 1, "中": 2, "低": 3}
    ledger = [
        dict(item) for item in (state.get("review_issue_ledger", []) or [])
        if item.get("status") != "已解决" and item.get("problem")
    ]
    # 系统职责问题 (参考文献元数据) 不进入 Writer 契约: Writer 无权修改参考文献章节,
    # 留在契约里只会消耗注意力且永远无法验收 (评分不升反降的死锁来源之一)。
    ledger = [
        item for item in ledger
        if not (item.get("system_owned") or is_reference_metadata_issue(str(item.get("problem", ""))))
    ]
    # stale 条目 (引文与当前稿不匹配, 疑似审稿人转述/幻觉) 不占优先席位,
    # 防止幻觉问题浪费修订轮次; 但契约有空位时递补进入, 避免真实问题
    # (审稿人转述引文导致误判 stale) 永远得不到修复 —— 递补条目带 stale_quote
    # 标记, 契约核验对其不做强制验收 (Writer 找不到对应文字时允许不改)。
    fresh = [i for i in ledger if not i.get("stale_quote")]
    stale = [i for i in ledger if i.get("stale_quote")]
    _by_priority = lambda item: priority_rank.get(str(item.get("priority", "高")).lower(), 2)
    fresh.sort(key=_by_priority)
    stale.sort(key=_by_priority)
    contract = fresh[:limit] + stale[: max(0, limit - len(fresh))]
    if contract:
        return contract

    report = state.get("review_report", "") or ""
    high = _re.search(r"高优先级[^\n]*\n+(.+?)(?=\n###|\n##|\Z)", report, _re.DOTALL)
    source = high.group(1) if high else _extract_actionable_sections(report)
    items = []
    for line in source.splitlines():
        text = _re.sub(r"^\s*(?:\d+[.)、]|[-*])\s*", "", line).strip()
        if len(text) < 8 or text.startswith(("#", "|")):
            continue
        items.append({
            "id": f"R-FALLBACK-{len(items) + 1:02d}",
            "status": "未解决",
            "priority": "高",
            "problem": text,
            "evidence": "修订后在对应章节可直接定位到修改结果",
        })
        if len(items) >= limit:
            break
    return items


def _blocked_fingerprints(blocked: list[dict]) -> list[str]:
    """被阻论文的归一化标题指纹 (用于判断契约条目是否涉及被阻论文)"""
    fps = []
    for b in blocked or []:
        norm = _norm_title(b.get("title", ""))
        if len(norm) >= 12:
            fps.append(norm[:20])
        elif len(norm) >= 8:
            fps.append(norm)
    return fps


def _augment_contract_with_blocked(contract: list[dict], blocked: list[dict]) -> list[dict]:
    """为涉及被阻论文的契约条目显式给出唯一可行的完成方式 (删除/改写)。

    否则 Writer 倾向保留方向但不引用 → Reviewer 判「悬空阐述」→ 问题每轮复活,
    形成永远无法闭环的循环 (实测: R-REF-01 → R-REF-03 → R-REF-01 反复横跳)。
    """
    fps = _blocked_fingerprints(blocked)
    if not fps or not contract:
        return contract
    for item in contract:
        text = _norm_title(f"{item.get('problem', '')} {item.get('evidence', '')}")
        if any(fp in text for fp in fps):
            item["problem"] = (
                str(item.get("problem", ""))
                + "【处置方式：所涉论文无法加入可信清单，严禁引用或编造编号——"
                "必须删除依赖该论文的正文描述，或改用清单中主题最接近的文献改写；"
                "方向可保留，但须以限定表述收尾（如「现有研究多在闭集假设下开展」），不得悬空；"
                "若整个分类子类因此没有任何可用文献，应删除该子类或并入相邻子类，不得保留 0 文献的空子类】"
            )
    return contract


_SPARSE_KEYWORDS = ("仅1篇", "仅一篇", "仅2篇", "仅两篇", "单篇", "内容单薄", "文献单薄", "文献不足", "文献严重不足", "篇文献，内容")


def _augment_contract_sparse(contract: list[dict]) -> list[dict]:
    """文献稀缺类契约条目: 给出合并/声明稀缺的处置方式。

    否则 Writer 倾向保留单薄子类或硬凑引用 (引入不当引用, 如把音频域
    文献以"借鉴意义"引入射频综述), 审稿人反复判"内容单薄"无法闭环。
    """
    for item in contract:
        problem = str(item.get("problem", ""))
        if "处置方式" in problem:
            continue
        if any(k in problem for k in _SPARSE_KEYWORDS):
            item["problem"] = problem + (
                "【处置方式：该方向文献客观稀缺——首选把该子类内容并入主题相邻的子类并删除原子类标题；"
                "次选把该子类精简为1-2句「开放问题」说明并如实陈述稀缺原因。"
                "不得保留只有0-1篇文献的独立子类，严禁为凑数编造引用或引入主题无关的文献】"
            )
    return contract


def _format_revision_contract(items: list[dict]) -> str:
    if not items:
        return "- 未解析到结构化问题；按下方审稿意见修复可明确定位的高优先级问题。"
    lines = [
        "| ID | 优先级 | 修改边界 | 本轮必须解决的问题 | 验收标准 |",
        "|----|--------|----------|--------------------|----------|",
    ]
    from src.agents.paper_reviewer import is_reference_metadata_issue

    for item in items:
        problem = str(item.get("problem", ""))
        evidence = str(item.get("evidence", "") or "在对应章节给出可定位的修复证据")
        metadata_only = bool(item.get("system_owned")) or is_reference_metadata_issue(problem)
        boundary = "仅改正文" if not metadata_only else "仅记录，禁止改正文"
        if metadata_only:
            problem = f"参考文献元数据问题：{problem}（由系统元数据链路处理）"
        lines.append(
            f"| {item.get('id', '')} | {item.get('priority', '高')} | {boundary} | "
            f"{problem.replace('|', '／')} | {evidence.replace('|', '／')} |"
        )
    return "\n".join(lines)


def increment_revision(state: PipelineState) -> dict:
    count = state.get("revision_count", 0) + 1
    review_report = state.get("review_report", "")
    citation_report = state.get("citation_report", {})
    topic = state["research_topic"]
    prior_draft = state.get("paper_draft", "")
    score = state.get("review_score", 0)
    verified_refs = list(state.get("verified_references", []))

    from src.utils.context_budget import budget_text, budget_sections

    def _strip_refs(s: str) -> str:
        """移除参考文献章节 (修订时 Writer 已单独收到 ref_sheet, 无需重复)"""
        from src.rag.reference_formatter import strip_references_section

        return strip_references_section(s)

    # 跨轮累积的被拒论文清单: 先加载历史, 供 _resolve_review_suggestions
    # 跳过重复检索 (审稿人常反复推荐同一批无法加入清单的预印本)
    prev_blocked = list(state.get("review_blocked_suggestions", []) or [])

    # 从审稿意见中提取论文建议，搜索验证后补入引用清单;
    # 无法补入的 (blocked) 与已在清单的 (existing_hits) 显式回喂 Writer,
    # 否则 "补充某文献" 类契约条目 Writer 永远无法完成 → 审稿人反复判未解决 → 评分下降
    new_refs, blocked, existing_hits = _resolve_review_suggestions(
        review_report, topic, verified_refs, prev_blocked
    )
    verified_refs.extend(new_refs)

    # 合并本轮新被拒论文 (去重), 供 Writer 规避 + 下一轮 Reviewer 停止索要
    seen_titles = {_norm_title(b.get("title", "")) for b in prev_blocked}
    for b in blocked:
        if _norm_title(b.get("title", "")) not in seen_titles:
            prev_blocked.append(b)
            seen_titles.add(_norm_title(b.get("title", "")))

    # 累积审稿历史
    prev_history = state.get("revision_history", "")
    this_summary = _summarize_review(review_report, score, count)
    revision_contract = _build_revision_contract(state)
    # 涉及被阻论文的契约条目 → 显式附上删除/改写处置方式 (否则 Writer 无法闭环)
    revision_contract = _augment_contract_with_blocked(revision_contract, prev_blocked)
    # 文献稀缺类条目 → 附上合并/声明稀缺处置方式 (防止硬凑引用引入不当文献)
    revision_contract = _augment_contract_sparse(revision_contract)
    # 人工意见优先: 用户在暂停点提出的要求作为最高优先级契约条目 (保持原文, 不做自动扩充)
    human_items = list(state.get("human_revision_contract", []) or [])
    revision_contract = human_items + revision_contract
    if human_items:
        print("  [revision] 本轮并入人工要求 (最高优先级): "
              + "; ".join(f"{h.get('id', '')}={str(h.get('problem', ''))[:60]}" for h in human_items))

    revision_prompt = (
        f"请根据以下审稿意见对论文进行修订。这是第 {count} 轮修订，当前评分 {score}/50。\n\n"
    )

    if prev_history:
        revision_prompt += (
            f"## 历史审稿要点（此前各轮的核心问题，检查是否已修复）\n\n{prev_history}\n\n"
        )

    revision_prompt += (
        f"## 本轮修订契约（优先且仅聚焦这些目标）\n\n"
        f"{_format_revision_contract(revision_contract)}\n\n"
        f"先满足表中验收标准；不得以整篇重写代替局部修订。\n\n"
        f"## 本轮审稿意见（用于理解契约，不得自行扩张修改范围）\n\n"
        f"{budget_text(_extract_actionable_sections(review_report), 8000, label='审稿意见')}\n"
    )

    if citation_report:
        revision_prompt += (
            f"\n## 引文核查报告（NOT_FOUND 的引用必须删除或替换为清单中的有效引用）\n\n"
            f"{budget_text(citation_report.get('report_md', ''), 8000, label='引文核查报告')}\n"
        )

    # 引用语义错配 (守门节点已删除错误引用标记): 要求 Writer 删除/改写对应的虚构正文描述
    guard_report = state.get("guard_report", {}) or {}
    sem_mismatches = guard_report.get("semantic_mismatches", []) or []
    if sem_mismatches:
        mm_block = "\n".join(
            f"- 正文「{m.get('context', '')[:70]}」中引用 [{m.get('num')}] 与作者"
            f"「{m.get('surname')}」不匹配（引用标记已删除）→ 请删除或改写该句的论文描述"
            for m in sem_mismatches[:10]
        )
        revision_prompt += (
            f"\n## 引用语义错配（必须删除或改写对应的正文描述）\n\n{mm_block}\n"
        )

    # 篇幅回喂: 上一版已超 20000 字时明确告知 Writer 必须删减, 否则修订每轮越改越长
    prior_body = _strip_refs(prior_draft)
    prior_chars = len(prior_body.replace(" ", "").replace("\n", ""))
    if prior_chars > 20000:
        revision_prompt += (
            f"\n## ⚠️ 篇幅警告（必须遵守）\n\n"
            f"上一版正文约 {prior_chars} 字，已超过 20000 字上限。本轮修订**必须删减**：\n"
            f"- 合并内容重复的段落（如挑战章节与结论的重复表述）\n"
            f"- 删除与审稿意见无关的赘述\n"
            f"- 修订后全文（不含参考文献）必须 ≤ 20000 字，宁可删减不可扩写\n"
        )

    # 上一版论文必须**完整**回喂: 截断会让 Writer 看不到中段章节,
    # 只能凭记忆复述 → 倾向原样复制、无法执行结构性修改 (契约落空的直接原因之一)
    revision_prompt += (
        f"\n## 上一版论文（在此基础上修改，保留审稿意见未涉及的章节）\n\n"
        f"{budget_sections(prior_body, PRIOR_DRAFT_BUDGET, label='上一版论文')}\n"
    )

    # 格式问题回喂: format_check 原本只在循环结束后运行一次,
    # "表编号缺失/表格不规范" 等问题从不反馈给 Writer, 每轮重复出现。
    # 这里提前对上一版草稿做格式检查, 把问题写入修订提示。
    try:
        from src.rag.format_validator import format_check_report

        fmt = format_check_report(prior_draft)
        fmt_issues = (
            fmt.get("table", {}).get("issues", [])
            + fmt.get("figure", {}).get("issues", [])
            + fmt.get("citation", {}).get("issues", [])
        )
        if fmt_issues:
            fmt_block = "\n".join(f"- {i}" for i in fmt_issues[:10])
            revision_prompt += (
                f"\n## 格式问题（必须在本轮修订中修复）\n\n{fmt_block}\n"
            )
    except Exception:
        pass

    if new_refs:
        new_ref_str = "\n".join(
            f"[{r['ref_number']}] {r.get('title','')} ({r.get('year','')}) [{r.get('venue', r.get('source',''))}]"
            for r in new_refs
        )
        revision_prompt += (
            f"\n## 新增可信引用（已根据审稿意见搜索验证，可直接使用）\n\n{new_ref_str}\n"
        )

    if existing_hits:
        hit_str = "\n".join(
            f"- [{h.get('ref_number')}] {h.get('title', '')}" for h in existing_hits[:10]
        )
        revision_prompt += (
            f"\n## 审稿人推荐且已在可信清单中的论文（审稿意见要求补充时, 直接引用这些编号）\n\n{hit_str}\n"
        )

    if prev_blocked:
        blocked_str = "\n".join(
            f"- 《{b.get('title', '')}》（{b.get('reason', '未通过验证')}）"
            for b in prev_blocked[:10]
        )
        revision_prompt += (
            f"\n## 无法加入清单的审稿推荐论文（严禁引用或虚构其编号）\n\n"
            f"{blocked_str}\n\n"
            f"这些论文未通过真实性/发表状态验证。审稿意见中涉及它们的内容，"
            f"一律改为用清单中主题最接近的文献支撑，或删除相关描述，"
            f"不得为其编造引用编号。\n"
        )

    revision_prompt += (
        f"\n## 修订要求\n\n"
        f"1. 逐条完成「本轮修订契约」，修改后应能按每条验收标准定位证据\n"
        f"2. 历史问题只做回归检查；不要把已解决问题重新改写，也不要处理契约外的低优先级意见\n"
        f"3. 删除或替换引文核查报告中标记为 NOT_FOUND 的虚构引用\n"
        f"4. 审稿意见未提及问题的章节保持原样，不要重写\n"
        f"5. 输出完整修订版论文（标题 + 摘要 + 6 章结构），不要只输出修改部分\n"
        f"6. 只能引用「可信参考文献清单」和「新增可信引用」中的文献；"
        f"审稿意见中提到的但未出现在上述清单里的论文，一律删除相关正文描述，不得写入\n"
        f"7. 修订后全文（不含参考文献）必须不超过 20000 字；如已超限，优先删减而非扩写\n"
        f"8. 保持未涉及章节的标题、关键论点和有效引用不变，避免修好一处又破坏另一处\n"
        f"9. 先在上一版中定位契约所指的章节、段落或引用号，再做最小编辑；不要凭审稿意见另起炉灶\n"
        f"10. 仅修改契约中标为‘仅改正文’的问题；‘仅记录，禁止改正文’的参考文献元数据问题，不要改写作者归属、引用句或新增引用，避免制造正文—参考文献错配\n"
        f"11. 输出前逐项复核契约目标、契约外章节和原有有效引用；不要输出修订说明或检查清单\n"
    )

    return {
        "revision_count": count,
        "revision_prompt": revision_prompt,
        "revision_contract": revision_contract,
        "revision_new_ref_numbers": [r.get("ref_number") for r in new_refs if r.get("ref_number")],
        "revision_history": prev_history + this_summary,
        "verified_references": verified_refs,
        "review_blocked_suggestions": prev_blocked,
        # 人工意见已并入本轮契约, 清空待处理队列 (避免下一轮重复执行)
        "human_revision_contract": [],
        # 审稿人需要确定性差异对比来判断旧问题是否已解决 (否则易误判"未解决")
        "previous_paper_draft": prior_draft,
        "messages": [{"role": "user", "content": revision_prompt}],
        "current_phase": "paper_writing",
    }


def build_pipeline(checkpointer=None, persist: bool = False) -> StateGraph:
    """构建流水线图

    persist=True 时使用 SQLite 持久化 checkpointer (断点续跑, 借鉴 HKUDS)
    """
    graph = StateGraph(PipelineState)

    graph.add_node("start", start_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("research_planner", research_planner_node)
    graph.add_node("human_confirm_plan", human_confirm_plan_node)
    graph.add_node("literature_review", literature_review_node)
    graph.add_node("pdf_ingestion", pdf_ingestion_node)
    graph.add_node("citation_precheck", citation_precheck_node)
    graph.add_node("outline_generation", outline_generation_node)
    graph.add_node("human_outline", human_outline_node)
    graph.add_node("paper_writing", paper_writing_node)
    graph.add_node("citation_guard", citation_guard_node)
    graph.add_node("citation_check", citation_check_node)
    graph.add_node("human_draft", human_draft_node)
    graph.add_node("paper_review", paper_review_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("increment_revision", increment_revision)
    graph.add_node("format_check", format_check_node)
    graph.add_node("latex_render", latex_render_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("regenerate_figures", regenerate_figures_node)

    graph.set_entry_point("start")

    # 编排式多智能体 (Supervisor 循环):
    #   start → supervisor → 工作智能体 → supervisor → ... → end
    graph.add_edge("start", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "research_planner": "research_planner",
            "literature_review": "literature_review",
            "outline_generation": "outline_generation",
            "regenerate_figures": "regenerate_figures",
            "end": END,
        },
    )

    # 工作智能体 1 (计划): 确定关键词/子主题 → 确认 → supervisor
    graph.add_edge("research_planner", "human_confirm_plan")
    graph.add_conditional_edges(
        "human_confirm_plan",
        route_after_plan,
        {
            "research_planner": "research_planner",
            "supervisor": "supervisor",
        },
    )

    # 工作智能体 2 (research): 检索文献 + 生成总结 → supervisor
    graph.add_edge("literature_review", "pdf_ingestion")
    graph.add_edge("pdf_ingestion", "citation_precheck")
    graph.add_edge("citation_precheck", "supervisor")

    # 工作智能体 3 (write): 大纲 → 写作 → 引用守门/核查 → 审稿循环 → 收尾 → supervisor
    graph.add_edge("outline_generation", "human_outline")
    graph.add_conditional_edges(
        "human_outline",
        route_after_human_outline,
        {
            "outline_generation": "outline_generation",
            "literature_review": "literature_review",
            "paper_writing": "paper_writing",
        },
    )
    graph.add_edge("paper_writing", "citation_guard")
    graph.add_edge("citation_guard", "citation_check")
    graph.add_edge("citation_check", "human_draft")
    graph.add_edge("human_draft", "paper_review")

    # 审阅 → (人工确认) → 修订循环 或 收尾
    graph.add_edge("paper_review", "human_review")
    graph.add_conditional_edges(
        "human_review",
        should_continue_review,
        {
            "paper_writing": "increment_revision",
            "end": "format_check",
        },
    )
    graph.add_edge("increment_revision", "paper_writing")
    graph.add_edge("format_check", "finalize")
    graph.add_edge("finalize", "latex_render")
    graph.add_edge("latex_render", "supervisor")

    # 工作智能体 4 (figures): 重新生成图表 → supervisor
    graph.add_edge("regenerate_figures", "supervisor")

    if checkpointer is None:
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)


def build_writing_graph(checkpointer=None):
    """构建"撰写"子图: 初稿 → 引用守门/核查 → 审阅 → [修订循环] → 格式 → 图表 → LaTeX

    供模块化流水线的"模块3·撰写"使用 (依赖缓存中的综述笔记/大纲/已验证引用)。
    """
    graph = StateGraph(PipelineState)

    graph.add_node("paper_writing", paper_writing_node)
    graph.add_node("citation_guard", citation_guard_node)
    graph.add_node("citation_check", citation_check_node)
    graph.add_node("human_draft", human_draft_node)
    graph.add_node("paper_review", paper_review_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("increment_revision", increment_revision)
    graph.add_node("format_check", format_check_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("latex_render", latex_render_node)

    graph.set_entry_point("paper_writing")
    graph.add_edge("paper_writing", "citation_guard")
    graph.add_edge("citation_guard", "citation_check")
    graph.add_edge("citation_check", "human_draft")
    graph.add_edge("human_draft", "paper_review")

    # 审阅 → (人工确认) → 修订循环 或 收尾
    graph.add_edge("paper_review", "human_review")
    graph.add_conditional_edges(
        "human_review",
        should_continue_review,
        {
            "paper_writing": "increment_revision",
            "end": "format_check",
        },
    )
    graph.add_edge("increment_revision", "paper_writing")
    graph.add_edge("format_check", "finalize")
    graph.add_edge("finalize", "latex_render")
    graph.add_edge("latex_render", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)


def _get_persistent_checkpointer():
    """SQLite 持久化 checkpointer (断点续跑用)"""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from src.config import DATA_DIR

        db_path = DATA_DIR / "pipeline_checkpoints.sqlite"
        conn = __import__("sqlite3").connect(str(db_path), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception as e:
        print(f"  [warning] SQLite checkpointer 不可用: {e}")
        return MemorySaver()


def run_pipeline(
    topic: str = "",
    keywords: list[str] = None,
    sub_topics: list[str] = None,
    time_range: str = "2019-2026",
    max_revisions: int = None,
    checkpointer = None,
    resume: bool = False,
    skip_retrieval: bool = False,
    request: str = None,
) -> dict:
    ensure_utf8_console()  # Windows 终端编码修复

    if checkpointer is None:
        checkpointer = _get_persistent_checkpointer() if resume else MemorySaver()
    app = build_pipeline(checkpointer)

    thread_id = sanitize_filename(topic or request or "research")

    if resume:
        # 断点续跑: 用同一 thread_id 恢复之前的状态 (借鉴 HKUDS 缓存续跑)
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = app.get_state(config)
            if snapshot and snapshot.values.get("research_topic"):
                print(f"  [resume] 恢复会话 {thread_id} (阶段: {snapshot.values.get('current_phase', 'unknown')})")
                for event in app.stream(None, config):
                    for node_name, node_state in event.items():
                        print(f"  [{node_name}] 完成")
                try:
                    return app.get_state(config).values
                except Exception:
                    return {}
            print(f"  [resume] 未找到会话 {thread_id}, 从头开始")
        except Exception as e:
            print(f"  [warning] 续跑失败, 从头开始: {e}")

    # 自然语言请求由入口 planner 节点解析; 此处仅透传。
    # 检索缓存加载已迁移至 research_planner_node (自然语言路径下主题待提取后才已知)。
    initial_state: PipelineState = {
        "messages": [],
        "research_topic": topic or "",
        "research_request": (request or "").strip(),
        "topic_keywords": keywords or [],
        "sub_topics": sub_topics or [],
        "time_range": time_range,
        "revision_count": 0,
        "max_revisions": max_revisions if max_revisions is not None else MAX_REVISIONS,
        "retrieved_papers": [],
        "current_phase": "start",
        "skip_retrieval": bool(skip_retrieval),
    }

    config = {"configurable": {"thread_id": thread_id}}

    langfuse_handler = _get_langfuse_handler()
    if langfuse_handler:
        config["callbacks"] = [langfuse_handler]
        print("  [langfuse] LLM 追踪已启用")

    final_state = None
    for event in app.stream(initial_state, config):
        for node_name, node_state in event.items():
            _print_node_progress(node_name, node_state)

    # 用 checkpointer 的完整最终状态作为返回值 (langgraph 1.x 中
    # stream 事件只含当前节点更新, 直接返回最后事件会丢失 review_score 等字段)
    try:
        final_state = app.get_state(config).values
    except Exception:
        final_state = None

    return final_state if final_state else {}


def _describe_node(node_name: str, node_state: dict) -> str:
    """返回单个节点的进度摘要文本 (CLI 打印与 Web 流式共用)。"""
    if node_name == "paper_review":
        score = node_state.get("review_score", "N/A")
        return f"[{node_name}] 审稿评分: {score}/50"
    elif node_name == "supervisor":
        nxt = node_state.get("supervisor_next", "")
        return f"[supervisor] 调度: {nxt}"
    elif node_name == "regenerate_figures":
        figs = len(node_state.get("figure_paths", []) or [])
        return f"[figures] 图表生成: {figs} 张"
    elif node_name == "research_planner":
        topic = node_state.get("research_topic", "")
        return f"[research_planner] 主题: {topic}"
    elif node_name == "paper_writing":
        words = node_state.get("total_words", "N/A")
        return f"[{node_name}] 初稿字数: {words}"
    elif node_name == "literature_review":
        papers = len(node_state.get("retrieved_papers", []))
        return f"[{node_name}] 检索到: {papers} 篇论文"
    elif node_name == "pdf_ingestion":
        ingested = len(node_state.get("ingested_papers", []))
        return f"[{node_name}] 全文入库: {ingested} 篇论文"
    elif node_name == "citation_precheck":
        verified = node_state.get("citation_precheck_verified", 0)
        not_found = node_state.get("citation_precheck_not_found", 0)
        return f"[{node_name}] 引用预验证: 可信 {verified}, 未通过 {not_found}"
    elif node_name == "outline_generation":
        outline = node_state.get("paper_outline", "")
        return f"[{node_name}] 大纲生成: {len(outline)} 字符"
    elif node_name == "citation_guard":
        invalid = node_state.get("guard_invalid_count", 0)
        return f"[{node_name}] 引用守门: {'✅ 全部在可信清单内' if invalid == 0 else f'⚠️ {invalid} 个越界引用'}"
    elif node_name == "format_check":
        report = node_state.get("format_report", {})
        ok = report.get("all_ok", False)
        line = f"[{node_name}] 格式检查: {'✅ 通过' if ok else '❌ 发现问题'}"
        if not ok:
            tbl = report.get("table", {}).get("issues", [])
            fig = report.get("figure", {}).get("issues", [])
            cit = report.get("citation", {}).get("issues", [])
            for i in (tbl + fig + cit)[:5]:
                line += f"\n    ⚠️ {i}"
        return line
    elif node_name == "citation_check":
        verified = node_state.get("citation_verified_count", 0)
        not_found = node_state.get("citation_not_found_count", 0)
        total = node_state.get("citation_total", 0)
        return f"[{node_name}] 引用核查: {total} 条, 已验证 {verified}, 未找到 {not_found}"
    elif node_name == "finalize":
        cost = node_state.get("total_cost", 0)
        return f"[{node_name}] 成本统计: ${cost}"
    elif node_name == "latex_render":
        ok = node_state.get("tex_compiled", False)
        return f"[{node_name}] LaTeX: {'✅ PDF 已生成' if ok else '⚠ 仅 .tex (编译失败/跳过)'}"
    else:
        return f"[{node_name}] 完成"


def _print_node_progress(node_name: str, node_state: dict):
    """打印单个节点的进度摘要 (run_pipeline 与模块化撰写共用)"""
    print("  " + _describe_node(node_name, node_state))


def run_retrieval_module(
    topic: str,
    keywords: list[str] = None,
    sub_topics: list[str] = None,
    time_range: str = "2019-2026",
) -> dict:
    """模块1·文献查找/入库: 检索 → PDF 下载入库 → 引用预验证。

    产物写入 data/pipeline_cache/{topic}.json (retrieved_papers/unfiltered_papers/
    verified_references), 供模块2/模块3 复用。
    """
    ensure_utf8_console()

    from src.agents.literature_reviewer import run_retrieval
    from src.agents.pdf_ingestor import run_pdf_ingestion
    from src.agents.citation_prechecker import run_citation_precheck
    from src.utils.pipeline_cache import update_retrieval_cache

    print("=" * 60)
    print("  模块1 · 文献查找/入库")
    print("=" * 60)

    state = {
        "research_topic": topic,
        "topic_keywords": keywords or [],
        "sub_topics": sub_topics or [],
        "time_range": time_range,
        "retrieved_papers": [],
        "unfiltered_papers": [],
        "manual_papers": [],
        "current_phase": "start",
    }

    # 1. 检索 + 过滤 + 出处解析
    r = run_retrieval(state)
    state["retrieved_papers"] = r.get("retrieved_papers", [])
    state["unfiltered_papers"] = r.get("unfiltered_papers", [])

    # 2. PDF 下载 + 全文入库 (+ 人工导入 PDF)
    state.update(run_pdf_ingestion(state))

    # 3. 引用预验证 → verified_references
    state.update(run_citation_precheck(state))

    # 4. 保存中间产物
    update_retrieval_cache(
        topic,
        retrieved_papers=state.get("retrieved_papers", []),
        unfiltered_papers=state.get("unfiltered_papers", []),
        verified_references=state.get("verified_references", []),
        keywords=state.get("topic_keywords", []),
        sub_topics=state.get("sub_topics", []),
        time_range=time_range,
    )

    print("=" * 60)
    print(f"  模块1 完成: {len(state.get('retrieved_papers', []))} 篇候选, "
          f"{len(state.get('verified_references', []))} 篇已验证引用")
    print("=" * 60)
    return state


def run_analysis_module(
    topic: str,
    keywords: list[str] = None,
    sub_topics: list[str] = None,
    time_range: str = "2019-2026",
) -> dict:
    """模块2·文件分析: 生成文献综述笔记 + 论文大纲。

    依赖模块1 的检索产物 (从缓存加载), 产出 literature_review_notes / paper_outline。
    """
    ensure_utf8_console()

    from src.agents.literature_reviewer import run_notes_synthesis
    from src.agents.outline_generator import run_outline_generation
    from src.utils.pipeline_cache import load_retrieval_cache_raw, update_retrieval_cache

    cached = load_retrieval_cache_raw(topic)
    if not cached:
        return {"error": "未找到检索缓存，请先运行「模块1 · 文献查找/入库」。", "current_phase": "analysis"}

    print("=" * 60)
    print("  模块2 · 文件分析")
    print("=" * 60)

    state = {
        "research_topic": topic,
        "topic_keywords": keywords or cached.get("keywords", []),
        "sub_topics": sub_topics or cached.get("sub_topics", []),
        "time_range": time_range or cached.get("time_range", ""),
        "retrieved_papers": cached.get("retrieved_papers", []),
        "verified_references": cached.get("verified_references", []),
        "current_phase": "analysis",
    }

    # 1. 生成综述笔记
    state["literature_review_notes"] = run_notes_synthesis(state).get("literature_review_notes", "")

    # 2. 生成大纲
    state["paper_outline"] = run_outline_generation(state).get("paper_outline", "")

    # 3. 保存中间产物
    update_retrieval_cache(
        topic,
        literature_review_notes=state.get("literature_review_notes", ""),
        paper_outline=state.get("paper_outline", ""),
    )

    print("=" * 60)
    print(f"  模块2 完成: 综述笔记 {len(state.get('literature_review_notes', ''))} 字符, "
          f"大纲 {len(state.get('paper_outline', ''))} 字符")
    print("=" * 60)
    return state


def run_writing_module(
    topic: str,
    keywords: list[str] = None,
    sub_topics: list[str] = None,
    time_range: str = "2019-2026",
    max_revisions: int = None,
) -> dict:
    """模块3·撰写: 初稿 → 审阅循环 → 图表 → LaTeX。

    依赖模块1/2 的缓存产物 (综述笔记/大纲/已验证引用), 输出草稿/审稿报告/PDF。
    """
    ensure_utf8_console()

    from src.utils.pipeline_cache import load_retrieval_cache_raw

    cached = load_retrieval_cache_raw(topic)
    if not cached:
        return {"error": "未找到检索缓存，请先运行模块1和模块2。", "current_phase": "paper_writing"}
    if not cached.get("literature_review_notes"):
        return {"error": "缓存中缺少文献综述笔记，请先运行「模块2 · 文件分析」。", "current_phase": "paper_writing"}
    if not cached.get("verified_references"):
        return {"error": "缓存中缺少已验证引用，请先运行「模块1 · 文献查找/入库」。", "current_phase": "paper_writing"}

    max_rev = max_revisions if max_revisions is not None else MAX_REVISIONS
    app = build_writing_graph()

    print("=" * 60)
    print("  模块3 · 撰写")
    print("=" * 60)

    initial_state: PipelineState = {
        "messages": [],
        "research_topic": topic,
        "topic_keywords": keywords or cached.get("keywords", []),
        "sub_topics": sub_topics or cached.get("sub_topics", []),
        "time_range": time_range or cached.get("time_range", ""),
        "literature_review_notes": cached.get("literature_review_notes", ""),
        "verified_references": cached.get("verified_references", []),
        "paper_outline": cached.get("paper_outline", ""),
        "revision_count": 0,
        "max_revisions": max_rev,
        "current_phase": "paper_writing",
    }

    config = {"configurable": {"thread_id": sanitize_filename(topic)}}
    langfuse_handler = _get_langfuse_handler()
    if langfuse_handler:
        config["callbacks"] = [langfuse_handler]

    for event in app.stream(initial_state, config):
        for node_name, node_state in event.items():
            _print_node_progress(node_name, node_state)

    try:
        final_state = app.get_state(config).values
    except Exception:
        final_state = None

    return final_state if final_state else {}
