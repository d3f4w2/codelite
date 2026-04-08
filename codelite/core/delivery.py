from __future__ import annotations

import hashlib
import json
import random
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
        self.recover_pending()

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        max_attempts: int | None = None,
    ) -> DeliveryItem:
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
        )
        self._write_item(self.layout.delivery_wal_dir / f"{delivery_id}.json", item)
        self._write_item(self.layout.delivery_pending_dir / f"{delivery_id}.json", item)
        if self.event_bus is not None:
            self.event_bus.emit("delivery_enqueued", {"delivery_id": delivery_id, "kind": kind})
        return item

    def recover_pending(self) -> list[str]:
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
                }
            )
            self._write_item(self.layout.delivery_pending_dir / f"{delivery_id}.json", restored)
            recovered.append(delivery_id)
        return recovered

    def status(self) -> dict[str, Any]:
        pending = self._read_items(self.layout.delivery_pending_dir)
        failed = self._read_items(self.layout.delivery_failed_dir)
        done = self._read_items(self.layout.delivery_done_dir)
        return {
            "generated_at": utc_now(),
            "wal_count": len(list(self.layout.delivery_wal_dir.glob("*.json"))),
            "pending_count": len(pending),
            "failed_count": len(failed),
            "done_count": len(done),
            "pending": [item.to_dict() for item in pending],
            "failed": [item.to_dict() for item in failed],
            "done": [item.to_dict() for item in done],
        }

    def process_one(self, handlers: dict[str, DeliveryHandler]) -> dict[str, Any] | None:
        due_items = self._due_pending_items()
        if not due_items:
            return None
        item = due_items[0]
        handler = handlers.get(item.kind)
        if handler is None:
            raise RuntimeError(f"no delivery handler registered for kind `{item.kind}`")

        try:
            result = handler(item.payload)
        except Exception as exc:
            updated = self._mark_retry(item, str(exc))
            if self.event_bus is not None:
                self.event_bus.emit(
                    "delivery_attempt_failed",
                    {"delivery_id": item.delivery_id, "kind": item.kind, "attempts": updated.attempts, "error": str(exc)},
                )
            return {"delivery_id": item.delivery_id, "status": updated.status, "last_error": updated.last_error}

        completed = DeliveryItem(
            **{
                **item.to_dict(),
                "status": "done",
                "updated_at": utc_now(),
                "last_result": result if isinstance(result, dict) else {"value": result},
            }
        )
        self._delete_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json")
        self._write_item(self.layout.delivery_done_dir / f"{item.delivery_id}.json", completed)
        if self.event_bus is not None:
            self.event_bus.emit("delivery_attempt_succeeded", {"delivery_id": item.delivery_id, "kind": item.kind})
        return {"delivery_id": item.delivery_id, "status": "done", "result": completed.last_result}

    def process_all(self, handlers: dict[str, DeliveryHandler], *, max_items: int | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        limit = max_items or 100
        for _ in range(limit):
            result = self.process_one(handlers)
            if result is None:
                break
            results.append(result)
        return results

    def _mark_retry(self, item: DeliveryItem, error: str) -> DeliveryItem:
        attempts = item.attempts + 1
        if attempts >= item.max_attempts:
            failed = DeliveryItem(
                **{
                    **item.to_dict(),
                    "status": "failed",
                    "attempts": attempts,
                    "updated_at": utc_now(),
                    "last_error": error,
                }
            )
            self._delete_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json")
            self._write_item(self.layout.delivery_failed_dir / f"{item.delivery_id}.json", failed)
            return failed

        backoff = self._backoff_seconds(item.delivery_id, attempts)
        next_attempt = datetime.now(timezone.utc) + timedelta(seconds=backoff)
        pending = DeliveryItem(
            **{
                **item.to_dict(),
                "status": "pending",
                "attempts": attempts,
                "updated_at": utc_now(),
                "next_attempt_at": next_attempt.isoformat(),
                "last_error": error,
            }
        )
        self._write_item(self.layout.delivery_pending_dir / f"{item.delivery_id}.json", pending)
        return pending

    def _due_pending_items(self) -> list[DeliveryItem]:
        now = datetime.now(timezone.utc)
        items = [
            item
            for item in self._read_items(self.layout.delivery_pending_dir)
            if datetime.fromisoformat(item.next_attempt_at).astimezone(timezone.utc) <= now
        ]
        return sorted(items, key=lambda item: (item.next_attempt_at, item.created_at, item.delivery_id))

    def _backoff_seconds(self, delivery_id: str, attempts: int) -> int:
        seed = int(hashlib.sha1(delivery_id.encode("utf-8")).hexdigest()[:8], 16)
        jitter = random.Random(seed + attempts).randint(0, self.backoff_base_sec)
        return self.backoff_base_sec * (2 ** max(attempts - 1, 0)) + jitter

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
