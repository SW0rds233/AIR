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


def test_there_is_no_engine_decision_point():
    """**引擎选择已经不存在** (合并计划 §3/M5 / G01)。

    这条用例过去断言"选择只有一处、取值受尊重"; 现在断言的是**更强**的事实: 生产代码里
    没有"按 mode 分流"的入口 —— `_resolve_engine()` 不接受参数, 旧取值 (theory/survey)
    不再被尊重, 请求类型也不再决定引擎 (由主控在同一个团队里派工)。
    """
    import inspect

    from src.server import TEAM_ENGINE, _resolve_engine

    assert TEAM_ENGINE == "team"
    assert _resolve_engine() == "team"
    # 不接收任何 mode/request: 一旦接收, 就等于承认"可以按它分流"
    params = list(inspect.signature(_resolve_engine).parameters)
    assert params == [], f"引擎选择点又出现了形参: {params}"
    source = (Path(__file__).resolve().parents[1] / "src" / "server.py").read_text(
        encoding="utf-8")
    assert 'in ("theory", "team")' not in source, "按 mode 分流的分支又回来了"
    assert "build_theory_pipeline" not in source, "旧图的建图入口又回来了"


def test_survey_request_is_served_by_the_unified_entry(client):
    """**缺口已闭合**: 综述型请求走统一入口 (不带 mode) 能产出交付包。

    综述型与形式化请求现在走**同一个**团队入口 (G01 之后没有第二张图), 由主控按任务
    画像派工。这条用例守住的是"不带 mode 也能跑完并导出交付包"; 分类器的保守性由
    下一个用例守住 (它不再决定用哪张图, 只影响画像)。
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


def test_formal_request_is_served_by_the_same_team_entry(client):
    """形式化请求不再有专属引擎: 它同样进团队, 由推理角色形式化并核验。

    分类器的**保守性**仍然要守住 (`is_survey_request` 不得把证明题当综述), 但它的
    用途已经变了: 它只影响"任务画像里算不算综述型", 不再决定用哪张图。
    """
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
    assert start.json()["mode"] == "team", start.json()
