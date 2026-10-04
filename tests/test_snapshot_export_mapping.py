from __future__ import annotations

"""交付包导出映射的回归测试 (§3.2 G11)。

审计探针 `audit_probes.py` 复现了三件事, 都在这里固定:
1. `_model_of()` import 了一个**不存在**的 `ModelRecord`, 异常又被上层裸 `except`
   吞掉 —— "模型一个都没进包"完全无声;
2. `_evidence_of()` 把 `locator` 当不成 `location`、`relation` 当不成 `support`,
   并 `pop` 掉 `version` —— 实测定位为空、支持关系退化成 insufficient、版本回到 1;
3. 快照只汇集 evidence/claim/model, 义务、验证方案等引用闭包缺失。

判据不是"字段名对不对", 而是**数据有没有丢**: 能不能反查定位、支持关系是否如实、
版本是否保留、以及丢行时有没有留下可见的理由。
"""

import pytest

from src.graph.team_session import _snapshot_from_store
from src.research.snapshot import (
    EVIDENCE_FIELD_ALIASES,
    SUPPORT_RELATION_MAP,
    _evidence_of,
    _model_of,
    _validation_plan_of,
)


# --------------------------------------------------------------------------
# 1. 领域映射: 字段名不同不等于数据可以丢
# --------------------------------------------------------------------------
def test_evidence_locator_becomes_location():
    """存储行的 `locator` 必须落到 schema 的 `location` (否则无法反查原文)。"""
    row = {"id": "e1", "title": "source", "locator": "page 9", "version": 7}
    evidence = _evidence_of(row)
    assert evidence.location == "page 9", "定位丢失: 交付包里回不到原文"


def test_evidence_relation_becomes_support():
    """`relation` 必须落到 `support`, 且取值映射正确 (不能退化成 insufficient)。"""
    supports = _evidence_of({"id": "e1", "title": "t", "relation": "supports"})
    assert supports.support.value == "supports"
    refutes = _evidence_of({"id": "e2", "title": "t", "relation": "refutes"})
    assert refutes.support.value == "contradicts"
    # 缺省仍是 insufficient —— 不能把"没写"当成"支持"
    unknown = _evidence_of({"id": "e3", "title": "t"})
    assert unknown.support.value == "insufficient"


def test_evidence_version_is_preserved_not_reset():
    """版本必须保留: 交付包说自己引用的是哪一版是硬要求。"""
    evidence = _evidence_of({"id": "e1", "title": "t", "version": 7})
    assert evidence.version == 7, "版本被重置: 交付包引用了错误的版本"


def test_model_mapping_uses_the_real_schema_class():
    """模型映射必须用真实存在的类 (曾 import 不存在的 `ModelRecord`)。"""
    from src.research.schemas import ResearchModel

    model = _model_of({"id": "m1", "version": 3, "name": "M",
                       "natural_language": "说明"})
    assert isinstance(model, ResearchModel)
    assert model.version == 3


def test_alias_table_is_not_stale():
    """差异表里的每个存储名都必须是真实存在的字段名 (否则它只是过期的猜测)。"""
    assert EVIDENCE_FIELD_ALIASES["locator"] == "location"
    assert EVIDENCE_FIELD_ALIASES["relation"] == "support"
    assert SUPPORT_RELATION_MAP["supports"] == "supports"


# --------------------------------------------------------------------------
# 2. 引用闭包: 快照不能只装三类对象
# --------------------------------------------------------------------------
class _FakeStore:
    """最小存储替身: 只实现 `list_latest`。"""

    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self._rows = rows

    def list_latest(self, kind: str):
        if kind == "boom":
            raise RuntimeError("存储读取失败")
        return list(self._rows.get(kind, []))


class _FakeTeam:
    project_id = "p"
    problem_id = "q"
    run_id = "r"

    class _Runtime:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def emit(self, name: str, payload: dict) -> None:
            self.events.append((name, payload))

    def __init__(self) -> None:
        self.runtime = self._Runtime()


def test_snapshot_collects_the_reference_closure():
    """义务/假设/定义/验证方案都要进快照, 不只是 evidence/claim/model。"""
    store = _FakeStore({
        "evidence": [{"id": "e1", "title": "t", "locator": "p1"}],
        "claim": [{"id": "c1", "statement": "s"}],
        "model": [{"id": "m1", "name": "M", "natural_language": "n"}],
        "obligation": [{"id": "o1", "statement": "需证明"}],
        "assumption": [{"id": "a1", "statement": "假设"}],
        "definition": [{"id": "d1", "symbol": "T", "statement": "意思"}],
        "validation_plan": [{"id": "v1", "purpose": "验证"}],
    })
    snapshot = _snapshot_from_store(store, _FakeTeam())
    assert [item.id for item in snapshot.evidence] == ["e1"]
    assert [item.id for item in snapshot.obligations] == ["o1"]
    assert [item.id for item in snapshot.assumptions] == ["a1"]
    assert [item.id for item in snapshot.definitions] == ["d1"]
    assert snapshot.experiment_specs, "验证方案没有进快照"
    # 方案明确标为未执行: 本轮不运行仿真/实验
    assert snapshot.experiment_specs[0]["executed"] is False
    assert snapshot.experiment_specs[0]["status"] == "proposed"


def test_validation_plan_is_never_marked_executed():
    plan = _validation_plan_of({"id": "v1", "purpose": "x"})
    assert plan["executed"] is False


def test_unmappable_row_is_reported_not_silently_dropped():
    """坏数据可以被跳过, 但必须留下"哪一条、为什么"。"""
    from src.research.schemas import GapType

    store = _FakeStore({"evidence": [
        {"id": "good", "title": "t"},
        {"id": "bad", "title": "t", "support": "不是合法取值"},
    ]})
    team = _FakeTeam()
    snapshot = _snapshot_from_store(store, team)
    assert [item.id for item in snapshot.evidence] == ["good"]
    # 缺口列表里能看到被跳过的那条与原因
    gaps = [gap for gap in snapshot.gaps
            if gap.gap_type == GapType.encoding_mismatch]
    assert gaps, f"跳过没有留下记录: {snapshot.gaps}"
    text = " ".join(f"{gap.statement} {gap.target_ref.id}" for gap in gaps)
    assert "bad" in text and "support" in text, text
    # 并且发了事件 (界面/日志能看到导出不完整)
    assert any(name == "snapshot_export_incomplete" for name, _ in team.runtime.events)


def test_store_read_failure_is_reported_not_treated_as_empty():
    """某类对象读不出来 (存储故障) 与"这类对象为空"必须区分。"""
    store = _FakeStore({})
    store._rows["obligation"] = []
    team = _FakeTeam()

    class _FailingStore(_FakeStore):
        def list_latest(self, kind: str):
            if kind == "evidence":
                raise RuntimeError("库损坏")
            return super().list_latest(kind)

    snapshot = _snapshot_from_store(_FailingStore({}), team)
    text = " ".join(f"{gap.statement} {gap.target_ref.id}" for gap in snapshot.gaps)
    assert "库损坏" in text, f"读取失败被当成空数据: {snapshot.gaps}"


def test_empty_store_still_returns_an_honest_snapshot():
    """什么对象都没有时也要给快照 (交付包是"未决报告", 不是"没跑过")。"""
    snapshot = _snapshot_from_store(_FakeStore({}), _FakeTeam())
    assert snapshot is not None
    assert snapshot.run_id == "r"
    assert not snapshot.evidence and not snapshot.claims


@pytest.mark.parametrize("kind", ["evidence", "claim", "model", "obligation"])
def test_each_kind_maps_without_throwing(kind):
    """每类对象的最小合法行都能映射 (不抛异常 = 不会被静默跳过)。"""
    from src.research import snapshot as snapshot_module

    samples = {
        "evidence": {"id": "x", "title": "t"},
        "claim": {"id": "x", "statement": "s"},
        "model": {"id": "x", "name": "M", "natural_language": "n"},
        "obligation": {"id": "x", "statement": "s"},
    }
    mappers = {
        "evidence": snapshot_module._evidence_of,
        "claim": snapshot_module._claim_of,
        "model": snapshot_module._model_of,
        "obligation": snapshot_module._obligation_of,
    }
    assert mappers[kind](samples[kind]) is not None
