from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelite.config import RuntimeConfig
from codelite.core.events import EventBus
from codelite.storage.events import RuntimeLayout, utc_now


@dataclass(frozen=True)
class ContextSnapshot:
    session_id: str
    compacted_at: str
    original_message_count: int
    compacted_message_count: int
    kept_message_count: int
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "compacted_at": self.compacted_at,
            "original_message_count": self.original_message_count,
            "compacted_message_count": self.compacted_message_count,
            "kept_message_count": self.kept_message_count,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ContextSnapshot:
        return cls(
            session_id=str(payload["session_id"]),
            compacted_at=str(payload["compacted_at"]),
            original_message_count=int(payload["original_message_count"]),
            compacted_message_count=int(payload["compacted_message_count"]),
            kept_message_count=int(payload["kept_message_count"]),
            summary=str(payload["summary"]),
        )


class ContextCompact:
    def __init__(
        self,
        layout: RuntimeLayout,
        runtime_config: RuntimeConfig,
        event_bus: EventBus | None = None,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.event_bus = event_bus
        self.max_messages = runtime_config.context_auto_compact_message_count
        self.max_chars = runtime_config.context_auto_compact_char_count
        self.keep_last_messages = runtime_config.context_keep_last_messages
        self.summary_line_chars = runtime_config.context_summary_line_chars

    def path_for(self, session_id: str) -> Path:
        return self.layout.context_dir / f"{session_id}.json"

    def get(self, session_id: str) -> ContextSnapshot | None:
        path = self.path_for(session_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return ContextSnapshot.from_dict(json.load(handle))

    def latest_session_ids(self, limit: int = 1) -> list[str]:
        files = sorted(
            self.layout.context_dir.glob("*.json"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        return [path.stem for path in files[:limit]]

    def should_compact(self, messages: list[dict[str, Any]]) -> bool:
        non_system = [message for message in messages if message.get("role") != "system"]
        total_chars = sum(len(self._message_text(message)) for message in non_system)
        return len(non_system) > self.max_messages or total_chars > self.max_chars

    def prepare(self, session_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.should_compact(messages):
            return messages

        system_prefix: list[dict[str, Any]] = []
        working = list(messages)
        if working and working[0].get("role") == "system":
            system_prefix.append(working.pop(0))

        if len(working) <= self.keep_last_messages:
            return messages

        retained = working[-self.keep_last_messages :]
        summarized = working[: -self.keep_last_messages]
        summary_text = self._summarize_messages(summarized)
        summary_message = {
            "role": "system",
            "content": "Previous session context summary:\n" + summary_text,
        }
        compacted = [*system_prefix, summary_message, *retained]
        snapshot = ContextSnapshot(
            session_id=session_id,
            compacted_at=utc_now(),
            original_message_count=len(messages),
            compacted_message_count=len(compacted),
            kept_message_count=len(retained),
            summary=summary_text,
        )
        self._write_snapshot(snapshot)
        if self.event_bus is not None:
            self.event_bus.emit(
                "context_compacted",
                {
                    "session_id": session_id,
                    "original_message_count": snapshot.original_message_count,
                    "compacted_message_count": snapshot.compacted_message_count,
                    "path": str(self.path_for(session_id)),
                },
                session_id=session_id,
            )
        return compacted

    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for index, message in enumerate(messages, start=1):
            text = self._message_text(message).replace("\n", " ").strip()
            if len(text) > self.summary_line_chars:
                text = text[: self.summary_line_chars - 3] + "..."
            role = str(message.get("role", "unknown"))
            lines.append(f"{index}. {role}: {text}")
        return "\n".join(lines) if lines else "(no older context)"

    def _message_text(self, message: dict[str, Any]) -> str:
        parts: list[str] = []
        if "name" in message:
            parts.append(str(message["name"]))
        if "content" in message and message["content"] is not None:
            parts.append(str(message["content"]))
        if "tool_calls" in message:
            parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
        if "tool_call_id" in message:
            parts.append(str(message["tool_call_id"]))
        return " ".join(part for part in parts if part)

    def _write_snapshot(self, snapshot: ContextSnapshot) -> None:
        path = self.path_for(snapshot.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
