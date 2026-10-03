from __future__ import annotations

"""受限验证子进程入口。

从 stdin 读取一个 JSON 请求 {"tool","operation","arguments"}, 调用对应适配器,
把 VerificationResult 以 JSON 写回 stdout。任何异常都转换为 error 结果,
保证父进程能解析。运行在独立进程中, 便于超时终止与崩溃隔离。
"""

import json
import sys


def main() -> None:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"tool": "", "status": "error", "detail": f"请求解析失败: {e}"}))
        return
    tool = req.get("tool", "")
    operation = req.get("operation", "")
    arguments = req.get("arguments", {}) or {}
    if tool == "sympy":
        from src.verification import sympy_adapter as adapter
    elif tool == "z3":
        from src.verification import z3_adapter as adapter
    elif tool == "lean":
        from src.verification import lean_adapter as adapter
    elif tool == "stats":
        from src.verification import stats_adapter as adapter
    else:
        print(json.dumps({"tool": tool, "status": "unsupported",
                          "detail": f"未知工具 {tool}"}))
        return
    result = adapter.run(operation, arguments)
    sys.stdout.write(result.model_dump_json())


if __name__ == "__main__":
    main()
