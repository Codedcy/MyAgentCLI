"""Full-screen prompt_toolkit chat window controller."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import re
import sys
import traceback
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from myagent.cli.control_commands import is_immediate_chat_command
from myagent.cli.input_controller import (
    SCROLL_LINES_PER_WHEEL_EVENT,
    ChatInputActions,
    InputController,
)
from myagent.cli.rich_capture import capture_renderable, sanitize_terminal_text
from myagent.cli.status import AgentInspectorPane
from myagent.cli.syntax_highlight import Fragment, StyledLine, highlight_transcript_text
from myagent.cli.text_decode import StreamingTextSanitizer
from myagent.cli.transcript import TranscriptBuffer, TranscriptEntry, TranscriptLine

logger = logging.getLogger("myagent.cli.chat_window")

DEFAULT_COLUMNS = 100
DEFAULT_ROWS = 24
INPUT_PROMPT = "INPUT> "
EXIT_CONFIRMATION_MESSAGE = "Press Ctrl+C again or Ctrl+D to exit."
MOUSE_REPORTING_RESET_SEQUENCE = (
    "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l"
)
MOUSE_WHEEL_REPORTING_ENABLE_SEQUENCE = "\x1b[?1000h\x1b[?1006h"
ROLE_LABELS = {
    "assistant": "Agent",
    "error": "Error",
    "system": "System",
    "tool": "Tool",
    "user": "You",
}
ROLE_LABEL_WIDTH = max(len(label) for label in ROLE_LABELS.values())
INLINE_HEADING_PATTERN = re.compile(r"([^#\n])\s*(#{2,6}\s+)")
MARKDOWN_SIGNAL_PATTERN = re.compile(
    r"---\s*#{2,6}\s*|(?:^|\n)\s*#{2,6}\s+|[^#\n]\s*#{2,6}\s+|(?:^|\n)\s*[-*]\s+|"
    r"(?:^|\s)\d{1,2}\.\s+\S+|"
    r"(?:^|\n)?\s*\|[^|\n]+\|[^|\n]+\|.*\|\s*:?-{3,}:?\s*\||"
    r"(?:^|\n)\s*\|[^\n]*\|\s*\n\s*\|(?:\s*:?-{3,}:?\s*\|)+"
)
DASH_SEPARATOR_PATTERN = re.compile(r"\s+[-\u2013\u2014]\s+")
ORDERED_LIST_MARKER_PATTERN = re.compile(r"(?<!\S)(\d{1,2})\.\s+(?=\S)")
TABLE_SEPARATOR_CELL_PATTERN = re.compile(r":?-{3,}:?")
TREE_BRANCH_PATTERN = re.compile(r"\s*(?=[├└]──)")
DEPENDENCY_GRAPH_TITLE_PATTERN = re.compile(
    r"^(?P<title>(?:依赖图|依赖关系|Dependency Graph|Dependencies))\s*"
    r"(?=(?:Phase|阶段|Step|\d))",
    re.IGNORECASE,
)
DEPENDENCY_GRAPH_GAP_PATTERN = re.compile(r"\s{2,}(?=(?:-->|[└├]-->|[└├]))")
DEPENDENCY_GRAPH_PROSE_JOIN_PATTERN = re.compile(
    r"(?<=[)）])(?=Phase\s+\d+\s+(?:和|与|and)\b)",
    re.IGNORECASE,
)


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


def _wrap_cells(text: str, width: int) -> list[str]:
    """Wrap text to a terminal cell width without splitting wide characters."""

    if width <= 0:
        return [""]

    wrapped: list[str] = []
    raw_lines = text.splitlines() or [""]
    for raw_line in raw_lines:
        if raw_line == "":
            wrapped.append("")
            continue

        current: list[str] = []
        cells = 0
        for character in raw_line:
            character_width = get_cwidth(character)
            if current and cells + character_width > width:
                wrapped.append("".join(current))
                current = []
                cells = 0

            if not current and character_width > width:
                clipped = _clip_cells(character, width)
                if clipped:
                    wrapped.append(clipped)
                continue

            current.append(character)
            cells += character_width

        wrapped.append("".join(current))

    return wrapped or [""]


def _format_agent_text_for_display(text: str) -> str:
    """Turn common compact Markdown-ish agent text into readable plain text."""

    segments = _split_code_fence_segments(text)
    format_markdown_segments = any(
        MARKDOWN_SIGNAL_PATTERN.search(segment)
        for is_code, segment in segments
        if not is_code
    )

    prepared_segments: list[tuple[bool, str, str]] = []
    code_changed = False
    for is_code, segment in segments:
        formatted_segment = (
            _format_agent_code_fence_segment(segment) if is_code else segment
        )
        code_changed = code_changed or formatted_segment != segment
        prepared_segments.append((is_code, segment, formatted_segment))

    if not format_markdown_segments and not code_changed:
        return text

    formatted_parts = []
    for is_code, original_segment, formatted_segment in prepared_segments:
        if is_code:
            formatted_parts.append(formatted_segment)
        elif format_markdown_segments:
            formatted_parts.append(_format_agent_markdown_segment(original_segment))
        else:
            formatted_parts.append(original_segment)
    return "".join(formatted_parts).strip("\n")


def _format_agent_code_fence_segment(segment: str) -> str:
    """Make compact fenced directory trees readable without changing code blocks."""

    if not segment.startswith("```") or not segment.endswith("```"):
        return segment

    inner = segment[3:-3].strip()
    if "\n" in inner:
        return segment
    if "├──" not in inner and "└──" not in inner:
        return segment

    lines = [
        line.strip()
        for line in TREE_BRANCH_PATTERN.sub("\n", inner).splitlines()
        if line.strip()
    ]
    if len(lines) < 2:
        return segment
    return "\n".join(lines)


def _split_code_fence_segments(text: str) -> list[tuple[bool, str]]:
    segments: list[tuple[bool, str]] = []
    cursor = 0
    while cursor < len(text):
        start = text.find("```", cursor)
        if start < 0:
            segments.append((False, text[cursor:]))
            break

        if start > cursor:
            segments.append((False, text[cursor:start]))

        end = text.find("```", start + 3)
        if end < 0:
            segments.append((True, text[start:]))
            break

        end += 3
        segments.append((True, text[start:end]))
        cursor = end

    return segments or [(False, "")]


def _format_agent_markdown_segment(segment: str) -> str:
    segment = re.sub(r"\s*---\s*(?=#{2,6}\s*)", "\n\n", segment)
    segment = INLINE_HEADING_PATTERN.sub(r"\1\n\n\2", segment)
    segment = _expand_compact_heading_lists(segment)
    segment = _expand_compact_ordered_lists(segment)
    segment = _expand_markdown_table_blocks(segment)

    cleaned_lines: list[str] = []
    for line in segment.split("\n"):
        cleaned_lines.extend(_clean_agent_markdown_line(line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines))


def _expand_compact_heading_lists(segment: str) -> str:
    lines: list[str] = []
    for line in segment.split("\n"):
        if "|" in line:
            lines.append(line)
            continue

        match = re.match(r"^(\s*#{2,6}\s+.*?\S)-\s+(.+)$", line)
        if not match:
            lines.append(line)
            continue

        heading, items_text = match.groups()
        lines.append(heading)
        lines.extend(
            f"- {item.strip()}"
            for item in re.split(r"\s+-\s+", items_text)
            if item.strip()
        )
    return "\n".join(lines)


def _expand_compact_ordered_lists(segment: str) -> str:
    """Split compact Markdown ordered lists that arrived without line breaks."""

    lines: list[str] = []
    for line in segment.split("\n"):
        if "|" in line:
            lines.append(line)
            continue

        expanded = ORDERED_LIST_MARKER_PATTERN.sub(r"\n\1. ", line)
        if expanded.startswith("\n"):
            expanded = expanded[1:]
        lines.append(expanded)
    return "\n".join(lines)


def _clean_agent_markdown_line(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped or stripped == "---":
        return [""]

    stripped = re.sub(r"^#{2,6}\s*", "", stripped)
    stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
    stripped = re.sub(r"^\*\s+", "- ", stripped)
    table_lines = _format_pipe_table_line(stripped)
    if table_lines is not None:
        return table_lines
    dependency_lines = _format_dependency_graph_line(stripped)
    if dependency_lines is not None:
        return dependency_lines
    dash_list_lines = _format_dash_separated_bullet_line(stripped)
    if dash_list_lines is not None:
        return dash_list_lines
    return [stripped]


def _format_dependency_graph_line(stripped: str) -> list[str] | None:
    if "-->" not in stripped:
        return None
    if not (
        "Phase" in stripped
        or "阶段" in stripped
        or "依赖图" in stripped
        or any(symbol in stripped for symbol in ("┐", "┤", "└", "├"))
    ):
        return None

    lines: list[str] = []
    body = stripped
    title_match = DEPENDENCY_GRAPH_TITLE_PATTERN.match(body)
    if title_match:
        lines.append(title_match.group("title").strip())
        body = body[title_match.end() :].strip()

    body = DEPENDENCY_GRAPH_GAP_PATTERN.sub("\n", body)
    body = DEPENDENCY_GRAPH_PROSE_JOIN_PATTERN.sub("\n", body)
    lines.extend(line.strip() for line in body.splitlines() if line.strip())
    return lines if lines else None


def _format_dash_separated_bullet_line(stripped: str) -> list[str] | None:
    if not stripped.startswith("- "):
        return None

    parts = [
        part.strip()
        for part in DASH_SEPARATOR_PATTERN.split(stripped[2:].strip())
        if part.strip()
    ]
    if len(parts) < 3:
        return None

    lines: list[str] = []
    for index in range(0, len(parts), 2):
        label = parts[index]
        if index + 1 < len(parts):
            lines.append(f"- {label}: {parts[index + 1]}")
        else:
            lines.append(f"- {label}")
    return lines


def _format_pipe_table_line(stripped: str) -> list[str] | None:
    if stripped.startswith("- "):
        return None
    if stripped.count("|") < 2:
        return None

    prefix = ""
    table_text = stripped
    if not stripped.lstrip().startswith("|"):
        before_pipe, _, after_pipe = stripped.partition("|")
        if before_pipe.strip():
            prefix = before_pipe.strip()
            table_text = f"|{after_pipe}"

    cells = _pipe_cells(table_text)
    if len(cells) < 2:
        return [prefix] if prefix else None

    rows = _compact_table_rows(cells)
    if rows is not None:
        lines: list[str] = [prefix] if prefix else []
        lines.extend(_render_table_rows(rows))
        return lines

    return None


def _compact_table_rows(cells: list[str]) -> list[list[str]] | None:
    separator_index = next(
        (
            index
            for index, cell in enumerate(cells)
            if _is_table_separator_cell(cell)
        ),
        None,
    )
    if separator_index is None:
        return None

    header_end = separator_index
    for index, cell in enumerate(cells[:separator_index]):
        if not cell.strip():
            header_end = index
            break

    header = [cell for cell in cells[:header_end] if cell.strip()]
    if len(header) < 2:
        return None

    data_cells = [
        cell
        for cell in cells[separator_index:]
        if cell.strip() and not _is_table_separator_cell(cell)
    ]
    rows = [header]
    rows.extend(_chunk_cells(data_cells, len(header)))
    return rows


def _expand_markdown_table_blocks(segment: str) -> str:
    lines = segment.split("\n")
    expanded: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            index + 1 < len(lines)
            and _is_pipe_table_row(line)
            and _is_pipe_separator_row(lines[index + 1])
        ):
            rows = [_pipe_cells(line)]
            index += 2
            while (
                index < len(lines)
                and _is_pipe_table_row(lines[index])
                and not _is_pipe_separator_row(lines[index])
            ):
                rows.append(_pipe_cells(lines[index]))
                index += 1
            expanded.extend(_render_table_rows(rows))
            continue

        expanded.append(line)
        index += 1
    return "\n".join(expanded)


def _pipe_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_pipe_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _is_pipe_separator_row(line: str) -> bool:
    if not _is_pipe_table_row(line):
        return False
    cells = _pipe_cells(line)
    return bool(cells) and all(_is_table_separator_cell(cell) for cell in cells)


def _is_table_separator_cell(cell: str) -> bool:
    compact = cell.replace(" ", "")
    return bool(TABLE_SEPARATOR_CELL_PATTERN.fullmatch(compact))


def _chunk_cells(cells: list[str], column_count: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for index in range(0, len(cells), column_count):
        row = cells[index : index + column_count]
        if row:
            rows.append(row)
    return rows


def _render_table_rows(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []

    column_count = max(len(row) for row in rows)
    widths = [
        min(
            24,
            max(
                (get_cwidth(row[index]) for row in rows if index < len(row)),
                default=0,
            ),
        )
        for index in range(column_count)
    ]

    rendered: list[str] = []
    for row in rows:
        padded_cells: list[str] = []
        cleaned_row = [_clean_table_cell(cell) for cell in row]
        for index, cell in enumerate(cleaned_row):
            if index == len(row) - 1:
                padded_cells.append(cell)
            else:
                padded_cells.append(_pad_cells(cell, widths[index]))
        rendered.append("  ".join(padded_cells).rstrip())
    return rendered


def _clean_table_cell(cell: str) -> str:
    cleaned = cell.strip()
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    if len(cleaned) >= 2 and cleaned.startswith("`") and cleaned.endswith("`"):
        return cleaned[1:-1].strip()
    return cleaned


class _ChatBodyControl(FormattedTextControl):
    def __init__(self, text: Any, scroll_lines: Callable[[int], Any]) -> None:
        super().__init__(text)
        self._scroll_lines_callback = scroll_lines

    def mouse_handler(self, mouse_event):
        result = super().mouse_handler(mouse_event)
        if result is not NotImplemented:
            return result

        event_type = getattr(mouse_event, "event_type", None)
        if event_type == MouseEventType.SCROLL_UP:
            self._scroll_lines_callback(-SCROLL_LINES_PER_WHEEL_EVENT)
            return None
        if event_type == MouseEventType.SCROLL_DOWN:
            self._scroll_lines_callback(SCROLL_LINES_PER_WHEEL_EVENT)
            return None
        return NotImplemented


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
        self._last_conversation_width = DEFAULT_COLUMNS
        self._visual_scroll_offset = 0
        self._visual_unread_count = 0
        self._on_submit: Callable[[str], Any] | None = None
        self._on_exit: Callable[[], Any] | None = None
        self._on_interrupt: Callable[[], Any] | None = None
        self._ask_future: asyncio.Future[str | None] | None = None
        self._ask_transient = False
        self._transient_prompt_text: str | None = None
        self._sanitizing_input_text = False
        self._exit_confirmation_pending = False
        self._queued_submissions: list[str] = []
        self._assistant_stream_sanitizer = StreamingTextSanitizer()

        self._input_actions = ChatInputActions(
            submit=self._handle_submit,
            insert_newline=self._insert_newline,
            interrupt=self._handle_interrupt,
            request_exit=self.request_stop,
            toggle_inspector=self._toggle_inspector,
            toggle_tool_details=self.toggle_latest_tool_detail,
            scroll_lines=self._scroll_lines,
            page=self._page,
            request_exit_confirmation=self._request_exit_confirmation,
            cancel_exit_confirmation=self._cancel_exit_confirmation,
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
            mouse_support = self._mouse_support_enabled()
            self._reset_terminal_mouse_reporting()
            layout = self._build_layout()
            self._app = Application(
                layout=layout,
                key_bindings=self._key_bindings,
                full_screen=True,
                mouse_support=mouse_support,
            )
            if mouse_support:
                self._install_wheel_only_mouse_reporting(self._app)
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
            if started:
                self._reset_terminal_mouse_reporting()
            self._app = None

    def append_user_input(self, text: str) -> None:
        """Append user-submitted text to the visible transcript."""

        self._append_transcript(lambda: self.transcript.append_user(text))

    def mark_submission_started(self, text: str) -> None:
        """Move a submitted message from the visible queue into the transcript."""

        normalized = self.input_controller.normalize_submit_text(text)
        if not normalized:
            return
        self._remove_queued_submission(normalized)
        self.append_user_input(normalized)

    def append_output(self, content: object, end: str = "\n") -> None:
        """Append assistant output, capturing Rich renderables as plain text."""

        plain_text = self._plain_assistant_output(content, end=end)
        stored_content = content if not isinstance(content, str) else plain_text
        self._append_transcript(
            lambda: self.transcript.append_assistant(
                stored_content,
                plain_text=plain_text,
                end=end,
            )
        )

    def append_system(self, text: str) -> None:
        """Append a system message to the visible transcript."""

        self._append_transcript(lambda: self.transcript.append_system(text))

    def append_error(self, text: str) -> None:
        """Append an error message to the visible transcript."""

        self._append_transcript(lambda: self.transcript.append_error(text))

    def append_tool(self, content: object, detail_text: str = "") -> int:
        """Append a tool message to the visible transcript."""

        plain_text = self._plain_output(content)
        entry_id: int | None = None

        def append() -> int:
            nonlocal entry_id
            entry_id = self.transcript.append_tool(
                content,
                plain_text=plain_text,
                detail_text=detail_text,
            )
            return entry_id

        self._append_transcript(append)
        return int(entry_id or 0)

    def append_tool_summary(self, summary: str, detail_text: str = "") -> int:
        """Append a folded tool summary with optional hidden detail text."""

        entry_id: int | None = None

        def append() -> int:
            nonlocal entry_id
            entry_id = self.transcript.append_tool(
                summary,
                plain_text=summary,
                detail_text=detail_text,
            )
            return entry_id

        self._append_transcript(append)
        return int(entry_id or 0)

    def update_tool_entry(
        self,
        entry_id: int,
        summary: str,
        detail_text: str = "",
    ) -> bool:
        """Update an existing folded tool entry."""

        updated = False

        def update() -> bool:
            nonlocal updated
            updated = self.transcript.update_tool_entry(
                entry_id,
                summary,
                content=summary,
                detail_text=detail_text,
            )
            return updated

        self._append_transcript(update)
        return updated

    def toggle_latest_tool_detail(self) -> bool:
        """Toggle the current tool detail panel."""

        toggled = False

        def toggle() -> bool:
            nonlocal toggled
            toggled = self.transcript.toggle_latest_tool_detail()
            return toggled

        self._append_transcript(toggle)
        return toggled

    def _append_legacy_tool(self, content: object) -> None:
        plain_text = self._plain_output(content)
        self._append_transcript(
            lambda: self.transcript.append_tool(content, plain_text=plain_text)
        )

    def refresh(self) -> None:
        """Invalidate the running application so prompt_toolkit redraws."""

        invalidate = getattr(self._app, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def _append_transcript(self, append: Callable[[], Any]) -> None:
        before_total = self._visual_transcript_line_count()
        before_unread_total = self._visual_transcript_unread_line_count()
        should_follow = self._should_follow_visual_output()
        append()
        after_total = self._visual_transcript_line_count()
        after_unread_total = self._visual_transcript_unread_line_count()
        line_delta = max(0, after_total - before_total)
        unread_delta = max(0, after_unread_total - before_unread_total)
        if should_follow:
            self._visual_scroll_offset = 0
            self._visual_unread_count = 0
        else:
            self._visual_scroll_offset += line_delta
            self._visual_unread_count += unread_delta
            self._clamp_visual_scroll_offset(after_total, self._last_viewport_height)
        self.refresh()

    def request_stop(self) -> None:
        """Request the full-screen application to exit."""

        self._cancel_exit_confirmation()
        was_running = self._is_running
        self._is_running = False
        self._finish_pending_ask(None)
        self._clear_transient_prompt()
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
        self.refresh()

    async def ask(
        self,
        prompt: str,
        timeout: float | None,
        transient: bool = False,
    ) -> str | None:
        """Ask for one response through the bottom input."""

        if self._ask_future is not None and not self._ask_future.done():
            raise RuntimeError("A chat window question is already pending")

        self._ask_transient = bool(transient)
        if transient:
            self._transient_prompt_text = prompt
            self.refresh()
        else:
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
                self._ask_transient = False
                self._clear_transient_prompt()

    def _build_layout(self) -> Layout:
        self._body_control = _ChatBodyControl(self._body_fragments, self._scroll_lines)
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

    def _body_fragments(self) -> list[Fragment]:
        columns, rows = self._current_terminal_size()
        input_text = self._current_input_text()
        input_height = self._sync_input_height(input_text)
        return self._render_body_fragments_for_size(
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

        status_text = self._status_text(columns)
        status_lines = status_text.splitlines()
        if rows < 3 or columns < 4:
            self._last_viewport_height = rows
            if not status_lines:
                return "\n".join(
                    _pad_cells(line, columns)
                    for line in self._conversation_lines(rows, columns)
                )
            return self._render_unbordered_body(columns, rows, status_lines)

        content_rows = rows - 2
        self._last_viewport_height = content_rows
        inner_columns = columns - 2
        if not status_lines:
            conversation_width = inner_columns
            self._last_conversation_width = conversation_width
            conversation_lines = self._conversation_lines(
                content_rows,
                conversation_width,
            )
            return "\n".join(
                self._render_bordered_body(
                    columns=columns,
                    conversation_width=conversation_width,
                    conversation_lines=conversation_lines,
                    status_width=0,
                    status_lines=[],
                )
            )

        status_width = min(
            max((get_cwidth(line) for line in status_lines), default=0),
            max(1, inner_columns - 2),
        )
        conversation_width = max(1, inner_columns - status_width - 1)
        self._last_conversation_width = conversation_width
        conversation_lines = self._conversation_lines(
            content_rows,
            conversation_width,
        )
        return "\n".join(
            self._render_bordered_body(
                columns=columns,
                conversation_width=conversation_width,
                conversation_lines=conversation_lines,
                status_width=status_width,
                status_lines=status_lines,
            )
        )

    def _render_body_fragments_for_size(
        self,
        terminal_columns: int,
        terminal_rows: int,
    ) -> list[Fragment]:
        lines = self._render_styled_body_lines_for_size(
            terminal_columns,
            terminal_rows,
        )
        return self._styled_lines_to_fragments(lines)

    def _render_styled_body_lines_for_size(
        self,
        terminal_columns: int,
        terminal_rows: int,
    ) -> list[StyledLine]:
        columns = max(1, int(terminal_columns))
        rows = max(1, int(terminal_rows))
        self._last_terminal_columns = columns
        self._last_terminal_rows = rows

        status_text = self._status_text(columns)
        status_lines = status_text.splitlines()
        if rows < 3 or columns < 4:
            self._last_viewport_height = rows
            if not status_lines:
                return [
                    self._pad_styled_line(line, columns)
                    for line in self._conversation_styled_lines(rows, columns)
                ]
            return self._render_unbordered_styled_body(columns, rows, status_lines)

        content_rows = rows - 2
        self._last_viewport_height = content_rows
        inner_columns = columns - 2
        if not status_lines:
            conversation_width = inner_columns
            self._last_conversation_width = conversation_width
            conversation_lines = self._conversation_styled_lines(
                content_rows,
                conversation_width,
            )
            return self._render_bordered_styled_body(
                columns=columns,
                conversation_width=conversation_width,
                conversation_lines=conversation_lines,
                status_width=0,
                status_lines=[],
            )

        status_width = min(
            max((get_cwidth(line) for line in status_lines), default=0),
            max(1, inner_columns - 2),
        )
        conversation_width = max(1, inner_columns - status_width - 1)
        self._last_conversation_width = conversation_width
        conversation_lines = self._conversation_styled_lines(
            content_rows,
            conversation_width,
        )
        return self._render_bordered_styled_body(
            columns=columns,
            conversation_width=conversation_width,
            conversation_lines=conversation_lines,
            status_width=status_width,
            status_lines=status_lines,
        )

    def _render_unbordered_styled_body(
        self,
        columns: int,
        rows: int,
        status_lines: list[str],
    ) -> list[StyledLine]:
        status_width = min(
            max((get_cwidth(line) for line in status_lines), default=0),
            max(1, columns - 1),
        )
        conversation_width = max(1, columns - status_width - 1)
        self._last_conversation_width = conversation_width
        conversation_lines = self._conversation_styled_lines(rows, conversation_width)
        rendered_lines: list[StyledLine] = []
        for index in range(rows):
            left = (
                conversation_lines[index]
                if index < len(conversation_lines)
                else self._styled_from_plain("")
            )
            right = status_lines[index] if index < len(status_lines) else ""
            if right:
                padded_left = self._pad_styled_line(left, conversation_width)
                rendered_lines.append(
                    self._pad_styled_line(
                        StyledLine(
                            f"{padded_left.plain} {right}",
                            [*padded_left.fragments, ("", f" {right}")],
                        ),
                        columns,
                    )
                )
            else:
                rendered_lines.append(self._pad_styled_line(left, columns))
        return rendered_lines

    def _render_bordered_styled_body(
        self,
        *,
        columns: int,
        conversation_width: int,
        conversation_lines: list[StyledLine],
        status_width: int,
        status_lines: list[str],
    ) -> list[StyledLine]:
        has_status = status_width > 0 and bool(status_lines)
        top = f"+{'-' * conversation_width}"
        if has_status:
            top = f"{top}+{'-' * status_width}"
        top = f"{top}+"

        rendered = [self._pad_styled_line(self._styled_from_plain(top), columns)]
        for index in range(len(conversation_lines)):
            left = (
                conversation_lines[index]
                if index < len(conversation_lines)
                else self._styled_from_plain("")
            )
            padded_left = self._pad_styled_line(left, conversation_width)
            plain = f"|{padded_left.plain}"
            fragments: list[Fragment] = [("", "|"), *padded_left.fragments]
            if has_status:
                right = status_lines[index] if index < len(status_lines) else ""
                padded_right = _pad_cells(right, status_width)
                plain = f"{plain}|{padded_right}"
                fragments.extend([("", "|"), ("", padded_right)])
            plain = f"{plain}|"
            fragments.append(("", "|"))
            rendered.append(self._pad_styled_line(StyledLine(plain, fragments), columns))
        rendered.append(self._pad_styled_line(self._styled_from_plain(top), columns))
        return rendered

    def _styled_lines_to_fragments(self, lines: list[StyledLine]) -> list[Fragment]:
        fragments: list[Fragment] = []
        for index, line in enumerate(lines):
            if index:
                fragments.append(("", "\n"))
            fragments.extend(line.fragments)
        return fragments

    def _render_unbordered_body(
        self,
        columns: int,
        rows: int,
        status_lines: list[str],
    ) -> str:
        status_width = min(
            max((get_cwidth(line) for line in status_lines), default=0),
            max(1, columns - 1),
        )
        conversation_width = max(1, columns - status_width - 1)
        self._last_conversation_width = conversation_width
        conversation_lines = self._conversation_lines(rows, conversation_width)
        rendered_lines: list[str] = []
        for index in range(rows):
            left = conversation_lines[index] if index < len(conversation_lines) else ""
            right = status_lines[index] if index < len(status_lines) else ""
            if right:
                rendered_lines.append(
                    _pad_cells(f"{_pad_cells(left, conversation_width)} {right}", columns)
                )
            else:
                rendered_lines.append(_pad_cells(left, columns))
        return "\n".join(rendered_lines)

    def _render_bordered_body(
        self,
        *,
        columns: int,
        conversation_width: int,
        conversation_lines: list[str],
        status_width: int,
        status_lines: list[str],
    ) -> list[str]:
        has_status = status_width > 0 and bool(status_lines)
        top = f"+{'-' * conversation_width}"
        if has_status:
            top = f"{top}+{'-' * status_width}"
        top = f"{top}+"

        rendered = [_pad_cells(top, columns)]
        for index in range(len(conversation_lines)):
            left = (
                conversation_lines[index]
                if index < len(conversation_lines)
                else ""
            )
            line = f"|{_pad_cells(left, conversation_width)}"
            if has_status:
                right = status_lines[index] if index < len(status_lines) else ""
                line = f"{line}|{_pad_cells(right, status_width)}"
            line = f"{line}|"
            rendered.append(_pad_cells(line, columns))
        rendered.append(_pad_cells(top, columns))
        return rendered

    def _conversation_styled_lines(self, height: int, width: int) -> list[StyledLine]:
        height = max(1, int(height))
        width = max(1, int(width))
        self._last_conversation_width = width

        prompt_lines = [
            self._styled_from_plain(line) for line in self._transient_prompt_lines(width)
        ]
        bottom_lines = [
            self._styled_from_plain(line) for line in self._bottom_state_lines(width)
        ]
        if prompt_lines:
            if bottom_lines and height > len(bottom_lines) + len(prompt_lines):
                bottom_lines.append(self._styled_from_plain(""))
            bottom_lines.extend(prompt_lines)
        bottom_separator_height = 1 if bottom_lines and height > len(bottom_lines) else 0
        content_height = max(0, height - len(bottom_lines) - bottom_separator_height)
        transcript_lines = self._render_styled_transcript_lines(width)
        queue_lines = [
            self._styled_from_plain(line)
            for line in self._visible_queue_lines(
                width=width,
                height=content_height,
                has_transcript=bool(transcript_lines),
            )
        ]
        separator_height = 1 if transcript_lines and queue_lines else 0
        transcript_height = max(0, content_height - len(queue_lines) - separator_height)
        lines = self._slice_visual_styled_transcript_lines(
            transcript_lines,
            transcript_height,
        )

        if self._visual_unread_count and self._visual_scroll_offset > 0:
            self._place_unread_marker_styled(
                lines,
                f"[{self._visual_unread_count} new messages]",
                width,
            )

        if queue_lines:
            if lines and len(lines) < content_height:
                lines.append(self._styled_from_plain(""))
            lines.extend(queue_lines)

        if not lines and not bottom_lines:
            lines = [self._styled_from_plain("Conversation")]

        clipped = lines[:content_height]
        if bottom_lines:
            clipped.extend(
                self._styled_from_plain("")
                for _ in range(max(0, content_height - len(clipped)))
            )
            if bottom_separator_height:
                clipped.append(self._styled_from_plain(""))
            clipped.extend(bottom_lines)
        return [self._clip_styled_line(line, width) for line in clipped] + [
            self._styled_from_plain("")
            for _ in range(max(0, height - len(clipped)))
        ]

    def _conversation_lines(self, height: int, width: int) -> list[str]:
        height = max(1, int(height))
        width = max(1, int(width))
        self._last_conversation_width = width

        prompt_lines = self._transient_prompt_lines(width)
        bottom_lines = self._bottom_state_lines(width)
        if prompt_lines:
            if bottom_lines and height > len(bottom_lines) + len(prompt_lines):
                bottom_lines.append("")
            bottom_lines.extend(prompt_lines)
        bottom_separator_height = 1 if bottom_lines and height > len(bottom_lines) else 0
        content_height = max(0, height - len(bottom_lines) - bottom_separator_height)
        transcript_lines = self._render_transcript_lines(width)
        queue_lines = self._visible_queue_lines(
            width=width,
            height=content_height,
            has_transcript=bool(transcript_lines),
        )
        separator_height = 1 if transcript_lines and queue_lines else 0
        transcript_height = max(0, content_height - len(queue_lines) - separator_height)
        lines = self._slice_visual_transcript_lines(
            transcript_lines,
            transcript_height,
        )

        if self._visual_unread_count and self._visual_scroll_offset > 0:
            self._place_unread_marker(
                lines,
                f"[{self._visual_unread_count} new messages]",
                width,
            )

        if queue_lines:
            if lines and len(lines) < content_height:
                lines.append("")
            lines.extend(queue_lines)

        if not lines and not bottom_lines:
            lines = ["Conversation"]

        clipped = lines[:content_height]
        if bottom_lines:
            clipped.extend("" for _ in range(max(0, content_height - len(clipped))))
            if bottom_separator_height:
                clipped.append("")
            clipped.extend(bottom_lines)
        return [_clip_cells(line, width) for line in clipped] + [""] * max(
            0,
            height - len(clipped),
        )

    def _render_transcript_lines(self, width: int) -> list[str]:
        lines: list[str] = []
        previous_role: str | None = None
        for entry in self.transcript.entries():
            if lines and previous_role != entry.role:
                lines.append("")
            lines.extend(self._transcript_entry_text(entry, width))
            previous_role = entry.role
        return lines

    def _render_styled_transcript_lines(self, width: int) -> list[StyledLine]:
        lines: list[StyledLine] = []
        previous_role: str | None = None
        for entry in self.transcript.entries():
            if lines and previous_role != entry.role:
                lines.append(self._styled_from_plain(""))
            lines.extend(self._transcript_entry_styled_lines(entry, width))
            previous_role = entry.role
        return lines

    def _transcript_entry_text(
        self,
        entry: TranscriptEntry,
        width: int,
    ) -> list[str]:
        lines: list[str] = []
        plain_text = (
            _format_agent_text_for_display(entry.plain_text)
            if entry.role == "assistant"
            else self._transcript_display_text(entry)
        )
        for line_index, text in enumerate(plain_text.split("\n")):
            line = TranscriptLine(entry=entry, line_index=line_index, text=text)
            lines.extend(self._transcript_line_text(line, width))
        return lines

    def _transcript_entry_styled_lines(
        self,
        entry: TranscriptEntry,
        width: int,
    ) -> list[StyledLine]:
        plain_text = (
            _format_agent_text_for_display(entry.plain_text)
            if entry.role == "assistant"
            else self._transcript_display_text(entry)
        )
        highlighted_lines = highlight_transcript_text(
            plain_text,
            enabled=self._syntax_highlight_enabled()
            and self._entry_syntax_highlight_role(entry),
        )
        lines: list[StyledLine] = []
        for line_index, styled_line in enumerate(highlighted_lines):
            prefix = (
                self._role_prefix(ROLE_LABELS.get(entry.role, entry.role.title()))
                if line_index == 0
                else self._continuation_prefix()
            )
            lines.extend(self._wrap_prefixed_styled_line(prefix, styled_line, width))
        return lines

    def _entry_syntax_highlight_role(self, entry: TranscriptEntry) -> bool:
        if entry.role == "assistant":
            return True
        return bool(entry.role == "tool" and entry.expanded and entry.detail_text)

    def _transcript_display_text(self, entry: TranscriptEntry) -> str:
        if entry.role == "tool" and entry.expanded and entry.detail_text:
            return f"{entry.plain_text}\n{entry.detail_text}"
        return entry.plain_text

    def _slice_visual_styled_transcript_lines(
        self,
        transcript_lines: list[StyledLine],
        height: int,
    ) -> list[StyledLine]:
        if height <= 0 or not transcript_lines:
            return []

        total_lines = len(transcript_lines)
        self._clamp_visual_scroll_offset(total_lines, height)
        bottom = max(0, total_lines - self._visual_scroll_offset)
        top = max(0, bottom - height)
        return transcript_lines[top:bottom]

    def _slice_visual_transcript_lines(
        self,
        transcript_lines: list[str],
        height: int,
    ) -> list[str]:
        if height <= 0 or not transcript_lines:
            return []

        total_lines = len(transcript_lines)
        self._clamp_visual_scroll_offset(total_lines, height)
        bottom = max(0, total_lines - self._visual_scroll_offset)
        top = max(0, bottom - height)
        return transcript_lines[top:bottom]

    def _visual_transcript_line_count(self) -> int:
        return len(self._render_transcript_lines(self._last_conversation_width))

    def _visual_transcript_unread_line_count(self) -> int:
        return sum(
            1
            for line in self._render_transcript_lines(self._last_conversation_width)
            if line.strip()
        )

    def _should_follow_visual_output(self) -> bool:
        if self.transcript.follow_output == "always":
            return True
        if self.transcript.follow_output == "manual":
            return False
        return self._visual_scroll_offset == 0

    def _clamp_visual_scroll_offset(self, total_lines: int, viewport_height: int) -> None:
        max_scroll = max(0, total_lines - max(1, int(viewport_height)))
        self._visual_scroll_offset = min(max(self._visual_scroll_offset, 0), max_scroll)
        if self._visual_scroll_offset == 0:
            self._visual_unread_count = 0

    def _transcript_line_text(self, line: TranscriptLine, width: int) -> list[str]:
        if line.line_index == 0:
            label = ROLE_LABELS.get(line.entry.role, line.entry.role.title())
            prefix = self._role_prefix(label)
        else:
            prefix = self._continuation_prefix()
        return self._wrap_prefixed_text(prefix, line.text, width)

    def _queue_lines(self, width: int) -> list[str]:
        lines = self._wrap_prefixed_text(
            self._role_prefix("Queue"),
            f"{len(self._queued_submissions)} pending",
            width,
        )
        for index, queued in enumerate(self._queued_submissions, start=1):
            lines.extend(
                self._wrap_prefixed_text(
                    self._continuation_prefix(),
                    f"{index}. {queued}",
                    width,
                )
            )
        return lines

    def _visible_queue_lines(
        self,
        *,
        width: int,
        height: int,
        has_transcript: bool,
    ) -> list[str]:
        if not self._queued_submissions or height <= 0:
            return []

        queue_lines = self._queue_lines(width)
        if not has_transcript or height <= 5:
            return queue_lines[:height]

        max_queue_height = max(2, height // 3)
        return queue_lines[: min(len(queue_lines), max_queue_height)]

    def _transient_prompt_lines(self, width: int) -> list[str]:
        if not self._transient_prompt_text:
            return []
        lines: list[str] = []
        for index, text in enumerate(
            sanitize_terminal_text(self._transient_prompt_text).splitlines() or [""]
        ):
            prefix = self._role_prefix("Prompt") if index == 0 else self._continuation_prefix()
            lines.extend(self._wrap_prefixed_text(prefix, text, width))
        return lines

    def _bottom_state_lines(self, width: int) -> list[str]:
        if self.status_model is None:
            return []
        snapshot = self.status_model.snapshot()
        thinking = getattr(snapshot, "thinking", None)
        if thinking is None or not getattr(thinking, "active", False):
            return []
        elapsed = self._format_duration_seconds(
            getattr(thinking, "elapsed_seconds", 0.0)
        )
        return self._wrap_prefixed_text(
            self._role_prefix("State"),
            f"Thinking {elapsed}",
            width,
        )

    def _format_duration_seconds(self, elapsed_seconds: object) -> str:
        try:
            seconds = max(0.0, float(elapsed_seconds or 0.0))
        except (TypeError, ValueError) as exc:
            logger.exception(
                "Chat window thinking duration could not be parsed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_chat_window_thinking_duration",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
            seconds = 0.0
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        remainder = int(seconds % 60)
        return f"{minutes}m{remainder:02d}s"

    def _role_prefix(self, label: str) -> str:
        return f"{label:<{ROLE_LABEL_WIDTH}} | "

    def _continuation_prefix(self) -> str:
        return f"{'':<{ROLE_LABEL_WIDTH}} | "

    def _wrap_prefixed_text(self, prefix: str, text: str, width: int) -> list[str]:
        prefix_width = get_cwidth(prefix)
        if width <= prefix_width:
            return [_clip_cells(prefix.rstrip(), width)]

        content_width = width - prefix_width
        continuation_indent = 2 if text.startswith("- ") and content_width > 2 else 0
        wrap_width = max(1, content_width - continuation_indent)
        wrapped = _wrap_cells(text, wrap_width)
        lines = [f"{prefix}{wrapped[0]}"]
        continuation = self._continuation_prefix()
        for segment in wrapped[1:]:
            lines.append(f"{continuation}{' ' * continuation_indent}{segment}")
        return [_clip_cells(line, width) for line in lines]

    def _wrap_prefixed_styled_line(
        self,
        prefix: str,
        styled_line: StyledLine,
        width: int,
    ) -> list[StyledLine]:
        prefix_width = get_cwidth(prefix)
        if width <= prefix_width:
            clipped = _clip_cells(prefix.rstrip(), width)
            return [self._styled_from_plain(clipped)]

        content_width = width - prefix_width
        continuation_indent = (
            2 if styled_line.plain.startswith("- ") and content_width > 2 else 0
        )
        wrap_width = max(1, content_width - continuation_indent)
        wrapped_content = self._wrap_styled_fragments(
            styled_line.fragments,
            wrap_width,
        )

        lines = [
            self._clip_styled_line(
                StyledLine(
                    f"{prefix}{wrapped_content[0].plain}",
                    [("", prefix), *wrapped_content[0].fragments],
                ),
                width,
            )
        ]
        continuation = self._continuation_prefix()
        for segment in wrapped_content[1:]:
            indent = " " * continuation_indent
            lines.append(
                self._clip_styled_line(
                    StyledLine(
                        f"{continuation}{indent}{segment.plain}",
                        [("", continuation), ("", indent), *segment.fragments],
                    ),
                    width,
                )
            )
        return lines

    def _wrap_styled_fragments(
        self,
        fragments: list[Fragment],
        width: int,
    ) -> list[StyledLine]:
        if width <= 0:
            return [self._styled_from_plain("")]

        lines: list[list[Fragment]] = [[]]
        current_cells = 0
        for style, text in fragments:
            for chunk_style, chunk_text in self._wrap_fragment_chunks(style, text):
                chunk_parts: list[str] = []
                for character in chunk_text:
                    character_width = get_cwidth(character)
                    if current_cells and current_cells + character_width > width:
                        if chunk_parts:
                            lines[-1].append((chunk_style, "".join(chunk_parts)))
                            chunk_parts = []
                        lines.append([])
                        current_cells = 0

                    if current_cells == 0 and character_width > width:
                        clipped = _clip_cells(character, width)
                        if clipped:
                            lines[-1].append((chunk_style, clipped))
                            lines.append([])
                        continue

                    chunk_parts.append(character)
                    current_cells += character_width
                if chunk_parts:
                    lines[-1].append((chunk_style, "".join(chunk_parts)))

        return [
            StyledLine(
                self._fragments_plain(line_fragments),
                line_fragments or [("", "")],
            )
            for line_fragments in lines
        ] or [self._styled_from_plain("")]

    def _wrap_fragment_chunks(self, style: str, text: str) -> list[Fragment]:
        if style:
            return [(style, text)] if text else []
        return [("", chunk) for chunk in re.findall(r"\s+|\S+", text) if chunk]

    def _syntax_highlight_enabled(self) -> bool:
        ui_config = getattr(self.config, "ui", None)
        return bool(getattr(ui_config, "syntax_highlight", True))

    def _styled_from_plain(self, text: str) -> StyledLine:
        return StyledLine(text, [("", text)])

    def _fragments_plain(self, fragments: list[Fragment]) -> str:
        return "".join(text for _style, text in fragments)

    def _pad_styled_line(self, line: StyledLine, width: int) -> StyledLine:
        clipped = self._clip_styled_line(line, width)
        padding = " " * max(0, width - get_cwidth(clipped.plain))
        return StyledLine(
            f"{clipped.plain}{padding}",
            [*clipped.fragments, ("", padding)] if padding else clipped.fragments,
        )

    def _clip_styled_line(self, line: StyledLine, width: int) -> StyledLine:
        cells = 0
        fragments: list[Fragment] = []
        plain_parts: list[str] = []
        for style, text in line.fragments:
            kept: list[str] = []
            for character in text:
                character_width = get_cwidth(character)
                if cells + character_width > width:
                    kept_text = "".join(kept)
                    if kept_text:
                        fragments.append((style, kept_text))
                        plain_parts.append(kept_text)
                    return StyledLine("".join(plain_parts), fragments or [("", "")])
                kept.append(character)
                cells += character_width
            kept_text = "".join(kept)
            if kept_text:
                fragments.append((style, kept_text))
                plain_parts.append(kept_text)
        return StyledLine("".join(plain_parts), fragments or [("", "")])

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

    def _place_unread_marker_styled(
        self,
        lines: list[StyledLine],
        marker: str,
        width: int,
    ) -> None:
        if width <= 0:
            return

        marker_text = _clip_cells(marker, width)
        if not lines:
            lines.append(self._styled_from_plain(marker_text))
            return

        separator = "  "
        candidate = f"{lines[-1].plain}{separator}{marker_text}"
        if get_cwidth(candidate) <= width:
            lines[-1] = StyledLine(
                candidate,
                [*lines[-1].fragments, ("", f"{separator}{marker_text}")],
            )
            return

        marker_width = get_cwidth(marker_text)
        separator_width = get_cwidth(separator)
        if marker_width + separator_width > width:
            lines[-1] = self._styled_from_plain(marker_text)
            return

        prefix_width = width - marker_width - separator_width
        prefix = self._clip_styled_line(lines[-1], prefix_width)
        prefix_plain = prefix.plain.rstrip()
        if prefix_plain:
            prefix = self._clip_styled_line(prefix, get_cwidth(prefix_plain))
            lines[-1] = StyledLine(
                f"{prefix.plain}{separator}{marker_text}",
                [*prefix.fragments, ("", f"{separator}{marker_text}")],
            )
        else:
            lines[-1] = self._styled_from_plain(marker_text)

    def _input_lines(self, text: str, width: int) -> list[str]:
        height = self.input_controller.input_height_for_text(text)
        raw_lines = sanitize_terminal_text(text).splitlines() or [""]
        raw_lines = raw_lines[:height]
        rendered = [f"{INPUT_PROMPT}{raw_lines[0]}"]
        rendered.extend(f"{' ' * len(INPUT_PROMPT)}{line}" for line in raw_lines[1:])
        if len(rendered) < height:
            rendered.extend("" for _ in range(height - len(rendered)))
        return [_pad_cells(line, width) for line in rendered]

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

    def _plain_assistant_output(self, content: object, *, end: str) -> str:
        if not isinstance(content, str):
            if end != "":
                self._assistant_stream_sanitizer.reset()
            return capture_renderable(content, width=self._last_terminal_columns)

        final = end != ""
        plain_text = self._assistant_stream_sanitizer.sanitize(content, final=final)
        if final:
            self._assistant_stream_sanitizer.reset()
        return plain_text

    def _handle_submit(self, text: str) -> None:
        normalized = self.input_controller.normalize_submit_text(text)
        if not normalized:
            return
        self._cancel_exit_confirmation()
        if self._ask_future is not None and not self._ask_future.done():
            if not self._ask_transient:
                self.append_user_input(normalized)
            self._finish_pending_ask(normalized)
            return
        if self._agent_running and not is_immediate_chat_command(normalized):
            self._queue_submission(normalized)
            return
        if self._on_submit is not None:
            self._call_background(self._on_submit, normalized)

    def _queue_submission(self, text: str) -> None:
        self._queued_submissions.append(text)
        self.refresh()

    def pop_next_queued_submission(self) -> str | None:
        if not self._queued_submissions:
            return None
        text = self._queued_submissions.pop(0)
        self.refresh()
        return text

    def _remove_queued_submission(self, text: str) -> None:
        with contextlib.suppress(ValueError):
            self._queued_submissions.remove(text)

    def _insert_newline(self, buffer: Any) -> None:
        self._cancel_exit_confirmation()
        insert_text = getattr(buffer, "insert_text", None)
        if callable(insert_text):
            insert_text("\n")
        self._sync_input_height()
        self.refresh()

    def _handle_interrupt(self) -> bool:
        if not self._agent_running:
            return False
        self._cancel_exit_confirmation()
        if self._on_interrupt is not None:
            self._call_background(self._on_interrupt)
        return True

    def _request_exit_confirmation(self) -> None:
        if self._exit_confirmation_pending:
            self.request_stop()
            return
        self._exit_confirmation_pending = True
        self.append_system(EXIT_CONFIRMATION_MESSAGE)

    def _cancel_exit_confirmation(self) -> None:
        self._exit_confirmation_pending = False

    def _toggle_inspector(self) -> None:
        toggle = getattr(self.status_pane, "toggle", None)
        if callable(toggle):
            toggle()
            self.refresh()

    def _scroll_lines(self, delta: int) -> None:
        self.transcript.scroll_lines(delta, self._last_viewport_height)
        self._visual_scroll_offset -= int(delta)
        self._clamp_visual_scroll_offset(
            self._visual_transcript_line_count(),
            self._last_viewport_height,
        )
        self.refresh()

    def _page(self, delta: int) -> None:
        self._scroll_lines(delta * max(1, self._last_viewport_height))

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
        text = getattr(buffer, "text", "") or ""
        text = self._sanitize_input_buffer_text(buffer, text)
        if text:
            self._cancel_exit_confirmation()
        self._sync_input_height(text)
        self.refresh()

    def _sanitize_input_buffer_text(self, buffer: Any, text: str) -> str:
        if self._sanitizing_input_text:
            return text

        sanitized = sanitize_terminal_text(text)
        if sanitized == text:
            return text

        cursor_position = getattr(buffer, "cursor_position", len(text))
        sanitized_prefix = sanitize_terminal_text(text[:cursor_position])
        self._sanitizing_input_text = True
        try:
            buffer.text = sanitized
            with contextlib.suppress(Exception):
                buffer.cursor_position = min(len(sanitized_prefix), len(sanitized))
        finally:
            self._sanitizing_input_text = False
        return sanitized

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

    def _clear_transient_prompt(self) -> None:
        if self._transient_prompt_text is None:
            return
        self._transient_prompt_text = None
        self.refresh()

    def _chat_config(self) -> Any:
        ui_config = getattr(self.config, "ui", None)
        chat_window = getattr(ui_config, "chat_window", None)
        if chat_window is not None:
            return chat_window
        return getattr(self.config, "chat_window", self.config)

    def _mouse_support_enabled(self) -> bool:
        return bool(getattr(self._chat_config(), "mouse_support", False))

    def _install_wheel_only_mouse_reporting(self, app: Any) -> None:
        output = getattr(app, "output", None)
        write_raw = getattr(output, "write_raw", None)
        if not callable(write_raw):
            return

        def enable_wheel_only_mouse_support() -> None:
            write_raw(MOUSE_REPORTING_RESET_SEQUENCE)
            write_raw(MOUSE_WHEEL_REPORTING_ENABLE_SEQUENCE)

        def disable_wheel_only_mouse_support() -> None:
            write_raw(MOUSE_REPORTING_RESET_SEQUENCE)

        try:
            output.enable_mouse_support = enable_wheel_only_mouse_support
            output.disable_mouse_support = disable_wheel_only_mouse_support
        except Exception as exc:
            logger.exception(
                "Chat window wheel-only mouse setup failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_chat_window_mouse_setup",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )

    def _reset_terminal_mouse_reporting(self) -> None:
        is_tty = getattr(sys.stdout, "isatty", None)
        if callable(is_tty) and not is_tty():
            return
        sys.stdout.write(MOUSE_REPORTING_RESET_SEQUENCE)
        sys.stdout.flush()

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
