"""Sub-agent pool — lifecycle, concurrency, and message routing."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.subagent")


class AgentStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass
class SubAgentHandle:
    id: str
    status: AgentStatus = AgentStatus.CREATED
    result: ToolResult | None = None
    _completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    _interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    _result_data: ToolResult | None = None
    _message: str | None = None

    async def wait(self) -> ToolResult:
        await self._completion_event.wait()
        return self._result_data or ToolResult(error="Sub-agent returned no result")

    async def send_message(self, msg: str) -> None:
        """Store message; trigger interrupt if 'stop'."""
        self._message = msg
        if msg.lower() == "stop":
            self._interrupt_event.set()


class CapExceededError(Exception):
    pass


class SubAgentPool:
    """Pool of sub-agents with concurrency limiting.

    Accepts optional llm, tool_registry, and tool_context at pool level;
    spawn() allows per-invocation overrides.
    """

    MAX_TOTAL = 1000

    def __init__(
        self,
        max_concurrent: int | None = None,
        llm=None,
        tool_registry=None,
        tool_context: ToolContext | None = None,
    ):
        if max_concurrent is None:
            max_concurrent = min(16, max(1, (os.cpu_count() or 2) - 2))
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._agents: dict[str, SubAgentHandle] = {}
        self._total_spawned = 0
        self._counter = 0
        self._llm = llm
        self._tool_registry = tool_registry
        self._tool_context = tool_context

    @property
    def active_count(self) -> int:
        return sum(1 for a in self._agents.values() if a.status == AgentStatus.RUNNING)

    async def spawn(
        self,
        prompt: str,
        tools: list[str] | None = None,
        mode: str = "Think High",
        isolation: str | None = None,
        schema: dict | None = None,
        background: bool = True,
        parent_session: str | None = None,
        model: str | None = None,
        llm=None,
        tool_registry=None,
        tool_context=None,
    ) -> SubAgentHandle:
        if self._total_spawned >= self.MAX_TOTAL:
            raise CapExceededError(f"Global sub-agent cap ({self.MAX_TOTAL}) exceeded")

        self._total_spawned += 1
        self._counter += 1
        agent_id = f"sub-{self._counter:03d}"

        handle = SubAgentHandle(id=agent_id, status=AgentStatus.RUNNING)
        self._agents[agent_id] = handle

        # Per-invocation overrides fall back to pool-level defaults
        _llm = llm or self._llm
        _tool_registry = tool_registry or self._tool_registry
        _tool_context = tool_context or self._tool_context

        if background:
            asyncio.create_task(
                self._run_background(
                    handle, prompt, tools, mode, model, _llm, _tool_registry,
                    handle._interrupt_event, _tool_context,
                )
            )
        else:
            await self._run_foreground(
                handle, prompt, tools, mode, model, _llm, _tool_registry,
                handle._interrupt_event, _tool_context,
            )

        return handle

    async def send_message(self, agent_id: str, message: str) -> None:
        """Send a message to a running sub-agent."""
        if agent_id in self._agents:
            await self._agents[agent_id].send_message(message)

    async def shutdown(self) -> None:
        """Interrupt all running sub-agents. Let _run_background finish naturally."""
        for handle in self._agents.values():
            if handle.status == AgentStatus.RUNNING:
                handle.status = AgentStatus.INTERRUPTED
                handle._interrupt_event.set()

    # ── internal ──────────────────────────────────────────────────

    async def _run_background(
        self,
        handle: SubAgentHandle,
        prompt: str,
        tools: list[str] | None,
        mode: str,
        model: str | None,
        llm,
        tool_registry,
        interrupt_event: asyncio.Event,
        tool_context: ToolContext | None,
    ) -> None:
        """Run a sub-agent worker under the concurrency semaphore."""
        async with self._semaphore:
            from myagent.subagent.worker import SubAgentWorker

            worker = SubAgentWorker(
                prompt=prompt,
                tools=tools,
                mode=mode,
                model=model,
                llm=llm,
                tool_registry=tool_registry,
                interrupt_event=interrupt_event,
                tool_context=tool_context,
            )
            try:
                logger.info(
                    "Sub-agent %s starting",
                    handle.id,
                    extra={"category": "subagent"},
                )
                t0 = time.monotonic()
                output = await worker.run()
                duration_ms = (time.monotonic() - t0) * 1000
                handle.status = AgentStatus.COMPLETED
                handle._result_data = ToolResult(output=output)
                logger.info(
                    "Sub-agent %s completed in %.1fms",
                    handle.id,
                    duration_ms,
                    extra={"category": "subagent"},
                )
            except Exception as e:
                logger.error(
                    "Sub-agent %s failed: %s",
                    handle.id,
                    str(e),
                    extra={"category": "error", "component": "subagent"},
                )
                handle.status = AgentStatus.FAILED
                handle._result_data = ToolResult(error=str(e))
            finally:
                handle._completion_event.set()

    async def _run_foreground(
        self,
        handle: SubAgentHandle,
        prompt: str,
        tools: list[str] | None,
        mode: str,
        model: str | None,
        llm,
        tool_registry,
        interrupt_event: asyncio.Event,
        tool_context: ToolContext | None,
    ) -> None:
        """Run a sub-agent worker in foreground (caller awaits)."""
        await self._run_background(
            handle, prompt, tools, mode, model, llm, tool_registry,
            interrupt_event, tool_context,
        )
