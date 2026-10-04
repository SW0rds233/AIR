from __future__ import annotations

"""新颖性对照服务 (合并计划 §5.4 `_act_compare_novelty` 的能力抽取)。

判定规则本身在内核/`research.novelty`(零 LLM): 检索命中**不等于**结论已被别人做过,
因此 `assess` 只给候选对照行与 `unchecked` 状态, 由人/独立审查确认等价性。

这一层只解决"**拿什么去比**":

- 有可用知识底座 → `kb.bridge.novelty_lookup`(本地库内检索);
- 否则 → `research.retrieval.build_lookup()`(外部文献检索), 并把实际覆盖的资料源
  写进记录 —— 未检索到等价结果 ≠ 世界首次, 必须能说清"比的是哪些范围"。

为什么要抽出来: 这段选择逻辑原本在 `graph/theory_pipeline._novelty_lookup_for` 里,
于是团队路径要么没有新颖性对照, 要么得自己再写一份"用什么比"的判据 —— 后者会让
两条路径给出不同的检索边界, 而"新颖性"恰恰是最不该有第二种口径的结论。
"""

from typing import Any, Callable

__all__ = ["novelty_lookup_for", "assess_novelty_for"]


def novelty_lookup_for(service: Any = None, *, source_policy: str = "") -> tuple[
        Callable[[Any], list] | None, dict]:
    """按可用性选对照来源; 返回 `(lookup, retrieval_scope)`。

    两条来源:
    1. 有可用知识底座 → `kb.bridge.novelty_lookup`(本地库内检索, 不受外搜授权限制);
    2. 否则 → 外部文献检索, **但只在任务授权外搜时** (`autonomous`/`both`)。
       `user_kb` 的任务不得联网 —— 新颖性对照不能成为绕过资料授权的入口。

    两条都不可用时返回 `(None, {})`: 调用方据此落一条 `unchecked` 记录 ——
    **"没比过"必须写下来**, 而不是省略这条记录让交付包看起来没有新颖性问题。
    """
    if service is not None and getattr(service, "usable", False):
        from src.kb.bridge import novelty_lookup

        lookup = novelty_lookup(service)
        return lookup, {"kind": "local_kb",
                        "covered": list(getattr(lookup, "covered_sources", []) or []),
                        "source_set_id": str(getattr(service, "topic", "") or "")}
    if str(source_policy or "").strip() not in ("autonomous", "both"):
        return None, {}
    try:
        from src.research.retrieval import build_lookup

        lookup = build_lookup()
        return lookup, {"kind": "external",
                        "covered": list(getattr(lookup, "covered_sources", []) or [])}
    except Exception:  # noqa: BLE001 - 外部检索不可用时如实返回 None
        return None, {}


def assess_novelty_for(claim: Any, *, service: Any = None,
                       evidence: list[Any] | None = None,
                       evidence_scope: list[str] | None = None,
                       source_policy: str = "") -> tuple[Any, dict]:
    """为一条命题做新颖性对照; 返回 `(NoveltyRecord, retrieval_scope)`。

    **绝不因为"没有检索能力"就跳过**: 那样交付物会看起来"没有新颖性问题";
    没有 lookup 时照常落一条 `unchecked` 记录并写明原因。
    """
    from src.research.novelty import assess

    lookup, scope = novelty_lookup_for(service, source_policy=source_policy)
    record = assess(claim, lookup, evidence=list(evidence or []),
                    evidence_scope=list(evidence_scope or []),
                    retrieval_scope=scope)
    return record, scope
