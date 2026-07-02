"""Dream engine — background memory consolidation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class DreamResult:
    memories_created: int = 0
    memories_updated: int = 0
    memories_deleted: int = 0
    log_path: Path | None = None


class DreamEngine:
    """Background memory consolidation engine.

    Triggers when: distance from last dream > trigger_hours AND
    cumulative rounds > trigger_rounds.
    """

    def __init__(self, config=None, memory_store=None, state_dir: Path | None = None):
        self.config = config
        self.memory_store = memory_store
        self.state_dir = state_dir or Path.home() / ".myagent"
        self._state_file = self.state_dir / "last_dream.json"

    def should_run(self, session_rounds: int) -> bool:
        if self.config and not self.config.enabled:
            return False

        state = self._load_state()
        last_run = state.get("last_run")
        trigger_hours = self.config.trigger_hours if self.config else 6
        trigger_rounds = self.config.trigger_rounds if self.config else 50

        if session_rounds < trigger_rounds:
            return False

        if last_run:
            elapsed = time.time() - last_run
            if elapsed < trigger_hours * 3600:
                return False

        return True

    async def run(self, session_store=None) -> DreamResult:
        result = DreamResult()

        # Create dream log
        log_dir = self.state_dir / "dreams"
        log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = log_dir / f"{today}.md"

        log_path.write_text(
            f"# Dream Log — {today}\n\n"
            f"Dream cycle completed.\n"
            f"- Created: {result.memories_created}\n"
            f"- Updated: {result.memories_updated}\n"
            f"- Deleted: {result.memories_deleted}\n"
        )

        result.log_path = log_path

        # Update state
        self._state_file.write_text(json.dumps({
            "last_run": time.time(),
            "round_count": 0,
        }))

        return result

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}
