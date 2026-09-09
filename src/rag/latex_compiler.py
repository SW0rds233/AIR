from __future__ import annotations

"""LaTeX 编译与错误修正循环

借鉴 AI-Scientist-v2 的 compile_latex + cleanup_map 后处理模式:
- pdflatex/xelatex 编译 → 检测错误 → LLM 修正 → 再编译 (≤3 轮)
- 编译后清理辅助文件 (.aux/.log/.out), 保留 .tex/.pdf
"""

import logging
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 编译轮次上限
MAX_COMPILE_ROUNDS = 3
# 单遍编译的失败重试次数 (针对 dvipdfmx "Unable to open" 这类瞬时错误)。
# Windows 实测: 杀毒软件/索引服务对新写出 PDF 的锁定可持续 10 秒以上,
# 重试间隔必须足够长 (2/4/6s), 否则整轮编译被误判失败。
PASS_RETRY_ATTEMPTS = 4


def compile_latex(tex_path: str, workdir: str | None = None, engine: str = "xelatex") -> tuple[bool, str]:
    """编译 LaTeX → PDF（xelatex 多遍, thebibliography 不需 bibtex）

    thebibliography 的 \\cite->\\bibitem 解析需要多遍编译:
    第 1 遍生成 .aux (此时 PDF 中引用显示为 [?]), 第 2 遍起解析引用。
    若第 2 遍后仍有 undefined 引用警告则再编译一遍。

    Windows 实测问题: 目标 PDF 若被占用 (PDF 阅读器打开旧文件 / 杀毒瞬时锁),
    dvipdfmx 报 "Unable to open" → 第一遍直接失败。旧版在第一遍失败即 return,
    根本没走到"PDF 已生成"兜底。修复:
    - 编译前删除旧 PDF (占用则警告提示关闭阅读器)
    - 第一遍失败不中断, 继续后续遍 (锁可能在下一遍前释放)
    """
    path = Path(tex_path).resolve()
    cwd = str(path.parent) if workdir is None else str(workdir)
    pdf_path = path.with_suffix(".pdf")

    def _run_once():
        try:
            result = subprocess.run(
                [engine, "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", cwd, str(path)],
                capture_output=True, text=True, timeout=180,
                cwd=cwd, encoding="utf-8", errors="replace",
            )
            log = result.stdout + result.stderr
            if "Fatal error" in log or "Emergency stop" in log:
                err = [l.strip() for l in log.split("\n") if l.startswith("!")]
                return False, "\n".join(err[:10]) if err else log[-2000:]
            return True, log
        except subprocess.TimeoutExpired:
            return False, "LaTeX 编译超时 (180s)"
        except FileNotFoundError:
            return False, f"{engine} 未安装"
        except Exception as e:
            return False, str(e)

    def _run_with_retry() -> tuple[bool, str]:
        """单遍编译 + 瞬时失败重试 (Unable to open / 文件被占用)"""
        ok, log = False, ""
        for attempt in range(PASS_RETRY_ATTEMPTS):
            ok, log = _run_once()
            if ok and "Unable to open" not in log:
                return True, log
            if attempt < PASS_RETRY_ATTEMPTS - 1:
                wait = 2.0 * (attempt + 1)
                logger.warning(f"LaTeX pass 失败, {wait:.0f}s 后重试: {log[-160:].strip()}")
                time.sleep(wait)
        return ok and "Unable to open" not in log, log

    # 编译前删除旧 PDF: 目标 PDF 被占用 (阅读器打开) 是 dvipdfmx 失败的常见根因。
    # 能删掉就排除了冲突; 删不掉则明确提示用户关闭阅读器。
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except OSError as e:
            logger.warning(
                f"目标 PDF 被占用无法覆盖: {pdf_path.name} ({e})。"
                f"若是 PDF 阅读器正在打开该文件, 请关闭后重试。"
            )

    # 多遍编译: 至多 MAX_COMPILE_ROUNDS 遍, 每遍带瞬时失败重试。
    # 某遍失败 (如第一遍锁) 不中断, 继续下一遍; 直到成功且无 undefined 引用。
    last_log = ""
    for _ in range(MAX_COMPILE_ROUNDS):
        ok, last_log = _run_with_retry()
        if ok and not re.search(r"Citation\s+`[^']+'\s+.*undefined", last_log):
            break

    # 成功标准: PDF 实际生成且非空 (即使某遍报瞬时错误, 只要最终 PDF 在就算成功)。
    # stat 也可能因文件瞬时被锁而失败, 做多次尝试。
    for wait in (0.0, 2.0, 4.0):
        if wait:
            time.sleep(wait)
        try:
            if pdf_path.exists() and pdf_path.stat().st_size >= 10 * 1024:
                if re.search(r"Citation\s+`[^']+'\s+.*undefined", last_log):
                    logger.warning("PDF 已生成但仍存在未解析引用, 请检查参考文献编号一致性")
                return True, last_log
        except OSError:
            continue
    return False, last_log[-2000:]


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
