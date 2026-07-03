---
date: 2026-07-03
round: 1
gaps_to_fix: 34
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-1.md
---

# Fix Plan -- Round 1

## Summary
Fix 34 gaps across 22 files. gap-19 (built-in SKILL.md files) already resolved -- the files exist in the codebase. Each section describes the files to modify, the exact approach, and verification steps.

---

## Task 1: Wire auto-compact trigger into ReAct loop (gap-01)

- **Gap IDs**: gap-01
- **Files**: `myagent/agent/engine.py`
- **Approach**: In `_react_loop()`, after each iteration, call `self._estimate_context_usage(messages)` via a new helper. If >= 75%, call `self.compression.compact(...)`. If >= 90%, trigger layer-4 hard truncation. The compaction result replaces `messages`. Also add a 50% threshold notification (gap-25).
- **Verification**: Unit test that _react_loop calls compression.compact when messages exceed 75% threshold.

## Task 2: Wire permissions.check() into _execute_tool (gap-02)

- **Gap IDs**: gap-02, gap-26
- **Files**: `myagent/agent/engine.py`
- **Approach**: In `_execute_tool()`, before executing, call `self.permissions.check(tool_name, level, params)`. On DENY, return ToolResult with error. On ASK, call `self.permissions.confirm(tool_name, params)`. On ALLOW, proceed. Gate ALL tools including MCP tools (default level 3).
- **Verification**: Unit test that engine._execute_tool calls permissions.check() and handles DENY/ASK/ALLOW results.

## Task 3: MCP server startup and tool registration (gap-03)

- **Gap IDs**: gap-03
- **Files**: `myagent/cli/main.py`
- **Approach**: Add `_startup_mcp_servers()` helper to async_main. Read `~/.myagent/mcp.json` and `.myagent/mcp.json` (project-level). Parse JSON for server configs. For each server, instantiate MCPClient, call `start()`, call `list_tools()`, wrap each tool in MCPToolAdapter, and register via tool_registry.register(). Add proper error handling and logging.
- **Verification**: Integration test that MCP config reading and tool registration works.

## Task 4: Dream engine auto-trigger on startup (gap-04)

- **Gap IDs**: gap-04
- **Files**: `myagent/cli/main.py`
- **Approach**: In async_main, after session loading, construct DreamEngine. Call `dream_engine.should_run(session_rounds)`. If true, spawn as background sub-agent. Wire dream_engine into CommandContext so /dream command works.
- **Verification**: Manual verification that dream engine is constructed and wired.

## Task 5: Fix CompressionEngine tool_result_max_chars config access (gap-05)

- **Gap IDs**: gap-05
- **Files**: `myagent/context/compression.py`, `myagent/cli/main.py`
- **Approach**: Change CompressionEngine.__init__ to accept an optional `tools_config` parameter (ToolsConfig). Use `tools_config.tool_result_max_chars` if available, else fallback to 5000. Update main.py wiring to pass `config.tools` to CompressionEngine.
- **Verification**: Unit test that tool_result_max_chars is read from ToolsConfig when available.

## Task 6: Stream interruption partial content preservation (gap-06)

- **Gap IDs**: gap-06
- **Files**: `myagent/agent/engine.py`
- **Approach**: In _react_loop's try/except around LLM streaming, collect TextChunk contents into `streamed_text_buffer`. On exception, yield a TruncatedText event (new event type or reuse existing) with the partial content and a "Stream interrupted. Continue? (y/n)" prompt. When the user says yes, the next turn's history should include the partial content.
- **Verification**: Unit test that interrupted streams preserve partial content.

## Task 7: Sub-agent transcript persistence (gap-07)

- **Gap IDs**: gap-07
- **Files**: `myagent/subagent/worker.py`, `myagent/subagent/pool.py`, `myagent/context/persistence.py`
- **Approach**: Modify SubAgentPool.spawn() to accept a session_store and session parameter. Create `subagents/sub-NNN/` directory in the session. Modify SubAgentWorker to collect messages/tool calls during execution. After worker completes, write transcript.json and transcript.md. Add a `save_subagent_transcript()` method to SessionStore.
- **Verification**: Unit test that sub-agent transcripts are written to disk.

## Task 8: Connect SubAgentPool state to StatusBar (gap-08)

- **Gap IDs**: gap-08
- **Files**: `myagent/cli/status.py`, `myagent/subagent/pool.py`, `myagent/agent/engine.py`
- **Approach**: Add a callback mechanism to SubAgentPool -- an optional `on_state_change` callback that fires on spawn/complete/fail. Wire it to StatusBar.update() in main.py. StatusBar polls or receives events showing active sub-agent count and details (name, status icon, progress).
- **Verification**: Visual verification that status bar shows sub-agent activity.

## Task 9: Daily log rotation with date-stamped filenames (gap-09)

- **Gap IDs**: gap-09
- **Files**: `myagent/logging/logger.py`
- **Approach**: Replace RotatingFileHandler with a combined approach: use TimedRotatingFileHandler for daily rotation (midnight rollover) producing filenames like `myagent-2026-07-03.jsonl`. Add size-based rotation check on top -- if a single day's file exceeds max_size_mb, rotate with numbered suffix (`.1`, `.2`).
- **Verification**: Verify log filenames include date stamps.

## Task 10: LLM prompt logging to .prompts/ directory (gap-10)

- **Gap IDs**: gap-10
- **Files**: `myagent/llm/provider.py`, `myagent/logging/logger.py`
- **Approach**: In LLMProvider._complete_with_model(), when config's llm_prompts is True and log level is DEBUG, write full request messages to `.prompts/<timestamp>-<session>-request.json` and full response to `.prompts/<timestamp>-<session>-response.json`. Read logging config from provider init. Add a static helper method in LogManager.
- **Verification**: Verify prompt files are written when config enables them.

## Task 11: Fix Anthropic model names in spawn_subagent (gap-11)

- **Gap IDs**: gap-11
- **Files**: `myagent/tools/builtin/agent_tools.py`
- **Approach**: Remove the `model` parameter entirely from SpawnSubagentTool, matching the design spec which does not include a model parameter for spawn_subagent. The sub-agent always inherits the parent model.
- **Verification**: Unit test that model param is removed from spawn_subagent schema.

## Task 12: Complete intent detection with correct/insert (gap-12)

- **Gap IDs**: gap-12
- **Files**: `myagent/agent/engine.py`
- **Approach**: Extend _detect_intent to detect all four intents: stop, correct, insert, continue. Add phrase lists for each. Return IntentSignal with the appropriate intent type. Handle correct/insert in the ReAct loop -- correct redirects with feedback, insert adds a new sub-task.
- **Verification**: Unit test that all four intents are detected correctly.

## Task 13: 120s timeout for AskUserQuestion (gap-13)

- **Gap IDs**: gap-13
- **Files**: `myagent/agent/engine.py`, `myagent/cli/repl.py`
- **Approach**: When AskUserQuestion is yielded, start a 120s timer in the REPL. If the user does not respond within 120s, auto-decide and continue. The REPL should use asyncio.wait_for with a timeout on the next input prompt.
- **Verification**: Unit test that timeout triggers auto-decision.

## Task 14: Persist tool call results to disk (gap-14)

- **Gap IDs**: gap-14
- **Files**: `myagent/agent/engine.py`
- **Approach**: In _execute_tool(), after getting the result, call session_store.save_tool_call(session, ToolCallRecord(...)) if session_store is available. The session object needs to be passed through or the session ID used.
- **Verification**: Verify tool call JSON files are written in the tools/ directory.

## Task 15: Persist compression summaries to disk (gap-15)

- **Gap IDs**: gap-15
- **Files**: `myagent/context/compression.py`, `myagent/context/persistence.py`
- **Approach**: Add a `session_dir` parameter to CompressionEngine.compact(). After layer-3 summarization, write the summary to `summaries/compact-NNN.md` in the session directory. Track compact counters.
- **Verification**: Verify summary files are written to disk after compression.

## Task 16: Implement session end flow (gap-16)

- **Gap IDs**: gap-16
- **Files**: `myagent/agent/session.py`
- **Approach**: In end_session(), use Rich Console to check permission changes (via PermissionController.get_session_changes()), prompt user to persist, display memory summary from memory_store.get_session_writes().
- **Verification**: Visual verification that session end shows prompts.

## Task 17: Track runtime rule changes in PermissionController (gap-17)

- **Gap IDs**: gap-17
- **Files**: `myagent/permissions/controller.py`
- **Approach**: Add `_runtime_changes` list. In apply_runtime_rule(), append each applied rule with timestamp. Add `get_session_changes()` method returning the list.
- **Verification**: Unit test that rule changes are tracked.

## Task 18: Enhance DreamEngine with transcript scanning (gap-18)

- **Gap IDs**: gap-18, gap-35
- **Files**: `myagent/memory/dream.py`
- **Approach**: Enhance run() to accept optional session_store. Scan recent session transcripts for patterns. Use LLM (if available) to identify new conventions, detect repeated corrections (>= 2 times), find contradictory memories, and merge/update. Add dream analysis narrative to the log output.
- **Verification**: Unit test for pattern extraction from transcripts.

## Task 19: Built-in SKILL.md files (gap-19) -- ALREADY FIXED

- **Status**: SKIPPED (already implemented)
- The `myagent/skills/builtin/` directory exists with 6 skill subdirectories, each containing a proper SKILL.md file with frontmatter (name, description) and instructions. No further action needed.

## Task 20: Non-stop message consumption in SubAgentWorker (gap-20)

- **Gap IDs**: gap-20
- **Files**: `myagent/subagent/worker.py`, `myagent/subagent/pool.py`
- **Approach**: At the start of each ReAct iteration in SubAgentWorker, check if a non-stop message is pending on the handle. If so, inject it as a user message. The handle's send_message() sets the message; worker reads and clears it at iteration start.
- **Verification**: Unit test that messages are consumed mid-execution.

## Task 21: Enforce speculative_exploration config (gap-21)

- **Gap IDs**: gap-21
- **Files**: `myagent/tools/builtin/agent_tools.py`, `myagent/subagent/pool.py`
- **Approach**: In SpawnSubagentTool.execute(), check if we are in goal mode (via context.config). In non-goal mode, if context.config.subagents.speculative_exploration is False, force background=False. Pass the config to spawn().
- **Verification**: Unit test that speculative exploration is gated by config.

## Task 22: Rich-formatted session listing (gap-22)

- **Gap IDs**: gap-22
- **Files**: `myagent/cli/main.py`
- **Approach**: Replace plain print() with Rich Table showing: session_id, status icon (checkmark/goal achieved, clipboard/in progress, dash/no goal), first message, duration, token count, goal achievement status.
- **Verification**: Visual verification of formatted session list output.

## Task 23: Ctrl+C exit confirmation in REPL (gap-23)

- **Gap IDs**: gap-23
- **Files**: `myagent/cli/repl.py`
- **Approach**: Remove the key binding that makes Ctrl+C clear buffer. Instead, catch KeyboardInterrupt in the REPL loop and prompt "Exit? (y/n)". On 'y', run full session end flow. On 'n', continue. Also handle Ctrl+C on idle (empty input).
- **Verification**: Manual verification of Ctrl+C behavior.

## Task 24: Add tool_calls_count to LLM response log (gap-24)

- **Gap IDs**: gap-24
- **Files**: `myagent/llm/provider.py`
- **Approach**: Count tool calls yielded from _stream_response(). Include tool_calls_count in the response log entry's extra dict.
- **Verification**: Verify response log includes tool_calls_count field.

## Task 25: Session-scoped memory cache (gap-27)

- **Gap IDs**: gap-27
- **Files**: `myagent/context/builder.py`
- **Approach**: Add a `_memory_cache` dict to ContextBuilder. Load memories once at session start. Store cached by session_id. Add `invalidate_cache()` method. Use cache on subsequent builds unless cache is stale.
- **Verification**: Unit test that memories are cached and only loaded once.

## Task 26: Config value validation (gap-28)

- **Gap IDs**: gap-28
- **Files**: `myagent/config/loader.py`
- **Approach**: Add a `validate()` method to ConfigLoader that checks threshold ranges [0.0, 1.0], positive concurrency, positive timing values, enum values. Log warnings for unreasonable values. Call validate() after load().
- **Verification**: Unit test that invalid config values produce warnings/errors.

## Task 27: Session export generation (gap-29)

- **Gap IDs**: gap-29
- **Files**: `myagent/context/persistence.py`
- **Approach**: Rewrite export_session() to generate a self-contained Markdown export file. Include full conversation, tool calls, metadata, and summaries. Write to a dedicated export file rather than returning existing paths.
- **Verification**: Verify exported Markdown includes full content.

## Task 28: ResultConsumed state (gap-30)

- **Gap IDs**: gap-30
- **Files**: `myagent/subagent/pool.py`
- **Approach**: Add RESULT_CONSUMED to AgentStatus enum. In SubAgentHandle.wait(), after returning the result, set status to RESULT_CONSUMED.
- **Verification**: Unit test that handle transitions to RESULT_CONSUMED after wait().

## Task 29: Pass project context to sub-agents (gap-31)

- **Gap IDs**: gap-31
- **Files**: `myagent/subagent/worker.py`, `myagent/subagent/pool.py`, `myagent/tools/builtin/agent_tools.py`
- **Approach**: Add optional `project_context` parameter to SubAgentWorker. Include project type, git status, directory structure in system prompt. Pass from pool.spawn() through from SpawnSubagentTool via ToolContext.
- **Verification**: Verify sub-agent system prompt includes project context.

## Task 30: Rebuild context with active_skill after skill_invoke (gap-32)

- **Gap IDs**: gap-32
- **Files**: `myagent/agent/engine.py`
- **Approach**: After detecting skill_invoke and loading the skill, set a flag `self._active_skill = skill_name`. At the start of the next ReAct iteration, re-build the context (calling context_builder.build() with active_skill) if the flag is set. Append the new system prompt to messages.
- **Verification**: Unit test that context is rebuilt with skill content after skill_invoke.

## Task 31: Retry progress to status bar (gap-33)

- **Gap IDs**: gap-33
- **Files**: `myagent/llm/provider.py`, `myagent/cli/status.py`
- **Approach**: Add an optional `retry_callback` parameter to LLMProvider. Accept a callable (or None). Call it with (attempt, max_retries, delay) during retry loops. Wire from main.py to StatusBar.update().
- **Verification**: Verify status bar updates during retries.

## Task 32: MCP transport abstraction (gap-34)

- **Gap IDs**: gap-34
- **Files**: `myagent/tools/mcp/client.py`
- **Approach**: Define an MCPTransport Protocol/ABC with connect(), send(), receive(), close(). Implement StdioTransport. Make MCPClient accept an optional transport parameter (defaulting to StdioTransport). Add a placeholder SSE transport class.
- **Verification**: Unit test that transport abstraction works with stdio transport.

## Task 33: Various remaining fixes (gaps 25)

- **Gap IDs**: gap-25
- **Files**: `myagent/agent/engine.py`
- **Approach**: Covered in Task 1 -- 50% manual compact notification is part of the _react_loop compact trigger implementation.
- **Verification**: Covered by Task 1 verification.

## Task 34: Remaining intents handling in ReAct loop (gap-12 continued)

This is part of Task 12. The "correct" and "insert" intent detection needs to be handled in the _react_loop's intent handling code path.

---

## Implementation Order

1. Fix gap-11, gap-05 (simple config/schema fixes) -- low risk, quick wins
2. Fix gap-17, gap-24, gap-28, gap-30 (tracking/logging/validation additions) -- additive changes
3. Fix gap-02 (permissions wiring) -- critical path change
4. Fix gap-01, gap-25 (compression trigger) -- critical path change
5. Fix gap-06, gap-12, gap-13, gap-32 (intent detection, error handling, skill context)
6. Fix gap-03, gap-34 (MCP startup + transport abstraction)
7. Fix gap-04 (dream auto-trigger)
8. Fix gap-07, gap-14, gap-15 (persistence)
9. Fix gap-08, gap-33 (status bar connections)
10. Fix gap-09, gap-10 (logging)
11. Fix gap-16, gap-23 (session flow)
12. Fix gap-18, gap-35 (dream enhancement)
13. Fix gap-20, gap-21, gap-31 (sub-agent improvements)
14. Fix gap-22, gap-29 (session display/export)
15. Fix gap-27 (memory cache)
