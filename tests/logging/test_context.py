"""Tests for LogContext."""

import asyncio

import pytest

from myagent.logging.context import clear_context, get_context, set_context


class TestLogContext:
    def test_set_and_get(self):
        set_context(session_id="sess-001", project_name="myproject")
        ctx = get_context()
        assert ctx["session_id"] == "sess-001"
        assert ctx["project"] == "myproject"
        clear_context()

    def test_clear(self):
        set_context(session_id="sess-001", project_name="myproject")
        clear_context()
        ctx = get_context()
        assert ctx["session_id"] is None
        assert ctx["project"] is None

    def test_partial_set(self):
        set_context(session_id="sess-only")
        ctx = get_context()
        assert ctx["session_id"] == "sess-only"
        assert ctx["project"] is None
        clear_context()

    @pytest.mark.asyncio
    async def test_context_isolation(self):
        """Two concurrent tasks should see their own context."""
        results = {}

        async def task(name, session_id):
            set_context(session_id=session_id)
            await asyncio.sleep(0.01)
            results[name] = get_context()["session_id"]

        await asyncio.gather(
            task("a", "session-a"),
            task("b", "session-b"),
        )

        assert results["a"] == "session-a"
        assert results["b"] == "session-b"
