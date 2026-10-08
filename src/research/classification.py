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


def uses_population_scope(claim: Any) -> bool:
    """Population wording requires an explicitly registered population scope."""
    study = _study(claim)
    fields = ("scope_population", "scope_region", "scope_period")
    explicit = (claim.get(key) for key in fields) if isinstance(claim, dict) else (
        getattr(claim, key, "") for key in fields)
    return any(explicit) or any(study.get(key) for key in ("population", "region", "period"))


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
    """Recognise a requested proof from its constraints, not its subject vocabulary.

    An ambiguous existence question remains unclassified.  A declared study
    design or claim strategy is authoritative once a claim has been created;
    this text-only fallback is for the intake stage.
    """
    body = str(text or "")
    intent = re.search(r"是否存在|能否存在|存在性|不存在|证明|推导|构造|等价|"
                       r"does .*exist|existence|prove|proof|construct|equivalen", body, re.I)
    if not intent:
        return False
    proof_action = bool(re.search(r"证明|推导|构造|prove|proof|construct", body, re.I))
    constructed_object = bool(re.search(r"这样的|使得|满足|such an|satisfying", body, re.I))
    tuple_values = re.search(r"\(\s*[A-Za-z]\w*(?:\s*,\s*[A-Za-z]\w*){1,}\s*\)\s*=\s*\(", body)
    symbolic_relation = (proof_action or constructed_object) and re.search(
        r"(?<!\w)[A-Za-z]\w*\s*(?:==|=|<=|>=|≤|≥|<|>)\s*(?:[-+]?\d|[A-Za-z])", body)
    quantified_relation = (proof_action or constructed_object or bool(
        re.search(r"两两|pairwise", body, re.I))) and re.search(
        r"(?:任意|所有|两两|forall|for every|pairwise).{0,45}"
        r"(?:为|等于|满足|=)", body, re.I)
    counted_design = len(re.findall(r"(?<!\d)\d+(?:\.\d+)?(?!\d)", body)) >= 2 and bool(
        re.search(r"每|两两|恰好|至多|至少|各|per|each|pairwise", body, re.I)) and bool(
        re.search(r"这样的|满足|such an|satisfying", body, re.I))
    implicit_constraint = bool(re.search(r"给定|规定|given|specified", body, re.I) and
                               re.search(r"两两|任意|所有|pairwise|for every", body, re.I))
    logical_relation = bool(re.search(r"证明|prove|proof", body, re.I) and
                            re.search(r"等价|当且仅当|equivalen|\biff\b", body, re.I))
    return bool(tuple_values or symbolic_relation or quantified_relation or counted_design
                or implicit_constraint or logical_relation)
