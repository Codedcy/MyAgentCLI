"""Rich layout controller for streamed agent output and inspector status."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import Console, RenderableType

logger = logging.getLogger("myagent.cli.layout")
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OUTPUT_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class AgentLayoutController:
    """Owns the shared Rich layout used for agent output and status rendering."""

    def __init__(self, console: Console, status_pane: Any, status_config: Any) -> None:
        self.console = console
        self.status_pane = status_pane
        self.status_config = status_config
        self.layout = Layout(name="root")
        self._output_layout = Layout(name="output", ratio=1)
        self._status_layout = Layout(name="status", size=self._full_status_width())
        self.layout.split_row(self._output_layout, self._status_layout)
        self._live: Any | None = None
        self._live_failed = False
        self._output_lines: list[str] = []
        self._line_open = False
        self._inspector_expanded = bool(getattr(status_pane, "_expanded", True))

    @property
    def is_live(self) -> bool:
        """Whether this controller currently owns an active Live display."""

        return self._live is not None

    def start(self) -> None:
        """Start the Rich Live display if it is not already active."""

        if self._live is not None or self._live_failed:
            return
        self.refresh()
        live = Live(
            self.layout,
            console=self.console,
            refresh_per_second=10,
            transient=False,
        )
        live.start()
        self._live = live

    def stop(self) -> None:
        """Stop the Rich Live display if active."""

        if self._live is None:
            return
        live = self._live
        self._live = None
        try:
            live.stop()
        except Exception:
            logger.exception(
                "Rich layout stop failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_layout_stop",
                },
            )

    def append_output(self, text: str, end: str = "\n") -> None:
        """Append streamed output text to the output buffer."""

        use_direct_fallback = self._live_failed and self._live is None
        self._append_payload(f"{text}{end}")
        self._trim_output_lines()
        self.refresh()
        if use_direct_fallback:
            self._print_direct(text, end=end)

    def set_output_lines(self, lines: list[str]) -> None:
        """Replace buffered output lines with a copy of the provided lines."""

        self._output_lines = [str(line) for line in lines]
        self._line_open = False
        self._trim_output_lines()
        self.refresh()

    def refresh(self) -> None:
        """Refresh output and status renderables, updating Live when active."""

        terminal_columns = self._terminal_columns()
        status_renderable = self._get_status_renderable(terminal_columns)

        self._output_layout.update(self._output_renderable())
        if status_renderable is None:
            self._status_layout.visible = False
        else:
            self._status_layout.visible = True
            self._status_layout.size = self._status_width(terminal_columns)
            self._status_layout.update(status_renderable)

        if self._live is None:
            return

        try:
            self._live.update(self.layout)
        except Exception:
            self._detach_failed_live()
            logger.exception(
                "Rich layout refresh failed; falling back to console output",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_layout_refresh",
                },
            )
            self._fallback_render_once()

    def toggle_inspector(self) -> bool:
        """Toggle the inspector expanded state and return the new state."""

        if hasattr(self.status_pane, "toggle"):
            self._inspector_expanded = bool(self.status_pane.toggle())
        else:
            self._inspector_expanded = not self._inspector_expanded
        self.refresh()
        return self._inspector_expanded

    def render_once(self) -> None:
        """Render the current layout once without starting Live."""

        self.refresh()
        self._fallback_render_once(context="cli_layout_render_once")

    def _append_payload(self, payload: str) -> None:
        if payload == "":
            return

        for segment in payload.splitlines(keepends=True):
            has_line_break = segment.endswith(("\n", "\r"))
            line = segment.rstrip("\r\n")
            if self._line_open and self._output_lines:
                self._output_lines[-1] += line
            else:
                self._output_lines.append(line)
            self._line_open = not has_line_break

    def _trim_output_lines(self) -> None:
        if len(self._output_lines) > 500:
            self._output_lines = self._output_lines[-300:]

    def _output_renderable(self) -> Panel:
        return Panel(
            Text(self._sanitize_output_text("\n".join(self._output_lines))),
            title="Output",
        )

    def _get_status_renderable(self, terminal_columns: int) -> RenderableType | None:
        if self.status_pane is None:
            return None
        try:
            return self.status_pane.get_renderable(terminal_columns=terminal_columns)
        except Exception:
            logger.exception(
                "Agent inspector render failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_layout_status_render",
                },
            )
            return None

    def _status_width(self, terminal_columns: int) -> int:
        if hasattr(self.status_pane, "preferred_width"):
            try:
                preferred_width = self.status_pane.preferred_width(
                    terminal_columns=terminal_columns,
                )
            except Exception:
                logger.exception(
                    "Agent inspector preferred width failed",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "cli_layout_status_width",
                    },
                )
            else:
                if isinstance(preferred_width, int | float) and preferred_width > 0:
                    return int(preferred_width)
        if self._uses_rail(terminal_columns):
            return self._int_config("rail_width", 5)
        return self._full_status_width()

    def _uses_rail(self, terminal_columns: int) -> bool:
        return (
            not self._inspector_expanded
            or terminal_columns < self._int_config("collapse_below_columns", 120)
        )

    def _full_status_width(self) -> int:
        width = self._int_config("width", 34)
        min_width = self._int_config("min_width", 28)
        max_width = self._int_config("max_width", 48)
        return min(max(width, min_width), max_width)

    def _terminal_columns(self) -> int:
        width = getattr(self.console, "width", None)
        if isinstance(width, bool):
            return 80
        if isinstance(width, int | float):
            return int(width)
        return 80

    def _pane_config(self) -> Any:
        return getattr(self.status_config, "status_pane", self.status_config)

    def _int_config(self, name: str, default: int) -> int:
        value = getattr(self._pane_config(), name, default)
        if isinstance(value, bool):
            return default
        if isinstance(value, int | float):
            return int(value)
        return default

    def _sanitize_output_text(self, text: str) -> str:
        text = ANSI_PATTERN.sub("", text)
        return OUTPUT_CONTROL_PATTERN.sub("", text)

    def _print_direct(self, text: str, end: str = "\n") -> None:
        self.console.print(Text(self._sanitize_output_text(text)), end=end)

    def _detach_failed_live(self) -> None:
        live = self._live
        self._live = None
        self._live_failed = True
        if live is None:
            return
        try:
            live.stop()
        except Exception:
            logger.exception(
                "Failed Rich Live stop after layout update failure",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_layout_live_stop_after_failure",
                },
            )

    def _fallback_render_once(self, context: str = "cli_layout_refresh_fallback") -> None:
        try:
            self.console.print(self.layout)
        except Exception:
            logger.exception(
                "Direct layout render failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": context,
                },
            )


__all__ = ["AgentLayoutController"]
