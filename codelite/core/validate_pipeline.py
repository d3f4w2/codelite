from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from codelite.core.failure_trace import append_validation_failure
from codelite.hooks import HookRuntime
from codelite.storage.events import utc_now


@dataclass(frozen=True)
class ValidateStageResult:
    stage: str
    command: list[str]
    exit_code: int
    ok: bool
    output: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


Executor = Callable[[list[str], Path], ValidateStageResult]


def default_executor(command: list[str], cwd: Path) -> ValidateStageResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return ValidateStageResult(
        stage="",
        command=command,
        exit_code=completed.returncode,
        ok=completed.returncode == 0,
        output=output,
    )


class ValidatePipeline:
    def __init__(
        self,
        workspace_root: Path,
        *,
        hook_runtime: HookRuntime | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.hook_runtime = hook_runtime
        self.executor = executor or default_executor

    def run(self, *, pytest_target: str = "tests/core") -> dict[str, object]:
        stages = [
            ("build", [sys.executable, "-m", "compileall", "codelite"]),
            ("lint-arch", [sys.executable, "scripts/lint_arch.py", "--json"]),
            ("test", [sys.executable, "-m", "pytest", pytest_target, "-q"]),
            ("verify", [sys.executable, "-m", "codelite.cli", "health", "--json"]),
        ]
        results: list[ValidateStageResult] = []
        for stage_name, command in stages:
            result = self.executor(command, self.workspace_root)
            normalized = ValidateStageResult(
                stage=stage_name,
                command=command,
                exit_code=result.exit_code,
                ok=result.ok,
                output=result.output,
            )
            results.append(normalized)
            if not normalized.ok:
                trace_path = append_validation_failure(
                    self.workspace_root,
                    stage=normalized.stage,
                    command=normalized.command,
                    exit_code=normalized.exit_code,
                    output=normalized.output,
                )
                if self.hook_runtime is not None:
                    self.hook_runtime.on_validation_fail(normalized.to_dict())
                return {
                    "generated_at": utc_now(),
                    "ok": False,
                    "stages": [item.to_dict() for item in results],
                    "failure_trace_path": str(trace_path),
                }
        return {
            "generated_at": utc_now(),
            "ok": True,
            "stages": [item.to_dict() for item in results],
        }
