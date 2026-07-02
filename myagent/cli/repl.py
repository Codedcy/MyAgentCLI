"""REPL engine — prompt_toolkit interactive loop."""

from __future__ import annotations

import asyncio
from pathlib import Path


class REPLEngine:
    """Interactive REPL using prompt_toolkit."""

    def __init__(
        self,
        engine=None,
        commands=None,
        session_mgr=None,
        config=None,
        project_dir: Path | None = None,
    ):
        self._engine = engine
        self._commands = commands
        self._session_mgr = session_mgr
        self._config = config
        self._project_dir = project_dir or Path.cwd()
        self._running = False
        self._current_session = None

    async def run(self) -> None:
        """Start the REPL loop."""
        self._running = True

        # Start session
        if self._session_mgr:
            self._current_session = await self._session_mgr.start_new(self._project_dir)

        print("MyAgentCLI — Type /help for commands, Ctrl+D to exit.")
        print(f"Project: {self._project_dir.name}")

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            from pathlib import Path as P

            history_file = P.home() / ".myagent" / ".history"
            history_file.parent.mkdir(parents=True, exist_ok=True)

            session = PromptSession(history=FileHistory(str(history_file)))

            while self._running:
                try:
                    user_input = await session.prompt_async("myagent> ")
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                user_input = user_input.strip()
                if not user_input:
                    continue

                await self.process_input(user_input)

        except ImportError:
            # Fallback: simple input without prompt_toolkit
            while self._running:
                try:
                    user_input = input("myagent> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                if not user_input:
                    continue

                await self.process_input(user_input)

    async def process_input(self, text: str) -> None:
        """Handle one input line."""
        # Slash commands
        if text.startswith("/"):
            if text in ("/exit", "/quit"):
                self._running = False
                if self._session_mgr and self._current_session:
                    await self._session_mgr.end_session(self._current_session)
                return

            if self._commands:
                from myagent.cli.commands import CommandContext
                ctx = CommandContext(
                    engine=self._engine,
                    config=self._config,
                    session=self._current_session,
                )
                result = await self._commands.dispatch(text, ctx)
                print(result.output)
                return

            print(f"Unknown command: {text}")
            return

        # Natural language → AgentEngine
        if self._engine and self._current_session:
            async for event in self._engine.run(text, self._current_session):
                match type(event).__name__:
                    case "TextChunk":
                        print(event.content, end="", flush=True)
                    case "ThinkingChunk":
                        pass  # Thinking content is usually hidden
                    case "ToolCallStart":
                        print(f"\n🔧 {event.name}...", end="", flush=True)
                    case "ToolCallEnd":
                        if event.result.error:
                            print(f" ❌ {event.result.error}")
                        else:
                            print(" ✅")
                    case "Done":
                        print()
                    case "Error":
                        print(f"\n❌ Error: {event.message}")
                    case _:
                        pass
        else:
            print(f"Echo: {text}")
