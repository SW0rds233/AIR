"""Folder selection and same-name library merge with conversation binding."""

from pathlib import Path

from fastapi.testclient import TestClient


SID_A = "1" * 32
SID_B = "2" * 32
SID_C = "3" * 32


def _client(tmp_path, monkeypatch):
    from src import config, server

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir()
    return TestClient(server.app, base_url="http://127.0.0.1",
                      client=("127.0.0.1", 50000))


def _conversation(session_id):
    from src.utils.conversation_store import save_conversation

    save_conversation(session_id, {
        "session_id": session_id, "thread_id": f"thread-{session_id}",
        "request": {"source_set_id": ""}, "messages": [],
    })


def _import(client, folder: Path, session_id: str):
    response = client.post("/api/library/import", json={
        "paths": [str(folder)], "topic": "星网 星链", "label": "星网 星链",
        "idempotency_key": "same-label", "session_id": session_id,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_same_name_merges_new_files_and_binds_each_conversation(tmp_path, monkeypatch):
    from src.kb.store import KBStore
    from src.utils.conversation_store import load_conversation

    client = _client(tmp_path, monkeypatch)
    first_folder = tmp_path / "data" / "first"
    second_folder = tmp_path / "data" / "second"
    first_folder.mkdir()
    second_folder.mkdir()
    (first_folder / "a.txt").write_text("卫星互联网架构与星链综述。", encoding="utf-8")
    (second_folder / "b.txt").write_text("低轨星座通信与链路分析。", encoding="utf-8")
    for sid in (SID_A, SID_B, SID_C):
        _conversation(sid)

    first = _import(client, first_folder, SID_A)
    assert first["library_existed"] is False
    assert first["counts"]["imported"] == 1
    assert first["bound_session_id"] == SID_A

    second = _import(client, second_folder, SID_B)
    assert second["library_existed"] is True
    assert second["cached"] is False
    assert second["counts"]["imported"] == 1
    assert second["bound_session_id"] == SID_B

    cached = _import(client, second_folder, SID_C)
    assert cached["cached"] is True
    assert cached["bound_session_id"] == SID_C
    assert len(KBStore("星网 星链").list_documents()) == 2
    for sid in (SID_A, SID_B, SID_C):
        record = load_conversation(sid)
        assert record["bound_source_set_id"] == "星网_星链"
        assert record["request"]["source_set_id"] == ""


def test_library_name_uses_canonical_source_set_id(tmp_path, monkeypatch):
    from src.kb.store import topic_dir
    from src.utils.conversation_store import load_conversation

    client = _client(tmp_path, monkeypatch)
    folder = tmp_path / "data" / "papers"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("星网与中国航海业通信研究。", encoding="utf-8")
    _conversation(SID_A)

    label = "探针 库名"
    request = {
        "paths": [str(folder)], "topic": label, "label": label,
        "idempotency_key": "spaced-label", "session_id": SID_A,
    }
    response = client.post("/api/library/import", json=request)
    assert response.status_code == 200, response.text
    report = response.json()
    sources_response = client.get("/api/sources")
    assert sources_response.status_code == 200, sources_response.text
    listed = {row["source_set_id"] for row in sources_response.json()["sources"]}

    assert report["topic"] == label
    assert report["source_set_id"] == topic_dir(label).name == "探针_库名"
    assert report["source_set_id"] in listed
    assert load_conversation(SID_A)["bound_source_set_id"] == report["source_set_id"]

    cached = client.post("/api/library/import", json=request).json()
    assert cached["cached"] is True
    assert cached["source_set_id"] == report["source_set_id"]


def test_import_rejects_unknown_conversation_before_creating_library(tmp_path, monkeypatch):
    from src.kb.store import default_db_path

    client = _client(tmp_path, monkeypatch)
    folder = tmp_path / "data" / "papers"
    folder.mkdir()
    (folder / "a.txt").write_text("星链研究资料。", encoding="utf-8")
    response = client.post("/api/library/import", json={
        "paths": [str(folder)], "label": "星网 星链", "session_id": SID_A,
    })
    assert response.status_code == 404
    assert not default_db_path("星网 星链").exists()


def test_live_binding_survives_persistence_without_changing_run_snapshot(tmp_path, monkeypatch):
    from src import config
    from src.sessions.controller import Session
    from src.sessions.store import SESSIONS, bind_conversation_library, persist_session
    from src.utils.conversation_store import load_conversation

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    session = Session("thread-live", session_id=SID_A, app=None)
    session.request = {"source_set_id": "old-library"}
    SESSIONS[session.thread_id] = session
    try:
        persist_session(session)
        bind_conversation_library(SID_A, "new-library")
        persist_session(session)
        record = load_conversation(SID_A)
        assert record["bound_source_set_id"] == "new-library"
        assert record["request"]["source_set_id"] == "old-library"
    finally:
        SESSIONS.pop(session.thread_id, None)


def test_folder_picker_is_local_only_and_cancellation_is_harmless(tmp_path, monkeypatch):
    from src.kb import folder_picker

    client = _client(tmp_path, monkeypatch)
    selected = tmp_path / "data" / "papers"
    selected.mkdir()
    monkeypatch.setattr(folder_picker, "pick_folder", lambda: selected)
    response = client.post("/api/library/pick-folder",
                           headers={"Origin": "http://127.0.0.1:8000"})
    assert response.status_code == 200
    assert response.json()["path"] == str(selected)
    assert response.json()["cancelled"] is False
    assert response.json()["grant_token"]

    denied = client.post("/api/library/pick-folder",
                         headers={"Origin": "https://foreign.example"})
    assert denied.status_code == 403
    monkeypatch.setattr(folder_picker, "pick_folder", lambda: None)
    cancelled = client.post("/api/library/pick-folder")
    assert cancelled.json() == {"path": "", "cancelled": True, "grant_token": ""}


def test_native_choice_authorizes_only_the_chosen_external_folder(tmp_path, monkeypatch):
    from src.kb import folder_picker

    client = _client(tmp_path, monkeypatch)
    external = tmp_path / "outside-data"
    external.mkdir()
    (external / "paper.txt").write_text("低轨卫星通信研究。", encoding="utf-8")
    other = tmp_path / "other-folder"
    other.mkdir()
    (other / "secret.txt").write_text("未授权目录。", encoding="utf-8")
    # HTTP 请求不能自行扩展授权根。
    denied = client.post("/api/library/scan", json={
        "paths": [str(external)], "allow_roots": [str(external)],
    })
    assert denied.status_code == 400
    assert "allow_roots" in denied.json()["detail"]
    rejected = client.post("/api/library/import", json={
        "paths": [str(external)], "label": "外部资料",
        "allow_roots": [str(external)],
    })
    assert rejected.status_code == 400
    assert "allow_roots" in rejected.json()["detail"]

    monkeypatch.setattr(folder_picker, "pick_folder", lambda: external)
    token = client.post("/api/library/pick-folder").json()["grant_token"]
    scan = client.post("/api/library/scan", json={
        "paths": [str(external)], "folder_grants": [token],
    }).json()
    assert scan["counts"]["ok"] == 1
    unrelated = client.post("/api/library/scan", json={
        "paths": [str(other)], "folder_grants": [token],
    }).json()
    assert unrelated["counts"]["ok"] == 0

    _conversation(SID_A)
    report = client.post("/api/library/import", json={
        "paths": [str(external)], "label": "外部资料", "folder_grants": [token],
        "session_id": SID_A,
    }).json()
    assert report["counts"]["imported"] == 1
    assert report["bound_session_id"] == SID_A
