from __future__ import annotations

"""定向检索与已有工作对照 (P2)。

当前缺什么就检索什么 (为闭合引理找定理, 为判断新颖性找更一般的已有结果,
为失败路线找已知反例)。检索结果只作为候选证据, 需核对前提后才能使用。
"""

from collections.abc import Callable

from src.research.schemas import Claim, NoveltyComparisonRow


def search_prior_work(
    claim: Claim,
    search_fn: Callable[[str, int], list[dict]] | None = None,
    limit: int = 5,
) -> list[NoveltyComparisonRow]:
    if search_fn is None:
        try:
            from src.tools.search_tools import search_all_sources

            search_fn = search_all_sources
        except Exception:  # noqa: BLE001
            return []
    query = claim.statement
    if claim.lhs and claim.rhs:
        query = f"{claim.lhs} {claim.relation.value} {claim.rhs}"
    try:
        results = search_fn(query, limit)
    except Exception:  # noqa: BLE001
        return []
    rows: list[NoveltyComparisonRow] = []
    for paper in (results or [])[:limit]:
        rows.append(NoveltyComparisonRow(
            result=paper.get("title", ""),
            premises=paper.get("abstract", "")[:200],
            conclusion="",
            applicability=paper.get("venue", paper.get("source", "")),
            method=paper.get("source", ""),
            # 差异**未分析**: 留空, 由 novelty.assess 保持"待比较"而不是判为可能不同
            difference="",
        ))
    return rows


# 外部检索实际覆盖的资料源 (供 NoveltyRecord 说明检索边界)
_DEFAULT_SOURCES = ["arXiv", "Semantic Scholar", "OpenAlex"]


def build_lookup(search_fn: Callable[[str, int], list[dict]] | None = None,
                 limit: int = 5, sources: list[str] | None = None):
    """构造**外部文献**新颖性对照查询 (无本地知识底座时的后备路径)。

    返回的函数带 `covered_sources` 属性, 供 `novelty.assess` 记录检索边界。
    """
    covered = list(sources or _DEFAULT_SOURCES)

    def _lookup(claim: Claim) -> list[NoveltyComparisonRow]:
        return search_prior_work(claim, search_fn=search_fn, limit=limit)

    _lookup.covered_sources = covered          # type: ignore[attr-defined]
    _lookup.kind = "external"                  # type: ignore[attr-defined]
    return _lookup
