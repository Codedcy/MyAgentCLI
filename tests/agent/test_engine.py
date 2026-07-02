"""Tests for AgentEngine ReAct loop."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.agent.engine import AgentEngine, Done, TextChunk
from myagent.context.builder import ContextBuilder, LLMRequest


class TestAgentEngine:
    @pytest.mark.asyncio
    async def test_run_without_llm_echoes(self):
        """Without LLM, engine should echo back input."""
        builder = MagicMock()
        builder.build = AsyncMock(return_value=LLMRequest(
            system="test", messages=[], tools=[]
        ))

        engine = AgentEngine(
            llm=None,
            context_builder=builder,
            project_context=MagicMock(),
        )

        session = MagicMock()
        session.get_recent_messages = MagicMock(return_value=[])

        events = []
        async for event in engine.run("Hello", session):
            events.append(event)

        assert len(events) >= 1
        assert isinstance(events[-1], Done)

    @pytest.mark.asyncio
    async def test_run_returns_events(self):
        builder = MagicMock()
        builder.build = AsyncMock(return_value=LLMRequest(
            system="test", messages=[], tools=[]
        ))

        engine = AgentEngine(
            llm=None,
            context_builder=builder,
            project_context=MagicMock(),
        )

        session = MagicMock()
        session.get_recent_messages = MagicMock(return_value=[])

        events = []
        async for event in engine.run("test input", session):
            events.append(event)

        assert any(isinstance(e, TextChunk) for e in events)
        assert any(isinstance(e, Done) for e in events)
