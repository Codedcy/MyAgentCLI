# MyAgentCLI TUI Syntax Highlighting Design

> Date: 2026-07-07 | Status: Proposed

## Goal

Add display-only syntax highlighting to the full-screen chat TUI so code and structured snippets are easier to read. The first implementation uses the approved option A: highlight fenced code blocks and clearly identified code snippets, while keeping ordinary prose untouched.

This feature must not change persisted transcripts, exported sessions, memory content, LLM prompts, tool results, or ReAct behavior. It only changes how visible transcript text is colored inside the prompt_toolkit chat window.

## Current State

`Renderer` can already create Rich `Syntax` renderables for fenced code blocks, but chat-window mode captures renderables as plain text through `capture_renderable(..., styles=False)`. `ChatWindowController._body_text()` then returns one plain string to `FormattedTextControl`. That path preserves wrapping and scrolling, but it strips color.

The right fix is to add styling at the prompt_toolkit display layer after transcript text has been sanitized, formatted, wrapped, and clipped.

## Scope

Highlight these cases in the conversation transcript:

- fenced Markdown code blocks such as ` ```python `, ` ```sql `, ` ```rust `, and ` ```cpp `;
- expanded tool details when the retained detail text contains fenced code blocks;
- obvious whole-line code snippets only when they are in a fenced block or tagged by a known language marker.

Do not color arbitrary prose by keyword scanning in the first phase. For example, a normal sentence containing the word `select` should stay plain unless it is inside a SQL code block.

## Supported Languages

Use Pygments lexer aliases through prompt_toolkit or a thin adapter. The first supported alias set is:

- Python: `python`, `py`
- JavaScript and TypeScript: `javascript`, `js`, `typescript`, `ts`
- SQL: `sql`
- JSON and YAML: `json`, `yaml`, `yml`
- Shells: `bash`, `sh`, `shell`, `powershell`, `ps1`
- Web: `html`, `css`, `xml`
- C and C++: `c`, `cpp`, `c++`, `cc`, `cxx`, `h`, `hpp`
- Rust: `rust`, `rs`
- Markdown and text fallback: `markdown`, `md`, `text`

Unknown or unavailable languages fall back to plain text, preserving the original content.

## Architecture

Add a small display helper, tentatively `myagent/cli/syntax_highlight.py`, with a framework-neutral API:

- `split_fenced_code_blocks(text: str) -> list[Segment]`
- `highlight_code_line(text: str, language: str) -> list[tuple[str, str]]`
- `highlight_transcript_text(text: str, role: str, enabled: bool) -> list[StyledLine]`

The helper returns prompt_toolkit-compatible style fragments while also exposing plain text for tests and wrapping. `StyledLine` stores:

- `plain`: the visible line text;
- `fragments`: ordered `(style, text)` fragments whose concatenation equals `plain`.

`ChatWindowController` keeps the existing plain rendering helpers for tests and fallback, then adds a styled body path:

1. Build transcript entries exactly as today.
2. Apply the existing assistant Markdown-ish cleanup.
3. Normalize compact code fences whose language name is immediately followed by
   code text instead of a newline.
4. Split code fences into prose and code segments.
5. Wrap and clip by terminal cell width while preserving fragment boundaries.
6. Return fragments from `_body_fragments()` to `FormattedTextControl`.
7. Keep `_body_text()` as a plain-text fallback and for tests that assert layout content.

The borders, role prefixes, queue area, transient permission tray, thinking line, and input area remain structurally unchanged.

## Styling

Use a conservative dark-terminal palette compatible with prompt_toolkit style strings:

- keywords: bold cyan
- builtins and types: cyan
- strings: green
- numbers: magenta
- comments: italic bright black
- operators and punctuation: default
- function and class names: yellow
- SQL keywords: bold cyan
- invalid lexer output or highlighting failure: default

The palette should not make the UI one-note and should avoid low-contrast colors on black terminals.

## Configuration

Reuse the existing `ui.syntax_highlight` boolean as the feature switch:

```yaml
ui:
  syntax_highlight: true
```

Do not add a theme setting in the first implementation. Keep the conservative palette hardcoded until the fragment rendering path is stable. A future patch can add named themes if users need customization.

## Error Handling

Syntax highlighting must fail soft:

- if Pygments or a lexer fails, render the same text without color;
- log unexpected highlighting errors with `logging.getLogger("myagent.cli.syntax_highlight")`;
- never break transcript rendering, scrolling, input handling, or status-pane rendering because of highlighting.

## Compatibility

The feature must preserve:

- current plain-text transcript entries;
- session JSON and Markdown export formats;
- line wrapping inside pane boundaries;
- mouse wheel and PageUp/PageDown scrolling;
- queue rendering;
- folded tool details and F3 expansion;
- transient permission prompt behavior;
- ability to disable mouse support for terminal-native selection.

Copying from terminals that preserve ANSI/style information is terminal-dependent, but the underlying prompt_toolkit fragment text must remain plain readable text.

## Testing

Required tests:

- fenced Python block colors `def`, strings, comments, and numbers while plain text is unchanged;
- fenced SQL block colors `SELECT`, `FROM`, `WHERE`, and comments;
- fenced C, C++, and Rust blocks resolve to supported lexers;
- unknown language falls back to unstyled text;
- disabling `ui.syntax_highlight` returns unstyled fragments;
- fragment concatenation equals existing `_body_text()` plain output;
- wrapping and clipping keep pane boundaries intact for styled wide CJK/ASCII lines;
- existing chat window tests continue to pass without style-aware assertions.

Manual smoke:

- run `myagent`, ask for Python and SQL snippets, and verify colored keywords/comments;
- test ` ```cpp ` and ` ```rust ` snippets;
- resize terminal narrow and verify rail/status layout still works;
- use F3 on a tool result containing a code block and verify details remain scrollable.

## Open Decisions

No product decision is blocked. The approved first phase is option A: code-block-first highlighting. Broad keyword scanning in ordinary prose is intentionally out of scope for this phase.
