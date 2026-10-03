from __future__ import annotations

"""只读数据源适配器的失败用例 (计划书 §3 R0 / §6.3)。

反向安全测试: 用户/模型给出的数据引用是**不可信输入**。
- 越权路径一律拒绝 (哪怕只回传聚合值也算泄漏);
- SQLite 一律只读: 写语句、PRAGMA、多语句、非 SELECT 全部拒绝;
- 表名必须来自白名单 (不接受拼接的表名);
- 行/列上限与快照 hash: 结果受限且可复现。
"""

import sqlite3

import pytest


def _write_csv(tmp_path, name="trade.csv", text=None):
    path = tmp_path / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or (
        "firm,region,year,output_yi_yuan,treated\n"
        "A,华东,2015,10.0,T\nA,华东,2019,20.0,T\n"
        "B,华东,2015,10.0,C\nB,华东,2019,12.0,C\n"
    ), encoding="utf-8")
    return path


def _write_sqlite(tmp_path, name="panel.sqlite"):
    path = tmp_path / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE panel (g TEXT, t TEXT, y REAL, note TEXT)")
    rows = []
    # 每格 2 条观测: DiD 估计要求每格样本量 >= 2
    for _ in range(2):
        rows += [("T", "pre", 10.0, "a"), ("T", "post", 20.0, "b"),
                 ("C", "pre", 10.0, "c"), ("C", "post", 12.0, "d")]
    conn.executemany("INSERT INTO panel VALUES (?,?,?,?)", rows)
    conn.execute("CREATE TABLE other (k TEXT)")
    conn.execute("INSERT INTO other VALUES ('x')")
    conn.commit()
    conn.close()
    return path


# --------------------------------------------------------------------------
# 路径授权
# --------------------------------------------------------------------------
def test_describe_rejects_out_of_scope_path(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(exist_ok=True)
    outside = tmp_path / "secret.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")

    schema = rd.describe_source(str(outside))
    assert schema.ok is False
    assert "授权读取范围" in schema.error
    rows, reason, meta = rd.load_rows(str(outside))
    assert rows == [] and "授权读取范围" in reason and meta == {}


def test_describe_reports_missing_and_unknown(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(exist_ok=True)
    missing = rd.describe_source("nope.csv")
    assert missing.ok is False and "不存在" in missing.error

    weird = tmp_path / "data" / "x.bin"
    weird.write_bytes(b"\x00\x01\x02\x03")
    unknown = rd.describe_source(str(weird))
    assert unknown.ok is False and "不支持" in unknown.error


# --------------------------------------------------------------------------
# CSV: schema / 单位 / 上限 / 快照
# --------------------------------------------------------------------------
def test_csv_schema_infers_types_units_and_hash(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_csv(tmp_path)
    schema = rd.describe_source(str(path))

    assert schema.ok is True and schema.kind == "csv"
    table = schema.tables[0]
    assert table.row_count == 4
    by_name = {c.name: c for c in table.columns}
    assert by_name["year"].dtype == "integer"
    assert by_name["output_yi_yuan"].dtype == "real"
    # 单位从字段名识别 (识别不到就不写, 不臆造)
    assert by_name["output_yi_yuan"].unit == "亿元"
    assert by_name["region"].unit == ""
    assert schema.snapshot_hash and len(schema.snapshot_hash) == 16
    assert "亿元" in schema.describe()


def test_csv_hash_changes_when_file_changes(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_csv(tmp_path)
    first = rd.describe_source(str(path)).snapshot_hash
    path.write_text(path.read_text(encoding="utf-8") + "C,华东,2019,9.0,C\n",
                    encoding="utf-8")
    second = rd.describe_source(str(path)).snapshot_hash
    assert first != second, "文件变化后快照 hash 必须变化 (结果可复现的前提)"


def test_csv_row_limit_and_where(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_csv(tmp_path)
    result = rd.query_csv(path, limit=2)
    assert result.ok and len(result.rows) == 2 and result.row_count == 4
    assert result.truncated is True

    filtered = rd.query_csv(path, where="treated=T")
    assert filtered.ok and len(filtered.rows) == 2
    assert {r["firm"] for r in filtered.rows} == {"A"}


def test_cell_length_is_capped(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_csv(tmp_path, "long.csv", "a,b\n" + "x" * 500 + ",1\n")
    result = rd.query_csv(path)
    assert result.ok
    assert len(result.rows[0]["a"]) == rd.MAX_CELL_CHARS


# --------------------------------------------------------------------------
# SQLite: 只读保证
# --------------------------------------------------------------------------
def test_sqlite_schema_lists_tables_and_rows(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_sqlite(tmp_path)
    schema = rd.describe_source(str(path))
    assert schema.ok is True and schema.kind == "sqlite"
    names = {t.name: t for t in schema.tables}
    assert set(names) == {"panel", "other"}
    assert names["panel"].row_count == 8
    assert names["panel"].kind == "table"
    assert {c.name for c in names["panel"].columns} == {"g", "t", "y", "note"}


@pytest.mark.parametrize("sql", [
    "INSERT INTO panel VALUES ('T','pre',1.0,'x')",
    "UPDATE panel SET y = 0",
    "DELETE FROM panel",
    "DROP TABLE panel",
    "CREATE TABLE hack (a)",
    "ATTACH DATABASE 'other.sqlite' AS other",
    "PRAGMA table_info(panel)",
    "SELECT 1; DELETE FROM panel",
    "SELECT * FROM panel; SELECT * FROM other",
    "",
])
def test_sqlite_rejects_non_readonly_statements(tmp_path, monkeypatch, sql):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_sqlite(tmp_path)
    ok, reason = rd.validate_select(sql)
    assert ok is False, sql
    assert reason, sql

    result = rd.query_sqlite(path, sql)
    assert result.ok is False, sql
    # 数据必须完好: 拒绝的语句不能被部分执行
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM panel").fetchone()[0] == 8
    conn.close()


def test_sqlite_connection_is_query_only(tmp_path, monkeypatch):
    """双保险: 即使绕过 SQL 校验, 连接层也必须是只读的。"""
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_sqlite(tmp_path)
    conn = rd._sqlite_connect(path)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO panel VALUES ('T','pre',1.0,'x')")
    finally:
        conn.close()


def test_select_returns_bounded_rows_and_hash(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_sqlite(tmp_path)
    result = rd.query_sqlite(path, 'SELECT g, y FROM panel ORDER BY y')
    assert result.ok is True and result.readonly is True
    assert result.columns == ["g", "y"]
    assert result.row_count == 8 and len(result.rows) == 8
    assert result.snapshot_hash == rd.file_digest(path)
    assert "只读查询" in result.describe()

    limited = rd.query_sqlite(path, "SELECT * FROM panel", limit=2)
    assert len(limited.rows) == 2 and limited.truncated is True
    assert limited.row_count == 8


def test_table_whitelist_and_where_guard(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_sqlite(tmp_path)
    bad = rd.query_table(path, "panel; DROP TABLE other")
    assert bad.ok is False and "不存在" in bad.error

    good = rd.query_table(path, "panel", where="g = 'T'")
    assert good.ok is True and good.row_count == 4

    injected = rd.query_table(path, "panel", where="1=1; DELETE FROM panel")
    assert injected.ok is False


def test_load_rows_requires_table_when_ambiguous(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_sqlite(tmp_path)
    rows, reason, meta = rd.load_rows(str(path))
    assert rows == [] and "必须显式指定 table" in reason

    rows, reason, meta = rd.load_rows(str(path), table="panel")
    assert reason == "" and len(rows) == 8
    assert meta["readonly"] is True and meta["source"] == "panel.sqlite"
    assert meta["snapshot_hash"] and meta["row_count"] == 8


def test_load_rows_reads_csv(tmp_path, monkeypatch):
    from src import config
    from src.kb.adapters import readonly_data as rd

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_csv(tmp_path)
    rows, reason, meta = rd.load_rows(str(path))
    assert reason == "" and len(rows) == 4
    assert meta["readonly"] is True and meta["truncated"] is False


# --------------------------------------------------------------------------
# 与研究验证层对接: SQLite 数据源必须能直接支撑估计
# --------------------------------------------------------------------------
def test_stats_adapter_reads_sqlite_table(tmp_path, monkeypatch):
    """计划书 R0 验收: 给定只读数据集时, 系统能执行并记录真实的只读查询。"""
    from src import config
    from src.kb.adapters import readonly_data as rd
    from src.verification.stats_adapter import run

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    path = _write_sqlite(tmp_path)
    result = run("difference_in_differences", {
        "data_ref": str(path), "table": "panel",
        "group_col": "g", "time_col": "t", "outcome_col": "y",
        "treated_label": "T", "control_label": "C", "pre_label": "pre",
        "post_label": "post",
    })
    assert result.status.value == "passed", result.detail
    assert result.values.get("estimate") == pytest.approx(8.0)

    # 只读查询的记录可复现: 同一数据源给出同一快照 hash
    rows, reason, meta = rd.load_rows(str(path), table="panel")
    assert reason == "" and len(rows) == 8
    assert meta["snapshot_hash"] == rd.file_digest(path)


def test_stats_adapter_rejects_write_data_ref(tmp_path, monkeypatch):
    """统计入口同样不得读取授权范围外的数据。"""
    from src import config
    from src.verification.stats_adapter import run

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(exist_ok=True)
    outside = tmp_path / "outside.csv"
    outside.write_text("g,t,y\nT,pre,1\nT,post,3\nC,pre,1\nC,post,1\n", encoding="utf-8")
    result = run("describe", {"data_ref": str(outside)})
    assert result.status.value == "unsupported"
    assert "授权读取范围" in result.detail
