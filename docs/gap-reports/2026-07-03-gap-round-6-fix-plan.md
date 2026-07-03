---
date: 2026-07-03
round: 6
gaps_to_fix: 8
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-6.md
---

# Fix Plan — Round 6

## Summary
Fix 8 gaps across 8 files.

## Task 1: Model-driven intent detection (replace keyword matching)
- **Gap IDs**: gap-r6-01
- **Files**: `myagent/agent/engine.py`, `myagent/context/builder.py`
- **Approach**:
  1. Update L0 system prompt to instruct the model to emit structured intent signals using a virtual `signal_intent` tool call format: `tool_call(name="signal_intent", params={"intent": "stop|correct|insert|continue"})`.
  2. Replace `_detect_intent` keyword matching with parsing of the model's assistant message for `signal_intent` tool calls. The model will now emit text content AND a `signal_intent` tool call in the same turn.
  3. Keep a thin fallback for brief continue phrases (< 30 chars) since the model might not always use the structured format.
- **Verification**: Test that stop/correct/insert intents are correctly detected from structured model output, and the keyword lists are reduced to only the minimal continue fallback.

## Task 2: Task-relevant sub-agent L3 project context filtering
- **Gap IDs**: gap-r6-02
- **Files**: `myagent/subagent/worker.py`, `myagent/subagent/pool.py`
- **Approach**:
  1. Create a `_filter_project_context(prompt, project_context)` function that checks the sub-task prompt for relevance keywords (e.g., "git" → git_branch, "structure"/"layout" → structure_summary, "python"/"test" → project_type).
  2. Only inject project context fields that match keyword relevance from the sub-task prompt.
  3. Always include project_type as a minimal default (it is almost always relevant).
- **Verification**: Verify that a sub-agent with prompt "review code" gets only project_type, while a "check git branch" sub-agent also gets git_branch.

## Task 3: Communicate skill_invoke virtual tool to the model
- **Gap IDs**: gap-r6-03
- **Files**: `myagent/context/builder.py`
- **Approach**:
  1. Update the L0 system prompt to explain the `skill_invoke` virtual tool mechanism.
  2. In the L2 skills index section, add instruction: when the model determines a listed skill matches the task, it should emit `tool_call(name="skill_invoke", params={"skill": "<name>"})`.
  3. Clarify that this virtual tool is intercepted internally and does not appear in the tools list.
- **Verification**: The L0 prompt now documents `skill_invoke` usage; a model reading the system prompt knows how to activate skills.

## Task 4: Remove transcript message truncation
- **Gap IDs**: gap-r6-04
- **Files**: `myagent/context/persistence.py`
- **Approach**:
  1. Remove the `[:5000]` and `[:2000]` truncation from `_write_transcripts()` and `_write_closed_session()`.
  2. Store full message content in both JSON and Markdown transcripts.
  3. For extremely long messages (> 50000 chars), store a reference file in `long-messages/` subdirectory and include the file reference in the transcript.
- **Verification**: Transcript files now contain full message content instead of truncated versions.

## Task 5: Add send_message to TOOL_LEVEL_MAP
- **Gap IDs**: gap-r6-05
- **Files**: `myagent/permissions/controller.py`
- **Approach**: Add `"send_message": 0` entry to `TOOL_LEVEL_MAP`. `send_message` is a read-only communication tool with no filesystem/network side effects, matching level 0 classification.
- **Verification**: `_get_level("send_message")` returns 0 instead of the default 3.

## Task 6: Memory cache expiration on topic drift
- **Gap IDs**: gap-r6-06
- **Files**: `myagent/context/builder.py`
- **Approach**:
  1. Track the last N user inputs (sliding window of 5) to detect topic drift.
  2. Compute a simple topic drift score by comparing the current input to the cached key's semantic space. Use a keyword-overlap heuristic: if < 30% of significant words overlap between current input and cached key topics, re-run recall.
  3. When drift is detected, clear the cache and re-run semantic recall with the new input. Update the cache key.
  4. Also add a turn-count-based expiry: after 20 turns, auto-refresh the cache.
- **Verification**: Topic changes mid-session cause fresh memory recall instead of serving stale cached results.

## Task 7: Dream engine round counting includes current session
- **Gap IDs**: gap-r6-07
- **Files**: `myagent/agent/session.py`, `myagent/cli/repl.py`
- **Approach**:
  1. Add optional `current_session` parameter to `SessionManager.estimate_total_rounds()`.
  2. When a current session is provided, add its `turn_count` to the persisted transcripts total.
  3. Update `_periodic_dream_check` in `repl.py` to pass `self._current_session` to `estimate_total_rounds()`.
- **Verification**: During a long-running session with 60 unsaved turns, the dream check correctly counts them and triggers the dream.

## Task 8: Sub-agent status bar updates on lifecycle events
- **Gap IDs**: gap-r6-08
- **Files**: `myagent/subagent/pool.py`, `myagent/cli/main.py`, `myagent/cli/status.py`
- **Approach**:
  1. Add a callback registration mechanism to `SubAgentPool`: `on_status_change(callback)` stores callbacks that fire on any status transition.
  2. In `_run_background`, after every status change (completed, failed, interrupted), invoke registered callbacks with the agent_id and new status.
  3. In `main.py`, register a callback that builds the `SubAgentInfo` list and calls `status_bar.update()` — replacing the monkey-patched `_spawn_with_status` wrapper.
  4. The monkey-patched `spawn` wrapper in `main.py` is simplified to only fire the initial spawn notification; subsequent lifecycle updates come from the pool callback.
- **Verification**: Status bar updates show real-time sub-agent completions, failures, and transitions — not just spawn events.
