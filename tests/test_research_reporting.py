from __future__ import annotations

"""工作台只读投影与交付引用 (计划书 §4)。

`reporting.py` 是计划书要求的深模块: 输入明确的 project/problem 与已过滤的对象,
输出稳定的工作台投影。这里测它的**契约**, 而不是重新跑一遍研究:

- 计数只有一处口径, 且每个字段都必须存在 (前端把缺失字段显示成 0);
- 投影字段与前端读取的键一一对应; `coverage_notes` 必须把"还缺什么"说出来;
- 实验建议按问题过滤: 引用别的问题命题的建议不得出现;
- 只读: 投影不得改动任何研究对象或结论状态。
"""

import pytest

from src.research import reporting




def test_count_contract_is_complete_and_uses_one_source():
    """F0-3: 计数字段必须齐全, 且服务端只保留一个口径实现。"""
    from src import server

    counts = reporting.objects_counts([], [], [], [], models=[], routes=[])
    assert set(counts) == set(reporting.COUNT_KEYS)
    assert all(value == 0 for value in counts.values())
    # server 的同名入口必须委托 (不是第二份实现)
    assert server.objects_counts([], [], [], []) == counts
    assert "reporting.objects_counts" in (server.objects_counts.__doc__ or "")


def test_counts_reflect_object_statuses():
    from src.research.schemas import (
        Claim,
        ClaimStatus,
        ProofObligation,
        VerificationRecord,
    )

    claims = [
        Claim(statement="a", status=ClaimStatus.supported, assurance="symbolic_checked",
              support_kind="symbolic_check", coverage="target",
              validation_status="verified"),
        Claim(statement="b", status=ClaimStatus.refuted),
        Claim(statement="c", status=ClaimStatus.proposed),
    ]
    obligations = [
        ProofObligation(statement="o1", status="open"),
        ProofObligation(statement="o2", status="closed"),
    ]
    records = [VerificationRecord(id="v1", tool="sympy", status="passed")]
    counts = reporting.objects_counts(claims, obligations, [], records,
                                      models=[object()], routes=[object()])
    assert counts["claims"] == 3
    assert counts["claims_supported"] == 1
    assert counts["claims_refuted"] == 1
    assert counts["claims_open"] == 1
    assert counts["obligations_open"] == 1
    assert counts["obligations_closed"] == 1
    assert counts["verifications"] == 1
    assert counts["models"] == 1 and counts["routes"] == 1


def test_coverage_notes_report_open_and_blocked_work():
    counts = dict.fromkeys(reporting.COUNT_KEYS, 0)
    counts.update({"claims": 2, "obligations_open": 3, "obligations_blocked": 1,
                   "evidence": 1, "verifications": 1})
    notes = reporting.coverage_notes(counts, metrics={"anomalies": ["x"],
                                                      "stopped_reason": "动作数超限"},
                                     novelty=[{"claim_id": "c"}])
    joined = " ".join(notes)
    assert "3 条义务未关闭" in joined
    assert "1 条义务受阻" in joined
    assert "日志契约异常" in joined
    assert "预算停止" in joined
    # 已有结论/证据/验证/对照时不应再报空缺
    assert "尚无结论" not in joined
    assert "不得据此宣称原创" not in joined


def test_experiments_from_other_problem_are_excluded():
    specs = [{"id": "exp-mine", "claim_id": "clm-1", "title": "我的"},
             {"id": "exp-foreign", "claim_id": "clm-2", "title": "别人的"}]
    kept = reporting.experiments_payload(specs, foreign_claim_ids={"clm-2"})
    assert [item["id"] for item in kept] == ["exp-mine"]


def test_claims_payload_carries_uncovered_factors():
    from src.research.schemas import Claim, ClaimType, StudyPlan

    causal = Claim(statement="A 影响 Y", claim_type=ClaimType.causal,
                   study=StudyPlan(design="did", data_source_kind="synthetic"))
    payload = reporting.claims_payload([causal], "p1")[0]
    assert payload["problem_id"] == "p1"
    assert "识别假设未列出" in payload["not_covered"]
    assert any("非现实数据" in item for item in payload["not_covered"])
    assert payload["conditions"] == []


def test_steps_payload_only_includes_owned_claims():
    attempts = [
        {"target_claim_id": "clm-1", "steps": [{"index": 0, "statement": "s0"}]},
        {"target_claim_id": "clm-2", "steps": [{"index": 0, "statement": "s1"}]},
    ]
    steps = reporting.steps_payload(attempts, owned=lambda cid: cid == "clm-1")
    assert [s["id"] for s in steps] == ["clm-1:0"]


@pytest.mark.parametrize("key", reporting.COUNT_KEYS)
def test_every_count_key_is_an_int(key):
    counts = reporting.objects_counts([], [], [], [])
    assert isinstance(counts[key], int)
