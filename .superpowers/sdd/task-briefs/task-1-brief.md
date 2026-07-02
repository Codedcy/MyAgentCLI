# Task 1: Fix ReAct Loop — true iterative execution with tool result feedback

**Files:**
- Modify: `myagent/agent/engine.py`
- Create: `tests/agent/test_engine.py` (if not existing, add tests to it)

**Interfaces:**
- Consumes: `LLMProvider.complete()`, `ToolRegistry`, `PermissionController`
- Produces: `AgentEngine.run()` yields full ReAct loop events; tool results fed back as messages for next LLM call
- Fixes audit issues: #1 (single-pass loop), #8 (AskUserQuestion/IntentSignal never yielded), #9 (large result truncated instead of sub-agent summarized), #11 (goal re-entry broken)

**Key fix:** The current `_react_loop()` does one LLM call + one tool execution batch then exits. It must loop — after executing tools, append results as messages and call LLM again until Done with no tool calls.

## Global Constraints

- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies unless absolutely required
- All modules must use `logging.getLogger("myagent.<module>")` per CLAUDE.md
- Follow existing patterns: dataclass configs, Protocol tools, async throughout
- Each task ends with `git commit` using conventional commit format
- DRY, YAGNI, TDD — write test first, then implementation

## Steps

### Step 1: Write failing test for multi-turn ReAct

Create file `tests/agent/test_engine.py` with these tests:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from myagent.agent.engine import (
    AgentEngine, TextChunk, ThinkingChunk, ToolCallStart, ToolCallEnd,
    Done, AskUserQuestion, IntentSignal, Error,
)
from myagent.tools.base import ToolResult
from myagent.agent.goal import GoalCheckResult


class FakeTextDelta:
    def __init__(self, content): self.content = content
class FakeThinkingDelta:
    def __init__(self, content): self.content = content
class FakeToolCall:
    def __init__(self, name, call_id="call-1", params=None):
        self.name = name; self.id = call_id; self.params = params or {}
class FakeDone:
    def __init__(self, usage=None): self.usage = usage
class FakeUsage:
    prompt_tokens = 100; completion_tokens = 50; total_tokens = 150


def _async_gen(items):
    async def gen():
        for item in items:
            yield item
    return gen()


@pytest.mark.asyncio
async def test_react_loop_iterates_multiple_turns():
    """After executing tool calls, the loop should call LLM again with results."""
    llm = AsyncMock()
    llm.complete.side_effect = [
        _async_gen([FakeToolCall("read", "call-1", {"file_path": "x.py"}), FakeDone()]),
        _async_gen([FakeTextDelta("File contents: hello"), FakeDone(FakeUsage())]),
    ]

    tool = MagicMock()
    tool.execute = AsyncMock(return_value=ToolResult(output="hello"))
    registry = MagicMock()
    registry.get = MagicMock(return_value=tool)

    engine = AgentEngine(llm=llm, tool_registry=registry)
    session = MagicMock()
    session.get_recent_messages.return_value = []
    session.id = "test"

    events = [e async for e in engine.run("read x.py", session)]
    tool_call_ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(tool_call_ends) == 1
    texts = [e for e in events if isinstance(e, TextChunk)]
    assert len(texts) == 1
    assert texts[0].content == "File contents: hello"
    assert isinstance(events[-1], (Done, Error))


@pytest.mark.asyncio
async def test_react_loop_yields_ask_user_question():
    """When LLM returns text that is a question + no tool calls + done, yield AskUserQuestion."""
    llm = AsyncMock()
    llm.complete.side_effect = [
        _async_gen([FakeTextDelta("Should I use pytest or unittest for testing?"), FakeDone()]),
    ]
    engine = AgentEngine(llm=llm)
    session = MagicMock()
    session.get_recent_messages.return_value = []
    events = [e async for e in engine.run("test", session)]
    questions = [e for e in events if isinstance(e, AskUserQuestion)]
    assert len(questions) >= 1


@pytest.mark.asyncio
async def test_goal_not_achieved_reenters_loop():
    """When goal check fails, engine feeds remaining_work and continues."""
    llm = AsyncMock()
    llm.complete.side_effect = [
        _async_gen([FakeTextDelta("Done with part 1"), FakeDone(FakeUsage())]),
        _async_gen([FakeTextDelta("Done with part 2"), FakeDone(FakeUsage())]),
    ]

    goal_tracker = MagicMock()
    goal_tracker.get_goal.return_value = "fix all bugs"
    goal_tracker.check_goal = AsyncMock()
    goal_tracker.check_goal.side_effect = [
        GoalCheckResult(achieved=False, reasoning="not yet", remaining_work="fix remaining bugs"),
        GoalCheckResult(achieved=True, reasoning="all fixed"),
    ]

    engine = AgentEngine(llm=llm, goal_tracker=goal_tracker)
    session = MagicMock()
    session.get_recent_messages.return_value = []
    session.goal = "fix all bugs"
    session.id = "test"

    events = [e async for e in engine.run("continue", session)]
    texts = [e for e in events if isinstance(e, TextChunk)]
    assert len(texts) == 2
    assert goal_tracker.check_goal.call_count == 2
```

Run: `pytest tests/agent/test_engine.py::test_react_loop_iterates_multiple_turns -v`
Expected: FAIL (loop stops after first tool execution)

### Step 2: Rewrite `_react_loop()` for true iterative loop

Replace the entire `_react_loop` method in `myagent/agent/engine.py`. Add `import json` at top.

The new `_react_loop` must:
1. Loop up to 50 iterations (safety limit)
2. Each iteration: stream LLM response → detect intent/question → execute tool calls → append results to messages → continue
3. On Done with no tool calls: break loop
4. After loop: check goal if set, re-enter with feedback if not achieved

New methods to add:
- `_continue_with_feedback()` — inject feedback and re-enter loop
- `_get_thinking_mode()` — extract from config
- `_detect_intent(text)` — detect stop/continue intent
- `_is_question(text)` — detect if response is a user question

### Step 3: Fix large result summarization

Modify `_execute_tool()`: instead of truncating to TOOL_RESULT_MAX_CHARS, try to spawn a sub-agent summarizer via `self.subagent_pool.spawn()`. Fall back to truncation if pool unavailable or summarization fails.

Add `_summarize_via_subagent()` method.

### Step 4: Run tests

Run: `pytest tests/agent/test_engine.py -v`
Expected: All tests PASS

### Step 5: Commit

```bash
git add myagent/agent/engine.py tests/agent/test_engine.py
git commit -m "fix(engine): true iterative ReAct loop with tool result feedback"
```
