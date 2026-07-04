"""Tests for SubAgentWorker."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.permissions.controller import PermissionResult
from myagent.subagent.worker import SubAgentWorker
from myagent.tools.base import ToolContext, ToolResult


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
    async def test_run_denies_tool_call_without_executing_tool(self, tmp_path):
        gen1 = _async_gen([
            FakeToolCall("write", "call-1", {"file_path": "x.py", "content": "x"}),
            FakeDone(),
        ])
        gen2 = _async_gen([FakeTextDelta("Denied handled"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=[gen1, gen2])

        tool = MagicMock()
        tool.execute = AsyncMock(return_value=ToolResult(output="should not run"))
        registry = MagicMock()
        registry.get = MagicMock(return_value=tool)

        permissions = MagicMock()
        permissions.check.return_value = PermissionResult.DENY
        permissions.confirm = AsyncMock(return_value=True)
        ctx = ToolContext(
            session_id="subagent",
            project_dir=tmp_path,
            permissions=permissions,
            config=None,
        )

        worker = SubAgentWorker(
            prompt="Write x.py",
            tools=["write"],
            llm=llm,
            tool_registry=registry,
            tool_context=ctx,
        )

        result = await worker.run()

        assert "Denied handled" in result
        permissions.check.assert_called_once_with(
            "write",
            level=1,
            params={"file_path": "x.py", "content": "x"},
        )
        permissions.confirm.assert_not_called()
        tool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_logs_permission_deny_tool_contract(self, tmp_path, caplog):
        gen1 = _async_gen([
            FakeToolCall(
                "write",
                "call-1",
                {"file_path": "x.py", "content": "x" * 250},
            ),
            FakeDone(),
        ])
        gen2 = _async_gen([FakeTextDelta("Denied handled"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=[gen1, gen2])

        tool = MagicMock()
        tool.execute = AsyncMock(return_value=ToolResult(output="should not run"))
        registry = MagicMock()
        registry.get = MagicMock(return_value=tool)

        permissions = MagicMock()
        permissions.check.return_value = PermissionResult.DENY
        permissions.confirm = AsyncMock(return_value=True)
        ctx = ToolContext(
            session_id="subagent",
            project_dir=tmp_path,
            permissions=permissions,
            config=None,
        )

        caplog.set_level("INFO", logger="myagent.subagent")
        worker = SubAgentWorker(
            prompt="Write x.py",
            tools=["write"],
            llm=llm,
            tool_registry=registry,
            tool_context=ctx,
        )

        await worker.run()

        tool.execute.assert_not_called()
        tool_records = [
            record for record in caplog.records
            if getattr(record, "category", None) == "tool"
            and getattr(record, "tool_name", None) == "write"
        ]
        assert tool_records
        record = tool_records[-1]
        assert record.permission_result == "denied"
        assert len(record.params_summary) <= 200
        assert isinstance(record.duration_ms, float)
        assert record.result_size_chars == 0
        assert record.error == "Permission denied: write requires level 1 access."

    @pytest.mark.asyncio
    async def test_run_asks_permission_and_skips_tool_when_user_denies(self, tmp_path):
        gen1 = _async_gen([
            FakeToolCall("bash", "call-1", {"command": "pytest"}),
            FakeDone(),
        ])
        gen2 = _async_gen([FakeTextDelta("User denial handled"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=[gen1, gen2])

        tool = MagicMock()
        tool.execute = AsyncMock(return_value=ToolResult(output="should not run"))
        registry = MagicMock()
        registry.get = MagicMock(return_value=tool)

        permissions = MagicMock()
        permissions.check.return_value = PermissionResult.ASK
        permissions.confirm = AsyncMock(return_value=False)
        ctx = ToolContext(
            session_id="subagent",
            project_dir=tmp_path,
            permissions=permissions,
            config=None,
        )

        worker = SubAgentWorker(
            prompt="Run tests",
            tools=["bash"],
            llm=llm,
            tool_registry=registry,
            tool_context=ctx,
        )

        result = await worker.run()

        assert "User denial handled" in result
        permissions.check.assert_called_once_with(
            "bash",
            level=2,
            params={"command": "pytest"},
        )
        permissions.confirm.assert_awaited_once_with("bash", {"command": "pytest"})
        tool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_logs_user_deny_tool_contract(self, tmp_path, caplog):
        gen1 = _async_gen([
            FakeToolCall("bash", "call-1", {"command": "pytest"}),
            FakeDone(),
        ])
        gen2 = _async_gen([FakeTextDelta("User denial handled"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=[gen1, gen2])

        tool = MagicMock()
        tool.execute = AsyncMock(return_value=ToolResult(output="should not run"))
        registry = MagicMock()
        registry.get = MagicMock(return_value=tool)

        permissions = MagicMock()
        permissions.check.return_value = PermissionResult.ASK
        permissions.confirm = AsyncMock(return_value=False)
        ctx = ToolContext(
            session_id="subagent",
            project_dir=tmp_path,
            permissions=permissions,
            config=None,
        )

        caplog.set_level("INFO", logger="myagent.subagent")
        worker = SubAgentWorker(
            prompt="Run tests",
            tools=["bash"],
            llm=llm,
            tool_registry=registry,
            tool_context=ctx,
        )

        await worker.run()

        permissions.confirm.assert_awaited_once_with("bash", {"command": "pytest"})
        tool.execute.assert_not_called()
        tool_records = [
            record for record in caplog.records
            if getattr(record, "category", None) == "tool"
            and getattr(record, "tool_name", None) == "bash"
        ]
        assert tool_records
        record = tool_records[-1]
        assert record.permission_result == "denied"
        assert len(record.params_summary) <= 200
        assert isinstance(record.duration_ms, float)
        assert record.result_size_chars == 0
        assert record.error == "User denied permission for 'bash'."

    @pytest.mark.asyncio
    async def test_run_logs_successful_tool_execution_contract(
        self, tmp_path, caplog
    ):
        gen1 = _async_gen([
            FakeToolCall(
                "write",
                "call-1",
                {"file_path": "x.py", "content": "x" * 250},
            ),
            FakeDone(),
        ])
        gen2 = _async_gen([FakeTextDelta("Write complete"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(side_effect=[gen1, gen2])

        tool = MagicMock()
        tool.execute = AsyncMock(return_value=ToolResult(output="written"))
        registry = MagicMock()
        registry.get = MagicMock(return_value=tool)

        permissions = MagicMock()
        permissions.check.return_value = PermissionResult.ALLOW
        permissions.confirm = AsyncMock(return_value=True)
        ctx = ToolContext(
            session_id="subagent",
            project_dir=tmp_path,
            permissions=permissions,
            config=None,
        )

        caplog.set_level("INFO", logger="myagent.subagent")
        worker = SubAgentWorker(
            prompt="Write x.py",
            tools=["write"],
            llm=llm,
            tool_registry=registry,
            tool_context=ctx,
        )

        await worker.run()

        tool_records = [
            record for record in caplog.records
            if getattr(record, "category", None) == "tool"
            and getattr(record, "tool_name", None) == "write"
        ]
        assert tool_records
        record = tool_records[-1]
        assert record.permission_result == "allowed"
        assert len(record.params_summary) <= 200
        assert isinstance(record.duration_ms, float)
        assert record.result_size_chars == len("written")

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
