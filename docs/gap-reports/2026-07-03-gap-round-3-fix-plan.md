---
date: 2026-07-03
round: 3
gaps_to_fix: 18
source_report: docs/gap-reports/2026-07-03-gap-round-3.md
---

# Fix Plan — Round 3

## Summary
Fix 18 gaps across 17 files. 2 new files created. Covers all categories: 3 deviations, 7 missing, 8 incomplete.

## Task 1: Fix dream engine MemoryStore.write() calls (gap-01)
- **Gap IDs**: gap-01
- **Files**: `myagent/memory/dream.py`
- **Approach**: `MemoryStore.write()` signature is `write(self, file_path: str, content: str)`. Dream engine calls `write(name=..., content=..., description=...)` which is invalid. Fix: construct a proper file path from the name (e.g., `str(mem_dir / f"{name}.md")`) and call `write(file_path=str(path), content=...)`. Use the project memory dir from `memory_store.project_dir`.
- **Verification**: Ensure dream engine no longer crashes with TypeError when `_merge_contradictions()` or `_create_memories_from_patterns()` run.

## Task 2: Fix JsonLineFormatter field extraction (gap-02)
- **Gap IDs**: gap-02
- **Files**: `myagent/logging/formatter.py`
- **Approach**: Python's `logging` module expands `extra={...}` keys into individual `record` attributes (e.g., `record.category`, `record.event`). The formatter reads `getattr(record, 'extra_fields', None)` but most code uses standard `extra`. Fix: change the formatter to collect all known spec fields directly from record attributes (`record.category`, `record.event`, `record.model`, etc.) plus any unknown `__dict__` keys not part of the standard LogRecord fields.
- **Verification**: All log records carry `category`, `event`, and other spec-mandated fields in the JSON output.

## Task 3: Wire compression _session_dir (gap-03)
- **Gap IDs**: gap-03
- **Files**: `myagent/context/compression.py`, `myagent/agent/engine.py`
- **Approach**: Add a `set_session_dir(path: Path)` method to `CompressionEngine`. In `AgentEngine._react_loop()`, call `self.compression.set_session_dir(session_dir)` once the session directory is known. The engine receives `session` in `run()`, and the session store can compute the session dir.
- **Verification**: Compression summaries are persisted to the `summaries/` subdirectory of the active session.

## Task 4: Implement per-turn message persistence (gap-04)
- **Gap IDs**: gap-04
- **Files**: `myagent/agent/engine.py`
- **Approach**: After each completed ReAct iteration where the assistant produces a text response or tool calls, call `session.add_message()` for the assistant message and `session_store.save_turn()` (or `_write_transcripts()`) to persist the updated transcript to disk. Store both raw dict messages and Message objects.
- **Verification**: Messages survive process crashes; transcript.json and transcript.md update incrementally.

## Task 5: Add thinking_mode, stream, and tools_count to LLM request log (gap-05, gap-17)
- **Gap IDs**: gap-05, gap-17
- **Files**: `myagent/llm/provider.py`
- **Approach**: In `_complete_with_model()`, add `thinking_mode: thinking`, `stream: True`, and `tools_count: len(tools) if tools else 0` to the request log `extra` dict.
- **Verification**: LLM request log records contain all spec-mandated fields: `model`, `thinking_mode`, `messages_count`, `estimated_tokens`, `tools_count`, `stream`.

## Task 6: Add parent_session and prompt_summary to sub-agent logs (gap-06, gap-15)
- **Gap IDs**: gap-06, gap-15
- **Files**: `myagent/subagent/pool.py`
- **Approach**: In `_run_background()`, add structured fields to the spawn and completion log extras: `subagent_id`, `event` (spawned/completed/failed), `parent_session`, `prompt_summary` (truncated to 100 chars), `duration_ms`. Also add `duration_ms` to the completion log.
- **Verification**: Sub-agent log records carry all spec-mandated fields.

## Task 7: Create config_set tool for runtime config overrides (gap-07)
- **Gap IDs**: gap-07
- **Files**: `myagent/tools/builtin/config_tools.py` (NEW), `myagent/cli/main.py`
- **Approach**: Create a `ConfigSetTool` with parameters `key` (dot-separated config path) and `value`. The `execute()` method calls `ConfigLoader.apply_runtime_override(key, value)` to update in-memory config. Register the tool in `main.py`. This gives the model a tool to call when it detects config-change intent in user messages.
- **Verification**: Model can call `config_set` to change thinking mode, concurrency, tool result char limit, etc. mid-conversation.

## Task 8: Route MemoryWriteTool through MemoryStore (gap-08)
- **Gap IDs**: gap-08
- **Files**: `myagent/tools/builtin/memory_tools.py`, `myagent/tools/base.py`, `myagent/agent/engine.py`
- **Approach**: Add `memory_store` to `ToolContext`. In `MemoryWriteTool.execute()`, parse frontmatter to get the name, construct a proper file path, and delegate to `context.memory_store.write(file_path, content)` instead of writing directly. Remove the static `_update_index` method (MemoryStore handles it).
- **Verification**: MemoryWriteTool uses MemoryStore's dedup logic and session logging.

## Task 9: Implement TimedRotatingFileHandler + RotatingFileHandler combination (gap-09)
- **Gap IDs**: gap-09
- **Files**: `myagent/logging/logger.py`
- **Approach**: Create a `MidnightRotatingHandler` class that wraps file opening and checks for date transition on each emit. On date rollover, close the old file and open a new one with the new date. Keep `RotatingFileHandler` for size-based rotation. The handler re-computes the date-based filename on each emit, and if the date has changed, performs a rollover.
- **Verification**: Logs automatically rotate at midnight, even for long-running sessions.

## Task 10: Implement mid-execution interrupt via Ctrl+C (gap-10, gap-18)
- **Gap IDs**: gap-10, gap-18
- **Files**: `myagent/cli/repl.py`, `myagent/agent/engine.py`
- **Approach**: 
  - Add an `interrupt_event: asyncio.Event` parameter to `AgentEngine.__init__()` and `_react_loop()`.
  - In `_react_loop()`, check `interrupt_event.is_set()` at each iteration boundary and yield `Interrupted` when set, then break.
  - In REPL's `process_input()`, run the engine in an asyncio task. Bind Ctrl+C (via keybindings or signal handler) to set `interrupt_event`.
  - When engine is interrupted, cancel the task and return control to the REPL prompt.
- **Verification**: Ctrl+C during agent execution stops the agent and returns to the prompt. `Interrupted` event is yielded by the engine.

## Task 11: Register /help command handler (gap-11)
- **Gap IDs**: gap-11
- **Files**: `myagent/cli/commands.py`
- **Approach**: In `CommandDispatcher._register_defaults()`, register `"help"` → `_cmd_help`. The `_cmd_help` handler lists all available slash commands with their descriptions.
- **Verification**: Typing `/help` lists available commands instead of "Unknown command".

## Task 12: Make task list session-scoped (gap-12)
- **Gap IDs**: gap-12
- **Files**: `myagent/tools/builtin/session_tools.py`, `myagent/cli/repl.py`
- **Approach**: Call `reset_task_list()` when a new session starts in `REPLEngine.run()` and when resuming in `main.py`. Pass a persist path derived from the session directory so tasks persist per-session.
- **Verification**: Task list resets when switching sessions; each session has independent tasks.

## Task 13: Read shell_timeout_seconds from config in BashTool (gap-13)
- **Gap IDs**: gap-13
- **Files**: `myagent/tools/builtin/exec_tools.py`
- **Approach**: In `BashTool.execute()`, read `context.config.tools.shell_timeout_seconds` (convert to ms) as the default timeout when `params.get("timeout")` is not explicitly provided by the model. Fall back to hardcoded 120000 if config is unavailable.
- **Verification**: Config `tools.shell_timeout_seconds` is honored instead of always using hardcoded 120000ms.

## Task 14: Implement worktree isolation for spawn_subagent (gap-14)
- **Gap IDs**: gap-14
- **Files**: `myagent/subagent/pool.py`, `myagent/subagent/worker.py`
- **Approach**: When `isolation="worktree"` is specified, the worker creates a git worktree under `.claude/worktrees/` with a unique name. The worker's `working_dir` is set to the worktree path. On completion/cleanup, the worktree is removed. The pool passes the `project_dir` for context so the worker knows where to create the worktree.
- **Verification**: Sub-agent with `isolation="worktree"` runs in an isolated git worktree directory.

## Task 15: Set log context session_id after session creation (gap-16)
- **Gap IDs**: gap-16
- **Files**: `myagent/cli/repl.py`, `myagent/cli/main.py`
- **Approach**: After `session_mgr.start_new()` creates a session in `REPLEngine.run()`, call `set_context(session_id=session.id, project_name=project_dir.name)` from `myagent.logging.context`. Also in main.py after resume creates a session.
- **Verification**: Log records after session creation carry the correct `session_id` and `project` fields.

## Verification
- Run `pytest tests/ -v` after each task to catch regressions.
- Run `git status` at the end to ensure all changes are committed.
