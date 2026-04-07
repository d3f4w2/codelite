from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import LeaseConflictError, TaskStateError, TaskStatus, TaskStore


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"tasks-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def build_store(workspace_dir: Path) -> TaskStore:
    return TaskStore(RuntimeLayout(workspace_dir))


def test_task_store_creates_pending_tasks(workspace_dir: Path) -> None:
    store = build_store(workspace_dir)

    task = store.create_task("demo-task", title="Demo task", metadata={"lane": "main"})

    assert task.status is TaskStatus.PENDING
    assert task.metadata == {"lane": "main"}
    assert store.task_path("demo-task").exists()
    assert store.get_task("demo-task") == task


def test_task_store_acquires_and_releases_leases(workspace_dir: Path) -> None:
    store = build_store(workspace_dir)

    lease = store.acquire_lease("demo-task", owner="agent/main")
    leased_task = store.get_task("demo-task")
    assert lease.owner == "agent/main"
    assert leased_task is not None
    assert leased_task.status is TaskStatus.LEASED
    assert leased_task.lease_id == lease.lease_id
    assert store.lease_path("demo-task").exists()

    running = store.start_task("demo-task", lease_id=lease.lease_id)
    assert running.status is TaskStatus.RUNNING

    done = store.complete_task("demo-task", lease_id=lease.lease_id)
    assert done.status is TaskStatus.DONE
    assert done.lease_id is None
    assert not store.lease_path("demo-task").exists()


def test_task_store_rejects_active_lease_conflicts(workspace_dir: Path) -> None:
    store = build_store(workspace_dir)
    store.acquire_lease("demo-task", owner="agent/a")

    with pytest.raises(LeaseConflictError, match="already leased"):
        store.acquire_lease("demo-task", owner="agent/b")


def test_task_store_can_renew_active_lease(workspace_dir: Path) -> None:
    store = build_store(workspace_dir)
    lease = store.acquire_lease("demo-task", owner="agent/a", ttl_seconds=30)

    renewed = store.renew_lease("demo-task", lease_id=lease.lease_id, ttl_seconds=90)

    assert renewed.lease_id == lease.lease_id
    assert renewed.expires_at != lease.expires_at
    task = store.get_task("demo-task")
    assert task is not None
    assert task.lease_expires_at == renewed.expires_at


def test_task_store_reconciles_expired_leases(workspace_dir: Path) -> None:
    store = build_store(workspace_dir)
    expired_lease = store.acquire_lease("demo-task", owner="agent/a", ttl_seconds=30)
    store.start_task("demo-task", lease_id=expired_lease.lease_id)

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    store._write_json(  # type: ignore[attr-defined]
        store.lease_path("demo-task"),
        {
            "task_id": "demo-task",
            "lease_id": expired_lease.lease_id,
            "owner": expired_lease.owner,
            "acquired_at": expired_lease.acquired_at,
            "expires_at": expired_at.isoformat(),
            "ttl_seconds": expired_lease.ttl_seconds,
        },
    )

    reconciled = store.reconcile_expired_leases(now=datetime.now(timezone.utc))

    assert len(reconciled) == 1
    task = store.get_task("demo-task")
    assert task is not None
    assert task.status is TaskStatus.BLOCKED
    assert task.blocked_reason == "lease expired"
    assert not store.lease_path("demo-task").exists()


def test_task_store_rejects_invalid_transitions(workspace_dir: Path) -> None:
    store = build_store(workspace_dir)
    lease = store.acquire_lease("demo-task", owner="agent/a")
    store.start_task("demo-task", lease_id=lease.lease_id)

    with pytest.raises(TaskStateError, match="already"):
        store.start_task("demo-task", lease_id=lease.lease_id)
