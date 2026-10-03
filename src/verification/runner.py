from __future__ import annotations

"""验证执行器 (P1)。

- 在独立子进程中调用 SymPy/Z3/Lean 适配器, 读真实退出码与标准输出;
  绝不接受 LLM 自述的"工具已通过"。
- 传入最小环境变量 (移除 API 凭据), 设置超时; 超时/崩溃映射为 timeout/error。
- 工具未安装时返回 unavailable, 并据此降低验证等级, 不静默绕过。
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.verification.schemas import VerificationResult, VerificationStatus

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_TOOL_SPECS = {
    "sympy": "sympy",
    "z3": "z3",
    "lean": None,  # 二进制工具, 由适配器自行探测
}


def tool_available(tool: str) -> bool:
    if tool in ("sympy", "z3"):
        import importlib.util

        name = "z3" if tool == "z3" else "sympy"
        return importlib.util.find_spec(name) is not None
    if tool == "stats":
        import importlib.util

        return importlib.util.find_spec("numpy") is not None
    if tool == "lean":
        import shutil

        return shutil.which("lean") is not None
    return False


def _minimal_env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "HOME", "USERPROFILE",
            "WINDIR", "COMSPEC", "PATHEXT")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    # 明确不继承任何 API key / 代理凭据
    return env


def run_request(
    tool: str,
    operation: str,
    arguments: dict[str, Any],
    timeout: float = 30.0,
) -> VerificationResult:
    # lean: 适配器先做静态检查 (禁用 sorry/admit), 与工具链是否安装无关
    if tool != "lean" and not tool_available(tool):
        return VerificationResult(
            tool=tool, status=VerificationStatus.unavailable,
            detail=f"{tool} 未安装或不可用",
        )
    payload = json.dumps({"tool": tool, "operation": operation, "arguments": arguments})
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "src.verification._worker"],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env=_minimal_env(),
            # 退出码由调用方按科学语义解释 (非零≠命题为假), 因此不做 check 抛错
            check=False,
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(tool=tool, status=VerificationStatus.timeout,
                                  detail=f"{tool} 执行超时 (>{timeout}s)")
    except Exception as e:  # noqa: BLE001
        return VerificationResult(tool=tool, status=VerificationStatus.error,
                                  detail=f"执行器异常: {e}")
    out = (proc.stdout or "").strip()
    if not out:
        return VerificationResult(tool=tool, status=VerificationStatus.error,
                                  detail=f"无输出 (exit={proc.returncode}): {(proc.stderr or '')[-500:]}")
    try:
        data = json.loads(out.splitlines()[-1])
    except json.JSONDecodeError:
        return VerificationResult(tool=tool, status=VerificationStatus.error,
                                  detail=f"输出解析失败: {out[-500:]}")
    try:
        result = VerificationResult.model_validate(data)
    except Exception as e:  # noqa: BLE001
        return VerificationResult(tool=tool, status=VerificationStatus.error,
                                  detail=f"结果 schema 无效: {e}")
    if result.raw_output and proc.stderr:
        result.raw_output = (result.raw_output + "\n" + proc.stderr)[-4000:]
    return result


class VerificationRunner:
    """按工具名路由的验证执行器。

    inproc=True 时在同进程内直接调用适配器 (仅用于测试/低开销场景);
    默认走独立子进程, 读取真实退出码与输出。
    """

    def __init__(self, timeout: float = 30.0, inproc: bool = False):
        self.timeout = timeout
        self.inproc = inproc

    def run(self, tool: str, operation: str, arguments: dict[str, Any],
            timeout: float | None = None) -> VerificationResult:
        if self.inproc:
            return _run_inproc(tool, operation, arguments)
        return run_request(tool, operation, arguments, timeout=timeout or self.timeout)

    def sympy(self, operation: str, arguments: dict[str, Any]) -> VerificationResult:
        return self.run("sympy", operation, arguments)

    def z3(self, operation: str, arguments: dict[str, Any]) -> VerificationResult:
        return self.run("z3", operation, arguments)


def _run_inproc(tool: str, operation: str, arguments: dict[str, Any]) -> VerificationResult:
    if tool != "lean" and not tool_available(tool):
        return VerificationResult(tool=tool, status=VerificationStatus.unavailable,
                                  detail=f"{tool} 未安装或不可用")
    try:
        if tool == "sympy":
            from src.verification import sympy_adapter as adapter
        elif tool == "z3":
            from src.verification import z3_adapter as adapter
        elif tool == "lean":
            from src.verification import lean_adapter as adapter
        elif tool == "stats":
            from src.verification import stats_adapter as adapter
        else:
            return VerificationResult(tool=tool, status=VerificationStatus.unsupported,
                                      detail=f"未知工具 {tool}")
        return adapter.run(operation, arguments)
    except Exception as e:  # noqa: BLE001
        return VerificationResult(tool=tool, status=VerificationStatus.error,
                                  detail=f"适配器异常: {e}")


def available_tools() -> dict[str, bool]:
    return {t: tool_available(t) for t in ("sympy", "z3", "stats", "lean")}
