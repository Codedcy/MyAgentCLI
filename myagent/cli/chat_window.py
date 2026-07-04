"""Full-screen prompt_toolkit chat window controller."""

from __future__ import annotations

import asyncio
import inspect
import logging
import traceback
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from myagent.cli.input_controller import ChatInputActions, InputController
from myagent.cli.rich_capture import capture_renderable, sanitize_terminal_text
from myagent.cli.status import AgentInspectorPane
from myagent.cli.transcript import TranscriptBuffer, TranscriptLine

logger = logging.getLogger("myagent.cli.chat_window")

DEFAULT_COLUMNS = 100
DEFAULT_ROWS = 24
INPUT_PROMPT = "INPUT> "
ROLE_LABELS = {
    "assistant": "Agent",
    "error": "Error",
    "system": "System",
    "tool": "Tool",
    "user": "You",
}


def _clip_cells(text: str, width: int) -> str:
    """Trim text to a terminal cell width without splitting wide characters."""

    if width <= 0:
        return ""

    cells = 0
    clipped: list[str] = []
    for character in text:
        character_width = get_cwidth(character)
        if cells + character_width > width:
            break
        clipped.append(character)
        cells += character_width
    return "".join(clipped)


def _pad_cells(text: str, width: int) -> str:
    clipped = _clip_cells(text, width)
    return f"{clipped}{' ' * max(0, width - get_cwidth(clipped))}"


class ChatWindowController:
    """Owns the full-screen chat display and bottom input lifecycle."""

    def __init__(
        self,
        config: Any,
        transcript: TranscriptBuffer,
        status_pane: Any = None,
        status_model: Any = None,
        completer: Any = None,
        lexer: Any = None,
    ) -> None:
        self.config = config
        self.transcript = transcript
        self.status_model = status_model
        self.completer = completer
        self.lexer = lexer
        self.status_pane = status_pane or AgentInspectorPane(
            self._status_config(),
            status_model=status_model,
        )
        self.input_controller = InputController(config, completer=completer, lexer=lexer)

        self._app: Any = None
        self._body_control: FormattedTextControl | None = None
        self._input_field: TextArea | None = None
        self._is_running = False
        self._agent_running = False
        self._last_terminal_columns = DEFAULT_COLUMNS
        self._last_terminal_rows = DEFAULT_ROWS
        self._last_viewport_height = DEFAULT_ROWS - 1
        self._on_submit: Callable[[str], Any] | None = None
        self._on_exit: Callable[[], Any] | None = None
        self._on_interrupt: Callable[[], Any] | None = None
        self._ask_future: asyncio.Future[str | None] | None = None

        self._input_actions = ChatInputActions(
            submit=self._handle_submit,
            insert_newline=self._insert_newline,
            interrupt=self._handle_interrupt,
            request_exit=self.request_stop,
            toggle_inspector=self._toggle_inspector,
            scroll_lines=self._scroll_lines,
            page=self._page,
        )
        self._key_bindings = self.input_controller.build_key_bindings(
            self._input_actions
        )

    @property
    def is_running(self) -> bool:
        """Whether the prompt_toolkit application is currently running."""

        return self._is_running

    async def run(
        self,
        on_submit: Callable[[str], Any],
        on_exit: Callable[[], Any] | None = None,
        on_interrupt: Callable[[], Any] | None = None,
    ) -> None:
        """Start the full-screen chat application."""

        self._on_submit = on_submit
        self._on_exit = on_exit
        self._on_interrupt = on_interrupt
        started = False
        try:
            layout = self._build_layout()
            self._app = Application(
                layout=layout,
                key_bindings=self._key_bindings,
                full_screen=True,
                mouse_support=True,
            )
            started = True
            self._is_running = True
            await self._app.run_async()
        except Exception as exc:
            logger.exception(
                "Chat window startup failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_chat_window_start",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
            raise
        finally:
            self._is_running = False
            self._finish_pending_ask(None)
            if started and self._on_exit is not None:
                await self._call_async(self._on_exit)
            self._app = None

    def append_user_input(self, text: str) -> None:
        """Append user-submitted text to the visible transcript."""

        self.transcript.append_user(text)
        self.refresh()

    def append_output(self, content: object, end: str = "\n") -> None:
        """Append assistant output, capturing Rich renderables as plain text."""

        plain_text = self._plain_output(content)
        stored_content = content if not isinstance(content, str) else plain_text
        self.transcript.append_assistant(
            stored_content,
            plain_text=plain_text,
            end=end,
        )
        self.refresh()

    def append_system(self, text: str) -> None:
        """Append a system message to the visible transcript."""

        self.transcript.append_system(text)
        self.refresh()

    def append_error(self, text: str) -> None:
        """Append an error message to the visible transcript."""

        self.transcript.append_error(text)
        self.refresh()

    def refresh(self) -> None:
        """Invalidate the running application so prompt_toolkit redraws."""

        invalidate = getattr(self._app, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def request_stop(self) -> None:
        """Request the full-screen application to exit."""

        was_running = self._is_running
        self._is_running = False
        self._finish_pending_ask(None)
        if not was_running:
            return
        exit_app = getattr(self._app, "exit", None)
        if callable(exit_app):
            try:
                exit_app()
            except Exception as exc:
                logger.exception(
                    "Chat window stop failed",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "cli_chat_window_stop",
                        "exception_type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )

    def set_agent_running(self, running: bool) -> None:
        """Tell input handling whether Ctrl+C should interrupt an active run."""

        self._agent_running = bool(running)

    async def ask(self, prompt: str, timeout: float) -> str | None:
        """Ask for one response through the bottom input."""

        if self._ask_future is not None and not self._ask_future.done():
            raise RuntimeError("A chat window question is already pending")

        self.append_system(prompt)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        self._ask_future = future
        try:
            done, _ = await asyncio.wait({future}, timeout=timeout)
            if not done:
                future.cancel()
                return None
            return future.result()
        finally:
            if self._ask_future is future:
                self._ask_future = None

    def _build_layout(self) -> Layout:
        self._body_control = FormattedTextControl(self._body_text)
        body = Window(content=self._body_control, wrap_lines=False)
        self._input_field = TextArea(
            height=self.input_controller.input_height_for_text(""),
            prompt=INPUT_PROMPT,
            multiline=True,
            completer=self.completer,
            lexer=self.lexer,
            wrap_lines=False,
        )
        self._input_field.buffer.on_text_changed += self._on_input_text_changed
        return Layout(HSplit([body, self._input_field]))

    def _body_text(self) -> str:
        columns, rows = self._current_terminal_size()
        input_text = self._current_input_text()
        input_height = self._sync_input_height(input_text)
        return self._render_body_for_size(
            terminal_columns=columns,
            terminal_rows=max(1, rows - input_height),
        )

    def _render_for_size(
        self,
        terminal_columns: int,
        terminal_rows: int,
        input_text: str = "",
    ) -> str:
        columns = max(1, int(terminal_columns))
        rows = max(1, int(terminal_rows))
        input_lines = self._input_lines(input_text, columns)
        body_rows = max(1, rows - len(input_lines))
        body = self._render_body_for_size(columns, body_rows)
        return "\n".join([body, *input_lines])

    def _render_body_for_size(
        self,
        terminal_columns: int,
        terminal_rows: int,
    ) -> str:
        columns = max(1, int(terminal_columns))
        rows = max(1, int(terminal_rows))
        self._last_terminal_columns = columns
        self._last_terminal_rows = rows
        self._last_viewport_height = rows

        status_text = self._status_text(columns)
        status_lines = status_text.splitlines()
        if not status_lines:
            return "\n".join(self._conversation_lines(rows, columns))

        status_width = min(
            max((len(line) for line in status_lines), default=0),
            max(1, columns - 1),
        )
        conversation_width = max(1, columns - status_width - 1)
        conversation_lines = self._conversation_lines(rows, conversation_width)
        rendered_lines: list[str] = []
        for index in range(rows):
            left = conversation_lines[index] if index < len(conversation_lines) else ""
            right = status_lines[index] if index < len(status_lines) else ""
            if right:
                rendered_lines.append(
                    f"{_pad_cells(left, conversation_width)} {right}"
                )
            else:
                rendered_lines.append(_clip_cells(left, columns))
        return "\n".join(rendered_lines)

    def _conversation_lines(self, height: int, width: int) -> list[str]:
        lines = [
            self._transcript_line_text(line, width)
            for line in self.transcript.visible_lines(height)
        ]
        if (
            self.transcript.unread_count
            and not self.transcript.at_bottom(height)
        ):
            self._place_unread_marker(
                lines,
                f"[{self.transcript.unread_count} new messages]",
                width,
            )

        if not lines:
            lines = ["Conversation"]

        clipped = lines[-height:]
        return [_clip_cells(line, width) for line in clipped] + [""] * max(
            0,
            height - len(clipped),
        )

    def _transcript_line_text(self, line: TranscriptLine, width: int) -> str:
        if line.line_index == 0:
            label = ROLE_LABELS.get(line.entry.role, line.entry.role.title())
            return _clip_cells(f"{label}: {line.text}", width)
        return _clip_cells(f"  {line.text}", width)

    def _place_unread_marker(
        self,
        lines: list[str],
        marker: str,
        width: int,
    ) -> None:
        if width <= 0:
            return

        marker_text = _clip_cells(marker, width)
        if not lines:
            lines.append(marker_text)
            return

        separator = "  "
        candidate = f"{lines[-1]}{separator}{marker_text}"
        if get_cwidth(candidate) <= width:
            lines[-1] = candidate
            return

        marker_width = get_cwidth(marker_text)
        separator_width = get_cwidth(separator)
        if marker_width + separator_width > width:
            lines[-1] = marker_text
            return

        prefix_width = width - marker_width - separator_width
        prefix = _clip_cells(lines[-1], prefix_width).rstrip()
        if prefix:
            lines[-1] = f"{prefix}{separator}{marker_text}"
        else:
            lines[-1] = marker_text

    def _input_lines(self, text: str, width: int) -> list[str]:
        height = self.input_controller.input_height_for_text(text)
        raw_lines = sanitize_terminal_text(text).splitlines() or [""]
        raw_lines = raw_lines[:height]
        rendered = [f"{INPUT_PROMPT}{raw_lines[0]}"]
        rendered.extend(f"{' ' * len(INPUT_PROMPT)}{line}" for line in raw_lines[1:])
        if len(rendered) < height:
            rendered.extend("" for _ in range(height - len(rendered)))
        return [_clip_cells(line, width) for line in rendered]

    def _status_text(self, terminal_columns: int) -> str:
        if self.status_pane is None:
            return ""
        try:
            renderable = self.status_pane.get_renderable(
                terminal_columns=terminal_columns
            )
            if renderable is None:
                return ""
            width = self._status_capture_width(terminal_columns)
            return capture_renderable(renderable, width=width)
        except Exception as exc:
            logger.exception(
                "Chat window status render failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_chat_window_render",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
            return ""

    def _status_capture_width(self, terminal_columns: int) -> int:
        preferred_width = getattr(self.status_pane, "preferred_width", None)
        if callable(preferred_width):
            try:
                return max(1, int(preferred_width(terminal_columns=terminal_columns)))
            except Exception as exc:
                logger.exception(
                    "Chat window status width calculation failed",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "cli_chat_window_render",
                        "exception_type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
                return max(1, min(DEFAULT_COLUMNS, terminal_columns))
        return max(1, min(DEFAULT_COLUMNS, terminal_columns))

    def _plain_output(self, content: object) -> str:
        if isinstance(content, str):
            return sanitize_terminal_text(content)
        return capture_renderable(content, width=self._last_terminal_columns)

    def _handle_submit(self, text: str) -> None:
        normalized = self.input_controller.normalize_submit_text(text)
        if not normalized:
            return
        self.append_user_input(normalized)
        if self._ask_future is not None and not self._ask_future.done():
            self._finish_pending_ask(normalized)
            return
        if self._on_submit is not None:
            self._call_background(self._on_submit, normalized)

    def _insert_newline(self, buffer: Any) -> None:
        insert_text = getattr(buffer, "insert_text", None)
        if callable(insert_text):
            insert_text("\n")
        self._sync_input_height()
        self.refresh()

    def _handle_interrupt(self) -> bool:
        if not self._agent_running:
            return False
        if self._on_interrupt is not None:
            self._call_background(self._on_interrupt)
        return True

    def _toggle_inspector(self) -> None:
        toggle = getattr(self.status_pane, "toggle", None)
        if callable(toggle):
            toggle()
            self.refresh()

    def _scroll_lines(self, delta: int) -> None:
        self.transcript.scroll_lines(delta, self._last_viewport_height)
        self.refresh()

    def _page(self, delta: int) -> None:
        self.transcript.page(delta, self._last_viewport_height)
        self.refresh()

    def _current_terminal_size(self) -> tuple[int, int]:
        app = get_app_or_none()
        output = getattr(app, "output", None)
        get_size = getattr(output, "get_size", None)
        if callable(get_size):
            size = get_size()
            columns = getattr(size, "columns", self._last_terminal_columns)
            rows = getattr(size, "rows", self._last_terminal_rows)
            return max(1, int(columns)), max(1, int(rows))
        return self._last_terminal_columns, self._last_terminal_rows

    def _current_input_text(self) -> str:
        if self._input_field is None:
            return ""
        buffer = getattr(self._input_field, "buffer", None)
        return getattr(buffer, "text", "") or ""

    def _on_input_text_changed(self, buffer: Any) -> None:
        self._sync_input_height(getattr(buffer, "text", "") or "")
        self.refresh()

    def _sync_input_height(self, text: str | None = None) -> int:
        if text is None:
            text = self._current_input_text()
        height = self.input_controller.input_height_for_text(text)
        if self._input_field is not None:
            self._input_field.window.height = height
        return height

    def _finish_pending_ask(self, value: str | None) -> None:
        future = self._ask_future
        if future is not None and not future.done():
            future.set_result(value)

    def _chat_config(self) -> Any:
        ui_config = getattr(self.config, "ui", None)
        chat_window = getattr(ui_config, "chat_window", None)
        if chat_window is not None:
            return chat_window
        return getattr(self.config, "chat_window", self.config)

    def _status_config(self) -> Any:
        ui_config = getattr(self.config, "ui", None)
        status_pane = getattr(ui_config, "status_pane", None)
        if status_pane is not None:
            return status_pane
        return getattr(self.config, "status_pane", self.config)

    def _call_background(self, callback: Callable[..., Any], *args: Any) -> None:
        result = callback(*args)
        if not inspect.isawaitable(result):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            logger.exception(
                "Chat window callback could not be scheduled",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_chat_window_callback",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
            close = getattr(result, "close", None)
            if callable(close):
                close()
            return
        loop.create_task(result)

    async def _call_async(self, callback: Callable[..., Any], *args: Any) -> None:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

__all__ = ["ChatWindowController"]
