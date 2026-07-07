"""Tests for GoalTracker."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.agent.goal import GoalTracker


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
    assert tracker.get_goal_snapshot() == (None, 0)
    tracker.set_goal("Test goal")
    assert tracker.get_goal() == "Test goal"
    goal, version = tracker.get_goal_snapshot()
    assert goal == "Test goal"
    assert version == 1
    assert tracker.is_current_goal(goal, version) is True
    tracker.clear_goal()
    assert tracker.get_goal() is None
    assert tracker.is_current_goal(goal, version) is False
    assert tracker.get_goal_snapshot() == (None, 2)


@pytest.mark.asyncio
async def test_check_goal_with_llm_parses_json_response():
    """With LLM returning JSON, check_goal parses the response."""
    llm = AsyncMock()

    class FakeTextDelta:
        def __init__(self, content):
            self.content = content

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
