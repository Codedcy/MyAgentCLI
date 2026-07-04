"""CLI entry point — argument parsing and component wiring."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myagent.agent.runtime_status import RuntimeStatusModel
    from myagent.cli.status import AgentInspectorPane


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="myagent",
        description="MyAgentCLI — 个人 AI Agent 助手",
        epilog=(
            "Examples:\n"
            "  myagent                              Start a new session\n"
            "  myagent --resume                     Resume the latest session\n"
            "  myagent --resume <session-id>        Resume a specific session\n"
            "  myagent --list-sessions              List all sessions\n"
            "  myagent --session <id> --export markdown   Export session as markdown\n"
            "  myagent --session <id> --export json       Export session as JSON\n"
            "  myagent --mode think-max             Start with Think Max mode\n"
            "  myagent --dangerously-skip-permissions  Start with full trust mode\n"
        ),
    )
    parser.add_argument(
        "--resume", nargs="?", const="__latest__", default=None,
        help="Resume a session (latest if no ID given)",
    )
    parser.add_argument("--list-sessions", action="store_true", help="List all sessions")
    parser.add_argument("--session", help="Session ID for export")
    parser.add_argument(
        "--export",
        choices=["markdown", "json"],
        help="Export format (markdown or json)",
        default="markdown",
    )
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


def _build_status_components(
    config,
    project_dir: Path,
) -> tuple[RuntimeStatusModel, AgentInspectorPane | None]:
    """Create runtime status state and the optional inspector pane."""
    from myagent.agent.runtime_status import RuntimeStatusModel
    from myagent.cli.status import AgentInspectorPane

    status_model = RuntimeStatusModel()
    status_model.update_session(
        project_name=project_dir.name,
        model=getattr(config.model, "model", ""),
        thinking=getattr(config.model, "thinking", ""),
    )

    status_pane = None
    if getattr(config.ui.status_pane, "enabled", True):
        status_pane = AgentInspectorPane(config.ui.status_pane, status_model)
    return status_model, status_pane


def _sync_status_model_session(status_model: RuntimeStatusModel, session) -> None:
    """Copy known session metadata into the runtime status model."""

    if status_model is None or session is None:
        return

    session_id = getattr(session, "id", "")
    if session_id:
        status_model.update_session(session_id=session_id)

    _sync_status_model_goal(
        status_model,
        getattr(session, "goal", None),
        achieved=getattr(session, "goal_achieved", None),
    )


def _sync_status_model_goal(
    status_model: RuntimeStatusModel,
    goal: str | None,
    *,
    achieved: bool | None = None,
) -> None:
    """Mark an initial CLI or restored session goal in runtime status."""

    if status_model is None or not goal:
        return

    achieved_flag = bool(achieved)
    status_model.update_goal(
        name=goal,
        active=not achieved_flag,
        achieved=achieved_flag,
        waiting_for_user=False,
    )


def _wire_subagent_status(subagent_pool, status_model: RuntimeStatusModel) -> None:
    """Wire sub-agent lifecycle callbacks into the runtime status model."""
    if getattr(subagent_pool, "_status_model_wired", False):
        return

    subagent_pool._status_model_wired = True
    if not hasattr(subagent_pool, "_task_names"):
        subagent_pool._task_names = {}

    async def _on_subagent_status_change(agent_id, status, handle, pool):
        status_value = _status_value(status)
        if status_value == "result_consumed":
            status_model.remove_subagent(agent_id)
            task_names = getattr(pool, "_task_names", None)
            if isinstance(task_names, dict):
                task_names.pop(agent_id, None)
            return

        agents = getattr(pool, "_agents", {})
        seen_agent_ids: set[str] = set()
        for hid, current_handle in list(agents.items()):
            seen_agent_ids.add(hid)
            _upsert_subagent_status(status_model, pool, hid, current_handle)

        if handle is not None and agent_id not in seen_agent_ids:
            _upsert_subagent_status(status_model, pool, agent_id, handle)

    subagent_pool.on_status_change(_on_subagent_status_change)

    original_spawn = subagent_pool.spawn
    subagent_pool._status_spawn_original = original_spawn

    async def _spawn_with_task_name(*spawn_args, **spawn_kw):
        prompt = spawn_kw.get("prompt", spawn_args[0] if spawn_args else "")
        handle = await original_spawn(*spawn_args, **spawn_kw)
        subagent_pool._task_names[handle.id] = _extract_task_name(prompt)
        await _on_subagent_status_change(
            handle.id, handle.status, handle, subagent_pool
        )
        return handle

    subagent_pool.spawn = _spawn_with_task_name


def _upsert_subagent_status(status_model, pool, agent_id: str, handle) -> None:
    status_value = _status_value(getattr(handle, "status", ""))
    if status_value == "result_consumed":
        status_model.remove_subagent(agent_id)
        return

    task_names = getattr(pool, "_task_names", {})
    fallback_name = agent_id[:12] + ".." if len(agent_id) > 14 else agent_id
    task_name = task_names.get(agent_id, fallback_name)
    progress_pct = _subagent_progress(handle)
    retry_count = getattr(handle, "_retry_count", 0)
    max_retries = getattr(handle, "_max_retries", 0)
    result_summary = ""

    if status_value in {"created", "running"} and retry_count > 0:
        status_value = "retrying"
    elif status_value == "completed":
        result = getattr(handle, "_result_data", None)
        output = getattr(result, "output", "") if result else ""
        if output:
            result_summary = output[:28] + ".." if len(output) > 30 else output

    status_model.upsert_subagent(
        agent_id,
        task_name=task_name,
        status=status_value,
        progress_pct=progress_pct,
        result_summary=result_summary,
        retry_count=retry_count,
        max_retries=max_retries,
    )


def _subagent_progress(handle) -> float:
    progress_iter = getattr(handle, "_progress_iter", None)
    if not progress_iter:
        return 0.0
    current, maximum = progress_iter
    if maximum <= 0:
        return 0.0
    return current / maximum


def _status_value(status) -> str:
    return getattr(status, "value", str(status))


def _extract_task_name(prompt: str, max_len: int = 20) -> str:
    """Extract a short task name from the spawn prompt."""
    if not prompt:
        return ""
    first_line = prompt.split("\n")[0].strip()
    if len(first_line) > max_len:
        first_line = first_line[:max_len - 2] + ".."
    return first_line or prompt[:max_len]


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
    mcp_clients = await _startup_mcp_servers(tool_registry, project_dir)
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

    # Status components must be created before LLMProvider so retry_callback
    # can update the runtime model. REPLEngine's existing status_bar parameter
    # receives the pane until Task 6 renames the integration.
    status_model, status_pane = _build_status_components(config, project_dir)

    from myagent.llm.provider import LLMProvider
    llm = LLMProvider(
        config.model,
        logging_config=config.logging,
        streaming=config.ui.streaming,
        retry_callback=lambda attempt, max_r, delay: status_model.update_health(
            retry_info=f"Retry {attempt}/{max_r} ({delay:.1f}s)"
        ),
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
    from myagent.tools.base import ToolContext
    subagent_pool._tool_context = ToolContext(
        session_id="subagent-pool",
        project_dir=project_dir,
        permissions=permissions,
        config=config,
        subagent_pool=subagent_pool,
        working_dir=project_dir,
        project_context=project_ctx,
        config_loader=loader,
        memory_store=memory_store,
        tool_registry=tool_registry,
        mcp_clients=mcp_clients,
    )

    from myagent.skills.registry import SkillRegistry
    skill_registry = SkillRegistry(project_dir=project_dir / ".myagent" / "skills")
    await skill_registry.discover()

    from myagent.context.builder import ContextBuilder
    context_builder = ContextBuilder(tool_registry, memory_store, skill_registry, config.context)

    from myagent.context.compression import CompressionEngine
    compression = CompressionEngine(
        config=config.context.compression,
        llm=llm,
        tools_config=config.tools,
    )

    from myagent.agent.session import SessionManager
    session_mgr = SessionManager(session_store, project_ctx, memory_store, permissions)

    # Dream engine — construct and auto-trigger on startup (gap-04)
    from myagent.memory.dream import DreamEngine
    dream_engine = DreamEngine(
        config=config.dream,
        memory_store=memory_store,
        state_dir=Path.home() / ".myagent",
        subagent_pool=subagent_pool,
        project_context=project_ctx,
        tool_registry=tool_registry,
        permissions=permissions,
        project_dir=project_dir,
        config_loader=loader,
    )
    # Record session start time for hours-based dream trigger (gap-r12-06)
    dream_engine.touch_session_start()
    # Check if dream should run.
    # gap-20-08: Pass last_run timestamp so estimate_total_rounds only counts
    # sessions created since the last dream — not ALL historical rounds.
    dream_state = dream_engine._load_state()
    last_dream_ts = dream_state.get("last_run")
    if dream_engine.should_run(
        session_mgr.estimate_total_rounds(since_timestamp=last_dream_ts)
        if hasattr(session_mgr, 'estimate_total_rounds') else 0
    ):
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
        _sync_status_model_goal(status_model, args.goal)

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

    # Wire status model to sub-agent pool state via lifecycle callbacks.
    _wire_subagent_status(subagent_pool, status_model)
    # Start REPL
    from myagent.cli.repl import REPLEngine

    if args.resume:
        session_id = None if args.resume == "__latest__" else args.resume
        session = await session_mgr.resume(session_id, project_dir)
        if session:
            # Set logging context with session_id (gap-16)
            from myagent.logging.context import set_context
            set_context(session_id=session.id, project_name=project_dir.name)
            # Emit startup event now that session_id is known (gap-18-04)
            from myagent.logging.logger import LogManager
            LogManager.log_startup(config=config.logging, session_id=session.id)
            # Wire session into sub-agent pool so transcripts are persisted
            # and the counter is advanced past existing sub-agent IDs (gap-r14-04)
            subagent_pool.set_session(session, session_store)
            _sync_status_model_session(status_model, session)
            # Restore goal from resumed session (gap-18-07)
            if session.goal:
                goal_tracker.set_goal(session.goal)
                _sync_status_model_goal(
                    status_model,
                    session.goal,
                    achieved=getattr(session, "goal_achieved", None),
                )
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
                renderer=renderer, status_bar=status_pane,
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
        renderer=renderer, status_bar=status_pane,
        dream_engine=dream_engine,
    )
    await repl.run()
    return 0


def _register_builtin_tools(registry) -> None:
    from myagent.tools.builtin.agent_tools import SendMessageTool, SpawnSubagentTool
    from myagent.tools.builtin.config_tools import ConfigSetTool
    from myagent.tools.builtin.exec_tools import BashTool
    from myagent.tools.builtin.file_tools import EditTool, GlobTool, ReadTool, WriteTool
    from myagent.tools.builtin.mcp_tools import MCPGetPromptTool, MCPReadResourceTool
    from myagent.tools.builtin.memory_tools import MemoryWriteTool
    from myagent.tools.builtin.search_tools import GrepTool
    from myagent.tools.builtin.session_tools import TaskCreateTool, TaskUpdateTool
    from myagent.tools.builtin.web_tools import WebFetchTool, WebSearchTool
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


async def _startup_mcp_servers(tool_registry, project_dir: Path) -> list:
    """Read mcp.json configs and start MCP servers.

    Checks user-level (~/.myagent/mcp.json) and project-level
    (.myagent/mcp.json) configs. For each configured server, spawns
    a subprocess via MCPClient, discovers tools, and registers them.
    """
    import json
    import logging
    _log = logging.getLogger("myagent.cli")

    server_configs: dict[str, dict] = {}
    config_paths = [
        Path.home() / ".myagent" / "mcp.json",
        project_dir / ".myagent" / "mcp.json",
    ]

    for config_path in config_paths:
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            _log.warning(
                "Failed to read MCP config %s: %s",
                config_path,
                e,
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp_config_load",
                },
            )
            continue

        if isinstance(data, dict):
            if isinstance(data.get("mcpServers"), dict):
                servers = data["mcpServers"]
            elif isinstance(data.get("servers"), dict):
                servers = data["servers"]
            else:
                servers = data
        else:
            servers = {}
        if isinstance(servers, dict):
            for name, server_cfg in servers.items():
                server_configs[name] = server_cfg

        # Also support top-level array format: [{"name": "...", "command": "..."}]
        if isinstance(data, list):
            for server_cfg in data:
                name = server_cfg.get("name", server_cfg.get("command", "unknown"))
                server_configs[name] = server_cfg

    mcp_clients = []
    for name, server_cfg in server_configs.items():
        try:
            client = await _start_single_mcp_server(name, server_cfg, tool_registry)
            if client:
                mcp_clients.append(client)
        except Exception:
            _log.exception(
                "Failed to start MCP server '%s'",
                name,
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp_start_all",
                },
            )

    return mcp_clients


async def _start_single_mcp_server(name: str, cfg: dict, tool_registry):
    """Start one MCP server and register its tools."""
    import logging
    _log = logging.getLogger("myagent.cli")

    from myagent.tools.mcp.adapter import MCPToolAdapter
    from myagent.tools.mcp.client import MCPClient

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
    except Exception:
        _log.exception(
            "MCP server '%s' failed to start",
            name,
            extra={
                "category": "error",
                "component": "mcp",
                "context": "mcp_server_start",
            },
        )
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

        # gap-19-10: Discover MCP resources and prompts with proper error
        # differentiation. The MCPClient methods now distinguish between
        # "method not supported" (logged at DEBUG) and genuine failures
        # (logged at WARNING) internally. We simply call them and collect
        # results — empty lists mean the capability is not available.
        resources = await client.list_resources()
        if resources:
            tool_registry.mcp_resources.extend(resources)
            _log.info("MCP server '%s' provides %d resources", name, len(resources))
        else:
            _log.debug("MCP server '%s' provides no resources", name)

        prompts = await client.list_prompts()
        if prompts:
            tool_registry.mcp_prompts.extend(prompts)
            _log.info("MCP server '%s' provides %d prompts", name, len(prompts))
        else:
            _log.debug("MCP server '%s' provides no prompts", name)

        _log.info("MCP server '%s' started with %d tools", name, len(raw_tools))
    except Exception:
        _log.exception(
            "MCP server '%s' failed during discovery",
            name,
            extra={
                "category": "error",
                "component": "mcp",
                "context": "mcp_server_discovery",
            },
        )
        try:
            await client.shutdown()
        except Exception:
            _log.exception(
                "MCP server '%s' failed during cleanup after discovery error",
                name,
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp_server_discovery_cleanup",
                },
            )
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
        logging.getLogger("myagent.cli").exception(
            "Rich unavailable while listing sessions",
            extra={
                "category": "error",
                "component": "system",
                "context": "fallback import rich table",
            },
        )
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
    except Exception:
        _log.exception(
            "Background dream failed",
            extra={
                "category": "error",
                "component": "agent",
                "context": "startup_background_dream",
            },
        )


def main() -> None:
    """Synchronous entry point for console_scripts."""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logging.getLogger("myagent.cli").exception(
            "CLI interrupted by keyboard",
            extra={
                "category": "error",
                "component": "system",
                "context": "handle CLI keyboard interrupt",
            },
        )
        print("\nGoodbye!")
        sys.exit(0)
