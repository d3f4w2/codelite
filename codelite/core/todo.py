from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from codelite.core.events import EventBus
from codelite.storage.events import RuntimeLayout, utc_now


class TodoStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class TodoItem:
    id: str
    content: str
    status: TodoStatus = TodoStatus.PENDING
    note: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TodoItem:
        return cls(
            id=str(payload["id"]),
            content=str(payload["content"]),
            status=TodoStatus(payload.get("status", TodoStatus.PENDING.value)),
            note=str(payload.get("note", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True)
class TodoSnapshot:
    session_id: str
    source: str
    created_at: str
    updated_at: str
    prompt: str = ""
    items: list[TodoItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TodoSnapshot:
        return cls(
            session_id=str(payload["session_id"]),
            source=str(payload.get("source", "manual")),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            prompt=str(payload.get("prompt", "")),
            items=[TodoItem.from_dict(item) for item in payload.get("items", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "prompt": self.prompt,
            "items": [item.to_dict() for item in self.items],
        }


class TodoManager:
    def __init__(self, layout: RuntimeLayout, event_bus: EventBus | None = None) -> None:
        self.layout = layout
        self.layout.ensure()
        self.event_bus = event_bus

    def path_for(self, session_id: str) -> Path:
        return self.layout.todos_dir / f"{session_id}.json"

    def get(self, session_id: str) -> TodoSnapshot | None:
        path = self.path_for(session_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return TodoSnapshot.from_dict(json.load(handle))

    def latest_session_ids(self, limit: int = 1) -> list[str]:
        files = sorted(
            self.layout.todos_dir.glob("*.json"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        return [path.stem for path in files[:limit]]

    def replace(
        self,
        session_id: str,
        items: list[dict[str, Any]],
        *,
        source: str = "manual",
        prompt: str | None = None,
    ) -> TodoSnapshot:
        existing = self.get(session_id)
        now = utc_now()
        normalized = [self._normalize_item(index, item) for index, item in enumerate(items, start=1)]
        snapshot = TodoSnapshot(
            session_id=session_id,
            source=source if existing is None else source or existing.source,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            prompt=prompt if prompt is not None else (existing.prompt if existing is not None else ""),
            items=normalized,
        )
        self._write_snapshot(snapshot)
        if self.event_bus is not None:
            self.event_bus.emit(
                "todo_updated",
                {
                    "session_id": session_id,
                    "source": snapshot.source,
                    "item_count": len(snapshot.items),
                    "path": str(self.path_for(session_id)),
                },
                session_id=session_id,
            )
        return snapshot

    def ensure_seeded(self, session_id: str, prompt: str) -> TodoSnapshot:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        return self.replace(
            session_id,
            [{"id": "task-1", "content": prompt, "status": TodoStatus.PENDING.value}],
            source="auto",
            prompt=prompt,
        )

    def mark_auto_seeded_done(self, session_id: str) -> TodoSnapshot | None:
        snapshot = self.get(session_id)
        if snapshot is None or snapshot.source != "auto" or len(snapshot.items) != 1:
            return snapshot
        item = snapshot.items[0]
        if item.status is TodoStatus.DONE:
            return snapshot
        return self.replace(
            session_id,
            [
                {
                    "id": item.id,
                    "content": item.content,
                    "status": TodoStatus.DONE.value,
                    "note": item.note,
                }
            ],
            source=snapshot.source,
            prompt=snapshot.prompt,
        )

    def summarize(self, session_id: str) -> dict[str, Any]:
        snapshot = self.get(session_id)
        if snapshot is None:
            return {"session_id": session_id, "items": [], "counts": {}}
        counts: dict[str, int] = {}
        for item in snapshot.items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return {
            **snapshot.to_dict(),
            "counts": counts,
        }

    def _normalize_item(self, index: int, payload: dict[str, Any]) -> TodoItem:
        content = str(payload.get("content") or payload.get("title") or "").strip()
        if not content:
            raise ValueError("todo item content must not be empty")
        status = TodoStatus(str(payload.get("status", TodoStatus.PENDING.value)))
        item_id = str(payload.get("id") or f"todo-{index}")
        note = str(payload.get("note", ""))
        return TodoItem(id=item_id, content=content, status=status, note=note)

    def _write_snapshot(self, snapshot: TodoSnapshot) -> None:
        path = self.path_for(snapshot.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
