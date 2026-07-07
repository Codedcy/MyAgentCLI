"""Display-only syntax highlighting helpers for the prompt_toolkit chat TUI."""

from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Keyword, Literal, Name, Number, String, Token

logger = logging.getLogger("myagent.cli.syntax_highlight")

type Fragment = tuple[str, str]

FENCE_PATTERN = re.compile(
    r"```([A-Za-z0-9_+#.-]*)[ \t]*\n(.*?)```",
    re.DOTALL,
)
KEYWORD_STYLE = "bold cyan"
TYPE_STYLE = "cyan"
STRING_STYLE = "green"
NUMBER_STYLE = "magenta"
COMMENT_STYLE = "italic brightblack"
NAME_STYLE = "yellow"

LANGUAGE_ALIASES = {
    "py": "python",
    "python": "python",
    "js": "javascript",
    "javascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "sql": "sql",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "bash": "bash",
    "sh": "bash",
    "shell": "bash",
    "powershell": "powershell",
    "ps1": "powershell",
    "html": "html",
    "css": "css",
    "xml": "xml",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "h": "cpp",
    "hpp": "cpp",
    "rust": "rust",
    "rs": "rust",
    "markdown": "markdown",
    "md": "markdown",
    "text": "text",
    "txt": "text",
}


@dataclass(frozen=True, slots=True)
class CodeFenceSegment:
    is_code: bool
    language: str
    fence_language: str
    text: str


@dataclass(frozen=True, slots=True)
class StyledLine:
    plain: str
    fragments: list[Fragment]


def normalize_language(language: str) -> str | None:
    normalized = (language or "text").strip().lower()
    canonical = LANGUAGE_ALIASES.get(normalized)
    if canonical is None:
        return None
    try:
        get_lexer_by_name(canonical)
    except Exception as exc:
        logger.exception(
            "Syntax lexer unavailable",
            extra={
                "category": "error",
                "component": "agent",
                "context": f"cli_syntax_highlight_lexer:{canonical}",
                "exception_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            },
        )
        return None
    return canonical


def split_fenced_code_blocks(text: str) -> list[CodeFenceSegment]:
    text = _normalize_newlines(text)
    segments: list[CodeFenceSegment] = []
    cursor = 0
    for match in FENCE_PATTERN.finditer(text):
        if match.start() > cursor:
            segments.append(CodeFenceSegment(False, "", "", text[cursor : match.start()]))
        fence_language = (match.group(1) or "").strip()
        language = (normalize_language(fence_language) or "") if fence_language else ""
        segments.append(
            CodeFenceSegment(
                True,
                language,
                fence_language,
                match.group(2),
            )
        )
        cursor = match.end()
    if cursor < len(text):
        segments.append(CodeFenceSegment(False, "", "", text[cursor:]))
    return segments or [CodeFenceSegment(False, "", "", "")]


def highlight_transcript_text(text: str, *, enabled: bool) -> list[StyledLine]:
    text = _normalize_newlines(text)
    if not enabled:
        return _plain_lines(text)

    fragments: list[Fragment] = []
    for segment in split_fenced_code_blocks(text):
        if not segment.is_code:
            fragments.append(("", segment.text))
            continue
        fence_header = f"```{segment.fence_language}"
        fragments.append(("", fence_header + "\n"))
        if segment.text and segment.language:
            fragments.extend(_highlight_code_fragments(segment.text, segment.language))
        elif segment.text:
            fragments.append(("", segment.text))
        fragments.append(("", "```"))
    return _fragments_to_lines(_merge_fragments(fragments))


def fragments_plain(fragments: list[Fragment]) -> str:
    return "".join(text for _style, text in fragments)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _highlight_code_fragments(code: str, language: str) -> list[Fragment]:
    try:
        lexer = get_lexer_by_name(language)
        raw_fragments = [(_style_for_token(token), value) for token, value in lex(code, lexer)]
    except Exception as exc:
        logger.exception(
            "Syntax highlighting failed",
            extra={
                "category": "error",
                "component": "agent",
                "context": f"cli_syntax_highlight:{language}",
                "exception_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            },
        )
        return [("", code)]
    if fragments_plain(raw_fragments) == code + "\n" and not code.endswith("\n"):
        raw_fragments = _remove_fragment_suffix(raw_fragments, "\n")
    if fragments_plain(raw_fragments) != code:
        return [("", code)]
    return _merge_fragments(raw_fragments)


def _merge_fragments(fragments: list[Fragment]) -> list[Fragment]:
    merged: list[Fragment] = []
    for style, value in fragments:
        if not value:
            continue
        if merged and merged[-1][0] == style:
            merged[-1] = (style, merged[-1][1] + value)
        else:
            merged.append((style, value))
    return merged


def _remove_fragment_suffix(fragments: list[Fragment], suffix: str) -> list[Fragment]:
    remaining = suffix
    trimmed = list(fragments)
    while remaining and trimmed:
        style, value = trimmed.pop()
        if value.endswith(remaining):
            kept = value[: -len(remaining)]
            if kept:
                trimmed.append((style, kept))
            return trimmed
        if remaining.endswith(value):
            remaining = remaining[: -len(value)]
            continue
        trimmed.append((style, value))
        return fragments
    return trimmed if not remaining else fragments


def _style_for_token(token: Token) -> str:
    if token in Comment:
        return COMMENT_STYLE
    if token in Keyword:
        return KEYWORD_STYLE
    if token in String:
        return STRING_STYLE
    if token in Number or token in Literal.Number:
        return NUMBER_STYLE
    if token in Name.Function or token in Name.Class:
        return NAME_STYLE
    if token in Name.Builtin or token in Name.Decorator or token in Name.Namespace:
        return TYPE_STYLE
    return ""


def _plain_lines(text: str) -> list[StyledLine]:
    return _fragments_to_lines([("", text)])


def _fragments_to_lines(fragments: list[Fragment]) -> list[StyledLine]:
    lines: list[list[Fragment]] = [[]]
    for style, value in fragments:
        parts = value.split("\n")
        for index, part in enumerate(parts):
            if index:
                lines.append([])
            if part:
                lines[-1].append((style, part))
    return [
        StyledLine(fragments_plain(line_fragments), line_fragments or [("", "")])
        for line_fragments in lines
    ]


def _trim_split_artifacts(lines: list[StyledLine]) -> list[StyledLine]:
    if len(lines) > 1 and lines[0].plain == "":
        lines = lines[1:]
    if len(lines) > 1 and lines[-1].plain == "":
        lines = lines[:-1]
    return lines
