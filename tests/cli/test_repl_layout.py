from __future__ import annotations

from types import SimpleNamespace

import pytest

import myagent.cli.repl as repl_module
from myagent.agent.engine import (
    AskUserQuestion,
    Done,
    Error,
    Interrupted,
    ToolCallEnd,
    ToolCallStart,
)
from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.repl import REPLEngine
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


class SpyLayoutController:
    instances = []

    def __init__(self, console, status_pane, status_config):
        self.console = console
        self.status_pane = status_pane
        self.status_config = status_config
        self.append_calls = []
        self.render_once_calls = 0
        self.refresh_calls = 0
        self.stop_calls = 0
        self.toggle_calls = 0
        SpyLayoutController.instances.append(self)

    def append_output(self, text, end="\n"):
        self.append_calls.append((text, end))

    def render_once(self):
        self.render_once_calls += 1

    def refresh(self):
        self.refresh_calls += 1

    def stop(self):
        self.stop_calls += 1

    def toggle_inspector(self):
        self.toggle_calls += 1
        return self.toggle_calls % 2 == 1


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
    assert console.calls == []


@pytest.mark.asyncio
async def test_shutdown_stops_layout_controller(layout_spy):
    repl = REPLEngine(status_pane=FakeStatusPane(), status_model=RuntimeStatusModel())
    repl._console = FakeConsole()

    await repl._shutdown()

    assert repl._layout_controller.stop_calls == 1


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


def test_ctrl_i_binding_toggles_inspector_without_touching_buffer(monkeypatch, layout_spy):
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

    kb.bindings[("c-i",)](event)

    assert repl._layout_controller.toggle_calls == 1
    assert repl._layout_controller.refresh_calls == 1
    assert buffer.text == "draft"
