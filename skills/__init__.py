"""Skills package.

A *skill* is a higher-level capability than a *tool*: it may orchestrate
multiple tools, embed its own prompt, or wrap a full sub-pipeline. Each
skill exposes itself as one or more LangChain tools so the agent can pick
it up transparently.

Each Skill subclass carries lightweight metadata (version / tags /
permissions / dependencies / enabled_by_default) that the registry can
surface for UI listing, filtering, and permission gating — see
`Skill.metadata()` and `SkillRegistry.list_metadata()`.

Future expansion hooks:
  * skill discovery via entry points / plugin directory
  * skill versioning
  * per-skill evaluation harness
"""
from skills.base import Skill
from skills.code_review import CodeReviewSkill
from skills.commerce import CommerceSkills
from skills.data_analysis import DataAnalysisSkill
from skills.registry import SkillRegistry
from skills.summarize import SummarizeSkill
from skills.translator import TranslatorSkill

__all__ = [
    "Skill",
    "SkillRegistry",
    "CommerceSkills",
    "SummarizeSkill",
    "DataAnalysisSkill",
    "TranslatorSkill",
    "CodeReviewSkill",
]
