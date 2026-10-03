from __future__ import annotations

"""用户数据源的**只读**适配器 (计划书 §3 R0 / §6.3)。

研究的输入不只是文献: 用户会给出结构化数据 (CSV / SQLite)。本模块把这类数据
收敛成一个受限接口, 并在**同一处**实现权限与只读约束:

```text
describe(path, table="") -> {"kind", "schema", "rows", "snapshot_hash", ...}
query(path, sql|table, ...) -> {"columns", "rows", "row_count", "truncated", ...}
```

硬约束 (计划书 §6.3 / §9.4)
--------------------------
1. **路径必须在授权根目录内** (`DATA_DIR` / `OUTPUT_DIR` / `DATA_READ_ROOTS`),
   `data_ref` 可能来自模型输出, 属于不可信输入;
2. **SQLite 一律以 `mode=ro` 打开**, 并且只接受单条 `SELECT`;
   任何写语句 (INSERT/UPDATE/DELETE/ATTACH/PRAGMA 等) 都被拒绝;
3. **表名白名单**: 只允许查询 `describe` 列出的真实表/视图, 不接受拼接出来的表名;
4. **行/列上限**: 任何查询都返回 `truncated` 标记, 不把整库塞进上下文;
5. **快照 hash**: 记录读取时的文件摘要, 结果可复现 —— 文件变了 hash 就变。

本模块只做"读取与描述", 不做统计推断 (那是 `verification/stats_adapter` 的职责)。
"""

import csv
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# 默认上限: 结果进入模型上下文前必须受限
MAX_ROWS = 200
MAX_COLUMNS = 60
MAX_CELL_CHARS = 200
# 采样行数 (用于推断字段类型/单位), 不是全表扫描
SAMPLE_ROWS = 50

# SQLite 只读连接: URI 模式 RO 保证内核层拒绝写
_SQLITE_RO_URI = "file:{path}?mode=ro"

# 只允许这些语句开头 (单条 SELECT / WITH ... SELECT)
_ALLOWED_SQL = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
# 明确的写操作/危险语句: 即使在 CTE 里出现也拒绝
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|"
    r"reindex|savepoint|begin|commit|rollback|grant|revoke)\b",
    re.IGNORECASE,
)
# 是否存在分号后还有内容 (多条语句)
_MULTI_STATEMENT = re.compile(r";\s*\S")

_EXCEL_HINTS = ("\\t", "sep=")


@dataclass
class Field:
    name: str
    dtype: str = ""            # text / integer / real / empty
    unit: str = ""             # 从字段名/表名里识别出的单位 (可能为空)
    non_null: int = 0
    sample: list[str] = field(default_factory=list)

    def describe(self) -> str:
        bits = [self.name, self.dtype or "unknown"]
        if self.unit:
            bits.append(f"单位 {self.unit}")
        return " ".join(bits)


@dataclass
class TableSchema:
    name: str
    kind: str = "table"        # table / view
    columns: list[Field] = field(default_factory=list)
    row_count: int = 0

    def describe(self) -> str:
        return f"{self.name} ({self.kind}, {self.row_count} 行): " + \
            ", ".join(c.describe() for c in self.columns)


@dataclass
class SourceSchema:
    """一个数据源的只读描述 (可审计、可复现)。"""

    path: str = ""
    kind: str = ""             # csv / sqlite / unknown
    tables: list[TableSchema] = field(default_factory=list)
    snapshot_hash: str = ""
    size_bytes: int = 0
    error: str = ""
    limits: dict = field(default_factory=lambda: {
        "max_rows": MAX_ROWS, "max_columns": MAX_COLUMNS,
        "max_cell_chars": MAX_CELL_CHARS})

    @property
    def ok(self) -> bool:
        return not self.error

    def table(self, name: str) -> TableSchema | None:
        return next((t for t in self.tables if t.name == name), None)

    def describe(self) -> str:
        if not self.ok:
            return f"数据源不可用: {self.error}"
        head = f"{self.kind} 数据源 {Path(self.path).name} (hash {self.snapshot_hash[:12]})"
        return head + "; " + " | ".join(t.describe() for t in self.tables)


# ----------------------------------------------------------------------
# 授权路径 (与 stats 适配器共用同一实现, 避免两套规则漂移)
# ----------------------------------------------------------------------
def authorized_roots() -> list[Path]:
    """允许读取的根目录: data/ 、outputs/ 与 DATA_READ_ROOTS 显式放行项。"""
    import os

    from src.config import DATA_DIR, OUTPUT_DIR

    roots: list[Path] = []
    for raw in (DATA_DIR, OUTPUT_DIR):
        try:
            roots.append(Path(raw).resolve())
        except Exception:  # noqa: BLE001
            continue
    for item in os.getenv("DATA_READ_ROOTS", "").split(";"):
        item = item.strip()
        if not item:
            continue
        try:
            roots.append(Path(item).resolve())
        except Exception:  # noqa: BLE001
            continue
    return roots


def resolve_readonly_path(data_ref: str,
                          allowed_roots: list[Path] | None = None) -> tuple[Path | None, str]:
    """把不可信 `data_ref` 解析为授权根内的真实文件路径; 越界返回 (None, 原因)。"""
    if not data_ref or not str(data_ref).strip():
        return None, "data_ref 为空"
    candidate = Path(str(data_ref).strip())
    if not candidate.is_absolute():
        from src.config import DATA_DIR

        candidate = Path(DATA_DIR) / candidate
    try:
        target = candidate.resolve()
    except Exception as e:  # noqa: BLE001
        return None, f"数据路径无法解析: {e}"
    roots = list(allowed_roots) if allowed_roots is not None else authorized_roots()
    for root in roots:
        try:
            target.relative_to(Path(root).resolve())
        except ValueError:
            continue
        if target.is_file():
            return target, ""
        return None, f"数据文件不存在: {data_ref}"
    return None, ("数据文件不在授权读取范围内 (只允许 data/ 或 outputs/ 之下, "
                  "或由 DATA_READ_ROOTS 显式放行): " + str(data_ref))


def file_digest(path: Path, *, chunk: int = 1 << 20) -> str:
    """文件内容摘要 (sha256, 前 16 位): 用于"结果可复现"与快照版本。"""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()[:16]


def detect_kind(path: Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in (".sqlite", ".sqlite3", ".db"):
        return "sqlite"
    if suffix in (".csv", ".tsv", ".txt"):
        return "csv"
    # 无扩展名/其它后缀: 看文件头
    try:
        head = Path(path).read_bytes()[:16]
    except Exception:  # noqa: BLE001
        return "unknown"
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    return "csv" if b"," in head or b"\n" in head else "unknown"


# ----------------------------------------------------------------------
# 字段类型/单位推断
# ----------------------------------------------------------------------
# 字段名里出现的单位关键词 -> 规范单位名。
# 用"关键词是否作为片段出现"判断, 避免 ^/$ 与中英文边界 (\b 对中文字符不成立)。
# 顺序敏感: 长的/更具体的写法必须排在前面 (亿元 先于 元, 万元 先于 元)。
_UNIT_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("亿元", "亿", "_yi_", "yi_yuan"), "亿元"),
    (("万元", "万", "_wan_"), "万元"),
    (("人民币", "rmb", "cny", "元", "yuan"), "元"),
    (("美元", "usd"), "美元"),
    (("百分比", "pct", "percent", "%"), "%"),
    (("千克", "公斤", "kg"), "kg"),
    (("千米", "公里", "km"), "km"),
    (("毫米", "mm"), "mm"),
    (("毫秒", "ms"), "ms"),
    (("分贝", "db"), "dB"),
    (("赫兹", "hz"), "Hz"),
    (("秒", "sec"), "s"),
    (("米", "meter", "metre"), "m"),
)


def infer_unit(*texts: str) -> str:
    """从字段名/表名里识别单位 (识别不到返回空, 不臆造)。"""
    for text in texts:
        low = (text or "").lower()
        if not low:
            continue
        for keys, unit in _UNIT_KEYWORDS:
            if any(key in low for key in keys):
                return unit
    return ""


def infer_dtype(values: list) -> str:
    """按样本推断字段类型; 混合类型按 text 处理 (不强行转数值)。"""
    seen_int = seen_float = seen_text = False
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        text = str(value).strip()
        try:
            number = float(text)
        except ValueError:
            seen_text = True
            continue
        if number.is_integer() and not any(ch in text for ch in ".eE"):
            seen_int = True
        else:
            seen_float = True
        if seen_text:
            break
    if seen_text:
        return "text"
    if seen_float:
        return "real"
    if seen_int:
        return "integer"
    return "empty"


# ----------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------
def _csv_delimiter(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    except Exception:  # noqa: BLE001
        return ","
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except Exception:  # noqa: BLE001
        return "\t" if "\t" in head and "," not in head else ","


def _truncate_cell(value) -> str:
    text = "" if value is None else str(value)
    return text[:MAX_CELL_CHARS]


def _read_csv_rows(path: Path, *, limit: int) -> tuple[list[str], list[dict], int, bool]:
    """读取 CSV: 返回 (列名, 行, 实际行数, 是否截断)。

    单元格内容在这里就截断: 只读适配器的输出会进入模型上下文,
    必须与 SQLite 路径一样受 `MAX_CELL_CHARS` 约束。
    """
    delimiter = _csv_delimiter(path)
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        columns = list(reader.fieldnames or [])[:MAX_COLUMNS]
        rows: list[dict] = []
        total = 0
        truncated = False
        for row in reader:
            total += 1
            if len(rows) >= limit:
                truncated = True
                continue
            rows.append({k: _truncate_cell(row.get(k)) for k in columns})
    return columns, rows, total, truncated


def _fields_for(columns: list[str], rows: list[dict], table_name: str = "") -> list[Field]:
    fields: list[Field] = []
    for name in columns[:MAX_COLUMNS]:
        samples = [_truncate_cell(r.get(name)) for r in rows[:SAMPLE_ROWS]]
        fields.append(Field(
            name=name,
            dtype=infer_dtype(samples),
            unit=infer_unit(name, table_name),
            non_null=sum(1 for s in samples if str(s).strip() != ""),
            sample=[s for s in samples if str(s).strip() != ""][:3],
        ))
    return fields


def describe_csv(path: Path) -> SourceSchema:
    columns, rows, total, _ = _read_csv_rows(path, limit=SAMPLE_ROWS)
    table = TableSchema(name=path.stem or "data", kind="table",
                        columns=_fields_for(columns, rows, path.stem), row_count=total)
    return SourceSchema(path=str(path), kind="csv", tables=[table],
                        snapshot_hash=file_digest(path), size_bytes=path.stat().st_size)


# ----------------------------------------------------------------------
# SQLite (只读)
# ----------------------------------------------------------------------
def _sqlite_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_SQLITE_RO_URI.format(path=Path(path).as_posix()),
                           uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # 双保险: 即使有人替换了 URI 逻辑, 也在连接层禁止写入
    conn.execute("PRAGMA query_only = ON")
    return conn


def list_tables(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view')"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [(r["name"], r["type"]) for r in rows]


def describe_sqlite(path: Path) -> SourceSchema:
    tables: list[TableSchema] = []
    with _sqlite_connect(path) as conn:
        for name, kind in list_tables(conn):
            columns = [r["name"] for r in
                       conn.execute(f'PRAGMA table_info("{name}")').fetchall()]
            columns = columns[:MAX_COLUMNS]
            count = conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"').fetchone()["c"]
            sample = conn.execute(
                f'SELECT * FROM "{name}" LIMIT {SAMPLE_ROWS}').fetchall()
            # 注意: sqlite3.Row 支持 .keys() 但不支持直接迭代, 这里不能用 `for k in r`
            rows = [{k: r[k] for k in r.keys()} for r in sample]  # noqa: SIM118
            tables.append(TableSchema(name=name, kind=kind,
                                      columns=_fields_for(columns, rows, name),
                                      row_count=int(count or 0)))
    return SourceSchema(path=str(path), kind="sqlite", tables=tables,
                        snapshot_hash=file_digest(path), size_bytes=path.stat().st_size)


def describe_source(data_ref: str,
                    allowed_roots: list[Path] | None = None) -> SourceSchema:
    """描述一个用户数据源 (CSV / SQLite); 越权或不存在时返回带 error 的结果。"""
    path, reason = resolve_readonly_path(data_ref, allowed_roots)
    if path is None:
        return SourceSchema(path=str(data_ref), error=reason)
    kind = detect_kind(path)
    try:
        if kind == "csv":
            return describe_csv(path)
        if kind == "sqlite":
            return describe_sqlite(path)
        return SourceSchema(path=str(path), kind="unknown",
                            error=f"不支持的数据源类型: {path.suffix or '(无扩展名)'}")
    except Exception as e:  # noqa: BLE001 - 描述失败要显式告知, 不能静默空表
        return SourceSchema(path=str(path), kind=kind, error=f"数据源解析失败: {e}")


# ----------------------------------------------------------------------
# 只读查询
# ----------------------------------------------------------------------
@dataclass
class QueryResult:
    """一次只读查询的结果 (含可复现信息)。"""

    ok: bool = False
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    sql: str = ""
    source: str = ""
    snapshot_hash: str = ""
    readonly: bool = True
    error: str = ""

    def describe(self) -> str:
        if not self.ok:
            return f"查询失败: {self.error}"
        shown = len(self.rows)
        limit_note = " (已截断)" if self.truncated else ""
        return (f"只读查询 {self.source} 返回 {self.row_count} 行, 展示 {shown} 行"
                f"{limit_note}; 快照 {self.snapshot_hash}")


def validate_select(sql: str) -> tuple[bool, str]:
    """校验是否为**单条**只读 SELECT。返回 (是否合法, 原因)。"""
    text = (sql or "").strip()
    if not text:
        return False, "SQL 为空"
    if _MULTI_STATEMENT.search(text):
        return False, "一次只允许执行一条语句 (检测到分号后的内容)"
    stripped = text.rstrip(";").strip()
    if not _ALLOWED_SQL.match(stripped):
        return False, "只允许 SELECT / WITH 查询 (只读适配器不接受其它语句)"
    forbidden = _FORBIDDEN_SQL.search(stripped)
    if forbidden:
        return False, f"查询包含被禁止的关键字: {forbidden.group(0).upper()}"
    return True, ""


def _safe_limit(limit: int | None) -> int:
    if not limit or int(limit) <= 0:
        return MAX_ROWS
    return min(int(limit), MAX_ROWS)


def query_sqlite(path: Path, sql: str, *, limit: int | None = None) -> QueryResult:
    ok, reason = validate_select(sql)
    if not ok:
        return QueryResult(ok=False, error=reason, sql=sql, source=str(path))
    max_rows = _safe_limit(limit)
    snapshot = file_digest(path)
    try:
        with _sqlite_connect(path) as conn:
            cursor = conn.execute(sql)
            columns = [d[0] for d in (cursor.description or [])][:MAX_COLUMNS]
            rows: list[dict] = []
            total = 0
            truncated = False
            for record in cursor:
                total += 1
                if len(rows) >= max_rows:
                    truncated = True
                    continue
                rows.append({k: _truncate_cell(record[k]) for k in columns})
    except Exception as e:  # noqa: BLE001 - 查询失败必须显式返回, 不返回空表冒充成功
        return QueryResult(ok=False, error=f"查询失败: {e}", sql=sql, source=str(path),
                           snapshot_hash=snapshot)
    return QueryResult(ok=True, columns=columns, rows=rows, row_count=total,
                       truncated=truncated, sql=sql, source=str(path),
                       snapshot_hash=snapshot)


def query_table(path: Path, table: str, *, limit: int | None = None,
                where: str = "") -> QueryResult:
    """按**白名单表名**读取: 表名必须存在于 `describe_sqlite` 的结果中。"""
    schema = describe_sqlite(path)
    if schema.table(table) is None:
        return QueryResult(ok=False, source=str(path),
                           error=f"表 {table!r} 不存在或不可读; 可用表: " +
                                 ", ".join(t.name for t in schema.tables))
    if where and _FORBIDDEN_SQL.search(where) or (where and _MULTI_STATEMENT.search(where)):
        return QueryResult(ok=False, source=str(path), error="where 子句包含被禁止的内容")
    sql = f'SELECT * FROM "{table}"' + (f" WHERE {where}" if where else "")
    return query_sqlite(path, sql, limit=limit)


def query_csv(path: Path, *, limit: int | None = None,
              where: str = "") -> QueryResult:
    """CSV 只支持整表读取 + 简单等值过滤 (不做 SQL 解析)。"""
    max_rows = _safe_limit(limit)
    columns, rows, total, truncated = _read_csv_rows(path, limit=max_rows)
    if where:
        key, _, value = where.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key in columns:
            filtered = [r for r in rows if str(r.get(key, "")).strip() == value]
            truncated = truncated or len(filtered) != len(rows)
            rows = filtered
    return QueryResult(ok=True, columns=columns, rows=rows, row_count=total,
                       truncated=truncated, sql=f"csv:{path.name}" + (f" where {where}" if where else ""),
                       source=str(path), snapshot_hash=file_digest(path))


def load_rows(data_ref: str, *, table: str = "", where: str = "",
              limit: int | None = None,
              allowed_roots: list[Path] | None = None) -> tuple[list[dict], str, dict]:
    """给统计/验证层用的行读取 (CSV 或 SQLite 单表)。

    返回 (行, 失败原因, 元信息)。元信息含 `source/snapshot_hash/truncated/row_count`,
    使"实际执行了哪次只读查询"可以写进研究报告。
    """
    path, reason = resolve_readonly_path(data_ref, allowed_roots)
    if path is None:
        return [], reason, {}
    kind = detect_kind(path)
    try:
        if kind == "sqlite":
            if not table:
                schema = describe_sqlite(path)
                if not schema.ok:
                    return [], schema.error, {}
                if len(schema.tables) != 1:
                    return [], ("SQLite 数据源含多个表, 必须显式指定 table: " +
                                ", ".join(t.name for t in schema.tables)), {}
                table = schema.tables[0].name
            result = query_table(path, table, limit=limit, where=where)
        elif kind == "csv":
            result = query_csv(path, limit=limit, where=where)
        else:
            return [], f"不支持的数据源类型: {path.suffix or '(无扩展名)'}", {}
    except Exception as e:  # noqa: BLE001
        return [], f"数据读取失败: {e}", {}
    if not result.ok:
        return [], result.error, {}
    return list(result.rows), "", {
        "source": Path(result.source).name,
        "snapshot_hash": result.snapshot_hash,
        "row_count": result.row_count,
        "returned_rows": len(result.rows),
        "truncated": result.truncated,
        "readonly": True,
        "sql": result.sql,
    }


__all__ = [
    "MAX_ROWS",
    "Field",
    "QueryResult",
    "SourceSchema",
    "TableSchema",
    "authorized_roots",
    "describe_source",
    "detect_kind",
    "file_digest",
    "infer_dtype",
    "infer_unit",
    "load_rows",
    "query_csv",
    "query_sqlite",
    "query_table",
    "resolve_readonly_path",
    "validate_select",
]
