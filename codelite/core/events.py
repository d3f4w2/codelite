from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codelite.storage.events import EventStore


@dataclass(frozen=True)
class EventEnvelope:
    session_id: str
    event_type: str
    payload: dict[str, Any]


class EventBus:
    SYSTEM_SESSION_ID = "system"

    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return self.event_store.append(session_id or self.SYSTEM_SESSION_ID, event_type, payload)

    def emit_component(
        self,
        component_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.emit(
            event_type,
            {"component_id": component_id, **payload},
            session_id=f"system-{component_id}",
        )
