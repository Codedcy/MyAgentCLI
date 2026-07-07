"""Project environment detection.

Detects project type, package manager, build system, git status,
and directory structure at startup.

Design doc reference: §十 — 会话系统与项目感知
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from myagent.cli.text_decode import decode_tool_output

logger = logging.getLogger("myagent.agent.project")


@dataclass
class ProjectContext:
    """Detected project metadata."""

    # Git
    is_git_repo: bool = False
    git_branch: str | None = None
    git_status: str | None = None  # e.g. "2 files modified"

    # Project type
    project_type: str = "unknown"  # "python" | "node" | "go" | "rust" | "unknown"
    package_manager: str | None = None  # "uv" | "pip" | "poetry" | "npm" | "pnpm" | "yarn"
    python_version: str | None = None
    build_system: str | None = None  # "make" | "pyproject" | ...
    test_framework: str | None = None  # "pytest" | "unittest" | ...
    linter: str | None = None  # "ruff" | "flake8" | ...

    # Directory structure
    structure_summary: str = ""

    # AGENT.md / CLAUDE.md content (for L3 context injection)
    agent_md_content: str | None = None

    # Project hash
    project_hash: str = ""


class ProjectDetector:
    """Detect project environment from directory structure and files."""

    # File-based type detection: file_name → project_type
    _TYPE_MARKERS = {
        "pyproject.toml": "python",
        "setup.py": "python",
        "setup.cfg": "python",
        "package.json": "node",
        "go.mod": "go",
        "Cargo.toml": "rust",
    }

    # Package manager detection: lock_file → manager
    _PACKAGE_MANAGER_MARKERS = {
        "uv.lock": "uv",
        "poetry.lock": "poetry",
        "Pipfile.lock": "pip",
        "package-lock.json": "npm",
        "pnpm-lock.yaml": "pnpm",
        "yarn.lock": "yarn",
    }

    # Test framework markers
    _TEST_FRAMEWORK_MARKERS = {
        "pytest": "pytest",
        ".pytest_cache": "pytest",
    }

    # Linter markers
    _LINTER_MARKERS = {
        "ruff.toml": "ruff",
        ".flake8": "flake8",
        ".eslintrc.js": "eslint",
        ".eslintrc.json": "eslint",
        ".eslintrc.cjs": "eslint",
    }

    # Agent MD file candidates (searched in priority order)
    _AGENT_MD_CANDIDATES = [
        ".myagent/AGENT.md",
        "AGENT.md",
        "CLAUDE.md",
    ]

    @staticmethod
    def _find_git_root(start_dir: Path) -> Path | None:
        """Walk up directory tree from start_dir looking for .git.

        Stops after 10 levels or at the filesystem root.
        Returns the directory containing .git, or None if not found.
        """
        current = start_dir.resolve()
        for _ in range(10):
            if (current / ".git").is_dir():
                return current
            parent = current.parent
            if parent == current:  # filesystem root
                break
            current = parent
        return None

    async def detect(self, project_dir: Path) -> ProjectContext:
        """Detect project environment.

        All detection steps are non-fatal — a missing tool or file
        results in the corresponding field being set to its default
        (False/None/"unknown"), never an exception.
        """
        # Auto-detect git root: walk up from project_dir to find .git
        git_root = self._find_git_root(project_dir)
        if git_root is not None:
            project_dir = git_root

        ctx = ProjectContext()
        ctx.project_hash = hashlib.sha256(
            str(project_dir.resolve()).encode()
        ).hexdigest()[:7]

        # Git detection
        await self._detect_git(project_dir, ctx)

        # Project type (by file markers)
        ctx.project_type = self._detect_project_type(project_dir)

        # Package manager (by lock files)
        ctx.package_manager = self._detect_package_manager(project_dir)

        # Python version
        ctx.python_version = await self._detect_python_version()

        # Build system
        ctx.build_system = self._detect_build_system(project_dir)

        # Test framework
        ctx.test_framework = self._detect_test_framework(project_dir)

        # Linter
        ctx.linter = self._detect_linter(project_dir)

        # Directory structure
        ctx.structure_summary = self._detect_structure(project_dir)

        # AGENT.md content
        ctx.agent_md_content = self._read_agent_md(project_dir)

        return ctx

    # ── detection helpers ──────────────────────────────────────

    async def _detect_git(self, project_dir: Path, ctx: ProjectContext) -> None:
        """Detect git repository information."""
        git_dir = project_dir / ".git"
        if not git_dir.exists():
            ctx.is_git_repo = False
            return

        ctx.is_git_repo = True

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_dir,
            )
            stdout, _ = await proc.communicate()
            branch = decode_tool_output(stdout).strip()
            ctx.git_branch = branch if branch else None
        except (OSError, FileNotFoundError):
            logger.exception(
                "Failed to detect git branch",
                extra={
                    "category": "error",
                    "component": "system",
                    "context": "detect git branch",
                },
            )
            ctx.git_branch = None

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_dir,
            )
            stdout, _ = await proc.communicate()
            lines = [line for line in decode_tool_output(stdout).strip().split("\n") if line]
            if lines:
                modified = sum(1 for line in lines if line[1] in "MARC")
                untracked = sum(1 for line in lines if line.startswith("??"))
                parts = []
                if modified:
                    parts.append(f"{modified} files modified")
                if untracked:
                    parts.append(f"{untracked} files untracked")
                ctx.git_status = ", ".join(parts) if parts else None
        except (OSError, FileNotFoundError):
            logger.exception(
                "Failed to detect git status",
                extra={
                    "category": "error",
                    "component": "system",
                    "context": "detect git status",
                },
            )
            ctx.git_status = None

    def _detect_project_type(self, project_dir: Path) -> str:
        """Detect project type by checking for marker files."""
        for marker, ptype in self._TYPE_MARKERS.items():
            if (project_dir / marker).exists():
                return ptype
        return "unknown"

    def _detect_package_manager(self, project_dir: Path) -> str | None:
        """Detect package manager from lock files."""
        for lock_file, manager in self._PACKAGE_MANAGER_MARKERS.items():
            if (project_dir / lock_file).exists():
                return manager
        return None

    async def _detect_python_version(self) -> str | None:
        """Detect Python version.

        Tries 'python3' first (correct on systems where 'python' is Python 2),
        then falls back to 'python', and finally uses sys.version from the
        running interpreter (always Python 3.12+ since we require it).
        """
        # Try python3 first, then python, then sys.version
        for python_cmd in ("python3", "python"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    python_cmd, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                output = decode_tool_output(stdout + stderr).strip()
                # "Python 3.12.3" → "3.12"
                if output.startswith("Python "):
                    version = output.split()[1]
                    parts = version.split(".")
                    if len(parts) >= 2:
                        return f"{parts[0]}.{parts[1]}"
                    return version
            except (OSError, FileNotFoundError):
                logger.exception(
                    "Failed to detect Python version using %s",
                    python_cmd,
                    extra={
                        "category": "error",
                        "component": "system",
                        "context": "detect python version",
                    },
                )
                continue
        # Final fallback: use the running interpreter's version
        import sys
        return f"{sys.version_info.major}.{sys.version_info.minor}"

    def _detect_build_system(self, project_dir: Path) -> str | None:
        """Detect build system."""
        # Check for Makefile
        if (project_dir / "Makefile").exists():
            return "make"
        # Check pyproject.toml for build-system
        pyproject = project_dir / "pyproject.toml"
        if pyproject.exists():
            return "pyproject"
        # package.json scripts
        package_json = project_dir / "package.json"
        if package_json.exists():
            return "npm"
        return None

    def _detect_test_framework(self, project_dir: Path) -> str | None:
        """Detect test framework from config files."""
        for marker, framework in self._TEST_FRAMEWORK_MARKERS.items():
            if (project_dir / marker).exists() or (project_dir / marker).is_dir():
                return framework
        # Check pyproject.toml for pytest config
        pyproject = project_dir / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8")
            if "pytest" in content.lower():
                return "pytest"
        return None

    def _detect_linter(self, project_dir: Path) -> str | None:
        """Detect linter from config files."""
        for marker, linter in self._LINTER_MARKERS.items():
            if (project_dir / marker).exists():
                return linter
        # Check pyproject.toml for ruff config
        pyproject = project_dir / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8")
            if "[tool.ruff" in content:
                return "ruff"
        return None

    def _detect_structure(self, project_dir: Path) -> str:
        """Scan top-level directories and produce compact summary."""
        try:
            dirs = sorted(
                d.name
                for d in project_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )
            return "/".join(dirs) if dirs else "(empty)"
        except PermissionError:
            logger.exception(
                "Failed to summarize project directory structure",
                extra={
                    "category": "error",
                    "component": "system",
                    "context": "summarize project directory structure",
                },
            )
            return "(no access)"

    def _read_agent_md(self, project_dir: Path) -> str | None:
        """Read AGENT.md or CLAUDE.md from project root."""
        for candidate in self._AGENT_MD_CANDIDATES:
            path = project_dir / candidate
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None
