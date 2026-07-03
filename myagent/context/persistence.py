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
    def __init__(self, base_dir: Path | None = None, config=None):
        self.base_dir = base_dir or Path.home() / ".myagent" / "sessions"
        self._config = config

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

        # Store complete tool result (gap-10-6: no truncation per spec).
        # For very large results, use a reference file to keep the main
        # tool call JSON manageable while preserving full traceability.
        result_raw = str(call.result)
        result_content, result_ref = self._persist_tool_result(
            sess_dir, call_count + 1, result_raw
        )
        call_data = {
            "call_id": call.call_id,
            "tool_name": call.tool_name,
            "params": call.params,
            "result": result_content,
            "permission": call.permission,
            "timestamp": call.timestamp.isoformat(),
        }
        if result_ref:
            call_data["result_ref"] = result_ref
        call_file.write_text(
            json.dumps(call_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
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
                    duration = data.get("duration", 0)
                    closed = data.get("closed", False)
                    # gap-8-10: compute live duration for open sessions
                    if duration == 0 and not closed:
                        try:
                            created_at = datetime.fromisoformat(
                                data.get("created_at", "2026-01-01T00:00:00")
                            )
                            duration = (datetime.now() - created_at).total_seconds()
                        except Exception:
                            duration = 0
                    summaries.append(
                        SessionSummary(
                            session_id=data.get("session_id", d.name),
                            created_at=datetime.fromisoformat(
                                data.get("created_at", "2026-01-01T00:00:00")
                            ),
                            first_message=data.get("first_message", ""),
                            duration=duration,
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
        session = Session(
            id=data["session_id"],
            project_name=data["project_name"],
            project_hash=data["project_hash"],
            created_at=datetime.fromisoformat(data.get("created_at", "2026-01-01T00:00:00")),
            total_tokens=data.get("total_tokens", 0),
            turn_count=data.get("turn_count", 0),
            goal=data.get("goal"),
            goal_achieved=data.get("goal_achieved"),
        )
        # Restore messages from transcript
        for msg_data in data.get("messages", []):
            msg = Message(
                role=msg_data.get("role", "user"),
                content=msg_data.get("content", ""),
                timestamp=datetime.fromisoformat(msg_data.get("timestamp", "2026-01-01T00:00:00")),
            )
            session._messages.append(msg)
        return session

    async def export_session(
        self, project_name: str, project_hash: str, session_id: str, fmt: str = "markdown"
    ) -> Path:
        """Generate a self-contained export file for the session (gap-29).

        Creates a standalone Markdown file with full conversation, tool
        calls, and summaries. Writes to the export/ subdirectory.
        """
        sess_dir = self._session_dir(project_name, project_hash, session_id)
        export_dir = sess_dir / "export"
        export_dir.mkdir(parents=True, exist_ok=True)

        if fmt == "markdown":
            export_path = export_dir / f"{session_id}-export.md"
            ts_path = sess_dir / "transcript.json"
            if not ts_path.exists():
                # Fall back to basic export
                export_path.write_text(
                    f"# Session Export: {session_id}\n\n"
                    f"No transcript data available.\n",
                    encoding="utf-8",
                )
                return export_path

            data = json.loads(ts_path.read_text(encoding="utf-8"))
            lines = [
                f"# Session Export: {session_id}",
                f"Project: {data.get('project_name', 'unknown')}",
                f"Created: {data.get('created_at', 'unknown')}",
                f"Duration: {data.get('duration', 0):.0f}s",
                f"Total Tokens: {data.get('total_tokens', 0)}",
                f"Goal: {data.get('goal', 'None')}",
                f"Goal Achieved: {data.get('goal_achieved', 'N/A')}",
                "",
                "---",
                "",
                "## Conversation",
                "",
            ]

            # Include full messages (truncated at 10000 chars each)
            for m in data.get("messages", []):
                role = m.get("role", "unknown").upper()
                content = m.get("content", "")
                timestamp = m.get("timestamp", "")
                lines.append(f"### [{role}] {timestamp}")
                lines.append("")
                # Truncate very long tool results but keep substantial content
                if len(content) > 10000:
                    lines.append(content[:10000])
                    lines.append(f"\n... (truncated, {len(content) - 10000} more chars)")
                else:
                    lines.append(content)
                lines.append("")

            # Include tool calls if available
            tools_dir = sess_dir / "tools"
            if tools_dir.exists():
                tool_files = sorted(tools_dir.glob("call-*.json"))
                if tool_files:
                    lines.append("---")
                    lines.append("")
                    lines.append("## Tool Calls")
                    lines.append("")
                    for tf in tool_files:
                        try:
                            tc = json.loads(tf.read_text(encoding="utf-8"))
                            lines.append(f"- **{tc.get('tool_name', 'unknown')}** "
                                       f"({tc.get('timestamp', '')})")
                            lines.append(f"  Result: {str(tc.get('result', ''))[:500]}")
                            lines.append("")
                        except Exception:
                            pass

            # Include summaries
            summaries_dir = sess_dir / "summaries"
            if summaries_dir.exists():
                summary_files = sorted(summaries_dir.glob("compact-*.md"))
                if summary_files:
                    lines.append("---")
                    lines.append("")
                    lines.append("## Context Summaries")
                    lines.append("")
                    for sf in summary_files:
                        lines.append(sf.read_text(encoding="utf-8")[:5000])
                        lines.append("")

            export_path.write_text("\n".join(lines), encoding="utf-8")
            return export_path

        # JSON export: include full transcript + tool calls + summaries
        export_path = export_dir / f"{session_id}-export.json"
        ts_path = sess_dir / "transcript.json"
        if ts_path.exists():
            export_data = json.loads(ts_path.read_text(encoding="utf-8"))
            # Include tool calls
            tools_dir = sess_dir / "tools"
            if tools_dir.exists():
                export_data["tool_calls"] = []
                for tf in sorted(tools_dir.glob("call-*.json")):
                    try:
                        export_data["tool_calls"].append(
                            json.loads(tf.read_text(encoding="utf-8"))
                        )
                    except Exception:
                        pass
            export_path.write_text(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return export_path

        return sess_dir / "transcript.json"

    def _write_closed_session(self, sess_dir: Path, session: Session) -> None:
        """Write final transcript state with a closed marker (G3).

        Called by SessionManager.end_session() to persist the final session
        state including the resolved goal_achieved value and a closed_at
        timestamp so session listings can distinguish active vs closed.
        """
        # G1: Gate on save_transcripts config
        if not self._should_save_transcripts():
            return

        import time
        closed_at = session.updated_at.isoformat() if hasattr(session, 'updated_at') else datetime.now().isoformat()

        # G2: Only write JSON if configured
        if self._should_write_format("json"):
            ts = sess_dir / "transcript.json"
            # Read existing transcript data to update
            existing = {}
            if ts.exists():
                try:
                    existing = json.loads(ts.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = {}

            # Write updated transcript with closed marker
            updated = {
                **existing,
                "session_id": session.id,
                "project_name": session.project_name,
                "project_hash": session.project_hash,
                "updated_at": closed_at,
                "goal": session.goal,
                "goal_achieved": session.goal_achieved,
                "total_tokens": session.total_tokens,
                "turn_count": session.turn_count,
                "closed": True,
                "closed_at": closed_at,
            }
            # Update first_message and duration if they existed
            if session._messages:
                updated["first_message"] = session._messages[0].content[:100]
                if "created_at" in existing:
                    try:
                        from datetime import datetime as dt
                        created = dt.fromisoformat(existing["created_at"])
                        updated["duration"] = (
                            dt.fromisoformat(closed_at) - created
                        ).total_seconds()
                    except Exception:
                        updated["duration"] = existing.get("duration", 0)

            ts.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # G2: Only write Markdown if configured
        if self._should_write_format("markdown"):
            md = sess_dir / "transcript.md"
            lines = [
                f"# Session: {session.id} [CLOSED]",
                f"Project: {session.project_name}",
                f"Closed at: {closed_at}",
                f"Goal: {session.goal or 'None'}",
                f"Goal Achieved: {session.goal_achieved}",
                "",
            ]
            for i, m in enumerate(session._messages):
                lines.append(f"### {m.role}")
                full_content = m.content or ""
                lines.append(self._persist_message_content(
                    sess_dir, i + 1, m.role, full_content
                ))
                lines.append("")
            md.write_text("\n".join(lines), encoding="utf-8")

    def _persist_tool_result(
        self, sess_dir: Path, call_index: int, content: str
    ) -> tuple[str, str | None]:
        """Persist a tool call result, using a reference file for very large results.

        Similar to _persist_message_content but for tool call results.
        Returns (content_to_store_in_json, ref_file_path_or_None).

        gap-10-6: Stores complete tool results without truncation per spec
        requirement for full input/output traceability.
        """
        if len(content) <= self._LONG_MESSAGE_THRESHOLD:
            return content, None

        # Store full result in long-messages/ subdirectory
        long_dir = sess_dir / "long-messages"
        long_dir.mkdir(parents=True, exist_ok=True)
        ref_file = long_dir / f"tool-call-{call_index:03d}.json"
        ref_file.write_text(
            json.dumps({
                "call_index": call_index,
                "content": content,
                "content_length": len(content),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ref_path = f"long-messages/tool-call-{call_index:03d}.json"
        truncated = (
            f"[Full result reference: {ref_path} ({len(content)} chars)]\n"
            f"{content[:2000]}\n"
            f"... (truncated in inline JSON, full content at {ref_path})"
        )
        return truncated, ref_path

    # Threshold: messages longer than this are stored in a separate reference
    # file to keep the main transcript manageable. Shorter messages are stored
    # inline with full content (no truncation — spec requirement: traceability).
    _LONG_MESSAGE_THRESHOLD = 50000

    def _persist_message_content(
        self, sess_dir: Path, msg_index: int, role: str, content: str
    ) -> str:
        """Persist message content, using reference files for very long messages.

        Messages <= _LONG_MESSAGE_THRESHOLD chars are returned as-is (full content).
        Longer messages are written to long-messages/msg-NNN.json and a reference
        string is returned. This ensures full traceability while keeping transcript
        files manageable.
        """
        if len(content) <= self._LONG_MESSAGE_THRESHOLD:
            return content

        # Store in long-messages/ subdirectory
        long_dir = sess_dir / "long-messages"
        long_dir.mkdir(parents=True, exist_ok=True)
        ref_file = long_dir / f"msg-{msg_index:03d}.json"
        ref_file.write_text(
            json.dumps({
                "msg_index": msg_index,
                "role": role,
                "content": content,
                "content_length": len(content),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return (
            f"[Full content reference: long-messages/msg-{msg_index:03d}.json "
            f"({len(content)} chars)]\n"
            f"{content[:2000]}\n"
            f"... (truncated in transcript, full content at "
            f"long-messages/msg-{msg_index:03d}.json)"
        )

    def _should_save_transcripts(self) -> bool:
        """Check config to determine if transcripts should be saved."""
        if self._config is None:
            return True  # No config means default behavior
        session_cfg = getattr(self._config, "session", None)
        if session_cfg is None:
            return True
        return getattr(session_cfg, "save_transcripts", True)

    def _should_write_format(self, fmt: str) -> bool:
        """Check if a specific transcript format should be written."""
        if self._config is None:
            return True
        session_cfg = getattr(self._config, "session", None)
        if session_cfg is None:
            return True
        transcript_format = getattr(session_cfg, "transcript_format", ["json", "markdown"])
        return fmt in transcript_format

    def _write_transcripts(self, sess_dir: Path, session: Session) -> None:
        # G1/G2: Gate on config save_transcripts and transcript_format
        if not self._should_save_transcripts():
            return

        # JSON — save ALL messages with full content (gap-r6-04: no truncation)
        if self._should_write_format("json"):
            ts = sess_dir / "transcript.json"
            messages_data = []
            for i, m in enumerate(session._messages):
                full_content = m.content or ""
                messages_data.append({
                    "role": m.role,
                    "content": self._persist_message_content(
                        sess_dir, i + 1, m.role, full_content
                    ),
                    "timestamp": m.timestamp.isoformat(),
                })
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
                        # Compute live duration on every save so crash-recovery
                        # yields a reasonable estimate (gap-r12-08).
                        "duration": (datetime.now() - session.created_at).total_seconds(),
                        "messages": messages_data,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        # Markdown — save ALL messages with full content
        if self._should_write_format("markdown"):
            md = sess_dir / "transcript.md"
            lines = [
                f"# Session: {session.id}",
                f"Project: {session.project_name}",
                f"Created: {session.created_at.isoformat()}",
                f"Goal: {session.goal or 'None'}",
                "",
            ]
            for i, m in enumerate(session._messages):
                lines.append(f"### {m.role}")
                full_content = m.content or ""
                lines.append(self._persist_message_content(
                    sess_dir, i + 1, m.role, full_content
                ))
                lines.append("")
            md.write_text("\n".join(lines), encoding="utf-8")
