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
        res = SkillResources()
        for subdir_name in SkillLoader.RESOURCE_DIRS:
            subdir = skill_dir / subdir_name
            if subdir.is_dir():
                files = sorted(subdir.iterdir())
                setattr(res, subdir_name, files)
        return res
