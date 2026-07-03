"""Built-in agent tools: spawn_subagent, send_message.

Uses stubs for SubAgentPool and MemoryStore when real implementations
are not yet available (Task 9 + Task 10). The stub contract matches the
final public interface so no code changes are needed when replacing.
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
            handle = await pool.spawn(
                prompt=params["prompt"],
                tools=params.get("tools"),
                mode=params.get("mode", "Think High"),
                isolation=params.get("isolation"),
                schema=params.get("schema"),
                background=params.get("background", True),
                parent_session=context.session_id,
                config=context.config,
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

        try:
            await pool.send_message(params["to"], params["message"])
            return ToolResult(
                output=f"Message sent to {params['to']}",
            )
        except Exception as e:
            return ToolResult(error=str(e))
