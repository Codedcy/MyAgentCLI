---
date: 2026-07-03
round: 4
gaps_to_fix: 11
source_report: docs/gap-reports/2026-07-03-gap-round-4.md
---

# Fix Plan — Round 4

## Summary
Fix 11 gaps across 10 files. All fixes are complete, working implementations — no stubs or TODOs.

## Task 1: G1 — Fix UnboundLocalError for status_bar in retry_callback
- **Gap IDs**: gap-1
- **Files**: `myagent/cli/main.py`
- **Approach**: Move `status_bar` construction (currently at line 177) to before the `LLMProvider` construction (line 83). The `LLMProvider` does not depend on `status_bar`, so this reorder is safe. The callback closure will then correctly reference the already-assigned `status_bar` variable.
- **Verification**: No UnboundLocalError at startup; retry progress updates appear in the status bar.

## Task 2: G2 — Persist goal_achieved to session on success
- **Gap IDs**: gap-2
- **Files**: `myagent/agent/engine.py`, `myagent/agent/session.py`
- **Approach**: 
  1. In `_react_loop()`, in the goal-achieved branch (before `yield Done()`), set `session.goal_achieved = True`.
  2. In `end_session()`, change the logic from unconditionally setting `goal_achieved = False` to only setting it to `False` if it is still `None`.
- **Verification**: Goal achievement is recorded in transcript; session listings show correct goal status.

## Task 3: G3 — Finalize transcript on session end
- **Gap IDs**: gap-3
- **Files**: `myagent/agent/session.py`
- **Approach**: In `end_session()`, after marking `session.goal_achieved`, call `self.session_store._write_transcripts()` with the final session state. Include a `closed: true` marker in the written data. To do this, add a `_write_closed_transcripts()` method to `SessionStore` that writes the final state with a `closed_at` timestamp.
- **Verification**: Transcript JSON contains `closed: true` and `closed_at` after session end.

## Task 4: G4 — Periodic dream trigger check in long-running sessions
- **Gap IDs**: gap-4
- **Files**: `myagent/cli/repl.py`, `myagent/memory/dream.py`
- **Approach**: In `REPLEngine.run()`, start a background asyncio task that periodically (every 30 minutes) re-checks `dream_engine.should_run()` against the current estimate of total rounds. If the condition becomes true mid-session, spawn a dream cycle in the background. Add an `estimate_total_rounds` parameter that can be updated over time via a callback. Use `asyncio.create_task` with a loop that sleeps and re-checks.
- **Verification**: In a session running >6 hours, dream triggers mid-session.

## Task 5: G5 — Live token counter in status bar
- **Gap IDs**: gap-5
- **Files**: `myagent/cli/repl.py`, `myagent/cli/status.py`
- **Approach**: In `REPLEngine.process_input()`, in the `_run_engine` callback, extract `usage.total_tokens` from `Done` events and call `self._status_bar.update(tokens=total_tokens)`. The status bar's existing token display field will then show live counts.
- **Verification**: Status bar shows live token count updates after each LLM turn.

## Task 6: G6 — Add functional code-review scripts OR update SKILL.md
- **Gap IDs**: gap-6
- **Files**: `myagent/skills/builtin/code-review/scripts/` (add `lint.sh`), OR `myagent/skills/builtin/code-review/SKILL.md`
- **Approach**: Add a real `lint.sh` script that runs `ruff check` on the project. This is a simple, functional script that matches the SKILL.md's declared resources. Also update SKILL.md to reference it properly. Remove `.gitkeep`.
- **Verification**: Script is executable and runs `ruff check` correctly.

## Task 7: G7 — Fix stale docstring in agent_tools.py
- **Gap IDs**: gap-7
- **Files**: `myagent/tools/builtin/agent_tools.py`
- **Approach**: Replace the file header docstring (lines 3-5) with an accurate description that reflects the current fully-implemented state of SubAgentPool and MemoryStore.
- **Verification**: Docstring accurately describes current state.

## Task 8: G8 — Add missing `context` field to error log records
- **Gap IDs**: gap-8
- **Files**: `myagent/agent/engine.py`, `myagent/llm/provider.py`, `myagent/subagent/worker.py`, `myagent/subagent/pool.py`
- **Approach**: Audit all `logger.error()` calls and ensure each includes a `"context"` key in the `extra` dict describing the triggering operation. For example: `"context": "execute_tool:read_file"`, `"context": "llm_complete_call"`, `"context": "subagent_run"`.
- **Verification**: All error log sites have context fields.

## Task 9: G9 — Independent 90% hard truncation trigger
- **Gap IDs**: gap-9
- **Files**: `myagent/agent/engine.py`
- **Approach**: In `_react_loop()`, after the 75% compression block (and outside the `if usage_pct >= 0.75:` guard), add an independent check for `usage_pct >= HARD_LIMIT` (0.90). When triggered, apply Layer 4 truncation directly (calling `compression._layer4_truncate()` or implementing equivalent logic in-engine). This matches the spec flowchart where 90% hard truncation is a separate trigger path.
- **Verification**: Context at 91% triggers hard truncation even if 75% compression was skipped due to minimum_messages guard.

## Task 10: G10 — Implement sub-agent-to-main-agent message channel
- **Gap IDs**: gap-10
- **Files**: `myagent/subagent/pool.py`, `myagent/agent/engine.py`, `myagent/tools/builtin/agent_tools.py`
- **Approach**: 
  1. Add `_outbound_queue: asyncio.Queue` to `SubAgentPool` for messages from sub-agents to main.
  2. Add `send_to_main(message: str)` method on `SubAgentHandle`.
  3. In `_react_loop()`, between iterations, drain `subagent_pool.get_outbound_messages()` and inject them as system-prompt context or user-visible messages.
  4. Update the `send_message` tool to support `to: "main"` which writes to the outbound queue instead of a sub-agent.
- **Verification**: A sub-agent configured to send messages to "main" delivers them; the main agent sees them in its loop.

## Task 11: G11 — Pass goal to ContextBuilder.build() in engine
- **Gap IDs**: gap-11
- **Files**: `myagent/agent/engine.py`
- **Approach**: In `AgentEngine.run()`, extract the current goal from `self.goal_tracker.get_goal()` and pass it as the `goal` parameter to `context_builder.build()`. This ensures the goal is always proactively visible in the system prompt on every turn, not just reactively after failed goal checks.
- **Verification**: Goal text appears in system prompt on every `build()` call.
