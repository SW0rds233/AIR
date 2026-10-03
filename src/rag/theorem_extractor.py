from __future__ import annotations

"""文献定理卡抽取 (P2)。

从全文解析结果中抽取定义/定理/引理/命题及证明思路, 保留原文定位 (行号/节号)。
模型转述不能替代原文; 拿不到条件时标记证据不完整。
"""

import re
from dataclasses import dataclass

from src.research.schemas import SourceEvidence, SourceKind

_HEADER = re.compile(
    r"^\s*(Theorem|Lemma|Proposition|Corollary|Definition|Assumption|Remark|"
    r"定理|引理|命题|推论|定义|假设|注)\s*([0-9]+(?:\.[0-9]+)*)?\s*[:：.]?\s*(.*)$"
)
_PROOF = re.compile(r"^\s*(Proof|证明)\s*[:：.]?\s*(.*)$")


@dataclass
class TheoremCard:
    kind: str
    number: str
    statement: str
    proof_sketch: str
    location: str


def extract_cards(text: str, max_cards: int = 50) -> list[TheoremCard]:
    lines = (text or "").splitlines()
    cards: list[TheoremCard] = []
    current: TheoremCard | None = None
    collecting_proof = False
    for idx, line in enumerate(lines, start=1):
        m = _HEADER.match(line)
        if m:
            if current is not None:
                cards.append(current)
            current = TheoremCard(kind=m.group(1), number=m.group(2) or "",
                                  statement=m.group(3).strip(), proof_sketch="",
                                  location=f"line {idx}")
            collecting_proof = False
            if len(cards) >= max_cards:
                break
            continue
        pm = _PROOF.match(line)
        if pm and current is not None:
            collecting_proof = True
            current.proof_sketch = pm.group(2).strip()
            continue
        if current is not None:
            if collecting_proof:
                current.proof_sketch = (current.proof_sketch + " " + line.strip()).strip()
            else:
                current.statement = (current.statement + " " + line.strip()).strip()
    if current is not None and len(cards) < max_cards:
        cards.append(current)
    # 丢弃过短/空的卡片 (疑似标题误匹配)
    return [c for c in cards if len(c.statement) >= 8]


def extract_source_evidence(
    text: str,
    literature_id: str,
    title: str = "",
    doi: str = "",
    source_kind: SourceKind = SourceKind.preprint,
    max_cards: int = 50,
) -> list[SourceEvidence]:
    evidence: list[SourceEvidence] = []
    for card in extract_cards(text, max_cards=max_cards):
        excerpt = f"{card.kind} {card.number}: {card.statement}"
        if not card.statement:
            continue
        evidence.append(SourceEvidence(
            literature_id=literature_id,
            title=title,
            doi=doi,
            source_kind=source_kind,
            location=f"{card.location} ({card.kind} {card.number})".strip(),
            excerpt=excerpt[:2000],
            content_supports=bool(card.statement),
        ))
    return evidence
