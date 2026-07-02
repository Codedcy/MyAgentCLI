"""Integration test: session lifecycle (create → save → list → resume)."""

import pytest

from myagent.context.persistence import SessionStore


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tmp_path):
        """Create → save → list → load session."""
        store = SessionStore(base_dir=tmp_path / "sessions")

        # Create
        session = await store.create_session("testproject", "abc1234")
        sid = session.id

        # List
        sessions = await store.list_sessions("testproject", "abc1234")
        assert len(sessions) >= 1

        # Load
        loaded = await store.load_session("testproject", "abc1234", sid)
        assert loaded is not None
        assert loaded.id == sid
        assert loaded.project_name == "testproject"
