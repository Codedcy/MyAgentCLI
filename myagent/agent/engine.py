"""Agent engine — the core ReAct loop.

Think → Decision → Execute → Observe, repeated until Done.

Design doc reference: §二 核心 Agent 循环
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from myagent.agent.prompt_capture import LastPromptCapture
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
class StatusUpdate:
    scope: str
    data: dict[str, object]


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
    | StatusUpdate
    | IntentSignal
)


# ── Engine ──────────────────────────────────────────────────────


class AgentEngine:

    @property
    def _tool_result_max_chars(self) -> int:
        """Read tool_result_max_chars from config, falling back to default 5000."""
        if self.config and hasattr(self.config, 'tools'):
            return getattr(self.config.tools, 'tool_result_max_chars', 5000)
        return 5000

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
        config_loader=None,
        memory_store=None,
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
        self._config_loader = config_loader
        self._memory_store = memory_store
        self.interrupt_event = asyncio.Event()
        self._session_token_totals: dict[object, int] = {}
        self._pending_background_subagents: set[str] = set()
        self._last_prompt_capture: LastPromptCapture | None = None

    def get_last_prompt_capture(self) -> LastPromptCapture | None:
        return self._last_prompt_capture

    def last_prompt_text(self) -> str:
        if self._last_prompt_capture is None:
            return "No LLM prompt captured yet."
        return self._last_prompt_capture.to_text()

    def last_prompt_json(self) -> str:
        if self._last_prompt_capture is None:
            return "No LLM prompt captured yet."
        return self._last_prompt_capture.to_json()

    def _capture_last_prompt(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None,
        thinking: str,
    ) -> None:
        model = self._current_model_name()
        estimated_tokens = self._estimate_prompt_tokens(messages)
        self._last_prompt_capture = LastPromptCapture.capture(
            model=model,
            thinking=thinking,
            messages=messages,
            tools=tools,
            estimated_tokens=estimated_tokens,
        )

    def _current_model_name(self) -> str:
        model = getattr(self.llm, "model", None)
        if isinstance(model, str) and model:
            return model

        model_config = getattr(self.config, "model", None)
        provider = getattr(model_config, "provider", None)
        configured_model = getattr(model_config, "model", None)
        if provider and configured_model:
            return f"{provider}/{configured_model}"
        if configured_model:
            return str(configured_model)
        return "unknown"

    def _estimate_prompt_tokens(self, messages: list[dict]) -> int | None:
        token_counter = getattr(self.llm, "token_count", None)
        if not callable(token_counter):
            return None
        if inspect.iscoroutinefunction(token_counter):
            return None
        try:
            token_count = token_counter(messages)
        except Exception:
            logger.exception(
                "Prompt token estimation failed",
                extra={
                    "category": "error",
                    "component": "llm",
                    "context": "last_prompt_capture_token_count",
                },
            )
            return None
        if inspect.isawaitable(token_count):
            close = getattr(token_count, "close", None)
            if callable(close):
                close()
            return None
        return token_count if isinstance(token_count, int) else None

    async def run(
        self, user_input: str, session, active_skill: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Execute one turn of the ReAct loop."""

        # Build context
        history = session.get_recent_messages() if hasattr(session, 'get_recent_messages') else []
        # G11: Proactively pass current goal to context builder so it appears
        # in the system prompt on every turn (not just reactively after failed checks).
        goal = self.goal_tracker.get_goal() if self.goal_tracker else None
        request = await self.context_builder.build(
            current_input=user_input,
            history=history,
            project_context=self.project_context,
            active_skill=active_skill,
            goal=goal,
        )

        # ReAct loop (simplified — in production, full loop with LLM streaming)
        if self.llm:
            async for event in self._react_loop(request, session, user_input):
                yield event
        else:
            # No LLM available — echo back for testing
            yield TextChunk(content=f"Received: {user_input}")
            yield Done()

    # ── ReAct loop ──────────────────────────────────────────────────

    MAX_ITERATIONS: int | None = None

    async def _react_loop(
        self, request, session, user_input: str = ""
    ) -> AsyncIterator[AgentEvent]:
        """Core ReAct loop with true iterative execution and tool result feedback.

        Each iteration:
        1. Stream LLM response, collecting text and tool calls.
        2. If tool calls present: execute them, append results to messages, loop again.
        3. If no tool calls: check intent / question / goal, then yield Done.
        4. If goal not achieved: inject feedback and loop again.
        """
        tools_list = request.tools
        api_format = request.to_api_format()
        messages = [
            message
            for message in api_format["messages"]
            if message.get("role") != "system"
        ]

        # Prepend system message (was lost in the old single-pass code)
        if api_format.get("system"):
            messages.insert(0, {"role": "system", "content": api_format["system"]})

        # Wire session directory for compression summary persistence (gap-03)
        if (
            self.compression
            and self.session_store
            and hasattr(session, "project_name")
            and hasattr(session, "project_hash")
        ):
            sess_dir = self.session_store._session_dir(
                session.project_name, session.project_hash, session.id
            )
            self.compression.set_session_dir(sess_dir)

        thinking_mode = self._get_thinking_mode()
        iteration = 0
        context_notified_50 = False  # gap-25: only notify once
        active_skill: str | None = None  # gap-32: track active skill for context rebuild
        pending_background_subagents = self._pending_background_subagents

        while self.MAX_ITERATIONS is None or iteration < self.MAX_ITERATIONS:
            iteration += 1

            # G10: Drain sub-agent-to-main-agent outbound messages
            if self.subagent_pool:
                outbound_msgs = self.subagent_pool.drain_outbound_messages()
                for out in outbound_msgs:
                    sub_id = out.get("from", "unknown")
                    msg_text = out.get("message", "")
                    if msg_text:
                        messages.append({
                            "role": "user",
                            "content": f"[Sub-agent {sub_id}]: {msg_text}",
                        })
                        logger.info(
                            "Sub-agent %s sent message to main", sub_id,
                            extra={"category": "agent", "event": "subagent_message"},
                        )
                completion_events = self._drain_subagent_completion_events(
                    pending_background_subagents
                )
                if completion_events:
                    self._append_subagent_completion_messages(
                        messages,
                        completion_events,
                    )
                    pending_background_subagents.difference_update(
                        self._completion_event_ids(completion_events)
                    )

            # Check for external interrupt signal (gap-10, gap-18)
            if self.interrupt_event and self.interrupt_event.is_set():
                logger.info(
                    "ReAct loop interrupted at iteration %d", iteration,
                    extra={"category": "agent", "event": "interrupted"},
                )
                self._persist_turn(session, messages)
                yield Interrupted()
                return

            tokens_this_turn = 0  # accumulated from LLM Done events this iteration
            logger.info(
                "ReAct iteration %d", iteration,
                extra={"category": "agent", "tokens_used_this_turn": tokens_this_turn},
            )

            # ── Context compression check (gap-01, gap-25) ──────
            if self.compression:
                usage_pct = self._estimate_context_usage(messages, tools_list)
                yield self._context_status_update(usage_pct)
                compact_was_called = False  # gap-19-07: track whether compact() ran
                if usage_pct >= 0.50 and not context_notified_50:
                    context_notified_50 = True
                    logger.info(
                        "Context at %.0f%% — consider /clear or manual compact",
                        usage_pct * 100,
                        extra={"category": "agent", "event": "context_warning"},
                    )
                    yield TextChunk(
                        content=(
                            f"\n[Note: Context usage at {int(usage_pct * 100)}%. "
                            f"Consider running /compact to compress context "
                            f"or /clear to wipe in-memory messages.]\n"
                        )
                    )
                if usage_pct >= 0.75:
                    compact_was_called = True
                    logger.info(
                        "Auto-compacting context at %.0f%%", usage_pct * 100,
                        extra={"category": "agent", "event": "auto_compact"},
                    )
                    from myagent.context.builder import Message as CtxMessage
                    ctx_messages = [
                        CtxMessage(
                            role=m.get("role", "user"),
                            content=m.get("content", ""),
                        )
                        for m in messages
                    ]
                    compact_result = await self.compression.compact(ctx_messages, usage_pct)
                    # Rebuild messages from compact result
                    messages = [
                        {"role": m.role, "content": m.content}
                        for m in compact_result.messages
                    ]
                    # Re-insert system message if it got removed
                    if api_format.get("system") and messages[0].get("role") != "system":
                        messages.insert(0, {"role": "system", "content": api_format["system"]})
                    # Surface Layer 3 degradation notice to user (gap-r12-07)
                    if compact_result.degradation_notice:
                        logger.warning(
                            "Compression Layer 3 degraded: %s",
                            compact_result.degradation_notice,
                            extra={"category": "agent", "event": "layer3_degraded"},
                        )
                        yield TextChunk(
                            content=(
                                f"\n[Warning: {compact_result.degradation_notice}]\n"
                            )
                        )
                    if compact_result.layers_applied:
                        yield TextChunk(
                            content=(
                                f"\n[Auto-compacted: context from {int(usage_pct * 100)}% "
                                f"to ~{int(compact_result.usage_after * 100)}% "
                                f"(layers: {compact_result.layers_applied})]\n"
                            )
                        )
                    # Re-estimate usage after compact for hard-limit check below
                    usage_pct = compact_result.usage_after
                    yield self._context_status_update(usage_pct)
                    # Reset 50% notification flag if compaction dropped usage
                    # well below the warning threshold, so the user gets
                    # re-warned if context climbs back above 50% (gap-16-03).
                    if usage_pct < 0.30:
                        context_notified_50 = False

                # gap-19-07: 90% hard limit — route through compact() pipeline
                # instead of calling _layer4_truncate directly. This ensures
                # L1-L3 are applied first, potentially reducing context enough
                # to avoid hard truncation. compact() already handles L4 as a
                # safety net (compression.py lines 170-175).
                compression_config = (
                    self.config.context.compression
                    if self.config
                    and getattr(self.config, "context", None)
                    and getattr(self.config.context, "compression", None)
                    else None
                )
                hard_limit = compression_config.hard_limit if compression_config else 0.90
                if usage_pct >= hard_limit:
                    if not compact_was_called:
                        # compact was skipped at 75% (e.g. minimum_messages guard) —
                        # call it now with all layers including L4 safety net
                        logger.warning(
                            "Context at %.0f%% — triggering compaction via pipeline (Layer 4)",
                            usage_pct * 100,
                            extra={"category": "agent", "event": "hard_truncation"},
                        )
                        from myagent.context.builder import Message as CtxMessage2
                        ctx_messages_hard = [
                            CtxMessage2(
                                role=m.get("role", "user"),
                                content=m.get("content", ""),
                            )
                            for m in messages
                        ]
                        compact_result_l4 = await self.compression.compact(
                            ctx_messages_hard, usage_pct
                        )
                        usage_pct = compact_result_l4.usage_after
                        yield self._context_status_update(usage_pct)
                        messages = [
                            {"role": m.role, "content": m.content}
                            for m in compact_result_l4.messages
                        ]
                        # Re-insert system message if it got removed
                        if api_format.get("system") and messages[0].get("role") != "system":
                            messages.insert(
                                0,
                                {
                                    "role": "system",
                                    "content": api_format["system"],
                                },
                            )
                        yield TextChunk(
                            content=(
                                "\n[Hard truncation applied: context exceeded "
                                f"{int(hard_limit * 100)}%. Messages reduced "
                                f"from {len(ctx_messages_hard)} to {len(messages)}. "
                                f"Consider running /clear to free more space.]\n"
                            )
                        )
                    else:
                        # compact was already called (>= 75% path), L4 was already
                        # applied by compact(). Log a warning that we're still
                        # above the hard limit despite all layers being applied.
                        logger.warning(
                            "Context at %.0f%% after compaction (layers: %s) — "
                            "all compression layers exhausted. Manual /clear "
                            "recommended.",
                            usage_pct * 100,
                            compact_result.layers_applied,
                            extra={"category": "agent", "event": "hard_limit_exhausted"},
                        )
                        yield TextChunk(
                            content=(
                                f"\n[Warning: Context at {int(usage_pct * 100)}% "
                                "after compression. All layers exhausted. "
                                "Consider running /clear to free space.]\n"
                            )
                        )

            text_buffer: list[str] = []
            tool_calls_in_turn: list = []

            # ── Stream LLM response ──────────────────────────────
            try:
                self._capture_last_prompt(
                    messages=messages,
                    tools=tools_list,
                    thinking=thinking_mode,
                )
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
                    elif kind == "done":
                        # Capture token usage from Done event for per-turn logging
                        usage = getattr(event, "usage", None)
                        if usage:
                            tokens_this_turn = getattr(usage, "total_tokens", 0)
                            yield self._token_status_update(usage, session)
            except Exception as e:
                # gap-06: preserve partial content on stream interruption
                partial_text = "".join(text_buffer)
                logger.error(
                    "LLM error in iteration %d: %s",
                    iteration,
                    str(e),
                    exc_info=True,
                    extra={
                        "category": "error",
                        "component": "llm",
                        "context": "llm_stream_complete",
                    },
                )
                yield self._health_status_update(last_error=str(e))
                if partial_text:
                    yield TextChunk(
                        content=(
                            f"\n[Stream interrupted. Received {len(partial_text)} chars. "
                            f"Error: {str(e)[:200]}]\n"
                        )
                    )
                    # Append partial assistant message to history
                    messages.append({"role": "assistant", "content": partial_text})
                    self._persist_turn(session, messages)
                    yield IntentSignal(intent="continue")
                    return
                # gap-8-03: user-friendly guidance when LLM retries exhausted
                self._persist_turn(session, messages)
                yield self._build_llm_error_event(e)
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
                    # Skill invocation shortcut (gap-32: rebuild context with skill)
                    if tc.name == "skill_invoke" and self.skill_registry:
                        skill = self.skill_registry.get(tc.params.get("skill", ""))
                        if skill:
                            yield ToolCallStart(name="skill_invoke", call_id=tc.id)
                            active_skill = skill.name
                            # Log skill invocation for metrics tracking (gap-20-05)
                            logger.info(
                                "Skill invoked: %s (source=model)",
                                skill.name,
                                extra={
                                    "category": "skill",
                                    "event": "invoked",
                                    "skill_name": skill.name,
                                    "invocation_source": "model",
                                },
                            )
                            # Rebuild context with skill content injected (gap-32)
                            if self.context_builder and user_input:
                                history = (
                                    session.get_recent_messages()
                                    if hasattr(session, "get_recent_messages")
                                    else []
                                )
                                new_request = await self.context_builder.build(
                                    current_input=user_input,
                                    history=history,
                                    project_context=self.project_context,
                                    active_skill=active_skill,
                                )
                                new_api = new_request.to_api_format()
                                # Replace system message
                                if new_api.get("system"):
                                    for i, m in enumerate(messages):
                                        if m.get("role") == "system":
                                            messages[i] = {
                                                "role": "system",
                                                "content": new_api["system"],
                                            }
                                            break
                                # Add skill tool result
                                skill_result = ToolResult(
                                    output=f"Skill '{skill.name}' loaded and context updated."
                                )
                            else:
                                skill_result = ToolResult(
                                    output=f"Skill '{skill.name}' loaded."
                                )
                            yield ToolCallEnd(call_id=tc.id, result=skill_result)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "name": "skill_invoke",
                                "content": skill_result.output,
                            })
                            continue

                    yield ToolCallStart(name=tc.name, call_id=tc.id)
                    result = await self._execute_tool(tc, session)
                    yield ToolCallEnd(call_id=tc.id, result=result)

                    result_text = result.output if not result.error else f"Error: {result.error}"
                    self._track_background_subagent_result(
                        tc.name,
                        result,
                        pending_background_subagents,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result_text,
                    })

                # Loop again — LLM will see tool results in next iteration
                if pending_background_subagents:
                    completion_events = await self._wait_for_pending_subagents(
                        pending_background_subagents
                    )
                    if completion_events:
                        self._append_subagent_completion_messages(
                            messages,
                            completion_events,
                        )
                        pending_background_subagents.difference_update(
                            self._completion_event_ids(completion_events)
                        )

                self._persist_turn(session, messages)
                logger.info(
                    "ReAct iteration %d complete (tool calls)", iteration,
                    extra={
                        "category": "agent",
                        "event": "tool_call",
                        "tokens_used_this_turn": tokens_this_turn,
                    },
                )
                continue

            # ── No tool calls — text response complete ───────────
            full_text = "".join(text_buffer)

            # Detect intent signals (stop / correct / insert / continue)
            # Must check BEFORE stripping marker — _detect_intent needs the marker.
            intent = self._detect_intent(full_text)

            # Strip the [INTENT: xxx] marker line from text before recording
            # in message history, so it doesn't pollute the visible conversation.
            clean_text = self._strip_intent_marker(full_text)

            # Record assistant response in message history
            if clean_text:
                messages.append({"role": "assistant", "content": clean_text})
            if intent and intent != "continue":
                yield IntentSignal(intent=intent)
                if intent == "stop":
                    self._persist_turn(session, messages)
                    yield Done()
                    return
                elif intent == "correct":
                    # Inject correction feedback and continue
                    messages.append({
                        "role": "user",
                        "content": (
                            "You indicated a direction correction. Please proceed with "
                            "the corrected approach. What would you like to do differently?"
                        ),
                    })
                    self._persist_turn(session, messages)
                    logger.info(
                        "ReAct iteration %d complete (correct)", iteration,
                        extra={
                            "category": "agent",
                            "event": "correct",
                            "tokens_used_this_turn": tokens_this_turn,
                        },
                    )
                    continue
                elif intent == "insert":
                    # Acknowledge new sub-task and continue
                    messages.append({
                        "role": "user",
                        "content": (
                            "Acknowledged. Please proceed with the additional sub-task "
                            "you mentioned, then continue with the original work."
                        ),
                    })
                    self._persist_turn(session, messages)
                    logger.info(
                        "ReAct iteration %d complete (insert)", iteration,
                        extra={
                            "category": "agent",
                            "event": "insert",
                            "tokens_used_this_turn": tokens_this_turn,
                        },
                    )
                    continue

            # Detect AskUserQuestion — stop this turn; wait for user reply
            if pending_background_subagents:
                completion_events = await self._wait_for_pending_subagents(
                    pending_background_subagents
                )
                if completion_events:
                    self._append_subagent_completion_messages(
                        messages,
                        completion_events,
                    )
                    pending_background_subagents.difference_update(
                        self._completion_event_ids(completion_events)
                    )
                    self._persist_turn(session, messages)
                    logger.info(
                        "ReAct iteration %d complete (subagent completion)",
                        iteration,
                        extra={
                            "category": "agent",
                            "event": "subagent_completion_continue",
                            "tokens_used_this_turn": tokens_this_turn,
                        },
                    )
                    continue

            if self._is_question(clean_text):
                self._persist_turn(session, messages)
                yield AskUserQuestion(question=clean_text)
                return

            # ── Goal check ──────────────────────────────────────
            goal, goal_version = self._goal_snapshot()
            if goal:
                yield self._goal_status_update(
                    goal,
                    state="checking",
                    achieved=False,
                )
                try:
                    goal_check = await self.goal_tracker.check_goal(
                        session,
                        messages,
                        goal=goal,
                    )
                except Exception as e:
                    logger.error(
                        "Goal check failed: %s", str(e),
                        exc_info=True,
                        extra={"category": "error", "component": "agent", "context": "goal_check"},
                    )
                    yield Done()
                    return

                if not self._is_goal_snapshot_current(goal, goal_version):
                    continue

                if not goal_check.achieved:
                    yield self._goal_status_update(
                        goal,
                        state="open",
                        achieved=False,
                    )
                    # Inject feedback and re-enter the loop
                    self._continue_with_feedback(goal_check, messages)
                    self._persist_turn(session, messages)
                    logger.info(
                        "ReAct iteration %d complete (goal not achieved)", iteration,
                        extra={
                            "category": "agent",
                            "event": "goal_continue",
                            "tokens_used_this_turn": tokens_this_turn,
                        },
                    )
                    continue

            # Goal achieved (or no goal set) — we are done
            # Persist goal achievement to session (G2)
            if goal and hasattr(session, 'goal_achieved'):
                session.goal_achieved = True
            if goal:
                yield self._goal_status_update(
                    goal,
                    state="achieved",
                    achieved=True,
                )
            self._persist_turn(session, messages)
            logger.info(
                "ReAct iteration %d complete (done)", iteration,
                extra={
                    "category": "agent",
                    "event": "done",
                    "tokens_used_this_turn": tokens_this_turn,
                },
            )
            yield Done()
            return

        # Max iterations exhausted
        self._persist_turn(session, messages)
        yield Error(
            message=f"ReAct loop reached max iterations ({self.MAX_ITERATIONS})"
        )

    # ── helpers ────────────────────────────────────────────────────

    def _context_status_update(self, usage_pct: float) -> StatusUpdate:
        return StatusUpdate(
            scope="context",
            data={
                "context_usage": usage_pct,
                "context_window": self._current_context_window(),
            },
        )

    def _token_status_update(self, usage, session=None) -> StatusUpdate:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is None:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
        turn_total = int(total_tokens or 0)
        session_total = self._accumulate_session_tokens(session, turn_total)

        data: dict[str, object] = {
            "turn_total": turn_total,
            "session_total": session_total,
        }
        if prompt_tokens is not None:
            data["prompt_tokens"] = prompt_tokens
        if completion_tokens is not None:
            data["completion_tokens"] = completion_tokens
        return StatusUpdate(scope="tokens", data=data)

    def _accumulate_session_tokens(self, session, turn_total: int) -> int:
        fallback_start = 0
        if session is not None:
            existing_total = getattr(session, "total_tokens", None)
            if (
                isinstance(existing_total, int | float)
                and not isinstance(existing_total, bool)
            ):
                fallback_start = int(existing_total)
                session_total = int(existing_total) + turn_total
                try:
                    session.total_tokens = session_total
                    return session_total
                except Exception:
                    logger.exception(
                        "Failed to update session token total",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "agent_update_session_tokens",
                        },
                    )

        key = self._session_token_key(session)
        session_total = self._session_token_totals.get(key, fallback_start) + turn_total
        self._session_token_totals[key] = session_total
        return session_total

    def _session_token_key(self, session) -> object:
        if session is None:
            return ("session", None)
        session_id = getattr(session, "id", None)
        if isinstance(session_id, str) and session_id:
            return ("session_id", session_id)
        return ("session_object", id(session))

    def _goal_snapshot(self) -> tuple[object | None, object | None]:
        if not self.goal_tracker:
            return None, None

        get_snapshot = getattr(self.goal_tracker, "get_goal_snapshot", None)
        if callable(get_snapshot):
            snapshot = get_snapshot()
            if isinstance(snapshot, tuple) and len(snapshot) == 2:
                return snapshot

        return self.goal_tracker.get_goal(), None

    def _is_goal_snapshot_current(
        self,
        goal: object | None,
        version: object | None,
    ) -> bool:
        if not self.goal_tracker or version is None:
            return True

        is_current = getattr(self.goal_tracker, "is_current_goal", None)
        if callable(is_current):
            return bool(is_current(goal, version))

        current_goal, current_version = self._goal_snapshot()
        return current_goal == goal and current_version == version

    def _goal_status_update(
        self,
        goal: object,
        *,
        state: str,
        achieved: bool,
    ) -> StatusUpdate:
        return StatusUpdate(
            scope="goal",
            data={
                "name": str(goal or ""),
                "active": True,
                "achieved": achieved,
                "waiting_for_user": False,
                "state": state,
            },
        )

    def _health_status_update(self, *, last_error: str) -> StatusUpdate:
        return StatusUpdate(
            scope="health",
            data={"last_error": last_error},
        )

    def _track_background_subagent_result(
        self,
        tool_name: str,
        result: ToolResult,
        pending_background_subagents: set[str],
    ) -> None:
        if tool_name != "spawn_subagent" or result.error:
            return
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        if not metadata.get("background"):
            return
        subagent_id = metadata.get("subagent_id")
        if subagent_id:
            pending_background_subagents.add(str(subagent_id))

    def _drain_subagent_completion_events(
        self,
        pending_background_subagents: set[str],
    ) -> list[dict]:
        if not pending_background_subagents or not self.subagent_pool:
            return []
        drain = getattr(self.subagent_pool, "drain_completion_events", None)
        if not callable(drain):
            return []
        return list(drain(pending_background_subagents) or [])

    async def _wait_for_pending_subagents(
        self,
        pending_background_subagents: set[str],
    ) -> list[dict]:
        if not pending_background_subagents or not self.subagent_pool:
            return []
        wait = getattr(self.subagent_pool, "wait_for_completion_events", None)
        if callable(wait):
            return list(await wait(set(pending_background_subagents)) or [])
        return self._drain_subagent_completion_events(pending_background_subagents)

    def has_pending_subagent_completions(self) -> bool:
        """Whether a completed background sub-agent still needs main-loop handling."""

        pending = set(self._pending_background_subagents)
        if not pending:
            return False

        events = getattr(self.subagent_pool, "_pending_completion_events", None)
        if isinstance(events, list):
            return any(
                str(event_id) in pending
                for event in events
                if isinstance(event, dict)
                if (event_id := event.get("subagent_id") or event.get("id"))
            )
        return True

    def _append_subagent_completion_messages(
        self,
        messages: list[dict],
        completion_events: list[dict],
    ) -> None:
        for event in completion_events:
            messages.append({
                "role": "user",
                "content": self._format_subagent_completion_observation(event),
            })
            logger.info(
                "Sub-agent %s completion observed",
                event.get("subagent_id") or event.get("id") or "unknown",
                extra={
                    "category": "agent",
                    "event": "subagent_completion",
                },
            )

    def _format_subagent_completion_observation(self, event: dict) -> str:
        subagent_id = event.get("subagent_id") or event.get("id") or "unknown"
        status = event.get("status") or "completed"
        task_name = event.get("task_name") or ""
        output = event.get("output") or ""
        error = event.get("error") or ""
        summary = event.get("summary") or ""
        transcript_path = event.get("transcript_path") or ""
        parts = [f"Sub-agent {subagent_id} {status}."]
        if task_name:
            parts.append(f"Task: {task_name}")
        if error:
            parts.append(f"Error: {error}")
        elif output:
            parts.append(f"Output:\n{output}")
        elif summary:
            parts.append(f"Summary: {summary}")
        if transcript_path:
            parts.append(f"Transcript: {transcript_path}")
        return "\n".join(parts)

    def _completion_event_ids(self, completion_events: list[dict]) -> set[str]:
        return {
            str(event_id)
            for event in completion_events
            if (event_id := event.get("subagent_id") or event.get("id"))
        }

    def _current_context_window(self) -> int:
        model_name = None
        if self.config and hasattr(self.config, 'model'):
            model_name = getattr(self.config.model, 'model', None)
        return self._get_context_window(model_name)

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
        if hasattr(event, "reasoning_content"):
            return "thinking"
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

    def _build_llm_error_event(self, exception: Exception) -> Error:
        """Build a user-friendly Error event for exhausted LLM retries (gap-8-03).

        When all 3 retries across all fallback models fail, provide actionable
        guidance to the user instead of raw exception text (per spec: "降级提示
        用户检查网络/API key").
        """
        from myagent.llm.provider import LLMError as ProviderLLMError

        err_msg = str(exception)
        if isinstance(exception, ProviderLLMError):
            code = exception.code
            if code == "rate_limit":
                guidance = (
                    "API 请求频率超限 (rate limit)。请稍等几分钟后重试，"
                    "或检查 API 配额是否充足。"
                )
            elif code == "connection_error":
                guidance = (
                    "无法连接到模型服务。请检查网络连接是否正常，"
                    "或确认 API endpoint 地址是否正确。"
                )
            elif code in ("auth_error",):
                guidance = (
                    "API Key 认证失败。请检查 API Key 是否有效，"
                    "或环境变量是否正确配置。"
                )
            elif code == "all_models_exhausted":
                guidance = (
                    "所有模型（主模型 + 备用模型）均已尝试但全部失败。"
                    "请检查网络连接和 API Key 配置。"
                )
            elif code == "max_retries":
                guidance = (
                    "LLM API 调用经过 3 次重试后仍然失败。"
                    "请检查网络连接和 API Key 是否有效。"
                )
            else:
                guidance = (
                    f"LLM API 调用失败 (错误码: {code})。"
                    "请检查网络连接和 API Key 是否有效。"
                )
        else:
            guidance = (
                f"LLM API 调用失败: {err_msg[:200]}。"
                "请检查网络连接和 API Key 是否有效。"
            )

        return Error(message=f"{guidance}\n错误详情: {err_msg[:300]}")

    # Structured intent marker prefix (spec §二: model signals intent via cues)
    _INTENT_MARKER_PREFIX = "[INTENT:"

    def _detect_intent(self, text: str) -> str | None:
        """Detect user intent signals in model response.

        Primary method: parses structured [INTENT: xxx] markers that the model
        emits as the first line of its response (instructed via L0 system prompt).
        This is the model-driven approach required by §二 — no hard-coded keyword
        guessing.

        Fallback: For very short responses (< 30 chars) that match common
        continue phrases, treats as "continue". This handles edge cases where
        the model produces a terse acknowledgment after interruption.

        Returns one of: "stop", "correct", "insert", "continue", or None.
        """
        if not text:
            return None

        # ── Primary: parse structured [INTENT: ...] marker ──────────
        stripped = text.strip()
        valid_intents = {"stop", "correct", "insert", "continue"}
        if stripped.startswith(self._INTENT_MARKER_PREFIX):
            # Extract the intent value: [INTENT: stop]\n...
            first_line = stripped.split("\n", 1)[0]
            try:
                # Parse "[INTENT: stop]" → "stop"
                intent_part = first_line[len(self._INTENT_MARKER_PREFIX):].rstrip("]").strip()
                if intent_part in valid_intents:
                    return intent_part
            except (ValueError, IndexError):
                logger.exception(
                    "Failed to parse leading interrupt intent marker",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "parse leading interrupt intent marker",
                    },
                )
                pass

        # Also scan for marker anywhere in the text (model may not always
        # put it as the very first line despite instructions)
        for line in stripped.split("\n"):
            line = line.strip()
            if line.startswith(self._INTENT_MARKER_PREFIX):
                try:
                    intent_part = line[len(self._INTENT_MARKER_PREFIX):].rstrip("]").strip()
                    if intent_part in valid_intents:
                        return intent_part
                except (ValueError, IndexError):
                    logger.exception(
                        "Failed to parse inline interrupt intent marker",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "parse inline interrupt intent marker",
                        },
                    )
                    pass

        # ── Thin fallback: brief continue phrases only ──────────────
        text_lower = stripped.lower()
        if len(text) < 30:
            continue_phrases = [
                "continue", "go on", "继续", "proceed", "resume",
                "carry on", "keep going", "go ahead",
            ]
            if any(p in text_lower for p in continue_phrases):
                return "continue"

        return None

    def _strip_intent_marker(self, text: str) -> str:
        """Strip [INTENT: xxx] marker line(s) from model response text.

        Removes the structured intent marker line so it doesn't appear
        in the user-visible conversation or get stored in message history.
        """
        if not text:
            return text
        result_lines = []
        for line in text.split("\n"):
            stripped_line = line.strip()
            if stripped_line.startswith(self._INTENT_MARKER_PREFIX):
                continue  # Skip intent marker lines
            result_lines.append(line)
        return "\n".join(result_lines)

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
        return any(text_lower.startswith(starter) for starter in question_starters)

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

        # Check permissions before executing.
        # Permission bypass is only allowed through PermissionController state,
        # such as CLI-level skip_all().
        if self.permissions:
            level = self._get_tool_level(tc.name)
            perm_result = self.permissions.check(tc.name, level=level, params=tc.params)
            if perm_result.name == "DENY":
                logger.info(
                    "Tool '%s' DENIED by permission controller", tc.name,
                    extra={"category": "tool", "tool_name": tc.name, "permission_result": "denied"},
                )
                return ToolResult(
                    error=f"Permission denied: {tc.name} requires level {level} access."
                )
            elif perm_result.name == "ASK":
                try:
                    allowed = await self.permissions.confirm(tc.name, tc.params)
                except Exception:
                    logger.exception(
                        "Permission confirmation failed for tool '%s'",
                        tc.name,
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": f"permission_confirm:{tc.name}",
                        },
                    )
                    allowed = False
                if not allowed:
                    logger.info(
                        "Tool '%s' DENIED by user", tc.name,
                        extra={
                            "category": "tool",
                            "tool_name": tc.name,
                            "permission_result": "denied",
                        },
                    )
                    return ToolResult(
                        error=f"User denied permission for '{tc.name}'."
                    )

        try:
            ctx = ToolContext(
                session_id=session.id if hasattr(session, "id") else "unknown",
                project_dir=self.project_dir,
                permissions=self.permissions,
                config=self.config,
                subagent_pool=self.subagent_pool,
                working_dir=self.project_dir,
                project_context=self.project_context,
                config_loader=getattr(self, '_config_loader', None),
                memory_store=getattr(self, '_memory_store', None),
                goal_tracker=self.goal_tracker,
                tool_registry=self.tool_registry,
                mcp_clients=(
                    getattr(self.tool_registry, "mcp_clients", [])
                    if self.tool_registry
                    else []
                ),
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
            if len(result.output) > self._tool_result_max_chars:
                result = await self._summarize_via_subagent(result, tc.name, call_id=tc.id)

            # Persist tool call to session store (gap-14)
            if self.session_store:
                try:
                    from myagent.context.builder import ToolCallRecord
                    record = ToolCallRecord(
                        call_id=tc.id,
                        tool_name=tc.name,
                        params=tc.params,
                        result=result,
                    )
                    await self.session_store.save_tool_call(session, record)
                except Exception:
                    logger.exception(
                        "Failed to persist tool call record",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "agent.persist_tool_call",
                        },
                    )

            return result
        except Exception as e:
            logger.error(
                "Tool '%s' failed: %s", tc.name, str(e),
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": f"execute_tool:{tc.name}",
                },
            )
            return ToolResult(error=str(e))

    async def _summarize_via_subagent(
        self, result: ToolResult, tool_name: str, call_id: str | None = None
    ) -> ToolResult:
        """Summarize a large tool result using a sub-agent.

        Falls back to truncation if the sub-agent pool is unavailable or
        summarization fails.

        Size limits (gap-19-06):
        - Results <= 200K chars: passed in full to the sub-agent prompt
        - Results 200K-1M chars: truncated to 200K in the prompt, with
          a file-reference instruction pointing to the persisted copy
        - Results > 1M chars: fall back to truncation immediately — even
          the sub-agent's 1M context window cannot hold the full result
        """
        if not self.subagent_pool:
            return self._truncate_result(result)

        # Compute the file reference for the persisted full result
        file_ref = ""
        if call_id:
            file_ref = f"tools/call-{call_id}.json"

        # Size guard: results exceeding 1M chars cannot fit in any
        # sub-agent context window — fall back to truncation immediately
        max_prompt_chars = 200_000
        hard_limit_chars = 1_000_000

        if len(result.output) > hard_limit_chars:
            logger.warning(
                "Tool result (%d chars) exceeds sub-agent context limit "
                "(%d chars) — falling back to truncation",
                len(result.output), hard_limit_chars,
                extra={"category": "tool"},
            )
            return self._truncate_result(result)

        try:
            full_output = result.output
            truncated_for_prompt = False

            # Apply 200K ceiling for the sub-agent prompt (gap-19-06)
            if len(full_output) > max_prompt_chars:
                truncated_for_prompt = True
                prompt_output = full_output[:max_prompt_chars]
            else:
                prompt_output = full_output

            prompt = (
                f"Summarize this tool result from '{tool_name}' concisely. "
                f"Keep all key information but compress redundant parts."
            )
            if truncated_for_prompt and file_ref:
                prompt += (
                    f" Note: the result was truncated to {max_prompt_chars} "
                    f"chars for this prompt. The full result ({len(result.output)} "
                    f"chars) is available at {file_ref} — use the read tool to "
                    f"access it if you need details beyond what is shown here."
                )
            elif truncated_for_prompt:
                prompt += (
                    f" Note: the result was truncated to {max_prompt_chars} "
                    f"chars for this prompt (original: {len(result.output)} chars)."
                )
            prompt += f"\n\n{prompt_output}"

            handle = await self.subagent_pool.spawn(
                prompt=prompt,
                tools=["read"] if (truncated_for_prompt and file_ref) else [],
                mode="Non-think",
                background=True,
            )
            summary_result = await handle.wait()
            if summary_result.error:
                raise Exception(summary_result.error)
            return ToolResult(
                output=(
                    "[Summarized from "
                    f"{len(result.output)} chars."
                    f"{' ' + file_ref if file_ref else ''}]\n"
                    f"{summary_result.output}"
                ),
                error=result.error,
                metadata=result.metadata,
            )
        except Exception:
            logger.exception(
                "Summarization failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "agent.summarize_tool_result",
                },
            )
            return self._truncate_result(result)

    def _truncate_result(self, result: ToolResult) -> ToolResult:
        """Fallback truncation for large tool results."""
        return ToolResult(
            output=(
                f"[Truncated from {len(result.output)} chars]\n"
                f"{result.output[:self._tool_result_max_chars]}"
            ),
            error=result.error,
            metadata=result.metadata,
        )

    def _get_tool_level(self, tool_name: str) -> int:
        from myagent.permissions.controller import TOOL_LEVEL_MAP
        return TOOL_LEVEL_MAP.get(tool_name, 3)

    # Known model context window sizes (in tokens).
    # Used as fallback when dynamic discovery from litellm fails.
    _CONTEXT_WINDOW_MAP: dict[str, int] = {
        "deepseek-v4-pro": 1_000_000,
        "deepseek-chat": 65536,
        "gpt-4o": 128000,
        "gpt-4-turbo": 128000,
        "gpt-3.5-turbo": 16385,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
        "claude-3-haiku": 200000,
        "claude-3.5-sonnet": 200000,
    }

    @staticmethod
    def _get_context_window(model_name: str | None) -> int:
        """Get the context window size for a model (gap-13-05).

        First tries dynamic discovery via litellm.model_cost, then falls
        back to the static _CONTEXT_WINDOW_MAP, defaulting to 1_000_000.

        Args:
            model_name: The model identifier (e.g. "deepseek-v4-pro").
                        Can be in litellm format ("deepseek/deepseek-v4-pro").

        Returns:
            Context window size in tokens.
        """
        if not model_name:
            return 1_000_000

        # ── Dynamic discovery via litellm.model_cost ──────────────
        try:
            import litellm
            model_cost_map = getattr(litellm, 'model_cost', None)
            if isinstance(model_cost_map, dict):
                # litellm.model_cost keys are like "deepseek/deepseek-v4-pro"
                # Try both the full litellm key and the short model name
                for candidate in (model_name, f"deepseek/{model_name}"):
                    info = model_cost_map.get(candidate)
                    if isinstance(info, dict):
                        max_input = info.get("max_input_tokens")
                        if max_input is not None and max_input > 0:
                            return int(max_input)
        except ImportError:
            # LiteLLM is optional for this best-effort discovery path.
            logger.exception(
                "LiteLLM unavailable during context window discovery",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "discover model context window",
                },
            )
            pass
        except (TypeError, ValueError):
            logger.warning(
                "Dynamic context-window discovery failed; using static fallback",
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "agent_context_window_lookup",
                },
            )

        # ── Fallback: static map with litellm prefix stripped ─────
        short_name = model_name.split("/")[-1] if "/" in model_name else model_name
        return AgentEngine._CONTEXT_WINDOW_MAP.get(short_name, 1_000_000)

    def _estimate_context_usage(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> float:
        """Estimate context window usage as a fraction [0.0, 1.0].

        Derives the context window from the active model configuration
        (using a known-window map, defaulting to 1M). Token counting
        prefers litellm's token_counter via LLMProvider when available,
        falling back to a character-based estimate with a language-aware
        ratio (3.5 chars/token for mixed-content default).
        """
        # ── Token estimation ────────────────────────────────────
        if self.llm is not None:
            try:
                estimated_tokens = self.llm.token_count(messages)
            except Exception:
                logger.exception(
                    "LLM token counting failed; using character estimate",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "agent.estimate_context_tokens",
                    },
                )
                estimated_tokens = self._char_based_token_estimate(messages, tools)
        else:
            estimated_tokens = self._char_based_token_estimate(messages, tools)

        # ── Context window lookup (dynamic + fallback, gap-13-05) ─
        model_name = None
        if self.config and hasattr(self.config, 'model'):
            model_name = getattr(self.config.model, 'model', None)
        context_window = self._get_context_window(model_name)

        return min(estimated_tokens / context_window, 1.0)

    def _char_based_token_estimate(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> int:
        """Character-based token estimate as a fallback when litellm is unavailable.

        Uses a weighted ratio: Chinese characters ~1.5 chars/token,
        ASCII ~4 chars/token. Mixed content defaults to ~3.5.
        """
        import json as _json
        total_chars = 0
        total_cjk = 0
        total_ascii = 0
        for m in messages:
            content = m.get("content", "") or ""
            for ch in content:
                total_chars += 1
                cp = ord(ch)
                # CJK Unified Ideographs (U+4E00–U+9FFF) and common extensions
                if (
                    0x4E00 <= cp <= 0x9FFF
                    or 0x3400 <= cp <= 0x4DBF
                    or 0x20000 <= cp <= 0x2A6DF
                ):
                    total_cjk += 1
                elif cp < 128:
                    total_ascii += 1
            total_chars += len(m.get("role", "") or "")
        if tools:
            tools_json = _json.dumps(tools, ensure_ascii=False)
            total_chars += len(tools_json)
            for ch in tools_json:
                cp = ord(ch)
                if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
                    total_cjk += 1
                elif cp < 128:
                    total_ascii += 1

        # Weighted estimate: CJK ~1.5 chars/token, ASCII ~4 chars/token
        other_chars = total_chars - total_cjk - total_ascii
        estimated = (total_cjk / 1.5) + (total_ascii / 4.0) + (other_chars / 3.5)
        return max(int(estimated), 1)

    def _persist_turn(self, session, messages: list[dict]) -> None:
        """Persist the current messages state to the session store (gap-04).

        Converts API-format dict messages to Message objects and appends
        them to the session, then writes transcripts to disk. Best-effort:
        failures are logged but do not interrupt the agent loop.
        """
        if not self.session_store or not session:
            return
        try:
            from datetime import datetime as _dt

            from myagent.context.builder import Message as CtxMessage
            from myagent.context.builder import ToolCallRecord
            # Persist only the new messages since last save: track a _persist_idx
            persist_idx = getattr(session, '_persist_idx', 0)
            new_msgs = messages[persist_idx:]
            if not new_msgs:
                return
            for m in new_msgs:
                if m.get("role") == "system":
                    continue
                # Extract tool_call_id, name, and tool_calls from dict messages
                # so that /history can display tool call details (gap-16-06).
                tc_id = m.get("tool_call_id")
                tc_name = m.get("name")
                tc_list = None
                raw_tool_calls = m.get("tool_calls")
                if raw_tool_calls:
                    tc_list = []
                    for tc in raw_tool_calls:
                        func_info = tc.get("function", {})
                        tc_list.append(ToolCallRecord(
                            call_id=tc.get("id", ""),
                            tool_name=func_info.get("name", "?"),
                            params=func_info.get("arguments", {}),
                        ))
                msg_obj = CtxMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    tool_call_id=tc_id,
                    name=tc_name,
                    tool_calls=tc_list,
                    timestamp=_dt.now(),
                )
                session.add_message(msg_obj)
            session._persist_idx = len(messages)
            # Write transcripts to disk
            if hasattr(session, 'project_name') and hasattr(session, 'project_hash'):
                sess_dir = self.session_store._session_dir(
                    session.project_name, session.project_hash, session.id
                )
                self.session_store._write_transcripts(sess_dir, session)
        except Exception:
            logger.exception(
                "Failed to persist turn to session store",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "agent.persist_turn",
                },
            )
