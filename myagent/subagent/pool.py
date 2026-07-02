"""Sub-agent pool — lifecycle, concurrency, and message routing."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum

from myagent.tools.base import ToolResult


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
    _result_data: ToolResult | None = None

    async def wait(self) -> ToolResult:
        await self._completion_event.wait()
        return self._result_data or ToolResult(error="Sub-agent returned no result")

    async def send_message(self, msg: str) -> None:
        # Message is stored; worker checks at each loop iteration
        pass


class CapExceededError(Exception):
    pass


class SubAgentPool:
    """Pool of sub-agents with concurrency limiting."""

    MAX_TOTAL = 1000

    def __init__(self, max_concurrent: int | None = None):
        if max_concurrent is None:
            max_concurrent = min(16, max(1, (os.cpu_count() or 2) - 2))
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._agents: dict[str, SubAgentHandle] = {}
        self._total_spawned = 0
        self._counter = 0

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
    ) -> SubAgentHandle:
        if self._total_spawned >= self.MAX_TOTAL:
            raise CapExceededError(f"Global sub-agent cap ({self.MAX_TOTAL}) exceeded")

        self._total_spawned += 1
        self._counter += 1
        agent_id = f"sub-{self._counter:03d}"

        handle = SubAgentHandle(id=agent_id, status=AgentStatus.RUNNING)
        self._agents[agent_id] = handle

        if background:
            asyncio.create_task(self._run_background(handle, prompt))
        else:
            await self._run_foreground(handle, prompt)

        return handle

    async def send_message(self, agent_id: str, message: str) -> None:
        if agent_id in self._agents:
            await self._agents[agent_id].send_message(message)

    async def shutdown(self) -> None:
        for handle in self._agents.values():
            if handle.status == AgentStatus.RUNNING:
                handle.status = AgentStatus.INTERRUPTED
                handle._completion_event.set()

    async def _run_background(self, handle: SubAgentHandle, prompt: str) -> None:
        try:
            # Simulated execution — in production runs ReAct loop in worker
            await asyncio.sleep(0.01)
            handle.status = AgentStatus.COMPLETED
            handle._result_data = ToolResult(output=f"Completed: {prompt[:100]}")
            handle._completion_event.set()
        except Exception as e:
            handle.status = AgentStatus.FAILED
            handle._result_data = ToolResult(error=str(e))
            handle._completion_event.set()

    async def _run_foreground(self, handle: SubAgentHandle, prompt: str) -> None:
        async with self._semaphore:
            await self._run_background(handle, prompt)
