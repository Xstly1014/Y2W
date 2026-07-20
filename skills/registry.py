"""Skill registry — collects skills and exposes all their tools at once.

Beyond the original register/get/all/all_tools surface, this module now
also exposes:
  * `unregister`           — remove a skill by name (useful for tests / hot-reload)
  * `list_metadata`        — machine-readable metadata for UI / API listing
  * `list_skills_info`     — human-readable multi-line summary for CLI / debugging
  * `filter_by_tag`        — discover skills by category tag
  * `filter_by_permission` — discover skills by required permission
  * `enabled_tools`        — respects `enabled_by_default` when flattening tools
"""
from __future__ import annotations

from typing import Iterable

from langchain_core.tools import BaseTool

from skills.base import Skill


class SkillRegistry:
    def __init__(self, skills: Iterable[Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        for s in skills or []:
            self.register(s)

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def unregister(self, name: str) -> bool:
        """Remove a skill by name. Returns True if removed."""
        if name in self._skills:
            del self._skills[name]
            return True
        return False

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def all_tools(self) -> list[BaseTool]:
        """Flatten all skills' tools into a single list for the agent."""
        tools: list[BaseTool] = []
        for skill in self._skills.values():
            tools.extend(skill.get_tools())
        return tools

    def list_metadata(self) -> list[dict]:
        """Return metadata of all registered skills (sorted by name)."""
        return [s.metadata() for s in sorted(self._skills.values(), key=lambda s: s.name)]

    def list_skills_info(self) -> str:
        """Human-readable summary (multi-line string) for CLI/debugging."""
        if not self._skills:
            return "(no skills registered)"
        lines = [f"Registered skills ({len(self._skills)}):"]
        for skill in sorted(self._skills.values(), key=lambda s: s.name):
            tags = ", ".join(skill.tags) if skill.tags else "-"
            perms = ", ".join(skill.permissions) if skill.permissions else "-"
            enabled = "on" if skill.enabled_by_default else "off"
            lines.append(
                f"  - {skill.name} v{skill.version} [{enabled}] "
                f"tags=[{tags}] perms=[{perms}]: {skill.description}"
            )
        return "\n".join(lines)

    def filter_by_tag(self, tag: str) -> list[Skill]:
        """Return skills that have `tag` in their tags."""
        return [s for s in self._skills.values() if tag in s.tags]

    def filter_by_permission(self, permission: str) -> list[Skill]:
        """Return skills that require `permission`."""
        return [s for s in self._skills.values() if permission in s.permissions]

    def enabled_tools(self, *, include_disabled: bool = False) -> list[BaseTool]:
        """Like all_tools but respects enabled_by_default flag.

        When include_disabled=True, return all tools regardless.
        """
        tools: list[BaseTool] = []
        for skill in self._skills.values():
            if not include_disabled and not skill.enabled_by_default:
                continue
            tools.extend(skill.get_tools())
        return tools
