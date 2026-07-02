"""Memory store — file CRUD with frontmatter Markdown + MEMORY.md index."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
        existed = path.exists()
        path.write_text(content, encoding="utf-8")

        fm = self._parse_frontmatter(content)
        mf = MemoryFile(
            name=fm.get("name", path.stem),
            description=fm.get("description", ""),
            metadata=fm.get("metadata", {}),
            content=self._body(content),
            path=path,
        )

        if existed:
            self._session_log.updated.append(mf.name)
        else:
            self._session_log.created.append(mf.name)

        await self._update_index(path.parent)
        return mf

    async def read(self, name: str) -> MemoryFile | None:
        for d in (self.project_dir, self.user_dir):
            for f in d.glob("*.md"):
                content = f.read_text(encoding="utf-8")
                fm = self._parse_frontmatter(content)
                fm_name = fm.get("name", "")
                if f.stem == name or fm_name == name:
                    return MemoryFile(
                        name=fm.get("name", f.stem),
                        description=fm.get("description", ""),
                        metadata=fm.get("metadata", {}),
                        content=self._body(content),
                        path=f,
                    )
        return None

    async def delete(self, name: str) -> None:
        for d in (self.project_dir, self.user_dir):
            for f in d.glob("*.md"):
                if f.stem == name:
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

    async def _update_index(self, mem_dir: Path) -> None:
        index_path = mem_dir / "MEMORY.md"
        entries = []
        for f in sorted(mem_dir.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            content = f.read_text(encoding="utf-8")
            fm = self._parse_frontmatter(content)
            name = fm.get("name", f.stem)
            desc = fm.get("description", "")
            mtype = fm.get("metadata", {}).get("type", "reference")
            entries.append(f"- [{name}]({f.name}) — {desc}")
        index_path.write_text("# Memory Index\n\n" + "\n".join(entries) + "\n", encoding="utf-8")

    def _read_index(self, mem_dir: Path) -> list[MemoryEntry]:
        index_path = mem_dir / "MEMORY.md"
        if not index_path.exists():
            return []
        entries = []
        for line in index_path.read_text(encoding="utf-8").split("\n"):
            m = re.match(r"- \[(.+?)\]\((.+?)\) — (.+)", line)
            if m:
                entries.append(MemoryEntry(name=m.group(1), file=m.group(2), description=m.group(3), type="reference"))
        return entries
