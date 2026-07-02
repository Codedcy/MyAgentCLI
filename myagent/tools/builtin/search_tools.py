"""Built-in search tool: grep (ripgrep with pure Python fallback).

Uses ripgrep when available for performance; transparently falls back
to pure Python (re + pathlib) so the tool works on every platform
without external binary dependencies.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import shutil
from pathlib import Path

from myagent.tools.base import ToolContext, ToolResult


class GrepTool:
    name = "grep"
    description = (
        "Content search using ripgrep when available, with pure Python fallback. "
        "Supports full regex syntax, file type filtering, and context lines."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in (defaults to project dir)",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode (default: files_with_matches)",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files",
            },
            "-A": {"type": "integer", "description": "Lines after match"},
            "-B": {"type": "integer", "description": "Lines before match"},
            "-C": {"type": "integer", "description": "Context lines (before + after)"},
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search",
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N lines/entries",
            },
        },
        "required": ["pattern"],
    }

    # Common text file extensions to avoid searching binaries
    _TEXT_EXTENSIONS: set[str] = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
        ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".scala", ".clj",
        ".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".json", ".xml",
        ".html", ".css", ".scss", ".less", ".svg",
        ".sh", ".bash", ".zsh", ".fish", ".ps1",
        ".cfg", ".ini", ".conf", ".env", ".envrc",
        ".sql", ".r", ".m", ".mm",
        ".vue", ".svelte", ".astro",
        ".csv", ".tsv", ".log",
        ".tf", ".hcl",
        ".gradle", ".properties",
        ".dockerfile", ".makefile", ".cmake",
        ".gitignore", ".gitattributes",
        ".proto",
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        pattern = params["pattern"]
        search_path = Path(params.get("path", str(context.project_dir)))

        # Prefer ripgrep when available (10–100x faster on large repos)
        rg_path = shutil.which("rg")
        if rg_path:
            return await self._search_with_rg(rg_path, params, search_path, context)

        # Pure Python fallback — works everywhere, no dependencies
        return await self._search_with_python(params, search_path)

    # ── ripgrep fast path ────────────────────────────────────────

    async def _search_with_rg(
        self, rg_path: str, params: dict, search_path: Path, context: ToolContext
    ) -> ToolResult:
        pattern = params["pattern"]
        cmd = [rg_path, "--no-heading", "--with-filename", "--line-number", "--color=never"]

        output_mode = params.get("output_mode", "files_with_matches")
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")

        if "head_limit" in params and params["head_limit"]:
            cmd.extend(["-m", str(params["head_limit"])])

        for flag in ("-A", "-B", "-C"):
            if flag in params:
                cmd.extend([flag, str(params[flag])])

        if params.get("-i"):
            cmd.append("-i")
        if "glob" in params:
            cmd.extend(["--glob", params["glob"]])

        cmd.append(pattern)
        cmd.append(str(search_path))

        timeout = 120
        if context.config:
            timeout = context.config.tools.shell_timeout_seconds

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 1:
                return ToolResult(output="(no matches)", metadata={"exit_code": 1, "engine": "rg"})
            elif proc.returncode != 0:
                return ToolResult(
                    error=stderr.decode().strip(),
                    metadata={"exit_code": proc.returncode, "engine": "rg"},
                )
            output = stdout.decode("utf-8", errors="replace")
            return ToolResult(
                output=output.strip() or "(no matches)",
                metadata={"exit_code": 0, "engine": "rg"},
            )
        except asyncio.TimeoutError:
            return ToolResult(error=f"grep timed out after {timeout}s")
        except Exception as e:
            return ToolResult(error=str(e))

    # ── pure Python fallback ─────────────────────────────────────

    async def _search_with_python(self, params: dict, search_path: Path) -> ToolResult:
        """Pure Python regex search — no external dependencies."""
        pattern = params["pattern"]
        output_mode = params.get("output_mode", "files_with_matches")
        file_glob = params.get("glob")
        ignore_case = params.get("-i", False)
        context_before = params.get("-B") or params.get("-C") or 0
        context_after = params.get("-A") or params.get("-C") or 0
        head_limit = params.get("head_limit")

        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(error=f"Invalid regex pattern: {e}")

        # Collect matching files
        files = self._collect_files(search_path, file_glob)
        if not files:
            return ToolResult(output="(no matches)", metadata={"engine": "python"})

        results: dict[str, list[str] | list[tuple[int, str]] | int] = {}
        total_lines = 0

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue

            lines = content.split("\n")
            file_matches: list[tuple[int, str]] = []

            for line_no, line in enumerate(lines, start=1):
                if regex.search(line):
                    file_matches.append((line_no, line))

            if not file_matches:
                continue

            if output_mode == "count":
                results[str(file_path)] = len(file_matches)
                total_lines += 1
            elif output_mode == "files_with_matches":
                results[str(file_path)] = []
                total_lines += 1
            else:  # content
                formatted = self._format_content_matches(
                    file_path, file_matches, lines, context_before, context_after
                )
                results[str(file_path)] = formatted
                total_lines += len(formatted)

            if head_limit and total_lines >= head_limit:
                break

        if not results:
            return ToolResult(output="(no matches)", metadata={"engine": "python"})

        output = self._format_results(results, output_mode)
        if head_limit:
            output = "\n".join(output.split("\n")[:head_limit])

        return ToolResult(
            output=output,
            metadata={
                "engine": "python",
                "files_matched": len(results),
                "total_matches": sum(
                    len(v) if isinstance(v, list) else v for v in results.values()
                ),
            },
        )

    def _collect_files(self, search_path: Path, file_glob: str | None) -> list[Path]:
        """Collect files to search, filtering by glob and binary heuristics."""
        if search_path.is_file():
            return [search_path]

        files: list[Path] = []
        for f in search_path.rglob("*"):
            if not f.is_file():
                continue
            if f.name.startswith(".") and ".git" in str(f):
                # Skip git internals
                if any(part.startswith(".git") for part in f.parts):
                    continue
            # Skip common non-text locations
            parts_lower = {p.lower() for p in f.parts}
            if parts_lower & {"__pycache__", "node_modules", ".git", "dist", "build", ".venv", "venv"}:
                continue
            # Apply file glob filter
            if file_glob and not fnmatch.fnmatch(f.name, file_glob):
                continue
            # Heuristic: skip likely binary files
            if f.suffix and f.suffix.lower() not in self._TEXT_EXTENSIONS:
                # Check if it looks like a text file by reading a sample
                try:
                    sample = f.read_bytes()[:1024]
                    if b"\x00" in sample:
                        continue
                except (PermissionError, OSError):
                    continue
            files.append(f)

        return sorted(files)

    def _format_content_matches(
        self,
        file_path: Path,
        matches: list[tuple[int, str]],
        all_lines: list[str],
        before: int,
        after: int,
    ) -> list[str]:
        """Format content output with line numbers and context."""
        output: list[str] = []
        shown_ranges: set[int] = set()

        for line_no, line in matches:
            start = max(0, line_no - before - 1)
            end = min(len(all_lines), line_no + after)
            for ctx_line in range(start, end):
                if ctx_line in shown_ranges:
                    continue
                shown_ranges.add(ctx_line)
                marker = ":" if ctx_line == line_no - 1 else "-"
                output.append(
                    f"{file_path}:{ctx_line + 1}{marker}{all_lines[ctx_line]}"
                )

        return output

    def _format_results(
        self,
        results: dict[str, list[str] | list[tuple[int, str]] | int],
        output_mode: str,
    ) -> str:
        """Format results into output string."""
        lines: list[str] = []
        for path, data in results.items():
            if output_mode == "count":
                lines.append(f"{path}:{data}")
            elif output_mode == "files_with_matches":
                lines.append(path)
            else:
                if isinstance(data, list):
                    lines.extend(str(item) if isinstance(item, str) else f"{path}:{item[0]}:{item[1]}" for item in data)
        return "\n".join(lines)
