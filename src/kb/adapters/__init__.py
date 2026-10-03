from __future__ import annotations

"""数据源适配器 (计划书 §6.3)。

当前提供:
- `readonly_data`: 用户 CSV / SQLite 的**只读**描述与查询 (路径授权、只允许
  SELECT、表名白名单、行/列上限、快照 hash)。

后续若要接入文献型资料源, 只在这里新增适配器, 由 `KnowledgeService`
统一暴露检索/读取接口 (不增加只传参的代理层)。
"""

from src.kb.adapters.readonly_data import (
    QueryResult,
    SourceSchema,
    describe_source,
    load_rows,
    query_sqlite,
    query_table,
    validate_select,
)

__all__ = [
    "QueryResult",
    "SourceSchema",
    "describe_source",
    "load_rows",
    "query_sqlite",
    "query_table",
    "validate_select",
]
