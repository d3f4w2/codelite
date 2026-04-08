from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelite.core.context import ContextCompact
from codelite.core.events import EventBus
from codelite.core.heartbeat import HeartService
from codelite.core.worktree import WorktreeManager
from codelite.storage.events import RuntimeLayout, utc_now
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import TaskStore


@dataclass(frozen=True)
class ReconcileResult:
    expired_task_ids: list[str]
    compacted_sessions: list[str]
    cleaned_orphan_worktrees: list[str]
    metrics_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "expired_task_ids": self.expired_task_ids,
            "compacted_sessions": self.compacted_sessions,
            "cleaned_orphan_worktrees": self.cleaned_orphan_worktrees,
            "metrics_path": self.metrics_path,
        }


class Reconciler:
    def __init__(
        self,
        *,
        layout: RuntimeLayout,
        session_store: SessionStore,
        task_store: TaskStore,
        context_compact: ContextCompact,
        heart_service: HeartService,
        worktree_manager: WorktreeManager | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.layout = layout
        self.session_store = session_store
        self.task_store = task_store
        self.context_compact = context_compact
        self.heart_service = heart_service
        self.worktree_manager = worktree_manager
        self.event_bus = event_bus

    def reconcile_expired_leases(self) -> list[str]:
        reconciled = self.task_store.reconcile_expired_leases()
        task_ids = [task.task_id for task in reconciled]
        if self.event_bus is not None:
            self.event_bus.emit(
                "task_reconcile_finished",
                {
                    "task_count": len(task_ids),
                    "task_ids": task_ids,
                },
            )
        return task_ids

    def compact_sessions(self) -> list[str]:
        compacted: list[str] = []
        for path in sorted(self.layout.sessions_dir.glob("*.jsonl")):
            session_id = path.stem
            messages = self.session_store.load_messages(session_id)
            prepared = self.context_compact.prepare(session_id, messages)
            if prepared is not messages:
                compacted.append(session_id)
        return compacted

    def cleanup_orphan_worktrees(self) -> list[str]:
        if self.worktree_manager is None:
            return []
        cleaned: list[str] = []
        for record in self.worktree_manager.list_managed():
            if record.attached or record.path_exists:
                continue
            metadata_path = self.worktree_manager.record_path(record.task_id)
            if metadata_path.exists():
                metadata_path.unlink()
                cleaned.append(record.task_id)
        return cleaned

    def rollup_metrics(self) -> Path:
        status_counts: dict[str, int] = {}
        for task in self.task_store.list_tasks():
            status_counts[task.status.value] = status_counts.get(task.status.value, 0) + 1

        payload = {
            "generated_at": utc_now(),
            "workspace_root": str(self.layout.workspace_root),
            "event_count": sum(1 for _ in self._iter_lines(self.layout.events_path)),
            "session_count": len(list(self.layout.sessions_dir.glob("*.jsonl"))),
            "task_counts": status_counts,
            "todo_snapshot_count": len(list(self.layout.todos_dir.glob("*.json"))),
            "context_snapshot_count": len(list(self.layout.context_dir.glob("*.json"))),
            "managed_worktree_count": len(self.worktree_manager.list_managed()) if self.worktree_manager else 0,
            "heart": self.heart_service.status(),
        }
        path = self.layout.metrics_dir / "rollup-latest.json"
        self._write_json(path, payload)
        if self.event_bus is not None:
            self.event_bus.emit(
                "metrics_rolled_up",
                {
                    "path": str(path),
                    "task_counts": status_counts,
                },
            )
        return path

    def run_cycle(self) -> ReconcileResult:
        expired_task_ids = self.reconcile_expired_leases()
        compacted_sessions = self.compact_sessions()
        cleaned_orphan_worktrees = self.cleanup_orphan_worktrees()
        metrics_path = self.rollup_metrics()
        return ReconcileResult(
            expired_task_ids=expired_task_ids,
            compacted_sessions=compacted_sessions,
            cleaned_orphan_worktrees=cleaned_orphan_worktrees,
            metrics_path=str(metrics_path),
        )

    @staticmethod
    def _iter_lines(path: Path) -> list[str]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            return [line for line in handle if line.strip()]

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
