"""Plain-text capture helpers for Rich renderables used by chat displays."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from myagent.cli.layout import ANSI_PATTERN, OUTPUT_CONTROL_PATTERN


def capture_renderable(renderable: object, width: int = 100) -> str:
    """Render a Rich-compatible object to sanitized plain terminal text."""

    console = Console(record=True, width=_normalize_width(width), color_system=None)
    if isinstance(renderable, str):
        console.print(Text(renderable), highlight=False)
    else:
        console.print(renderable, highlight=False)
    return sanitize_terminal_text(console.export_text(styles=False).rstrip("\n"))


def capture_many(renderables: list[object], width: int = 100) -> str:
    """Capture renderables in order with one newline between captured items."""

    return "\n".join(
        capture_renderable(renderable, width=width) for renderable in renderables
    )


def sanitize_terminal_text(text: object) -> str:
    """Strip ANSI escapes and unsafe controls while preserving tabs and newlines."""

    plain_text = "" if text is None else str(text)
    plain_text = plain_text.replace("\r\n", "\n").replace("\r", "\n")
    plain_text = ANSI_PATTERN.sub("", plain_text)
    return OUTPUT_CONTROL_PATTERN.sub("", plain_text)


def _normalize_width(width: int) -> int:
    if isinstance(width, bool):
        return 100
    try:
        return max(1, int(width))
    except (TypeError, ValueError):
        return 100


__all__ = ["capture_many", "capture_renderable", "sanitize_terminal_text"]
