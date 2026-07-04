"""Tests for stream renderer."""

from myagent.agent.engine import Done, Error, StatusUpdate, TextChunk
from myagent.cli.renderer import Renderer


class TestRenderer:
    def test_render_text(self):
        r = Renderer()
        result = r.render_event(TextChunk(content="Hello"))
        assert result is not None

    def test_render_done(self):
        r = Renderer()
        result = r.render_event(Done())
        assert result is not None

    def test_render_error(self):
        r = Renderer()
        result = r.render_event(Error(message="Something went wrong"))
        assert result is not None

    def test_render_status_update(self):
        r = Renderer()
        result = r.render_event(
            StatusUpdate(scope="context", data={"context_usage": 0.42})
        )
        assert result is None

    def test_render_unknown(self):
        r = Renderer()
        from dataclasses import dataclass
        @dataclass
        class UnknownEvent:
            pass
        result = r.render_event(UnknownEvent())
        assert result is None
