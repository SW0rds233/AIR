from __future__ import annotations

"""统一入口的 HTTP 端到端验收 (合并计划 §3 / §9.4 / M5)。

用户不再选择"综述/理论模式": 启动请求**不带 `mode`**, 服务端按统一引擎启动,
返回的身份与交付物必须完整。这里用 `problem2.md` 的题面走真实 HTTP 入口
(离线模型), 核对:

1. 不带 mode 也能启动, 且 `mode` 由服务端决定 (不再是"未指定即综述");
2. 事件流带递增序号且可回放 (前端按游标重连依赖它);
3. 交付包产出完整论文, 且**没有任何模型调用**(离线可复现);
4. 正文可反查性检查说明它核对的是正文本体。
"""

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CASE = Path(__file__).resolve().parents[1] / "problem2.md"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setenv("THEORY_PROPOSER", "0")
    from src import server

    with TestClient(server.app) as c:
        yield c
        for thread_id in list(server.SESSIONS):
            try:
                c.post(f"/api/sessions/{thread_id}/stop")
            except Exception:  # noqa: BLE001 - 清理失败不影响断言
                pass
            server.SESSIONS.pop(thread_id, None)


def _wait_done(c: TestClient, thread_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        resp = c.get(f"/api/sessions/{thread_id}/state")
        assert resp.status_code == 200, resp.text[:300]
        last = resp.json()
        if last.get("status") in ("done", "stopped", "error"):
            return last
        time.sleep(0.2)
    raise AssertionError(f"会话未在 {timeout}s 内收尾: {last.get('status')}")


@pytest.mark.skipif(not CASE.is_file(), reason="缺少 problem2 用例")
def test_mode_free_entry_runs_the_unified_engine(client):
    """不带 mode 的启动请求必须走进统一引擎并产出完整交付包。"""
    body = {
        "request": CASE.read_text(encoding="utf-8"),
        "topic": "problem2",
        "project_id": "unified",
        "problem_id": "p1",
        "max_actions": 30,
        "max_tool_calls": 30,
    }
    assert "mode" not in body
    start = client.post("/api/sessions", json=body)
    assert start.status_code == 200, start.text[:400]
    payload = start.json()
    # 只有一种引擎 (G01): 请求里没有 mode 字段, 回传的是团队引擎
    assert payload["mode"] == "team", payload
    assert payload["project_id"] == "unified" and payload["problem_id"] == "p1"
    assert payload["run_id"], "统一入口同样必须回传运行身份"

    thread_id = payload["thread_id"]
    state = _wait_done(client, thread_id)
    assert state["status"] == "done", state
    assert state["mode"] == "team"

    # 事件流: 序号递增且可回放
    events = client.get(f"/api/sessions/{thread_id}/events", params={"last_event_id": 0})
    assert events.status_code == 200
    seqs = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    assert seqs and seqs == sorted(set(seqs)), seqs

    # 交付包: 完整论文 + 无模型调用 + 可反查性检查口径明确
    listing = client.get("/api/artifacts", params={
        "project_id": "unified", "problem_id": "p1",
        "run_id": payload["run_id"]}).json()
    names = [f["name"] for f in listing["files"]]
    manifest_name = next((n for n in names if n.endswith("manifest.json")), "")
    assert manifest_name, f"交付包必须含 manifest.json: {names[:10]}"
    manifest = json.loads(
        client.get(f"/api/artifacts/{manifest_name}").json()["content"])
    expected_level = "完整论文" if shutil.which("xelatex") else "论文草稿"
    assert manifest["delivery_level"] == expected_level, manifest["delivery_level"]
    assert manifest["usage"]["llm_calls"] == 0, manifest["usage"]
    assert manifest["usage"]["cost_usd"] == 0.0, manifest["usage"]
    trace = manifest.get("manuscript_traceability", {})
    assert trace.get("checked") in ("markdown", "snapshot_only"), trace
    # 多格式同源 (G17): PDF 与 Markdown 来自**同一份**文稿 IR, 清单必须记录编译产物,
    # 且产物列表里真的存在那个文件 (编译成功但没落盘的"看起来有 PDF"要被挡住)。
    pdf_name = str(manifest.get("pdf") or "")
    if shutil.which("xelatex"):
        assert pdf_name.endswith(".pdf"), manifest.get("pdf")
        assert any(n.endswith("manuscript.pdf") for n in names), names[:10]
    else:
        assert not pdf_name and manifest["compilation_status"] in (
            "unavailable", "failed: xelatex 未安装")
