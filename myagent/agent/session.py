"""Session manager — start, resume, list, end, export."""

from __future__ import annotations

from pathlib import Path


class SessionManager:
    """Manages session lifecycle: creation, resume, listing, and cleanup."""

    def __init__(
        self,
        session_store=None,
        project_context=None,
        memory_store=None,
        permissions=None,
    ):
        self.session_store = session_store
        self.project_context = project_context
        self.memory_store = memory_store
        self.permissions = permissions

    async def start_new(self, project_dir: Path, goal: str | None = None) -> object:
        project_name = project_dir.name
        import hashlib
        project_hash = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:7]

        if self.session_store:
            return await self.session_store.create_session(project_name, project_hash, goal)

        # Fallback
        from datetime import datetime
        import secrets
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        session_id = f"{date_prefix}-{secrets.token_hex(3)}"
        from myagent.context.persistence import Session
        return Session(id=session_id, project_name=project_name, project_hash=project_hash, goal=goal)

    async def resume(self, session_id: str | None, project_dir: Path) -> object | None:
        project_name = project_dir.name
        import hashlib
        project_hash = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:7]

        if self.session_store:
            if session_id:
                return await self.session_store.load_session(project_name, project_hash, session_id)
            # Resume latest
            sessions = await self.session_store.list_sessions(project_name, project_hash)
            if sessions:
                return await self.session_store.load_session(project_name, project_hash, sessions[0].session_id)
        return None

    def estimate_total_rounds(self) -> int:
        """Estimate total conversation rounds from session history.

        Used for dream engine trigger check on startup.
        Returns 0 if no sessions exist or session_store is unavailable.
        """
        if not self.session_store:
            return 0
        try:
            sessions_dir = self.session_store.base_dir
            total = 0
            if sessions_dir.exists():
                for proj_dir in sessions_dir.iterdir():
                    if not proj_dir.is_dir():
                        continue
                    for hash_dir in proj_dir.iterdir():
                        if not hash_dir.is_dir():
                            continue
                        for sess_dir in hash_dir.iterdir():
                            if not sess_dir.is_dir():
                                continue
                            ts_file = sess_dir / "transcript.json"
                            if ts_file.exists():
                                try:
                                    import json
                                    data = json.loads(ts_file.read_text(encoding="utf-8"))
                                    total += data.get("turn_count", 0)
                                except Exception:
                                    pass
            return total
        except Exception:
            return 0

    async def list_sessions(self, project_dir: Path) -> list:
        project_name = project_dir.name
        import hashlib
        project_hash = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:7]

        if self.session_store:
            return await self.session_store.list_sessions(project_name, project_hash)
        return []

    async def end_session(self, session) -> None:
        """Finalize session: mark closed, prompt for permission persistence, summarize memories."""
        import logging
        _log = logging.getLogger("myagent.session")

        # Mark session as closed in transcript
        if hasattr(session, 'goal_achieved'):
            if session.goal and session.goal_achieved is None:
                session.goal_achieved = False

        # Prompt for permission persistence (gap-2-04)
        if self.permissions:
            changes = self.permissions.get_session_changes()
            if changes:
                try:
                    from rich.console import Console
                    from rich.prompt import Prompt
                    console = Console()
                    console.print(
                        f"\n[bold yellow]权限变更[/bold yellow]: 本次会话中调整了 "
                        f"{len(changes)} 条权限规则:"
                    )
                    for c in changes:
                        console.print(f"  - {c.get('rule', 'unknown')} ({c.get('action', 'unknown')})")
                    answer = Prompt.ask(
                        "是否持久化到配置文件？[Y/n]",
                        choices=["Y", "y", "N", "n"],
                        default="Y",
                    )
                    if answer.lower() == "y":
                        self._persist_permission_changes(changes, console)
                except ImportError:
                    pass  # Rich not available

        # Display memory changes (gap-16)
        if self.memory_store:
            session_writes = self.memory_store.get_session_writes()
            created = len(session_writes.created)
            updated = len(session_writes.updated)
            deleted = len(session_writes.deleted)
            total = created + updated + deleted
            if total > 0:
                try:
                    from rich.console import Console
                    console = Console()
                    console.print(f"\n[bold]记忆变更[/bold]: 本次对话中新写入/更新了 {total} 条记忆:")
                    for name in session_writes.created:
                        console.print(f"  [green]+ 新建[/green] {name}")
                    for name in session_writes.updated:
                        console.print(f"  [yellow]~ 更新[/yellow] {name}")
                    for name in session_writes.deleted:
                        console.print(f"  [red]- 删除[/red] {name}")
                except ImportError:
                    # Fallback plain text
                    print(f"\nMemory changes: {total} total")
                    for name in session_writes.created:
                        print(f"  + Created: {name}")
                    for name in session_writes.updated:
                        print(f"  ~ Updated: {name}")
                    for name in session_writes.deleted:
                        print(f"  - Deleted: {name}")

        _log.info("Session ended: %s", getattr(session, 'id', 'unknown'))

    def _persist_permission_changes(self, changes: list[dict], console=None) -> None:
        """Write permission changes to the appropriate YAML config file (gap-2-04).

        Priority: project-level (.myagent/config.yaml) if it exists,
        otherwise user-level (~/.myagent/config.yaml).
        Creates the config file if it doesn't exist.
        """
        import yaml
        from pathlib import Path

        # Determine target config file
        project_config = Path.cwd() / ".myagent" / "config.yaml"
        user_config = Path.home() / ".myagent" / "config.yaml"

        if project_config.exists():
            config_path = project_config
        else:
            config_path = user_config

        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing config
        existing: dict = {}
        if config_path.exists():
            try:
                existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                existing = {}

        # Ensure permissions section exists
        if "permissions" not in existing:
            existing["permissions"] = {}
        perms = existing["permissions"]

        # Apply changes
        for change in changes:
            action = change.get("action", "")
            if action == "set_mode_allow_all":
                perms["default_mode"] = "allow_all"
            elif action == "add_allow":
                allowed = change.get("allowed", "")
                if allowed:
                    perms.setdefault("auto_allow", {})
                    perms["auto_allow"].setdefault("commands", [])
                    if allowed not in perms["auto_allow"]["commands"]:
                        perms["auto_allow"]["commands"].append(allowed)
            elif action == "add_deny":
                denied = change.get("denied", "")
                if denied:
                    perms.setdefault("auto_deny", {})
                    perms["auto_deny"].setdefault("commands", [])
                    if denied not in perms["auto_deny"]["commands"]:
                        perms["auto_deny"]["commands"].append(denied)

        # Write back
        try:
            config_path.write_text(
                yaml.safe_dump(existing, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
            if console:
                console.print(f"[green]权限规则已持久化到 {config_path}[/green]")
            else:
                print(f"Permission changes saved to {config_path}")
        except Exception as e:
            if console:
                console.print(f"[red]持久化失败: {e}[/red]")
            else:
                print(f"Failed to save permission changes: {e}")

    async def export_session(self, session_id: str, fmt: str, project_dir: Path) -> Path | None:
        project_name = project_dir.name
        import hashlib
        project_hash = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:7]

        if self.session_store:
            return await self.session_store.export_session(project_name, project_hash, session_id, fmt)
        return None
