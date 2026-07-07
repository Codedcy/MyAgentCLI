"""Text decoding and sanitizing helpers for CLI-visible output."""

from __future__ import annotations

import contextlib
import locale
import re
import sys

ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OUTPUT_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


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
        "gb18030",
        locale.getpreferredencoding(False),
        getattr(sys.stdout, "encoding", None),
        getattr(sys.stderr, "encoding", None),
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for encoding in encodings:
        if not encoding:
            continue
        normalized = str(encoding).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(str(encoding))
    return unique


__all__ = ["decode_tool_output", "sanitize_display_text"]
