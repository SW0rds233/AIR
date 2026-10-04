from __future__ import annotations

"""图表产物**质量检查**与审阅文本解析 (合并计划 §3.3 G18)。

这里原来还有一个"LLM 写 matplotlib 脚本 → 子进程执行 → 代码级/视觉审阅"的
自治绘图循环 (`generate_figure_with_llm` / `_execute_code` / `_sanitize_code` …)。
G18 把它删掉了, 原因不是"写得不好", 而是它**没有权限边界**: 模型产出的任意
Python 会被真的执行, 而进程隔离不等于权限隔离 —— 图的数据来源、单位、模型域与
写权限都无法在执行前校验。新的唯一入口是 `src.agents.figures.render_figure_spec`
(声明式 FigureSpec), 公式另走 `src.rag.figure_formula` 的白名单解析。

保留下来的是**不执行任何代码**的两件事:

- `check_png_quality`: 渲染产物的确定性质量检测 (空白/尺寸/内容密度/颜色多样性),
  现在由产物服务在写盘后调用, 让"画出来但等于白画"也能被如实报出来;
- `_extract_review_issues` / `_extract_vision_issues`: 把审阅文本解析成问题清单
  (过滤模板回显与吹毛求疵), 与"谁来审"无关, 供审阅路径复用。

注意: 本模块**没有**任何接受源码字符串并执行的函数, 也不起子进程;
`tests/test_figure_artifact_service.py::test_free_script_execution_path_is_gone`
专门锁这一点。
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

#: 保留给外部引用的**默认**图表目录 (生成函数已改为按调用时的配置与 run/task 作用域
#: 解析, 见 `figure_generator.figure_artifact_dir`)。保留常量是为了不破坏既有导入点。
FIGURE_DIR = Path(__file__).resolve().parent.parent.parent / "outputs" / "figures"


def check_png_quality(path: str) -> tuple[bool, str]:
    """PNG 质量检测: 存在性 / 尺寸 / 空白 / 内容密度 / 颜色多样性

    除 stddev 外增加两类确定性信号 (纯 numpy/PIL, 无 LLM 成本):
    - 内容占比过低 → 空白图 (仅渲染了标题/空坐标轴)
    - 唯一颜色数过少 → 纯色/近纯色塌缩图
    Returns: (通过?, 失败原因)
    """
    try:
        import numpy as _np
        from PIL import Image, ImageStat
    except ImportError:
        return True, ""  # 无 PIL/numpy 时跳过检测

    try:
        p = Path(path)
        if not p.exists():
            return False, "图片文件不存在"
        size = p.stat().st_size
        if size < 8 * 1024:
            return False, f"图片过小 ({size}B), 疑似空白"
        with Image.open(p) as img:
            w, h = img.size
            if not (400 <= w <= 5000 and 300 <= h <= 5000):
                return False, f"尺寸异常 {w}x{h}"
            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            stddev = stat.stddev[0]
            if stddev < 10:
                return False, f"图片几乎纯色 (stddev={stddev:.1f}), 疑似空白"
            # 内容密度: 非白像素 (灰度 < 245) 占比过低 → 大面积空白
            arr = _np.asarray(gray)
            fill_ratio = float((arr < 245).mean())
            if fill_ratio < 0.02:
                return False, f"图片内容占比过低 ({fill_ratio:.1%}), 疑似大面积空白"
            # 颜色多样性: 采样 + 量化 (每通道 8 级) 后统计唯一颜色数。
            # 采样而非 resize: resize 的插值会制造渐变过渡色, 污染统计。
            # 阈值保守 (<4 种): 只拦截"纯色/近纯色塌缩"图, 不误伤灰度线图。
            rgb = _np.asarray(img.convert("RGB"))
            step = max(1, min(rgb.shape[0], rgb.shape[1]) // 96)
            sampled = rgb[::step, ::step].reshape(-1, 3) // 32
            n_colors = len(set(map(tuple, sampled)))
            if n_colors < 4:
                return False, f"颜色数量过少 ({n_colors} 种), 疑似纯色/近纯色图"
        return True, ""
    except Exception as e:
        return False, f"PNG 检测异常: {e}"


def _extract_review_issues(review_text: str) -> list[str]:
    """从审阅意见提取必须修复的问题列表"""
    if not review_text:
        return []
    m = re.search(r"问题列表[:：]\s*(.*)", review_text, re.DOTALL)
    body = m.group(1) if m else review_text
    issues = []
    for line in body.split("\n"):
        raw = line.strip()
        if not raw or "无" in raw[:6]:
            continue
        # 跳过 prompt 模板回显 (审阅者有时会回显"若全部合规则输出"等指导语)
        if "若全部合规则" in raw or "输出格式" in raw or "问题列表" in raw:
            continue
        if raw[:1] not in ("-", "•", "*", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
            continue
        issues.append(raw.lstrip("-•* ").strip())
    return issues[:5]


# 视觉审阅 Critical 关键词: 只认含这些词的"硬伤"问题, 过滤"字号略小/配色可优化"
# 等吹毛求疵 —— 实测 kimi 每轮报满 5 个此类问题, 导致修正循环空转耗光预算。
_CRITICAL_VISION_HINTS = (
    "空白", "纯色", "空图", "截断", "裁切", "被裁", "超出", "溢出",
    "遮挡", "重叠", "覆盖", "缺失", "丢失", "无标签", "无标题", "无图例",
    "缺标签", "缺图例", "缺标题", "错误", "错乱", "颠倒", "乱序",
    "不可读", "看不清", "无法辨认", "模糊", "看不见", "看不到", "消失",
)


def _extract_vision_issues(review_text: str) -> list[str]:
    """从视觉审阅意见中提取**仅 Critical 级**问题。

    过滤掉不含 _CRITICAL_VISION_HINTS 关键词的吹毛求疵问题,
    避免"每轮报满 5 个问题"导致修正循环永不收敛 (实测痛点)。
    """
    all_issues = _extract_review_issues(review_text)
    return [i for i in all_issues if any(h in i for h in _CRITICAL_VISION_HINTS)]
