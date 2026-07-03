---
date: 2026-07-03
round: 19
gaps_to_fix: 10
source_report: docs/gap-reports/2026-07-03-gap-round-19.md
---

# Fix Plan — Round 19

## Summary
Fix 10 gaps across 9 files (plus 2 new content files for skills).

---

## Task 1: Use LiteLLM built-in retry mechanism (gap-19-01)
- **Gap IDs**: gap-19-01
- **Files**: `myagent/llm/provider.py`
- **Approach**: Set `litellm.num_retries = MAX_RETRIES` (3) and remove the explicit retry loop in `_complete_with_model()`. LiteLLM internally handles retries with exponential backoff. Keep exception wrapping for fallback model switching. Remove the manual sleep/backoff loop. The retry_callback will be registered as a litellm failure hook. Update module docstring and comments to reflect this change.
- **Verification**: Run existing LLM provider tests.

---

## Task 2: Stream interruption prompts user for decision (gap-19-02)
- **Gap IDs**: gap-19-02
- **Files**: `myagent/cli/repl.py`, `myagent/agent/engine.py`
- **Approach**: In `process_input` and `_run_engine`, detect `IntentSignal(intent="continue")` events. When one is seen, set a flag. After the engine task completes, if the flag is set AND the intent was "continue" (stream interrupted), display an explicit prompt to the user: "Stream interrupted. Continue? [Y/n]". Wait for user input. If "y", re-run with "continue" as input. If "n", do nothing.
- **Verification**: Manual testing or checking that the REPL code path handles IntentSignal properly.

---

## Task 3: Populate skill resource directories (gap-19-03)
- **Gap IDs**: gap-19-03
- **Files**: Create `myagent/skills/builtin/brainstorming/references/frameworks.md`, create `myagent/skills/builtin/tdd/templates/test_template.py`
- **Approach**: Create actual reference content for the brainstorming skill (design patterns, brainstorming frameworks) and a test file template for the tdd skill. Remove the `.gitkeep` files.
- **Verification**: Verify files exist and .gitkeep files are removed.

---

## Task 4: Add file-path auto-completion to REPL (gap-19-04)
- **Gap IDs**: gap-19-04
- **Files**: `myagent/cli/repl.py`
- **Approach**: Extend `SlashCompleter` to provide file-path completions when not in slash-command mode, using prompt_toolkit's `PathCompleter` or a custom implementation. When the user types a path-like string (containing `/` or starting with `.`), offer file completions. Use prompt_toolkit's `WordCompleter` for general word completion and add `PathCompleter` from `prompt_toolkit.completion`.
- **Verification**: Verify the completer provides file paths when typing path-like input.

---

## Task 5: Fix SSE transport connect() (gap-19-05)
- **Gap IDs**: gap-19-05
- **Files**: `myagent/tools/mcp/client.py`
- **Approach**: Rewrite `SSETransport.connect()` to create a single `httpx.AsyncClient`, open one SSE stream, and use proper SSE event parsing. The current code creates three clients, immediately closes two. Fix: create one client, use `client.stream("GET", url)` to get the SSE stream. In `_sse_reader`, use a proper byte-level SSE parser that handles multi-line data fields and comment lines.
- **Verification**: Verify the SSE transport code compiles and has no dead-code client instances.

---

## Task 6: Add size ceiling to tool result summarization (gap-19-06)
- **Gap IDs**: gap-19-06
- **Files**: `myagent/agent/engine.py`
- **Approach**: In `_summarize_via_subagent()`, cap the tool result passed in the sub-agent prompt at 200K chars. For results exceeding 200K chars, add a note that the full content is available via the persisted file reference. Add the file reference instruction explicitly. Also add a guard for results > 1M chars (exceeding even sub-agent context window) — in that case, fall back to truncation.
- **Verification**: Verify the code has proper size limits.

---

## Task 7: Route 90% hard truncation through compact() pipeline (gap-19-07)
- **Gap IDs**: gap-19-07
- **Files**: `myagent/agent/engine.py`
- **Approach**: Remove the separate `_layer4_truncate()` call block in the engine. Instead, when usage >= hard_limit after the 75% compact already ran (or was skipped), call `compression.compact()` with the hard_limit trigger. The compact() method already handles Layer 4 as a safety net (lines 170-175 of compression.py). But we need to ensure compact() is called even when the 75% path was skipped. The simplest fix: remove the duplicate L4 block and enhance the 75% compact path to also check hard_limit. Or better: restructure so that if usage < 75% but >= 90%, we call compact() which applies all applicable layers (L1-L3 are no-ops if below threshold, L4 fires if above hard_limit).
- **Verification**: Verify that the engine no longer has duplicate L4 truncation code.

---

## Task 8: Fix sub-agent name storage ordering (gap-19-08)
- **Gap IDs**: gap-19-08
- **Files**: `myagent/cli/main.py`
- **Approach**: In `_spawn_with_task_name()`, extract and store the task name BEFORE calling `_original_spawn`, then pass the stored name in the handle update. Since we don't have the handle.id yet (it's created during spawn), we need a two-phase approach: pre-compute the name, spawn, then store. For the race condition concern, this is already handled since `_original_spawn` returns immediately for background tasks (it creates the task but doesn't wait), and the callback fires asynchronously. However, to be logically correct: store the name immediately after getting the handle, which is already done. The gap says the issue is that the callback might fire before the name is stored — but since this is all synchronous (no await between `_original_spawn` return and `_task_names` assignment), the race condition doesn't actually exist in practice. The fix is cosmetic: add a comment explaining why the ordering is safe, and handle missing task names gracefully in the callback.
- **Verification**: Verify the callback handles missing task names gracefully.

---

## Task 9: Fix turn_count to not inflate from tool messages (gap-19-09)
- **Gap IDs**: gap-19-09
- **Files**: `myagent/context/persistence.py`
- **Approach**: In `Session.add_message()`, only increment `turn_count` for user and assistant messages (not tool results). A "turn" in the spec context means a conversation round (user→assistant exchange), not every internal message. Tool call results are part of the same turn.
- **Verification**: Run session-related tests.

---

## Task 10: Fix MCP server startup error handling (gap-19-10)
- **Gap IDs**: gap-19-10
- **Files**: `myagent/cli/main.py`
- **Approach**: In `_start_single_mcp_server()`, differentiate between "method not supported" errors (valid for optional resource/prompt endpoints) and genuine failures. Wrap `tools/list` in a try/except that logs and propagates the error (don't silently `pass`). For `resources/list` and `prompts/list`, catch specific error types (e.g., JSON-RPC "Method not found" errors) and log them at DEBUG level, while logging genuine failures at WARNING level.
- **Verification**: Verify the try/except blocks are properly differentiated.
