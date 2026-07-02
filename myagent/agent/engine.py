"""Agent engine — the core ReAct loop.

Think → Decision → Execute → Observe, repeated until Done.

Design doc reference: §二 核心 Agent 循环
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from myagent.tools.base import ToolContext, ToolResult


# ── Events emitted by the engine ────────────────────────────────


@dataclass
class TextChunk:
    content: str


@dataclass
class ThinkingChunk:
    content: str


@dataclass
class ToolCallStart:
    name: str
    call_id: str


@dataclass
class ToolCallEnd:
    call_id: str
    result: ToolResult


@dataclass
class AskUserQuestion:
    question: str
    options: list[str] | None = None


@dataclass
class Done:
    usage: object | None = None


@dataclass
class Error:
    message: str


@dataclass
class Interrupted:
    pass


@dataclass
class IntentSignal:
    intent: str  # "stop" | "correct" | "insert" | "continue"


AgentEvent = (
    TextChunk
    | ThinkingChunk
    | ToolCallStart
    | ToolCallEnd
    | AskUserQuestion
    | Done
    | Error
    | Interrupted
    | IntentSignal
)


# ── Engine ──────────────────────────────────────────────────────


class AgentEngine:
    TOOL_RESULT_MAX_CHARS = 5000

    def __init__(
        self,
        llm=None,
        tool_registry=None,
        permissions=None,
        subagent_pool=None,
        context_builder=None,
        compression=None,
        session_store=None,
        skill_registry=None,
        goal_tracker=None,
        project_context=None,
        config=None,
        project_dir: Path | None = None,
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.permissions = permissions
        self.subagent_pool = subagent_pool
        self.context_builder = context_builder
        self.compression = compression
        self.session_store = session_store
        self.skill_registry = skill_registry
        self.goal_tracker = goal_tracker
        self.project_context = project_context
        self.config = config
        self.project_dir = project_dir or Path.cwd()

    async def run(self, user_input: str, session) -> AsyncIterator[AgentEvent]:
        """Execute one turn of the ReAct loop."""

        # Build context
        history = session.get_recent_messages() if hasattr(session, 'get_recent_messages') else []
        request = await self.context_builder.build(
            current_input=user_input,
            history=history,
            project_context=self.project_context,
        )

        # ReAct loop (simplified — in production, full loop with LLM streaming)
        if self.llm:
            async for event in self._react_loop(request, session):
                yield event
        else:
            # No LLM available — echo back for testing
            yield TextChunk(content=f"Received: {user_input}")
            yield Done()

    async def _react_loop(self, request, session) -> AsyncIterator[AgentEvent]:
        """Core ReAct loop with LLM streaming."""
        tools_list = request.tools
        messages = request.to_api_format()

        tool_calls_in_turn = []
        has_done = False

        thinking_mode = "Think High"
        if self.config:
            thinking_mode = self.config.model.thinking

        try:
            async for event in self.llm.complete(
                messages=messages["messages"],
                tools=tools_list,
                thinking=thinking_mode,
            ):
                event_type = type(event).__name__
                if event_type == "TextDelta":
                    yield TextChunk(content=getattr(event, "content", ""))
                elif event_type == "ThinkingDelta":
                    yield ThinkingChunk(content=getattr(event, "content", ""))
                elif event_type == "ToolCall":
                    tool_calls_in_turn.append(event)
                elif event_type == "Done":
                    has_done = True

            # Execute tool calls
            for tc in tool_calls_in_turn:
                # Skill invocation
                if tc.name == "skill_invoke" and self.skill_registry:
                    skill = self.skill_registry.get(tc.params.get("skill", ""))
                    if skill:
                        yield ToolCallEnd(
                            call_id=tc.id,
                            result=ToolResult(output=f"Skill '{skill.name}' loaded."),
                        )
                        continue

                yield ToolCallStart(name=tc.name, call_id=tc.id)

                # Permission check
                result = await self._execute_tool(tc, session)
                yield ToolCallEnd(call_id=tc.id, result=result)

            if has_done:
                goal = self.goal_tracker.get_goal() if self.goal_tracker else None
                if goal and hasattr(session, 'goal') and session.goal:
                    goal_check = await self.goal_tracker.check_goal(session, history)
                    if not goal_check.achieved:
                        yield TextChunk(content=f"\nGoal not yet achieved: {goal_check.remaining_work}")
                        return

                yield Done()

        except Exception as e:
            yield Error(message=str(e))

    async def _execute_tool(self, tc, session) -> ToolResult:
        tool = self.tool_registry.get(tc.name) if self.tool_registry else None
        if not tool:
            return ToolResult(error=f"Unknown tool: {tc.name}")

        try:
            ctx = ToolContext(
                session_id=session.id if hasattr(session, 'id') else "unknown",
                project_dir=self.project_dir,
                permissions=self.permissions,
                config=self.config,
                subagent_pool=self.subagent_pool,
                working_dir=self.project_dir,
            )
            result = await tool.execute(tc.params, ctx)

            # Summarize large results
            if len(result.output) > self.TOOL_RESULT_MAX_CHARS:
                result = ToolResult(
                    output=f"[Summary of {len(result.output)} chars]\n{result.output[:self.TOOL_RESULT_MAX_CHARS]}",
                    error=result.error,
                    metadata=result.metadata,
                )

            return result
        except Exception as e:
            return ToolResult(error=str(e))

    def _get_tool_level(self, tool_name: str) -> int:
        from myagent.permissions.controller import TOOL_LEVEL_MAP
        return TOOL_LEVEL_MAP.get(tool_name, 3)
