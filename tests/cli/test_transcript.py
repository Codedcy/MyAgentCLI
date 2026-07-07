"""Tests for display-only transcript buffering."""

from myagent.cli.transcript import TranscriptBuffer, TranscriptEntry


def visible_text(buffer: TranscriptBuffer, height: int = 100) -> list[str]:
    return [entry.plain_text for entry in buffer.visible_entries(height)]


def test_append_user_creates_user_entry_and_ids_increase_monotonically():
    buffer = TranscriptBuffer()

    ids = [
        buffer.append_user("hello"),
        buffer.append_tool("tool output"),
        buffer.append_error("boom"),
        buffer.append_system("ready"),
    ]

    entries = buffer.visible_entries(viewport_height=20)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert [entry.role for entry in entries] == ["user", "tool", "error", "system"]
    assert [entry.entry_id for entry in entries] == ids
    assert visible_text(buffer) == ["hello", "tool output", "boom", "ready"]


def test_assistant_streaming_chunks_merge_until_newline_closes_entry():
    buffer = TranscriptBuffer()

    first_id = buffer.append_assistant("Hel", end="")
    second_id = buffer.append_assistant("lo", end="")
    closing_id = buffer.append_assistant("!", end="\n")

    entries = buffer.visible_entries(viewport_height=10)
    assert first_id == second_id == closing_id
    assert len(entries) == 1
    assert entries[0].role == "assistant"
    assert entries[0].content == "Hello!"
    assert entries[0].plain_text == "Hello!"
    assert entries[0].is_streaming is False

    next_id = buffer.append_assistant("fresh response")
    assert next_id > first_id
    assert visible_text(buffer) == ["Hello!", "fresh response"]


def test_tool_error_and_system_entries_stay_separate():
    buffer = TranscriptBuffer()

    first_tool_id = buffer.append_tool("tool one")
    second_tool_id = buffer.append_tool("tool two")
    error_id = buffer.append_error("error one")
    system_id = buffer.append_system("system one")

    entries = buffer.visible_entries(viewport_height=10)
    assert [entry.entry_id for entry in entries] == [
        first_tool_id,
        second_tool_id,
        error_id,
        system_id,
    ]
    assert [entry.role for entry in entries] == ["tool", "tool", "error", "system"]
    assert visible_text(buffer) == ["tool one", "tool two", "error one", "system one"]


def test_folded_tool_entry_hides_detail_until_toggled():
    buffer = TranscriptBuffer()

    buffer.append_tool(
        "Tool read completed (F3 for details)",
        detail_text="line 1\nline 2",
    )

    entry = buffer.entries()[0]
    assert entry.plain_text == "Tool read completed (F3 for details)"
    assert entry.detail_text == "line 1\nline 2"
    assert entry.expanded is False

    assert buffer.toggle_latest_tool_detail() is True

    entry = buffer.entries()[0]
    assert entry.expanded is True


def test_update_tool_entry_preserves_single_tool_record():
    buffer = TranscriptBuffer()
    entry_id = buffer.append_tool(
        "Tool read running",
        detail_text="",
    )

    assert buffer.update_tool_entry(
        entry_id,
        "Tool read completed (F3 for details)",
        detail_text="full output",
    ) is True

    entries = buffer.entries()
    assert len(entries) == 1
    assert entries[0].plain_text == "Tool read completed (F3 for details)"
    assert entries[0].detail_text == "full output"
    assert entries[0].expanded is False


def test_scrollback_trims_to_most_recent_plain_text_lines_not_entry_count():
    buffer = TranscriptBuffer(max_lines=4)

    first_id = buffer.append_system("old-a\nold-b\nold-c")
    second_id = buffer.append_user("new-a\nnew-b")

    entries = buffer.visible_entries(viewport_height=10)
    assert [entry.entry_id for entry in entries] == [first_id, second_id]
    assert entries[0].plain_text == "old-b\nold-c"
    assert entries[1].plain_text == "new-a\nnew-b"
    assert sum(entry.plain_text.count("\n") + 1 for entry in entries) == 4


def test_scroll_lines_moves_viewport_up_and_down_within_bounds():
    buffer = TranscriptBuffer()
    for index in range(10):
        buffer.append_system(f"line-{index}")

    assert visible_text(buffer, height=3) == ["line-7", "line-8", "line-9"]
    assert buffer.at_bottom(viewport_height=3) is True

    buffer.scroll_lines(delta=-2, viewport_height=3)
    assert visible_text(buffer, height=3) == ["line-5", "line-6", "line-7"]
    assert buffer.at_bottom(viewport_height=3) is False

    buffer.scroll_lines(delta=1, viewport_height=3)
    assert visible_text(buffer, height=3) == ["line-6", "line-7", "line-8"]

    buffer.scroll_lines(delta=-100, viewport_height=3)
    assert visible_text(buffer, height=3) == ["line-0", "line-1", "line-2"]

    buffer.scroll_lines(delta=100, viewport_height=3)
    assert visible_text(buffer, height=3) == ["line-7", "line-8", "line-9"]
    assert buffer.at_bottom(viewport_height=3) is True


def test_page_moves_viewport_by_viewport_height():
    buffer = TranscriptBuffer()
    for index in range(10):
        buffer.append_system(f"line-{index}")

    buffer.page(delta=-1, viewport_height=4)
    assert visible_text(buffer, height=4) == ["line-2", "line-3", "line-4", "line-5"]

    buffer.page(delta=1, viewport_height=4)
    assert visible_text(buffer, height=4) == ["line-6", "line-7", "line-8", "line-9"]


def test_auto_follow_keeps_bottom_when_output_arrives_at_bottom():
    buffer = TranscriptBuffer(follow_output="auto")
    for index in range(5):
        buffer.append_assistant(f"line-{index}")

    assert visible_text(buffer, height=3) == ["line-2", "line-3", "line-4"]
    assert buffer.at_bottom(viewport_height=3) is True

    buffer.append_assistant("line-5")

    assert visible_text(buffer, height=3) == ["line-3", "line-4", "line-5"]
    assert buffer.at_bottom(viewport_height=3) is True
    assert buffer.unread_count == 0


def test_auto_follow_does_not_yank_scrolled_view_and_clears_unread_at_bottom():
    buffer = TranscriptBuffer(follow_output="auto")
    for index in range(10):
        buffer.append_assistant(f"line-{index}")

    buffer.scroll_lines(delta=-4, viewport_height=3)
    before_output = visible_text(buffer, height=3)

    buffer.append_assistant("line-10")
    buffer.append_assistant("line-11\nline-12")

    assert before_output == ["line-3", "line-4", "line-5"]
    assert visible_text(buffer, height=3) == before_output
    assert buffer.at_bottom(viewport_height=3) is False
    assert buffer.unread_count == 3

    buffer.scroll_lines(delta=100, viewport_height=3)
    assert visible_text(buffer, height=3) == ["line-10", "line-11\nline-12"]
    assert buffer.at_bottom(viewport_height=3) is True
    assert buffer.unread_count == 0


def test_always_and_manual_follow_modes_have_explicit_viewport_behavior():
    always = TranscriptBuffer(follow_output="always")
    for index in range(5):
        always.append_assistant(f"always-{index}")
    always.scroll_lines(delta=-2, viewport_height=2)

    always.append_assistant("always-new")

    assert visible_text(always, height=2) == ["always-4", "always-new"]
    assert always.at_bottom(viewport_height=2) is True
    assert always.unread_count == 0

    manual = TranscriptBuffer(follow_output="manual")
    for index in range(3):
        manual.append_assistant(f"manual-{index}")

    manual.scroll_lines(delta=100, viewport_height=2)
    assert visible_text(manual, height=2) == ["manual-1", "manual-2"]
    manual.append_assistant("manual-new")

    assert visible_text(manual, height=2) == ["manual-1", "manual-2"]
    assert manual.at_bottom(viewport_height=2) is False
    assert manual.unread_count == 1


def test_replace_entries_resets_display_and_continues_after_loaded_ids():
    buffer = TranscriptBuffer()
    buffer.append_user("discarded")

    buffer.replace_entries(
        [
            TranscriptEntry(10, "system", "loaded-a", "loaded-a"),
            TranscriptEntry(11, "assistant", "loaded-b", "loaded-b"),
        ]
    )

    assert visible_text(buffer) == ["loaded-a", "loaded-b"]
    assert buffer.append_user("after load") == 12
    assert visible_text(buffer) == ["loaded-a", "loaded-b", "after load"]


def test_clear_view_removes_entries_without_resetting_next_entry_id():
    buffer = TranscriptBuffer()
    first_id = buffer.append_user("before clear")

    buffer.clear_view()

    assert buffer.visible_entries(viewport_height=10) == []
    assert buffer.unread_count == 0
    assert buffer.append_user("after clear") == first_id + 1
    assert visible_text(buffer) == ["after clear"]
