"""Tests for LLM provider wrapper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from myagent.llm.provider import (
    Done,
    LLMError,
    LLMProvider,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    Usage,
)


class TestUsage:
    def test_usage_creation(self):
        u = Usage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        assert u.prompt_tokens == 1000
        assert u.completion_tokens == 500
        assert u.total_tokens == 1500


class TestLLMEvents:
    def test_text_delta(self):
        e = TextDelta(content="Hello")
        assert e.content == "Hello"

    def test_thinking_delta(self):
        e = ThinkingDelta(content="I should check the file first")
        assert e.content == "I should check the file first"

    def test_tool_call(self):
        tc = ToolCall(id="call_1", name="read", params={"file_path": "/tmp/test"})
        assert tc.id == "call_1"
        assert tc.name == "read"
        assert tc.params == {"file_path": "/tmp/test"}

    def test_done(self):
        d = Done(stop_reason="end_turn", usage=Usage(100, 50, 150))
        assert d.stop_reason == "end_turn"
        assert d.usage.total_tokens == 150


class TestLLMError:
    def test_retryable_error(self):
        e = LLMError(code="rate_limit", message="Rate limited", retryable=True)
        assert e.retryable is True

    def test_fatal_error(self):
        e = LLMError(code="auth_error", message="Invalid API key", retryable=False)
        assert e.retryable is False


class TestLLMProvider:
    @pytest.mark.asyncio
    async def test_stream_text(self):
        """Test streaming text chunks."""
        mock_response = AsyncMock()
        mock_response.__aiter__.return_value = [
            MagicMock(
                choices=[
                    MagicMock(
                        delta=MagicMock(content="Hello", reasoning_content=None)
                    )
                ],
                usage=None,
            ),
            MagicMock(
                choices=[
                    MagicMock(
                        delta=MagicMock(content=" world", reasoning_content=None)
                    )
                ],
                usage=None,
            ),
            MagicMock(
                choices=[MagicMock(delta=MagicMock(content="", reasoning_content=None))],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
        ]

        provider = LLMProvider.__new__(LLMProvider)
        # Mock litellm.acompletion
        with patch("litellm.acompletion", return_value=mock_response):
            provider.model = "deepseek/deepseek-v4-pro"

            events = []
            async for event in provider.complete(
                messages=[{"role": "user", "content": "Hi"}],
                tools=None,
                thinking="Think High",
            ):
                events.append(event)

        texts = [e for e in events if isinstance(e, TextDelta)]
        assert len(texts) == 2
        assert texts[0].content == "Hello"
        assert texts[1].content == " world"

        done_events = [e for e in events if isinstance(e, Done)]
        assert len(done_events) == 1
        assert done_events[0].usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_stream_with_thinking(self):
        """Test streaming with reasoning_content (thinking)."""
        mock_response = AsyncMock()
        mock_response.__aiter__.return_value = [
            MagicMock(
                choices=[
                    MagicMock(
                        delta=MagicMock(
                            reasoning_content="Let me think...",
                            content=None,
                        )
                    )
                ],
                usage=None,
            ),
            MagicMock(
                choices=[
                    MagicMock(
                        delta=MagicMock(
                            reasoning_content=None,
                            content="Here is the answer",
                        )
                    )
                ],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
        ]

        provider = LLMProvider.__new__(LLMProvider)
        with patch("litellm.acompletion", return_value=mock_response):
            provider.model = "deepseek/deepseek-v4-pro"

            events = []
            async for event in provider.complete(
                messages=[{"role": "user", "content": "Why?"}],
                tools=None,
                thinking="Think High",
            ):
                events.append(event)

        thinking_events = [e for e in events if isinstance(e, ThinkingDelta)]
        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert len(thinking_events) == 1
        assert thinking_events[0].content == "Let me think..."
        assert len(text_events) == 1
        assert text_events[0].content == "Here is the answer"

    @pytest.mark.asyncio
    async def test_stream_with_tool_call(self):
        """Test tool call response."""
        from unittest.mock import PropertyMock

        mock_response = AsyncMock()
        func_mock = MagicMock()
        type(func_mock).name = PropertyMock(return_value="read")
        type(func_mock).arguments = PropertyMock(
            return_value='{"file_path": "/tmp/test"}'
        )

        tc_delta = MagicMock()
        type(tc_delta).index = PropertyMock(return_value=0)
        type(tc_delta).id = PropertyMock(return_value="call_abc")
        type(tc_delta).function = PropertyMock(return_value=func_mock)

        delta_mock = MagicMock()
        type(delta_mock).content = PropertyMock(return_value=None)
        type(delta_mock).tool_calls = PropertyMock(return_value=[tc_delta])

        choice_mock = MagicMock()
        type(choice_mock).delta = PropertyMock(return_value=delta_mock)

        usage_mock = MagicMock()
        type(usage_mock).prompt_tokens = PropertyMock(return_value=10)
        type(usage_mock).completion_tokens = PropertyMock(return_value=5)
        type(usage_mock).total_tokens = PropertyMock(return_value=15)

        chunk_mock = MagicMock()
        type(chunk_mock).choices = PropertyMock(return_value=[choice_mock])
        type(chunk_mock).usage = PropertyMock(return_value=usage_mock)

        mock_response.__aiter__.return_value = [chunk_mock]

        provider = LLMProvider.__new__(LLMProvider)
        with patch("litellm.acompletion", return_value=mock_response):
            provider.model = "deepseek/deepseek-v4-pro"

            events = []
            async for event in provider.complete(
                messages=[{"role": "user", "content": "Read test file"}],
                tools=[{"type": "function", "function": {"name": "read", "parameters": {}}}],
                thinking="Think High",
            ):
                events.append(event)

        tool_calls = [e for e in events if isinstance(e, ToolCall)]
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "read"
        assert tool_calls[0].params == {"file_path": "/tmp/test"}

    def test_thinking_mode_mapping(self):
        """Test thinking mode parameter mapping."""
        provider = LLMProvider.__new__(LLMProvider)
        provider.model = "deepseek/deepseek-v4-pro"

        # Think High → enabled (default)
        assert provider._build_thinking_param("Think High") == {"type": "enabled"}

        # Think Max → enabled with budget
        assert provider._build_thinking_param("Think Max") == {
            "type": "enabled",
            "budget_tokens": 32000,
        }

        # Non-think → disabled
        assert provider._build_thinking_param("Non-think") == {"type": "disabled"}

    def test_token_count_fallback(self):
        """Test token count with fallback estimate."""
        provider = LLMProvider.__new__(LLMProvider)
        messages = [{"role": "user", "content": "Hello world " * 100}]

        count = provider.token_count(messages)
        # Fallback: len(json) // 4
        assert count > 0
