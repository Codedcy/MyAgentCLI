# CLI Runtime UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MyAgentCLI display permissions, tool execution, decoded text, and thinking state in a compact Claude Code style chat window.

**Architecture:** Keep persisted conversation semantics unchanged while improving the display-only transcript and runtime status model. The permission controller keeps the injected confirmation handler, but the chat window renders that handler as a transient tray instead of a transcript message. Tool output is represented as folded display entries with details available through a single current/recent expansion toggle.

**Tech Stack:** Python 3.12+, prompt_toolkit, Rich, pytest, ruff.

## Global Constraints

- Use TDD for behavior changes and watch new tests fail before implementation.
- Keep changes local to `myagent/cli`, `myagent/agent/runtime_status.py`, decoding helpers, and focused tool decode call sites.
- Do not persist transient permission tray or thinking timer text into session transcript files.
- Commit after each completed document or implementation batch.

---

### Task 1: Shared Text Decoding

**Files:**
- Create: `myagent/cli/text_decode.py`
- Modify: `myagent/tools/builtin/exec_tools.py`
- Modify: `myagent/tools/builtin/search_tools.py`
- Modify: `myagent/tools/mcp/client.py`
- Test: `tests/cli/test_text_decode.py`

**Interfaces:**
- Produces: `decode_tool_output(data: bytes | str | None) -> str`
- Produces: `sanitize_display_text(text: object) -> str`
- Consumes: existing ANSI and output-control semantics from `myagent.cli.rich_capture`.

- [ ] **Step 1: Write failing tests**

Cover UTF-8, GB18030 Chinese bytes, already-decoded strings, and control stripping.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/test_text_decode.py -q`
Expected: FAIL because `myagent.cli.text_decode` does not exist.

- [ ] **Step 3: Implement the helper**

Create `decode_tool_output()` with UTF-8 first, preferred locale and console encodings next, GB18030 next, replacement fallback last.

- [ ] **Step 4: Use the helper in tool decode call sites**

Replace direct `stdout.decode("utf-8", errors="replace")` style display decoding in the focused tool paths.

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/cli/test_text_decode.py tests/tools/builtin/test_exec_tools.py tests/tools/builtin/test_search_tools.py tests/tools/mcp/test_client.py -q`

### Task 2: Permission Prompt Tray

**Files:**
- Modify: `myagent/cli/chat_window.py`
- Modify: `myagent/cli/repl.py`
- Test: `tests/cli/test_chat_window.py`
- Test: `tests/cli/test_repl_layout.py`

**Interfaces:**
- Produces: `ChatWindowController.ask(prompt, timeout, transient=False) -> str | None`
- Produces: transient prompt text rendered above the input line and cleared after response.

- [ ] **Step 1: Write failing tests**

Cover permission prompts not appearing in transcript entries, tray rendering above input, and tray clearing after submit.

- [ ] **Step 2: Run focused tests to verify failure**

Run: `pytest tests/cli/test_chat_window.py -k permission -q`

- [ ] **Step 3: Implement tray state**

Add display-only prompt state to `ChatWindowController.ask(..., transient=True)` and render it in the chat layout above the input area.

- [ ] **Step 4: Route permission confirms through transient ask**

Update `REPLEngine._confirm_permission_request()` to call `_prompt_with_timeout(..., transient=True)`.

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/cli/test_chat_window.py tests/cli/test_repl_layout.py -q`

### Task 3: Folded Tool Output and F3 Toggle

**Files:**
- Modify: `myagent/cli/transcript.py`
- Modify: `myagent/cli/chat_window.py`
- Modify: `myagent/cli/input_controller.py`
- Modify: `myagent/cli/repl.py`
- Test: `tests/cli/test_transcript.py`
- Test: `tests/cli/test_chat_window.py`
- Test: `tests/cli/test_input_controller.py`
- Test: `tests/cli/test_repl_layout.py`

**Interfaces:**
- Produces: folded transcript entries for role `tool`.
- Produces: F3 binding that toggles details for the current/recent tool.
- Consumes: `ToolCallStart` and `ToolCallEnd` events.

- [ ] **Step 1: Write failing tests**

Cover collapsed tool summaries, details hidden by default, F3 expansion of the current/recent tool, and failed tool summaries.

- [ ] **Step 2: Run focused tests to verify failure**

Run: `pytest tests/cli/test_transcript.py tests/cli/test_chat_window.py -k tool -q`

- [ ] **Step 3: Add folded entry metadata**

Extend `TranscriptEntry` with optional display metadata or a focused helper that stores `plain_text` as the folded summary and full detail separately.

- [ ] **Step 4: Update tool event rendering**

Make `REPLEngine` create/update a compact tool entry instead of appending separate start/end panels.

- [ ] **Step 5: Add F3 input binding**

Bind F3 in the chat input controller to toggle the current/recent tool expansion.

- [ ] **Step 6: Verify and commit**

Run: `pytest tests/cli/test_transcript.py tests/cli/test_chat_window.py tests/cli/test_input_controller.py tests/cli/test_repl_layout.py -q`

### Task 4: Thinking Timer

**Files:**
- Modify: `myagent/agent/runtime_status.py`
- Modify: `myagent/cli/status.py`
- Modify: `myagent/cli/chat_window.py`
- Modify: `myagent/cli/repl.py`
- Test: `tests/agent/test_runtime_status.py`
- Test: `tests/cli/test_status.py`
- Test: `tests/cli/test_chat_window.py`
- Test: `tests/cli/test_repl_layout.py`

**Interfaces:**
- Produces: thinking runtime status with active flag and elapsed seconds.
- Produces: compact chat status line `State | Thinking <elapsed>s`.

- [ ] **Step 1: Write failing tests**

Cover thinking status activation, elapsed rendering, status clear on text/tool/done/error, and no transcript pollution.

- [ ] **Step 2: Run focused tests to verify failure**

Run: `pytest tests/agent/test_runtime_status.py tests/cli/test_status.py -k thinking -q`

- [ ] **Step 3: Add runtime status fields**

Extend the runtime status model with a thinking state, and render it in the inspector.

- [ ] **Step 4: Wire REPL lifecycle**

Set thinking active at agent run start or first `ThinkingChunk`, clear it on text/tool/question/done/error, and refresh the chat window periodically while active.

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/agent/test_runtime_status.py tests/cli/test_status.py tests/cli/test_chat_window.py tests/cli/test_repl_layout.py -q`

### Task 5: Final Verification and Review

**Files:**
- No direct code ownership; review all files touched in Tasks 1-4.

- [ ] **Step 1: Run complete verification**

Run: `pytest tests/ -q`
Run: `ruff check myagent/cli myagent/agent tests/cli tests/agent`
Run: `git diff --check`

- [ ] **Step 2: Request code review**

Ask a read-only reviewer subagent to inspect the final diff against this plan.

- [ ] **Step 3: Fix accepted review findings**

Use TDD for any behavior changes from review feedback.

- [ ] **Step 4: Commit final fixes**

Commit with a message matching the final behavior changed.
