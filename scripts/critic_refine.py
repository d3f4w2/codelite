from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite.storage.events import utc_now


@dataclass(frozen=True)
class FailureClusterKey:
    failure_kind: str
    stage: str
    command_head: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FailureCluster:
    key: FailureClusterKey
    count: int
    command_preview: str
    last_trace_path: str
    last_output_preview: str
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["key"] = self.key.to_dict()
        return payload


def _failure_trace_dir(workspace_root: Path) -> Path:
    return workspace_root / "runtime" / "harness" / "trace" / "failures"


def _recommendation_for(*, stage: str, output: str) -> str:
    lowered = output.lower()
    if stage == "build":
        return "Run `python -m compileall codelite` locally and fix syntax/import errors."
    if stage == "lint-arch":
        return "Run `python scripts/lint_arch.py --json` and fix layer violations or update layer map."
    if stage == "test":
        return "Re-run pytest on the failing target and address assertion or fixture errors."
    if stage == "verify":
        return "Run `python -m codelite.cli health --json` and fix runtime health errors."
    if "modulenotfounderror" in lowered:
        return "Check missing imports and ensure dependencies are installed or paths are correct."
    if "permission" in lowered or "access is denied" in lowered:
        return "Check filesystem permissions and ensure the command stays within workspace."
    if "syntaxerror" in lowered:
        return "Fix syntax errors reported by the interpreter before re-running validation."
    return "Capture a minimal repro, then fix the root cause and re-run the validation stage."


def _command_head(command: Any) -> str:
    if isinstance(command, list) and command:
        return str(command[0])
    if isinstance(command, str):
        return command.split()[0] if command.split() else ""
    return ""


def _command_preview(command: Any) -> str:
    if isinstance(command, list):
        return " ".join(str(item) for item in command[:4])
    if isinstance(command, str):
        return " ".join(command.split()[:4])
    return ""


def _output_preview(text: str, *, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _load_trace(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            payload["_trace_path"] = str(path)
            return payload
    except Exception:
        return None
    return None


def build_report(workspace_root: Path) -> dict[str, Any]:
    trace_dir = _failure_trace_dir(workspace_root)
    traces: list[dict[str, Any]] = []
    if trace_dir.exists():
        for path in sorted(trace_dir.glob("*.json")):
            payload = _load_trace(path)
            if payload is not None:
                traces.append(payload)

    clusters: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for trace in traces:
        failure_kind = str(trace.get("failure_kind", "unknown"))
        stage = str(trace.get("stage", "unknown"))
        command_head = _command_head(trace.get("command"))
        clusters.setdefault((failure_kind, stage, command_head), []).append(trace)

    cluster_items: list[FailureCluster] = []
    for (failure_kind, stage, command_head), items in sorted(clusters.items(), key=lambda item: (-len(item[1]), item[0])):
        latest = items[-1]
        output = str(latest.get("output", ""))
        recommendation = _recommendation_for(stage=stage, output=output)
        cluster_items.append(
            FailureCluster(
                key=FailureClusterKey(failure_kind=failure_kind, stage=stage, command_head=command_head),
                count=len(items),
                command_preview=_command_preview(latest.get("command")),
                last_trace_path=str(latest.get("_trace_path", "")),
                last_output_preview=_output_preview(output),
                recommendation=recommendation,
            )
        )

    return {
        "generated_at": utc_now(),
        "workspace_root": str(workspace_root),
        "trace_count": len(traces),
        "cluster_count": len(cluster_items),
        "clusters": [item.to_dict() for item in cluster_items],
    }


def _write_report(report: dict[str, Any], workspace_root: Path) -> Path:
    target = workspace_root / "runtime" / "harness" / "trace" / "recommendations-latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    tmp.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize validation failure traces into actionable recommendations.")
    parser.add_argument("--workspace", default=".", help="Workspace root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Print JSON payload")
    parser.add_argument("--write", action="store_true", help="Write recommendations-latest.json to runtime trace dir")
    args = parser.parse_args(argv)

    workspace_root = Path(args.workspace).resolve()
    report = build_report(workspace_root)
    if args.write:
        report["written_path"] = str(_write_report(report, workspace_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"Failure traces: {report['trace_count']} | clusters: {report['cluster_count']}")
    for cluster in report["clusters"]:
        key = cluster["key"]
        print(
            f"- {cluster['count']}x {key['failure_kind']} | stage={key['stage']} | head={key['command_head']} | {cluster['command_preview']}"
        )
        print(f"  recommendation: {cluster['recommendation']}")
        if cluster.get("last_trace_path"):
            print(f"  last_trace: {cluster['last_trace_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
