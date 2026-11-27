import json
from types import SimpleNamespace

from src.agents.protocol import AgentTask, ContextPack, UsageRecord
from src.agents.writing import WritingAgent
from src.research.commit import ResearchCommitService
from src.research.progress import write_run_progress
from src.research.projection import object_rows
from src.research.store import ResearchStore


def draft(task, rows):
    return WritingAgent()._run(
        task, ContextPack(request="研究问题", objects={"manuscript": rows}),
        SimpleNamespace(llm_available=lambda *_: False), UsageRecord(), [])


def test_fresh_draft_replay_and_revision_share_one_storage_object(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.OUTPUT_DIR", tmp_path)
    store = ResearchStore("p", tmp_path / "test.sqlite")
    try:
        create = AgentTask(project_id="p", problem_id="q", run_id="r",
                           task_id="first", agent="writing", objective="写作")
        first = draft(create, [])
        object_id = first.proposed_changes[0].object_id
        assert object_id and draft(create, []).proposed_changes[0].object_id == object_id
        commit = ResearchCommitService(store)
        assert commit.commit(create, first).committed
        assert commit.commit(create, first).idempotent
        revise = create.model_copy(update={"task_id": "revise", "objective": "修订"})
        second = draft(revise, object_rows(store, "manuscript", run_id="r"))
        assert commit.commit(revise, second).committed
        root = write_run_progress(store, project_id="p", problem_id="q", run_id="r",
                                  task=revise, result=second)
        history = json.loads((root / "manuscript-history.json").read_text("utf-8"))
        assert [(row["id"], row["version"]) for row in history] == [(object_id, 1), (object_id, 2)]
        assert all(row["sections"] for row in history)
        assert len(json.loads((root / "manuscript.json").read_text("utf-8"))) == 1
    finally:
        store.close()


def test_legacy_split_identity_keeps_manuscript_version_and_scope(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.OUTPUT_DIR", tmp_path)
    store = ResearchStore("p", tmp_path / "legacy.sqlite")
    try:
        for key, version, run in [("manobj-legacy", 1, "r"), ("ms-legacy", 2, "r"),
                                  ("ms-other", 1, "other-run")]:
            store.put("manuscript", key, {"id": key, "manuscript_id": key if run != "r" else "ms-legacy",
                      "version": version, "_scope": {"run_id": run}})
        rows = object_rows(store, "manuscript", run_id="r")
        old = next(row for row in rows if row["id"] == "ms-legacy")
        assert old["version"] == 2 and old["_storage_revision"] == 1
        revise = AgentTask(project_id="p", problem_id="q", run_id="r", agent="writing", objective="修订")
        result = draft(revise, [old])
        assert ResearchCommitService(store).commit(revise, result).committed
        root = write_run_progress(store, project_id="p", problem_id="q", run_id="r", task=revise, result=result)
        history = json.loads((root / "manuscript-history.json").read_text("utf-8"))
        assert [(row["id"], row["version"]) for row in history] == [
            ("manobj-legacy", 1), ("ms-legacy", 2), ("ms-legacy", 3)]
    finally:
        store.close()
