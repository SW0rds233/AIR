from __future__ import annotations

"""唯一装配入口: 角色模型、离线开关、装配口径。"""

import os
from typing import Any

__all__ = ["ROLE_MODEL_KEYS", "metered_llm", "offline_requested", "role_llm_factory"]

#: 角色 -> 模型配置键 (见 `config.build_llm`)。未登记角色用主模型。
ROLE_MODEL_KEYS: dict[str, str] = {
    "writing": "main",
    "review": "reviewer",
    "reasoning": "theorist",
    "modeling": "theorist",
    "validation": "verifier",
    "evidence": "cheap",
    "figures": "main",
    "supervisor": "coordinator",
    "coordinator": "coordinator",
}


def offline_requested() -> bool:
    """是否显式要求离线 (`THEORY_LLM=0`)。"""
    return os.getenv("THEORY_LLM", "").strip() == "0"


def role_llm_factory(stage: str = "") -> Any:
    """按角色取一个模型实例 (离线时返回 None)。

    取模型失败时抛错而不是返回 None: 见模块文档最后一条。
    """
    from src.agents.registry import AGENT_ROLES  # noqa: F401  (导入即校验角色表)

    if offline_requested():
        return None
    from src.config import build_llm

    key = ROLE_MODEL_KEYS.get(str(stage or ""), "main")
    return build_llm(key)


def metered_llm(model: Any, usage_record: Any, *, stage: str = "supervisor") -> Any:
    """把模型包成"调用即计入 `usage_record`"的形态 (实现复用 cost_tracker 的那一份)。

    为什么需要它: 主控的模型调用不发生在任何 `AgentTask` 里, 因此运行时的任务级记账
    看不到它们 —— 结果是"这次运行有没有真的调用模型"在用量里显示为 0, 而审计正是用
    `llm_calls` 判断这一点的。

    网关不返回 usage 时如实记 `unknown_parts`, 不用 0 冒充"没花 token"。
    """
    from src.utils.cost_tracker import MeteredLLM

    def _on_usage(*, model: str = "", usage: dict | None = None,
                  stage: str = "") -> None:
        usage_record.llm_calls += 1
        if model and model not in usage_record.models:
            usage_record.models.append(model)
        if usage:
            usage_record.input_tokens += int(usage.get("input_tokens", 0) or 0)
            usage_record.output_tokens += int(usage.get("output_tokens", 0) or 0)
        elif "token_usage" not in usage_record.unknown_parts:
            usage_record.unknown_parts.append("token_usage")

    return MeteredLLM(model, _on_usage, stage=stage)
