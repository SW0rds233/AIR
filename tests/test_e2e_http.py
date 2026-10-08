"""Bounded offline research through the actual session HTTP API."""

import time

from fastapi.testclient import TestClient


def test_offline_team_run_is_visible_through_http(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server
    from src.graph import research_graph

    monkeypatch.setattr(server, "DATA_DIR", config.DATA_DIR)
    monkeypatch.setattr(server, "OUTPUT_DIR", config.OUTPUT_DIR)
    monkeypatch.setattr(server, "CHECKPOINT_DIR", config.DATA_DIR / "checkpoints")
    original_team = research_graph.TeamRun

    def bounded_team(*args, **kwargs):
        kwargs["max_rounds"] = 3
        return original_team(*args, **kwargs)

    monkeypatch.setattr(research_graph, "TeamRun", bounded_team)
    try:
        with TestClient(server.app) as client:
            response = client.post("/api/sessions", json={
                "request": "证明对所有实数 x，有 x² ≥ 0，并说明等号条件。",
                "project_id": "http-e2e", "problem_id": "formal",
                "source_policy": "user_kb",
            })
            assert response.status_code == 200, response.text
            started = response.json()
            assert started["mode"] == "team"
            deadline = time.monotonic() + 90
            state = {}
            while time.monotonic() < deadline:
                reply = client.get(f"/api/sessions/{started['thread_id']}/state")
                assert reply.status_code == 200, reply.text
                state = reply.json()
                if state["status"] in {"done", "error", "waiting"}:
                    break
                time.sleep(0.1)
            assert state.get("status") == "done", state
            assert (state["project_id"], state["problem_id"], state["run_id"]) == (
                "http-e2e", "formal", started["run_id"])
            assert state["event_seq"] > 0
            listing = client.get("/api/artifacts", params={
                "project_id": "http-e2e", "problem_id": "formal",
                "run_id": started["run_id"]})
            assert listing.status_code == 200, listing.text
            names = [row["name"] for row in listing.json()["files"]]
            assert any(name.endswith("manifest.json") for name in names), names
    finally:
        server.shutdown_sessions()
