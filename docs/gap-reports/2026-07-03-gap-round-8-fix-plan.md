---
date: 2026-07-03
round: 8
gaps_to_fix: 14
source_report: docs/gap-reports/2026-07-03-gap-round-8.md
---

# Fix Plan -- Round 8

## Summary
Fix 14 gaps across 11 files. Covers sub-agent retry, error logging, LLM error UX, log cleanup, Ctrl+C exit flow, progress tracking, status bar, session paths, memory index, session duration, web search fallback, config tool registration, log formatting consistency, and sub-agent messaging wiring.

---

## Task 1: Add LLM retry with exponential backoff to SubAgentWorker
- **Gap IDs**: gap-8-01
- **Files**: myagent/subagent/worker.py
- **Approach**: SubAgentWorker._run_impl() currently catches all LLM exceptions once and immediately returns an error. Add retry logic identical to LLMProvider: up to 3 retries with exponential backoff (2s initial, 30s max). Use same error classification (rate_limit/connection_error/server_error/timeout = retryable, auth_error/bad_request = fatal). Silent retries -- no user notification. Only final failure after 3 retries produces error return.
- **Verification**: Sub-agent LLM calls retry up to 3 times on transient errors. Fatal errors return immediately. Retry-related log entries have exc_info=True for traceback capture.

## Task 2: Add exc_info=True to all error-level log calls
- **Gap IDs**: gap-8-02
- **Files**: myagent/agent/engine.py, myagent/subagent/worker.py, myagent/subagent/pool.py
- **Approach**: Convert all identified `logger.error(...)` calls without `exc_info=True` to use `exc_info=True`. The mcp/client.py line 342 was already addressed in a previous round. Fix engine.py:337, engine.py:793, worker.py:254, pool.py:369.
- **Verification**: Every except block produces full traceback in logs as required by spec S11.

## Task 3: Produce user-friendly error guidance when LLM retries exhausted
- **Gap IDs**: gap-8-03
- **Files**: myagent/agent/engine.py
- **Approach**: At engine.py line 333-356, the catch for `Exception` yields `Error(message=str(e))`. When the error is an LLMError (or has LLMError characteristics), produce a specific user-facing message advising to check network connectivity and API key validity. Detect LLMError by checking `isinstance(e, LLMError)` or string matching. Also cover the case when partial_text is present (stream interrupted mid-response).
- **Verification**: User sees actionable guidance instead of raw exception text when all retries fail.

## Task 4: Fix log cleanup to also clean up JSONL files
- **Gap IDs**: gap-8-04
- **Files**: myagent/logging/logger.py
- **Approach**: Change `LogManager._cleanup_old_logs()` glob pattern from `myagent*.log*` to `myagent*.*` to cover both `.jsonl` and `.log` files (and their rotated siblings).
- **Verification**: Both .jsonl and .log files older than retention_days are cleaned up.

## Task 5: Fix Ctrl+C on idle to trigger exit confirmation
- **Gap IDs**: gap-8-05
- **Files**: myagent/cli/repl.py
- **Approach**: The prompt_toolkit key binding for Ctrl+C at line 175-186 currently resets the buffer when the engine is idle. Change the idle-buffer case to call `event.app.exit(result="__ctrl_c_exit__")`, then catch this value in the prompt loop to trigger the "Exit? (y/n)" confirmation flow. Using `event.app.exit()` properly signals the prompt_toolkit event loop to return from `prompt_async()`, which the existing KeyboardInterrupt handler can process. Alternative: directly show the exit confirmation inline by reading from the event's buffer.
- **Verification**: Pressing Ctrl+C on idle shows "Exit? (y/n)?" prompt instead of clearing the buffer.

## Task 6: Track and display sub-agent iteration progress
- **Gap IDs**: gap-8-06
- **Files**: myagent/subagent/worker.py, myagent/cli/main.py, myagent/cli/status.py
- **Approach**: SubAgentWorker stores `MAX_ITERATIONS` as class constant (30). The worker reports current iteration through a progress callback or by storing it on a handle attribute. The pool's _run_background reads worker._iteration after each loop iteration and fires a progress notification. The status callback in main.py uses this to compute progress_pct = current_iteration / MAX_ITERATIONS * 100. Update SubAgentInfo with actual progress_pct.
- **Verification**: Status bar shows sub-agent progress like "(50%)" when a sub-agent is on iteration 15/30.

## Task 7: Display LLM retry progress in status bar
- **Gap IDs**: gap-8-07
- **Files**: myagent/cli/status.py
- **Approach**: StatusBar.get_renderable() checks for "retry_info" key in self._data and renders it as an additional line in the status bar panel (e.g., "Retrying: 2/3 (15.0s)").
- **Verification**: During LLM retries, the status bar shows retry progress. The retry_info disappears when retries complete.

## Task 8: Fix session directory path resolution to use Path.expanduser()
- **Gap IDs**: gap-8-08
- **Files**: myagent/cli/main.py
- **Approach**: Replace `sessions_dir_raw.replace("~", str(Path.home()))` with `Path(sessions_dir_raw).expanduser()` at main.py line 99-102.
- **Verification**: Paths with `~` are correctly expanded. Paths with literal `~` in non-home positions are not mangled.

## Task 9: Enrich MEMORY.md index with structured metadata
- **Gap IDs**: gap-8-09
- **Files**: myagent/memory/store.py
- **Approach**: In _update_index(), include memory type and last-updated timestamp in a markdown table format. Read the frontmatter metadata from each file to extract type and updated fields. Format index entries as `| Name | Type | Updated | Description |` table rows.
- **Verification**: MEMORY.md index includes a markdown table with type and updated columns.

## Task 10: Compute live duration for open sessions in listing
- **Gap IDs**: gap-8-10
- **Files**: myagent/context/persistence.py
- **Approach**: In list_sessions(), when reading a transcript.json that shows `duration: 0` and no `closed` flag, compute the duration as `(now - created_at).total_seconds()`. This gives accurate live duration for still-open sessions.
- **Verification**: Listing sessions shows accurate duration for open sessions instead of "--".

## Task 11: Make WebSearchTool degrade gracefully on API failure
- **Gap IDs**: gap-8-11
- **Files**: myagent/tools/builtin/web_tools.py
- **Approach**: When DuckDuckGo API fails, instead of returning a stub message, attempt a fallback approach: try alternative search URL or return a clear error result that signals to the LLM that search is unavailable. At minimum, change the stub to clearly indicate SEARCH FAILED rather than a result that looks like search output.
- **Verification**: WebSearchTool returns clear failure signal when API is down, not a misleading stub.

## Task 12: Register config_set tool and add to built-in tool list
- **Gap IDs**: gap-8-12
- **Files**: myagent/cli/main.py, docs/superpowers/specs/2026-07-02-myagentcli-design.md
- **Approach**: ConfigSetTool file exists but is NOT currently registered in _register_builtin_tools(). Register it to make the runtime config adjustment capability available to the model. Add config_set entry to the design spec's built-in tools table under a new "Config" category.
- **Verification**: config_set tool is registered and listed in the design spec.

## Task 13: Fix LogManager startup to use flat extra pattern
- **Gap IDs**: gap-8-13
- **Files**: myagent/logging/logger.py
- **Approach**: Change the startup and shutdown log calls from the legacy `extra={"extra_fields": {"category": LOG_SYSTEM, ...}}` pattern to the flat `extra={"category": LOG_SYSTEM, "event": "startup", ...}` pattern, consistent with LLMProvider and AgentEngine.
- **Verification**: All log calls use the flat extra={...} pattern consistently.

## Task 14: Ensure SendMessageTool 'main' target wiring is complete
- **Gap IDs**: gap-8-14
- **Files**: myagent/subagent/worker.py, myagent/tools/builtin/agent_tools.py
- **Approach**: The SendMessageTool.execute() calls `pool.send_to_main()` when target=="main". This works when tool_context.subagent_pool is properly set. The fallback ToolContext at worker.py line 231-236 has subagent_pool defaulting to None. Add `subagent_pool=self.tool_context.subagent_pool` to the fallback ToolContext construction in worker.py so that even the fallback path preserves the pool reference.
- **Verification**: Sub-agent can send messages to main agent via send_message tool even when using the fallback ToolContext.
