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
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in output (default: true)",
                "default": True,
            },
            "-o": {
                "type": "boolean",
                "description": "Print only the matched (non-empty) parts of each matching line, "
                "one match per output line (rg -o / --only-matching). Requires output_mode: "
                "\"content\", ignored otherwise. Defaults to false.",
                "default": False,
            },
            "type": {
                "type": "string",
                "description": "File type to search (rg --type). Common types: py, js, rust, go, "
                "java, etc. More efficient than include for standard file types.",
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N lines/entries before applying head_limit, "
                "equivalent to \"| tail -n +N | head -N\". Works across all output modes. "
                "Defaults to 0.",
                "default": 0,
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode where . matches newlines and patterns "
                "can span lines (rg -U --multiline-dotall). Default: false.",
                "default": False,
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

    # Mapping from ripgrep --type names to file extensions (for Python fallback)
    _TYPE_EXTENSIONS: dict[str, set[str]] = {
        "py": {".py"},
        "js": {".js", ".jsx", ".mjs", ".cjs"},
        "ts": {".ts", ".tsx"},
        "rust": {".rs"},
        "go": {".go"},
        "java": {".java"},
        "c": {".c", ".h"},
        "cpp": {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"},
        "rb": {".rb"},
        "php": {".php"},
        "swift": {".swift"},
        "kt": {".kt", ".kts"},
        "scala": {".scala"},
        "md": {".md"},
        "txt": {".txt"},
        "yaml": {".yaml", ".yml"},
        "toml": {".toml"},
        "json": {".json"},
        "xml": {".xml"},
        "html": {".html", ".htm"},
        "css": {".css", ".scss", ".less"},
        "sh": {".sh", ".bash", ".zsh", ".fish"},
        "sql": {".sql"},
        "vue": {".vue"},
        "svelte": {".svelte"},
        "tf": {".tf", ".tfvars"},
        "gradle": {".gradle"},
        "proto": {".proto"},
        "csv": {".csv"},
        "log": {".log"},
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
        cmd = [rg_path, "--no-heading", "--with-filename", "--color=never"]

        # Line numbers (default true)
        if params.get("-n", True):
            cmd.append("--line-number")

        output_mode = params.get("output_mode", "files_with_matches")
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")

        # NOTE: We intentionally do NOT use rg's -m flag for head_limit
        # because -m limits matches per-file, not globally. Instead, we
        # post-process the combined output to apply a global head_limit.
        for flag in ("-A", "-B", "-C"):
            if flag in params:
                cmd.extend([flag, str(params[flag])])

        if params.get("-i"):
            cmd.append("-i")
        if "glob" in params:
            cmd.extend(["--glob", params["glob"]])
        if params.get("-o"):
            cmd.append("--only-matching")
        if "type" in params:
            cmd.extend(["--type", params["type"]])
        if params.get("multiline"):
            cmd.extend(["--multiline", "--multiline-dotall"])

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
            output = stdout.decode("utf-8", errors="replace").strip() or "(no matches)"

            # Apply offset and head_limit as global post-processing
            # (rg's -m flag is per-file, not global, so we handle the limit here)
            lines = output.split("\n")
            offset = params.get("offset", 0)
            head_limit = params.get("head_limit")

            if offset and offset > 0:
                lines = lines[offset:]

            if head_limit and len(lines) > head_limit:
                lines = lines[:head_limit]

            output = "\n".join(lines) or "(no matches)"

            return ToolResult(
                output=output,
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
        show_line_numbers = params.get("-n", True)
        only_matching = params.get("-o", False)
        file_type = params.get("type")
        multiline = params.get("multiline", False)
        offset = params.get("offset", 0)

        # Build regex flags
        try:
            flags = re.IGNORECASE if ignore_case else 0
            if multiline:
                flags |= re.MULTILINE | re.DOTALL
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(error=f"Invalid regex pattern: {e}")

        # Collect matching files
        files = self._collect_files(search_path, file_glob, file_type)
        if not files:
            return ToolResult(output="(no matches)", metadata={"engine": "python"})

        results: dict[str, list[str] | list[tuple[int, str]] | int] = {}
        total_lines = 0

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue

            if multiline:
                # Multiline: search full content and map spans to line numbers
                file_matches: list[tuple[int, str]] = []
                for m in regex.finditer(content):
                    line_no = content[: m.start()].count("\n") + 1
                    match_text = m.group() if only_matching else m.group()
                    file_matches.append((line_no, match_text))
            else:
                lines = content.split("\n")
                file_matches: list[tuple[int, str]] = []
                for line_no, line in enumerate(lines, start=1):
                    if only_matching:
                        for m in regex.finditer(line):
                            if m.group():  # skip empty matches
                                file_matches.append((line_no, m.group()))
                    else:
                        if regex.search(line):
                            file_matches.append((line_no, line))

            if not file_matches:
                continue

            # Apply offset
            if offset > 0:
                file_matches = file_matches[offset:]
            if not file_matches:
                continue

            if output_mode == "count":
                results[str(file_path)] = len(file_matches)
                total_lines += 1
            elif output_mode == "files_with_matches":
                results[str(file_path)] = []
                total_lines += 1
            else:  # content
                all_lines = content.split("\n")
                if only_matching and not context_before and not context_after:
                    # Only matching without context: just list match texts
                    formatted = [
                        f"{file_path}:{line_no}:{text}" if show_line_numbers else text
                        for line_no, text in file_matches
                    ]
                else:
                    formatted = self._format_content_matches(
                        file_path, file_matches, all_lines, context_before, context_after,
                        show_line_numbers=show_line_numbers,
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

    def _collect_files(
        self, search_path: Path, file_glob: str | None, file_type: str | None = None
    ) -> list[Path]:
        """Collect files to search, filtering by glob, type, and binary heuristics."""
        if search_path.is_file():
            return [search_path]

        # Resolve file_type to a set of allowed extensions
        type_extensions: set[str] | None = None
        if file_type:
            type_extensions = self._TYPE_EXTENSIONS.get(file_type)
            if type_extensions is None:
                # Unknown type: treat as an extension (e.g. "py" → ".py")
                type_extensions = {f".{file_type}"}

        files: list[Path] = []
        for f in search_path.rglob("*"):
            if not f.is_file():
                continue
            # Skip git internals
            if any(part.startswith(".git") for part in f.parts):
                continue
            # Skip common non-text locations
            parts_lower = {p.lower() for p in f.parts}
            if parts_lower & {"__pycache__", "node_modules", ".git", "dist", "build", ".venv", "venv"}:
                continue
            # Apply file type filter
            if type_extensions is not None:
                if f.suffix.lower() not in type_extensions:
                    # Also match files without extension by basename (e.g. "Makefile")
                    if f.name not in type_extensions:
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
        show_line_numbers: bool = True,
    ) -> list[str]:
        """Format content output with line numbers and context."""
        output: list[str] = []
        shown_ranges: set[int] = set()

        for line_no, _line in matches:
            start = max(0, line_no - before - 1)
            end = min(len(all_lines), line_no + after)
            for ctx_line in range(start, end):
                if ctx_line in shown_ranges:
                    continue
                shown_ranges.add(ctx_line)
                if show_line_numbers:
                    marker = ":" if ctx_line == line_no - 1 else "-"
                    output.append(
                        f"{file_path}:{ctx_line + 1}{marker}{all_lines[ctx_line]}"
                    )
                else:
                    output.append(all_lines[ctx_line])

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
                    lines.extend(
                        str(item) if isinstance(item, str)
                        else f"{path}:{item[0]}:{item[1]}"
                        for item in data
                    )
        return "\n".join(lines)
