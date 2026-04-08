from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelite.core.events import EventBus
from codelite.core.heartbeat import HeartService
from codelite.core.reconcile import Reconciler
from codelite.storage.events import RuntimeLayout, utc_now


@dataclass(frozen=True)
class WatchdogDecision:
    component_id: str
    status_before: str
    status_after: str
    reason: str
    actions: list[str]
    reconciled_task_ids: list[str]
    snapshot_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "reason": self.reason,
            "actions": self.actions,
            "reconciled_task_ids": self.reconciled_task_ids,
            "snapshot_path": self.snapshot_path,
        }


class Watchdog:
    def __init__(
        self,
        layout: RuntimeLayout,
        *,
        heart_service: HeartService,
        reconciler: Reconciler,
        event_bus: EventBus | None = None,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.heart_service = heart_service
        self.reconciler = reconciler
        self.event_bus = event_bus

    def scan(self) -> list[WatchdogDecision]:
        payload = self.heart_service.status()
        decisions: list[WatchdogDecision] = []
        for component in payload["components"]:
            if component["status"] != "red":
                continue
            decisions.append(
                self._recover_component(
                    component_id=component["component_id"],
                    status_before=component["status"],
                    reason=component["last_error"] or "heartbeat stale",
                )
            )
        if self.event_bus is not None:
            self.event_bus.emit(
                "watchdog_scan_finished",
                {"red_component_count": len(decisions)},
            )
        return decisions

    def simulate(self, component_id: str) -> WatchdogDecision:
        return self._recover_component(
            component_id=component_id,
            status_before="red",
            reason="simulated failure",
        )

    def _recover_component(
        self,
        *,
        component_id: str,
        status_before: str,
        reason: str,
    ) -> WatchdogDecision:
        snapshot_path = self._write_snapshot(
            {
                "captured_at": utc_now(),
                "component_id": component_id,
                "reason": reason,
                "heart": self.heart_service.status(),
            }
        )
        actions = [
            "captured diagnostic snapshot",
            "queued safe pause marker",
        ]
        reconciled_task_ids: list[str] = []
        if component_id in {"agent_loop", "tool_router", "cron_scheduler", "worktree_manager"}:
            reconciled_task_ids = self.reconciler.reconcile_expired_leases()
            if reconciled_task_ids:
                actions.append("reconciled expired task leases")
        self.heart_service.beat(
            "watchdog",
            status="yellow",
            last_error=f"{component_id}: {reason}",
        )
        if component_id != "watchdog":
            self.heart_service.beat(
                component_id,
                status="yellow",
                last_error=f"recovery pending: {reason}",
            )
        decision = WatchdogDecision(
            component_id=component_id,
            status_before=status_before,
            status_after="yellow",
            reason=reason,
            actions=actions,
            reconciled_task_ids=reconciled_task_ids,
            snapshot_path=str(snapshot_path),
        )
        if self.event_bus is not None:
            self.event_bus.emit(
                "watchdog_recovery_planned",
                decision.to_dict(),
            )
        return decision

    def _write_snapshot(self, payload: dict[str, Any]) -> Path:
        component_id = str(payload["component_id"])
        filename = f"{component_id}-{payload['captured_at'].replace(':', '').replace('.', '-')}.json"
        path = self.layout.watchdog_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        return path
