"""Focused checks for visible, durable in-task progress."""

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from src.agents.protocol import TaskBudget, UsageRecord
from src.agents.runtime import BudgetedLLM, ToolLoop, ToolSpec, _safe_tool_arguments
from src.graph.team_session import TeamApp, TeamSession
from src.research.progress import append_run_event


def test_bound_llm_emits_visible_output_but_not_private_reasoning():
    events = []

    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(content="<think>private scratchpad</think>公开的阶段结论",
                             additional_kwargs={"reasoning_content": "hidden"})

    llm = BudgetedLLM(Model(), UsageRecord(), TaskBudget(), task_id="t",
                      stage="evidence", on_event=lambda kind, payload: events.append((kind, payload)))
    llm.bind_tools([]).invoke([])
    assert [kind for kind, _ in events] == ["llm_started", "llm_result"]
    assert events[-1][1]["output_preview"] == "公开的阶段结论"
    assert "hidden" not in json.dumps(events, ensure_ascii=False)


def test_tool_progress_distinguishes_start_and_result():
    events = []

    class Model:
        calls = 0

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{"id": "1", "name": "lookup",
                                                          "args": {"query": "paper"}}])
            return AIMessage(content="完成")

    loop = ToolLoop([ToolSpec(name="lookup", fn=lambda query: "找到资料", description="lookup")],
                    sink=SimpleNamespace(event=lambda kind, payload: events.append((kind, payload))),
                    agent="evidence")
    loop.run(Model(), [], task_id="t")
    assert [kind for kind, _ in events] == ["tool_started", "tool_run"]
    assert events[0][1]["arguments"] == {"query": "paper"}
    assert events[1][1]["result_preview"] == "找到资料"


def test_tool_credentials_are_redacted_in_ui_and_journal(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    arguments = {
        "key": "sk-plain-key-111", "Authorization": "Bearer sk-auth-222",
        "headers": {"Authorization": "Bearer sk-hdr-333"},
        "env": "OPENAI_API_KEY=sk-env-444", "apiKey": "sk-camel-777",
        "passwd": "pw-999", "query": "Bearer sk-query-12345",
    }
    emitted = []
    team = SimpleNamespace(project_id="p", problem_id="q", run_id="r",
                           runtime=SimpleNamespace(sink=SimpleNamespace(event=lambda *args: None)))
    TeamSession(team, emit=emitted.append)
    team.runtime.sink.event("tool_started", {"arguments": arguments})
    append_run_event(project_id="p", problem_id="q", run_id="direct",
                     kind="tool_started", payload={"arguments": arguments})

    visible = json.dumps(_safe_tool_arguments(arguments), ensure_ascii=False)
    live = json.dumps(emitted, ensure_ascii=False)
    journal = (tmp_path / "research" / "p" / "direct" / "progress" /
               "events.jsonl").read_text(encoding="utf-8")
    for secret in ("sk-plain-key-111", "sk-auth-222", "sk-hdr-333",
                   "sk-env-444", "sk-camel-777", "pw-999", "sk-query-12345"):
        assert secret not in visible + live + journal


def test_team_event_reaches_ui_before_task_finishes_and_is_durable(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    emitted = []
    original = SimpleNamespace(event=lambda kind, payload: None)
    team = SimpleNamespace(project_id="p", problem_id="q", run_id="r",
                           runtime=SimpleNamespace(sink=original))
    TeamSession(team, emit=emitted.append)
    team.runtime.sink.event("llm_started", {"agent": "evidence", "task_id": "t"})
    assert emitted[0]["type"] == "node"
    assert emitted[0]["team_event"]["event"] == "llm_started"
    journal = tmp_path / "research" / "p" / "r" / "progress" / "events.jsonl"
    assert json.loads(journal.read_text(encoding="utf-8").splitlines()[0])["event"] == "llm_started"


def test_live_team_events_are_not_replayed_after_step(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    emitted = []
    loop = SimpleNamespace(brief=object(), finished=False)
    # stream() 现在会尝试继续暂缓任务并读取 team.outcome.delivery, 替身补齐这两个入口
    team = SimpleNamespace(project_id="p", problem_id="q", run_id="r", loop=loop,
                           runtime=SimpleNamespace(sink=SimpleNamespace(event=lambda *args: None)),
                           outcome=SimpleNamespace(delivery={}),
                           cancel=SimpleNamespace(cancelled=False),
                           prepare=lambda: None, reopen_deferred=lambda: False)
    session = TeamSession(team, emit=emitted.append)

    def step():
        team.runtime.sink.event("task_started", {"agent": "evidence", "task_id": "t"})
        loop.finished = True
        return False

    team.step = step
    session._clarification_pending = lambda: False
    session.export = lambda: None
    session.outcome_summary = lambda: {}
    assert [next(iter(TeamApp(session).stream()))] == [{"research_team_done": {
        "node": "research_team_done", "summary": {}, "package_dir": "", "delivery": {}}}]
    assert [event["team_event"]["event"] for event in emitted] == ["task_started"]
