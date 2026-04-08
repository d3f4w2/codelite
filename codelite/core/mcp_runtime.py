from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite.core.memory_runtime import MemoryRuntime
from codelite.storage.events import RuntimeLayout, utc_now


DANGEROUS_MCP_COMMANDS = {"rm", "del", "remove-item", "format", "shutdown", "reboot", "mkfs", "dd"}


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str
    description: str
    enabled: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> McpServerSpec:
        return cls(
            name=str(payload["name"]),
            command=str(payload["command"]),
            args=[str(item) for item in payload.get("args") or []],
            env={str(key): str(value) for key, value in dict(payload.get("env") or {}).items()},
            cwd=str(payload.get("cwd", "")),
            description=str(payload.get("description", "")),
            enabled=bool(payload.get("enabled", True)),
            created_at=str(payload.get("created_at", utc_now())),
            updated_at=str(payload.get("updated_at", utc_now())),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class McpRuntime:
    def __init__(
        self,
        *,
        workspace_root: Path,
        layout: RuntimeLayout,
        memory_runtime: MemoryRuntime | None = None,
        default_timeout_sec: int = 60,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.layout = layout
        self.layout.ensure()
        self.memory_runtime = memory_runtime
        self.default_timeout_sec = max(1, int(default_timeout_sec))

    def list_servers(self) -> list[dict[str, Any]]:
        return [spec.to_dict() for spec in self._load_registry()]

    def add_server(
        self,
        *,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str = "",
        description: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        normalized_name = self._normalize_name(name)
        normalized_command = command.strip()
        if not normalized_command:
            raise RuntimeError("mcp command must not be empty")
        head = Path(normalized_command).name.lower()
        if head in DANGEROUS_MCP_COMMANDS:
            raise RuntimeError(f"blocked dangerous MCP command head `{head}`")

        normalized_cwd = self._normalize_cwd(cwd)
        now = utc_now()
        registry = self._load_registry()
        existing = {spec.name: spec for spec in registry}
        created_at = existing[normalized_name].created_at if normalized_name in existing else now
        spec = McpServerSpec(
            name=normalized_name,
            command=normalized_command,
            args=[str(item) for item in (args or [])],
            env={str(key): str(value) for key, value in dict(env or {}).items()},
            cwd=normalized_cwd,
            description=description.strip(),
            enabled=enabled,
            created_at=created_at,
            updated_at=now,
        )
        existing[normalized_name] = spec
        self._write_registry(sorted(existing.values(), key=lambda item: item.name))
        return spec.to_dict()

    def remove_server(self, name: str) -> dict[str, Any]:
        normalized_name = self._normalize_name(name)
        registry = self._load_registry()
        before = len(registry)
        kept = [item for item in registry if item.name != normalized_name]
        self._write_registry(kept)
        return {"name": normalized_name, "removed": len(kept) != before}

    def call(
        self,
        *,
        name: str,
        request: dict[str, Any],
        timeout_sec: int | None = None,
    ) -> dict[str, Any]:
        spec = self._require_server(name)
        if not spec.enabled:
            raise RuntimeError(f"MCP server `{spec.name}` is disabled")

        timeout = timeout_sec or self.default_timeout_sec
        cwd = Path(spec.cwd).resolve() if spec.cwd else self.workspace_root
        env = {**os.environ, **spec.env}
        argv = [spec.command, *spec.args]
        stdin_payload = json.dumps(request, ensure_ascii=False) + "\n"

        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                input=stdin_payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"MCP call timed out after {timeout}s") from exc

        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
        if completed.returncode != 0:
            raise RuntimeError(
                f"MCP server `{spec.name}` failed (exit={completed.returncode}): {stderr_text.strip() or stdout_text.strip()}"
            )

        response = self._parse_response(stdout_text)
        invocation = {
            "invocation_id": uuid.uuid4().hex,
            "server": spec.name,
            "command": argv,
            "cwd": str(cwd),
            "request": request,
            "response": response,
            "stdout": stdout_text.strip(),
            "stderr": stderr_text.strip(),
            "called_at": utc_now(),
        }
        invocation_path = self._invocation_path(spec.name, invocation["called_at"], invocation["invocation_id"])
        self._write_json(invocation_path, invocation)

        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="mcp",
                text=f"mcp call {spec.name}",
                metadata={"server": spec.name},
                evidence=[{"invocation_path": str(invocation_path)}],
            )

        return {
            "server": spec.name,
            "response": response,
            "invocation_path": str(invocation_path),
        }

    def _require_server(self, name: str) -> McpServerSpec:
        normalized = self._normalize_name(name)
        for spec in self._load_registry():
            if spec.name == normalized:
                return spec
        raise RuntimeError(f"unknown MCP server `{normalized}`")

    def _load_registry(self) -> list[McpServerSpec]:
        path = self.layout.mcp_servers_path
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        entries = payload.get("servers") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return []
        return [McpServerSpec.from_dict(item) for item in entries]

    def _write_registry(self, specs: list[McpServerSpec]) -> None:
        payload = {
            "updated_at": utc_now(),
            "servers": [spec.to_dict() for spec in specs],
        }
        self._write_json(self.layout.mcp_servers_path, payload)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise RuntimeError("mcp server name must not be empty")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
            raise RuntimeError("mcp server name only allows [A-Za-z0-9._-]")
        return normalized

    def _normalize_cwd(self, raw_cwd: str) -> str:
        stripped = raw_cwd.strip()
        if not stripped:
            return ""
        candidate = Path(stripped)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.workspace_root):
            raise RuntimeError(f"mcp cwd must stay inside workspace: {resolved}")
        return str(resolved)

    def _invocation_path(self, server: str, called_at: str, invocation_id: str) -> Path:
        stamp = called_at.replace(":", "").replace(".", "-")
        return self.layout.mcp_invocations_dir / f"{server}-{stamp}-{invocation_id[:8]}.json"

    @staticmethod
    def _parse_response(stdout_text: str) -> dict[str, Any] | str:
        lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
        if not lines:
            return ""
        for line in reversed(lines):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        return lines[-1]
