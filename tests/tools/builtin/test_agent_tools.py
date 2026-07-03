"""Tests for agent tools: spawn_subagent, send_message."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.builtin.agent_tools import SendMessageTool, SpawnSubagentTool


def make_ctx(subagent_pool=None):
    return ToolContext(
        session_id="test",
        project_dir=MagicMock(),
        permissions=MagicMock(),
        config=MagicMock(),
        subagent_pool=subagent_pool,
    )


class TestSpawnSubagentTool:
    @pytest.mark.asyncio
    async def test_spawn_without_pool(self):
        tool = SpawnSubagentTool()
        ctx = make_ctx(subagent_pool=None)
        result = await tool.execute(
            {"prompt": "Review code"},
            ctx,
        )
        assert result.error is None
        assert "not available" in result.output

    @pytest.mark.asyncio
    async def test_spawn_with_pool(self):
        pool = AsyncMock()
        handle = MagicMock()
        handle.id = "sub-001"
        pool.spawn = AsyncMock(return_value=handle)

        tool = SpawnSubagentTool()
        ctx = make_ctx(subagent_pool=pool)
        result = await tool.execute(
            {"prompt": "Review code", "background": False},
            ctx,
        )
        assert result.error is None
        assert result.metadata["subagent_id"] == "sub-001"
        pool.spawn.assert_called_once()


class TestSendMessageTool:
    @pytest.mark.asyncio
    async def test_send_without_pool(self):
        tool = SendMessageTool()
        ctx = make_ctx(subagent_pool=None)
        result = await tool.execute(
            {"to": "sub-001", "message": "Hello"},
            ctx,
        )
        assert result.error is None
        assert "queued" in result.output

    @pytest.mark.asyncio
    async def test_send_with_pool(self):
        pool = AsyncMock()
        pool.send_message = AsyncMock(return_value=None)

        tool = SendMessageTool()
        ctx = make_ctx(subagent_pool=pool)
        result = await tool.execute(
            {"to": "sub-001", "message": "Focus on XSS"},
            ctx,
        )
        assert result.error is None
        pool.send_message.assert_called_once_with("sub-001", "Focus on XSS")

    @pytest.mark.asyncio
    async def test_send_to_main_uses_current_subagent_id_when_from_omitted(self):
        class Pool:
            def __init__(self):
                self.messages = []

            def send_to_main(self, subagent_id, message):
                self.messages.append((subagent_id, message))

        pool = Pool()
        tool = SendMessageTool()
        ctx = ToolContext(
            session_id="test",
            project_dir=MagicMock(),
            permissions=MagicMock(),
            config=MagicMock(),
            subagent_pool=pool,
            current_subagent_id="sub-042",
        )

        result = await tool.execute(
            {"to": "main", "message": "Need guidance"},
            ctx,
        )

        assert result.error is None
        assert pool.messages == [("sub-042", "Need guidance")]
