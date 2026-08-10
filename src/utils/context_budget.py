from __future__ import annotations

"""上下文预算工具：所有截断必须带 marker 告知模型"缺了什么、缺多少"

借鉴: OpenAI4S 的 memory_budget / observation budget 设计 —
"被静默省略的内容比从未提供更糟"。模型不知道被截断的内容，就无法判断
"这里是不是证据不足"，容易把省略当不存在，进而编造。

用法:
    budget_text(notes, 20000, label="文献综述素材")
"""


def budget_text(
    text: str,
    limit: int,
    head_ratio: float = 0.5,
    label: str = "文档",
) -> str:
    """保留 head + tail，中间省略，并写入 marker 告知模型省略情况。

    marker 明确三点: 缺了什么（label）、缺多少（字符数）、
    以及"被省略内容不在上下文中，不得推断/不得虚构"。
    """
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text

    head = max(int(limit * head_ratio), 0)
    tail = limit - head
    omitted = len(text) - limit
    marker = (
        f"\n\n> [系统提示] 上述{label}过长，中间 {omitted} 个字符已被省略，"
        f"省略内容不在本上下文中。"
        f"**不得虚构被省略部分提到的文献、数据或结论**；"
        f"如需引用请只依据下方仍可见的内容。\n\n"
    )
    return text[:head] + marker + text[-tail:]


def list_to_budgeted(
    items: list[str],
    total_budget: int,
    label: str = "条目",
) -> str:
    """把多条文本按总预算拼接，超出预算的部分丢弃并注明丢弃数量。

    与 budget_text 不同: 这里是"逐条丢"而非"掐头去尾"，
    因为列表类内容（论文清单/证据段落）中间被掐掉会破坏完整性，
    丢弃时必须让模型知道"还有 N 条没给"。
    """
    kept: list[str] = []
    used = 0
    dropped = 0
    for it in items:
        block = str(it) + "\n"
        if used + len(block) > total_budget:
            dropped += 1
            continue
        kept.append(block)
        used += len(block)

    body = "".join(kept)
    if dropped:
        body += (
            f"\n> [系统提示] 因上下文预算限制，另有 {dropped} 条{label}"
            f"未包含在本上下文中（共 {len(items)} 条，展示 {len(kept)} 条）。"
            f"**不得虚构被省略{label}的内容**。\n"
        )
    return body
