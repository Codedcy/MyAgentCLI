# Task 3 Report: Wire CLI — Renderer, StatusBar, CommandDispatcher, --resume, multi-line REPL

**Status:** COMPLETE
**Commit:** `89a24e1` — `fix(cli): wire CommandDispatcher, Renderer, StatusBar; fix --resume; enable multi-line REPL`
**Tests:** 188 passed, 0 failed
**Lint:** Clean (ruff check passes with 0 errors)

## Changes Made

### `myagent/cli/main.py` — Entry point wiring

1. **CommandDispatcher wired**: Created and wired `CommandDispatcher()` instance, passed to REPLEngine instead of `commands=None`
2. **Renderer wired**: Created `Renderer()` instance and passed to REPLEngine
3. **StatusBar wired**: Created `StatusBar(config.ui)` conditionally (when `config.ui.show_status_bar` is true), passed to REPLEngine
4. **--resume fixed**: Previously the `args.resume` flag was parsed but ignored — it always called `start_new()`. Now it:
   - Resolves `__latest__` to `None` (telling `resume()` to pick the latest)
   - Calls `session_mgr.resume(session_id, project_dir)`
   - If a session is found, creates REPLEngine, sets `_current_session` directly, and runs
   - If no session found, prints a message and returns exit code 1

### `myagent/cli/repl.py` — REPL engine overhaul

1. **New constructor params**: Accepts `renderer` and `status_bar` (both optional with `None` defaults for backward compatibility)
2. **Multi-line input**: `PromptSession(multiline=True)` — Enter inserts newline, Alt+Enter submits
3. **Ctrl+C interrupt**: KeyBindings intercept `c-c` to clear the buffer via `buffer.reset()`, instead of raising KeyboardInterrupt and exiting
4. **Rich Renderer usage**: Events are dispatched to `self._renderer.render_event(event)`, then printed via Rich `Console`. TextChunk gets inline printing (`end=""`); thinking chunks hidden; other events printed as block output. Falls back to `_render_event_fallback()` when no renderer is wired.
5. **Full CommandContext**: `CommandContext` now includes `session_manager`, `goal_tracker`, and `skill_registry` fields (extracted from `self._engine`)
6. **Graceful shutdown via `_shutdown()`**: On any exit path (Ctrl+D, `/exit`, `/quit`), `_shutdown()` is called which:
   - Stops the status bar (`self._status_bar.stop()`)
   - Calls `session_mgr.end_session()` to finalize the session
   - Prints "Goodbye!"
7. **Fallback rendering extracted**: `print()`-based event rendering moved to `_render_event_fallback()` for clarity

### `myagent/cli/renderer.py` — No changes needed

The Renderer already had a complete `render_event()` method with handlers for all event types (TextChunk, ThinkingChunk, ToolCallStart, ToolCallEnd, Done, Error). No modifications required.

### `myagent/cli/commands.py` — No changes needed

CommandDispatcher was already fully functional. The only change was that it's now actually wired in and receives a properly populated CommandContext.

## Verification

- `pytest tests/ -v`: 188 passed
- `ruff check myagent/cli/main.py myagent/cli/repl.py`: 0 errors

## Fix Report

**Date:** 2026-07-03

Five review findings addressed:

### 1. `StatusBar.stop()` not awaited (no change needed)

Checked `myagent/cli/status.py`: `StatusBar.stop()` is a synchronous method (lines 39-41). It calls `self._live.stop()` which is also synchronous. No `await` is needed -- the existing `self._status_bar.stop()` call is correct.

### 2. Dead code removed (repl.py ~lines 134-136)

The `/exit`/`/quit` check at lines 106-108 in `process_input()` already catches those commands before entering the `if self._commands:` block. The duplicate check inside the `if self._commands:` block (lines 132-135) was unreachable. Removed.

### 3. Import reordering reverted (main.py `_register_builtin_tools`)

Imports in `_register_builtin_tools()` had been alphabetized (agent, exec, file, memory, search, session, web) from the original order in commit `8a6076f` (file, search, exec, agent, session, memory, web). Reverted to the original ordering.

### 4. Double "Goodbye!" in fallback path fixed (repl.py line 94)

In the `ImportError` fallback path, the `KeyboardInterrupt` catch at line 94 printed `"\nGoodbye!"` and broke, then `_shutdown()` also printed `"\nGoodbye!"` -- causing a double goodbye. Fixed by changing line 94 to just print a newline (`print()`) instead. `_shutdown()` remains the single source of truth for the "Goodbye!" message.

### 5. Redundant `import pathlib` removed (repl.py line 52)

`import pathlib` was used only for `pathlib.Path.home()`. The module-level `from pathlib import Path` (line 5) already provides `Path.home()`. Removed the redundant import and changed `pathlib.Path.home()` to `Path.home()`.

### Verification

- `pytest tests/cli/ -v`: 17 passed
- `pytest tests/ -v`: 188 passed, 0 failed
