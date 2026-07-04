from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console

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
from myagent.cli.renderer import Renderer
from myagent.cli.repl import REPLEngine
from myagent.cli.status import AgentInspectorPane
from myagent.config.schema import StatusPaneConfig
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
