from __future__ import annotations

"""学术图表生成工具

综述论文需要的典型图表:
1. 分类体系图 (Taxonomy Tree) — 展示方法分类结构 (借鉴 PaperOrchestra plotting-agent)
2. 研究时间线图 (Timeline) — 展示领域发展脉络
3. 方法对比雷达图/柱状图 (Comparison) — 展示不同方法对比

使用 matplotlib 生成，中文字体自动探测 (微软雅黑/宋体/Noto)。
输出为 PNG (300dpi)，嵌入 Markdown 报告。
"""

import logging
import re
import textwrap
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from src.rag.figure_style import ACCENT, PALETTE, apply_style

logger = logging.getLogger(__name__)

# 中文字体探测
_CN_FONT = None
for name in ["Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC", "WenQuanYi Zen Hei"]:
    try:
        if any(name.lower() in f.name.lower() for f in font_manager.fontManager.ttflist):
            _CN_FONT = name
            break
    except Exception:
        pass
if _CN_FONT:
    plt.rcParams["font.family"] = _CN_FONT
    plt.rcParams["axes.unicode_minus"] = False


#: 当前产物归属。修复前只有 run 身份一层 (`figures/<run>/`), 而文件名只是局部序号
#: (`taxonomy_0.png`): 同一 run 里两张**不同**的图仍可能同名互相覆盖 (§3.3 G18)。
#: 现在归属是 `(run_id, task_id, artifact_version)` 三元组, 目录逐层下分。
#: 用 ContextVar 而不是参数: 绘图函数有 8 个入口, 每个都加一个 scope 参数会把
#: 归属信息散到调用链上; 而"这次是谁在画第几张图"本来就是环境事实。
_FIGURE_SCOPE: ContextVar[FigureScope | None] = ContextVar("air_figure_scope",
                                                           default=None)


@dataclass(frozen=True)
class FigureScope:
    """图表产物的归属: 哪次运行 / 哪个任务 / 第几个产物版本。"""

    run_id: str = ""
    task_id: str = ""
    artifact_version: str = ""

    def is_empty(self) -> bool:
        return not (self.run_id or self.task_id or self.artifact_version)

    def to_dict(self) -> dict[str, str]:
        return {"run_id": self.run_id, "task_id": self.task_id,
                "artifact_version": self.artifact_version}

    def parts(self) -> list[str]:
        """目录分量: run → task → v<版本> (缺失的层不下分)。"""
        run = _sanitize_scope(self.run_id)
        task = _sanitize_scope(self.task_id)
        version = _sanitize_scope(self.artifact_version)
        if not (run or task or version):
            return []
        parts = [run or "_unscoped"]
        if task:
            parts.append(task)
        if version:
            parts.append(f"v{version}")
        return parts


def current_figure_scope() -> FigureScope:
    """当前绑定的产物归属 (未绑定时为空归属)。"""
    return _FIGURE_SCOPE.get() or FigureScope()


def figure_scope() -> str:
    """当前图表产物归属的 run 身份 (空表示未绑定; 保留旧接口)。"""
    return current_figure_scope().run_id


@contextmanager
def figure_scope_bound(scope: str, *, task_id: str = "", artifact_version: str | int = ""):
    """在 `with` 块内把图表产物归到 `(run, task, 产物版本)` 名下 (合并计划 §7.4 / §3.3 G18)。

    为什么必须做: 图表文件名是**局部序号** (`taxonomy_0.png`、`framework_0.png`),
    此前所有会话共用 `outputs/figures/`, 于是两个会话同时画图会互相覆盖对方的产物
    ("两任务不串图表"是 M4 的退出条件之一)。只按 run 分一层还不够 —— 同一次运行里的
    两张不同图仍会同名; 因此归属扩到任务与产物版本: `figures/<run>/<task>/v<version>/`。
    未绑定任何身份时保持原来的扁平目录 (向后兼容旧调用点与测试)。
    """
    bound = FigureScope(run_id=str(scope or "").strip(),
                        task_id=str(task_id or "").strip(),
                        artifact_version=str(artifact_version or "").strip())
    token = _FIGURE_SCOPE.set(bound)
    try:
        yield
    finally:
        _FIGURE_SCOPE.reset(token)


def _figure_base() -> Path:
    """`OUTPUT_DIR/figures` (**调用时**读配置, 测试隔离与部署都据此生效)。"""
    from src import config

    root = Path(getattr(config, "OUTPUT_DIR",
                        Path(__file__).resolve().parent.parent.parent / "outputs"))
    return root / "figures"


def _scoped_dir(base: Path, scope: FigureScope) -> Path:
    parts = scope.parts()
    return base / Path(*parts) if parts else base


def figure_artifact_dir(*, scope: FigureScope | None = None,
                        output_dir: str | Path | None = None) -> tuple[Path | None, str]:
    """**产物服务**解析图的目标目录; 返回 `(目录, 拒绝原因)`。

    与 `_ensure_figure_dir()` 的区别: 这是新图纸路径的入口, 因此**必须有身份** ——
    缺任务或产物版本时返回 `(None, 原因)` 让调用方如实报错, 而不是退回共用目录
    (退回共用目录正是"多 run 互相覆盖"的成因)。显式给出 `output_dir` 时由调用方
    自己负责归属 (测试与手工产物)。
    """
    if output_dir is not None:
        return Path(output_dir), ""
    bound = scope or current_figure_scope()
    if not bound.task_id:
        return None, ("缺少任务身份: 图必须经 figure_scope_bound(run_id, task_id=...,"
                      " artifact_version=...) 绑定归属, 不得写入共用图表目录")
    if not bound.artifact_version:
        return None, "缺少产物版本 (artifact_version): 无法保证同一次运行的图不互相覆盖"
    return _scoped_dir(_figure_base(), bound), ""


def _ensure_figure_dir() -> Path:
    """图表输出目录 (**调用时**读取配置 + 当前产物归属)。

    早先这里写死 `Path(__file__).../outputs/figures`: 模块只按项目根拼路径,
    于是 `config.OUTPUT_DIR` 改了也不生效 —— 测试隔离失效(测试图直接写进仓库
    outputs/figures/), 部署到别的产物根时也会写错地方。与 server.py 的
    `output_dir()` 保持一致, 统一按调用时的配置取值。

    M4/G18: 目录按归属分 `figures/<run>/<task>/v<版本>/`, 使并发运行与同一次运行的
    多张图都不互相覆盖; 未绑定身份时保持原来的扁平目录 (向后兼容旧调用点与测试)。
    """
    d = _scoped_dir(_figure_base(), current_figure_scope())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sanitize_scope(scope: str) -> str:
    """身份片段用作目录名: 只留安全字符, 空/异常一律回退为不分子目录。"""
    text = "".join(ch for ch in str(scope or "") if ch.isalnum() or ch in "-_.")
    return text.strip("._-")[:64]


def generate_taxonomy_tree(
    title: str,
    taxonomy: dict[str, list[str]],
    filename: str = "taxonomy.png",
) -> str:
    """生成分类体系树状图（纵向分层布局）

    taxonomy: {"大类A": ["子类A1", "子类A2", ...], "大类B": [...]}
    布局: 根标题在顶部 → 大类纵向排列 → 每个大类的子类纵向堆叠在右侧,
    用颜色区分大类、连线连接大类与其子类。子类纵向堆叠彻底避免横向重叠,
    图高随子类总数自动增长。
    Returns: 图片文件路径
    """
    apply_style(style="journal")
    n_subs = sum(len(subs) for subs in taxonomy.values())
    # 每个子类占约 0.5 英寸高度, 保证文字不重叠
    fig_h = max(4.0, 1.6 + n_subs * 0.55)
    fig, ax = plt.subplots(figsize=(10, fig_h))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # 根标题 (顶部)
    ax.text(0.5, 0.985, title, ha="center", va="top", fontsize=13, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#4A6FA5",
                      edgecolor="none", alpha=0.15))

    total_rows = max(n_subs, 1)
    row_step = 0.88 / total_rows   # 归一化垂直步长
    y_cursor = 0.90

    for ci, (cat, subs) in enumerate(taxonomy.items()):
        color = PALETTE[ci % len(PALETTE)]
        n = len(subs)
        # 大类标签: 左侧, 垂直居中于该类子类块
        y_mid = y_cursor - (n - 1) * row_step / 2
        ax.text(0.03, y_mid, cat, ha="left", va="center", fontsize=11, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                          edgecolor=color, lw=1.6))
        for j, sub in enumerate(subs):
            y = y_cursor - j * row_step
            ax.text(0.20, y, sub, ha="left", va="center", fontsize=9,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                              edgecolor="#999999", lw=0.8))
            # 大类 → 子类连线
            ax.plot([0.115, 0.195], [y_mid, y], color=color, lw=0.8, alpha=0.6, zorder=0)
        y_cursor -= n * row_step

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Taxonomy figure saved: {out}")
    return str(out)


def generate_timeline(
    title: str,
    milestones: list[dict],
    filename: str = "timeline.png",
) -> str:
    """生成研究时间线图（事件等距 + 上下交错 + 文字换行）

    milestones: [{"year": 2017, "event": "...", "note": "..."}, ...]
    布局: 水平轴线, 事件按序号**等距**排布 (而非按年份数值, 避免稀疏/密集不均),
    事件上下交错, 事件文字自动换行, 年份标注在轴线下方。
    Returns: 图片文件路径
    """
    apply_style(style="journal")
    n = len(milestones)
    fig_w = max(9.0, n * 1.7)
    fig, ax = plt.subplots(figsize=(fig_w, 5.2))
    ax.axis("off")
    ax.set_xlim(-0.8, n - 0.2)
    ax.set_ylim(-1.5, 1.5)

    # 水平轴线
    ax.plot([0, n - 1], [0, 0], color="#4A6FA5", lw=2.5, zorder=1)

    for i, m in enumerate(milestones):
        year = m.get("year", 0)
        event = (m.get("event") or "").strip()
        direction = 1 if i % 2 == 0 else -1
        ax.scatter([i], [0], s=80, color=ACCENT, zorder=3)
        ax.vlines(i, 0, direction * 0.5, color="#4A6FA5", lw=1.2, zorder=2)
        # 年份标注 (轴线下方, 始终可读)
        ax.text(i, -0.14, str(year), ha="center", va="top", fontsize=9,
                fontweight="bold", color=ACCENT)
        # 事件文字: 上下交错 + 换行 (每行约 16 个字符宽)
        wrapped = "\n".join(textwrap.wrap(event, width=16)) if event else ""
        if direction > 0:
            ax.text(i, 0.56, wrapped, ha="center", va="bottom", fontsize=9)
        else:
            ax.text(i, -0.58, wrapped, ha="center", va="top", fontsize=9)

    ax.set_title(title, fontsize=13, fontweight="bold", pad=15)

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Timeline figure saved: {out}")
    return str(out)


def generate_comparison_chart(
    title: str,
    methods: list[str],
    scores: list[float],
    categories: list[str],
    filename: str = "comparison.png",
) -> str:
    """生成方法对比雷达图

    methods: ["方法A", "方法B", ...]
    scores:  [[4,3,5,...], [3,4,4,...]] 每方法在 categories 上的得分 (1-5)
    Returns: 图片文件路径
    """
    import numpy as np

    apply_style(style="journal")
    n_cats = len(categories)
    angles = np.linspace(0, 2 * np.pi, n_cats, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

    for method, method_scores in zip(methods, scores):
        values = method_scores + method_scores[:1]
        ax.plot(angles, values, linewidth=2, label=method)
        ax.fill(angles, values, alpha=0.1)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=10)
    ax.set_ylim(0, 5)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.0), fontsize=9)

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Comparison figure saved: {out}")
    return str(out)


def generate_trend_chart(
    title: str,
    year_counts: dict[int, int],
    venue_counts: dict[str, int] = None,
    filename: str = "trend.png",
) -> str:
    """生成出版趋势图（柱状+折线叠加）

    year_counts: {2017: 5, 2018: 8, ...}  年份→该年论文数
    venue_counts: {"IEEE TIFS": 3, ...} 可选出处分布
    Returns: 图片文件路径, 或空串
    """
    if not year_counts:
        return ""
    apply_style(style="journal")

    has_venue = bool(venue_counts)
    if has_venue:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    else:
        fig, ax1 = plt.subplots(figsize=(10, 5))
        ax2 = None

    # 左: 年度趋势折线图
    years = sorted(year_counts.keys())
    counts = [year_counts[y] for y in years]
    ax1.bar(years, counts, color=PALETTE[2], alpha=0.8, edgecolor="white")
    ax1.plot(years, counts, color=ACCENT, lw=2, marker="o", markersize=6)
    ax1.set_xlabel("Year", fontsize=10)
    ax1.set_ylabel("Papers", fontsize=10)
    ax1.set_title("Publication Trend", fontsize=11, fontweight="bold")
    ax1.grid(axis="y", alpha=0.3)

    # 右: 出处分布横向条形图
    if has_venue and ax2:
        top_venues = sorted(venue_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        v_names = [v[0] for v in top_venues]
        v_counts = [v[1] for v in top_venues]
        colors = [PALETTE[i % len(PALETTE)] for i in range(len(v_names))]
        ax2.barh(v_names[::-1], v_counts[::-1], color=colors[::-1], alpha=0.8, edgecolor="white")
        ax2.set_xlabel("Papers", fontsize=10)
        ax2.set_title("Venue Distribution", fontsize=11, fontweight="bold")
        ax2.grid(axis="x", alpha=0.3)

    fig.suptitle(title, fontsize=13, fontweight="bold")
    fig.tight_layout()

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Trend figure saved: {out}")
    return str(out)


def generate_method_comparison_barchart(
    title: str,
    methods: list[str],
    metrics: dict[str, list[float]],
    filename: str = "method_comp.png",
) -> str:
    """生成方法对比分组柱状图（比雷达图更直观, 借鉴 academic-plotting 决策表）

    methods:   ["方法A", "方法B", "方法C"]
    metrics:   {"精度": [0.85, 0.92, 0.78], "鲁棒性": [0.7, 0.8, 0.9], ...}
    Returns: 图片文件路径
    """
    import numpy as np

    if not methods or not metrics:
        return ""
    apply_style(style="journal")

    m_names = list(metrics.keys())
    n_methods = len(methods)
    n_metrics = len(m_names)
    x = np.arange(n_methods)
    width = 0.8 / max(n_metrics, 1)

    fig, ax = plt.subplots(figsize=(max(8, n_methods * 1.5 + 2), 5.5))
    all_vals = [v for vals in metrics.values() for v in vals if isinstance(v, (int, float))]
    for i, (m_name, vals) in enumerate(metrics.items()):
        offset = (i - n_metrics / 2 + 0.5) * width
        bars = [vals[j] if j < len(vals) else 0 for j in range(n_methods)]
        ax.bar(x + offset, bars, width * 0.9, label=m_name,
               color=PALETTE[i % len(PALETTE)], alpha=0.85, edgecolor="white")
        # 柱顶数值标注 (提升可读性, 借鉴学术绘图惯例)
        for j, v in enumerate(bars):
            if isinstance(v, (int, float)) and v > 0:
                ax.text(x[j] + offset, v + 0.015, f"{v:.2g}",
                        ha="center", va="bottom", fontsize=7, color="#444444")

    ax.set_xticks(x)
    ax.set_xticklabels(methods, fontsize=9, rotation=15, ha="right")
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    if all_vals:
        ax.set_ylim(0, max(all_vals) * 1.18)
    fig.tight_layout()

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Comparison bar chart saved: {out}")
    return str(out)


def parse_taxonomy_from_text(text: str) -> dict[str, list[str]]:
    """从 LLM 输出的分类体系文本解析为结构化 dict

    支持格式:
    - "### 大类A\n- 子类A1\n- 子类A2"
    - "**大类A**\n  1. 子类A1\n  2. 子类A2"
    """
    taxonomy: dict[str, list[str]] = {}
    current_cat = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("###") or line.startswith("##"):
            current_cat = re.sub(r"^#+\s*", "", line).strip()
            taxonomy[current_cat] = []
        elif line.startswith("**") and line.endswith("**") and len(line) > 4:
            current_cat = line.strip("*").strip()
            taxonomy[current_cat] = []
        elif (line.startswith("-") or line.startswith("*") or
              re.match(r"^\d+[.)]", line)):
            item = re.sub(r"^[-*\d.)\s]+", "", line).strip()
            if current_cat and item:
                taxonomy[current_cat].append(item)
    # 过滤空分类
    return {k: v for k, v in taxonomy.items() if k and v}


def parse_timeline_from_text(text: str) -> list[dict]:
    """从 LLM 输出的时间线文本解析为结构化 list

    支持格式:
    - "2017: Transformer 提出"
    - "- 2017 | Transformer 提出 | 说明"
    - "2017 年: 事件"
    """
    milestones = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 直接查找年份模式（避免前缀清除吃掉年份数字）
        m = re.search(r"(20\d{2})\s*[年:：|\-]?\s*(.+)", line)
        if m:
            milestones.append({"year": int(m.group(1)), "event": m.group(2).strip()})
    return milestones


def generate_figures_from_notes(
    topic: str, lit_notes: str, verified_refs: list[dict] | None = None
) -> list[str]:
    """从文献综述素材生成图表（**确定性模板**, 不再执行 LLM 生成的绘图脚本）

    策略 (借鉴 PaperBanana + FigMirror + AI-Scientist 12 图上限):
    1. 研究框架总览图 (framework, 分层框图模板)
    2. 识别流水线图 (pipeline, 确定性流程框图)
    3. 分类体系图 (taxonomy, 从素材解析出的结构)
    4. 研究时间线图 (timeline, 从素材解析出的里程碑)
    5. 出版趋势图 (trend, 确定性: 从 verified_refs 年份统计)
    6. 主题×年份热力图 (heatmap, 确定性: 从 verified_refs 统计)
    7. 方法对比图 (comparison, LLM 只提取**结构化数据**, 仍由本模块绘图)

    为什么不再走"LLM 写 matplotlib 脚本 → 子进程执行" (§3.3 G18): 那条路径等于
    让模型产出任意 Python 并执行, 进程隔离不等于权限隔离。绘图统一走本模块的
    声明式实现; 方法对比数据仍可由模型提取, 但必须过 `_validate_comparison_data`
    的结构/值域校验才会被画出来。

    Returns: 生成的图片路径列表
    """
    paths: list[str] = []
    refs = verified_refs or []

    # ===== 1. 研究框架总览图 (确定性模板) =====
    print("  [figure] 生成研究框架总览图 (确定性分层框图)...")
    paths.append(generate_framework_overview(f"{topic} 研究框架总览",
                                             filename=f"framework_{len(paths)}.png"))

    # ===== 2. 识别流水线图 (确定性) =====
    print("  [figure] 生成识别流水线图 (确定性流程框图)...")
    paths.append(generate_rf_pipeline(f"{topic} 识别流程", filename=f"pipeline_{len(paths)}.png"))

    # ===== 3. 分类体系图 =====
    taxonomy = parse_taxonomy_from_text(lit_notes)
    if taxonomy:
        print("  [figure] 生成分类体系图 (从素材结构)...")
        paths.append(generate_taxonomy_tree(
            f"{topic} 分类体系", {k: v[:6] for k, v in taxonomy.items()},
            filename=f"taxonomy_{len(paths)}.png"))

    # ===== 4. 时间线图 =====
    milestones = parse_timeline_from_text(lit_notes)
    if len(milestones) >= 3:
        print("  [figure] 生成研究时间线图 (从素材里程碑)...")
        paths.append(generate_timeline(f"{topic} 研究发展脉络", milestones[:12],
                                       filename=f"timeline_{len(paths)}.png"))

    # ===== 5. 出版趋势图 (确定性: 从 verified_refs 统计) =====
    print("  [figure] 生成出版趋势图 (确定性统计)...")
    trend_path = _generate_trend_from_refs(topic, refs, len(paths))
    if trend_path:
        paths.append(trend_path)

    # ===== 6. 主题×年份热力图 (确定性: 从 verified_refs 统计) =====
    print("  [figure] 生成主题×年份热力图 (确定性统计)...")
    heatmap_path = _generate_topic_year_heatmap(topic, refs, len(paths))
    if heatmap_path:
        paths.append(heatmap_path)

    # ===== 7. 方法对比图 (LLM 提取结构化数据) =====
    print("  [figure] 生成方法对比图 (LLM 提取数据)...")
    cmp_path = _generate_comparison_from_notes(topic, lit_notes, len(paths))
    if cmp_path:
        paths.append(cmp_path)

    return paths


def _generate_trend_from_refs(topic: str, refs: list[dict], idx: int) -> str:
    """从可信参考文献清单统计年份/出处分布 → 趋势图"""
    if not refs:
        return ""
    year_counts: dict[int, int] = {}
    venue_counts: dict[str, int] = {}
    for r in refs:
        y = r.get("year", "")
        try:
            yi = int(str(y)[:4])
            if 1900 < yi < 2100:
                year_counts[yi] = year_counts.get(yi, 0) + 1
        except (ValueError, TypeError):
            pass
        v = r.get("venue") or r.get("source") or ""
        if v and v not in ("arXiv", "OpenAlex", "Semantic Scholar", ""):
            venue_counts[v] = venue_counts.get(v, 0) + 1
    if not year_counts:
        return ""
    return generate_trend_chart(f"{topic} 出版趋势与出处分布", year_counts, venue_counts, filename=f"trend_{idx}.png")


# 主题类别分类规则 (基于标题关键词, 中英混合, 用于主题×年份热力图)。
# 顺序即优先级: 强领域信号 (安全/信道/数据集) 在前, 弱泛词 (learning/network) 在后,
# 避免 "adversarial machine learning" 被 learning 误分到"模型架构"。
_REF_CATEGORY_RULES = (
    ("安全与对抗", ("security", "attack", "adversarial", "authentic", "spoof",
                    "countermeasure", "defense", "安全", "对抗", "攻击", "认证", "伪造", "防御")),
    ("信道鲁棒与泛化", ("channel", "robust", "domain", "generalization", "transfer",
                        "信道", "鲁棒", "泛化", "域适应", "可移植")),
    ("数据集与实测", ("dataset", "benchmark", "experimental", "measurement",
                      "实测", "数据集", "基准", "实验")),
    ("特征工程与信号处理", ("feature", "transform", "statistic", "bispectrum", "entropy",
                            "fractal", "wavelet", "dctf", "constellation", "星座",
                            "特征", "双谱", "分形", "小波", "变换", "轨迹")),
    ("模型架构与深度学习", ("cnn", "neural", "network", "deep", "transformer", "resnet",
                            "learning", "dnn", "attention", "卷积", "神经网络", "深度学习", "注意力")),
)


def _classify_ref_category(title: str) -> str:
    t = (title or "").lower()
    for cat, kws in _REF_CATEGORY_RULES:
        if any(k in t for k in kws):
            return cat
    return "其他"


def generate_topic_year_heatmap(
    title: str,
    categories: list[str],
    years: list[int],
    matrix: list[list[int]],
    filename: str = "heatmap.png",
) -> str:
    """生成主题×年份热力图 (文献数量分布, 确定性)"""
    import numpy as np

    if not categories or not years:
        return ""
    apply_style(style="journal")
    data = np.array(matrix, dtype=float)
    fig, ax = plt.subplots(figsize=(max(6, len(years) * 0.6 + 3), max(3, len(categories) * 0.55 + 1.5)))
    im = ax.imshow(data, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([str(y) for y in years], fontsize=9, rotation=45, ha="right")
    ax.set_yticks(range(len(categories)))
    ax.set_yticklabels(categories, fontsize=9)
    ax.set_title(title, fontsize=13, fontweight="bold")
    # 格内标注数值 (深色格用白字)
    vmax = float(data.max()) if data.size else 1.0
    for i in range(len(categories)):
        for j in range(len(years)):
            v = data[i, j]
            if v > 0:
                ax.text(j, i, str(int(v)), ha="center", va="center", fontsize=8,
                        color="white" if v > vmax * 0.5 else "black")
    fig.colorbar(im, ax=ax, label="文献数量", shrink=0.8)
    fig.tight_layout()

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Topic-year heatmap saved: {out}")
    return str(out)


def _generate_topic_year_heatmap(topic: str, refs: list[dict], idx: int) -> str:
    """从 verified_refs 统计 主题类别×年份 的文献数量 → 热力图 (确定性)"""
    if not refs:
        return ""
    year_cat: list[tuple[int, str]] = []
    for r in refs:
        try:
            yi = int(str(r.get("year", ""))[:4])
        except (ValueError, TypeError):
            continue
        if not (1900 < yi < 2100):
            continue
        year_cat.append((yi, _classify_ref_category(r.get("title", ""))))
    if not year_cat:
        return ""
    years = sorted({y for y, _ in year_cat})
    used_cats = {c for _, c in year_cat}
    categories = [c for c, _ in _REF_CATEGORY_RULES if c in used_cats]
    categories += ["其他"] if "其他" in used_cats else []
    if not categories:
        return ""
    matrix = [
        [sum(1 for (y, c) in year_cat if y == yy and c == cc) for yy in years]
        for cc in categories
    ]
    return generate_topic_year_heatmap(
        f"{topic} 主题×年份文献分布", categories, years, matrix, filename=f"heatmap_{idx}.png"
    )


def generate_rf_pipeline(title: str, stages: list[str] | None = None, filename: str = "pipeline.png") -> str:
    """生成识别流水线流程框图 (确定性: 端到端识别流程)"""
    if stages is None:
        stages = ["信号采集", "预处理", "特征提取", "识别模型", "身份/认证决策"]
    apply_style(style="journal")
    n = len(stages)
    fig, ax = plt.subplots(figsize=(max(9, n * 2.4), 3))
    ax.axis("off")
    ax.set_xlim(-0.2, n + 0.2)
    ax.set_ylim(0, 1.3)
    for i, s in enumerate(stages):
        x = i + 0.15
        ax.add_patch(plt.Rectangle((x, 0.4), 0.7, 0.5,
                                   facecolor=PALETTE[i % len(PALETTE)], edgecolor="white", alpha=0.9))
        ax.text(x + 0.35, 0.65, s, ha="center", va="center", fontsize=10,
                color="white", fontweight="bold")
        if i < n - 1:
            ax.annotate("", xy=(i + 1.15, 0.65), xytext=(i + 0.87, 0.65),
                        arrowprops=dict(arrowstyle="->", color=ACCENT, lw=2))
    ax.set_title(title, fontsize=13, fontweight="bold", pad=15)
    fig.tight_layout()

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Pipeline figure saved: {out}")
    return str(out)


def generate_framework_overview(
    title: str,
    layers: list[tuple[str, list[str]]] | None = None,
    filename: str = "framework.png",
) -> str:
    """研究框架总览图 (确定性模板兜底: 分层框图)

    layers: [("信号表征", ["瞬态","稳态","变换域"]), ("模型架构", [...]), ...]
    """
    if layers is None:
        layers = [
            ("信号表征", ["瞬态信号", "稳态信号", "变换域特征"]),
            ("模型架构", ["传统分类器", "端到端CNN", "复值/集成网络"]),
            ("训练范式", ["监督学习", "小样本/自监督", "域适应"]),
            ("安全与应用", ["鲁棒认证", "对抗防御", "系统部署"]),
        ]
    apply_style(style="journal")
    n = len(layers)
    fig, ax = plt.subplots(figsize=(10, max(4, n * 1.1)))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    row_step = 0.88 / max(n, 1)
    y_cursor = 0.92
    for li, (cat, subs) in enumerate(layers):
        y_mid = y_cursor - row_step / 2 + 0.04
        color = PALETTE[li % len(PALETTE)]
        ax.text(0.04, y_mid, cat, ha="left", va="center", fontsize=11, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor=color, lw=1.6))
        # 子项横向平铺
        sub_w = 0.72 / max(len(subs), 1)
        for j, sub in enumerate(subs):
            x = 0.24 + j * sub_w + sub_w / 2
            ax.text(x, y_mid, sub, ha="center", va="center", fontsize=9,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#999999", lw=0.8))
            ax.plot([0.12, 0.23], [y_mid, y_mid], color=color, lw=0.8, alpha=0.6)
        y_cursor -= row_step
    ax.set_title(title, fontsize=13, fontweight="bold", pad=15)
    fig.tight_layout()

    out = _ensure_figure_dir() / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Framework overview saved: {out}")
    return str(out)


def _validate_comparison_data(methods: list, metrics: dict) -> tuple[bool, str]:
    """校验 LLM 提取的方法×指标对比数据 (防幻觉/防编造数值)。

    Returns: (合法?, 失败原因)
    """
    if not isinstance(methods, list) or not isinstance(metrics, dict):
        return False, "JSON 结构错误 (methods 应为数组, metrics 应为对象)"
    methods = [m for m in methods if isinstance(m, str) and m.strip()]
    if not (2 <= len(methods) <= 8):
        return False, f"方法数须在 2-8 之间, 实际 {len(methods)}"
    if any(len(m) > 30 for m in methods):
        return False, "方法名过长 (>30 字符), 疑似含解释文字而非缩写"
    if len(metrics) < 3:
        return False, f"指标数须 ≥3, 实际 {len(metrics)}"
    for name, vals in metrics.items():
        if not (isinstance(name, str) and name.strip()):
            return False, "存在空指标名"
        if not isinstance(vals, (list, tuple)) or len(vals) != len(methods):
            return False, f"指标「{name}」的值数量 ({len(vals) if isinstance(vals,(list,tuple)) else '非列表'}) 与方法数 ({len(methods)}) 不一致"
        for v in vals:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return False, f"指标「{name}」含非数值 ({v!r})"
            if not (0.0 <= fv <= 1.0):
                return False, f"指标「{name}」数值 {fv} 超出 [0,1] 范围"
    return True, ""


def _generate_comparison_from_notes(topic: str, lit_notes: str, idx: int) -> str:
    """LLM 从文献素材提取方法对比数据 → 分组柱状图。

    数据经 _validate_comparison_data 严格校验 (值域/数量/结构),
    校验失败重试一次, 仍失败则放弃 (返回空, 不渲染无来源/编造的数据)。
    """
    if len(lit_notes) < 200:
        return ""
    try:
        import json

        from langchain_core.messages import HumanMessage, SystemMessage

        from src.config import build_llm

        llm = build_llm("cheap")
        base_prompt = (
            "从以下文献综述素材中, 提取一个\"方法×指标\"对比矩阵 (JSON 格式)。\n\n"
            "硬性要求:\n"
            "1. 方法列表: 从素材中识别 2-6 个主要方法/模型 (英文缩写即可, ≤30 字符)\n"
            "2. 指标列表: **至少 3 个**评估维度, 如 精度/鲁棒性/计算开销/适用场景/实时性\n"
            "3. 每个方法在每个指标上的得分 (0-1 浮点数): 素材有数值则用数值,\n"
            "   素材有定性描述则映射: 很好→0.9, 较好→0.75, 一般→0.5, 较差→0.3, 未知→0.1\n"
            "4. **每个数值都必须有素材依据**; 素材中无法确认的指标/方法一律删除,\n"
            "   严禁编造数值或用随机数填充\n"
            "5. 只输出 JSON, 不要任何解释文字\n\n"
            "JSON 格式:\n"
            '{"methods": ["M1","M2",...], "metrics": {"精度":[0.9,0.75,...], "鲁棒性":[...], "开销":[...]}}\n\n'
            f"---素材---\n{lit_notes[:4000]}\n---素材结束---"
        )

        data = None
        # 提前初始化: 首次进入 attempt==1 前的异常路径若无此变量会 NameError
        last_reason = "（上一次尝试未产生可校验数据）"
        for attempt in range(2):
            prompt = base_prompt
            if attempt == 1:
                prompt = (
                    "你上一次的输出数据校验未通过，问题: " + last_reason + "\n"
                    "请严格按 JSON 格式重新提取，删除无法确认的指标/方法，数值必须在 [0,1]。\n\n"
                ) + base_prompt
            result = llm.invoke([SystemMessage(content="你是数据提取专家。只输出 JSON。"), HumanMessage(content=prompt)])
            text = result.content if hasattr(result, "content") else str(result)
            try:
                m = re.search(r"\{[\s\S]*\}", text)
                data = json.loads(m.group(0)) if m else json.loads("{" + text.split("{", 1)[-1].rsplit("}", 1)[0] + "}")
            except Exception as e:
                last_reason = f"JSON 解析失败 ({e})"
                continue
            methods = data.get("methods", [])
            metrics = data.get("metrics", {})
            ok, reason = _validate_comparison_data(methods, metrics)
            if ok:
                return generate_method_comparison_barchart(
                    f"{topic} 方法对比", methods, metrics, filename=f"method_comp_{idx}.png"
                )
            last_reason = reason
            logger.warning(f"方法对比数据校验失败: {reason}")
    except Exception as e:
        logger.debug(f"LLM method comparison extraction skipped: {e}")
    return ""
