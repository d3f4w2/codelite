from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimeLayout:
    workspace_root: Path

    @property
    def runtime_dir(self) -> Path:
        return self.workspace_root / "runtime"

    @property
    def events_path(self) -> Path:
        return self.runtime_dir / "events.jsonl"

    @property
    def sessions_dir(self) -> Path:
        return self.runtime_dir / "sessions"

    @property
    def tasks_dir(self) -> Path:
        return self.runtime_dir / "tasks"

    @property
    def leases_dir(self) -> Path:
        return self.runtime_dir / "leases"

    @property
    def worktrees_dir(self) -> Path:
        return self.runtime_dir / "worktrees"

    @property
    def worktrees_index_dir(self) -> Path:
        return self.worktrees_dir / ".index"

    def ensure(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.leases_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_index_dir.mkdir(parents=True, exist_ok=True)


class EventStore:
    def __init__(self, layout: RuntimeLayout) -> None:
        self.layout = layout
        self.layout.ensure()

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = {
            "event_id": uuid.uuid4().hex,
            "timestamp_utc": utc_now(),
            "session_id": session_id,
            "event_type": event_type,
            "payload": payload,
        }
        self._append_jsonl(self.layout.events_path, event)
        self._append_jsonl(self.session_path(session_id), event)
        return event

    def read_session_events(self, session_id: str) -> list[dict[str, Any]]:
        path = self.session_path(session_id)
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def latest_session_ids(self, limit: int = 1) -> list[str]:
        session_files = sorted(
            self.layout.sessions_dir.glob("*.jsonl"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        return [path.stem for path in session_files[:limit]]

    def session_path(self, session_id: str) -> Path:
        return self.layout.sessions_dir / f"{session_id}.jsonl"

    @staticmethod
    def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str))
            handle.write("\n")
