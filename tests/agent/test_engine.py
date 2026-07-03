"""Tests for AgentEngine ReAct loop."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.agent.engine import (
    AgentEngine,
    AskUserQuestion,
    Done,
    Error,
    IntentSignal,
    TextChunk,
    ThinkingChunk,
    ToolCallEnd,
    ToolCallStart,
)
from myagent.agent.goal import GoalCheckResult
from myagent.context.builder import ContextBuilder, LLMRequest
from myagent.tools.base import ToolResult


class FakeTextDelta:
    def __init__(self, content):
        self.content = content


class FakeThinkingDelta:
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


class FakeUsage:
    prompt_tokens = 100
    completion_tokens = 50
    total_tokens = 150


def _async_gen(items):
    async def gen():
        for item in items:
            yield item

    return gen()


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


@pytest.mark.asyncio
async def test_react_loop_iterates_multiple_turns():
    """After executing tool calls, the loop should call LLM again with results."""
    gen1 = _async_gen([FakeToolCall("read", "call-1", {"file_path": "x.py"}), FakeDone()])
    gen2 = _async_gen([FakeTextDelta("File contents: hello"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1, gen2])

    tool = MagicMock()
    tool.execute = AsyncMock(return_value=ToolResult(output="hello"))
    registry = MagicMock()
    registry.get = MagicMock(return_value=tool)

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(
        llm=llm,
        tool_registry=registry,
        context_builder=builder,
    )
    session = MagicMock()
    session.get_recent_messages.return_value = []
    session.id = "test"

    events = [e async for e in engine.run("read x.py", session)]
    tool_call_ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(tool_call_ends) == 1
    texts = [e for e in events if isinstance(e, TextChunk)]
    assert len(texts) == 1
    assert texts[0].content == "File contents: hello"
    assert isinstance(events[-1], (Done, Error))


@pytest.mark.asyncio
async def test_react_loop_yields_ask_user_question():
    """When LLM returns text that is a question + no tool calls + done, yield AskUserQuestion."""
    gen1 = _async_gen([FakeTextDelta("Should I use pytest or unittest for testing?"), FakeDone()])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1])

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, context_builder=builder)
    session = MagicMock()
    session.get_recent_messages.return_value = []
    events = [e async for e in engine.run("test", session)]
    questions = [e for e in events if isinstance(e, AskUserQuestion)]
    assert len(questions) >= 1


@pytest.mark.asyncio
async def test_goal_not_achieved_reenters_loop():
    """When goal check fails, engine feeds remaining_work and continues."""
    gen1 = _async_gen([FakeTextDelta("Done with part 1"), FakeDone(FakeUsage())])
    gen2 = _async_gen([FakeTextDelta("Done with part 2"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1, gen2])

    goal_tracker = MagicMock()
    goal_tracker.get_goal.return_value = "fix all bugs"
    goal_tracker.check_goal = AsyncMock()
    goal_tracker.check_goal.side_effect = [
        GoalCheckResult(achieved=False, reasoning="not yet", remaining_work="fix remaining bugs"),
        GoalCheckResult(achieved=True, reasoning="all fixed"),
    ]

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, goal_tracker=goal_tracker, context_builder=builder)
    session = MagicMock()
    session.get_recent_messages.return_value = []
    session.goal = "fix all bugs"
    session.id = "test"

    events = [e async for e in engine.run("continue", session)]
    texts = [e for e in events if isinstance(e, TextChunk)]
    assert len(texts) == 2
    assert goal_tracker.check_goal.call_count == 2


@pytest.mark.asyncio
async def test_tool_params_cannot_bypass_permission_checks():
    """Model-provided params must not skip centralized permission checks."""
    tool = MagicMock()
    tool.execute = AsyncMock(return_value=ToolResult(output="should not run"))
    registry = MagicMock()
    registry.get = MagicMock(return_value=tool)

    permissions = MagicMock()
    permissions.check.return_value = SimpleNamespace(name="DENY")

    engine = AgentEngine(
        llm=None,
        tool_registry=registry,
        permissions=permissions,
    )
    session = MagicMock()
    session.id = "test"

    tc = FakeToolCall(
        "bash",
        "call-1",
        {"command": "echo unsafe", "dangerouslyDisableSandbox": True},
    )
    result = await engine._execute_tool(tc, session)

    permissions.check.assert_called_once()
    tool.execute.assert_not_called()
    assert result.error is not None
    assert "Permission denied" in result.error
