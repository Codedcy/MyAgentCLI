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
    RESULT_CONSUMED = "result_consumed"


@dataclass
class SubAgentHandle:
    id: str
    status: AgentStatus = AgentStatus.CREATED
    result: ToolResult | None = None
    _completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    _interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    _result_data: ToolResult | None = None
    _message: str | None = None
    _pending_messages: list = field(default_factory=list)

    async def wait(self) -> ToolResult:
        await self._completion_event.wait()
        result = self._result_data or ToolResult(error="Sub-agent returned no result")
        if self.status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.INTERRUPTED):
            self.status = AgentStatus.RESULT_CONSUMED
        return result

    async def send_message(self, msg: str) -> None:
        """Store message for worker consumption.

        Non-stop messages are queued for the worker to read at iteration start.
        'stop' messages set the interrupt event and are also queued.
        """
        self._message = msg
        self._pending_messages.append(msg)
        if msg.lower() == "stop":
            self._interrupt_event.set()

    def send_to_main(self, pool, message: str) -> None:
        """G10: Send a message from this sub-agent to the main agent."""
        if pool and hasattr(pool, 'send_to_main'):
            pool.send_to_main(self.id, message)


async def _persist_subagent_transcript(session_store, session, handle, worker, duration_ms, output):
    """Persist sub-agent transcript to session's subagents/ directory (gap-07)."""
    import json
    from pathlib import Path

    try:
        if hasattr(session, 'project_name') and hasattr(session, 'project_hash'):
            sess_dir = session_store._session_dir(
                session.project_name, session.project_hash, session.id
            )
        else:
            return

        sub_dir = sess_dir / "subagents" / handle.id
        sub_dir.mkdir(parents=True, exist_ok=True)

        # Build transcript messages from worker's collected data
        transcript_messages = getattr(worker, '_transcript_messages', [])
        transcript_tool_calls = getattr(worker, '_transcript_tool_calls', [])

        # JSON transcript
        ts_data = {
            "subagent_id": handle.id,
            "parent_session": session.id,
            "status": handle.status.value,
            "duration_ms": duration_ms,
            "prompt": getattr(worker, 'prompt', ''),
            "output": output,
            "iterations": getattr(worker, 'MAX_ITERATIONS', 30),
            "messages": transcript_messages,
            "tool_calls": transcript_tool_calls,
        }
        (sub_dir / "transcript.json").write_text(
            json.dumps(ts_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Markdown transcript
        md_lines = [
            f"# Sub-agent: {handle.id}",
            f"Status: {handle.status.value}",
            f"Duration: {duration_ms:.0f}ms",
            f"Prompt: {getattr(worker, 'prompt', '')}",
            "",
            "## Output",
            output,
            "",
        ]
        (sub_dir / "transcript.md").write_text(
            "\n".join(md_lines), encoding="utf-8",
        )
    except Exception:
        pass  # Best-effort persistence


class CapExceededError(Exception):
    pass


class SubAgentPool:
    """Pool of sub-agents with concurrency limiting.

    Accepts optional llm, tool_registry, and tool_context at pool level;
    spawn() allows per-invocation overrides.

    G10: Outbound message queue for sub-agent-to-main-agent communication.
    Sub-agents write messages via SubAgentHandle.send_to_main(); the main
    agent drains this queue between ReAct iterations.
    """

    MAX_TOTAL = 1000

    def __init__(
        self,
        max_concurrent: int | None = None,
        llm=None,
        tool_registry=None,
        tool_context: ToolContext | None = None,
        session_store=None,
        session=None,
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
        self._session_store = session_store
        self._session = session
        # G10: Outbound message queue for sub→main communication
        self._outbound_queue: asyncio.Queue[dict] = asyncio.Queue()

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
        config=None,
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

        project_ctx = getattr(_tool_context, 'project_context', None) if _tool_context else None

        if background:
            asyncio.create_task(
                self._run_background(
                    handle, prompt, tools, mode, model, _llm, _tool_registry,
                    handle._interrupt_event, _tool_context, project_ctx, handle._pending_messages,
                    isolation=isolation, schema=schema, parent_session=parent_session,
                )
            )
        else:
            await self._run_foreground(
                handle, prompt, tools, mode, model, _llm, _tool_registry,
                handle._interrupt_event, _tool_context, project_ctx, handle._pending_messages,
                isolation=isolation, schema=schema, parent_session=parent_session,
            )

        return handle

    async def send_message(self, agent_id: str, message: str) -> None:
        """Send a message to a running sub-agent."""
        if agent_id in self._agents:
            await self._agents[agent_id].send_message(message)

    def send_to_main(self, subagent_id: str, message: str) -> None:
        """G10: Enqueue a message from a sub-agent to the main agent.

        Called by sub-agent workers when they want to report progress or
        ask for guidance. The main agent drains these between iterations.
        """
        self._outbound_queue.put_nowait({
            "from": subagent_id,
            "message": message,
            "timestamp": time.time(),
        })

    def drain_outbound_messages(self) -> list[dict]:
        """G10: Drain all pending outbound messages from sub-agents.

        Returns a list of {from, message, timestamp} dicts.
        Called by the main agent between ReAct iterations.
        """
        messages = []
        while not self._outbound_queue.empty():
            try:
                messages.append(self._outbound_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

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
        project_context=None,
        message_store: list | None = None,
        isolation: str | None = None,
        schema: dict | None = None,
        parent_session: str | None = None,
    ) -> None:
        """Run a sub-agent worker under the concurrency semaphore."""
        async with self._semaphore:
            from myagent.subagent.worker import SubAgentWorker

            worker = SubAgentWorker(
                prompt=prompt,
                tools=tools,
                mode=mode,
                isolation=isolation,
                schema=schema,
                model=model,
                llm=llm,
                tool_registry=tool_registry,
                interrupt_event=interrupt_event,
                tool_context=tool_context,
                project_context=project_context,
                message_store=message_store,
                project_dir=getattr(tool_context, 'project_dir', None) if tool_context else None,
            )
            try:
                # Format prompt_summary: first ~100 chars of the spawn prompt
                prompt_summary = prompt[:100] if prompt else ""
                ps = getattr(self._session, 'id', None) if self._session else None
                actual_parent = parent_session or ps
                logger.info(
                    "Sub-agent %s starting",
                    handle.id,
                    extra={
                        "category": "subagent",
                        "event": "spawned",
                        "subagent_id": handle.id,
                        "parent_session": actual_parent,
                        "prompt_summary": prompt_summary,
                    },
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
                    extra={
                        "category": "subagent",
                        "event": "completed",
                        "subagent_id": handle.id,
                        "parent_session": actual_parent,
                        "prompt_summary": prompt_summary,
                        "duration_ms": round(duration_ms, 1),
                    },
                )
                # Persist sub-agent transcript (gap-07)
                if self._session_store and self._session:
                    await _persist_subagent_transcript(
                        self._session_store, self._session, handle, worker,
                        duration_ms, output,
                    )
            except Exception as e:
                logger.error(
                    "Sub-agent %s failed: %s",
                    handle.id,
                    str(e),
                    extra={
                        "category": "error",
                        "component": "subagent",
                        "subagent_id": handle.id,
                        "event": "failed",
                        "context": "subagent_run",
                    },
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
        project_context=None,
        message_store: list | None = None,
        isolation: str | None = None,
        schema: dict | None = None,
        parent_session: str | None = None,
    ) -> None:
        """Run a sub-agent worker in foreground (caller awaits)."""
        await self._run_background(
            handle, prompt, tools, mode, model, llm, tool_registry,
            interrupt_event, tool_context, project_context, message_store,
            isolation=isolation, schema=schema, parent_session=parent_session,
        )
