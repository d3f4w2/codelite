from __future__ import annotations

from pathlib import Path
from typing import Any


def handle(
    *,
    workspace_root: Path,
    tool_name: str,
    arguments: dict[str, Any],
    output: str,
) -> dict[str, Any]:
    return {
        "workspace_root": str(workspace_root),
        "tool_name": tool_name,
        "argument_keys": sorted(arguments.keys()),
        "output_preview": output[:200],
    }
