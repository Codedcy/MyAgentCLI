"""Framework-neutral transcript buffer for chat-style CLI displays."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OUTPUT_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
FOLLOW_OUTPUT_MODES = {"auto", "always", "manual"}


@dataclass(slots=True)
class TranscriptEntry:
    """A single display entry in the chat transcript."""

    entry_id: int
    role: str
    content: object
    plain_text: str
    is_streaming: bool = False


@dataclass(slots=True)
class TranscriptLine:
    """A single plain-text line intersecting the current transcript viewport."""

    entry: TranscriptEntry
    line_index: int
    text: str


class TranscriptBuffer:
    """Owns display-only transcript entries and viewport state."""

    def __init__(self, max_lines: int = 2000, follow_output: str = "auto") -> None:
        if follow_output not in FOLLOW_OUTPUT_MODES:
            raise ValueError(f"Unsupported follow_output mode: {follow_output}")

        self.max_lines = max(1, int(max_lines))
        self.follow_output = follow_output
        self._entries: list[TranscriptEntry] = []
        self._next_entry_id = 1
        self._scroll_offset = 0
        self._unread_count = 0
        self._streaming_entry_id: int | None = None

    @property
    def unread_count(self) -> int:
        """Number of newly appended plain-text lines while away from bottom."""

        return self._unread_count

    def append(
        self,
        role: str,
        content: object,
        plain_text: str | None = None,
        end: str = "\n",
        streaming: bool = False,
    ) -> int:
        """Append an entry or merge with the active streaming entry."""

        text = _sanitize_plain_text(content if plain_text is None else plain_text)
        is_streaming = streaming or end == ""
        should_follow = self._should_follow_output()
        lines_before = self._total_lines()

        if self._can_merge_stream(role):
            entry_id = self._merge_streaming_entry(content, text, is_streaming)
        else:
            self._close_active_stream()
            entry_id = self._append_new_entry(role, content, text, is_streaming)

        self._update_after_append(lines_before, should_follow)
        return entry_id

    def append_user(self, text: str) -> int:
        """Append a user message."""

        return self.append("user", text, plain_text=text)

    def append_assistant(
        self,
        content: object,
        plain_text: str | None = None,
        end: str = "\n",
    ) -> int:
        """Append assistant output, merging chunks while ``end`` is empty."""

        return self.append("assistant", content, plain_text=plain_text, end=end)

    def append_tool(self, content: object, plain_text: str | None = None) -> int:
        """Append tool output as a separate display entry."""

        return self.append("tool", content, plain_text=plain_text)

    def append_error(self, text: str) -> int:
        """Append an error display entry."""

        return self.append("error", text, plain_text=text)

    def append_system(self, text: str) -> int:
        """Append a system display entry."""

        return self.append("system", text, plain_text=text)

    def replace_entries(self, entries: list[TranscriptEntry]) -> None:
        """Replace display entries, preserving monotonic IDs for future appends."""

        self._entries = [
            replace(entry, plain_text=_sanitize_plain_text(entry.plain_text))
            for entry in entries
        ]
        if self._entries:
            self._next_entry_id = max(
                self._next_entry_id,
                max(entry.entry_id for entry in self._entries) + 1,
            )
        self._streaming_entry_id = (
            self._entries[-1].entry_id if self._entries[-1].is_streaming else None
        ) if self._entries else None
        self._scroll_offset = 0
        self._unread_count = 0
        self._trim_to_max_lines()

    def clear_view(self) -> None:
        """Remove visible transcript entries without resetting the ID sequence."""

        self._entries = []
        self._scroll_offset = 0
        self._unread_count = 0
        self._streaming_entry_id = None

    def scroll_lines(self, delta: int, viewport_height: int) -> None:
        """Scroll down for positive deltas and up for negative deltas."""

        self._scroll_offset -= int(delta)
        self._clamp_scroll_offset(viewport_height)

    def page(self, delta: int, viewport_height: int) -> None:
        """Scroll by one viewport height per page delta."""

        self.scroll_lines(delta * max(1, int(viewport_height)), viewport_height)

    def visible_entries(self, viewport_height: int) -> list[TranscriptEntry]:
        """Return entries intersecting the current viewport."""

        viewport_height = max(1, int(viewport_height))
        self._clamp_scroll_offset(viewport_height)
        total_lines = self._total_lines()
        if total_lines == 0:
            return []

        bottom_line = total_lines - self._scroll_offset
        top_line = max(0, bottom_line - viewport_height)
        visible: list[TranscriptEntry] = []
        line_cursor = 0
        for entry in self._entries:
            entry_lines = _entry_line_count(entry)
            entry_top = line_cursor
            entry_bottom = line_cursor + entry_lines
            if entry_top < bottom_line and entry_bottom > top_line:
                visible.append(entry)
            line_cursor = entry_bottom

        return visible

    def entries(self) -> list[TranscriptEntry]:
        """Return a snapshot of retained transcript entries."""

        return list(self._entries)

    def visible_lines(self, viewport_height: int) -> list[TranscriptLine]:
        """Return line-sliced entries intersecting the current viewport."""

        viewport_height = max(1, int(viewport_height))
        self._clamp_scroll_offset(viewport_height)
        total_lines = self._total_lines()
        if total_lines == 0:
            return []

        bottom_line = total_lines - self._scroll_offset
        top_line = max(0, bottom_line - viewport_height)
        visible: list[TranscriptLine] = []
        line_cursor = 0
        for entry in self._entries:
            entry_lines = _display_lines(entry.plain_text)
            entry_top = line_cursor
            entry_bottom = line_cursor + len(entry_lines)
            if entry_top < bottom_line and entry_bottom > top_line:
                start = max(0, top_line - entry_top)
                end = min(len(entry_lines), bottom_line - entry_top)
                visible.extend(
                    TranscriptLine(entry=entry, line_index=index, text=entry_lines[index])
                    for index in range(start, end)
                )
            line_cursor = entry_bottom

        return visible

    def at_bottom(self, viewport_height: int) -> bool:
        """Whether the viewport is currently at the newest retained line."""

        self._clamp_scroll_offset(viewport_height)
        return self._scroll_offset == 0

    def _append_new_entry(
        self,
        role: str,
        content: object,
        plain_text: str,
        is_streaming: bool,
    ) -> int:
        entry_id = self._next_entry_id
        self._next_entry_id += 1
        self._entries.append(
            TranscriptEntry(
                entry_id=entry_id,
                role=role,
                content=content,
                plain_text=plain_text,
                is_streaming=is_streaming,
            )
        )
        self._streaming_entry_id = entry_id if is_streaming else None
        return entry_id

    def _merge_streaming_entry(
        self,
        content: object,
        plain_text: str,
        is_streaming: bool,
    ) -> int:
        entry = self._entries[-1]
        merged_content = _merge_content(entry.content, content)
        merged_entry = replace(
            entry,
            content=merged_content,
            plain_text=f"{entry.plain_text}{plain_text}",
            is_streaming=is_streaming,
        )
        self._entries[-1] = merged_entry
        self._streaming_entry_id = entry.entry_id if is_streaming else None
        return entry.entry_id

    def _can_merge_stream(self, role: str) -> bool:
        if self._streaming_entry_id is None or not self._entries:
            return False
        entry = self._entries[-1]
        return entry.entry_id == self._streaming_entry_id and entry.role == role

    def _close_active_stream(self) -> None:
        if self._streaming_entry_id is None:
            return
        for index, entry in enumerate(self._entries):
            if entry.entry_id == self._streaming_entry_id:
                self._entries[index] = replace(entry, is_streaming=False)
                break
        self._streaming_entry_id = None

    def _update_after_append(self, lines_before: int, should_follow: bool) -> None:
        lines_after = self._total_lines()
        line_delta = max(0, lines_after - lines_before)

        if should_follow:
            self._scroll_offset = 0
            self._unread_count = 0
        else:
            self._scroll_offset += line_delta
            self._unread_count += line_delta

        self._trim_to_max_lines()
        self._scroll_offset = min(self._scroll_offset, max(0, self._total_lines() - 1))
        if self._scroll_offset == 0:
            self._unread_count = 0

    def _should_follow_output(self) -> bool:
        if self.follow_output == "always":
            return True
        if self.follow_output == "manual":
            return False
        return self._scroll_offset == 0

    def _trim_to_max_lines(self) -> None:
        total_lines = self._total_lines()
        while self._entries and total_lines > self.max_lines:
            overflow = total_lines - self.max_lines
            first = self._entries[0]
            first_line_count = _entry_line_count(first)
            if first_line_count <= overflow:
                removed = self._entries.pop(0)
                if removed.entry_id == self._streaming_entry_id:
                    self._streaming_entry_id = None
                total_lines -= first_line_count
                continue

            kept_lines = _display_lines(first.plain_text)[overflow:]
            trimmed_plain_text = "\n".join(kept_lines)
            trimmed_content = (
                trimmed_plain_text if isinstance(first.content, str) else first.content
            )
            self._entries[0] = replace(
                first,
                content=trimmed_content,
                plain_text=trimmed_plain_text,
            )
            total_lines -= overflow

    def _clamp_scroll_offset(self, viewport_height: int) -> None:
        max_scroll = max(0, self._total_lines() - max(1, int(viewport_height)))
        self._scroll_offset = min(max(self._scroll_offset, 0), max_scroll)
        if self._scroll_offset == 0:
            self._unread_count = 0

    def _total_lines(self) -> int:
        return sum(_entry_line_count(entry) for entry in self._entries)


def _merge_content(existing: object, chunk: object) -> object:
    if isinstance(existing, str) and isinstance(chunk, str):
        return f"{existing}{chunk}"
    return existing


def _sanitize_plain_text(text: object) -> str:
    plain_text = "" if text is None else str(text)
    plain_text = plain_text.replace("\r\n", "\n").replace("\r", "\n")
    plain_text = ANSI_PATTERN.sub("", plain_text)
    return OUTPUT_CONTROL_PATTERN.sub("", plain_text)


def _display_lines(plain_text: str) -> list[str]:
    return plain_text.split("\n")


def _entry_line_count(entry: TranscriptEntry) -> int:
    return len(_display_lines(entry.plain_text))


__all__ = ["TranscriptBuffer", "TranscriptEntry", "TranscriptLine"]
