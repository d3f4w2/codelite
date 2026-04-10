from __future__ import annotations

import hashlib
import json
import random
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from codelite.config import RuntimeConfig
from codelite.core.events import EventBus
from codelite.storage.events import RuntimeLayout, utc_now


DeliveryHandler = Callable[[dict[str, Any]], dict[str, Any] | str | None]


@dataclass(frozen=True)
class DeliveryItem:
    delivery_id: str
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    created_at: str
    updated_at: str
    next_attempt_at: str
    last_error: str = ""
    last_result: dict[str, Any] | None = None
    team_id: str = ""
    kind_priority: int = 0
    claimed_by: str = ""
    claimed_at: str = ""
    claim_expires_at: str = ""
    started_at: str = ""
    finished_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DeliveryItem:
        return cls(
            delivery_id=str(payload["delivery_id"]),
            kind=str(payload["kind"]),
            payload=dict(payload.get("payload") or {}),
            status=str(payload.get("status", "pending")),
            attempts=int(payload.get("attempts", 0)),
            max_attempts=int(payload.get("max_attempts", 3)),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            next_attempt_at=str(payload["next_attempt_at"]),
            last_error=str(payload.get("last_error", "")),
            last_result=payload.get("last_result"),
            team_id=str(payload.get("team_id", "")),
            kind_priority=int(payload.get("kind_priority", 0)),
            claimed_by=str(payload.get("claimed_by", "")),
            claimed_at=str(payload.get("claimed_at", "")),
            claim_expires_at=str(payload.get("claim_expires_at", "")),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeliveryQueue:
    def __init__(
        self,
        layout: RuntimeLayout,
        runtime_config: RuntimeConfig,
        event_bus: EventBus | None = None,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.event_bus = event_bus
        self.max_attempts = runtime_config.delivery_max_attempts
        self.backoff_base_sec = runtime_config.delivery_backoff_base_sec
        self.claim_ttl_sec = max(5, int(getattr(runtime_config, "dispatcher_claim_ttl_sec", 120)))
        self.dispatcher_global_workers = max(1, int(getattr(runtime_config, "dispatcher_global_workers", 8)))
        self.dispatcher_subagent_reserved_workers = max(
            0, int(getattr(runtime_config, "dispatcher_subagent_reserved_workers", 5))
        )
        self.dispatcher_background_reserved_workers = max(
            0, int(getattr(runtime_config, "dispatcher_background_reserved_workers", 2))
        )
        self.dispatcher_team_default_limit = max(
            1, int(getattr(runtime_config, "dispatcher_team_default_limit", 3))
        )
        self._lock = threading.RLock()
        self.recover_pending()

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
        team_id: str | None = None,
        kind_priority: int = 0,
    ) -> DeliveryItem:
        with self._lock:
            now = utc_now()
            delivery_id = uuid.uuid4().hex
            item = DeliveryItem(
                delivery_id=delivery_id,
                kind=kind,
                payload=dict(payload),
                status="pending",
                attempts=0,
                max_attempts=max_attempts or self.max_attempts,
                created_at=now,
                updated_at=now,
                next_attempt_at=now,
                team_id=(team_id or ""),
                kind_priority=int(kind_priority),
            )
            self._write_item(self.layout.delivery_wal_dir / f"{delivery_id}.json", item)
            self._write_item(self.layout.delivery_pending_dir / f"{delivery_id}.json", item)
            if self.event_bus is not None:
                self.event_bus.emit(
                    "delivery_enqueued",
                    {"delivery_id": delivery_id, "kind": kind, "team_id": item.team_id},
                )
            return item

    def recover_pending(self) -> list[str]:
        with self._lock:
            recovered = self._recover_missing_pending_locked()
            reclaimed = self._requeue_expired_claims_locked()
            merged = list(dict.fromkeys(recovered + reclaimed))
            return merged

    def status(self) -> dict[str, Any]:
        with self._lock:
            pending_all = self._read_items(self.layout.delivery_pending_dir)
            pending = [item for item in pending_all if item.status == "pending"]
            running = [item for item in pending_all if item.status == "running"]
            failed = self._read_items(self.layout.delivery_failed_dir)
            done = self._read_items(self.layout.delivery_done_dir)
            return {
                "generated_at": utc_now(),
                "wal_count": len(list(self.layout.delivery_wal_dir.glob("*.json"))),
                "pending_count": len(pending),
                "running_count": len(running),
                "failed_count": len(failed),
                "done_count": len(done),
                "claim_ttl_sec": self.claim_ttl_sec,
                "pending": [item.to_dict() for item in pending_all],
                "failed": [item.to_dict() for item in failed],
                "done": [item.to_dict() for item in done],
            }

    def process_one(self, handlers: dict[str, DeliveryHandler]) -> dict[str, Any] | None:
        return self._process_one(handlers)

    def process_one_for_kinds(
        self,
        handlers: dict[str, DeliveryHandler],
        *,
        allowed_kinds: set[str],
    ) -> dict[str, Any] | None:
        return self._process_one(handlers, allowed_kinds=allowed_kinds)

    def _process_one(
        self,
        handlers: dict[str, DeliveryHandler],
        *,
        allowed_kinds: set[str] | None = None,
    ) -> dict[str, Any] | None:
        item = self.claim_one(
            allowed_kinds=allowed_kinds,
            worker_id="sync",
        )
        if item is None:
            return None
        handler = handlers.get(item.kind)
        if handler is None:
            message = f"no delivery handler registered for kind `{item.kind}`"
            updated = self.fail_claim(item, message)
            return {"delivery_id": item.delivery_id, "status": updated.status, "last_error": updated.last_error}

        try:
            result = handler(item.payload)
        except Exception as exc:
            updated = self.fail_claim(item, str(exc))
            return {"delivery_id": item.delivery_id, "status": updated.status, "last_error": updated.last_error}

        completed = self.complete_claim(item, result=result)
        return {
            "delivery_id": item.delivery_id,
            "kind": item.kind,
            "team_id": item.team_id,
            "status": "done",
            "result": completed.last_result,
        }

    def process_all(self, handlers: dict[str, DeliveryHandler], *, max_items: int | None = None) -> list[dict[str, Any]]:
        return self._process_all(handlers, max_items=max_items)

    def process_all_for_kinds(
        self,
        handlers: dict[str, DeliveryHandler],
        *,
        allowed_kinds: set[str],
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._process_all(handlers, allowed_kinds=allowed_kinds, max_items=max_items)

    def _process_all(
        self,
        handlers: dict[str, DeliveryHandler],
        *,
        allowed_kinds: set[str] | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        limit = max_items or 100
        for _ in range(limit):
            result = self._process_one(handlers, allowed_kinds=allowed_kinds)
            if result is None:
                break
            results.append(result)
        return results

    def claim_one(
        self,
        *,
        allowed_kinds: set[str] | None = None,
        worker_id: str,
        exclude_team_ids: set[str] | None = None,
    ) -> DeliveryItem | None:
        with self._lock:
            self._requeue_expired_claims_locked()
            due_items = self._due_pending_items_locked(statuses={"pending"})
            if allowed_kinds is not None:
                due_items = [item for item in due_items if item.kind in allowed_kinds]
            if exclude_team_ids:
                due_items = [item for item in due_items if item.team_id not in exclude_team_ids]
            if not due_items:
                return None
            item = due_items[0]
            now = utc_now()
            claim_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=self.claim_ttl_sec)
            ).isoformat()
            claimed = DeliveryItem(
                **{
                    **item.to_dict(),
                    "status": "running",
                    "updated_at": now,
                    "claimed_by": worker_id,
                    "claimed_at": now,
                    "claim_expires_at": claim_expires_at,
                    "started_at": item.started_at or now,
                }
            )
            self._write_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json", claimed)
            self._write_item(self.layout.delivery_wal_dir / f"{item.delivery_id}.json", claimed)
            if self.event_bus is not None:
                self.event_bus.emit(
                    "delivery_claimed",
                    {"delivery_id": item.delivery_id, "kind": item.kind, "worker_id": worker_id},
                )
            return claimed

    def defer_claim(
        self,
        item: DeliveryItem,
        *,
        delay_sec: float = 0.25,
        reason: str = "",
    ) -> DeliveryItem:
        with self._lock:
            current = self._read_pending_item_locked(item.delivery_id)
            if current is None:
                return item
            next_attempt = datetime.now(timezone.utc) + timedelta(seconds=max(delay_sec, 0.0))
            deferred = DeliveryItem(
                **{
                    **current.to_dict(),
                    "status": "pending",
                    "updated_at": utc_now(),
                    "next_attempt_at": next_attempt.isoformat(),
                    "claimed_by": "",
                    "claimed_at": "",
                    "claim_expires_at": "",
                    "last_error": reason or current.last_error,
                }
            )
            self._write_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json", deferred)
            self._write_item(self.layout.delivery_wal_dir / f"{item.delivery_id}.json", deferred)
            return deferred

    def complete_claim(self, item: DeliveryItem, *, result: dict[str, Any] | str | None) -> DeliveryItem:
        with self._lock:
            current = self._require_pending_item_locked(item.delivery_id)
            if item.claimed_by and current.claimed_by and current.claimed_by != item.claimed_by:
                raise RuntimeError(
                    f"delivery claim mismatch for `{item.delivery_id}`: `{item.claimed_by}` != `{current.claimed_by}`"
                )
            completed = DeliveryItem(
                **{
                    **current.to_dict(),
                    "status": "done",
                    "updated_at": utc_now(),
                    "finished_at": utc_now(),
                    "last_error": "",
                    "last_result": result if isinstance(result, dict) else {"value": result},
                    "claimed_by": "",
                    "claimed_at": "",
                    "claim_expires_at": "",
                }
            )
            self._delete_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json")
            self._write_item(self.layout.delivery_done_dir / f"{item.delivery_id}.json", completed)
            self._write_item(self.layout.delivery_wal_dir / f"{item.delivery_id}.json", completed)
            if self.event_bus is not None:
                self.event_bus.emit(
                    "delivery_attempt_succeeded",
                    {"delivery_id": item.delivery_id, "kind": item.kind},
                )
            return completed

    def fail_claim(self, item: DeliveryItem, error: str) -> DeliveryItem:
        with self._lock:
            current = self._read_pending_item_locked(item.delivery_id)
            if current is None:
                return item
            attempts = current.attempts + 1
            if attempts >= current.max_attempts:
                failed = DeliveryItem(
                    **{
                        **current.to_dict(),
                        "status": "failed",
                        "attempts": attempts,
                        "updated_at": utc_now(),
                        "finished_at": utc_now(),
                        "last_error": error,
                        "claimed_by": "",
                        "claimed_at": "",
                        "claim_expires_at": "",
                    }
                )
                self._delete_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json")
                self._write_item(self.layout.delivery_failed_dir / f"{item.delivery_id}.json", failed)
                self._write_item(self.layout.delivery_wal_dir / f"{item.delivery_id}.json", failed)
                if self.event_bus is not None:
                    self.event_bus.emit(
                        "delivery_attempt_failed",
                        {
                            "delivery_id": item.delivery_id,
                            "kind": item.kind,
                            "attempts": failed.attempts,
                            "error": error,
                        },
                    )
                return failed

            backoff = self._backoff_seconds(item.delivery_id, attempts)
            next_attempt = datetime.now(timezone.utc) + timedelta(seconds=backoff)
            pending = DeliveryItem(
                **{
                    **current.to_dict(),
                    "status": "pending",
                    "attempts": attempts,
                    "updated_at": utc_now(),
                    "next_attempt_at": next_attempt.isoformat(),
                    "last_error": error,
                    "claimed_by": "",
                    "claimed_at": "",
                    "claim_expires_at": "",
                }
            )
            self._write_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json", pending)
            self._write_item(self.layout.delivery_wal_dir / f"{item.delivery_id}.json", pending)
            if self.event_bus is not None:
                self.event_bus.emit(
                    "delivery_attempt_failed",
                    {
                        "delivery_id": item.delivery_id,
                        "kind": item.kind,
                        "attempts": pending.attempts,
                        "error": error,
                    },
                )
            return pending

    def _backoff_seconds(self, delivery_id: str, attempts: int) -> int:
        seed = int(hashlib.sha1(delivery_id.encode("utf-8")).hexdigest()[:8], 16)
        jitter = random.Random(seed + attempts).randint(0, self.backoff_base_sec)
        return self.backoff_base_sec * (2 ** max(attempts - 1, 0)) + jitter

    def _recover_missing_pending_locked(self) -> list[str]:
        recovered: list[str] = []
        done_ids = {path.stem for path in self.layout.delivery_done_dir.glob("*.json")}
        failed_ids = {path.stem for path in self.layout.delivery_failed_dir.glob("*.json")}
        pending_ids = {path.stem for path in self.layout.delivery_pending_dir.glob("*.json")}
        for wal_path in self.layout.delivery_wal_dir.glob("*.json"):
            delivery_id = wal_path.stem
            if delivery_id in done_ids or delivery_id in failed_ids or delivery_id in pending_ids:
                continue
            item = self._read_item(wal_path)
            restored = DeliveryItem(
                **{
                    **item.to_dict(),
                    "status": "pending",
                    "updated_at": utc_now(),
                    "next_attempt_at": utc_now(),
                    "claimed_by": "",
                    "claimed_at": "",
                    "claim_expires_at": "",
                }
            )
            self._write_item(self.layout.delivery_pending_dir / f"{delivery_id}.json", restored)
            recovered.append(delivery_id)
        return recovered

    def _requeue_expired_claims_locked(self) -> list[str]:
        now = datetime.now(timezone.utc)
        reclaimed: list[str] = []
        for item in self._read_items(self.layout.delivery_pending_dir):
            if item.status != "running" or not item.claim_expires_at:
                continue
            expires = datetime.fromisoformat(item.claim_expires_at).astimezone(timezone.utc)
            if expires > now:
                continue
            restored = DeliveryItem(
                **{
                    **item.to_dict(),
                    "status": "pending",
                    "updated_at": utc_now(),
                    "next_attempt_at": utc_now(),
                    "claimed_by": "",
                    "claimed_at": "",
                    "claim_expires_at": "",
                    "last_error": item.last_error or "claim expired and task was re-queued",
                }
            )
            self._write_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json", restored)
            self._write_item(self.layout.delivery_wal_dir / f"{item.delivery_id}.json", restored)
            reclaimed.append(item.delivery_id)
            if self.event_bus is not None:
                self.event_bus.emit(
                    "delivery_claim_recovered",
                    {"delivery_id": item.delivery_id, "kind": item.kind},
                )
        return reclaimed

    def _due_pending_items_locked(self, *, statuses: set[str]) -> list[DeliveryItem]:
        now = datetime.now(timezone.utc)
        items = [
            item
            for item in self._read_items(self.layout.delivery_pending_dir)
            if item.status in statuses
            and datetime.fromisoformat(item.next_attempt_at).astimezone(timezone.utc) <= now
        ]
        return sorted(
            items,
            key=lambda item: (
                -int(item.kind_priority),
                item.next_attempt_at,
                item.created_at,
                item.delivery_id,
            ),
        )

    def _read_pending_item_locked(self, delivery_id: str) -> DeliveryItem | None:
        path = self.layout.delivery_pending_dir / f"{delivery_id}.json"
        if not path.exists():
            return None
        return self._read_item(path)

    def _require_pending_item_locked(self, delivery_id: str) -> DeliveryItem:
        item = self._read_pending_item_locked(delivery_id)
        if item is None:
            raise RuntimeError(f"delivery item `{delivery_id}` is not pending")
        return item

    @staticmethod
    def _read_item(path: Path) -> DeliveryItem:
        with path.open("r", encoding="utf-8") as handle:
            return DeliveryItem.from_dict(json.load(handle))

    def _read_items(self, directory: Path) -> list[DeliveryItem]:
        return [self._read_item(path) for path in sorted(directory.glob("*.json"))]

    @staticmethod
    def _write_item(path: Path, item: DeliveryItem) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(item.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    @staticmethod
    def _delete_item(path: Path) -> None:
        if path.exists():
            path.unlink()
