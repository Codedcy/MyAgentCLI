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
    parser.add_argument("--export", help="Export format (markdown)", default="markdown")
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
    loader = ConfigLoader(project_dir=project_dir)
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

    from myagent.permissions.controller import PermissionController
    permissions = PermissionController(
        default_mode=config.permissions.default_mode,
        auto_allow=config.permissions.auto_allow,
        auto_deny=config.permissions.auto_deny,
    )
    if args.dangerously_skip_permissions:
        permissions.skip_all(True)

    from myagent.llm.provider import LLMProvider
    llm = LLMProvider(config.model)

    from myagent.subagent.pool import SubAgentPool
    subagent_pool = SubAgentPool(
        config.subagents.max_concurrent, llm=llm, tool_registry=tool_registry,
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
    compression = CompressionEngine(config=config.context.compression, llm=llm)

    from myagent.context.persistence import SessionStore
    session_store = SessionStore()
    from myagent.agent.session import SessionManager
    session_mgr = SessionManager(session_store, project_ctx, memory_store, permissions)

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
    )

    # Handle one-shot commands
    if args.list_sessions:
        sessions = await session_mgr.list_sessions(project_dir)
        print(f"Sessions for {project_dir.name}:")
        for s in sessions:
            print(f"  {s.session_id} — {s.first_message[:50]}...")
        return 0

    if args.session and args.export:
        path = await session_mgr.export_session(args.session, args.export, project_dir)
        print(f"Exported to: {path}")
        return 0

    # Wire CLI components
    from myagent.cli.commands import CommandDispatcher
    commands = CommandDispatcher()

    from myagent.cli.renderer import Renderer
    renderer = Renderer()

    from myagent.cli.status import StatusBar
    status_bar = StatusBar(config.ui) if config.ui.show_status_bar else None

    # Start REPL
    from myagent.cli.repl import REPLEngine

    if args.resume:
        session_id = None if args.resume == "__latest__" else args.resume
        session = await session_mgr.resume(session_id, project_dir)
        if session:
            repl = REPLEngine(
                engine=engine, commands=commands, session_mgr=session_mgr,
                config=config, project_dir=project_dir,
                renderer=renderer, status_bar=status_bar,
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

    for tool_cls in [
        ReadTool, WriteTool, EditTool, GlobTool,
        GrepTool, BashTool,
        SpawnSubagentTool, SendMessageTool,
        TaskCreateTool, TaskUpdateTool,
        MemoryWriteTool, WebFetchTool, WebSearchTool,
    ]:
        registry.register(tool_cls())


def main() -> None:
    """Synchronous entry point for console_scripts."""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
