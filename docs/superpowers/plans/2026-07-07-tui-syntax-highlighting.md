# TUI Syntax Highlighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add display-only syntax highlighting for fenced code blocks in the full-screen MyAgentCLI chat TUI.

**Architecture:** Add a focused syntax highlighting helper that converts fenced Markdown code blocks into prompt_toolkit style fragments while preserving plain text. Then wire the helper into `ChatWindowController` through a styled body fragments path, keeping the existing plain `_body_text()` path for layout tests and fallback.

**Tech Stack:** Python 3.12+, prompt_toolkit formatted fragments, Pygments lexers, pytest, ruff.

## Global Constraints

- Only highlight display text inside the prompt_toolkit chat window.
- Do not change transcript storage, session export, memory files, LLM prompts, tool output persistence, or ReAct behavior.
- Reuse existing `ui.syntax_highlight`; do not add a new theme config in this implementation.
- Highlight fenced code blocks first; do not scan ordinary prose for keywords.
- Support Python, JavaScript, TypeScript, SQL, JSON, YAML, Shell, PowerShell, HTML, CSS, XML, C, C++, and Rust aliases.
- Unknown or unavailable languages must render as unstyled plain text.
- Keep pane wrapping, scrolling, queue display, transient permission tray, folded tools, and F3 behavior intact.
- Use TDD for behavior changes and commit after each completed implementation batch.

---

## File Structure

- Create `myagent/cli/syntax_highlight.py`: parsing fenced code blocks, language alias normalization, Pygments token-to-style mapping, and fragment utilities.
- Create `tests/cli/test_syntax_highlight.py`: focused unit tests for language support, fallback, disabled mode, fragment text preservation, and styles.
- Modify `myagent/cli/chat_window.py`: add styled body rendering while keeping plain rendering behavior.
- Modify `tests/cli/test_chat_window.py`: add integration tests proving body fragments are colored only when enabled and that plain body text remains unchanged.
- Modify `README.md`: mention TUI fenced-code syntax highlighting and supported language families.

---

### Task 1: Syntax Highlighting Helper

**Files:**
- Create: `myagent/cli/syntax_highlight.py`
- Create: `tests/cli/test_syntax_highlight.py`

**Interfaces:**
- Produces: `Fragment = tuple[str, str]`
- Produces: `StyledLine(plain: str, fragments: list[Fragment])`
- Produces: `CodeFenceSegment(is_code: bool, language: str, text: str)`
- Produces: `normalize_language(language: str) -> str | None`
- Produces: `split_fenced_code_blocks(text: str) -> list[CodeFenceSegment]`
- Produces: `highlight_transcript_text(text: str, *, enabled: bool) -> list[StyledLine]`
- Produces: `fragments_plain(fragments: list[Fragment]) -> str`

- [ ] **Step 1: Write failing tests for language support and fallback**

Add `tests/cli/test_syntax_highlight.py` with:

```python
from __future__ import annotations

from myagent.cli.syntax_highlight import (
    fragments_plain,
    highlight_transcript_text,
    normalize_language,
    split_fenced_code_blocks,
)


def flatten(lines):
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

    assert [(segment.is_code, segment.language) for segment in segments] == [
        (False, ""),
        (True, "python"),
        (False, ""),
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
```

- [ ] **Step 2: Run helper tests to verify failure**

Run:

```bash
pytest tests/cli/test_syntax_highlight.py -q
```

Expected: FAIL because `myagent.cli.syntax_highlight` does not exist.

- [ ] **Step 3: Implement the helper**

Create `myagent/cli/syntax_highlight.py` with:

```python
"""Display-only syntax highlighting helpers for the prompt_toolkit chat TUI."""

from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass
from typing import TypeAlias

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Keyword, Literal, Name, Number, String, Token

logger = logging.getLogger("myagent.cli.syntax_highlight")

Fragment: TypeAlias = tuple[str, str]

FENCE_PATTERN = re.compile(r"```([A-Za-z0-9_+#.-]*)[ \t]*\n(.*?)```", re.DOTALL)
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
    segments: list[CodeFenceSegment] = []
    cursor = 0
    for match in FENCE_PATTERN.finditer(text):
        if match.start() > cursor:
            segments.append(CodeFenceSegment(False, "", text[cursor : match.start()]))
        language = normalize_language(match.group(1) or "text") or ""
        segments.append(CodeFenceSegment(True, language, match.group(2)))
        cursor = match.end()
    if cursor < len(text):
        segments.append(CodeFenceSegment(False, "", text[cursor:]))
    return segments or [CodeFenceSegment(False, "", "")]


def highlight_transcript_text(text: str, *, enabled: bool) -> list[StyledLine]:
    if not enabled:
        return _plain_lines(text)

    lines: list[StyledLine] = []
    for segment in split_fenced_code_blocks(text):
        if not segment.is_code:
            lines.extend(_plain_lines(segment.text))
            continue
        lines.append(StyledLine(f"```{segment.language or 'text'}", [("", f"```{segment.language or 'text'}")]))
        if segment.language:
            lines.extend(_highlight_code(segment.text, segment.language))
        else:
            lines.extend(_plain_lines(segment.text))
        lines.append(StyledLine("```", [("", "```")]))
    return _trim_split_artifacts(lines)


def fragments_plain(fragments: list[Fragment]) -> str:
    return "".join(text for _style, text in fragments)


def _highlight_code(code: str, language: str) -> list[StyledLine]:
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
        return _plain_lines(code)
    return _fragments_to_lines(raw_fragments)


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
    return [StyledLine(line, [("", line)]) for line in text.split("\n")]


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
```

- [ ] **Step 4: Run helper tests to verify pass**

Run:

```bash
pytest tests/cli/test_syntax_highlight.py -q
```

Expected: PASS.

- [ ] **Step 5: Run lint for the new helper**

Run:

```bash
ruff check myagent/cli/syntax_highlight.py tests/cli/test_syntax_highlight.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add myagent/cli/syntax_highlight.py tests/cli/test_syntax_highlight.py
git commit -m "feat: add tui syntax highlighting helper"
```

---

### Task 2: Chat Window Styled Fragments Integration

**Files:**
- Modify: `myagent/cli/chat_window.py`
- Modify: `tests/cli/test_chat_window.py`

**Interfaces:**
- Consumes: `highlight_transcript_text(text: str, *, enabled: bool) -> list[StyledLine]`
- Consumes: `StyledLine.plain`
- Consumes: `StyledLine.fragments`
- Produces: `ChatWindowController._body_fragments() -> list[tuple[str, str]]`
- Produces: `ChatWindowController._render_body_fragments_for_size(terminal_columns: int, terminal_rows: int) -> list[tuple[str, str]]`
- Produces: `_wrap_prefixed_styled_line(prefix: str, styled_line: StyledLine, width: int) -> list[StyledLine]`

- [ ] **Step 1: Write failing chat-window tests**

Add tests to `tests/cli/test_chat_window.py`:

```python
def fragments_text(fragments):
    return "".join(text for _style, text in fragments)


def test_body_fragments_highlight_fenced_python_code_without_changing_plain_text() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("```python\ndef run():\n    return 42\n```")

    plain = controller._render_body_for_size(terminal_columns=100, terminal_rows=10)
    fragments = controller._render_body_fragments_for_size(
        terminal_columns=100,
        terminal_rows=10,
    )

    assert fragments_text(fragments) == plain
    assert any("bold" in style and text == "def" for style, text in fragments)
    assert any("magenta" in style and text == "42" for style, text in fragments)


def test_body_fragments_highlight_sql_but_not_plain_prose_keyword() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("Please select an option.\n```sql\nSELECT * FROM users\n```")

    fragments = controller._render_body_fragments_for_size(
        terminal_columns=100,
        terminal_rows=10,
    )

    assert any("bold" in style and text.upper() == "SELECT" for style, text in fragments)
    prose_select = [
        style
        for style, text in fragments
        if text == "select"
    ]
    assert prose_select == [""]


def test_body_fragments_support_cpp_and_rust_fences() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "```cpp\nint main() { return 0; }\n```\n"
        "```rust\nfn main() { let value = 1; }\n```"
    )

    fragments = controller._render_body_fragments_for_size(
        terminal_columns=120,
        terminal_rows=14,
    )

    assert any("bold" in style and text == "int" for style, text in fragments)
    assert any("bold" in style and text == "fn" for style, text in fragments)
    assert any("magenta" in style and text in {"0", "1"} for style, text in fragments)


def test_body_fragments_can_disable_syntax_highlighting() -> None:
    config = make_config()
    config.ui.syntax_highlight = False
    controller = make_controller(config=config, status_pane=EmptyStatusPane())
    controller.append_output("```python\ndef run():\n    return 42\n```")

    fragments = controller._render_body_fragments_for_size(
        terminal_columns=100,
        terminal_rows=10,
    )

    assert fragments_text(fragments) == controller._render_body_for_size(100, 10)
    assert all(style == "" for style, text in fragments if text.strip())
```

- [ ] **Step 2: Run chat-window tests to verify failure**

Run:

```bash
pytest tests/cli/test_chat_window.py -k "body_fragments" -q
```

Expected: FAIL because `_render_body_fragments_for_size` does not exist.

- [ ] **Step 3: Add fragment helpers to `chat_window.py`**

Import the new helper and add plain-to-fragment utilities:

```python
from myagent.cli.syntax_highlight import Fragment, StyledLine, highlight_transcript_text
```

Add methods:

```python
def _syntax_highlight_enabled(self) -> bool:
    ui_config = getattr(self.config, "ui", None)
    return bool(getattr(ui_config, "syntax_highlight", True))


def _plain_styled_line(self, text: str) -> StyledLine:
    return StyledLine(text, [("", text)])


def _fragments_plain(self, fragments: list[Fragment]) -> str:
    return "".join(text for _style, text in fragments)
```

- [ ] **Step 4: Add styled body rendering path**

Keep `_body_text()` unchanged as the plain fallback. Add:

```python
def _body_fragments(self) -> list[Fragment]:
    columns, rows = self._current_terminal_size()
    input_text = self._current_input_text()
    input_height = self._sync_input_height(input_text)
    return self._render_body_fragments_for_size(
        terminal_columns=columns,
        terminal_rows=max(1, rows - input_height),
    )


def _render_body_fragments_for_size(
    self,
    terminal_columns: int,
    terminal_rows: int,
) -> list[Fragment]:
    lines = self._render_styled_body_lines_for_size(
        terminal_columns,
        terminal_rows,
    )
    return self._styled_lines_to_fragments(lines)
```

Add the helper signatures used by `_render_body_fragments_for_size()`:

```python
def _render_styled_body_lines_for_size(
    self,
    terminal_columns: int,
    terminal_rows: int,
) -> list[StyledLine]:
    ...


def _styled_lines_to_fragments(self, lines: list[StyledLine]) -> list[Fragment]:
    fragments: list[Fragment] = []
    for index, line in enumerate(lines):
        if index:
            fragments.append(("", "\n"))
        fragments.extend(line.fragments)
    return fragments
```

- [ ] **Step 5: Add styled transcript lines**

Add a styled analogue for transcript entries:

```python
def _transcript_entry_styled_lines(
    self,
    entry: TranscriptEntry,
    width: int,
) -> list[StyledLine]:
    plain_text = (
        _format_agent_text_for_display(entry.plain_text)
        if entry.role == "assistant"
        else self._transcript_display_text(entry)
    )
    highlighted_lines = highlight_transcript_text(
        plain_text,
        enabled=self._syntax_highlight_enabled() and entry.role in {"assistant", "tool"},
    )
    lines: list[StyledLine] = []
    for line_index, styled_line in enumerate(highlighted_lines):
        prefix = (
            self._role_prefix(ROLE_LABELS.get(entry.role, entry.role.title()))
            if line_index == 0
            else self._continuation_prefix()
        )
        lines.extend(self._wrap_prefixed_styled_line(prefix, styled_line, width))
    return lines
```

Use default fragments for user, system, error, queue, prompt, and state text in the first implementation.

- [ ] **Step 6: Wrap, pad, clip, and frame styled lines by terminal cell width**

Implement `_wrap_prefixed_styled_line()` with the same plain behavior as `_wrap_prefixed_text()`:

```python
def _wrap_prefixed_styled_line(
    self,
    prefix: str,
    styled_line: StyledLine,
    width: int,
) -> list[StyledLine]:
    prefix_width = get_cwidth(prefix)
    if width <= prefix_width:
        clipped = _clip_cells(prefix.rstrip(), width)
        return [StyledLine(clipped, [("", clipped)])]

    content_width = width - prefix_width
    continuation_indent = (
        2 if styled_line.plain.startswith("- ") and content_width > 2 else 0
    )
    wrap_width = max(1, content_width - continuation_indent)
    wrapped_content = self._wrap_styled_fragments(styled_line.fragments, wrap_width)

    lines = [
        self._clip_styled_line(
            StyledLine(
                f"{prefix}{wrapped_content[0].plain}",
                [("", prefix), *wrapped_content[0].fragments],
            ),
            width,
        )
    ]
    continuation = self._continuation_prefix()
    for segment in wrapped_content[1:]:
        indent = " " * continuation_indent
        lines.append(
            self._clip_styled_line(
                StyledLine(
                    f"{continuation}{indent}{segment.plain}",
                    [("", continuation), ("", indent), *segment.fragments],
                ),
                width,
            )
        )
    return lines
```

Add focused helpers for reused line operations:

```python
def _styled_from_plain(self, text: str) -> StyledLine:
    return StyledLine(text, [("", text)])


def _pad_styled_line(self, line: StyledLine, width: int) -> StyledLine:
    clipped = self._clip_styled_line(line, width)
    padding = " " * max(0, width - get_cwidth(clipped.plain))
    return StyledLine(
        f"{clipped.plain}{padding}",
        [*clipped.fragments, ("", padding)] if padding else clipped.fragments,
    )


def _clip_styled_line(self, line: StyledLine, width: int) -> StyledLine:
    cells = 0
    fragments: list[Fragment] = []
    plain_parts: list[str] = []
    for style, text in line.fragments:
        kept: list[str] = []
        for character in text:
            character_width = get_cwidth(character)
            if cells + character_width > width:
                kept_text = "".join(kept)
                if kept_text:
                    fragments.append((style, kept_text))
                    plain_parts.append(kept_text)
                return StyledLine("".join(plain_parts), fragments)
            kept.append(character)
            cells += character_width
        kept_text = "".join(kept)
        if kept_text:
            fragments.append((style, kept_text))
            plain_parts.append(kept_text)
    return StyledLine("".join(plain_parts), fragments or [("", "")])
```

Implement `_wrap_styled_fragments()` by iterating characters across fragments and starting a new `StyledLine` when adding the next character would exceed `wrap_width`. Preserve the original fragment style when a token crosses a wrap boundary by splitting the fragment into the two resulting lines.

Implement `_render_styled_body_lines_for_size()` by mirroring `_render_body_for_size()` with styled lines:

- call `_status_text()` as today and convert status lines with `_styled_from_plain()`;
- call `_conversation_styled_lines()` instead of `_conversation_lines()`;
- add borders with default-style fragments;
- use `_pad_styled_line()` on every final line;
- verify in tests that `fragments_plain(_render_body_fragments_for_size(...)) == _render_body_for_size(...)`.

- [ ] **Step 7: Wire fragments into `FormattedTextControl`**

Change layout construction from:

```python
self._body_control = _ChatBodyControl(self._body_text, self._scroll_lines)
```

to:

```python
self._body_control = _ChatBodyControl(self._body_fragments, self._scroll_lines)
```

`FormattedTextControl` accepts a callable returning formatted fragments, and existing mouse handling remains in `_ChatBodyControl`.

- [ ] **Step 8: Verify focused chat-window tests**

Run:

```bash
pytest tests/cli/test_chat_window.py -k "body_fragments or markdown or code_fence or tool" -q
```

Expected: PASS.

- [ ] **Step 9: Verify full chat-window tests**

Run:

```bash
pytest tests/cli/test_chat_window.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add myagent/cli/chat_window.py tests/cli/test_chat_window.py
git commit -m "feat: highlight code blocks in chat tui"
```

---

### Task 3: Documentation and Final Verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: implemented `ui.syntax_highlight` behavior from Tasks 1 and 2.
- Produces: user-facing README note that TUI code blocks are highlighted and C/C++/Rust are supported.

- [ ] **Step 1: Update README**

Add one bullet under the interaction window section:

```markdown
- Fenced code blocks in Agent and expanded tool output receive display-only syntax highlighting when `ui.syntax_highlight` is enabled. Supported language families include Python, JavaScript/TypeScript, SQL, JSON/YAML, Shell/PowerShell, HTML/CSS/XML, C/C++, and Rust.
```

Add one config line in the YAML example if it is not already present:

```yaml
ui:
  syntax_highlight: true
```

- [ ] **Step 2: Run full verification**

Run:

```bash
pytest tests/ -q
ruff check myagent tests
git diff --check
```

Expected:

- pytest exits 0 with all tests passing;
- ruff exits 0;
- `git diff --check` exits 0.

- [ ] **Step 3: Commit docs and any final adjustments**

Run:

```bash
git add README.md
git commit -m "docs: document tui syntax highlighting"
```

If final verification required code fixes, include those touched files in this commit and use:

```bash
git add README.md myagent/cli tests/cli
git commit -m "fix: finish tui syntax highlighting integration"
```

---

## Self-Review

- Spec coverage: Tasks 1 and 2 cover display-only highlighting, supported languages, unknown fallback, disabled mode, prompt_toolkit fragments, wrapping, and existing transcript preservation. Task 3 covers README documentation.
- Placeholder scan: no TBD/TODO placeholders are left in this plan.
- Type consistency: `StyledLine`, `Fragment`, and `highlight_transcript_text()` are introduced in Task 1 and consumed by Task 2 with matching names.
