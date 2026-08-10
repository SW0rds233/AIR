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
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from src.rag.figure_style import apply_style, PALETTE, ACCENT
from src.config import build_llm

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


def _ensure_figure_dir() -> Path:
    d = Path(__file__).resolve().parent.parent.parent / "outputs" / "figures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_taxonomy_tree(
    title: str,
    taxonomy: dict[str, list[str]],
    filename: str = "taxonomy.png",
) -> str:
    """生成分类体系树状图

    taxonomy: {"大类A": ["子类A1", "子类A2", ...], "大类B": [...]}
    Returns: 图片文件路径
    """
    apply_style(style="journal")
    fig, ax = plt.subplots(figsize=(10, max(5, len(taxonomy) * 1.2)))
    ax.axis("off")

    # 根节点
    ax.text(0.5, 0.97, title, ha="center", va="top",
            fontsize=13, fontweight="bold")

    y_top = 0.90
    y_bottom = 0.08
    n_cats = len(taxonomy)
    cat_height = (y_top - y_bottom) / max(n_cats, 1)

    for i, (cat, subs) in enumerate(taxonomy.items()):
        y_cat = y_top - i * cat_height - cat_height * 0.2
        # 大类节点
        ax.text(0.08, y_cat, cat, ha="left", va="center",
                fontsize=11, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#E8F0FE", edgecolor="#4A6FA5"))
        # 子类节点
        n_sub = max(len(subs), 1)
        sub_w = 0.82 / max(n_sub, 1)
        for j, sub in enumerate(subs):
            x = 0.16 + j * sub_w + sub_w / 2
            ax.text(x, y_cat, sub, ha="center", va="center", fontsize=9,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="#F5F5F5", edgecolor="#999999"))
            ax.annotate("", xy=(x, y_cat + 0.01), xytext=(0.08, y_cat + 0.01),
                        arrowprops=dict(arrowstyle="-", color="#999999", lw=0.8))

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    try:
        fig.tight_layout()
    except Exception:
        pass  # 分类过多时 tight_layout 可能失败, 不影响渲染

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
    """生成研究时间线图

    milestones: [{"year": 2017, "event": "Transformer 提出", "note": "..."}, ...]
    Returns: 图片文件路径
    """
    apply_style(style="journal")
    fig, ax = plt.subplots(figsize=(12, max(4, len(milestones) * 0.55)))
    ax.axis("off")

    years = [m.get("year", 0) for m in milestones]
    events = [m.get("event", "") for m in milestones]

    if years:
        y_min, y_max = min(years) - 1, max(years) + 1
        ax.plot([y_min, y_max], [0, 0], color="#4A6FA5", lw=2.5, zorder=1)

        for i, (y, ev) in enumerate(zip(years, events)):
            direction = 1 if i % 2 == 0 else -1
            ax.scatter([y], [0], s=80, color="#4A6FA5", zorder=3)
            ax.vlines(y, 0, direction * 0.35, color="#4A6FA5", lw=1.2, zorder=2)
            ax.text(y, direction * 0.42, str(y), ha="center",
                    fontsize=10, fontweight="bold")
            ax.text(y, direction * 0.58, ev, ha="center", va="top" if direction > 0 else "bottom",
                    fontsize=9, wrap=True,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="#F5F5F5", edgecolor="#999999"))

    ax.set_xlim(y_min - 0.5, y_max + 0.5)
    ax.set_ylim(-0.75, 0.75)
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
    for i, (m_name, vals) in enumerate(metrics.items()):
        offset = (i - n_metrics / 2 + 0.5) * width
        bars = [vals[j] if j < len(vals) else 0 for j in range(n_methods)]
        ax.bar(x + offset, bars, width * 0.9, label=m_name,
               color=PALETTE[i % len(PALETTE)], alpha=0.85, edgecolor="white")

    ax.set_xticks(x)
    ax.set_xticklabels(methods, fontsize=9, rotation=15, ha="right")
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
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
    """从文献综述素材自动生成图表（LLM 优先，确定性模板兜底）

    策略 (借鉴 PaperBanana + FigMirror + AI-Scientist 12 图上限):
    1. 分类体系图 (taxonomy, LLM 优先 → 模板兜底)
    2. 研究时间线图 (timeline, LLM 优先 → 模板兜底)
    3. 出版趋势图 (trend, 确定性: 从 verified_refs 年份统计)
    4. 方法对比图 (comparison, LLM 提取数据 → 分组柱状图)

    Returns: 生成的图片路径列表
    """
    paths: list[str] = []
    refs = verified_refs or []

    # ===== 1. 分类体系图 =====
    taxonomy = parse_taxonomy_from_text(lit_notes)
    if taxonomy:
        tax_text = "\n".join(f"- {cat}: {', '.join(subs[:8])}" for cat, subs in taxonomy.items())
        try:
            from src.rag.figure_llm import generate_taxonomy_figure
            llm_path = generate_taxonomy_figure(topic, tax_text, index=0)
            if llm_path:
                paths.append(llm_path)
            else:
                paths.append(generate_taxonomy_tree(f"{topic} 分类体系", {k: v[:6] for k, v in taxonomy.items()}, filename=f"taxonomy_{len(paths)}.png"))
        except Exception as e:
            logger.warning(f"LLM taxonomy failed: {e}")
            paths.append(generate_taxonomy_tree(f"{topic} 分类体系", {k: v[:6] for k, v in taxonomy.items()}, filename=f"taxonomy_{len(paths)}.png"))

    # ===== 2. 时间线图 =====
    milestones = parse_timeline_from_text(lit_notes)
    if len(milestones) >= 3:
        tl_text = "\n".join(f"{m['year']}: {m['event']}" for m in milestones[:12])
        try:
            from src.rag.figure_llm import generate_timeline_figure
            llm_path = generate_timeline_figure(topic, tl_text, index=len(paths))
            if llm_path:
                paths.append(llm_path)
            else:
                paths.append(generate_timeline(f"{topic} 研究发展脉络", milestones[:12], filename=f"timeline_{len(paths)}.png"))
        except Exception as e:
            logger.warning(f"LLM timeline failed: {e}")
            paths.append(generate_timeline(f"{topic} 研究发展脉络", milestones[:12], filename=f"timeline_{len(paths)}.png"))

    # ===== 3. 出版趋势图 (确定性: 从 verified_refs 统计) =====
    trend_path = _generate_trend_from_refs(topic, refs, len(paths))
    if trend_path:
        paths.append(trend_path)

    # ===== 4. 方法对比图 (LLM 提取结构化数据) =====
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


def _generate_comparison_from_notes(topic: str, lit_notes: str, idx: int) -> str:
    """LLM 从文献素材提取方法对比数据 → 分组柱状图"""
    if len(lit_notes) < 200:
        return ""
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from src.config import build_llm

        llm = build_llm("cheap")
        prompt = (
            "从以下文献综述素材中, 提取一个\"方法×指标\"对比矩阵 (JSON 格式)。\n\n"
            "要求:\n"
            "1. 方法列表: 从素材中识别 4-6 个主要方法/模型 (英文缩写即可)\n"
            "2. 指标列表: **至少 3 个**评估维度, 如 精度/鲁棒性/计算开销/适用场景/实时性\n"
            "3. 每个方法在每个指标上的得分 (0-1 浮点数, 素材有数值则用数值,\n"
            "   素材有定性描述则映射: 很好→0.9, 较好→0.75, 一般→0.5, 较差→0.3, 未知→0.1)\n"
            "4. 只输出 JSON, 不要任何解释文字\n\n"
            "JSON 格式:\n"
            '{"methods": ["M1","M2",...], "metrics": {"精度":[0.9,0.75,...], "鲁棒性":[...], "开销":[...]}}\n\n'
            f"---素材---\n{lit_notes[:4000]}\n---素材结束---"
        )
        result = llm.invoke([SystemMessage(content="你是数据提取专家。只输出 JSON。"), HumanMessage(content=prompt)])
        text = result.content if hasattr(result, "content") else str(result)

        import json
        m = re.search(r"\{[\s\S]*\}", text)
        data = json.loads(m.group(0)) if m else json.loads("{" + text.split("{", 1)[-1].rsplit("}", 1)[0] + "}")

        methods = data.get("methods", [])
        metrics = data.get("metrics", {})
        if len(methods) >= 2 and len(metrics) >= 2:
            return generate_method_comparison_barchart(
                f"{topic} 方法对比", methods, metrics, filename=f"method_comp_{idx}.png"
            )
    except Exception as e:
        logger.debug(f"LLM method comparison extraction skipped: {e}")
    return ""
