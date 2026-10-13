from __future__ import annotations

"""数据源适配器。"""

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
