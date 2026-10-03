from __future__ import annotations

"""知识卡片抽取: 定义/定理/引理/命题/假设/方法/数据/结果/局限。

每条卡片保留原文定位 (页/节) 与置信度; 只做结构化索引, 不替代原文。
中文与英文模式均支持; 关键词型卡片 (结果/局限) 置信度较低, 便于人工复核。
"""

import hashlib
import re

from src.kb.parse import ParsedDoc
from src.kb.schema import Card, CardType

_HEADER_PATTERNS: list[tuple[CardType, re.Pattern]] = [
    (CardType.definition, re.compile(r"^\s*(定义|Definition)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：.]?\s*(.*)$", re.IGNORECASE)),
    (CardType.theorem, re.compile(r"^\s*(定理|Theorem)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：.]?\s*(.*)$", re.IGNORECASE)),
    (CardType.lemma, re.compile(r"^\s*(引理|Lemma)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：.]?\s*(.*)$", re.IGNORECASE)),
    (CardType.proposition, re.compile(r"^\s*(命题|Proposition)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：.]?\s*(.*)$", re.IGNORECASE)),
    (CardType.corollary, re.compile(r"^\s*(推论|Corollary)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：.]?\s*(.*)$", re.IGNORECASE)),
    (CardType.assumption, re.compile(r"^\s*(假设|Assumption|Hypothesis)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：.]?\s*(.*)$", re.IGNORECASE)),
]

_KEYWORD_PATTERNS: list[tuple[CardType, re.Pattern]] = [
    (CardType.result, re.compile(r"(结果表明|结果显示|实验表明|研究发现|本文发现|we find|results show|we observe)", re.IGNORECASE)),
    (CardType.method, re.compile(r"(本文提出|我们提出|该方法|算法流程|we propose|our method|框架|framework)", re.IGNORECASE)),
    (CardType.dataset, re.compile(r"(数据集|数据来自|样本量|公开数据|dataset|we collected|sample of)", re.IGNORECASE)),
    (CardType.limitation, re.compile(r"(局限性|不足之处|未来工作|有待进一步|limitation|future work)", re.IGNORECASE)),
]

_SYMBOL = re.compile(r"(?<![A-Za-z])([A-Za-z])(?![A-Za-z])")


def _card_id(doc_id: str, card_type: CardType, locator: str, text: str) -> str:
    basis = f"{doc_id}|{card_type.value}|{locator}|{text[:60]}"
    return "card-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def extract_cards(doc_id: str, parsed: ParsedDoc, max_cards: int = 200) -> list[Card]:
    cards: list[Card] = []
    for section in parsed.sections:
        lines = section.text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            matched = None
            for card_type, pattern in _HEADER_PATTERNS:
                m = pattern.match(line)
                if m:
                    label = (m.group(1) or card_type.value).strip()
                    number = (m.group(2) or "").strip()
                    first = (m.group(3) or "").strip()
                    body = [first] if first else []
                    j = i + 1
                    while j < len(lines) and lines[j].strip() and not any(
                        p.match(lines[j]) for _, p in _HEADER_PATTERNS
                    ):
                        body.append(lines[j].strip())
                        j += 1
                    text = " ".join(x for x in body if x).strip()
                    if text:
                        locator = f"p{section.page} / {label} {number}".strip()
                        cards.append(Card(
                            card_id=_card_id(doc_id, card_type, locator, text),
                            doc_id=doc_id, card_type=card_type, text=text[:2000],
                            locator=locator, page=section.page, heading=section.heading,
                            symbols=list(dict.fromkeys(_SYMBOL.findall(text)))[:10],
                        ))
                    matched = card_type
                    i = j
                    break
            if matched:
                continue
            i += 1

        # 关键词型卡片 (按句抽取, 低置信度)
        for card_type, pattern in _KEYWORD_PATTERNS:
            for sent in re.split(r"(?<=[。.!?！？])\s*", section.text):
                if pattern.search(sent) and 12 <= len(sent.strip()) <= 500:
                    text = sent.strip()
                    locator = f"p{section.page} / {section.heading or card_type.value}"
                    cards.append(Card(
                        card_id=_card_id(doc_id, card_type, locator, text),
                        doc_id=doc_id, card_type=card_type, text=text,
                        locator=locator, page=section.page, heading=section.heading,
                        confidence=0.6,
                        symbols=list(dict.fromkeys(_SYMBOL.findall(text)))[:10],
                    ))
                    if len(cards) >= max_cards:
                        return cards
    # 去重 (同类型同文本)
    seen: set[str] = set()
    unique: list[Card] = []
    for card in cards:
        key = f"{card.card_type.value}|{card.text[:80]}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique[:max_cards]
