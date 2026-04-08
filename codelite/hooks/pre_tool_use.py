from __future__ import annotations

from pathlib import Path
from typing import Any


def handle(
    *,
    workspace_root: Path,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    lower_name = tool_name.lower()
    if lower_name == "bash":
        command = str(arguments.get("command", "")).lower()
        blocked = ("git commit", "git push", "git merge", "git rebase", "git reset", "git clean")
        if any(token in command for token in blocked):
            raise RuntimeError("pre_tool_use blocked a high-risk git command")

    if lower_name not in {"write_file", "edit_file"}:
        return

    raw_path = arguments.get("path")
    if not raw_path:
        return

    target = Path(str(raw_path))
    if not target.is_absolute():
        target = workspace_root / target
    resolved = target.resolve(strict=False)
    protected_roots = (
        workspace_root / "runtime" / "leases",
        workspace_root / "runtime" / "delivery-queue" / "wal",
        workspace_root / "runtime" / "hooks",
    )
    for root in protected_roots:
        if resolved.is_relative_to(root.resolve(strict=False)):
            raise RuntimeError(f"pre_tool_use blocked writes into protected runtime state: {resolved}")
