---
date: 2026-07-03
round: 13
gaps_to_fix: 8
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-13.md
---

# Fix Plan -- Round 13

## Summary
Fix 7 gaps across 9 files (gap-13-01 already resolved -- all 6 SKILL.md files exist).

## Task 1: Fix retry_count hardcoded to 0 (gap-13-02)
- **Gap IDs**: gap-13-02
- **Files**: myagent/llm/provider.py
- **Approach**: After the API call in `_complete_with_model()`, read `self._failure_count` (incremented by the failure hook during LiteLLM internal retries) and use that value for `retry_count` in both the response log extra dict and the `_write_response_log()` call. Reset the counter before the next call. Also update `_write_response_log` to accept the real retry_count.
- **Verification**: Check that `retry_count` is no longer hardcoded to 0 in the response log.

## Task 2: Fix spawn_subagent bypass of speculative_exploration gate (gap-13-03)
- **Gap IDs**: gap-13-03
- **Files**: myagent/tools/builtin/agent_tools.py
- **Approach**: Remove the `and "background" not in params` check so that when `speculative_exploration` is False and no goal is active, `background` is always forced to False regardless of what the model passes in params.
- **Verification**: The config gate cannot be bypassed by explicitly passing `background=True`.

## Task 3: Fix session end permission prompt to use Console.print (gap-13-04)
- **Gap IDs**: gap-13-04
- **Files**: myagent/agent/session.py
- **Approach**: Replace `Prompt.ask()` with `Console.print()` to display the prompt, then read user input via `sys.stdin.readline()`. This avoids prompt_toolkit dependency which may have been torn down at session end.
- **Verification**: Session end works without prompt_toolkit dependency.

## Task 4: Add dynamic model context window discovery (gap-13-05)
- **Gap IDs**: gap-13-05
- **Files**: myagent/agent/engine.py
- **Approach**: Add a `_get_context_window()` method that first attempts to query `litellm.model_cost` for the model's `max_input_tokens`, then falls back to the existing `_CONTEXT_WINDOW_MAP`. Update `_estimate_context_usage()` to use this method.
- **Verification**: Context window is dynamically determined from litellm when available.

## Task 5: Add category field to all log calls missing it (gap-13-06)
- **Gap IDs**: gap-13-06
- **Files**: myagent/tools/mcp/client.py, myagent/config/loader.py, myagent/memory/recall.py, myagent/permissions/controller.py, myagent/subagent/worker.py, myagent/subagent/pool.py, myagent/agent/engine.py, myagent/memory/dream.py, myagent/context/compression.py, myagent/tools/builtin/exec_tools.py, myagent/tools/builtin/web_tools.py, myagent/tools/builtin/mcp_tools.py, myagent/agent/goal.py
- **Approach**: Add `extra={"category": "<appropriate>"}` to every log call that is missing it. Categories follow spec: system, llm, tool, agent, subagent, error.
- **Verification**: All log calls include the category field.

## Task 6: Remove hardcoded bool_keys fallback in ConfigSetTool (gap-13-07)
- **Gap IDs**: gap-13-07
- **Files**: myagent/tools/builtin/config_tools.py
- **Approach**: Remove the `bool_keys` fallback set. Since `_TYPE_MAP` already contains type information for all known keys, the fallback is unnecessary. The `_validate_value` method should rely solely on `_TYPE_MAP` for type coercion.
- **Verification**: No duplicate type information; all boolean config keys are handled via `_TYPE_MAP`.

## Task 7: Add send_message to spec's level 0 tool list (gap-13-08)
- **Gap IDs**: gap-13-08
- **Files**: docs/superpowers/specs/2026-07-02-myagentcli-design.md
- **Approach**: Add `send_message` to the level 0 tool enumeration in the design spec. Level 0 is "read-only" and `send_message` (inter-agent communication) has no filesystem/network side effects, so level 0 is correct.
- **Verification**: Spec lists `send_message` in level 0 tools alongside read, glob, grep, etc.
