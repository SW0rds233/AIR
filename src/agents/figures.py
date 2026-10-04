from __future__ import annotations

"""FigureAgent 配图 (合并计划 §3.1 / §7.3 / §3.3 G18)。

职责: 按论证结构、来源数据、公式/模型与版面要求**选图**、绘图、修正。
提交 `FigureSpec` 与产物: 数据与脚本引用、图注、核查结果。

G18 的三条硬约束 (修复前都缺):

1. **唯一 FigureSpec/产物服务**: 一切图都经 `render_figure_spec()` 落到
   `figures/<run>/<task>/v<artifact_version>/`; 多 run、以及同一次运行里的多张图
   都不会互相覆盖。修复前默认写 `OUTPUT_DIR/figures` 且只按 `kind-purpose` 命名,
   两次运行会覆盖同一张图。
2. **先校验再渲染**: 来源引用、模型域 (公式变量)、单位与权限 (令牌的
   `tools:figure` 读范围 + `propose_figure` 能力) 全部在渲染**之前**检查; 任一项
   不通过就**不渲染**, 并把结构化失败交回主控 —— "不支持/绘制失败可见", 而不是
   修复前的"记录校验问题后照样画"。
3. **公式只走白名单解析 + 受限执行**: 见 `src.rag.figure_formula`; 任意 Python
   (`__import__('os').system('echo x')`) 在解析阶段就被显式拒绝, 不再经 `parse_expr`。

另外每张图都记录 `input_versions` (它由哪些对象的哪个版本产出), 并以 `ArtifactRef`
指向**真实存在**的文件; 自由绘图脚本题材不再执行 (进程隔离不等于权限隔离)。
"""

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from src.agents.base import (
    AgentBase,
    as_bool,
    as_list_of_str,
    build_proposal,
    clip,
    context_summary,
    extract_json,
    record_artifact,
)
from src.agents.protocol import (
    AgentResult,
    AgentTask,
    CapabilityGrant,
    ChangeProposal,
    ContextPack,
    NeedKind,
    ResearchNeed,
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec
from src.agents.tools import figure_tools
from src.rag.figure_formula import (
    DEFAULT_FORMULA_VARIABLES,
    FormulaRejected,
    parse_formula,
)
from src.rag.figure_generator import (
    FigureScope,
    current_figure_scope,
    figure_artifact_dir,
    figure_scope_bound,
)

__all__ = [
    "QUANTITATIVE_FIGURE_KINDS",
    "SUPPORTED_FIGURE_KINDS",
    "FigureAgent",
    "figure_input_versions",
    "figure_source_index",
    "permission_problems",
    "render_figure_spec",
    "validate_figure_spec",
]

#: 支持的图种。每种都必须声明它能承担什么论证作用与数据来源。
SUPPORTED_FIGURE_KINDS: tuple[str, ...] = (
    "bar",            # 分类对比
    "line",           # 趋势
    "scatter",        # 两变量关系
    "distribution",   # 分布
    "heatmap",        # 矩阵关系
    "curve",          # 函数曲线 (来自模型/公式)
    "diagram",        # 结构/流程示意 (来自模型关系, 不是从领域模板套)
)

#: 需要真实数值系列的图种 (没有数据就只能示意, 不能用示意冒充数据)。
QUANTITATIVE_FIGURE_KINDS: tuple[str, ...] = (
    "bar", "line", "scatter", "distribution", "heatmap",
)


class FigureAgent(AgentBase):
    """配图角色。"""

    role = "figures"
    prompt_version = "figures/v1"
    kinds = ("figure",)

    SYSTEM = """你是"配图"智能体。

你的任务: 按论证需要**选择**该画什么图, 并给出可复现的绘图声明。

每张图必须声明:
1. 用途 (这张图在论证里承担什么: 对比/趋势/关系/分布/结构);
2. 图种 (bar/line/scatter/distribution/heatmap/curve/diagram);
3. 数据来源 (来自哪条已登记数据/模型/公式的 **id**; 没有来源就不要画);
4. 轴与单位、图注 (图注必须与正文的说法一致; 无量纲才可写 unitless: true);
5. 它支持哪条结论。

纪律:
- 不得编造数值: 数据必须来自给定来源; 缺数据就提出需求, 不要画示意图冒充数据图;
- 不要为每个题目固定画几张图; 也不要把某个领域的流程套到别的领域;
- 结构化关系 (变量之间的机制) 用 diagram; 有真实数值才用定量图;
- 公式只允许基本运算与白名单数学函数 (sin/cos/exp/log/sqrt/...); 不得写代码。

最终输出 JSON:
{"figures": [{"purpose": "", "kind": "", "data_source": "", "x": "", "y": "",
              "x_unit": "", "y_unit": "", "unitless": false,
              "series": [{"label": "", "values": []}], "equations": [""],
              "nodes": [], "edges": [], "caption": "", "supports": ""}],
 "needs": [{"kind": "figure_data", "statement": "", "why": ""}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        """绘图工具**带上本次任务的来源登记表与令牌**, 让工具与服务用同一套校验。"""
        return figure_tools(grant=context.grant, sources=figure_source_index(context))

    def _run(self, task: AgentTask, context: ContextPack, runtime: AgentRuntime,
             usage: UsageRecord, tools: list[ToolSpec]) -> AgentResult:
        specs: list[dict[str, Any]] = []
        parse_note = ""
        if runtime.llm_available() and not task.budget.exceeded_by(usage):
            from langchain_core.messages import HumanMessage, SystemMessage

            prompt = (
                f"# 配图需求\n{task.objective}\n"
                f"# 上下文\n{context_summary(context)}"
            )
            llm = runtime.llm(task, usage, stage="figures")
            try:
                result = llm.invoke([SystemMessage(content=self.SYSTEM),
                                     HumanMessage(content=prompt)])
                payload, parse_note = extract_json(getattr(result, "content", ""))
            except Exception as e:  # noqa: BLE001
                payload, parse_note = None, f"配图调用失败: {e}"
            if isinstance(payload, dict):
                specs = [s for s in (payload.get("figures") or []) if isinstance(s, dict)]

        if not specs:
            specs = self.select_figures(task, context)
            if parse_note:
                runtime.emit("figure_fallback", {"task_id": task.task_id,
                                                 "reason": parse_note})

        if not specs:
            return self.blocked(
                task, "没有可用的数据或模型, 因此不配图 (不为凑数量画示意图)",
                needs=[ResearchNeed(
                    kind=NeedKind.figure_data,
                    statement="缺少可绘图的数据或模型结构",
                    why="没有数据来源的图只能示意, 不能作为研究证据",
                    acceptance=["给出可引用的数据/模型, 或明确本交付不需要图"],
                    blocking=False)],
                usage=usage)

        sources = figure_source_index(context)
        changes: list[ChangeProposal] = []
        rendered: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        artifacts: list[Any] = []
        unresolved: list[str] = []
        for index, spec in enumerate(specs, start=1):
            spec = dict(spec)
            version = _artifact_version(spec, index)
            spec["artifact_version"] = version
            # 身份先绑定再渲染: 目录按 (run, task, 产物版本) 分层, 谁都不会覆盖谁。
            with figure_scope_bound(task.run_id, task_id=task.task_id,
                                    artifact_version=version):
                outcome = render_figure_spec(spec, context=context, sources=sources,
                                             grant=context.grant)
            problems = list(outcome.get("problems") or []) or \
                [str(outcome.get("message", "") or "渲染未成功")]
            # 绝对路径不进共享状态 (§13.3): 载荷里只留产物根下的相对 uri。
            spec["render"] = {key: value for key, value in outcome.items() if key != "path"}
            if not outcome.get("ok"):
                spec["rejected"] = True
                rejected.append({
                    "purpose": clip(str(spec.get("purpose", "")), 120),
                    "kind": str(spec.get("kind", "")),
                    "status": str(outcome.get("status", "failed")),
                    "problems": problems,
                })
                unresolved.extend(
                    f"图 {clip(str(spec.get('purpose', '')), 60)!r} 未渲染: {p}"
                    for p in problems[:3])
                runtime.emit("figure_rejected", {
                    "task_id": task.task_id, "kind": str(spec.get("kind", "")),
                    "status": str(outcome.get("status", "failed")),
                    "problems": problems[:4],
                })
                continue
            spec["input_versions"] = dict(outcome.get("input_versions") or {})
            rendered.append(spec)
            changes.append(build_proposal(
                "figure",
                payload=spec,
                rationale=clip(str(spec.get("purpose", "") or ""), 200)
                          or "按论证需要选择图种",
                input_versions=dict(spec["input_versions"]),
            ))
            if outcome.get("uri"):
                artifacts.append(record_artifact(
                    kind="png", uri=str(outcome["uri"]),
                    label=clip(str(spec.get("caption") or spec.get("purpose") or ""), 80),
                    summary=clip(str(spec.get("purpose", "")), 200),
                    sha256=str(outcome.get("sha256", "")),
                    size=int(outcome.get("bytes", 0) or 0),
                ))

        summary = f"选择 {len(specs)} 张图 (" + ", ".join(
            str(s.get("kind", "?")) for s in specs) + ")"
        if rejected:
            summary += f"; {len(rejected)} 张未通过校验/渲染 (未落盘, 已如实上报)"
        if unresolved:
            summary += f"; {len(unresolved)} 项待处理"
        payload_out: dict[str, Any] = {
            "schema": "FigureSpec/v1",
            "figures": rendered,
            "rejected": rejected,
            "scope": {"run_id": task.run_id, "task_id": task.task_id},
        }
        outcome_fn = self.completed if rendered and not rejected else self.partial
        return outcome_fn(task, summary, changes=changes, artifacts=artifacts,
                          unresolved=unresolved[:6], usage=usage,
                          payload=payload_out)

    # ---- 确定性选图 ----
    def select_figures(self, task: AgentTask,
                       context: ContextPack) -> list[dict[str, Any]]:
        """按**论证需要**选图, 而不是按领域模板。

        判据:
        - 有真实数据集卡 (且卡里带数值系列) → 定量图 (有分组维度用 bar, 有时间/序列
          维度用 line);
        - 只有模型/公式 → `curve` (若给出了方程) 或 `diagram` (关系示意);
        - 什么都没有 → 不画 (调用方据此如实说明)。

        单位/数值都**照抄来源卡片的声明**: 卡片没声明单位就不替它编 —— 那样这张图
        会在渲染前的校验里被判不通过并如实上报, 而不是画一张没有单位的图。
        """
        figures: list[dict[str, Any]] = []
        datasets = context.objects.get("dataset") or []
        for dataset in datasets[:3]:
            columns = as_list_of_str(dataset.get("columns") or dataset.get("fields"))
            if not columns:
                continue
            kind = "line" if any(_is_temporal(c) for c in columns) else "bar"
            spec: dict[str, Any] = {
                "purpose": f"展示数据集 {clip(str(dataset.get('name', '')), 60)} 的分布/对比",
                "kind": kind,
                "data_source": str(dataset.get("id", "") or ""),
                "x": _column_name(columns[0]),
                "y": _column_name(columns[1] if len(columns) > 1 else columns[0]),
                "x_unit": str(dataset.get("x_unit", "") or ""),
                "y_unit": str(dataset.get("y_unit", "") or ""),
                "caption": f"数据来源: {dataset.get('name', '')}",
                "supports": task.subquestion,
            }
            series = _dataset_series(dataset)
            if series:
                spec["series"] = series
                spec["values_available"] = True
            else:
                spec["values_available"] = False
                spec["caption"] = (f"{spec['caption']} (数据卡里没有可绘制的数值序列)")
            if as_bool(dataset.get("unitless")):
                spec["unitless"] = True
            figures.append(spec)
        for model in (context.objects.get("model") or [])[:3]:
            equations = as_list_of_str(model.get("equations"))
            if equations:
                spec = {
                    "purpose": f"展示模型 {clip(str(model.get('name', '')), 60)} 的函数形式",
                    "kind": "curve",
                    "data_source": str(model.get("id", "") or ""),
                    "equations": equations[:3],
                    "caption": clip(str(model.get("mechanism", "")), 200),
                    "supports": task.subquestion,
                    "x_unit": _variable_unit(model, "x"),
                    "y_unit": _variable_unit(model, "y"),
                }
                if as_bool(model.get("unitless")):
                    spec["unitless"] = True
                figures.append(spec)
            elif model.get("mechanism"):
                figures.append({
                    "purpose": f"示意模型 {clip(str(model.get('name', '')), 60)} 的机制关系",
                    "kind": "diagram",
                    "data_source": str(model.get("id", "") or ""),
                    "nodes": [{"id": v.get("symbol", ""), "label": v.get("meaning", "")}
                              for v in (model.get("variables") or [])[:8]
                              if isinstance(v, dict)],
                    "edges": [],
                    "caption": clip(str(model.get("mechanism", "")), 200),
                    "supports": task.subquestion,
                })
        return figures


def _is_temporal(column: str) -> bool:
    text = str(column or "").lower()
    return any(marker in text for marker in ("year", "年", "date", "日期", "time", "时间",
                                             "period", "期", "month", "月"))


def _column_name(column: Any) -> str:
    """列名可能是 `{"name": "年份", "unit": "年"}` 或纯字符串。"""
    if isinstance(column, dict):
        return str(column.get("name") or column.get("field") or "")
    return str(column or "")


def _dataset_series(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """从数据集卡里取**已登记的**数值系列 (没有就返回空, 不编造)。"""
    raw = dataset.get("series") or dataset.get("values") or []
    series: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        raw = [{"label": key, "values": value} for key, value in raw.items()]
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        values = item.get("values") or []
        numeric = [float(v) for v in values
                   if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numeric:
            series.append({"label": str(item.get("label", "")), "values": numeric})
    return series


def _variable_unit(model: dict[str, Any], symbol: str) -> str:
    """模型变量声明的单位 (模型没声明就返回空, 由校验如实拒绝)。"""
    for variable in model.get("variables") or []:
        if isinstance(variable, dict) and str(variable.get("symbol", "")) == symbol:
            return str(variable.get("unit", "") or "")
    return ""


def _artifact_version(spec: dict[str, Any], index: int) -> str:
    """本张图的产物版本: 显式声明优先, 否则按它在本次任务里的序号。"""
    raw = spec.get("artifact_version")
    if raw is None or isinstance(raw, bool) or str(raw).strip() == "":
        return str(max(1, int(index)))
    return str(raw).strip()


# ----------------------------------------------------------------------
# 来源登记表
# ----------------------------------------------------------------------
def figure_source_index(context: ContextPack | None) -> dict[str, int]:
    """本任务可引用的来源登记表: `{对象 id: 版本}`。

    图的 `data_source` 必须是这里的键 —— 这是"来源先校验"的事实依据: 没有登记表
    就没有可核对的身份, 只能拒绝而不是"看起来像来源就画"。
    """
    index: dict[str, int] = {}
    for rows in (context.objects or {}).values() if context else []:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            object_id = str(row.get("id", "") or "")
            if not object_id or object_id in index:
                continue
            try:
                index[object_id] = int(row.get("version", 1) or 1)
            except (TypeError, ValueError):
                index[object_id] = 1
    return index


def figure_input_versions(spec: dict[str, Any],
                          sources: dict[str, int]) -> dict[str, int]:
    """本图依据的对象版本 (只登记**表内**对象, 版本以登记表为准)。"""
    wanted = [str(spec.get("data_source", "") or "")]
    declared = spec.get("input_versions")
    if isinstance(declared, dict):
        wanted.extend(str(key) for key in declared)
    out: dict[str, int] = {}
    for object_id in wanted:
        if object_id and object_id in sources:
            out[object_id] = int(sources[object_id])
    return out


def _model_domain(context: ContextPack | None, data_source: str) -> set[str]:
    """公式允许出现的符号: 默认 `x/y/t` + 被引用模型自己声明的变量符号。"""
    domain = {str(name) for name in DEFAULT_FORMULA_VARIABLES}
    for row in (context.objects.get("model") if context else None) or []:
        if not isinstance(row, dict) or str(row.get("id", "")) != data_source:
            continue
        for variable in row.get("variables") or []:
            symbol = str(variable.get("symbol", "") if isinstance(variable, dict)
                         else variable or "").strip()
            if symbol.isidentifier():
                domain.add(symbol)
        for symbol in as_list_of_str(row.get("symbols")):
            if symbol.strip().isidentifier():
                domain.add(symbol.strip())
    return domain


# ----------------------------------------------------------------------
# 渲染前校验 (来源 / 模型域 / 单位 / 权限)
# ----------------------------------------------------------------------
def validate_figure_spec(spec: dict[str, Any], *,
                         context: ContextPack | None = None,
                         sources: dict[str, int] | None = None,
                         grant: CapabilityGrant | None = None) -> list[str]:
    """渲染前的确定性校验; 返回问题列表 (**非空即不得渲染**)。

    四类必查项 (合并计划 §3.3 G18: "来源、模型域、单位、权限先校验再渲染"):

    - **来源**: `data_source` 必须出现在本任务的来源登记表里 (没有登记表就拒绝);
    - **模型域**: `curve` 图的方程必须在模型声明的符号域内, 且只使用白名单语法;
    - **单位**: 定量图与曲线图必须声明轴单位, 或显式 `unitless: true`;
    - **权限**: 令牌必须授权读范围 `tools:figure` 与可写能力 `propose_figure`。

    此外保留原有的图种/用途/图注/数值系列检查。本函数不修改 `spec`。
    """
    problems: list[str] = []
    kind = str(spec.get("kind", "") or "")
    if kind not in SUPPORTED_FIGURE_KINDS:
        problems.append(f"未登记的图种: {kind!r}")
    if not str(spec.get("purpose", "") or "").strip():
        problems.append("图没有说明用途")
    if not str(spec.get("caption", "") or "").strip():
        problems.append("图没有图注")
    data_source = str(spec.get("data_source", "") or "").strip()
    if not data_source:
        problems.append("图没有数据/模型来源 (不得凭印象画图)")
    if kind == "diagram" and not (spec.get("nodes") or spec.get("edges")):
        problems.append("结构示意图没有节点或关系")
    if kind in QUANTITATIVE_FIGURE_KINDS and not (
            spec.get("series") and any(s.get("values") for s in spec["series"]
                                       if isinstance(s, dict))):
        problems.append("定量图没有数值系列 (需要真实数据, 不能用示意代替)")
    if kind == "curve" and not as_list_of_str(spec.get("equations")):
        problems.append("函数图没有给出方程")

    # ---- 来源 ----
    if sources is None:
        problems.append("没有来源登记表: 无法校验图的数据/模型来源 (拒绝未校验的来源)")
    elif data_source and data_source not in sources:
        problems.append(f"图的来源 {data_source!r} 不在本任务已登记对象中 (来源先校验)")

    # ---- 单位 ----
    problems.extend(_unit_problems(spec, kind))

    # ---- 模型域 / 公式白名单 ----
    problems.extend(_formula_problems(spec, kind, context, data_source))

    # ---- 权限 ----
    problems.extend(permission_problems(grant))
    return problems


def _unit_problems(spec: dict[str, Any], kind: str) -> list[str]:
    """定量图与曲线图必须有单位, 或显式声明无量纲。"""
    if kind not in QUANTITATIVE_FIGURE_KINDS and kind != "curve":
        return []
    if as_bool(spec.get("unitless")):
        return []
    problems: list[str] = []
    if not str(spec.get("x_unit", "") or "").strip():
        problems.append("图缺少 x 轴单位 (无量纲图请显式声明 unitless: true)")
    if not str(spec.get("y_unit", "") or "").strip():
        problems.append("图缺少 y 轴单位 (无量纲图请显式声明 unitless: true)")
    return problems


def _formula_problems(spec: dict[str, Any], kind: str,
                      context: ContextPack | None, data_source: str) -> list[str]:
    """`curve` 图的方程必须能过白名单解析, 且符号在模型域内。

    先按"任意标识符都算变量"解析 (于是"夹带 Python"与"用错符号"给出两种诊断),
    再判自由变量是否越出模型域 —— 这就是"模型域先校验"的落点。
    """
    if kind != "curve":
        return []
    equations = as_list_of_str(spec.get("equations"))
    if not equations:
        return []                       # 缺方程的提示由通用检查给出
    domain = _model_domain(context, data_source)
    problems: list[str] = []
    for equation in equations[:3]:
        try:
            formula = parse_formula(equation, variables=None)
        except FormulaRejected as e:
            problems.append(f"公式 {clip(equation, 40)!r} 未被支持: {e}")
            continue
        outside = sorted(set(formula.variables) - domain)
        if outside:
            problems.append(f"公式 {clip(equation, 40)!r} 使用了模型域外的符号 "
                            f"{', '.join(outside)} (模型域: {', '.join(sorted(domain))})")
    return problems


def permission_problems(grant: CapabilityGrant | None) -> list[str]:
    """绘图权限: 令牌必须授权 `tools:figure` 读范围与 `propose_figure` 能力。"""
    if grant is None:
        return ["未提供绘图权限令牌 (需要读范围 'tools:figure' 与能力 'propose_figure')"]
    problems: list[str] = []
    if not grant.allows_read("tools:figure"):
        problems.append("令牌未授权读范围 'tools:figure' (不得渲染图)")
    if not grant.allows_tool("propose_figure"):
        problems.append("令牌未授权可写能力 'propose_figure' (不得提交图)")
    return problems


# ----------------------------------------------------------------------
# 确定性绘图入口 (唯一)
# ----------------------------------------------------------------------
def render_figure_spec(spec: dict[str, Any], *,
                       output_dir: str | Path | None = None,
                       context: ContextPack | None = None,
                       sources: dict[str, int] | None = None,
                       grant: CapabilityGrant | None = None,
                       scope: FigureScope | None = None) -> dict[str, Any]:
    """按声明式 FigureSpec 渲染一张图 (唯一产物服务)。

    顺序是**校验 → 落目录 → 渲染 → 质量检查**, 任一环节不通过都返回结构化失败
    (`ok=False` + `status` + `problems`), 而不是写一个占位文件冒充产物:

    - 校验不通过 (`status="rejected"`): **不建目录、不渲染**;
    - 身份/写入不通过 (`status="no_write_permission"`): 不渲染;
    - 渲染异常 (`status="render_failed"`) / 质量不合格 (`status="quality_failed"`):
      如实报出原因。

    只支持声明式输入: 不接受自由绘图代码 (G18: 进程隔离不等于权限隔离)。
    产物写进 `figures/<run>/<task>/v<版本>/` (由 `figure_scope_bound` 绑定);
    显式给出 `output_dir` 时由调用方负责归属。
    """
    spec = dict(spec or {})
    index = dict(sources) if sources is not None else (
        figure_source_index(context) if context is not None else None)
    problems = validate_figure_spec(spec, context=context, sources=index, grant=grant)
    if problems:
        return {"ok": False, "status": "rejected", "problems": problems,
                "message": "校验未通过, 未渲染: " + "; ".join(problems[:3]),
                "path": "", "uri": "", "input_versions": {},
                "scope": (scope or current_figure_scope()).to_dict()}

    kind = str(spec.get("kind", "") or "")
    target_dir, reason = figure_artifact_dir(scope=scope, output_dir=output_dir)
    if target_dir is None:
        return {"ok": False, "status": "no_write_permission", "problems": [reason],
                "message": f"未渲染: {reason}", "path": "", "uri": "",
                "input_versions": {},
                "scope": (scope or current_figure_scope()).to_dict()}
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "status": "no_write_permission",
                "problems": [f"无法写入产物目录 {target_dir}: {e}"],
                "message": f"未渲染: 无法写入产物目录 {target_dir}: {e}",
                "path": "", "uri": "", "input_versions": {},
                "scope": (scope or current_figure_scope()).to_dict()}

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "render_failed",
                "problems": [f"绘图后端不可用: {e}"],
                "message": f"渲染失败: 绘图后端不可用: {e}", "path": "", "uri": "",
                "input_versions": {},
                "scope": (scope or current_figure_scope()).to_dict()}

    path = target_dir / _artifact_name(spec)
    variables = _model_domain(context, str(spec.get("data_source", "") or ""))
    try:
        figure, axis = plt.subplots(figsize=(6.0, 4.0))
        _draw(axis, kind, spec, variables)
        axis.set_xlabel(str(spec.get("x", "") or "")
                        + (f" ({spec['x_unit']})" if spec.get("x_unit") else ""))
        axis.set_ylabel(str(spec.get("y", "") or "")
                        + (f" ({spec['y_unit']})" if spec.get("y_unit") else ""))
        if spec.get("caption"):
            figure.suptitle(clip(str(spec["caption"]), 120), fontsize=9)
        figure.tight_layout()
        figure.savefig(path, dpi=140)
        plt.close(figure)
    except FormulaRejected as e:
        # 公式解析在渲染前的校验里已经查过; 走到这里说明是本函数被绕过校验直接调用,
        # 仍如实报错而不是画一张错图。
        return {"ok": False, "status": "render_failed",
                "problems": [f"公式未被支持: {e}"],
                "message": f"渲染失败: 公式未被支持: {e}", "path": "", "uri": "",
                "input_versions": {},
                "scope": (scope or current_figure_scope()).to_dict()}
    except Exception as e:  # noqa: BLE001 - 渲染失败如实上报
        return {"ok": False, "status": "render_failed", "problems": [f"渲染失败: {e}"],
                "message": f"渲染失败: {e}", "path": "", "uri": "",
                "input_versions": {},
                "scope": (scope or current_figure_scope()).to_dict()}

    from src.rag.figure_llm import check_png_quality

    ok_quality, quality_reason = check_png_quality(str(path))
    if not ok_quality:
        return {"ok": False, "status": "quality_failed",
                "problems": [f"渲染产物未通过质量检查: {quality_reason}"],
                "message": f"渲染产物未通过质量检查: {quality_reason}",
                "path": str(path), "uri": _artifact_uri(path), "kind": kind,
                "input_versions": figure_input_versions(spec, index or {}),
                "scope": (scope or current_figure_scope()).to_dict()}
    return {"ok": True, "status": "rendered", "message": f"已渲染 {path.name}",
            "path": str(path), "uri": _artifact_uri(path), "name": path.name,
            "kind": kind, "sha256": _sha256(path), "bytes": path.stat().st_size,
            "input_versions": figure_input_versions(spec, index or {}),
            "scope": (scope or current_figure_scope()).to_dict()}


def _artifact_name(spec: dict[str, Any]) -> str:
    """产物名: `kind-purpose-<内容摘要>.png`。

    目录已经按 (run, task, 产物版本) 分层; 文件名再带上**内容摘要**, 于是同一版本
    目录里的不同图 (例如同一次运行的两张 bar 图) 也不会互相覆盖, 幂等重画同一张图
    则落到同一个文件。
    """
    kind = _safe_name(spec.get("kind") or "figure")
    purpose = _safe_name(spec.get("purpose") or kind)
    return f"{kind}-{purpose}-{_spec_digest(spec)}.png"


def _spec_digest(spec: dict[str, Any]) -> str:
    keys = ("kind", "purpose", "data_source", "x", "y", "x_unit", "y_unit",
            "series", "equations", "nodes", "edges", "caption", "supports")
    blob = json.dumps({key: spec.get(key) for key in keys}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:10]


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _artifact_uri(path: Path) -> str:
    """产物根下的相对 uri (绝对路径不进共享状态, §13.3)。"""
    from src import config

    try:
        root = Path(getattr(config, "OUTPUT_DIR", "")).resolve()
        return path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return path.name


def _draw(axis, kind: str, spec: dict[str, Any], variables: set[str]) -> None:
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
    if kind == "bar":
        labels = [str(s.get("label", "")) for s in series]
        values = [float(v) for s in series for v in (s.get("values") or [])[:1]]
        if labels and values:
            axis.bar(labels, values[:len(labels)])
    elif kind == "line":
        for item in series:
            values = [float(v) for v in (item.get("values") or [])]
            axis.plot(range(1, len(values) + 1), values, marker="o",
                      label=str(item.get("label", "")))
        if series:
            axis.legend(fontsize=8)
    elif kind == "scatter":
        for item in series:
            values = [float(v) for v in (item.get("values") or [])]
            pairs = list(zip(values[0::2], values[1::2]))
            if pairs:
                axis.scatter([p[0] for p in pairs], [p[1] for p in pairs],
                             label=str(item.get("label", "")))
        if series:
            axis.legend(fontsize=8)
    elif kind == "distribution":
        for item in series:
            values = [float(v) for v in (item.get("values") or [])]
            if values:
                axis.hist(values, bins=min(20, max(3, len(values) // 2)),
                          label=str(item.get("label", "")), alpha=0.6)
        if series:
            axis.legend(fontsize=8)
    elif kind == "heatmap":
        import numpy as np

        for item in series:
            values = [float(v) for v in (item.get("values") or [])]
            side = int(len(values) ** 0.5)
            if side >= 2:
                matrix = np.array(values[: side * side]).reshape(side, side)
                axis.imshow(matrix, cmap="viridis", aspect="auto")
                break
    elif kind == "curve":
        for equation in as_list_of_str(spec.get("equations"))[:3]:
            _plot_formula(axis, str(equation), tuple(sorted(variables)))
        if axis.get_legend_handles_labels()[0]:
            axis.legend(fontsize=8)
    elif kind == "diagram":
        nodes = [n for n in (spec.get("nodes") or []) if isinstance(n, dict)]
        for index, node in enumerate(nodes):
            axis.annotate(str(node.get("label") or node.get("id") or ""),
                          xy=(index % 4, -(index // 4)), xytext=(index % 4, -(index // 4)),
                          ha="center", fontsize=8,
                          bbox={"boxstyle": "round", "fc": "#eef"})
        axis.set_axis_off()
        axis.set_xlim(-0.5, 3.5)
        axis.set_ylim(-(max(1, len(nodes) // 4)) - 0.5, 0.5)


def _plot_formula(axis, equation: str, variables: tuple[str, ...]) -> None:
    """按**白名单解析 + 受限执行**画一条函数曲线。

    修复前这里是 `sympy.parse_expr` (内部 eval) + `lambdify`, 并且单条方程失败时
    静默跳过 —— 于是"公式里夹带 Python"既没有边界、也看不出来。现在解析失败会抛
    `FormulaRejected`, 由调用方转成可见失败。
    """
    formula = parse_formula(equation, variables=variables)
    primary = formula.variables[0] if formula.variables else (
        variables[0] if variables else "x")
    xs, ys = formula.series(primary)
    axis.plot(xs, ys, label=clip(equation, 40))


def _safe_name(text: Any) -> str:
    from src.utils.file_utils import sanitize_filename

    return sanitize_filename(clip(str(text or "figure"), 40)) or "figure"


def figure_csv(values: list[float], name: str = "figure") -> str:
    """把数值导出为 CSV 文本 (供产物记录, 便于复核图的数据来源)。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([name])
    for value in values:
        writer.writerow([value])
    return buffer.getvalue()
