from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from codelite.config import RuntimeConfig
from codelite.core.agent_team import AgentTeamRuntime
from codelite.core.heartbeat import HeartService
from codelite.core.mcp_runtime import McpRuntime
from codelite.core.permissions import (
    PERMISSION_ALLOW,
    PERMISSION_ASK,
    PERMISSION_DENY,
    PermissionDecision,
    PermissionStore,
)
from codelite.core.policy import PolicyError, PolicyGate
from codelite.core.skills_runtime import SkillRuntime
from codelite.core.subagent_profiles import GENERAL_PURPOSE_AGENT_TYPE, normalize_agent_type
from codelite.core.tavily import TavilySearchClient
from codelite.core.todo import TodoManager
from codelite.hooks import HookRuntime


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolExecutionResult:
    name: str
    output: str
    call_id: str = ""
    ok: bool = True
    error: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    is_enabled: Callable[[], bool]
    is_concurrency_safe: Callable[[], bool]
    is_read_only: Callable[[], bool]
    is_destructive: Callable[[], bool]
    check_permissions: Callable[[dict[str, Any]], PermissionDecision]
    to_auto_classifier_input: Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class _ToolInvocation:
    call_id: str
    name: str
    arguments: dict[str, Any]


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
        skill_runtime: SkillRuntime | None = None,
        agent_team_runtime: AgentTeamRuntime | None = None,
        mcp_runtime: McpRuntime | None = None,
        tavily_api_key: str = "",
        web_search_client: TavilySearchClient | None = None,
        permission_store: PermissionStore | None = None,
        permission_approval_ttl_sec: int = 1800,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.runtime_config = runtime_config
        self.policy = PolicyGate(self.workspace_root)
        self.todo_manager = todo_manager
        self.session_id = session_id
        self.heart_service = heart_service
        self.hook_runtime = hook_runtime
        self.skill_runtime = skill_runtime
        self.agent_team_runtime = agent_team_runtime
        self.mcp_runtime = mcp_runtime
        self.web_search_client = web_search_client or (TavilySearchClient(tavily_api_key) if tavily_api_key else None)
        self.permission_store = permission_store
        self.permission_approval_ttl_sec = max(1, int(permission_approval_ttl_sec))
        self.allowed_tools = allowed_tools
        self._specs = self._build_specs()

    def for_session(self, session_id: str) -> ToolRouter:
        return ToolRouter(
            self.workspace_root,
            self.runtime_config,
            todo_manager=self.todo_manager,
            session_id=session_id,
            heart_service=self.heart_service,
            hook_runtime=self.hook_runtime,
            skill_runtime=self.skill_runtime,
            agent_team_runtime=self.agent_team_runtime,
            mcp_runtime=self.mcp_runtime,
            web_search_client=self.web_search_client,
            permission_store=self.permission_store,
            permission_approval_ttl_sec=self.permission_approval_ttl_sec,
            allowed_tools=self.allowed_tools,
        )

    def with_allowed_tools(self, allowed_tools: set[str] | frozenset[str] | None) -> ToolRouter:
        normalized = frozenset(allowed_tools) if allowed_tools is not None else None
        return ToolRouter(
            self.workspace_root,
            self.runtime_config,
            todo_manager=self.todo_manager,
            session_id=self.session_id,
            heart_service=self.heart_service,
            hook_runtime=self.hook_runtime,
            skill_runtime=self.skill_runtime,
            agent_team_runtime=self.agent_team_runtime,
            mcp_runtime=self.mcp_runtime,
            web_search_client=self.web_search_client,
            permission_store=self.permission_store,
            permission_approval_ttl_sec=self.permission_approval_ttl_sec,
            allowed_tools=normalized,
        )

    def _new_spec(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., str],
        is_enabled: Callable[[], bool] | None = None,
        is_concurrency_safe: Callable[[], bool] | None = None,
        is_read_only: Callable[[], bool] | None = None,
        is_destructive: Callable[[], bool] | None = None,
        check_permissions: Callable[[dict[str, Any]], PermissionDecision] | None = None,
        to_auto_classifier_input: Callable[[dict[str, Any]], str] | None = None,
    ) -> ToolSpec:
        return ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            is_enabled=is_enabled or (lambda: True),
            is_concurrency_safe=is_concurrency_safe or (lambda: False),
            is_read_only=is_read_only or (lambda: False),
            is_destructive=is_destructive or (lambda: False),
            check_permissions=check_permissions or (lambda _arguments: PERMISSION_ALLOW),
            to_auto_classifier_input=to_auto_classifier_input or (lambda _arguments: ""),
        )

    def _build_specs(self) -> dict[str, ToolSpec]:
        specs = [
            self._new_spec(
                name="bash",
                description=(
                    "Run one safe shell command for inspection or tests. On Windows this runs PowerShell, not Unix bash. "
                    "Use one simple command only. Prefer list_files and read_file when inspecting a project."
                ),
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                    "additionalProperties": False,
                },
                handler=self._tool_bash,
                check_permissions=self._check_bash_permission,
            ),
            self._new_spec(
                name="list_files",
                description="List files under a workspace directory. Prefer this over shell ls/find for structure checks.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_depth": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": False,
                },
                handler=self._tool_list_files,
                is_concurrency_safe=lambda: True,
                is_read_only=lambda: True,
            ),
            self._new_spec(
                name="read_file",
                description="Read a UTF-8 text file inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=self._tool_read_file,
                is_concurrency_safe=lambda: True,
                is_read_only=lambda: True,
            ),
            self._new_spec(
                name="write_file",
                description="Write a UTF-8 text file inside the workspace. Creates parent directories if needed.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                handler=self._tool_write_file,
            ),
            self._new_spec(
                name="edit_file",
                description="Replace exactly one matching text span in a workspace file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False,
                },
                handler=self._tool_edit_file,
            ),
            self._new_spec(
                name="todo_write",
                description="Replace the current todo list for the active session.",
                parameters={
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
                                    "status": {"type": "string", "enum": ["pending", "in_progress", "done", "blocked"]},
                                    "note": {"type": "string"},
                                },
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["items"],
                    "additionalProperties": False,
                },
                handler=self._tool_todo_write,
            ),
            self._new_spec(
                name="team_create",
                description="Create an agent team.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "strategy": {"type": "string"},
                        "max_subagents": {"type": "integer", "minimum": 1},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
                handler=self._tool_team_create,
            ),
            self._new_spec(
                name="team_list",
                description="List available agent teams so you can pick a team_id for subagent work.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=self._tool_team_list,
                is_concurrency_safe=lambda: True,
                is_read_only=lambda: True,
            ),
            self._new_spec(
                name="skills_list",
                description="List available builtin and external skills.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": False,
                },
                handler=self._tool_skills_list,
                is_concurrency_safe=lambda: True,
                is_read_only=lambda: True,
            ),
            self._new_spec(
                name="subagent_spawn",
                description="Spawn a subagent for a team. mode=sync runs immediately; mode=queue enqueues.",
                parameters={
                    "type": "object",
                    "properties": {
                        "team_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "agent_type": {"type": "string"},
                        "mode": {"type": "string", "enum": ["queue", "sync"]},
                        "parent_session_id": {"type": "string"},
                    },
                    "required": ["team_id", "prompt"],
                    "additionalProperties": False,
                },
                handler=self._tool_subagent_spawn,
            ),
            self._new_spec(
                name="subagent_process",
                description="Process queued subagent tasks.",
                parameters={
                    "type": "object",
                    "properties": {"max_items": {"type": "integer", "minimum": 1}},
                    "additionalProperties": False,
                },
                handler=self._tool_subagent_process,
            ),
            self._new_spec(
                name="subagent_status",
                description="Show one or more subagent records.",
                parameters={
                    "type": "object",
                    "properties": {
                        "subagent_id": {"type": "string"},
                        "team_id": {"type": "string"},
                        "status": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": False,
                },
                handler=self._tool_subagent_status,
                is_concurrency_safe=lambda: True,
                is_read_only=lambda: True,
            ),
            self._new_spec(
                name="mcp_list",
                description="List configured MCP servers.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=self._tool_mcp_list,
                is_concurrency_safe=lambda: True,
                is_read_only=lambda: True,
            ),
            self._new_spec(
                name="mcp_call",
                description="Call one configured MCP server with a JSON payload.",
                parameters={
                    "type": "object",
                    "properties": {
                        "server": {"type": "string"},
                        "request": {"type": "object"},
                        "timeout_sec": {"type": "integer", "minimum": 1},
                    },
                    "required": ["server", "request"],
                    "additionalProperties": False,
                },
                handler=self._tool_mcp_call,
                check_permissions=lambda _arguments: PERMISSION_ASK,
            ),
        ]

        if self.web_search_client is not None and self.web_search_client.configured:
            specs.append(
                self._new_spec(
                    name="web_search",
                    description="Search the public web using Tavily.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "minimum": 1},
                            "topic": {"type": "string", "enum": ["general", "news"]},
                            "search_depth": {"type": "string", "enum": ["basic", "advanced"]},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    handler=self._tool_web_search,
                    is_concurrency_safe=lambda: True,
                    is_read_only=lambda: True,
                )
            )

        return {item.name: item for item in specs}

    def tool_schemas(self) -> list[dict[str, Any]]:
        tools = []
        for spec in self._specs.values():
            if not spec.is_enabled():
                continue
            if self.allowed_tools is not None and spec.name not in self.allowed_tools:
                continue
            tools.append({"name": spec.name, "description": spec.description, "parameters": spec.parameters})
        return tools

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        spec = self._require_spec(name)
        start = time.perf_counter()
        try:
            self._enforce_permission(spec, arguments)
            if self.heart_service is not None:
                self.heart_service.beat("tool_router")
            if self.hook_runtime is not None:
                self.hook_runtime.pre_tool_use(name, arguments)
            output = spec.handler(**arguments)
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

        return ToolExecutionResult(
            name=name,
            output=output,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            metadata={
                "read_only": spec.is_read_only(),
                "destructive": spec.is_destructive(),
                "concurrency_safe": spec.is_concurrency_safe(),
            },
        )

    def execute_tool_calls(self, calls: list[Any]) -> list[ToolExecutionResult]:
        invocations = [
            _ToolInvocation(
                call_id=str(getattr(call, "id", "")),
                name=str(getattr(call, "name", "")),
                arguments=dict(getattr(call, "arguments", {}) or {}),
            )
            for call in calls
        ]
        if not invocations:
            return []

        if not self.runtime_config.tool_parallel_enabled:
            return [self._execute_one(item) for item in invocations]

        results: list[ToolExecutionResult] = []
        safe_buffer: list[_ToolInvocation] = []
        for item in invocations:
            try:
                spec = self._require_spec(item.name)
            except ToolError as exc:
                if safe_buffer:
                    results.extend(self._flush_safe_buffer(safe_buffer))
                    safe_buffer = []
                results.append(
                    ToolExecutionResult(
                        name=item.name,
                        call_id=item.call_id,
                        output=f"TOOL_ERROR: {exc}",
                        ok=False,
                        error=str(exc),
                    )
                )
                continue
            if spec.is_concurrency_safe():
                safe_buffer.append(item)
                continue
            if safe_buffer:
                results.extend(self._flush_safe_buffer(safe_buffer))
                safe_buffer = []
            results.append(self._execute_one(item))
        if safe_buffer:
            results.extend(self._flush_safe_buffer(safe_buffer))
        return results

    def _flush_safe_buffer(self, buffer: list[_ToolInvocation]) -> list[ToolExecutionResult]:
        workers = max(1, min(len(buffer), 8))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self._execute_one, item) for item in buffer]
            return [future.result() for future in futures]

    def _execute_one(self, item: _ToolInvocation) -> ToolExecutionResult:
        try:
            result = self.dispatch(item.name, item.arguments)
            return ToolExecutionResult(
                name=result.name,
                output=result.output,
                call_id=item.call_id,
                ok=True,
                error="",
                duration_ms=result.duration_ms,
                metadata=result.metadata,
            )
        except ToolError as exc:
            return ToolExecutionResult(
                name=item.name,
                call_id=item.call_id,
                output=f"TOOL_ERROR: {exc}",
                ok=False,
                error=str(exc),
            )

    def _require_spec(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None or not spec.is_enabled():
            raise ToolError(f"Unknown tool: {name}")
        if self.allowed_tools is not None and name not in self.allowed_tools:
            raise ToolError(f"Tool `{name}` is blocked for this subagent profile.")
        return spec

    def _enforce_permission(self, spec: ToolSpec, arguments: dict[str, Any]) -> None:
        decision = spec.check_permissions(arguments)
        if decision == PERMISSION_ALLOW:
            return
        if decision == PERMISSION_DENY:
            raise ToolError(f"Permission denied for tool `{spec.name}`.")
        if decision != PERMISSION_ASK:
            raise ToolError(f"Invalid permission decision `{decision}` for tool `{spec.name}`.")

        if self.permission_store is None or not self.session_id:
            raise ToolError(f"Tool `{spec.name}` requires approval in this session.")

        remembered = self.permission_store.get_decision(
            session_id=self.session_id,
            tool_name=spec.name,
            arguments=arguments,
        )
        if remembered == PERMISSION_ALLOW:
            return
        if remembered == PERMISSION_DENY:
            raise ToolError(f"Permission denied for tool `{spec.name}` by session decision.")

        args_json = json.dumps(arguments, ensure_ascii=False)
        raise ToolError(
            "Permission requires approval. "
            f"Run: codelite permissions allow --session-id {self.session_id} "
            f"--tool {spec.name} --arguments-json '{args_json}'"
        )

    def _check_bash_permission(self, arguments: dict[str, Any]) -> PermissionDecision:
        command = str(arguments.get("command", "")).lower()
        high_risk = (
            "git push",
            "git commit",
            "git merge",
            "git rebase",
            "git reset",
            "git clean",
        )
        if any(token in command for token in high_risk):
            return PERMISSION_ASK
        return PERMISSION_ALLOW

    def _shell_argv(self, command: str) -> list[str]:
        if os.name == "nt":
            preamble = (
                "[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false); "
                "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
                "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
                "$PSStyle.OutputRendering='PlainText'; "
            )
            return ["powershell.exe", "-NoProfile", "-Command", preamble + command]
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

    def _tool_bash(self, command: str) -> str:
        normalized_command = command.strip()
        if os.name == "nt":
            if (
                normalized_command == "ls -la"
                or normalized_command.startswith("find . -maxdepth")
                or normalized_command.startswith("sed ")
            ):
                raise ToolError(
                    "Detected Unix command in Windows PowerShell environment. "
                    "Prefer list_files/read_file or native PowerShell commands."
                )
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

    def _tool_list_files(self, path: str = ".", max_depth: int = 2, limit: int = 200) -> str:
        base = self.policy.resolve_workspace_path(path, must_exist=True)
        if not base.is_dir():
            raise ToolError(f"Not a directory: {self._display_path(base)}")

        normalized_limit = max(1, min(limit, 500))
        entries: list[str] = []
        base_parts = base.relative_to(self.workspace_root).parts if base != self.workspace_root else ()

        for item in sorted(base.rglob("*")):
            relative = item.relative_to(self.workspace_root)
            if len(relative.parts) - len(base_parts) > max_depth:
                continue
            suffix = "/" if item.is_dir() else ""
            entries.append(str(relative).replace("\\", "/") + suffix)
            if len(entries) >= normalized_limit:
                break

        if not entries:
            return "(no files)"
        return self._truncate("\n".join(entries))

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

    def _tool_team_create(self, name: str, strategy: str = "parallel", max_subagents: int = 3) -> str:
        if self.agent_team_runtime is None:
            raise ToolError("agent team runtime unavailable")
        payload = self.agent_team_runtime.create_team(
            name=name,
            strategy=strategy,
            max_subagents=max_subagents,
        ).to_dict()
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    def _tool_team_list(self) -> str:
        if self.agent_team_runtime is None:
            raise ToolError("agent team runtime unavailable")
        payload = [item.to_dict() for item in self.agent_team_runtime.list_teams()]
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    def _tool_skills_list(self, query: str = "", limit: int = 20) -> str:
        if self.skill_runtime is None:
            raise ToolError("skills runtime unavailable")
        entries = self.skill_runtime.list_skills()
        normalized = query.strip().lower()
        if normalized:
            entries = [
                item
                for item in entries
                if normalized in item.get("name", "").lower()
                or normalized in item.get("summary", "").lower()
            ]
        return self._truncate(json.dumps(entries[: max(limit, 1)], ensure_ascii=False, indent=2))

    def _tool_subagent_spawn(
        self,
        team_id: str,
        prompt: str,
        agent_type: str = GENERAL_PURPOSE_AGENT_TYPE,
        mode: str = "queue",
        parent_session_id: str = "",
    ) -> str:
        if self.agent_team_runtime is None:
            raise ToolError("agent team runtime unavailable")
        normalized_agent_type = normalize_agent_type(agent_type)
        parent_id = parent_session_id.strip() or self.session_id
        if mode == "sync":
            payload = self.agent_team_runtime.run_subagent_inline(
                team_id=team_id,
                prompt=prompt,
                agent_type=normalized_agent_type,
                parent_session_id=parent_id,
            )
        else:
            payload = self.agent_team_runtime.spawn_subagent(
                team_id=team_id,
                prompt=prompt,
                agent_type=normalized_agent_type,
                parent_session_id=parent_id,
            )
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    def _tool_subagent_process(self, max_items: int = 20) -> str:
        if self.agent_team_runtime is None:
            raise ToolError("agent team runtime unavailable")
        payload = self.agent_team_runtime.process_subagents(max_items=max_items)
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    def _tool_subagent_status(
        self,
        subagent_id: str = "",
        team_id: str = "",
        status: str = "",
        limit: int = 20,
    ) -> str:
        if self.agent_team_runtime is None:
            raise ToolError("agent team runtime unavailable")
        if subagent_id.strip():
            record = self.agent_team_runtime.get_subagent(subagent_id.strip())
            payload: dict[str, Any] | list[dict[str, Any]] = (
                record.to_dict() if record is not None else {"error": f"unknown subagent_id `{subagent_id}`"}
            )
            return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))
        records = self.agent_team_runtime.list_subagents(
            team_id=team_id.strip() or None,
            status=status.strip() or None,
            limit=limit,
        )
        payload = [record.to_dict() for record in records]
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    def _tool_mcp_list(self) -> str:
        if self.mcp_runtime is None:
            raise ToolError("mcp runtime unavailable")
        payload = self.mcp_runtime.list_servers()
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    def _tool_mcp_call(self, server: str, request: dict[str, Any], timeout_sec: int = 60) -> str:
        if self.mcp_runtime is None:
            raise ToolError("mcp runtime unavailable")
        payload = self.mcp_runtime.call(name=server, request=request, timeout_sec=timeout_sec)
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))

    def _tool_web_search(
        self,
        query: str,
        max_results: int = 5,
        topic: str = "general",
        search_depth: str = "basic",
    ) -> str:
        if self.web_search_client is None or not self.web_search_client.configured:
            raise ToolError("web search unavailable: TAVILY_API_KEY is not configured")
        payload = self.web_search_client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            search_depth=search_depth,
            include_answer=True,
        )
        return self._truncate(json.dumps(payload, ensure_ascii=False, indent=2))
