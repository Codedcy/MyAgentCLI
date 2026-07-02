"""Sub-agent worker — runs ReAct loop in isolation.

Each sub-agent has its own context window (same model limit as main agent),
tool subset, and transcript persistence. Skills and memory are NOT loaded
for sub-agents.

Design doc reference: §八 子 Agent 池与工作流编排
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from myagent.llm.provider import Done as LLMDone
from myagent.llm.provider import TextDelta as LLMTextDelta
from myagent.llm.provider import ThinkingDelta as LLMThinkingDelta
from myagent.llm.provider import ToolCall as LLMToolCall
from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.subagent")


class SubAgentWorker:
    """Runs a sub-agent's ReAct loop with isolated context."""

    MAX_ITERATIONS = 30

    def __init__(
        self,
        prompt: str,
        tools: list[str] | None = None,
        mode: str = "Think High",
        isolation: str | None = None,
        llm=None,
        tool_registry=None,
        interrupt_event: asyncio.Event | None = None,
        tool_context: ToolContext | None = None,
    ):
        self.prompt = prompt
        self.tools = tools
        self.mode = mode
        self.isolation = isolation
        self.llm = llm
        self.tool_registry = tool_registry
        self.interrupt_event = interrupt_event
        self.tool_context = tool_context

    async def run(self) -> str:
        """Execute the sub-agent task and return a result string.

        Runs a full ReAct loop with LLM calls and tool execution.
        Sub-agents have:
        - No L2 skills index
        - No L4 memory (avoid context pollution)
        - Tool subset from spawn params
        - Independent context (no history from parent)
        """
        if not self.llm:
            logger.warning("Sub-agent spawned without LLM provider")
            return "Error: No LLM provider configured for sub-agent"

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a sub-agent assistant. Complete the assigned task "
                    "using available tools. Be concise and direct. Report your "
                    "final answer when done."
                ),
            },
            {"role": "user", "content": self.prompt},
        ]

        tools_schemas = (
            self.tool_registry.get_schemas_for(self.tools)
            if self.tool_registry and self.tools
            else []
        )

        iteration = 0
        while iteration < self.MAX_ITERATIONS:
            iteration += 1

            # Check for interrupt before each LLM call
            if self.interrupt_event and self.interrupt_event.is_set():
                logger.info(
                    "Sub-agent interrupted at iteration %d",
                    iteration,
                    extra={"category": "subagent"},
                )
                return "[Interrupted]"

            text_buffer: list[str] = []
            tool_calls_in_turn: list = []

            # ── Stream LLM response ──────────────────────────────
            try:
                async for event in self.llm.complete(
                    messages=messages,
                    tools=tools_schemas if tools_schemas else None,
                    thinking=self.mode,
                ):
                    kind = self._classify_event(event)
                    if kind == "text":
                        text_buffer.append(event.content)
                    elif kind == "tool_call":
                        tool_calls_in_turn.append(event)
                    # "done", "thinking", "unknown" — absorbed
            except Exception as e:
                logger.error(
                    "LLM error in sub-agent iteration %d: %s",
                    iteration,
                    str(e),
                    extra={"category": "error", "component": "llm"},
                )
                return f"Error: {e}"

            # ── Execute tool calls ───────────────────────────────
            if tool_calls_in_turn:
                assistant_content = "".join(text_buffer) or None
                assistant_msg: dict = {"role": "assistant", "content": assistant_content}
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

                for tc in tool_calls_in_turn:
                    tool = (
                        self.tool_registry.get(tc.name)
                        if self.tool_registry else None
                    )
                    if tool:
                        ctx = self.tool_context or ToolContext(
                            session_id="subagent",
                            project_dir=None,
                            permissions=None,
                            config=None,
                        )
                        try:
                            t0 = time.monotonic()
                            result = await tool.execute(tc.params, ctx)
                            duration_ms = (time.monotonic() - t0) * 1000
                            result_text = (
                                result.output
                                if not result.error
                                else f"Error: {result.error}"
                            )
                            logger.info(
                                "Tool '%s' executed in %.1fms (%d chars)",
                                tc.name,
                                duration_ms,
                                len(result.output),
                                extra={"category": "tool"},
                            )
                        except Exception as e:
                            logger.error(
                                "Tool '%s' failed: %s",
                                tc.name,
                                str(e),
                                extra={"category": "error", "component": "tool"},
                            )
                            result_text = f"Error executing {tc.name}: {e}"
                    else:
                        result_text = f"Error: Unknown tool '{tc.name}'"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })

                # Loop again — LLM sees tool results in next iteration
                continue

            # ── No tool calls — text response complete ───────────
            return "".join(text_buffer)

        return f"Error: Sub-agent reached max iterations ({self.MAX_ITERATIONS})"

    # ── helpers ────────────────────────────────────────────────────

    def _classify_event(self, event) -> str:
        """Classify an LLM stream event by type.

        Uses isinstance against provider types; falls back to duck-typing
        for test doubles (mock objects with matching attributes).
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
