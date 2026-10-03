from __future__ import annotations

"""P0-4 引文硬约束: 没有可精确定位的引文, 就不许把关系判成支持或反对。

验收口径来自计划书: 故意删掉引文、改掉原文前提时, 支持关系必须失效并显示缺口。
"""

import json
from types import SimpleNamespace

from src.research.evidence import assess_support, build_support_judge, quote_is_locatable
from src.research.schemas import Claim, ClaimType, SourceEvidence, SupportKindOfEvidence

EXCERPT = ("在低信噪比区间, 噪声使特征分布重叠, 因此不同发射机的可分性随信道变化而下降; "
           "高信噪比下该效应减弱。")


def _claim() -> Claim:
    from src.research.schemas import StudyPlan

    return Claim(statement="信道变化使射频指纹可分性下降", claim_type=ClaimType.causal,
                 variables=["snr", "separability"],
                 study=StudyPlan(treatment="信道变化", outcome="可分性"))


def _evidence(**over) -> SourceEvidence:
    base = {"id": "ev-1", "title": "低信噪比下的指纹可分性", "source_id": "doc-1",
            "excerpt": EXCERPT, "location": "§3.2 p.5", "page": 5,
            "source_kind": "journal"}
    base.update(over)
    return SourceEvidence(**base)


def _judge(relation: str, quote: str):
    payload = json.dumps({"relation": relation, "reason": "示例理由", "quote": quote},
                         ensure_ascii=False)
    llm = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=payload))
    return build_support_judge(llm)


def test_quote_is_locatable_requires_text_and_locator():
    item = _evidence()
    assert quote_is_locatable(item, "噪声使特征分布重叠")
    assert not quote_is_locatable(item, "")                       # 空引文
    assert not quote_is_locatable(item, "短")                     # 过短
    assert not quote_is_locatable(item, "原文里没有的句子内容")     # 定位失效
    assert not quote_is_locatable(_evidence(location="", page=0),
                                  "噪声使特征分布重叠")             # 无页/节


def test_rule_judge_never_produces_strong_support():
    item = assess_support(_claim(), _evidence())
    assert item.support is SupportKindOfEvidence.partially_supports
    assert item.reviewer == "rule"
    # 规则判定没有引文: 不得把摘要开头当成依据
    assert item.support_evidence == ""
    assert "候选支持" in item.support_reason


def test_rule_judge_contradiction_is_a_candidate_only():
    item = assess_support(_claim(), _evidence(excerpt="信道一致时不同发射机的可分性提升"))
    assert item.support is SupportKindOfEvidence.contradicts
    assert "待核反例候选" in item.support_reason
    assert item.support_evidence == ""


def test_llm_support_requires_locatable_quote():
    item = _judge("supports", "噪声使特征分布重叠")(_claim(), _evidence())
    assert item.support is SupportKindOfEvidence.supports
    assert item.support_evidence == "噪声使特征分布重叠"
    assert item.reviewer == "llm"


def test_empty_quote_is_downgraded_to_pending():
    item = _judge("supports", "")(_claim(), _evidence())
    assert item.support is SupportKindOfEvidence.insufficient
    assert "待审" in item.support_reason or "待审" in item.notes
    assert item.support_evidence == ""


def test_quote_that_cannot_be_located_is_downgraded():
    item = _judge("supports", "这句话不在原文里, 属于编造引文")(_claim(), _evidence())
    assert item.support is SupportKindOfEvidence.insufficient
    assert "降为待审" in item.support_reason
    assert "原判定=supports" in item.notes, item.notes


def test_missing_page_locator_blocks_strong_relation():
    judge = _judge("contradicts", "噪声使特征分布重叠")
    item = judge(_claim(), _evidence(location="", page=0))
    assert item.support is SupportKindOfEvidence.insufficient
    assert "定位" in item.support_reason


def test_deleting_the_quote_invalidates_the_relation():
    """验收: 删掉引文后同一判定不得再算作支持。"""
    judge = _judge("supports", "噪声使特征分布重叠")
    kept = judge(_claim(), _evidence())
    deleted = judge(_claim(), _evidence())
    assert kept.support is SupportKindOfEvidence.supports

    judge_without_quote = _judge("supports", "")
    deleted = judge_without_quote(_claim(), _evidence())
    assert deleted.support is not SupportKindOfEvidence.supports


def test_changing_the_premise_invalidates_the_quote():
    """验收: 原文被改到引文对不上时, 关系降为待审。"""
    judge = _judge("supports", "噪声使特征分布重叠")
    changed = _evidence(excerpt=EXCERPT.replace("噪声使特征分布重叠", "量化误差被完全消除"))
    item = judge(_claim(), changed)
    assert item.support is SupportKindOfEvidence.insufficient
    assert "原判定=supports" in item.notes


def test_gate_requires_locatable_quote_for_textual_support():
    """以原文支持的强结论必须有可定位引文, 否则交付门槛拦下。"""
    from src.research.acceptance import theory_validity_gate
    from src.research.schemas import (
        Assurance,
        ClaimStatus,
        Coverage,
        ResearchSnapshot,
        SupportKind,
        ValidationStatus,
    )

    claim = Claim(statement="信道变化使可分性下降", claim_type=ClaimType.causal,
                  status=ClaimStatus.supported, assurance=Assurance.informal_reviewed,
                  support_kind=SupportKind.textual_support, coverage=Coverage.target,
                  validation_status=ValidationStatus.verified)
    bare = SourceEvidence(id="ev-1", claim_id=claim.id, excerpt=EXCERPT, location="p.5",
                          support=SupportKindOfEvidence.supports, support_evidence="")
    gate = theory_validity_gate(
        [claim], [], [], [],
        snapshot=ResearchSnapshot(project_id="p", claims=[claim], evidence=[bare]))
    assert any("缺少可定位引文" in r for r in gate.reasons), gate.reasons

    quoted = bare.model_copy(update={"support_evidence": "噪声使特征分布重叠"})
    gate2 = theory_validity_gate(
        [claim], [], [], [],
        snapshot=ResearchSnapshot(project_id="p", claims=[claim], evidence=[quoted]))
    assert not any("缺少可定位引文" in r for r in gate2.reasons), gate2.reasons
