# Chat Window UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make interactive `myagent` start in a fixed chat-like terminal window with scrollable conversation history, a persistent bottom input box, and the existing Agent Inspector Pane on the right or in rail mode.

**Architecture:** Add a prompt_toolkit full-screen chat-window path for interactive sessions while keeping the current REPL as fallback. Runtime events continue to flow through `REPLEngine.process_input()`, but visible conversation output goes to a transcript buffer owned by the chat window instead of transient Rich `Live`.

**Tech Stack:** Python 3.12+, prompt_toolkit full-screen `Application`, Rich renderables captured for transcript display, dataclasses, pytest, ruff.

---

## Scope Check

This plan covers one subsystem: the interactive CLI/TUI shell. It does not change the ReAct loop, model provider, tool execution, sub-agent scheduling, permissions, session persistence formats, memory behavior, MCP protocol, or one-shot CLI commands.

Repository convention from `AGENTS.md`: implementation plans must not include code blocks. This plan therefore lists exact files, interface signatures, test cases, commands, dependencies, and commit points; implementation code is written only during execution.

## File Structure

- Modify `myagent/config/schema.py`: add `ChatWindowConfig` and expose it as `UIConfig.chat_window`.
- Modify `myagent/config/loader.py`: validate `ui.chat_window` values and keep one-shot command behavior outside full-screen UI.
- Modify `tests/config/test_schema.py`: cover chat window defaults.
- Modify `tests/config/test_loader.py`: cover chat window override loading and validation warnings.
- Create `myagent/cli/transcript.py`: own display-only transcript entries, streaming merge behavior, scrollback trimming, viewport position, auto-follow, unread marker state, and clear-view behavior.
- Create `tests/cli/test_transcript.py`: focused transcript buffer and viewport tests.
- Create `myagent/cli/rich_capture.py`: convert Rich renderables, strings, and lists of renderables into sanitized plain terminal text for prompt_toolkit display.
- Create `tests/cli/test_rich_capture.py`: conversion and sanitization tests.
- Create `myagent/cli/input_controller.py`: build chat-window key bindings and input behavior separate from `REPLEngine`.
- Create `tests/cli/test_input_controller.py`: key binding and input normalization tests.
- Create `myagent/cli/chat_window.py`: prompt_toolkit full-screen `ChatWindowController`, layout builder, status pane embedding, output sink methods, refresh lifecycle, and fallback error boundary.
- Create `tests/cli/test_chat_window.py`: layout, scroll, output sink, lifecycle, and fallback tests.
- Modify `myagent/cli/repl.py`: split the existing prompt loop into a legacy method, add chat-window startup path, route output into chat mode, skip transient Rich `Live` when chat mode is active, and preserve fallback behavior.
- Modify `tests/cli/test_repl_layout.py`: add chat-mode routing tests without weakening existing Inspector layout tests.
- Modify `myagent/cli/main.py`: pass chat-window config through the existing REPL wiring and keep `--help`, `--list-sessions`, and export paths non-full-screen.
- Create `tests/cli/test_main_chat_window.py`: startup wiring and one-shot command tests.
- Modify `myagent/cli/__init__.py`: export new chat UI classes that are part of the CLI integration surface.
- Modify `README.md`: document the default chat window, config keys, fallback flag, key bindings, and smoke commands.
- Modify `docs/superpowers/specs/2026-07-05-chat-window-ui-design.md`: change status from draft to implemented or update any behavior that changes during execution.

## Dependencies And Order

Tasks must be executed in order. Configuration comes first because startup routing depends on it. Transcript and Rich capture come before the prompt_toolkit controller. Input bindings are separated so they can be tested without launching a real terminal app. REPL routing comes after the controller exists. Startup wiring and documentation come last.

Commit after each task. Do not push.

---

### Task 1: Add Chat Window Configuration

**Files:**
- Modify: `myagent/config/schema.py`
- Modify: `myagent/config/loader.py`
- Modify: `tests/config/test_schema.py`
- Modify: `tests/config/test_loader.py`

**Interfaces:**
- `ChatWindowConfig`
- `UIConfig.chat_window: ChatWindowConfig`
- `ConfigLoader._validate(config: AppConfig) -> None` validates chat-window numeric fields and mode values

- [x] **Step 1: Write failing schema tests**

Add tests asserting `UIConfig().chat_window.enabled is True`, `input_position == "bottom"`, `scrollback_lines == 2000`, `input_min_lines == 1`, `input_max_lines == 6`, and `follow_output == "auto"`. Keep existing status pane default assertions unchanged.

- [x] **Step 2: Write failing loader tests**

Add loader tests for YAML overrides: `ui.chat_window.enabled: false`, `ui.chat_window.scrollback_lines: 5000`, `ui.chat_window.input_max_lines: 8`, and `ui.chat_window.follow_output: manual`. Add validation warning tests for `scrollback_lines < 100`, `input_min_lines < 1`, `input_max_lines < input_min_lines`, unsupported `input_position`, and unsupported `follow_output`.

- [x] **Step 3: Run focused failing tests**

Run: `pytest tests/config/test_schema.py::TestUIConfig tests/config/test_loader.py -v`

Expected: FAIL because `ChatWindowConfig` and validation do not exist yet.

- [x] **Step 4: Implement config schema and validation**

Add `ChatWindowConfig` to `myagent/config/schema.py` with `enabled`, `input_position`, `scrollback_lines`, `input_min_lines`, `input_max_lines`, and `follow_output`. `input_position` accepts `bottom`. `follow_output` accepts `auto`, `always`, and `manual`. Add `chat_window` to `UIConfig`. In `ConfigLoader._validate()`, log structured warnings with `category="system"` for invalid numeric values and unsupported mode strings. Do not mutate user config in validation.

- [x] **Step 5: Run focused passing tests**

Run: `pytest tests/config/test_schema.py::TestUIConfig tests/config/test_loader.py -v`

Expected: PASS.

- [x] **Step 6: Commit**

Run: `git add myagent/config/schema.py myagent/config/loader.py tests/config/test_schema.py tests/config/test_loader.py`

Run: `git commit -m "feat: add chat window config"`

---

### Task 2: Create Transcript Buffer And Viewport

**Files:**
- Create: `myagent/cli/transcript.py`
- Create: `tests/cli/test_transcript.py`

**Interfaces:**
- `TranscriptEntry(entry_id: int, role: str, content: object, plain_text: str, is_streaming: bool = False)`
- `TranscriptBuffer(max_lines: int = 2000, follow_output: str = "auto")`
- `TranscriptBuffer.append(role: str, content: object, plain_text: str | None = None, end: str = "\n", streaming: bool = False) -> int`
- `TranscriptBuffer.append_user(text: str) -> int`
- `TranscriptBuffer.append_assistant(content: object, plain_text: str | None = None, end: str = "\n") -> int`
- `TranscriptBuffer.append_tool(content: object, plain_text: str | None = None) -> int`
- `TranscriptBuffer.append_error(text: str) -> int`
- `TranscriptBuffer.append_system(text: str) -> int`
- `TranscriptBuffer.replace_entries(entries: list[TranscriptEntry]) -> None`
- `TranscriptBuffer.clear_view() -> None`
- `TranscriptBuffer.scroll_lines(delta: int, viewport_height: int) -> None`
- `TranscriptBuffer.page(delta: int, viewport_height: int) -> None`
- `TranscriptBuffer.visible_entries(viewport_height: int) -> list[TranscriptEntry]`
- `TranscriptBuffer.at_bottom(viewport_height: int) -> bool`
- `TranscriptBuffer.unread_count: int`

- [x] **Step 1: Write failing append and streaming tests**

Add tests that user messages create `role == "user"` entries, assistant text chunks with `end=""` merge into the active streaming entry, a later newline closes that streaming entry, tool/error/system roles stay separate, and entry IDs are monotonically increasing.

- [x] **Step 2: Write failing viewport tests**

Add tests for scrollback trimming to the most recent `max_lines`, `scroll_lines()` moving up and down within bounds, `page()` moving by `viewport_height`, auto-follow staying at bottom when already at bottom, no auto-yank when the user has scrolled up, unread count incrementing while scrolled away, unread count clearing after returning to bottom, and `clear_view()` removing display entries without touching the next entry ID.

- [x] **Step 3: Run focused failing tests**

Run: `pytest tests/cli/test_transcript.py -v`

Expected: FAIL because `myagent.cli.transcript` does not exist yet.

- [x] **Step 4: Implement transcript buffer**

Create dataclasses and buffer methods in `myagent/cli/transcript.py`. Keep it framework-neutral: no Rich imports and no prompt_toolkit imports. Treat entries as display-only state; do not write session transcript files here. Store sanitized plain text alongside original content so tests and prompt_toolkit rendering do not depend on Rich internals.

- [x] **Step 5: Run focused passing tests**

Run: `pytest tests/cli/test_transcript.py -v`

Expected: PASS.

- [x] **Step 6: Commit**

Run: `git add myagent/cli/transcript.py tests/cli/test_transcript.py`

Run: `git commit -m "feat: add chat transcript buffer"`

---

### Task 3: Add Rich Renderable Capture For Chat Display

**Files:**
- Create: `myagent/cli/rich_capture.py`
- Create: `tests/cli/test_rich_capture.py`

**Interfaces:**
- `capture_renderable(renderable: object, width: int = 100) -> str`
- `capture_many(renderables: list[object], width: int = 100) -> str`
- `sanitize_terminal_text(text: object) -> str`

- [x] **Step 1: Write failing capture tests**

Add tests proving strings, Rich `Text`, Rich `Panel`, and lists of mixed Rich renderables become readable plain text. Assert captured panel output contains the panel title and body and never contains Python object representations such as `<rich.panel.Panel object`.

- [x] **Step 2: Write failing sanitization tests**

Add tests proving ANSI escape sequences and unsafe control characters are removed, tabs and newlines are preserved, long content remains readable, and non-string objects are converted without raising.

- [x] **Step 3: Run focused failing tests**

Run: `pytest tests/cli/test_rich_capture.py -v`

Expected: FAIL because `myagent.cli.rich_capture` does not exist yet.

- [x] **Step 4: Implement capture helpers**

Use a record-capable Rich `Console` inside `capture_renderable()` and export plain text with styles disabled. Keep the helper stateless. `capture_many()` should capture each renderable in order and join them with one newline between renderables. `sanitize_terminal_text()` should share the same ANSI and unsafe-control filtering semantics as `AgentLayoutController`.

- [x] **Step 5: Run focused passing tests**

Run: `pytest tests/cli/test_rich_capture.py -v`

Expected: PASS.

- [x] **Step 6: Commit**

Run: `git add myagent/cli/rich_capture.py tests/cli/test_rich_capture.py`

Run: `git commit -m "feat: capture rich output for chat ui"`

---

### Task 4: Build Chat Window Input Controller

**Files:**
- Create: `myagent/cli/input_controller.py`
- Create: `tests/cli/test_input_controller.py`

**Interfaces:**
- `ChatInputActions(submit, insert_newline, interrupt, request_exit, toggle_inspector, scroll_lines, page)`
- `InputController(config, completer=None, lexer=None)`
- `InputController.build_key_bindings(actions: ChatInputActions) -> KeyBindings`
- `InputController.normalize_submit_text(text: str) -> str`
- `InputController.input_height_for_text(text: str) -> int`

- [x] **Step 1: Write failing submit and multiline tests**

Add tests for `normalize_submit_text()` trimming surrounding whitespace while preserving internal newlines. Add key-binding tests showing `Enter` submits, `escape` + `enter` inserts a newline, and empty submissions are ignored by the controller before calling the submit action.

- [x] **Step 2: Write failing control-key tests**

Add key-binding tests showing `F2` calls `toggle_inspector`, `Ctrl+C` calls `interrupt` when an agent run is active, `Ctrl+C` clears input or asks exit when idle, `Ctrl+D` requests exit only when the input is empty, `PageUp` and `PageDown` call `page`, mouse wheel bindings call `scroll_lines`, and `Home`/`End` are not bound by the chat window controller.

- [x] **Step 3: Write failing input height tests**

Add tests proving `input_height_for_text()` respects `input_min_lines` and `input_max_lines`, grows for multiline input, and caps very long input without hiding the transcript.

- [x] **Step 4: Run focused failing tests**

Run: `pytest tests/cli/test_input_controller.py -v`

Expected: FAIL because `myagent.cli.input_controller` does not exist yet.

- [x] **Step 5: Implement input controller**

Create a prompt_toolkit key-binding builder that delegates all side effects to `ChatInputActions`. Do not import `REPLEngine`. Use `escape` + `enter` as the reliable terminal representation for Alt+Enter; add Shift+Enter only if prompt_toolkit exposes a stable key name in the local dependency.

- [x] **Step 6: Run focused passing tests**

Run: `pytest tests/cli/test_input_controller.py -v`

Expected: PASS.

- [x] **Step 7: Commit**

Run: `git add myagent/cli/input_controller.py tests/cli/test_input_controller.py`

Run: `git commit -m "feat: add chat input controller"`

---

### Task 5: Create Full-Screen Chat Window Controller

**Files:**
- Create: `myagent/cli/chat_window.py`
- Create: `tests/cli/test_chat_window.py`
- Modify: `myagent/cli/__init__.py`

**Interfaces:**
- `ChatWindowController(config, transcript: TranscriptBuffer, status_pane=None, status_model=None, completer=None, lexer=None)`
- `async ChatWindowController.run(on_submit, on_exit=None, on_interrupt=None) -> None`
- `ChatWindowController.append_user_input(text: str) -> None`
- `ChatWindowController.append_output(content: object, end: str = "\n") -> None`
- `ChatWindowController.append_system(text: str) -> None`
- `ChatWindowController.append_error(text: str) -> None`
- `ChatWindowController.refresh() -> None`
- `ChatWindowController.request_stop() -> None`
- `ChatWindowController.set_agent_running(running: bool) -> None`
- `async ChatWindowController.ask(prompt: str, timeout: float) -> str | None`
- `ChatWindowController.is_running: bool`

- [x] **Step 1: Write failing layout tests**

Add tests with synthetic terminal widths proving wide layout includes conversation, bottom input, and full `Agent Inspector`; narrow layout includes conversation, bottom input, and rail markers; the input area remains last in the vertical layout; and the status region never covers the input.

- [x] **Step 2: Write failing output sink tests**

Add tests proving `append_user_input()`, `append_output()` for strings, `append_output()` for Rich panels, `append_system()`, and `append_error()` all update `TranscriptBuffer` and call `refresh()` exactly once per append. Assert status-only updates are not appended here because they are handled by `REPLEngine`.

- [x] **Step 3: Write failing scroll and refresh tests**

Add tests proving PageUp/PageDown and wheel actions move the transcript viewport, new output follows bottom only when the buffer is near bottom, new output while scrolled up increments unread state, and returning to bottom clears unread state.

- [x] **Step 4: Write failing lifecycle and fallback tests**

Add tests proving `run()` starts a prompt_toolkit full-screen application, `request_stop()` exits it, `set_agent_running()` changes Ctrl+C behavior through `InputController`, `ask()` collects one response through the same bottom input, and startup/render exceptions are logged with `category="error"`, `component="agent"`, and context `cli_chat_window_start` or `cli_chat_window_render`.

- [x] **Step 5: Run focused failing tests**

Run: `pytest tests/cli/test_chat_window.py -v`

Expected: FAIL because `myagent.cli.chat_window` does not exist yet.

- [x] **Step 6: Implement chat window controller**

Build the prompt_toolkit full-screen application in `myagent/cli/chat_window.py`. Use `TranscriptBuffer` for all conversation display state, `InputController` for key bindings, and `AgentInspectorPane.get_renderable()` plus `capture_renderable()` for status text. Keep Rich `Live` out of this controller. Log and re-raise startup failures so `REPLEngine` can choose the legacy fallback.

- [x] **Step 7: Export chat UI classes**

Update `myagent/cli/__init__.py` to export `ChatWindowController`, `TranscriptBuffer`, and `InputController`.

- [x] **Step 8: Run focused passing tests**

Run: `pytest tests/cli/test_chat_window.py tests/cli/test_transcript.py tests/cli/test_input_controller.py tests/cli/test_rich_capture.py -v`

Expected: PASS.

- [x] **Step 9: Commit**

Run: `git add myagent/cli/chat_window.py myagent/cli/transcript.py myagent/cli/input_controller.py myagent/cli/rich_capture.py myagent/cli/__init__.py tests/cli/test_chat_window.py tests/cli/test_transcript.py tests/cli/test_input_controller.py tests/cli/test_rich_capture.py`

Run: `git commit -m "feat: add full screen chat window"`

---

### Task 6: Route REPL Output Through Chat Mode

**Files:**
- Modify: `myagent/cli/repl.py`
- Modify: `tests/cli/test_repl_layout.py`

**Interfaces:**
- `REPLEngine(..., chat_window_factory=None, chat_window_controller=None)`
- `REPLEngine._should_use_chat_window() -> bool`
- `async REPLEngine._run_chat_window_loop() -> None`
- `async REPLEngine._run_prompt_session_loop() -> None`
- `REPLEngine._chat_window_active() -> bool`
- `REPLEngine._append_chat_output(text: object, end: str = "\n") -> bool`

- [x] **Step 1: Write failing chat output tests**

Add tests proving when a chat window controller is active, `_output_to_console()` appends to chat output and does not call `AgentLayoutController.append_output()`, `render_once()`, or console print. Add a Rich panel case proving the panel is captured as readable transcript text.

- [x] **Step 2: Write failing stream routing tests**

Add async tests for `process_input()` proving text chunks append into one streaming assistant message, tool panels append as tool entries, errors append as error entries, slash command results append as system or assistant output, `StatusUpdate` changes `RuntimeStatusModel` but does not append transcript output, and `_start_layout_for_engine_stream()` returns false while chat mode is active.

- [x] **Step 3: Write failing prompt-loop split tests**

Add tests proving `run()` calls `_run_chat_window_loop()` when `config.ui.chat_window.enabled is True`, calls `_run_prompt_session_loop()` when false, and falls back to `_run_prompt_session_loop()` after a chat startup exception while logging context `cli_chat_window_start`.

- [x] **Step 4: Write failing ask-user tests**

Add tests proving `_prompt_with_timeout()` awaits `ChatWindowController.ask()` while chat mode is active and keeps the existing prompt_toolkit/simple-input fallback while chat mode is inactive.

- [x] **Step 5: Run focused failing tests**

Run: `pytest tests/cli/test_repl_layout.py -v`

Expected: FAIL because REPL has no chat-window path yet.

- [x] **Step 6: Implement REPL chat routing**

Refactor `REPLEngine.run()` so session startup, logging context, dream checker startup, status sync, and shutdown remain shared. Move the existing `PromptSession` loop into `_run_prompt_session_loop()`. Add `_run_chat_window_loop()` that constructs or uses a `ChatWindowController`, appends the greeting/project lines to the transcript, and passes submitted text into `process_input()`. In chat mode, `_output_to_console()` must append to the chat window and `_should_start_layout_for_engine_stream()` must return false.

- [x] **Step 7: Run focused passing tests**

Run: `pytest tests/cli/test_repl_layout.py -v`

Expected: PASS.

- [x] **Step 8: Commit**

Run: `git add myagent/cli/repl.py tests/cli/test_repl_layout.py`

Run: `git commit -m "feat: route repl through chat window"`

---

### Task 7: Wire Interactive Startup And One-Shot Compatibility

**Files:**
- Modify: `myagent/cli/main.py`
- Create: `tests/cli/test_main_chat_window.py`
- Modify: `tests/cli/test_main_status.py`
- Modify: `tests/cli/test_main.py`

**Interfaces:**
- `main._build_chat_window_factory(config, status_pane, status_model) -> callable`
- `main._is_one_shot_command(args) -> bool`
- Existing `async_main(argv: list[str] | None = None) -> int` keeps one-shot commands outside full-screen UI

- [x] **Step 1: Write failing startup wiring tests**

Add tests proving normal interactive startup passes a chat window factory into `REPLEngine`, resumed sessions also pass the factory, disabled `ui.chat_window.enabled` passes no factory or passes a disabled factory that REPL ignores, and the factory receives the same `AgentInspectorPane` and `RuntimeStatusModel` created by `_build_status_components()`.

- [x] **Step 2: Write failing one-shot tests**

Add tests proving `myagent --list-sessions`, `myagent --help`, and `myagent --session <id> --export markdown` do not instantiate `ChatWindowController` and do not start a prompt_toolkit full-screen application.

- [x] **Step 3: Write failing fallback wiring test**

Add a test proving a chat window startup exception in interactive mode returns to the existing prompt-style REPL path and does not change the session start/end behavior.

- [x] **Step 4: Run focused failing tests**

Run: `pytest tests/cli/test_main_chat_window.py tests/cli/test_main_status.py tests/cli/test_main.py -v`

Expected: FAIL because main wiring helpers do not exist yet.

- [x] **Step 5: Implement startup wiring**

Create a small factory in `main.py` that builds `TranscriptBuffer` with `config.ui.chat_window.scrollback_lines` and `follow_output`, then builds `ChatWindowController` with config, transcript, status pane, status model, completer inputs supplied by REPL, and no Rich `Live`. Keep one-shot command handling before REPL construction. Keep resume and fresh session paths symmetrical.

- [x] **Step 6: Run focused passing tests**

Run: `pytest tests/cli/test_main_chat_window.py tests/cli/test_main_status.py tests/cli/test_main.py -v`

Expected: PASS.

- [x] **Step 7: Commit**

Run: `git add myagent/cli/main.py tests/cli/test_main_chat_window.py tests/cli/test_main_status.py tests/cli/test_main.py`

Run: `git commit -m "feat: start interactive chat window"`

---

### Task 8: Preserve Legacy REPL And Inspector Regression Behavior

**Files:**
- Modify: `tests/cli/test_layout.py`
- Modify: `tests/cli/test_status.py`
- Modify: `tests/cli/test_repl_layout.py`
- Modify: `myagent/cli/layout.py` only if a regression test exposes a real issue
- Modify: `myagent/cli/status.py` only if a regression test exposes a real issue

**Interfaces:**
- Existing `AgentLayoutController` public methods remain unchanged.
- Existing `AgentInspectorPane` public methods remain unchanged.
- `StatusBar = AgentInspectorPane` compatibility alias remains unchanged.

- [x] **Step 1: Add no-second-Live regression test**

Add a test proving chat-window mode never constructs `rich.live.Live` during `process_input()`. Keep the existing tests proving legacy REPL still constructs transient `AgentLayoutController` during streaming when chat-window mode is disabled.

- [x] **Step 2: Add Inspector compatibility tests**

Add tests proving `F2` still toggles the Inspector in legacy mode, wide/narrow rendering behavior remains unchanged, rail width still respects marker length, and disabled status pane still allows output rendering.

- [x] **Step 3: Run focused regression tests**

Run: `pytest tests/cli/test_layout.py tests/cli/test_status.py tests/cli/test_repl_layout.py -v`

Expected: PASS after Tasks 1 through 7. If any test fails, fix only the regression exposed by the failing assertion.

- [x] **Step 4: Commit**

Run: `git add tests/cli/test_layout.py tests/cli/test_status.py tests/cli/test_repl_layout.py myagent/cli/layout.py myagent/cli/status.py`

Run: `git commit -m "test: guard chat window ui regressions"`

---

### Task 9: Update User-Facing Documentation And Verify

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-05-chat-window-ui-design.md`
- Modify: `docs/superpowers/plans/2026-07-05-chat-window-ui.md`

**Documentation Changes:**
- README describes default interactive chat window behavior.
- README lists `ui.chat_window.enabled`, `scrollback_lines`, `input_min_lines`, `input_max_lines`, and `follow_output`.
- README lists key bindings: `Enter`, `Esc+Enter` or `Alt+Enter`, `F2`, `Ctrl+C`, `Ctrl+D`, `PageUp`, `PageDown`, and mouse wheel.
- README documents `ui.chat_window.enabled: false` as the legacy prompt-style fallback.
- Design spec status changes from draft to implemented after verification passes.
- This plan's checkboxes are updated to match completed work.

- [x] **Step 1: Update README**

Add a concise section for the chat-window UI near existing CLI usage/configuration docs. Include the fallback flag and mention that one-shot commands stay non-full-screen.

- [x] **Step 2: Update design spec status**

Change `docs/superpowers/specs/2026-07-05-chat-window-ui-design.md` status from draft to implemented. If execution chose a different key binding or config value, record the final behavior exactly.

- [x] **Step 3: Update plan checkboxes**

Mark completed tasks in this plan so future reviewers can see what shipped.

- [ ] **Step 4: Run full verification**

Run: `ruff check myagent/`

Expected: PASS.

Run: `pytest tests/ -v`

Expected: PASS.

Run: `git diff --check`

Expected: PASS with no output.

Run: `myagent --help`

Expected: prints help and does not start the full-screen chat UI.

Run: `myagent --list-sessions`

Expected: prints sessions or an empty list and does not start the full-screen chat UI.

- [ ] **Step 5: Manual smoke**

Run `myagent` in a normal terminal. Verify startup shows a fixed chat window, input stays at the bottom while long output streams, mouse wheel and PageUp/PageDown scroll conversation history, new output does not yank the viewport when scrolled up, `F2` toggles the Inspector, narrow width uses rail mode, `Ctrl+C` interrupts an active run, `Ctrl+D` exits when input is empty, and `ui.chat_window.enabled: false` restores the old prompt-style REPL.

- [x] **Step 6: Commit**

Run: `git add README.md docs/superpowers/specs/2026-07-05-chat-window-ui-design.md docs/superpowers/plans/2026-07-05-chat-window-ui.md`

Run: `git commit -m "docs: document chat window ui"`

---

## Self-Review Checklist

- [x] Config defaults and overrides map to Task 1.
- [x] Fixed conversation viewport, auto-follow, scrollback trimming, unread state, and clear-view behavior map to Task 2.
- [x] Rich renderables from existing renderer are captured without object repr output in Task 3.
- [x] Persistent bottom input and key bindings map to Task 4.
- [x] Full-screen prompt_toolkit owner, wide Inspector, and narrow rail map to Task 5.
- [x] Streaming output, slash command output, `StatusUpdate` routing, AskUserQuestion prompt behavior, and no transient Rich `Live` in chat mode map to Task 6.
- [x] Default interactive startup, resume, one-shot compatibility, and fallback map to Task 7.
- [x] Legacy REPL and Inspector compatibility map to Task 8.
- [ ] README and spec status are complete; full test suite, `--help`, `--list-sessions`, and manual smoke remain for final verification.
