"""Tests for AgentEngine ReAct loop."""

import copy
import gc
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from myagent.agent.engine import (
    AgentEngine,
    AskUserQuestion,
    Done,
    Error,
    StatusUpdate,
    TextChunk,
    ToolCallEnd,
)
from myagent.agent.goal import GoalCheckResult, GoalTracker
from myagent.context.builder import LLMRequest
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
    def __init__(
        self,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    ):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


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
async def test_react_loop_captures_last_prompt_passed_to_llm():
    messages = [{"role": "user", "content": "inspect prompt"}]
    tools = [
        {
            "type": "function",
            "function": {"name": "read", "parameters": {"type": "object"}},
        }
    ]
    gen = _async_gen([FakeTextDelta("done"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.model = "deepseek-v4-pro"
    llm.token_count = MagicMock(return_value=42)
    captured_call_kwargs = {}

    def complete(**kwargs):
        captured_call_kwargs["messages"] = copy.deepcopy(kwargs["messages"])
        captured_call_kwargs["tools"] = copy.deepcopy(kwargs["tools"])
        captured_call_kwargs["thinking"] = kwargs["thinking"]
        return gen

    llm.complete = MagicMock(side_effect=complete)

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="system prompt", messages=messages, tools=tools
    ))

    engine = AgentEngine(
        llm=llm,
        context_builder=builder,
    )
    session = SimpleNamespace(id="test", get_recent_messages=lambda: [])

    events = [event async for event in engine.run("inspect", session)]

    assert any(isinstance(event, TextChunk) for event in events)
    capture = engine.get_last_prompt_capture()
    assert capture.model == "deepseek-v4-pro"
    assert capture.thinking == "Think High"
    assert capture.messages == captured_call_kwargs["messages"]
    assert capture.tools == captured_call_kwargs["tools"]
    assert capture.estimated_tokens == 42
    assert capture.messages[0] == {"role": "system", "content": "system prompt"}
    messages[0]["content"] = "mutated"
    tools[0]["function"]["name"] = "write"
    assert capture.messages[1]["content"] == "inspect prompt"
    assert capture.tools[0]["function"]["name"] == "read"


def test_prompt_token_estimation_skips_async_token_counter_without_warning(recwarn):
    llm = SimpleNamespace(token_count=AsyncMock(return_value=42))
    engine = AgentEngine(llm=llm)

    assert engine._estimate_prompt_tokens([{"role": "user", "content": "hello"}]) is None
    gc.collect()

    assert not [
        warning
        for warning in recwarn
        if "was never awaited" in str(warning.message)
    ]


@pytest.mark.asyncio
async def test_react_loop_default_has_no_iteration_cap():
    tool_generations = [
        _async_gen([
            FakeToolCall("read", f"call-{index}", {"file_path": f"{index}.txt"}),
            FakeDone(),
        ])
        for index in range(55)
    ]
    final_gen = _async_gen([
        FakeTextDelta("Finished after many tool steps"),
        FakeDone(FakeUsage()),
    ])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[*tool_generations, final_gen])

    tool = MagicMock()
    tool.execute = AsyncMock(return_value=ToolResult(output="ok"))
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

    events = [e async for e in engine.run("keep going", session)]

    assert llm.complete.call_count == 56
    assert any(
        isinstance(event, TextChunk)
        and event.content == "Finished after many tool steps"
        for event in events
    )
    assert not any(
        isinstance(event, Error) and "max iterations" in event.message
        for event in events
    )


@pytest.mark.asyncio
async def test_background_subagent_completion_reenters_react_loop():
    gen1 = _async_gen([
        FakeToolCall(
            "spawn_subagent",
            "call-1",
            {"prompt": "Draft PRD", "background": True},
        ),
        FakeDone(),
    ])
    gen2 = _async_gen([
        FakeTextDelta("I reviewed the sub-agent output and will continue."),
        FakeDone(FakeUsage()),
    ])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1, gen2])

    tool = MagicMock()
    tool.execute = AsyncMock(
        return_value=ToolResult(
            output="Sub-agent spawned: sub-001",
            metadata={"subagent_id": "sub-001", "background": True},
        )
    )
    registry = MagicMock()
    registry.get = MagicMock(return_value=tool)

    class FakeSubagentPool:
        def __init__(self):
            self._drained_outbound = False
            self._drained_completion = False

        def drain_outbound_messages(self):
            if self._drained_outbound:
                return []
            self._drained_outbound = True
            return []

        async def wait_for_completion_events(self, agent_ids, timeout=None):
            assert agent_ids == {"sub-001"}
            self._drained_completion = True
            return [
                {
                    "subagent_id": "sub-001",
                    "status": "completed",
                    "output": "PRD complete",
                    "transcript_path": "sessions/subagents/sub-001/transcript.md",
                }
            ]

        def drain_completion_events(self, agent_ids=None):
            return []

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    pool = FakeSubagentPool()
    engine = AgentEngine(
        llm=llm,
        tool_registry=registry,
        context_builder=builder,
        subagent_pool=pool,
    )
    session = SimpleNamespace(id="test", get_recent_messages=lambda: [])

    events = [e async for e in engine.run("create docs", session)]

    assert llm.complete.call_count == 2
    assert pool._drained_completion is True
    assert any(
        isinstance(event, TextChunk)
        and "reviewed the sub-agent output" in event.content
        for event in events
    )
    second_messages = llm.complete.call_args_list[1].kwargs["messages"]
    assert any(
        message["role"] == "user"
        and "PRD complete" in message["content"]
        and "Sub-agent sub-001 completed" in message["content"]
        for message in second_messages
    )


@pytest.mark.asyncio
async def test_background_subagent_completion_survives_until_next_turn():
    gen1 = _async_gen([
        FakeToolCall(
            "spawn_subagent",
            "call-1",
            {"prompt": "Draft PRD", "background": True},
        ),
        FakeDone(),
    ])
    gen2 = _async_gen([
        FakeTextDelta("PM is working; I will continue when it finishes."),
        FakeDone(FakeUsage()),
    ])
    gen3 = _async_gen([
        FakeTextDelta("I saw the PRD and will start architecture."),
        FakeDone(FakeUsage()),
    ])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1, gen2, gen3])

    tool = MagicMock()
    tool.execute = AsyncMock(
        return_value=ToolResult(
            output="Sub-agent spawned: sub-001",
            metadata={"subagent_id": "sub-001", "background": True},
        )
    )
    registry = MagicMock()
    registry.get = MagicMock(return_value=tool)

    class FakeSubagentPool:
        def __init__(self):
            self.completed = False

        def drain_outbound_messages(self):
            return []

        async def wait_for_completion_events(self, agent_ids, timeout=None):
            return []

        def drain_completion_events(self, agent_ids=None):
            if not self.completed:
                return []
            assert set(agent_ids) == {"sub-001"}
            self.completed = False
            return [
                {
                    "subagent_id": "sub-001",
                    "status": "completed",
                    "output": "PRD complete",
                }
            ]

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    pool = FakeSubagentPool()
    engine = AgentEngine(
        llm=llm,
        tool_registry=registry,
        context_builder=builder,
        subagent_pool=pool,
    )
    session = SimpleNamespace(id="test", get_recent_messages=lambda: [])

    first_events = [e async for e in engine.run("create docs", session)]
    assert any(
        isinstance(event, TextChunk)
        and "PM is working" in event.content
        for event in first_events
    )

    pool.completed = True
    second_events = [e async for e in engine.run("continue", session)]

    assert any(
        isinstance(event, TextChunk)
        and "start architecture" in event.content
        for event in second_events
    )
    third_messages = llm.complete.call_args_list[2].kwargs["messages"]
    assert any(
        message["role"] == "user"
        and "PRD complete" in message["content"]
        and "Sub-agent sub-001 completed" in message["content"]
        for message in third_messages
    )


@pytest.mark.asyncio
async def test_react_loop_yields_context_status_after_usage_estimate():
    gen1 = _async_gen([FakeTextDelta("Context-aware answer"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.complete = MagicMock(return_value=gen1)

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(
        llm=llm,
        context_builder=builder,
        compression=MagicMock(),
    )
    engine._estimate_context_usage = MagicMock(return_value=0.42)
    session = MagicMock()
    session.get_recent_messages.return_value = []

    events = [e async for e in engine.run("check context", session)]

    context_updates = [
        e for e in events
        if isinstance(e, StatusUpdate) and e.scope == "context"
    ]
    assert context_updates
    assert context_updates[0].data["context_usage"] == 0.42
    assert context_updates[0].data["context_window"] == 1_000_000
    assert isinstance(events[events.index(context_updates[0]) + 1], TextChunk)


@pytest.mark.asyncio
async def test_react_loop_yields_token_status_from_llm_done_usage():
    gen1 = _async_gen([FakeTextDelta("Token-aware answer"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.complete = MagicMock(return_value=gen1)

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, context_builder=builder)
    session = MagicMock()
    session.get_recent_messages.return_value = []

    events = [e async for e in engine.run("count tokens", session)]

    token_updates = [
        e for e in events
        if isinstance(e, StatusUpdate) and e.scope == "tokens"
    ]
    assert token_updates
    assert token_updates[0].data == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "turn_total": 150,
        "session_total": 150,
    }


@pytest.mark.asyncio
async def test_react_loop_accumulates_token_status_across_runs_for_same_session():
    gen1 = _async_gen([
        FakeTextDelta("First answer"),
        FakeDone(FakeUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)),
    ])
    gen2 = _async_gen([
        FakeTextDelta("Second answer"),
        FakeDone(FakeUsage(prompt_tokens=120, completion_tokens=80, total_tokens=200)),
    ])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1, gen2])

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, context_builder=builder)
    session = SimpleNamespace(
        id="test",
        total_tokens=0,
        get_recent_messages=lambda: [],
    )

    first_events = [e async for e in engine.run("first", session)]
    second_events = [e async for e in engine.run("second", session)]

    first_token_updates = [
        e for e in first_events
        if isinstance(e, StatusUpdate) and e.scope == "tokens"
    ]
    second_token_updates = [
        e for e in second_events
        if isinstance(e, StatusUpdate) and e.scope == "tokens"
    ]
    assert first_token_updates[-1].data["turn_total"] == 150
    assert first_token_updates[-1].data["session_total"] == 150
    assert second_token_updates[-1].data["prompt_tokens"] == 120
    assert second_token_updates[-1].data["completion_tokens"] == 80
    assert second_token_updates[-1].data["turn_total"] == 200
    assert second_token_updates[-1].data["session_total"] == 350
    assert session.total_tokens == 350


@pytest.mark.asyncio
async def test_react_loop_accumulates_token_status_across_multi_iteration_run():
    gen1 = _async_gen([
        FakeToolCall("read", "call-1", {"file_path": "x.py"}),
        FakeDone(FakeUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)),
    ])
    gen2 = _async_gen([
        FakeTextDelta("File contents processed"),
        FakeDone(FakeUsage(prompt_tokens=120, completion_tokens=80, total_tokens=200)),
    ])
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
    session = SimpleNamespace(
        id="test",
        total_tokens=0,
        get_recent_messages=lambda: [],
    )

    events = [e async for e in engine.run("read x.py", session)]

    token_updates = [
        e for e in events
        if isinstance(e, StatusUpdate) and e.scope == "tokens"
    ]
    assert [update.data["turn_total"] for update in token_updates] == [150, 200]
    assert [update.data["session_total"] for update in token_updates] == [150, 350]
    assert token_updates[-1].data["prompt_tokens"] == 120
    assert token_updates[-1].data["completion_tokens"] == 80
    assert session.total_tokens == 350


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
async def test_react_loop_yields_goal_status_when_checking_and_resolved():
    gen1 = _async_gen([FakeTextDelta("Done with part 1"), FakeDone(FakeUsage())])
    gen2 = _async_gen([FakeTextDelta("Done with part 2"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1, gen2])

    goal_tracker = MagicMock()
    goal_tracker.get_goal.return_value = "fix all bugs"
    goal_tracker.check_goal = AsyncMock()
    goal_tracker.check_goal.side_effect = [
        GoalCheckResult(
            achieved=False,
            reasoning="not yet",
            remaining_work="fix remaining bugs",
        ),
        GoalCheckResult(achieved=True, reasoning="all fixed"),
    ]

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, goal_tracker=goal_tracker, context_builder=builder)
    session = MagicMock()
    session.get_recent_messages.return_value = []
    session.id = "test"

    events = [e async for e in engine.run("continue", session)]

    goal_updates = [
        e for e in events
        if isinstance(e, StatusUpdate) and e.scope == "goal"
    ]
    assert [update.data["state"] for update in goal_updates] == [
        "checking",
        "open",
        "checking",
        "achieved",
    ]
    assert goal_updates[0].data["name"] == "fix all bugs"
    assert goal_updates[1].data["active"] is True
    assert goal_updates[1].data["achieved"] is False
    assert goal_updates[-1].data["achieved"] is True


@pytest.mark.asyncio
async def test_react_loop_yields_goal_status_when_goal_remains_open():
    gen1 = _async_gen([FakeTextDelta("Still working"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1])

    goal_tracker = MagicMock()
    goal_tracker.get_goal.return_value = "finish task"
    goal_tracker.check_goal = AsyncMock(
        return_value=GoalCheckResult(
            achieved=False,
            reasoning="not yet",
            remaining_work="more work",
        )
    )

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, goal_tracker=goal_tracker, context_builder=builder)
    engine.MAX_ITERATIONS = 1
    session = MagicMock()
    session.get_recent_messages.return_value = []

    events = [e async for e in engine.run("continue", session)]

    goal_updates = [
        e for e in events
        if isinstance(e, StatusUpdate) and e.scope == "goal"
    ]
    assert [update.data["state"] for update in goal_updates] == ["checking", "open"]
    assert goal_updates[-1].data["active"] is True
    assert goal_updates[-1].data["achieved"] is False


@pytest.mark.asyncio
async def test_react_loop_ignores_stale_goal_check_after_goal_changes():
    gen1 = _async_gen([FakeTextDelta("Done with old goal"), FakeDone(FakeUsage())])
    gen2 = _async_gen([FakeTextDelta("Done with new goal"), FakeDone(FakeUsage())])
    llm = MagicMock()
    llm.complete = MagicMock(side_effect=[gen1, gen2])

    goal_tracker = GoalTracker()
    goal_tracker.set_goal("old goal")
    checked_goals: list[str | None] = []

    async def check_goal(session, messages, goal=None):
        checked_goals.append(goal)
        if len(checked_goals) == 1:
            goal_tracker.set_goal("new goal")
            return GoalCheckResult(
                achieved=False,
                reasoning="old result",
                remaining_work="old work",
            )
        return GoalCheckResult(achieved=True, reasoning="new done")

    goal_tracker.check_goal = check_goal
    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, goal_tracker=goal_tracker, context_builder=builder)
    session = MagicMock()
    session.get_recent_messages.return_value = []
    session.id = "test"

    events = [e async for e in engine.run("continue", session)]

    goal_updates = [
        e for e in events
        if isinstance(e, StatusUpdate) and e.scope == "goal"
    ]
    assert checked_goals == ["old goal", "new goal"]
    assert [(update.data["name"], update.data["state"]) for update in goal_updates] == [
        ("old goal", "checking"),
        ("new goal", "checking"),
        ("new goal", "achieved"),
    ]


@pytest.mark.asyncio
async def test_react_loop_yields_health_status_when_llm_stream_errors():
    async def failing_stream():
        raise RuntimeError("stream boom")
        yield

    llm = MagicMock()
    llm.complete = MagicMock(return_value=failing_stream())

    builder = MagicMock()
    builder.build = AsyncMock(return_value=LLMRequest(
        system="test", messages=[], tools=[]
    ))

    engine = AgentEngine(llm=llm, context_builder=builder)
    session = MagicMock()
    session.get_recent_messages.return_value = []

    events = [e async for e in engine.run("fail", session)]

    health_updates = [
        e for e in events
        if isinstance(e, StatusUpdate) and e.scope == "health"
    ]
    assert health_updates
    assert health_updates[0].data["last_error"] == "stream boom"
    assert isinstance(events[-1], Error)


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
