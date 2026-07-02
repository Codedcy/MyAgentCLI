# Task 2: Fix Goal Tracker — LLM-driven evaluation instead of always-True stub

**Files:**
- Modify: `myagent/agent/goal.py`
- Test: `tests/agent/test_goal.py` (create if not existing)

**Interfaces:**
- Consumes: `LLMProvider` (for evaluation call)
- Produces: `GoalTracker.check_goal()` returns real `GoalCheckResult` with LLM evaluation
- Fixes audit issue: #10

## Global Constraints

- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies unless absolutely required
- All modules must use `logging.getLogger("myagent.<module>")` per CLAUDE.md
- Follow existing patterns: dataclass configs, Protocol tools, async throughout
- DRY, YAGNI, TDD — write test first, then implementation
- Python 3.12+

## Steps

### Step 1: Write tests

Create/modify `tests/agent/test_goal.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from myagent.agent.goal import GoalTracker, GoalCheckResult


@pytest.mark.asyncio
async def test_check_goal_no_llm_conservative_fallback():
    """Without LLM, check_goal returns NOT achieved (conservative)."""
    tracker = GoalTracker()
    tracker.set_goal("Add login feature")
    result = await tracker.check_goal(MagicMock(), [])
    assert result.achieved is False
    assert "No LLM" in result.reasoning

@pytest.mark.asyncio
async def test_check_goal_no_goal_returns_false():
    tracker = GoalTracker()
    result = await tracker.check_goal(MagicMock(), [])
    assert result.achieved is False
    assert result.reasoning == "No goal set"

@pytest.mark.asyncio
async def test_set_clear_goal():
    tracker = GoalTracker()
    tracker.set_goal("Test goal")
    assert tracker.get_goal() == "Test goal"
    tracker.clear_goal()
    assert tracker.get_goal() is None

@pytest.mark.asyncio
async def test_check_goal_with_llm_parses_json_response():
    """With LLM returning JSON, check_goal parses the response."""
    llm = AsyncMock()
    class FakeTextDelta:
        def __init__(self, content): self.content = content
    async def fake_complete(messages=None, tools=None, thinking=None):
        yield FakeTextDelta('{"achieved": true, "reasoning": "all done", "remaining_work": null}')
    llm.complete = fake_complete

    tracker = GoalTracker(llm=llm)
    tracker.set_goal("Test feature")
    result = await tracker.check_goal(MagicMock(), [
        MagicMock(role="assistant", content="Done with tests")
    ])
    assert result.achieved is True
    assert result.reasoning == "all done"
```

Run: `pytest tests/agent/test_goal.py -v`
Expected: FAIL (current always-True stub returns achieved=True without LLM)

### Step 2: Implement LLM-driven goal checking

Replace `myagent/agent/goal.py`:

- `GoalTracker.__init__` accepts optional `llm` parameter
- `check_goal()` with no LLM: returns conservative "not achieved" (safer than falsely claiming done)
- `check_goal()` with LLM: calls `_llm_check()` which builds evaluation prompt, streams LLM response, extracts JSON, returns structured result
- `_llm_check()` builds history summary from last 20 messages, sends evaluation prompt to LLM in Non-think mode, parses JSON response
- On any error: returns conservative "not achieved" with error reasoning

### Step 3: Run tests

Run: `pytest tests/agent/test_goal.py -v`
Expected: All PASS

### Step 4: Commit

```bash
git add myagent/agent/goal.py tests/agent/test_goal.py
git commit -m "fix(goal): LLM-driven goal evaluation instead of always-True stub"
```
