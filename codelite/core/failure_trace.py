from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite.storage.events import utc_now


@dataclass(frozen=True)
class FailureTraceRecord:
    trace_id: str
    generated_at: str
    failure_kind: str
    stage: str
    command: list[str]
    exit_code: int
    output: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def trace_failure_dir(workspace_root: Path) -> Path:
    return workspace_root / "runtime" / "harness" / "trace" / "failures"


def append_validation_failure(
    workspace_root: Path,
    *,
    stage: str,
    command: list[str],
    exit_code: int,
    output: str,
) -> Path:
    trace_id = uuid.uuid4().hex
    record = FailureTraceRecord(
        trace_id=trace_id,
        generated_at=utc_now(),
        failure_kind="validation_stage_failed",
        stage=stage,
        command=list(command),
        exit_code=int(exit_code),
        output=str(output),
    )
    target_dir = trace_failure_dir(workspace_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{record.generated_at.replace(':', '').replace('.', '').replace('+', '_')}-{stage}-{trace_id[:8]}.json"
    path = target_dir / filename
    with path.open("w", encoding="utf-8") as handle:
        json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
    return path
