"""REPL engine — prompt_toolkit interactive loop."""

from __future__ import annotations

import asyncio
from pathlib import Path

from prompt_toolkit.completion import Completer


class SlashCompleter(Completer):
    """Auto-completion for slash commands, skills, mode values, and file paths.

    Provides context-aware completions for the REPL input:
    - Slash commands (e.g. /mode, /goal, /skills, /exit)
    - Skill names after / (e.g. /code-review, /brainstorming)
    - Mode values after /mode (think-high, think-max, non-think)
    - File paths for path-like input (gap-19-04)
    """

    # Built-in slash commands
    BUILTIN_COMMANDS = [
        "mode", "goal", "skills", "dream", "clear", "compact", "history",
        "export", "help", "exit", "quit",
    ]

    # Mode values for /mode completion
    MODE_VALUES = ["think-high", "think-max", "non-think"]

    def __init__(self, skill_registry=None):
        self._skill_registry = skill_registry

    def get_completions(self, document, complete_event):
        """Yield Completion objects for the current input.

        Provides slash-command completions when input starts with /,
        and file-path completions for path-like natural language input.
        """
        from prompt_toolkit.completion import Completion

        text = document.text_before_cursor

        # ── Slash command completions ─────────────────────────────
        if text.startswith("/"):
            yield from self._get_slash_completions(text)
            # Also provide file-path completions for slash command args
            # that look like paths (e.g. /export path/to/file)
            parts = text[1:].split()
            if len(parts) >= 2:
                yield from self._get_path_completions(document, text)
            return

        # ── File-path completions for natural language input ──────
        # Complete when the last word looks like a file path
        words = text.split()
        if words:
            last_word = words[-1]
            # Trigger path completion if the last word contains a path separator
            # or starts with common path indicators
            if "/" in last_word or last_word.startswith(("./", "../", "~/")):
                yield from self._get_path_completions(document, text)

    def _get_slash_completions(self, text: str):
        """Yield completions for slash commands."""
        from prompt_toolkit.completion import Completion

        parts = text[1:].split()
        if not parts:
            return

        cmd = parts[0]
        is_first_word = len(parts) == 1 and not text.endswith(" ")

        if is_first_word:
            # Completing the command name itself
            word_before = cmd
            for name in self.BUILTIN_COMMANDS:
                if name.startswith(word_before):
                    yield Completion(name, start_position=-len(word_before))

            if self._skill_registry:
                for entry in self._skill_registry.list_all():
                    if entry.name.startswith(word_before):
                        yield Completion(
                            entry.name,
                            start_position=-len(word_before),
                            display_meta=entry.description,
                        )
        elif cmd == "mode" and len(parts) == 1:
            # Completing mode value
            arg = parts[1] if len(parts) > 1 else ""
            for mode_val in self.MODE_VALUES:
                if mode_val.startswith(arg):
                    yield Completion(mode_val, start_position=-len(arg))

    def _get_path_completions(self, document, text: str):
        """Yield file-path completions using prompt_toolkit's PathCompleter.

        Provides completions for relative paths and handles common path
        patterns including ./, ../, and ~/ expansions.
        """
        import os
        from pathlib import Path
        from prompt_toolkit.completion import Completion, PathCompleter

        try:
            # Extract the last "word" that looks like a path
            words = text.split()
            if not words:
                return

            last = words[-1]

            # Expand ~ to user home directory
            expanded = last
            if last.startswith("~"):
                expanded = os.path.expanduser(last)

            # Determine the directory to search and the prefix to match
            base_dir = Path(expanded)
            if base_dir.is_dir() and last.endswith("/"):
                # User typed a directory followed by / — list its contents
                search_dir = base_dir
                prefix = ""
                start_pos = 0
            else:
                # User typed a partial path — complete the last component
                search_dir = base_dir.parent if base_dir.parent != base_dir else Path(".")
                prefix = base_dir.name
                start_pos = -len(prefix)

            # Gather matching files/dirs
            if search_dir.exists() and search_dir.is_dir():
                try:
                    for entry in sorted(search_dir.iterdir()):
                        if entry.name.startswith(prefix):
                            display = entry.name + ("/" if entry.is_dir() else "")
                            # Build the completion text relative to the original input
                            yield Completion(
                                display,
                                start_position=start_pos,
                                display_meta="dir" if entry.is_dir() else "file",
                            )
                except (PermissionError, OSError):
                    pass
        except Exception:
            pass  # Best-effort path completion


class REPLEngine:
    """Interactive REPL using prompt_toolkit."""

    # Sentinel object for Ctrl+C exit flow (gap-8-05)
    _SENTINEL_CTRL_C = object()

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
        self._live = None
        self._output_lines: list[str] = []
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
            # Set logging context with session_id (gap-16)
            from myagent.logging.context import set_context
            set_context(
                session_id=self._current_session.id,
                project_name=self._project_dir.name,
            )
            # Emit startup event now that session_id is known (gap-18-04)
            from myagent.logging.logger import LogManager as _LM
            _LM.log_startup(
                config=getattr(self._config, 'logging', None),
                session_id=self._current_session.id,
            )
            # Reset task list for the new session (gap-12)
            if hasattr(self._current_session, 'project_name') and self._session_mgr.session_store:
                from myagent.tools.builtin.session_tools import reset_task_list
                sess_dir = self._session_mgr.session_store._session_dir(
                    self._current_session.project_name,
                    self._current_session.project_hash,
                    self._current_session.id,
                )
                reset_task_list(persist_path=sess_dir / "tasks.json")

        # G4: Start periodic dream trigger checker for long-running sessions
        self._dream_checker_task = None
        if self._dream_engine:
            self._dream_checker_task = asyncio.create_task(self._periodic_dream_check())

        # Shared Rich Live layout for status bar + output (gap-2-07)
        self._live = None
        if self._status_bar:
            try:
                from rich.live import Live
                from rich.layout import Layout
                from rich.panel import Panel

                layout = Layout()
                layout.split(
                    Layout(name="status", size=3),
                    Layout(name="output"),
                )
                # Initialize with status bar panel
                status_renderable = self._status_bar.get_renderable()
                layout["status"].update(status_renderable or Panel(""))
                layout["output"].update(Panel("MyAgentCLI — Type /help for commands, Ctrl+D to exit.\n"
                                               f"Project: {self._project_dir.name}",
                                               title="Output"))

                self._live = Live(layout, refresh_per_second=4, console=self._console, vertical_overflow="visible")
                self._live.start()
                self._output_lines: list[str] = []
            except ImportError:
                self._live = None

        if self._live is None:
            self._console.print("MyAgentCLI — Type /help for commands, Ctrl+D to exit.")
            self._console.print(f"Project: [bold]{self._project_dir.name}[/bold]")

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.key_binding import KeyBindings

            history_file = Path.home() / ".myagent" / ".history"
            history_file.parent.mkdir(parents=True, exist_ok=True)

            # Key bindings: Ctrl+C interrupts running engine or clears buffer
            kb = KeyBindings()

            @kb.add("c-c")
            def _(event):
                # If engine is running in background, interrupt it (gap-10)
                engine_task = getattr(self, '_active_engine_task', None)
                if engine_task and not engine_task.done():
                    if hasattr(self._engine, 'interrupt_event'):
                        self._engine.interrupt_event.set()
                    engine_task.cancel()
                    event.app.current_buffer.reset()
                    return
                # Idle — trigger exit confirmation flow (gap-8-05)
                # Use app.exit() with sentinel to break out of prompt_async
                # so the main loop can show "Exit? (y/n)" confirmation
                event.app.exit(result=self._SENTINEL_CTRL_C)

            # Build completer (gap-2-05)
            skill_registry = (
                self._engine.skill_registry if self._engine else None
            )
            completer = SlashCompleter(skill_registry=skill_registry)

            # Build lexer for syntax highlighting (G4)
            lexer = None
            if self._config and getattr(self._config.ui, "syntax_highlight", True):
                try:
                    from prompt_toolkit.lexers import PygmentsLexer
                    from pygments.lexers.python import PythonLexer
                    lexer = PygmentsLexer(PythonLexer)
                except ImportError:
                    pass  # Pygments not available; skip syntax highlighting

            session = PromptSession(
                history=FileHistory(str(history_file)),
                multiline=True,
                key_bindings=kb,
                completer=completer,
                lexer=lexer,
            )

            while self._running:
                try:
                    user_input = await session.prompt_async("myagent> ")
                except KeyboardInterrupt:
                    # This is a fallback — the key binding handles most Ctrl+C cases.
                    # If KeyboardInterrupt still fires (e.g. during prompt_toolkit init),
                    # show the exit confirmation.
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

                # gap-8-05: Ctrl+C on idle — key binding returns sentinel
                if user_input is self._SENTINEL_CTRL_C:
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
                self._output_to_console(result.output)

                if result.exit_requested:
                    self._running = False
                    return

                if result.skill_invoked:
                    # Store active skill for the next natural-language input (gap-2-01)
                    self._active_skill = result.skill_invoked

                if not result.success:
                    return
                return

            self._output_to_console(f"Unknown command: {text}")
            return

        # Natural language → AgentEngine
        if self._engine and self._current_session:
            # Inject active skill if set by /skill-name (gap-2-01)
            active_skill = self._active_skill
            self._active_skill = None  # Clear after injecting

            # Reset interrupt event before each run (gap-10)
            if hasattr(self._engine, 'interrupt_event'):
                self._engine.interrupt_event.clear()

            # Run engine in a background task to allow interrupt (gap-10)
            import asyncio as _asyncio
            has_pending_question = False
            stream_interrupted = False  # gap-19-02: track stream interruption

            async def _run_engine():
                nonlocal has_pending_question, stream_interrupted
                async for event in self._engine.run(
                    text, self._current_session, active_skill=active_skill
                ):
                    # G5: Update status bar with live token count from Done events
                    if type(event).__name__ == "Done":
                        usage = getattr(event, 'usage', None)
                        if usage and hasattr(usage, 'total_tokens'):
                            if self._status_bar:
                                self._status_bar.update(tokens=usage.total_tokens)
                    if self._renderer:
                        rendered = self._renderer.render_event(event)
                        if rendered:
                            event_type = type(event).__name__
                            if event_type == "TextChunk":
                                self._output_to_console(rendered, end="")
                            elif event_type == "ThinkingChunk":
                                pass  # Thinking content is usually hidden
                            elif event_type == "AskUserQuestion":
                                has_pending_question = True
                                self._output_to_console(rendered)
                            elif event_type == "Interrupted":
                                self._output_to_console("\n[Interrupted]")
                            elif event_type == "IntentSignal":
                                intent = getattr(event, 'intent', '')
                                if intent == 'continue':
                                    stream_interrupted = True
                                self._output_to_console(rendered)
                            else:
                                self._output_to_console(rendered)
                    else:
                        # Fallback: simple print-based rendering
                        self._render_event_fallback(event)

            engine_task = _asyncio.ensure_future(_run_engine())
            self._active_engine_task = engine_task
            try:
                await engine_task
            except _asyncio.CancelledError:
                self._output_to_console("\n[Interrupted by user]")
            finally:
                self._active_engine_task = None

            self._output_to_console("")  # trailing newline after streaming

            # gap-19-02: After stream interruption, prompt user to decide
            if stream_interrupted:
                try:
                    confirm = await self._prompt_with_timeout(
                        "Stream interrupted. Continue? [Y/n] ",
                        timeout=120.0,
                    )
                    if confirm and confirm.strip().lower() in ("y", "yes", ""):
                        await self.process_input("continue")
                except Exception:
                    pass

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

    def _output_to_console(self, text: str, end: str = "\n") -> None:
        """Route output through the shared Live layout or plain console (gap-2-07)."""
        if self._live:
            from rich.layout import Layout
            from rich.panel import Panel
            from rich.live import Live
            try:
                if text:
                    self._output_lines.append(text)
                # Trim output lines to prevent memory growth
                if len(self._output_lines) > 500:
                    self._output_lines = self._output_lines[-300:]
                # Update status bar in the layout
                if self._status_bar:
                    status_panel = self._status_bar.get_renderable()
                    if status_panel:
                        self._live._layout["status"].update(status_panel)
                output_text = "\n".join(self._output_lines)
                self._live._layout["output"].update(Panel(output_text, title="Output"))
            except Exception:
                if self._console:
                    self._console.print(text, end=end)
        elif self._console:
            if end == "":
                self._console.print(text, end="")
            else:
                self._console.print(text)
        else:
            print(text, end=end)

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

    async def _periodic_dream_check(self) -> None:
        """G4: Periodically re-check dream trigger condition in long-running sessions.

        Checked every 30 minutes by default. If the dream trigger
        conditions become true mid-session, spawn a dream cycle in
        the background without blocking the REPL.
        """
        import logging as _logging
        _log = _logging.getLogger("myagent.cli")

        DREAM_CHECK_INTERVAL = 1800  # 30 minutes in seconds

        while self._running:
            await asyncio.sleep(DREAM_CHECK_INTERVAL)
            if not self._running:
                break
            try:
                # Re-estimate total rounds (includes current session's live turns)
                total_rounds = 0
                if self._session_mgr and hasattr(self._session_mgr, 'estimate_total_rounds'):
                    total_rounds = self._session_mgr.estimate_total_rounds(
                        current_session=self._current_session
                    )
                if self._dream_engine and self._dream_engine.should_run(total_rounds):
                    _log.info("Mid-session dream trigger fired (interval check)")
                    session_store = getattr(self._engine, 'session_store', None) if self._engine else None

                    async def _run_dream_bg():
                        try:
                            result = await self._dream_engine.run(session_store=session_store)
                            _log.info(
                                "Dream completed: created=%d updated=%d deleted=%d log=%s",
                                result.memories_created, result.memories_updated,
                                result.memories_deleted, result.log_path,
                            )
                        except Exception as exc:
                            _log.error("Background dream failed: %s", exc)
                    asyncio.create_task(_run_dream_bg())
            except Exception as e:
                _log.warning("Periodic dream check failed: %s", e)

    async def _shutdown(self) -> None:
        """Graceful shutdown: stop status bar, end session, clean up."""
        self._running = False

        # G4: Cancel periodic dream checker
        if self._dream_checker_task and not self._dream_checker_task.done():
            self._dream_checker_task.cancel()
            try:
                await self._dream_checker_task
            except asyncio.CancelledError:
                pass

        # Stop shared Live layout (gap-2-07)
        if self._live:
            self._live.stop()
            self._live = None

        # End session
        if self._session_mgr and self._current_session:
            await self._session_mgr.end_session(self._current_session)

        # Final goodbye on fresh console
        if self._console:
            self._console.print("\nGoodbye!")
        else:
            print("\nGoodbye!")
