"""Input controller for the future full-screen chat window."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

DEFAULT_INPUT_MIN_LINES = 1
DEFAULT_INPUT_MAX_LINES = 6
DEFAULT_INSPECTOR_TOGGLE_KEY = "f2"
SCROLL_LINES_PER_WHEEL_EVENT = 3
TAB_EQUIVALENT_KEYS = {"c-i", "ctrl+i", "control+i", "control-i", "tab"}
RESERVED_TOGGLE_KEYS = TAB_EQUIVALENT_KEYS | {
    "c-c",
    "c-d",
    "c-m",
    "control-c",
    "control-d",
    "control-m",
    "control+c",
    "control+d",
    "control+m",
    "ctrl+c",
    "ctrl+d",
    "ctrl+m",
    "end",
    "enter",
    "esc",
    "escape",
    "home",
    "page-down",
    "page-up",
    "pagedown",
    "pageup",
    "return",
    "scroll-down",
    "scroll-up",
}
logger = logging.getLogger("myagent.cli.input_controller")


@dataclass(frozen=True, slots=True)
class ChatInputActions:
    """Callbacks used by the chat input key bindings.

    ``interrupt`` returns True when an active run was interrupted; Ctrl+C then
    stops without idle behavior. It returns False when no active run exists, so
    the controller may clear non-empty input or request exit for empty input.
    """

    submit: Callable[[str], Any]
    insert_newline: Callable[[Any], Any]
    interrupt: Callable[[], bool]
    request_exit: Callable[[], Any]
    toggle_inspector: Callable[[], Any]
    scroll_lines: Callable[[int], Any]
    page: Callable[[int], Any]


class InputController:
    """Build prompt_toolkit input bindings without depending on the REPL engine."""

    def __init__(self, config: Any, completer: Any = None, lexer: Any = None) -> None:
        self.config = config
        self.completer = completer
        self.lexer = lexer

    def build_key_bindings(self, actions: ChatInputActions) -> KeyBindings:
        """Build key bindings that delegate application actions to callbacks."""

        kb = KeyBindings()

        @kb.add("enter")
        def _(event) -> None:
            buffer = self._event_buffer(event)
            text = self.normalize_submit_text(self._buffer_text(buffer))
            if not text:
                return
            actions.submit(text)
            self._reset_buffer(buffer)

        @kb.add("escape", "enter")
        def _(event) -> None:
            actions.insert_newline(self._event_buffer(event))

        @kb.add("c-c")
        def _(event) -> None:
            buffer = self._event_buffer(event)
            if actions.interrupt():
                return
            if self._buffer_text(buffer):
                self._reset_buffer(buffer)
                return
            actions.request_exit()

        @kb.add("c-d")
        def _(event) -> None:
            if not self._buffer_text(self._event_buffer(event)):
                actions.request_exit()

        self._add_toggle_binding(kb, actions)

        @kb.add("pageup")
        def _(event) -> None:
            actions.page(-1)

        @kb.add("pagedown")
        def _(event) -> None:
            actions.page(1)

        @kb.add(Keys.ScrollUp)
        def _(event) -> None:
            actions.scroll_lines(-SCROLL_LINES_PER_WHEEL_EVENT)

        @kb.add(Keys.ScrollDown)
        def _(event) -> None:
            actions.scroll_lines(SCROLL_LINES_PER_WHEEL_EVENT)

        return kb

    @staticmethod
    def normalize_submit_text(text: str) -> str:
        """Trim surrounding whitespace while preserving internal newlines."""

        return text.strip()

    def input_height_for_text(self, text: str) -> int:
        """Return the bounded input height for the current draft text."""

        min_lines = max(
            DEFAULT_INPUT_MIN_LINES,
            self._line_setting("input_min_lines", DEFAULT_INPUT_MIN_LINES),
        )
        max_lines = max(
            min_lines,
            self._line_setting("input_max_lines", DEFAULT_INPUT_MAX_LINES),
        )
        text_lines = (text or "").count("\n") + 1
        return min(max(text_lines, min_lines), max_lines)

    def _add_toggle_binding(
        self,
        kb: KeyBindings,
        actions: ChatInputActions,
    ) -> None:
        def toggle_inspector(event) -> None:
            actions.toggle_inspector()

        key = self._inspector_toggle_key()
        try:
            kb.add(key)(toggle_inspector)
        except ValueError as exc:
            logger.exception(
                "Chat inspector toggle key binding failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_input_toggle_binding",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
            kb.add(DEFAULT_INSPECTOR_TOGGLE_KEY)(toggle_inspector)

    def _line_setting(self, name: str, default: int) -> int:
        value = getattr(self._chat_config(), name, default)
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            logger.exception(
                "Chat input line setting could not be parsed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": f"cli_input_line_setting_{name}",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
            return default

    def _chat_config(self) -> Any:
        for candidate in self._config_candidates():
            chat_window = getattr(candidate, "chat_window", None)
            if chat_window is not None:
                return chat_window
            if hasattr(candidate, "input_min_lines") or hasattr(
                candidate,
                "input_max_lines",
            ):
                return candidate
        return self.config

    def _inspector_toggle_key(self) -> str:
        for candidate in self._config_candidates():
            key = getattr(candidate, "toggle_key", None)
            if key:
                return self._normalize_toggle_key(key)

            status_pane = getattr(candidate, "status_pane", None)
            key = getattr(status_pane, "toggle_key", None)
            if key:
                return self._normalize_toggle_key(key)

        return DEFAULT_INSPECTOR_TOGGLE_KEY

    def _normalize_toggle_key(self, key: Any) -> str:
        key_text = str(key or DEFAULT_INSPECTOR_TOGGLE_KEY).strip().lower()
        if (
            not key_text
            or key_text in TAB_EQUIVALENT_KEYS
            or key_text in RESERVED_TOGGLE_KEYS
        ):
            return DEFAULT_INSPECTOR_TOGGLE_KEY
        return key_text

    def _config_candidates(self) -> list[Any]:
        candidates = [self.config]
        ui_config = getattr(self.config, "ui", None)
        if ui_config is not None:
            candidates.append(ui_config)
        return candidates

    @staticmethod
    def _event_buffer(event) -> Any:
        buffer = getattr(event, "current_buffer", None)
        if buffer is not None:
            return buffer
        app = getattr(event, "app", None)
        return getattr(app, "current_buffer", None)

    @staticmethod
    def _buffer_text(buffer: Any) -> str:
        return getattr(buffer, "text", "") or ""

    @staticmethod
    def _reset_buffer(buffer: Any) -> None:
        reset = getattr(buffer, "reset", None)
        if callable(reset):
            reset()


__all__ = ["ChatInputActions", "InputController"]
