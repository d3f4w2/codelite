from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from codelite.storage.events import EventStore


class SessionStore:
    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    def new_session_id(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{uuid.uuid4().hex[:8]}"

    def ensure_session(self, session_id: str) -> None:
        session_path = self.event_store.session_path(session_id)
        if session_path.exists():
            return
        self.event_store.append(
            session_id,
            "session_started",
            {"workspace_root": str(self.event_store.layout.workspace_root)},
        )

    def append_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_session(session_id)
        return self.event_store.append(session_id, event_type, payload)

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": role}
        if content is not None:
            payload["content"] = content
        if tool_calls is not None:
            payload["tool_calls"] = tool_calls
        if tool_call_id is not None:
            payload["tool_call_id"] = tool_call_id
        if name is not None:
            payload["name"] = name
        return self.append_event(session_id, "message", payload)

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for event in self.replay(session_id):
            if event.get("event_type") != "message":
                continue
            payload = dict(event.get("payload") or {})
            message: dict[str, Any] = {"role": payload["role"]}
            if "content" in payload:
                message["content"] = payload["content"]
            if "tool_calls" in payload:
                message["tool_calls"] = payload["tool_calls"]
            if "tool_call_id" in payload:
                message["tool_call_id"] = payload["tool_call_id"]
            if "name" in payload:
                message["name"] = payload["name"]
            messages.append(message)
        return messages

    def latest_session_ids(self, limit: int = 1, *, include_system: bool = False) -> list[str]:
        return self.event_store.latest_session_ids(limit=limit, include_system=include_system)

    def replay(self, session_id: str) -> list[dict[str, Any]]:
        return self.event_store.read_session_events(session_id)
