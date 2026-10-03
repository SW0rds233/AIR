"""从模型/外部文本里稳健地取出结构化数据 (全库唯一实现)。

为什么单独成模块: `derivation.py` 与 `proposal.py` 各有一份**逐字相同**的
`_loads_json_object` (归一化后完全一致)。两份实现意味着"容错规则"有两个真相源 ——
修了一处、另一处仍按旧规则解析, 而这类解析失败是静默的 (返回 None 后调用方通常
退化为默认值), 很难在测试里发现。这里收敛为一份。
"""

from __future__ import annotations

import json
import re

__all__ = ["loads_json_object"]


def loads_json_object(text: str) -> dict | None:
    """从模型输出里取出一个 JSON 对象; 容忍 ```json 围栏与前后解释文字。

    解析策略由宽到严: 先直接解析 (去掉围栏), 失败再从文本里抓第一个 `{...}` 块。
    两层都失败返回 None —— 调用方据此退化为默认行为, 不得抛异常。
    """
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
    return data if isinstance(data, dict) else None
