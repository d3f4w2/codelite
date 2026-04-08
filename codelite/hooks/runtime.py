from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codelite.storage.events import RuntimeLayout, utc_now

from . import on_validation_fail, post_tool_use, pre_tool_use


class HookRuntime:
    def __init__(self, workspace_root: Path, layout: RuntimeLayout) -> None:
        self.workspace_root = workspace_root.resolve()
        self.layout = layout
        self.layout.ensure()

    def pre_tool_use(self, tool_name: str, arguments: dict[str, Any]) -> None:
        pre_tool_use.handle(workspace_root=self.workspace_root, tool_name=tool_name, arguments=arguments)
        self._append_jsonl(
            self.layout.hook_events_path,
            {
                "timestamp_utc": utc_now(),
                "hook": "pre_tool_use",
                "tool_name": tool_name,
                "arguments": arguments,
            },
        )

    def post_tool_use(self, tool_name: str, arguments: dict[str, Any], output: str) -> dict[str, Any]:
        payload = post_tool_use.handle(
            workspace_root=self.workspace_root,
            tool_name=tool_name,
            arguments=arguments,
            output=output,
        )
        self._append_jsonl(
            self.layout.hook_events_path,
            {
                "timestamp_utc": utc_now(),
                "hook": "post_tool_use",
                **payload,
            },
        )
        return payload

    def on_validation_fail(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = on_validation_fail.handle(payload=payload)
        self._append_jsonl(
            self.layout.hook_failures_path,
            {
                "timestamp_utc": utc_now(),
                **normalized,
            },
        )
        return normalized

    def doctor(self) -> dict[str, Any]:
        agents_path = self.workspace_root / "AGENTS.md"
        modules = {
            "pre_tool_use": Path(pre_tool_use.__file__).resolve(),
            "post_tool_use": Path(post_tool_use.__file__).resolve(),
            "on_validation_fail": Path(on_validation_fail.__file__).resolve(),
        }
        return {
            "workspace_root": str(self.workspace_root),
            "agents_md_exists": agents_path.exists(),
            "agents_md_path": str(agents_path),
            "hook_events_path": str(self.layout.hook_events_path),
            "hook_failures_path": str(self.layout.hook_failures_path),
            "modules": {
                name: {"path": str(path), "exists": path.exists()}
                for name, path in modules.items()
            },
        }

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")
