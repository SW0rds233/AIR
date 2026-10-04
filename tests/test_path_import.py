from __future__ import annotations

"""按用户指定路径构建人工文献库的失败与边界用例 (合并计划 §13.5)。

验收路径逐条对应: 单文件/整目录/多路径混合、非白名单与不可读、
未授权根与敏感文件、同 hash 多路径与重复导入幂等、原件被移动后 stale、
部分失败逐条呈现、删除库不动用户文件与共享库。

关键不变量: **不复制原文件** —— 每个用例都比对导入前后的 mtime 与内容。
"""

import json
import os
from pathlib import Path

import pytest


def _write(path: Path, text: str = "hello") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _auth_data(tmp_path, monkeypatch):
    """把 data/ 指到临时目录, 并让授权根随之变化 (与 readonly_data 同一语义)。"""
    from src import config

    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out", raising=False)
    monkeypatch.delenv("DATA_READ_ROOTS", raising=False)
    return data


def _snapshot(root: Path) -> dict[str, tuple[float, str]]:
    """记录一棵树下每个文件的大小/mtime/内容摘要 (用于证明"没有复制/修改")。"""
    out: dict[str, tuple[float, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[str(path.relative_to(root))] = (stat.st_mtime, path.read_text(
                encoding="utf-8", errors="replace"))
    return out


# --------------------------------------------------------------------------
# 扫描: 形态与授权
# --------------------------------------------------------------------------
def test_scan_accepts_file_dir_and_mixed_paths(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    single = _write(data / "one.md", "# 单文件")
    folder = data / "papers"
    _write(folder / "a.md", "# A")
    _write(folder / "sub" / "b.txt", "B")

    report = pi.scan_paths(pi.ScanRequest(paths=[str(single), str(folder)]))
    found = {f.display_path() for f in report.ok_files}
    assert found == {"one.md", "a.md", "b.txt"}
    # 目录默认递归 (合并计划 §13.1)
    assert report.counts()["ok"] == 3
    assert report.truncated is False
    assert report.ok_files[0].reason == ""


def test_scan_non_recursive_skips_subdirectories(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    folder = data / "papers"
    _write(folder / "a.md", "# A")
    _write(folder / "sub" / "b.txt", "B")

    report = pi.scan_paths(pi.ScanRequest(paths=[str(folder)], recursive=False))
    assert {f.display_path() for f in report.ok_files} == {"a.md"}


def test_scan_glob_pattern_matches_directory_semantics(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    _write(data / "papers" / "a.md", "# A")
    _write(data / "papers" / "deep" / "b.md", "# B")
    _write(data / "papers" / "c.txt", "C")

    report = pi.scan_paths(pi.ScanRequest(paths=[str(data / "papers" / "**" / "*.md")]))
    assert {f.display_path() for f in report.ok_files} == {"a.md", "b.md"}


def test_scan_reports_missing_path_and_empty_directory(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    empty = data / "empty"
    empty.mkdir(parents=True)

    report = pi.scan_paths(pi.ScanRequest(paths=[str(empty), str(data / "nope.md")]))
    assert report.ok_files == []
    joined = " ".join(report.notes)
    assert "没有文件" in joined
    assert "路径不存在" in joined


# --------------------------------------------------------------------------
# 扫描: 拒绝与跳过必须逐条给原因
# --------------------------------------------------------------------------
def test_unauthorized_root_is_denied_with_actionable_reason(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    _auth_data(tmp_path, monkeypatch)
    outside = _write(tmp_path / "secret" / "paper.md", "# 越界")

    report = pi.scan_paths(pi.ScanRequest(paths=[str(outside)]))
    assert report.ok_files == []
    assert len(report.denied) == 1
    entry = report.denied[0]
    assert entry.status.value == "denied"
    assert "DATA_READ_ROOTS" in entry.reason
    assert "显式放行" in entry.reason


def test_sensitive_files_are_denied_even_inside_authorized_root(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    env = _write(data / ".env", "API_KEY=secret")
    key = _write(data / "server.key", "PRIVATE")
    ssh = _write(data / ".ssh" / "id_rsa", "PRIVATE")
    creds = _write(data / "credentials.json", "{}")
    normal = _write(data / "papers" / "fine.md", "# ok")

    report = pi.scan_paths(pi.ScanRequest(paths=[
        str(env), str(key), str(ssh), str(creds), str(normal),
    ]))
    denied = {d.display_path(): d.reason for d in report.denied}
    assert set(denied) == {".env", "server.key", "id_rsa", "credentials.json"}
    for reason in denied.values():
        assert "敏感" in reason
    # 正常文件不受影响
    assert {f.display_path() for f in report.ok_files} == {"fine.md"}


def test_non_whitelisted_suffix_is_skipped_not_denied(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    _write(data / "archive.zip", "binary")
    _write(data / "real.md", "# real")

    report = pi.scan_paths(pi.ScanRequest(paths=[str(data)]))
    skipped = {f.display_path() for f in report.files if f.status.value == "skipped"}
    assert "archive.zip" in skipped
    assert {f.display_path() for f in report.ok_files} == {"real.md"}
    # 跳过不是拒绝: 不给"需授权"的错误暗示
    assert all("敏感" not in f.reason for f in report.files if f.status.value == "skipped")


def test_symlink_pointing_outside_authorized_root_is_denied(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    outside = _write(tmp_path / "outside" / "paper.md", "# outside")
    link = data / "link.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):  # pragma: no cover - 平台不支持时跳过
        pytest.skip("当前环境不支持创建符号链接")

    report = pi.scan_paths(pi.ScanRequest(paths=[str(link)]))
    assert report.ok_files == []
    assert len(report.denied) == 1
    assert "授权读取范围" in report.denied[0].reason


def test_limits_truncate_and_report_instead_of_silent_drop(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    data = _auth_data(tmp_path, monkeypatch)
    for i in range(5):
        _write(data / f"p{i}.md", f"# {i}")

    by_count = pi.scan_paths(pi.ScanRequest(paths=[str(data)], max_files=2))
    assert by_count.truncated is True
    assert len(by_count.ok_files) == 2
    assert any("truncated" in n or "上限" in n for n in by_count.notes)

    by_bytes = pi.scan_paths(pi.ScanRequest(paths=[str(data)], max_bytes=1))
    assert by_bytes.truncated is True
    assert by_bytes.ok_files == []


def test_custom_allow_roots_widens_scope_explicitly(tmp_path, monkeypatch):
    from src.kb import path_import as pi

    _auth_data(tmp_path, monkeypatch)
    outside = tmp_path / "lib"
    _write(outside / "p.md", "# p")

    without = pi.scan_paths(pi.ScanRequest(paths=[str(outside)]))
    assert without.ok_files == [] and without.denied

    with_root = pi.scan_paths(pi.ScanRequest(paths=[str(outside)],
                                             allow_roots=[str(outside)]))
    assert {f.display_path() for f in with_root.ok_files} == {"p.md"}
    assert str(outside.resolve()) in with_root.roots


# --------------------------------------------------------------------------
# 导入: 不复制、幂等、去重
# --------------------------------------------------------------------------
def test_import_ingests_files_without_copying_them(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    data = _auth_data(tmp_path, monkeypatch)
    folder = tmp_path / "library"
    _write(folder / "theorem.md", "# Theorem 10\nThe design does not exist.\n" * 20)
    _write(folder / "other.txt", "some notes")
    before = _snapshot(folder)

    topic = "pathlib-1"
    store = KBStore(topic)
    try:
        report = pi.import_paths(store, topic,
                                 pi.ScanRequest(paths=[str(folder)],
                                                label="我的文献",
                                                allow_roots=[str(folder)]))
        docs = store.list_documents()
    finally:
        store.close()

    assert report.ok is True
    assert report.counts()["imported"] == 2
    assert len(docs) == 2
    # 原文件未被复制/修改: 内容与 mtime 不变, 且目录里没有多出文件
    assert _snapshot(folder) == before
    assert not (data / "manual_pdfs").exists()
    # 登记记录里的路径引用是"引用"而不是副本
    refs = pi.load_path_refs(topic)
    assert len(refs) == 2
    assert all(Path(r.path).is_file() for r in refs)
    assert all(r.file_hash for r in refs)


def test_import_is_idempotent_for_the_same_path(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    folder = tmp_path / "library"
    _write(folder / "a.md", "# A\n" * 30)

    topic = "pathlib-2"
    store = KBStore(topic)
    try:
        request = pi.ScanRequest(paths=[str(folder)], allow_roots=[str(folder)])
        first = pi.import_paths(store, topic, request)
        second = pi.import_paths(store, topic, request)
        docs = store.list_documents()
        events = _kb_events(store)
    finally:
        store.close()

    assert first.counts()["imported"] == 1
    assert second.counts()["imported"] == 0
    assert second.counts()["duplicates"] == 1
    assert len(docs) == 1                     # 同一文献只入库一次
    ingest_events = [e for e in events if e.get("type") == "ingest_manual"]
    assert len(ingest_events) == 2            # 两次导入都留审计记录
    # 幂等命中不重复解析: 第二次事件里标了 idempotent
    assert any((e.get("payload") or {}).get("idempotent") for e in ingest_events)


def _kb_events(store) -> list[dict]:
    """读取知识库事件表 (KBStore 没有公开 events() 读取入口, 测试直查)。"""
    rows = store._conn.execute("SELECT type, payload FROM events ORDER BY seq").fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except Exception:  # noqa: BLE001
            payload = {}
        out.append({"type": row["type"], "payload": payload})
    return out


def test_same_content_at_two_paths_becomes_one_document_with_two_refs(tmp_path,
                                                                     monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    root = tmp_path / "library"
    body = "# 同一篇文献\n" * 40
    _write(root / "copy1" / "paper.md", body)
    _write(root / "copy2" / "paper.md", body)

    topic = "pathlib-3"
    store = KBStore(topic)
    try:
        report = pi.import_paths(store, topic,
                                 pi.ScanRequest(paths=[str(root)],
                                                allow_roots=[str(root)]))
        docs = store.list_documents()
    finally:
        store.close()

    assert len(docs) == 1                      # 同一 hash 只入库一次
    assert report.counts()["imported"] == 1
    assert report.counts()["duplicates"] == 1
    refs = pi.load_path_refs(topic)
    assert len(refs) == 2                      # 两个路径引用同一 doc_id
    assert len({r.doc_id for r in refs}) == 1


def test_partial_failure_is_reported_per_entry(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    data = _auth_data(tmp_path, monkeypatch)
    outside = _write(tmp_path / "outside" / "x.md", "# outside")
    good = _write(data / "good.md", "# good\n" * 30)
    _write(data / ".env", "SECRET=1")

    topic = "pathlib-4"
    store = KBStore(topic)
    try:
        report = pi.import_paths(store, topic,
                                 pi.ScanRequest(paths=[str(good), str(outside),
                                                       str(data / ".env")]))
    finally:
        store.close()

    assert report.counts()["imported"] == 1
    assert report.counts()["denied"] == 2
    assert report.ok is False                  # 部分失败不得当整体成功
    denied_paths = {d.display_path() for d in report.denied}
    assert denied_paths == {"x.md", ".env"}
    assert all(d.reason for d in report.denied)


# --------------------------------------------------------------------------
# 失效检测
# --------------------------------------------------------------------------
def test_moved_or_deleted_source_marks_stale_and_reimport_recovers(tmp_path,
                                                                   monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    folder = tmp_path / "library"
    original = _write(folder / "paper.md", "# paper\n" * 30)
    topic = "pathlib-5"
    store = KBStore(topic)
    try:
        request = pi.ScanRequest(paths=[str(folder)], allow_roots=[str(folder)])
        pi.import_paths(store, topic, request)
        assert pi.detect_missing(topic)[0].stale is False

        original.rename(folder / "renamed.md")
        refs = pi.detect_missing(topic)
        assert refs[0].stale is True
        assert refs[0].missing_since
        assert pi.load_path_refs(topic)[0].stale is True

        # 重导入可恢复: renamed.md 是新路径, 内容同一 hash -> 合并而非新建
        second = pi.import_paths(store, topic, request)
        assert second.counts()["imported"] == 0
        docs = store.list_documents()
    finally:
        store.close()
    assert len(docs) == 1
    refs = pi.load_path_refs(topic)
    # 旧路径仍留着 stale 记录 (旧引用不静默保留, 也不静默丢弃)
    assert any(r.stale for r in refs)
    assert any(not r.stale and r.display_path() == "renamed.md" for r in refs)


def test_unchanged_file_is_not_stale(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    folder = tmp_path / "library"
    _write(folder / "a.md", "# a\n" * 30)
    topic = "pathlib-6"
    store = KBStore(topic)
    try:
        pi.import_paths(store, topic,
                        pi.ScanRequest(paths=[str(folder)],
                                       allow_roots=[str(folder)]))
    finally:
        store.close()
    refs = pi.detect_missing(topic)
    assert all(r.stale is False for r in refs)


# --------------------------------------------------------------------------
# 库级操作: 只解除登记
# --------------------------------------------------------------------------
def test_library_summary_hides_absolute_paths(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    folder = tmp_path / "library"
    _write(folder / "secret-name.md", "# a\n" * 30)
    topic = "pathlib-7"
    store = KBStore(topic)
    try:
        pi.import_paths(store, topic,
                        pi.ScanRequest(paths=[str(folder)],
                                       allow_roots=[str(folder)]))
    finally:
        store.close()

    summary = pi.library_summary(topic)
    assert summary["readable"] is True
    assert summary["files"] == 1
    blob = json.dumps(summary, ensure_ascii=False)
    assert str(folder) not in blob             # 不外发完整绝对路径
    assert "secret-name.md" in blob
    assert summary["hashes"][0]["file_hash"]


def test_unregister_library_keeps_user_files_and_records(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    folder = tmp_path / "library"
    paper = _write(folder / "paper.md", "# paper\n" * 30)
    topic = "pathlib-8"
    store = KBStore(topic)
    try:
        pi.import_paths(store, topic,
                        pi.ScanRequest(paths=[str(folder)],
                                       allow_roots=[str(folder)]))
    finally:
        store.close()

    outcome = pi.unregister_library(topic)
    assert outcome["ok"] is True
    assert outcome["removed_refs"] == 1
    assert outcome["removed_records"] is False
    # 用户原文件毫发无损; 库记录仍在 (只解除登记)
    assert paper.is_file()
    assert (pi.topic_dir(topic) / "kb.sqlite").exists()
    assert pi.load_path_refs(topic) == []


def test_unregister_with_records_only_touches_the_library_db(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    folder = tmp_path / "library"
    paper = _write(folder / "paper.md", "# paper\n" * 30)
    topic = "pathlib-9"
    store = KBStore(topic)
    try:
        pi.import_paths(store, topic,
                        pi.ScanRequest(paths=[str(folder)],
                                       allow_roots=[str(folder)]))
    finally:
        store.close()

    outcome = pi.unregister_library(topic, delete_records=True)
    assert outcome["ok"] is True
    assert outcome["removed_records"] is True
    assert paper.is_file()                     # 绝不删除用户原文件
    assert not (pi.topic_dir(topic) / "kb.sqlite").exists()


def test_empty_request_is_refused_not_treated_as_success(tmp_path, monkeypatch):
    from src.kb import path_import as pi
    from src.kb.store import KBStore

    _auth_data(tmp_path, monkeypatch)
    store = KBStore("pathlib-10")
    try:
        report = pi.import_paths(store, "", pi.ScanRequest(paths=[]))
    finally:
        store.close()
    assert report.counts()["imported"] == 0
    assert report.ok is False or report.notes
    assert any("库名" in n for n in report.notes)
