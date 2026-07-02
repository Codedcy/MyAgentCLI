"""Session persistence — transcript, tool calls, summaries.

Directory structure:
~/.myagent/sessions/<project_name>/<project_hash>/<session_id>/
├── transcript.json, transcript.md
├── subagents/sub-NNN/transcript.{json,md}
├── tools/call-NNN.json
└── summaries/compact-NNN.md
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from myagent.context.builder import Message, ToolCallRecord


@dataclass
class Session:
    id: str
    project_name: str
    project_hash: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    goal: str | None = None
    goal_achieved: bool | None = None
    total_tokens: int = 0
    turn_count: int = 0
    _messages: list[Message] = field(default_factory=list)

    def get_recent_messages(self, limit: int = 100) -> list[Message]:
        return self._messages[-limit:]

    def add_message(self, msg: Message) -> None:
        self._messages.append(msg)
        self.turn_count += 1


@dataclass
class SessionSummary:
    session_id: str
    created_at: datetime
    first_message: str
    duration: float
    total_tokens: int
    goal_achieved: bool | None = None


class SessionStore:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path.home() / ".myagent" / "sessions"

    def _session_dir(self, project_name: str, project_hash: str, session_id: str) -> Path:
        return self.base_dir / project_name / project_hash / session_id

    async def create_session(
        self, project_name: str, project_hash: str, goal: str | None = None
    ) -> Session:
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        random_suffix = secrets.token_hex(3)
        session_id = f"{date_prefix}-{random_suffix}"

        session = Session(
            id=session_id,
            project_name=project_name,
            project_hash=project_hash,
            goal=goal,
        )

        sess_dir = self._session_dir(project_name, project_hash, session_id)
        sess_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("subagents", "tools", "summaries"):
            (sess_dir / sub).mkdir(exist_ok=True)

        # Write initial transcript
        self._write_transcripts(sess_dir, session)

        return session

    async def save_turn(self, session: Session, msg: Message) -> None:
        session.updated_at = datetime.now()
        sess_dir = self._session_dir(
            session.project_name, session.project_hash, session.id
        )
        session._messages.append(msg)
        self._write_transcripts(sess_dir, session)

    async def save_tool_call(
        self, session: Session, call: ToolCallRecord
    ) -> None:
        sess_dir = self._session_dir(
            session.project_name, session.project_hash, session.id
        )
        tools_dir = sess_dir / "tools"
        call_count = len(list(tools_dir.glob("call-*.json")))
        call_file = tools_dir / f"call-{call_count + 1:03d}.json"
        call_file.write_text(
            json.dumps(
                {
                    "call_id": call.call_id,
                    "tool_name": call.tool_name,
                    "params": call.params,
                    "result": str(call.result)[:50000],
                    "permission": call.permission,
                    "timestamp": call.timestamp.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    async def list_sessions(
        self, project_name: str, project_hash: str
    ) -> list[SessionSummary]:
        proj_dir = self.base_dir / project_name / project_hash
        if not proj_dir.exists():
            return []
        summaries = []
        for d in sorted(proj_dir.iterdir(), reverse=True):
            if d.is_dir():
                ts = d / "transcript.json"
                if ts.exists():
                    data = json.loads(ts.read_text())
                    summaries.append(
                        SessionSummary(
                            session_id=data.get("session_id", d.name),
                            created_at=datetime.fromisoformat(
                                data.get("created_at", "2026-01-01T00:00:00")
                            ),
                            first_message=data.get("first_message", ""),
                            duration=data.get("duration", 0),
                            total_tokens=data.get("total_tokens", 0),
                            goal_achieved=data.get("goal_achieved"),
                        )
                    )
        return summaries

    async def load_session(
        self, project_name: str, project_hash: str, session_id: str
    ) -> Session | None:
        sess_dir = self._session_dir(project_name, project_hash, session_id)
        ts = sess_dir / "transcript.json"
        if not ts.exists():
            return None
        data = json.loads(ts.read_text())
        return Session(
            id=data["session_id"],
            project_name=data["project_name"],
            project_hash=data["project_hash"],
            created_at=datetime.fromisoformat(data.get("created_at", "2026-01-01T00:00:00")),
            total_tokens=data.get("total_tokens", 0),
            turn_count=data.get("turn_count", 0),
            goal=data.get("goal"),
            goal_achieved=data.get("goal_achieved"),
        )

    async def export_session(
        self, project_name: str, project_hash: str, session_id: str, fmt: str = "markdown"
    ) -> Path:
        sess_dir = self._session_dir(project_name, project_hash, session_id)
        if fmt == "markdown":
            md_path = sess_dir / "transcript.md"
            if md_path.exists():
                return md_path
        return sess_dir / "transcript.json"

    def _write_transcripts(self, sess_dir: Path, session: Session) -> None:
        # JSON
        ts = sess_dir / "transcript.json"
        ts.write_text(
            json.dumps(
                {
                    "session_id": session.id,
                    "project_name": session.project_name,
                    "project_hash": session.project_hash,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "goal": session.goal,
                    "goal_achieved": session.goal_achieved,
                    "total_tokens": session.total_tokens,
                    "turn_count": session.turn_count,
                    "first_message": session._messages[0].content[:100] if session._messages else "",
                    "duration": 0,
                    "messages": [
                        {
                            "role": m.role,
                            "content": m.content[:5000],
                            "timestamp": m.timestamp.isoformat(),
                        }
                        for m in session._messages[-50:]  # last 50 only
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        # Markdown
        md = sess_dir / "transcript.md"
        lines = [
            f"# Session: {session.id}",
            f"Project: {session.project_name}",
            f"Created: {session.created_at.isoformat()}",
            f"Goal: {session.goal or 'None'}",
            "",
        ]
        for m in session._messages[-50:]:
            lines.append(f"### {m.role}")
            lines.append(m.content[:2000])
            lines.append("")
        md.write_text("\n".join(lines), encoding="utf-8")
