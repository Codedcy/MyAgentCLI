"""REPL engine - prompt_toolkit interactive loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
from pathlib import Path

from prompt_toolkit.completion import Completer
from prompt_toolkit.key_binding import KeyBindings

from myagent.cli.layout import AgentLayoutController

logger = logging.getLogger("myagent.cli.repl")

DEFAULT_INSPECTOR_TOGGLE_KEY = "f2"
TAB_EQUIVALENT_KEYS = {"c-i", "ctrl+i", "control+i", "control-i", "tab"}


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

        text = document.text_before_cursor

        # Slash command completions.
        if text.startswith("/"):
            yield from self._get_slash_completions(text)
            # Also provide file-path completions for slash command args
            # that look like paths (e.g. /export path/to/file)
            parts = text[1:].split()
            if len(parts) >= 2:
                yield from self._get_path_completions(document, text)
            return

        # File-path completions for natural language input.
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

        from prompt_toolkit.completion import Completion

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
                # User typed a directory followed by /; list its contents.
                search_dir = base_dir
                prefix = ""
                start_pos = 0
            else:
                # User typed a partial path; complete the last component.
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
                    logger.exception(
                        "Path completion could not inspect directory",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "complete filesystem path",
                        },
                    )
                    pass
        except Exception:
            logger.exception(
                "Path completion failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_path_completion",
                },
            )


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
        status_pane=None,
        status_model=None,
        status_bar=None,
        dream_engine=None,
        chat_window_factory=None,
        chat_window_controller=None,
    ):
        self._engine = engine
        self._commands = commands
        self._session_mgr = session_mgr
        self._config = config
        self._project_dir = project_dir or Path.cwd()
        self._renderer = renderer
        self._status_pane = status_pane if status_pane is not None else status_bar
        self._status_model = (
            status_model
            if status_model is not None
            else getattr(self._status_pane, "status_model", None)
        )
        self._status_bar = status_bar if status_bar is not None else self._status_pane
        if (
            self._status_pane is not None
            and self._status_model is not None
            and hasattr(self._status_pane, "status_model")
            and getattr(self._status_pane, "status_model", None) is None
        ):
            self._status_pane.status_model = self._status_model
        self._dream_engine = dream_engine
        self._chat_window_factory = chat_window_factory
        self._chat_window = chat_window_controller
        self._chat_window_loop_active = False
        self._chat_streaming = False
        self._chat_submission_lock: asyncio.Lock | None = None
        self._chat_submission_tasks: set[asyncio.Task[None]] = set()
        self._chat_submission_cancel_requested = False
        self._running = False
        self._current_session = None
        self._console = self._create_console() if self._status_pane else None
        self._live = None
        self._layout_controller = self._create_layout_controller()
        self._output_lines: list[str] = []
        self._dream_checker_task = None
        self._tool_call_names: dict[str, str] = {}
        self._done_usage_session_total: int | None = None
        # Skill name to inject into the next engine run (gap-2-01).
        self._active_skill: str | None = None

    def _create_console(self):
        from rich.console import Console

        return Console()

    def _create_layout_controller(self):
        if self._status_pane is None:
            return None
        if self._console is None:
            self._console = self._create_console()
        return AgentLayoutController(
            self._console,
            self._status_pane,
            self._status_config(),
        )

    def _status_config(self):
        return getattr(self._config, "ui", self._config)

    def _build_key_bindings(self):
        """Build testable prompt key bindings for REPL-only actions."""

        kb = KeyBindings()
        self._bind_inspector_toggle(kb)
        return kb

    def _bind_inspector_toggle(self, kb) -> None:
        @kb.add(self._inspector_toggle_key())
        def _(event):
            """Toggle the inspector pane without changing the prompt buffer."""
            self._toggle_inspector()

    def _inspector_toggle_key(self) -> str:
        status_config = self._status_config()
        pane_config = getattr(status_config, "status_pane", status_config)
        key = getattr(pane_config, "toggle_key", DEFAULT_INSPECTOR_TOGGLE_KEY)
        key_text = str(key or DEFAULT_INSPECTOR_TOGGLE_KEY).strip().lower()
        if key_text in TAB_EQUIVALENT_KEYS:
            return DEFAULT_INSPECTOR_TOGGLE_KEY
        return key_text

    def _toggle_inspector(self) -> None:
        """Toggle the inspector pane and refresh the layout."""

        if self._layout_controller is None:
            return
        self._layout_controller.toggle_inspector()
        self._layout_controller.refresh()

    def _update_status_from_event(self, event) -> None:
        """Update the runtime status model and legacy status pane from an event."""

        event_type = type(event).__name__
        if event_type == "Done":
            self._update_token_status(getattr(event, "usage", None))
        elif event_type == "ToolCallStart":
            self._handle_tool_start(event)
        elif event_type == "ToolCallEnd":
            self._handle_tool_end(event)
        elif event_type == "AskUserQuestion":
            self._update_goal_waiting()
        elif event_type == "Error":
            self._update_health_error(getattr(event, "message", "Error"))
        elif event_type == "Interrupted":
            self._update_health_error("Interrupted")
        elif event_type == "StatusUpdate":
            self._handle_status_update(event)

        if self._layout_controller:
            self._layout_controller.refresh()
        self._refresh_chat_window()

    def _handle_status_update(self, event) -> None:
        scope = getattr(event, "scope", "")
        data = getattr(event, "data", {}) or {}
        if not isinstance(data, dict):
            return

        if scope == "context":
            self._merge_token_status(
                data,
                {"context_usage", "context_window"},
            )
            self._update_legacy_status_bar(
                **self._filter_status_data(
                    data,
                    {"context_usage", "context_window"},
                )
            )
        elif scope == "tokens":
            token_updates = self._filter_status_data(
                data,
                {
                    "prompt_tokens",
                    "completion_tokens",
                    "turn_total",
                    "session_total",
                    "context_usage",
                    "context_window",
                },
            )
            if self._status_model and token_updates:
                self._status_model.update_tokens(**token_updates)
            if "session_total" in token_updates:
                self._done_usage_session_total = int(token_updates["session_total"])
            legacy_total = token_updates.get(
                "session_total",
                token_updates.get("turn_total"),
            )
            if legacy_total is not None:
                self._update_legacy_status_bar(tokens=legacy_total)
        elif scope == "goal":
            goal_updates = self._filter_status_data(
                data,
                {
                    "name",
                    "active",
                    "achieved",
                    "waiting_for_user",
                    "budget_used",
                    "budget_limit",
                },
            )
            if self._status_model and goal_updates:
                self._status_model.update_goal(**goal_updates)
            self._update_legacy_status_bar(
                **self._legacy_goal_status_updates(goal_updates)
            )
        elif scope == "health":
            health_updates = self._filter_status_data(
                data,
                {"retry_info", "mcp_connected", "last_error"},
            )
            if self._status_model and health_updates:
                self._status_model.update_health(**health_updates)
            self._update_legacy_status_bar(**health_updates)

    def _merge_token_status(self, data: dict, allowed_keys: set[str]) -> None:
        token_updates = self._filter_status_data(data, allowed_keys)
        if self._status_model and token_updates:
            self._status_model.update_tokens(**token_updates)

    def _filter_status_data(self, data: dict, allowed_keys: set[str]) -> dict:
        return {key: data[key] for key in allowed_keys if key in data}

    def _legacy_goal_status_updates(self, updates: dict) -> dict:
        legacy: dict = {}
        mapping = {
            "name": "goal_name",
            "active": "goal_active",
            "achieved": "goal_achieved",
            "waiting_for_user": "goal_waiting_for_user",
            "budget_used": "goal_budget_used",
            "budget_limit": "goal_budget_limit",
        }
        for key, legacy_key in mapping.items():
            if key in updates:
                legacy[legacy_key] = updates[key]
        return legacy

    def _update_token_status(self, usage) -> None:
        if usage is None:
            return

        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is None:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
        turn_total = int(total_tokens or 0)
        session_total = self._accumulate_done_usage_total(turn_total)

        if self._status_model:
            token_updates = {
                "turn_total": turn_total,
                "session_total": session_total,
            }
            if prompt_tokens is not None:
                token_updates["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                token_updates["completion_tokens"] = completion_tokens
            self._status_model.update_tokens(**token_updates)

        if self._status_bar and hasattr(self._status_bar, "update"):
            self._status_bar.update(tokens=session_total)

    def _accumulate_done_usage_total(self, turn_total: int) -> int:
        if self._done_usage_session_total is None:
            existing_total = 0
            if self._status_model:
                existing_total = self._status_model.snapshot().tokens.session_total
            self._done_usage_session_total = int(existing_total or 0)
        self._done_usage_session_total += turn_total
        return self._done_usage_session_total

    def _handle_tool_start(self, event) -> None:
        name = getattr(event, "name", "")
        call_id = getattr(event, "call_id", "")
        if not name:
            return
        if call_id:
            self._tool_call_names[call_id] = name
        if self._status_model:
            self._status_model.update_tool(name, status="running")
        self._update_legacy_status_bar(
            current_tool=name,
            tool_status="running",
            tool_result_summary="",
        )

    def _handle_tool_end(self, event) -> None:
        call_id = getattr(event, "call_id", "")
        name = self._tool_call_names.get(call_id, call_id)
        if not name:
            return
        result = getattr(event, "result", None)
        error = getattr(result, "error", None)
        output = getattr(result, "output", "")
        status = "failed" if error else "completed"
        summary = self._short_status_summary(error or output)
        if self._status_model:
            self._status_model.update_tool(
                name,
                status=status,
                last_result_summary=summary,
            )
        self._update_legacy_status_bar(
            current_tool=name,
            tool_status=status,
            tool_result_summary=summary,
        )

    def _update_goal_waiting(self) -> None:
        if self._status_model:
            self._status_model.update_goal(active=True, waiting_for_user=True)
        self._update_legacy_status_bar(
            goal_active=True,
            goal_waiting_for_user=True,
        )

    def _update_health_error(self, message: str) -> None:
        summary = self._short_status_summary(message)
        if self._status_model:
            self._status_model.update_health(last_error=summary)
        self._update_legacy_status_bar(last_error=summary)

    def _update_legacy_status_bar(self, **kwargs) -> None:
        if self._status_bar and hasattr(self._status_bar, "update"):
            self._status_bar.update(**kwargs)

    def _short_status_summary(self, value, max_chars: int = 200) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def _sync_status_from_session(self, session) -> None:
        if not self._status_model or session is None:
            return

        session_id = getattr(session, "id", "")
        if session_id:
            self._status_model.update_session(session_id=session_id)

        goal = getattr(session, "goal", None)
        if goal:
            achieved = bool(getattr(session, "goal_achieved", False))
            self._status_model.update_goal(
                name=goal,
                active=not achieved,
                achieved=achieved,
                waiting_for_user=False,
            )
            self._update_legacy_status_bar(
                goal_name=goal,
                goal_active=not achieved,
                goal_achieved=achieved,
                goal_waiting_for_user=False,
            )

    def _chat_window_config(self):
        ui_config = getattr(self._config, "ui", None)
        if ui_config is not None:
            chat_window = getattr(ui_config, "chat_window", None)
            if chat_window is not None:
                return chat_window
        if hasattr(self._config, "chat_window"):
            return self._config.chat_window
        if hasattr(self._config, "enabled"):
            return self._config
        return None

    def _should_use_chat_window(self) -> bool:
        """Whether this REPL run should start the full-screen chat window."""

        chat_config = self._chat_window_config()
        if chat_config is None:
            return False
        return bool(getattr(chat_config, "enabled", False))

    def _chat_window_active(self) -> bool:
        """Whether output should be routed to the chat window transcript."""

        if self._chat_window is None:
            return False
        return bool(
            self._chat_window_loop_active
            or getattr(self._chat_window, "is_running", False)
        )

    def _build_slash_completer(self):
        skill_registry = self._engine.skill_registry if self._engine else None
        return SlashCompleter(skill_registry=skill_registry)

    def _build_prompt_lexer(self):
        ui_config = getattr(self._config, "ui", None)
        if not bool(getattr(ui_config, "syntax_highlight", True)):
            return None
        try:
            from prompt_toolkit.lexers import PygmentsLexer
            from pygments.lexers.python import PythonLexer
            return PygmentsLexer(PythonLexer)
        except ImportError:
            logger.exception(
                "Pygments unavailable for REPL syntax highlighting",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "import pygments lexer",
                },
            )
            return None

    def _create_chat_window_controller(self):
        from myagent.cli.chat_window import ChatWindowController
        from myagent.cli.transcript import TranscriptBuffer

        chat_config = self._chat_window_config()
        transcript = TranscriptBuffer(
            max_lines=getattr(chat_config, "scrollback_lines", 2000),
            follow_output=getattr(chat_config, "follow_output", "auto"),
        )
        kwargs = {
            "config": self._config,
            "transcript": transcript,
            "status_pane": self._status_pane,
            "status_model": self._status_model,
            "completer": self._build_slash_completer(),
            "lexer": self._build_prompt_lexer(),
        }
        if self._chat_window_factory is not None:
            return self._chat_window_factory(**kwargs)
        return ChatWindowController(**kwargs)

    def _capture_chat_text(self, content: object) -> str:
        from myagent.cli.rich_capture import capture_renderable, sanitize_terminal_text

        if isinstance(content, str):
            return sanitize_terminal_text(content)
        width = getattr(self._chat_window, "_last_terminal_columns", 100)
        return capture_renderable(content, width=width)

    def _append_chat_output(self, text: object, end: str = "\n") -> bool:
        """Append assistant output to the active chat window if possible."""

        if not self._chat_window_active():
            return False
        if isinstance(text, str) and text == "" and end != "" and not self._chat_streaming:
            return True

        append_output = getattr(self._chat_window, "append_output", None)
        if not callable(append_output):
            return False

        append_output(text, end=end)
        self._chat_streaming = end == ""
        return True

    def _append_chat_system_output(self, content: object) -> bool:
        if not self._chat_window_active():
            return False
        text = self._capture_chat_text(content)
        if not text:
            return True
        append_system = getattr(self._chat_window, "append_system", None)
        if not callable(append_system):
            return False
        append_system(text)
        self._chat_streaming = False
        return True

    def _append_chat_error_output(self, content: object) -> bool:
        if not self._chat_window_active():
            return False
        text = self._capture_chat_text(content)
        if not text:
            return True
        append_error = getattr(self._chat_window, "append_error", None)
        if not callable(append_error):
            return False
        append_error(text)
        self._chat_streaming = False
        return True

    def _append_chat_tool_output(self, content: object) -> bool:
        if not self._chat_window_active():
            return False

        append_tool = getattr(self._chat_window, "append_tool", None)
        if callable(append_tool):
            append_tool(content)
            self._chat_streaming = False
            return True

        transcript = getattr(self._chat_window, "transcript", None)
        transcript_append_tool = getattr(transcript, "append_tool", None)
        if callable(transcript_append_tool):
            transcript_append_tool(content, plain_text=self._capture_chat_text(content))
            refresh = getattr(self._chat_window, "refresh", None)
            if callable(refresh):
                refresh()
            self._chat_streaming = False
            return True

        return self._append_chat_system_output(content)

    def _output_system_message(
        self,
        message: str,
        console_message: str | None = None,
    ) -> None:
        if self._append_chat_system_output(message):
            return
        output = console_message if console_message is not None else message
        if self._console:
            self._console.print(output)
        else:
            print(output)

    def _refresh_chat_window(self) -> None:
        if not self._chat_window_active():
            return
        refresh = getattr(self._chat_window, "refresh", None)
        if callable(refresh):
            refresh()

    def _set_chat_agent_running(self, running: bool) -> None:
        if not self._chat_window_active():
            return
        set_agent_running = getattr(self._chat_window, "set_agent_running", None)
        if callable(set_agent_running):
            set_agent_running(running)

    def _submit_chat_input(self, text: str):
        task = asyncio.create_task(self._process_chat_submission(text))
        self._chat_submission_tasks.add(task)
        task.add_done_callback(self._on_chat_submission_done)
        return self._await_chat_submission_task(task)

    async def _await_chat_submission_task(self, task: asyncio.Task[None]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    def _on_chat_submission_done(self, task: asyncio.Task[None]) -> None:
        self._chat_submission_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            logger.exception(
                "Chat submission failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_chat_submission",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )

    async def _drain_chat_submission_tasks(self, *, cancel: bool) -> None:
        if cancel:
            self._chat_submission_cancel_requested = True
        current_task = asyncio.current_task()
        while True:
            tasks = [
                task
                for task in self._chat_submission_tasks
                if task is not current_task
            ]
            if not tasks:
                return

            if cancel:
                for task in tasks:
                    if not task.done():
                        task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

    def _current_chat_submission_task(self) -> asyncio.Task[None] | None:
        current_task = asyncio.current_task()
        if current_task in self._chat_submission_tasks:
            return current_task
        return None

    async def _skip_chat_followups_for_submission_teardown(self) -> bool:
        current_task = self._current_chat_submission_task()
        if current_task is None:
            return False
        if (
            self._chat_submission_cancel_requested
            or current_task.cancelling()
            or not getattr(self._chat_window, "is_running", True)
        ):
            return True

        await asyncio.sleep(0)
        return (
            self._chat_submission_cancel_requested
            or current_task.cancelling()
            or not getattr(self._chat_window, "is_running", True)
        )

    async def _process_chat_submission(self, text: str) -> None:
        if self._chat_submission_lock is None:
            self._chat_submission_lock = asyncio.Lock()
        async with self._chat_submission_lock:
            self._mark_chat_submission_started(text)
            await self.process_input(text)

    def _mark_chat_submission_started(self, text: str) -> None:
        if not self._chat_window_active():
            return

        mark_submission_started = getattr(
            self._chat_window,
            "mark_submission_started",
            None,
        )
        if callable(mark_submission_started):
            mark_submission_started(text)
            return

        append_user_input = getattr(self._chat_window, "append_user_input", None)
        if callable(append_user_input):
            append_user_input(text)
            return

        transcript = getattr(self._chat_window, "transcript", None)
        transcript_append_user = getattr(transcript, "append_user", None)
        if callable(transcript_append_user):
            transcript_append_user(text)
            self._refresh_chat_window()

    async def _handle_chat_interrupt(self) -> None:
        engine_task = getattr(self, "_active_engine_task", None)
        if engine_task and not engine_task.done():
            if hasattr(self._engine, "interrupt_event"):
                self._engine.interrupt_event.set()
            engine_task.cancel()

    async def _handle_chat_exit(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Start the REPL loop."""
        self._running = True

        # Initialize Rich console for renderer output
        if self._console is None:
            self._console = self._create_console()

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
            from myagent.logging.logger import LogManager
            LogManager.log_startup(
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

        self._sync_status_from_session(self._current_session)

        # G4: Start periodic dream trigger checker for long-running sessions
        self._dream_checker_task = None
        if self._dream_engine:
            self._dream_checker_task = asyncio.create_task(self._periodic_dream_check())

        # Initialize Rich Live for output display during engine processing.
        # We do NOT start Live here. Rich Live is a full-screen display that
        # conflicts with prompt_toolkit's terminal control. Instead, we render
        # the initial greeting once, then let prompt_toolkit take full control.
        # Live is only used transiently during process_input() for status+output.
        self._live = None
        self._output_lines: list[str] = []

        try:
            if self._should_use_chat_window():
                await self._run_chat_window_loop()
            else:
                await self._run_prompt_session_loop()
        finally:
            await self._shutdown()
        return


    async def _run_chat_window_loop(self) -> None:
        """Run the full-screen chat window and route submissions to the REPL."""

        controller = None
        try:
            controller = self._chat_window or self._create_chat_window_controller()
            self._chat_window = controller
            self._chat_window_loop_active = True
            self._chat_streaming = False
            self._chat_submission_lock = asyncio.Lock()
            self._chat_submission_cancel_requested = False

            self._append_chat_system_output(
                "MyAgentCLI - Type /help for commands, Ctrl+D to exit."
            )
            self._append_chat_system_output(f"Project: {self._project_dir.name}")

            await controller.run(
                self._submit_chat_input,
                on_exit=self._handle_chat_exit,
                on_interrupt=self._handle_chat_interrupt,
            )
            self._chat_submission_cancel_requested = True
            await self._drain_chat_submission_tasks(cancel=False)
        except Exception as exc:
            logger.exception(
                "Chat window startup failed; falling back to prompt session",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "cli_chat_window_start",
                    "exception_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
            self._set_chat_agent_running(False)
            request_stop = (
                getattr(controller, "request_stop", None)
                if controller
                else None
            )
            if callable(request_stop):
                request_stop()
            await self._drain_chat_submission_tasks(cancel=True)
            self._chat_window_loop_active = False
            self._running = True
            await self._run_prompt_session_loop()
        finally:
            await self._drain_chat_submission_tasks(cancel=True)
            self._set_chat_agent_running(False)
            self._chat_window_loop_active = False
            self._chat_submission_lock = None
            self._chat_submission_cancel_requested = False

    async def _run_prompt_session_loop(self) -> None:
        """Run the legacy prompt_toolkit REPL loop."""

        self._console.print("MyAgentCLI - Type /help for commands, Ctrl+D to exit.")
        self._console.print(f"Project: [bold]{self._project_dir.name}[/bold]")

        if self._layout_controller:
            self._layout_controller.render_once()
        elif self._status_bar:
            try:
                status_renderable = self._status_bar.get_renderable()
                if status_renderable:
                    self._console.print(status_renderable)
                self._console.print()
            except Exception:
                logger.exception(
                    "Initial status render failed",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "cli_initial_status_render",
                    },
                )

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory

            history_file = Path.home() / ".myagent" / ".history"
            history_file.parent.mkdir(parents=True, exist_ok=True)

            kb = KeyBindings()

            @kb.add("c-c")
            def _(event):
                engine_task = getattr(self, "_active_engine_task", None)
                if engine_task and not engine_task.done():
                    if hasattr(self._engine, "interrupt_event"):
                        self._engine.interrupt_event.set()
                    engine_task.cancel()
                    event.app.current_buffer.reset()
                    return
                event.app.exit(result=self._SENTINEL_CTRL_C)

            @kb.add("enter")
            def _(event):
                """Submit on Enter. Use Alt+Enter or Esc+Enter for newlines."""
                buffer = event.current_buffer
                buffer.validate_and_handle()

            @kb.add("escape", "enter")
            def _(event):
                """Alt+Enter / Esc+Enter inserts a newline."""
                event.current_buffer.insert_text("\n")

            self._bind_inspector_toggle(kb)

            session = PromptSession(
                history=FileHistory(str(history_file)),
                multiline=True,
                key_bindings=kb,
                completer=self._build_slash_completer(),
                lexer=self._build_prompt_lexer(),
            )

            while self._running:
                try:
                    user_input = await session.prompt_async("myagent> ")
                except KeyboardInterrupt:
                    logger.exception(
                        "Prompt loop interrupted by keyboard",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "prompt toolkit keyboard interrupt",
                        },
                    )
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
                        logger.exception(
                            "Exit confirmation prompt interrupted",
                            extra={
                                "category": "error",
                                "component": "agent",
                                "context": "prompt exit confirmation",
                            },
                        )
                        self._console.print()
                        break
                except EOFError:
                    logger.exception(
                        "Prompt loop received EOF",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "prompt toolkit EOF",
                        },
                    )
                    self._console.print()
                    break

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
                        logger.exception(
                            "Idle interrupt confirmation prompt interrupted",
                            extra={
                                "category": "error",
                                "component": "agent",
                                "context": "idle interrupt confirmation",
                            },
                        )
                        self._console.print()
                        break

                user_input = user_input.strip()
                if not user_input:
                    continue

                await self.process_input(user_input)

        except ImportError:
            logger.exception(
                "prompt_toolkit unavailable; using simple input fallback",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "import prompt_toolkit repl",
                },
            )
            while self._running:
                try:
                    user_input = input("myagent> ").strip()
                except (EOFError, KeyboardInterrupt):
                    logger.exception(
                        "Fallback input prompt interrupted",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "fallback input prompt",
                        },
                    )
                    self._console.print() if self._console else print()
                    break

                if not user_input:
                    continue

                await self.process_input(user_input)

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
                if not self._append_chat_system_output(result.output):
                    self._output_to_console(result.output)

                if result.exit_requested:
                    self._running = False
                    if self._chat_window_active():
                        request_stop = getattr(self._chat_window, "request_stop", None)
                        if callable(request_stop):
                            request_stop()
                    return

                if result.skill_invoked:
                    # Store active skill for the next natural-language input (gap-2-01)
                    self._active_skill = result.skill_invoked

                if not result.success:
                    return
                return

            message = f"Unknown command: {text}"
            if not self._append_chat_system_output(message):
                self._output_to_console(message)
            return

        # Natural language to AgentEngine
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
                    self._update_status_from_event(event)
                    event_type = type(event).__name__
                    if self._renderer:
                        rendered = self._renderer.render_event(event)
                        if event_type == "StatusUpdate":
                            continue
                        if event_type == "ThinkingChunk":
                            continue
                        if event_type == "AskUserQuestion":
                            has_pending_question = True
                            if rendered and not self._append_chat_system_output(rendered):
                                self._output_to_console(rendered)
                            continue
                        if event_type == "IntentSignal":
                            intent = getattr(event, 'intent', '')
                            if intent == 'continue':
                                stream_interrupted = True
                            if rendered:
                                self._output_to_console(rendered)
                            continue
                        if event_type == "Interrupted":
                            self._output_to_console("\n[Interrupted]")
                            continue
                        if event_type == "Done":
                            if self._chat_window_active():
                                self._output_to_console("")
                            elif rendered:
                                self._output_to_console(rendered)
                            continue
                        if not rendered:
                            continue
                        if event_type == "TextChunk":
                            self._output_to_console(rendered, end="")
                        elif event_type in {"ToolCallStart", "ToolCallEnd"}:
                            if not self._append_chat_tool_output(rendered):
                                self._output_to_console(rendered)
                        elif event_type == "Error":
                            message = getattr(event, "message", rendered)
                            if not self._append_chat_error_output(message):
                                self._output_to_console(rendered)
                        else:
                            self._output_to_console(rendered)
                    else:
                        # Fallback: simple print-based rendering
                        self._render_event_fallback(event)

            layout_started = self._start_layout_for_engine_stream()
            engine_task = _asyncio.ensure_future(_run_engine())
            self._active_engine_task = engine_task
            self._set_chat_agent_running(True)
            try:
                await engine_task
            except _asyncio.CancelledError:
                current_task = _asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                logger.exception(
                    "Active engine task cancelled",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "await active engine task",
                    },
                )
                self._output_to_console("\n[Interrupted by user]")
            finally:
                self._active_engine_task = None
                self._set_chat_agent_running(False)
                self._output_to_console("")  # trailing newline after streaming
                self._stop_layout_after_engine_stream(layout_started)

            if await self._skip_chat_followups_for_submission_teardown():
                return

            # gap-19-02: After stream interruption, prompt user to decide
            if stream_interrupted:
                try:
                    confirm = await self._prompt_with_timeout(
                        "Stream interrupted. Continue? [Y/n] ",
                        timeout=120.0,
                    )
                    if confirm and confirm.strip().lower() in ("y", "yes", ""):
                        await self.process_input("continue")
                except Exception as exc:
                    logger.exception(
                        "Stream interruption follow-up prompt failed",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "cli_stream_interruption_prompt",
                            "exception_type": type(exc).__name__,
                            "traceback": traceback.format_exc(),
                        },
                    )

            # gap-13: 120s timeout for AskUserQuestion; agent auto-decides
            if has_pending_question:
                try:
                    user_answer = await self._prompt_with_timeout(
                        "Your answer (120s timeout, or agent auto-decides): ",
                        timeout=120.0,
                    )
                    if user_answer:
                        await self.process_input(user_answer)
                    else:
                        self._output_system_message(
                            "No response within 120s; agent will auto-decide.",
                            "[dim]No response within 120s; agent will auto-decide.[/dim]",
                        )
                        # Send "continue" to let the agent auto-decide
                        await self.process_input("continue")
                except Exception as exc:
                    logger.exception(
                        "Ask-user prompt failed; auto-deciding",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": "cli_ask_user_prompt",
                            "exception_type": type(exc).__name__,
                            "traceback": traceback.format_exc(),
                        },
                    )
                    self._output_system_message(
                        "Timeout; agent will auto-decide.",
                        "[dim]Timeout; agent will auto-decide.[/dim]",
                    )
        else:
            self._output_system_message(f"Echo: {text}")

    async def _prompt_with_timeout(self, prompt_text: str, timeout: float) -> str | None:
        """Prompt the user with a timeout. Returns the response or None if timed out.

        Uses asyncio.wait_for with the event loop. Falls back to regular
        input if prompt_toolkit is not available.
        """
        if self._chat_window_active():
            ask = getattr(self._chat_window, "ask", None)
            if callable(ask):
                return await ask(prompt_text, timeout)

        try:
            import asyncio as _asyncio

            from prompt_toolkit.shortcuts import prompt as pt_prompt

            loop = _asyncio.get_event_loop()
            try:
                result = await _asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: pt_prompt(prompt_text, multiline=False),
                    ),
                    timeout=timeout,
                )
                return result
            except TimeoutError:
                logger.exception(
                    "Prompt timed out waiting for user input",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "prompt toolkit ask-user timeout",
                    },
                )
                return None
        except ImportError:
            logger.exception(
                "prompt_toolkit unavailable for ask-user prompt",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "import prompt_toolkit prompt",
                },
            )
            # Fallback: standard input (blocks forever, ignore timeout)
            try:
                return input(prompt_text).strip()
            except (EOFError, KeyboardInterrupt):
                logger.exception(
                    "Fallback ask-user input interrupted",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "fallback ask-user input",
                    },
                )
                return None

    def _output_to_console(self, text: str, end: str = "\n") -> None:
        """Route output through the shared Live layout or plain console (gap-2-07)."""
        if self._append_chat_output(text, end=end):
            return

        if self._layout_controller:
            self._layout_controller.append_output(text, end=end)
            if self._should_render_layout_once_after_append():
                self._layout_controller.render_once()
            return

        if self._live:
            from rich.panel import Panel
            try:
                if text:
                    self._output_lines.append(text)
                # Trim output lines to prevent memory growth
                if len(self._output_lines) > 500:
                    self._output_lines = self._output_lines[-300:]
                # Update the status pane in the layout.
                if self._status_bar:
                    status_panel = self._status_bar.get_renderable()
                    if status_panel:
                        self._live._layout["status"].update(status_panel)
                output_text = "\n".join(self._output_lines)
                self._live._layout["output"].update(Panel(output_text, title="Output"))
            except Exception:
                logger.exception(
                    "Live output update failed; falling back to console",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "cli_live_output_update",
                    },
                )
                if self._console:
                    self._console.print(text, end=end)
        elif self._console:
            if end == "":
                self._console.print(text, end="")
            else:
                self._console.print(text)
        else:
            print(text, end=end)

    def _should_render_layout_once_after_append(self) -> bool:
        if self._layout_controller is None:
            return False
        if getattr(self._layout_controller, "is_live", False):
            return False
        return not getattr(self._layout_controller, "_live_failed", False)

    def _start_layout_for_engine_stream(self) -> bool:
        if not self._should_start_layout_for_engine_stream():
            return False
        self._layout_controller.start()
        return bool(getattr(self._layout_controller, "is_live", False))

    def _should_start_layout_for_engine_stream(self) -> bool:
        if self._chat_window_active():
            return False
        if self._layout_controller is None:
            return False
        status_config = self._status_config()
        pane_config = getattr(status_config, "status_pane", status_config)
        return not (hasattr(pane_config, "enabled") and not bool(pane_config.enabled))

    def _stop_layout_after_engine_stream(self, started: bool) -> None:
        if started and self._layout_controller:
            self._layout_controller.stop()

    def _render_event_fallback(self, event) -> None:
        """Fallback renderer when no Rich Renderer is wired."""
        event_type = type(event).__name__
        if event_type == "TextChunk":
            self._output_to_console(getattr(event, "content", ""), end="")
            return
        if event_type == "ThinkingChunk":
            return
        if event_type == "ToolCallStart":
            if self._append_chat_tool_output(f"Tool: {event.name}..."):
                return
            self._output_to_console(f"\nTool: {event.name}...", end="")
            return
        if event_type == "ToolCallEnd":
            if event.result.error:
                if self._append_chat_tool_output(f"Error: {event.result.error}"):
                    return
                self._output_to_console(f" failed: {event.result.error}")
            else:
                if self._append_chat_tool_output("done"):
                    return
                self._output_to_console(" done")
            return
        if event_type == "Done":
            self._output_to_console("")
            return
        if event_type == "Error":
            if self._append_chat_error_output(getattr(event, "message", "Error")):
                return
            self._output_to_console(f"\nError: {event.message}")
            return
        if event_type == "Interrupted":
            self._output_to_console("\n[Interrupted]")
            return

        match type(event).__name__:
            case "TextChunk":
                print(event.content, end="", flush=True)
            case "ThinkingChunk":
                pass
            case "ToolCallStart":
                print(f"\nTool: {event.name}...", end="", flush=True)
            case "ToolCallEnd":
                if event.result.error:
                    print(f" Error: {event.result.error}")
                else:
                    print(" done")
            case "Done":
                print()
            case "Error":
                print(f"\nError: {event.message}")
            case _:
                pass

    async def _periodic_dream_check(self) -> None:
        """G4: Periodically re-check dream trigger condition in long-running sessions.

        Checked every 30 minutes by default. If the dream trigger
        conditions become true mid-session, spawn a dream cycle in
        the background without blocking the REPL.
        """
        dream_check_interval = 1800  # 30 minutes in seconds

        while self._running:
            await asyncio.sleep(dream_check_interval)
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
                    logger.info("Mid-session dream trigger fired (interval check)")
                    session_store = (
                        getattr(self._engine, "session_store", None)
                        if self._engine
                        else None
                    )

                    async def _run_dream_bg(session_store=session_store):
                        try:
                            result = await self._dream_engine.run(
                                session_store=session_store
                            )
                            logger.info(
                                "Dream completed: created=%d updated=%d deleted=%d log=%s",
                                result.memories_created, result.memories_updated,
                                result.memories_deleted, result.log_path,
                            )
                        except Exception:
                            logger.exception(
                                "Background dream failed",
                                extra={
                                    "category": "error",
                                    "component": "agent",
                                    "context": "cli_background_dream",
                                },
                            )
                    asyncio.create_task(_run_dream_bg())
            except Exception:
                logger.exception(
                    "Periodic dream check failed",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "cli_periodic_dream_check",
                    },
                )

    async def _shutdown(self) -> None:
        """Graceful shutdown: stop status UI, end session, clean up."""
        self._running = False

        # G4: Cancel periodic dream checker
        if self._dream_checker_task and not self._dream_checker_task.done():
            self._dream_checker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dream_checker_task

        if self._chat_window_active():
            request_stop = getattr(self._chat_window, "request_stop", None)
            if callable(request_stop):
                request_stop()
        await self._drain_chat_submission_tasks(cancel=True)

        # Stop shared layout (gap-2-07)
        if self._layout_controller:
            self._layout_controller.stop()
        elif self._live:
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
