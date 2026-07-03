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
        self._commands["compact"] = self._cmd_compact
        self._commands["history"] = self._cmd_history
        self._commands["export"] = self._cmd_export
        self._commands["help"] = self._cmd_help
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

    async def _cmd_export(self, args: str, ctx: CommandContext) -> CommandResult:
        """Export the current session transcript to a file (gap-18-02).

        Usage: /export [markdown|json]

        Exports the current session transcript to the session's export/
        subdirectory. Matches the behavior of the --export CLI flag but
        is available mid-session from the REPL.
        """
        fmt = args.strip().lower() or "markdown"
        if fmt not in ("markdown", "json"):
            return CommandResult(
                output="Usage: /export [markdown|json]\nExport session transcript to a file.",
                success=False,
            )

        session = ctx.session
        if not session:
            return CommandResult(
                output="No active session to export.",
                success=False,
            )

        session_mgr = ctx.session_manager
        if not session_mgr:
            return CommandResult(
                output="Session manager not available — cannot export.",
                success=False,
            )

        try:
            # Resolve project_dir from session or config
            from pathlib import Path
            project_dir = Path.cwd()

            path = await session_mgr.export_session(session.id, fmt, project_dir)
            if path:
                return CommandResult(
                    output=f"Session exported to: {path}",
                    success=True,
                )
            return CommandResult(
                output="Export failed — no output path returned.",
                success=False,
            )
        except Exception as e:
            return CommandResult(
                output=f"Export failed: {e}",
                success=False,
            )

    async def _cmd_help(self, args: str, ctx: CommandContext) -> CommandResult:
        """List all available slash commands with brief descriptions."""
        lines = [
            "Available commands:",
            "",
            "  /mode think-high|think-max|non-think — Switch thinking mode",
            "  /goal [text|clear]                    — Set, view, or clear current goal",
            "  /skills                               — List available skills",
            "  /dream                                — Run memory consolidation dream cycle",
            "  /compact                              — Non-destructively compress conversation context",
            "  /clear                                — Clear in-memory conversation (preserves transcripts)",
            "  /export [markdown|json]               — Export current session transcript",
            "  /history [N]                          — Show recent conversation history",
            "  /help                                 — Show this help message",
            "  /exit, /quit                          — Exit MyAgentCLI",
            "",
            "You can also type /<skill-name> to invoke a registered skill.",
            "Use Ctrl+C to interrupt a running agent, Ctrl+D to exit.",
        ]
        return CommandResult(output="\n".join(lines))

    async def _cmd_compact(self, args: str, ctx: CommandContext) -> CommandResult:
        """Trigger manual context compression (G7).

        Non-destructive alternative to /clear. Compresses conversation
        history through the 4-layer compression engine (cleanup, summarize
        tool results, summarize conversation, truncate) instead of wiping
        all in-memory messages.
        """
        compression = getattr(ctx.engine, 'compression', None) if ctx.engine else None
        if not compression:
            return CommandResult(
                output="Compression engine not available.",
                success=False,
            )

        if ctx.session is None or not hasattr(ctx.session, "_messages"):
            return CommandResult(
                output="No conversation messages to compact.",
                success=False,
            )

        messages = list(ctx.session._messages)
        if not messages:
            return CommandResult(output="No messages to compact.")

        before_count = len(messages)
        before_chars = sum(len(m.content) for m in messages)

        try:
            # Estimate current context usage for the compression engine
            estimated_usage = 0.55  # Default to above 50% for manual trigger
            result = await compression.compact(messages, estimated_usage)

            # Replace session messages with compacted ones
            ctx.session._messages.clear()
            ctx.session._messages.extend(result.messages)

            after_count = len(result.messages)
            after_chars = sum(len(m.content) for m in result.messages)

            layers_desc = ", ".join(f"L{layer}" for layer in result.layers_applied) if result.layers_applied else "none"
            reduction_pct = (
                ((before_chars - after_chars) / before_chars * 100)
                if before_chars > 0 else 0
            )

            return CommandResult(
                output=(
                    f"Context compacted: {before_count} → {after_count} messages "
                    f"({reduction_pct:.0f}% size reduction).\n"
                    f"Layers applied: {layers_desc}.\n"
                    f"Tip: Use /clear to fully wipe in-memory messages."
                )
            )
        except Exception as e:
            return CommandResult(
                output=f"Compaction failed: {e}",
                success=False,
            )

    async def _cmd_clear(self, args: str, ctx: CommandContext) -> CommandResult:
        """Clear in-memory conversation messages while preserving disk transcripts."""
        cleared = 0
        if ctx.session is not None and hasattr(ctx.session, "_messages"):
            cleared = len(ctx.session._messages)
            ctx.session._messages.clear()
        return CommandResult(
            output=(
                f"Conversation cleared: {cleared} messages removed "
                f"(transcripts preserved on disk).\n"
                f"Tip: Use /compact for non-destructive compression instead."
            )
        )

    async def _cmd_history(self, args: str, ctx: CommandContext) -> CommandResult:
        """Show real conversation history from session, including tool calls."""
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

            # Tool result message: show tool name and result preview
            if role == "TOOL":
                tool_name = getattr(m, "name", None) or "unknown"
                content = getattr(m, "content", "") or ""
                result_preview = content[:80].replace("\n", " ")
                ellipsis = "..." if len(content) > 80 else ""
                lines.append(f"  {i}. [TOOL: {tool_name}] {result_preview}{ellipsis}")
                continue

            # Assistant message with tool calls: show call names and params
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                content = getattr(m, "content", "") or ""
                text_preview = content[:60].replace("\n", " ") if content else ""
                tc_summaries = []
                for tc in tool_calls:
                    tc_name = getattr(tc, "tool_name", None) or getattr(tc, "name", "?")
                    tc_params = getattr(tc, "params", {}) or {}
                    if isinstance(tc_params, dict):
                        param_keys = list(tc_params.keys())[:3]
                        tc_summaries.append(f"{tc_name}({', '.join(param_keys)})")
                    else:
                        tc_summaries.append(str(tc_name))
                tc_str = "; ".join(tc_summaries)
                if text_preview:
                    lines.append(f"  {i}. [ASSISTANT] {text_preview}... → tool_calls: {tc_str}")
                else:
                    lines.append(f"  {i}. [ASSISTANT] → tool_calls: {tc_str}")
                continue

            content = m.content if hasattr(m, "content") else str(m)
            preview = content[:120] + "..." if len(content) > 120 else content
            lines.append(f"  {i}. [{role}] {preview}")
        return CommandResult(output="\n".join(lines))

    async def _cmd_exit(self, args: str, ctx: CommandContext) -> CommandResult:
        """Request exit — triggers session-end flow per spec section 10.

        Per design spec §十, /exit and /quit directly initiate the session-end
        flow (stop ReAct loop, end session, stop status bar, exit). No --force
        flag is required.
        """
        return CommandResult(output="Goodbye!", success=True, exit_requested=True)
