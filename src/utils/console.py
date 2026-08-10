from __future__ import annotations

"""Windows 终端编码修复

Windows PowerShell 默认代码页 (cp936/cp1252) 无法编码 emoji/部分 Unicode，
导致 print 中文+emoji 时抛 UnicodeEncodeError。

方案: 在程序入口调用 ensure_utf8_console()，将 stdout/stderr 重配置为 UTF-8。
Python 3.7+ 的 sys.stdout.reconfigure() 可在运行时修改编码。
"""

import sys


def ensure_utf8_console() -> None:
    """将 stdout/stderr 重配置为 UTF-8（Windows 终端编码修复）"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


if __name__ == "__main__":
    ensure_utf8_console()
    print("编码测试: ✅ 中文 ✅ emoji 🚀 ✅")
