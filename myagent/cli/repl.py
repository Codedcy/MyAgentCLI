"""REPL engine — prompt_toolkit interactive loop."""

from __future__ import annotations

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
        renderer=None,
        status_bar=None,
        dream_engine=None,
    ):
        self._engine = engine
        self._commands = commands
        self._session_mgr = session_mgr
        self._config = config
        self._project_dir = project_dir or Path.cwd()
        self._renderer = renderer
        self._status_bar = status_bar
        self._dream_engine = dream_engine
        self._running = False
        self._current_session = None
        self._console = None
        self._active_skill: str | None = None  # skill name to inject into next engine run (gap-2-01)

    async def run(self) -> None:
        """Start the REPL loop."""
        self._running = True

        # Initialize Rich console for renderer output
        from rich.console import Console
        self._console = Console()

        # Start session
        if self._session_mgr and self._current_session is None:
            self._current_session = await self._session_mgr.start_new(self._project_dir)

        # Start status bar
        if self._status_bar:
            await self._status_bar.start()

        self._console.print("MyAgentCLI — Type /help for commands, Ctrl+D to exit.")
        self._console.print(f"Project: [bold]{self._project_dir.name}[/bold]")

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.key_binding import KeyBindings

            history_file = Path.home() / ".myagent" / ".history"
            history_file.parent.mkdir(parents=True, exist_ok=True)

            # Key bindings: Ctrl+C clears buffer, Ctrl+D on empty exits
            kb = KeyBindings()

            @kb.add("c-c")
            def _(event):
                buffer = event.app.current_buffer
                buffer.reset()

            session = PromptSession(
                history=FileHistory(str(history_file)),
                multiline=True,
                key_bindings=kb,
            )

            while self._running:
                try:
                    user_input = await session.prompt_async("myagent> ")
                except KeyboardInterrupt:
                    # Ctrl+C in idle: ask "Exit? (y/n)"
                    self._console.print()
                    try:
                        confirm = await session.prompt_async(
                            "Exit? (y/n) ", multiline=False
                        )
                        if confirm.strip().lower() in ("y", "yes"):
                            self._console.print()
                            break
                        continue
                    except (EOFError, KeyboardInterrupt):
                        self._console.print()
                        break
                except EOFError:
                    self._console.print()
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
                    self._console.print() if self._console else print()
                    break

                if not user_input:
                    continue

                await self.process_input(user_input)

        await self._shutdown()

    async def process_input(self, text: str) -> None:
        """Handle one input line."""
        # Slash commands
        if text.startswith("/"):
            if text in ("/exit", "/quit"):
                self._running = False
                return

            if self._commands:
                from myagent.cli.commands import CommandContext
                ctx = CommandContext(
                    engine=self._engine,
                    config=self._config,
                    session=self._current_session,
                    session_manager=self._session_mgr,
                    goal_tracker=(
                        self._engine.goal_tracker if self._engine else None
                    ),
                    skill_registry=(
                        self._engine.skill_registry if self._engine else None
                    ),
                    dream_engine=self._dream_engine,
                )
                result = await self._commands.dispatch(text, ctx)
                if self._console:
                    self._console.print(result.output)
                else:
                    print(result.output)

                if result.skill_invoked:
                    # Store active skill for the next natural-language input (gap-2-01)
                    self._active_skill = result.skill_invoked

                if not result.success:
                    return
                return

            if self._console:
                self._console.print(f"Unknown command: {text}")
            else:
                print(f"Unknown command: {text}")
            return

        # Natural language → AgentEngine
        if self._engine and self._current_session:
            # Inject active skill if set by /skill-name (gap-2-01)
            active_skill = self._active_skill
            self._active_skill = None  # Clear after injecting

            has_pending_question = False
            async for event in self._engine.run(
                text, self._current_session, active_skill=active_skill
            ):
                if self._renderer:
                    rendered = self._renderer.render_event(event)
                    if rendered and self._console:
                        event_type = type(event).__name__
                        if event_type == "TextChunk":
                            self._console.print(rendered, end="")
                        elif event_type == "ThinkingChunk":
                            pass  # Thinking content is usually hidden
                        elif event_type == "AskUserQuestion":
                            has_pending_question = True
                            self._console.print(rendered)
                        else:
                            self._console.print(rendered)
                else:
                    # Fallback: simple print-based rendering
                    self._render_event_fallback(event)

            if self._console:
                self._console.print()  # trailing newline after streaming

            # gap-13: 120s timeout for AskUserQuestion — agent auto-decides
            if has_pending_question:
                try:
                    user_answer = await self._prompt_with_timeout(
                        "Your answer (120s timeout, or agent auto-decides): ",
                        timeout=120.0,
                    )
                    if user_answer:
                        await self.process_input(user_answer)
                    else:
                        if self._console:
                            self._console.print(
                                "[dim]No response within 120s — agent will auto-decide.[/dim]"
                            )
                        # Send "continue" to let the agent auto-decide
                        await self.process_input("continue")
                except Exception:
                    if self._console:
                        self._console.print(
                            "[dim]Timeout — agent will auto-decide.[/dim]"
                        )
        else:
            if self._console:
                self._console.print(f"Echo: {text}")
            else:
                print(f"Echo: {text}")

    async def _prompt_with_timeout(self, prompt_text: str, timeout: float) -> str | None:
        """Prompt the user with a timeout. Returns the response or None if timed out.

        Uses asyncio.wait_for with the event loop. Falls back to regular
        input if prompt_toolkit is not available.
        """
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.shortcuts import prompt as pt_prompt
            import asyncio as _asyncio

            loop = _asyncio.get_event_loop()
            try:
                result = await _asyncio.wait_for(
                    loop.run_in_executor(None, lambda: pt_prompt(prompt_text, multiline=False)),
                    timeout=timeout,
                )
                return result
            except _asyncio.TimeoutError:
                return None
        except ImportError:
            # Fallback: standard input (blocks forever, ignore timeout)
            try:
                return input(prompt_text).strip()
            except (EOFError, KeyboardInterrupt):
                return None

    def _render_event_fallback(self, event) -> None:
        """Fallback renderer when no Rich Renderer is wired."""
        match type(event).__name__:
            case "TextChunk":
                print(event.content, end="", flush=True)
            case "ThinkingChunk":
                pass
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

    async def _shutdown(self) -> None:
        """Graceful shutdown: stop status bar, end session, clean up."""
        self._running = False

        # Stop status bar
        if self._status_bar:
            self._status_bar.stop()

        # End session
        if self._session_mgr and self._current_session:
            await self._session_mgr.end_session(self._current_session)

        self._console.print("\nGoodbye!") if self._console else print("\nGoodbye!")
