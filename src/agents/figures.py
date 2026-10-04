from __future__ import annotations

"""FigureAgent 配图 (合并计划 §3.1 / §7.3)。

职责: 按论证结构、来源数据、公式/模型与版面要求**选图**、绘图、修正。
提交 `FigureSpec` 与产物: 数据与脚本引用、图注、代码/视觉核查结果。

合并要点 (§7.3、§11):
- `rag/figure_generator` + `figure_llm` + `pipeline.regenerate_figures_node` 合并为
  本角色调用的绘图工具;
- **不再自动给所有领域画识别流程图**: 图由 `FigureSpec` 声明用途与数据来源,
  `render_figure_spec()` 是唯一的确定性绘图入口;
- 绘图执行需单列验收: `figure_llm._execute_code()` 用子进程执行生成代码,
  **进程隔离不等于权限隔离**。因此本角色首版优先走**声明式 FigureSpec**,
  自由绘图代码不在本轮开放。

发现数据缺口时提 `ResearchNeed(figure_data)`, 不自行编造数值。
"""

import csv
import io
from pathlib import Path
from typing import Any

from src.agents.base import (
    AgentBase,
    as_list_of_str,
    build_proposal,
    clip,
    context_summary,
    extract_json,
)
from src.agents.protocol import (
    AgentResult,
    AgentTask,
    ChangeProposal,
    ContextPack,
    NeedKind,
    ResearchNeed,
    UsageRecord,
)
from src.agents.runtime import AgentRuntime, ToolSpec
from src.agents.tools import figure_tools

__all__ = ["SUPPORTED_FIGURE_KINDS", "FigureAgent", "render_figure_spec"]

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
3. 数据来源 (来自哪条已登记数据/模型/公式; 没有来源就不要画);
4. 轴与单位、图注 (图注必须与正文的说法一致);
5. 它支持哪条结论。

纪律:
- 不得编造数值: 数据必须来自给定来源; 缺数据就提出需求, 不要画示意图冒充数据图;
- 不要为每个题目固定画几张图; 也不要把某个领域的流程套到别的领域;
- 结构化关系 (变量之间的机制) 用 diagram; 有真实数值才用定量图。

最终输出 JSON:
{"figures": [{"purpose": "", "kind": "", "data_source": "", "x": "", "y": "", "x_unit": "", "y_unit": "",
              "series": [{"label": "", "values": []}], "equations": [""], "nodes": [], "edges": [],
              "caption": "", "supports": ""}],
 "needs": [{"kind": "figure_data", "statement": "", "why": ""}]}
"""

    def tools(self, task: AgentTask, context: ContextPack,
              runtime: AgentRuntime) -> list[ToolSpec]:
        return figure_tools()

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

        changes: list[ChangeProposal] = []
        rendered: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for spec in specs:
            problems = validate_figure_spec(spec)
            spec = {**spec, "validation_problems": problems}
            unresolved.extend(problems)
            outcome = render_figure_spec(spec)
            spec["render"] = outcome
            if not outcome.get("ok"):
                unresolved.append(f"图 {spec.get('purpose', '')} 渲染失败: "
                                  f"{outcome.get('message', '')}")
            rendered.append(spec)
            changes.append(build_proposal(
                "figure",
                payload=spec,
                rationale=clip(str(spec.get("purpose", "") or ""), 200)
                          or "按论证需要选择图种",
                input_versions={},
            ))
        summary = f"选择 {len(specs)} 张图 (" + ", ".join(
            str(s.get("kind", "?")) for s in specs) + ")"
        if unresolved:
            summary += f"; {len(unresolved)} 项待处理"
        payload_out = {"schema": "FigureSpec/v1", "figures": rendered}
        outcome_fn = self.completed if all(
            s.get("render", {}).get("ok") for s in rendered) else self.partial
        return outcome_fn(task, summary, changes=changes, unresolved=unresolved[:6],
                          usage=usage, payload=payload_out)

    # ---- 确定性选图 ----
    def select_figures(self, task: AgentTask,
                       context: ContextPack) -> list[dict[str, Any]]:
        """按**论证需要**选图, 而不是按领域模板。

        判据:
        - 有真实数据集卡 → 定量图 (有分组维度用 bar, 有时间/序列维度用 line);
        - 只有模型/公式 → `curve` (若给出了方程) 或 `diagram` (关系示意);
        - 什么都没有 → 不画 (调用方据此如实说明)。
        """
        figures: list[dict[str, Any]] = []
        datasets = context.objects.get("dataset") or []
        for dataset in datasets[:3]:
            columns = as_list_of_str(dataset.get("columns") or dataset.get("fields"))
            if not columns:
                continue
            kind = "line" if any(_is_temporal(c) for c in columns) else "bar"
            figures.append({
                "purpose": f"展示数据集 {clip(str(dataset.get('name', '')), 60)} 的分布/对比",
                "kind": kind,
                "data_source": str(dataset.get("id", "") or ""),
                "x": columns[0],
                "y": columns[1] if len(columns) > 1 else columns[0],
                "x_unit": "", "y_unit": "",
                "caption": f"数据来源: {dataset.get('name', '')} (未标注单位)",
                "supports": task.subquestion,
                "values_available": False,
            })
        for model in (context.objects.get("model") or [])[:3]:
            equations = as_list_of_str(model.get("equations"))
            if equations:
                figures.append({
                    "purpose": f"展示模型 {clip(str(model.get('name', '')), 60)} 的函数形式",
                    "kind": "curve",
                    "data_source": str(model.get("id", "") or ""),
                    "equations": equations[:3],
                    "caption": clip(str(model.get("mechanism", "")), 200),
                    "supports": task.subquestion,
                })
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


def validate_figure_spec(spec: dict[str, Any]) -> list[str]:
    """确定性检查: 图是否有来源、图注是否与用途一致、图种是否登记。"""
    problems: list[str] = []
    kind = str(spec.get("kind", "") or "")
    if kind not in SUPPORTED_FIGURE_KINDS:
        problems.append(f"未登记的图种: {kind!r}")
    if not str(spec.get("purpose", "") or "").strip():
        problems.append("图没有说明用途")
    if not str(spec.get("caption", "") or "").strip():
        problems.append("图没有图注")
    if not str(spec.get("data_source", "") or "").strip():
        problems.append("图没有数据/模型来源 (不得凭印象画图)")
    if kind == "diagram" and not (spec.get("nodes") or spec.get("edges")):
        problems.append("结构示意图没有节点或关系")
    if (kind in ("bar", "line", "scatter", "distribution", "heatmap")
            and not (spec.get("series") and any(s.get("values")
                                                for s in spec["series"]
                                                if isinstance(s, dict)))):
        problems.append("定量图没有数值系列 (需要真实数据, 不能用示意代替)")
    if kind == "curve" and not as_list_of_str(spec.get("equations")):
        problems.append("函数图没有给出方程")
    return problems


# ----------------------------------------------------------------------
# 确定性绘图入口 (唯一)
# ----------------------------------------------------------------------
def render_figure_spec(spec: dict[str, Any], *,
                       output_dir: str | Path | None = None) -> dict[str, Any]:
    """按声明式 FigureSpec 渲染一张图。

    只支持声明式输入: 不接受自由绘图代码 (合并计划 §11: 自由绘图代码需要受限执行
    环境, 本轮不开放)。产物写进**任务产物目录**, 不跨越当前任务的数据与输出范围。
    """
    kind = str(spec.get("kind", "") or "")
    if kind not in SUPPORTED_FIGURE_KINDS:
        return {"ok": False, "message": f"不支持的图种 {kind!r}"}
    if kind in ("bar", "line", "scatter", "distribution", "heatmap") \
            and not (spec.get("series") or spec.get("values")):
        return {"ok": False, "message": "定量图缺少数值系列, 拒绝渲染 (不编造数值)"}
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"绘图后端不可用: {e}"}

    target_dir = Path(output_dir) if output_dir else _default_figure_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "message": f"无法创建产物目录: {e}"}
    name = _safe_name(spec.get("purpose") or kind)
    path = target_dir / f"{kind}-{name}.png"

    try:
        figure, axis = plt.subplots(figsize=(6.0, 4.0))
        _draw(axis, kind, spec)
        axis.set_xlabel(str(spec.get("x", "") or "")
                        + (f" ({spec.get('x_unit')})" if spec.get("x_unit") else ""))
        axis.set_ylabel(str(spec.get("y", "") or "")
                        + (f" ({spec.get('y_unit')})" if spec.get("y_unit") else ""))
        if spec.get("caption"):
            figure.suptitle(clip(str(spec["caption"]), 120), fontsize=9)
        figure.tight_layout()
        figure.savefig(path, dpi=140)
        plt.close(figure)
    except Exception as e:  # noqa: BLE001 - 渲染失败如实上报
        return {"ok": False, "message": f"渲染失败: {e}"}
    return {"ok": True, "message": f"已渲染 {path.name}", "path": str(path.name),
            "kind": kind}


def _draw(axis, kind: str, spec: dict[str, Any]) -> None:
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
        import numpy as np
        from sympy import lambdify, symbols
        from sympy.parsing.sympy_parser import parse_expr

        for equation in as_list_of_str(spec.get("equations"))[:3]:
            _plot_equation(axis, str(equation), np, symbols, parse_expr, lambdify)
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


def _plot_equation(axis, equation: str, np, symbols, parse_expr, lambdify) -> None:
    """画一条函数曲线; 单条方程解析/求值失败就跳过 (不影响其余曲线)。"""
    try:
        x, y = symbols("x"), symbols("y")
        expr = parse_expr(equation, local_dict={"x": x, "y": y})
        free = sorted(expr.free_symbols, key=lambda s: s.name)
        if not free:
            return
        fn = lambdify(free[0], expr, "numpy")
        grid = np.linspace(-10, 10, 400)
        with np.errstate(all="ignore"):
            axis.plot(grid, fn(grid), label=equation[:40])
    except Exception:  # noqa: BLE001 - 单条方程画不出就跳过, 不影响其余
        return


def _default_figure_dir() -> Path:
    from src.config import OUTPUT_DIR

    return Path(OUTPUT_DIR) / "figures"


def _safe_name(text: str) -> str:
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
