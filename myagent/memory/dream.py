"""Dream engine — background memory consolidation."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("myagent.memory.dream")


@dataclass
class DreamResult:
    memories_created: int = 0
    memories_updated: int = 0
    memories_deleted: int = 0
    log_path: Path | None = None


@dataclass
class TranscriptFindings:
    """Structured findings from transcript scanning (G7)."""
    correction_count: int = 0
    correction_markers: list[str] = field(default_factory=list)
    top_topics: list[tuple[str, int]] = field(default_factory=list)
    sessions_scanned: int = 0
    text: list[str] = field(default_factory=list)


class DreamEngine:
    """Background memory consolidation engine.

    Triggers when: distance from last dream > trigger_hours AND
    cumulative rounds > trigger_rounds.

    When a sub-agent pool is available (G3), the dream spawns a background
    sub-agent that uses LLM reasoning to analyze memories and transcripts
    for contradictions, new conventions, repeated corrections, and stale
    facts. Falls back to rule-based analysis when no pool is available.
    """

    def __init__(
        self,
        config=None,
        memory_store=None,
        state_dir: Path | None = None,
        subagent_pool=None,
    ):
        self.config = config
        self.memory_store = memory_store
        self.state_dir = state_dir or Path.home() / ".myagent"
        self._state_file = self.state_dir / "last_dream.json"
        self._subagent_pool = subagent_pool

    def should_run(self, session_rounds: int) -> bool:
        if self.config and not self.config.enabled:
            return False

        state = self._load_state()
        last_run = state.get("last_run")
        session_started_at = state.get("session_started_at")
        trigger_hours = self.config.trigger_hours if self.config else 6
        trigger_rounds = self.config.trigger_rounds if self.config else 50

        if session_rounds < trigger_rounds:
            return False

        # Use the minimum of last dream completion time and session start time.
        # This ensures the hours counter resets on each fresh session — even if
        # a previous session ended without triggering a dream (gap-r12-06).
        effective_last = last_run
        if session_started_at is not None:
            if effective_last is None or session_started_at > effective_last:
                effective_last = session_started_at

        if effective_last:
            elapsed = time.time() - effective_last
            if elapsed < trigger_hours * 3600:
                return False

        return True

    def touch_session_start(self) -> None:
        """Record session start time in the dream state file (gap-r12-06).

        Called at the beginning of each CLI session so that the hours-based
        dream trigger resets on every fresh session, not just on dream
        completion. Without this, a session that starts, runs many rounds
        without crossing the dream trigger, then exits, would leave a stale
        timestamp in last_dream.json — causing the next session to potentially
        trigger a dream too soon.
        """
        state = self._load_state()
        state["session_started_at"] = time.time()
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(state))

    async def _run_as_subagent(self, session_store=None) -> DreamResult:
        """G3: Run dream analysis via spawned sub-agent with LLM reasoning.

        The sub-agent receives:
        - Full memory index (name, description, content summary per file)
        - Recent session transcript excerpts
        - Instructions to detect contradictions, new conventions, stale facts,
          and overlapping memories

        The sub-agent is spawned in background mode and produces a structured
        analysis, which we parse for concrete memory actions.
        """
        result = DreamResult()

        # ── Create dream log directory ──
        log_dir = self.state_dir / "dreams"
        log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = log_dir / f"{today}.md"

        # ── Build the sub-agent prompt ──
        memory_summary = await self._build_memory_summary()
        transcript_excerpts = await self._build_transcript_excerpts(session_store)

        prompt = self._build_dream_subagent_prompt(memory_summary, transcript_excerpts)

        # ── Spawn the sub-agent ──
        try:
            handle = await self._subagent_pool.spawn(
                prompt=prompt,
                tools=["memory_write", "read", "glob"],  # Tools needed for memory ops
                mode="Think High",
                background=True,
            )
            sub_result = await handle.wait()
        except Exception as e:
            logger.error("Dream sub-agent spawn/wait failed: %s", e, exc_info=True,
                         extra={"category": "error", "component": "dream"})
            raise

        # ── Parse sub-agent result ──
        analysis_text = sub_result.output or ""
        if sub_result.error:
            logger.warning(
                "Dream sub-agent reported error: %s", sub_result.error,
                extra={"category": "system"},
            )
            result.memories_created = 0
            result.memories_updated = 0
            result.memories_deleted = 0
        else:
            # Count memory operations mentioned in the sub-agent output
            import re
            created = len(re.findall(r'(?:created?|wrote?|新增|创建)\s+(?:memory|记忆)', analysis_text, re.IGNORECASE))
            updated = len(re.findall(r'(?:updated?|merged?|合并|更新)\s+(?:memory|记忆)', analysis_text, re.IGNORECASE))
            deleted = len(re.findall(r'(?:deleted?|removed?|删除|清理)\s+(?:memory|记忆)', analysis_text, re.IGNORECASE))
            result.memories_created = created
            result.memories_updated = updated
            result.memories_deleted = deleted

        # ── Write dream log ──
        log_lines = [
            f"# Dream Log - {today} (Sub-Agent)",
            "",
            "This dream cycle was executed by a background sub-agent with LLM-driven "
            "analysis. The sub-agent reviewed all memory files and recent session "
            "transcripts to identify patterns, contradictions, and stale facts.",
            "",
            "## Sub-Agent Analysis",
            "",
            analysis_text[:10000] if analysis_text else "(No analysis produced)",
            "",
            "## Summary",
            "",
            f"- Created: {result.memories_created}",
            f"- Updated: {result.memories_updated}",
            f"- Deleted: {result.memories_deleted}",
            "",
        ]
        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        result.log_path = log_path

        # ── Update state ──
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps({
            "last_run": time.time(),
            "round_count": 0,
        }))

        return result

    async def _build_memory_summary(self) -> str:
        """Build a summary of all memories for the sub-agent prompt."""
        if self.memory_store is None:
            return "(No memory store available)"

        lines = ["## Current Memories", ""]
        for scope in ("project", "user"):
            try:
                entries = await self.memory_store.list_all(scope)
            except Exception:
                continue

            if entries:
                lines.append(f"### {scope.capitalize()} Memories")
                for entry in entries:
                    try:
                        mf = await self.memory_store.read(entry.name)
                    except Exception:
                        continue
                    if mf is None:
                        continue
                    content_preview = mf.content[:300].replace("\n", " ")
                    lines.append(
                        f"- **{entry.name}** ({entry.type or 'reference'})"
                    )
                    lines.append(f"  Description: {entry.description or '(none)'}")
                    lines.append(f"  Content: {content_preview}...")
                    lines.append("")

        return "\n".join(lines) if len(lines) > 1 else "(No memories found)"

    async def _build_transcript_excerpts(self, session_store) -> str:
        """Build excerpts from recent session transcripts for the sub-agent prompt."""
        import json as _json
        lines = ["## Recent Session Transcripts", ""]

        if session_store is None:
            return "\n".join(lines + ["(No session store available)"])

        try:
            sessions_dir = Path(str(getattr(session_store, 'base_dir', '')))
            if not sessions_dir or not sessions_dir.exists():
                return "\n".join(lines + ["(No sessions found)"])

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

            recent_sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            sampled = recent_sessions[:3]

            for i, ts_file in enumerate(sampled):
                try:
                    data = _json.loads(ts_file.read_text(encoding="utf-8"))
                    session_id = data.get("session_id", ts_file.parent.name)
                    messages = data.get("messages", [])
                    # Take first 3 and last 3 messages to capture context
                    excerpt_messages = messages[:3] + (messages[-3:] if len(messages) > 6 else [])
                    lines.append(f"### Session {i + 1}: {session_id}")
                    for m in excerpt_messages:
                        role = m.get("role", "unknown")
                        content = m.get("content", "")[:200]
                        lines.append(f"  [{role}] {content}")
                    lines.append("")
                except Exception:
                    continue

            if not lines[1:]:
                lines.append("(No recent session transcripts found)")

        except Exception as e:
            lines.append(f"(Error scanning transcripts: {e})")

        return "\n".join(lines)

    def _build_dream_subagent_prompt(
        self, memory_summary: str, transcript_excerpts: str
    ) -> str:
        """Build the sub-agent prompt for dream analysis (G3, G10)."""
        return f"""You are a memory consolidation agent running in a background "dream" cycle.
Your task is to analyze existing memories and recent session transcripts,
then produce a structured analysis and execute memory maintenance actions.

## Memory Files
{memory_summary}

## Session Transcripts
{transcript_excerpts}

## Instructions

Review the memories and transcripts carefully. For each of the following,
identify specific issues and, where appropriate, use memory_write to fix them:

### 1. Duplicate / Overlapping Memories
Look for memories that cover the same facts with different names
(e.g., "coding-style" and "python-conventions" both describing snake_case rules).
If found, use memory_write to merge them into the best-named file.

### 2. Contradictory Memories (G10)
Look for memories that make opposing statements (e.g., "Use type hints" vs
"Type hints are optional"). Use semantic reasoning — not just keyword matching.
If found, use memory_write to resolve the contradiction (keep the newer or
more authoritative finding, and note the resolution).

### 3. New Conventions or Patterns
Look for repeated corrections, new conventions, or patterns in the transcripts
that are not yet captured as memories. If found, use memory_write to create a
new memory file for each convention.

### 4. Repeated Corrections
If the same correction appears 2+ times across sessions, create a
"common-corrections" memory documenting what the user consistently corrects.

### 5. Stale Memories
Identify memories that reference outdated practices or haven't been relevant
for the recent sessions. Flag them — but DO NOT delete them (the dream runner
handles deletions).

### 6. Frequent Topics
Note the most frequently discussed topics in recent sessions.

## Output Format

First, write a clear analysis section covering each of the 6 areas above.
Then execute memory_write for any concrete actions needed.

Do NOT ask questions or wait for confirmation. This runs silently in background.
Do NOT interact with project code files — only memory files."""

    async def run(self, session_store=None) -> DreamResult:
        """Consolidate memories: deduplicate, scan transcripts, find patterns.

        G3: When sub-agent pool is available, spawns a background sub-agent
        with LLM-driven analysis instead of rule-based heuristics. The sub-agent
        receives full memory index and recent transcript excerpts, then produces
        memory actions (create, update, delete, merge) using reasoning.

        Falls back to inline rule-based analysis when no sub-agent pool exists.

        Principles: never modify project code, never ask user, always background.
        """
        # G3: Delegate to sub-agent when available
        if self._subagent_pool is not None:
            try:
                return await self._run_as_subagent(session_store)
            except Exception as e:
                logger.warning(
                    "Dream sub-agent failed: %s — falling back to inline analysis", e,
                    exc_info=True,
                    extra={"category": "error", "component": "dream"},
                )

        return await self._run_inline(session_store)

    async def _run_inline(self, session_store=None) -> DreamResult:
        """Inline rule-based dream analysis (fallback path).

        Used when no sub-agent pool is available. Performs deterministic
        checks: empty memory cleanup, description-based dedup, keyword-based
        contradiction detection, and transcript pattern scanning.
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
                        "Dream: failed to list %s memories", scope, exc_info=True,
                        extra={"category": "system"},
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
                            "Dream: failed to read memory '%s'", entry.name, exc_info=True,
                            extra={"category": "system"},
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
                        "Dream: failed to delete empty memory '%s'", name, exc_info=True,
                        extra={"category": "system"},
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
                            "Dream: failed to delete duplicate '%s'", mf.name, exc_info=True,
                            extra={"category": "system"},
                        )

            # ── 4. Scan recent transcripts for patterns (gap-18) ──
            transcript_findings = await self._scan_transcripts(session_store)
            if transcript_findings.text:
                analysis_sections.append("")
                analysis_sections.append("### Patterns from Recent Sessions")
                for finding in transcript_findings.text:
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
                        "Dream: failed to delete stale memory '%s'", name, exc_info=True,
                        extra={"category": "system"},
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

    async def _scan_transcripts(self, session_store) -> TranscriptFindings:
        """Scan recent unprocessed session transcripts for patterns (gap-18).

        Identifies repeated corrections, new conventions, and topic clusters
        by scanning transcript files from recent sessions.

        Returns structured TranscriptFindings for programmatic processing (G7).
        """
        findings = TranscriptFindings()
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
                findings.text.append("No recent sessions found.")
                return findings

            # Sample up to 5 most recent sessions
            recent_sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            sampled = recent_sessions[:5]
            findings.sessions_scanned = len(sampled)

            # Scan for patterns: repeated corrections, common topics, new conventions
            correction_count = 0
            correction_markers_found: list[str] = []
            topic_keywords: dict[str, int] = {}
            correction_patterns = [
                "let me correct", "actually,", "correction:",
                "更正", "纠正", "my mistake", "i was wrong",
                "let me fix", "that's not right",
            ]
            for ts_file in sampled:
                try:
                    import json as _json
                    data = _json.loads(ts_file.read_text(encoding="utf-8"))
                    messages = data.get("messages", [])
                    text = " ".join(
                        m.get("content", "") for m in messages
                        if isinstance(m, dict)
                    )

                    # Detect correction patterns and track which markers were found
                    for marker in correction_patterns:
                        if marker in text.lower():
                            correction_count += 1
                            if marker not in correction_markers_found:
                                correction_markers_found.append(marker)

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

            findings.correction_count = correction_count
            findings.correction_markers = correction_markers_found

            if correction_count >= 2:
                findings.text.append(
                    f"**{correction_count} corrections** detected across {len(sampled)} "
                    f"recent sessions. Markers found: {', '.join(correction_markers_found)}. "
                    f"Consider consolidating correction patterns into conventions."
                )
            else:
                findings.text.append(
                    f"No significant correction patterns detected "
                    f"({correction_count} corrections across {len(sampled)} sessions)."
                )

            if topic_keywords:
                top_topics = sorted(topic_keywords.items(), key=lambda x: -x[1])[:3]
                findings.top_topics = top_topics
                topic_str = ", ".join(f"`{t}`" for t, _ in top_topics)
                findings.text.append(f"Most common topics: {topic_str}")

            if not findings.text:
                findings.text.append(
                    f"Scanned {len(sampled)} recent sessions. No significant patterns "
                    f"or repeated corrections detected."
                )

        except Exception as e:
            logger.warning("Dream transcript scanning failed: %s", e,
                           extra={"category": "system"})
            findings.text.append(f"Transcript scanning encountered an error: {e}")

        return findings

    # ── Contradiction detection constants ─────────────────────────

    # Pairs of opposing keywords used for sentence-level contradiction detection.
    # Format: (positive/assertive word, negative/denying counterpart)
    _CONTRADICTION_PAIRS: set[tuple[str, str]] = {
        ("use", "avoid"),
        ("use", "don't use"),
        ("use", "do not use"),
        ("always", "never"),
        ("prefer", "avoid"),
        ("prefer", "don't"),
        ("recommend", "avoid"),
        ("recommend", "don't"),
        ("required", "optional"),
        ("must", "must not"),
        ("must", "should not"),
        ("should", "should not"),
        ("should", "shouldn't"),
        ("enabled", "disabled"),
        ("enable", "disable"),
        ("allow", "deny"),
        ("allow", "block"),
        ("include", "exclude"),
        ("支持", "不支持"),
        ("推荐", "避免"),
        ("推荐", "不推荐"),
        ("必须", "不能"),
        ("必须", "禁止"),
        ("允许", "禁止"),
        ("开启", "关闭"),
        ("启用", "禁用"),
    }

    # Negation prefixes/suffixes that indicate contradictory assertions
    _NEGATION_PATTERNS: list[str] = [
        "no ", "not ", "never ", "don't ", "doesn't ", "shouldn't ",
        "mustn't ", "cannot ", "can't ", "won't ",
    ]

    # Chinese negation markers
    _CN_NEGATION_PATTERNS: list[str] = [
        "不", "非", "无", "未", "没", "别", "勿", "莫", "否",
    ]

    @staticmethod
    def _tokenize_name(name: str) -> set[str]:
        """Extract significant tokens from a memory name for fuzzy matching."""
        import re
        tokens = set()
        # Split on common separators: dash, underscore, dot, space
        for token in re.split(r'[-_\.\s]+', name.lower()):
            token = token.strip()
            if len(token) >= 2:
                tokens.add(token)
        return tokens

    @staticmethod
    def _name_similarity(name1: str, name2: str) -> float:
        """Compute Jaccard similarity between token sets of two memory names."""
        tokens1 = DreamEngine._tokenize_name(name1)
        tokens2 = DreamEngine._tokenize_name(name2)
        if not tokens1 or not tokens2:
            return 0.0
        intersection = len(tokens1 & tokens2)
        union = len(tokens1 | tokens2)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _extract_significant_words(text: str, min_len: int = 4) -> set[str]:
        """Extract significant lowercase words from memory content for pre-filtering."""
        import re
        words = set()
        for word in re.split(r'[\s,;:.!?()\[\]{}"\']+', text.lower()):
            word = word.strip().strip('`*_~')
            if len(word) >= min_len:
                words.add(word)
        # Remove very common stop words
        stop_words = {
            'this', 'that', 'with', 'from', 'have', 'been', 'were',
            'they', 'will', 'would', 'could', 'should', 'about',
            'their', 'there', 'which', 'when', 'where', 'what',
        }
        return words - stop_words

    @staticmethod
    def _content_overlap_ratio(text1: str, text2: str) -> float:
        """Compute overlap ratio of significant words between two texts."""
        words1 = DreamEngine._extract_significant_words(text1)
        words2 = DreamEngine._extract_significant_words(text2)
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        smaller = min(len(words1), len(words2))
        return intersection / smaller if smaller > 0 else 0.0

    def _detect_contradictions(self, all_memories: list) -> list[str]:
        """Detect potentially contradictory memories using multi-layer analysis.

        This is the inline fallback path. The primary sub-agent path uses full
        LLM semantic reasoning (see _run_as_subagent). This method provides a
        more sophisticated heuristic than simple keyword matching:

        1. Name similarity pre-filter: only compare memories whose names share
           tokens (e.g., "coding-style" and "python-conventions" are compared;
           "coding-style" and "user-preferences" are not).

        2. Content overlap pre-filter: only compare memories with significant
           word overlap (Jaccard >= 0.15), indicating related topics.

        3. Sentence-level contradiction: split each memory into sentences and
           check for negation patterns in one against positive assertions in
           the other, using an expanded set of opposition pairs.

        4. Opposite keyword pair matching: the original keyword-pair heuristic
           is retained but with a much larger, comprehensive pair set.
        """
        contradictions: list[str] = []
        if len(all_memories) < 2:
            return contradictions

        import re

        # ── Pre-compute tokenized names for similarity filtering ──
        name_tokens_cache: dict[str, set[str]] = {}
        for mf, _ in all_memories:
            name_tokens_cache[mf.name] = self._tokenize_name(mf.name)

        checked: set = set()
        for i, (mf1, _) in enumerate(all_memories):
            for mf2, _ in all_memories[i + 1:]:
                pair_key = tuple(sorted([mf1.name, mf2.name]))
                if pair_key in checked:
                    continue
                checked.add(pair_key)

                # ── Pre-filter 1: Name similarity ──
                name_sim = self._name_similarity(mf1.name, mf2.name)
                if name_sim < 0.2:
                    # Try fuzzy matching: check if any token from one name
                    # is a substring of any token from the other name.
                    tokens1 = name_tokens_cache[mf1.name]
                    tokens2 = name_tokens_cache[mf2.name]
                    fuzzy_match = any(
                        t1 in t2 or t2 in t1
                        for t1 in tokens1 for t2 in tokens2
                    )
                    if not fuzzy_match:
                        continue

                # ── Pre-filter 2: Content overlap ──
                overlap = self._content_overlap_ratio(mf1.content, mf2.content)
                if overlap < 0.10 and name_sim < 0.4:
                    continue

                text1_lower = mf1.content.lower()
                text2_lower = mf2.content.lower()

                # ── Layer 1: Extended keyword-pair matching ──
                for pos_word, neg_word in self._CONTRADICTION_PAIRS:
                    if pos_word in text1_lower and neg_word in text2_lower:
                        contradictions.append(
                            f"Potential contradiction: `{mf1.name}` uses `{pos_word}` "
                            f"while `{mf2.name}` uses `{neg_word}`"
                        )
                    elif neg_word in text1_lower and pos_word in text2_lower:
                        contradictions.append(
                            f"Potential contradiction: `{mf1.name}` uses `{neg_word}` "
                            f"while `{mf2.name}` uses `{pos_word}`"
                        )

                # ── Layer 2: Sentence-level negation analysis ──
                # Split each memory into sentences
                sentences1 = [s.strip() for s in re.split(r'[.!?\n。！？\n]+', mf1.content) if len(s.strip()) > 10]
                sentences2 = [s.strip() for s in re.split(r'[.!?\n。！？\n]+', mf2.content) if len(s.strip()) > 10]

                # Check for negation patterns in one memory's sentences against
                # key assertions in the other memory's sentences.
                for s1 in sentences1:
                    s1_lower = s1.lower()
                    # Determine if s1 is a negated statement
                    is_negated_s1 = any(
                        s1_lower.startswith(p) or f" {p}" in s1_lower
                        for p in self._NEGATION_PATTERNS
                    ) or any(p in s1 for p in self._CN_NEGATION_PATTERNS)

                    for s2 in sentences2:
                        s2_lower = s2.lower()
                        is_negated_s2 = any(
                            s2_lower.startswith(p) or f" {p}" in s2_lower
                            for p in self._NEGATION_PATTERNS
                        ) or any(p in s2 for p in self._CN_NEGATION_PATTERNS)

                        # If one sentence is negated and the other is not, they might
                        # be asserting opposite things about the same topic.
                        if is_negated_s1 != is_negated_s2:
                            # Check for shared topic words
                            words1 = self._extract_significant_words(s1, min_len=3)
                            words2 = self._extract_significant_words(s2, min_len=3)
                            shared = words1 & words2
                            if len(shared) >= 2:
                                contradictions.append(
                                    f"Potential contradiction: `{mf1.name}` states "
                                    f"\"{s1[:80]}...\" while `{mf2.name}` states "
                                    f"\"{s2[:80]}...\""
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
                # Construct frontmatter Markdown content for MemoryStore.write()
                import yaml as _yaml
                fm_dict = {
                    "name": keeper.name,
                    "description": keeper.description,
                    "metadata": getattr(keeper, "metadata", {}),
                }
                fm_yaml = _yaml.safe_dump(fm_dict, default_flow_style=False, allow_unicode=True).strip()
                full_content = f"---\n{fm_yaml}\n---\n\n{updated_content}"
                file_path = str(self.memory_store.project_dir / f"{keeper.name}.md")
                await self.memory_store.write(
                    file_path=file_path,
                    content=full_content,
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
                    extra={"category": "system"},
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
                    "Dream: failed to delete older memory '%s'", older.name, exc_info=True,
                    extra={"category": "system"},
                )

    async def _create_memories_from_patterns(
        self, findings: TranscriptFindings, result: DreamResult,
        actions: list[str], analysis_sections: list[str],
    ) -> None:
        """Create new memory files from detected patterns in transcripts (gap-2-02, G7).

        Uses structured TranscriptFindings with count-based logic instead of
        fragile substring matching on finding text.
        """
        if not findings or not self.memory_store:
            return

        import yaml as _yaml

        # G7: Use correction_count directly instead of substring matching on text
        if findings.correction_count >= 2:
            try:
                markers_desc = ", ".join(findings.correction_markers[:5]) if findings.correction_markers else "various"
                name = "common-corrections"
                description = (
                    f"Repeated corrections ({findings.correction_count} instances across "
                    f"{findings.sessions_scanned} sessions)"
                )
                body = (
                    f"Across {findings.sessions_scanned} recent sessions, "
                    f"{findings.correction_count} correction instances were detected. "
                    f"The agent was frequently corrected or self-corrected, "
                    f"indicating recurring knowledge gaps or unclear conventions.\n\n"
                    f"**Patterns detected:** {markers_desc}\n\n"
                    f"**Recommendation:** Before starting new work, review the project "
                    f"CLAUDE.md and related conventions to avoid repeated corrections.\n\n"
                    f"**Dream finding:** {findings.text[0] if findings.text else 'corrections detected'}"
                )
                fm_yaml = _yaml.safe_dump(
                    {"name": name, "description": description, "metadata": {}},
                    default_flow_style=False, allow_unicode=True,
                ).strip()
                full_content = f"---\n{fm_yaml}\n---\n\n{body}"
                file_path = str(self.memory_store.project_dir / f"{name}.md")
                await self.memory_store.write(
                    file_path=file_path,
                    content=full_content,
                )
                result.memories_created += 1
                actions.append(f"- Created `common-corrections` memory ({findings.correction_count} corrections)")
                logger.info(
                    "Dream: created 'common-corrections' memory (%d corrections)",
                    findings.correction_count,
                    extra={"category": "system"},
                )
            except Exception:
                logger.warning("Dream: failed to create 'common-corrections' memory", exc_info=True,
                               extra={"category": "system"})

        if findings.top_topics:
            try:
                topics_list = ", ".join(f"`{t[0]}`" for t in findings.top_topics)
                name = "frequent-topics"
                description = "Frequently discussed topics across recent sessions"
                body = (
                    f"The most frequently discussed topics in {findings.sessions_scanned} "
                    f"recent sessions are: {topics_list}.\n\n"
                    f"This indicates areas of active focus in the project.\n\n"
                    f"**Dream finding:** {findings.text[1] if len(findings.text) > 1 else findings.text[0] if findings.text else 'topics detected'}"
                )
                fm_yaml = _yaml.safe_dump(
                    {"name": name, "description": description, "metadata": {}},
                    default_flow_style=False, allow_unicode=True,
                ).strip()
                full_content = f"---\n{fm_yaml}\n---\n\n{body}"
                file_path = str(self.memory_store.project_dir / f"{name}.md")
                await self.memory_store.write(
                    file_path=file_path,
                    content=full_content,
                )
                result.memories_created += 1
                actions.append(f"- Created `frequent-topics` memory ({topics_list})")
            except Exception:
                logger.warning("Dream: failed to create 'frequent-topics' memory", exc_info=True,
                               extra={"category": "system"})

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}
