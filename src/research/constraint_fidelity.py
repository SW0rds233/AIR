from __future__ import annotations

"""Conservative checks for explicit, named numerical constraints.

This is not a general semantic verifier. It only blocks a candidate that assigns
an explicit value to the *same named parameter* differently from the input.
Conditional variants are left to research review rather than silently rewritten.
"""

import re


_VARIANT = re.compile(r"若|如果|假设|改用|另取|替换为|变体")
_TUPLE = re.compile(r"\(\s*n\s*,\s*M\s*,\s*d\s*\)\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)
_NAMED_VALUE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]{0,20})\s*(?:=|:|：)\s*"
    r"(-?\d+(?:\.\d+)?)([%°℃A-Za-z]+)?"
)


def _first_int(text: str, *patterns: str) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


def code_parameters(problem: str) -> dict[str, int]:
    """Read explicit values for n, M and d without guessing the research field."""
    problem = problem or ""
    pairwise_distance = bool(re.search(r"两两|pairwise", problem, re.I) and
                             re.search(r"距离|distance", problem, re.I))
    values = {
        "n": _first_int(problem,
                        r"(?:码长|序列长度)\s*(?:n\s*)?(?:为|是|=|:|：)\s*(\d+)",
                        r"(?<![A-Za-z])n\s*(?:=|:|：)\s*(\d+)(?!\d)",
                        r"\\?\{0,1\\?\}\s*\^\s*\{?(\d+)\}?",
                        *(r"长度\s*(?:为|是|=|:|：)\s*(\d+)",) if pairwise_distance else ()),
        "M": _first_int(problem,
                        r"(?:码字数|码字数量)\s*(?:M\s*)?(?:为|是|=|:|：)\s*(\d+)",
                        r"(?<![A-Za-z])M\s*(?:=|:|：)\s*(\d+)(?!\d)",
                        r"(\d+)\s*条\s*(?:长度|二进制|序列|码字)"),
        "d": _first_int(problem,
                        r"(?:Hamming|汉明)?\s*距离\s*(?:恒为|为|是|=|:|：)?\s*(\d+)",
                        r"(?<![A-Za-z])d\s*(?:=|:|：)\s*(\d+)(?!\d)",
                        r"d_H\s*\([^)]*\)\s*=\s*(\d+)"),
    }
    tuple_match = _TUPLE.search(problem)
    if tuple_match:
        values.update(zip(("n", "M", "d"), (int(raw) for raw in tuple_match.groups())))
    return {name: value for name, value in values.items() if value is not None}


def conflicting_code_parameters(problem: str, statement: str) -> list[str]:
    """Return explicit contradictions, never infer truth from agreement.

    The public name is retained for existing callers.  Subject words are not
    evidence that two numerical constraints refer to the same parameter.
    """
    expected = code_parameters(problem)
    issues: list[str] = []
    labels = {"n": "码长", "M": "码字数", "d": "距离"}
    observed: dict[str, int | None] = {
        "n": _first_int(statement, r"(?:码长|序列长度)\s*(?:n\s*)?(?:=|为|是|:|：)\s*(\d+)",
                        r"(?<![A-Za-z])n\s*(?:=|:|：)\s*(\d+)(?!\d)"),
        "M": _first_int(statement, r"(?:码字数|码字数量)\s*(?:M\s*)?(?:=|为|是|:|：)\s*(\d+)",
                        r"(?<![A-Za-z])M\s*(?:=|:|：)\s*(\d+)(?!\d)"),
        "d": _first_int(statement, r"(?:Hamming|汉明)?\s*距离\s*(?:d\s*)?(?:=|为|是|恒为|:|：)\s*(\d+)",
                        r"(?<![A-Za-z])d\s*(?:=|:|：)\s*(\d+)(?!\d)"),
    }
    tuple_match = _TUPLE.search(statement)
    if tuple_match:
        for name, raw in zip(("n", "M", "d"), tuple_match.groups()):
            observed[name] = int(raw)
    for name, value in observed.items():
        if value is None or name not in expected or value == expected[name]:
            continue
        # A clearly marked alternative case is not a misquotation of the input.
        role_start = tuple_match.start() if tuple_match else statement.find(labels[name])
        if role_start < 0:
            role_start = next((m.start() for m in re.finditer(r"(?<![A-Za-z])" + name + r"\s*(?:=|:|：)", statement, re.I)), 0)
        if _VARIANT.search(statement[max(0, role_start - 20):role_start]):
            continue
        issues.append(f"{labels[name]}冲突：题面 {expected[name]}，候选写成 {value}")
    # Shared symbolic assignments (RH=40, t=24, k=7, etc.) are compared by
    # exact identifier.  This is independent of a subject glossary and does
    # not infer relationships between differently named quantities.
    explicit: dict[str, set[tuple[str, str]]] = {}
    for name, value, unit in _NAMED_VALUE.findall(problem or ""):
        explicit.setdefault(name, set()).add((value, unit))
    for match in _NAMED_VALUE.finditer(statement or ""):
        name, value, unit = match.groups()
        unit = unit or ""
        if name in ("n", "M", "d") or len(explicit.get(name, ())) != 1:
            continue
        expected_value, expected_unit = next(iter(explicit[name]))
        if unit != expected_unit or value == expected_value:
            continue  # Unknown unit conversion or multiple allowed input values.
        if _VARIANT.search(statement[max(0, match.start() - 20):match.start()]):
            continue
        issues.append(f"参数 {name} 冲突：题面 {expected_value}{expected_unit}，"
                      f"候选写成 {value}{unit or ''}")
    return issues
