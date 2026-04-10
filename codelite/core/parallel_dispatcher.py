from __future__ import annotations

import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable

from codelite.core.delivery import DeliveryHandler, DeliveryItem, DeliveryQueue
from codelite.storage.events import utc_now


TeamLimitResolver = Callable[[str], int]


@dataclass(frozen=True)
class DispatchOutcome:
    delivery_id: str
    kind: str
    team_id: str
    status: str
    result: dict[str, Any] | None = None
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "delivery_id": self.delivery_id,
            "kind": self.kind,
            "team_id": self.team_id,
            "status": self.status,
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.last_error:
            payload["last_error"] = self.last_error
        return payload


class ParallelDispatcher:
    def __init__(
        self,
        *,
        delivery_queue: DeliveryQueue,
        handlers: dict[str, DeliveryHandler],
        team_limit_resolver: TeamLimitResolver | None = None,
    ) -> None:
        self.delivery_queue = delivery_queue
        self.handlers = handlers
        self.team_limit_resolver = team_limit_resolver

    def process(
        self,
        *,
        max_items: int | None = None,
        workers: int | None = None,
        allowed_kinds: set[str] | None = None,
        kind_reservations: dict[str, int] | None = None,
        worker_prefix: str = "dispatcher",
    ) -> list[dict[str, Any]]:
        limit = max(1, int(max_items or 100))
        worker_count = max(1, int(workers or self.delivery_queue.dispatcher_global_workers))
        allowed = set(allowed_kinds) if allowed_kinds is not None else set(self.handlers.keys())
        if not allowed:
            return []

        reservations = dict(kind_reservations or self._default_reservations())
        slots = self._build_slots(worker_count=worker_count, allowed_kinds=allowed, reservations=reservations)

        outcomes: list[DispatchOutcome] = []
        active_futures: dict[Future[tuple[bool, dict[str, Any] | str | None, str]], tuple[DeliveryItem, str]] = {}
        active_by_team: dict[str, int] = {}
        lock = threading.Lock()
        started = 0
        slot_index = 0

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"{worker_prefix}-pool") as pool:
            while True:
                scheduled = False
                while len(active_futures) < worker_count and started < limit:
                    slot_kind = slots[slot_index % len(slots)]
                    slot_index += 1
                    excluded = self._blocked_teams(active_by_team)
                    preferred = {slot_kind} if slot_kind is not None else allowed
                    claim = self.delivery_queue.claim_one(
                        allowed_kinds=preferred,
                        worker_id=f"{worker_prefix}-{uuid.uuid4().hex[:8]}",
                        exclude_team_ids=excluded,
                    )
                    if claim is None and slot_kind is not None:
                        claim = self.delivery_queue.claim_one(
                            allowed_kinds=allowed,
                            worker_id=f"{worker_prefix}-{uuid.uuid4().hex[:8]}",
                            exclude_team_ids=excluded,
                        )
                    if claim is None:
                        break

                    team_id = claim.team_id.strip()
                    team_limit = self._team_limit(team_id)
                    if team_id and active_by_team.get(team_id, 0) >= team_limit:
                        self.delivery_queue.defer_claim(
                            claim,
                            delay_sec=0.25,
                            reason=f"team `{team_id}` reached concurrency limit ({team_limit})",
                        )
                        continue

                    handler = self.handlers.get(claim.kind)
                    if handler is None:
                        updated = self.delivery_queue.fail_claim(
                            claim, f"no delivery handler registered for kind `{claim.kind}`"
                        )
                        outcomes.append(
                            DispatchOutcome(
                                delivery_id=claim.delivery_id,
                                kind=claim.kind,
                                team_id=claim.team_id,
                                status=updated.status,
                                last_error=updated.last_error,
                            )
                        )
                        started += 1
                        scheduled = True
                        continue

                    if team_id:
                        active_by_team[team_id] = active_by_team.get(team_id, 0) + 1
                    future = pool.submit(self._execute_handler, handler, claim)
                    active_futures[future] = (claim, team_id)
                    started += 1
                    scheduled = True

                if not active_futures:
                    if not scheduled:
                        break
                    continue

                done, _ = wait(active_futures.keys(), return_when=FIRST_COMPLETED, timeout=0.2)
                if not done:
                    continue

                for future in done:
                    claim, team_id = active_futures.pop(future)
                    try:
                        ok, payload, error_text = future.result()
                    except Exception as exc:  # pragma: no cover
                        ok = False
                        payload = None
                        error_text = str(exc)

                    if team_id:
                        with lock:
                            remaining = max(active_by_team.get(team_id, 1) - 1, 0)
                            if remaining == 0:
                                active_by_team.pop(team_id, None)
                            else:
                                active_by_team[team_id] = remaining

                    if ok:
                        completed = self.delivery_queue.complete_claim(claim, result=payload)
                        outcomes.append(
                            DispatchOutcome(
                                delivery_id=claim.delivery_id,
                                kind=claim.kind,
                                team_id=claim.team_id,
                                status="done",
                                result=completed.last_result,
                            )
                        )
                    else:
                        updated = self.delivery_queue.fail_claim(claim, error_text)
                        outcomes.append(
                            DispatchOutcome(
                                delivery_id=claim.delivery_id,
                                kind=claim.kind,
                                team_id=claim.team_id,
                                status=updated.status,
                                last_error=updated.last_error,
                            )
                        )

                if started >= limit and not active_futures:
                    break

        return [item.to_dict() for item in outcomes]

    @staticmethod
    def _execute_handler(
        handler: DeliveryHandler,
        claim: DeliveryItem,
    ) -> tuple[bool, dict[str, Any] | str | None, str]:
        try:
            return True, handler(claim.payload), ""
        except Exception as exc:
            return False, None, str(exc)

    def _team_limit(self, team_id: str) -> int:
        if not team_id:
            return self.delivery_queue.dispatcher_global_workers
        if self.team_limit_resolver is not None:
            return max(1, int(self.team_limit_resolver(team_id)))
        return self.delivery_queue.dispatcher_team_default_limit

    def _blocked_teams(self, active_by_team: dict[str, int]) -> set[str]:
        blocked: set[str] = set()
        for team_id, active_count in active_by_team.items():
            if active_count >= self._team_limit(team_id):
                blocked.add(team_id)
        return blocked

    def _default_reservations(self) -> dict[str, int]:
        return {
            "subagent_task": self.delivery_queue.dispatcher_subagent_reserved_workers,
            "background_task": self.delivery_queue.dispatcher_background_reserved_workers,
        }

    @staticmethod
    def _build_slots(
        *,
        worker_count: int,
        allowed_kinds: set[str],
        reservations: dict[str, int],
    ) -> list[str | None]:
        slots: list[str | None] = []
        for kind, count in reservations.items():
            if kind not in allowed_kinds:
                continue
            for _ in range(max(0, int(count))):
                if len(slots) >= worker_count:
                    break
                slots.append(kind)
            if len(slots) >= worker_count:
                break
        while len(slots) < worker_count:
            slots.append(None)
        return slots
