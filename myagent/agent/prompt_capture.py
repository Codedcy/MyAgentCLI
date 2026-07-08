"""In-memory capture of the latest main-agent LLM prompt."""

from __future__ import annotations

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
            content = message.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            lines.append(f"[{index}] {role}")
            lines.append(content)
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

    @staticmethod
    def _tool_name(tool: dict[str, Any]) -> str:
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        if tool.get("name"):
            return str(tool["name"])
        return "unknown"
