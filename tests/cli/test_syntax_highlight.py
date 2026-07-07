from __future__ import annotations

from myagent.cli.syntax_highlight import (
    Fragment,
    StyledLine,
    fragments_plain,
    highlight_transcript_text,
    normalize_language,
    split_fenced_code_blocks,
)


def flatten(lines: list[StyledLine]) -> list[Fragment]:
    return [fragment for line in lines for fragment in line.fragments]


def test_normalize_language_supports_c_cpp_and_rust_aliases() -> None:
    assert normalize_language("c") == "c"
    assert normalize_language("cpp") == "cpp"
    assert normalize_language("c++") == "cpp"
    assert normalize_language("cc") == "cpp"
    assert normalize_language("rust") == "rust"
    assert normalize_language("rs") == "rust"


def test_unknown_language_normalizes_to_none() -> None:
    assert normalize_language("not-a-real-language") is None


def test_split_fenced_code_blocks_preserves_prose_and_code() -> None:
    text = "before\n```python\ndef run():\n    return 1\n```\nafter"

    segments = split_fenced_code_blocks(text)

    assert [
        (segment.is_code, segment.language, segment.fence_language)
        for segment in segments
    ] == [
        (False, "", ""),
        (True, "python", "python"),
        (False, "", ""),
    ]
    assert segments[0].text == "before\n"
    assert segments[1].text == "def run():\n    return 1\n"
    assert segments[2].text == "\nafter"


def test_python_code_block_highlights_keywords_comments_strings_and_numbers() -> None:
    text = "```python\n# note\ndef greet():\n    return \"hi\", 42\n```"

    lines = highlight_transcript_text(text, enabled=True)
    fragments = flatten(lines)
    plain = "\n".join(line.plain for line in lines)

    assert plain == text
    assert any("italic" in style and token == "# note" for style, token in fragments)
    assert any("bold" in style and token == "def" for style, token in fragments)
    assert any("green" in style and token == '"hi"' for style, token in fragments)
    assert any("magenta" in style and token == "42" for style, token in fragments)


def test_sql_code_block_highlights_sql_keywords_and_comments() -> None:
    text = "```sql\n-- active users\nSELECT id FROM users WHERE active = 1\n```"

    fragments = flatten(highlight_transcript_text(text, enabled=True))

    assert any("italic" in style and token == "-- active users" for style, token in fragments)
    assert any("bold" in style and token.upper() == "SELECT" for style, token in fragments)
    assert any("bold" in style and token.upper() == "FROM" for style, token in fragments)
    assert any("bold" in style and token.upper() == "WHERE" for style, token in fragments)


def test_unknown_fence_language_stays_unstyled() -> None:
    text = "```mystery\nSELECT 1\n```"

    fragments = flatten(highlight_transcript_text(text, enabled=True))

    assert fragments_plain(fragments) == text
    assert all(style == "" for style, _token in fragments)


def test_disabled_highlighting_returns_unstyled_fragments() -> None:
    text = "```python\ndef run():\n    return 1\n```"

    fragments = flatten(highlight_transcript_text(text, enabled=False))

    assert fragments_plain(fragments) == text
    assert all(style == "" for style, _token in fragments)
