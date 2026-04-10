from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from codelite.core.validate_pipeline import ValidatePipeline, ValidateStageResult


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"validate-trace-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_validate_pipeline_writes_failure_trace_and_returns_path(workspace_dir: Path) -> None:
    def fake_executor(command: list[str], cwd: Path) -> ValidateStageResult:
        del cwd
        return ValidateStageResult(
            stage="",
            command=command,
            exit_code=1,
            ok=False,
            output="build failed",
        )

    pipeline = ValidatePipeline(workspace_dir, executor=fake_executor)
    report = pipeline.run(pytest_target="tests/core/test_action_verify.py")

    assert report["ok"] is False
    failure_trace_path = Path(str(report["failure_trace_path"]))
    assert failure_trace_path.exists()

    payload = json.loads(failure_trace_path.read_text(encoding="utf-8"))
    assert payload["failure_kind"] == "validation_stage_failed"
    assert payload["stage"] == "build"
    assert payload["exit_code"] == 1
