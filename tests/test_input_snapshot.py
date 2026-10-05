from __future__ import annotations

"""输入快照的附件可用性校验 (计划书 §3 R6 收尾)。

覆盖三类情形:
1. 单元: 逐份附件重算 sha256 —— 一致 / 内容被改 / 文件被删 / 登记被删 / 路径越界;
2. 单元: 快照自身声明不可复现时, `reproducible` 必须为 False;
3. 端到端: 两份附件的理论研究断点续跑 —— 输入一致则包里的 reproducible=True;
   附件内容被改后 `resume` 必须被拒绝, 且绝不产出新的交付包。
"""

import hashlib
from pathlib import Path

import pytest

from src.utils import uploads

REQUEST = "研究在噪声强度影响下误码率的变化"


def verify_input_snapshot(snapshot):
    """转发到实现: 端到端断言里常用到, 放这里避免每处 import。"""
    from src.research.input_snapshot import verify_input_snapshot as _verify

    return _verify(snapshot)


# --------------------------------------------------------------------------
# 测试夹具: 隔离 DATA_DIR 并按上传登记的真实形态登记附件
# --------------------------------------------------------------------------
@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把 DATA_DIR/OUTPUT_DIR 指到临时目录。

    附件登记是**全局**的 `DATA_DIR/uploads/attachments.json`: 不隔离就会读到仓库
    `data/` 里的登记, 既让测试结果随本机状态漂移, 也会污染真实工作区。
    """
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    return tmp_path


def _register(project_id: str, problem_id: str, filename: str, text: str) -> dict:
    """按上传登记的字段落盘一份附件并登记 (等价于真实上传后的登记项)。

    这里不用 `uploads.save_problem_attachment` 的原因: 它的入参形态 (bytes → 暂存
    路径) 正在被另一项任务改动; 直接用 `uploads.save/load` 造登记项既能覆盖校验
    真正依赖的字段 (path/sha256/kind/project_id), 又不会随上传实现的版本漂移。
    """
    root = uploads.upload_root() / project_id
    root.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    path = root / f"{digest[:12]}_{filename}"
    path.write_bytes(data)
    record = {
        "attachment_id": uploads.attachment_id("problem", project_id, digest),
        "kind": "problem", "project_id": project_id, "problem_id": problem_id,
        "topic": "", "filename": filename, "size": len(data), "sha256": digest,
        "short_hash": digest[:12], "path": str(path),
        "parse_quality": "ok", "visibility_flags": [],
    }
    uploads.save(uploads.load() + [record])
    return record


def _snapshot(records: list[dict], request: str = REQUEST) -> dict:
    """按 `build_theory_initial_state` 的形态造启动输入快照。"""
    return {
        "request": request,
        "topic": "方向",
        "source_set_id": "",
        "source_set_kind": "kb",
        "source_policy": "user_kb",
        "attachment_ids": [r["attachment_id"] for r in records],
        "attachments": [{"attachment_id": r["attachment_id"], "filename": r["filename"],
                         "sha256": r["sha256"], "parse_quality": r["parse_quality"],
                         "visibility_flags": r["visibility_flags"]} for r in records],
        "budget": {"max_actions": 40, "max_tool_calls": 60},
        "created_at": "2026-01-01T00:00:00Z",
    }


def _status_of(check, attachment_id: str) -> str:
    """取某份附件的状态; 既接受 `SnapshotCheck`, 也接受写进 manifest 的字典形态。"""
    items = check.attachments if hasattr(check, "attachments") else check["attachments"]
    return next(a["status"] for a in items if a["attachment_id"] == attachment_id)


# --------------------------------------------------------------------------
# 单元: 每份附件的状态与原因
# --------------------------------------------------------------------------
def test_snapshot_hash_match_is_usable(sandbox):
    from src.research.input_snapshot import verify_input_snapshot

    record = _register("proj-ok", "p1", "scope.txt", "研究范围限定在 2.4GHz 频段")
    check = verify_input_snapshot(_snapshot([record]))

    assert check.ok is True
    assert check.reproducible is True
    assert [a["status"] for a in check.attachments] == ["ok"]
    assert check.attachments[0]["recorded_sha256"] == check.attachments[0]["actual_sha256"]
    assert "一致" in check.reason


def test_snapshot_content_change_is_hash_mismatch(sandbox):
    from src.research.input_snapshot import verify_input_snapshot

    record = _register("proj-mod", "p1", "scope.txt", "研究范围限定在 2.4GHz 频段")
    Path(record["path"]).write_bytes("研究范围改为全频段".encode())

    check = verify_input_snapshot(_snapshot([record]))
    assert check.ok is False
    assert check.reproducible is False
    assert _status_of(check, record["attachment_id"]) == "hash_mismatch"
    # 原因必须说明是"内容变了", 并给出两边的摘要, 而不是笼统的失败
    assert any("内容已改变" in n for n in check.notes), check.notes
    assert any("sha256" in a["detail"] or "摘要" in a["detail"] for a in check.attachments)


def test_snapshot_deleted_file_is_missing(sandbox):
    from src.research.input_snapshot import verify_input_snapshot

    record = _register("proj-del", "p1", "extra.txt", "只讨论设备集固定的情形")
    Path(record["path"]).unlink()

    check = verify_input_snapshot(_snapshot([record]))
    assert check.ok is False
    assert check.reproducible is False
    assert _status_of(check, record["attachment_id"]) == "missing"
    assert any("原件已不存在" in n for n in check.notes), check.notes


def test_snapshot_deleted_registration_is_unregistered(sandbox):
    """登记被删即不可用: 磁盘上还留着文件也不能算"还在输入里"。"""
    from src.research.input_snapshot import verify_input_snapshot

    record = _register("proj-unreg", "p1", "note.txt", "补充说明: 只考虑低信噪比")
    uploads.save([i for i in uploads.load() if i["attachment_id"] != record["attachment_id"]])
    assert Path(record["path"]).is_file(), "只删登记, 文件仍在"

    check = verify_input_snapshot(_snapshot([record]))
    assert check.ok is False
    assert check.reproducible is False
    assert _status_of(check, record["attachment_id"]) == "unregistered"
    assert any("登记已不存在" in n for n in check.notes), check.notes


def test_snapshot_outside_root_is_rejected(sandbox, tmp_path):
    from src.research.input_snapshot import verify_input_snapshot

    record = _register("proj-out", "p1", "note.txt", "补充说明")
    outside = tmp_path / "outside" / "note.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(Path(record["path"]).read_bytes())
    uploads.save([{**i, "path": str(outside)} if i["attachment_id"] == record["attachment_id"]
                  else i for i in uploads.load()])

    check = verify_input_snapshot(_snapshot([record]))
    assert check.ok is False
    assert check.reproducible is False
    assert _status_of(check, record["attachment_id"]) == "outside_root"
    assert any("路径越界" in n for n in check.notes), check.notes


def test_snapshot_declared_non_reproducible_stays_non_reproducible(sandbox):
    """退化快照 (没有附件上下文) 的 `reproducible: False` 不得被校验"洗白"。"""
    from src.research.input_snapshot import verify_input_snapshot

    snapshot = {"request": REQUEST, "attachment_ids": [], "attachments": [],
                "reproducible": False,
                "note": "直接调用未提供附件/资料集上下文: 该快照不足以完整复现"}
    check = verify_input_snapshot(snapshot)
    assert check.ok is True, "没有附件要校验, 不是附件坏了"
    assert check.reproducible is False
    assert any("reproducible=False" in n for n in check.notes), check.notes


def test_snapshot_declared_attachment_without_record_is_noted(sandbox):
    """快照声明了附件却没有 sha256 记录: 校验不了就要说明快照不完整。"""
    from src.research.input_snapshot import verify_input_snapshot

    snapshot = {"attachment_ids": ["att-ghost"], "attachments": []}
    check = verify_input_snapshot(snapshot)
    assert check.ok is True
    assert any("att-ghost" in n for n in check.notes), check.notes


# --------------------------------------------------------------------------
# 端到端: 续跑先校验, 不一致就拒绝续跑
# --------------------------------------------------------------------------
def _start_interactive_run(tmp_path, monkeypatch):
    """造一个"停在候选确认"的理论研究: 两份附件 + 不可变输入快照。

    停在 interrupt 上是**真实续跑场景**: 此时检查点里保存着启动输入快照, 下一次
    `resume` 才会真正沿用上次的附件。检查点用内存实现, 避免跨测试/跨进程残留。
    """
    from langgraph.checkpoint.memory import MemorySaver

    from src.graph import theory_pipeline

    records = [_register("proj-e2e", "p1", "scope.txt", "研究范围限定在 2.4GHz 频段"),
               _register("proj-e2e", "p1", "extra.txt", "只讨论设备集固定的情形")]
    checkpointer = MemorySaver()
    first = theory_pipeline.run_theory_pipeline(
        request=REQUEST, topic="方向", project_id="proj-e2e", problem_id="p1",
        interactive=True, input_snapshot=_snapshot(records), checkpointer=checkpointer)
    # 停在候选确认: 尚未产出任何交付包
    assert first.get("package_dir") is None
    assert first.get("needs_confirmation") is True
    return records, checkpointer, first


def _packages(config) -> list[str]:
    return sorted(str(p) for p in (config.OUTPUT_DIR / "research").rglob("manifest.json"))
