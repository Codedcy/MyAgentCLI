"""Slash command dispatch — /mode, /goal, /skills, /dream, /clear, /history, /exit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CommandContext:
    engine: Any = None
    goal_tracker: Any = None
    skill_registry: Any = None
    dream_engine: Any = None
    session_manager: Any = None
    session: Any = None
    config: Any = None


@dataclass
class CommandResult:
    output: str
    success: bool = True
    exit_requested: bool = False
    skill_invoked: str | None = None  # skill name if a skill was invoked via /skill-name


class CommandDispatcher:
    """Dispatches slash commands to registered handlers."""

    def __init__(self):
        self._commands: dict[str, callable] = {}
        self._register_defaults()

    def _register_defaults(self):
        self._commands["mode"] = self._cmd_mode
        self._commands["goal"] = self._cmd_goal
        self._commands["skills"] = self._cmd_skills
        self._commands["dream"] = self._cmd_dream
        self._commands["clear"] = self._cmd_clear
        self._commands["history"] = self._cmd_history
        self._commands["exit"] = self._cmd_exit
        self._commands["quit"] = self._cmd_exit

    async def dispatch(self, line: str, ctx: CommandContext) -> CommandResult:
        line = line.strip()
        if not line.startswith("/"):
            return CommandResult(output="", success=False)

        parts = line[1:].split(maxsplit=1)
        cmd_name = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        handler = self._commands.get(cmd_name)
        if handler:
            return await handler(args, ctx)

        # Check if cmd_name matches a registered skill (gap-2-01: /skill-name forced invocation)
        if ctx.skill_registry:
            skill = ctx.skill_registry.get(cmd_name)
            if skill:
                return CommandResult(
                    output=f"Skill invoked: /{cmd_name} — {skill.description}",
                    success=True,
                    skill_invoked=skill.name,
                )

        return CommandResult(output=f"Unknown command: /{cmd_name}", success=False)

    async def _cmd_mode(self, args: str, ctx: CommandContext) -> CommandResult:
        mode_map = {
            "think-high": "Think High",
            "think-max": "Think Max",
            "non-think": "Non-think",
        }
        if args in mode_map:
            mode = mode_map[args]
            if ctx.config:
                ctx.config.model.thinking = mode
            return CommandResult(output=f"Thinking mode: {mode}")
        return CommandResult(output=f"Usage: /mode think-high|think-max|non-think")

    async def _cmd_goal(self, args: str, ctx: CommandContext) -> CommandResult:
        if not args:
            goal = ctx.goal_tracker.get_goal() if ctx.goal_tracker else None
            return CommandResult(output=f"Current goal: {goal or 'None'}")
        if args == "clear":
            if ctx.goal_tracker:
                ctx.goal_tracker.clear_goal()
            return CommandResult(output="Goal cleared.")
        if ctx.goal_tracker:
            ctx.goal_tracker.set_goal(args)
        return CommandResult(output=f"Goal set: {args}")

    async def _cmd_skills(self, args: str, ctx: CommandContext) -> CommandResult:
        if ctx.skill_registry:
            skills = ctx.skill_registry.list_all()
            lines = ["Available skills:"]
            for s in skills:
                lines.append(f"  /{s.name} — {s.description}")
            return CommandResult(output="\n".join(lines))
        return CommandResult(output="Skill registry not available.")

    async def _cmd_dream(self, args: str, ctx: CommandContext) -> CommandResult:
        if ctx.dream_engine:
            result = await ctx.dream_engine.run()
            return CommandResult(output=f"Dream cycle completed. Log: {result.log_path}")
        return CommandResult(output="Dream engine not available.")

    async def _cmd_clear(self, args: str, ctx: CommandContext) -> CommandResult:
        """Clear in-memory conversation messages while preserving disk transcripts."""
        cleared = 0
        if ctx.session is not None and hasattr(ctx.session, "_messages"):
            cleared = len(ctx.session._messages)
            ctx.session._messages.clear()
        return CommandResult(
            output=f"Conversation cleared: {cleared} messages removed (transcripts preserved on disk)."
        )

    async def _cmd_history(self, args: str, ctx: CommandContext) -> CommandResult:
        """Show real conversation history from session."""
        if ctx.session is None or not hasattr(ctx.session, "_messages"):
            return CommandResult(output="No conversation history available.")

        messages = ctx.session._messages
        if not messages:
            return CommandResult(output="No conversation history yet.")

        n = 20  # default
        if args.strip().isdigit():
            n = int(args.strip())

        recent = messages[-n:]
        lines = [f"Recent conversation history (last {len(recent)} of {len(messages)} turns):", ""]
        for i, m in enumerate(recent, 1):
            role = m.role.upper() if hasattr(m, "role") else "?"
            content = m.content if hasattr(m, "content") else str(m)
            preview = content[:120] + "..." if len(content) > 120 else content
            lines.append(f"  {i}. [{role}] {preview}")
        return CommandResult(output="\n".join(lines))

    async def _cmd_exit(self, args: str, ctx: CommandContext) -> CommandResult:
        """Request exit with confirmation flag for the REPL layer."""
        if args.strip().lower() in ("--force", "-f"):
            return CommandResult(output="Goodbye!", success=True, exit_requested=True)
        return CommandResult(
            output="Goodbye! (Type /exit --force or /quit --force to confirm, or /exit -f)",
            success=True,
            exit_requested=False,
        )
