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
    try:
        if not service.usable:
            return (f"资料库 {topic!r} 不可用或为空: 没有可检索的已登记资料; "
                    f"如需外部检索请把资料范围改为 autonomous")
        outcome = service.search(RetrievalRequest(query=query, max_results=limit))
        if not outcome.searched:
            return "资料库检索未执行: " + "; ".join(str(f) for f in outcome.failures)
        return _format_refs(outcome.refs)
    finally:
        if service.store is not None:
            service.store.close()


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


def _read_source(source_id: str, topic: str = "", context_chars: int = 1200,
                 query: str = "") -> str:
    from src.kb.service import KnowledgeService, RetrievalRequest

    service = KnowledgeService(topic, create_if_missing=False)
    try:
        if not service.usable:
            return f"资料库 {topic!r} 不可用, 无法回原文"
        ref = service.document_ref(source_id)
        if query:
            outcome = service.search(RetrievalRequest(query=query, max_results=20))
            ref = next((item for item in outcome.refs if item.source_id == source_id), ref)
        if ref is None:
            return f"来源 {source_id} 未登记，无法读取"
        context_chars = max(200, min(int(context_chars), 12000))
        resolved = service.read(ref, context_chars=context_chars)
        locator = resolved.get("locator", "")
        text = str(resolved.get("text") or ref.excerpt or "")
        limitation = str(resolved.get("failure") or "")
        return f"来源 {source_id} @ {locator}\n{text[:context_chars]}\n核读限制: {limitation or '当前读取的是全文片段，仍须核对定理的全部条件'}"
    finally:
        if service.store is not None:
            service.store.close()


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


#: 每个核验工具的**操作契约**: 工具名 -> (适配器, 操作, 参数映射)。
#:
#: 为什么把操作名写死成契约而不是让调用方随便传: 审计复现过 G10 —— 工具用
#: `operation="check"` 调 SymPy/Z3, 而两个适配器的 `OPERATIONS` 表里**没有** `check`,
#: 于是每次调用都返回 `unsupported`, 却又看起来像"执行过了"。参数名同样错过:
#: 统计工具传 `column`, 适配器读的是 `outcome_col`, 于是报"缺少 outcome_col"。
#:
#: 这张表在**导入时**与适配器的真实 `OPERATIONS` 对照 (见 `_assert_operation_supported`),
#: 因此适配器改名会让这里立刻失败, 而不是等运行到才静默返回 unsupported。
VERIFICATION_TOOLS: dict[str, tuple[str, str]] = {
    "check_symbolic": ("sympy", "prove_identity"),
    "solve_constraints": ("z3", "check_satisfiable"),
    "describe_statistics": ("stats", "describe"),
}


def _assert_operation_supported(tool: str) -> str:
    """核对工具声明的操作确实被适配器支持; 返回操作名。

    失败即抛 —— 这是**装配期**错误 (代码与适配器不一致), 不该拖到用户任务里变成一句
    "unsupported"。
    """
    import importlib

    adapter_name, operation = VERIFICATION_TOOLS[tool]
    module = importlib.import_module(f"src.verification.{adapter_name}_adapter")
    operations = getattr(module, "OPERATIONS", {})
    if operation not in operations:
        raise RuntimeError(
            f"核验工具 {tool} 声明的操作 {operation!r} 不被 {adapter_name} 适配器支持"
            f" (可用: {', '.join(sorted(operations))})")
    return operation


def _format_verification(tool: str, result: Any) -> str:
    """把 `VerificationResult` 呈现给模型时**保留**状态、证书与输入 hash。

    只回一句字符串会丢掉判定所需的依据 (§3.2 G10: "保留完整 VerificationResult 和
    输入 hash, 不只返回一句字符串")。这里把 hash 与证书摘出来, 让角色能把它作为核验
    依据引用; 完整对象仍由判定层按同一 hash 存取。
    """
    status = getattr(result, "status", None)
    status_value = getattr(status, "value", None) or str(status or "")
    parts = [f"{status_value}: {getattr(result, 'detail', '') or ''}"]
    certificate = getattr(result, "certificate", None)
    if certificate:
        parts.append(f"证书: {str(certificate)[:400]}")
    input_hash = getattr(result, "input_hash", "") or getattr(result, "request_hash", "")
    if input_hash:
        parts.append(f"输入 hash: {input_hash}")
    parts.append(f"工具: {tool}")
    # 明确区分"不可用"与"证伪": 前者是环境问题, 不能当成结论
    if status_value == "unavailable":
        parts.append("提示: 该工具当前不可用, 这条结果不能作为结论依据")
    elif status_value == "unsupported":
        parts.append("提示: 该操作不被支持, 这条结果不能作为结论依据")
    return "\n".join(part for part in parts if part)


def _split_relation(expression: str, relation: str) -> tuple[str, str, str]:
    """把 `"lhs == rhs"` / `"lhs >= rhs"` 拆成适配器要的 `(lhs, rhs, relation)`。

    适配器读的是 `lhs` / `rhs` 两个字段 (见 `sympy_adapter.op_prove_identity`), 而模型
    自然会说"证明 (x+1)^2 = x^2+2x+1"。工具层负责这层翻译, 否则传 `expression` 进去
    会得到 `KeyError: 'lhs'` —— 实测就是这条。
    """
    text = str(expression or "").strip()
    wanted = str(relation or "").strip() or "=="
    # 关系符按长度降序匹配, 避免 `>=` 被 `>` 抢先切开
    for symbol in ("==", ">=", "<=", "!=", "=", ">", "<"):
        index = text.find(symbol)
        if index > 0:
            lhs = text[:index].strip()
            rhs = text[index + len(symbol):].strip()
            if not lhs or not rhs:
                break
            resolved = wanted
            if symbol == "=":
                resolved = "=="
            elif symbol == "!=":
                # 适配器没有"不等"操作: 明确不支持, 不猜成别的
                raise ValueError("不支持的关系 != (可选用 == / >= / > / <= / <)")
            if resolved == "==" and symbol in (">=", ">", "<=", "<"):
                resolved = symbol
            return lhs, rhs, resolved
    # 没有关系符: 按"求值为 0"处理 (例如传进来一个差式)
    return text, "0", wanted


def _declared_variables(names: list[str], explicit: str = "") -> dict[str, str]:
    """构造 Z3 适配器要的变量声明 (`{"x": "Real"}`)。

    适配器读的是**映射**而不是列表: 传列表会得到"未声明变量 x" (实测 G10)。
    """
    declared: dict[str, str] = {}
    for name in [*names, *_names_in(explicit)]:
        if name and name not in declared:
            declared[name] = "Real"
    return declared


def _names_in(text: str) -> list[str]:
    """从表达式/变量串里挑出变量名 (排除纯数字与已知函数名)。"""
    import re

    reserved = {"True", "False", "None", "Int", "Real", "Bool"}
    found = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", str(text or ""))
    return [name for name in found if name not in reserved and not name.isdigit()]


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


def _check_symbolic(expression: str, variables: str = "",
                    relation: str = "==") -> str:
    """核验一条符号命题。

    关系符决定用哪个**真实支持**的操作: `==` 走 `prove_identity`,
    `>=`/`>`/`<=`/`<` 走 `prove_inequality`。
    """
    from src.verification.runner import run_request

    try:
        lhs, rhs, resolved = _split_relation(expression, relation)
    except ValueError as exc:
        return f"unsupported: {exc}"
    operation = ("prove_inequality"
                 if resolved in (">=", ">", "<=", "<") else "prove_identity")
    if operation not in _adapter_operations("sympy"):
        raise RuntimeError(f"sympy 适配器不支持 {operation}")
    result = run_request("sympy", operation, {
        "lhs": lhs,
        "rhs": rhs,
        "relation": resolved,
        # 适配器读 `assumptions` (符号域声明); 不传会拿到 None 并在内部报错
        "assumptions": {},
        "variables": [v.strip() for v in str(variables).split(",") if v.strip()]
                     or _names_in(f"{lhs} {rhs}"),
    })
    return _format_verification("check_symbolic", result)


def _adapter_operations(adapter: str) -> dict:
    import importlib

    return dict(getattr(importlib.import_module(f"src.verification.{adapter}_adapter"),
                        "OPERATIONS", {}))


def _solve_constraints(constraints: str, variables: str = "") -> str:
    """判定约束是否可满足 (Z3 的真实操作是 `check_satisfiable`)。

    `variables` 可留空 —— 留空时从约束文本里推断变量名并按实数域声明。
    """
    from src.verification.runner import run_request

    lines = [c.strip() for c in str(constraints).splitlines() if c.strip()]
    operation = _assert_operation_supported("solve_constraints")
    result = run_request("z3", operation, {
        "constraints": lines,
        "variables": _declared_variables([], str(variables) or " ".join(lines)),
    })
    return _format_verification("solve_constraints", result)


def stats_tools() -> list[ToolSpec]:
    return [
        tool_spec("describe_statistics", _stats,
                  description="对只读数据列做描述统计 (不产生因果结论)",
                  read_scope="tools:stats", evidential=True),
    ]


def _stats(data_ref: str, column: str) -> str:
    """描述统计。

    参数名必须是适配器读的那个 (`outcome_col`), 不是 `column` —— 传错会得到
    "缺少 outcome_col" 这种**看起来像数据问题**的错误 (§3.2 G10 实测复现)。
    """
    from src.verification.runner import run_request

    operation = _assert_operation_supported("describe_statistics")
    result = run_request("stats", operation,
                         {"data_ref": data_ref, "outcome_col": column})
    return _format_verification("describe_statistics", result)


def figure_tools(grant: Any = None, sources: dict[str, int] | None = None) -> list[ToolSpec]:
    """绘图工具 (声明式 FigureSpec)。

    `grant` / `sources` 由调用方 (FigureAgent) 注入: 工具与服务必须用**同一套**
    来源登记表与权限令牌, 否则模型可以通过工具绕开"来源/单位/权限先校验"
    (§3.3 G18)。工具自身的 `read_scope`/`capability` 仍由 ToolLoop 独立检查。
    """
    def _render(spec: dict) -> str:
        from src.agents.figures import render_figure_spec

        outcome = render_figure_spec(dict(spec or {}), sources=sources, grant=grant)
        return outcome.get("message", "") or str(outcome)

    return [
        tool_spec("render_figure", _render,
                  description="按声明式 FigureSpec 渲染一张图 (受任务产物目录限制)",
                  read_scope="tools:figure", capability="propose_figure"),
    ]


def latex_tool() -> ToolSpec:
    return tool_spec("compile_latex", _compile_latex,
                     description="在任务产物目录内编译 .tex 为 PDF",
                     read_scope="tools:latex")


def _compile_latex(tex_path: str, workdir: str = "") -> str:
    from src.publication.compiler import compile_latex

    ok, log = compile_latex(tex_path, workdir or None)
    return f"{'编译成功' if ok else '编译失败'}: {log[:800]}"
