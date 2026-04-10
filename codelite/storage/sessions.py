from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from codelite.storage.events import EventStore


class SessionStore:
    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store
        self._listeners: list[Any] = []

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
        event = self.event_store.append(session_id, event_type, payload)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                continue
        return event

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
        return [message for _, message in self.load_messages_with_event_ids(session_id)]

    def load_messages_with_event_ids(self, session_id: str) -> list[tuple[str | None, dict[str, Any]]]:
        events = self.replay(session_id)
        message_events: list[tuple[str, dict[str, Any]]] = []
        latest_compaction: dict[str, Any] | None = None

        for event in events:
            event_type = str(event.get("event_type", ""))
            if event_type == "message":
                payload = dict(event.get("payload") or {})
                message_events.append(
                    (
                        str(event.get("event_id", "")),
                        self._message_from_payload(payload),
                    )
                )
                continue
            if event_type == "session_compacted":
                latest_compaction = dict(event.get("payload") or {})

        if latest_compaction is None:
            return [(event_id, dict(message)) for event_id, message in message_events]

        boundary_event_id = str(latest_compaction.get("boundary_event_id") or "").strip()
        start_index = 0
        if boundary_event_id:
            found = False
            for index, (event_id, _) in enumerate(message_events):
                if event_id == boundary_event_id:
                    start_index = index
                    found = True
                    break
            if not found:
                start_index = 0

        active = [(event_id, dict(message)) for event_id, message in message_events[start_index:]]
        summary = str(latest_compaction.get("summary") or "").strip()
        if summary:
            active.insert(
                0,
                (
                    None,
                    {
                        "role": "system",
                        "content": "Compacted conversation summary:\n" + summary,
                    },
                ),
            )
        return active

    def latest_session_ids(self, limit: int = 1, *, include_system: bool = False) -> list[str]:
        session_ids = self.list_session_ids(include_system=include_system)
        return session_ids[: max(1, int(limit))]

    def list_session_ids(self, *, include_system: bool = False) -> list[str]:
        session_files = sorted(
            self.event_store.layout.sessions_dir.glob("*.jsonl"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        session_ids: list[str] = []
        for path in session_files:
            session_id = path.stem
            if not include_system and session_id.startswith("system"):
                continue
            session_ids.append(session_id)
        return session_ids

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        normalized = title.strip()
        if not normalized:
            raise RuntimeError("session title must not be empty")
        return self.append_event(session_id, "session_renamed", {"title": normalized})

    def session_title(self, session_id: str) -> str | None:
        events = self.replay(session_id)
        for event in reversed(events):
            if str(event.get("event_type", "")) != "session_renamed":
                continue
            payload = dict(event.get("payload") or {})
            title = str(payload.get("title", "")).strip()
            if title:
                return title
        return None

    def list_session_summaries(
        self,
        *,
        limit: int = 20,
        include_system: bool = False,
        query: str = "",
    ) -> list[dict[str, Any]]:
        normalized_query = query.strip().lower()
        summaries: list[dict[str, Any]] = []
        for session_id in self.list_session_ids(include_system=include_system):
            summary = self.session_summary(session_id)
            if summary is None:
                continue
            if normalized_query:
                haystack = " ".join(
                    [
                        str(summary.get("session_id", "")),
                        str(summary.get("title", "")),
                        str(summary.get("preview", "")),
                    ]
                ).lower()
                if normalized_query not in haystack:
                    continue
            summaries.append(summary)
            if len(summaries) >= max(1, int(limit)):
                break
        return summaries

    def session_summary(self, session_id: str) -> dict[str, Any] | None:
        events = self.replay(session_id)
        if not events:
            return None
        title = self.session_title(session_id) or ""
        preview = self._session_preview(events)
        created_at = str(events[0].get("timestamp_utc", "") or "")
        updated_at = str(events[-1].get("timestamp_utc", "") or created_at)
        conversation = title or preview or session_id
        return {
            "session_id": session_id,
            "title": title,
            "preview": preview,
            "conversation": conversation,
            "created_at": created_at,
            "updated_at": updated_at,
            "session_path": str(self.event_store.session_path(session_id)),
        }

    def replay(self, session_id: str) -> list[dict[str, Any]]:
        return self.event_store.read_session_events(session_id)

    def add_listener(self, listener: Any) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Any) -> None:
        self._listeners = [item for item in self._listeners if item is not listener]

    @staticmethod
    def _message_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        message: dict[str, Any] = {"role": payload["role"]}
        if "content" in payload:
            message["content"] = payload["content"]
        if "tool_calls" in payload:
            message["tool_calls"] = payload["tool_calls"]
        if "tool_call_id" in payload:
            message["tool_call_id"] = payload["tool_call_id"]
        if "name" in payload:
            message["name"] = payload["name"]
        return message

    @staticmethod
    def _session_preview(events: list[dict[str, Any]]) -> str:
        for event in reversed(events):
            event_type = str(event.get("event_type", ""))
            payload = dict(event.get("payload") or {})
            if event_type == "session_renamed":
                title = str(payload.get("title", "")).strip()
                if title:
                    return title[:96]
            if event_type == "turn_finished":
                preview = str(payload.get("answer_preview", "")).strip()
                if preview:
                    return preview[:96]
            if event_type == "message":
                role = str(payload.get("role", ""))
                content = str(payload.get("content", "")).strip()
                if not content:
                    continue
                if role == "assistant":
                    return content[:96]
                if role == "user":
                    return content[:96]
        return ""
