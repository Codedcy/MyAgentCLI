"""Memory store — file CRUD with frontmatter Markdown + MEMORY.md index."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("myagent.memory.store")


@dataclass
class MemoryFile:
    name: str
    description: str
    metadata: dict
    content: str
    path: Path


@dataclass
class MemoryEntry:
    name: str
    description: str
    type: str  # user | feedback | project | reference
    file: str


@dataclass
class SessionMemoryLog:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Pattern for [[wiki-style links]] in markdown body
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


class MemoryStore:
    """Manages memory files with frontmatter + MEMORY.md index."""

    def __init__(self, project_memory_dir: Path, user_memory_dir: Path):
        self.project_dir = Path(project_memory_dir)
        self.user_dir = Path(user_memory_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self._session_log = SessionMemoryLog()

    def get_session_writes(self) -> SessionMemoryLog:
        return self._session_log

    def reset_session_log(self) -> None:
        self._session_log = SessionMemoryLog()

    async def write(self, file_path: str, content: str) -> MemoryFile:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        fm = self._parse_frontmatter(content)
        fm_name = fm.get("name", path.stem)

        # Dedup check: look for existing files with the same frontmatter name
        # (from a different path). If found, treat as update (not duplicate).
        existed = path.exists()
        if not existed:
            existing = await self._find_by_name(fm_name)
            if existing and existing != path:
                existed = True

        # G9: Semantic dedup — if no name match, check for content similarity
        if not existed:
            body = self._body(content)
            description = fm.get("description", "")
            similar = await self._find_by_similarity(description, body)
            if similar is not None:
                # Found a semantically similar memory — update it instead
                path = similar
                existed = True

        path.write_text(content, encoding="utf-8")

        # Extract [[wiki-style links]] from the body
        body = self._body(content)
        links = LINK_RE.findall(body)

        mf = MemoryFile(
            name=fm_name,
            description=fm.get("description", ""),
            metadata=fm.get("metadata", {}),
            content=body,
            path=path,
        )

        # Store extracted links in metadata for cross-reference
        if links:
            mf.metadata["links"] = links

        if existed:
            self._session_log.updated.append(mf.name)
        else:
            self._session_log.created.append(mf.name)

        await self._update_index(path.parent)
        return mf

    async def read(self, name: str) -> MemoryFile | None:
        found = await self._find_by_name(name)
        if found is None:
            return None
        f = found
        content = f.read_text(encoding="utf-8")
        fm = self._parse_frontmatter(content)
        body = self._body(content)
        metadata = fm.get("metadata", {})
        # Extract [[wiki-style links]] from the body for cross-reference resolution
        links = LINK_RE.findall(body)
        if links:
            metadata["links"] = links
        return MemoryFile(
            name=fm.get("name", f.stem),
            description=fm.get("description", ""),
            metadata=metadata,
            content=body,
            path=f,
        )

    async def delete(self, name: str) -> None:
        for d in (self.project_dir, self.user_dir):
            for f in d.glob("*.md"):
                if f.name == "MEMORY.md":
                    continue
                content = f.read_text(encoding="utf-8")
                fm = self._parse_frontmatter(content)
                fm_name = fm.get("name", "")
                if f.stem == name or fm_name == name:
                    f.unlink()
                    self._session_log.deleted.append(name)
                    await self._update_index(d)
                    return

    async def list_all(self, scope: str = "project") -> list[MemoryEntry]:
        d = self.project_dir if scope == "project" else self.user_dir
        return self._read_index(d)

    async def update_index(self) -> None:
        await self._update_index(self.project_dir)
        await self._update_index(self.user_dir)

    def _parse_frontmatter(self, content: str) -> dict:
        m = FRONTMATTER_RE.match(content)
        if m:
            try:
                return yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                return {}
        return {}

    def _body(self, content: str) -> str:
        m = FRONTMATTER_RE.match(content)
        return content[m.end():] if m else content

    async def _find_by_name(self, name: str) -> Path | None:
        """Find a memory file by frontmatter name or stem across both dirs."""
        for d in (self.project_dir, self.user_dir):
            for f in d.glob("*.md"):
                if f.name == "MEMORY.md":
                    continue
                content = f.read_text(encoding="utf-8")
                fm = self._parse_frontmatter(content)
                fm_name = fm.get("name", "")
                if f.stem == name or fm_name == name:
                    return f
        return None

    async def _find_by_similarity(
        self, description: str, body: str
    ) -> Path | None:
        """Find a semantically similar memory file (G9).

        Uses embedding-based recall from myagent.memory.recall to find
        existing memories with similar content. If the top result's
        description or content has high overlap with the new memory,
        return the Path of the existing file so it can be updated.

        Falls back to keyword overlap if the embedding model is unavailable.

        Args:
            description: The new memory's description (frontmatter).
            body: The new memory's body content.

        Returns:
            Path to an existing similar memory, or None.
        """
        query = f"{description} {body[:200]}".strip()
        if not query:
            return None

        # Try embedding-based similarity via recall module
        try:
            from myagent.memory.recall import recall
            results = await recall(query, self, limit=3)
            if results:
                for result in results:
                    if result.name:
                        existing_path = await self._find_by_name(result.name)
                        if existing_path:
                            # Check keyword overlap as a confidence signal
                            existing_mf = await self.read(result.name)
                            if existing_mf:
                                overlap_ratio = self._compute_overlap_ratio(
                                    body[:500], existing_mf.content[:500]
                                )
                                # Threshold: >60% keyword overlap = likely duplicate
                                if overlap_ratio > 0.60:
                                    return existing_path
        except Exception:
            logger.exception(
                "Semantic memory dedup failed; falling back to keyword overlap",
                extra={
                    "category": "error",
                    "component": "memory",
                    "context": "memory_semantic_dedup",
                },
            )

        # Fallback: keyword overlap with all existing memories
        return await self._find_by_keyword_overlap(body)

    async def _find_by_keyword_overlap(self, body: str) -> Path | None:
        """Fallback dedup: check keyword overlap against all memory files (G9).

        Extracts significant words from the new body and compares against
        each existing memory's content. Returns the first match with >70%
        overlap ratio.
        """
        import re

        def extract_keywords(text: str) -> set[str]:
            words = set()
            for w in re.split(r'[\s,;:.!?()\[\]{}"\'`\n]+', text.lower()):
                w = w.strip()
                if len(w) >= 3 and w not in {
                    'the', 'and', 'for', 'you', 'can', 'that', 'this',
                    'with', 'have', 'from', 'are', 'not', 'but', 'all',
                    'was', 'has', 'had', 'its', 'his', 'her', 'our',
                    'will', 'would', 'could', 'should', 'been', 'being',
                }:
                    words.add(w)
            return words

        new_keywords = extract_keywords(body[:500])
        if len(new_keywords) < 5:
            return None

        best_path = None
        best_ratio = 0.0

        for d in (self.project_dir, self.user_dir):
            for f in d.glob("*.md"):
                if f.name == "MEMORY.md":
                    continue
                try:
                    existing_content = f.read_text(encoding="utf-8")
                    existing_body = self._body(existing_content)
                    existing_keywords = extract_keywords(existing_body[:500])
                    if not existing_keywords:
                        continue
                    overlap = len(new_keywords & existing_keywords)
                    ratio = overlap / max(len(new_keywords), 1)
                    if ratio > 0.70 and ratio > best_ratio:
                        best_ratio = ratio
                        best_path = f
                except Exception:
                    logger.exception(
                        "Failed to inspect memory file for keyword overlap",
                        extra={
                            "category": "error",
                            "component": "memory",
                            "context": "memory_keyword_overlap_read",
                        },
                    )
                    continue

        return best_path

    @staticmethod
    def _compute_overlap_ratio(text1: str, text2: str) -> float:
        """Compute keyword overlap ratio between two texts (G9)."""
        import re

        def extract_keywords(text: str) -> set[str]:
            words = set()
            for w in re.split(r'[\s,;:.!?()\[\]{}"\'`\n]+', text.lower()):
                w = w.strip()
                if len(w) >= 3:
                    words.add(w)
            return words

        kw1 = extract_keywords(text1)
        kw2 = extract_keywords(text2)
        if not kw1 or not kw2:
            return 0.0
        overlap = len(kw1 & kw2)
        return overlap / max(len(kw1), 1)

    async def _update_index(self, mem_dir: Path) -> None:
        """Rebuild MEMORY.md index with structured metadata table (gap-8-09).

        Includes memory type, last-updated date, and description in a markdown
        table format as implied by the spec's frontmatter examples (§六).
        """
        index_path = mem_dir / "MEMORY.md"
        rows: list[dict] = []
        for f in sorted(mem_dir.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            content = f.read_text(encoding="utf-8")
            fm = self._parse_frontmatter(content)
            name = fm.get("name", f.stem)
            desc = fm.get("description", "")
            metadata = fm.get("metadata", {}) if isinstance(fm.get("metadata"), dict) else {}
            mtype = metadata.get("type", "reference")
            updated = metadata.get("updated", "")
            # Also check file modification time as fallback
            if not updated:
                from datetime import datetime
                mtime = f.stat().st_mtime
                updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            rows.append({
                "name": name,
                "file": f.name,
                "type": mtype,
                "updated": updated,
                "description": desc,
            })

        # Build markdown table
        lines = ["# Memory Index", ""]
        if rows:
            lines.append("| Name | Type | Updated | Description |")
            lines.append("|------|------|---------|-------------|")
            for r in rows:
                name_link = f"[{r['name']}]({r['file']})"
                lines.append(
                    f"| {name_link} | {r['type']} | {r['updated']} | {r['description']} |"
                )
            lines.append("")
        else:
            lines.append("No memories indexed yet.")
        index_path.write_text("\n".join(lines), encoding="utf-8")

    def _read_index(self, mem_dir: Path) -> list[MemoryEntry]:
        """Read MEMORY.md index.

        Supports both the new table format and legacy list format (gap-8-09).
        """
        index_path = mem_dir / "MEMORY.md"
        if not index_path.exists():
            return []
        entries: list[MemoryEntry] = []
        text = index_path.read_text(encoding="utf-8")

        # Try new table format first: | [Name](file) | type | updated | description |
        for line in text.split("\n"):
            # Match table row with link
            table_match = re.match(
                r"\|\s*\[(.+?)\]\((.+?)\)\s*\|\s*(\w+)\s*\|\s*([^|]*)\s*\|\s*(.+?)\s*\|",
                line,
            )
            if table_match:
                entries.append(MemoryEntry(
                    name=table_match.group(1),
                    file=table_match.group(2),
                    type=table_match.group(3).strip(),
                    description=table_match.group(5).strip(),
                ))
                continue

            # Fallback: legacy list format "- [name](file) — description"
            m = re.match(r"- \[(.+?)\]\((.+?)\) — (.+)", line)
            if m:
                entries.append(MemoryEntry(
                    name=m.group(1), file=m.group(2),
                    description=m.group(3), type="reference",
                ))

        return entries
