from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelite.config import RuntimeConfig
from codelite.core.policy import PolicyError, PolicyGate


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolExecutionResult:
    name: str
    output: str


class ToolRouter:
    def __init__(self, workspace_root: Path, runtime_config: RuntimeConfig) -> None:
        self.workspace_root = workspace_root.resolve()
        self.runtime_config = runtime_config
        self.policy = PolicyGate(self.workspace_root)

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "bash",
                "description": "Run one safe shell command for inspection or tests. Editing files via shell is not allowed in v0.0.",
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
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        method = getattr(self, f"_tool_{name}", None)
        if method is None:
            raise ToolError(f"未知工具: {name}")

        try:
            output = method(**arguments)
        except PolicyError as exc:
            raise ToolError(str(exc)) from exc
        except TypeError as exc:
            raise ToolError(f"工具参数不合法: {exc}") from exc

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
            raise ToolError(f"命令超时: {exc}") from exc

        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
        output = output or "(no output)"
        output = self._truncate(output)
        if completed.returncode != 0:
            raise ToolError(f"命令失败(exit={completed.returncode}):\n{output}")
        return output

    def _tool_read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        target = self.policy.resolve_workspace_path(path, must_exist=True)
        if target.stat().st_size > self.runtime_config.file_size_limit_bytes:
            raise ToolError(f"文件过大，拒绝读取: {self._display_path(target)}")
        if start_line < 1:
            raise ToolError("start_line 必须 >= 1。")
        if end_line is not None and end_line < start_line:
            raise ToolError("end_line 必须 >= start_line。")

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
            raise ToolError("old_text 未匹配到任何内容。")
        if match_count > 1:
            raise ToolError("old_text 匹配到多处内容，v0.0 为避免歧义已拒绝编辑。")
        updated = text.replace(old_text, new_text, 1)
        self._atomic_write(target, updated)
        return f"Edited {self._display_path(target)}"

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

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
