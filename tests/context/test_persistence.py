"""Tests for SessionStore."""

import json

import pytest

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

    @pytest.mark.asyncio
    async def test_list_sessions_skips_corrupt_transcripts(self, tmp_path, caplog):
        store = SessionStore(base_dir=tmp_path / "sessions")
        valid = await store.create_session("test", "hash003")
        corrupt_dir = (
            tmp_path / "sessions" / "test" / "hash003" / "2026-07-03-empty"
        )
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "transcript.json").write_text("", encoding="utf-8")

        summaries = await store.list_sessions("test", "hash003")

        assert [summary.session_id for summary in summaries] == [valid.id]
        record = next(
            record
            for record in caplog.records
            if record.name == "myagent.context.persistence"
        )
        assert record.category == "error"
        assert record.component == "agent"
        assert record.context == "session_list_read_transcript"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [[], "oops", 123])
    async def test_list_sessions_skips_non_object_transcripts(
        self,
        tmp_path,
        caplog,
        payload,
    ):
        store = SessionStore(base_dir=tmp_path / "sessions")
        valid = await store.create_session("test", "hash004")
        bad_dir = (
            tmp_path
            / "sessions"
            / "test"
            / "hash004"
            / f"2026-07-03-{type(payload).__name__}"
        )
        bad_dir.mkdir(parents=True)
        (bad_dir / "transcript.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

        summaries = await store.list_sessions("test", "hash004")

        assert [summary.session_id for summary in summaries] == [valid.id]
        record = next(
            record
            for record in caplog.records
            if getattr(record, "context", "") == "session_list_invalid_transcript"
        )
        assert record.category == "error"
        assert record.component == "agent"
        assert record.transcript_path == str(bad_dir / "transcript.json")

    @pytest.mark.asyncio
    async def test_list_sessions_skips_transcript_with_wrong_field_types(
        self,
        tmp_path,
        caplog,
    ):
        store = SessionStore(base_dir=tmp_path / "sessions")
        valid = await store.create_session("test", "hash005")
        bad_dir = (
            tmp_path
            / "sessions"
            / "test"
            / "hash005"
            / "2026-07-03-wrong-types"
        )
        bad_dir.mkdir(parents=True)
        (bad_dir / "transcript.json").write_text(
            json.dumps(
                {
                    "session_id": ["not", "a", "string"],
                    "created_at": "2026-07-03T00:00:00",
                    "first_message": {"not": "text"},
                    "duration": "soon",
                    "total_tokens": "many",
                }
            ),
            encoding="utf-8",
        )

        summaries = await store.list_sessions("test", "hash005")

        assert [summary.session_id for summary in summaries] == [valid.id]
        record = next(
            record
            for record in caplog.records
            if getattr(record, "context", "") == "session_list_invalid_transcript"
        )
        assert record.category == "error"
        assert record.component == "agent"
