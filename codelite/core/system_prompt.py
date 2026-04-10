from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DYNAMIC_BOUNDARY_MARKER = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


@dataclass(frozen=True)
class SystemPromptParts:
    static_prefix: str
    dynamic_suffix: str

    @property
    def full_prompt(self) -> str:
        if not self.dynamic_suffix.strip():
            return self.static_prefix.rstrip()
        return (
            self.static_prefix.rstrip()
            + "\n\n"
            + DYNAMIC_BOUNDARY_MARKER
            + "\n"
            + self.dynamic_suffix.strip()
        )


def build_system_prompt(
    *,
    base_prompt: str,
    workspace_root: Path,
    session_id: str,
    profile_name: str,
    enable_dynamic_boundary: bool,
) -> SystemPromptParts:
    static_prefix = base_prompt.strip()
    dynamic_suffix = (
        f"session_id: {session_id}\n"
        f"workspace_root: {workspace_root}\n"
        f"routing_profile: {profile_name}\n"
        f"timestamp_utc: {datetime.now(timezone.utc).isoformat()}"
    )
    if not enable_dynamic_boundary:
        return SystemPromptParts(static_prefix=static_prefix, dynamic_suffix="")
    return SystemPromptParts(static_prefix=static_prefix, dynamic_suffix=dynamic_suffix)
