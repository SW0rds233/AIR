"""Separate the research method from a claim's semantic type."""
from __future__ import annotations

import re
from typing import Any


def _study(claim: Any) -> dict:
    value = claim.get("study", {}) if isinstance(claim, dict) else claim.study
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value or {})


def has_empirical_context(claim: Any) -> bool:
    study = _study(claim)
    if study.get("design", "none") not in ("none", "theory", ""):
        return True
    return any(study.get(key) for key in (
        "rows", "data_ref", "data_table", "population", "region", "period",
        "treatment", "outcome", "measurement_notes", "treatment_col", "outcome_col"))


def is_theoretical_claim(claim: Any) -> bool:
    if has_empirical_context(claim):
        return False
    if _study(claim).get("design") == "theory":
        return True
    kind = claim.get("claim_type", "") if isinstance(claim, dict) else claim.claim_type.value
    strategy = claim.get("strategy", "") if isinstance(claim, dict) else claim.strategy
    # Do not erase an empirical assertion merely because it mentions a matrix.
    return kind in ("definitional", "descriptive") and strategy in ("derivation", "proof")


def is_formal_question(text: str) -> bool:
    """Natural-language mathematical tasks need not contain a symbolic '='."""
    body = str(text or "").lower()
    mathematical = re.search(
        r"矩阵|定理|整数|二进制序列|二元序列|汉明|hamming|等距码|多项式|不等式|"
        r"matrix|matrices|theorem|polynomial|binary (?:code|sequence)|integer|finite group", body)
    intent = re.search(r"是否存在|能否存在|存在性|不存在|证明|推导|构造|等价|"
                       r"does .*exist|existence|prove|proof|construct|equivalen", body)
    return bool(mathematical and intent)
