from __future__ import annotations

"""LaTeX 编译与错误修正循环

借鉴 AI-Scientist-v2 的 compile_latex + cleanup_map 后处理模式:
- pdflatex/xelatex 编译 → 检测错误 → LLM 修正 → 再编译 (≤3 轮)
- 编译后清理辅助文件 (.aux/.log/.out), 保留 .tex/.pdf
"""

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# 编译轮次上限
MAX_COMPILE_ROUNDS = 3


def compile_latex(tex_path: str, workdir: str | None = None, engine: str = "xelatex") -> tuple[bool, str]:
    """编译 LaTeX → PDF（xelatex 双遍, thebibliography 不需 bibtex）

    thebibliography 的 \cite→\bibitem 解析需要两遍 xelatex (标准 LaTeX 行为)。
    """
    path = Path(tex_path).resolve()
    cwd = str(path.parent) if workdir is None else str(workdir)

    def _run():
        try:
            result = subprocess.run(
                [engine, "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", cwd, str(path)],
                capture_output=True, text=True, timeout=60,
                cwd=cwd, encoding="utf-8", errors="replace",
            )
            log = result.stdout + result.stderr
            if "Fatal error" in log or "Emergency stop" in log:
                err = [l.strip() for l in log.split("\n") if l.startswith("!")]
                return False, "\n".join(err[:10]) if err else log[-2000:]
            return True, log
        except subprocess.TimeoutExpired:
            return False, "LaTeX 编译超时 (60s)"
        except FileNotFoundError:
            return False, f"{engine} 未安装"
        except Exception as e:
            return False, str(e)

    ok, log = _run()
    if not ok:
        return False, log
    # 第二遍: 解析 \cite 引用 (thebibliography 需要)
    ok2, _ = _run()
    return True if ok2 else False, "Pass 2 failed"


def extract_latex_errors(log: str) -> list[str]:
    """从编译日志提取可读错误列表（供 LLM 修复参考）"""
    errors = []
    for m in re.finditer(r"^!(.*?)$", log, re.M):
        msg = m.group(1).strip()
        # 跳过 Undefined control sequence 里含 endless loop 检测的噪音
        if "Undefined control sequence" in msg:
            errors.append(msg[:200])
        elif len(msg) > 5:
            errors.append(msg[:200])
    return errors[:10]


def cleanup_aux_files(tex_path: str) -> None:
    """清理编译辅助文件"""
    path = Path(tex_path)
    for ext in (".aux", ".log", ".out", ".toc", ".lof", ".lot", ".bbl", ".blg", ".synctex.gz"):
        p = path.with_suffix(ext)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def pdflatex_count_pages(pdf_path: str) -> int:
    """用 pdftotext 统计 PDF 页数（借鉴 AI-Scientist-v2）"""
    try:
        result = subprocess.run(
            ["pdftotext", "-l", "999", pdf_path, "-"],
            capture_output=True, text=True, timeout=30,
        )
        # 统计换页符 \f 数量 + 1
        pages = result.stdout.count("\f") + 1
        return max(pages, 1) if result.stdout.strip() else 0
    except Exception:
        return 0
