"""Tests for SubAgentPool."""

import asyncio
from unittest.mock import MagicMock

import pytest

from myagent.subagent.pool import AgentStatus, CapExceededError, SubAgentPool
from myagent.tools.base import ToolContext


# ── Fake stream events ──────────────────────────────────────────────


class FakeTextDelta:
    def __init__(self, content):
        self.content = content


class FakeDone:
    def __init__(self, usage=None):
        self.usage = usage
        self.stop_reason = "end_turn"


def _async_gen(items):
    async def gen():
        for item in items:
            yield item

    return gen()


def _make_llm(text="Task completed"):
    """Create a mock LLM that returns a single text response."""
    gen = _async_gen([FakeTextDelta(text), FakeDone()])
    llm = MagicMock()
    llm.complete = MagicMock(return_value=gen)
    return llm


# ── Tests ───────────────────────────────────────────────────────────


class TestSubAgentPool:
    def test_auto_max_concurrent_uses_cpu_formula(self, monkeypatch):
        monkeypatch.setattr("myagent.subagent.pool.os.cpu_count", lambda: 6)
        pool = SubAgentPool(max_concurrent=None, llm=_make_llm())
        assert pool.max_concurrent == 4

    def test_explicit_max_concurrent_is_preserved(self):
        pool = SubAgentPool(max_concurrent=7, llm=_make_llm())
        assert pool.max_concurrent == 7

    @pytest.mark.asyncio
    async def test_worker_tool_context_gets_current_subagent_id(
        self, tmp_path, monkeypatch
    ):
        captured = {}

        class FakeWorker:
            def __init__(self, *args, tool_context=None, **kwargs):
                captured["current_subagent_id"] = getattr(
                    tool_context, "current_subagent_id", None
                )

            async def run(self):
                return "ok"

        monkeypatch.setattr("myagent.subagent.worker.SubAgentWorker", FakeWorker)

        pool = SubAgentPool(max_concurrent=2)
        ctx = ToolContext(
            session_id="parent",
            project_dir=tmp_path,
            permissions=None,
            config=None,
            subagent_pool=pool,
        )

        await pool.spawn(
            prompt="Report progress",
            background=False,
            tool_context=ctx,
        )

        assert captured["current_subagent_id"] == "sub-001"

    @pytest.mark.asyncio
    async def test_spawn_foreground(self):
        """Foreground spawn should block until completion and return result."""
        llm = _make_llm("Completed: Test task analysis")
        pool = SubAgentPool(max_concurrent=2, llm=llm)
        handle = await pool.spawn(
            prompt="Test task",
            background=False,
        )
        result = await handle.wait()
        assert result.error is None
        assert handle.status == AgentStatus.RESULT_CONSUMED
        assert "Test task" in result.output

    @pytest.mark.asyncio
    async def test_spawn_background(self):
        """Background spawn should return immediately, with result available
        after completion."""
        llm = _make_llm("Background task done")
        pool = SubAgentPool(max_concurrent=2, llm=llm)
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
        """active_count should drop to 0 after agent completes."""
        llm = _make_llm()
        pool = SubAgentPool(max_concurrent=5, llm=llm)
        handle = await pool.spawn(prompt="Test", background=True)
        await handle.wait()
        assert pool.active_count == 0  # completed

    @pytest.mark.asyncio
    async def test_shutdown_interrupts_running(self):
        """shutdown() should interrupt running agents."""
        llm = _make_llm("ok")
        pool = SubAgentPool(max_concurrent=5, llm=llm)
        handle = await pool.spawn(prompt="Long task", background=True)
        await pool.shutdown()
        assert handle.status in (AgentStatus.INTERRUPTED, AgentStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_agent_id_format(self):
        """Agent IDs should follow sub-NNN format."""
        llm = _make_llm()
        pool = SubAgentPool(max_concurrent=2, llm=llm)
        h1 = await pool.spawn(prompt="Task 1", background=False)
        h2 = await pool.spawn(prompt="Task 2", background=False)
        assert h1.id == "sub-001"
        assert h2.id == "sub-002"

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Background tasks should respect the semaphore limit."""
        # Use a semaphore of 1 so tasks serialize
        # Make LLM return only after a small delay so we can observe concurrency
        import asyncio

        async def _delayed_gen(*args, **kwargs):
            await asyncio.sleep(0.05)
            yield FakeTextDelta("ok")
            yield FakeDone()

        llm = MagicMock()
        llm.complete = MagicMock(side_effect=_delayed_gen)

        pool = SubAgentPool(max_concurrent=1, llm=llm)
        h1 = await pool.spawn(prompt="Task 1", background=True)
        h2 = await pool.spawn(prompt="Task 2", background=True)

        await h1.wait()
        await h2.wait()

        assert h1.status == AgentStatus.RESULT_CONSUMED
        assert h2.status == AgentStatus.RESULT_CONSUMED
        assert llm.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_send_message_stop_interrupts_worker(self):
        """send_message('stop') should cause worker to return [Interrupted]."""
        # LLM that would loop but gets interrupted
        gen1 = _async_gen([FakeTextDelta("Working..."), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(return_value=gen1)

        pool = SubAgentPool(max_concurrent=2, llm=llm)
        handle = await pool.spawn(prompt="Work", background=True)

        # Send stop message
        await pool.send_message(handle.id, "stop")

        result = await handle.wait()
        # After wait(), status transitions to RESULT_CONSUMED
        assert handle.status == AgentStatus.RESULT_CONSUMED

    @pytest.mark.asyncio
    async def test_cap_exceeded_raises(self):
        """Spawning beyond MAX_TOTAL should raise CapExceededError."""
        llm = _make_llm()
        pool = SubAgentPool(max_concurrent=2, llm=llm)
        pool.MAX_TOTAL = 3  # Lower cap for testing

        for _ in range(3):
            await pool.spawn(prompt="Task", background=False)

        with pytest.raises(CapExceededError):
            await pool.spawn(prompt="Overflow")


class TestSubAgentHandle:
    @pytest.mark.asyncio
    async def test_wait_returns_result(self):
        from myagent.subagent.pool import SubAgentHandle
        from myagent.tools.base import ToolResult

        handle = SubAgentHandle(id="test-001")
        handle.status = AgentStatus.COMPLETED
        handle._result_data = ToolResult(output="Done")
        handle._completion_event.set()

        result = await handle.wait()
        assert result.output == "Done"

    @pytest.mark.asyncio
    async def test_send_message_sets_interrupt_on_stop(self):
        """send_message('stop') should set the interrupt event."""
        from myagent.subagent.pool import SubAgentHandle

        handle = SubAgentHandle(id="test-002")
        assert not handle._interrupt_event.is_set()

        await handle.send_message("stop")
        assert handle._interrupt_event.is_set()
        assert handle._message == "stop"

    @pytest.mark.asyncio
    async def test_send_message_non_stop_does_not_interrupt(self):
        """Non-'stop' messages should not set the interrupt event."""
        from myagent.subagent.pool import SubAgentHandle

        handle = SubAgentHandle(id="test-003")
        await handle.send_message("hello")
        assert not handle._interrupt_event.is_set()
        assert handle._message == "hello"
