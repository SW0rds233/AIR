from __future__ import annotations

"""主题文献知识底座 (Knowledge Base, KB)。

两个入口、一个权威库:
- 人工入口: data/kb/{topic}/manual/ 下的 PDF/txt/md/docx + meta.json (人可确认存在性);
- 机器入口: arXiv/Semantic Scholar/OpenAlex 等检索结果。

解析与 embedding 解耦: 无向量化时仍保存结构化全文、抽取卡片并支持关键词检索。
权威数据落 SQLite; 向量仅作可重建的检索增强。
"""
