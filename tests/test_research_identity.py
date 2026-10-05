from __future__ import annotations

"""问题级运行与交付身份 (计划书 §3 R6)。

反向安全测试:
- `run_id` / `branch_id` 必须**持久化**: 续跑不能生成新的运行身份, 否则同一次研究
  在动作账本、runtime、快照和产物目录里会变成多次运行;
- 快照自带 project/problem/run/branch —— 权威身份来自对象本身, 而不是调用方
  "取第一个规格"再拼;
- 交付 manifest 与快照使用同一个身份; 目录名不决定归属;
- 旧库 (`snapshots` 没有身份列) 打开时补列并**按已有 JSON 回填**, 缺失的保持为空
  (不猜归属), 不破坏历史数据。
"""

import sqlite3





def test_legacy_snapshot_table_is_migrated(tmp_path):
    """旧库没有身份列: 打开时补列, 并从 JSON 回填真实身份。"""
    from src.research.schemas import ResearchSnapshot
    from src.research.store import ResearchStore

    # 库文件放在隔离研究目录下 (conftest 会把非隔离路径的研究库重定向到这里)
    db = tmp_path / "data" / "research" / "legacy.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE snapshots (
            project_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, snapshot_id)
        );
        """)
    good = ResearchSnapshot(project_id="legacy", problem_id="p1", run_id="run-old",
                            branch_id="route-old")
    partial = ResearchSnapshot(project_id="legacy", problem_id="p1")
    conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                 ("legacy", good.snapshot_id, good.model_dump_json(), good.created_at))
    conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                 ("legacy", partial.snapshot_id, partial.model_dump_json(),
                  partial.created_at))
    conn.commit()
    conn.close()

    store = ResearchStore("legacy", db_path=db)
    raw = sqlite3.connect(str(db))
    columns = {r[1] for r in raw.execute("PRAGMA table_info(snapshots)")}
    assert {"problem_id", "run_id", "branch_id"} <= columns
    by_id = {r[0]: (r[1], r[2], r[3]) for r in raw.execute(
        "SELECT snapshot_id, problem_id, run_id, branch_id FROM snapshots")}
    assert set(by_id) == {good.snapshot_id, partial.snapshot_id}, by_id
    assert by_id[good.snapshot_id] == ("p1", "run-old", "route-old")
    # 旧 JSON 里没有的身份字段保持为空 (NULL), 不猜测
    assert by_id[partial.snapshot_id] == ("p1", None, None)

    # 历史数据未被改写: 内容仍可读回
    loaded = store.load_snapshot(good.snapshot_id)
    assert loaded is not None and loaded.run_id == "run-old"
    store.close()
    raw.close()
    # 迁移可重复执行 (幂等)
    store2 = ResearchStore("legacy", db_path=db)
    assert store2.list_snapshots(problem_id="p1")[0]["snapshot_id"] == partial.snapshot_id
    store2.close()
