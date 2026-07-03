# Task 4 Report: Sub-agent Worker Real ReAct Loop + Pool Fixes

**Status: COMPLETE**
**Commit: ee1c950**

## Summary

Fixed all four issues identified in the sub-agent system:

1. **Worker stub (#2)**: `SubAgentWorker.run()` was a placeholder returning a hardcoded string. Now executes a real ReAct loop with LLM streaming, tool execution, interrupt checking, and error handling.

2. **Pool doesn't use worker (#12)**: `_run_background()` used `asyncio.sleep(0.01)` instead of creating and running a `SubAgentWorker`. Now instantiates the worker and calls `worker.run()`.

3. **Background semaphore bypass (#46b)**: `_run_background()` did not acquire the concurrency semaphore. Now wraps worker execution in `async with self._semaphore:`.

4. **send_message stub (#46c)**: `SubAgentHandle.send_message()` was a no-op. Now stores the message and sets the interrupt event when the message is "stop".

## Changes

### Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `myagent/subagent/worker.py` | +180 / -14 | Real ReAct loop with streaming, tool execution, interrupt, error handling |
| `myagent/subagent/pool.py` | +165 / -16 | Wire worker, semaphore guard, interrupt events, send_message implementation |
| `tests/subagent/test_worker.py` | +140 / -6 | 8 tests: no-llm error, streaming, tool execution, interrupt, max iterations, tool subset, empty tools |
| `tests/subagent/test_pool.py` | +150 / -28 | 11 tests: foreground/background spawn, semaphore limit, send_message interrupt, cap exceeded, handle tests |
| `tests/integration/test_subagent_flow.py` | +17 / -4 | Updated to pass mock LLM |

### Design Decisions

- **Constructor extensibility**: Added `llm`, `tool_registry`, `interrupt_event`, and `tool_context` as optional keyword arguments to `SubAgentWorker.__init__` to maintain backward compatibility for code that constructs workers without these params.

- **Pool-level defaults**: `SubAgentPool.__init__` accepts `llm`, `tool_registry`, `tool_context` as optional params. `spawn()` also accepts them as per-invocation overrides (falling back to pool-level). This allows the engine's `_summarize_via_subagent` to continue working without changes — it calls `pool.spawn()` without these params, and they come from the pool's stored defaults (set during pool construction in `main.py`).

- **Semaphore placement**: `_run_background()` acquires the semaphore. `_run_foreground()` is a thin wrapper that delegates to `_run_background()`. This ensures both foreground and background spawns go through the same semaphore path without double-acquiring.

- **Event classification**: Uses the same duck-typing pattern as `AgentEngine._classify_event()` — tries `isinstance` against provider types first, falls back to attribute checking for test doubles.

### ReAct Loop Details

- Max 30 iterations (vs 50 for main agent — sub-agents have smaller scope)
- Checks `interrupt_event.is_set()` at the top of each iteration
- Streams LLM response, collects text and tool calls
- Executes tools via `tool_registry.get()` with a minimal `ToolContext`
- Tool results are appended to messages and the loop continues
- Returns concatenated text when LLM finishes without tool calls
- No skills, no memory, no goal tracking (sub-agent design spec)

### Pool Concurrency Fix

- `_run_background()` now wraps the entire worker lifecycle in `async with self._semaphore:`
- `test_semaphore_limits_concurrency` verifies that with `max_concurrent=1`, two background tasks serialize correctly

### send_message Implementation

- `SubAgentHandle.send_message(msg)`: stores message in `_message`, sets `_interrupt_event` for "stop"
- `SubAgentPool.send_message(agent_id, message)`: routes to handle
- `shutdown()`: now also calls `handle._interrupt_event.set()` for running agents
- `test_send_message_stop_interrupts_worker`: verifies the interrupt path end-to-end

## Test Results

```
pytest tests/ -v
200 passed, 3 warnings in 13.70s
```

- 8 new/updated worker tests
- 11 new/updated pool tests
- All existing tests continue to pass
