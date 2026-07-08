"""Shared metadata for chat control commands."""

IMMEDIATE_CHAT_COMMANDS = frozenset({"goal", "prompt", "subagent", "subagents"})


def slash_command_name(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return ""
    return stripped[1:].split(maxsplit=1)[0].lower()


def slash_command_args(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return ""
    parts = stripped[1:].split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def is_immediate_chat_command(text: str) -> bool:
    return slash_command_name(text) in IMMEDIATE_CHAT_COMMANDS
