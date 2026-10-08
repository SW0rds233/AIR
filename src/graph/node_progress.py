from __future__ import annotations

"""节点进度摘要与链路追踪: 两个**与具体图无关**的通用件。

为什么单独成模块
----------------
`_describe_node` 与 `_get_langfuse_handler` 原本住在旧的综述图 `graph/pipeline.py` 里,
但它们的用途与会话/研究流程绑定, 与"哪张图"无关:

- `_describe_node`: 把一次节点状态说成一句进度 (CLI 打印与 Web 流式共用);
- `_get_langfuse_handler`: 可选的 Langfuse 追踪回调 (配置了 key 才启用)。

旧综述图删除后, 这两个仍被 `server.py` / `sessions/controller.py` 使用, 因此先抽出来。
描述表里**同时保留**旧节点名与新引擎的节点名: 历史会话的 checkpoint 里仍有旧节点名,
删掉分支会让回看旧会话时进度变成一句空话。
"""

import logging

__all__ = ["describe_node", "get_langfuse_handler", "print_node_progress"]


def get_langfuse_handler():
    """可选 Langfuse 追踪: 配置了 Langfuse key 时启用, 否则返回 `None`。

    兼容多种配置方式:
    - 新版: `LANGFUSE_API_KEY` (langfuse >= 4.x)
    - 旧版: `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`
    - host: `LANGFUSE_HOST` 或 `LANGFUSE_BASE_URL`
    """
    import os

    # langfuse 4.x 装饰器在 client 未初始化时打印
    # "No Langfuse client ... has been initialized" 噪音 (不影响追踪), 过滤该行
    logging.getLogger("langfuse").addFilter(
        lambda record: "No Langfuse client" not in record.getMessage())

    api_key = os.getenv("LANGFUSE_API_KEY", "")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_HOST", "") or os.getenv("LANGFUSE_BASE_URL", "")
    if not (api_key or (public_key and secret_key)):
        return None
    try:
        # langfuse 4.x 通过环境变量初始化 client (SDK 内部读 os.getenv);
        # `.env` 由 dotenv 加载, 需显式注入进程环境变量
        if api_key:
            os.environ.setdefault("LANGFUSE_API_KEY", api_key)
            os.environ.setdefault("LANGFUSE_PUBLIC_KEY", api_key)
        if secret_key:
            os.environ.setdefault("LANGFUSE_SECRET_KEY", secret_key)
        if public_key and not os.environ.get("LANGFUSE_PUBLIC_KEY"):
            os.environ.setdefault("LANGFUSE_PUBLIC_KEY", public_key)
        if host:
            os.environ.setdefault("LANGFUSE_HOST", host)

        from langfuse import get_client as _get_lf_client

        _get_lf_client(public_key=public_key or api_key)
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(public_key=public_key or api_key)
    except ImportError:
        print("  [warning] langfuse 未安装，跳过追踪 (pip install langfuse)")
        return None
    except Exception as e:  # noqa: BLE001 - 追踪不可用不得影响研究
        print(f"  [warning] langfuse 初始化失败 ({e})，跳过追踪")
        return None


def describe_node(node_name: str, node_state: dict) -> str:
    """返回单个节点的进度摘要文本 (CLI 打印与 Web 流式共用)。

    同时覆盖三类节点名: 团队会话 (`research_team*`)、理论引擎 (`theory_*`) 与
    历史会话里的旧综述节点 —— 旧节点名保留是为了回看历史会话时仍能说出进度。
    """
    state = node_state or {}
    if node_name == "research_team":
        event = str(state.get("event") or state.get("node") or "")
        agent = str(state.get("agent") or "")
        if event == "task_started":
            return f"[团队] {agent} 开始: {state.get('objective', '')}"[:180]
        if event == "task_result":
            return f"[团队] {agent} 结果: {state.get('outcome', '')}"[:180]
        if event == "supervisor_decision":
            return (f"[主控] 第 {state.get('round', '?')} 轮决策: "
                    f"{state.get('decision', '')} — {state.get('reason', '')}")[:200]
        if event == "brief_ready":
            return "[团队] 已完成任务画像"
        if event == "plan_ready":
            return f"[团队] 计划就绪: {len(state.get('tasks') or [])} 个任务"
        if event == "run_finished":
            return f"[团队] 运行结束: {state.get('status', '')}"
        if event == "progress_saved":
            return f"[团队] {agent} 中间成果已落盘: {state.get('path', '')}"[:220]
        return f"[团队] {event or '进行中'}"
    if node_name == "research_team_done":
        summary = state.get("summary") or {}
        return (f"[团队] 完成: {summary.get('rounds', '?')} 轮, "
                f"{summary.get('tasks', '?')} 个任务, 交付包 {state.get('package_dir', '') or '未产出'}")
    if node_name == "paper_review":
        return f"[{node_name}] 审稿评分: {state.get('review_score', 'N/A')}/50"
    if node_name == "supervisor":
        return f"[supervisor] 调度: {state.get('supervisor_next', '')}"
    if node_name == "regenerate_figures":
        return f"[figures] 图表生成: {len(state.get('figure_paths', []) or [])} 张"
    if node_name == "research_planner":
        return f"[research_planner] 主题: {state.get('research_topic', '')}"
    if node_name == "paper_writing":
        return f"[{node_name}] 初稿字数: {state.get('total_words', 'N/A')}"
    if node_name == "literature_review":
        return f"[{node_name}] 检索到: {len(state.get('retrieved_papers', []) or [])} 篇论文"
    if node_name == "pdf_ingestion":
        return f"[{node_name}] 全文入库: {len(state.get('ingested_papers', []) or [])} 篇论文"
    if node_name == "citation_precheck":
        verified = state.get("citation_precheck_verified", 0)
        not_found = state.get("citation_precheck_not_found", 0)
        return f"[{node_name}] 引用预验证: 可信 {verified}, 未通过 {not_found}"
    if node_name == "outline_generation":
        return f"[{node_name}] 大纲生成: {len(state.get('paper_outline', ''))} 字符"
    if node_name == "citation_guard":
        invalid = state.get("guard_invalid_count", 0)
        tail = "全部在可信清单内" if invalid == 0 else f"{invalid} 个越界引用"
        return f"[{node_name}] 引用守门: {tail}"
    if node_name == "format_check":
        report = state.get("format_report", {}) or {}
        ok = bool(report.get("all_ok", False))
        line = f"[{node_name}] 格式检查: {'通过' if ok else '发现问题'}"
        if not ok:
            issues = ((report.get("table", {}) or {}).get("issues", [])
                      + (report.get("figure", {}) or {}).get("issues", [])
                      + (report.get("citation", {}) or {}).get("issues", []))
            for item in issues[:5]:
                line += f"\n    {item}"
        return line
    if node_name == "citation_check":
        return (f"[{node_name}] 引用核查: {state.get('citation_total', 0)} 条, "
                f"已验证 {state.get('citation_verified_count', 0)}, "
                f"未找到 {state.get('citation_not_found_count', 0)}")
    if node_name == "finalize":
        return f"[{node_name}] 成本统计: ${state.get('total_cost', 0)}"
    if node_name == "latex_render":
        ok = state.get("tex_compiled", False)
        return f"[{node_name}] LaTeX: {'PDF 已生成' if ok else '仅 .tex (编译失败/跳过)'}"
    if node_name == "theory_bootstrap":
        return f"[theory] 问题形式化: {'完成' if state.get('bootstrapped') else '需要澄清'}"
    if node_name == "theory_step":
        action = (state.get("action") or {}).get("action", "")
        return f"[theory] 第 {state.get('step_index', '?')} 步: {action}"
    if node_name == "theory_finalize":
        gate = "通过" if state.get("gate_passed") else "未通过"
        return f"[theory] 交付门槛: {gate} (研究包: {state.get('package_dir', '')})"
    return f"[{node_name}] 完成"


def print_node_progress(node_name: str, node_state: dict) -> None:
    """打印单个节点的进度摘要 (CLI 用)。"""
    print("  " + describe_node(node_name, node_state))
