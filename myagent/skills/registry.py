"""Skill registry — 3-tier discovery with priority override."""

from __future__ import annotations

from pathlib import Path

from myagent.skills.loader import Skill, SkillEntry, SkillLoader


class SkillRegistry:
    """Scans three tiers (built-in < user < project) and registers skills.

    Same-named skill at higher priority completely replaces lower.
    """

    def __init__(
        self,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
    ):
        self.builtin_dir = builtin_dir or Path(__file__).parent / "builtin"
        self.user_dir = user_dir or Path.home() / ".myagent" / "skills"
        self.project_dir = project_dir
        self._skills: dict[str, Skill] = {}
        self._entries: list[SkillEntry] = []

    async def discover(self) -> None:
        self._skills.clear()
        self._entries.clear()

        # Scan tiers in priority order (low → high, later overrides)
        tiers = [
            ("builtin", self.builtin_dir),
            ("user", self.user_dir),
        ]
        if self.project_dir:
            tiers.append(("project", self.project_dir))

        for source, dir_path in tiers:
            if not dir_path or not dir_path.is_dir():
                continue
            for skill_dir in sorted(dir_path.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                skill = SkillLoader.parse_skill_md(skill_md)
                if skill:
                    self._skills[skill.name] = skill
                    # Replace entry if same name
                    self._entries = [
                        e for e in self._entries if e.name != skill.name
                    ]
                    self._entries.append(
                        SkillEntry(
                            name=skill.name,
                            description=skill.description,
                            source=source,
                        )
                    )

    def list_all(self) -> list[SkillEntry]:
        return list(self._entries)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)
