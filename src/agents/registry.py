from __future__ import annotations

"""角色注册表: 契约 + **真实可用能力** (合并计划 §5 / §8)。

为什么注册表不能只写一份静态清单
--------------------------------
合并计划 §5 明确要求 `registry.py` 注册"角色与**真实可用**能力"。只声明
"EvidenceAgent 能检索"没有意义 —— 检索后端可能没配 key、没有向量库、没有
只读数据源。于是界面会显示"检索中"而实际什么都没发生 (系统已有的现场缺陷之一
就是"工具不可用却伪造检索完成")。

因此这里的每个能力都要经过一个**探测函数**: 探测说不可用, 注册表就如实报
不可用并给出原因, 而不是把静态清单当事实。
"""

from collections.abc import Callable
from typing import Any

from src.agents.protocol import (
    AGENT_CAPABILITIES,
    AGENT_ROLES,
    ROLE_LABELS,
    AgentContract,
    role_of,
)

__all__ = [
    "ROLE_CONTRACTS",
    "available_capabilities",
    "contract_for",
    "describe_team",
    "probe",
    "team_roles",
]


# ----------------------------------------------------------------------
# 能力探测 (只回答"现在能不能用", 不触发任何研究动作)
# ----------------------------------------------------------------------
def _probe_search() -> tuple[bool, str]:
    """外部检索是否可用: 需要网络配置; 无 key 时 arXiv 仍可用但如实标注。"""
    try:
        from src.tools import search_tools
    except Exception as e:  # noqa: BLE001
        return False, f"检索模块不可导入: {e}"
    fn = getattr(search_tools, "arxiv_search", None)
    if fn is None:
        return False, "检索后端未提供 arxiv_search"
    return True, ""


def _probe_vector() -> tuple[bool, str]:
    try:
        from src.rag.vector_store import embedding_available

        if embedding_available():
            return True, ""
        return False, "未配置 embedding, 只能关键词+卡片召回"
    except Exception as e:  # noqa: BLE001
        return False, f"向量后端不可用: {e}"


def _probe_pdf() -> tuple[bool, str]:
    """PDF 解析探测: 只判断后端可导入, 不打开任何文件。"""
    try:
        import pymupdf  # noqa: F401
    except Exception:  # noqa: BLE001
        try:
            import fitz  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return False, f"PDF 解析不可用: {e}"
    return True, ""


def _probe_readonly_data() -> tuple[bool, str]:
    try:
        from src.kb.adapters.readonly_data import authorized_roots

        roots = authorized_roots()
    except Exception as e:  # noqa: BLE001
        return False, f"只读数据适配器不可用: {e}"
    if not roots:
        return False, "没有授权读取根目录"
    return True, ""


def _probe_math() -> tuple[bool, str]:
    missing: list[str] = []
    try:
        import sympy  # noqa: F401
    except Exception:  # noqa: BLE001
        missing.append("sympy")
    try:
        import z3  # noqa: F401
    except Exception:  # noqa: BLE001
        missing.append("z3")
    if len(missing) == 2:
        return False, "符号/约束求解后端均不可用"
    if missing:
        return True, f"缺少 {', '.join(missing)}: 部分命题类型无法核验"
    return True, ""


def _probe_stats() -> tuple[bool, str]:
    try:
        from src.verification import stats_adapter

        if hasattr(stats_adapter, "run"):
            return True, ""
        return False, "统计适配器缺少统一入口 run()"
    except Exception as e:  # noqa: BLE001
        return False, f"统计适配器不可用: {e}"


def _probe_figure() -> tuple[bool, str]:
    try:
        import matplotlib  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, f"绘图后端不可用: {e}"
    return True, ""


def _probe_latex() -> tuple[bool, str]:
    """LaTeX 引擎探测: 用 PATH 上的常见引擎名判断, 不真的编译。"""
    import shutil

    for engine in ("xelatex", "pdflatex", "tectonic"):
        if shutil.which(engine):
            return True, ""
    return False, "未找到 LaTeX 引擎 (xelatex/pdflatex/tectonic): 只能产出 .tex, 不能编译 PDF"


#: 可探测能力: 名字 -> 探测函数。`tools:*` 读范围映射到这里。
_CAPABILITY_PROBES: dict[str, Callable[[], tuple[bool, str]]] = {
    "tools:search": _probe_search,
    "tools:pdf": _probe_pdf,
    "tools:vector": _probe_vector,
    "tools:readonly_data": _probe_readonly_data,
    "tools:math": _probe_math,
    "tools:stats": _probe_stats,
    "tools:figure": _probe_figure,
    "tools:latex": _probe_latex,
}


def probe(capability: str) -> tuple[bool, str]:
    """探测单个能力。未登记的能力返回 `(False, 原因)` —— 不默认放行。"""
    fn = _CAPABILITY_PROBES.get(capability)
    if fn is None:
        return False, f"未登记的能力 {capability!r}"
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - 探测失败本身不是致命错误
        return False, f"探测 {capability!r} 失败: {e}"


# ----------------------------------------------------------------------
# 角色契约
# ----------------------------------------------------------------------
def _contract(role: str, objective: str, deliverables: tuple[str, ...],
              need_kinds: tuple[str, ...], *, may_request_downgrade: bool = False,
              ) -> AgentContract:
    return AgentContract(
        agent=role, label=ROLE_LABELS.get(role, role), objective=objective,
        deliverables=list(deliverables), need_kinds=list(need_kinds),
        may_change_conclusion=bool(
            AGENT_CAPABILITIES.get(role, {}).get("may_change_conclusion", False)),
        may_request_downgrade=may_request_downgrade,
    )


#: 七个功能角色 + 主控的职责声明 (与合并计划 §3.1 的表逐条对应)。
ROLE_CONTRACTS: dict[str, AgentContract] = {
    "supervisor": _contract(
        "supervisor",
        "理解研究目标、拆子问题、安排依赖与预算、委派、验收与重新规划",
        ("ResearchBrief", "TeamPlan", "AgentTask", "SupervisorDecision", "交付说明"),
        ("clarification",),
    ),
    "evidence": _contract(
        "evidence",
        "自主决定查询、资料源、阅读与扩展检索; 覆盖文献/案例/数据",
        ("EvidenceBundle",),
        ("more_sources", "source_locator", "reader_source", "clause", "figure_data"),
    ),
    "modeling": _contract(
        "modeling",
        "提取变量、量纲、域、约束、机制与假设, 比较候选模型",
        ("ModelProposal",),
        ("model_condition",),
    ),
    "reasoning": _contract(
        "reasoning",
        "分解义务、推导、查反例、综合文献/案例并回答问题",
        ("ReasoningResult",),
        ("counterexample", "derivation"),
    ),
    "validation": _contract(
        "validation",
        "判断什么验证能区分解释, 产出仿真/实验实现建议 (标为未执行)",
        ("ValidationPlan",),
        ("empirical_support",),
    ),
    "writing": _contract(
        "writing",
        "设计大纲、写正文、按审阅意见局部修订",
        ("Manuscript", "WritingGap"),
        ("manuscript_revision",),
    ),
    "figures": _contract(
        "figures",
        "按论证需要选图、绘图、修正图文一致性",
        ("FigureSpec", "FigureArtifact"),
        ("figure_data",),
    ),
    # 审阅是唯一可以**要求降级**的角色; 它仍然不能自己改写结论等级。
    "review": _contract(
        "review",
        "从原始任务、成果与证据独立审阅科学逻辑、忠实度、引文、可读性与图文一致性",
        ("ReviewReport", "ReviewIssue"),
        ("review",),
        may_request_downgrade=True,
    ),
}


def contract_for(agent: str) -> AgentContract | None:
    role = role_of(agent)
    if not role:
        return None
    return ROLE_CONTRACTS.get(role)


def team_roles() -> tuple[str, ...]:
    """参与团队执行的角色 (含主控)。"""
    return AGENT_ROLES


def available_capabilities(agent: str) -> dict[str, Any]:
    """该角色**当前**真实可用的读范围/工具, 附不可用原因。

    返回值形如 `{"agent", "available": [...], "unavailable": [{"capability", "reason"}]}`。
    界面/主控据此决定派什么任务 —— 例如向量库不可用时不应承诺"语义检索"。
    """
    contract = contract_for(agent)
    if contract is None:
        return {"agent": agent, "available": [], "unavailable": [
            {"capability": agent or "(空)", "reason": "未登记的角色"}]}
    available: list[str] = []
    unavailable: list[dict[str, str]] = []
    for cap in contract.reads:
        if not cap.startswith("tools:"):
            # 数据范围类 (sources/fulltext/objects/...) 由资料权限决定, 不做进程级探测
            available.append(cap)
            continue
        ok, reason = probe(cap)
        if ok:
            available.append(cap)
        else:
            unavailable.append({"capability": cap, "reason": reason})
    return {
        "agent": contract.agent,
        "label": contract.label,
        "objective": contract.objective,
        "deliverables": list(contract.deliverables),
        "need_kinds": list(contract.need_kinds),
        "tools": list(contract.tools),
        "may_change_conclusion": contract.may_change_conclusion,
        "may_request_downgrade": contract.may_request_downgrade,
        "available": available,
        "unavailable": unavailable,
    }


def describe_team(*, probe_capabilities: bool = True) -> list[dict[str, Any]]:
    """整队的能力视图 (界面与验收使用)。

    `probe_capabilities=False` 时只返回契约, 不触发任何导入/探测 (用于离线文档).
    """
    out: list[dict[str, Any]] = []
    for role in AGENT_ROLES:
        if probe_capabilities:
            out.append(available_capabilities(role))
            continue
        contract = ROLE_CONTRACTS[role]
        out.append({
            "agent": role, "label": contract.label, "objective": contract.objective,
            "deliverables": list(contract.deliverables),
            "need_kinds": list(contract.need_kinds),
            "tools": list(contract.tools),
            "may_change_conclusion": contract.may_change_conclusion,
            "may_request_downgrade": contract.may_request_downgrade,
            "available": list(contract.reads), "unavailable": [],
        })
    return out
