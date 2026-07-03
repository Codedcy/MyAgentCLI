---
date: 2026-07-03
round: 16
gaps_to_fix: 6
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-16.md
---

# Fix Plan — Round 16

## Summary
Fix 6 gaps across 4 files: commands.py, compression.py, engine.py, dream.py, persistence.py.

## Task 1: /exit triggers session-end directly without --force
- **Gap IDs**: gap-16-01
- **Files**: `myagent/cli/commands.py`
- **Approach**: Remove the `--force` flag requirement from `_cmd_exit`. The spec (§十) states `/exit` and `/quit` should directly initiate the session-end flow. Change the handler to set `exit_requested=True` unconditionally. This matches the spec's statement: "当用户执行 `/exit`、`/quit` 或 Ctrl+D 退出时，按以下顺序执行"
- **Verification**: Type `/exit` in REPL — should exit immediately. Type `/quit` — same.

## Task 2: Post-Layer-3 compression usage estimate uses actual measurement
- **Gap IDs**: gap-16-02
- **Files**: `myagent/context/compression.py`
- **Approach**: After Layer 3 compression, compute the actual character-based size reduction ratio and apply it to `current_usage_pct` instead of setting it to `config.target_after`. This ensures the estimate reflects actual reduction achieved. If the reduction is very small, the real usage is preserved rather than being falsely optimistic.
- **Verification**: After auto-compact, verify in logs that usage_after reflects actual character reduction, not hardcoded 0.30.

## Task 3: 50% context warning flag resets after compression drops usage below 30%
- **Gap IDs**: gap-16-03
- **Files**: `myagent/agent/engine.py`
- **Approach**: After auto-compaction in `_react_loop`, if `usage_pct` drops below 0.30, reset `context_notified_50 = False` so the user gets re-warned if usage climbs back above 50%. This handles the within-single-turn edge case where compaction brings usage down, then subsequent tool calls push it back up.
- **Verification**: Trigger auto-compact at 75%+, verify flag resets when usage drops < 30%.

## Task 4: Dream sub-agent uses structured JSON output for operation counting
- **Gap IDs**: gap-16-04
- **Files**: `myagent/memory/dream.py`
- **Approach**: 
  1. Modify `_build_dream_subagent_prompt` to instruct the sub-agent to include a structured JSON block at the very end of its response, e.g.:
     ```
     ```json
     {"created": N, "updated": N, "deleted": N}
     ```
  2. In `_run_as_subagent`, after receiving the sub-agent output, parse this JSON block using regex to extract the structured counts.
  3. Fall back to introspecting `memory_store.get_session_writes()` after the sub-agent completes to cross-validate and fill in any missing counts.
  4. The regex-based counting on NL output is removed as the primary mechanism.
- **Verification**: Run a dream cycle, verify log shows accurate operation counts from structured output.

## Task 5: Session resume restores _persist_idx
- **Gap IDs**: gap-16-05
- **Files**: `myagent/context/persistence.py`
- **Approach**: In `load_session()`, after restoring messages from the transcript, set `session._persist_idx = len(session._messages)`. This ensures the first `_persist_turn` call after resume only persists newly added messages, not the full history.
- **Verification**: Resume a session, add a new message, check that only the new message is written to transcript.json.

## Task 6: /history shows tool call details
- **Gap IDs**: gap-16-06
- **Files**: `myagent/cli/commands.py`
- **Approach**: In `_cmd_history`, inspect each message for `tool_call_id`, `tool_calls`, or `name` attributes (which indicate tool-related messages). For tool role messages, show `[TOOL: <name>] <result_preview>`. For assistant messages containing `tool_calls`, show `[ASSISTANT] → tool_call: <name>(<params_summary>)`. This gives users visibility into tool activity in the conversation history.
- **Verification**: Run `/history` after a session with tool calls, verify tool call names and results appear.
