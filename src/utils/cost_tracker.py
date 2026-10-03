from __future__ import annotations

"""LLM 用量与成本追踪

参考: deer-flow 的 /cost 成本估算, ARIS 的 /cost dollars 报告
"""

import logging

from src.config import DEFAULT_MODEL_PRICE, MODEL_PRICES

logger = logging.getLogger(__name__)


def _price_for(model: str) -> dict:
    """按模型名查找价格（支持精确匹配和前缀匹配）"""
    model = model.lower()
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]
    for key, price in MODEL_PRICES.items():
        if model.startswith(key):
            return price
    return dict(DEFAULT_MODEL_PRICE)


def price_source(model: str) -> str:
    """该模型的价格来自价目表还是兜底单价 (费用报告必须能区分)。"""
    from src.config import FALLBACK_MODEL_PRICE_NOTE

    name = (model or "").lower()
    if name in MODEL_PRICES or any(name.startswith(k) for k in MODEL_PRICES):
        return "价目表"
    return FALLBACK_MODEL_PRICE_NOTE


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    """根据 token 用量估算成本 (USD)"""
    price = _price_for(model)
    cost = (input_tokens / 1_000_000) * price["input"]
    cost += (output_tokens / 1_000_000) * price["output"]
    return round(cost, 6)


def extract_usage_metadata(result) -> dict | None:
    """从 langchain LLMResult 提取 usage_metadata

    langchain-openai 返回结果带有 usage_metadata:
    {"input_tokens": N, "output_tokens": N, "total_tokens": N}
    """
    if result is None:
        return None
    try:
        usage = getattr(result, "usage_metadata", None)
        if usage:
            return {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
    except Exception:
        pass
    return None


class UsageTracker:
    """简单的进程内用量累计器"""

    def __init__(self):
        self.calls: list[dict] = []

    def add_call(self, model: str, usage: dict | None, stage: str = "") -> None:
        if not usage:
            return
        cost = estimate_cost(
            model,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )
        record = {
            "stage": stage,
            "model": model,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost_usd": cost,
        }
        self.calls.append(record)
        logger.debug(f"LLM call [{stage}] {model}: {record}")

    def summary(self) -> dict:
        total_input = sum(c["input_tokens"] for c in self.calls)
        total_output = sum(c["output_tokens"] for c in self.calls)
        total_cost = round(sum(c["cost_usd"] for c in self.calls), 4)
        by_stage = {}
        for c in self.calls:
            key = c["stage"] or "unknown"
            if key not in by_stage:
                by_stage[key] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            by_stage[key]["calls"] += 1
            by_stage[key]["input_tokens"] += c["input_tokens"]
            by_stage[key]["output_tokens"] += c["output_tokens"]
            by_stage[key]["cost_usd"] = round(
                by_stage[key]["cost_usd"] + c["cost_usd"], 4
            )
        return {
            "total_calls": len(self.calls),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost_usd": total_cost,
            "by_stage": by_stage,
        }

    def summary_md(self) -> str:
        s = self.summary()
        lines = [
            "# LLM 用量与成本报告",
            "",
            f"- 总调用次数: {s['total_calls']}",
            f"- 输入 tokens: {s['total_input_tokens']}",
            f"- 输出 tokens: {s['total_output_tokens']}",
            f"- 总 tokens: {s['total_tokens']}",
            f"- **总成本: ${s['total_cost_usd']}**",
            "",
            "## 分阶段明细",
            "",
            "| 阶段 | 调用数 | 输入tokens | 输出tokens | 成本($) |",
            "|------|--------|-----------|-----------|---------|",
        ]
        for stage, d in s["by_stage"].items():
            lines.append(
                f"| {stage} | {d['calls']} | {d['input_tokens']} | "
                f"{d['output_tokens']} | {d['cost_usd']} |"
            )
        return "\n".join(lines)


# 模块级单例（供各 agent 共享）
tracker = UsageTracker()
# 模型角色价格只能按模型名查表; 研究循环里同一角色可能换模型, 因此记录时带上实际模型名。


def usage_metadata_of(result) -> dict | None:
    """从 LangChain 返回结果里稳健地取用量信息 (兼容多种字段名)。

    - `usage_metadata` (langchain-openai 新版) 优先;
    - 退回 `response_metadata["token_usage"]` (部分网关/旧版);
    - 都取不到返回 None —— 不让用量统计影响研究本身。
    """
    usage = extract_usage_metadata(result)
    if usage:
        return usage
    try:
        meta = getattr(result, "response_metadata", None) or {}
        token_usage = meta.get("token_usage") or meta.get("usage") or {}
        if token_usage:
            in_tokens = int(token_usage.get("prompt_tokens", 0) or 0)
            out_tokens = int(token_usage.get("completion_tokens", 0) or 0)
            total = int(token_usage.get("total_tokens", 0) or 0) or (in_tokens + out_tokens)
            if total:
                return {"input_tokens": in_tokens, "output_tokens": out_tokens,
                        "total_tokens": total}
    except Exception:  # noqa: BLE001 - 用量缺失不影响主流程
        return None
    return None


class MeteredLLM:
    """包一层 LLM, 把每次调用的用量报给回调 (计划书 §9.3)。

    只做记账: 不改提示词、不改返回值; 取不到用量时静默放行。
    """

    def __init__(self, inner, on_usage, stage: str = "research"):
        self._inner = inner
        self._on_usage = on_usage
        self._stage = stage

    def invoke(self, *args, **kwargs):
        result = self._inner.invoke(*args, **kwargs)
        try:
            usage = usage_metadata_of(result)
            model = getattr(self._inner, "model_name", "") or getattr(self._inner, "model", "")
            if usage:
                self._on_usage(model=str(model or ""), usage=usage, stage=self._stage)
        except Exception:  # noqa: BLE001
            pass
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)
