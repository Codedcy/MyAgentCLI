"""CLI entry point — argument parsing and component wiring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="myagent",
        description="MyAgentCLI — 个人 AI Agent 助手",
    )
    parser.add_argument(
        "--resume", nargs="?", const="__latest__", default=None,
        help="Resume a session (latest if no ID given)",
    )
    parser.add_argument("--list-sessions", action="store_true", help="List all sessions")
    parser.add_argument("--session", help="Session ID for export")
    parser.add_argument("--export", choices=["markdown", "json"], help="Export format (markdown or json)", default="markdown")
    parser.add_argument(
        "--mode",
        choices=["think-high", "think-max", "non-think"],
        default=None,
        help="Thinking mode override",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        help="Full trust mode",
    )
    parser.add_argument("--goal", help="Start with a goal")
    parser.add_argument("--config", help="Custom config path")
    parser.add_argument("--project-dir", help="Override project directory")
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    """Async entry point. Returns exit code."""
    args = parse_args(argv)

    project_dir = Path(args.project_dir) if args.project_dir else Path.cwd()

    # Load config
    from myagent.config.loader import ConfigLoader
    loader = ConfigLoader(project_dir=project_dir, config_path=args.config)
    cli_overrides = {}
    if args.mode:
        cli_overrides["mode"] = args.mode
    if args.dangerously_skip_permissions:
        cli_overrides["dangerously_skip_permissions"] = True
    config = loader.load(cli_args=cli_overrides if cli_overrides else None)

    # Detect project
    from myagent.agent.project import ProjectDetector
    detector = ProjectDetector()
    project_ctx = await detector.detect(project_dir)

    # Setup logging
    from myagent.logging.logger import LogManager
    LogManager.setup(config=config.logging)

    # Wire components
    from myagent.tools.registry import ToolRegistry
    tool_registry = ToolRegistry()
    _register_builtin_tools(tool_registry)

    # Start MCP servers and register their tools (gap-03)
    mcp_clients = await _startup_mcp_servers(tool_registry)
    # G6: Store MCP clients on the registry for mcp_read_resource/mcp_get_prompt tools
    tool_registry.mcp_clients = mcp_clients

    from myagent.permissions.controller import PermissionController
    permissions = PermissionController(
        default_mode=config.permissions.default_mode,
        auto_allow=config.permissions.auto_allow,
        auto_deny=config.permissions.auto_deny,
    )
    if args.dangerously_skip_permissions:
        permissions.skip_all(True)

    # Status bar must be created before LLMProvider so retry_callback
    # can reference it. LLMProvider doesn't depend on status_bar.
    from myagent.cli.status import StatusBar
    status_bar = StatusBar(config.ui) if config.ui.show_status_bar else None

    from myagent.llm.provider import LLMProvider
    llm = LLMProvider(
        config.model,
        logging_config=config.logging,
        streaming=config.ui.streaming,
        retry_callback=(lambda attempt, max_r, delay:
            status_bar.update(retry_info=f"Retry {attempt}/{max_r} ({delay:.1f}s)")
            if status_bar else None
        ) if status_bar else None,
    )

    from myagent.context.persistence import SessionStore
    # G4: respect config.session.sessions_dir; resolve ~ and expand env vars
    sessions_dir_raw = config.session.sessions_dir
    sessions_dir = Path(sessions_dir_raw).expanduser() if sessions_dir_raw else None
    session_store = SessionStore(base_dir=sessions_dir, config=config)
    from myagent.subagent.pool import SubAgentPool
    subagent_pool = SubAgentPool(
        config.subagents.max_concurrent, llm=llm, tool_registry=tool_registry,
        session_store=session_store, session=None,  # session set when REPL starts
    )

    from myagent.memory.store import MemoryStore
    memory_store = MemoryStore(
        project_memory_dir=project_dir / ".myagent" / "memory",
        user_memory_dir=Path.home() / ".myagent" / "memory",
    )

    from myagent.skills.registry import SkillRegistry
    skill_registry = SkillRegistry(project_dir=project_dir / ".myagent" / "skills")
    await skill_registry.discover()

    from myagent.context.builder import ContextBuilder
    context_builder = ContextBuilder(tool_registry, memory_store, skill_registry, config.context)

    from myagent.context.compression import CompressionEngine
    compression = CompressionEngine(config=config.context.compression, llm=llm, tools_config=config.tools)

    from myagent.agent.session import SessionManager
    session_mgr = SessionManager(session_store, project_ctx, memory_store, permissions)

    # Dream engine — construct and auto-trigger on startup (gap-04)
    from myagent.memory.dream import DreamEngine
    dream_engine = DreamEngine(
        config=config.dream,
        memory_store=memory_store,
        state_dir=Path.home() / ".myagent",
        subagent_pool=subagent_pool,
    )
    # Record session start time for hours-based dream trigger (gap-r12-06)
    dream_engine.touch_session_start()
    # Check if dream should run
    if dream_engine.should_run(session_mgr.estimate_total_rounds() if hasattr(session_mgr, 'estimate_total_rounds') else 0):
        import logging as _logging
        _log_dream = _logging.getLogger("myagent.cli")
        _log_dream.info("Auto-triggering dream engine on startup")
        # Run dream in background — non-blocking
        if subagent_pool:
            asyncio.create_task(_run_dream_background(dream_engine, session_store))

    from myagent.agent.goal import GoalTracker
    goal_tracker = GoalTracker(llm=llm)
    if args.goal:
        goal_tracker.set_goal(args.goal)

    from myagent.agent.engine import AgentEngine
    engine = AgentEngine(
        llm=llm,
        tool_registry=tool_registry,
        permissions=permissions,
        subagent_pool=subagent_pool,
        context_builder=context_builder,
        compression=compression,
        session_store=session_store,
        skill_registry=skill_registry,
        goal_tracker=goal_tracker,
        project_context=project_ctx,
        config=config,
        project_dir=project_dir,
        config_loader=loader,
        memory_store=memory_store,
    )

    # Handle one-shot commands
    if args.list_sessions:
        sessions = await session_mgr.list_sessions(project_dir)
        _print_sessions_rich(sessions, project_dir)
        return 0

    if args.session and args.export:
        path = await session_mgr.export_session(args.session, args.export, project_dir)
        print(f"Exported to: {path}")
        return 0

    # Wire CLI components
    from myagent.cli.commands import CommandDispatcher
    commands = CommandDispatcher()

    from myagent.cli.renderer import Renderer
    renderer = Renderer(syntax_highlight=config.ui.syntax_highlight)

    # Wire status bar to sub-agent pool state via lifecycle callbacks (gap-r6-08)
    if status_bar:
        from myagent.cli.status import SubAgentInfo

        # Store per-agent task names on the pool so callbacks can access them
        subagent_pool._task_names: dict[str, str] = {}

        async def _on_subagent_status_change(agent_id, status, handle, pool):
            """Callback invoked on every sub-agent lifecycle transition.

            Rebuilds the SubAgentInfo list from the current pool state and
            pushes it to the status bar. This covers completions, failures,
            and interruptions — not just spawn events.
            """
            details = []
            for hid, h in pool._agents.items():
                task_name = pool._task_names.get(hid, hid)
                if h.status.value in ("running", "created"):
                    # gap-8-06: compute real progress from worker iteration
                    progress_pct = 0.0
                    pi = getattr(h, '_progress_iter', None)
                    if pi:
                        cur, max_i = pi
                        if max_i > 0:
                            progress_pct = (cur / max_i) * 100.0
                    details.append(SubAgentInfo(
                        agent_id=hid,
                        task_name=task_name,
                        status="running",
                        progress_pct=progress_pct,
                    ))
                elif h.status.value == "completed":
                    result = h._result_data
                    summary = ""
                    if result and result.output:
                        output = result.output
                        if len(output) > 30:
                            summary = output[:28] + ".."
                        else:
                            summary = output
                    details.append(SubAgentInfo(
                        agent_id=hid,
                        task_name=task_name,
                        status="completed",
                        result_summary=summary,
                    ))
                elif h.status.value == "failed":
                    details.append(SubAgentInfo(
                        agent_id=hid,
                        task_name=task_name,
                        status="failed",
                    ))
                elif h.status.value == "interrupted":
                    details.append(SubAgentInfo(
                        agent_id=hid,
                        task_name=task_name,
                        status="interrupted",
                    ))
            status_bar.update(
                subagents_active=pool.active_count,
                subagents_details=details,
            )

        subagent_pool.on_status_change(_on_subagent_status_change)

        # Wrap spawn to capture task names and trigger initial status update
        _original_spawn = subagent_pool.spawn

        def _extract_task_name(prompt: str, max_len: int = 20) -> str:
            """Extract a short task name from the spawn prompt."""
            if not prompt:
                return ""
            first_line = prompt.split("\n")[0].strip()
            if len(first_line) > max_len:
                first_line = first_line[:max_len - 2] + ".."
            return first_line or prompt[:max_len]

        async def _spawn_with_task_name(*spawn_args, **spawn_kw):
            prompt = spawn_kw.get("prompt", spawn_args[0] if spawn_args else "")
            handle = await _original_spawn(*spawn_args, **spawn_kw)
            # Store task name for use in lifecycle callbacks
            subagent_pool._task_names[handle.id] = _extract_task_name(prompt)
            # Fire initial spawn notification via the same callback
            await _on_subagent_status_change(
                handle.id, handle.status, handle, subagent_pool
            )
            return handle

        subagent_pool.spawn = _spawn_with_task_name

    # Start REPL
    from myagent.cli.repl import REPLEngine

    if args.resume:
        session_id = None if args.resume == "__latest__" else args.resume
        session = await session_mgr.resume(session_id, project_dir)
        if session:
            # Set logging context with session_id (gap-16)
            from myagent.logging.context import set_context
            set_context(session_id=session.id, project_name=project_dir.name)
            # Wire session into sub-agent pool so transcripts are persisted
            # and the counter is advanced past existing sub-agent IDs (gap-r14-04)
            subagent_pool.set_session(session, session_store)
            # Restore goal from resumed session (gap-18-07)
            if session.goal:
                goal_tracker.set_goal(session.goal)
                import logging
                logging.getLogger("myagent.cli").info(
                    "Goal restored from resumed session: %s", session.goal[:100],
                    extra={"category": "agent", "event": "goal_restored"},
                )
            # Reset task list with session persistence path (gap-12)
            if hasattr(session, 'project_name') and session_store:
                from myagent.tools.builtin.session_tools import reset_task_list
                sess_dir = session_store._session_dir(
                    session.project_name, session.project_hash, session.id
                )
                reset_task_list(persist_path=sess_dir / "tasks.json")
            repl = REPLEngine(
                engine=engine, commands=commands, session_mgr=session_mgr,
                config=config, project_dir=project_dir,
                renderer=renderer, status_bar=status_bar,
                dream_engine=dream_engine,
            )
            repl._current_session = session
            await repl.run()
            return 0
        else:
            print("No session found to resume.")
            return 1

    repl = REPLEngine(
        engine=engine, commands=commands, session_mgr=session_mgr,
        config=config, project_dir=project_dir,
        renderer=renderer, status_bar=status_bar,
        dream_engine=dream_engine,
    )
    await repl.run()
    return 0


def _register_builtin_tools(registry) -> None:
    from myagent.tools.builtin.file_tools import EditTool, GlobTool, ReadTool, WriteTool
    from myagent.tools.builtin.search_tools import GrepTool
    from myagent.tools.builtin.exec_tools import BashTool
    from myagent.tools.builtin.agent_tools import SendMessageTool, SpawnSubagentTool
    from myagent.tools.builtin.session_tools import TaskCreateTool, TaskUpdateTool
    from myagent.tools.builtin.memory_tools import MemoryWriteTool
    from myagent.tools.builtin.web_tools import WebFetchTool, WebSearchTool
    from myagent.tools.builtin.config_tools import ConfigSetTool
    from myagent.tools.builtin.mcp_tools import MCPGetPromptTool, MCPReadResourceTool
    for tool_cls in [
        ReadTool, WriteTool, EditTool, GlobTool,
        GrepTool, BashTool,
        SpawnSubagentTool, SendMessageTool,
        TaskCreateTool, TaskUpdateTool,
        MemoryWriteTool, WebFetchTool, WebSearchTool,
        ConfigSetTool,
        MCPReadResourceTool, MCPGetPromptTool,
    ]:
        registry.register(tool_cls())


async def _startup_mcp_servers(tool_registry) -> list:
    """Read mcp.json configs and start MCP servers.

    Checks user-level (~/.myagent/mcp.json) and project-level
    (.myagent/mcp.json) configs. For each configured server, spawns
    a subprocess via MCPClient, discovers tools, and registers them.
    """
    import json
    import logging
    _log = logging.getLogger("myagent.cli")

    mcp_clients = []
    # Priority: project-level overrides user-level for same-named servers
    config_paths = [
        Path.home() / ".myagent" / "mcp.json",
        Path.cwd() / ".myagent" / "mcp.json",
    ]
    seen_servers: set[str] = set()

    for config_path in config_paths:
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("Failed to read MCP config %s: %s", config_path, e)
            continue

        servers = data.get("servers", data) if isinstance(data, dict) else {}
        if isinstance(servers, dict):
            for name, server_cfg in servers.items():
                if name in seen_servers:
                    continue
                seen_servers.add(name)
                try:
                    client = await _start_single_mcp_server(name, server_cfg, tool_registry)
                    if client:
                        mcp_clients.append(client)
                except Exception as e:
                    _log.error("Failed to start MCP server '%s': %s", name, e)

        # Also support top-level array format: [{"name": "...", "command": "..."}]
        if isinstance(data, list):
            for server_cfg in data:
                name = server_cfg.get("name", server_cfg.get("command", "unknown"))
                if name in seen_servers:
                    continue
                seen_servers.add(name)
                try:
                    client = await _start_single_mcp_server(name, server_cfg, tool_registry)
                    if client:
                        mcp_clients.append(client)
                except Exception as e:
                    _log.error("Failed to start MCP server '%s': %s", name, e)

    return mcp_clients


async def _start_single_mcp_server(name: str, cfg: dict, tool_registry):
    """Start one MCP server and register its tools."""
    import logging
    _log = logging.getLogger("myagent.cli")

    from myagent.tools.mcp.client import MCPClient
    from myagent.tools.mcp.adapter import MCPToolAdapter

    command = cfg.get("command")
    if not command:
        _log.warning("MCP server '%s' has no command — skipping", name)
        return None

    args = cfg.get("args", [])
    env = cfg.get("env", {})
    disabled = cfg.get("disabled", False)
    if disabled:
        _log.info("MCP server '%s' is disabled — skipping", name)
        return None

    client = MCPClient(command=command, args=args, env=env)
    try:
        await client.start()
    except Exception as e:
        _log.error("MCP server '%s' failed to start: %s", name, e)
        return None

    try:
        raw_tools = await client.list_tools()
        for raw_tool in raw_tools:
            adapter = MCPToolAdapter(
                {"name": raw_tool.name, "description": raw_tool.description,
                 "inputSchema": raw_tool.inputSchema},
                client,
            )
            tool_registry.register(adapter, source="mcp")
            _log.info("Registered MCP tool: %s (from %s)", raw_tool.name, name)

        # Also list and store resources and prompts (G10: integrate into context)
        try:
            resources = await client.list_resources()
            if resources:
                tool_registry.mcp_resources.extend(resources)
                _log.info("MCP server '%s' provides %d resources", name, len(resources))
        except Exception:
            pass

        try:
            prompts = await client.list_prompts()
            if prompts:
                tool_registry.mcp_prompts.extend(prompts)
                _log.info("MCP server '%s' provides %d prompts", name, len(prompts))
            else:
                _log.debug("MCP server '%s' provides no prompts", name)
        except Exception:
            pass

        _log.info("MCP server '%s' started with %d tools", name, len(raw_tools))
    except Exception as e:
        _log.error("MCP server '%s' failed to list tools: %s", name, e)
        try:
            await client.shutdown()
        except Exception:
            pass
        return None

    return client


def _print_sessions_rich(sessions, project_dir) -> None:
    """Compact line-based session listing matching the design spec format.

    Spec format (§十):
      2026-07-02-abc123  ✅ "设计 MyAgentCLI 架构"    2.3h  238K tk  Goal ✓
    """
    if not sessions:
        print(f"No sessions found for {project_dir.name}.")
        return
    try:
        from rich.console import Console
        from rich.text import Text
        console = Console()
    except ImportError:
        console = None

    print(f"\n{project_dir.name} ({project_dir}):")
    for s in sessions:
        # Status icon
        if s.goal_achieved is True:
            status_icon = "✅"
        elif s.goal_achieved is False:
            status_icon = "📋"
        else:
            status_icon = "—"

        # Duration formatting
        if s.duration > 0:
            hours = s.duration / 3600
            dur = f"{hours:.1f}h"
        else:
            dur = "—"

        # Token count
        if s.total_tokens > 0:
            if s.total_tokens >= 1000:
                tk = f"{s.total_tokens / 1000:.0f}K tk"
            else:
                tk = f"{s.total_tokens} tk"
        else:
            tk = "—"

        # Goal status
        if s.goal_achieved is True:
            goal_str = "Goal ✓"
        elif s.goal_achieved is False:
            goal_str = "Goal ⏳"
        else:
            goal_str = "—"

        # First message (quote-wrapped, truncated to 40 chars)
        first_msg = f'"{s.first_message[:40]}{"..." if len(s.first_message) > 40 else ""}"'

        # Build the line in spec format:
        #   <session-id>  <status> <"first msg">    <duration>  <tokens>  <goal>
        line = f"  {s.session_id}  {status_icon}  {first_msg:<44}  {dur:>6}  {tk:>8}  {goal_str}"

        if console:
            # Style with Rich: session_id in cyan, the rest in default
            text = Text()
            parts = line.split("  ", 1)
            if len(parts) >= 2:
                text.append(parts[0], style="cyan")
                text.append("  " + parts[1])
            else:
                text.append(line)
            console.print(text)
        else:
            print(line)


async def _run_dream_background(dream_engine, session_store=None) -> None:
    """Run dream engine in background without blocking startup."""
    import logging as _logging
    _log = _logging.getLogger("myagent.cli")
    try:
        result = await dream_engine.run(session_store=session_store)
        _log.info(
            "Dream completed: created=%d updated=%d deleted=%d log=%s",
            result.memories_created, result.memories_updated,
            result.memories_deleted, result.log_path,
        )
    except Exception as e:
        _log.error("Background dream failed: %s", e)


def main() -> None:
    """Synchronous entry point for console_scripts."""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
