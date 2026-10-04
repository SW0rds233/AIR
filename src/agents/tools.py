from __future__ import annotations

"""子智能体的受控工具集 (合并计划 §3.2 / §7.2)。

工具是"智能体决定何时调用、程序保证执行范围"的那一半: 每个 `ToolSpec` 自带
**读范围或可写能力**, 由运行时签发的令牌实际控制 (不是提示词约定)。

这里只注册**真实存在**的入口, 并且对每个工具如实标注可用性 —— 系统已有一条现场
缺陷是"工具不可用却伪造检索完成", 因此探测失败时工具仍然注册但会返回明确的
错误说明, 而不是静默成功。
"""

from typing import Any

from src.agents.runtime import ToolSpec, tool_spec

__all__ = [
    "evidence_tools",
    "figure_tools",
    "latex_tool",
    "math_tools",
    "readonly_data_tools",
    "stats_tools",
]


def _arxiv(query: str, max_results: int = 10) -> list[dict]:
    from src.tools.search_tools import arxiv_search

    fn = getattr(arxiv_search, "func", arxiv_search)
    return list(fn(query, max_results) or [])


def _openalex(query: str, max_results: int = 10) -> list[dict]:
    from src.tools.search_tools import openalex_search

    fn = getattr(openalex_search, "func", openalex_search)
    return list(fn(query, max_results) or [])


def _semantic_scholar(query: str, max_results: int = 10) -> list[dict]:
    from src.tools.search_tools import semantic_scholar_search

    fn = getattr(semantic_scholar_search, "func", semantic_scholar_search)
    return list(fn(query, max_results) or [])


def _search_all(query: str, max_results: int = 10) -> list[dict]:
    from src.tools.search_tools import search_all_sources

    fn = getattr(search_all_sources, "func", search_all_sources)
    return list(fn(query, max_results) or [])


def _format_papers(papers: Any) -> str:
    rows = list(papers or [])
    lines = [f"检索到 {len(rows)} 条:"]
    for index, paper in enumerate(rows[:25], 1):
        abstract = str(paper.get("abstract") or "")[:240]
        lines.append(
            f"{index}. {paper.get('title', '')} | {paper.get('year', '')} | "
            f"{paper.get('venue') or paper.get('source', '')} | 被引 {paper.get('citations', 0)}")
        if abstract:
            lines.append(f"   摘要: {abstract}")
    return "\n".join(lines)


def evidence_tools() -> list[ToolSpec]:
    """检索与资料接入类工具 (EvidenceAgent)。

    检索后端**不设最低篇数与固定年份** —— 合并计划 §7.2 明确要求清除
    "固定年份/最低篇数/学科词", 查询与停止条件由角色按研究缺口决定。
    """
    return [
        tool_spec("search_all_sources", _search_all,
                  description="同时检索 arXiv / Semantic Scholar / OpenAlex",
                  read_scope="tools:search", evidential=True, formatter=_format_papers),
        tool_spec("arxiv_search", _arxiv, description="按关键词检索 arXiv 预印本",
                  read_scope="tools:search", evidential=True, formatter=_format_papers),
        tool_spec("openalex_search", _openalex,
                  description="按关键词检索 OpenAlex (支持中文关键词)",
                  read_scope="tools:search", evidential=True, formatter=_format_papers),
        tool_spec("semantic_scholar_search", _semantic_scholar,
                  description="按关键词检索 Semantic Scholar",
                  read_scope="tools:search", evidential=True, formatter=_format_papers),
        tool_spec("search_local_kb", _search_local_kb,
                  description="在已授权资料库内检索可定位片段 (返回来源 id/定位/片段)",
                  read_scope="sources", evidential=True, formatter=_format_refs),
        tool_spec("read_source", _read_source,
                  description="按来源 id 回原文取上下文 (返回定位与片段)",
                  read_scope="fulltext", evidential=True),
        tool_spec("scan_paths", _scan_paths,
                  description="扫描本机路径以预览可导入的文献文件 (不导入、不复制)",
                  read_scope="sources", formatter=str),
        tool_spec("import_paths", _import_paths,
                  description="把授权路径下的文献登记入资料库 (只读引用, 不复制原文件)",
                  read_scope="sources"),
    ]


def _topic_from(kwargs: dict) -> str:
    return str(kwargs.get("topic", "") or "")


def _search_local_kb(query: str, topic: str = "", limit: int = 8) -> str:
    from src.kb.service import KnowledgeService, RetrievalRequest

    service = KnowledgeService(topic, create_if_missing=False)
    if not service.usable:
        return (f"资料库 {topic!r} 不可用或为空: 没有可检索的已登记资料; "
                f"如需外部检索请把资料范围改为 autonomous")
    outcome = service.search(RetrievalRequest(query=query, max_results=limit))
    if not outcome.searched:
        return "资料库检索未执行: " + "; ".join(str(f) for f in outcome.failures)
    return _format_refs(outcome.refs)


def _format_refs(refs: Any) -> str:
    rows = list(refs or [])
    if not rows:
        return "没有命中可定位来源 (这不等于该结论不成立)"
    lines = [f"命中 {len(rows)} 条可定位来源:"]
    for index, ref in enumerate(rows, 1):
        locatable = "可定位" if getattr(ref, "is_locatable", lambda: False)() else "无定位"
        lines.append(
            f"{index}. [{ref.kind}] {ref.title} @ {ref.locator or '(无定位)'} "
            f"({locatable}) id={ref.source_id}")
        excerpt = str(getattr(ref, "excerpt", "") or "")[:240]
        if excerpt:
            lines.append(f"   片段: {excerpt}")
    return "\n".join(lines)


def _read_source(source_id: str, topic: str = "", context_chars: int = 1200) -> str:
    from src.kb.service import KnowledgeService, SourceRef

    service = KnowledgeService(topic, create_if_missing=False)
    if not service.usable:
        return f"资料库 {topic!r} 不可用, 无法回原文"
    ref = SourceRef(source_id=source_id, kind="doc")
    resolved = service.read(ref, context_chars=context_chars)
    if not resolved:
        return f"来源 {source_id} 无法读取"
    locator = resolved.get("locator", "")
    text = str(resolved.get("text") or "")
    return f"来源 {source_id} @ {locator}\n{text[:context_chars]}"


def _scan_paths(paths: list[str], recursive: bool = True, label: str = "") -> str:
    from src.kb import path_import as pi

    report = pi.scan_paths(pi.ScanRequest(paths=list(paths or []), recursive=recursive,
                                          label=label))
    body = report.to_dict()
    return (
        f"扫描结果: 可用 {body['counts']['ok']} / 跳过 {body['counts']['skipped']} / "
        f"拒绝 {body['counts']['denied']} / 截断 {body['truncated']}\n"
        + "\n".join(f"- {f['path']} [{f['status']}] {f['reason']}"
                    for f in body["files"][:40])
        + "\n".join(f"- {f['path']} [denied] {f['reason']}" for f in body["denied"][:20])
    )


def _import_paths(paths: list[str], topic: str, label: str = "",
                  recursive: bool = True, embed: bool = False) -> str:
    from src.kb import path_import as pi

    report = pi.import_paths(None, topic, pi.ScanRequest(
        paths=list(paths or []), recursive=recursive, label=label), embed=embed)
    counts = report.counts()
    return (f"导入结果: 新增 {counts['imported']} / 合并 {counts['merged']} / "
            f"幂等命中 {counts['duplicates']} / 跳过 {counts['skipped']} / "
            f"拒绝 {counts['denied']} / 失败 {counts['failed']} / "
            f"截断 {report.truncated}")


def readonly_data_tools() -> list[ToolSpec]:
    """只读数据源工具 (检索的数据任务 / 验证方案)。"""
    return [
        tool_spec("describe_dataset", _describe_source,
                  description="描述只读数据源的结构 (表/列/单位/缺失/快照 hash)",
                  read_scope="tools:readonly_data", evidential=True),
        tool_spec("load_dataset_rows", _load_rows,
                  description="按行读取只读数据源 (受行列上限与授权根限制)",
                  read_scope="tools:readonly_data", evidential=True),
    ]


def _describe_source(data_ref: str) -> str:
    from src.kb.adapters.readonly_data import describe_source

    schema = describe_source(data_ref)
    return schema.describe()


def _load_rows(data_ref: str, limit: int = 20) -> str:
    from src.kb.adapters.readonly_data import load_rows

    rows, reason, meta = load_rows(data_ref, limit=limit)
    if reason:
        return f"读取失败: {reason}"
    return (f"快照 {meta.get('snapshot_hash', '')[:12]} | 行 {len(rows)}\n"
            + "\n".join(str(row) for row in rows[:limit]))


def math_tools() -> list[ToolSpec]:
    """符号/约束核验工具 (推理角色请求, 由判定层执行)。"""
    return [
        tool_spec("check_symbolic", _check_symbolic,
                  description="用 SymPy 核验一条符号等式/不等式 (返回证书或反例)",
                  read_scope="tools:math", evidential=True),
        tool_spec("solve_constraints", _solve_constraints,
                  description="用 Z3 判定约束可满足性 (返回模型或不可满足)",
                  read_scope="tools:math", evidential=True),
    ]


def _check_symbolic(expression: str, variables: str = "") -> str:
    from src.verification.runner import run_request

    result = run_request("sympy", "check",
                         {"expression": expression,
                          "variables": [v for v in str(variables).split(",") if v]})
    return f"{result.status.value}: {result.detail}"


def _solve_constraints(constraints: str) -> str:
    from src.verification.runner import run_request

    lines = [c.strip() for c in str(constraints).splitlines() if c.strip()]
    result = run_request("z3", "check", {"constraints": lines})
    return f"{result.status.value}: {result.detail}"


def stats_tools() -> list[ToolSpec]:
    return [
        tool_spec("describe_statistics", _stats,
                  description="对只读数据列做描述统计 (不产生因果结论)",
                  read_scope="tools:stats", evidential=True),
    ]


def _stats(data_ref: str, column: str) -> str:
    from src.verification.runner import run_request

    result = run_request("stats", "describe", {"data_ref": data_ref, "column": column})
    return f"{result.status.value}: {result.detail}"


def figure_tools() -> list[ToolSpec]:
    return [
        tool_spec("render_figure", _render_figure,
                  description="按声明式 FigureSpec 渲染一张图 (受任务产物目录限制)",
                  read_scope="tools:figure", capability="propose_figure"),
    ]


def _render_figure(spec: dict) -> str:
    from src.agents.figures import render_figure_spec

    outcome = render_figure_spec(dict(spec or {}))
    return outcome.get("message", "") or str(outcome)


def latex_tool() -> ToolSpec:
    return tool_spec("compile_latex", _compile_latex,
                     description="在任务产物目录内编译 .tex 为 PDF",
                     read_scope="tools:latex")


def _compile_latex(tex_path: str, workdir: str = "") -> str:
    from src.rag.latex_compiler import compile_latex

    ok, log = compile_latex(tex_path, workdir or None)
    return f"{'编译成功' if ok else '编译失败'}: {log[:800]}"
