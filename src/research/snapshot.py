from __future__ import annotations

"""存储 → 领域快照的**唯一**映射 (合并计划 §3.2 G11 / §6.1)。

为什么单独成模块
----------------
"把团队登记的候选组装成交付快照"这件事此前长在 `graph/team_session.py` 里, 而交付
评估 (`research/delivery.py`) 与团队循环 (`graph/research_graph.py`) 都要用它。重复
一份映射就是重复一份丢字段的机会 —— G11 正是这样丢掉了证据定位与版本的。因此这里
只做映射, 不做判定, 也不依赖任何图/会话对象。

两条**不得静默**的规则 (§3.2 G11):
1. 单条坏数据不阻断整次导出, 但必须记下**是哪条、为什么**;
2. 只汇集 evidence/claim/model 是不够的 —— 义务、验证方案、图表、审阅问题同样属于
   引用闭包, 缺了它们交付包讲不出"结论依赖什么"。
"""

from typing import Any

from src.research.schemas import ResearchSnapshot

__all__ = [
    "EVIDENCE_FIELD_ALIASES",
    "SUPPORT_RELATION_MAP",
    "manuscript_markdown",
    "snapshot_from_store",
]

#: 存储行的字段名与领域 schema 的差异表 (§6.1 "统一领域 schema、显式映射并校验")。
#:
#: 为什么要显式映射而不是直接 `model_validate(row)`: 存储行用的是**投影层写入时的
#: 字段名**, 领域 schema 用的是自己的名字。靠"名字碰巧一样"就会静默丢数据 ——
#: 实测 `locator` 进不了 `location` (定位变空)、`relation` 进不了 `support`
#: (支持关系退化成 insufficient)、`version` 被 `pop` 掉 (版本回到 1)。
EVIDENCE_FIELD_ALIASES: dict[str, str] = {
    "locator": "location",
    "relation": "support",
}
#: 投影层写入的支持关系取值 -> `SourceEvidence.support` 的取值。
SUPPORT_RELATION_MAP: dict[str, str] = {
    "supports": "supports",
    "support": "supports",
    "refutes": "contradicts",
    "contradicts": "contradicts",
    "context": "context",
    "insufficient": "insufficient",
    "": "insufficient",
}


def freeze_snapshot(store: Any, snapshot: Any, *, run_id: str = "") -> str:
    """把**已提交**的对象冻结成一份可引用的快照 (派生/历史对比/交付共用的唯一入口)。

    为什么单独一个函数: 冻结是"给已落盘的对象拍一张不可变照片", **不改任何结论状态**
    (不写命题、不改义务、不新增验证)。因此它不是"第二个判定写入点", 但确实是**写存储**
    的动作 —— 把它收在一处, 写入点清单里就能写清"谁在写、为什么可以写", 而不是散在
    交付/派生/引擎三处各写一遍 (`save_snapshot` 曾经只在引擎与旧流水线里被调用, 于是
    团队运行"看起来冻结了"但对象库里没有快照, 派生接口直接报"未找到可派生的快照")。
    """
    return store.save_snapshot(snapshot, allow_replace=True)


def snapshot_from_store(store: Any, *, project_id: str, problem_id: str, run_id: str,
                        on_skip=None) -> ResearchSnapshot:
    """由存储里的候选组装 `ResearchSnapshot` (只带已登记对象, 不造结论)。

    关键限制: 团队只**提交候选**。因此这里的命题状态就是判定层写入的状态 ——
    未经判定的命题保持 `proposed`, 不冒充已确证。

    即使**什么都还没登记**也要返回快照: 那种情况下交付包本身就是"未决报告"
    (如实写清缺什么、下一步怎么办)。把它当作空包丢掉, 会让"研究没做出来"在界面上
    与"没跑过"无法区分。
    """
    snapshot = ResearchSnapshot(project_id=project_id, problem_id=problem_id,
                                run_id=run_id)
    #: 存储 kind -> (目标字段, 映射函数)
    #
    # **引用闭包必须完整** (§3.2 G11): 只有 evidence/claim/model/obligation 是不够的 ——
    # 缺少 `verification` 时交付门槛会报"结论缺少有效验证记录", 而那条记录其实就存在
    # (实测: 判定层已据它把命题算成 supported, 快照里却是 0 条)。缺 `attempt` 时
    # "已定结论缺少已完成的证明尝试记录"也会误报。因此新增对象种类时**一并加进这张表**,
    # 否则门槛与交付包会与真实研究状态不一致。
    plan = (
        ("evidence", snapshot.evidence, _evidence_of),
        ("evidence_link", snapshot.evidence_links, _evidence_link_of),
        ("claim", snapshot.claims, _claim_of),
        ("model", snapshot.models, _model_of),
        ("obligation", snapshot.obligations, _obligation_of),
        ("verification", snapshot.verifications, _verification_of),
        ("attempt", snapshot.attempts, _attempt_of),
        ("novelty", snapshot.novelty, _novelty_of),
        ("assumption", snapshot.assumptions, _assumption_of),
        ("definition", snapshot.definitions, _definition_of),
        ("validation_plan", snapshot.experiment_specs, _validation_plan_of),
    )
    skipped: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    # **按问题过滤** (R6 / F0-2): 同一个项目里另一个问题的对象绝不能进这份快照 ——
    # 否则交付包里会混入别的问题的结论与验证记录, 门槛也会照着别人的命题判等级
    # (实测: 同项目 p2 的冻结快照里带着 p1 的命题)。
    #
    # 归属读**两条来源** (与只读检视层同一口径): 对象自己的 `problem_id`, 或提交口写入的
    # `_scope.problem_id`。两条都读不到的是旧数据 —— 只有当项目里**没有**别的问题的对象时
    # 才保留 (单问题项目不该因为字段缺失而变成空包)。
    def _owner(row: dict[str, Any]) -> str:
        return str(row.get("problem_id")
                   or (row.get("_scope") or {}).get("problem_id") or "")

    rows_by_kind: dict[str, list[dict[str, Any]]] = {}
    foreign: set[str] = set()
    for kind, _target, _mapper in plan:
        try:
            rows = list(store.list_latest(kind) or [])
        except Exception as e:  # noqa: BLE001 - 某类对象读不出来要如实记, 不是当没有
            skipped.append({"kind": kind, "reason": f"读取失败: {type(e).__name__}: {e}"})
            continue
        rows_by_kind[kind] = rows
        foreign |= {owner for owner in (_owner(row) for row in rows)
                    if owner and owner != problem_id}
    for kind, target, mapper in plan:
        rows = rows_by_kind.get(kind)
        if rows is None:
            continue
        rows = [row for row in rows
                if _owner(row) == problem_id or (not _owner(row) and not foreign)]
        counts[kind] = len(rows)
        for row in rows:
            try:
                target.append(mapper(row))
            except Exception as e:  # noqa: BLE001 - 单条坏数据不阻断导出, 但要可见
                skipped.append({
                    "kind": kind,
                    "object_id": str(row.get("id", "")) or "(无 id)",
                    "reason": f"{type(e).__name__}: {e}",
                })
    if skipped:
        # 写进快照的缺口列表: 交付包必须能回答"有多少登记对象没能进包、为什么"。
        from src.research.schemas import GapType, ObjectRef, ResearchGap

        for item in skipped:
            snapshot.gaps.append(ResearchGap(
                gap_type=GapType.encoding_mismatch,
                target_ref=ObjectRef(id=str(item.get("object_id", "")) or "export"),
                statement=(f"{item['kind']} 对象未能进入交付快照: "
                           f"{item.get('reason', '')}"),
                blocking=["交付包缺少这部分依据"],
                resolving_actions=["修正字段映射或补全该对象的必填字段后重新导出"],
                resolution_criteria="该对象出现在快照对应列表里, 且没有 export 缺口",
            ))
        if on_skip is not None:
            on_skip({"skipped": skipped, "registered": counts})
    # 正文位置映射必须**真的**算出来 (§3.2 G12): 交付门槛用 `writing_map` 判断
    # "核心结论能否在正文里反查到"。留空会让一份明明写了结论的稿件被判成
    # "主要结论未映射到正文位置" —— 那不是内容缺失, 是映射没做。
    snapshot.writing_map = _writing_map_from_store(store)
    return snapshot


def _writing_map_from_store(store: Any) -> dict[str, str]:
    """由最新稿件块的**引用**反推 `claim_id -> 正文锚点` (零 LLM)。"""
    try:
        rows = store.list_latest("manuscript") or []
    except Exception:  # noqa: BLE001 - 读不出来就是没有映射, 门槛会如实报缺
        return {}
    if not rows:
        return {}
    from src.publication.schemas import Manuscript

    row = rows[-1]
    payload = row.get("manuscript") if isinstance(row.get("manuscript"), dict) else row
    try:
        manuscript = Manuscript.model_validate({k: v for k, v in payload.items()
                                                if not k.startswith("_")})
    except Exception:  # noqa: BLE001 - 结构不完整的稿件不参与映射
        return {}
    mapping: dict[str, str] = {}
    for block in manuscript.all_blocks():
        for kind, ref in zip(block.ref_kinds, block.refs):
            if kind == "claim" and ref.id:
                mapping.setdefault(ref.id, f"block:{block.block_id}")
    return mapping


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    """去掉投影层加的元数据字段 (`_` 前缀), **保留** `version`。"""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def manuscript_markdown(store: Any) -> str:
    """取已登记的稿件 Markdown (唯一 Manuscript IR 优先, 退回已有文本)。

    交付门槛判的是"正文有没有如实表达结论", 因此这里必须给出**真实正文**, 而不是
    一句摘要 —— 拿摘要去判门槛会让"正文过短/缺条件"这类问题全部漏过。
    """
    try:
        rows = store.list_latest("manuscript") or []
    except Exception:  # noqa: BLE001 - 读不出来按"没有正文"处理 (门槛会如实报缺)
        return ""
    if not rows:
        return ""
    import json

    row = rows[-1]
    from src.agents.writing import render_markdown
    from src.publication.schemas import Manuscript

    payload = row.get("manuscript") if isinstance(row.get("manuscript"), dict) else row
    try:
        manuscript = Manuscript.model_validate({k: v for k, v in payload.items()
                                                if not k.startswith("_")})
        return render_markdown(manuscript)
    except Exception:  # noqa: BLE001 - 结构不完整时退回已有文本
        text = str(row.get("markdown") or row.get("text") or "")
        if text:
            return text
        return json.dumps(payload, ensure_ascii=False)[:2000]


def _evidence_of(row: dict[str, Any]):
    from src.research.schemas import SourceEvidence

    data = _row_payload(row)
    for stored_name, schema_name in EVIDENCE_FIELD_ALIASES.items():
        if stored_name in data and schema_name not in data:
            data[schema_name] = data.pop(stored_name)
    if "support" in data:
        data["support"] = SUPPORT_RELATION_MAP.get(str(data["support"]).lower(),
                                                   str(data["support"]))
    return SourceEvidence.model_validate(data)


def _claim_of(row: dict[str, Any]):
    from src.research.schemas import Claim

    data = _row_payload(row)
    # 团队提交的是**候选**: 没有判定层结论时一律按 proposed, 不得冒充已确证
    data.setdefault("status", "proposed")
    return Claim.model_validate(data)


def _model_of(row: dict[str, Any]):
    """模型候选 -> `ResearchModel`。

    这里曾 import 一个**不存在**的 `ModelRecord`, 抛出的 `ImportError` 又被上层的
    裸 `except` 吞掉 —— 于是"模型一个都没进包"完全无声 (§3.2 G11)。真实类名是
    `ResearchModel`。
    """
    from src.research.schemas import ResearchModel

    return ResearchModel.model_validate(_row_payload(row))


def _obligation_of(row: dict[str, Any]):
    from src.research.schemas import ProofObligation

    return ProofObligation.model_validate(_row_payload(row))


def _verification_of(row: dict[str, Any]):
    """核验记录 -> `VerificationRecord` (交付门槛据此判断结论有没有依据)。"""
    from src.research.schemas import VerificationRecord

    return VerificationRecord.model_validate(_row_payload(row))


def _attempt_of(row: dict[str, Any]):
    """证明尝试 -> `ProofAttempt` (表达门槛据此判断"证明计划"有没有被当成已证)。"""
    from src.research.schemas import ProofAttempt

    return ProofAttempt.model_validate(_row_payload(row))


def _novelty_of(row: dict[str, Any]):
    """新颖性对照 -> `NoveltyRecord`。

    交付包与出版层按它决定"能不能宣称原创"(`package.py`: 缺记录或状态 `unchecked`
    时必须如实写成局限)。**不收集它**的后果是交付物看起来没有新颖性问题 —— 与
    G11 丢字段同一类错误。
    """
    from src.research.schemas import NoveltyRecord

    return NoveltyRecord.model_validate(_row_payload(row))


def _evidence_link_of(row: dict[str, Any]):
    """证据归属 -> `EvidenceLink` (结论与来源的绑定关系)。"""
    from src.research.schemas import EvidenceLink

    return EvidenceLink.model_validate(_row_payload(row))


def _assumption_of(row: dict[str, Any]):
    from src.research.schemas import Assumption

    return Assumption.model_validate(_row_payload(row))


def _definition_of(row: dict[str, Any]):
    from src.research.schemas import Definition

    return Definition.model_validate(_row_payload(row))


def _validation_plan_of(row: dict[str, Any]) -> dict[str, Any]:
    """验证方案以 dict 形式进入 `experiment_specs` (schema 未定义专门类型)。

    补上 `executed=False`: 本轮**不运行**仿真/实验, 方案不是成功证据 (§4 角色表)。
    """
    data = _row_payload(row)
    data.setdefault("executed", False)
    data.setdefault("status", "proposed")
    return data
