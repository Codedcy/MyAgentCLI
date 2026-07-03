"""Dream engine — background memory consolidation."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
        project_context=None,
    ):
        self.config = config
        self.memory_store = memory_store
        self.state_dir = state_dir or Path.home() / ".myagent"
        self._state_file = self.state_dir / "last_dream.json"
        self._subagent_pool = subagent_pool
        # ProjectContext for cross-referencing memory facts against
        # detected environment (gap-15-04: inline factual error detection)
        self._project_context = project_context

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
        if session_started_at is not None and (
            effective_last is None or session_started_at > effective_last
        ):
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

    def _transcript_id(self, path: Path) -> str:
        """Stable transcript identifier persisted in dream state."""
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def _processed_transcript_ids(self) -> set[str]:
        state = self._load_state()
        processed = state.get("processed_transcripts", [])
        if not isinstance(processed, list):
            return set()
        return {str(item) for item in processed if isinstance(item, str)}

    def _discover_transcript_files(self, session_store) -> list[Path]:
        """Discover all persisted transcript files without age or count limits."""
        if session_store is None:
            return []

        sessions_dir = Path(str(getattr(session_store, 'base_dir', '')))
        if not sessions_dir or not sessions_dir.exists():
            return []

        transcripts: list[Path] = []
        try:
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
                        if ts_file.exists():
                            transcripts.append(ts_file)
        except Exception:
            logger.exception(
                "Dream transcript discovery failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "dream.discover_transcripts",
                },
            )
            return []

        transcripts.sort(
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        return transcripts

    def _unprocessed_transcript_files(self, session_store) -> list[Path]:
        processed = self._processed_transcript_ids()
        return [
            path for path in self._discover_transcript_files(session_store)
            if self._transcript_id(path) not in processed
        ]

    def _write_completed_state(
        self,
        session_store=None,
        processed_files: list[Path] | None = None,
    ) -> None:
        state = self._load_state()
        now = time.time()
        processed = self._processed_transcript_ids()
        files = (
            processed_files
            if processed_files is not None
            else self._discover_transcript_files(session_store)
        )
        processed.update(self._transcript_id(path) for path in files)
        state.update({
            "last_run": now,
            "round_count": 0,
            "last_processed_at": now,
            "processed_transcripts": sorted(processed),
        })
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
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        log_path = log_dir / f"{today}.md"

        # ── Build the sub-agent prompt ──
        memory_summary = await self._build_memory_summary()
        unprocessed_transcripts = self._unprocessed_transcript_files(session_store)
        prompt_transcripts = unprocessed_transcripts[:3]
        transcript_excerpts = await self._build_transcript_excerpts(
            session_store, transcript_files=prompt_transcripts
        )

        # ── Detect factual errors to pass to the sub-agent (gap-17-04) ──
        factual_errors_text = await self._build_factual_errors_section()

        prompt = self._build_dream_subagent_prompt(
            memory_summary, transcript_excerpts, factual_errors_text
        )

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
            logger.error(
                "Dream sub-agent spawn/wait failed: %s",
                e,
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "dream.subagent_spawn",
                },
            )
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
            # Primary: parse structured JSON summary block from sub-agent output (gap-16-04)
            parsed = self._parse_dream_json_summary(analysis_text)
            if parsed:
                result.memories_created = parsed.get("created", 0)
                result.memories_updated = parsed.get("updated", 0)
                result.memories_deleted = parsed.get("deleted", 0)
            else:
                # Fallback 1: introspect memory_store session writes for actual counts
                session_counts = self._count_from_session_writes()
                if session_counts:
                    result.memories_created = session_counts.get("created", 0)
                    result.memories_updated = session_counts.get("updated", 0)
                    result.memories_deleted = session_counts.get("deleted", 0)
                else:
                    # Fallback 2: regex-based counting on NL output (legacy)
                    import re
                    created = len(re.findall(
                        r'(?:created?|wrote?|新增|创建)\s+(?:memory|记忆)',
                        analysis_text, re.IGNORECASE,
                    ))
                    updated = len(re.findall(
                        r'(?:updated?|merged?|合并|更新)\s+(?:memory|记忆)',
                        analysis_text, re.IGNORECASE,
                    ))
                    deleted = len(re.findall(
                        r'(?:deleted?|removed?|删除|清理)\s+(?:memory|记忆)',
                        analysis_text, re.IGNORECASE,
                    ))
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
        self._write_completed_state(
            session_store=session_store,
            processed_files=prompt_transcripts,
        )

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
                logger.exception(
                    "Dream memory listing failed",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": f"dream.memory_summary.list:{scope}",
                    },
                )
                continue

            if entries:
                lines.append(f"### {scope.capitalize()} Memories")
                for entry in entries:
                    try:
                        mf = await self.memory_store.read(entry.name)
                    except Exception:
                        logger.exception(
                            "Dream memory read failed",
                            extra={
                                "category": "error",
                                "component": "agent",
                                "context": f"dream.memory_summary.read:{entry.name}",
                            },
                        )
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

    async def _build_transcript_excerpts(
        self,
        session_store,
        transcript_files: list[Path] | None = None,
    ) -> str:
        """Build excerpts from unprocessed transcripts for the sub-agent prompt."""
        import json as _json
        lines = ["## Unprocessed Session Transcripts", ""]

        if session_store is None:
            return "\n".join(lines + ["(No session store available)"])

        try:
            sampled = (
                transcript_files
                if transcript_files is not None
                else self._unprocessed_transcript_files(session_store)[:3]
            )

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
                    logger.exception(
                        "Dream transcript excerpt failed",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": f"dream.transcript_excerpt:{ts_file}",
                        },
                    )
                    continue

            if not lines[1:]:
                lines.append("(No unprocessed session transcripts found)")

        except Exception as e:
            logger.exception(
                "Dream transcript excerpt scanning failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "dream.transcript_excerpts",
                },
            )
            lines.append(f"(Error scanning transcripts: {e})")

        return "\n".join(lines)

    async def _build_factual_errors_section(self) -> str:
        """Build a factual error summary for the sub-agent prompt (gap-17-04).

        Runs _check_factual_errors on all memories to detect discrepancies
        between memory file content and the detected project environment.
        Returns formatted text for injection into the sub-agent prompt,
        or an empty string if no errors are found or no project context exists.
        """
        if self._project_context is None or self.memory_store is None:
            return ""

        # Gather all memories (same logic as _run_inline)
        all_memories: list[tuple] = []
        seen_names: set[str] = set()
        for scope in ("project", "user"):
            try:
                entries = await self.memory_store.list_all(scope)
            except Exception:
                logger.exception(
                    "Dream factual error memory listing failed",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": f"dream.factual_errors.list:{scope}",
                    },
                )
                continue
            for entry in entries:
                if entry.name in seen_names:
                    continue
                seen_names.add(entry.name)
                try:
                    mf = await self.memory_store.read(entry.name)
                except Exception:
                    logger.exception(
                        "Dream factual error memory read failed",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": f"dream.factual_errors.read:{entry.name}",
                        },
                    )
                    continue
                if mf is None:
                    continue
                all_memories.append((mf, 0.0))

        factual_errors = self._check_factual_errors(all_memories)
        if not factual_errors:
            return ""

        lines = [
            "## Detected Factual Discrepancies (vs Detected Environment)",
            "",
            "The following memory files contain statements that conflict with "
            "the project environment detected at startup. Please correct these "
            "memories by updating the content via memory_write to use the "
            "correct detected values:",
            "",
        ]
        for fe in factual_errors:
            lines.append(f"- {fe}")
        return "\n".join(lines)

    def _build_dream_subagent_prompt(
        self, memory_summary: str, transcript_excerpts: str,
        factual_errors: str = "",
    ) -> str:
        """Build the sub-agent prompt for dream analysis (G3, G10)."""
        # Build factual errors section if provided (gap-17-04)
        factual_section = ""
        if factual_errors:
            factual_section = f"\n{factual_errors}\n"

        return f"""You are a memory consolidation agent running in a background "dream" cycle.
Your task is to analyze existing memories and recent session transcripts,
then produce a structured analysis and execute memory maintenance actions.

## Memory Files
{memory_summary}

## Session Transcripts
{transcript_excerpts}
{factual_section}

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

After ALL your analysis and memory operations, you MUST append a structured
summary block at the VERY END of your response, enclosed in a fenced JSON
code block. This block is machine-parsed to count your operations:

```json
{
  "created": <number of new memories created>,
  "updated": <number of existing memories updated>,
  "deleted": <number of memories deleted>
}
```

This JSON block must be the LAST thing in your response. Use actual counts,
not estimates. Even if you created 0 memories, include the block with 0 values.

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
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "dream.run_as_subagent",
                    },
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
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        log_path = log_dir / f"{today}.md"

        actions: list[str] = []
        analysis_sections: list[str] = []
        analysis_sections.append("## Dream Analysis")
        unprocessed_transcripts = self._unprocessed_transcript_files(session_store)

        if self.memory_store is not None:
            # ── 1. Gather all memories from both scopes ──
            all_memories: list[tuple] = []
            seen_names: set[str] = set()

            for scope in ("project", "user"):
                try:
                    entries = await self.memory_store.list_all(scope)
                except Exception:
                    logger.exception(
                        "Dream: failed to list %s memories",
                        scope,
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": f"dream.inline.list:{scope}",
                        },
                    )
                    continue

                for entry in entries:
                    if entry.name in seen_names:
                        continue
                    seen_names.add(entry.name)

                    try:
                        mf = await self.memory_store.read(entry.name)
                    except Exception:
                        logger.exception(
                            "Dream: failed to read memory '%s'",
                            entry.name,
                            extra={
                                "category": "error",
                                "component": "agent",
                                "context": f"dream.inline.read:{entry.name}",
                            },
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
                    logger.exception(
                        "Dream: failed to delete empty memory '%s'",
                        name,
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": f"dream.inline.delete_empty:{name}",
                        },
                    )

            all_memories = [(mf, mt) for mf, mt in all_memories if mf.name not in empty_names]

            # ── 3. Deduplicate by description (keep newest by mtime) ──
            by_desc: dict[str, list[tuple]] = defaultdict(list)
            for mf, mtime in all_memories:
                desc_key = mf.description.strip()
                if not desc_key:
                    continue
                by_desc[desc_key].append((mf, mtime))

            for items in by_desc.values():
                if len(items) < 2:
                    continue
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
                        logger.exception(
                            "Dream: failed to delete duplicate '%s'",
                            mf.name,
                            extra={
                                "category": "error",
                                "component": "agent",
                                "context": f"dream.inline.delete_duplicate:{mf.name}",
                            },
                        )

            # ── 4. Scan recent transcripts for patterns (gap-18) ──
            transcript_findings = await self._scan_transcripts(
                session_store,
                transcript_files=unprocessed_transcripts,
            )
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

            # ── 5b. Cross-reference memory facts against detected project
            #        conventions (gap-15-04: factual error detection in single files)
            #        AND auto-correct detected errors (gap-17-04) ──
            factual_errors = self._check_factual_errors(all_memories)
            if factual_errors:
                analysis_sections.append("")
                analysis_sections.append("### Factual Discrepancies (vs Detected Environment)")
                analysis_sections.append(
                    "The following memories contain statements that may conflict "
                    "with the project environment detected at startup:"
                )
                for fe in factual_errors:
                    analysis_sections.append(f"- {fe}")

                # Auto-correct detected factual errors (gap-17-04):
                # Update each affected memory file to replace outdated references
                # with the detected environment facts. Track corrections in result.
                corrected = await self._auto_correct_factual_errors(
                    factual_errors, all_memories, result, actions
                )
                if corrected > 0:
                    analysis_sections.append(
                        f"\nAutomatically corrected {corrected} memory file(s) "
                        f"to align with detected project environment."
                    )

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
                    logger.exception(
                        "Dream: failed to delete stale memory '%s'",
                        name,
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": f"dream.inline.delete_stale:{name}",
                        },
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
        total_memories = len(all_memories) if self.memory_store else "N/A"
        log_lines.append(f"- Total memories after dream: {total_memories}")
        log_lines.append("")

        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        result.log_path = log_path

        # ── 8. Update state ──
        self._write_completed_state(
            session_store=session_store,
            processed_files=unprocessed_transcripts,
        )

        return result

    async def _scan_transcripts(
        self,
        session_store,
        transcript_files: list[Path] | None = None,
    ) -> TranscriptFindings:
        """Scan unprocessed session transcripts for patterns (gap-18).

        Identifies repeated corrections, new conventions, and topic clusters
        by scanning transcript files from recent sessions.

        Returns structured TranscriptFindings for programmatic processing (G7).
        """
        findings = TranscriptFindings()
        if session_store is None:
            return findings

        try:
            recent_sessions = (
                transcript_files
                if transcript_files is not None
                else self._unprocessed_transcript_files(session_store)
            )

            if not recent_sessions:
                findings.text.append("No unprocessed sessions found.")
                return findings

            recent_sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            sampled = recent_sessions
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
                    logger.exception(
                        "Dream transcript scan failed for file",
                        extra={
                            "category": "error",
                            "component": "agent",
                            "context": f"dream.scan_transcript:{ts_file}",
                        },
                    )
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
            logger.exception(
                "Dream transcript scanning failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "dream.scan_transcripts",
                },
            )
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
                sentence_split_re = r'[.!?\n。！？\n]+'
                sentences1 = [
                    s.strip()
                    for s in re.split(sentence_split_re, mf1.content)
                    if len(s.strip()) > 10
                ]
                sentences2 = [
                    s.strip()
                    for s in re.split(sentence_split_re, mf2.content)
                    if len(s.strip()) > 10
                ]

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

    def _check_factual_errors(self, all_memories: list) -> list[str]:
        """Cross-reference individual memory content against detected project conventions.

        The inline dream path previously could only detect contradictions between
        pairs of memories. This method adds the ability to flag potentially
        incorrect facts within a single memory file by comparing its content
        against the project environment detected at startup (gap-15-04).

        Checks performed:
        - Python version: memory claims a version that conflicts with detected version
        - Package manager: memory references a different package manager
        - Linter: memory references a linter not detected in the project
        - Test framework: memory references a framework not detected

        Returns:
            List of human-readable discrepancy descriptions for the dream log.
            Does NOT modify memory files directly — the sub-agent path handles
            actual corrections via LLM reasoning.
        """
        if self._project_context is None:
            return []

        ctx = self._project_context
        errors: list[str] = []

        import re

        # Build a map of detected facts from the project context
        detected_facts: dict[str, str | None] = {
            "python_version": getattr(ctx, "python_version", None),
            "package_manager": getattr(ctx, "package_manager", None),
            "linter": getattr(ctx, "linter", None),
            "test_framework": getattr(ctx, "test_framework", None),
            "build_system": getattr(ctx, "build_system", None),
        }

        for mf, _ in all_memories:
            content_lower = mf.content.lower()

            # ── Python version check ──
            if detected_facts.get("python_version"):
                detected_ver = detected_facts["python_version"]
                # Look for patterns like "Python 3.8", "python 3.12", "3.8+"
                version_matches = re.findall(
                    r'(?:python\s*)?(\d+\.\d+)(?:\+)?',
                    content_lower,
                )
                for vm in version_matches:
                    # Only flag if a specific version is mentioned that differs
                    # from detected (ignore generic references like "3.x")
                    if vm != detected_ver and vm.count(".") == 1:
                        errors.append(
                            f"`{mf.name}` mentions Python {vm}, "
                            f"but detected Python version is {detected_ver}"
                        )
                        break  # One discrepancy per memory is enough

            # ── Package manager check ──
            if detected_facts.get("package_manager"):
                detected_pm = detected_facts["package_manager"]
                # Check if memory recommends a different package manager
                pm_alternatives = {
                    "uv": ["pip", "poetry", "pipenv"],
                    "pip": ["uv", "poetry", "pipenv"],
                    "poetry": ["uv", "pip", "pipenv"],
                    "npm": ["yarn", "pnpm"],
                    "yarn": ["npm", "pnpm"],
                    "pnpm": ["npm", "yarn"],
                }
                alternatives = pm_alternatives.get(detected_pm, [])
                for alt in alternatives:
                    # Match the alternative as a whole word (not part of another word)
                    alt_referenced = re.search(r'\b' + re.escape(alt) + r'\b', content_lower)
                    alt_is_preferred = (
                        f"use {alt}" in content_lower
                        or f"using {alt}" in content_lower
                        or f"with {alt}" in content_lower
                    )
                    if alt_referenced and alt_is_preferred:
                        errors.append(
                            f"`{mf.name}` references `{alt}` as package manager, "
                            f"but the project uses `{detected_pm}`"
                        )
                        break

            # ── Linter check ──
            if detected_facts.get("linter"):
                detected_linter = detected_facts["linter"]
                linter_alternatives = {
                    "ruff": ["flake8", "pylint", "black", "isort"],
                    "flake8": ["ruff", "pylint"],
                    "eslint": ["prettier", "tslint"],
                }
                alternatives = linter_alternatives.get(detected_linter, [])
                for alt in alternatives:
                    alt_referenced = re.search(r'\b' + re.escape(alt) + r'\b', content_lower)
                    alt_is_preferred = any(
                        phrase in content_lower
                        for phrase in [
                            f"use {alt}", f"using {alt}", f"run {alt}",
                            f"with {alt}", f"linter is {alt}",
                        ]
                    )
                    if alt_referenced and alt_is_preferred:
                        errors.append(
                            f"`{mf.name}` references `{alt}` as linter, "
                            f"but the project uses `{detected_linter}`"
                        )
                        break

            # ── Test framework check ──
            if detected_facts.get("test_framework"):
                detected_tf = detected_facts["test_framework"]
                tf_alternatives = {
                    "pytest": ["unittest", "nose", "nose2"],
                    "unittest": ["pytest", "nose"],
                    "jest": ["mocha", "ava", "jasmine"],
                }
                alternatives = tf_alternatives.get(detected_tf, [])
                for alt in alternatives:
                    alt_referenced = re.search(r'\b' + re.escape(alt) + r'\b', content_lower)
                    alt_is_preferred = any(
                        phrase in content_lower
                        for phrase in [
                            f"use {alt}", f"using {alt}", f"run {alt}",
                            f"with {alt}", f"tests? with {alt}",
                        ]
                    )
                    if alt_referenced and alt_is_preferred:
                        errors.append(
                            f"`{mf.name}` references `{alt}` as test framework, "
                            f"but the project uses `{detected_tf}`"
                        )
                        break

            # ── Build system check ──
            if detected_facts.get("build_system"):
                detected_bs = detected_facts["build_system"]
                bs_alternatives = {
                    "make": ["cmake", "bazel", "gradle"],
                    "pyproject": ["setuptools", "distutils"],
                    "npm": ["grunt", "gulp", "webpack"],
                }
                alternatives = bs_alternatives.get(detected_bs, [])
                for alt in alternatives:
                    alt_referenced = re.search(r'\b' + re.escape(alt) + r'\b', content_lower)
                    alt_is_preferred = any(
                        phrase in content_lower
                        for phrase in [
                            f"use {alt}", f"using {alt}", f"build with {alt}",
                        ]
                    )
                    if alt_referenced and alt_is_preferred:
                        errors.append(
                            f"`{mf.name}` references `{alt}` as build system, "
                            f"but the project uses `{detected_bs}`"
                        )
                        break

        return errors[:10]  # Limit to top 10

    async def _auto_correct_factual_errors(
        self, factual_errors: list[str], all_memories: list,
        result: DreamResult, actions: list[str],
    ) -> int:
        """Auto-correct detected factual errors in memory files (gap-17-04).

        For each factual discrepancy detected by _check_factual_errors, this
        method updates the affected memory file by replacing the outdated
        reference (e.g., "Python 3.8", "flake8", "pip") with the correct
        detected value (e.g., "Python 3.12", "ruff", "uv").

        The correction is conservative: it uses word-boundary regex replacement
        to avoid partial-word matches, and adds a correction note to the
        memory content documenting what was changed and why.

        Returns the number of successfully corrected memory files.
        """
        import re

        if not self.memory_store or not self._project_context:
            return 0

        ctx = self._project_context
        corrected_count = 0
        corrected_names: set[str] = set()

        # Build a mapping from error detection category to the correct value
        detected_facts = {
            "python_version": getattr(ctx, "python_version", None),
            "package_manager": getattr(ctx, "package_manager", None),
            "linter": getattr(ctx, "linter", None),
            "test_framework": getattr(ctx, "test_framework", None),
            "build_system": getattr(ctx, "build_system", None),
        }

        # Map of outdated → correct replacement pairs extracted from error strings.
        # We parse the error strings to determine what to replace.
        # Error formats (from _check_factual_errors):
        #   "`name` mentions Python X.Y, but detected Python version is Z"
        #   "`name` references `X` as package manager, but the project uses `Z`"
        #   "`name` references `X` as linter, but the project uses `Z`"
        #   "`name` references `X` as test framework, but the project uses `Z`"
        #   "`name` references `X` as build system, but the project uses `Z`"

        # Extract memory name from each error
        for fe_text in factual_errors:
            name_match = re.search(r'`([^`]+)`', fe_text)
            if not name_match:
                continue
            mem_name = name_match.group(1)
            if mem_name in corrected_names:
                continue

            # Determine the category and replacement pair from the error text
            category = None
            outdated_val = None
            correct_val = None

            # Python version: "mentions Python X.Y, but detected ... is Z"
            py_match = re.search(
                r'mentions Python (\d+\.\d+),.*?detected Python version is (\d+\.\d+)',
                fe_text,
            )
            if py_match:
                category = "python_version"
                outdated_val = py_match.group(1)
                correct_val = py_match.group(2)

            # Package manager / Linter / Test framework / Build system:
            # "references `X` as <category>, but the project uses `Z`"
            ref_match = re.search(
                (
                    r'references `([^`]+)` as '
                    r'(package manager|linter|test framework|build system), '
                    r'but the project uses `([^`]+)`'
                ),
                fe_text,
            )
            if ref_match:
                outdated_val = ref_match.group(1)
                cat_str = ref_match.group(2)
                correct_val = ref_match.group(3)
                cat_map = {
                    "package manager": "package_manager",
                    "linter": "linter",
                    "test framework": "test_framework",
                    "build system": "build_system",
                }
                category = cat_map.get(cat_str)

            if not category or not outdated_val or not correct_val:
                continue
            if not detected_facts.get(category):
                continue

            # Find the memory file in all_memories
            mf = None
            for mem, _ in all_memories:
                if mem.name == mem_name:
                    mf = mem
                    break
            if mf is None:
                continue

            # Update the memory file content
            try:
                original_content = mf.content

                # Use word-boundary regex to replace the outdated value.
                # We construct a pattern that matches the outdated term as a
                # whole word/version to avoid false matches.
                escaped_outdated = re.escape(outdated_val)
                pattern = re.compile(r'\b' + escaped_outdated + r'\b', re.IGNORECASE)

                new_content = pattern.sub(correct_val, original_content)
                if new_content == original_content:
                    # No replacement made — try without word boundary
                    # (the outdated value may be part of a compound word)
                    pattern_loose = re.compile(escaped_outdated, re.IGNORECASE)
                    new_content = pattern_loose.sub(correct_val, original_content)

                if new_content == original_content:
                    # Still no replacement — skip this memory
                    continue

                # Append a correction note
                correction_note = (
                    f"\n\n*[Dream auto-correction: Replaced `{outdated_val}` → "
                    f"`{correct_val}` ({category}) to align with detected "
                    f"project environment.]*"
                )
                updated_content = new_content + correction_note

                # Write the updated memory file via memory_store
                import yaml as _yaml
                fm_dict = {
                    "name": mf.name,
                    "description": mf.description,
                    "metadata": getattr(mf, "metadata", {}),
                }
                fm_yaml = _yaml.safe_dump(
                    fm_dict, default_flow_style=False, allow_unicode=True,
                ).strip()
                full_content = f"---\n{fm_yaml}\n---\n\n{updated_content}"
                file_path = str(self.memory_store.project_dir / f"{mf.name}.md")
                await self.memory_store.write(
                    file_path=file_path,
                    content=full_content,
                )

                result.memories_updated += 1
                corrected_count += 1
                corrected_names.add(mem_name)
                actions.append(
                    f"- Corrected factual error in `{mem_name}`: "
                    f"`{outdated_val}` → `{correct_val}` ({category})"
                )
                logger.info(
                    "Dream: auto-corrected factual error in '%s': %s→%s (%s)",
                    mem_name, outdated_val, correct_val, category,
                    extra={"category": "system"},
                )
            except Exception:
                logger.exception(
                    "Dream: failed to auto-correct factual error in '%s'",
                    mem_name,
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "dream.auto_correct_factual_error",
                    },
                )

        return corrected_count

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
                fm_yaml = _yaml.safe_dump(
                    fm_dict,
                    default_flow_style=False,
                    allow_unicode=True,
                ).strip()
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
                logger.exception(
                    "Dream: failed to update '%s' for contradiction resolution",
                    keeper.name,
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "dream.resolve_contradiction_update",
                    },
                )

            # Delete the older memory
            try:
                await self.memory_store.delete(older.name)
                actions.append(
                    f"- Deleted `{older.name}` "
                    f"(older duplicate from contradiction with `{keeper.name}`)"
                )
                result.memories_deleted += 1
            except Exception:
                logger.exception(
                    "Dream: failed to delete older memory '%s'",
                    older.name,
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "dream.resolve_contradiction_delete",
                    },
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
                markers_desc = (
                    ", ".join(findings.correction_markers[:5])
                    if findings.correction_markers
                    else "various"
                )
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
                    "**Dream finding:** "
                    f"{findings.text[0] if findings.text else 'corrections detected'}"
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
                actions.append(
                    f"- Created `common-corrections` memory "
                    f"({findings.correction_count} corrections)"
                )
                logger.info(
                    "Dream: created 'common-corrections' memory (%d corrections)",
                    findings.correction_count,
                    extra={"category": "system"},
                )
            except Exception:
                logger.exception(
                    "Dream: failed to create 'common-corrections' memory",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "dream.create_common_corrections_memory",
                    },
                )

        if findings.top_topics:
            try:
                topics_list = ", ".join(f"`{t[0]}`" for t in findings.top_topics)
                name = "frequent-topics"
                description = "Frequently discussed topics across recent sessions"
                dream_finding = (
                    findings.text[1]
                    if len(findings.text) > 1
                    else findings.text[0] if findings.text else "topics detected"
                )
                body = (
                    f"The most frequently discussed topics in {findings.sessions_scanned} "
                    f"recent sessions are: {topics_list}.\n\n"
                    f"This indicates areas of active focus in the project.\n\n"
                    "**Dream finding:** "
                    f"{dream_finding}"
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
                logger.exception(
                    "Dream: failed to create 'frequent-topics' memory",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "dream.create_frequent_topics_memory",
                    },
                )

    @staticmethod
    def _parse_dream_json_summary(text: str) -> dict | None:
        """Parse the structured JSON summary block from sub-agent output.

        The sub-agent is instructed to append a fenced JSON block at the very
        end of its response:
        ```json
        {"created": N, "updated": N, "deleted": N}
        ```

        Returns the parsed dict or None if no valid block is found.
        """
        import re as _re
        # Find the last fenced JSON block in the text
        # Pattern: ```json\n{...}\n```
        json_blocks = list(_re.finditer(
            r'```json\s*\n(.*?)\n\s*```', text, _re.DOTALL | _re.IGNORECASE,
        ))
        if not json_blocks:
            # Also try without json tag
            json_blocks = list(_re.finditer(
                r'```\s*\n(\{.*?"created".*?\})\s*\n\s*```', text, _re.DOTALL,
            ))
        if not json_blocks:
            return None

        # Try each block from last to first
        for match in reversed(json_blocks):
            try:
                data = json.loads(match.group(1).strip())
                if isinstance(data, dict) and "created" in data:
                    return {
                        "created": int(data.get("created", 0)),
                        "updated": int(data.get("updated", 0)),
                        "deleted": int(data.get("deleted", 0)),
                    }
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return None

    def _count_from_session_writes(self) -> dict | None:
        """Get operation counts from memory_store session write log.

        Introspects the memory store's session-level tracking of writes
        to determine actual memory operations performed by the sub-agent.
        Returns None if memory_store is unavailable.
        """
        if self.memory_store is None:
            return None
        try:
            session_writes = self.memory_store.get_session_writes()
            return {
                "created": len(session_writes.created),
                "updated": len(session_writes.updated),
                "deleted": len(session_writes.deleted),
            }
        except Exception:
            logger.exception(
                "Dream session write counting failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "dream.count_session_writes",
                },
            )
            return None

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}
