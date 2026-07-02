"""Tests for SkillRegistry."""

import pytest

from myagent.skills.registry import SkillRegistry


def create_skill(dir_path, name, description="Test skill"):
    """Create a minimal SKILL.md in a skill directory."""
    skill_dir = dir_path / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"""---
name: {name}
description: {description}
---

# {name}
Test content.
""")
    return skill_dir


class TestSkillRegistry:
    @pytest.mark.asyncio
    async def test_discover_finds_skills(self, tmp_path):
        builtin = tmp_path / "builtin"
        create_skill(builtin, "test-skill")

        registry = SkillRegistry(builtin_dir=builtin, user_dir=tmp_path / "user")
        await registry.discover()

        entries = registry.list_all()
        assert len(entries) == 1
        assert entries[0].name == "test-skill"
        assert entries[0].source == "builtin"

    @pytest.mark.asyncio
    async def test_user_overrides_builtin(self, tmp_path):
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        create_skill(builtin, "shared-skill", "Built-in version")
        create_skill(user, "shared-skill", "User version")

        registry = SkillRegistry(builtin_dir=builtin, user_dir=user)
        await registry.discover()

        entries = registry.list_all()
        assert len(entries) == 1
        assert entries[0].source == "user"
        assert entries[0].description == "User version"

    @pytest.mark.asyncio
    async def test_get_returns_full_skill(self, tmp_path):
        builtin = tmp_path / "builtin"
        create_skill(builtin, "my-skill", "Full skill")

        registry = SkillRegistry(builtin_dir=builtin, user_dir=tmp_path / "user")
        await registry.discover()

        skill = registry.get("my-skill")
        assert skill is not None
        assert "# my-skill" in skill.content

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, tmp_path):
        registry = SkillRegistry(builtin_dir=tmp_path / "builtin", user_dir=tmp_path / "user")
        await registry.discover()
        assert registry.get("nonexistent") is None
