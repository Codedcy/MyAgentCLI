from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import get_type_hints

import pytest
from prompt_toolkit.keys import Keys

from myagent.cli.input_controller import ChatInputActions, InputController
from myagent.config.schema import (
    AppConfig,
    ChatWindowConfig,
    StatusPaneConfig,
    UIConfig,
)


class FakeBuffer:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.inserted: list[str] = []
        self.reset_calls = 0

    def insert_text(self, text: str) -> None:
        self.inserted.append(text)
        self.text += text

    def reset(self) -> None:
        self.reset_calls += 1
        self.text = ""


class ActionSpy:
    def __init__(self, interrupt_result: bool = False) -> None:
        self.interrupt_result = interrupt_result
        self.submitted: list[str] = []
        self.insert_newline_buffers: list[FakeBuffer] = []
        self.interrupt_calls = 0
        self.request_exit_calls = 0
        self.toggle_inspector_calls = 0
        self.scroll_calls: list[int] = []
        self.page_calls: list[int] = []

    def actions(self) -> ChatInputActions:
        return ChatInputActions(
            submit=self.submit,
            insert_newline=self.insert_newline,
            interrupt=self.interrupt,
            request_exit=self.request_exit,
            toggle_inspector=self.toggle_inspector,
            scroll_lines=self.scroll_lines,
            page=self.page,
        )

    def submit(self, text: str) -> None:
        self.submitted.append(text)

    def insert_newline(self, buffer: FakeBuffer) -> None:
        self.insert_newline_buffers.append(buffer)
        buffer.insert_text("\n")

    def interrupt(self) -> bool:
        self.interrupt_calls += 1
        return self.interrupt_result

    def request_exit(self) -> None:
        self.request_exit_calls += 1

    def toggle_inspector(self) -> None:
        self.toggle_inspector_calls += 1

    def scroll_lines(self, delta: int) -> None:
        self.scroll_calls.append(delta)

    def page(self, direction: int) -> None:
        self.page_calls.append(direction)


def invoke_binding(kb, keys, text: str = "") -> FakeBuffer:
    buffer = FakeBuffer(text)
    event = SimpleNamespace(
        current_buffer=buffer,
        app=SimpleNamespace(current_buffer=buffer),
    )
    for binding in kb.bindings:
        if binding.keys == tuple(keys):
            binding.handler(event)
            return buffer
    raise AssertionError(f"No binding found for {keys!r}")


def binding_keys(kb):
    return [binding.keys for binding in kb.bindings]


def test_interrupt_action_contract_is_explicit_bool_and_documented() -> None:
    hints = get_type_hints(ChatInputActions)

    assert hints["interrupt"] == Callable[[], bool]
    assert "True" in ChatInputActions.__doc__
    assert "active run" in ChatInputActions.__doc__
    assert "False" in ChatInputActions.__doc__
    assert "idle behavior" in ChatInputActions.__doc__


def test_normalize_submit_text_trims_surrounding_whitespace_only() -> None:
    controller = InputController(SimpleNamespace())

    assert controller.normalize_submit_text(" \n hello\n  world \t\n") == (
        "hello\n  world"
    )


def test_enter_submits_normalized_text_and_resets_buffer() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()

    buffer = invoke_binding(
        controller.build_key_bindings(spy.actions()),
        (Keys.ControlM,),
        " \n hello\nworld \n",
    )

    assert spy.submitted == ["hello\nworld"]
    assert buffer.reset_calls == 1
    assert buffer.text == ""


def test_escape_enter_inserts_newline_through_action() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()

    buffer = invoke_binding(
        controller.build_key_bindings(spy.actions()),
        (Keys.Escape, Keys.ControlM),
        "draft",
    )

    assert spy.insert_newline_buffers == [buffer]
    assert buffer.text == "draft\n"


def test_empty_enter_submission_is_ignored_before_submit_action() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()

    invoke_binding(
        controller.build_key_bindings(spy.actions()),
        (Keys.ControlM,),
        " \n\t ",
    )

    assert spy.submitted == []


def test_f2_calls_toggle_inspector() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()

    invoke_binding(controller.build_key_bindings(spy.actions()), (Keys.F2,))

    assert spy.toggle_inspector_calls == 1


def test_ctrl_c_calls_interrupt_when_agent_run_is_active() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy(interrupt_result=True)

    buffer = invoke_binding(
        controller.build_key_bindings(spy.actions()),
        (Keys.ControlC,),
        "draft",
    )

    assert spy.interrupt_calls == 1
    assert spy.request_exit_calls == 0
    assert buffer.reset_calls == 0


def test_ctrl_c_clears_input_when_idle_with_text() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy(interrupt_result=False)

    buffer = invoke_binding(
        controller.build_key_bindings(spy.actions()),
        (Keys.ControlC,),
        "draft",
    )

    assert spy.interrupt_calls == 1
    assert buffer.reset_calls == 1
    assert buffer.text == ""
    assert spy.request_exit_calls == 0


def test_ctrl_c_requests_exit_when_idle_with_empty_input() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy(interrupt_result=False)

    buffer = invoke_binding(
        controller.build_key_bindings(spy.actions()),
        (Keys.ControlC,),
        "",
    )

    assert spy.interrupt_calls == 1
    assert spy.request_exit_calls == 1
    assert buffer.reset_calls == 0


def test_ctrl_d_requests_exit_only_when_input_is_empty() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()
    kb = controller.build_key_bindings(spy.actions())

    empty = invoke_binding(kb, (Keys.ControlD,), "")
    non_empty = invoke_binding(kb, (Keys.ControlD,), "draft")

    assert spy.request_exit_calls == 1
    assert empty.reset_calls == 0
    assert non_empty.reset_calls == 0
    assert non_empty.text == "draft"


def test_page_keys_call_page_action() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()
    kb = controller.build_key_bindings(spy.actions())

    invoke_binding(kb, (Keys.PageUp,))
    invoke_binding(kb, (Keys.PageDown,))

    assert spy.page_calls == [-1, 1]


def test_mouse_wheel_keys_call_scroll_lines_action() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()
    kb = controller.build_key_bindings(spy.actions())

    invoke_binding(kb, (Keys.ScrollUp,))
    invoke_binding(kb, (Keys.ScrollDown,))

    assert spy.scroll_calls == [-3, 3]


def test_home_and_end_are_not_bound_by_chat_window_controller() -> None:
    controller = InputController(SimpleNamespace())
    spy = ActionSpy()

    keys = binding_keys(controller.build_key_bindings(spy.actions()))

    assert (Keys.Home,) not in keys
    assert (Keys.End,) not in keys


@pytest.mark.parametrize(
    ("toggle_key", "owned_key"),
    [
        ("enter", (Keys.ControlM,)),
        ("c-c", (Keys.ControlC,)),
        ("c-d", (Keys.ControlD,)),
    ],
)
def test_controller_owned_toggle_key_collisions_fall_back_to_f2(
    toggle_key,
    owned_key,
) -> None:
    config = SimpleNamespace(status_pane=SimpleNamespace(toggle_key=toggle_key))
    controller = InputController(config)
    spy = ActionSpy()

    kb = controller.build_key_bindings(spy.actions())
    keys = binding_keys(kb)
    invoke_binding(kb, (Keys.F2,))

    assert keys.count(owned_key) == 1
    assert (Keys.F2,) in keys
    assert spy.toggle_inspector_calls == 1


def test_input_height_respects_minimum_lines_from_direct_config() -> None:
    controller = InputController(
        SimpleNamespace(input_min_lines=3, input_max_lines=6)
    )

    assert controller.input_height_for_text("one line") == 3


def test_input_height_grows_for_multiline_input() -> None:
    controller = InputController(
        SimpleNamespace(
            chat_window=SimpleNamespace(input_min_lines=1, input_max_lines=6)
        )
    )

    assert controller.input_height_for_text("one\ntwo\nthree") == 3


def test_input_height_caps_very_long_input_at_max_lines() -> None:
    controller = InputController(
        SimpleNamespace(
            chat_window=SimpleNamespace(input_min_lines=1, input_max_lines=4)
        )
    )

    assert controller.input_height_for_text("\n".join(str(i) for i in range(20))) == 4


def test_input_height_uses_nested_ui_chat_window_config() -> None:
    controller = InputController(
        SimpleNamespace(
            ui=SimpleNamespace(
                chat_window=SimpleNamespace(input_min_lines=2, input_max_lines=5)
            )
        )
    )

    assert controller.input_height_for_text("one") == 2


def test_input_height_uses_real_chat_window_config_dataclass() -> None:
    controller = InputController(ChatWindowConfig(input_min_lines=2, input_max_lines=4))

    assert controller.input_height_for_text("one") == 2
    assert controller.input_height_for_text("\n".join(str(i) for i in range(10))) == 4


def test_key_bindings_use_real_ui_config_dataclass() -> None:
    config = UIConfig(status_pane=StatusPaneConfig(toggle_key="f3"))
    controller = InputController(config)
    spy = ActionSpy()

    invoke_binding(controller.build_key_bindings(spy.actions()), (Keys.F3,))

    assert spy.toggle_inspector_calls == 1


def test_input_controller_uses_real_app_config_dataclass() -> None:
    config = AppConfig(
        ui=UIConfig(
            chat_window=ChatWindowConfig(input_min_lines=3, input_max_lines=5),
            status_pane=StatusPaneConfig(toggle_key="f4"),
        )
    )
    controller = InputController(config)
    spy = ActionSpy()
    kb = controller.build_key_bindings(spy.actions())

    invoke_binding(kb, (Keys.F4,))

    assert controller.input_height_for_text("one") == 3
    assert spy.toggle_inspector_calls == 1
