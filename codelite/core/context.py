from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    applied_strategies: list[str] = field(default_factory=list)
    cleared_tool_results: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "compacted_at": self.compacted_at,
            "original_message_count": self.original_message_count,
            "compacted_message_count": self.compacted_message_count,
            "kept_message_count": self.kept_message_count,
            "summary": self.summary,
            "applied_strategies": self.applied_strategies,
            "cleared_tool_results": self.cleared_tool_results,
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
            applied_strategies=[str(item) for item in payload.get("applied_strategies", [])],
            cleared_tool_results=int(payload.get("cleared_tool_results", 0)),
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
        self.snip_enabled = runtime_config.context_snip_enabled
        self.collapse_enabled = runtime_config.context_collapse_enabled
        self.keep_recent_tool_results = max(0, runtime_config.tool_result_keep_recent)

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
        original = list(messages)
        working = list(messages)
        applied: list[str] = []
        summary_text = "(no older context)"
        retained_count = min(len([item for item in working if item.get("role") != "system"]), self.keep_last_messages)
        cleared_tool_results = 0

        if self.snip_enabled:
            snipped = self._snip_messages(working)
            if snipped != working:
                working = snipped
                applied.append("snip_compact")

        if self.should_compact(working):
            working, summary_text, retained_count = self._auto_compact(working)
            applied.append("auto_compact")

        if self.collapse_enabled:
            collapsed = self._collapse_context(working)
            if collapsed != working:
                working = collapsed
                applied.append("context_collapse")

        working, cleared_tool_results = self._clear_old_tool_results(working)
        if cleared_tool_results > 0:
            applied.append("function_result_clearing")

        if not applied:
            return messages

        snapshot = ContextSnapshot(
            session_id=session_id,
            compacted_at=utc_now(),
            original_message_count=len(original),
            compacted_message_count=len(working),
            kept_message_count=retained_count,
            summary=summary_text,
            applied_strategies=applied,
            cleared_tool_results=cleared_tool_results,
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
                    "applied_strategies": applied,
                    "cleared_tool_results": cleared_tool_results,
                },
                session_id=session_id,
            )
        return working

    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for index, message in enumerate(messages, start=1):
            text = self._message_text(message).replace("\n", " ").strip()
            if len(text) > self.summary_line_chars:
                text = text[: self.summary_line_chars - 3] + "..."
            role = str(message.get("role", "unknown"))
            lines.append(f"{index}. {role}: {text}")
        return "\n".join(lines) if lines else "(no older context)"

    def _auto_compact(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, int]:
        system_prefix: list[dict[str, Any]] = []
        working = list(messages)
        while working and working[0].get("role") == "system":
            system_prefix.append(working.pop(0))

        if len(working) <= self.keep_last_messages:
            return messages, "(no older context)", len(working)

        retained = working[-self.keep_last_messages :]
        summarized = working[: -self.keep_last_messages]
        summary_text = self._summarize_messages(summarized)
        summary_message = {
            "role": "system",
            "content": "Previous session context summary:\n" + summary_text,
        }
        compacted = [*system_prefix, summary_message, *retained]
        return compacted, summary_text, len(retained)

    def _snip_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reminder_messages: list[int] = []
        for index, message in enumerate(messages):
            role = str(message.get("role", ""))
            content = str(message.get("content", "")).strip()
            if role == "system" and content.startswith("Reminder: "):
                reminder_messages.append(index)

        keep_reminder_indexes = set(reminder_messages[-1:]) if reminder_messages else set()
        filtered: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            role = str(message.get("role", ""))
            content = str(message.get("content", "")).strip()
            tool_calls = message.get("tool_calls")
            if role == "system" and index in reminder_messages and index not in keep_reminder_indexes:
                continue
            if role == "tool" and not content:
                continue
            if role == "assistant" and not content and not tool_calls:
                continue
            filtered.append(message)
        return filtered

    def _collapse_context(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return messages
        first_system_index = next((idx for idx, item in enumerate(messages) if item.get("role") == "system"), None)
        if first_system_index is None:
            return messages

        base = dict(messages[first_system_index])
        dynamic_notes: list[str] = []
        remaining: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            if index == first_system_index:
                continue
            if message.get("role") == "system":
                content = str(message.get("content", "")).strip()
                if content:
                    dynamic_notes.append(content)
                continue
            remaining.append(message)

        if not dynamic_notes:
            return messages

        note_lines = []
        for content in dynamic_notes[:8]:
            flattened = content.replace("\n", " ").strip()
            if len(flattened) > self.summary_line_chars:
                flattened = flattened[: self.summary_line_chars - 3] + "..."
            note_lines.append(f"- {flattened}")
        merged = {
            "role": "system",
            "content": "Dynamic session notes:\n" + "\n".join(note_lines),
        }
        return [base, merged, *remaining]

    def _clear_old_tool_results(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        if self.keep_recent_tool_results <= 0:
            return messages, 0

        tool_indexes = [index for index, item in enumerate(messages) if item.get("role") == "tool"]
        if len(tool_indexes) <= self.keep_recent_tool_results:
            return messages, 0

        keep = set(tool_indexes[-self.keep_recent_tool_results :])
        updated = [dict(item) for item in messages]
        cleared = 0
        for index in tool_indexes:
            if index in keep:
                continue
            updated[index]["content"] = "[tool result cleared to free context budget]"
            cleared += 1

        advisory_prefix = "Old tool results were cleared from context"
        has_advisory = any(
            item.get("role") == "system" and str(item.get("content", "")).startswith(advisory_prefix)
            for item in updated
        )
        if not has_advisory:
            advisory = {
                "role": "system",
                "content": (
                    "Old tool results were cleared from context to free up space. "
                    f"The {self.keep_recent_tool_results} most recent tool results are kept."
                ),
            }
            insert_at = 0
            while insert_at < len(updated) and updated[insert_at].get("role") == "system":
                insert_at += 1
            updated.insert(insert_at, advisory)
        return updated, cleared

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
