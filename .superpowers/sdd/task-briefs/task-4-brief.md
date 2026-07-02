# Task 4: Sub-agent Worker Real ReAct Loop + Pool Fixes

**Files:**
- Modify: `myagent/subagent/worker.py`
- Modify: `myagent/subagent/pool.py`

**Interfaces:**
- Consumes: `LLMProvider`, `ToolRegistry` (passed via pool)
- Produces: `SubAgentWorker.run()` executes real ReAct loop; `SubAgentPool._run_background/foreground` use actual worker; `send_message` actually interrupts
- Fixes audit issues: #2 (worker stub), #12 (pool doesn't use worker), #46b (background semaphore bypass), #46c (send_message stub)

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies
- Use `logging.getLogger("myagent.subagent")` for logging
- Follow existing patterns
- Python 3.12+

## Steps

### Step 1: Fix worker.py — implement real ReAct loop

Replace `SubAgentWorker.run()` to execute a real ReAct loop:
- Accept `llm`, `tool_registry`, `interrupt_event` in constructor
- Loop up to 30 iterations
- Each iteration: stream LLM → collect text + tool calls → execute tools → append results → continue
- Check `interrupt_event` at top of each iteration
- If no LLM: return error message
- Sub-agents have: no L2 skills, no L4 memory, tool subset from spawn params, independent context
- On LLM error: log and return error string

### Step 2: Fix pool.py — wire real worker, fix semaphore, implement send_message

1. **`spawn()`**: Accept `llm` and `tool_registry` params. Create `asyncio.Event` for interrupts. Pass everything to `_run_background/foreground`.

2. **`_run_background()`**: Use `async with self._semaphore:` (FIX: currently bypasses semaphore). Create `SubAgentWorker`, call `worker.run()`, set result.

3. **`_run_foreground()`**: Same but caller awaits.

4. **`send_message()`**: Check if agent exists, if message is "stop" set `_interrupt_event`.

5. **`SubAgentHandle.send_message()`**: Store message, set interrupt event for "stop".

### Step 3: Run tests and commit

Run: `pytest tests/subagent/ -v`
Expected: PASS

```bash
git add myagent/subagent/worker.py myagent/subagent/pool.py tests/subagent/
git commit -m "fix(subagent): real worker ReAct loop, pool wiring, semaphore fix, send_message"
```
