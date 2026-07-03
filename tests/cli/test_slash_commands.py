"""Tests for slash commands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from myagent.cli.commands import CommandContext, CommandDispatcher
from myagent.memory.dream import DreamResult


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

    @pytest.mark.asyncio
    async def test_dream_command_passes_active_session_store(self, tmp_path):
        dispatcher = CommandDispatcher()
        session_store = object()
        dream_engine = SimpleNamespace(
            run=AsyncMock(return_value=DreamResult(log_path=tmp_path / "dream.md"))
        )
        ctx = CommandContext(
            dream_engine=dream_engine,
            session_manager=SimpleNamespace(session_store=session_store),
        )

        result = await dispatcher.dispatch("/dream", ctx)

        assert result.success
        dream_engine.run.assert_awaited_once_with(session_store=session_store)
