from __future__ import annotations

import asyncio
import builtins
import contextlib
import logging
from types import SimpleNamespace

import pytest
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.panel import Panel

import myagent.cli.layout as layout_module
import myagent.cli.repl as repl_module
from myagent.agent.engine import (
    AskUserQuestion,
    Done,
    Error,
    Interrupted,
    StatusUpdate,
    TextChunk,
    ToolCallEnd,
    ToolCallStart,
)
from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.chat_window import ChatWindowController
from myagent.cli.commands import CommandResult
from myagent.cli.renderer import Renderer
from myagent.cli.repl import REPLEngine
from myagent.cli.rich_capture import capture_renderable, sanitize_terminal_text
from myagent.cli.status import AgentInspectorPane
from myagent.cli.transcript import TranscriptBuffer
from myagent.config.schema import ChatWindowConfig, StatusPaneConfig
from myagent.llm.provider import Usage
from myagent.tools.base import ToolResult


class FakeStatusPane:
    def get_renderable(self, terminal_columns=None):
        return "status"


class FakeConsole:
    def __init__(self):
        self.calls = []

    def print(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class FakeSessionManager:
    def __init__(self, session):
        self.session_store = None
        self.session = session
        self.started_project_dir = None
        self.ended_session = None

    async def start_new(self, project_dir):
        self.started_project_dir = project_dir
        return self.session

    async def end_session(self, session):
        self.ended_session = session


class FakeChatWindowController:
    def __init__(
        self,
        *,
        transcript=None,
        running=True,
        run_error=None,
        submissions=None,
        ask_response="chat answer",
        ask_error=None,
    ):
        self.transcript = transcript or TranscriptBuffer()
        self.is_running = running
        self.run_error = run_error
        self.submissions = list(submissions or [])
        self.ask_response = ask_response
        self.ask_error = ask_error
        self.append_calls = []
        self.tool_calls = []
        self.system_calls = []
        self.error_calls = []
        self.ask_calls = []
        self.run_calls = []
        self.request_stop_calls = 0
        self.agent_running_values = []
        self.refresh_calls = 0

    async def run(self, on_submit, on_exit=None, on_interrupt=None):
        self.run_calls.append(
            {
                "on_submit": on_submit,
                "on_exit": on_exit,
                "on_interrupt": on_interrupt,
            }
        )
        self.is_running = True
        if self.run_error:
            raise self.run_error
        for submitted in self.submissions:
            await on_submit(submitted)
        self.is_running = False
        if on_exit:
            result = on_exit()
            if hasattr(result, "__await__"):
                await result

    def append_output(self, content, end="\n"):
        self.append_calls.append((content, end))
        plain_text = (
            sanitize_terminal_text(content)
            if isinstance(content, str)
            else capture_renderable(content)
        )
        self.transcript.append_assistant(
            content if not isinstance(content, str) else plain_text,
            plain_text=plain_text,
            end=end,
        )

    def append_tool(self, content):
        self.tool_calls.append(content)
        plain_text = (
            sanitize_terminal_text(content)
            if isinstance(content, str)
            else capture_renderable(content)
        )
        self.transcript.append_tool(content, plain_text=plain_text)

    def append_system(self, text):
        self.system_calls.append(text)
        self.transcript.append_system(sanitize_terminal_text(text))

    def append_error(self, text):
        self.error_calls.append(text)
        self.transcript.append_error(sanitize_terminal_text(text))

    async def ask(self, prompt, timeout):
        self.ask_calls.append((prompt, timeout))
        if self.ask_error:
            raise self.ask_error
        return self.ask_response

    def request_stop(self):
        self.request_stop_calls += 1
        self.is_running = False

    def set_agent_running(self, running):
        self.agent_running_values.append(running)

    def refresh(self):
        self.refresh_calls += 1


class SpyLayoutController:
    instances = []

    def __init__(self, console, status_pane, status_config):
        self.console = console
        self.status_pane = status_pane
        self.status_config = status_config
        self.append_calls = []
        self.start_calls = 0
        self.render_once_calls = 0
        self.refresh_calls = 0
        self.stop_calls = 0
        self.toggle_calls = 0
        self.is_live = False
        SpyLayoutController.instances.append(self)

    def start(self):
        self.start_calls += 1
        self.is_live = True

    def append_output(self, text, end="\n"):
        self.append_calls.append((text, end))

    def render_once(self):
        self.render_once_calls += 1

    def refresh(self):
        self.refresh_calls += 1

    def stop(self):
        self.stop_calls += 1
        self.is_live = False

    def toggle_inspector(self):
        self.toggle_calls += 1
        return self.toggle_calls % 2 == 1


class FakeLive:
    def __init__(self, renderable, *, console, refresh_per_second=10, transient=False):
        self.renderable = renderable
        self.console = console
        self.refresh_per_second = refresh_per_second
        self.transient = transient
        self.started = False
        self.stop_calls = 0
        self.updates = []

    def start(self):
        self.started = True

    def stop(self):
        self.stop_calls += 1
        self.started = False

    def update(self, renderable):
        self.updates.append(renderable)


class StartFailingLive(FakeLive):
    def start(self):
        raise RuntimeError("live start failed")


@pytest.fixture
def layout_spy(monkeypatch):
    SpyLayoutController.instances = []
    monkeypatch.setattr(
        repl_module,
        "AgentLayoutController",
        SpyLayoutController,
        raising=False,
    )
    return SpyLayoutController


def test_constructing_with_status_pane_creates_layout_controller(layout_spy):
    pane = FakeStatusPane()
    model = RuntimeStatusModel()

    repl = REPLEngine(status_pane=pane, status_model=model)

    assert repl._status_pane is pane
    assert repl._status_model is model
    assert isinstance(repl._layout_controller, SpyLayoutController)
    assert repl._layout_controller.status_pane is pane


def test_status_bar_alias_still_creates_layout_controller(layout_spy):
    pane = FakeStatusPane()

    repl = REPLEngine(status_bar=pane)

    assert repl._status_pane is pane
    assert isinstance(repl._layout_controller, SpyLayoutController)
    assert repl._status_bar is pane


def test_output_to_console_appends_to_layout_controller(layout_spy):
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=RuntimeStatusModel())
    console = FakeConsole()
    repl._console = console

    repl._output_to_console("streamed", end="")

    assert repl._layout_controller.append_calls == [("streamed", "")]
    assert repl._layout_controller.render_once_calls == 1
    assert console.calls == []


def test_real_layout_controller_renders_output_without_live(monkeypatch):
    console = Console(record=True, width=140, height=40)
    monkeypatch.setattr(REPLEngine, "_create_console", lambda _self: console)
    model = RuntimeStatusModel()
    config = StatusPaneConfig(width=34, collapse_below_columns=80)
    pane = AgentInspectorPane(config, status_model=model)
    repl = REPLEngine(status_pane=pane, status_model=model, config=config)

    repl._output_to_console("visible through fixed pane")

    text = console.export_text(styles=False)
    assert repl._layout_controller.is_live is False
    assert "Output" in text
    assert "visible through fixed pane" in text


def test_legacy_status_bar_alias_renders_output_without_live(monkeypatch):
    console = Console(record=True, width=140, height=40)
    monkeypatch.setattr(REPLEngine, "_create_console", lambda _self: console)
    model = RuntimeStatusModel()
    config = StatusPaneConfig(width=34, collapse_below_columns=80)
    pane = AgentInspectorPane(config, status_model=model)
    repl = REPLEngine(status_bar=pane, config=config)

    repl._output_to_console("visible through legacy alias")

    assert "visible through legacy alias" in console.export_text(styles=False)


def test_disabled_status_pane_still_renders_output_without_status(monkeypatch):
    console = Console(record=True, width=140, height=40)
    monkeypatch.setattr(REPLEngine, "_create_console", lambda _self: console)
    model = RuntimeStatusModel()
    config = StatusPaneConfig(enabled=False)
    pane = AgentInspectorPane(config, status_model=model)
    repl = REPLEngine(status_pane=pane, status_model=model, config=config)

    repl._output_to_console("visible with disabled pane")

    text = console.export_text(styles=False)
    assert "visible with disabled pane" in text
    assert "Agent Inspector" not in text


@pytest.mark.asyncio
async def test_shutdown_stops_layout_controller(layout_spy):
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=RuntimeStatusModel())
    repl._console = FakeConsole()

    await repl._shutdown()

    assert repl._layout_controller.stop_calls == 1


class FakeStreamingEngine:
    def __init__(self, events):
        self.events = events
        self.interrupt_event = SimpleNamespace(clear=lambda: None)

    async def run(self, text, session, active_skill=None):
        for event in self.events:
            yield event


@pytest.mark.asyncio
async def test_process_input_uses_live_layout_for_streaming_chunks(layout_spy):
    engine = FakeStreamingEngine(
        [
            TextChunk("hel"),
            TextChunk("lo"),
            Done(),
        ]
    )
    repl = REPLEngine(
        engine=engine,
        status_pane=FakeStatusPane(),
        status_model=RuntimeStatusModel(),
    )
    repl._current_session = SimpleNamespace(id="session-1")

    await repl.process_input("hello")

    controller = repl._layout_controller
    assert controller.start_calls == 1
    assert controller.stop_calls == 1
    assert controller.append_calls == [("hel", ""), ("lo", ""), ("", "\n"), ("", "\n")]
    assert controller.render_once_calls == 0


@pytest.mark.asyncio
async def test_process_input_preserves_renderer_panel_output(monkeypatch):
    console = Console(record=True, width=140, height=40)
    monkeypatch.setattr(REPLEngine, "_create_console", lambda _self: console)
    monkeypatch.setattr(layout_module, "Live", FakeLive)
    engine = FakeStreamingEngine(
        [
            ToolCallStart(name="read", call_id="call-1"),
            Done(),
        ]
    )
    model = RuntimeStatusModel()
    config = StatusPaneConfig(width=34, collapse_below_columns=80)
    pane = AgentInspectorPane(config, status_model=model)
    repl = REPLEngine(
        engine=engine,
        renderer=Renderer(),
        status_pane=pane,
        status_model=model,
        config=config,
    )
    repl._current_session = SimpleNamespace(id="session-1")

    await repl.process_input("use a tool")
    repl._layout_controller.render_once()

    text = console.export_text(styles=False)
    assert "Tool: read" in text
    assert "<rich.panel.Panel object" not in text


@pytest.mark.asyncio
async def test_process_input_continues_when_layout_live_start_fails(
    monkeypatch,
    caplog,
):
    console = Console(record=True, width=140, height=40)
    monkeypatch.setattr(REPLEngine, "_create_console", lambda _self: console)
    monkeypatch.setattr(layout_module, "Live", StartFailingLive)
    engine = FakeStreamingEngine(
        [
            TextChunk("still runs"),
            Done(),
        ]
    )
    model = RuntimeStatusModel()
    config = StatusPaneConfig(width=34, collapse_below_columns=80)
    pane = AgentInspectorPane(config, status_model=model)
    repl = REPLEngine(
        engine=engine,
        status_pane=pane,
        status_model=model,
        config=config,
    )
    repl._current_session = SimpleNamespace(id="session-1")

    with caplog.at_level(logging.ERROR, logger="myagent.cli.layout"):
        await repl.process_input("hello")

    text = console.export_text(styles=False)
    assert repl._layout_controller.is_live is False
    assert repl._layout_controller._live_failed is True
    assert "still runs" in text
    assert any(
        record.name == "myagent.cli.layout"
        and getattr(record, "category", None) == "error"
        and getattr(record, "component", None) == "agent"
        and getattr(record, "context", None) == "cli_layout_start"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_process_input_merges_engine_status_updates(layout_spy):
    model = RuntimeStatusModel()
    engine = FakeStreamingEngine(
        [
            StatusUpdate(
                scope="context",
                data={"context_usage": 0.42, "context_window": 200_000},
            ),
            StatusUpdate(
                scope="tokens",
                data={
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "turn_total": 150,
                    "session_total": 150,
                },
            ),
            StatusUpdate(
                scope="tokens",
                data={
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "turn_total": 200,
                    "session_total": 350,
                },
            ),
            Done(),
        ]
    )
    repl = REPLEngine(
        engine=engine,
        status_pane=FakeStatusPane(),
        status_model=model,
    )
    repl._current_session = SimpleNamespace(id="session-1")

    await repl.process_input("hello")

    snapshot = model.snapshot()
    assert snapshot.tokens.context_usage == 0.42
    assert snapshot.tokens.context_window == 200_000
    assert snapshot.tokens.prompt_tokens == 120
    assert snapshot.tokens.completion_tokens == 80
    assert snapshot.tokens.turn_total == 200
    assert snapshot.tokens.session_total == 350


def test_status_update_merges_context_goal_health_and_tokens(layout_spy):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)

    repl._update_status_from_event(
        StatusUpdate(
            scope="context",
            data={"context_usage": 0.25, "context_window": 100_000},
        )
    )
    repl._update_status_from_event(
        StatusUpdate(
            scope="goal",
            data={
                "name": "ship task",
                "active": True,
                "achieved": False,
                "waiting_for_user": False,
                "state": "open",
            },
        )
    )
    repl._update_status_from_event(
        StatusUpdate(
            scope="health",
            data={"last_error": "stream boom", "retry_info": "retry 1/3"},
        )
    )
    repl._update_status_from_event(
        StatusUpdate(
            scope="tokens",
            data={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "turn_total": 15,
                "session_total": 15,
            },
        )
    )

    snapshot = model.snapshot()
    assert snapshot.tokens.context_usage == 0.25
    assert snapshot.tokens.context_window == 100_000
    assert snapshot.tokens.prompt_tokens == 10
    assert snapshot.tokens.completion_tokens == 5
    assert snapshot.tokens.turn_total == 15
    assert snapshot.tokens.session_total == 15
    assert snapshot.goal.name == "ship task"
    assert snapshot.goal.active is True
    assert snapshot.goal.achieved is False
    assert snapshot.goal.waiting_for_user is False
    assert snapshot.health.last_error == "stream boom"
    assert snapshot.health.retry_info == "retry 1/3"


def test_sync_status_from_session_updates_session_id_and_goal(layout_spy):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)
    session = SimpleNamespace(
        id="2026-07-04-real",
        goal="ship inspector pane",
        goal_achieved=None,
    )

    repl._sync_status_from_session(session)

    snapshot = model.snapshot()
    assert snapshot.session.session_id == "2026-07-04-real"
    assert snapshot.goal.name == "ship inspector pane"
    assert snapshot.goal.active is True
    assert snapshot.goal.achieved is False
    assert snapshot.goal.waiting_for_user is False


@pytest.mark.asyncio
async def test_run_start_new_syncs_real_session_id(
    layout_spy,
    monkeypatch,
    tmp_path,
):
    class FakePromptSession:
        def __init__(self, *args, **kwargs):
            pass

        async def prompt_async(self, *args, **kwargs):
            raise EOFError

    monkeypatch.setattr("prompt_toolkit.PromptSession", FakePromptSession)
    monkeypatch.setattr(
        repl_module.Path,
        "home",
        classmethod(lambda cls: tmp_path),
    )
    session = SimpleNamespace(
        id="2026-07-04-started",
        project_name="Project",
        project_hash="hash123",
        goal=None,
    )
    session_mgr = FakeSessionManager(session)
    model = RuntimeStatusModel()
    project_dir = tmp_path / "Project"
    repl = REPLEngine(
        session_mgr=session_mgr,
        status_pane=FakeStatusPane(),
        status_model=model,
        project_dir=project_dir,
    )

    await repl.run()

    assert session_mgr.started_project_dir == project_dir
    assert session_mgr.ended_session is session
    assert model.snapshot().session.session_id == "2026-07-04-started"


def test_done_event_updates_runtime_status_tokens(layout_spy):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)

    repl._update_status_from_event(
        Done(usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
    )

    snapshot = model.snapshot()
    assert snapshot.tokens.prompt_tokens == 100
    assert snapshot.tokens.completion_tokens == 50
    assert snapshot.tokens.turn_total == 150
    assert snapshot.tokens.session_total == 150


def test_done_event_accumulates_session_token_total(layout_spy):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)

    repl._update_status_from_event(
        Done(usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
    )
    repl._update_status_from_event(
        Done(usage=Usage(prompt_tokens=120, completion_tokens=80, total_tokens=200))
    )

    snapshot = model.snapshot()
    assert snapshot.tokens.prompt_tokens == 120
    assert snapshot.tokens.completion_tokens == 80
    assert snapshot.tokens.turn_total == 200
    assert snapshot.tokens.session_total == 350


def test_tool_call_start_marks_current_tool_running(layout_spy):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)

    repl._update_status_from_event(ToolCallStart(name="read", call_id="call-1"))

    tool = model.snapshot().tools[0]
    assert tool.name == "read"
    assert tool.status == "running"


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_summary"),
    [
        (ToolResult(output="read ok"), "completed", "read ok"),
        (ToolResult(error="permission denied"), "failed", "permission denied"),
    ],
)
def test_tool_call_end_marks_current_tool_completed_or_failed(
    layout_spy,
    result,
    expected_status,
    expected_summary,
):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)
    repl._update_status_from_event(ToolCallStart(name="read", call_id="call-1"))

    repl._update_status_from_event(ToolCallEnd(call_id="call-1", result=result))

    tool = model.snapshot().tools[0]
    assert tool.name == "read"
    assert tool.status == expected_status
    assert tool.last_result_summary == expected_summary


def test_ask_user_question_marks_goal_waiting_for_user(layout_spy):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)

    repl._update_status_from_event(AskUserQuestion(question="Continue?"))

    snapshot = model.snapshot()
    assert snapshot.goal.active is True
    assert snapshot.goal.waiting_for_user is True


def test_error_and_interrupted_update_health(layout_spy):
    model = RuntimeStatusModel()
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=model)

    repl._update_status_from_event(Error(message="boom"))
    assert model.snapshot().health.last_error == "boom"

    repl._update_status_from_event(Interrupted())
    assert model.snapshot().health.last_error == "Interrupted"


def test_toggle_inspector_delegates_to_layout_controller_and_refreshes(layout_spy):
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=RuntimeStatusModel())

    repl._toggle_inspector()

    assert repl._layout_controller.toggle_calls == 1
    assert repl._layout_controller.refresh_calls == 1


def test_f2_binding_toggles_inspector_without_touching_buffer(monkeypatch, layout_spy):
    class FakeKeyBindings:
        last_instance = None

        def __init__(self):
            self.bindings = {}
            FakeKeyBindings.last_instance = self

        def add(self, *keys):
            def decorator(func):
                self.bindings[keys] = func
                return func

            return decorator

    monkeypatch.setattr(repl_module, "KeyBindings", FakeKeyBindings, raising=False)
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=RuntimeStatusModel())
    kb = repl._build_key_bindings()
    buffer = SimpleNamespace(text="draft", reset=lambda: (_ for _ in ()).throw(AssertionError))
    event = SimpleNamespace(app=SimpleNamespace(current_buffer=buffer), current_buffer=buffer)

    kb.bindings[("f2",)](event)

    assert repl._layout_controller.toggle_calls == 1
    assert repl._layout_controller.refresh_calls == 1
    assert buffer.text == "draft"


def test_inspector_toggle_key_does_not_capture_tab_completion(layout_spy):
    tab_bindings = KeyBindings()

    @tab_bindings.add("tab")
    def _(event):
        pass

    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=RuntimeStatusModel())
    repl_bindings = repl._build_key_bindings()

    assert tab_bindings.bindings[0].keys == (Keys.ControlI,)
    assert (Keys.F2,) in [binding.keys for binding in repl_bindings.bindings]
    assert (Keys.ControlI,) not in [binding.keys for binding in repl_bindings.bindings]


def make_chat_config(*, enabled=True):
    return SimpleNamespace(
        ui=SimpleNamespace(
            chat_window=ChatWindowConfig(enabled=enabled),
            status_pane=StatusPaneConfig(width=34, collapse_below_columns=80),
            syntax_highlight=True,
        )
    )


def make_real_chat_controller(transcript=None):
    config = make_chat_config(enabled=True)
    controller = ChatWindowController(
        config,
        transcript or TranscriptBuffer(),
        status_pane=FakeStatusPane(),
        status_model=RuntimeStatusModel(),
    )
    controller.refresh = lambda: None
    return controller


def active_chat_repl(chat_controller, **kwargs):
    repl = REPLEngine(chat_window_controller=chat_controller, **kwargs)
    repl._chat_window_loop_active = True
    return repl


def transcript_entries(transcript, height=100):
    return transcript.visible_entries(height)


def test_output_to_console_routes_to_active_chat_window_before_layout_or_console(
    layout_spy,
):
    chat = FakeChatWindowController()
    repl = active_chat_repl(
        chat,
        status_pane=FakeStatusPane(),
        status_model=RuntimeStatusModel(),
    )
    console = FakeConsole()
    repl._console = console

    repl._output_to_console("streamed", end="")

    assert chat.append_calls == [("streamed", "")]
    assert repl._layout_controller.append_calls == []
    assert repl._layout_controller.render_once_calls == 0
    assert console.calls == []


def test_output_to_console_captures_rich_panel_as_chat_transcript_text():
    transcript = TranscriptBuffer()
    chat = make_real_chat_controller(transcript=transcript)
    repl = active_chat_repl(chat)

    repl._output_to_console(Panel("panel body", title="Panel Title"))

    entry = transcript_entries(transcript)[-1]
    assert entry.role == "assistant"
    assert "Panel Title" in entry.plain_text
    assert "panel body" in entry.plain_text
    assert "<rich.panel.Panel object" not in entry.plain_text


@pytest.mark.asyncio
async def test_process_input_routes_chat_stream_tool_and_error_entries():
    transcript = TranscriptBuffer()
    chat = make_real_chat_controller(transcript=transcript)
    engine = FakeStreamingEngine(
        [
            TextChunk("hel"),
            TextChunk("lo"),
            ToolCallStart(name="read", call_id="call-1"),
            ToolCallEnd(call_id="call-1", result=ToolResult(output="read ok")),
            Error(message="boom"),
            Done(),
        ]
    )
    repl = active_chat_repl(
        chat,
        engine=engine,
        renderer=Renderer(),
    )
    repl._current_session = SimpleNamespace(id="session-1")

    await repl.process_input("hello")

    entries = transcript_entries(transcript)
    assistant_entries = [entry for entry in entries if entry.role == "assistant"]
    tool_entries = [entry for entry in entries if entry.role == "tool"]
    error_entries = [entry for entry in entries if entry.role == "error"]
    assert len(assistant_entries) == 1
    assert assistant_entries[0].plain_text == "hello"
    assert assistant_entries[0].is_streaming is False
    assert any("Tool: read" in entry.plain_text for entry in tool_entries)
    assert any("read ok" in entry.plain_text for entry in tool_entries)
    assert [entry.plain_text for entry in error_entries] == ["boom"]


@pytest.mark.asyncio
async def test_process_input_routes_slash_command_result_to_chat_system_entry():
    class FakeCommands:
        async def dispatch(self, line, ctx):
            return CommandResult(output="Thinking mode: think-high")

    transcript = TranscriptBuffer()
    chat = make_real_chat_controller(transcript=transcript)
    repl = active_chat_repl(chat, commands=FakeCommands())
    repl._current_session = SimpleNamespace(id="session-1")

    await repl.process_input("/mode think-high")

    entry = transcript_entries(transcript)[-1]
    assert entry.role == "system"
    assert entry.plain_text == "Thinking mode: think-high"


@pytest.mark.asyncio
async def test_process_input_status_update_updates_model_without_chat_transcript_output():
    model = RuntimeStatusModel()
    transcript = TranscriptBuffer()
    chat = make_real_chat_controller(transcript=transcript)
    engine = FakeStreamingEngine(
        [
            StatusUpdate(
                scope="tokens",
                data={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "turn_total": 15,
                    "session_total": 15,
                },
            ),
            Done(),
        ]
    )
    repl = active_chat_repl(
        chat,
        engine=engine,
        status_model=model,
    )
    repl._current_session = SimpleNamespace(id="session-1")

    await repl.process_input("status only")

    snapshot = model.snapshot()
    assert snapshot.tokens.prompt_tokens == 10
    assert snapshot.tokens.completion_tokens == 5
    assert snapshot.tokens.turn_total == 15
    assert snapshot.tokens.session_total == 15
    assert transcript_entries(transcript) == []


def test_start_layout_for_engine_stream_returns_false_while_chat_mode_is_active(
    layout_spy,
):
    chat = FakeChatWindowController()
    repl = active_chat_repl(
        chat,
        status_pane=FakeStatusPane(),
        status_model=RuntimeStatusModel(),
    )

    assert repl._start_layout_for_engine_stream() is False
    assert repl._layout_controller.start_calls == 0


@pytest.mark.asyncio
async def test_chat_submissions_are_serialized_and_preserve_active_engine_task():
    class ControlledEngine:
        def __init__(self):
            self.interrupt_event = SimpleNamespace(clear=lambda: None)
            self.started = []
            self.first_started = asyncio.Event()
            self.second_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def run(self, text, session, active_skill=None):
            self.started.append(text)
            if text == "first":
                self.first_started.set()
                yield TextChunk("first")
                await self.release_first.wait()
                yield Done()
                return

            self.second_started.set()
            yield TextChunk("second")
            yield Done()

    class ConcurrentSubmitChat(FakeChatWindowController):
        def __init__(self):
            super().__init__()
            self.second_submitted = asyncio.Event()
            self.first_active_engine_task = None

        async def run(self, on_submit, on_exit=None, on_interrupt=None):
            self.is_running = True
            first_task = asyncio.create_task(on_submit("first"))
            await asyncio.wait_for(engine.first_started.wait(), timeout=1.0)
            self.first_active_engine_task = repl._active_engine_task

            second_task = asyncio.create_task(on_submit("second"))
            self.second_submitted.set()
            await first_task
            await second_task
            self.is_running = False

    engine = ControlledEngine()
    chat = ConcurrentSubmitChat()
    repl = REPLEngine(
        chat_window_controller=chat,
        engine=engine,
        renderer=Renderer(),
    )
    repl._current_session = SimpleNamespace(id="session-1")

    run_task = asyncio.create_task(repl._run_chat_window_loop())
    await asyncio.wait_for(chat.second_submitted.wait(), timeout=1.0)
    await asyncio.sleep(0)

    try:
        assert engine.started == ["first"]
        assert repl._active_engine_task is chat.first_active_engine_task
    finally:
        engine.release_first.set()
        await asyncio.wait_for(run_task, timeout=1.0)

    assert engine.started == ["first", "second"]


@pytest.mark.asyncio
async def test_chat_loop_waits_for_background_submission_before_normal_exit():
    class BlockingEngine:
        def __init__(self):
            self.interrupt_event = SimpleNamespace(clear=lambda: None)
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run(self, text, session, active_skill=None):
            self.started.set()
            yield TextChunk("blocked")
            await self.release.wait()
            yield Done()

    class BackgroundSubmitChat(FakeChatWindowController):
        def __init__(self):
            super().__init__()
            self.submission_task = None

        async def run(self, on_submit, on_exit=None, on_interrupt=None):
            self.is_running = True
            self.submission_task = asyncio.create_task(on_submit("blocked"))
            await asyncio.wait_for(engine.started.wait(), timeout=1.0)
            self.is_running = False
            if on_exit:
                result = on_exit()
                if hasattr(result, "__await__"):
                    await result

    engine = BlockingEngine()
    chat = BackgroundSubmitChat()
    repl = REPLEngine(
        chat_window_controller=chat,
        engine=engine,
        renderer=Renderer(),
    )
    repl._current_session = SimpleNamespace(id="session-1")

    run_task = asyncio.create_task(repl._run_chat_window_loop())
    await asyncio.wait_for(engine.started.wait(), timeout=1.0)
    await asyncio.sleep(0)

    try:
        assert run_task.done() is False
        assert repl._active_engine_task is not None
    finally:
        engine.release.set()
        await asyncio.wait_for(run_task, timeout=1.0)

    assert chat.submission_task.done()
    assert repl._active_engine_task is None


@pytest.mark.asyncio
async def test_chat_crash_cancels_background_submission_before_prompt_fallback(
    monkeypatch,
    caplog,
):
    prompt_active_engine_tasks = []
    prompt_pending_submission_states = []

    class BlockingEngine:
        def __init__(self):
            self.interrupt_event = SimpleNamespace(clear=lambda: None)
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def run(self, text, session, active_skill=None):
            self.started.set()
            try:
                yield TextChunk("blocked")
                await self.release.wait()
                yield Done()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    class CrashingBackgroundSubmitChat(FakeChatWindowController):
        def __init__(self):
            super().__init__()
            self.submission_task = None

        async def run(self, on_submit, on_exit=None, on_interrupt=None):
            self.is_running = True
            self.submission_task = asyncio.create_task(on_submit("blocked"))
            await asyncio.wait_for(engine.started.wait(), timeout=1.0)
            if on_exit:
                result = on_exit()
                if hasattr(result, "__await__"):
                    await result
            raise RuntimeError("chat crashed after submit")

    async def fake_prompt_loop(self):
        prompt_active_engine_tasks.append(self._active_engine_task)
        prompt_pending_submission_states.append(
            any(
                not task.done()
                for task in getattr(self, "_chat_submission_tasks", ())
            )
        )
        self._running = False

    engine = BlockingEngine()
    chat = CrashingBackgroundSubmitChat()
    monkeypatch.setattr(REPLEngine, "_run_prompt_session_loop", fake_prompt_loop)
    repl = REPLEngine(
        config=make_chat_config(enabled=True),
        chat_window_controller=chat,
        engine=engine,
        renderer=Renderer(),
    )
    repl._current_session = SimpleNamespace(id="session-1")
    repl._console = FakeConsole()

    try:
        with caplog.at_level(logging.ERROR, logger="myagent.cli.repl"):
            await repl.run()
    finally:
        engine.release.set()
        if chat.submission_task is not None:
            await asyncio.wait_for(chat.submission_task, timeout=1.0)

    assert prompt_active_engine_tasks == [None]
    assert prompt_pending_submission_states == [False]
    assert engine.cancelled.is_set()
    assert repl._active_engine_task is None


@pytest.mark.asyncio
async def test_chat_crash_after_ask_question_skips_chat_ask_before_prompt_fallback(
    monkeypatch,
    caplog,
):
    prompt_started = asyncio.Event()
    prompt_active_engine_tasks = []

    class AskThenBlockEngine:
        def __init__(self):
            self.interrupt_event = SimpleNamespace(clear=lambda: None)
            self.question_yielded = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def run(self, text, session, active_skill=None):
            if text != "ask":
                yield Done()
                return

            yield AskUserQuestion(question="Need input?")
            self.question_yielded.set()
            try:
                await self.release.wait()
                yield Done()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    class CrashingAskChat(FakeChatWindowController):
        def __init__(self):
            super().__init__()
            self.submission_task = None
            self.ask_release = asyncio.Event()

        async def run(self, on_submit, on_exit=None, on_interrupt=None):
            self.is_running = True
            self.submission_task = asyncio.create_task(on_submit("ask"))
            await asyncio.wait_for(engine.question_yielded.wait(), timeout=1.0)
            if on_exit:
                result = on_exit()
                if hasattr(result, "__await__"):
                    await result
            raise RuntimeError("chat crashed after ask")

        async def ask(self, prompt, timeout):
            self.ask_calls.append((prompt, timeout))
            await self.ask_release.wait()
            return None

    async def fake_prompt_loop(self):
        prompt_active_engine_tasks.append(self._active_engine_task)
        prompt_started.set()
        self._running = False

    engine = AskThenBlockEngine()
    chat = CrashingAskChat()
    monkeypatch.setattr(REPLEngine, "_run_prompt_session_loop", fake_prompt_loop)
    repl = REPLEngine(
        config=make_chat_config(enabled=True),
        chat_window_controller=chat,
        engine=engine,
        renderer=Renderer(),
    )
    repl._current_session = SimpleNamespace(id="session-1")
    repl._console = FakeConsole()

    run_task = asyncio.create_task(repl.run())
    try:
        with caplog.at_level(logging.ERROR, logger="myagent.cli.repl"):
            await asyncio.wait_for(prompt_started.wait(), timeout=1.0)
        await asyncio.wait_for(run_task, timeout=1.0)
    finally:
        engine.release.set()
        chat.ask_release.set()
        if not run_task.done():
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task

    assert chat.ask_calls == []
    assert prompt_active_engine_tasks == [None]
    assert engine.cancelled.is_set()


@pytest.mark.asyncio
async def test_ask_timeout_routes_system_message_to_chat_without_console():
    class AskTimeoutEngine:
        def __init__(self):
            self.interrupt_event = SimpleNamespace(clear=lambda: None)
            self.inputs = []

        async def run(self, text, session, active_skill=None):
            self.inputs.append(text)
            if text == "ask":
                yield AskUserQuestion(question="Need input?")
            yield Done()

    chat = FakeChatWindowController(ask_response=None)
    repl = active_chat_repl(
        chat,
        engine=AskTimeoutEngine(),
        renderer=Renderer(),
    )
    repl._current_session = SimpleNamespace(id="session-1")
    repl._console = FakeConsole()

    await repl.process_input("ask")

    assert repl._console.calls == []
    assert any(
        "No response within 120s; agent will auto-decide." in message
        for message in chat.system_calls
    )


@pytest.mark.asyncio
async def test_ask_failure_routes_system_message_to_chat_without_console():
    class AskFailureEngine:
        def __init__(self):
            self.interrupt_event = SimpleNamespace(clear=lambda: None)

        async def run(self, text, session, active_skill=None):
            yield AskUserQuestion(question="Need input?")
            yield Done()

    chat = FakeChatWindowController(ask_error=RuntimeError("ask failed"))
    repl = active_chat_repl(
        chat,
        engine=AskFailureEngine(),
        renderer=Renderer(),
    )
    repl._current_session = SimpleNamespace(id="session-1")
    repl._console = FakeConsole()

    await repl.process_input("ask")

    assert repl._console.calls == []
    assert any(
        "Timeout; agent will auto-decide." in message
        for message in chat.system_calls
    )


@pytest.mark.asyncio
async def test_echo_fallback_routes_to_chat_without_console():
    chat = FakeChatWindowController()
    repl = active_chat_repl(chat)
    repl._console = FakeConsole()

    await repl.process_input("hello without engine")

    assert repl._console.calls == []
    assert chat.system_calls == ["Echo: hello without engine"]


@pytest.mark.asyncio
async def test_status_update_refreshes_chat_without_transcript_entry():
    model = RuntimeStatusModel()
    transcript = TranscriptBuffer()
    chat = FakeChatWindowController(transcript=transcript)
    engine = FakeStreamingEngine(
        [
            StatusUpdate(
                scope="tokens",
                data={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "turn_total": 15,
                    "session_total": 15,
                },
            ),
            Done(),
        ]
    )
    repl = active_chat_repl(
        chat,
        engine=engine,
        status_model=model,
    )
    repl._current_session = SimpleNamespace(id="session-1")

    await repl.process_input("status only")

    assert chat.refresh_calls >= 1
    assert transcript_entries(transcript) == []


@pytest.mark.asyncio
async def test_exit_command_stops_persistent_chat_window():
    class ExitCommands:
        async def dispatch(self, line, ctx):
            return CommandResult(
                output="Goodbye!",
                success=True,
                exit_requested=True,
            )

    class PersistentExitChat(FakeChatWindowController):
        def __init__(self):
            super().__init__()
            self.stop_event = asyncio.Event()

        async def run(self, on_submit, on_exit=None, on_interrupt=None):
            self.is_running = True
            await on_submit("/exit")
            await self.stop_event.wait()
            self.is_running = False
            if on_exit:
                result = on_exit()
                if hasattr(result, "__await__"):
                    await result

        def request_stop(self):
            super().request_stop()
            self.stop_event.set()

    chat = PersistentExitChat()
    repl = REPLEngine(
        chat_window_controller=chat,
        commands=ExitCommands(),
    )
    repl._current_session = SimpleNamespace(id="session-1")

    run_task = asyncio.create_task(repl._run_chat_window_loop())
    completed = False
    try:
        await asyncio.wait_for(asyncio.shield(run_task), timeout=0.2)
        completed = True
    except TimeoutError:
        completed = False
    finally:
        if not completed:
            chat.request_stop()
            await asyncio.wait_for(run_task, timeout=1.0)

    assert completed is True
    assert chat.request_stop_calls == 1


@pytest.mark.asyncio
async def test_run_uses_chat_window_loop_when_config_enabled(monkeypatch):
    calls = []

    async def fake_chat_loop(self):
        calls.append("chat")
        self._running = False

    async def fake_prompt_loop(self):
        calls.append("prompt")
        self._running = False

    monkeypatch.setattr(REPLEngine, "_run_chat_window_loop", fake_chat_loop)
    monkeypatch.setattr(REPLEngine, "_run_prompt_session_loop", fake_prompt_loop)
    repl = REPLEngine(config=make_chat_config(enabled=True))
    repl._console = FakeConsole()

    await repl.run()

    assert calls == ["chat"]


@pytest.mark.asyncio
async def test_run_uses_prompt_session_loop_when_chat_window_disabled(monkeypatch):
    calls = []

    async def fake_chat_loop(self):
        calls.append("chat")
        self._running = False

    async def fake_prompt_loop(self):
        calls.append("prompt")
        self._running = False

    monkeypatch.setattr(REPLEngine, "_run_chat_window_loop", fake_chat_loop)
    monkeypatch.setattr(REPLEngine, "_run_prompt_session_loop", fake_prompt_loop)
    repl = REPLEngine(config=make_chat_config(enabled=False))
    repl._console = FakeConsole()

    await repl.run()

    assert calls == ["prompt"]


@pytest.mark.asyncio
async def test_run_falls_back_to_prompt_loop_after_chat_startup_exception(
    monkeypatch,
    caplog,
):
    prompt_calls = 0

    async def fake_prompt_loop(self):
        nonlocal prompt_calls
        prompt_calls += 1
        self._running = False

    chat = FakeChatWindowController(run_error=RuntimeError("chat failed"))
    monkeypatch.setattr(REPLEngine, "_run_prompt_session_loop", fake_prompt_loop)
    repl = REPLEngine(
        config=make_chat_config(enabled=True),
        chat_window_controller=chat,
    )
    repl._console = FakeConsole()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.repl"):
        await repl.run()

    assert prompt_calls == 1
    record = next(
        record
        for record in caplog.records
        if getattr(record, "context", "") == "cli_chat_window_start"
    )
    assert record.category == "error"
    assert record.component == "agent"
    assert record.exception_type == "RuntimeError"
    assert "chat failed" in record.traceback


@pytest.mark.asyncio
async def test_run_fallback_restores_running_after_chat_on_exit_then_error(
    monkeypatch,
    caplog,
):
    prompt_running_states = []

    class ExitThenFailChat(FakeChatWindowController):
        async def run(self, on_submit, on_exit=None, on_interrupt=None):
            self.is_running = True
            if on_exit:
                result = on_exit()
                if hasattr(result, "__await__"):
                    await result
            raise RuntimeError("chat failed after exit")

    async def fake_prompt_loop(self):
        prompt_running_states.append(self._running)
        self._running = False

    chat = ExitThenFailChat()
    monkeypatch.setattr(REPLEngine, "_run_prompt_session_loop", fake_prompt_loop)
    repl = REPLEngine(
        config=make_chat_config(enabled=True),
        chat_window_controller=chat,
    )
    repl._console = FakeConsole()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.repl"):
        await repl.run()

    assert prompt_running_states == [True]
    record = next(
        record
        for record in caplog.records
        if getattr(record, "context", "") == "cli_chat_window_start"
    )
    assert record.category == "error"
    assert record.component == "agent"
    assert record.exception_type == "RuntimeError"
    assert "chat failed after exit" in record.traceback


@pytest.mark.asyncio
async def test_run_falls_back_to_prompt_loop_after_chat_factory_exception(
    monkeypatch,
    caplog,
):
    prompt_calls = 0

    async def fake_prompt_loop(self):
        nonlocal prompt_calls
        prompt_calls += 1
        self._running = False

    def failing_factory(**kwargs):
        raise RuntimeError("factory failed")

    monkeypatch.setattr(REPLEngine, "_run_prompt_session_loop", fake_prompt_loop)
    repl = REPLEngine(
        config=make_chat_config(enabled=True),
        chat_window_factory=failing_factory,
    )
    repl._console = FakeConsole()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.repl"):
        await repl.run()

    assert prompt_calls == 1
    record = next(
        record
        for record in caplog.records
        if getattr(record, "context", "") == "cli_chat_window_start"
    )
    assert record.category == "error"
    assert record.component == "agent"
    assert record.exception_type == "RuntimeError"
    assert "factory failed" in record.traceback


@pytest.mark.asyncio
async def test_prompt_with_timeout_uses_chat_window_ask_when_active():
    chat = FakeChatWindowController(ask_response="from chat")
    repl = active_chat_repl(chat)

    result = await repl._prompt_with_timeout("Need a value? ", timeout=12.0)

    assert result == "from chat"
    assert chat.ask_calls == [("Need a value? ", 12.0)]


@pytest.mark.asyncio
async def test_prompt_with_timeout_uses_prompt_toolkit_when_chat_inactive(monkeypatch):
    chat = FakeChatWindowController(running=False)
    repl = REPLEngine(chat_window_controller=chat)
    prompt_calls = []

    def fake_prompt(prompt_text, multiline=False):
        prompt_calls.append((prompt_text, multiline))
        return "typed"

    monkeypatch.setattr("prompt_toolkit.shortcuts.prompt", fake_prompt)

    result = await repl._prompt_with_timeout("Need a value? ", timeout=1.0)

    assert result == "typed"
    assert prompt_calls == [("Need a value? ", False)]
    assert chat.ask_calls == []


@pytest.mark.asyncio
async def test_prompt_with_timeout_keeps_simple_input_fallback_when_chat_inactive(
    monkeypatch,
):
    real_import = builtins.__import__
    repl = REPLEngine()

    def fake_import(name, *args, **kwargs):
        if name == "prompt_toolkit.shortcuts":
            raise ImportError("prompt toolkit unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr("builtins.input", lambda prompt_text: "fallback typed")

    result = await repl._prompt_with_timeout("Need a value? ", timeout=1.0)

    assert result == "fallback typed"
