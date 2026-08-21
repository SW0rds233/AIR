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


def budget_sections(
    text: str,
    limit: int,
    label: str = "文档",
    keep_tail_sections: tuple[str, ...] = ("参考文献", "References", "结论", "Conclusion"),
) -> str:
    """按章节截断：正文与结论优先完整保留，参考文献列表在极端超长时降级截断。

    重要原则：审稿人必须看到**完整全文**才能正确评分（尤其"参考文献格式/数量/
    预印本占比"等维度）。因此调用方应把 limit 设得足够大（kimi-k2.6 窗口 262K），
    正常情况下本函数**不会触发任何截断**，直接原样返回全文。

    仅当文档远超模型窗口、必须降级时，才按以下优先级截断：
    1. 结论完整保留（通常很短）
    2. 正文完整保留（除非正文本身也超预算，才掐正文中间）
    3. 参考文献列表降级：保留前 N 条，但**明确标注总条数**，
       避免审稿人误以为"文献数量不足"或"格式只有这几种"。
    """
    import re as _re

    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text

    def _find_header(name: str) -> int:
        # 精确匹配 "## 参考文献"(无编号, 实际引用列表) 优先; 否则回退到含该词的任意标题
        m = _re.search(rf"^#{{1,3}}\s*{_re.escape(name)}\s*$", text, _re.M)
        if m is not None:
            return m.start()
        m = _re.search(rf"^#{{1,3}}\s*[^\n]*{_re.escape(name)}[^\n]*$", text, _re.M)
        return m.start() if m is not None else -1

    ref_pos = _find_header("参考文献")
    concl_pos = _find_header("结论")

    if ref_pos < 0 and concl_pos < 0:
        # 找不到关键章节: 退回普通掐头去尾
        return budget_text(text, limit, label=label)

    # 正文结束位置 = 结论/参考文献中较靠前者
    body_end = len(text)
    for pos in (concl_pos, ref_pos):
        if 0 <= pos < body_end:
            body_end = pos

    body = text[:body_end]
    concl = ""
    refs = ""
    if concl_pos >= 0:
        concl_end = ref_pos if ref_pos > concl_pos else len(text)
        concl = text[concl_pos:concl_end]
    if ref_pos >= 0:
        refs = text[ref_pos:]

    # 1) 结论完整保留 (通常 < 2000 字符)
    # 2) 正文尽量完整
    # 3) 参考文献用剩余预算, 保留前 N 条 + 提示
    concl_budget = min(len(concl), 2000)
    body_budget = limit - concl_budget - 200
    if body_budget < 0:
        body_budget = int(limit * 0.7)

    if len(body) <= body_budget:
        # 正文放得下: 剩余预算全给参考文献
        ref_budget = limit - len(body) - len(concl) - 200
        if ref_budget < 0:
            ref_budget = 0
        refs_trimmed = _trim_reference_list(refs, ref_budget, label)
        return body + concl + refs_trimmed

    # 正文本身超预算: 掐正文中间 (保留 head+tail), 参考文献再给少量预算
    head = int(body_budget * 0.5)
    tail_part = body_budget - head
    omitted = len(body) - body_budget
    marker = (
        f"\n\n> [系统提示] 上述{label}正文过长，中间 {omitted} 个字符已被省略，"
        f"省略内容不在本上下文中。"
        f"**不得虚构被省略部分提到的文献、数据或结论**；"
        f"如需引用请只依据下方仍可见的内容。\n\n"
    )
    body_trimmed = body[:head] + marker + body[-tail_part:]
    ref_budget = max(0, limit - len(body_trimmed) - len(concl) - 200)
    refs_trimmed = _trim_reference_list(refs, ref_budget, label)
    return body_trimmed + concl + refs_trimmed


def _trim_reference_list(refs: str, budget: int, label: str) -> str:
    """参考文献列表按预算降级截断：保留前 N 条，明确标注总条数与剩余数量。

    仅在文档极端超长、必须降级时调用。截断时**必须**告知总条数，
    否则审稿人会误判"参考文献数量不足"或"格式仅限前几种"。
    预算 <=0 时至少保留标题行 + 提示总条数。
    """
    if not refs:
        return ""

    # 统计总条数 (参考文献条目行以 [数字] 开头)
    import re as _re

    total = len(_re.findall(r"^\[\d+\]", refs, _re.M))

    if len(refs) <= budget:
        return refs

    # 预算不足时至少保留标题行 + 提示总条数
    if budget <= 0:
        first_line = refs.split("\n", 1)[0]
        return (
            first_line
            + f"\n> [系统提示] 因上下文预算限制，{label}共 {total} 条参考文献未显示。"
            + "**不得虚构未显示条目的内容**。\n"
        )

    # 保留开头 (标题 + 前若干条) 直到接近 budget
    lines = refs.split("\n")
    kept = []
    used = 0
    dropped = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > budget:
            dropped += 1
        else:
            kept.append(line)
            used += cost
    body = "\n".join(kept)
    if dropped:
        body += (
            f"\n> [系统提示] 因上下文预算限制，{label}参考文献共 {total} 条，"
            f"展示前 {total - dropped} 条，另有 {dropped} 条未显示。"
            f"**不得虚构未显示条目的内容**。\n"
        )
    return body


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
