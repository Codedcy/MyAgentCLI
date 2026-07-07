from myagent.cli.text_decode import decode_tool_output, sanitize_display_text


def test_decode_tool_output_preserves_utf8_text() -> None:
    assert decode_tool_output("hello \u4f60\u597d".encode("utf-8")) == "hello \u4f60\u597d"


def test_decode_tool_output_falls_back_to_gb18030_for_windows_bytes() -> None:
    assert decode_tool_output(b"\xc4\xe3\xba\xc3") == "\u4f60\u597d"


def test_decode_tool_output_accepts_already_decoded_text() -> None:
    assert decode_tool_output("plain \u4f60\u597d") == "plain \u4f60\u597d"


def test_sanitize_display_text_strips_ansi_and_unsafe_controls() -> None:
    assert sanitize_display_text("\x1b[31mred\x1b[0m\x00 ok") == "red ok"
