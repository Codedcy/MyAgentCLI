"""Tests for SessionManager."""

from pathlib import Path

import pytest

from myagent.context.persistence import SessionStore
from myagent.agent.session import SessionManager


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_start_new_with_store(self, tmp_path):
        store = SessionStore(base_dir=tmp_path / "sessions")
        mgr = SessionManager(session_store=store)

        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        session = await mgr.start_new(project_dir)
        assert session is not None
        assert session.project_name == "myproject"

    @pytest.mark.asyncio
    async def test_list_sessions(self, tmp_path):
        store = SessionStore(base_dir=tmp_path / "sessions")
        mgr = SessionManager(session_store=store)

        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        await mgr.start_new(project_dir)
        await mgr.start_new(project_dir)

        sessions = await mgr.list_sessions(project_dir)
        assert len(sessions) == 2
