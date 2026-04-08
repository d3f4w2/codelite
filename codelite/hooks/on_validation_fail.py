from __future__ import annotations

from typing import Any


def handle(*, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": payload.get("stage"),
        "command": payload.get("command"),
        "exit_code": payload.get("exit_code"),
        "output_preview": str(payload.get("output", ""))[:200],
    }
