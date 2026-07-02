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

    async def list_sessions(self, project_dir: Path) -> list:
        project_name = project_dir.name
        import hashlib
        project_hash = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:7]

        if self.session_store:
            return await self.session_store.list_sessions(project_name, project_hash)
        return []

    async def end_session(self, session) -> None:
        """Finalize session: mark closed, prompt for permission persistence, summarize memories."""
        # Mark session as closed in transcript
        if hasattr(session, 'goal_achieved'):
            if session.goal and session.goal_achieved is None:
                session.goal_achieved = False

        # Runtime permission changes would be prompted here via rich.Console.print()

        # Memory summary would be displayed here

    async def export_session(self, session_id: str, fmt: str, project_dir: Path) -> Path | None:
        project_name = project_dir.name
        import hashlib
        project_hash = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:7]

        if self.session_store:
            return await self.session_store.export_session(project_name, project_hash, session_id, fmt)
        return None
