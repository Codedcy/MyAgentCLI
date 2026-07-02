"""Dream engine — background memory consolidation."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("myagent.memory.dream")


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
        """Consolidate memories: deduplicate by description and remove empty ones.

        Principles: never modify project code, never ask user, always background.
        """
        result = DreamResult()

        # ── Create dream log directory ──
        log_dir = self.state_dir / "dreams"
        log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = log_dir / f"{today}.md"

        actions: list[str] = []

        if self.memory_store is not None:
            # ── 1. Gather all memories from both scopes ──
            all_memories: list[tuple] = []   # (MemoryFile, mtime_float)
            seen_names: set[str] = set()

            for scope in ("project", "user"):
                try:
                    entries = await self.memory_store.list_all(scope)
                except Exception:
                    logger.warning(
                        "Dream: failed to list %s memories", scope, exc_info=True
                    )
                    continue

                for entry in entries:
                    if entry.name in seen_names:
                        continue
                    seen_names.add(entry.name)

                    try:
                        mf = await self.memory_store.read(entry.name)
                    except Exception:
                        logger.warning(
                            "Dream: failed to read memory '%s'",
                            entry.name,
                            exc_info=True,
                        )
                        continue

                    if mf is None:
                        continue

                    mtime = mf.path.stat().st_mtime if mf.path.exists() else 0.0
                    all_memories.append((mf, mtime))

            # ── 2. Remove empty / placeholder memories (< 20 chars body) ──
            empty_names: set[str] = set()
            for mf, _ in all_memories:
                if len(mf.content.strip()) < 20:
                    empty_names.add(mf.name)

            for name in empty_names:
                try:
                    await self.memory_store.delete(name)
                    actions.append(
                        f"- Deleted empty memory: `{name}` (body < 20 chars)"
                    )
                    result.memories_deleted += 1
                    logger.info(
                        "Dream: deleted empty memory '%s'", name,
                        extra={"category": "system"},
                    )
                except Exception:
                    logger.warning(
                        "Dream: failed to delete empty memory '%s'",
                        name, exc_info=True,
                    )

            # Remove deleted empties from the working set
            all_memories = [
                (mf, mt) for mf, mt in all_memories
                if mf.name not in empty_names
            ]

            # ── 3. Deduplicate by description (keep newest by mtime) ──
            by_desc: dict[str, list[tuple]] = defaultdict(list)
            for mf, mtime in all_memories:
                desc_key = mf.description.strip()
                if not desc_key:
                    continue
                by_desc[desc_key].append((mf, mtime))

            for desc_key, items in by_desc.items():
                if len(items) < 2:
                    continue
                # Sort by mtime descending — newest first
                items.sort(key=lambda x: x[1], reverse=True)
                keeper = items[0][0]
                for mf, _ in items[1:]:
                    try:
                        await self.memory_store.delete(mf.name)
                        actions.append(
                            f"- Deleted duplicate memory: `{mf.name}` "
                            f"(duplicate of `{keeper.name}`)"
                        )
                        result.memories_deleted += 1
                        logger.info(
                            "Dream: deleted duplicate '%s' (kept '%s')",
                            mf.name, keeper.name,
                            extra={"category": "system"},
                        )
                    except Exception:
                        logger.warning(
                            "Dream: failed to delete duplicate '%s'",
                            mf.name, exc_info=True,
                        )

        # ── 4. Write dream log ──
        log_lines = [f"# Dream Log - {today}", ""]
        if actions:
            log_lines.append("## Actions")
            log_lines.append("")
            log_lines.extend(actions)
            log_lines.append("")
        log_lines.append("## Summary")
        log_lines.append("")
        log_lines.append(f"- Created: {result.memories_created}")
        log_lines.append(f"- Updated: {result.memories_updated}")
        log_lines.append(f"- Deleted: {result.memories_deleted}")
        log_lines.append("")

        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        result.log_path = log_path

        # ── 5. Update state ──
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
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
