from rich.panel import Panel
from rich.text import Text

from myagent.cli.rich_capture import (
    capture_many,
    capture_renderable,
    sanitize_terminal_text,
)


def test_capture_renderable_handles_strings_and_rich_text_as_plain_text():
    assert capture_renderable("hello from agent") == "hello from agent"
    assert capture_renderable(Text("styled answer", style="bold magenta")) == "styled answer"


def test_capture_renderable_handles_panel_without_object_repr():
    captured = capture_renderable(Panel("tool body", title="Tool Panel"), width=60)

    assert "Tool Panel" in captured
    assert "tool body" in captured
    assert "<rich.panel.Panel object" not in captured


def test_capture_renderable_does_not_write_to_stdout_or_stderr(capsys):
    captured = capture_renderable(Panel("quiet body", title="Quiet"), width=60)

    streams = capsys.readouterr()
    assert "Quiet" in captured
    assert "quiet body" in captured
    assert streams.out == ""
    assert streams.err == ""


def test_capture_many_preserves_mixed_renderable_order_with_single_separator():
    captured = capture_many(
        [
            "first line",
            Text("second line", style="green"),
            Panel("third body", title="Third"),
        ],
        width=60,
    )

    assert "first line\nsecond line\n" in captured
    assert "Third" in captured
    assert "third body" in captured
    assert "<rich.panel.Panel object" not in captured


def test_sanitize_terminal_text_strips_ansi_and_unsafe_controls():
    sanitized = sanitize_terminal_text(
        "safe\x1b[31mred\x1b[0m\tok\nbad\x07value\x08!\rnext",
    )

    assert sanitized == "safered\tok\nbadvalue!next"
    assert "\x1b" not in sanitized
    assert "\x07" not in sanitized
    assert "\x08" not in sanitized
    assert "\r" not in sanitized


def test_sanitize_terminal_text_removes_carriage_returns_without_normalizing_to_newlines():
    assert sanitize_terminal_text("one\r\ntwo\rthree") == "one\ntwothree"


def test_sanitize_terminal_text_preserves_long_readable_content():
    text = "prefix " + ("word " * 300) + "\nnext\tline"

    sanitized = sanitize_terminal_text(text)

    assert sanitized.startswith("prefix word word")
    assert sanitized.endswith("\nnext\tline")
    assert len(sanitized) == len(text)


def test_sanitize_terminal_text_converts_non_string_objects_without_raising():
    class CustomObject:
        def __str__(self):
            return "custom\x1b[31m object\x1b[0m"

    assert sanitize_terminal_text(None) == ""
    assert sanitize_terminal_text(1234) == "1234"
    assert sanitize_terminal_text(CustomObject()) == "custom object"
