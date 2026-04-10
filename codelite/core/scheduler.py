from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from codelite.core.events import EventBus
from codelite.storage.events import RuntimeLayout, utc_now


JobHandler = Callable[[], dict[str, Any] | list[Any] | str | None]


@dataclass
class ScheduledJob:
    name: str
    schedule: str
    description: str
    handler: JobHandler
    enabled: bool = True
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str = ""
    run_count: int = 0

    def to_dict(self, *, due: bool = False) -> dict[str, Any]:
        return {
            "name": self.name,
            "schedule": self.schedule,
            "description": self.description,
            "enabled": self.enabled,
            "due": due,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "run_count": self.run_count,
        }


class CronScheduler:
    def __init__(
        self,
        layout: RuntimeLayout,
        *,
        event_bus: EventBus | None = None,
        enabled: bool = True,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.event_bus = event_bus
        self.enabled = self._load_scheduler_enabled(default=enabled)
        self.jobs: dict[str, ScheduledJob] = {}
        self._overrides = self._load_overrides()

    def register(
        self,
        name: str,
        schedule: str,
        description: str,
        handler: JobHandler,
        *,
        enabled: bool = True,
    ) -> ScheduledJob:
        override = self._overrides.get(name, {})
        job = ScheduledJob(
            name=name,
            schedule=str(override.get("schedule", schedule)),
            description=str(override.get("description", description)),
            handler=handler,
            enabled=bool(override.get("enabled", enabled)),
        )
        state = self._load_state(name)
        if state is not None:
            job.last_run_at = state.get("last_run_at")
            job.last_status = state.get("last_status")
            job.last_error = str(state.get("last_error", ""))
            job.run_count = int(state.get("run_count", 0))
        self.jobs[name] = job
        return job

    def configure_job(
        self,
        name: str,
        *,
        schedule: str | None = None,
        enabled: bool | None = None,
        description: str | None = None,
    ) -> ScheduledJob:
        if name not in self.jobs:
            raise KeyError(f"unknown cron job: {name}")
        job = self.jobs[name]
        if schedule is not None:
            job.schedule = schedule
        if enabled is not None:
            job.enabled = enabled
        if description is not None and description.strip():
            job.description = description.strip()

        self._overrides[name] = {
            "schedule": job.schedule,
            "enabled": job.enabled,
            "description": job.description,
        }
        self._persist_overrides()
        self._persist_state(job)
        return job

    def set_enabled(self, enabled: bool) -> bool:
        self.enabled = bool(enabled)
        self._persist_scheduler_enabled()
        return self.enabled

    def list_jobs(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        checkpoint = now or datetime.now(timezone.utc)
        return [
            job.to_dict(due=self._is_due(job, checkpoint))
            for job in sorted(self.jobs.values(), key=lambda item: item.name)
        ]

    def run_job(self, name: str, *, now: datetime | None = None) -> dict[str, Any]:
        if name not in self.jobs:
            raise KeyError(f"unknown cron job: {name}")
        checkpoint = now or datetime.now(timezone.utc)
        job = self.jobs[name]
        if not self.enabled or not job.enabled:
            payload = {**job.to_dict(due=False), "result": None}
            self._persist_state(job)
            return payload

        if self.event_bus is not None:
            self.event_bus.emit(
                "cron_job_started",
                {"job": name, "schedule": job.schedule},
            )

        try:
            result = job.handler()
            job.last_status = "ok"
            job.last_error = ""
        except Exception as exc:
            job.last_status = "error"
            job.last_error = str(exc)
            job.last_run_at = checkpoint.astimezone(timezone.utc).isoformat()
            job.run_count += 1
            self._persist_state(job)
            if self.event_bus is not None:
                self.event_bus.emit(
                    "cron_job_finished",
                    {"job": name, "status": job.last_status, "error": job.last_error},
                )
            raise

        job.last_run_at = checkpoint.astimezone(timezone.utc).isoformat()
        job.run_count += 1
        self._persist_state(job)
        if self.event_bus is not None:
            self.event_bus.emit(
                "cron_job_finished",
                {"job": name, "status": job.last_status},
            )
        return {
            **job.to_dict(due=False),
            "result": result,
        }

    def run_due(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        checkpoint = now or datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []
        for job in sorted(self.jobs.values(), key=lambda item: item.name):
            if self._is_due(job, checkpoint):
                results.append(self.run_job(job.name, now=checkpoint))
        return results

    def _is_due(self, job: ScheduledJob, now: datetime) -> bool:
        if not self.enabled or not job.enabled:
            return False
        if not _cron_matches(job.schedule, now):
            return False
        if job.last_run_at is None:
            return True
        last_run = datetime.fromisoformat(job.last_run_at).astimezone(timezone.utc)
        return last_run.strftime("%Y%m%d%H%M") != now.astimezone(timezone.utc).strftime("%Y%m%d%H%M")

    def _state_path(self, name: str) -> Path:
        return self.layout.cron_dir / f"{name}.json"

    def _override_path(self) -> Path:
        return self.layout.cron_dir / "overrides.json"

    def _scheduler_path(self) -> Path:
        return self.layout.cron_dir / "scheduler.json"

    def _load_state(self, name: str) -> dict[str, Any] | None:
        path = self._state_path(name)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_overrides(self) -> dict[str, dict[str, Any]]:
        path = self._override_path()
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}

    def _load_scheduler_enabled(self, *, default: bool) -> bool:
        path = self._scheduler_path()
        if not path.exists():
            return bool(default)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict) and isinstance(payload.get("enabled"), bool):
            return bool(payload["enabled"])
        return bool(default)

    def _persist_state(self, job: ScheduledJob) -> None:
        path = self._state_path(job.name)
        payload = {
            "name": job.name,
            "schedule": job.schedule,
            "description": job.description,
            "enabled": job.enabled,
            "last_run_at": job.last_run_at,
            "last_status": job.last_status,
            "last_error": job.last_error,
            "run_count": job.run_count,
            "updated_at": utc_now(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _persist_overrides(self) -> None:
        path = self._override_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self._overrides, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _persist_scheduler_enabled(self) -> None:
        path = self._scheduler_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": self.enabled,
            "updated_at": utc_now(),
        }
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)


def _cron_matches(expression: str, now: datetime) -> bool:
    minute, hour, day, month, weekday = expression.split()
    cron_weekday = (now.weekday() + 1) % 7
    return (
        _field_matches(minute, now.minute)
        and _field_matches(hour, now.hour)
        and _field_matches(day, now.day)
        and _field_matches(month, now.month)
        and _field_matches(weekday, cron_weekday)
    )


def _field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    if "," in field:
        return any(_field_matches(part.strip(), value) for part in field.split(","))
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0
    return int(field) == value
