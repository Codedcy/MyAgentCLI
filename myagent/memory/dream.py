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
        """Consolidate memories: deduplicate, scan transcripts, find patterns.

        Enhanced with transcript scanning (gap-18) and narrative analysis (gap-35).
        Principles: never modify project code, never ask user, always background.
        """
        result = DreamResult()

        # ── Create dream log directory ──
        log_dir = self.state_dir / "dreams"
        log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = log_dir / f"{today}.md"

        actions: list[str] = []
        analysis_sections: list[str] = []
        analysis_sections.append("## Dream Analysis")

        if self.memory_store is not None:
            # ── 1. Gather all memories from both scopes ──
            all_memories: list[tuple] = []
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
                            "Dream: failed to read memory '%s'", entry.name, exc_info=True
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
                    actions.append(f"- Deleted empty memory: `{name}` (body < 20 chars)")
                    result.memories_deleted += 1
                    logger.info(
                        "Dream: deleted empty memory '%s'", name,
                        extra={"category": "system"},
                    )
                except Exception:
                    logger.warning(
                        "Dream: failed to delete empty memory '%s'", name, exc_info=True
                    )

            all_memories = [(mf, mt) for mf, mt in all_memories if mf.name not in empty_names]

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
                items.sort(key=lambda x: x[1], reverse=True)
                keeper = items[0][0]
                for mf, _ in items[1:]:
                    try:
                        await self.memory_store.delete(mf.name)
                        actions.append(
                            f"- Deleted duplicate memory: `{mf.name}` (duplicate of `{keeper.name}`)"
                        )
                        result.memories_deleted += 1
                        logger.info(
                            "Dream: deleted duplicate '%s' (kept '%s')",
                            mf.name, keeper.name,
                            extra={"category": "system"},
                        )
                    except Exception:
                        logger.warning(
                            "Dream: failed to delete duplicate '%s'", mf.name, exc_info=True
                        )

            # ── 4. Scan recent transcripts for patterns (gap-18) ──
            transcript_findings = await self._scan_transcripts(session_store)
            if transcript_findings:
                analysis_sections.append("")
                analysis_sections.append("### Patterns from Recent Sessions")
                for finding in transcript_findings:
                    analysis_sections.append(f"- {finding}")

            # ── 5. Detect contradictions between memories (gap-18) ──
            contradictions = self._detect_contradictions(all_memories)
            if contradictions:
                analysis_sections.append("")
                analysis_sections.append("### Contradictions Detected")
                for c in contradictions:
                    analysis_sections.append(f"- {c}")
                # gap-2-02: Merge contradictory memories — keep newer, delete older
                await self._merge_contradictions(contradictions, all_memories, result, actions)

            # ── 6. Identify and DELETE stale memories (gap-2-13, > 30 days) ──
            stale_count = 0
            stale_to_delete: list[str] = []
            for mf, mtime in all_memories:
                age_days = (time.time() - mtime) / 86400
                if age_days > 30 and len(mf.content.strip()) >= 20:
                    analysis_sections.append(
                        f"- Stale memory: `{mf.name}` (last modified {age_days:.0f} days ago)"
                    )
                    stale_to_delete.append(mf.name)
                    stale_count += 1

            for name in stale_to_delete:
                try:
                    await self.memory_store.delete(name)
                    actions.append(f"- Deleted stale memory: `{name}` (> 30 days)")
                    result.memories_deleted += 1
                    logger.info(
                        "Dream: deleted stale memory '%s'", name,
                        extra={"category": "system"},
                    )
                except Exception:
                    logger.warning(
                        "Dream: failed to delete stale memory '%s'", name, exc_info=True
                    )

            if stale_count == 0:
                analysis_sections.append("")
                analysis_sections.append("### No Stale Memories")
                analysis_sections.append("All memories have been accessed within the last 30 days.")

            # ── 6b. Create memories from transcript patterns (gap-2-02) ──
            await self._create_memories_from_patterns(
                transcript_findings, result, actions, analysis_sections
            )

        # ── 7. Write dream log with narrative analysis (gap-35) ──
        log_lines = [f"# Dream Log - {today}", ""]
        log_lines.append(
            "This dream cycle analyzed memories and recent sessions to consolidate "
            "knowledge, detect patterns, and maintain memory health."
        )
        log_lines.append("")
        if actions:
            log_lines.append("## Actions")
            log_lines.append("")
            log_lines.extend(actions)
            log_lines.append("")
        log_lines.extend(analysis_sections)
        log_lines.append("")
        log_lines.append("## Summary")
        log_lines.append("")
        log_lines.append(f"- Created: {result.memories_created}")
        log_lines.append(f"- Updated: {result.memories_updated}")
        log_lines.append(f"- Deleted: {result.memories_deleted}")
        log_lines.append(f"- Total memories after dream: {len(all_memories) if self.memory_store else 'N/A'}")
        log_lines.append("")

        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        result.log_path = log_path

        # ── 8. Update state ──
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps({
            "last_run": time.time(),
            "round_count": 0,
        }))

        return result

    async def _scan_transcripts(self, session_store) -> list[str]:
        """Scan recent unprocessed session transcripts for patterns (gap-18).

        Identifies repeated corrections, new conventions, and topic clusters
        by scanning transcript files from recent sessions.
        """
        findings: list[str] = []
        if session_store is None:
            return findings

        try:
            sessions_dir = Path(str(getattr(session_store, 'base_dir', '')))
            if not sessions_dir or not sessions_dir.exists():
                return findings

            # Find recent sessions (last 7 days)
            recent_cutoff = time.time() - 7 * 86400
            recent_sessions = []
            for proj_dir in sessions_dir.iterdir():
                if not proj_dir.is_dir():
                    continue
                for hash_dir in proj_dir.iterdir():
                    if not hash_dir.is_dir():
                        continue
                    for sess_dir in hash_dir.iterdir():
                        if not sess_dir.is_dir():
                            continue
                        ts_file = sess_dir / "transcript.json"
                        if ts_file.exists() and ts_file.stat().st_mtime > recent_cutoff:
                            recent_sessions.append(ts_file)

            if not recent_sessions:
                return findings

            # Sample up to 5 most recent sessions
            recent_sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            sampled = recent_sessions[:5]

            # Scan for patterns: repeated corrections, common topics, new conventions
            correction_count = 0
            topic_keywords: dict[str, int] = {}
            for ts_file in sampled:
                try:
                    import json as _json
                    data = _json.loads(ts_file.read_text(encoding="utf-8"))
                    messages = data.get("messages", [])
                    text = " ".join(
                        m.get("content", "") for m in messages
                        if isinstance(m, dict)
                    )

                    # Detect correction patterns: "actually", "let me correct", etc.
                    correction_markers = [
                        "let me correct", "actually,", "correction:",
                        "更正", "纠正", "my mistake", "i was wrong",
                        "let me fix", "that's not right",
                    ]
                    for marker in correction_markers:
                        if marker in text.lower():
                            correction_count += 1

                    # Track topic keywords
                    keywords = [
                        "python", "javascript", "api", "database", "test",
                        "config", "deploy", "error", "bug", "fix",
                        "refactor", "性能", "安全", "日志", "权限",
                    ]
                    for kw in keywords:
                        if kw in text.lower():
                            topic_keywords[kw] = topic_keywords.get(kw, 0) + 1

                except Exception:
                    continue

            if correction_count >= 2:
                findings.append(
                    f"**{correction_count} corrections** detected across {len(sampled)} "
                    f"recent sessions. Consider consolidating correction patterns into "
                    f"conventions."
                )

            if topic_keywords:
                top_topics = sorted(topic_keywords.items(), key=lambda x: -x[1])[:3]
                topic_str = ", ".join(f"`{t}`" for t, _ in top_topics)
                findings.append(f"Most common topics: {topic_str}")

            if not findings:
                findings.append(
                    f"Scanned {len(sampled)} recent sessions. No significant patterns "
                    f"or repeated corrections detected."
                )

        except Exception as e:
            logger.warning("Dream transcript scanning failed: %s", e)
            findings.append(f"Transcript scanning encountered an error: {e}")

        return findings

    def _detect_contradictions(self, all_memories: list) -> list[str]:
        """Detect potentially contradictory memories by comparing content (gap-18).

        Uses simple keyword-based contradiction detection as a heuristic.
        Full semantic contradiction detection would require LLM analysis.
        """
        contradictions: list[str] = []
        if len(all_memories) < 2:
            return contradictions

        # Heuristic: check for memories with similar names but contradictory keywords
        contradiction_pairs = {
            ("use", "avoid"),
            ("always", "never"),
            ("prefer", "avoid"),
            ("支持", "不支持"),
            ("推荐", "避免"),
        }

        names_seen: dict[str, str] = {}  # name_lower → full_name
        for mf, _ in all_memories:
            name_lower = mf.name.lower()
            if name_lower in names_seen:
                continue
            names_seen[name_lower] = mf.name

        # Compare pairs
        checked: set = set()
        for i, (mf1, _) in enumerate(all_memories):
            for mf2, _ in all_memories[i + 1:]:
                pair_key = tuple(sorted([mf1.name, mf2.name]))
                if pair_key in checked:
                    continue
                checked.add(pair_key)

                text1 = mf1.content.lower()
                text2 = mf2.content.lower()
                for pos_word, neg_word in contradiction_pairs:
                    if pos_word in text1 and neg_word in text2:
                        contradictions.append(
                            f"Potential contradiction: `{mf1.name}` uses `{pos_word}` "
                            f"while `{mf2.name}` uses `{neg_word}`"
                        )
                    elif neg_word in text1 and pos_word in text2:
                        contradictions.append(
                            f"Potential contradiction: `{mf1.name}` uses `{neg_word}` "
                            f"while `{mf2.name}` uses `{pos_word}`"
                        )

        return contradictions[:5]  # Limit to top 5

    async def _merge_contradictions(
        self, contradictions: list[str], all_memories: list,
        result: DreamResult, actions: list[str],
    ) -> None:
        """Merge contradictory memories: keep the newer one, delete the older (gap-2-02)."""
        # Extract conflicting name pairs from contradiction strings
        import re
        name_pattern = re.compile(r'`([^`]+)`')

        processed: set[str] = set()
        for c_text in contradictions:
            names = name_pattern.findall(c_text)
            if len(names) < 2:
                continue
            # Sort pair for consistent handling
            pair = tuple(sorted(names[:2]))
            if pair in processed:
                continue
            processed.add(pair)

            # Find the actual memory files for these names
            name_to_entry: dict[str, tuple] = {}
            for mf, mtime in all_memories:
                if mf.name in pair and mf.name not in name_to_entry:
                    name_to_entry[mf.name] = (mf, mtime)

            if len(name_to_entry) < 2:
                continue

            entries = list(name_to_entry.values())
            entries.sort(key=lambda x: x[1], reverse=True)
            keeper, keeper_mtime = entries[0]
            older, older_mtime = entries[1]

            # Update the keeper's content to note the resolution
            try:
                resolution_note = (
                    f"\n\n**Resolved contradiction (dream cycle):** Previously conflicted "
                    f"with `{older.name}`. The newer memory (`{keeper.name}`) is kept."
                )
                updated_content = keeper.content + resolution_note
                await self.memory_store.write(
                    name=keeper.name,
                    content=updated_content,
                    description=keeper.description,
                )
                result.memories_updated += 1
                actions.append(
                    f"- Updated `{keeper.name}` to resolve contradiction with `{older.name}`"
                )
                logger.info(
                    "Dream: resolved contradiction — updated '%s', kept newer",
                    keeper.name,
                    extra={"category": "system"},
                )
            except Exception:
                logger.warning(
                    "Dream: failed to update '%s' for contradiction resolution",
                    keeper.name, exc_info=True,
                )

            # Delete the older memory
            try:
                await self.memory_store.delete(older.name)
                actions.append(
                    f"- Deleted `{older.name}` (older duplicate from contradiction with `{keeper.name}`)"
                )
                result.memories_deleted += 1
            except Exception:
                logger.warning(
                    "Dream: failed to delete older memory '%s'", older.name, exc_info=True
                )

    async def _create_memories_from_patterns(
        self, findings: list[str], result: DreamResult,
        actions: list[str], analysis_sections: list[str],
    ) -> None:
        """Create new memory files from detected patterns in transcripts (gap-2-02)."""
        if not findings or not self.memory_store:
            return

        for finding in findings:
            # Extract meaningful patterns to create memories
            if "corrections" in finding.lower() and "detected" in finding.lower():
                try:
                    await self.memory_store.write(
                        name="common-corrections",
                        content=(
                            "This project has patterns of repeated corrections across sessions. "
                            "Common issues include: approach corrections, naming fixes, and style adjustments. "
                            "Consider reviewing the project conventions before starting new work.\n\n"
                            f"**Dream finding:** {finding}"
                        ),
                        description="Common correction patterns detected in recent sessions",
                    )
                    result.memories_created += 1
                    actions.append("- Created `common-corrections` memory from transcript patterns")
                    logger.info(
                        "Dream: created 'common-corrections' memory from transcript patterns",
                        extra={"category": "system"},
                    )
                except Exception:
                    logger.warning("Dream: failed to create 'common-corrections' memory", exc_info=True)

            if "topics" in finding.lower() and ":" in finding:
                try:
                    await self.memory_store.write(
                        name="frequent-topics",
                        content=(
                            "The most frequently discussed topics in recent sessions are "
                            "recorded here for context awareness.\n\n"
                            f"**Dream finding:** {finding}"
                        ),
                        description="Frequently discussed topics across recent sessions",
                    )
                    result.memories_created += 1
                    actions.append("- Created `frequent-topics` memory from transcript topics")
                except Exception:
                    logger.warning("Dream: failed to create 'frequent-topics' memory", exc_info=True)

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}
