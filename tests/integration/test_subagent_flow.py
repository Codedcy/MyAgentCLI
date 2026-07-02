"""Integration test: sub-agent spawn and completion."""

import pytest

from myagent.subagent.pool import SubAgentPool


class TestSubagentFlow:
    @pytest.mark.asyncio
    async def test_spawn_and_wait(self):
        """Spawn sub-agent and wait for result."""
        pool = SubAgentPool(max_concurrent=2)

        handle = await pool.spawn(
            prompt="Analyze file X",
            background=False,
        )

        result = await handle.wait()
        assert result.error is None
        assert "Analyze file X" in result.output
