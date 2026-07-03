"""Tests for SkillLoader."""

import tomllib
from pathlib import Path

from myagent.skills.loader import SkillLoader


class TestSkillLoader:
    def test_parse_valid_skill_md(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: my-skill
description: A test skill
---

## When to use
For testing purposes.
""")

        skill = SkillLoader.parse_skill_md(skill_md)
        assert skill is not None
        assert skill.name == "my-skill"
        assert skill.description == "A test skill"
        assert "## When to use" in skill.content

    def test_parse_missing_file(self, tmp_path):
        skill = SkillLoader.parse_skill_md(tmp_path / "nonexistent" / "SKILL.md")
        assert skill is None

    def test_enumerate_resources(self, tmp_path):
        skill_dir = tmp_path / "skill-with-resources"
        (skill_dir / "references").mkdir(parents=True)
        (skill_dir / "scripts").mkdir()
        (skill_dir / "references" / "guide.md").write_text("# Guide")
        (skill_dir / "scripts" / "run.sh").write_text("echo hi")

        resources = SkillLoader.enumerate_resources(skill_dir)
        assert len(resources.references) == 1
        assert len(resources.scripts) == 1
        assert len(resources.templates) == 0
        assert len(resources.assets) == 0

    def test_builtin_tdd_template_uses_non_python_suffix_and_is_enumerated(self):
        repo_root = Path(__file__).resolve().parents[2]
        skill_dir = repo_root / "myagent" / "skills" / "builtin" / "tdd"

        resources = SkillLoader.enumerate_resources(skill_dir)
        template_paths = {path.as_posix() for path in resources.templates}

        assert "templates/test_template.py.tmpl" in template_paths
        assert "templates/test_template.py" not in template_paths

    def test_package_data_includes_all_builtin_skill_resources(self):
        repo_root = Path(__file__).resolve().parents[2]
        pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))

        package_data = pyproject["tool"]["setuptools"]["package-data"]["myagent"]

        assert "skills/builtin/**/*" in package_data
