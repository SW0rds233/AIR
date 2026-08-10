from __future__ import annotations

"""文本分块器：将论文全文切成适合向量检索的块

参考: LangChain TextSplitter / RecursiveCharacterTextSplitter 的思路
"""

import re


def _cjk_ratio(text: str) -> float:
    """中文占比 (bge 系模型 1 个中文字符≈1 token, 需按比例降块大小)"""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(text)


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


def chunk_paper_fulltext(
    title: str, full_text: str, chunk_size: int = 500, overlap: int = 100
) -> list[dict]:
    """将论文全文切成带元数据的块

    Returns:
        [{"title": ..., "chunk_index": i, "text": ...}, ...]
    """
    chunks = chunk_text(full_text, chunk_size, overlap)
    return [
        {
            "title": title,
            "chunk_index": i,
            "text": c,
        }
        for i, c in enumerate(chunks)
    ]
