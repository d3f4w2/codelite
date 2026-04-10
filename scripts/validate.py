from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codelite.core.validate_pipeline import ValidatePipeline
from codelite.hooks import HookRuntime
from codelite.storage.events import RuntimeLayout


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Run the CodeLite validate pipeline")
    parser.add_argument("--pytest-target", default="tests/core", help="pytest target for the test stage")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)

    workspace_root = Path.cwd()
    pipeline = ValidatePipeline(
        workspace_root,
        hook_runtime=HookRuntime(workspace_root, RuntimeLayout(workspace_root)),
    )
    report = pipeline.run(pytest_target=args.pytest_target)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for stage in report["stages"]:
            print(f"{stage['stage']}: {'ok' if stage['ok'] else 'failed'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
