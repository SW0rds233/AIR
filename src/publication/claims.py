"""Shared wording guard: a proof assertion is not a verification certificate."""
import re


def anchored_science_findings(blocks, issues):
    """Locate an open substantive correction by its original quote in the named block."""
    by_id = {block.block_id: "".join((block.text + "\n" + block.math).split()) for block in blocks}
    matches = []
    for issue in issues:
        if (issue.get("category") != "science" or issue.get("severity") not in {"major", "blocking"}
                or issue.get("repair_action") != "revise_text"
                or issue.get("status", "open") not in {"open", "not_rechecked"}):
            continue
        quote = "".join(str(issue.get("quoted_text") or "").split())
        if not quote:
            continue
        for ref in issue.get("affected_refs") or []:
            block_id = ref.get("id") if isinstance(ref, dict) else None
            if block_id in by_id and quote in by_id[block_id]:
                matches.append((block_id, issue))
    return matches


_PROOF_ASSERTION = re.compile(
    r"我们证明|已(?:经)?(?:完成|完整)?证明|已被完整证明|完整证明了|严格证明|证明了|归约成立|"
    r"必定成立|必然成立|\bqed\b|we (?:have )?(?:prove[d]?|establish)|has been proven", re.I)
_NEGATION = re.compile(
    r"(?:尚未|未能|不能|不得|不声称|不作为|不视作|不视为|不算作|不作|并非|不是|不代表|没有|尚无|未见|缺乏|"
    r"不(?:应|宜|能|可)?(?:写作|表述为|称为|记为|当作|视为))"
    r"[^，,。；;\n但却而]{0,80}$", re.I)
_REQUIREMENT = re.compile(r"(?:须|需|(?<![效反响适对供])应|必须|要求|请)\s*(?:给出|提供|完成)?\s*$")


def asserts_completed_proof(text: str) -> bool:
    for clause in re.split(r"[。；;\n，,]", str(text or "")):
        for match in _PROOF_ASSERTION.finditer(clause):
            prefix = clause[:match.start()]
            if match.group() in {"必定成立", "必然成立"} and re.search(r"不(?:再)?\s*$", prefix):
                continue
            if not _NEGATION.search(prefix) and not _REQUIREMENT.search(prefix):
                return True
    return False
