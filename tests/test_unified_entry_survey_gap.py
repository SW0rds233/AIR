from __future__ import annotations

"""统一入口的**能力边界**: 综述型请求目前还不能由默认真式引擎交付。

这个文件不是"验收通过"的用例, 而是一条**如实记录缺口**的回归: 在界面已经
只留一个入口(不再有模式选择)的前提下, 必须能回答"用户提综述型请求时会发生什么"。

实测 (离线) 结论: 默认真式引擎把"检索并总结某方向的研究进展"判为 `scenario`,
没有形式化对象可判定 → `needs_clarification=True`, 交付"条件性研究报告",
`claims` 与 `evidence` 均为空, 工作台投影里 0 条结论 / 0 条证据。

因此**旧综述引擎 (`graph/pipeline.py`) 还不能删除** —— 它仍承担这一类真实请求。
一旦团队会话引擎能产出综述交付包 (检索 → 笔记综合 → 写作 → 配图 → 交付包),
本用例的两个断言应当反转: 那时综述型请求必须能产出非空证据与"论文草稿"以上等级,
并且旧图可以随之删除 (见合并计划 §15.1 的下一步)。

保持这条用例的价值: 它把"重构有没有把一类请求悄悄弄坏"变成红灯, 而不是靠记忆。
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SURVEY_REQUEST = "检索并总结「射频指纹识别」方向近年的研究进展, 写一篇综述"


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


def test_engine_resolution_is_a_single_decision_point():
    """引擎选择只有一处: 形式化请求走理论引擎, 综述型请求走团队会话引擎。

    旧的 `survey` 取值已随 stage 图一起退役 —— 它不再是一个可选引擎。
    """
    from src.server import DEFAULT_ENGINE, _resolve_engine

    assert _resolve_engine("") == DEFAULT_ENGINE
    assert _resolve_engine("  ") == DEFAULT_ENGINE
    assert _resolve_engine("theory") == "theory"
    assert _resolve_engine("team") == "team"
    # 综述型请求 → 团队会话引擎 (不再有第二个综述引擎)
    assert _resolve_engine("", request="检索并总结某方向研究进展, 写一篇综述") == "team"
    # 形式化请求 → 理论引擎
    assert _resolve_engine("", request="证明 2-(211,15,1) 设计不存在") == "theory"
    # 退役取值与未识别取值都不得把请求送进未知引擎
    assert _resolve_engine("survey") == DEFAULT_ENGINE
    assert _resolve_engine("nonsense") == DEFAULT_ENGINE


def test_survey_request_is_served_by_the_unified_entry(client):
    """**缺口已闭合**: 综述型请求走统一入口 (不带 mode) 能产出交付包。

    本轮之前这里是反过来的断言 (请求澄清 + 零结论); 现在综述型请求由
    `research.intake.is_survey_request` 识别并交给团队会话引擎, 由
    Evidence/Reasoning/Writing 角色跑完并导出交付包。

    这条用例同时守住"分类不得过度触发": 形式化请求 (见下一个用例) 必须仍走
    形式化引擎, 不能被综述流程糊弄。
    """
    start = client.post("/api/sessions", json={
        "request": SURVEY_REQUEST,
        "topic": "射频指纹识别研究进展",
        "project_id": "gap-survey",
        "problem_id": "p1",
        "max_actions": 12,
        "max_tool_calls": 12,
    })
    assert start.status_code == 200, start.text[:400]
    payload = start.json()
    # 服务端自己决定引擎; 界面没有模式可选
    assert payload["mode"] == "team", payload
    state = _wait_done(client, payload["thread_id"])
    assert state["status"] == "done", state
    final = state.get("final_state") or {}
    assert final.get("engine") == "team_v1", final
    # 交付包必须真的落盘 (没有可用资料时它就是一份**未决报告**, 而不是空包)
    package_dir = str(final.get("package_dir", ""))
    assert package_dir, final
    package = Path(package_dir)
    assert (package / "manifest.json").is_file()
    assert (package / "unresolved.md").is_file()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.get("delivery_level"), manifest
    summary = final.get("summary") or {}
    # 没有绑定资料源时不得伪造证据; 但必须如实说出缺什么
    assert summary.get("objects", {}).get("evidence", 0) == 0, summary
    unresolved = " ".join(summary.get("unresolved", []))
    assert "资料" in unresolved or "澄清" in unresolved or "来源" in unresolved, summary


def test_formal_request_does_not_get_routed_to_the_survey_engine(client):
    """反例保护: 形式化请求不得被判成综述 (否则证明题会被综述流程糊弄)。"""
    from src.research.intake import is_survey_request

    assert is_survey_request("判断参数为 2-(211,15,1) 的设计是否存在") is False
    # 即使出现"综述"字样, 只要同时有形式化意图, 也按形式化处理
    assert is_survey_request("综述一下 2-(211,15,1) 的设计是否存在的证明") is False
    start = client.post("/api/sessions", json={
        "request": "判断参数为 2-(211,15,1) 的设计是否存在",
        "topic": "problem2", "project_id": "gap-formal", "problem_id": "p1",
        "max_actions": 6, "max_tool_calls": 6,
    })
    assert start.status_code == 200, start.text[:400]
    assert start.json()["mode"] == "theory", start.json()
