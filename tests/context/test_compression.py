"""Tests for CompressionEngine."""

import pytest

from myagent.context.builder import Message
from myagent.context.compression import CompressionEngine


class TestCompressionEngine:
    @pytest.mark.asyncio
    async def test_cleanup_removes_empty_tool_results(self):
        from myagent.config.schema import CompressionConfig

        config = CompressionConfig(primary_threshold=0.5, minimum_messages=0)
        engine = CompressionEngine(config=config)

        messages = [
            Message(role="user", content="test"),
            Message(role="tool", content="", tool_call_id="1", name="read"),
            Message(role="assistant", content="done"),
        ]

        result = await engine.compact(messages, 0.8)
        assert len(result.messages) == 2

    @pytest.mark.asyncio
    async def test_no_compact_below_minimum(self):
        from myagent.config.schema import CompressionConfig

        config = CompressionConfig(minimum_messages=10)
        engine = CompressionEngine(config=config)

        messages = [Message(role="user", content="test")]
        result = await engine.compact(messages, 0.8)
        assert len(result.messages) == 1  # unchanged

    @pytest.mark.asyncio
    async def test_summarize_large_results(self):
        from myagent.config.schema import CompressionConfig

        config = CompressionConfig(primary_threshold=0.5, minimum_messages=0)
        engine = CompressionEngine(config=config)
        engine.config.tool_result_max_chars = 100

        large_content = "x" * 5000
        # Put large tool result early (before protection window of 10 messages)
        messages = [Message(role="tool", content=large_content)]
        for i in range(12):
            messages.append(Message(role="user", content=f"msg{i}"))

        result = await engine.compact(messages, 0.8)
        # First message (index 0) should be summarized since 0 < 13-10=3
        assert "[Summarized:" in result.messages[0].content
