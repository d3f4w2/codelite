from __future__ import annotations

from typing import Any

from codelite.memory import MemoryLedger, MemoryPolicy, MemoryViews


class MemoryRuntime:
    def __init__(self, ledger: MemoryLedger, views: MemoryViews, policy: MemoryPolicy) -> None:
        self.ledger = ledger
        self.views = views
        self.policy = policy

    def remember(
        self,
        *,
        kind: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not self.policy.should_write(kind=kind, text=text):
            return None
        entry = self.ledger.append(kind=kind, text=text, metadata=metadata, evidence=evidence)
        view_paths = self.views.refresh(self.ledger.list_entries())
        return {
            "entry": entry.to_dict(),
            "views": view_paths,
        }

    def timeline(self) -> dict[str, Any]:
        return self.views.read_timeline()

    def keywords(self) -> dict[str, Any]:
        return self.views.read_keywords()

    def skills(self) -> dict[str, Any]:
        return self.views.read_skills()
