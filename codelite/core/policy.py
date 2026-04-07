from __future__ import annotations

import os
import re
import shlex
from pathlib import Path


class PolicyError(RuntimeError):
    pass


SHELL_BLOCKED_TOKENS = ("&&", "||", ";", "|", ">", "<", "`", "$(")
ALLOWED_SHELL_HEADS = {
    "cat",
    "dir",
    "echo",
    "find",
    "get-childitem",
    "get-content",
    "git",
    "ls",
    "pwd",
    "py",
    "pytest",
    "python",
    "rg",
    "test-path",
    "type",
    "where",
    "which",
}
SAFE_GIT_SUBCOMMANDS = {"branch", "diff", "log", "show", "status"}
DANGEROUS_COMMAND_PATTERNS = (
    r"\brm\s+-rf\b",
    r"\bdel\s+/",
    r"\bremove-item\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bmkfs\b",
    r"\bdd\b",
    r"\bgit\s+reset\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+checkout\b",
    r"\bgit\s+switch\b",
    r"\bgit\s+restore\b",
    r"\bgit\s+push\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+merge\b",
    r"\bgit\s+rebase\b",
    r":\(\)\s*\{",
)


class PolicyGate:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def resolve_workspace_path(self, raw_path: str, *, must_exist: bool = False) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.workspace_root):
            raise PolicyError(f"路径越界已拦截: {raw_path}")
        if must_exist and not resolved.exists():
            raise PolicyError(f"路径不存在: {raw_path}")
        return resolved

    def validate_shell_command(self, command: str) -> str:
        normalized = command.strip()
        if not normalized:
            raise PolicyError("命令不能为空。")
        if any(token in normalized for token in SHELL_BLOCKED_TOKENS):
            raise PolicyError("bash 仅支持单条简单命令，不允许管道、重定向或命令连接。")

        lower = normalized.lower()
        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, lower):
                raise PolicyError(f"危险命令已拦截: {command}")

        try:
            tokens = shlex.split(normalized, posix=os.name != "nt")
        except ValueError as exc:
            raise PolicyError(f"命令解析失败: {exc}") from exc

        if not tokens:
            raise PolicyError("命令不能为空。")

        head = Path(tokens[0]).name.lower()
        if head not in ALLOWED_SHELL_HEADS:
            raise PolicyError(f"命令 `{tokens[0]}` 不在 v0.0 allowlist 中。")

        if head == "git":
            subcommand = tokens[1].lower() if len(tokens) > 1 else ""
            if subcommand not in SAFE_GIT_SUBCOMMANDS:
                raise PolicyError("v0.0 的 bash 工具仅允许只读 git 命令。")

        if head in {"python", "py"} and not self._is_safe_python(tokens):
            raise PolicyError("v0.0 的 bash 工具仅允许安全的 python 只读命令。")

        return normalized

    @staticmethod
    def _is_safe_python(tokens: list[str]) -> bool:
        if len(tokens) == 2 and tokens[1] in {"-V", "--version"}:
            return True
        if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in {"codelite.cli", "pytest"}:
            return True
        return False
