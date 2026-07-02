"""Tests for SubAgentWorker."""

import pytest

from myagent.subagent.worker import SubAgentWorker


class TestSubAgentWorker:
    @pytest.mark.asyncio
    async def test_run_returns_prompt_summary(self):
        worker = SubAgentWorker(prompt="Review auth module", tools=["read", "grep"])
        result = await worker.run()
        assert "Review auth module" in result
