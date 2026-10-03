from __future__ import annotations

"""Lean 4 形式化适配器 (P4 增强; 未安装时返回 unavailable)。

计划书 §7.2 / §2 P2-A16: **不能只检查编译退出码或扫描源码关键词**。
- 必须先固定并校验目标声明名 (checker 只对指定定理负责);
- 编译后强制执行 `#print axioms <target>`, 审计**传递公理闭包**:
  出现 `sorryAx` 一律拒绝; 出现未在允许清单内的自定义公理一律拒绝;
- 拒绝 `sorry` / `admit` / `unsafe` 等留洞与逃生构造;
- 通过只表示"内核检查了该形式化声明", 仍需人工确认陈述忠实于原问题。
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.verification.schemas import VerificationResult, VerificationStatus

# 禁用构造 (留洞 / 逃生 / 自定义公理声明)
_FORBIDDEN_TOKENS = ("sorryax", "admit", "unsafe ")
_FORBIDDEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])sorry(?![A-Za-z0-9_])", re.IGNORECASE)
_AXIOM_DECL = re.compile(r"^\s*(axiom|constant)\s+([A-Za-z_][A-Za-z0-9_'.]*)", re.MULTILINE)

# Lean 允许的基础公理 (标准三律 + 商 + 函数外延 + 选择公理)。
# 注意: `sorryAx` **不在**允许清单 —— 它是留洞标记, 出现即拒绝。
_ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound", "funext",
                   "Quotient.sound", "choice"}
_BASE_OK = {"propext", "Classical.choice", "Quot.sound", "funext"}


def _result(status, detail="", raw="", certificate="") -> VerificationResult:
    return VerificationResult(tool="lean", tool_version=_version(), status=status, detail=detail,
                              raw_output=raw, certificate=certificate)


def _version() -> str:
    binary = shutil.which("lean")
    if not binary:
        return ""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=15, check=False)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except Exception:  # noqa: BLE001
        return ""


def _target_name(source: str, declared: str) -> str:
    """确定要审计的目标声明名: 显式声明的优先, 否则取源码中最后一个 theorem/lemma。"""
    if declared:
        return declared
    names = re.findall(r"^\s*(?:theorem|lemma|example)\s+([A-Za-z_][A-Za-z0-9_'.]*)",
                       source, re.MULTILINE)
    return names[-1] if names else ""


def _axioms_from_output(output: str) -> list[str]:
    """解析 `#print axioms` 的输出 (形如 "'thm' depends on axioms: [a, b]")。"""
    match = re.search(r"depends on axioms:\s*\[([^\]]*)\]", output)
    if not match:
        return []
    return [a.strip() for a in match.group(1).split(",") if a.strip()]


def op_check_declaration(arguments: dict) -> VerificationResult:
    source = arguments.get("source", "")
    if not source:
        return _result(VerificationStatus.unsupported, "缺少 source")

    allowed_extra = set(arguments.get("allow_axioms") or [])
    target = _target_name(source, str(arguments.get("target", "") or ""))

    # 1) 静态构造检查 (与工具链是否安装无关)
    lowered = source.lower()
    for token in _FORBIDDEN_TOKENS:
        if token in lowered:
            return _result(VerificationStatus.failed, f"源文件含禁用构造: {token.strip()}")
    if _FORBIDDEN_PATTERN.search(source):
        return _result(VerificationStatus.failed, "源文件含禁用构造: sorry")

    # 2) 自定义公理声明必须显式允许 (拒绝"把目标改成公理"式通过)
    declared_axioms = [m.group(2) for m in _AXIOM_DECL.finditer(source)]
    unapproved = [a for a in declared_axioms
                  if a.split(".")[-1] not in _BASE_OK and a not in allowed_extra]
    if unapproved:
        return _result(VerificationStatus.failed,
                       f"源文件声明了未经批准的公理: {', '.join(unapproved)}")

    binary = shutil.which("lean")
    if not binary:
        return _result(VerificationStatus.unavailable, "未安装 Lean 工具链 (P4 增强)")

    # 3) 编译 + 强制打印目标定理的公理闭包
    probe = source
    if target:
        probe = source + f"\n#print axioms {target}\n"
    tmp_dir = Path(tempfile.mkdtemp())
    tmp = tmp_dir / "Check.lean"
    tmp.write_text(probe, encoding="utf-8")
    try:
        proc = subprocess.run([binary, str(tmp)], capture_output=True, text=True,
                              timeout=int(arguments.get("timeout", 120)), cwd=str(tmp_dir),
                              check=False)
    except subprocess.TimeoutExpired:
        return _result(VerificationStatus.timeout, "Lean 检查超时")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return _result(VerificationStatus.failed, "内核检查未通过", out[-2000:])
    if "sorryAx" in out or "sorryAx" in probe:
        return _result(VerificationStatus.failed, "传递公理依赖含 sorryAx", out[-2000:])

    # 4) 公理闭包审计: 必须真正拿到 #print axioms 输出
    if target:
        axioms = _axioms_from_output(out)
        if not axioms and "depends on axioms" not in out:
            return _result(
                VerificationStatus.unknown,
                f"未能取得目标 {target} 的公理闭包输出; 编译通过不等于目标被完整证明",
                out[-2000:])
        unexpected = [a for a in axioms
                      if a.split(".")[-1] not in _BASE_OK and a not in allowed_extra
                      and a.split(".")[-1] not in _ALLOWED_AXIOMS]
        if unexpected:
            return _result(VerificationStatus.failed,
                           f"公理闭包含未批准公理: {', '.join(unexpected)}", out[-2000:])
        cert = f"目标 {target} 公理闭包: [{', '.join(axioms) or '无额外公理'}]"
        return _result(VerificationStatus.passed,
                       "内核检查与公理闭包审计通过 (仍需人工确认陈述忠实性)",
                       out[-2000:], certificate=cert)

    return _result(
        VerificationStatus.unknown,
        "未指定目标声明名, 无法审计公理闭包; 不视为完整形式化证明",
        out[-2000:])


OPERATIONS = {"check_declaration": op_check_declaration}


def run(operation: str, arguments: dict) -> VerificationResult:
    fn = OPERATIONS.get(operation)
    if fn is None:
        return _result(VerificationStatus.unsupported, f"lean 适配器不支持操作 {operation}")
    return fn(arguments)
