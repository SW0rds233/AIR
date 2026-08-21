"""LLM 驱动的学术图表生成 (Drawer-Reviewer 修正循环)"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.config import LLM_CONFIG, build_llm
from src.rag.figure_style import style_guide_prompt, PALETTE, ACCENT, CN_FONT_CANDIDATES, EN_FONT
from src.utils.cost_tracker import tracker, extract_usage_metadata

logger = logging.getLogger(__name__)

FIGURE_DIR = Path(__file__).resolve().parent.parent.parent / "outputs" / "figures"

# 注入沙箱的期刊级样式样板: 保证即使 LLM 代码极简, 渲染质量也达标
STYLE_BOILERPLATE = f'''import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

# ---- 学术样式样板 (系统注入, LLM 代码在其后执行, 可覆盖) ----
plt.rcParams["figure.dpi"] = {300}
plt.rcParams["savefig.dpi"] = {300}
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["axes.spines.top"] = False
plt.rcParams["axes.spines.right"] = False
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3
plt.rcParams["grid.linestyle"] = "--"
plt.rcParams["lines.linewidth"] = 1.5
plt.rcParams["lines.markersize"] = 5
plt.rcParams["font.size"] = 10
plt.rcParams["axes.titlesize"] = 11
plt.rcParams["axes.labelsize"] = 10
plt.rcParams["xtick.labelsize"] = 9
plt.rcParams["ytick.labelsize"] = 9
plt.rcParams["legend.fontsize"] = 8
for _name in {CN_FONT_CANDIDATES!r}:
    if any(_name.lower() in f.name.lower() for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = [_name, {EN_FONT!r}]
        break
'''

CODE_GEN_SYSTEM = f"""你是"学术图表绘制专家"。你的任务是根据数据描述和风格指南，生成可直接运行的 matplotlib 代码。

{style_guide_prompt()}

## 代码规范
- 只输出 Python 代码, 用 ```python 围栏包裹
- 代码必须独立可运行 (包含所有 import)
- 使用 plt.savefig 保存图片到指定路径 (路径已给出)
- 必须 plt.close(fig) 释放内存
- 绘图标记只能用 matplotlib 标准样式 ("o", "s", "^", "D", "v", "*" 等 ASCII 标记)；严禁使用 "•"、"★"、"●" 等 Unicode 字符作 marker (会触发 ValueError: Unrecognized marker style)
- 中文文本自动由 matplotlib 处理 (已配置字体)
- 优先使用 ax = fig.add_subplot() 或 plt.subplots() 方式
- 必须设置 figsize=(宽, 高) (单栏 3.5-5 英寸宽, 分类树 8-10 英寸宽)
- 分类树: 使用 ax.axis("off") + 文本框 bbox + 箭头 annotate 绘制层次结构
- 分类树/层次图节点必须用 dict 表示 (如 node = dict(name="...", depth=1))，访问层级用 node["depth"]；严禁对 int/str 变量做下标访问（node["depth"] 会触发 'int' object is not subscriptable）
- 时间线: 使用 ax.axis("off") + 水平轴线 + 上下交替的事件标签
"""

# 代码级审阅专家: 无多模态 API 时, 审阅代码而非图像 (FigMirror 模式)
CODE_REVIEWER_SYSTEM = f"""你是"学术图表审阅专家"。你无法看到渲染后的图片, 只能审阅 matplotlib 代码。

请从学术出版规范角度检查以下问题, 只报告**必须修复**的问题:
1. 坐标轴是否缺少标签（含单位）
2. 分类树/时间线是否缺少根节点/轴线/连接线
3. 图例是否可能过多或遮挡数据（超过 8 项应简化）
4. 是否设置 figsize（未设置会导致默认尺寸模糊）
5. 是否使用 plt.savefig 且 dpi>=300
6. 是否有 plt.close 释放内存
7. 数据是否被硬编码为随机数而非真实数据
8. 文字是否可能重叠（标签过多/字号过大）

输出格式（严格）:
```
问题列表:
- 问题1: 位置/原因
- 问题2: ...
（若全部合规则输出: 问题列表:\n- 无）
```

{style_guide_prompt()}"""

MAX_FIX_ROUNDS = 2
# 单张图的总时间预算 (秒): 超限即放弃 LLM 路径, 由调用方回退确定性模板。
# 此前无预算限制: 5 轮 × (主模型代码生成 + 子进程执行 + 廉价模型审阅) 可静默耗时
# 10+ 分钟, 用户误以为流水线卡死。
FIGURE_TIME_BUDGET = float(os.getenv("FIGURE_TIME_BUDGET", "300"))
# 代码级审阅 (Drawer-Reviewer) 默认关闭: 实测审阅几乎总是报满 5 个吹毛求疵的问题,
# 强制修正轮耗尽时间预算后整图回退模板, 净浪费 ~6 分钟/图。
# 执行成功 + PNG 质量检测通过即接受; 设 FIGURE_CODE_REVIEW=1 可重新启用。
FIGURE_CODE_REVIEW = os.getenv("FIGURE_CODE_REVIEW", "0") == "1"


def _extract_code(llm_output: str) -> str:
    """从 LLM 输出提取 python 代码块"""
    m = re.search(r"```python\n(.*?)```", llm_output, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\n(.*?)```", llm_output, re.DOTALL)
    if m:
        return m.group(1).strip()
    return llm_output.strip()


def _sanitize_code(code: str, save_path: str) -> str:
    """清洗 LLM 生成的代码中的路径/赋值错误"""
    if not code:
        return code
    # 1) 删除 "盘符:\...\文件名 = r"..."" / "outputs\figures\x.png = r"..."" 赋值垃圾行
    code = re.sub(
        r"(?m)^\s*[A-Za-z]:\\[^\r\n=]*=\s*r?['\"][^'\"]*['\"]\s*$",
        "",
        code,
    )
    code = re.sub(
        r"(?m)^\s*(?:outputs|figures|\.)[^\r\n=]*\\[^\r\n=]*=\s*r?['\"][^'\"]*['\"]\s*$",
        "",
        code,
    )
    # 2) 保存路径的所有形态 → OUTPUT_PATH (带引号/不带引号, 绝对/相对/仅文件名)
    base = os.path.basename(save_path)                    # taxonomy_llm_0.png
    norm = save_path.replace("\\", "/")                   # 正斜杠版绝对路径
    for variant in dict.fromkeys([save_path, norm, base]):
        escaped = re.escape(variant)
        # 带引号形态: r"..." 或 "..."
        code = re.sub(rf"r?['\"]{escaped}['\"]", "OUTPUT_PATH", code)
        # 不带引号形态 (裸 token, 前后非标识符字符)
        code = re.sub(rf"(?<![A-Za-z0-9_'\"])(?:{escaped})(?![A-Za-z0-9_])", "OUTPUT_PATH", code)
    # 3) 关键: 强制所有 savefig 的目标路径替换为 OUTPUT_PATH。
    # LLM 常自创文件名 (如 savefig("射频指纹分类体系图.png")), 这些不被上面的
    # save_path 变体拦截, 导致图片散落到工作目录而非 outputs/figures/。
    code = _force_savefig_output_path(code)
    return code


def _force_savefig_output_path(code: str) -> str:
    """把代码里所有 savefig 调用统一改为保存到 OUTPUT_PATH。

    匹配 savefig(...) 的第一个位置参数 (字符串/变量), 替换为 OUTPUT_PATH。
    兼容: savefig("x.png") / savefig('x') / savefig(x) / savefig(r"x") / savefig(x, dpi=300)
    """
    if not code:
        return code
    # 匹配 savefig( 后紧跟的第一个参数 (带引号字符串 或 裸标识符), 连同其后的逗号/括号
    pattern = re.compile(
        r"(?P<prefix>savefig\s*\(\s*)(?:r?[\"'][^\"']*[\"']|[A-Za-z_][\w\.]*)"
    )
    code = pattern.sub(lambda m: m.group("prefix") + "OUTPUT_PATH", code)
    return code


def _execute_code(code: str, save_path: str, timeout: int = 60) -> tuple[bool, str]:
    """在子进程执行代码 (沙箱, 防止污染主进程 matplotlib 状态)

    OUTPUT_PATH 是沙箱脚本中定义的**真实变量**, 不是文本替换——
    文本替换会把变量名换成裸路径导致 NameError (实测失败模式)。
    Returns: (成功?, 错误信息或stdout)
    """
    code = _sanitize_code(code, save_path)
    # 引号包裹的 OUTPUT_PATH (LLM 可能写 r"OUTPUT_PATH") → 裸标识符
    code = code.replace('r"OUTPUT_PATH"', "OUTPUT_PATH").replace("'OUTPUT_PATH'", "OUTPUT_PATH")
    if "savefig" not in code:
        code += '\nplt.savefig(OUTPUT_PATH, dpi=300, bbox_inches="tight")\nplt.close("all")'

    # 手动缩进（textwrap.indent 不处理空行）
    indented = "\n".join(
        "    " + line if line.strip() else line for line in code.split("\n")
    )
    script = (
        STYLE_BOILERPLATE
        + "\n"
        + f"OUTPUT_PATH = {save_path!r}\n"
        + 'import json, os\n'
        "try:\n"
        f"{indented}\n"
        '    print("EXEC_OK")\n'
        "except Exception as e:\n"
        "    import traceback\n"
        '    print("EXEC_ERROR")\n'
        "    traceback.print_exc()\n"
    )

    try:
        # PYTHONIOENCODING=utf-8: 沙箱内 print 中文标签在 Windows cp1252 控制台
        # 会触发 UnicodeEncodeError, 导致代码被误判为执行失败
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        output = result.stdout + result.stderr
        if "EXEC_OK" in output:
            # 渲染质量检测: 空白图/尺寸异常视为失败
            ok, reason = check_png_quality(save_path)
            if not ok:
                return False, f"PNG 质量检测失败: {reason}"
            return True, output
        return False, output[-2000:]
    except subprocess.TimeoutExpired:
        return False, "代码执行超时"
    except Exception as e:
        return False, str(e)


def check_png_quality(path: str) -> tuple[bool, str]:
    """PNG 质量检测: 存在性 / 尺寸 / 空白检测

    Returns: (通过?, 失败原因)
    """
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return True, ""  # 无 PIL 时跳过检测

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


def generate_figure_with_llm(
    description: str,
    save_path: str,
    context: str = "",
    max_rounds: int = MAX_FIX_ROUNDS,
) -> tuple[bool, str]:
    """LLM 生成图表: 生成代码 → 执行+质量检测 → 代码审阅 → 修正循环

    Drawer-Reviewer 模式 (借鉴 FigMirror): 代码执行通过后,
    由审阅 LLM 按学术出版规范检查代码缺陷 (无多模态 API 时审代码而非图像),
    有必须修复的问题则再生成一轮。

    Args:
        description: 图表内容描述 (如分类体系、数据)
        save_path: 输出 PNG 路径
        context: 额外的上下文 (如文献素材片段)
        max_rounds: 最大修正轮数 (执行修正 + 审阅修正共用)

    Returns: (成功?, 图片路径或错误信息)
    """
    llm = build_llm("main")
    reviewer_llm = build_llm("cheap")

    palette_str = ", ".join(PALETTE)
    accent_str = ACCENT

    prompt = f"""请根据以下描述生成一个学术图表。

---图表描述---
{description}
---描述结束---

---参考上下文---
{context[:3000]}
---上下文结束---

输出要求:
1. 图表尺寸约 8x5 英寸 (分类树可用 10x6)
2. **保存图片: 使用变量 OUTPUT_PATH（系统已定义好）**, 写法:
   plt.savefig(OUTPUT_PATH, dpi=300, bbox_inches="tight")
   禁止写出完整路径, 禁止写 "路径 = r"路径"" 之类的赋值语句
3. 可用色板: {palette_str}, 强调色: {accent_str}
4. 图表标题: 简洁中文标题
5. 若描述中包含分类层次 (如 A→B, C→D), 请绘制树状/层次图
6. 若包含年份序列, 请绘制时间线/折线图
7. **图表类型选择**: 时间序列→折线图; 方法×指标对比→分组柱状图;
   排名→水平条形图; 相关关系→散点图; 占比→堆叠柱状图(禁用饼图)
8. **只能使用描述中给出的数据**, 不得编造/臆造任何数据点
"""

    messages = [SystemMessage(content=CODE_GEN_SYSTEM), HumanMessage(content=prompt)]
    last_error = ""
    last_code = ""

    import time as _time

    t0 = _time.monotonic()
    for round_idx in range(max_rounds + 1):
        elapsed = _time.monotonic() - t0
        if elapsed > FIGURE_TIME_BUDGET:
            print(f"  [figure] 已耗时 {elapsed:.0f}s 超出 {FIGURE_TIME_BUDGET:.0f}s 预算, 放弃 LLM 路径 (回退确定性模板)")
            break
        try:
            if round_idx > 0:
                # 修正轮: 附上错误信息或审阅意见
                if last_error.startswith("PNG 质量检测失败") or "EXEC_ERROR" in last_error \
                        or "执行失败" in last_error or "超时" in last_error:
                    fix_prompt = (
                        f"上轮生成的代码执行/渲染失败，信息如下:\n```\n{last_error}\n```\n"
                        f"请修正代码重新输出完整可运行版本。"
                    )
                else:
                    fix_prompt = (
                        f"上轮代码已成功渲染，但审阅专家提出了以下必须修复的问题:\n"
                        f"```\n{last_error}\n```\n"
                        f"请在保持图表内容不变的前提下修正这些问题，重新输出完整代码。"
                    )
                messages = messages[:2] + [HumanMessage(content=fix_prompt)]

            print(f"  [figure] LLM 代码生成 (第 {round_idx + 1}/{max_rounds + 1} 轮, {Path(save_path).name})...")
            result = llm.invoke(messages)
            code = _extract_code(result.content if hasattr(result, "content") else str(result))
            last_code = code

            usage = extract_usage_metadata(result)
            if usage:
                model_name = (
                    result.response_metadata.get("model_name")
                    if isinstance(result.response_metadata, dict)
                    else None
                ) or LLM_CONFIG["model"]
                tracker.add_call(model_name, usage, stage=f"figure_gen_r{round_idx}")

            print(f"  [figure] 沙箱执行代码 (第 {round_idx + 1} 轮)...")
            ok, output = _execute_code(code, save_path)
            if not ok:
                last_error = output
                print(f"  [figure] 执行/渲染失败: {output[-150:].strip()}")
                logger.warning(f"Figure code exec failed (round {round_idx}): {output[-300:]}")
                continue

            # 执行通过即接受的两种情形: 末轮 (避免耗尽预算后整图回退)
            # 或代码审阅关闭 (默认: 执行+PNG 质量通过已足够, 审阅轮实测只拖慢流程)
            if round_idx >= max_rounds or not FIGURE_CODE_REVIEW:
                print(f"  [figure] 渲染通过, 采用: {Path(save_path).name}")
                return True, save_path

            # 代码执行 + 渲染质量均通过 → 代码级审阅 (Drawer-Reviewer)
            print(f"  [figure] 代码级审阅 (第 {round_idx + 1} 轮)...")
            try:
                review_prompt = (
                    f"请审阅以下 matplotlib 代码的学术出版规范问题。\n\n"
                    f"---图表描述---\n{description[:2000]}\n---描述结束---\n\n"
                    f"---代码---\n```python\n{code[:6000]}\n```\n---代码结束---\n"
                )
                review_result = reviewer_llm.invoke(
                    [SystemMessage(content=CODE_REVIEWER_SYSTEM), HumanMessage(content=review_prompt)]
                )
                review_usage = extract_usage_metadata(review_result)
                if review_usage:
                    from src.config import CHEAP_CONFIG

                    review_model = (
                        review_result.response_metadata.get("model_name")
                        if isinstance(review_result.response_metadata, dict)
                        else None
                    ) or CHEAP_CONFIG["model"]
                    tracker.add_call(review_model, review_usage, stage=f"figure_review_r{round_idx}")
                review_text = review_result.content if hasattr(review_result, "content") else str(review_result)
                issues = _extract_review_issues(review_text)
            except Exception as e:
                logger.warning(f"Figure review failed (round {round_idx}): {e}")
                issues = []

            if not issues:
                print(f"  [figure] 审阅通过: {Path(save_path).name}")
                logger.info(f"Figure generated & reviewed OK: {save_path}")
                return True, save_path

            last_error = "\n".join(issues)
            print(f"  [figure] 审阅发现 {len(issues)} 个问题, 进入修正轮")
            logger.info(f"Figure review issues (round {round_idx}): {issues}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Figure LLM call failed (round {round_idx}): {e}")

    if not last_code:
        return False, f"图表生成失败（{max_rounds} 轮修正后仍失败）: {last_error[:300]}"
    return False, f"图表生成失败（{max_rounds} 轮修正后仍未通过审阅）: {last_error[:300]}"


def generate_taxonomy_figure(topic: str, taxonomy_text: str, index: int = 0) -> str:
    """用 LLM 生成分类体系图"""
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    path = str(FIGURE_DIR / f"taxonomy_llm_{index}.png")
    desc = (
        f"绘制「{topic}」研究方法的分类体系图（树状层次结构）。\n"
        f"分类结构:\n{taxonomy_text}\n"
        f"要求: 根节点为「{topic}」，一级节点为大类，叶子为具体方法/子类。"
        f"使用圆角矩形框 + 箭头连接，颜色区分层级。"
    )
    ok, result = generate_figure_with_llm(desc, path, context=taxonomy_text)
    return result if ok else ""


def generate_timeline_figure(topic: str, timeline_text: str, index: int = 0) -> str:
    """用 LLM 生成研究时间线图"""
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    path = str(FIGURE_DIR / f"timeline_llm_{index}.png")
    desc = (
        f"绘制「{topic}」领域研究发展时间线图。\n"
        f"时间线数据:\n{timeline_text}\n"
        f"要求: 横向时间轴，事件用圆点+标签标注在轴上下交替，用强调色标记关键节点。"
    )
    ok, result = generate_figure_with_llm(desc, path, context=timeline_text)
    return result if ok else ""
