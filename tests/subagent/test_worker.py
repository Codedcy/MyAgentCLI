"""Tests for SubAgentWorker."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.subagent.worker import SubAgentWorker


# ── Fake stream events (matching the duck-typing fallback) ──────────


class FakeTextDelta:
    def __init__(self, content):
        self.content = content


class FakeToolCall:
    def __init__(self, name, call_id="call-1", params=None):
        self.name = name
        self.id = call_id
        self.params = params or {}


class FakeDone:
    def __init__(self, usage=None):
        self.usage = usage
        self.stop_reason = "end_turn"


def _async_gen(items):
    async def gen():
        for item in items:
            yield item

    return gen()


# ── Tests ───────────────────────────────────────────────────────────


class TestSubAgentWorker:
    @pytest.mark.asyncio
    async def test_run_without_llm_returns_error(self):
        """Without LLM, worker should return an error message."""
        worker = SubAgentWorker(prompt="Review auth module", tools=["read", "grep"])
        result = await worker.run()
        assert "Error" in result
        assert "LLM" in result

    @pytest.mark.asyncio
    async def test_run_with_llm_streams_text(self):
        """Worker should stream LLM text and return the final content."""
        gen1 = _async_gen([FakeTextDelta("Analysis complete."), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(return_value=gen1)

        worker = SubAgentWorker(
            prompt="Review auth module",
            tools=["read", "grep"],
            llm=llm,
        )
        result = await worker.run()
        assert "Analysis complete" in result
        llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_executes_tool_calls_and_continues(self):
        """Worker should execute tool calls from LLM, feed results back,
        and continue the loop."""
        gen1 = _async_gen([
            FakeToolCall("read", "call-1", {"file_path": "x.py"}),
            FakeDone(),
        ])
        gen2 = _async_gen([FakeTextDelta("Got file contents"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=[gen1, gen2])

        from myagent.tools.base import ToolResult

        tool = MagicMock()
        tool.execute = AsyncMock(return_value=ToolResult(output="file content"))
        registry = MagicMock()
        registry.get = MagicMock(return_value=tool)

        worker = SubAgentWorker(
            prompt="Read x.py",
            tools=["read"],
            llm=llm,
            tool_registry=registry,
        )
        result = await worker.run()
        assert "Got file contents" in result
        assert llm.complete.call_count == 2
        tool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_interrupt_stops_immediately(self):
        """When interrupt_event is set before run, worker should stop
        at the first iteration without calling LLM."""
        import asyncio

        interrupt = asyncio.Event()
        interrupt.set()

        llm = MagicMock()
        worker = SubAgentWorker(
            prompt="Do something",
            llm=llm,
            interrupt_event=interrupt,
        )
        result = await worker.run()
        assert "[Interrupted]" in result
        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_llm_error_returns_error_string(self):
        """Worker should catch LLM exceptions and return an error string."""
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=Exception("API down"))

        worker = SubAgentWorker(
            prompt="Do something",
            llm=llm,
        )
        result = await worker.run()
        assert "Error" in result
        assert "API down" in result

    @pytest.mark.asyncio
    async def test_run_max_iterations(self):
        """Worker should stop after MAX_ITERATIONS even if LLM keeps
        emitting tool calls."""
        call_count = SubAgentWorker.MAX_ITERATIONS + 2
        fakes = [[FakeToolCall("read", f"call-{i}", {"file_path": "x.py"}), FakeDone()]
                  for i in range(call_count)]
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=[_async_gen(f) for f in fakes])

        from myagent.tools.base import ToolResult

        tool = MagicMock()
        tool.execute = AsyncMock(return_value=ToolResult(output="ok"))
        registry = MagicMock()
        registry.get = MagicMock(return_value=tool)

        worker = SubAgentWorker(
            prompt="Read many files",
            tools=["read"],
            llm=llm,
            tool_registry=registry,
        )
        result = await worker.run()
        assert "max iterations" in result.lower()
        assert llm.complete.call_count == SubAgentWorker.MAX_ITERATIONS

    @pytest.mark.asyncio
    async def test_run_tool_subset_filtering(self):
        """Worker should only provide schemas for the requested tool subset."""
        gen1 = _async_gen([FakeTextDelta("Done"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(return_value=gen1)

        registry = MagicMock()
        registry.get_schemas_for = MagicMock(return_value=[
            {"type": "function", "function": {"name": "read", "description": "", "parameters": {}}},
        ])

        worker = SubAgentWorker(
            prompt="Read something",
            tools=["read"],
            llm=llm,
            tool_registry=registry,
        )
        result = await worker.run()
        assert "Done" in result
        registry.get_schemas_for.assert_called_once_with(["read"])

    @pytest.mark.asyncio
    async def test_run_without_tools_passes_no_schemas(self):
        """When no tools are specified, no tool schemas should be sent to LLM."""
        gen1 = _async_gen([FakeTextDelta("Plain text response"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(return_value=gen1)

        worker = SubAgentWorker(
            prompt="Answer a question",
            llm=llm,
        )
        result = await worker.run()
        assert "Plain text response" in result
        # Verify tools=None was passed (or tools=[])
        call_kwargs = llm.complete.call_args.kwargs
        assert call_kwargs.get("tools") in (None, [])
