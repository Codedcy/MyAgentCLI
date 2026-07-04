"""Stream renderer — converts AgentEvent stream to Rich renderables."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("myagent.cli.renderer")


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

    def __init__(self, syntax_highlight: bool = True):
        self._syntax_highlight = syntax_highlight

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
            "StatusUpdate": self._render_status_update,
        }

        handler = handlers.get(event_type)
        if handler:
            return handler(event)
        return None

    def _render_text(self, event):
        from rich.text import Text
        content = event.content

        # G4: Syntax highlight fenced code blocks in output when enabled
        if self._syntax_highlight and "```" in content:
            return self._render_with_code_highlight(content)

        return Text(content)

    def _render_with_code_highlight(self, content: str):
        """Render text with code blocks syntax-highlighted via Rich Syntax."""
        from rich.syntax import Syntax
        from rich.text import Text

        # Split by fenced code blocks: ```lang\ncode\n```
        pattern = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
        parts = []
        last_end = 0

        for m in pattern.finditer(content):
            # Add text before this code block
            before = content[last_end:m.start()]
            if before:
                parts.append(Text(before))

            lang = m.group(1) or "text"
            code = m.group(2)
            try:
                parts.append(Syntax(code, lang, theme="monokai", word_wrap=True))
            except Exception:
                logger.exception(
                    "Syntax highlighting failed; rendering plain code block",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "cli_render_syntax_highlight",
                    },
                )
                # Fall back to plain text for unsupported languages
                parts.append(Text(f"```{lang}\n{code}\n```"))
            last_end = m.end()

        # Add remaining text after last code block
        remaining = content[last_end:]
        if remaining:
            parts.append(Text(remaining))

        if len(parts) == 1:
            return parts[0]
        return parts  # Return list for Rich to render sequentially

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

    def _render_status_update(self, event):
        return None
