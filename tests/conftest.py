"""Shared test fixtures for myagent tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_project_dir():
    """Create a temporary project directory with .myagent/ skeleton."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        myagent_dir = project_dir / ".myagent"
        myagent_dir.mkdir(parents=True)
        (myagent_dir / "memory").mkdir()
        (myagent_dir / "memory" / "MEMORY.md").write_text("# Memory Index\n")
        yield project_dir


@pytest.fixture
def tmp_home_dir():
    """Create a temporary home directory with default config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home_dir = Path(tmpdir)
        myagent_home = home_dir / ".myagent"
        myagent_home.mkdir(parents=True)
        config = myagent_home / "config.yaml"
        config.write_text("model:\n  provider: deepseek\n  model: deepseek-v4-pro\n")
        (myagent_home / "memory").mkdir()
        (myagent_home / "memory" / "MEMORY.md").write_text("# Memory Index\n")
        (myagent_home / "sessions").mkdir()
        (myagent_home / "logs").mkdir()
        (myagent_home / "skills").mkdir()
        yield home_dir
