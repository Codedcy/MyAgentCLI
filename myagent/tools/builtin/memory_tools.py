"""Built-in memory tool: memory_write."""

from __future__ import annotations

from myagent.tools.base import ToolContext, ToolResult


class MemoryWriteTool:
    name = "memory_write"
    description = "Write or update a memory file. One fact per file."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the memory file to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write (frontmatter + markdown body)",
            },
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        from pathlib import Path

        path = Path(params["file_path"])
        content = params["content"]

        # Validate basic frontmatter presence
        if not content.strip().startswith("---"):
            return ToolResult(
                error="Memory content must start with YAML frontmatter (---)"
            )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

            # Update MEMORY.md index in the same directory
            # Look for a MEMORY.md index file in the parent directory
            index_path = path.parent / "MEMORY.md"
            self._update_index(index_path, path, content)

            return ToolResult(
                output=f"Memory written: {path.name}",
                metadata={
                    "file_path": str(path),
                    "size_bytes": len(content),
                    "index_updated": index_path.exists(),
                },
            )
        except Exception as e:
            return ToolResult(error=str(e))

    @staticmethod
    def _update_index(index_path, memory_file, content):
        """Update the MEMORY.md index with a reference to the new memory file."""
        import re
        import yaml

        FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

        fm_name = memory_file.stem
        fm_desc = ""
        if content.strip().startswith("---"):
            m = FRONTMATTER_RE.match(content)
            if m:
                try:
                    fm = yaml.safe_load(m.group(1)) or {}
                    fm_name = fm.get("name", memory_file.stem)
                    fm_desc = fm.get("description", "")
                except yaml.YAMLError:
                    pass

        # Read existing index or create new
        existing_lines = []
        if index_path.exists():
            existing_lines = index_path.read_text(encoding="utf-8").split("\n")

        # Build the new entry
        new_entry = f"- [{fm_name}]({memory_file.name}) — {fm_desc}"

        # Check if entry for this file already exists, update it
        updated = False
        new_lines = []
        file_pattern = re.compile(rf"- \[.+?\]\({re.escape(memory_file.name)}\) — .*")
        for line in existing_lines:
            if file_pattern.match(line):
                new_lines.append(new_entry)
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            # Insert after the header line
            header_idx = next(
                (i for i, line in enumerate(new_lines) if line.startswith("# Memory Index")),
                -1,
            )
            if header_idx >= 0:
                new_lines.insert(header_idx + 2, new_entry)
            else:
                new_lines.append(new_entry)

        index_path.write_text("\n".join(new_lines), encoding="utf-8")
