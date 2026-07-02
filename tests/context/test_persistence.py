"""Tests for SessionStore."""

from datetime import datetime

import pytest

from myagent.context.builder import Message
from myagent.context.persistence import SessionStore


class TestSessionStore:
    @pytest.mark.asyncio
    async def test_create_session(self, tmp_path):
        store = SessionStore(base_dir=tmp_path / "sessions")
        session = await store.create_session("myproject", "abc1234")
        assert session.id
        assert session.project_name == "myproject"
        assert session.project_hash == "abc1234"
        assert (tmp_path / "sessions" / "myproject" / "abc1234" / session.id).exists()

    @pytest.mark.asyncio
    async def test_save_and_load(self, tmp_path):
        store = SessionStore(base_dir=tmp_path / "sessions")
        session = await store.create_session("test", "hash001")
        sid = session.id

        loaded = await store.load_session("test", "hash001", sid)
        assert loaded is not None
        assert loaded.id == sid

    @pytest.mark.asyncio
    async def test_list_sessions(self, tmp_path):
        store = SessionStore(base_dir=tmp_path / "sessions")
        await store.create_session("test", "hash002")
        await store.create_session("test", "hash002")

        summaries = await store.list_sessions("test", "hash002")
        assert len(summaries) == 2
