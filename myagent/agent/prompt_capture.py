"""In-memory capture of the latest main-agent LLM prompt."""

from __future__ import annotations

import contextlib
import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable copy, stringifying unsupported values."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


@dataclass(frozen=True)
class LastPromptCapture:
    captured_at: str
    model: str
    thinking: str
    estimated_tokens: int | None
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]

    @classmethod
    def capture(
        cls,
        *,
        model: str,
        thinking: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        estimated_tokens: int | None = None,
        captured_at: str | None = None,
    ) -> LastPromptCapture:
        return cls(
            captured_at=captured_at or datetime.now(UTC).isoformat(
                timespec="seconds"
            ),
            model=model,
            thinking=thinking,
            estimated_tokens=estimated_tokens,
            messages=copy.deepcopy(messages),
            tools=copy.deepcopy(tools or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "model": self.model,
            "thinking": self.thinking,
            "estimated_tokens": self.estimated_tokens,
            "message_count": len(self.messages),
            "tool_count": len(self.tools),
            "messages": _json_safe(self.messages),
            "tools": _json_safe(self.tools),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        lines = [
            "Last LLM prompt",
            f"Captured: {self.captured_at}",
            f"Model: {self.model}",
            f"Thinking: {self.thinking}",
            (
                f"Messages: {len(self.messages)} | Tools: {len(self.tools)} | "
                f"Estimated tokens: {self.estimated_tokens or 'unknown'}"
            ),
            "",
            "Messages:",
        ]
        for index, message in enumerate(self.messages, 1):
            role = message.get("role", "unknown")
            lines.append(f"[{index}] {role}")
            lines.extend(self._message_lines(message))
            lines.append("")

        if self.tools:
            lines.append("Tools:")
            for index, tool in enumerate(self.tools, 1):
                name = self._tool_name(tool)
                lines.append(f"[{index}] {name}")
                lines.append(json.dumps(_json_safe(tool), ensure_ascii=False, indent=2))
                lines.append("")
        else:
            lines.append("Tools: none")

        return "\n".join(lines).rstrip()

    def _message_lines(self, message: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        role = message.get("role", "")
        tool_calls = message.get("tool_calls") or []

        if role == "tool":
            metadata = []
            if message.get("name"):
                metadata.append(f"name: {message['name']}")
            if message.get("tool_call_id"):
                metadata.append(f"tool_call_id: {message['tool_call_id']}")
            if metadata:
                lines.append("Tool result metadata:")
                lines.extend(metadata)

        content = message.get("content", "")
        if content is None and tool_calls:
            lines.append(
                "(content is null because this assistant message contains tool calls)"
            )
        elif content is not None:
            lines.append(self._format_message_content(content))

        if tool_calls:
            lines.append("Tool calls:")
            for index, tool_call in enumerate(tool_calls, 1):
                lines.extend(self._tool_call_lines(index, tool_call))

        extra = {
            key: value
            for key, value in message.items()
            if key not in {"role", "content", "tool_calls", "name", "tool_call_id"}
        }
        if extra:
            lines.append("Extra fields:")
            lines.append(json.dumps(_json_safe(extra), ensure_ascii=False, indent=2))
        return lines

    @staticmethod
    def _format_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        return json.dumps(_json_safe(content), ensure_ascii=False, indent=2)

    def _tool_call_lines(self, index: int, tool_call: dict[str, Any]) -> list[str]:
        function = tool_call.get("function")
        function = function if isinstance(function, dict) else {}
        name = function.get("name") or tool_call.get("name") or "unknown"
        call_id = tool_call.get("id") or "unknown"
        lines = [f"[{index}] {name} (id: {call_id})"]
        arguments = function.get("arguments", {})
        lines.append("Arguments:")
        lines.append(self._format_tool_arguments(arguments))
        return lines

    @staticmethod
    def _format_tool_arguments(arguments: Any) -> str:
        if isinstance(arguments, str):
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(arguments)
                return json.dumps(_json_safe(parsed), ensure_ascii=False, indent=2)
            return arguments
        return json.dumps(_json_safe(arguments), ensure_ascii=False, indent=2)

    @staticmethod
    def _tool_name(tool: dict[str, Any]) -> str:
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        if tool.get("name"):
            return str(tool["name"])
        return "unknown"
