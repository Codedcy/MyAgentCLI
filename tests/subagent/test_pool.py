"""Tests for SubAgentPool."""

import pytest

from myagent.subagent.pool import AgentStatus, CapExceededError, SubAgentPool


class TestSubAgentPool:
    @pytest.mark.asyncio
    async def test_spawn_foreground(self):
        pool = SubAgentPool(max_concurrent=2)
        handle = await pool.spawn(
            prompt="Test task",
            background=False,
        )
        result = await handle.wait()
        assert result.error is None
        assert handle.status == AgentStatus.COMPLETED
        assert "Test task" in result.output

    @pytest.mark.asyncio
    async def test_spawn_background(self):
        pool = SubAgentPool(max_concurrent=2)
        handle = await pool.spawn(
            prompt="Background task",
            background=True,
        )
        # Should return immediately
        assert handle.status == AgentStatus.RUNNING
        # Wait for completion
        result = await handle.wait()
        assert result.error is None

    @pytest.mark.asyncio
    async def test_active_count(self):
        pool = SubAgentPool(max_concurrent=5)
        handle = await pool.spawn(prompt="Test", background=True)
        # After spawn, one agent is running
        await handle.wait()
        assert pool.active_count == 0  # completed

    @pytest.mark.asyncio
    async def test_shutdown_interrupts_running(self):
        pool = SubAgentPool(max_concurrent=5)
        handle = await pool.spawn(prompt="Long task", background=True)
        await pool.shutdown()
        assert handle.status in (AgentStatus.INTERRUPTED, AgentStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_agent_id_format(self):
        pool = SubAgentPool(max_concurrent=2)
        h1 = await pool.spawn(prompt="Task 1", background=False)
        h2 = await pool.spawn(prompt="Task 2", background=False)
        assert h1.id == "sub-001"
        assert h2.id == "sub-002"


class TestSubAgentHandle:
    @pytest.mark.asyncio
    async def test_wait_returns_result(self):
        from myagent.subagent.pool import SubAgentHandle
        import asyncio

        handle = SubAgentHandle(id="test-001")
        handle.status = AgentStatus.COMPLETED
        handle._result_data = __import__("myagent.tools.base", fromlist=["ToolResult"]).ToolResult(
            output="Done"
        )
        handle._completion_event.set()

        result = await handle.wait()
        assert result.output == "Done"
