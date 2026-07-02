"""Tests for slash commands."""

import pytest

from myagent.cli.commands import CommandContext, CommandDispatcher


class TestCommandDispatcher:
    @pytest.mark.asyncio
    async def test_mode_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext(config=None)
        result = await dispatcher.dispatch("/mode think-max", ctx)
        assert "Think Max" in result.output

    @pytest.mark.asyncio
    async def test_goal_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext(goal_tracker=None)
        result = await dispatcher.dispatch("/goal", ctx)
        assert "None" in result.output

    @pytest.mark.asyncio
    async def test_goal_set(self):
        from myagent.agent.goal import GoalTracker
        gt = GoalTracker()
        dispatcher = CommandDispatcher()
        ctx = CommandContext(goal_tracker=gt)
        result = await dispatcher.dispatch("/goal Fix all bugs", ctx)
        assert "Fix all bugs" in result.output
        assert gt.get_goal() == "Fix all bugs"

    @pytest.mark.asyncio
    async def test_exit(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext()
        result = await dispatcher.dispatch("/exit", ctx)
        assert "Goodbye" in result.output

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext()
        result = await dispatcher.dispatch("/nonexistent", ctx)
        assert not result.success

    @pytest.mark.asyncio
    async def test_not_a_command(self):
        dispatcher = CommandDispatcher()
        ctx = CommandContext()
        result = await dispatcher.dispatch("not a slash command", ctx)
        assert not result.success
