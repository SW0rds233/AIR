from __future__ import annotations

"""KB 命令行: 入库 / 统计 / 检索 / 卡片查看。

用法:
    python -m src.kb ingest --topic "射频指纹识别"
    python -m src.kb ingest --topic "射频指纹识别" --machine machine_papers.json
    python -m src.kb stats --topic "射频指纹识别"
    python -m src.kb search --topic "射频指纹识别" "深度学习 特征提取"
    python -m src.kb cards --topic "射频指纹识别" --doc-id doc-xxxx
"""

import argparse
import json
import sys

from src.utils.console import ensure_utf8_console


def _ingest(args) -> int:
    from src.kb.ingest import ensure_topic, ingest_machine, ingest_manual

    ensure_topic(args.topic)
    report = ingest_manual(args.topic, embed=not args.no_embed)
    print(f"人工导入: 新增 {len(report['ingested'])} 篇, 合并 {len(report['merged'])} 篇")
    for item in report["ingested"] + report["merged"]:
        print(f"  - [{item['doc_id']}] {item['title'][:50]} (质量: {item.get('quality','')}, "
              f"卡片: {item.get('cards', '-')})")
    if args.machine:
        with open(args.machine, encoding="utf-8") as fh:
            papers = json.load(fh)
        mreport = ingest_machine(args.topic, papers, embed=not args.no_embed)
        print(f"机器检索: 新增 {len(mreport['ingested'])} 篇, 合并 {len(mreport['merged'])} 篇")
    return 0


def _stats(args) -> int:
    from src.kb.retrieve import stats

    print(json.dumps(stats(args.topic), ensure_ascii=False, indent=2))
    return 0


def _search(args) -> int:
    from src.kb.retrieve import search

    hits = search(args.topic, args.query, k=args.k, use_vector=not args.no_vector)
    if not hits:
        print("无命中")
        return 0
    for hit in hits:
        if hit.get("kind") == "card":
            print(f"[卡片/{hit.get('card_type')}] {hit.get('text','')[:100]}")
            print(f"        定位: {hit.get('locator','')}  (rrf={hit.get('rrf_score')})")
        else:
            print(f"[文献] {hit.get('title','')} ({hit.get('year','')})")
            print(f"       {hit.get('text','')[:100]}")
    return 0


def _cards(args) -> int:
    from src.kb.store import KBStore

    store = KBStore(args.topic)
    cards = store.get_cards(args.doc_id, card_types=args.types.split(",") if args.types else None)
    for card in cards:
        print(f"[{card['card_type']}] {card['locator']}: {card['text'][:120]}")
    print(f"共 {len(cards)} 张卡片")
    return 0


def main() -> int:
    ensure_utf8_console()
    parser = argparse.ArgumentParser(description="主题文献知识底座 (KB)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="导入人工文献 / 机器检索结果")
    p_ing.add_argument("--topic", required=True)
    p_ing.add_argument("--machine", default="", help="机器检索结果 JSON 文件")
    p_ing.add_argument("--no-embed", action="store_true", help="跳过向量入库")
    p_ing.set_defaults(func=_ingest)

    p_stats = sub.add_parser("stats", help="查看主题底座统计")
    p_stats.add_argument("--topic", required=True)
    p_stats.set_defaults(func=_stats)

    p_search = sub.add_parser("search", help="主题内混合检索")
    p_search.add_argument("--topic", required=True)
    p_search.add_argument("query")
    p_search.add_argument("--k", type=int, default=8)
    p_search.add_argument("--no-vector", action="store_true")
    p_search.set_defaults(func=_search)

    p_cards = sub.add_parser("cards", help="查看知识卡片")
    p_cards.add_argument("--topic", required=True)
    p_cards.add_argument("--doc-id", default="")
    p_cards.add_argument("--types", default="", help="逗号分隔: theorem,definition,...")
    p_cards.set_defaults(func=_cards)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
