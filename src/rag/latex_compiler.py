from __future__ import annotations

"""LaTeX 编译 (转发到 `publication/compiler.py`)。

合并计划 §5.5 把编译归到 `publication/compiler.py`; 本模块保留是为了既有调用方
(`graph/theory_pipeline.py`、`agents/tools.py`) 与用例仍能按旧名取到**同一份**实现。
**没有第二份实现**: 这里只重新导出, 因此不存在"两处编译行为不同"的可能。
等旧理论引擎退役时, 本文件与它的调用点一并删除。
"""

from src.publication.compiler import (  # noqa: F401
    MAX_COMPILE_ROUNDS,
    PASS_RETRY_ATTEMPTS,
    cleanup_aux_files,
    compile_latex,
    extract_latex_errors,
    pdflatex_count_pages,
)

__all__ = [
    "MAX_COMPILE_ROUNDS",
    "PASS_RETRY_ATTEMPTS",
    "cleanup_aux_files",
    "compile_latex",
    "extract_latex_errors",
    "pdflatex_count_pages",
]
