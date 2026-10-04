from __future__ import annotations

"""研究预算 (合并计划 §7 `runtime/budget.py` / 计划书 §9.3)。

为什么单独成模块
----------------
预算上限既是**执行**的约束 (引擎/团队循环据此停止), 也是**只读呈现**的事实
(工作台要显示"动作 3/40"); 它原本长在 `research/loop.py` 里, 于是"读工作台"这类
只读需求也被迫依赖那个能执行研究的引擎。默认值只有这一处定义, 执行侧与只读侧
取同一个对象。
"""

from dataclasses import dataclass

__all__ = ["ResearchBudget"]


@dataclass
class ResearchBudget:
    """一次研究的资源上限 (0 表示该类不限制)。"""

    max_actions: int = 40
    max_tool_calls: int = 60
    no_progress_limit: int = 3
    max_routes: int = 3
    # ---- 资源预算 (计划书 §9.3): 0 表示不限制 ----
    max_tokens: int = 0
    max_cost_usd: float = 0.0
    max_wall_seconds: float = 0.0

    def exhausted_reason(self, *, actions: int, tool_calls: int, tokens: int,
                         cost_usd: float, elapsed: float) -> str:
        """返回触顶的原因; 未触顶返回空串。"""
        if actions >= self.max_actions:
            return f"动作预算耗尽 ({actions}/{self.max_actions})"
        if tool_calls >= self.max_tool_calls:
            return f"工具调用预算耗尽 ({tool_calls}/{self.max_tool_calls})"
        if self.max_tokens and tokens >= self.max_tokens:
            return f"token 预算耗尽 ({tokens}/{self.max_tokens})"
        if self.max_cost_usd and cost_usd >= self.max_cost_usd:
            return f"费用预算耗尽 (${cost_usd:.4f}/${self.max_cost_usd})"
        if self.max_wall_seconds and elapsed >= self.max_wall_seconds:
            return f"墙钟预算耗尽 ({elapsed:.0f}s/{self.max_wall_seconds:.0f}s)"
        return ""
