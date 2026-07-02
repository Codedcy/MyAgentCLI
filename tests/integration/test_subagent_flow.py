"""Integration test: sub-agent spawn and completion."""

from unittest.mock import MagicMock

import pytest

from myagent.subagent.pool import SubAgentPool


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


class TestSubagentFlow:
    @pytest.mark.asyncio
    async def test_spawn_and_wait(self):
        """Spawn sub-agent and wait for result."""
        gen = _async_gen([FakeTextDelta("Completed: Analyze file X"), FakeDone()])
        llm = MagicMock()
        llm.complete = MagicMock(return_value=gen)

        pool = SubAgentPool(max_concurrent=2, llm=llm)

        handle = await pool.spawn(
            prompt="Analyze file X",
            background=False,
        )

        result = await handle.wait()
        assert result.error is None
        assert "Analyze file X" in result.output
