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
        self.updates.append(renderable)
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

    assert controller.is_live is False
    assert FakeLive.instances[0].stop_calls == 1
    assert "fallback output" in console.export_text(styles=False)
    assert any(
        record.name == "myagent.cli.layout"
        and getattr(record, "category", None) == "error"
        and getattr(record, "context", None) == "cli_layout_refresh"
        for record in caplog.records
    )


def test_repeated_refresh_after_live_failure_does_not_retry_or_spam(
    caplog,
    monkeypatch,
):
    FakeLive.instances = []
    monkeypatch.setattr(layout_module, "Live", FailingLive)
    controller, console = build_controller(width=160)
    controller.set_output_lines(["one fallback"])
    controller.start()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.layout"):
        controller.refresh()

    first_render = console.export_text(styles=False, clear=False)
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.layout"):
        controller.refresh()
        controller.refresh()

    assert len(FakeLive.instances[0].updates) == 1
    assert caplog.records == []
    assert console.export_text(styles=False, clear=False) == first_render


def test_append_output_after_live_failure_uses_direct_console_fallback(
    caplog,
    monkeypatch,
):
    FakeLive.instances = []
    monkeypatch.setattr(layout_module, "Live", FailingLive)
    controller, console = build_controller(width=160)
    controller.set_output_lines(["one fallback"])
    controller.start()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.layout"):
        controller.refresh()

    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="myagent.cli.layout"):
        controller.append_output("later", end="")

    assert len(FakeLive.instances[0].updates) == 1
    assert caplog.records == []
    assert "later" in console.export_text(styles=False)


def test_rail_layout_uses_status_pane_marker_aware_width():
    config = StatusPaneConfig(width=34, collapse_below_columns=120, rail_width=5)
    controller, _console = build_controller(width=80, status_config=config)
    controller.status_pane.update(subagents_active=123456789)

    controller.refresh()

    assert controller.layout["status"].size == len("SA 123456789") + 2


def test_disabled_status_pane_keeps_output_rendering_available():
    config = StatusPaneConfig(enabled=False)
    controller, console = build_controller(width=160, status_config=config)

    controller.set_output_lines(["output survives disabled inspector"])
    controller.render_once()

    text = console.export_text(styles=False)
    assert controller.layout["output"].visible is True
    assert controller.layout["status"].visible is False
    assert "output survives disabled inspector" in text
    assert "Agent Inspector" not in text


def test_output_rendering_strips_ansi_and_unsafe_controls_but_keeps_layout_text():
    config = StatusPaneConfig(enabled=False)
    controller, _console = build_controller(width=160, status_config=config)

    controller.set_output_lines(["safe\x1b[31mred\x1b[0m\tok", "bad\x07value\x08!"])
    panel = controller.layout["output"].renderable

    plain_text = panel.renderable.plain
    assert "\x1b" not in plain_text
    assert "\x07" not in plain_text
    assert "\x08" not in plain_text
    assert plain_text == "safered\tok\nbadvalue!"


def test_append_output_streaming_chunks_merge_into_one_line():
    config = StatusPaneConfig(enabled=False)
    controller, _console = build_controller(width=160, status_config=config)

    controller.append_output("hel", end="")
    controller.append_output("lo")

    assert controller._output_lines == ["hello"]
    assert controller.layout["output"].renderable.renderable.plain == "hello"


def test_append_output_preserves_rich_renderables():
    from rich.panel import Panel

    controller, console = build_controller(width=160)

    controller.append_output(Panel("tool body", title="Tool Panel"))
    controller.render_once()

    text = console.export_text(styles=False)
    assert "Tool Panel" in text
    assert "tool body" in text
    assert "<rich.panel.Panel object" not in text


def test_constructor_defers_initial_refresh_until_needed():
    class SpyStatusPane:
        def __init__(self):
            self.calls = 0

        def get_renderable(self, terminal_columns=None):
            self.calls += 1
            return None

    console = Console(record=True, width=160, height=80)
    pane = SpyStatusPane()

    controller = AgentLayoutController(console, pane, StatusPaneConfig())

    assert pane.calls == 0
    controller.refresh()
    assert pane.calls == 1
