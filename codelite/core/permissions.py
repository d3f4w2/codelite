from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite.storage.events import RuntimeLayout, utc_now


class PermissionError(RuntimeError):
    pass


PermissionDecision = str
PERMISSION_ALLOW: PermissionDecision = "allow"
PERMISSION_DENY: PermissionDecision = "deny"
PERMISSION_ASK: PermissionDecision = "ask"
_VALID_PERMISSION_DECISIONS = {PERMISSION_ALLOW, PERMISSION_DENY, PERMISSION_ASK}


@dataclass(frozen=True)
class ApprovalRecord:
    session_id: str
    fingerprint: str
    tool_name: str
    decision: PermissionDecision
    reason: str
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ApprovalRecord:
        return cls(
            session_id=str(payload.get("session_id", "")),
            fingerprint=str(payload.get("fingerprint", "")),
            tool_name=str(payload.get("tool_name", "")),
            decision=str(payload.get("decision", PERMISSION_ASK)),
            reason=str(payload.get("reason", "")),
            created_at=str(payload.get("created_at", "")),
            expires_at=str(payload.get("expires_at", "")),
        )

    @property
    def expired(self) -> bool:
        # ISO8601 lexical order is stable for UTC timestamps produced by utc_now.
        return bool(self.expires_at) and self.expires_at <= utc_now()


class PermissionStore:
    def __init__(self, layout: RuntimeLayout) -> None:
        self.layout = layout
        self.layout.ensure()

    def list_decisions(self, *, session_id: str | None = None, limit: int = 200) -> list[ApprovalRecord]:
        records: list[ApprovalRecord] = []
        for payload in self._iter_records():
            record = ApprovalRecord.from_dict(payload)
            if session_id is not None and record.session_id != session_id:
                continue
            records.append(record)
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records[: max(limit, 1)]

    def get_decision(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> PermissionDecision | None:
        fingerprint = self.fingerprint(tool_name=tool_name, arguments=arguments)
        for payload in reversed(list(self._iter_records())):
            record = ApprovalRecord.from_dict(payload)
            if record.session_id != session_id:
                continue
            if record.fingerprint != fingerprint:
                continue
            if record.expired:
                continue
            if record.decision not in _VALID_PERMISSION_DECISIONS:
                continue
            return record.decision
        return None

    def remember(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        decision: PermissionDecision,
        ttl_seconds: int,
        reason: str = "",
    ) -> ApprovalRecord:
        normalized = str(decision).strip().lower()
        if normalized not in _VALID_PERMISSION_DECISIONS:
            raise PermissionError(f"invalid decision `{decision}`")
        if ttl_seconds <= 0:
            raise PermissionError("ttl_seconds must be > 0")
        now = utc_now()
        expiry = self._plus_seconds(now, ttl_seconds)
        record = ApprovalRecord(
            session_id=session_id,
            fingerprint=self.fingerprint(tool_name=tool_name, arguments=arguments),
            tool_name=tool_name,
            decision=normalized,
            reason=reason.strip(),
            created_at=now,
            expires_at=expiry,
        )
        self._append_record(record.to_dict())
        return record

    def fingerprint(self, *, tool_name: str, arguments: dict[str, Any]) -> str:
        # Stable fingerprint to scope one approval decision to one tool invocation shape.
        normalized = {
            "tool_name": tool_name.strip().lower(),
            "arguments": self._normalize_arguments(arguments),
        }
        raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _iter_records(self) -> list[dict[str, Any]]:
        path = self.layout.permissions_decisions_path
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _append_record(self, payload: dict[str, Any]) -> None:
        path = self.layout.permissions_decisions_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")

    @staticmethod
    def _normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key in sorted(arguments):
            value = arguments[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                normalized[key] = value
                continue
            if isinstance(value, list):
                normalized[key] = [item if isinstance(item, (str, int, float, bool)) or item is None else str(item) for item in value]
                continue
            if isinstance(value, dict):
                normalized[key] = {
                    str(sub_key): sub_value
                    if isinstance(sub_value, (str, int, float, bool)) or sub_value is None
                    else str(sub_value)
                    for sub_key, sub_value in sorted(value.items(), key=lambda item: str(item[0]))
                }
                continue
            normalized[key] = str(value)
        return normalized

    @staticmethod
    def _plus_seconds(base: str, seconds: int) -> str:
        # Keep dependency surface small and avoid importing datetime in multiple modules.
        from datetime import datetime, timedelta, timezone

        stamp = datetime.fromisoformat(base).astimezone(timezone.utc)
        return (stamp + timedelta(seconds=seconds)).isoformat()
