"""Candidate mechanism links for retrieval; splitting a chain establishes no truth."""
import re


def mechanism_links(statement: str) -> list[str]:
    links = []
    for sentence in re.split(r"[；;。\n]", statement or ""):
        parts = [part.strip() for part in re.split(r"→|⇒|->", sentence)]
        for left, right in zip(parts, parts[1:]):
            if left and right:
                links.append(f"{left[-160:]} → {right[:160]}")
    return list(dict.fromkeys(links))[:8]
