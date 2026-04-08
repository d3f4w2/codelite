from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelite.config import RuntimeConfig
from codelite.core.heartbeat import HeartService
from codelite.core.policy import PolicyError, PolicyGate
from codelite.core.todo import TodoManager
from codelite.hooks import HookRuntime


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolExecutionResult:
    name: str
    output: str


class ToolRouter:
    def __init__(
        self,
        workspace_root: Path,
        runtime_config: RuntimeConfig,
        *,
        todo_manager: TodoManager | None = None,
        session_id: str | None = None,
        heart_service: HeartService | None = None,
        hook_runtime: HookRuntime | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.runtime_config = runtime_config
        self.policy = PolicyGate(self.workspace_root)
        self.todo_manager = todo_manager
        self.session_id = session_id
        self.heart_service = heart_service
        self.hook_runtime = hook_runtime

    def for_session(self, session_id: str) -> ToolRouter:
        return ToolRouter(
            self.workspace_root,
            self.runtime_config,
            todo_manager=self.todo_manager,
            session_id=session_id,
            heart_service=self.heart_service,
            hook_runtime=self.hook_runtime,
        )

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "bash",
                "description": "Run one safe shell command for inspection or tests. Editing files via shell is not allowed in v0.2.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "read_file",
                "description": "Read a UTF-8 text file inside the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "write_file",
                "description": "Write a UTF-8 text file inside the workspace. Creates parent directories if needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "edit_file",
                "description": "Replace exactly one matching text span in a workspace file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "todo_write",
                "description": "Replace the current todo list for the active session.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "content": {"type": "string"},
                                    "title": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "done", "blocked"],
                                    },
                                    "note": {"type": "string"},
                                },
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["items"],
                    "additionalProperties": False,
                },
            },
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        method = getattr(self, f"_tool_{name}", None)
        if method is None:
            raise ToolError(f"Unknown tool: {name}")

        try:
            if self.heart_service is not None:
                self.heart_service.beat("tool_router")
            if self.hook_runtime is not None:
                self.hook_runtime.pre_tool_use(name, arguments)
            output = method(**arguments)
            if self.hook_runtime is not None:
                self.hook_runtime.post_tool_use(name, arguments, output)
        except PolicyError as exc:
            self._note_error(str(exc))
            raise ToolError(str(exc)) from exc
        except RuntimeError as exc:
            self._note_error(str(exc))
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            self._note_error(str(exc))
            raise ToolError(str(exc)) from exc
        except TypeError as exc:
            self._note_error(str(exc))
            raise ToolError(f"Invalid tool arguments: {exc}") from exc

        return ToolExecutionResult(name=name, output=output)

    def _tool_bash(self, command: str) -> str:
        validated = self.policy.validate_shell_command(command)
        argv = self._shell_argv(validated)
        try:
            completed = subprocess.run(
                argv,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.runtime_config.shell_timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._note_error(str(exc))
            raise ToolError(f"Command timed out: {exc}") from exc

        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        output = output or "(no output)"
        output = self._truncate(output)
        if completed.returncode != 0:
            self._note_error(output)
            raise ToolError(f"Command failed (exit={completed.returncode}):\n{output}")
        return output

    def _tool_read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        target = self.policy.resolve_workspace_path(path, must_exist=True)
        if target.stat().st_size > self.runtime_config.file_size_limit_bytes:
            raise ToolError(f"File too large to read: {self._display_path(target)}")
        if start_line < 1:
            raise ToolError("start_line must be >= 1")
        if end_line is not None and end_line < start_line:
            raise ToolError("end_line must be >= start_line")

        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        selected = lines[start_line - 1 : end_line]
        if not selected:
            return "(empty selection)"
        numbered = [f"{index}: {line}" for index, line in enumerate(selected, start=start_line)]
        return self._truncate("\n".join(numbered))

    def _tool_write_file(self, path: str, content: str) -> str:
        target = self.policy.resolve_workspace_path(path, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, content)
        return f"Wrote {len(content.encode('utf-8'))} bytes to {self._display_path(target)}"

    def _tool_edit_file(self, path: str, old_text: str, new_text: str) -> str:
        target = self.policy.resolve_workspace_path(path, must_exist=True)
        text = target.read_text(encoding="utf-8", errors="replace")
        match_count = text.count(old_text)
        if match_count == 0:
            raise ToolError("old_text did not match any content")
        if match_count > 1:
            raise ToolError("old_text matched multiple locations and was rejected")
        updated = text.replace(old_text, new_text, 1)
        self._atomic_write(target, updated)
        return f"Edited {self._display_path(target)}"

    def _tool_todo_write(self, items: list[dict[str, Any]]) -> str:
        if self.todo_manager is None or self.session_id is None:
            raise ToolError("todo_write requires an active session-bound TodoManager")
        snapshot = self.todo_manager.replace(self.session_id, items, source="agent")
        counts: dict[str, int] = {}
        for item in snapshot.items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return f"Updated {len(snapshot.items)} todo items: {counts}"

    def _shell_argv(self, command: str) -> list[str]:
        if os.name == "nt":
            return ["powershell.exe", "-NoProfile", "-Command", command]
        return ["bash", "-lc", command]

    def _display_path(self, path: Path) -> str:
        return str(path.relative_to(self.workspace_root))

    def _truncate(self, text: str) -> str:
        limit = self.runtime_config.tool_output_limit_chars
        if len(text) <= limit:
            return text
        return text[:limit] + "\n...[truncated]..."

    def _note_error(self, message: str) -> None:
        if self.heart_service is None:
            return
        self.heart_service.beat("tool_router", status="yellow", last_error=message)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
