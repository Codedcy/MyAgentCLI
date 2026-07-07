from myagent.cli.text_decode import (
    StreamingTextSanitizer,
    decode_tool_output,
    sanitize_display_text,
)


def test_decode_tool_output_preserves_utf8_text() -> None:
    assert decode_tool_output("hello \u4f60\u597d".encode("utf-8")) == "hello \u4f60\u597d"


def test_decode_tool_output_falls_back_to_gb18030_for_windows_bytes() -> None:
    assert decode_tool_output(b"\xc4\xe3\xba\xc3") == "\u4f60\u597d"


def test_decode_tool_output_prefers_gb18030_before_permissive_locale(monkeypatch) -> None:
    monkeypatch.setattr("locale.getpreferredencoding", lambda _do_setlocale=False: "cp1252")

    assert decode_tool_output(b"\xc4\xe3\xba\xc3") == "\u4f60\u597d"


def test_decode_tool_output_accepts_already_decoded_text() -> None:
    assert decode_tool_output("plain \u4f60\u597d") == "plain \u4f60\u597d"


def test_sanitize_display_text_strips_ansi_and_unsafe_controls() -> None:
    assert sanitize_display_text("\x1b[31mred\x1b[0m\x00 ok") == "red ok"


def test_sanitize_display_text_strips_literal_sgr_mouse_reports() -> None:
    assert sanitize_display_text("^[[<35;64;22Mdrag^[[<35;65;22m ok") == "drag ok"


def test_streaming_text_sanitizer_buffers_split_terminal_escapes() -> None:
    sanitizer = StreamingTextSanitizer()

    parts = [
        sanitizer.sanitize("a\x1b[2", final=False),
        sanitizer.sanitize("K b\x1bPpayload", final=False),
        sanitizer.sanitize("\x1b\\ c\x1b[?25", final=False),
        sanitizer.sanitize("h d", final=True),
    ]

    assert "".join(parts) == "a b c d"
