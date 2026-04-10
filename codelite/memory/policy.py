from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MemoryPolicy:
    allowed_kinds: set[str] = field(
        default_factory=lambda: {
            "prompt",
            "answer",
            "retrieval",
            "skill",
            "background",
            "subagent",
            "mcp",
            "failure",
            "review",
            "preference",
            "experience",
            "reflection_rule",
            "memory_candidate",
            "memory_decision",
            "memory_file_update",
        }
    )

    def should_write(self, *, kind: str, text: str) -> bool:
        return bool(text.strip()) and kind in self.allowed_kinds
