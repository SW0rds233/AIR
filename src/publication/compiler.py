from __future__ import annotations

"""Compile the canonical manuscript; reject partial PDFs and unresolved citations."""

import logging
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 编译轮次上限
MAX_COMPILE_ROUNDS = 3
# Retry transient Windows PDF locks with 2/4/6-second backoff.
PASS_RETRY_ATTEMPTS = 4
OVERFULL_TOLERANCE_PT = 2.0


def layout_diagnostics(log: str) -> dict:
    boxes = [float(value) for value in re.findall(
        r"Overfull \\[hv]box\s*\(([\d.]+)pt too (?:wide|high)\)", log or "")]
    return {"overfull_count": len(boxes), "max_overfull_pt": max(boxes, default=0.0),
            "tolerance_pt": OVERFULL_TOLERANCE_PT,
            "missing_glyphs": list(dict.fromkeys(re.findall(r"Missing character:[^\r\n]+", log or "")))}
_RERUN_PATTERNS = (
    r"Rerun to get cross-references right",
    r"There were undefined references",
    r"Citation\s+`[^']+'\s+.*undefined",
    r"Label\s+[`']?[^`']+[`']?\s+multiply defined",
)


def compile_latex(tex_path: str, workdir: str | None = None, engine: str = "xelatex") -> tuple[bool, str]:
    """Compile until references stabilize; only file-lock failures are retried."""
    path = Path(tex_path).resolve()
    output_dir = Path(workdir).resolve() if workdir is not None else path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    cwd = str(output_dir)
    pdf_path = output_dir / path.with_suffix(".pdf").name

    def _run_once():
        try:
            result = subprocess.run(
                [engine, "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", cwd, str(path)],
                capture_output=True, text=True, timeout=180,
                cwd=cwd, encoding="utf-8", errors="replace",
            )
            log = result.stdout + result.stderr
            # TeX can leave a partial PDF on failure; its presence is not success.
            errors = [line.strip() for line in log.splitlines() if line.startswith("!")]
            if errors:
                return False, "LaTeX 编译报错, 输出可能被截断:\n" + "\n".join(errors[:10])
            if result.returncode != 0:
                return False, (f"{engine} 退出码 {result.returncode}, 编译未正常结束:\n"
                               + log[-2000:])
            if "No pages of output" in log:
                return False, "LaTeX 没有输出任何页面 (No pages of output):\n" + log[-2000:]
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
            if "Unable to open" not in log:
                # 编译器缺失、语法错误、超时等并非临时文件锁；重试只会空等。
                return False, log
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
    last_ok = False
    for _ in range(MAX_COMPILE_ROUNDS):
        ok, last_log = _run_with_retry()
        last_ok = ok
        if "未安装" in last_log:
            return False, last_log
        if not ok and "Unable to open" not in last_log:
            break
        if ok and not any(re.search(pattern, last_log, re.IGNORECASE)
                          for pattern in _RERUN_PATTERNS):
            break

    # 编译报错时**不能**再用"PDF 存在且非空"兜底: LaTeX 遇错仍会写出截断的 PDF
    # (实测 1 页), 旧逻辑据此返回成功, 于是半篇论文被当成正常交付。
    if not last_ok:
        return False, last_log[-2000:]

    remaining = [pattern for pattern in _RERUN_PATTERNS
                 if re.search(pattern, last_log, re.IGNORECASE)]
    if remaining:
        return False, "交叉引用/标签经过多遍编译仍未稳定:\n" + last_log[-2000:]
    diagnostics = layout_diagnostics(last_log)
    if diagnostics["max_overfull_pt"] > OVERFULL_TOLERANCE_PT:
        return False, (f"LaTeX 排版溢出 {diagnostics['max_overfull_pt']:.2f}pt "
                       f"(容差 {OVERFULL_TOLERANCE_PT:.1f}pt)，请修正后再交付\n" + last_log)

    # A valid short article can be smaller than 10 KB. Check the PDF signature,
    # after confirming successful compilation and stable references above.
    for wait in (0.0, 2.0, 4.0):
        if wait:
            time.sleep(wait)
        try:
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                with pdf_path.open("rb") as pdf:
                    if pdf.read(5) == b"%PDF-":
                        return True, last_log
        except OSError:
            continue
    return False, last_log[-2000:]


def extract_latex_errors(log: str) -> list[str]:
    """从编译日志提取可读错误列表（供 LLM 修复参考）"""
    errors = []
    for m in re.finditer(r"^!(.*?)$", log, re.MULTILINE):
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
            # 中文 PDF 的文本是 UTF-8; 不指定编码时用系统 ANSI (cp1252) 解码,
            # 读取线程抛 UnicodeDecodeError, stdout 变 None, 页数恒为 0。
            encoding="utf-8", errors="replace",
        )
        text = result.stdout or ""
        if not text.strip():
            return 0
        # pdftotext 每页输出一个换页符, **末页也有**, 因此页数等于换页符个数。
        # 旧实现 `count("\\f") + 1` 会把 4 页报成 5 页。
        return text.count("\f")
    except Exception:
        return 0
