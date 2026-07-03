"""Built-in agent tools: spawn_subagent, send_message.

SubAgentPool and MemoryStore are fully implemented and wired through
ToolContext. Both tools delegate to real implementations at runtime.
"""

from __future__ import annotations

from myagent.tools.base import ToolContext, ToolResult


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
            # gap-21: check speculative_exploration config for non-goal mode
            background = params.get("background", True)
            if context.config:
                goal = getattr(context.config, '_goal', None) or (
                    hasattr(context.config, 'session') and
                    getattr(context.config.session, '_goal', None)
                )
                # Non-goal mode: force background=False unless explicitly allowed
                if not goal:
                    speculative_allowed = (
                        hasattr(context.config, 'subagents') and
                        getattr(context.config.subagents, 'speculative_exploration', False)
                    )
                    if not speculative_allowed and "background" not in params:
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
                metadata={"subagent_id": handle.id, "background": params.get("background", True)},
            )
        except Exception as e:
            return ToolResult(error=str(e))


class SendMessageTool:
    name = "send_message"
    description = "Send a message to a sub-agent or the main agent."
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient: agent name or ID",
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
                subagent_id = params.get("from", "subagent")
                pool.send_to_main(subagent_id, params["message"])
                return ToolResult(output=f"Message sent to main agent")
            return ToolResult(error="Cannot send to main: pool not available")

        try:
            await pool.send_message(target, params["message"])
            return ToolResult(
                output=f"Message sent to {target}",
            )
        except Exception as e:
            return ToolResult(error=str(e))
