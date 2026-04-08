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

    @property
    def todos_dir(self) -> Path:
        return self.runtime_dir / "todos"

    @property
    def context_dir(self) -> Path:
        return self.runtime_dir / "context"

    @property
    def hearts_path(self) -> Path:
        return self.runtime_dir / "hearts.jsonl"

    @property
    def metrics_dir(self) -> Path:
        return self.runtime_dir / "metrics"

    @property
    def cron_dir(self) -> Path:
        return self.runtime_dir / "cron"

    @property
    def watchdog_dir(self) -> Path:
        return self.runtime_dir / "watchdog"

    @property
    def lanes_dir(self) -> Path:
        return self.runtime_dir / "lanes"

    @property
    def delivery_queue_dir(self) -> Path:
        return self.runtime_dir / "delivery-queue"

    @property
    def delivery_wal_dir(self) -> Path:
        return self.delivery_queue_dir / "wal"

    @property
    def delivery_pending_dir(self) -> Path:
        return self.delivery_queue_dir / "pending"

    @property
    def delivery_done_dir(self) -> Path:
        return self.delivery_queue_dir / "done"

    @property
    def delivery_failed_dir(self) -> Path:
        return self.delivery_queue_dir / "failed"

    @property
    def audit_path(self) -> Path:
        return self.runtime_dir / "audit.jsonl"

    @property
    def hooks_dir(self) -> Path:
        return self.runtime_dir / "hooks"

    @property
    def hook_events_path(self) -> Path:
        return self.hooks_dir / "events.jsonl"

    @property
    def hook_failures_path(self) -> Path:
        return self.hooks_dir / "validation-failures.jsonl"

    @property
    def memory_dir(self) -> Path:
        return self.runtime_dir / "memory"

    @property
    def memory_ledger_path(self) -> Path:
        return self.memory_dir / "ledger.jsonl"

    @property
    def memory_views_dir(self) -> Path:
        return self.memory_dir / "views"

    @property
    def background_dir(self) -> Path:
        return self.runtime_dir / "background"

    @property
    def background_results_dir(self) -> Path:
        return self.background_dir / "results"

    @property
    def critic_dir(self) -> Path:
        return self.runtime_dir / "critic"

    @property
    def critic_failures_path(self) -> Path:
        return self.critic_dir / "failures.jsonl"

    @property
    def critic_rules_path(self) -> Path:
        return self.critic_dir / "rules.json"

    def ensure(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.leases_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_index_dir.mkdir(parents=True, exist_ok=True)
        self.todos_dir.mkdir(parents=True, exist_ok=True)
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.cron_dir.mkdir(parents=True, exist_ok=True)
        self.watchdog_dir.mkdir(parents=True, exist_ok=True)
        self.lanes_dir.mkdir(parents=True, exist_ok=True)
        self.delivery_queue_dir.mkdir(parents=True, exist_ok=True)
        self.delivery_wal_dir.mkdir(parents=True, exist_ok=True)
        self.delivery_pending_dir.mkdir(parents=True, exist_ok=True)
        self.delivery_done_dir.mkdir(parents=True, exist_ok=True)
        self.delivery_failed_dir.mkdir(parents=True, exist_ok=True)
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_views_dir.mkdir(parents=True, exist_ok=True)
        self.background_dir.mkdir(parents=True, exist_ok=True)
        self.background_results_dir.mkdir(parents=True, exist_ok=True)
        self.critic_dir.mkdir(parents=True, exist_ok=True)


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

    def latest_session_ids(self, limit: int = 1, *, include_system: bool = False) -> list[str]:
        session_files = sorted(
            self.layout.sessions_dir.glob("*.jsonl"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        session_ids: list[str] = []
        for path in session_files:
            session_id = path.stem
            if not include_system and session_id.startswith("system"):
                continue
            session_ids.append(session_id)
            if len(session_ids) >= limit:
                break
        return session_ids

    def session_path(self, session_id: str) -> Path:
        return self.layout.sessions_dir / f"{session_id}.jsonl"

    @staticmethod
    def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str))
            handle.write("\n")
