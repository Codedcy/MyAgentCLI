import logging

from rich.console import Console

import myagent.cli.layout as layout_module
from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.layout import AgentLayoutController
from myagent.cli.status import AgentInspectorPane
from myagent.config.schema import StatusPaneConfig


class FakeLive:
    instances = []

    def __init__(self, renderable, *, console, refresh_per_second=10, transient=False):
        self.renderable = renderable
        self.console = console
        self.refresh_per_second = refresh_per_second
        self.transient = transient
        self.started = False
        self.stop_calls = 0
        self.updates = []
        FakeLive.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stop_calls += 1
        self.started = False

    def update(self, renderable):
        self.updates.append(renderable)


class FailingLive(FakeLive):
    def update(self, renderable):
        raise RuntimeError("live update failed")


def build_controller(width=160, status_config=None, status_model=None):
    console = Console(record=True, width=width, height=80)
    config = status_config or StatusPaneConfig(width=34, collapse_below_columns=120)
    pane = AgentInspectorPane(config, status_model=status_model)
    return AgentLayoutController(console, pane, config), console


def test_wide_console_builds_output_with_full_right_inspector():
    model = RuntimeStatusModel()
    model.update_session(project_name="MyAgentCLI", model="deepseek-v4-pro")
    model.update_tokens(session_total=500)
    controller, console = build_controller(width=160, status_model=model)

    controller.set_output_lines(["hello from agent"])
    controller.refresh()
    controller.render_once()

    text = console.export_text(styles=False)
    assert controller.layout["output"].name == "output"
    assert controller.layout["status"].name == "status"
    assert controller.layout["status"].visible is True
    assert controller.layout["status"].size == 34
    assert "Output" in text
    assert "hello from agent" in text
    assert "Agent Inspector" in text
    assert "deepseek-v4-pro" in text


def test_narrow_console_uses_rail_mode():
    model = RuntimeStatusModel()
    model.update_tokens(context_usage=0.42)
    model.upsert_subagent("agent-1", task_name="narrow rail", status="running")
    config = StatusPaneConfig(width=34, collapse_below_columns=120, rail_width=8)
    controller, console = build_controller(
        width=80,
        status_config=config,
        status_model=model,
    )

    controller.refresh()
    controller.render_once()

    text = console.export_text(styles=False)
    assert controller.layout["status"].visible is True
    assert controller.layout["status"].size == 8
    assert "Agent Inspector" not in text
    assert "42%" in text
    assert "SA 1" in text


def test_output_buffer_trims_to_last_300_entries_after_500():
    config = StatusPaneConfig(enabled=False)
    controller, _console = build_controller(width=160, status_config=config)

    for index in range(501):
        controller.append_output(f"line-{index}")

    assert len(controller._output_lines) == 300
    assert controller._output_lines[0] == "line-201"
    assert controller._output_lines[-1] == "line-500"


def test_toggle_inspector_flips_expanded_state():
    model = RuntimeStatusModel()
    model.update_tokens(session_total=100)
    controller, console = build_controller(width=160, status_model=model)

    assert controller.toggle_inspector() is False
    controller.render_once()
    collapsed_text = console.export_text(styles=False)

    assert "Agent Inspector" not in collapsed_text

    assert controller.toggle_inspector() is True
    controller.render_once()
    expanded_text = console.export_text(styles=False)

    assert "Agent Inspector" in expanded_text


def test_start_and_stop_are_idempotent(monkeypatch):
    FakeLive.instances = []
    monkeypatch.setattr(layout_module, "Live", FakeLive)
    controller, _console = build_controller(width=160)

    controller.start()
    controller.start()

    assert controller.is_live is True
    assert len(FakeLive.instances) == 1

    live = FakeLive.instances[0]
    controller.stop()
    controller.stop()

    assert live.stop_calls == 1
    assert controller.is_live is False


def test_refresh_live_failure_logs_and_falls_back_to_console(caplog, monkeypatch):
    FakeLive.instances = []
    monkeypatch.setattr(layout_module, "Live", FailingLive)
    controller, console = build_controller(width=160)
    controller.set_output_lines(["fallback output"])
    controller.start()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.layout"):
        controller.refresh()

    assert controller.is_live is True
    assert "fallback output" in console.export_text(styles=False)
    assert any(
        record.name == "myagent.cli.layout"
        and getattr(record, "category", None) == "error"
        and getattr(record, "context", None) == "cli_layout_refresh"
        for record in caplog.records
    )
