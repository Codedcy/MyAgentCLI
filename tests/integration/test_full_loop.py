"""Integration test: full ReAct loop with mocked LLM."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.agent.engine import AgentEngine
from myagent.agent.goal import GoalTracker
from myagent.agent.project import ProjectContext
from myagent.context.builder import ContextBuilder, LLMRequest
from myagent.llm.provider import Done, TextDelta
from myagent.permissions.controller import PermissionController
from myagent.tools.registry import ToolRegistry
from myagent.tools.builtin.file_tools import ReadTool


class TestFullLoop:
    @pytest.mark.asyncio
    async def test_text_response(self):
        """Full loop: user input → stream text → Done."""
        # Setup mocks
        llm = AsyncMock()
        async def mock_complete(*args, **kwargs):
            yield TextDelta(content="Hello, I can help!")
            yield Done(stop_reason="end_turn")

        llm.complete = mock_complete

        registry = ToolRegistry()
        registry.register(ReadTool())

        builder = MagicMock()
        builder.build = AsyncMock(return_value=LLMRequest(
            system="test system", messages=[{"role": "user", "content": "Hi"}], tools=[]
        ))

        engine = AgentEngine(
            llm=llm,
            tool_registry=registry,
            permissions=PermissionController(),
            context_builder=builder,
            goal_tracker=GoalTracker(),
            project_context=ProjectContext(),
            project_dir=Path("/tmp/test"),
        )

        session = MagicMock()
        session.get_recent_messages = MagicMock(return_value=[])
        session.id = "test-session"

        events = []
        async for event in engine.run("Hi", session):
            events.append(event)

        from myagent.agent.engine import TextChunk as TC, Done as D
        assert any(isinstance(e, TC) for e in events)
        assert any(isinstance(e, D) for e in events)

    @pytest.mark.asyncio
    async def test_echo_without_llm(self):
        """Without LLM, engine echoes input."""
        builder = MagicMock()
        builder.build = AsyncMock(return_value=LLMRequest(
            system="test", messages=[], tools=[]
        ))

        engine = AgentEngine(
            llm=None,
            context_builder=builder,
            project_context=ProjectContext(),
        )

        session = MagicMock()
        session.get_recent_messages = MagicMock(return_value=[])

        events = []
        async for event in engine.run("test input", session):
            events.append(event)

        from myagent.agent.engine import Done
        assert any(isinstance(e, Done) for e in events)
