---
date: 2026-07-03
round: 10
gaps_to_fix: 7
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-10.md
---

# Fix Plan -- Round 10

## Summary
Fix 7 gaps across 6 files.

## Task 1: Remove hardcoded TOOL_RESULT_MAX_CHARS in AgentEngine -- use config
- **Gap IDs**: gap-10-1
- **Files**: `myagent/agent/engine.py`
- **Approach**:
  1. Remove the class constant `TOOL_RESULT_MAX_CHARS = 5000` (line 95).
  2. Add a `_tool_result_max_chars` property that reads `self.config.tools.tool_result_max_chars` if config is available, falling back to 5000.
  3. Replace `self.TOOL_RESULT_MAX_CHARS` references at lines 829 and 906 with `self._tool_result_max_chars`.
- **Verification**: Run `pytest tests/ -v` to ensure no regressions. The default value matches the old constant (5000), so behavior is unchanged unless user overrides config.

## Task 2: Remove terminal sub-agent handles from pool after lifecycle completes
- **Gap IDs**: gap-10-2
- **Files**: `myagent/subagent/pool.py`
- **Approach**:
  1. In `_run_background`, after status callbacks have fired and `_completion_event` is set, schedule removal of the handle from `_agents` dict.
  2. Use `asyncio.get_event_loop().call_later(5.0, ...)` approach or an async sleep. Best approach: add a `_cleanup_handle(agent_id)` method that removes the handle after a short delay (e.g., 5 seconds), giving status bar time to show the final state.
  3. Actually, a better approach: remove immediately from `_agents` after the status callback fires, because the completion event is already set. But the status bar enumerates `_agents` to show completed agents... Let me re-read the gap.
  4. The gap says: "remove them from the dict immediately after the status bar callback has consumed the final state." Since the status callback fires inside `_run_background` at the same time we'd remove, we should keep them for a brief period then remove. Use a small delay (2 seconds) via `asyncio.sleep(2)` before removing, since the status callback was already called synchronously at that point.
- **Verification**: Run `pytest tests/ -v`. After fix, `_agents` should not grow indefinitely.

## Task 3: Pass model override from SubAgentWorker to LLMProvider.complete()
- **Gap IDs**: gap-10-3
- **Files**: `myagent/subagent/worker.py`, `myagent/llm/provider.py`
- **Approach**:
  1. In `LLMProvider.complete()`, add `model_override: str | None = None` parameter. When provided, use it instead of `self.model` in the `models_to_try` list.
  2. In `SubAgentWorker._run_impl()`, pass `model_override=self.model` to `self.llm.complete()`.
- **Verification**: When `spawn_subagent` is called with `model="deepseek-v4-pro"`, the sub-agent should use that model. Default behavior unchanged.

## Task 4: Log MCP stderr at appropriate levels based on content
- **Gap IDs**: gap-10-4
- **Files**: `myagent/tools/mcp/client.py`
- **Approach**:
  1. In `StdioTransport._drain_stderr`, classify each line before logging:
     - Lines containing "error", "traceback", "panic", "fatal", "critical" (case-insensitive) -> `logger.error()`
     - Lines containing "warn" (case-insensitive) -> `logger.warning()`
     - Everything else -> `logger.debug()` (unchanged)
  2. This ensures MCP server errors are visible at default INFO level.
- **Verification**: MCP server errors on stderr now produce ERROR-level logs visible at INFO.

## Task 5: Add format validation to --export CLI argument
- **Gap IDs**: gap-10-5
- **Files**: `myagent/cli/main.py`
- **Approach**:
  1. Change `parser.add_argument("--export", ...)` to include `choices=["markdown", "json"]`.
  2. Update the `default` from `"markdown"` to `"json"` to match the current behavior of `export_session()` (which treats non-markdown as JSON).
- **Verification**: Running `myagent --export pdf` should now fail with an argparse error about invalid choice.

## Task 6: Remove hard truncation in save_tool_call, use reference file pattern
- **Gap IDs**: gap-10-6
- **Files**: `myagent/context/persistence.py`
- **Approach**:
  1. In `save_tool_call`, replace `str(call.result)[:50000]` with the full result string.
  2. For very large results (over `_LONG_MESSAGE_THRESHOLD`), use a similar reference-file pattern: store full result in `long-messages/tool-call-NNN.json` and write a reference string to the JSON. Simpler: just store the full result without truncation. Tool call result files are small enough that 50K+ results are edge cases. The spec says "完整输入/输出", so store complete.
  3. Remove `[:50000]` truncation entirely.
- **Verification**: Tool call results > 50000 chars are now fully persisted.

## Task 7: Pass config to sub-agent worker for proper ToolContext fallback
- **Gap IDs**: gap-10-7
- **Files**: `myagent/subagent/worker.py`, `myagent/subagent/pool.py`
- **Approach**:
  1. In `SubAgentWorker.__init__`, add `config=None` parameter and store as `self._config`.
  2. In `SubAgentWorker._run_impl`, when creating the fallback `ToolContext`, pass `config=self._config` instead of `config=None`.
  3. In `SubAgentPool._run_background`, already receives `config` via `spawn()`; pass it through to `SubAgentWorker()`.
  4. In `SubAgentPool.spawn()`, pass `config` through to `_run_background`.
- **Verification**: Sub-agents without explicit `tool_context` now respect `config.tools.shell_timeout_seconds`.
