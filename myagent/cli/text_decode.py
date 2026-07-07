"""Text decoding and sanitizing helpers for CLI-visible output."""

from __future__ import annotations

import contextlib
import locale
import sys

from myagent.cli.layout import ANSI_PATTERN, OUTPUT_CONTROL_PATTERN


def decode_tool_output(data: bytes | str | None) -> str:
    """Decode bytes from tools/subprocesses for display without mojibake."""

    if data is None:
        return ""
    if isinstance(data, str):
        return sanitize_display_text(data)

    for encoding in _candidate_encodings():
        with contextlib.suppress(UnicodeDecodeError, LookupError):
            return sanitize_display_text(data.decode(encoding))

    return sanitize_display_text(data.decode("utf-8", errors="replace"))


def sanitize_display_text(text: object) -> str:
    """Strip ANSI escapes and unsafe controls while preserving Unicode text."""

    plain_text = "" if text is None else str(text)
    plain_text = ANSI_PATTERN.sub("", plain_text)
    return OUTPUT_CONTROL_PATTERN.sub("", plain_text)


def _candidate_encodings() -> list[str]:
    encodings = [
        "utf-8",
        locale.getpreferredencoding(False),
        getattr(sys.stdout, "encoding", None),
        getattr(sys.stderr, "encoding", None),
        "gb18030",
    ]
    seen: set[str] = set()
    candidates: list[str] = []
    for encoding in encodings:
        normalized = (encoding or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


__all__ = ["decode_tool_output", "sanitize_display_text"]
