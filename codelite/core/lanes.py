from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from codelite.core.events import EventBus
from codelite.storage.events import RuntimeLayout, utc_now


@dataclass(frozen=True)
class LaneJob:
    job_id: str
    generation: int
    payload: dict[str, Any]
    submitted_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LaneJob:
        return cls(
            job_id=str(payload["job_id"]),
            generation=int(payload["generation"]),
            payload=dict(payload.get("payload") or {}),
            submitted_at=str(payload["submitted_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LaneState:
    name: str
    generation: int
    max_concurrency: int
    queue_depth: int
    active_count: int
    last_job_id: str | None
    last_status: str | None
    last_result_preview: str
    updated_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LaneState:
        return cls(
            name=str(payload["name"]),
            generation=int(payload.get("generation", 1)),
            max_concurrency=int(payload.get("max_concurrency", 1)),
            queue_depth=int(payload.get("queue_depth", 0)),
            active_count=int(payload.get("active_count", 0)),
            last_job_id=payload.get("last_job_id"),
            last_status=payload.get("last_status"),
            last_result_preview=str(payload.get("last_result_preview", "")),
            updated_at=str(payload.get("updated_at", utc_now())),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LaneScheduler:
    DEFAULTS = {
        "main": 1,
        "cron": 1,
        "heartbeat": 1,
    }

    def __init__(self, layout: RuntimeLayout, event_bus: EventBus | None = None) -> None:
        self.layout = layout
        self.layout.ensure()
        self.event_bus = event_bus
        for lane_name, max_concurrency in self.DEFAULTS.items():
            self.register_lane(lane_name, max_concurrency=max_concurrency)

    def register_lane(self, name: str, *, max_concurrency: int = 1) -> LaneState:
        existing = self.get_lane(name)
        if existing is not None:
            return existing
        state = LaneState(
            name=name,
            generation=1,
            max_concurrency=max_concurrency,
            queue_depth=0,
            active_count=0,
            last_job_id=None,
            last_status=None,
            last_result_preview="",
            updated_at=utc_now(),
        )
        self._write_lane(state)
        self._write_queue(name, [])
        return state

    def get_lane(self, name: str) -> LaneState | None:
        path = self._lane_path(name)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return LaneState.from_dict(json.load(handle))

    def enqueue(
        self,
        name: str,
        *,
        job_id: str,
        payload: dict[str, Any] | None = None,
        generation: int | None = None,
    ) -> dict[str, Any]:
        lane = self.register_lane(name, max_concurrency=self.DEFAULTS.get(name, 1))
        effective_generation = generation if generation is not None else lane.generation
        if effective_generation != lane.generation:
            return {
                "accepted": False,
                "reason": "stale_generation",
                "current_generation": lane.generation,
                "job_generation": effective_generation,
            }
        jobs = self._read_queue(name)
        jobs.append(
            LaneJob(
                job_id=job_id,
                generation=effective_generation,
                payload=dict(payload or {}),
                submitted_at=utc_now(),
            )
        )
        updated = LaneState(
            **{
                **lane.to_dict(),
                "queue_depth": len(jobs),
                "updated_at": utc_now(),
            }
        )
        self._write_queue(name, jobs)
        self._write_lane(updated)
        return {"accepted": True, "lane": updated.to_dict(), "job_id": job_id}

    def execute_sync(
        self,
        name: str,
        *,
        job_id: str,
        payload: dict[str, Any] | None = None,
        callback: Callable[[], Any],
        generation: int | None = None,
    ) -> dict[str, Any]:
        enqueue_result = self.enqueue(name, job_id=job_id, payload=payload, generation=generation)
        if not enqueue_result["accepted"]:
            return enqueue_result

        lane = self.register_lane(name, max_concurrency=self.DEFAULTS.get(name, 1))
        jobs = self._read_queue(name)
        if not jobs:
            return {"accepted": False, "reason": "empty_queue"}
        current_job = jobs.pop(0)
        active_lane = LaneState(
            **{
                **lane.to_dict(),
                "queue_depth": len(jobs),
                "active_count": lane.active_count + 1,
                "updated_at": utc_now(),
            }
        )
        self._write_queue(name, jobs)
        self._write_lane(active_lane)
        if self.event_bus is not None:
            self.event_bus.emit("lane_job_started", {"lane": name, "job_id": current_job.job_id})

        try:
            result = callback()
            status = "ok"
            preview = str(result)[:200]
        except Exception as exc:
            status = "error"
            preview = str(exc)[:200]
            raise
        finally:
            latest = self.get_lane(name) or active_lane
            finished = LaneState(
                **{
                    **latest.to_dict(),
                    "queue_depth": len(self._read_queue(name)),
                    "active_count": max(latest.active_count - 1, 0),
                    "last_job_id": current_job.job_id,
                    "last_status": status,
                    "last_result_preview": preview,
                    "updated_at": utc_now(),
                }
            )
            self._write_lane(finished)
            if self.event_bus is not None:
                self.event_bus.emit("lane_job_finished", {"lane": name, "job_id": current_job.job_id, "status": status})

        return {
            "accepted": True,
            "lane": (self.get_lane(name) or active_lane).to_dict(),
            "job_id": current_job.job_id,
            "status": status,
            "result_preview": preview,
            "result": result if status == "ok" else None,
        }

    def bump_generation(self, name: str) -> LaneState:
        lane = self.register_lane(name, max_concurrency=self.DEFAULTS.get(name, 1))
        updated = LaneState(
            **{
                **lane.to_dict(),
                "generation": lane.generation + 1,
                "queue_depth": 0,
                "updated_at": utc_now(),
            }
        )
        self._write_lane(updated)
        self._write_queue(name, [])
        return updated

    def status(self) -> dict[str, Any]:
        lanes: list[dict[str, Any]] = []
        for path in sorted(self.layout.lanes_dir.glob("*.json")):
            if path.name.endswith(".queue.json"):
                continue
            with path.open("r", encoding="utf-8") as handle:
                lane = LaneState.from_dict(json.load(handle))
            lanes.append(lane.to_dict())
        return {
            "generated_at": utc_now(),
            "lanes": lanes,
        }

    def _lane_path(self, name: str) -> Path:
        return self.layout.lanes_dir / f"{name}.json"

    def _queue_path(self, name: str) -> Path:
        return self.layout.lanes_dir / f"{name}.queue.json"

    def _read_queue(self, name: str) -> list[LaneJob]:
        path = self._queue_path(name)
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return [LaneJob.from_dict(item) for item in payload]

    def _write_queue(self, name: str, jobs: list[LaneJob]) -> None:
        path = self._queue_path(name)
        self._write_json(path, [job.to_dict() for job in jobs])

    def _write_lane(self, lane: LaneState) -> None:
        self._write_json(self._lane_path(lane.name), lane.to_dict())

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        for attempt in range(3):
            try:
                tmp_path.replace(path)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05)
