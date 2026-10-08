"""HTTP contract for the single team entry point."""

from fastapi.testclient import TestClient


def test_start_uses_team_without_a_client_mode(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setenv("THEORY_LLM", "0")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    from src import server

    monkeypatch.setattr(server, "DATA_DIR", config.DATA_DIR)
    monkeypatch.setattr(server, "OUTPUT_DIR", config.OUTPUT_DIR)
    monkeypatch.setattr(server, "CHECKPOINT_DIR", config.DATA_DIR / "checkpoints")

    def finish_immediately(session, _initial_state):
        session.status = "done"
        session.emit({"type": "done"})

    monkeypatch.setattr(server, "_run_session", finish_immediately)
    try:
        with TestClient(server.app) as client:
            before = set(server.SESSIONS)
            ambiguous = client.post("/api/sessions", json={
                "request": "是否存在一条长度 333 的金属导线？",
                "project_id": "http-entry", "problem_id": "ambiguous",
            })
            assert ambiguous.status_code == 422, ambiguous.text
            assert ambiguous.json()["detail"]["needs_clarification"] is True
            assert set(server.SESSIONS) == before
            response = client.post("/api/sessions", json={
                "request": "证明对所有实数 x，有 x² ≥ 0，并说明等号条件。",
                "project_id": "http-entry", "problem_id": "formal",
                "source_policy": "user_kb",
            })
            assert response.status_code == 200, response.text
            started = response.json()
            assert started["mode"] == "team"
            assert (started["project_id"], started["problem_id"]) == ("http-entry", "formal")
            assert started["run_id"] and started["thread_id"]
            state = client.get(f"/api/sessions/{started['thread_id']}/state")
            assert state.status_code == 200
            assert state.json()["run_id"] == started["run_id"]
            assert state.json()["mode"] == "team"
            assert client.post(f"/api/sessions/{started['thread_id']}/respond",
                               json={"response": "重复回答"}).status_code == 409
    finally:
        server.shutdown_sessions()
