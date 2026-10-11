"""资料库路径导入的幂等语义回归。

背景（2026-10-11 实测，库 `星网 星链`）：幂等键原本只由「库名 + 输入路径字符串」
决定，与目录里有哪些文件无关。于是往同一目录新增文件后再点「确认导入」，服务端
命中旧缓存并原样返回上一次的报告 —— 界面显示"将入库 42"，导入结果却是几天前的
"新增 12 · 合并 1"，新增的 29 个文件从未入库，而界面还报"所有命中文件都已入库"。

这里钉住修好之后的三条语义：
1. 内容一致时重复导入 → 命中缓存，且报告**如实标注** `cached`；
2. 目录新增文件 → 缓存失效，**新文件必须真的入库**；
3. 计数由服务端给出（`counts`），不再依赖前端各算各的 `len()`。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    """把 DATA_DIR/OUTPUT_DIR 指到临时目录，使授权根与库都隔离在 tmp 里。"""
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    from src import server

    monkeypatch.setattr(server, "DATA_DIR", config.DATA_DIR)
    monkeypatch.setattr(server, "OUTPUT_DIR", config.OUTPUT_DIR)
    return TestClient(server.app)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _import(client: TestClient, papers: Path, key: str = "k1") -> dict:
    response = client.post("/api/library/import", json={
        "paths": [str(papers)], "topic": "t", "embed": False, "idempotency_key": key})
    assert response.status_code == 200, response.text
    return response.json()


def test_unchanged_directory_hits_cache_and_is_labelled(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    papers = tmp_path / "data" / "papers"
    _write(papers / "a.txt", "卫星互联网 星链 综述。")

    first = _import(client, papers)
    assert first["cached"] is False
    assert first["counts"]["imported"] == 1
    # 服务端给出 counts，界面不必自己数列表
    assert first["counts"]["imported"] == len(first["imported"])

    again = _import(client, papers)
    assert again["cached"] is True, "内容未变时应命中缓存"
    # 命中缓存返回的是**上次那份报告**：计数与首次一致（它记录的是那次做了什么），
    # 但显式标注 `cached`，界面据此不得再报"导入完成"
    assert again["counts"] == first["counts"]
    assert again["batch_id"] == first["batch_id"], "应返回上次那份报告"
    assert any("命中幂等缓存" in note for note in again["notes"])


def test_adding_files_to_the_same_directory_imports_them(tmp_path, monkeypatch):
    """本次回归：同一目录新增文件后，**必须真正导入**，而不是回旧报告。"""
    client = _client(tmp_path, monkeypatch)
    papers = tmp_path / "data" / "papers"
    _write(papers / "a.txt", "卫星互联网 星链 综述。")

    first = _import(client, papers)
    assert first["counts"]["imported"] == 1

    # 往同一目录新增文件（内容签名变化，客户端键保持不变）
    _write(papers / "b.txt", "低轨卫星 星间激光通信 分析。")

    second = _import(client, papers)
    assert second["cached"] is False, "目录已变化，不得再命中旧缓存"
    assert second["counts"]["imported"] == 1, "新增的文件必须入库"
    assert any("b.txt" in (row.get("path") or "") for row in second["imported"])

    # 再点一次：这次内容一致，应命中缓存
    third = _import(client, papers)
    assert third["cached"] is True

    # 库里确实有两篇
    from src.kb.store import KBStore

    assert len(KBStore("t").list_documents()) == 2


def test_signature_changes_with_same_size_edit(tmp_path, monkeypatch):
    """内容签名含 mtime：同长度改写也应被视为变化（否则新版本会被旧缓存挡住）。"""
    from src.kb import path_import as pi

    papers = tmp_path / "papers"
    _write(papers / "a.txt", "AAAA")
    first = pi.scan_paths(pi.ScanRequest(paths=[str(papers)], allow_roots=[str(papers)]))
    sig1 = pi.scan_signature(first)

    import time

    time.sleep(0.01)
    _write(papers / "a.txt", "BBBB")          # 同字节数，不同内容
    second = pi.scan_paths(pi.ScanRequest(paths=[str(papers)], allow_roots=[str(papers)]))
    assert pi.scan_signature(second) != sig1, "同长度的改写必须改变签名"


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_textual_sources_are_importable(tmp_path, monkeypatch, suffix):
    """回归用 .txt/.md 做夹具，这里显式钉住它们确实可被解析入库。"""
    client = _client(tmp_path, monkeypatch)
    papers = tmp_path / "data" / "papers"
    _write(papers / f"a{suffix}", "卫星互联网 承载网 白皮书 要点。")

    report = _import(client, papers, key=f"k-{suffix}")
    assert report["counts"]["imported"] == 1
    assert report["counts"]["failed"] == 0
