from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codelite.config import RuntimeConfig
from codelite.core.events import EventBus
from codelite.storage.events import RuntimeLayout, utc_now


@dataclass(frozen=True)
class HeartbeatRecord:
    component_id: str
    timestamp: str
    status: str
    queue_depth: int
    active_task_count: int
    last_error: str
    latency_ms_p95: float
    failure_streak: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> HeartbeatRecord:
        return cls(
            component_id=str(payload["component_id"]),
            timestamp=str(payload["timestamp"]),
            status=str(payload.get("status", "green")),
            queue_depth=int(payload.get("queue_depth", 0)),
            active_task_count=int(payload.get("active_task_count", 0)),
            last_error=str(payload.get("last_error", "")),
            latency_ms_p95=float(payload.get("latency_ms_p95", 0.0)),
            failure_streak=int(payload.get("failure_streak", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HeartService:
    DEFAULT_COMPONENTS = (
        "agent_loop",
        "tool_router",
        "todo_manager",
        "context_compact",
        "event_bus",
        "cron_scheduler",
        "worktree_manager",
        "watchdog",
    )

    def __init__(
        self,
        layout: RuntimeLayout,
        runtime_config: RuntimeConfig,
        event_bus: EventBus | None = None,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.event_bus = event_bus
        self.green_window_sec = runtime_config.heart_green_window_sec
        self.yellow_window_sec = runtime_config.heart_yellow_window_sec
        self.red_fail_streak = runtime_config.heart_red_fail_streak

    def beat(
        self,
        component_id: str,
        *,
        status: str = "green",
        queue_depth: int = 0,
        active_task_count: int = 0,
        last_error: str = "",
        latency_ms_p95: float = 0.0,
        failure_streak: int = 0,
        timestamp: str | None = None,
    ) -> HeartbeatRecord:
        record = HeartbeatRecord(
            component_id=component_id,
            timestamp=timestamp or utc_now(),
            status=status,
            queue_depth=queue_depth,
            active_task_count=active_task_count,
            last_error=last_error,
            latency_ms_p95=latency_ms_p95,
            failure_streak=failure_streak,
        )
        self._append_jsonl(self.layout.hearts_path, record.to_dict())
        if self.event_bus is not None:
            self.event_bus.emit_component(
                component_id,
                "heartbeat_recorded",
                {
                    "status": record.status,
                    "queue_depth": record.queue_depth,
                    "active_task_count": record.active_task_count,
                },
            )
        return record

    def latest_records(self) -> dict[str, HeartbeatRecord]:
        if not self.layout.hearts_path.exists():
            return {}
        latest: dict[str, HeartbeatRecord] = {}
        with self.layout.hearts_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                record = HeartbeatRecord.from_dict(json.loads(line))
                latest[record.component_id] = record
        return latest

    def evaluate_status(
        self,
        record: HeartbeatRecord | None,
        *,
        now: datetime | None = None,
    ) -> tuple[str, float | None]:
        if record is None:
            return ("unknown", None)
        current = now or datetime.now(timezone.utc)
        seen_at = datetime.fromisoformat(record.timestamp).astimezone(timezone.utc)
        age_sec = max((current - seen_at).total_seconds(), 0.0)
        if record.failure_streak >= self.red_fail_streak or record.status == "red":
            return ("red", age_sec)
        if age_sec <= self.green_window_sec and record.status == "green":
            return ("green", age_sec)
        if age_sec <= self.yellow_window_sec:
            return ("yellow", age_sec)
        return ("red", age_sec)

    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        records = self.latest_records()
        components = sorted(set(self.DEFAULT_COMPONENTS).union(records))
        payload: list[dict[str, Any]] = []
        for component_id in components:
            record = records.get(component_id)
            component_status, age_sec = self.evaluate_status(record, now=now)
            payload.append(
                {
                    "component_id": component_id,
                    "status": component_status,
                    "last_seen_age_sec": round(age_sec, 3) if age_sec is not None else None,
                    "queue_depth": record.queue_depth if record else 0,
                    "active_task_count": record.active_task_count if record else 0,
                    "latency_ms_p95": record.latency_ms_p95 if record else 0.0,
                    "last_error": record.last_error if record else "",
                    "failure_streak": record.failure_streak if record else 0,
                    "timestamp": record.timestamp if record else None,
                }
            )
        return {
            "generated_at": utc_now(),
            "hearts_path": str(self.layout.hearts_path),
            "green_window_sec": self.green_window_sec,
            "yellow_window_sec": self.yellow_window_sec,
            "red_fail_streak": self.red_fail_streak,
            "components": payload,
        }

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")
