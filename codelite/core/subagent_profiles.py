from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Final

GENERAL_PURPOSE_AGENT_TYPE: Final[str] = "general-purpose"
EXPLORE_AGENT_TYPE: Final[str] = "explore"
PLAN_AGENT_TYPE: Final[str] = "plan"
VERIFICATION_AGENT_TYPE: Final[str] = "verification"

ALL_AGENT_TYPES: Final[tuple[str, ...]] = (
    GENERAL_PURPOSE_AGENT_TYPE,
    EXPLORE_AGENT_TYPE,
    PLAN_AGENT_TYPE,
    VERIFICATION_AGENT_TYPE,
)

READ_ONLY_AGENT_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "bash",
        "list_files",
        "read_file",
        "skills_list",
        "team_list",
        "subagent_status",
        "web_search",
    }
)


@dataclass(frozen=True)
class SubagentProfile:
    agent_type: str
    summary: str
    system_prompt: str
    allowed_tools: frozenset[str] | None = None
    model: str = "inherit"


@lru_cache(maxsize=1)
def get_builtin_agent_profiles() -> dict[str, SubagentProfile]:
    return {
        GENERAL_PURPOSE_AGENT_TYPE: SubagentProfile(
            agent_type=GENERAL_PURPOSE_AGENT_TYPE,
            summary="General-purpose execution agent.",
            system_prompt=(
                "You are a general-purpose subagent. Complete the assigned task using available tools. "
                "Prefer concise, factual output and include key findings."
            ),
            allowed_tools=None,
        ),
        EXPLORE_AGENT_TYPE: SubagentProfile(
            agent_type=EXPLORE_AGENT_TYPE,
            summary="Fast read-only codebase exploration agent.",
            system_prompt=(
                "You are an explore subagent in strict read-only mode. "
                "Do not create, edit, delete, or move files. "
                "Search broadly, read relevant files, and report findings clearly."
            ),
            allowed_tools=READ_ONLY_AGENT_TOOLS,
        ),
        PLAN_AGENT_TYPE: SubagentProfile(
            agent_type=PLAN_AGENT_TYPE,
            summary="Read-only implementation planning agent.",
            system_prompt=(
                "You are a planning subagent in strict read-only mode. "
                "Do not modify files. Explore existing code and produce a concrete implementation plan "
                "with key files, sequencing, risks, and validation steps."
            ),
            allowed_tools=READ_ONLY_AGENT_TOOLS,
        ),
        VERIFICATION_AGENT_TYPE: SubagentProfile(
            agent_type=VERIFICATION_AGENT_TYPE,
            summary="Verification-focused agent that tries to break changes.",
            system_prompt=(
                "You are a verification subagent. Try to break the implementation with real checks. "
                "Do not modify project files. Prefer command-based evidence over code reading alone. "
                "End with exactly one line: VERDICT: PASS, VERDICT: FAIL, or VERDICT: PARTIAL."
            ),
            allowed_tools=READ_ONLY_AGENT_TOOLS,
        ),
    }


def normalize_agent_type(agent_type: str | None) -> str:
    raw = str(agent_type or "").strip().lower()
    if not raw:
        return GENERAL_PURPOSE_AGENT_TYPE
    aliases = {
        "general": GENERAL_PURPOSE_AGENT_TYPE,
        "default": GENERAL_PURPOSE_AGENT_TYPE,
        "general-purpose": GENERAL_PURPOSE_AGENT_TYPE,
        "explore": EXPLORE_AGENT_TYPE,
        "plan": PLAN_AGENT_TYPE,
        "planner": PLAN_AGENT_TYPE,
        "verification": VERIFICATION_AGENT_TYPE,
        "verify": VERIFICATION_AGENT_TYPE,
        "review": VERIFICATION_AGENT_TYPE,
        "reviewer": VERIFICATION_AGENT_TYPE,
    }
    normalized = aliases.get(raw, raw)
    if normalized not in get_builtin_agent_profiles():
        supported = ", ".join(ALL_AGENT_TYPES)
        raise RuntimeError(f"invalid agent_type `{agent_type}`; supported values: {supported}")
    return normalized

