"""Project initialization helpers for MyAgentCLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

GUIDANCE_FILENAME = "AGENTS.md"

DEFAULT_AGENTS_MD_TEMPLATE = """# AGENTS.md

This file gives MyAgentCLI project-specific guidance. It is loaded automatically
from the project root when a session starts.

## Project Overview

- Describe what this project does.
- Note the primary language, framework, runtime, and package manager.

## Build, Test, and Lint

```bash
# Install dependencies

# Run tests

# Run lint
```

## Architecture

- Summarize the main modules and ownership boundaries.
- List important data files, generated files, and runtime directories.

## Conventions

- Describe coding style, naming, logging, and documentation expectations.
- Note any files or directories the agent should not edit without confirmation.

## Verification

- List the checks that should be run before considering work complete.
- Include any platform-specific commands or environment requirements.
"""


@dataclass(frozen=True)
class InitResult:
    """Result of creating or locating project guidance."""

    path: Path
    created: bool


def resolve_project_root(project_dir: Path) -> Path:
    """Resolve the project root used for generated guidance files."""

    start_dir = project_dir.expanduser()
    git_root = _find_git_root(start_dir)
    return (git_root or start_dir).resolve()


def _find_git_root(start_dir: Path) -> Path | None:
    """Walk up from ``start_dir`` looking for a Git repository root."""

    current = start_dir.resolve()
    for _ in range(10):
        if (current / ".git").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_existing_guidance_file(project_root: Path) -> Path | None:
    """Return an existing root guidance file if one is already present."""

    for name in (GUIDANCE_FILENAME, "AGENTS.MD"):
        path = project_root / name
        if path.is_file():
            return path
    return None


def initialize_project_guidance(project_dir: Path, *, force: bool = False) -> InitResult:
    """Create the root AGENTS.md file for a project.

    Existing user-authored guidance is preserved unless ``force`` is true.
    """

    project_root = resolve_project_root(project_dir)
    project_root.mkdir(parents=True, exist_ok=True)

    existing = find_existing_guidance_file(project_root)
    target = existing or project_root / GUIDANCE_FILENAME
    if existing is not None and not force:
        return InitResult(path=existing, created=False)

    target.write_text(DEFAULT_AGENTS_MD_TEMPLATE, encoding="utf-8")
    return InitResult(path=target, created=True)
