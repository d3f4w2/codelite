from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from codelite.storage.events import RuntimeLayout, utc_now


class TaskStateError(RuntimeError):
    pass


class LeaseConflictError(RuntimeError):
    pass


class TaskStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    BLOCKED = "blocked"
    DONE = "done"


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.LEASED, TaskStatus.BLOCKED, TaskStatus.DONE},
    TaskStatus.LEASED: {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.BLOCKED},
    TaskStatus.RUNNING: {TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.DONE},
    TaskStatus.BLOCKED: {TaskStatus.PENDING, TaskStatus.LEASED, TaskStatus.DONE},
    TaskStatus.DONE: {TaskStatus.PENDING, TaskStatus.LEASED},
}


@dataclass(frozen=True)
class LeaseRecord:
    task_id: str
    lease_id: str
    owner: str
    acquired_at: str
    expires_at: str
    ttl_seconds: int

    @property
    def expired(self) -> bool:
        return _parse_utc(self.expires_at) <= datetime.now(timezone.utc)


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    title: str
    status: TaskStatus
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    lease_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    blocked_reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        return cls(
            task_id=str(data["task_id"]),
            title=str(data.get("title", "")),
            status=TaskStatus(data["status"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            metadata=dict(data.get("metadata") or {}),
            lease_id=data.get("lease_id"),
            lease_owner=data.get("lease_owner"),
            lease_expires_at=data.get("lease_expires_at"),
            blocked_reason=data.get("blocked_reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


class TaskStore:
    DEFAULT_LEASE_TTL_SEC = 15 * 60

    def __init__(self, layout: RuntimeLayout) -> None:
        self.layout = layout
        self.layout.ensure()

    def create_task(
        self,
        task_id: str,
        *,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        existing = self.get_task(task_id)
        if existing is not None:
            return existing

        now = utc_now()
        task = TaskRecord(
            task_id=task_id,
            title=title,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        self._write_json(self.task_path(task_id), task.to_dict())
        return task

    def get_task(self, task_id: str) -> TaskRecord | None:
        path = self.task_path(task_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return TaskRecord.from_dict(json.load(handle))

    def list_tasks(self) -> list[TaskRecord]:
        tasks: list[TaskRecord] = []
        for path in sorted(self.layout.tasks_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                tasks.append(TaskRecord.from_dict(json.load(handle)))
        return tasks

    def update_metadata(self, task_id: str, metadata_patch: dict[str, Any]) -> TaskRecord:
        task = self._require_task(task_id)
        merged = _merge_dicts(task.metadata, metadata_patch)
        updated = TaskRecord(
            task_id=task.task_id,
            title=task.title,
            status=task.status,
            created_at=task.created_at,
            updated_at=utc_now(),
            metadata=merged,
            lease_id=task.lease_id,
            lease_owner=task.lease_owner,
            lease_expires_at=task.lease_expires_at,
            blocked_reason=task.blocked_reason,
        )
        self._write_json(self.task_path(task_id), updated.to_dict())
        return updated

    def get_lease(self, task_id: str) -> LeaseRecord | None:
        path = self.lease_path(task_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return LeaseRecord(**json.load(handle))

    def acquire_lease(
        self,
        task_id: str,
        *,
        owner: str,
        ttl_seconds: int | None = None,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LeaseRecord:
        ttl = ttl_seconds or self.DEFAULT_LEASE_TTL_SEC
        task = self.create_task(task_id, title=title, metadata=metadata)
        current_lease = self.get_lease(task_id)
        if current_lease and not current_lease.expired:
            raise LeaseConflictError(f"task `{task_id}` already leased by `{current_lease.owner}`")
        if current_lease and current_lease.expired:
            self._delete_path(self.lease_path(task_id))

        lease = self._new_lease(task_id=task_id, owner=owner, ttl_seconds=ttl)
        updated_task = self._transition(
            task,
            TaskStatus.LEASED,
            lease=lease,
            blocked_reason=None,
            allow_same_state=task.status == TaskStatus.LEASED and current_lease is not None,
        )
        self._write_json(self.lease_path(task_id), asdict(lease))
        self._write_json(self.task_path(task_id), updated_task.to_dict())
        return lease

    def renew_lease(self, task_id: str, *, lease_id: str, ttl_seconds: int | None = None) -> LeaseRecord:
        current = self._require_matching_lease(task_id, lease_id)
        ttl = ttl_seconds or current.ttl_seconds
        renewed = self._new_lease(
            task_id=task_id,
            owner=current.owner,
            ttl_seconds=ttl,
            lease_id=current.lease_id,
        )
        task = self._require_task(task_id)
        updated_task = self._transition(task, task.status, lease=renewed, allow_same_state=True)
        self._write_json(self.lease_path(task_id), asdict(renewed))
        self._write_json(self.task_path(task_id), updated_task.to_dict())
        return renewed

    def start_task(self, task_id: str, *, lease_id: str) -> TaskRecord:
        task = self._require_task(task_id)
        lease = self._require_matching_lease(task_id, lease_id)
        updated_task = self._transition(task, TaskStatus.RUNNING, lease=lease)
        self._write_json(self.task_path(task_id), updated_task.to_dict())
        return updated_task

    def release_lease(
        self,
        task_id: str,
        *,
        lease_id: str,
        next_status: TaskStatus = TaskStatus.PENDING,
        blocked_reason: str | None = None,
    ) -> TaskRecord:
        task = self._require_task(task_id)
        self._require_matching_lease(task_id, lease_id)
        updated_task = self._transition(task, next_status, lease=None, blocked_reason=blocked_reason)
        self._delete_path(self.lease_path(task_id))
        self._write_json(self.task_path(task_id), updated_task.to_dict())
        return updated_task

    def block_task(self, task_id: str, *, lease_id: str, reason: str) -> TaskRecord:
        return self.release_lease(
            task_id,
            lease_id=lease_id,
            next_status=TaskStatus.BLOCKED,
            blocked_reason=reason,
        )

    def complete_task(self, task_id: str, *, lease_id: str) -> TaskRecord:
        return self.release_lease(task_id, lease_id=lease_id, next_status=TaskStatus.DONE)

    def list_expired_leases(self, *, now: datetime | None = None) -> list[LeaseRecord]:
        checkpoint = now or datetime.now(timezone.utc)
        expired: list[LeaseRecord] = []
        for path in sorted(self.layout.leases_dir.glob("*.lock")):
            with path.open("r", encoding="utf-8") as handle:
                lease = LeaseRecord(**json.load(handle))
            if _parse_utc(lease.expires_at) <= checkpoint:
                expired.append(lease)
        return expired

    def reconcile_expired_leases(self, *, now: datetime | None = None) -> list[TaskRecord]:
        reconciled: list[TaskRecord] = []
        for lease in self.list_expired_leases(now=now):
            task = self.get_task(lease.task_id)
            if task is None:
                self._delete_path(self.lease_path(lease.task_id))
                continue
            updated_task = self._transition(
                task,
                TaskStatus.BLOCKED,
                lease=None,
                blocked_reason="lease expired",
                allow_same_state=task.status == TaskStatus.BLOCKED,
            )
            self._delete_path(self.lease_path(lease.task_id))
            self._write_json(self.task_path(lease.task_id), updated_task.to_dict())
            reconciled.append(updated_task)
        return reconciled

    def task_path(self, task_id: str) -> Path:
        return self.layout.tasks_dir / f"{_task_key(task_id)}.json"

    def lease_path(self, task_id: str) -> Path:
        return self.layout.leases_dir / f"{_task_key(task_id)}.lock"

    def _require_task(self, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise TaskStateError(f"task `{task_id}` does not exist")
        return task

    def _require_matching_lease(self, task_id: str, lease_id: str) -> LeaseRecord:
        lease = self.get_lease(task_id)
        if lease is None:
            raise LeaseConflictError(f"task `{task_id}` does not have an active lease")
        if lease.lease_id != lease_id:
            raise LeaseConflictError(f"lease mismatch for task `{task_id}`")
        if lease.expired:
            raise LeaseConflictError(f"lease `{lease_id}` for task `{task_id}` has expired")
        return lease

    def _transition(
        self,
        task: TaskRecord,
        new_status: TaskStatus,
        *,
        lease: LeaseRecord | None = None,
        blocked_reason: str | None = None,
        allow_same_state: bool = False,
    ) -> TaskRecord:
        if new_status != task.status:
            allowed = ALLOWED_TRANSITIONS[task.status]
            if new_status not in allowed:
                raise TaskStateError(f"cannot transition task `{task.task_id}` from `{task.status}` to `{new_status}`")
        elif not allow_same_state:
            raise TaskStateError(f"task `{task.task_id}` is already `{task.status}`")

        now = utc_now()
        return TaskRecord(
            task_id=task.task_id,
            title=task.title,
            status=new_status,
            created_at=task.created_at,
            updated_at=now,
            metadata=dict(task.metadata),
            lease_id=lease.lease_id if lease else None,
            lease_owner=lease.owner if lease else None,
            lease_expires_at=lease.expires_at if lease else None,
            blocked_reason=blocked_reason,
        )

    def _new_lease(
        self,
        *,
        task_id: str,
        owner: str,
        ttl_seconds: int,
        lease_id: str | None = None,
        acquired_at: str | None = None,
    ) -> LeaseRecord:
        acquired = acquired_at or utc_now()
        acquired_dt = _parse_utc(acquired)
        expires_at = (acquired_dt + timedelta(seconds=ttl_seconds)).isoformat()
        return LeaseRecord(
            task_id=task_id,
            lease_id=lease_id or uuid.uuid4().hex,
            owner=owner,
            acquired_at=acquired,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
        )

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        for attempt in range(5):
            try:
                tmp_path.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))

    @staticmethod
    def _delete_path(path: Path) -> None:
        if path.exists():
            path.unlink()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _task_key(task_id: str) -> str:
    stripped = task_id.strip()
    if not stripped:
        raise TaskStateError("task_id must not be empty")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stripped).strip("._-") or "task"
    digest = hashlib.sha1(stripped.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


def _merge_dicts(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
