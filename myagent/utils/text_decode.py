"""Text decoding and sanitizing helpers for CLI-visible output."""

from __future__ import annotations

import contextlib
import locale
import re
import sys

ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SGR_MOUSE_REPORT_PATTERN = re.compile(
    r"(?:\x1b\[|\^\[\[|\[\[?)<\d+;\d+;\d+[Mm]"
)
OUTPUT_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CSI_FINAL_MIN = 0x40
_CSI_FINAL_MAX = 0x7E
_STRING_CONTROL_INTRODUCERS = {"]", "P", "^", "_", "X"}
_CHARSET_CONTROL_INTRODUCERS = {"(", ")", "*", "+", "-", ".", "/", "%", "#"}


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

    return StreamingTextSanitizer().sanitize(text, final=True)


class StreamingTextSanitizer:
    """Stateful sanitizer that can strip terminal escapes split across chunks."""

    def __init__(self) -> None:
        self._pending_escape = ""

    def sanitize(self, text: object, *, final: bool = False) -> str:
        plain_text = "" if text is None else str(text)
        source = f"{self._pending_escape}{plain_text}"
        source = SGR_MOUSE_REPORT_PATTERN.sub("", source)
        self._pending_escape = ""
        output: list[str] = []
        index = 0

        while index < len(source):
            character = source[index]
            if character == "\x1b":
                next_index = self._consume_escape(source, index, final=final)
                if next_index is None:
                    self._pending_escape = source[index:]
                    break
                index = next_index
                continue
            if character == "\x9b":
                next_index = self._consume_csi(source, index + 1, final=final)
                if next_index is None:
                    self._pending_escape = source[index:]
                    break
                index = next_index
                continue
            if OUTPUT_CONTROL_PATTERN.fullmatch(character):
                index += 1
                continue
            output.append(character)
            index += 1

        if final:
            self.reset()
        return "".join(output)

    def reset(self) -> None:
        self._pending_escape = ""

    def _consume_escape(
        self,
        source: str,
        index: int,
        *,
        final: bool,
    ) -> int | None:
        introducer_index = index + 1
        if introducer_index >= len(source):
            return len(source) if final else None

        introducer = source[introducer_index]
        if introducer == "[":
            return self._consume_csi(source, introducer_index + 1, final=final)
        if introducer in _STRING_CONTROL_INTRODUCERS:
            return self._consume_string_control(
                source,
                introducer_index + 1,
                final=final,
            )
        if introducer in _CHARSET_CONTROL_INTRODUCERS:
            terminator_index = introducer_index + 1
            if terminator_index >= len(source):
                return len(source) if final else None
            return terminator_index + 1
        return introducer_index + 1

    @staticmethod
    def _consume_csi(source: str, index: int, *, final: bool) -> int | None:
        cursor = index
        while cursor < len(source):
            codepoint = ord(source[cursor])
            if _CSI_FINAL_MIN <= codepoint <= _CSI_FINAL_MAX:
                return cursor + 1
            cursor += 1
        return len(source) if final else None

    @staticmethod
    def _consume_string_control(source: str, index: int, *, final: bool) -> int | None:
        cursor = index
        while cursor < len(source):
            character = source[cursor]
            if character == "\x07":
                return cursor + 1
            if (
                character == "\x1b"
                and cursor + 1 < len(source)
                and source[cursor + 1] == "\\"
            ):
                return cursor + 2
            cursor += 1
        return len(source) if final else None


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


__all__ = ["StreamingTextSanitizer", "decode_tool_output", "sanitize_display_text"]
