---
date: 2026-07-03
round: 18
gaps_to_fix: 7
source_report: docs/gap-reports/2026-07-03-gap-round-18.md
---

# Fix Plan -- Round 18

## Summary
Fix 7 gaps across 7 files.

## Task 1: Add web_fetch_answer_model config and use it instead of primary model
- **Gap IDs**: GAP-18-01
- **Files**: `myagent/config/schema.py`, `myagent/tools/builtin/web_tools.py`
- **Approach**: Add `web_fetch_answer_model` field to ToolsConfig (default `"deepseek/deepseek-chat"` -- the fast non-reasoning model). Modify `WebFetchTool._llm_answer` to use this configured model instead of the primary model from context.config.model.
- **Verification**: Check that web_fetch uses the configured fast model, not the primary DeepSeek V4 Pro.

## Task 2: Add /export slash command to CommandDispatcher
- **Gap IDs**: GAP-18-02
- **Files**: `myagent/cli/commands.py`
- **Approach**: Register `/export markdown` and `/export json` commands in CommandDispatcher. The handler calls `ctx.session_manager.export_session()` with the current session ID. Add `session_manager` to `CommandContext`.
- **Verification**: Check /export markdown and /export json work from within the REPL.

## Task 3: Wire sub-agent retry progress from worker to status bar
- **Gap IDs**: GAP-18-03
- **Files**: `myagent/subagent/worker.py`, `myagent/subagent/pool.py`, `myagent/cli/main.py`
- **Approach**: Add `retry_callback` parameter to `SubAgentWorker.__init__` and `_stream_llm_with_retry`. The pool creates a callback that updates `SubAgentInfo.retry_count`/`max_retries`/`status="retrying"` and fires status change callbacks. The status bar already renders retrying state; it just needs to receive the data.
- **Verification**: Status bar shows sub-agent retrying state during LLM retries.

## Task 4: Emit startup log event with session_id
- **Gap IDs**: GAP-18-04
- **Files**: `myagent/logging/logger.py`, `myagent/cli/main.py`
- **Approach**: Extract the startup log emission into a separate `log_startup` class method on LogManager. Call LogManager.setup() early (infrastructure setup). Call LogManager.log_startup(session_id=...) after session creation when session_id is known. For resume paths, session_id is available immediately. For new sessions, call it from REPL after session creation.
- **Verification**: Startup event in log has session_id (not null).

## Task 5: Implement SSE transport for MCP
- **Gap IDs**: GAP-18-05
- **Files**: `myagent/tools/mcp/client.py`
- **Approach**: Implement `SSETransport` class implementing the `MCPTransport` protocol. The SSE transport connects to an HTTP SSE endpoint for MCP communication, following the MCP SSE transport specification (POST for client->server messages, SSE stream for server->client messages).
- **Verification**: SSETransport implements the MCPTransport protocol and can be used in tests.

## Task 6: Semantic summarization for Layer 2 compression
- **Gap IDs**: GAP-18-06
- **Files**: `myagent/context/compression.py`
- **Approach**: Modify `_layer2_summarize` to use LLM-based semantic summarization for large tool results instead of raw character truncation. The method should identify large tool result messages, send them to the LLM for summarization (in Non-think mode), and replace with a "semantic summary + file reference" pattern. Keep a lightweight character fallback if LLM is unavailable.
- **Verification**: Layer 2 compression produces semantic summaries, not just truncated text.

## Task 7: Restore goal on session resume
- **Gap IDs**: GAP-18-07
- **Files**: `myagent/cli/main.py`
- **Approach**: After resuming a session, check if `session.goal` is not None and call `goal_tracker.set_goal(session.goal)`.
- **Verification**: Upon resume, goal tracker contains the session's stored goal.
