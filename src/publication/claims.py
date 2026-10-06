"""Shared wording guard: a proof assertion is not a verification certificate."""
import re

_PROOF_ASSERTION = re.compile(
    r"我们证明|已(?:经)?(?:完成|完整)?证明|已被完整证明|完整证明了|严格证明|证明了|归约成立|"
    r"必定成立|必然成立|\bqed\b|we (?:have )?(?:prove[d]?|establish)|has been proven", re.I)
_NEGATION = re.compile(r"(?:尚未|未能|不能|不得|不声称|并非|不是|不代表|没有).{0,10}$", re.I)
_REQUIREMENT = re.compile(r"(?:须|需|应|必须|要求|请)\s*(?:给出|提供|完成)?\s*$")


def asserts_completed_proof(text: str) -> bool:
    for clause in re.split(r"[。；;\n]", str(text or "")):
        for match in _PROOF_ASSERTION.finditer(clause):
            prefix = clause[:match.start()]
            if not _NEGATION.search(prefix) and not _REQUIREMENT.search(prefix):
                return True
    return False
