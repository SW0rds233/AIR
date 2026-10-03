from __future__ import annotations

"""验证请求与结果 schema (P1)。"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    passed = "passed"            # 在声明的域与条件下核验通过
    failed = "failed"            # 找到反例或明确否定
    unknown = "unknown"          # 工具无法判定 (不构成数学反驳)
    unsupported = "unsupported"  # 超出适配器支持片段
    timeout = "timeout"
    error = "error"
    unavailable = "unavailable"  # 工具未安装/版本不可用


class VerificationRequest(BaseModel):
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 30.0


class VerificationResult(BaseModel):
    tool: str = ""
    tool_version: str = ""
    status: VerificationStatus = VerificationStatus.unknown
    raw_output: str = ""
    certificate: str = ""
    counterexample: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)  # 结构化数值结果 (效应量/CI 等)
    detail: str = ""

    @property
    def is_conclusive(self) -> bool:
        return self.status in (VerificationStatus.passed, VerificationStatus.failed)
