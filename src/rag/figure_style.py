"""学术图表风格指南

借鉴:
- PaperBanana (2.2k⭐) 的 NeurIPS-style aesthetic guidelines (Stylist agent)
- tueplots (752⭐) 期刊级 matplotlib 配置
- FigMirror (497⭐) 的 Aesthetic Lib

提供统一的中文学术论文图表风格配置:
- 配色: 色盲友好 + 高对比
- 字体: 微软雅黑/DejaVu Sans 混合, 统一字号
- 布局: 期刊宽度 (3.5in/7in), 300dpi
"""

# 学术论文标准配色 (ColorBrewer Set2 色盲友好)
PALETTE = [
    "#66C2A5",  # 绿色
    "#FC8D62",  # 橙色
    "#8DA0CB",  # 蓝色
    "#E78AC3",  # 粉色
    "#A6D854",  # 黄绿
    "#FFD92F",  # 黄色
    "#E5C494",  # 棕色
    "#B3B3B3",  # 灰色
]

# 强调色
ACCENT = "#4A6FA5"

# 字体设置
CN_FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC"]
EN_FONT = "DejaVu Sans"

# 期刊图宽 (inches): 单栏 3.5in, 双栏 7in
SINGLE_COL_WIDTH = 3.5
DOUBLE_COL_WIDTH = 7.0

# 字号 (pt)
FONT_SIZE = {
    "title": 11,
    "label": 10,
    "tick": 9,
    "legend": 8,
    "annotation": 8,
}

# DPI
OUTPUT_DPI = 300


def apply_style(ax=None, fig=None, style: str = "journal"):
    """应用统一风格到 matplotlib 对象

    style: "journal" (期刊默认) 或 "presentation" (报告演示)
    """
    import matplotlib.pyplot as plt

    # 全局 rcParams
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = FONT_SIZE["label"]
    plt.rcParams["axes.titlesize"] = FONT_SIZE["title"]
    plt.rcParams["axes.labelsize"] = FONT_SIZE["label"]
    plt.rcParams["xtick.labelsize"] = FONT_SIZE["tick"]
    plt.rcParams["ytick.labelsize"] = FONT_SIZE["tick"]
    plt.rcParams["legend.fontsize"] = FONT_SIZE["legend"]
    plt.rcParams["figure.dpi"] = OUTPUT_DPI
    plt.rcParams["savefig.dpi"] = OUTPUT_DPI

    if style == "journal":
        plt.rcParams["axes.grid"] = True
        plt.rcParams["grid.alpha"] = 0.3
        plt.rcParams["grid.linestyle"] = "--"
        plt.rcParams["axes.spines.top"] = False
        plt.rcParams["axes.spines.right"] = False
        plt.rcParams["lines.linewidth"] = 1.5
        plt.rcParams["lines.markersize"] = 5
    elif style == "presentation":
        plt.rcParams["axes.grid"] = True
        plt.rcParams["grid.alpha"] = 0.4
        plt.rcParams["axes.spines.top"] = False
        plt.rcParams["axes.spines.right"] = False
        plt.rcParams["lines.linewidth"] = 2.0
        plt.rcParams["lines.markersize"] = 6

    # 中文字体探测
    from matplotlib import font_manager

    for name in CN_FONT_CANDIDATES:
        if any(name.lower() in f.name.lower() for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = [name, EN_FONT]
            break


def style_guide_prompt() -> str:
    """返回给 LLM 的风格指南文本 (用于生成 matplotlib 代码时约束)"""
    return f"""## 学术图表风格指南 (必须严格遵守)

### 配色
- 首选色板: {PALETTE} (ColorBrewer Set2, 色盲友好)
- 强调色: {ACCENT}

### 字体与字号
- 中文: 微软雅黑/SimHei, 英文: DejaVu Sans
- 标题: {FONT_SIZE['title']}pt 加粗, 轴标签: {FONT_SIZE['label']}pt, 刻度: {FONT_SIZE['tick']}pt, 图例: {FONT_SIZE['legend']}pt

### 布局
- 单栏图宽 {SINGLE_COL_WIDTH} 英寸, 双栏 {DOUBLE_COL_WIDTH} 英寸
- 输出 300 DPI PNG
- 关闭上/右边框 (spines), 网格线用虚线 alpha=0.3
- 线条宽度 1.5, 标记大小 5
- 图例放在图内最佳位置, 避免遮挡数据

### 规范
- 坐标轴必须有清晰的标签 (含单位)
- 标题简洁, 不用句号结尾
- 图内文字不重叠, 不留大块空白
- 使用 plt.tight_layout() 或 constrained_layout
- 所有图例/标签清晰可读, 颜色对比足够
"""
