from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from codelite.core.delivery import DeliveryQueue
from codelite.core.memory_runtime import MemoryRuntime
from codelite.storage.events import RuntimeLayout, utc_now


SubagentExecutor = Callable[[str, str | None, str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class AgentTeamSpec:
    team_id: str
    name: str
    strategy: str
    max_subagents: int
    created_at: str
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentTeamSpec:
        return cls(
            team_id=str(payload["team_id"]),
            name=str(payload["name"]),
            strategy=str(payload.get("strategy", "parallel")),
            max_subagents=int(payload.get("max_subagents", 3)),
            created_at=str(payload["created_at"]),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SubagentRecord:
    subagent_id: str
    team_id: str
    prompt: str
    parent_session_id: str | None
    subagent_session_id: str | None
    status: str
    attempts: int
    created_at: str
    updated_at: str
    result_preview: str
    result_path: str
    error: str
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SubagentRecord:
        return cls(
            subagent_id=str(payload["subagent_id"]),
            team_id=str(payload["team_id"]),
            prompt=str(payload["prompt"]),
            parent_session_id=payload.get("parent_session_id"),
            subagent_session_id=payload.get("subagent_session_id"),
            status=str(payload.get("status", "queued")),
            attempts=int(payload.get("attempts", 0)),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            result_preview=str(payload.get("result_preview", "")),
            result_path=str(payload.get("result_path", "")),
            error=str(payload.get("error", "")),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentTeamRuntime:
    def __init__(
        self,
        *,
        layout: RuntimeLayout,
        delivery_queue: DeliveryQueue,
        memory_runtime: MemoryRuntime | None = None,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.delivery_queue = delivery_queue
        self.memory_runtime = memory_runtime
        self._executor: SubagentExecutor | None = None
        self.ensure_default_team()

    def set_executor(self, executor: SubagentExecutor) -> None:
        self._executor = executor

    def ensure_default_team(self) -> AgentTeamSpec:
        return self.create_team(
            name="default",
            strategy="parallel",
            max_subagents=3,
            metadata={"auto_created": True},
        )

    def create_team(
        self,
        *,
        name: str,
        strategy: str = "parallel",
        max_subagents: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> AgentTeamSpec:
        team_id = _team_key(name)
        existing = self.get_team(team_id)
        if existing is not None:
            return existing
        now = utc_now()
        team = AgentTeamSpec(
            team_id=team_id,
            name=name.strip(),
            strategy=strategy.strip() or "parallel",
            max_subagents=max(1, int(max_subagents)),
            created_at=now,
            metadata=dict(metadata or {}),
        )
        self._write_json(self._team_path(team.team_id), team.to_dict())
        return team

    def list_teams(self) -> list[AgentTeamSpec]:
        teams: list[AgentTeamSpec] = []
        for path in sorted(self.layout.agent_team_teams_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                teams.append(AgentTeamSpec.from_dict(json.load(handle)))
        return teams

    def get_team(self, team_id: str) -> AgentTeamSpec | None:
        path = self._team_path(team_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return AgentTeamSpec.from_dict(json.load(handle))

    def spawn_subagent(
        self,
        *,
        team_id: str,
        prompt: str,
        parent_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        if self.get_team(team_id) is None:
            raise RuntimeError(f"unknown team_id `{team_id}`")
        record = self._new_subagent_record(
            team_id=team_id,
            prompt=prompt,
            parent_session_id=parent_session_id,
            metadata=metadata,
        )
        self._write_subagent(record)
        item = self.delivery_queue.enqueue(
            "subagent_task",
            {"subagent_id": record.subagent_id},
            max_attempts=max_attempts,
        )
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="subagent",
                text=f"spawned subagent {record.subagent_id}",
                metadata={"team_id": team_id, "parent_session_id": parent_session_id or ""},
            )
        return {
            "subagent": record.to_dict(),
            "delivery": item.to_dict(),
        }

    def run_subagent_inline(
        self,
        *,
        team_id: str,
        prompt: str,
        parent_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.get_team(team_id) is None:
            raise RuntimeError(f"unknown team_id `{team_id}`")
        record = self._new_subagent_record(
            team_id=team_id,
            prompt=prompt,
            parent_session_id=parent_session_id,
            metadata=metadata,
        )
        self._write_subagent(record)
        result = self._execute_subagent(record, attempt=1)
        return {
            "subagent": self.get_subagent(record.subagent_id).to_dict(),  # type: ignore[union-attr]
            "result": result,
        }

    def process_subagents(self, *, max_items: int | None = None) -> list[dict[str, Any]]:
        processed = self.delivery_queue.process_all_for_kinds(
            {"subagent_task": self._handle_subagent_task},
            allowed_kinds={"subagent_task"},
            max_items=max_items,
        )
        normalized: list[dict[str, Any]] = []
        for item in processed:
            if isinstance(item, dict) and isinstance(item.get("result"), dict):
                result = dict(item["result"])
                if "subagent_id" in result:
                    normalized.append(
                        {
                            **result,
                            "delivery_id": item.get("delivery_id"),
                            "delivery_status": item.get("status"),
                        }
                    )
                    continue
            normalized.append(item)
        return normalized

    def list_subagents(
        self,
        *,
        team_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SubagentRecord]:
        records: list[SubagentRecord] = []
        for path in sorted(self.layout.agent_team_subagents_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                record = SubagentRecord.from_dict(json.load(handle))
            if team_id is not None and record.team_id != team_id:
                continue
            if status is not None and record.status != status:
                continue
            records.append(record)
        records.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        return records[: max(limit, 1)]

    def get_subagent(self, subagent_id: str) -> SubagentRecord | None:
        path = self._subagent_path(subagent_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return SubagentRecord.from_dict(json.load(handle))

    def _handle_subagent_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        subagent_id = str(payload.get("subagent_id", "")).strip()
        if not subagent_id:
            raise RuntimeError("invalid subagent_task payload: missing subagent_id")
        record = self.get_subagent(subagent_id)
        if record is None:
            raise RuntimeError(f"unknown subagent_id `{subagent_id}`")
        return self._execute_subagent(record, attempt=record.attempts + 1)

    def _execute_subagent(self, record: SubagentRecord, *, attempt: int) -> dict[str, Any]:
        if self._executor is None:
            raise RuntimeError("subagent executor is not configured")
        running = SubagentRecord(
            **{
                **record.to_dict(),
                "status": "running",
                "attempts": attempt,
                "updated_at": utc_now(),
                "error": "",
            }
        )
        self._write_subagent(running)

        try:
            result = self._executor(
                running.prompt,
                running.parent_session_id,
                running.team_id,
                dict(running.metadata),
            )
        except Exception as exc:
            failed = SubagentRecord(
                **{
                    **running.to_dict(),
                    "status": "failed",
                    "updated_at": utc_now(),
                    "error": str(exc),
                }
            )
            self._write_subagent(failed)
            if self.memory_runtime is not None:
                self.memory_runtime.remember(
                    kind="subagent",
                    text=f"subagent {running.subagent_id} failed",
                    metadata={"team_id": running.team_id},
                    evidence=[{"error": str(exc)}],
                )
            raise

        session_id = str(result.get("session_id", "") or "")
        answer = str(result.get("answer", "") or "")
        result_payload = {
            "subagent_id": running.subagent_id,
            "team_id": running.team_id,
            "parent_session_id": running.parent_session_id,
            "subagent_session_id": session_id,
            "prompt": running.prompt,
            "answer": answer,
            "metadata": running.metadata,
            "completed_at": utc_now(),
        }
        result_path = self._result_path(running.subagent_id, result_payload["completed_at"])
        self._write_json(result_path, result_payload)

        done = SubagentRecord(
            **{
                **running.to_dict(),
                "status": "done",
                "updated_at": utc_now(),
                "subagent_session_id": session_id or None,
                "result_preview": answer[:200],
                "result_path": str(result_path),
                "error": "",
            }
        )
        self._write_subagent(done)
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="subagent",
                text=f"subagent {running.subagent_id} completed",
                metadata={"team_id": running.team_id, "session_id": session_id},
                evidence=[{"result_path": str(result_path)}],
            )

        return {
            "subagent_id": running.subagent_id,
            "team_id": running.team_id,
            "status": "done",
            "session_id": session_id,
            "result_path": str(result_path),
        }

    def _new_subagent_record(
        self,
        *,
        team_id: str,
        prompt: str,
        parent_session_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> SubagentRecord:
        now = utc_now()
        return SubagentRecord(
            subagent_id=uuid.uuid4().hex,
            team_id=team_id,
            prompt=prompt,
            parent_session_id=parent_session_id,
            subagent_session_id=None,
            status="queued",
            attempts=0,
            created_at=now,
            updated_at=now,
            result_preview="",
            result_path="",
            error="",
            metadata=dict(metadata or {}),
        )

    def _team_path(self, team_id: str) -> Path:
        return self.layout.agent_team_teams_dir / f"{team_id}.json"

    def _subagent_path(self, subagent_id: str) -> Path:
        return self.layout.agent_team_subagents_dir / f"{subagent_id}.json"

    def _result_path(self, subagent_id: str, completed_at: str) -> Path:
        stamp = completed_at.replace(":", "").replace(".", "-")
        return self.layout.agent_team_results_dir / f"{subagent_id}-{stamp}.json"

    def _write_subagent(self, record: SubagentRecord) -> None:
        self._write_json(self._subagent_path(record.subagent_id), record.to_dict())

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)


def _team_key(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise RuntimeError("team name must not be empty")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stripped).strip("._-") or "team"
    safe = safe[:48].rstrip("._-") or "team"
    digest = hashlib.sha1(stripped.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"
