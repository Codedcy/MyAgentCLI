"""Skill loader — parses SKILL.md files and enumerates resources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class SkillResources:
    references: list[Path] = None
    scripts: list[Path] = None
    templates: list[Path] = None
    assets: list[Path] = None

    def __post_init__(self):
        self.references = self.references or []
        self.scripts = self.scripts or []
        self.templates = self.templates or []
        self.assets = self.assets or []


@dataclass
class Skill:
    name: str
    description: str
    content: str
    resources: SkillResources
    base_dir: Path | None = None


@dataclass
class SkillEntry:
    name: str
    description: str
    source: str  # builtin | user | project


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class SkillLoader:
    """Static methods to parse SKILL.md and enumerate resources."""

    RESOURCE_DIRS = ["references", "scripts", "templates", "assets"]

    @staticmethod
    def parse_skill_md(path: Path) -> Skill | None:
        if not path.exists():
            return None

        content = path.read_text(encoding="utf-8")
        fm_match = FRONTMATTER_RE.match(content)
        if not fm_match:
            return None

        try:
            fm = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError:
            return None

        name = fm.get("name", path.parent.name)
        description = fm.get("description", "")
        body = content[fm_match.end():]

        resources = SkillLoader.enumerate_resources(path.parent)
        return Skill(name=name, description=description, content=body,
                     resources=resources, base_dir=path.parent)

    @staticmethod
    def enumerate_resources(skill_dir: Path) -> SkillResources:
        """Enumerate resource files relative to the skill directory root.

        Per spec §七: "脚本路径相对于技能目录根". Resource paths are stored
        as relative paths from skill_dir so they appear as e.g. 'scripts/lint.sh'
        instead of absolute paths in the system prompt (gap-20-03).
        """
        res = SkillResources()
        for subdir_name in SkillLoader.RESOURCE_DIRS:
            subdir = skill_dir / subdir_name
            if subdir.is_dir():
                files = sorted(subdir.iterdir())
                # Convert absolute Path objects to paths relative to skill_dir
                relative_files = [f.relative_to(skill_dir) for f in files]
                setattr(res, subdir_name, relative_files)
        return res
