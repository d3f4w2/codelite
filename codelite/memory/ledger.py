from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite.storage.events import RuntimeLayout, utc_now


@dataclass(frozen=True)
class MemoryEntry:
    entry_id: str
    kind: str
    text: str
    metadata: dict[str, Any]
    evidence: list[dict[str, Any]]
    created_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MemoryEntry:
        return cls(
            entry_id=str(payload["entry_id"]),
            kind=str(payload["kind"]),
            text=str(payload["text"]),
            metadata=dict(payload.get("metadata") or {}),
            evidence=list(payload.get("evidence") or []),
            created_at=str(payload["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryLedger:
    def __init__(self, layout: RuntimeLayout) -> None:
        self.layout = layout
        self.layout.ensure()

    def append(
        self,
        *,
        kind: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            entry_id=uuid.uuid4().hex,
            kind=kind,
            text=text,
            metadata=dict(metadata or {}),
            evidence=list(evidence or []),
            created_at=utc_now(),
        )
        with self.layout.memory_ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False))
            handle.write("\n")
        return entry

    def list_entries(self) -> list[MemoryEntry]:
        path = self.layout.memory_ledger_path
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            return [MemoryEntry.from_dict(json.loads(line)) for line in handle if line.strip()]

    def get(self, entry_id: str) -> MemoryEntry | None:
        for entry in self.list_entries():
            if entry.entry_id == entry_id:
                return entry
        return None
