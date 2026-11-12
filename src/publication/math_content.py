"""Normalize model-supplied math without putting prose inside math mode."""
import re

_DELIMITED = re.compile(r'\$\$(.*?)\$\$|\\\[(.*?)\\\]|(?<!\\)\$([^$]*?)(?<!\\)\$|\\\((.*?)\\\)', re.S)


def math_parts(value: str) -> list[tuple[bool, str]]:
    text = (value or '').strip()
    if not text:
        return []
    matches = list(_DELIMITED.finditer(text))
    if not matches:
        return [(True, text)]
    parts = []
    cursor = 0
    for match in matches:
        prose = text[cursor:match.start()].strip()
        if prose:
            parts.append((False, prose))
        body = next(group for group in match.groups() if group is not None).strip()
        if body:
            parts.extend(math_parts(body))
        cursor = match.end()
    if text[cursor:].strip():
        parts.append((False, text[cursor:].strip()))
    return parts


def normalize_manuscript_math(manuscript) -> None:
    for section in manuscript.sections:
        normalized = []
        for block in section.blocks:
            parts = math_parts(block.math)
            if not parts:
                normalized.append(block)
                continue
            for index, (is_math, content) in enumerate(parts):
                item = block if index == 0 else block.model_copy(deep=True, update={
                    'block_id': f'{block.block_id}-part-{index + 1}', 'text': ''})
                prior = block.text if index == 0 else ''
                item.math = content if is_math else ''
                item.text = prior if is_math else '\n'.join(filter(None, [prior, content]))
                normalized.append(item)
        section.blocks = normalized
