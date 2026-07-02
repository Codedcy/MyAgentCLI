"""Agent engine — the core ReAct loop.

Think → Decision → Execute → Observe, repeated until Done.

Design doc reference: §二 核心 Agent 循环
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from myagent.llm.provider import Done as LLMDone
from myagent.llm.provider import TextDelta as LLMTextDelta
from myagent.llm.provider import ThinkingDelta as LLMThinkingDelta
from myagent.llm.provider import ToolCall as LLMToolCall
from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.agent")


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

    # ── ReAct loop ──────────────────────────────────────────────────

    MAX_ITERATIONS = 50

    async def _react_loop(self, request, session) -> AsyncIterator[AgentEvent]:
        """Core ReAct loop with true iterative execution and tool result feedback.

        Each iteration:
        1. Stream LLM response, collecting text and tool calls.
        2. If tool calls present: execute them, append results to messages, loop again.
        3. If no tool calls: check intent / question / goal, then yield Done.
        4. If goal not achieved: inject feedback and loop again.
        """
        tools_list = request.tools
        api_format = request.to_api_format()
        messages = api_format["messages"]

        # Prepend system message (was lost in the old single-pass code)
        if api_format.get("system"):
            messages.insert(0, {"role": "system", "content": api_format["system"]})

        thinking_mode = self._get_thinking_mode()
        iteration = 0

        while iteration < self.MAX_ITERATIONS:
            iteration += 1
            logger.info("ReAct iteration %d", iteration, extra={"category": "agent"})

            text_buffer: list[str] = []
            tool_calls_in_turn: list = []

            # ── Stream LLM response ──────────────────────────────
            try:
                async for event in self.llm.complete(
                    messages=messages,
                    tools=tools_list,
                    thinking=thinking_mode,
                ):
                    kind = self._classify_event(event)
                    if kind == "text":
                        content = getattr(event, "content", "")
                        yield TextChunk(content=content)
                        text_buffer.append(content)
                    elif kind == "thinking":
                        yield ThinkingChunk(content=getattr(event, "content", ""))
                    elif kind == "tool_call":
                        tool_calls_in_turn.append(event)
                    # kind == "done": stream is concluding — fall through
            except Exception as e:
                logger.error(
                    "LLM error in iteration %d: %s",
                    iteration,
                    str(e),
                    extra={"category": "error", "component": "llm"},
                )
                yield Error(message=str(e))
                return

            # ── Execute tool calls and feed results back ─────────
            if tool_calls_in_turn:
                # Build assistant message with tool calls
                assistant_text = "".join(text_buffer) or None
                assistant_msg: dict = {"role": "assistant", "content": assistant_text}
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.params, ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls_in_turn
                ]
                messages.append(assistant_msg)

                # Execute each tool and append result messages
                for tc in tool_calls_in_turn:
                    # Skill invocation shortcut
                    if tc.name == "skill_invoke" and self.skill_registry:
                        skill = self.skill_registry.get(tc.params.get("skill", ""))
                        if skill:
                            skill_result = ToolResult(
                                output=f"Skill '{skill.name}' loaded."
                            )
                            yield ToolCallEnd(call_id=tc.id, result=skill_result)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": skill_result.output,
                            })
                            continue

                    yield ToolCallStart(name=tc.name, call_id=tc.id)
                    result = await self._execute_tool(tc, session)
                    yield ToolCallEnd(call_id=tc.id, result=result)

                    result_text = result.output if not result.error else f"Error: {result.error}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })

                # Loop again — LLM will see tool results in next iteration
                continue

            # ── No tool calls — text response complete ───────────
            full_text = "".join(text_buffer)

            # Record assistant response in message history
            if full_text:
                messages.append({"role": "assistant", "content": full_text})

            # Detect intent signals (stop / correct / insert / continue)
            intent = self._detect_intent(full_text)
            if intent and intent != "continue":
                yield IntentSignal(intent=intent)
                if intent == "stop":
                    yield Done()
                    return

            # Detect AskUserQuestion
            if self._is_question(full_text):
                yield AskUserQuestion(question=full_text)

            # ── Goal check ──────────────────────────────────────
            goal = self.goal_tracker.get_goal() if self.goal_tracker else None
            if goal and hasattr(session, "goal") and session.goal:
                try:
                    goal_check = await self.goal_tracker.check_goal(session, messages)
                except Exception as e:
                    logger.error(
                        "Goal check failed: %s", str(e),
                        extra={"category": "error", "component": "agent"},
                    )
                    yield Done()
                    return

                if not goal_check.achieved:
                    # Inject feedback and re-enter the loop
                    self._continue_with_feedback(goal_check, messages)
                    continue

            # Goal achieved (or no goal set) — we are done
            yield Done()
            return

        # Max iterations exhausted
        yield Error(
            message=f"ReAct loop reached max iterations ({self.MAX_ITERATIONS})"
        )

    # ── helpers ────────────────────────────────────────────────────

    def _classify_event(self, event) -> str:
        """Classify an LLM stream event by type.

        Uses isinstance against the provider types; falls back to duck-typing
        for test doubles (mock objects that have the right attributes).
        """
        if isinstance(event, LLMTextDelta):
            return "text"
        if isinstance(event, LLMThinkingDelta):
            return "thinking"
        if isinstance(event, LLMToolCall):
            return "tool_call"
        if isinstance(event, LLMDone):
            return "done"

        # Duck-typing fallback for test doubles
        if hasattr(event, "name") and hasattr(event, "params") and hasattr(event, "id"):
            return "tool_call"
        if hasattr(event, "content") and not hasattr(event, "name"):
            return "text"
        if hasattr(event, "stop_reason"):
            return "done"
        if "Done" in type(event).__name__:
            return "done"
        return "unknown"

    def _get_thinking_mode(self) -> str:
        """Extract thinking mode from config."""
        if self.config:
            return getattr(self.config.model, "thinking", "Think High")
        return "Think High"

    def _detect_intent(self, text: str) -> str | None:
        """Detect user intent signals in model response.

        Returns one of: "stop", "continue", or None.
        """
        if not text:
            return None
        text_lower = text.lower().strip()

        # Detect brief continue phrases (< 30 chars)
        if len(text) < 30:
            continue_phrases = [
                "continue", "go on", "继续", "proceed", "resume",
                "carry on", "keep going", "go ahead",
            ]
            if any(p in text_lower for p in continue_phrases):
                return "continue"

        stop_phrases = [
            "i'll stop",
            "stopping now",
            "task complete",
            "all done",
            "i am done",
        ]
        if any(p in text_lower for p in stop_phrases):
            return "stop"
        return None

    def _is_question(self, text: str) -> bool:
        """Detect if text appears to be a question to the user."""
        if not text or len(text) < 10:
            return False
        if "?" in text:
            return True
        text_lower = text.lower().strip()
        question_starters = (
            "should i",
            "would you",
            "do you",
            "can you",
            "could you",
            "which",
            "what",
            "how",
            "where",
            "when",
            "why",
            "who",
            "would you like",
            "is it",
            "are you",
        )
        for starter in question_starters:
            if text_lower.startswith(starter):
                return True
        return False

    def _continue_with_feedback(
        self, goal_check, messages: list[dict]
    ) -> None:
        """Inject goal-feedback message so the next LLM call sees it."""
        feedback = (
            f"Goal not yet achieved. {goal_check.remaining_work or goal_check.reasoning}\n"
            f"Please continue working to achieve the goal: "
            f"{self.goal_tracker.get_goal() if self.goal_tracker else 'complete the task'}"
        )
        messages.append({"role": "user", "content": feedback})

    # ── tool execution ─────────────────────────────────────────────

    async def _execute_tool(self, tc, session) -> ToolResult:
        tool = self.tool_registry.get(tc.name) if self.tool_registry else None
        if not tool:
            return ToolResult(error=f"Unknown tool: {tc.name}")

        try:
            ctx = ToolContext(
                session_id=session.id if hasattr(session, "id") else "unknown",
                project_dir=self.project_dir,
                permissions=self.permissions,
                config=self.config,
                subagent_pool=self.subagent_pool,
                working_dir=self.project_dir,
            )

            t0 = time.monotonic()
            result = await tool.execute(tc.params, ctx)
            duration_ms = (time.monotonic() - t0) * 1000

            # Log successful tool execution
            params_str = str(tc.params)
            params_summary = params_str[:200] if len(params_str) > 200 else params_str
            logger.info(
                "Tool '%s' succeeded: %.1fms, %d chars",
                tc.name, duration_ms, len(result.output),
                extra={
                    "category": "tool",
                    "tool_name": tc.name,
                    "params_summary": params_summary,
                    "permission_result": "allowed",
                    "duration_ms": round(duration_ms, 1),
                    "result_size_chars": len(result.output),
                },
            )

            # Summarize large results via sub-agent; fall back to truncation
            if len(result.output) > self.TOOL_RESULT_MAX_CHARS:
                result = await self._summarize_via_subagent(result, tc.name)

            return result
        except Exception as e:
            logger.error(
                "Tool '%s' failed: %s", tc.name, str(e),
                extra={"category": "error", "component": "tool"},
            )
            return ToolResult(error=str(e))

    async def _summarize_via_subagent(
        self, result: ToolResult, tool_name: str
    ) -> ToolResult:
        """Summarize a large tool result using a sub-agent.

        Falls back to truncation if the sub-agent pool is unavailable or
        summarization fails.
        """
        if not self.subagent_pool:
            return self._truncate_result(result)

        try:
            prompt = (
                f"Summarize this tool result from '{tool_name}' concisely. "
                f"Keep all key information but compress redundant parts.\n\n"
                f"{result.output[:20000]}"
            )
            handle = await self.subagent_pool.spawn(
                prompt=prompt,
                tools=[],
                mode="Non-think",
                background=True,
            )
            summary_result = await handle.wait()
            if summary_result.error:
                raise Exception(summary_result.error)
            return ToolResult(
                output=(
                    f"[Summarized from {len(result.output)} chars]\n"
                    f"{summary_result.output}"
                ),
                error=result.error,
                metadata=result.metadata,
            )
        except Exception:
            return self._truncate_result(result)

    def _truncate_result(self, result: ToolResult) -> ToolResult:
        """Fallback truncation for large tool results."""
        return ToolResult(
            output=(
                f"[Truncated from {len(result.output)} chars]\n"
                f"{result.output[:self.TOOL_RESULT_MAX_CHARS]}"
            ),
            error=result.error,
            metadata=result.metadata,
        )

    def _get_tool_level(self, tool_name: str) -> int:
        from myagent.permissions.controller import TOOL_LEVEL_MAP
        return TOOL_LEVEL_MAP.get(tool_name, 3)
