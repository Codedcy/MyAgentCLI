"""Tests for GoalTracker."""

import pytest

from myagent.agent.goal import GoalTracker


class TestGoalTracker:
    def test_set_and_get(self):
        gt = GoalTracker()
        gt.set_goal("Implement feature X")
        assert gt.get_goal() == "Implement feature X"

    def test_clear(self):
        gt = GoalTracker()
        gt.set_goal("Test")
        gt.clear_goal()
        assert gt.get_goal() is None

    @pytest.mark.asyncio
    async def test_check_goal_no_goal(self):
        gt = GoalTracker()
        result = await gt.check_goal(None, [])
        assert result.achieved is False

    @pytest.mark.asyncio
    async def test_check_goal_with_goal(self):
        gt = GoalTracker()
        gt.set_goal("Test goal")
        result = await gt.check_goal(None, [])
        assert result.achieved is True
