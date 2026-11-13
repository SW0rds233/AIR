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


def evidence_targets(claim) -> dict[str, list[str]]:
    """Separate empirical premises from algebra; targets establish no support."""
    data = claim.model_dump() if hasattr(claim, "model_dump") else claim
    links = list(data.get("mechanism_links") or []) + mechanism_links(data.get("statement", ""))
    parameters = list(data.get("evidence_requirements") or [])
    if data.get("lhs") or data.get("rhs") or data.get("variable_domains"):
        parameters += list(data.get("premises") or [])
    return {"mechanism_link": list(dict.fromkeys(links))[:8],
            "parameter_assumption": list(dict.fromkeys(parameters))[:12]}
