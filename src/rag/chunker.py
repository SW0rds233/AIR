from __future__ import annotations

"""文本分块器：将论文全文切成适合向量检索的块

参考: LangChain TextSplitter / RecursiveCharacterTextSplitter 的思路
"""



def _cjk_ratio(text: str) -> float:
    """中文占比 (bge 系模型 1 个中文字符≈1 token, 需按比例降块大小)"""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(text)


def chunk_text_offsets(text: str, chunk_size: int = 500,
                       overlap: int = 100) -> list[tuple[int, int]]:
    """与 `chunk_text` 相同的切分逻辑, 但返回每块在 `text` 中的 (start, end) 偏移。

    偏移是**精确字符范围**, 用于把片段定位回原文 (计划书 §6.1-3:
    片段应记录实际起止页/字符范围)。`start` 已对齐到 `chunk_text` 里 strip 之后
    的首字符, 因此 `text[start:end] == chunk_text(...)[i]`。
    """
    stripped = text.strip()
    if not stripped:
        return []
    # strip() 会改变坐标, 先把偏移换算回原串
    leading = len(text) - len(text.lstrip())

    # 与 chunk_text 完全一致的块大小调整顺序 (中文为主时降到 400)
    local_size = chunk_size
    if _cjk_ratio(stripped) > 0.3 and chunk_size >= 500:
        local_size = max(400, int(chunk_size * 0.8))

    if len(stripped) <= local_size:
        return [(leading, leading + len(stripped))]

    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(stripped):
        end = min(start + local_size, len(stripped))
        if end < len(stripped):
            search_start = max(start + local_size // 2, end - 200)
            sentence_end = max(
                [stripped.rfind(sep, search_start, end)
                 for sep in ("\n", "。", ". ", "; ", "；")]
            )
            if sentence_end > search_start:
                end = min(sentence_end + 1, start + local_size + 100)

        piece = stripped[start:end]
        # 与 chunk_text 一致: 去掉块首尾空白, 偏移随之收缩
        piece_lstrip = piece.lstrip()
        piece_start = start + (len(piece) - len(piece_lstrip))
        piece_rstrip = piece_lstrip.rstrip()
        piece_end = piece_start + len(piece_rstrip)
        if piece_rstrip:
            spans.append((leading + piece_start, leading + piece_end))
        start = max(end - overlap, start + local_size // 2)
        if start >= len(stripped):
            break
    return spans


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """将长文本切成重叠块

    Args:
        text: 原始文本
        chunk_size: 每块字符数 (默认 500: 适配 bge 等 embedding 模型的
            512 token 输入上限)。**中文占比>30% 时自动降到 400 字符**——
            纯中文 1 字符≈1 token, 500 中文字符+标题前缀可能超 512 token,
            硅基流动会返回 400 (code 20015)。
        overlap: 相邻块重叠字符数
    """
    text = text.strip()
    if not text:
        return []

    # 中文为主时降低块大小 (bge-large-zh 上限 512 token)
    if _cjk_ratio(text) > 0.3 and chunk_size >= 500:
        chunk_size = max(400, int(chunk_size * 0.8))

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))

        # 尽量在句子边界（. ! ? ；\n）处断开
        if end < len(text):
            search_start = max(start + chunk_size // 2, end - 200)
            sentence_end = max(
                [text.rfind(sep, search_start, end) for sep in ("\n", "。", ". ", "; ", "；")]
            )
            if sentence_end > search_start:
                # 硬上限: 句子边界扩展不得把块顶过 512 token 安全线
                end = min(sentence_end + 1, start + chunk_size + 100)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = max(end - overlap, start + chunk_size // 2)

        # 防死循环保护
        if start >= len(text):
            break

    return chunks


def chunk_text_with_offsets(
    text: str, chunk_size: int = 500, overlap: int = 100
) -> list[tuple[str, int, int]]:
    """返回 [(片段, start, end)], 供需要**可回溯定位**的入库路径使用。"""
    chunks = chunk_text(text, chunk_size, overlap)
    spans = chunk_text_offsets(text, chunk_size, overlap)
    out: list[tuple[str, int, int]] = []
    for idx, piece in enumerate(chunks):
        if idx < len(spans):
            start, end = spans[idx]
        else:  # 极端情况下的兜底: 用逐块查找近似定位
            start = text.find(piece)
            end = start + len(piece) if start >= 0 else -1
        out.append((piece, start, end))
    return out


def chunk_paper_fulltext(
    title: str, full_text: str, chunk_size: int = 500, overlap: int = 100
) -> list[dict]:
    """将论文全文切成带元数据的块

    Returns:
        [{"title": ..., "chunk_index": i, "text": ..., "char_start": s, "char_end": e}, ...]
    """
    chunks = chunk_text_with_offsets(full_text, chunk_size, overlap)
    return [
        {
            "title": title,
            "chunk_index": i,
            "text": c,
            "char_start": start,
            "char_end": end,
        }
        for i, (c, start, end) in enumerate(chunks)
    ]
