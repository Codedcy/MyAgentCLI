"""Built-in agent tools: spawn_subagent, send_message.

SubAgentPool and MemoryStore are fully implemented and wired through
ToolContext. Both tools delegate to real implementations at runtime.
"""

from __future__ import annotations

import logging

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.tools.agent")


class SpawnSubagentTool:
    name = "spawn_subagent"
    description = "Create a sub-agent to execute an independent task."
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["Think High", "Think Max", "Non-think"]},
            "isolation": {"type": "string", "enum": ["worktree"]},
            "schema": {"type": "object"},
            "background": {"type": "boolean", "default": True},
        },
        "required": ["prompt"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        pool = context.subagent_pool
        if pool is None:
            return ToolResult(
                output="Sub-agent pool not available. Task would be: " + params["prompt"],
                metadata={"deferred": True},
            )

        try:
            # gap-21 (G1 fixed): check speculative_exploration config for non-goal mode
            # Use goal_tracker from ToolContext to detect if session is in goal mode.
            background = params.get("background", True)
            if context.config:
                goal_tracker = getattr(context, 'goal_tracker', None)
                goal = (
                    goal_tracker.get_goal()
                    if goal_tracker and hasattr(goal_tracker, 'get_goal')
                    else None
                )
                # Non-goal mode: force background=False unless explicitly allowed.
                # gap-13-03: Always enforce the gate regardless of what the model
                # passes in params. The model cannot bypass the config gate by
                # explicitly passing background=True.
                if not goal:
                    speculative_allowed = (
                        hasattr(context.config, 'subagents') and
                        getattr(context.config.subagents, 'speculative_exploration', False)
                    )
                    if not speculative_allowed:
                        background = False

            handle = await pool.spawn(
                prompt=params["prompt"],
                tools=params.get("tools"),
                mode=params.get("mode", "Think High"),
                isolation=params.get("isolation"),
                schema=params.get("schema"),
                background=background,
                parent_session=context.session_id,
                config=context.config,
                tool_context=context,
            )
            return ToolResult(
                output=f"Sub-agent spawned: {handle.id}",
                metadata={"subagent_id": handle.id, "background": background},
            )
        except Exception as e:
            logger.exception(
                "Spawn sub-agent tool failed",
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "spawn_subagent.execute",
                },
            )
            return ToolResult(error=str(e))


class SendMessageTool:
    name = "send_message"
    description = "Send a message to a sub-agent or the main agent."
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient: agent name or ID, or 'main' to send to the main agent",
            },
            "from": {
                "type": "string",
                "description": (
                    "Sender agent ID (populated automatically in sub-agent "
                    "context). When calling from a sub-agent to 'main', the "
                    "sub-agent's own ID is used if this field is omitted."
                ),
            },
            "summary": {
                "type": "string",
                "description": "Short summary for UI display",
                "maxLength": 200,
            },
            "message": {
                "type": "string",
                "description": "Plain text message content",
            },
        },
        "required": ["to", "message"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        pool = context.subagent_pool
        if pool is None:
            return ToolResult(
                output=f"Message to '{params['to']}' queued: {params['message'][:100]}",
                metadata={"deferred": True},
            )

        target = params["to"]

        # G10: Support sub-agent-to-main-agent messaging
        if target == "main":
            if hasattr(pool, 'send_to_main'):
                # Called from a sub-agent context — use its own ID
                subagent_id = params.get("from") or getattr(
                    context, "current_subagent_id", None
                )
                if not subagent_id:
                    return ToolResult(
                        error="Cannot send to main: sender sub-agent ID unavailable"
                    )
                pool.send_to_main(subagent_id, params["message"])
                return ToolResult(output="Message sent to main agent")
            return ToolResult(error="Cannot send to main: pool not available")

        try:
            await pool.send_message(target, params["message"])
            return ToolResult(
                output=f"Message sent to {target}",
            )
        except Exception as e:
            logger.exception(
                "Send message tool failed",
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "send_message.execute",
                },
            )
            return ToolResult(error=str(e))
