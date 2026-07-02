"""Stream renderer — converts AgentEvent stream to Rich renderables."""

from __future__ import annotations

from typing import Any


class Renderer:
    """Converts AgentEvent instances to Rich renderable objects.

    Event mapping:
    - TextChunk → streamed inline (live update)
    - ThinkingChunk → dim/collapsed
    - ToolCallStart → tool name + params preview
    - ToolCallEnd → result summary
    - Done → final usage stats
    - Error → red panel
    """

    def render_event(self, event: Any) -> Any:
        """Dispatch by event type name to the appropriate renderer."""
        event_type = type(event).__name__

        handlers = {
            "TextChunk": self._render_text,
            "ThinkingChunk": self._render_thinking,
            "ToolCallStart": self._render_tool_start,
            "ToolCallEnd": self._render_tool_end,
            "Done": self._render_done,
            "Error": self._render_error,
        }

        handler = handlers.get(event_type)
        if handler:
            return handler(event)
        return None

    def _render_text(self, event):
        from rich.text import Text
        return Text(event.content)

    def _render_thinking(self, event):
        from rich.text import Text
        return Text(event.content, style="dim italic")

    def _render_tool_start(self, event):
        from rich.panel import Panel
        return Panel(f"Tool: {event.name}", style="blue")

    def _render_tool_end(self, event):
        from rich.panel import Panel
        if event.result.error:
            return Panel(f"Error: {event.result.error}", style="red")
        preview = event.result.output[:200]
        return Panel(preview, style="green", title="Result")

    def _render_done(self, event):
        from rich.text import Text
        return Text("✓ Done", style="bold green")

    def _render_error(self, event):
        from rich.panel import Panel
        return Panel(event.message, style="red", title="Error")
