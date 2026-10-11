"""Conversation ownership for records, downloaded PDFs and machine literature."""

import json
from pathlib import Path

from src.utils.conversation_layout import (conversation_dir, conversation_topic,
                                           pdf_dir, session_id_from_topic)
from src.utils.conversation_store import (delete_conversation, list_conversations,
                                          load_conversation, save_conversation)


SID = "0123456789abcdef0123456789abcdef"


def test_conversation_record_and_legacy_read(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    root = tmp_path / "conversations"
    root.mkdir()
    (root / "old.json").write_text(json.dumps({"session_id": "old", "messages": []}),
                                   encoding="utf-8")
    assert load_conversation("old")["session_id"] == "old"

    save_conversation(SID, {"messages": [{"content": "hello"}]})
    assert (conversation_dir(SID) / "conversation.json").is_file()
    assert load_conversation(SID)["messages"][0]["content"] == "hello"
    assert {row["session_id"] for row in list_conversations()} == {"old", SID}

    shared = tmp_path / "kb" / "manual" / "kb.sqlite"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"shared")
    pdf = pdf_dir(SID) / "paper.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    assert delete_conversation(SID)
    assert not conversation_dir(SID).exists()
    assert shared.read_bytes() == b"shared"
    assert load_conversation("old") is not None


def test_conversation_kb_and_download_paths(tmp_path, monkeypatch):
    from src import config
    from src.kb.store import KBStore, default_db_path

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    topic = conversation_topic(SID)
    assert session_id_from_topic(topic) == SID
    assert default_db_path(topic) == conversation_dir(SID) / "literature" / "kb.sqlite"
    assert pdf_dir(SID) == conversation_dir(SID) / "literature" / "pdfs"
    assert session_id_from_topic("conversation-my-manual-library") is None
    store = KBStore(topic)
    store.close()
    assert default_db_path(topic).is_file()


def test_pdf_batch_uses_conversation_destination(tmp_path, monkeypatch):
    from src.tools import pdf_fetcher

    target = tmp_path / "pdfs"
    seen = []

    def fake_arxiv(url, *, save_dir, nice_name):
        seen.append(save_dir)
        path = save_dir / nice_name
        path.write_bytes(b"%PDF example")
        return str(path)

    monkeypatch.setattr(pdf_fetcher, "download_arxiv_pdf", fake_arxiv)
    monkeypatch.setattr("src.utils.http_client.circuit_remaining_cooldown",
                        lambda url: 0)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    papers = [{"title": "Example", "url": "https://arxiv.org/abs/2401.12345"}]
    result = pdf_fetcher.download_pdfs_for_papers(papers, limit=1, save_dir=target)
    assert seen == [target]
    assert Path(result[0]["pdf_path"]).parent == target


def test_autonomous_web_task_uses_its_conversation_library():
    from src.agents.evidence import _topic_of
    from src.agents.protocol import AgentTask, ContextPack

    task = AgentTask(agent="evidence", objective="Find sources", session_id=SID,
                     source_policy="autonomous", project_id="shared-project")
    assert _topic_of(task, ContextPack(request="Starlink literature")) == conversation_topic(SID)


def test_both_policy_routes_network_ingestion_to_session(tmp_path, monkeypatch):
    from src import config
    from src.kb import bridge
    from src.research.schemas import Claim, SourcePolicy

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    visited = []

    def harvest(queries, policy, coverage, *, topic, **kwargs):
        visited.append(topic)
        return []

    monkeypatch.setattr(bridge, "_harvest_external", harvest)
    bridge.gather_sources(None, None, topic="user-library",
                          external_topic=conversation_topic(SID),
                          policy=SourcePolicy.both,
                          claim=Claim(statement="Starlink spectrum"))
    assert visited == [conversation_topic(SID)]
    assert not (tmp_path / "kb" / "user-library").exists()


def test_session_downloads_are_not_manual_import_candidates(tmp_path, monkeypatch):
    from src import config
    from src.kb.path_import import _generated_source_reason

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    pdf = pdf_dir(SID) / "paper.pdf"
    assert "自动下载缓存" in _generated_source_reason(pdf.resolve())
