from __future__ import annotations

"""CLI / 脚本用的检索薄适配层 (计划书 §6.1)。

**唯一召回路径是 `KnowledgeService.search()`。** 早期这里另有一套独立的
RRF 融合实现, 它绕过了范围/权限过滤、语言与年份约束, 也不返回字符范围与
起止页 —— 于是"工作台看到的候选"与"研究循环实际用的候选"可能不一致。

现在本模块只做三件事:
1. 把 `KnowledgeService` 的 `SourceRef` 转成 CLI/旧调用方习惯的 dict;
2. `stats()` 直接转发到知识底座统计;
3. `describe()` 暴露检索能力自述 (哪些通道可用、是否退化为关键词检索)。

不再有任何独立的关键词/向量/RRF 实现: 融合发生在 `KnowledgeService` 内部
(`rrf_fuse`), 因此**范围过滤与融合排序对所有调用方一致**。
"""

from src.kb.service import KnowledgeService, RetrievalRequest, SourceRef
from src.kb.store import KBStore


def ref_to_dict(ref: SourceRef) -> dict:
    """把统一引用对象转成兼容 dict (字段名与旧实现保持一致)。

    返回的键刻意覆盖旧实现的 `kind/title/text/doc_id/locator/source`, 同时
    补上旧实现没有的定位信息 (字符范围/起止页/年份/可信度), 便于 CLI 展示。
    """
    return {
        "kind": ref.kind,
        "doc_id": ref.source_id,
        "card_id": ref.chunk_id if ref.kind == "card" else "",
        "chunk_id": ref.chunk_id,
        "title": ref.title,
        "text": ref.excerpt,
        "locator": ref.locator,
        "page": ref.page,
        "char_start": ref.char_start,
        "char_end": ref.char_end,
        "card_type": ref.card_type,
        "year": ref.year,
        "doi": ref.doi,
        "doc_type": ref.doc_type,
        "credibility": ref.credibility,
        "channel": ref.channel,
        "score": ref.score,
        "rrf_score": ref.fusion_score,
        "source": ref.channel or "keyword",
        "topic": ref.topic,
    }


class _NoVectorService(KnowledgeService):
    """禁用向量通道的服务包装 (CLI 的 `--no-vector`)。"""

    def _vector_hits(self, query: str, limit: int) -> list[SourceRef]:
        return []


def search(topic: str, query: str, k: int = 8, card_types: list[str] | None = None,
           manual_only: bool = False, use_vector: bool = True,
           store: KBStore | None = None, language: str = "",
           time_range: str = "") -> list[dict]:
    """主题内检索 (委托给 KnowledgeService, 含范围/权限/语言/年份过滤)。

    `use_vector=False` 时只跑关键词与卡片通道; 向量不可用时会自动退化,
    不会因此报错 (可用性优先)。
    """
    return search_detailed(topic, query, k=k, card_types=card_types,
                           manual_only=manual_only, use_vector=use_vector,
                           store=store, language=language,
                           time_range=time_range)["hits"]


def search_detailed(topic: str, query: str, k: int = 8,
                    card_types: list[str] | None = None, manual_only: bool = False,
                    use_vector: bool = True, store: KBStore | None = None,
                    language: str = "", time_range: str = "") -> dict:
    """检索 + 元信息: 命中列表、调用的通道与各通道条数、失败原因、越范围剔除数。

    元信息与命中来自**同一次**检索, 因此不会出现"报告说跑了向量通道但结果里没有"。
    """
    service = KnowledgeService(topic, store=store)
    if not use_vector and service.store is not None:
        service = _NoVectorService(topic, store=service.store)
    request = RetrievalRequest(query=query, max_results=k,
                               card_types=list(card_types or []),
                               manual_only=manual_only, language=language,
                               time_range=time_range,
                               stop_condition="取得可定位引用即可")
    outcome = service.search(request)
    return {
        "hits": [ref_to_dict(ref) for ref in outcome.refs],
        "channels": list(outcome.recall_channels),
        "channel_counts": dict(outcome.channel_counts),
        "dropped_out_of_scope": outcome.dropped_out_of_scope,
        "failures": list(outcome.failures),
        "searched": outcome.searched,
        "fusion": "rrf",
    }


def stats(topic: str, store: KBStore | None = None) -> dict:
    return KnowledgeService(topic, store=store).stats()


def describe(topic: str, store: KBStore | None = None) -> dict:
    """检索能力自述: 哪些通道可用、是否退化、融合方式。"""
    return KnowledgeService(topic, store=store).retrieval_debug()


__all__ = ["describe", "ref_to_dict", "search", "search_detailed", "stats"]
