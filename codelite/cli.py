from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from difflib import get_close_matches
import inspect
import json
import os
import platform
import random
import re
import shlex
import subprocess
import threading
import sys
import time
from urllib.parse import urlparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from codelite import __version__
from codelite.config import AppConfig, load_app_config, resolve_workspace_root
from codelite.core.auto_orchestrator import AutoOrchestrationDecision, AutoOrchestrationPolicy
from codelite.core.agent_team import AgentTeamRuntime
from codelite.core.context import ContextCompact
from codelite.core.delivery import DeliveryQueue
from codelite.core.events import EventBus
from codelite.core.heartbeat import HeartService
from codelite.core.lanes import LaneScheduler
from codelite.core.llm import ModelClient
from codelite.core.loop import AgentLoop
from codelite.core.memory_runtime import MemoryRuntime
from codelite.core.mcp_runtime import McpRuntime
from codelite.core.model_router import CriticRefiner, ModelRouter
from codelite.core.permissions import PermissionStore
from codelite.core.parallel_dispatcher import ParallelDispatcher
from codelite.core.reconcile import Reconciler
from codelite.core.resilience import ResilienceRunner
from codelite.core.retrieval import RetrievalRouter
from codelite.core.scheduler import CronScheduler
from codelite.core.skills_runtime import SkillRuntime
from codelite.core.subagent_profiles import (
    ALL_AGENT_TYPES,
    EXPLORE_AGENT_TYPE,
    GENERAL_PURPOSE_AGENT_TYPE,
    get_builtin_agent_profiles,
    normalize_agent_type,
)
from codelite.core.task_runner import TaskRunner
from codelite.core.todo import TodoManager
from codelite.core.tools import ToolRouter
from codelite.core.validate_pipeline import ValidatePipeline
from codelite.core.watchdog import Watchdog
from codelite.core.worktree import WorktreeError, WorktreeManager
from codelite.hooks import HookRuntime
from codelite.memory import MemoryLedger, MemoryPolicy, MemoryViews
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import LeaseConflictError, TaskStateError, TaskStatus, TaskStore
from codelite.tui import (
    LockBoardData,
    QueueBoardData,
    ShellCommandSpec,
    ShellInputFocus,
    ShellInputModel,
    ShellMode,
    ShellRenderer,
    ShellWelcomeData,
    SubagentCardData,
    TaskBoardData,
    TeamBoardData,
    TimelineGroupData,
    ToolCardData,
    TodoBoardData,
)

_TOP_LEVEL_COMMANDS = frozenset(
    {
        "version",
        "status",
        "health",
        "run",
        "shell",
        "session",
        "worktree",
        "task",
        "lanes",
        "delivery",
        "resilience",
        "cron",
        "heart",
        "watchdog",
        "hooks",
        "permissions",
        "skills",
        "team",
        "subagent",
        "mcp",
        "background",
        "todo",
        "context",
        "retrieval",
        "memory",
        "model",
        "critic",
    }
)


@dataclass(frozen=True)
class _ShellLocalCommand:
    name: str
    usage: str
    help_text: str
    palette_text: str
    aliases: tuple[str, ...] = ()


_SHELL_LOCAL_COMMANDS: tuple[_ShellLocalCommand, ...] = (
    _ShellLocalCommand("help", "help", "Show local commands", "\u663e\u793a\u672c\u5730\u547d\u4ee4\u5e2e\u52a9", aliases=("h", "?")),
    _ShellLocalCommand("version", "version", "Show current shell version", "show shell version"),
    _ShellLocalCommand("plan", "plan", "Switch to plan mode", "switch to plan mode", aliases=("planner",)),
    _ShellLocalCommand("act", "act", "Switch to act mode", "switch to act mode", aliases=("accept",)),
    _ShellLocalCommand("mode", "mode [name]", "Show or set current mode", "show or switch mode"),
    _ShellLocalCommand("status", "status", "Show runtime health summary", "show runtime health", aliases=("health",)),
    _ShellLocalCommand("session", "session", "Show current session summary", "show session summary", aliases=("sid", "session id")),
    _ShellLocalCommand("resume", "resume [id]", "Resume a saved chat", "resume saved chat"),
    _ShellLocalCommand("rename", "rename <name>", "Rename the current thread", "rename current thread"),
    _ShellLocalCommand("replay", "replay [N]", "Replay current or latest N sessions", "replay recent sessions"),
    _ShellLocalCommand("todo", "todo", "Show current todo snapshot", "show todo snapshot"),
    _ShellLocalCommand("tasks", "tasks", "Show task board", "show task board"),
    _ShellLocalCommand("task", "task ...", "Task actions (show/claim/release/block/retry/jump)", "task actions"),
    _ShellLocalCommand("team", "team ...", "Run Agent Team demo (default), or /team board for board view", "run or view agent team"),
    _ShellLocalCommand("subagents", "subagents ...", "Alias of /team run <task>", "alias of /team run"),
    _ShellLocalCommand("turns", "turns [N]", "Turn fold view (error turns first)", "show folded turns"),
    _ShellLocalCommand("view", "view [mode]", "Turn output view (compact/full)", "switch turn output view", aliases=("ui",)),
    _ShellLocalCommand("cron", "cron", "Cron setup/query and enable|disable jobs", "configure or query cron"),
    _ShellLocalCommand("heart", "heart", "Natural language heartbeat setup/query", "heartbeat status and config"),
    _ShellLocalCommand("queue", "queue ...", "Queue actions (status/process/recover/replay)", "queue actions"),
    _ShellLocalCommand("locks", "locks", "Show lock status and lease countdown", "show locks and leases"),
    _ShellLocalCommand("ops", "ops [section]", "Ops workbench (runtime/watchdog/lanes/model/mcp/memory/all)", "ops workbench", aliases=("workbench",)),
    _ShellLocalCommand("runtime", "runtime", "Runtime panel (metrics/health/queue/bg)", "runtime panel", aliases=("metrics",)),
    _ShellLocalCommand("watchdog", "watchdog", "Watchdog scans and decision board", "watchdog board"),
    _ShellLocalCommand("lanes", "lanes", "Lane and delivery board", "lane and delivery board"),
    _ShellLocalCommand("delivery", "delivery", "Delivery queue panel and actions", "delivery queue actions"),
    _ShellLocalCommand("model", "model", "Model/Resilience/Critic panel", "\u6a21\u578b/\u97e7\u6027/\u8bc4\u5ba1\u9762\u677f"),
    _ShellLocalCommand("critic", "critic", "Critic/refiner panel", "critic and refiner panel"),
    _ShellLocalCommand("background", "background", "Background task panel and logs", "background tasks and logs"),
    _ShellLocalCommand("validate", "validate [run]", "Run unified validation and refresh ops board", "run unified validation"),
    _ShellLocalCommand("context", "context", "Show context compaction snapshot", "show context compaction"),
    _ShellLocalCommand("memory", "memory ...", "Memory files + prefs (remember/forget/prefs/audit/timeline/full)", "memory files and prefs", aliases=("momery",)),
    _ShellLocalCommand("skills", "skills ...", "Skill runtime (list/load/recent)", "skill runtime panel"),
    _ShellLocalCommand("skill", "skill", "Show legacy skill quick help", "legacy skill quick help"),
    _ShellLocalCommand("retrieval", "retrieval ...", "Retrieval routing (show/decide/run)", "retrieval routing panel"),
    _ShellLocalCommand("compact", "compact [N]", "Compact older context turns (default N=12)", "compact older context turns"),
    _ShellLocalCommand("mcp", "mcp", "Show MCP server quick help", "MCP quick panel"),
    _ShellLocalCommand("new", "new", "Start a new chat during a conversation", "start new chat", aliases=("reset",)),
    _ShellLocalCommand("clear", "clear", "Clear screen", "clear screen", aliases=("cls",)),
    _ShellLocalCommand("welcome", "welcome", "Show welcome panel again", "show welcome panel", aliases=("banner",)),
    _ShellLocalCommand("exit", "exit", "Exit shell", "exit shell", aliases=("quit", "q")),
)
def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        stream.reconfigure(errors="replace")


def _split_relaxed_json_items(raw: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote: str | None = None
    escaped = False

    for ch in raw:
        if in_quote is not None:
            buf.append(ch)
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == in_quote:
                in_quote = None
            continue

        if ch in {'"', "'"}:
            in_quote = ch
            buf.append(ch)
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth < 0:
                raise ValueError("invalid relaxed JSON: unbalanced brackets")

        if ch == "," and depth == 0:
            part = "".join(buf).strip()
            if not part:
                raise ValueError("invalid relaxed JSON: empty item")
            parts.append(part)
            buf = []
            continue
        buf.append(ch)

    if in_quote is not None or depth != 0:
        raise ValueError("invalid relaxed JSON: unterminated quote or bracket")

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    elif raw.strip():
        raise ValueError("invalid relaxed JSON: trailing comma")
    return parts


def _parse_relaxed_json_string(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return str(json.loads(text))
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        body = text[1:-1]
        return body.replace("\\'", "'").replace("\\\\", "\\")
    return text


def _parse_relaxed_json_value(raw: str) -> Any:
    text = raw.strip()
    if not text:
        raise ValueError("invalid relaxed JSON: empty value")
    if text.startswith("{") and text.endswith("}"):
        return _parse_relaxed_json_object(text[1:-1])
    if text.startswith("[") and text.endswith("]"):
        return _parse_relaxed_json_array(text[1:-1])
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return _parse_relaxed_json_string(text)

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None

    if text[0] in "-0123456789":
        try:
            if any(ch in text for ch in ".eE"):
                return float(text)
            return int(text)
        except ValueError:
            pass
    return text


def _parse_relaxed_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    payload: dict[str, Any] = {}
    for item in _split_relaxed_json_items(text):
        if ":" not in item:
            raise ValueError("invalid relaxed JSON object: missing ':'")
        key_raw, value_raw = item.split(":", 1)
        key = _parse_relaxed_json_string(key_raw)
        if not key:
            raise ValueError("invalid relaxed JSON object: empty key")
        payload[key] = _parse_relaxed_json_value(value_raw)
    return payload


def _parse_relaxed_json_array(raw: str) -> list[Any]:
    text = raw.strip()
    if not text:
        return []
    return [_parse_relaxed_json_value(item) for item in _split_relaxed_json_items(text)]


def _parse_json_arg(raw: str, *, field: str, expected_type: type[Any] | tuple[type[Any], ...] | None = None) -> Any:
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as strict_error:
        try:
            if text.startswith("{") and text.endswith("}"):
                parsed = _parse_relaxed_json_object(text[1:-1])
            elif text.startswith("[") and text.endswith("]"):
                parsed = _parse_relaxed_json_array(text[1:-1])
            else:
                raise ValueError("relaxed mode only supports object/array")
        except Exception as relaxed_error:
            raise RuntimeError(f"invalid JSON for `{field}`: {strict_error.msg}") from relaxed_error

    if expected_type is None:
        return parsed
    if isinstance(expected_type, tuple):
        expected_names = " or ".join(item.__name__ for item in expected_type)
    else:
        expected_names = expected_type.__name__
    if not isinstance(parsed, expected_type):
        raise RuntimeError(f"`{field}` must decode to {expected_names}")
    return parsed


@dataclass
class RuntimeInfo:
    version: str
    python: str
    platform: str
    cwd: str


@dataclass
class RuntimeServices:
    config: AppConfig
    layout: RuntimeLayout
    event_store: EventStore
    event_bus: EventBus
    session_store: SessionStore
    task_store: TaskStore
    todo_manager: TodoManager
    context_manager: ContextCompact
    heart_service: HeartService
    hook_runtime: HookRuntime
    lane_scheduler: LaneScheduler
    delivery_queue: DeliveryQueue
    skill_runtime: SkillRuntime
    agent_team_runtime: AgentTeamRuntime
    mcp_runtime: McpRuntime
    retrieval_router: RetrievalRouter
    memory_runtime: MemoryRuntime
    model_router: ModelRouter
    resilience_runner: ResilienceRunner
    critic_refiner: CriticRefiner
    permission_store: PermissionStore
    tool_router: ToolRouter
    worktree_manager: WorktreeManager | None
    reconciler: Reconciler
    cron_scheduler: CronScheduler
    validate_pipeline: ValidatePipeline
    watchdog: Watchdog
    agent_loop: AgentLoop


def build_runtime(
    workspace_root: Path | None = None,
    model_client: ModelClient | None = None,
) -> RuntimeServices:
    root = resolve_workspace_root(workspace_root)
    config = load_app_config(root)
    layout = RuntimeLayout(root)
    event_store = EventStore(layout)
    event_bus = EventBus(event_store)
    session_store = SessionStore(event_store)
    task_store = TaskStore(layout)
    todo_manager = TodoManager(layout, event_bus)
    context_manager = ContextCompact(layout, config.runtime, event_bus)
    heart_service = HeartService(layout, config.runtime, event_bus)
    hook_runtime = HookRuntime(root, layout)
    lane_scheduler = LaneScheduler(layout, event_bus)
    memory_runtime = MemoryRuntime(
        MemoryLedger(layout),
        MemoryViews(layout),
        MemoryPolicy(),
        runtime_config=config.runtime,
    )
    delivery_queue = DeliveryQueue(layout, config.runtime, event_bus)
    skill_runtime = SkillRuntime(
        layout=layout,
        session_store=session_store,
        todo_manager=todo_manager,
        delivery_queue=delivery_queue,
        memory_runtime=memory_runtime,
        nag_after_steps=config.runtime.todo_nag_after_steps,
    )
    agent_team_runtime = AgentTeamRuntime(
        layout=layout,
        delivery_queue=delivery_queue,
        memory_runtime=memory_runtime,
    )
    mcp_runtime = McpRuntime(
        workspace_root=root,
        layout=layout,
        memory_runtime=memory_runtime,
        default_timeout_sec=config.runtime.shell_timeout_sec,
    )
    model_router = ModelRouter(
        layout,
        config.llm,
        primary_client=model_client,
        memory_runtime=memory_runtime,
    )
    resilience_runner = ResilienceRunner(
        context_manager=context_manager,
        model_router=model_router,
    )
    retrieval_router = RetrievalRouter(
        root,
        layout,
        config.runtime,
        memory_runtime=memory_runtime,
        tavily_api_key=config.tavily.api_key,
    )
    critic_refiner = CriticRefiner(
        layout,
        memory_runtime=memory_runtime,
    )
    permission_store = PermissionStore(layout)
    try:
        worktree_manager = WorktreeManager(root)
    except WorktreeError:
        worktree_manager = None

    tool_router = ToolRouter(
        root,
        config.runtime,
        todo_manager=todo_manager,
        heart_service=heart_service,
        hook_runtime=hook_runtime,
        skill_runtime=skill_runtime,
        agent_team_runtime=agent_team_runtime,
        mcp_runtime=mcp_runtime,
        tavily_api_key=config.tavily.api_key,
        permission_store=permission_store,
        permission_approval_ttl_sec=config.runtime.permission_approval_ttl_sec,
    )
    agent_loop = AgentLoop(
        config=config,
        session_store=session_store,
        tool_router=tool_router,
        model_client=model_client,
        todo_manager=todo_manager,
        context_manager=context_manager,
        heart_service=heart_service,
        retrieval_router=retrieval_router,
        model_router=model_router,
        resilience_runner=resilience_runner,
        skill_runtime=skill_runtime,
        memory_runtime=memory_runtime,
    )
    def _subagent_executor(
        prompt: str,
        parent_session_id: str | None,
        team_id: str,
        agent_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        subagent_session_id = session_store.new_session_id()
        normalized_agent_type = normalize_agent_type(agent_type)
        profile = get_builtin_agent_profiles()[normalized_agent_type]
        restricted_tool_router = tool_router.with_allowed_tools(profile.allowed_tools)
        answer = agent_loop.run_turn(
            session_id=subagent_session_id,
            user_input=prompt,
            tool_router_override=restricted_tool_router,
            extra_system_messages=[profile.system_prompt],
        )
        return {
            "team_id": team_id,
            "agent_type": normalized_agent_type,
            "parent_session_id": parent_session_id,
            "metadata": dict(metadata),
            "session_id": subagent_session_id,
            "answer": answer,
        }

    agent_team_runtime.set_executor(_subagent_executor)
    reconciler = Reconciler(
        layout=layout,
        session_store=session_store,
        task_store=task_store,
        context_compact=context_manager,
        heart_service=heart_service,
        worktree_manager=worktree_manager,
        event_bus=event_bus,
    )
    cron_scheduler = CronScheduler(
        layout,
        event_bus=event_bus,
        enabled=config.runtime.scheduler_enabled,
    )
    cron_scheduler.register(
        "heartbeat_scan",
        "*/1 * * * *",
        "Collect current heartbeat status for all registered components.",
        lambda: heart_service.status(),
    )
    cron_scheduler.register(
        "task_reconcile",
        "*/2 * * * *",
        "Reconcile expired task leases and move stale tasks into blocked.",
        lambda: {"expired_task_ids": reconciler.reconcile_expired_leases()},
    )
    cron_scheduler.register(
        "worktree_gc",
        "*/10 * * * *",
        "Clean orphaned managed worktree index records.",
        lambda: {"cleaned_orphan_worktrees": reconciler.cleanup_orphan_worktrees()},
    )
    cron_scheduler.register(
        "compact_maintenance",
        "*/15 * * * *",
        "Create context snapshots for sessions that exceed the compaction threshold.",
        lambda: {"compacted_sessions": reconciler.compact_sessions()},
    )
    cron_scheduler.register(
        "metrics_rollup",
        "0 * * * *",
        "Roll up runtime metrics into runtime/metrics/rollup-latest.json.",
        lambda: {"metrics_path": str(reconciler.rollup_metrics())},
    )
    _register_custom_cron_jobs(cron_scheduler, layout)
    watchdog = Watchdog(
        layout,
        heart_service=heart_service,
        reconciler=reconciler,
        event_bus=event_bus,
    )
    validate_pipeline = ValidatePipeline(
        root,
        hook_runtime=hook_runtime,
    )

    heart_service.beat("event_bus")
    heart_service.beat("todo_manager")
    heart_service.beat("context_compact")
    heart_service.beat("cron_scheduler", queue_depth=len(cron_scheduler.jobs))
    heart_service.beat("lane_scheduler", queue_depth=len(lane_scheduler.status()["lanes"]))
    heart_service.beat("delivery_queue")
    heart_service.beat("retrieval_router")
    heart_service.beat("model_router")
    heart_service.beat("skill_runtime")
    heart_service.beat("agent_team_runtime")
    heart_service.beat("mcp_runtime")
    heart_service.beat("permission_store")
    if worktree_manager is not None:
        heart_service.beat("worktree_manager")

    return RuntimeServices(
        config=config,
        layout=layout,
        event_store=event_store,
        event_bus=event_bus,
        session_store=session_store,
        task_store=task_store,
        todo_manager=todo_manager,
        context_manager=context_manager,
        heart_service=heart_service,
        hook_runtime=hook_runtime,
        lane_scheduler=lane_scheduler,
        delivery_queue=delivery_queue,
        skill_runtime=skill_runtime,
        agent_team_runtime=agent_team_runtime,
        mcp_runtime=mcp_runtime,
        retrieval_router=retrieval_router,
        memory_runtime=memory_runtime,
        model_router=model_router,
        resilience_runner=resilience_runner,
        critic_refiner=critic_refiner,
        permission_store=permission_store,
        tool_router=tool_router,
        worktree_manager=worktree_manager,
        reconciler=reconciler,
        cron_scheduler=cron_scheduler,
        validate_pipeline=validate_pipeline,
        watchdog=watchdog,
        agent_loop=agent_loop,
    )


def _runtime_info() -> RuntimeInfo:
    return RuntimeInfo(
        version=__version__,
        python=sys.version.split()[0],
        platform=f"{platform.system()} {platform.release()}",
        cwd=str(Path.cwd()),
    )


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def build_health_snapshot(services: RuntimeServices) -> dict[str, Any]:
    latest = services.session_store.latest_session_ids(limit=1)
    heart = services.heart_service.status()
    heart_summary: dict[str, int] = {}
    for component in heart["components"]:
        heart_summary[component["status"]] = heart_summary.get(component["status"], 0) + 1
    return {
        **asdict(_runtime_info()),
        "workspace_root": str(services.layout.workspace_root),
        "runtime_dir": str(services.layout.runtime_dir),
        "events_path": str(services.layout.events_path),
        "sessions_dir": str(services.layout.sessions_dir),
        "session_count": len(list(services.layout.sessions_dir.glob("*.jsonl"))),
        "managed_worktree_count": len(services.worktree_manager.list_managed()) if services.worktree_manager else 0,
        "event_count": _count_lines(services.layout.events_path),
        "todo_snapshot_count": len(list(services.layout.todos_dir.glob("*.json"))),
        "context_snapshot_count": len(list(services.layout.context_dir.glob("*.json"))),
        "cron_job_count": len(services.cron_scheduler.jobs),
        "lane_count": len(services.lane_scheduler.status()["lanes"]),
        "delivery_pending_count": len(list(services.layout.delivery_pending_dir.glob("*.json"))),
        "delivery_failed_count": len(list(services.layout.delivery_failed_dir.glob("*.json"))),
        "agent_team_count": len(services.agent_team_runtime.list_teams()),
        "subagent_count": len(services.agent_team_runtime.list_subagents(limit=10000)),
        "mcp_server_count": len(services.mcp_runtime.list_servers()),
        "memory_entry_count": _count_lines(services.layout.memory_ledger_path),
        "permission_decision_count": _count_lines(services.layout.permissions_decisions_path),
        "hearts_path": str(services.layout.hearts_path),
        "heart_status_summary": heart_summary,
        "last_session_id": latest[0] if latest else None,
        "llm": {
            "provider": services.config.llm.provider,
            "model": services.config.llm.model,
            "base_url": services.config.llm.base_url,
            "configured": services.config.llm.configured,
        },
        "embedding": {
            "provider": services.config.embedding.provider,
            "model": services.config.embedding.model,
            "base_url": services.config.embedding.base_url,
            "configured": services.config.embedding.configured,
        },
        "rerank": {
            "provider": services.config.rerank.provider,
            "model": services.config.rerank.model,
            "base_url": services.config.rerank.base_url,
            "configured": services.config.rerank.configured,
        },
        "tavily": {
            "configured": services.config.tavily.configured,
        },
    }


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _print_json(data: Any) -> None:
    print(_json_text(data))


def _format_event(event: dict[str, Any]) -> str:
    timestamp = event.get("timestamp_utc", "?")
    event_type = event.get("event_type", "unknown")
    payload = event.get("payload", {})

    if event_type == "message":
        role = payload.get("role", "unknown")
        if role == "assistant" and payload.get("tool_calls"):
            names = ", ".join(
                call.get("function", {}).get("name", "unknown") for call in payload["tool_calls"]
            )
            return f"[{timestamp}] assistant -> tool_calls: {names}"
        if role == "tool":
            content = str(payload.get("content", "")).strip()
            return f"[{timestamp}] tool {payload.get('name', 'unknown')}: {content[:240]}"
        content = str(payload.get("content", "")).strip()
        return f"[{timestamp}] {role}: {content[:240]}"

    return f"[{timestamp}] {event_type}: {json.dumps(payload, ensure_ascii=False)}"


def _cron_job_aliases() -> dict[str, str]:
    return {
        "heartbeat": "heartbeat_scan",
        "heartbeat_scan": "heartbeat_scan",
        "task_reconcile": "task_reconcile",
        "worktree_gc": "worktree_gc",
        "compact_maintenance": "compact_maintenance",
        "metrics": "metrics_rollup",
        "metrics_rollup": "metrics_rollup",
    }


def _resolve_cron_job_name(text: str, available_jobs: set[str]) -> str | None:
    lowered = text.lower()
    for raw, resolved in _cron_job_aliases().items():
        if raw.lower() in lowered and resolved in available_jobs:
            return resolved
    for job in available_jobs:
        if job.lower() in lowered:
            return job
    return None


@dataclass(frozen=True)
class CronIntentDecision:
    intent: str = "unknown"
    scope: str = "none"
    job_name: str | None = None
    schedule: str | None = None
    enabled: bool | None = None
    message_template: str = ""
    missing_fields: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    confidence: float = 0.0
    source: str = "rules"
    reason: str = ""


def _match_cron_job_candidates(name: str, available_jobs: set[str]) -> list[str]:
    query = name.strip().lower()
    if not query:
        return []

    ordered_jobs = sorted(available_jobs)
    exact = [job for job in ordered_jobs if job.lower() == query]
    if exact:
        return exact

    contains = [job for job in ordered_jobs if query in job.lower() or job.lower() in query]
    if contains:
        return contains

    aliases = {raw.lower(): resolved for raw, resolved in _cron_job_aliases().items() if resolved in available_jobs}
    if query in aliases:
        return [aliases[query]]

    close = get_close_matches(query, [job.lower() for job in ordered_jobs], n=3, cutoff=0.55)
    if not close:
        return []
    close_lookup = set(close)
    return [job for job in ordered_jobs if job.lower() in close_lookup]


def _looks_like_global_cron_scope(text: str) -> bool:
    lowered = text.lower()
    if "scheduler" in lowered:
        return True
    if any(token in lowered for token in ("all cron", "all jobs", "all schedules", "global cron", "global scheduler")):
        return True
    if re.search(r"\bcron\b", lowered) and any(token in lowered for token in ("globally", "global", "entire", "whole")):
        return True
    return False


def _extract_json_object_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    direct_candidates = [stripped]
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.IGNORECASE | re.DOTALL)
    if fence_match:
        direct_candidates.append(fence_match.group(1).strip())

    for candidate in direct_candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    for start in (idx for idx, ch in enumerate(stripped) if ch == "{"):
        depth = 0
        in_quote: str | None = None
        escaped = False
        for index in range(start, len(stripped)):
            ch = stripped[index]
            if in_quote is not None:
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if ch == in_quote:
                    in_quote = None
                continue
            if ch in {'"', "'"}:
                in_quote = ch
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = stripped[start : index + 1]
                    try:
                        payload = json.loads(snippet)
                    except json.JSONDecodeError:
                        break
                    if isinstance(payload, dict):
                        return payload
                    break
    return None


_CRON_EXPRESSION_PATTERN = re.compile(
    r"^\s*"
    r"(\*|[0-5]?\d|\*/\d+)\s+"
    r"(\*|[01]?\d|2[0-3]|\*/\d+)\s+"
    r"(\*|[12]?\d|3[01]|\*/\d+)\s+"
    r"(\*|1[0-2]|0?\d|\*/\d+)\s+"
    r"(\*|[0-6]|\*/\d+)"
    r"\s*$"
)


def _looks_like_cron_expression(text: str) -> bool:
    return bool(_CRON_EXPRESSION_PATTERN.match(text))


def _parse_nl_schedule(text: str) -> str | None:
    normalized = text.strip().lower()
    cron_match = re.search(
        r"(\*|[0-5]?\d|\*/\d+)\s+(\*|[01]?\d|2[0-3]|\*/\d+)\s+(\*|[12]?\d|3[01]|\*/\d+)\s+(\*|1[0-2]|0?\d|\*/\d+)\s+(\*|[0-6]|\*/\d+)",
        normalized,
    )
    if cron_match:
        return cron_match.group(0)

    every_minutes = re.search(r"every\s*(\d+)\s*minutes?", normalized)
    if every_minutes:
        value = max(1, int(every_minutes.group(1)))
        return f"*/{value} * * * *"
    if "every minute" in normalized:
        return "*/1 * * * *"

    every_hours = re.search(r"every\s*(\d+)\s*hours?", normalized)
    if every_hours:
        value = max(1, int(every_hours.group(1)))
        return f"0 */{value} * * *"
    if "every hour" in normalized:
        return "0 * * * *"

    daily_time = re.search(r"(?:daily|every day)\s*(\d{1,2})(?::(\d{1,2}))?", normalized)
    if daily_time:
        hour = int(daily_time.group(1))
        minute = int(daily_time.group(2) or 0)
        return f"{minute} {hour} * * *"

    weekly = re.search(
        r"(?:weekly|every week)\s*(sun|mon|tue|wed|thu|fri|sat)\s*(\d{1,2})(?::(\d{1,2}))?",
        normalized,
    )
    if weekly:
        weekday_map = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
        weekday = weekday_map.get(weekly.group(1), 0)
        hour = int(weekly.group(2))
        minute = int(weekly.group(3) or 0)
        return f"{minute} {hour} * * {weekday}"

    return None


def _cron_seconds_requested(text: str) -> bool:
    lowered = text.lower()
    return "every second" in lowered or bool(re.search(r"\b\d+\s*s(ec(ond)?s?)?\b", lowered))


def _parse_natural_number(text: str) -> int:
    stripped = text.strip().lower()
    if stripped.isdigit():
        return int(stripped)
    mapping = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    return mapping.get(stripped, 1)

def _custom_cron_jobs_path(layout: RuntimeLayout) -> Path:
    return layout.cron_dir / "custom-jobs.json"


def _load_custom_cron_jobs(layout: RuntimeLayout) -> list[dict[str, Any]]:
    path = _custom_cron_jobs_path(layout)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, list) else []


def _save_custom_cron_jobs(layout: RuntimeLayout, jobs: list[dict[str, Any]]) -> None:
    path = _custom_cron_jobs_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(jobs, handle, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _set_custom_cron_enabled(layout: RuntimeLayout, *, name: str, enabled: bool) -> bool:
    specs = _load_custom_cron_jobs(layout)
    changed = False
    for spec in specs:
        if str(spec.get("name", "")).strip() != name:
            continue
        spec["enabled"] = bool(enabled)
        changed = True
    if changed:
        _save_custom_cron_jobs(layout, specs)
    return changed


def _render_custom_cron_message(spec: dict[str, Any]) -> str:
    template = str(spec.get("message_template", "") or "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if template == "current_time":
        return f"闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁炬儳缍婇弻鐔兼⒒鐎靛壊妲紒鐐劤濠€閬嶅焵椤掑倹鍤€閻庢凹鍙冨畷宕囧鐎ｃ劋姹楅梺鍦劋閸ㄥ綊宕愰悙鐑樺仭婵犲﹤鍟扮粻鑽も偓娈垮枟婵炲﹪寮崘顔肩＜婵炴垶鑹鹃獮妤呮⒒娓氣偓濞佳呮崲閸儱鏄ラ柛鏇ㄥ灡閸婂潡鏌涢幘妤€鎳愰敍婊堟⒑瑜版帒浜伴柛娆忛閳绘挸顭ㄩ崼鐔哄幐閻庡厜鍋撻柍褜鍓熷畷浼村冀椤撶偠鎽曢梺鎼炲労閸撴岸寮插┑瀣厓鐟滄粓宕滈悢濂夊殨缂佸绨卞Σ鍫ユ煏韫囥儳纾块柛姗€浜跺娲濞戣京鍙氭繝纰樷偓铏窛缂侇喖顭烽幃娆撳箹椤撶噥鍟嶉梻浣虹帛閸旀洟顢氶鐔告珷妞ゅ繐鐗婇崵鏇㈡煙闁箑骞戝ù婊勭矒閺岀喖宕崟顒夋婵炲瓨绮撶粻鏍ь潖閾忚瀚氶柛娆忣槺椤╃増绻涚€涙鐭嬬紒璇插楠炴垿濮€閻橆偅顫嶉梺闈涚箳婵绮ｅ☉姗嗘富闁靛牆妫涙晶顒併亜閺囩喐灏﹂柡浣瑰姍瀹曘儵宕橀弻銉ュ及閻庤娲橀崕濂嘎ㄩ崒鐐搭棅妞ゆ帒顦晶顕€鏌嶇憴鍕伌闁搞劍鍎抽悾鐑藉炊瑜忛崢浠嬫煟鎼淬値娼愭繛鎻掔箻瀹曟繈骞嬮敂琛″亾娴ｇ硶鏋庨柟鎯х－椤ρ呯磼閻愵剚绶茬憸鏉款樀瀹曨偄螖閸愵亞锛濋梺绋挎湰閼归箖鍩€椤掍焦鍊愮€规洘鍔欓幃婊堟嚍閵夈儲鐣遍梻浣藉亹椤牓鎮樺璺虹煑闊洦绋掗悡娆撴煙椤栨粌顣兼い銉ヮ槺閻ヮ亪顢樺☉妯瑰闂傚倸鍊峰ù鍥綖婢舵劕纾块柣鎾冲濞戙垹绀嬫い鏍ㄧ☉閻濇棃姊虹紒妯荤叆闁硅姤绮庣划缁樸偅閸愨晝鍘甸柣搴ｆ暩椤牓鍩€椤掍礁鐏ユい顐ｇ箞椤㈡牠鍩＄€ｎ剛袦閻庤娲栭妶鎼佸箖閵忋垻鐭欓柛顭戝枙缁辩喎鈹戦悩鑼闁哄绨遍崑鎾诲箛閺夎法锛涢梺鐟板⒔缁垶鎮￠悢闀愮箚闁靛牆鍊告禍楣冩⒑閹稿孩澶勫ù婊勭矒椤㈡岸鏁愭径妯绘櫇闂佹寧娲嶉崑鎾剁磼閻樿櫕鐨戦柟鎻掓啞瀵板嫰骞囬鍌氭憢濠电偛顕慨鎾敄閸℃稒鍋傞柣鏂垮悑閻撴瑩姊洪銊х暠濠⒀屽枤缁辨帡鎮▎蹇斿闁绘挻娲熼弻銊モ攽閸℃瑥顤€濡炪們鍎遍ˇ鐢稿蓟瀹ュ洦鍠嗛柛鏇ㄥ亞娴煎矂姊虹拠鈥虫灍闁荤啿鏅犲畷娲焺閸愨晛顎撻悗鐟板閸嬪﹤螞濠婂牊鈷掗柛灞捐壘閳ь剚鎮傚畷鎰槹鎼达絿鐒兼繛鎾村焹閸嬫挻顨ラ悙瀵稿⒌妞ゃ垺娲熼弫鍌炴寠婢跺﹤顥楁繝鐢靛О閸ㄧ厧鈻斿☉銏″殣妞ゆ牗绮嶉浠嬫煏閸繍妲归柣鎾存礀閳规垿鎮╅幓鎺濅患闂佸搫顑嗛弻銊╂箒濠电姴锕ゅΛ妤勵暱缂傚倷鑳剁划顖滄崲閸儱绠栧ù鐘差儐椤ュ牊绻涢幋鐑嗘畼闁硅娲樼换婵堝枈濡椿娼戦梺绋款儏閹虫﹢骞冮悜钘夌厸濞达絿鍎ゅ▓楣冩⒑閸濆嫭鍌ㄩ柛銊ユ贡缁牓宕橀埡鍐啎闂佸搫顦伴崺鍫ュ磿閹扮増鐓熼煫鍥ф捣椤︼附銇勯鍕殻濠碘€崇埣瀹曞崬螣閻戞ɑ顔傛繝鐢靛У椤旀牠宕抽敐澹﹀骞樼€靛摜褰鹃梺鍝勬储閸ㄨ櫣鈧數濮撮…璺ㄦ崉閾忕懓顣甸梺绋款儐閹瑰洭鐛幒妤€绠ｆい鎾跺枎閸忓﹥淇婇悙顏勨偓鏍暜婵犲洦鍊块柨鏇炲€哥粻鏍ㄧ箾閸℃ê濮夌紒鐘荤畺閹鈽夊▎妯煎姺闂佹椿鍘奸鍥╂閹烘鏁婇柛蹇撳悑瀹曞啿鈹戦垾鍐茬骇闁告梹鐟╅悰顕€骞掑Δ鈧粻鑽も偓瑙勬礀濞层劑鎮伴灏栨斀闁绘ê鐏氶弳鈺佲攽椤旂⒈鍤熼柍褜鍓氶崙褰掑窗濡ゅ懌鈧啫鈻庨幘绮规嫼闁荤姴娲﹁ぐ鍐敆閵徛颁簻闁哄啠鍋撻柣妤冨Т閻ｇ兘寮舵惔鎾搭潔闂侀潧绻嗛弲婊堝疾椤忓牊鍋℃繝濠傚椤ュ牏鈧娲樺鑺ヤ繆閻戣姤鏅濋柍褜鍓涘▎銏ゆ倷閻戞鍘甸梻渚囧弿缁犳垶鏅剁紒妯肩闁绘挸鍑介煬顒佹叏婵犲啯銇濈€规洘锕㈠畷锝嗗緞鐎ｎ亜澹嶉梻鍌欒兌椤牓鏁冮敃鍌氱疇闁规崘顕ч悡婵堚偓骞垮劚椤︻垶鎮欐繝鍐︿簻闁瑰搫绉烽澶愭煙妞嬪海甯涚紒缁樼⊕濞煎繘宕滆閸嬔囨⒑绾懏鐝柟鐟版喘閵嗕礁顫濋澶屽弳闂佸憡渚楅崹鎶芥晬濠婂牊鐓熼柣妯哄级婢跺嫮鎲搁弶鍨殻闁糕斁鍋撳銈嗗笂閼冲爼骞楅崘顔界厽闊洦鎼╅崕蹇涙煃鐟欏嫬鐏撮柟顔界懇瀵爼骞嬮悩杈ㄥ枤闂傚倷鑳堕幊鎾跺椤撱垹纾婚柕鍫濇噽閺嗭箓鏌涢锝嗗闁轰礁妫濋弻宥堫檨闁告挻宀稿畷鏇㈩敃閿旇В鎷绘繛杈剧悼椤牓寮抽柆宥嗙厵缁炬澘宕禍鎵偓瑙勬礃閸旀瑩骞冮悾宀€鐭欓悹渚厛閸? {now}"
    return template.replace("{now}", now)


def _register_custom_cron_jobs(cron_scheduler: CronScheduler, layout: RuntimeLayout) -> None:
    for spec in _load_custom_cron_jobs(layout):
        if str(spec.get("kind", "")) != "terminal_message":
            continue
        job_name = str(spec.get("name", "")).strip()
        schedule = str(spec.get("schedule", "")).strip()
        if not job_name or not schedule:
            continue

        def handler(spec: dict[str, Any] = dict(spec)) -> dict[str, Any]:
            message = _render_custom_cron_message(spec)
            return {
                "type": "terminal_message",
                "message": message,
                "job_name": spec.get("name", ""),
            }

        cron_scheduler.register(
            job_name,
            schedule,
            str(spec.get("description", "Custom terminal cron job")),
            handler,
            enabled=bool(spec.get("enabled", True)),
        )


def _parse_nl_heart_status(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("red", "danger", "critical", "error")):
        return "red"
    if any(token in lowered for token in ("yellow", "warn", "warning")):
        return "yellow"
    return "green"
def _parse_nl_heart_number(text: str, *patterns: str, default: int = 0) -> int:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return default


def _parse_nl_heart_float(text: str, *patterns: str, default: float = 0.0) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return default


def _resolve_replay_session_ids(services: RuntimeServices, args: argparse.Namespace) -> list[str]:
    if getattr(args, "session_id", None):
        return [args.session_id]
    return services.session_store.latest_session_ids(limit=max(args.last, 1))


def _resolve_task_prompt(services: RuntimeServices, args: argparse.Namespace) -> str:
    raw_prompt = " ".join(getattr(args, "prompt", [])).strip()
    if raw_prompt:
        return raw_prompt

    existing = services.task_store.get_task(args.task_id)
    if existing is not None and existing.metadata.get("prompt"):
        return str(existing.metadata["prompt"])
    if existing is not None and existing.title:
        return existing.title
    if getattr(args, "title", None):
        return args.title
    return f"Please complete task {args.task_id} inside the managed worktree."


def _resolve_latest_id(
    session_id: str | None,
    limit: int,
    latest_fn: Callable[[int], list[str]],
) -> str | None:
    if session_id:
        return session_id
    latest = latest_fn(limit)
    return latest[0] if latest else None


def cmd_health(args: argparse.Namespace) -> int:
    services = build_runtime()
    snapshot = build_health_snapshot(services)
    if args.json:
        _print_json(snapshot)
        return 0

    print(f"version: {snapshot['version']}")
    print(f"python: {snapshot['python']}")
    print(f"platform: {snapshot['platform']}")
    print(f"workspace_root: {snapshot['workspace_root']}")
    print(f"runtime_dir: {snapshot['runtime_dir']}")
    print(f"last_session_id: {snapshot['last_session_id']}")
    print(f"llm: {snapshot['llm']['model']} ({'configured' if snapshot['llm']['configured'] else 'missing api key'})")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    services = build_runtime(model_client=getattr(args, "_model_client", None))
    session_id = args.session_id or services.session_store.new_session_id()
    prompt = " ".join(args.prompt).strip()
    answer = services.agent_loop.run_turn(session_id=session_id, user_input=prompt)

    if args.json:
        _print_json({"session_id": session_id, "answer": answer})
        return 0

    print(answer)
    return 0


def cmd_session_replay(args: argparse.Namespace) -> int:
    services = build_runtime()
    session_ids = _resolve_replay_session_ids(services, args)
    if not session_ids:
        print("No sessions available to replay.")
        return 1

    payload = []
    for session_id in session_ids:
        events = services.session_store.replay(session_id)
        payload.append({"session_id": session_id, "events": events})

    if args.json:
        _print_json(payload)
        return 0

    for item in payload:
        print(f"session: {item['session_id']}")
        for event in item["events"]:
            print(_format_event(event))
    return 0


def cmd_worktree_prepare(args: argparse.Namespace) -> int:
    services = build_runtime()
    if services.worktree_manager is None:
        raise RuntimeError("Current workspace is not a git toplevel, so managed worktrees are unavailable.")
    record = services.worktree_manager.prepare(
        args.task_id,
        title=args.title or "",
        base_ref=args.base_ref,
    )

    if args.json:
        _print_json(record.to_dict())
        return 0

    print(f"task_id: {record.task_id}")
    print(f"branch: {record.branch}")
    print(f"path: {record.path}")
    print(f"base_ref: {record.base_ref}")
    return 0


def cmd_worktree_list(args: argparse.Namespace) -> int:
    services = build_runtime()
    if services.worktree_manager is None:
        records: list[dict[str, Any]] = []
    else:
        records = [record.to_dict() for record in services.worktree_manager.list_managed()]

    if args.json:
        _print_json(records)
        return 0

    if not records:
        print("No managed worktrees.")
        return 0

    for record in records:
        print(f"task_id: {record['task_id']}")
        print(f"branch: {record['branch']}")
        print(f"path: {record['path']}")
        print(f"attached: {record['attached']}")
        print(f"path_exists: {record['path_exists']}")
        print(f"head: {record['head']}")
        print("")
    return 0


def cmd_worktree_remove(args: argparse.Namespace) -> int:
    services = build_runtime()
    if services.worktree_manager is None:
        raise RuntimeError("Current workspace is not a git toplevel, so managed worktrees are unavailable.")
    record = services.worktree_manager.remove(args.task_id, force=args.force)

    if args.json:
        _print_json(record.to_dict())
        return 0

    print(f"removed worktree for task {record.task_id}: {record.path}")
    return 0


def cmd_task_run(args: argparse.Namespace) -> int:
    services = build_runtime(model_client=getattr(args, "_model_client", None))
    if services.worktree_manager is None:
        raise RuntimeError("Current workspace is not a git toplevel, so task worktrees are unavailable.")

    prompt = _resolve_task_prompt(services, args)
    runner = TaskRunner(
        workspace_root=services.layout.workspace_root,
        config=services.config,
        session_store=services.session_store,
        task_store=services.task_store,
        worktree_manager=services.worktree_manager,
        model_client=services.agent_loop.model_client,
        todo_manager=services.todo_manager,
        context_manager=services.context_manager,
        heart_service=services.heart_service,
        retrieval_router=services.retrieval_router,
        model_router=services.model_router,
        resilience_runner=services.resilience_runner,
        skill_runtime=services.skill_runtime,
        agent_team_runtime=services.agent_team_runtime,
        mcp_runtime=services.mcp_runtime,
        memory_runtime=services.memory_runtime,
        hook_runtime=services.hook_runtime,
        permission_store=services.permission_store,
    )
    lane_payload = services.lane_scheduler.execute_sync(
        "main",
        job_id=f"task:{args.task_id}",
        payload={"task_id": args.task_id},
        callback=lambda: runner.run(
            task_id=args.task_id,
            prompt=prompt,
            title=args.title or args.task_id,
            session_id=args.session_id,
        ).to_dict(),
    )
    result_payload = lane_payload["result"]
    result = result_payload

    if args.json:
        _print_json(result)
        return 0

    print(f"task_id: {result['task']['task_id']}")
    print(f"status: {result['task']['status']}")
    print(f"session_id: {result['session_id']}")
    print(f"worktree: {result['worktree']['path']}")
    print(f"answer: {result['answer']}")
    return 0


def cmd_task_list(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = [task.to_dict() for task in services.task_store.list_tasks()]

    if args.json:
        _print_json(payload)
        return 0

    if not payload:
        print("No tasks.")
        return 0

    for item in payload:
        print(f"task_id: {item['task_id']}")
        print(f"status: {item['status']}")
        print(f"title: {item['title']}")
        print(f"updated_at: {item['updated_at']}")
        print("")
    return 0


def cmd_task_show(args: argparse.Namespace) -> int:
    services = build_runtime()
    task = services.task_store.get_task(args.task_id)
    if task is None:
        print(f"Task not found: {args.task_id}")
        return 1

    if args.json:
        _print_json(task.to_dict())
        return 0

    print(f"task_id: {task.task_id}")
    print(f"status: {task.status}")
    print(f"title: {task.title}")
    print(f"created_at: {task.created_at}")
    print(f"updated_at: {task.updated_at}")
    print(f"metadata: {json.dumps(task.metadata, ensure_ascii=False)}")
    return 0


def cmd_cron_list(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.cron_scheduler.list_jobs()
    if args.json:
        _print_json(payload)
        return 0
    for item in payload:
        print(f"{item['name']}: {item['schedule']} | due={item['due']} | last_status={item['last_status']}")
    return 0


def cmd_cron_run(args: argparse.Namespace) -> int:
    services = build_runtime()
    lane_payload = services.lane_scheduler.execute_sync(
        "cron",
        job_id=f"cron:{args.job}",
        payload={"job": args.job},
        callback=lambda: services.cron_scheduler.run_job(args.job),
    )
    payload = lane_payload["result"]
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_cron_tick(args: argparse.Namespace) -> int:
    services = build_runtime()
    lane_payload = services.lane_scheduler.execute_sync(
        "cron",
        job_id="cron:tick",
        payload={"job": "tick"},
        callback=lambda: services.cron_scheduler.run_due(),
    )
    payload = lane_payload["result"]
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_heart_status(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.heart_service.status()
    if args.json:
        _print_json(payload)
        return 0
    for item in payload["components"]:
        print(f"{item['component_id']}: {item['status']} (age={item['last_seen_age_sec']})")
    return 0


def cmd_heart_beat(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.heart_service.beat(
        args.component,
        status=args.status,
        queue_depth=args.queue_depth,
        active_task_count=args.active_task_count,
        last_error=args.last_error or "",
        latency_ms_p95=args.latency_ms_p95,
        failure_streak=args.failure_streak,
    )
    if args.json:
        _print_json(payload.to_dict())
        return 0
    print(json.dumps(payload.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_watchdog_scan(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = [decision.to_dict() for decision in services.watchdog.scan()]
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_watchdog_simulate(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.watchdog.simulate(args.component).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_todo_show(args: argparse.Namespace) -> int:
    services = build_runtime()
    session_id = _resolve_latest_id(args.session_id, args.last, services.todo_manager.latest_session_ids)
    if session_id is None:
        print("No todo snapshots available.")
        return 1
    payload = services.todo_manager.summarize(session_id)
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_context_show(args: argparse.Namespace) -> int:
    services = build_runtime()
    session_id = _resolve_latest_id(args.session_id, args.last, services.context_manager.latest_session_ids)
    if session_id is None:
        print("No context snapshots available.")
        return 1
    snapshot = services.context_manager.get(session_id)
    if snapshot is None:
        print(f"Context snapshot not found for session: {session_id}")
        return 1
    payload = snapshot.to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _delivery_handlers(services: RuntimeServices) -> dict[str, Callable[[dict[str, Any]], dict[str, Any] | str | None]]:
    def demo_echo(payload: dict[str, Any]) -> dict[str, Any]:
        return {"echo": payload}

    def always_fail(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(str(payload.get("message", "forced delivery failure")))

    return {
        "background_task": services.skill_runtime._handle_background_task,
        "subagent_task": services.agent_team_runtime._handle_subagent_task,
        "demo_echo": demo_echo,
        "always_fail": always_fail,
    }


def cmd_lanes_status(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.lane_scheduler.status()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_lanes_bump(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.lane_scheduler.bump_generation(args.lane).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_delivery_status(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.delivery_queue.status()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_delivery_enqueue(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = _parse_json_arg(args.payload_json, field="payload-json", expected_type=dict)
    item = services.delivery_queue.enqueue(args.kind, payload, max_attempts=args.max_attempts)
    if args.json:
        _print_json(item.to_dict())
        return 0
    print(json.dumps(item.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_delivery_process(args: argparse.Namespace) -> int:
    services = build_runtime()
    handlers = _delivery_handlers(services)
    dispatcher = ParallelDispatcher(
        delivery_queue=services.delivery_queue,
        handlers=handlers,
        team_limit_resolver=services.agent_team_runtime._resolve_team_limit,
    )
    allowed_kinds = set(args.kind) if getattr(args, "kind", None) else None
    payload = dispatcher.process(
        max_items=args.max_items,
        workers=args.workers,
        allowed_kinds=allowed_kinds,
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_delivery_recover(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = {"recovered_delivery_ids": services.delivery_queue.recover_pending()}
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_resilience_drill(args: argparse.Namespace) -> int:
    from codelite.core.llm import ModelResult

    class DrillClient:
        def __init__(self, scenario: str) -> None:
            self.scenario = scenario
            self.calls = 0

        def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResult:
            del messages, tools
            self.calls += 1
            if self.scenario == "overflow_then_fallback":
                if self.calls == 1:
                    raise RuntimeError("CONTEXT_OVERFLOW")
                if self.calls in {2, 3}:
                    raise RuntimeError("generic failure after compaction")
                return ModelResult(text="resilience succeeded via fallback", tool_calls=[])
            if self.scenario == "auth_then_retry":
                if self.calls == 1:
                    raise RuntimeError("AUTH_ERROR")
                return ModelResult(text="resilience succeeded after auth rotation", tool_calls=[])
            return ModelResult(text="resilience drill completed", tool_calls=[])

    services = build_runtime(model_client=DrillClient(args.scenario))
    result = services.resilience_runner.complete(
        messages=[{"role": "system", "content": "drill"}],
        tools=[],
        preferred_profile="fast",
        primary_client=services.agent_loop.model_client,
        session_id=services.session_store.new_session_id(),
    )
    payload = result.to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_hooks_doctor(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.hook_runtime.doctor()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_permissions_status(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = [item.to_dict() for item in services.permission_store.list_decisions(session_id=args.session_id, limit=args.limit)]
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_permissions_allow(args: argparse.Namespace) -> int:
    services = build_runtime()
    arguments = _parse_json_arg(args.arguments_json, field="arguments-json", expected_type=dict)
    payload = services.permission_store.remember(
        session_id=args.session_id,
        tool_name=args.tool,
        arguments=arguments,
        decision="allow",
        ttl_seconds=args.ttl_sec or services.config.runtime.permission_approval_ttl_sec,
        reason=args.reason or "",
    ).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_permissions_deny(args: argparse.Namespace) -> int:
    services = build_runtime()
    arguments = _parse_json_arg(args.arguments_json, field="arguments-json", expected_type=dict)
    payload = services.permission_store.remember(
        session_id=args.session_id,
        tool_name=args.tool,
        arguments=arguments,
        decision="deny",
        ttl_seconds=args.ttl_sec or services.config.runtime.permission_approval_ttl_sec,
        reason=args.reason or "",
    ).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_skills_load(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.skill_runtime.load_skill(args.name).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_skills_list(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.skill_runtime.list_skills()
    query = (args.query or "").strip().lower()
    if query:
        payload = [
            item
            for item in payload
            if query in str(item.get("name", "")).lower()
            or query in str(item.get("summary", "")).lower()
        ]
    payload = payload[: max(args.limit, 1)]
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_team_create(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.agent_team_runtime.create_team(
        name=args.name,
        strategy=args.strategy,
        max_subagents=args.max_subagents,
        metadata=_parse_json_arg(args.metadata_json, field="metadata-json", expected_type=dict) if args.metadata_json else {},
    ).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_team_list(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = [item.to_dict() for item in services.agent_team_runtime.list_teams()]
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_subagent_spawn(args: argparse.Namespace) -> int:
    services = build_runtime(model_client=getattr(args, "_model_client", None))
    metadata = _parse_json_arg(args.metadata_json, field="metadata-json", expected_type=dict) if args.metadata_json else {}
    agent_type = normalize_agent_type(getattr(args, "agent_type", GENERAL_PURPOSE_AGENT_TYPE))
    if args.mode == "sync":
        payload = services.agent_team_runtime.run_subagent_inline(
            team_id=args.team_id,
            prompt=args.prompt,
            agent_type=agent_type,
            parent_session_id=args.session_id,
            metadata=metadata,
        )
    else:
        payload = services.agent_team_runtime.spawn_subagent(
            team_id=args.team_id,
            prompt=args.prompt,
            agent_type=agent_type,
            parent_session_id=args.session_id,
            metadata=metadata,
            max_attempts=args.max_attempts,
        )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_subagent_process(args: argparse.Namespace) -> int:
    services = build_runtime(model_client=getattr(args, "_model_client", None))
    payload = services.agent_team_runtime.process_subagents(
        max_items=args.max_items,
        workers=args.workers,
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_subagent_list(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = [
        item.to_dict()
        for item in services.agent_team_runtime.list_subagents(
            team_id=args.team_id,
            status=args.status,
            limit=args.limit,
        )
    ]
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_subagent_show(args: argparse.Namespace) -> int:
    services = build_runtime()
    record = services.agent_team_runtime.get_subagent(args.subagent_id)
    if record is None:
        print(f"Subagent not found: {args.subagent_id}")
        return 1
    payload = record.to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_mcp_add(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.mcp_runtime.add_server(
        name=args.name,
        command=args.server_command,
        args=_parse_json_arg(args.args_json, field="args-json", expected_type=list) if args.args_json else [],
        env=_parse_json_arg(args.env_json, field="env-json", expected_type=dict) if args.env_json else {},
        cwd=args.cwd or "",
        description=args.description or "",
        enabled=not args.disabled,
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_mcp_list(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.mcp_runtime.list_servers()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_mcp_remove(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.mcp_runtime.remove_server(args.name)
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_mcp_call(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.mcp_runtime.call(
        name=args.name,
        request=_parse_json_arg(args.request_json, field="request-json", expected_type=dict),
        timeout_sec=args.timeout_sec,
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_background_run(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.skill_runtime.enqueue_background_task(
        name=args.name,
        payload=_parse_json_arg(args.payload_json, field="payload-json", expected_type=dict),
        session_id=args.session_id,
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_background_process(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.skill_runtime.process_background_tasks(
        max_items=args.max_items,
        workers=args.workers,
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_background_status(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.skill_runtime.background_status()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_retrieval_decide(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.retrieval_router.decide(args.prompt).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_retrieval_run(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.retrieval_router.run(args.prompt)
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_timeline(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.memory_runtime.timeline()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_keyword(args: argparse.Namespace) -> int:
    services = build_runtime()
    keywords = services.memory_runtime.keywords()
    entry_ids = keywords.get("index", {}).get(args.keyword.lower(), [])
    payload = {
        "keyword": args.keyword.lower(),
        "entry_ids": entry_ids,
        "entries": [
            services.memory_runtime.ledger.get(entry_id).to_dict()
            for entry_id in entry_ids
            if services.memory_runtime.ledger.get(entry_id) is not None
        ],
    }
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_trace(args: argparse.Namespace) -> int:
    services = build_runtime()
    entry = services.memory_runtime.ledger.get(args.entry_id)
    if entry is None:
        print(f"Memory entry not found: {args.entry_id}")
        return 1
    payload = entry.to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_files(args: argparse.Namespace) -> int:
    services = build_runtime()
    bootstrap = services.memory_runtime.bootstrap_memory_files()
    payload = {
        "bootstrap": bootstrap,
        "files": services.memory_runtime.memory_files(include_preview=True),
    }
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_prefs(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = {
        "count": len(services.memory_runtime.effective_preferences()),
        "items": services.memory_runtime.effective_preferences(),
    }
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_remember(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.memory_runtime.remember_preference(
        domain=args.domain,
        text=args.text,
        source="cli",
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_forget(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.memory_runtime.forget_preference(
        domain=args.domain,
        keyword=args.keyword,
        source="cli",
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_memory_audit(args: argparse.Namespace) -> int:
    services = build_runtime()
    timeline = services.memory_runtime.timeline()
    items = timeline.get("items", []) if isinstance(timeline, dict) else []
    kinds = {"memory_candidate", "memory_decision", "memory_file_update"}
    selected = [item for item in items if str(item.get("kind", "")) in kinds]
    payload = {
        "count": len(selected),
        "items": selected[-max(1, args.limit) :],
    }
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_model_route(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.model_router.select_profile(args.prompt).to_dict()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_critic_review(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.critic_refiner.review(prompt=args.prompt, answer=args.answer)
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_critic_log(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.critic_refiner.log_failure(
        kind=args.kind,
        message=args.message,
        metadata=_parse_json_arg(args.metadata_json, field="metadata-json", expected_type=dict) if args.metadata_json else {},
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_critic_refine(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.critic_refiner.refine_rules()
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


class CodeLiteShell:
    def __init__(
        self,
        services: RuntimeServices,
        session_id: str | None = None,
        *,
        label: str | None = None,
    ) -> None:
        self.services = services
        self.session_id = session_id or services.session_store.new_session_id()
        self.services.session_store.ensure_session(self.session_id)
        self._running = True
        env_label = os.environ.get("CODELITE_SHELL_LABEL", "")
        env_style = os.environ.get("CODELITE_SHELL_STYLE", "codex")
        self.label = (label or env_label or "CodeLite").strip() or "CodeLite"
        self._shell_style = (env_style or "codex").strip().lower()
        effort = (os.environ.get("CODELITE_REASONING_EFFORT") or os.environ.get("OPENAI_REASONING_EFFORT") or "xhigh").strip()
        self._reasoning_effort = effort or "xhigh"
        self.renderer = ShellRenderer(
            label=self.label,
            color_enabled=self._shell_supports_color(),
            style=self._shell_style,
        )
        self.mode = ShellMode.ACT
        self.turn_index = 0
        self.input_history: list[str] = []
        self._grouped_events: dict[str, list[str]] = {}
        self._last_runtime_poll_at = 0.0
        self._live_notifications: list[str] = []
        self._tool_cards: list[ToolCardData] = []
        self._pending_tool_arguments: dict[str, list[dict[str, Any]]] = {}
        self._pending_tool_status: dict[str, list[dict[str, Any]]] = {}
        self._status_lines_current_turn: list[str] = []
        self._status_events_current_turn: list[tuple[str, str]] = []

        self._status_block_line_count = 0

        self._submitted_live_prompt = ""

        self._live_turn_line_count = 0

        self._live_turn_active = False

        self._assistant_live_text = ""
        self._milestones_emitted_current_turn: set[str] = set()
        self._latest_model_usage: dict[str, Any] | None = None
        self._turn_history: list[dict[str, Any]] = []
        self._latest_watchdog_decision: dict[str, Any] | None = None
        self._pending_memory_candidate: dict[str, Any] | None = None
        self._pending_plan_confirmation: dict[str, Any] | None = None
        self._pending_plan_clarification: dict[str, Any] | None = None
        self._post_turn_view = self._resolve_post_turn_view(os.environ.get("CODELITE_SHELL_POST_TURN_VIEW"))
        self._prompt_read_count = 0
        self.auto_orchestrator = AutoOrchestrationPolicy(self.services.config.runtime)

    @staticmethod
    def _shell_supports_color() -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("CLICOLOR_FORCE") not in {None, "", "0"}:
            return True
        if os.environ.get("FORCE_COLOR") not in {None, "", "0"}:
            return True
        if os.environ.get("TERM", "").lower() == "dumb":
            return False
        try:
            return bool(sys.stdout.isatty())
        except Exception:
            return False

    def run(self) -> int:
        self._print_welcome()
        while self._running:
            try:
                raw = self._read_shell_input()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            try:
                if self._handle_plan_confirmation_input(raw):
                    continue
                if self._handle_plan_clarification_input(raw):
                    continue
                if not raw:
                    continue
                if self._handle_pending_memory_candidate_input(raw):
                    continue
                if self._handle_local_command(raw):
                    continue
                if self._handle_nl_local_shortcut(raw):
                    continue
                if self._maybe_start_plan_clarification(raw):
                    continue
                self._run_agent_turn(raw)
            except Exception as exc:  # pragma: no cover
                if not getattr(exc, "_shell_rendered", False):
                    print(f"[error] {exc}")
        return 0

    def _handle_nl_local_shortcut(self, raw: str) -> bool:
        text = raw.strip()
        if not text or text.startswith("/"):
            return False
        available_jobs = {item["name"] for item in self.services.cron_scheduler.list_jobs()}
        if not available_jobs and "cron" not in text.lower():
            return False
        decision = self._decide_cron_intent(text=text, available_jobs=available_jobs)
        if decision.intent != "toggle":
            return False
        self._handle_cron_command(text.split())
        return True

    def _print_welcome(self) -> None:
        print(self.renderer.render_welcome(self._welcome_data()))

    def _handle_plan_confirmation_input(self, raw: str) -> bool:
        pending = self._pending_plan_confirmation
        if pending is None:
            return False
        normalized = raw.strip()
        lowered = normalized.lower()
        execute_tokens = {
            "implement the plan",
            "implement plan",
            "execute the plan",
            "execute plan",
            "start execution",
            "execute now",
            "run plan",
            "/act",
            "act",
        }
        if not normalized or lowered in execute_tokens:
            self._pending_plan_confirmation = None
            plan_text = str(pending.get("plan_text", "")).strip()
            if not plan_text:
                self.mode = ShellMode.PLAN
                print("No proposed plan block captured. Staying in plan mode.")
                return True
            self._execute_confirmed_plan(plan_text)
            return True
        if normalized.startswith("/"):
            return False

        self._pending_plan_confirmation = None
        self._run_plan_revision(feedback=normalized, plan_text=str(pending.get("plan_text", "")))
        return True
    def _execute_confirmed_plan(self, plan_text: str) -> None:
        self.mode = ShellMode.ACT
        print(self.mode.status_text)
        execution_prompt = (
            "Execute the approved plan now. Do not restate the plan. "
            "Perform the work directly, show milestone updates, and conclude with final results.\n\n"
            + plan_text
        )
        self._run_agent_turn(execution_prompt)

    def _run_plan_revision(self, *, feedback: str, plan_text: str) -> None:
        self.mode = ShellMode.PLAN
        revision_prompt = (
            "Revise the previous proposed plan based on this feedback. "
            "Return only one complete <proposed_plan> block.\n\n"
            f"Feedback:\n{feedback}\n\n"
            "Previous plan:\n"
            + plan_text
        )
        self._run_agent_turn(revision_prompt)

    @staticmethod
    def _contains_proposed_plan(text: str) -> bool:
        payload = (text or "").lower()
        return "<proposed_plan>" in payload and "</proposed_plan>" in payload

    @staticmethod
    def _extract_proposed_plan_block(text: str) -> str:
        payload = text or ""
        match = re.search(r"<proposed_plan>\s*.*?\s*</proposed_plan>", payload, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return match.group(0).strip()

    @staticmethod
    def _prompt_has_plan_context(prompt: str) -> bool:
        text = prompt.strip()
        if not text:
            return False
        lowered = text.lower()
        compact_text = re.sub(r"\s+", "", text)
        goal_markers = (
            "implement", "fix", "refactor", "design", "build", "upgrade", "migrate",
            "optimize", "debug", "add", "update", "modify", "change", "create", "write", "test",
            "repair", "clean up", "investigate", "resolve", "ship", "improve", "lint", "validate",
        )
        goal_markers_zh = (
            "修复", "新增", "优化", "重构", "实现", "排查", "更新", "修改", "调整", "补", "恢复", "处理", "通过",
            "测试", "验证", "整理", "兼容", "回滚", "方案",
        )
        scope_markers = ("scope", "in scope", "out of scope", "boundary", "only", "within", "limited to")
        scope_markers_zh = ("范围", "仅限", "限定", "只改", "只在", "仅改", "只做")
        constraint_markers = (
            "constraint", "constraints", "must", "should", "compatible", "risk", "deadline",
            "without", "keep", "rollback", "acceptance", "validation", "preserve",
        )
        constraint_markers_zh = ("必须", "需要", "保持", "兼容", "风险", "回滚", "验收", "验证", "不要", "不能", "保留")
        output_markers = (
            "deliverable", "acceptance", "test", "tests", "milestone", "api", "interface", "output",
            "validate", "lint", "build", "pytest", "compileall",
        )
        output_markers_zh = ("测试", "用例", "验收", "验证", "接口", "输出", "命令", "回归", "构建")
        code_markers = (
            "file", "files", "module", "function", "class", "cli", "ui", "prompt", "command",
            "session", "renderer", "shell", "memory", "worktree", "bug", "error", "regression",
        )
        code_markers_zh = ("文件", "模块", "函数", "类", "路径", "命令", "界面", "提示", "会话", "工作区", "上下文", "记忆", "错误")
        has_goal = any(marker in lowered for marker in goal_markers) or any(marker in text for marker in goal_markers_zh)
        has_scope = any(marker in lowered for marker in scope_markers) or any(marker in text for marker in scope_markers_zh)
        has_constraints = any(marker in lowered for marker in constraint_markers) or any(marker in text for marker in constraint_markers_zh)
        has_output = any(marker in lowered for marker in output_markers) or any(marker in text for marker in output_markers_zh)
        has_code_context = any(marker in lowered for marker in code_markers) or any(marker in text for marker in code_markers_zh)
        has_structure = "\n" in text or any(token in text for token in (":", ";", ",", "：", "；", "，", "、"))
        has_file_ref = bool(re.search(r"(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+|\b[A-Za-z0-9_.-]+\.[A-Za-z0-9_]+\b", text))
        has_backticks = "`" in text
        strong_signals = has_file_ref or has_backticks or "tests/" in text or "tests\\" in text or "codelite/" in text or "codelite\\" in text
        detail_score = (
            int(has_scope)
            + int(has_constraints)
            + int(has_output)
            + int(has_structure)
            + int(has_file_ref)
            + int(has_backticks)
            + int(has_code_context)
        )
        if len(text) >= 120 and has_goal:
            return True
        if strong_signals and has_goal:
            return True
        if has_goal and len(compact_text) >= 18 and detail_score >= 2:
            return True
        if has_goal and len(compact_text) >= 24 and detail_score >= 1:
            return True
        if has_goal and has_code_context and len(text.split()) >= 4:
            return True
        return False

    def _needs_plan_clarification(self, prompt: str) -> bool:
        text = prompt.strip()
        if self.mode is not ShellMode.PLAN:
            return False
        if not text or text.startswith("/"):
            return False
        if self._contains_proposed_plan(text):
            return False
        if self._prompt_has_plan_context(text):
            return False
        lowered = text.lower()
        compact_text = re.sub(r"\s+", "", text)
        ambiguous_markers = (
            "optimize this", "fix this", "improve this", "figure out", "something", "this thing",
            "do one thing", "plan this", "help with this", "quickly do",
        )
        ambiguous_markers_zh = ("优化一下", "搞一下", "看一下", "弄一下", "处理一下", "这个流程", "这个问题", "这个东西", "规划一下")
        if any(marker in lowered for marker in ambiguous_markers) or any(marker in text for marker in ambiguous_markers_zh):
            return True
        return len(compact_text) <= 12

    def _build_plan_clarification_questions(self, prompt: str) -> list[dict[str, Any]]:
        _ = prompt
        return [
            {
                "question": "Which primary outcome should this plan optimize for?",
                "options": [
                    "Ship a correct implementation with clear acceptance criteria",
                    "Prioritize speed with a minimal viable solution",
                    "Prioritize robustness with extra risk controls",
                ],
            },
            {
                "question": "How should execution happen after the plan is approved?",
                "options": [
                    "Execute immediately after pressing Enter",
                    "Wait for explicit /act command",
                    "Revise once more before execution",
                ],
            },
            {
                "question": "What level of validation is required before completion?",
                "options": [
                    "Run targeted tests only",
                    "Run the unified validation pipeline",
                    "Run both targeted and full validation",
                ],
            },
        ]

    def _maybe_start_plan_clarification(self, raw: str) -> bool:
        if self._pending_plan_clarification is not None:
            return False
        if not self._needs_plan_clarification(raw):
            return False
        questions = self._build_plan_clarification_questions(raw)
        self._pending_plan_clarification = {
            "base_prompt": raw.strip(),
            "questions": questions,
            "answers": [],
            "cursor": 0,
        }
        print("Need a little more plan detail before execution. Please choose one option per question.")
        self._print_current_clarification_question()
        return True

    def _print_current_clarification_question(self) -> None:
        pending = self._pending_plan_clarification
        if pending is None:
            return
        questions = list(pending.get("questions", []))
        cursor = int(pending.get("cursor", 0))
        if cursor < 0 or cursor >= len(questions):
            return
        item = dict(questions[cursor])
        options = list(item.get("options", []))
        print(f"Question {cursor + 1}/{len(questions)}")
        print(item.get("question", ""))
        for index, option in enumerate(options, start=1):
            print(f"  {index}. {option}")
        print("Reply with: <number> [optional note]")

    @staticmethod
    def _parse_clarification_selection(raw: str) -> tuple[int | None, str]:
        text = raw.strip()
        if not text:
            return None, ""
        candidate = text
        note = ""
        if "\t" in candidate:
            head, tail = candidate.split("\t", 1)
            candidate = head.strip()
            note = tail.strip()
        else:
            parts = candidate.split(maxsplit=1)
            candidate = parts[0].strip() if parts else ""
            note = parts[1].strip() if len(parts) > 1 else ""
        if not candidate.isdigit():
            return None, ""
        return int(candidate), note

    def _handle_plan_clarification_input(self, raw: str) -> bool:
        pending = self._pending_plan_clarification
        if pending is None:
            return False
        normalized = raw.strip()
        if normalized.startswith("/"):
            return False
        selected_number, user_note = self._parse_clarification_selection(raw)
        if selected_number is None:
            print("Invalid selection. Please reply with 1 / 2 / 3, optionally followed by a note.")
            self._print_current_clarification_question()
            return True

        cursor = int(pending.get("cursor", 0))
        questions = list(pending.get("questions", []))
        if cursor < 0 or cursor >= len(questions):
            self._pending_plan_clarification = None
            return True
        options = list(dict(questions[cursor]).get("options", []))
        selected = selected_number - 1
        if selected < 0 or selected >= len(options):
            print("Selection out of range. Choose one listed option.")
            self._print_current_clarification_question()
            return True

        answers = list(pending.get("answers", []))
        answers.append({
            "question": str(dict(questions[cursor]).get("question", "")),
            "answer": str(options[selected]),
            "note": user_note,
        })
        pending["answers"] = answers
        pending["cursor"] = cursor + 1
        print(f"Captured answer {cursor + 1}/{len(questions)}")
        print(f"  Option: {options[selected]}")
        if user_note:
            print(f"  补充: {user_note}")

        if int(pending["cursor"]) < len(questions):
            self._print_current_clarification_question()
            return True

        base_prompt = str(pending.get("base_prompt", "")).strip()
        clarification_lines: list[str] = []
        for item in answers:
            line = f"- {item['question']} => {item['answer']}"
            note = str(item.get("note", "")).strip()
            if note:
                line += f" | 补充: {note}"
            clarification_lines.append(line)
        self._pending_plan_clarification = None
        clarified_prompt = (
            base_prompt
            + "\n\nClarification answers:\n"
            + "\n".join(clarification_lines)
            + "\n\nUse these constraints to produce one complete <proposed_plan>."
        )
        self._run_agent_turn(clarified_prompt)
        return True

    def _maybe_set_pending_plan_confirmation(self, answer: str) -> bool:
        if self.mode is not ShellMode.PLAN:
            return False
        plan_text = self._extract_proposed_plan_block(answer)
        if not plan_text:
            return False
        self._pending_plan_clarification = None
        self._pending_plan_confirmation = {
            "state": "awaiting_plan_confirmation",
            "plan_text": plan_text,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        print("Press Enter to execute plan, or type edits to refine plan.")
        return True

    def _read_shell_input(self) -> str:
        if self._use_live_input():
            return self._read_live_input().strip()
        if self._pending_memory_candidate is not None:
            preview = self._compact_preview(str(self._pending_memory_candidate.get("text", "")), max_chars=88)
            print(f"[memory pending] {preview} | yes/no")
        if self.renderer.is_codex_style() and self._prompt_read_count > 0:
            print(self._prompt_status_line())
        if self.renderer.is_claude_style():
            print(self._prompt_status_line())
        self._prompt_read_count += 1
        prompt_prefix = "planner" if self.mode is ShellMode.PLAN else "agent"
        return input(f"{prompt_prefix}> ").strip()

    def _use_live_input(self) -> bool:

        if not self.renderer.is_codex_style():

            return False

        plain_input = (os.environ.get("CODELITE_PLAIN_INPUT") or "").strip().lower()

        if plain_input not in {"", "0", "false", "no", "off"}:

            return False

        live_input = (os.environ.get("CODELITE_LIVE_INPUT") or "").strip().lower()

        if live_input in {"0", "false", "no", "off"}:

            return False

        try:

            return bool(sys.stdin.isatty()) and bool(sys.stdout.isatty())

        except Exception:

            return False

    def _prompt_status_line(self) -> str:

        return self.renderer.render_prompt_status(

            workspace_name=self.services.layout.workspace_root.name,

            session_id=self.session_id,

            mode=self.mode,

            model_name=str(self.services.config.llm.model),

            provider=str(self.services.config.llm.provider),

            reasoning_effort=self._reasoning_effort,

            remaining_percent=self._context_left_percent(),

            current_dir=str(Path.cwd().resolve()),

            runtime_summary=self._runtime_status_summary(),

        )

    @staticmethod
    def _is_live_input_mode_toggle(
        key: str,
        *,
        ctrl_pressed: bool = False,
        shift_pressed: bool = False,
        escape_sequence: str = "",
        extended_key: str = "",
    ) -> bool:
        if key == "\r":
            return ctrl_pressed
        if key == "\t":
            return shift_pressed
        if key == "\x1b":
            return escape_sequence == "[Z"
        if key in {"\x00", "\xe0"}:
            return extended_key in {"\x0f", "\x94"}
        return False

    def _read_live_input(self) -> str:
        import msvcrt
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except Exception:
            ctypes = None
            wintypes = None
            user32 = None
            kernel32 = None

        LEFT_CTRL_PRESSED = 0x0008
        RIGHT_CTRL_PRESSED = 0x0004
        SHIFT_PRESSED = 0x0010
        KEY_EVENT = 0x0001
        WAIT_OBJECT_0 = 0x00000000
        WAIT_TIMEOUT = 0x00000102

        VK_BACK = 0x08
        VK_TAB = 0x09
        VK_RETURN = 0x0D
        VK_ESCAPE = 0x1B
        VK_HOME = 0x24
        VK_LEFT = 0x25
        VK_UP = 0x26
        VK_RIGHT = 0x27
        VK_DOWN = 0x28
        VK_END = 0x23
        VK_DELETE = 0x2E
        VK_P = 0x50

        input_handle = None
        use_console_events = False
        _InputRecord = None

        def _is_shift_pressed() -> bool:
            if user32 is None:
                return False
            return bool(user32.GetAsyncKeyState(0x10) & 0x8000)

        def _is_ctrl_pressed() -> bool:
            if user32 is None:
                return False
            return bool(user32.GetAsyncKeyState(0x11) & 0x8000)

        if kernel32 is not None and ctypes is not None and wintypes is not None:
            class _CharUnion(ctypes.Union):
                _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", ctypes.c_char)]

            class _KeyEventRecord(ctypes.Structure):
                _fields_ = [
                    ("bKeyDown", wintypes.BOOL),
                    ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD),
                    ("wVirtualScanCode", wintypes.WORD),
                    ("uChar", _CharUnion),
                    ("dwControlKeyState", wintypes.DWORD),
                ]

            class _EventUnion(ctypes.Union):
                _fields_ = [
                    ("KeyEvent", _KeyEventRecord),
                    ("_padding", ctypes.c_byte * 16),
                ]

            class _InputRecordStruct(ctypes.Structure):
                _fields_ = [("EventType", wintypes.WORD), ("Event", _EventUnion)]

            _InputRecord = _InputRecordStruct
            try:
                input_handle = kernel32.GetStdHandle(-10)
                use_console_events = bool(input_handle not in {None, 0, -1} and sys.stdin.isatty() and sys.stdout.isatty())
            except Exception:
                input_handle = None
                use_console_events = False

        def _read_console_key_event() -> dict[str, Any] | None:
            if (
                not use_console_events
                or kernel32 is None
                or ctypes is None
                or wintypes is None
                or _InputRecord is None
                or input_handle is None
            ):
                return None
            record = _InputRecord()
            read = wintypes.DWORD()
            ok = kernel32.ReadConsoleInputW(input_handle, ctypes.byref(record), 1, ctypes.byref(read))
            if not ok or int(read.value) == 0:
                return None
            if int(record.EventType) != KEY_EVENT:
                return None
            key_event = record.Event.KeyEvent
            if not bool(key_event.bKeyDown):
                return None
            virtual_key = int(key_event.wVirtualKeyCode)
            if virtual_key in {0x10, 0x11, 0x12}:
                return None
            control_state = int(key_event.dwControlKeyState)
            return {
                "key": str(getattr(key_event.uChar, "UnicodeChar", "") or ""),
                "virtual_key": virtual_key,
                "ctrl_pressed": bool(control_state & (LEFT_CTRL_PRESSED | RIGHT_CTRL_PRESSED)),
                "shift_pressed": bool(control_state & SHIFT_PRESSED),
            }

        self._live_notifications = []
        model = ShellInputModel(
            commands=self._command_specs(),
            skills=self._skill_specs(),
            mode=self.mode,
            history=list(self.input_history),
        )
        line_count = self._paint_live_input(model, previous_line_count=0)

        while True:
            key = ""
            virtual_key = 0
            ctrl_pressed = False
            shift_pressed = False
            escape_sequence = ""
            extended = ""

            if use_console_events and kernel32 is not None and input_handle is not None:
                wait_result = kernel32.WaitForSingleObject(input_handle, 50)
                if wait_result == WAIT_TIMEOUT:
                    line_count = self._poll_runtime_tasks(model, previous_line_count=line_count)
                    continue
                if wait_result != WAIT_OBJECT_0:
                    line_count = self._poll_runtime_tasks(model, previous_line_count=line_count)
                    continue
                event = _read_console_key_event()
                if event is None:
                    continue
                key = str(event.get("key", ""))
                virtual_key = int(event.get("virtual_key", 0) or 0)
                ctrl_pressed = bool(event.get("ctrl_pressed", False))
                shift_pressed = bool(event.get("shift_pressed", False))
            else:
                if not msvcrt.kbhit():
                    line_count = self._poll_runtime_tasks(model, previous_line_count=line_count)
                    time.sleep(0.05)
                    continue
                key = msvcrt.getwch()
                if key in {"\r", "\n"}:
                    ctrl_pressed = _is_ctrl_pressed()
                elif key == "\t":
                    shift_pressed = _is_shift_pressed()
                elif key == "\x1b":
                    if msvcrt.kbhit():
                        next_char = msvcrt.getwch()
                        escape_sequence += next_char
                        if next_char == "[" and msvcrt.kbhit():
                            escape_sequence += msvcrt.getwch()
                elif key in {"\x00", "\xe0"}:
                    extended = msvcrt.getwch()

            if key in {"\r", "\n"} or virtual_key == VK_RETURN:
                normalized_key = "\n" if key == "\n" else "\r"
                if self._is_live_input_mode_toggle("\r", ctrl_pressed=ctrl_pressed):
                    model.toggle_mode()
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                if normalized_key == "\n":
                    model.insert_newline()
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                if model.focus is ShellInputFocus.COMMAND and model.confirm_suggestion():
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                if model.should_confirm_suggestion_on_enter():
                    model.confirm_suggestion()
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                raw = model.consume()
                self.mode = model.mode
                self._clear_live_input(line_count)
                return raw
            if key == "\x03":
                self.mode = model.mode
                self._clear_live_input(line_count)
                raise KeyboardInterrupt
            if key == "\x1a":
                self.mode = model.mode
                self._clear_live_input(line_count)
                raise EOFError
            if key == "\x08" or virtual_key == VK_BACK:
                model.backspace()
            elif key == "\x1b" or virtual_key == VK_ESCAPE:
                if self._is_live_input_mode_toggle(key, escape_sequence=escape_sequence):
                    model.toggle_mode()
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                model.set_focus(ShellInputFocus.EDITOR)
            elif key == "\t" or virtual_key == VK_TAB:
                if self._is_live_input_mode_toggle("\t", shift_pressed=shift_pressed):
                    model.toggle_mode()
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                if self._pending_plan_clarification is not None and not model.active_palette_prefix():
                    model.insert("\t")
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                model.autocomplete()
            elif key in {"\x00", "\xe0"} or virtual_key in {VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_DELETE, VK_UP, VK_DOWN}:
                if extended and self._is_live_input_mode_toggle(key, extended_key=extended):
                    model.toggle_mode()
                    line_count = self._paint_live_input(model, previous_line_count=line_count)
                    continue
                if extended == "K" or virtual_key == VK_LEFT:
                    model.move_left()
                elif extended == "M" or virtual_key == VK_RIGHT:
                    model.move_right()
                elif extended == "G" or virtual_key == VK_HOME:
                    model.move_home()
                elif extended == "O" or virtual_key == VK_END:
                    model.move_end()
                elif extended == "S" or virtual_key == VK_DELETE:
                    model.delete()
                elif extended == "H" or virtual_key == VK_UP:
                    if model.suggestions() and "\n" not in model.buffer:
                        model.set_focus(ShellInputFocus.COMMAND)
                        model.move_suggestion(-1)
                    elif "\n" in model.buffer:
                        model.move_up()
                    else:
                        model.history_previous()
                elif extended == "P" or virtual_key == VK_DOWN:
                    if model.suggestions() and "\n" not in model.buffer:
                        model.set_focus(ShellInputFocus.COMMAND)
                        model.move_suggestion(1)
                    elif "\n" in model.buffer:
                        model.move_down()
                    else:
                        model.history_next()
            elif key == "\x10" or (ctrl_pressed and virtual_key == VK_P):  # Ctrl+P toggles command focus
                model.toggle_focus()
            elif key.isprintable():
                model.insert(key)
            line_count = self._paint_live_input(model, previous_line_count=line_count)

    def _poll_runtime_tasks(self, model: ShellInputModel, *, previous_line_count: int) -> int:
        now = time.monotonic()
        if now - self._last_runtime_poll_at < 1.0:
            return previous_line_count
        self._last_runtime_poll_at = now

        due_results = self.services.cron_scheduler.run_due()
        terminal_messages = [
            item.get("result", {})
            for item in due_results
            if isinstance(item, dict) and isinstance(item.get("result"), dict)
        ]
        terminal_messages = [item for item in terminal_messages if item.get("type") == "terminal_message"]
        if not terminal_messages:
            return previous_line_count

        for item in terminal_messages:
            message = str(item.get("message", "")).strip()
            job_name = str(item.get("job_name", "")).strip()
            title = f"[Cron:{job_name}]" if job_name else "[Cron]"
            self._append_live_notification(f"{title} {message}")
        return self._paint_live_input(model, previous_line_count=previous_line_count)

    def _paint_live_input(self, model: ShellInputModel, *, previous_line_count: int) -> int:
        lines = self.renderer.render_live_input(
            model=model,
            workspace_name=self.services.layout.workspace_root.name,
            session_id=self.session_id,
            notifications=list(self._live_notifications),
            runtime_summary=self._runtime_status_summary(),
        )
        if previous_line_count:
            self._clear_live_input(previous_line_count)
        payload = "\n".join(f"\r\033[2K{line}" for line in lines)
        sys.stdout.write(payload)
        sys.stdout.flush()
        return len(lines)

    def _render_live_turn_lines(self) -> list[str]:
        lines = self._render_submitted_prompt_snapshot(include_cursor=True)

        lines.extend(self.renderer.render_status_block(self._status_display_lines()).splitlines())

        if self._assistant_live_text:

            lines.extend(self.renderer.render_assistant_output(self._assistant_live_text).splitlines())

        return lines

    def _render_submitted_prompt_snapshot(self, *, include_cursor: bool) -> list[str]:
        waiting_model = ShellInputModel(
            commands=self._command_specs(),
            skills=self._skill_specs(),
            mode=self.mode,
        )
        if self._submitted_live_prompt:
            waiting_model.set_buffer(self._submitted_live_prompt)
        lines = self.renderer.render_live_input(
            model=waiting_model,
            workspace_name=self.services.layout.workspace_root.name,
            session_id=self.session_id,
            runtime_summary=self._runtime_status_summary(),
            hint="",
        )
        if include_cursor:
            return lines
        return [line.replace("█", "") for line in lines]

    def _refresh_live_turn_display(self) -> None:

        if not self._supports_status_block_streaming():

            return


        lines = self._render_live_turn_lines()

        if self._live_turn_line_count > 0:

            self._clear_live_input(self._live_turn_line_count)

        payload = "\n".join(f"\r\033[2K{line}" for line in lines)

        sys.stdout.write(payload)

        sys.stdout.flush()

        self._live_turn_line_count = len(lines)

    def _clear_live_turn_display(self) -> None:

        if self._live_turn_line_count > 0:

            self._clear_live_input(self._live_turn_line_count)

        self._live_turn_line_count = 0

        self._live_turn_active = False

    @staticmethod
    def _clear_live_input(line_count: int) -> None:
        if line_count <= 0:
            return
        for _ in range(max(line_count - 1, 0)):
            sys.stdout.write("\r\033[2K\033[1A")
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()

    def _append_live_notification(self, message: str) -> None:
        text = message.strip()
        if not text:
            return
        self._live_notifications.append(text)
        if len(self._live_notifications) > 6:
            self._live_notifications = self._live_notifications[-6:]

    def _agent_prompt(self, raw: str) -> str:
        prefix = self.mode.guidance_prefix
        return raw if not prefix else prefix + raw

    def _turn_task_id(self, turn_index: int) -> str:
        return f"shell-{self.session_id[-8:]}-turn-{turn_index:02d}"

    def _auto_orchestration_decision(self, raw: str) -> AutoOrchestrationDecision:
        snapshot = self.services.todo_manager.get(self.session_id)
        return self.auto_orchestrator.decide(
            prompt=raw,
            mode=self.mode.value,
            worktree_available=self.services.worktree_manager is not None,
            todo_snapshot=snapshot,
        )

    def _run_agent_loop_turn(
        self,
        prompt: str,
        *,
        require_plan: bool,
        turn_timeout_sec: float | None = None,
        timeout_error_message: str | None = None,
    ) -> str:
        run_turn = self.services.agent_loop.run_turn
        if not require_plan:
            try:
                parameters = inspect.signature(run_turn).parameters
            except (TypeError, ValueError):
                parameters = {}
            kwargs: dict[str, Any] = {}
            if turn_timeout_sec is not None and "turn_timeout_sec" in parameters:
                kwargs["turn_timeout_sec"] = turn_timeout_sec
            if timeout_error_message and "timeout_error_message" in parameters:
                kwargs["timeout_error_message"] = timeout_error_message
            return run_turn(self.session_id, prompt, **kwargs)
        try:
            parameters = inspect.signature(run_turn).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs: dict[str, Any] = {}
        if "require_plan" in parameters:
            kwargs["require_plan"] = True
        if turn_timeout_sec is not None and "turn_timeout_sec" in parameters:
            kwargs["turn_timeout_sec"] = turn_timeout_sec
        if timeout_error_message and "timeout_error_message" in parameters:
            kwargs["timeout_error_message"] = timeout_error_message
        return run_turn(self.session_id, prompt, **kwargs)

    def _run_with_optional_spinner(self, runner: Callable[[], str]) -> str:
        return runner()

    def _run_worktree_turn(
        self,
        *,
        task_id: str,
        raw: str,
        cleaned: str,
        decision: AutoOrchestrationDecision,
        turn_timeout_sec: float | None = None,
        timeout_error_message: str | None = None,
    ) -> str:
        if self.services.worktree_manager is None:
            raise RuntimeError("worktree manager unavailable")

        runner = TaskRunner(
            workspace_root=self.services.layout.workspace_root,
            config=self.services.config,
            session_store=self.services.session_store,
            task_store=self.services.task_store,
            worktree_manager=self.services.worktree_manager,
            model_client=self.services.agent_loop.model_client,
            todo_manager=self.services.todo_manager,
            context_manager=self.services.context_manager,
            heart_service=self.services.heart_service,
            retrieval_router=self.services.retrieval_router,
            model_router=self.services.model_router,
            resilience_runner=self.services.resilience_runner,
            skill_runtime=self.services.skill_runtime,
            agent_team_runtime=self.services.agent_team_runtime,
            mcp_runtime=self.services.mcp_runtime,
            memory_runtime=self.services.memory_runtime,
            hook_runtime=self.services.hook_runtime,
        )
        prompt = self._agent_prompt(raw)
        title = decision.task_title_hint or (cleaned[:80] or task_id)
        lane_payload = self.services.lane_scheduler.execute_sync(
            "main",
            job_id=f"shell-task:{task_id}",
            payload={"task_id": task_id, "session_id": self.session_id},
            callback=lambda: runner.run(
                task_id=task_id,
                prompt=prompt,
                title=title,
                session_id=self.session_id,
                owner=f"shell:{self.session_id}",
                require_plan=decision.require_plan,
                turn_timeout_sec=turn_timeout_sec,
                timeout_error_message=timeout_error_message,
            ).to_dict(),
        )
        result = dict(lane_payload["result"] or {})
        worktree_payload = dict(result.get("worktree") or {})
        self.services.session_store.append_event(
            self.session_id,
            "auto_worktree_routed",
            {
                "task_id": task_id,
                "reason": decision.reason,
                "worktree_path": str(worktree_payload.get("path", "")),
            },
        )
        self._remember_group_event(
            "Mechanism",
            f"auto worktree route -> {worktree_payload.get('path', '')}",
        )
        return str(result.get("answer", ""))

    def _run_agent_turn(self, raw: str) -> None:
        turn_started_at = time.monotonic()
        cleaned = raw.strip()
        if cleaned:
            self._remember_history(cleaned)
        self.turn_index += 1
        current_turn = self.turn_index
        self._grouped_events = {}
        self._tool_cards = []
        self._pending_tool_arguments = {}
        self._pending_tool_status = {}
        self._status_lines_current_turn = []
        self._status_block_line_count = 0
        self._pending_plan_confirmation = None
        self._milestones_emitted_current_turn = set()
        self._status_events_current_turn = []

        self._live_turn_line_count = 0

        self._live_turn_active = False

        self._assistant_live_text = ""

        self._submitted_live_prompt = cleaned
        turn_timeout_sec = self._shell_turn_timeout_sec()
        timeout_error_message = self._shell_turn_timeout_message(turn_timeout_sec)

        decision = self._auto_orchestration_decision(cleaned)
        listener = self._build_session_listener()
        self.services.session_store.add_listener(listener)
        try:
            self.services.session_store.append_event(
                self.session_id,
                "auto_orchestrator_decision",
                decision.to_dict(),
            )
            header = self.renderer.render_turn_header(turn_index=current_turn, mode=self.mode, raw=cleaned)
            if header.strip():
                print(header)
            if self.renderer.is_codex_style() and self._supports_status_block_streaming():
                self._live_turn_active = True
                self._refresh_live_turn_display()
            task_id = self._turn_task_id(current_turn)
            routed_to_worktree = bool(decision.require_worktree and self.services.worktree_manager is not None)
            lease_id = ""
            if not routed_to_worktree:
                task_id, lease_id = self._auto_claim_turn_task(cleaned)
            else:
                self._remember_group_event("Mechanism", f"auto route to task/worktree: {task_id}")
            try:
                if routed_to_worktree:
                    answer = self._run_with_optional_spinner(
                        lambda: self._run_worktree_turn(
                            task_id=task_id,
                            raw=raw,
                            cleaned=cleaned,
                            decision=decision,
                            turn_timeout_sec=turn_timeout_sec,
                            timeout_error_message=timeout_error_message,
                        )
                    )
                else:
                    answer = self._run_with_optional_spinner(
                        lambda: self._run_agent_loop_turn(
                            self._agent_prompt(raw),
                            require_plan=decision.require_plan,
                            turn_timeout_sec=turn_timeout_sec,
                            timeout_error_message=timeout_error_message,
                        )
                    )
                    self._complete_turn_task(task_id, lease_id, answer)
            except Exception as exc:
                if routed_to_worktree:
                    self._remember_group_event("Mechanism", f"worktree routed task failed: {task_id}")
                else:
                    self._block_turn_task(task_id, lease_id, str(exc))
                self._append_status_event_line(kind="error", line=self._compact_preview(str(exc), max_chars=88))
                self._print_status_block()
                self._record_turn_history(
                    turn_index=current_turn,
                    prompt=cleaned,
                    status="error",
                    answer_preview="",
                    error=str(exc),
                    grouped_events=self._grouped_events,
                )
                self._submitted_live_prompt = ""
                self._assistant_live_text = ""
                try:
                    setattr(exc, "_shell_rendered", True)
                except Exception:
                    pass
                raise
        finally:
            self.services.session_store.remove_listener(listener)

        if self.mode is ShellMode.ACT:
            self._ensure_minimum_milestones()

        self._append_status_event_line(kind="done", line="response ready")
        self._print_status_block()
        self._print_assistant_output(answer)
        self._submitted_live_prompt = ""
        self._maybe_set_pending_plan_confirmation(answer)
        self._record_turn_history(
            turn_index=current_turn,
            prompt=cleaned,
            status="done",
            answer_preview=answer[:200],
            error="",
            grouped_events=self._grouped_events,
        )
        self._print_post_turn_summary(
            turn_index=current_turn,
            current_task_id=task_id,
            elapsed_s=time.monotonic() - turn_started_at,
        )
        if self._pending_plan_confirmation is None:
            self._stage_memory_candidate(cleaned)

    def _shell_turn_timeout_sec(self) -> float | None:
        raw_value = getattr(self.services.config.runtime, "shell_timeout_sec", 0)
        try:
            timeout_sec = float(raw_value)
        except (TypeError, ValueError):
            return None
        return timeout_sec if timeout_sec > 0 else None

    @staticmethod
    def _format_timeout_seconds(timeout_sec: float) -> str:
        rounded = round(float(timeout_sec), 3)
        if rounded.is_integer():
            return str(int(rounded))
        return f"{rounded:.3f}".rstrip("0").rstrip(".")

    def _shell_turn_timeout_message(self, timeout_sec: float | None = None) -> str:
        resolved = timeout_sec if timeout_sec is not None else self._shell_turn_timeout_sec()
        if resolved is None:
            return "shell turn timed out while waiting for model response"
        return (
            "shell turn timed out after "
            + f"{self._format_timeout_seconds(resolved)}s while waiting for model response"
        )

    def _handle_pending_memory_candidate_input(self, raw: str) -> bool:
        candidate = self._pending_memory_candidate
        if candidate is None:
            return False
        lowered = raw.strip().lower()
        if lowered in {"yes", "y", "ok", "confirm"}:
            self._accept_memory_candidate(candidate)
            self._pending_memory_candidate = None
            return True
        if lowered in {"no", "n", "skip", "cancel"}:
            self._reject_memory_candidate(candidate)
            self._pending_memory_candidate = None
            return True
        return False


    def _stage_memory_candidate(self, prompt: str) -> None:
        if self._pending_memory_candidate is not None:
            return
        candidate = self.services.memory_runtime.suggest_candidate(prompt)
        if candidate is None:
            return
        self._pending_memory_candidate = candidate
        self.services.memory_runtime.record_candidate(candidate=candidate, session_id=self.session_id)
        preview = self._compact_preview(str(candidate.get("text", "")), max_chars=88)
        domain = str(candidate.get("domain", "user"))
        domain_label = {"user": "user memory", "soul": "soul memory"}.get(domain, domain)
        print(
            "检测到记忆候选: "
            + preview
            + f" | {domain_label} | reply `yes` to remember, `no` to skip, or use `/memory remember ...`"
        )

    def _accept_memory_candidate(self, candidate: dict[str, Any]) -> None:
        domain = str(candidate.get("domain", "user")).strip() or "user"
        text = str(candidate.get("text", "")).strip()
        if not text:
            return
        try:
            payload = self.services.memory_runtime.remember_preference(
                domain=domain,
                text=text,
                source="candidate_yes",
            )
        except Exception as exc:
            print(f"Memory candidate save failed: {exc}")
            self.services.memory_runtime.record_candidate_decision(
                candidate=candidate,
                session_id=self.session_id,
                accepted=False,
            )
            return
        self.services.memory_runtime.record_candidate_decision(
            candidate=candidate,
            session_id=self.session_id,
            accepted=True,
        )
        print(
            "记忆已保存: "
            + self._compact_preview(str(payload.get("text", "")), max_chars=88)
            + f" ({payload.get('domain', 'user')})"
        )

    def _reject_memory_candidate(self, candidate: dict[str, Any]) -> None:
        self.services.memory_runtime.record_candidate_decision(
            candidate=candidate,
            session_id=self.session_id,
            accepted=False,
        )
        print("Memory candidate skipped.")

    def _print_assistant_output(self, answer: str) -> None:
        print(self.renderer.render_assistant_output(answer))

    def _remember_history(self, raw: str) -> None:
        if not raw:
            return
        if self.input_history and self.input_history[-1] == raw:
            return
        self.input_history.append(raw)
        if len(self.input_history) > 100:
            self.input_history = self.input_history[-100:]

    def _record_turn_history(
        self,
        *,
        turn_index: int,
        prompt: str,
        status: str,
        answer_preview: str,
        error: str,
        grouped_events: dict[str, list[str]],
    ) -> None:
        snapshot = {
            "turn_index": turn_index,
            "prompt": prompt,
            "status": status,
            "answer_preview": answer_preview,
            "error": error,
            "groups": {key: list(value) for key, value in grouped_events.items()},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._turn_history = [item for item in self._turn_history if int(item.get("turn_index", -1)) != turn_index]
        self._turn_history.append(snapshot)
        self._turn_history.sort(key=lambda item: int(item.get("turn_index", 0)))
        if len(self._turn_history) > 30:
            self._turn_history = self._turn_history[-30:]

    def _print_turn_fold_board(self, *, expanded_turn: int | None = None) -> None:
        if not self._turn_history:
            return
        chosen = expanded_turn if expanded_turn is not None else int(self._turn_history[-1]["turn_index"])
        error_turns = [item for item in self._turn_history if str(item.get("status")) == "error"]
        normal_turns = [item for item in self._turn_history if str(item.get("status")) != "error"]
        ordered = [*error_turns, *normal_turns]
        items: list[str] = []
        for item in ordered:
            turn_no = int(item.get("turn_index", 0))
            status = str(item.get("status", "done"))
            prompt = self._compact_preview(str(item.get("prompt", "")), max_chars=44)
            prefix = "expand" if turn_no == chosen else "collapse"
            status_badge = "[ERROR]" if status == "error" else "[DONE]"
            items.append(f"{prefix} turn{turn_no:02d} {status_badge} {prompt}")
            if turn_no != chosen:
                continue
            if status == "error":
                items.append(f"  error: {self._compact_preview(str(item.get('error', '')), max_chars=80)}")
            else:
                items.append(f"  answer: {self._compact_preview(str(item.get('answer_preview', '')), max_chars=80)}")
            groups = dict(item.get("groups") or {})
            for group_name in ("Receive", "Retrieve", "Think", "File", "Shell", "Web", "Subagent", "TODO", "Mechanism", "Done", "Error"):
                group_items = list(groups.get(group_name) or [])
                if not group_items:
                    continue
                items.append(f"  {group_name}: {self._compact_preview(group_items[-1], max_chars=76)}")
        summary = f"total turns {len(self._turn_history)} | error turns {len(error_turns)}"
        print(
            self.renderer.render_named_board(
                title="Turn Fold View",
                summary=summary,
                items=items[:18],
                empty_text="no turn history",
            )
        )

    def _build_session_listener(self) -> Callable[[dict[str, Any]], None]:
        session_id = self.session_id

        def listener(event: dict[str, Any]) -> None:
            if event.get("session_id") != session_id:
                return
            self._collect_runtime_artifacts(event)
            described = self._describe_runtime_event(event)
            if described:
                kind, line, group = described
                self._remember_group_event(group, line)
                self._append_status_event_line(kind=kind, line=line)
            self._maybe_emit_milestone_from_event(event)

        return listener

    def _append_status_event_line(self, *, kind: str, line: str) -> None:
        rendered = self.renderer.render_runtime_event(kind, line)
        self._status_lines_current_turn.append(rendered)

        self._status_events_current_turn.append((kind, line))

        if self._live_turn_active and self._supports_status_block_streaming():

            self._refresh_live_turn_display()

            return
        if not self.renderer.is_claude_style() and not self.renderer.is_codex_style():
            print(rendered)

    def _status_display_lines(self) -> list[str]:
        lines = [item for item in self._status_lines_current_turn if str(item).strip()]
        if not lines:
            return ["[WAIT] running..."]
        if not self.renderer.is_codex_style():
            return lines[-8:]
        if not self._status_events_current_turn:
            return lines[-3:]
        first_receive: str | None = None
        latest_retrieve: str | None = None
        latest_think: str | None = None
        latest_task: str | None = None
        latest_tool: str | None = None
        latest_done: str | None = None
        latest_error: str | None = None
        for kind, raw in self._status_events_current_turn:
            rendered = self.renderer.render_runtime_event(kind, raw)
            if kind == "receive" and first_receive is None:
                first_receive = rendered
                continue
            if kind == "retrieve":
                lowered = raw.lower()
                if "route=none" not in lowered and "route=skip" not in lowered:
                    latest_retrieve = rendered
                continue
            if kind == "think":
                latest_think = rendered
                continue
            if kind == "task":
                lowered = raw.lower()
                if lowered.startswith("auto decision "):
                    continue
                if any(token in lowered for token in ("subagent", "worktree", "team=", "route to task/worktree")):
                    latest_task = rendered
                continue
            if kind == "tool":
                latest_tool = rendered
                continue
            if kind == "done":
                latest_done = rendered
                continue
            if kind == "error":
                latest_error = rendered
        selected = [
            item
            for item in (first_receive, latest_retrieve, latest_think, latest_task, latest_tool, latest_error or latest_done)
            if item is not None
        ]
        return selected[-6:] or lines[-3:]

    def _supports_status_block_streaming(self) -> bool:

        return self._use_live_input()

    def _refresh_status_block_live(self) -> None:

        if self._live_turn_active:

            self._refresh_live_turn_display()

            return

        rendered = self.renderer.render_status_block(self._status_display_lines())

        if not self._supports_status_block_streaming():

            print(rendered)

            self._status_block_line_count = 0

            return

        lines = rendered.splitlines() or [rendered]

        if self._status_block_line_count > 0:

            self._clear_live_input(self._status_block_line_count)

        payload = "\n".join(f"\r\033[2K{line}" for line in lines)

        sys.stdout.write(payload + "\n")

        sys.stdout.flush()

        self._status_block_line_count = len(lines) + 1

    @staticmethod
    def _milestone_stage_from_event(event: dict[str, Any]) -> str | None:
        event_type = str(event.get("event_type", ""))
        if event_type in {"turn_started", "retrieval_decision", "model_request"}:
            return "discover"
        if event_type in {"auto_orchestrator_decision", "model_response", "auto_plan_gate_injected"}:
            return "plan"
        if event_type in {"tool_started", "tool_finished", "auto_worktree_routed"}:
            return "implement"
        if event_type in {"resilience_result", "todo_nag", "session_compacted"}:
            return "verify"
        if event_type in {"turn_finished", "turn_failed"}:
            return "wrapup"
        if event_type != "message":
            return None

        payload = dict(event.get("payload") or {})
        role = str(payload.get("role", ""))
        if role == "assistant" and payload.get("tool_calls"):
            return "implement"
        if role == "assistant":
            return "plan"
        if role == "tool":
            return "implement"
        return None

    def _maybe_emit_milestone_from_event(self, event: dict[str, Any]) -> None:
        stage = self._milestone_stage_from_event(event)
        if not stage:
            return
        self._emit_milestone(stage)

    def _emit_milestone(self, stage: str) -> None:
        if stage in self._milestones_emitted_current_turn:
            return

        templates: dict[str, tuple[str, str, str]] = {
            "discover": (
                "Discover",
                "Collected context and identified the working area.",
                "Select a concrete implementation path.",
            ),
            "plan": (
                "Plan",
                "Locked the next concrete actions.",
                "Execute the first action and report progress.",
            ),
            "implement": (
                "Implement",
                "Applied code-level changes for the current step.",
                "Run focused checks before moving on.",
            ),
            "validate": (
                "Validate",
                "Executed verification and reviewed outcomes.",
                "Fix remaining issues and rerun validation.",
            ),
            "wrapup": (
                "Wrap Up",
                "Prepared final summary and next actions.",
                "Present concise outcome and follow-ups.",
            ),
            "failure": (
                "Failure",
                "Hit an issue that needs correction.",
                "Diagnose root cause and retry safely.",
            ),
        }

        title, summary, next_action = templates.get(
            stage,
            (
                "Progress",
                f"Updated stage: {stage}",
                "Continue with the next concrete step.",
            ),
        )

        self._remember_group_event("Milestone", f"{title}: {summary}")
        self._status_lines_current_turn.append(f"milestone {len(self._milestones_emitted_current_turn) + 1}: {summary}")
        self._status_lines_current_turn.append(f"next: {next_action}")
        self._milestones_emitted_current_turn.add(stage)
    def _ensure_minimum_milestones(self) -> None:
        for stage in ("discover", "plan", "wrapup"):
            self._emit_milestone(stage)

    def _print_status_block(self) -> None:
        if not self.renderer.is_codex_style():
            return
        if self._live_turn_active:
            self._clear_live_turn_display()
        if self._submitted_live_prompt:
            print("\n".join(self._render_submitted_prompt_snapshot(include_cursor=False)))
        print(self.renderer.render_status_block(self._status_display_lines()))

    def _describe_runtime_event(self, event: dict[str, Any]) -> tuple[str, str, str] | None:
        event_type = str(event.get("event_type", ""))
        payload = dict(event.get("payload") or {})

        if event_type == "turn_started":
            return ("receive", "request received", "Receive")
        if event_type == "retrieval_decision":
            decision = payload.get("decision") or {}
            route = str(decision.get("route", "none"))
            if route in {"", "none", "skip"}:

                return None

            result_count = len(payload.get("results") or [])

            return ("retrieve", f"retrieval route={route} hits={result_count}", "Retrieve")
        if event_type == "model_request":
            return ("think", "thinking", "Think")
        if event_type == "model_stream":

            stream_type = str(payload.get("type", "")).strip().lower()

            if stream_type == "reset":

                self._assistant_live_text = ""

            elif stream_type == "text":

                self._assistant_live_text += str(payload.get("text", ""))

            if self._live_turn_active and self._supports_status_block_streaming():

                self._refresh_live_turn_display()

            return

        if event_type == "model_response":
            tool_call_count = int(payload.get("tool_call_count", 0) or 0)
            if tool_call_count > 0:

                return ("think", f"tools planned: {tool_call_count}", "Think")

            return ("think", "drafting response", "Think")
        if event_type == "resilience_result":
            attempts = list(payload.get("attempts") or [])

            if len(attempts) <= 1:

                return None

            profile = payload.get("profile") or payload.get("preferred_profile") or payload.get("selected_profile") or "default"

            return ("think", f"retry path -> {profile}", "Think")
        if event_type == "auto_orchestrator_decision":
            return (
                "task",
                "auto decision "
                + f"plan={payload.get('require_plan', False)} "
                + f"worktree={payload.get('require_worktree', False)} "
                + f"reason={payload.get('reason', 'default')}",
                "Mechanism",
            )
        if event_type == "auto_plan_gate_injected":
            return ("think", f"plan gate step={payload.get('step', '?')}", "TODO")
        if event_type == "auto_worktree_routed":
            path = str(payload.get("worktree_path", "")).strip()
            return ("task", f"worktree routed: {path}", "Mechanism")
        if event_type == "todo_nag":
            return ("think", f"todo reminder: {payload.get('message', '')}", "TODO")
        if event_type == "tool_started":
            tool_name = str(payload.get("tool_name", "unknown"))
            return ("tool", self._describe_tool_start(tool_name, payload.get("arguments") or {}), self._tool_group_name(tool_name))
        if event_type == "tool_finished":
            tool_name = str(payload.get("tool_name", "unknown"))
            if payload.get("status") == "error":
                return ("error", f"tool failed: {payload.get('tool_name', 'unknown')} | {payload.get('error', '')}", self._tool_group_name(tool_name))
            return ("tool", f"tool done: {payload.get('tool_name', 'unknown')}", self._tool_group_name(tool_name))
        if event_type == "session_compacted":
            keep_turns = payload.get("keep_turns", "?")
            dropped = payload.get("dropped_message_count", 0)
            return ("done", f"context compacted keep_turns={keep_turns} dropped={dropped}", "Done")
        if event_type == "turn_finished":
            return ("done", "turn finished", "Done")
        if event_type == "turn_failed":
            return ("error", f"turn failed: {payload.get('error', '')}", "Error")
        if event_type != "message":
            return None

        role = payload.get("role")
        if role == "assistant" and payload.get("tool_calls"):
            names = ", ".join(call.get("function", {}).get("name", "unknown") for call in payload.get("tool_calls", []))
            return ("tool", f"assistant plans tools: {names}", self._combined_tool_group(payload.get("tool_calls", [])))
        if role == "tool":
            content = str(payload.get("content", "")).strip()
            tool_name = str(payload.get("name", "unknown"))
            return ("tool", self._summarize_tool_output(tool_name, content), self._tool_group_name(tool_name))
        return None
    def _collect_runtime_artifacts(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type", ""))
        payload = dict(event.get("payload") or {})
        if event_type == "model_response":
            usage = payload.get("usage")
            if isinstance(usage, dict) and usage:
                self._latest_model_usage = dict(usage)
            return
        if event_type == "tool_started":
            tool_name = str(payload.get("tool_name", "unknown"))
            self._pending_tool_arguments.setdefault(tool_name, []).append(dict(payload.get("arguments") or {}))
            return
        if event_type == "tool_finished":
            tool_name = str(payload.get("tool_name", "unknown"))
            self._pending_tool_status.setdefault(tool_name, []).append(
                {
                    "status": str(payload.get("status", "ok")),
                    "error": str(payload.get("error", "")),
                }
            )
            return
        if event_type != "message":
            return
        if payload.get("role") == "assistant" and not payload.get("tool_calls"):

            self._assistant_live_text = str(payload.get("content", "") or "")

            if self._live_turn_active and self._supports_status_block_streaming():

                self._refresh_live_turn_display()

            return

        if payload.get("role") != "tool":
            return

        tool_name = str(payload.get("name", "unknown"))
        content = str(payload.get("content", "") or "")
        arguments = self._pop_pending_item(self._pending_tool_arguments, tool_name, default={})
        status_payload = self._pop_pending_item(
            self._pending_tool_status,
            tool_name,
            default={"status": "ok", "error": ""},
        )
        self._tool_cards.append(
            self._build_tool_card(
                tool_name=tool_name,
                arguments=dict(arguments),
                content=content,
                status_payload=dict(status_payload),
            )
        )
        if len(self._tool_cards) > 16:
            self._tool_cards = self._tool_cards[-16:]

    @staticmethod
    def _pop_pending_item(
        queue: dict[str, list[dict[str, Any]]],
        tool_name: str,
        *,
        default: dict[str, Any],
    ) -> dict[str, Any]:
        items = queue.get(tool_name)
        if not items:
            return dict(default)
        head = dict(items.pop(0))
        if not items:
            queue.pop(tool_name, None)
        return head

    def _build_tool_card(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        content: str,
        status_payload: dict[str, Any],
    ) -> ToolCardData:
        normalized_status = str(status_payload.get("status", "ok")).lower()
        error_detail = str(status_payload.get("error", "") or "")
        tool_error = self._tool_error_detail(content)
        if tool_error:
            normalized_status = "failed"
            if not error_detail:
                error_detail = tool_error

        if tool_name in {"read_file", "list_files", "write_file", "edit_file"}:
            return self._build_file_tool_card(
                tool_name=tool_name,
                arguments=arguments,
                status=normalized_status,
                error_detail=error_detail,
                content=content,
            )
        if tool_name == "bash":
            return self._build_shell_tool_card(
                arguments=arguments,
                status=normalized_status,
                error_detail=error_detail,
                content=content,
            )
        if tool_name == "web_search":
            return self._build_search_tool_card(
                arguments=arguments,
                status=normalized_status,
                error_detail=error_detail,
                content=content,
            )
        if tool_name in {"team_create", "team_list", "subagent_spawn", "subagent_process", "subagent_status"}:
            return self._build_team_tool_card(
                tool_name=tool_name,
                arguments=arguments,
                status=normalized_status,
                error_detail=error_detail,
                content=content,
            )
        if tool_name == "todo_write":
            return self._build_todo_tool_card(
                arguments=arguments,
                status=normalized_status,
                error_detail=error_detail,
                content=content,
            )
        preview = self._compact_preview(content, max_chars=72)
        return ToolCardData(
            tool_name=tool_name,
            card_kind="generic",
            status=normalized_status,
            title=f"Tool Card | {tool_name}",
            lines=[
                f"args: {self._preview_json(arguments)}",
                f"result: {preview}" if preview else "result: (empty)",
            ],
        )

    def _build_file_tool_card(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        error_detail: str,
        content: str,
    ) -> ToolCardData:
        path_value = str(arguments.get("path", "")).strip() or "."
        lines = [f"path: {path_value}"]
        if tool_name == "list_files":
            lines.append(f"depth: {arguments.get('max_depth', 2)}")
        if status == "failed":
            lines.append(f"error: {self._compact_preview(error_detail or content, max_chars=88)}")
        else:
            lines.append(f"result: {self._compact_preview(content, max_chars=88)}")
        return ToolCardData(
            tool_name=tool_name,
            card_kind="file",
            status=status,
            title=f"File Card | {tool_name}",
            lines=lines,
        )

    def _build_shell_tool_card(
        self,
        *,
        arguments: dict[str, Any],
        status: str,
        error_detail: str,
        content: str,
    ) -> ToolCardData:
        command = str(arguments.get("command", "")).strip()
        detail = error_detail or content
        blocked = any(token in detail.lower() for token in ("blocked", "policy", "denied"))
        exit_code = 0
        exit_match = re.search(r"exit=(\d+)", detail)
        if exit_match:
            exit_code = int(exit_match.group(1))
        elif status == "failed":
            exit_code = 1
        lines = [
            f"Command: {self._compact_preview(command, max_chars=88)}",
            "Platform: Windows PowerShell",
            f"Exit code: {exit_code}",
            f"Policy blocked: {'yes' if blocked else 'no'}",
        ]
        if status == "failed" and detail:
            lines.append(f"Error: {self._compact_preview(detail, max_chars=88)}")
        return ToolCardData(
            tool_name="bash",
            card_kind="shell",
            status=status,
            title="Shell Card | bash",
            lines=lines,
        )

    def _build_search_tool_card(
        self,
        *,
        arguments: dict[str, Any],
        status: str,
        error_detail: str,
        content: str,
    ) -> ToolCardData:
        query = str(arguments.get("query", "")).strip()
        lines = [f"query: {self._compact_preview(query, max_chars=88)}"]
        if status == "failed":
            lines.append(f"error: {self._compact_preview(error_detail or content, max_chars=88)}")
            return ToolCardData(
                tool_name="web_search",
                card_kind="search",
                status=status,
                title="Search Card | web_search",
                lines=lines,
            )

        payload = self._json_or_none(content)
        if isinstance(payload, dict):
            answer = self._compact_preview(str(payload.get("answer", "") or ""), max_chars=80)
            results = payload.get("results") or []
            lines.append(f"results: {len(results) if isinstance(results, list) else 0}")
            if answer:
                lines.append(f"answer: {answer}")
            if isinstance(results, list) and results:
                source_preview = []
                for item in results[:2]:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    url = str(item.get("url", "")).strip()
                    host = self._host_from_url(url)
                    display_source = host or url
                    source_preview.append(self._compact_preview(f"{title} | {display_source}", max_chars=72))
                if source_preview:
                    lines.append("sources: " + " ; ".join(source_preview))
        else:
            lines.append(f"result: {self._compact_preview(content, max_chars=88)}")
        return ToolCardData(
            tool_name="web_search",
            card_kind="search",
            status=status,
            title="Search Card | web_search",
            lines=lines,
        )

    def _build_team_tool_card(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        error_detail: str,
        content: str,
    ) -> ToolCardData:
        lines = [f"action: {tool_name}"]
        payload = self._json_or_none(content)
        if status == "failed":
            lines.append(f"error: {self._compact_preview(error_detail or content, max_chars=88)}")
        elif isinstance(payload, dict):
            if "team_id" in payload:
                lines.append(f"team_id: {payload.get('team_id')}")
            if "subagent" in payload and isinstance(payload.get("subagent"), dict):
                subagent = payload["subagent"]
                lines.append(
                    "subagent: "
                    f"{str(subagent.get('subagent_id', ''))[:8]} "
                    f"| status={subagent.get('status', '')} "
                    f"| type={subagent.get('agent_type', GENERAL_PURPOSE_AGENT_TYPE)}"
                )
            elif "subagent_id" in payload:
                lines.append(
                    "subagent: "
                    f"{str(payload.get('subagent_id', ''))[:8]} "
                    f"| status={payload.get('status', '')} "
                    f"| type={payload.get('agent_type', GENERAL_PURPOSE_AGENT_TYPE)}"
                )
            else:
                lines.append(f"result: {self._compact_preview(content, max_chars=88)}")
        elif isinstance(payload, list):
            lines.append(f"result_items: {len(payload)}")
        else:
            lines.append(f"args: {self._preview_json(arguments)}")
        return ToolCardData(
            tool_name=tool_name,
            card_kind="team",
            status=status,
            title=f"Team Card | {tool_name}",
            lines=lines,
        )

    def _build_todo_tool_card(
        self,
        *,
        arguments: dict[str, Any],
        status: str,
        error_detail: str,
        content: str,
    ) -> ToolCardData:
        item_count = len(arguments.get("items", []) or [])
        lines = [f"submitted_items: {item_count}"]
        if status == "failed":
            lines.append(f"error: {self._compact_preview(error_detail or content, max_chars=88)}")
        else:
            lines.append(f"result: {self._compact_preview(content, max_chars=88)}")
        return ToolCardData(
            tool_name="todo_write",
            card_kind="todo",
            status=status,
            title="Todo Card | todo_write",
            lines=lines,
        )

    @staticmethod
    def _tool_error_detail(content: str) -> str:
        text = content.strip()
        if not text.startswith("TOOL_ERROR:"):
            return ""
        return text[len("TOOL_ERROR:") :].strip()

    @staticmethod
    def _json_or_none(text: str) -> Any:
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _host_from_url(url: str) -> str:
        text = str(url).strip()
        if not text:
            return ""
        parsed = urlparse(text if "://" in text else f"https://{text}")
        host = (parsed.netloc or parsed.path).strip().lower()
        if host.startswith("www."):
            host = host[4:]
        return host.split("/", 1)[0]

    @staticmethod
    def _compact_preview(text: str, *, max_chars: int) -> str:
        clean = " ".join(str(text).split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max_chars - 3] + "..."

    @staticmethod
    def _preview_json(payload: dict[str, Any]) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= 90:
            return text
        return text[:87] + "..."

    def _describe_tool_start(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "bash":
            return f"run shell: {str(arguments.get('command', ''))[:80]}"
        if name == "read_file":
            return f"read file: {arguments.get('path', '')}"
        if name == "list_files":
            return f"list files: {arguments.get('path', '.')} | depth={arguments.get('max_depth', 2)}"
        if name == "web_search":
            return f"web search: {str(arguments.get('query', ''))[:80]}"
        if name == "team_create":
            return f"create team: {arguments.get('name', '')} | max_subagents={arguments.get('max_subagents', 3)}"
        if name == "subagent_spawn":
            return (
                f"spawn subagent: team={arguments.get('team_id', '')} "
                f"| type={arguments.get('agent_type', GENERAL_PURPOSE_AGENT_TYPE)}"
            )
        if name == "subagent_process":
            return f"process subagent queue: max_items={arguments.get('max_items', 20)}"
        if name == "todo_write":
            item_count = len(arguments.get("items", []) or [])
            return f"update todo: {item_count} items"
        return f"start tool {name} {self._preview_json(arguments)}"

    def _summarize_tool_output(self, name: str, content: str) -> str:
        if content.startswith("TOOL_ERROR:"):
            detail = content[len("TOOL_ERROR:") :].strip()
            lower_detail = detail.lower()
            if "single command only" in lower_detail or "pipe" in lower_detail:
                return "shell command blocked by policy (single simple command only)"
            if "windows powershell" in lower_detail:
                return detail
            if len(detail) > 100:
                detail = detail[:97] + "..."
            return f"{name} error: {detail}"

        if name == "bash":
            return "shell returned output"
        if name == "read_file":
            return "file content loaded"
        if name == "list_files":
            lines = [item for item in content.splitlines() if item.strip()]
            preview = ", ".join(lines[:3])
            return f"directory listed, total {len(lines)} items" + (f" | {preview}" if preview else "")
        if name == "web_search":
            try:
                payload = json.loads(content)
                result_count = len(payload.get("results", []) or [])
                answer = str(payload.get("answer", "") or "").strip()
                answer_preview = answer[:60] + ("..." if len(answer) > 60 else "")
                return f"web search done, {result_count} results" + (f" | {answer_preview}" if answer_preview else "")
            except Exception:
                return "web search done"
        if name == "team_create":
            try:
                payload = json.loads(content)
                return f"team created: {payload.get('team_id', '')}"
            except Exception:
                return "team created"
        if name == "subagent_spawn":
            try:
                payload = json.loads(content)
                subagent = payload.get("subagent", {})
                return (
                    f"subagent queued: {subagent.get('subagent_id', '')[:8]} "
                    f"| team={subagent.get('team_id', '')} "
                    f"| type={subagent.get('agent_type', GENERAL_PURPOSE_AGENT_TYPE)}"
                )
            except Exception:
                return "subagent created"
        if name == "subagent_process":
            try:
                payload = json.loads(content)
                if isinstance(payload, list):
                    return f"subagent queue processed, {len(payload)} results"
            except Exception:
                pass
            return "subagent queue processed"
        if name == "todo_write":
            return content
        return content[:100] + ("..." if len(content) > 100 else "")

    @staticmethod
    def _tool_group_name(name: str) -> str:
        if name in {"read_file", "list_files", "write_file", "edit_file"}:
            return "File"
        if name == "bash":
            return "Shell"
        if name == "web_search":
            return "Web"
        if name in {"team_create", "team_list", "subagent_spawn", "subagent_process", "subagent_status"}:
            return "Subagent"
        if name == "todo_write":
            return "TODO"
        return "Tool"

    def _combined_tool_group(self, tool_calls: list[dict[str, Any]]) -> str:
        groups = {
            self._tool_group_name(str(call.get("function", {}).get("name", "unknown")))
            for call in tool_calls
        }
        if len(groups) == 1:
            return next(iter(groups))
        return "Tool"

    def _remember_group_event(self, group: str, line: str) -> None:
        bucket = self._grouped_events.setdefault(group, [])
        if bucket and bucket[-1] == line:
            return
        bucket.append(line)
        if len(bucket) > 8:
            del bucket[:-8]

    @staticmethod
    def _resolve_post_turn_view(raw: str | None) -> str:
        value = (raw or "").strip().lower()
        if value in {"full", "verbose", "workbench", "expanded"}:
            return "full"
        return "compact"

    def _print_post_turn_summary(self, *, turn_index: int, current_task_id: str, elapsed_s: float) -> None:
        if self._post_turn_view == "full":
            self._print_turn_fold_board(expanded_turn=turn_index)
            self._print_grouped_timeline()
            self._print_tool_cards()
            self._print_team_board()
            self._print_boards(current_task_id=current_task_id)
            self._print_runtime_workbench_panel()
            self._print_memory_workbench_panel()
            return
        tool_count = len(self._tool_cards)
        event_count = sum(len(items) for items in self._grouped_events.values())
        footer = self.renderer.render_compact_turn_footer(
            turn_index=turn_index,
            mode=self.mode,
            tool_count=tool_count,
            task_id=current_task_id,
            elapsed_s=elapsed_s,
            event_count=event_count,
        )
        if footer.strip():
            print(footer)

    def _handle_local_command(self, raw: str) -> bool:
        normalized = raw.strip()
        if not normalized:
            return False

        if normalized == "/":
            return self._dispatch_local_command("help", [])

        resolved = self._resolve_shell_local_command(normalized)
        if resolved is not None:
            return self._dispatch_local_command(resolved, [])
        if normalized.startswith("session replay"):
            try:
                tokens = shlex.split(normalized)
            except ValueError as exc:
                print(f"[error] {exc}")
                return True
            return self._dispatch_local_command("replay", tokens[2:])
        if normalized.startswith("rename "):
            try:
                tokens = shlex.split(normalized)
            except ValueError as exc:
                print(f"[error] {exc}")
                return True
            return self._dispatch_local_command("rename", tokens[1:])
        if normalized.startswith("resume "):
            try:
                tokens = shlex.split(normalized)
            except ValueError as exc:
                print(f"[error] {exc}")
                return True
            return self._dispatch_local_command("resume", tokens[1:])

        try:
            tokens = shlex.split(normalized)
        except ValueError as exc:
            print(f"[error] {exc}")
            return True
        for inline_command in ("cron", "heart", "queue", "task", "watchdog", "background", "validate", "ops", "compact"):
            token = f"/{inline_command}"
            if token in tokens and not normalized.startswith(token):
                args = [item for item in tokens if item != token]
                return self._dispatch_local_command(inline_command, args)
        if not tokens or not tokens[0].startswith("/"):
            return False
        resolved = self._resolve_shell_local_command(tokens[0])
        if resolved is None:
            return False
        return self._dispatch_local_command(resolved, tokens[1:])

    def _dispatch_local_command(self, command: str, args: list[str]) -> bool:
        command = self._resolve_shell_local_command(command) or command.strip().lower()
        if command in {"exit", "quit", "q"}:
            self._running = False
            return True
        if command in {"help", "h"}:
            self._print_help()
            return True
        if command == "version":
            print(__version__)
            return True
        if command in {"status", "health"}:
            _print_json(build_health_snapshot(self.services))
            return True
        if command in {"clear", "cls"}:
            self._clear_screen()
            return True
        if command in {"welcome", "banner"}:
            self._print_welcome()
            return True
        if command in {"session", "sid"}:
            if args and args[0].lower() == "replay":
                self._print_session_replay(args[1:])
                return True
            _print_json(self._session_summary())
            return True
        if command == "resume":
            self._handle_resume_command(args)
            return True
        if command == "rename":
            self._handle_rename_command(args)
            return True
        if command == "replay":
            self._print_session_replay(args)
            return True
        if command == "todo":
            _print_json(self.services.todo_manager.summarize(self.session_id))
            return True
        if command == "context":
            snapshot = self.services.context_manager.get(self.session_id)
            if snapshot is None:
                print(f"No context snapshot for session: {self.session_id}")
            else:
                _print_json(snapshot.to_dict())
            return True
        if command in {"runtime", "metrics"}:
            self._handle_runtime_command(args)
            return True
        if command in {"memory", "momery"}:
            self._handle_memory_command(args)
            return True
        if command in {"skills", "skill"}:
            self._handle_skills_command(args)
            return True
        if command == "retrieval":
            self._handle_retrieval_command(args)
            return True
        if command == "cron":
            self._handle_cron_command(args)
            return True
        if command == "heart":
            self._handle_heart_command(args)
            return True
        if command == "queue":
            self._handle_queue_command(args)
            return True
        if command == "locks":
            self._handle_locks_command(args)
            return True
        if command == "tasks":
            print(self.renderer.render_task_board(self._task_board_data()))
            return True
        if command == "task":
            self._handle_task_command(args)
            return True
        if command == "team":
            self._handle_team_command(args)
            return True
        if command == "turns":
            self._handle_turns_command(args)
            return True
        if command in {"view", "ui"}:
            self._handle_view_command(args)
            return True
        if command in {"ops", "workbench"}:
            self._handle_ops_command(args)
            return True
        if command == "watchdog":
            self._handle_watchdog_local_command(args)
            return True
        if command == "lanes":
            self._print_lanes_delivery_panel()
            return True
        if command == "delivery":
            self._handle_queue_command(args)
            return True
        if command in {"model", "critic"}:
            self._print_model_resilience_critic_panel()
            return True
        if command == "mcp":
            self._handle_mcp_local_command(args)
            return True
        if command == "background":
            self._handle_background_local_command(args)
            return True
        if command == "validate":
            self._handle_validate_local_command(args)
            return True
        if command == "compact":
            self._handle_compact_command(args)
            return True
        if command in {"plan", "planner"}:
            self.mode = ShellMode.PLAN
            print(self.mode.status_text)
            if args:
                self._run_agent_turn(" ".join(args).strip())
            return True
        if command in {"act", "accept"}:
            self.mode = ShellMode.ACT
            print(self.mode.status_text)
            if args:
                self._run_agent_turn(" ".join(args).strip())
            return True
        if command == "mode":
            if args and args[0].lower() in {"plan", "act"}:
                self.mode = ShellMode(args[0].lower())
                if len(args) > 1:
                    print(self.mode.status_text)
                    self._run_agent_turn(" ".join(args[1:]).strip())
                    return True
            print(self.mode.status_text)
            return True
        if command in {"new", "reset"}:
            self._start_new_session()
            return True
        if command == "subagents":
            self._handle_subagents_command(args)
            return True
        return False

    def _start_new_session(self) -> None:
        self.session_id = self.services.session_store.new_session_id()
        self.services.session_store.ensure_session(self.session_id)
        print(f"Started new session: {self.session_id}")

    def _handle_rename_command(self, args: list[str]) -> None:
        title = " ".join(args).strip()
        if not title:
            print("Usage: /rename <new-thread-name>")
            return
        try:
            self.services.session_store.rename_session(self.session_id, title)
        except Exception as exc:
            print(f"Rename failed: {exc}")
            return
        print(f"Thread renamed: {title}")

    def _handle_resume_command(self, args: list[str]) -> None:
        if args:
            session_ref = " ".join(args).strip()
            summary = self._resolve_session_summary(session_ref)
            if summary is None:
                print(f"Session not found: {session_ref}")
                return
            self._resume_session(summary)
            return
        self._interactive_resume_selector()

    def _interactive_resume_selector(self) -> None:
        query = ""
        while True:
            sessions = self.services.session_store.list_session_summaries(limit=20, query=query)
            if not sessions:
                if query:
                    print(f"No sessions matched `{query}`.")
                    query = ""
                    continue
                print("No previous sessions found.")
                return

            print("Resume a previous session  Sort: Updated at")
            print("Type to search")
            print("  #  Created at       Updated at       Branch  Conversation")
            for index, item in enumerate(sessions, start=1):
                marker = ">" if index == 1 else " "
                created = self._relative_time_label(str(item.get("created_at", "")))
                updated = self._relative_time_label(str(item.get("updated_at", "")))
                conversation = self._compact_preview(str(item.get("conversation", "")), max_chars=42)
                print(f"{marker} {index:>2} {created:<15} {updated:<15} {'-':<6} {conversation}")

            choice = input("Resume # / search (Enter to cancel): ").strip()
            if not choice:
                print("Resume cancelled.")
                return
            if choice.isdigit():
                picked_index = int(choice) - 1
                if picked_index < 0 or picked_index >= len(sessions):
                    print("Selection out of range.")
                    continue
                self._resume_session(sessions[picked_index])
                return

            exact = self._resolve_session_summary(choice)
            if exact is not None:
                self._resume_session(exact)
                return
            query = choice

    def _resolve_session_summary(self, session_ref: str) -> dict[str, Any] | None:
        normalized = session_ref.strip()
        if not normalized:
            return None
        session_list = self.services.session_store.list_session_summaries(limit=100, include_system=True)
        lowered = normalized.lower()
        for item in session_list:
            if str(item.get("session_id", "")).strip() == normalized:
                return item
        for item in session_list:
            if str(item.get("session_id", "")).strip().startswith(normalized):
                return item
        for item in session_list:
            title = str(item.get("title", "")).strip().lower()
            if title and title == lowered:
                return item
        return None

    def _resume_session(self, summary: dict[str, Any]) -> None:
        session_id = str(summary.get("session_id", "")).strip()
        if not session_id:
            print("Session not found.")
            return
        self.session_id = session_id
        self.services.session_store.ensure_session(self.session_id)
        conversation = str(summary.get("conversation", "")).strip() or session_id
        print(f"Resumed session: {conversation} ({session_id})")

    @staticmethod
    def _relative_time_label(timestamp: str) -> str:
        raw = timestamp.strip()
        if not raw:
            return "-"
        try:
            target = datetime.fromisoformat(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - target.astimezone(timezone.utc)
            total_seconds = max(0, int(delta.total_seconds()))
        except Exception:
            return "-"
        if total_seconds < 60:
            return "just now"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        if total_seconds < 86400:
            hours = total_seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = total_seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"

    def _handle_team_command(self, args: list[str]) -> None:
        if not args:
            self._run_team_demo(user_request="", source="shell_team_default")
            self._print_team_board()
            return
        action = args[0].strip().lower()
        if action in {"board", "show", "status"}:
            self._print_team_board()
            return
        if action in {"help", "-h", "--help"}:
            print("Usage: /team [board | run <task> | <task>]")
            return
        if action == "run":
            user_request = " ".join(args[1:]).strip()
            self._run_team_demo(user_request=user_request, source="shell_team_run")
            self._print_team_board()
            return
        user_request = " ".join(args).strip()
        self._run_team_demo(user_request=user_request, source="shell_team_inline")
        self._print_team_board()

    def _handle_subagents_command(self, args: list[str]) -> None:
        user_request = " ".join(args).strip()
        if not user_request:
            print("Usage: /subagents <task>")
            return
        self._run_team_demo(user_request=user_request, source="shell_subagents_alias")


    def _run_team_demo(self, *, user_request: str, source: str) -> None:
        default_team = self.services.agent_team_runtime.ensure_default_team()
        assignments = self._build_team_demo_assignments(
            user_request=user_request,
            max_subagents=max(1, int(default_team.max_subagents)),
        )
        if not assignments:
            print("No runnable team tasks were generated.")
            return
        worker_labels = "/".join(item["worker"] for item in assignments)
        if user_request.strip():
            print(f"[Agent Team] request: {user_request}")
        else:
            print("[Agent Team] running default demo tasks")
        print(f"[Agent Team] workers: {worker_labels}")

        print()
        agent_label = "agent" if len(assignments) == 1 else "agents"
        print(f"[Agent Team] Waiting for {len(assignments)} {agent_label}")
        print("[Agent Team] 并行分工 / assignments:")
        for item in assignments:
            print(f"  - {item['worker']} [{item.get('agent_type', EXPLORE_AGENT_TYPE)}]")

        future_to_assignment: dict[Future[dict[str, Any]], dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(assignments), thread_name_prefix="team-demo") as executor:
            for item in assignments:
                future = executor.submit(
                    self.services.agent_team_runtime.run_subagent_inline,
                    team_id=default_team.team_id,
                    prompt=item["prompt"],
                    agent_type=str(item.get("agent_type", EXPLORE_AGENT_TYPE)),
                    parent_session_id=self.session_id,
                    metadata={
                        "source": source,
                        "worker_name": item["worker"],
                        "agent_type": str(item.get("agent_type", EXPLORE_AGENT_TYPE)),
                        "task_title": item["title"],
                        "user_request": user_request,
                    },
                )
                future_to_assignment[future] = item

            done, pending = wait(set(future_to_assignment.keys()), return_when=FIRST_COMPLETED)
            print()
            print("[Agent Team] first result batch received")
            results: list[dict[str, Any]] = []
            for future in sorted(done, key=lambda item: int(future_to_assignment[item]["index"])):
                result = self._collect_team_future_result(future, future_to_assignment[future])
                results.append(result)
                self._print_team_result_line(result)

            pending_futures = set(pending)
            if pending_futures:
                print()
                print("[Agent Team] Waiting for remaining agents")
                print(f"[Agent Team] pending: {len(pending_futures)}")
                for future in sorted(pending_futures, key=lambda item: int(future_to_assignment[item]["index"])):
                    assignment = future_to_assignment[future]
                    print(f"  - {assignment['worker']} [{assignment.get('agent_type', EXPLORE_AGENT_TYPE)}]")
                done_pending, _ = wait(pending_futures)
                print()
                print("[Agent Team] Finished waiting")
                for future in sorted(done_pending, key=lambda item: int(future_to_assignment[item]["index"])):
                    result = self._collect_team_future_result(future, future_to_assignment[future])
                    results.append(result)
                    self._print_team_result_line(result)

            if not pending_futures:
                print()
                print("[Agent Team] Finished waiting")

        final_results = sorted(results, key=lambda item: int(item["index"]))

        print()
        print("[Agent Team] closing inline subagents")
        for item in assignments:
            print(f"  - closed {item['worker']} [{item.get('agent_type', EXPLORE_AGENT_TYPE)}]")

        print()
        print(f"[Agent Team] run complete: {len(assignments)} agent(s)")
        print()
        print("[Agent Team] 并行分工 / task assignments:")
        for index, result in enumerate(final_results, start=1):
            subagent_id = str(result.get("subagent_id", "")).strip()
            print(f"{index}. agent {result['worker']} ({subagent_id[:8] if subagent_id else 'n/a'})")
            print(f"   task: {result['title']}")

        print()
        print("[Agent Team] 团队汇总结论（合并版） / result summary:")
        for index, result in enumerate(final_results, start=1):
            summary = self._compact_preview(str(result.get("answer", "")), max_chars=180)
            if str(result.get("status", "")).lower() == "failed":
                print(f"{index}. {result['title']} - failed: {summary}")
            else:
                print(f"{index}. {result['title']} - {summary}")
        print("[Agent Team] done.")
        print("[Agent Team] full artifacts: runtime/agent-team")

    def _collect_team_future_result(
        self,
        future: Future[dict[str, Any]],
        assignment: dict[str, Any],
    ) -> dict[str, Any]:
        worker = str(assignment["worker"])
        title = str(assignment["title"])
        index = int(assignment["index"])
        try:
            payload = future.result()
        except Exception as exc:
            return {
                "index": index,
                "worker": worker,
                "title": title,
                "agent_type": str(assignment.get("agent_type", EXPLORE_AGENT_TYPE)),
                "status": "failed",
                "status_label": "Failed",
                "answer": str(exc),
                "subagent_id": "",
            }

        subagent = dict(payload.get("subagent") or {})
        result = dict(payload.get("result") or {})
        status = str(subagent.get("status", result.get("status", "done"))).strip().lower()
        status_label = "Completed" if status == "done" else status.capitalize() or "Completed"
        answer = self._load_subagent_answer(str(result.get("result_path", "")))
        if not answer:
            answer = str(subagent.get("result_preview", "")).strip()
        if not answer:
            answer = "(empty answer)"
        return {
            "index": index,
            "worker": worker,
            "title": title,
            "agent_type": str(subagent.get("agent_type", assignment.get("agent_type", EXPLORE_AGENT_TYPE))),
            "status": status or "done",
            "status_label": status_label,
            "answer": answer,
            "subagent_id": str(subagent.get("subagent_id", "")).strip(),
        }

    def _print_team_result_line(self, result: dict[str, Any]) -> None:
        preview = self._compact_preview(str(result.get("answer", "")), max_chars=220)
        print(f"  L {result['worker']} [{result.get('agent_type', EXPLORE_AGENT_TYPE)}]: {result['status_label']} -- {preview}")

    def _build_team_demo_assignments(self, *, user_request: str, max_subagents: int) -> list[dict[str, Any]]:
        tasks: list[tuple[str, str]] = []
        text = user_request.strip()
        if not text:
            tasks = self._default_team_demo_tasks()
        else:
            splits = self._split_team_user_request(text)
            if len(splits) <= 1:
                tasks = self._fallback_team_review_tasks(text)
            else:
                tasks = [(self._compact_preview(item, max_chars=42), item) for item in splits]
        if not tasks:
            tasks = self._default_team_demo_tasks()

        bounded = tasks[: max(1, int(max_subagents))]
        workers = self._team_worker_names()
        assignments: list[dict[str, Any]] = []
        for index, (title, task) in enumerate(bounded):
            worker = workers[index % len(workers)]
            assignments.append(
                {
                    "index": index,
                    "worker": worker,
                    "title": title.strip() or f"task-{index + 1}",
                    "prompt": self._team_prompt(task),
                    "agent_type": EXPLORE_AGENT_TYPE,
                }
            )
        return assignments

    @staticmethod
    def _split_team_user_request(user_request: str) -> list[str]:
        chunks: list[str] = []
        for raw_chunk in re.split(r"[;\n；]+", user_request):
            chunk = raw_chunk.strip(" ,.;；")
            if not chunk:
                continue
            matched = re.match(r"^(?:\d+[.)]|[A-Za-z][.)])\s*(.+)$", chunk)
            if matched:
                chunk = matched.group(1).strip()
            if chunk:
                chunks.append(chunk)
        if len(chunks) >= 2:
            return chunks
        payload = user_request.strip()
        return [payload] if payload else []

    @staticmethod
    def _default_team_demo_tasks() -> list[tuple[str, str]]:
        return [
            (
                "Review acceptance criteria",
                "Read docs/acceptance and summarize target outcomes, guardrails, and open questions.",
            ),
            (
                "Inspect changed modules",
                "Check the most relevant runtime and CLI files for behavior changes and potential regressions.",
            ),
            (
                "Prepare handoff checklist",
                "Draft a concise handoff checklist with exact validation commands and expected results.",
            ),
        ]

    @staticmethod
    def _fallback_team_review_tasks(user_request: str) -> list[tuple[str, str]]:
        scope = user_request.strip()
        return [
            (
                "Clarify request",
                f"Restate the request as implementation goal + acceptance criteria: {scope}",
            ),
            (
                "Map evidence",
                f"Identify relevant files, commands, and signals to validate: {scope}",
            ),
            (
                "Draft execution",
                f"Create an execution checklist with risks and rollback notes for: {scope}",
            ),
        ]

    @staticmethod
    def _team_prompt(task: str) -> str:
        return (
            "You are an explore-type subagent in the CodeLite team demo.\n"
            "Focus on concrete findings and concise outputs.\n\n"
            f"Task:\n{task}\n\n"
            "Constraints:\n"
            "- Stay within the current workspace.\n"
            "- Avoid destructive actions.\n"
            "- Prefer actionable bullets over long prose.\n"
        )
    @staticmethod

    def _team_worker_names() -> list[str]:
        return ["Herschel", "Tesla", "Pascal", "Curie", "Noether", "Turing"]

    @staticmethod
    def _load_subagent_answer(result_path: str) -> str:
        raw_path = result_path.strip()
        if not raw_path:
            return ""
        path = Path(raw_path)
        if not path.exists():
            return ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        answer = str(payload.get("answer", "")).strip()
        return answer

    @classmethod
    def _shell_local_command_lookup(cls) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for command in _SHELL_LOCAL_COMMANDS:
            lookup[command.name] = command.name
            for alias in command.aliases:
                lookup[alias] = command.name
        return lookup

    @classmethod
    def _resolve_shell_local_command(cls, raw: str) -> str | None:
        token = raw.strip().lower()
        if token.startswith("/"):
            token = token[1:]
        if not token:
            return None
        return cls._shell_local_command_lookup().get(token)

    @staticmethod
    def _command_help_lines() -> list[str]:
        usage_width = max(len(item.usage) for item in _SHELL_LOCAL_COMMANDS)
        return [f"/{item.usage:<{usage_width}}  {item.help_text}" for item in _SHELL_LOCAL_COMMANDS]
    def _print_help(self) -> None:
        print(self.renderer.render_help(self._command_help_lines()))

    def _handle_runtime_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"show", "status", "panel", "metrics"}:
            self._print_runtime_workbench_panel()
            return
        action = args[0].lower()
        if action in {"refresh", "rollup"}:
            metrics_path = self.services.reconciler.rollup_metrics()
            print(
                self.renderer.render_named_board(
                    title="Runtime metrics refreshed",
                    summary=metrics_path.name,
                    items=[str(metrics_path)],
                    empty_text="metrics file not generated",
                )
            )
            self._print_runtime_workbench_panel()
            return
        print("Unknown runtime command. Use status|refresh.")

    def _handle_memory_command(self, args: list[str]) -> None:
        if not args:
            self._interactive_memory_files()
            return
        action = args[0].lower()
        if action in {"files", "manage"}:
            self._interactive_memory_files()
            return
        if action in {"prefs", "preferences"}:
            self._print_memory_preferences()
            return
        if action == "remember":
            if len(args) < 3:
                print("Usage: /memory remember <agent|user|soul|tool> <text>")
                return
            domain = args[1].strip()
            text = " ".join(args[2:]).strip()
            try:
                payload = self.services.memory_runtime.remember_preference(domain=domain, text=text, source="shell")
            except Exception as exc:
                print(f"remember failed: {exc}")
                return
            print(
                self.renderer.render_named_board(
                    title="Memory Remembered",
                    summary=f"{payload.get('domain', '')} | added={payload.get('added', False)}",
                    items=[
                        f"path: {payload.get('path', '')}",
                        f"text: {self._compact_preview(str(payload.get('text', '')), max_chars=96)}",
                    ],
                    empty_text="no memory update",
                )
            )
            return
        if action == "forget":
            if len(args) < 3:
                print("Usage: /memory forget <agent|user|soul|tool> <keyword>")
                return
            domain = args[1].strip()
            keyword = " ".join(args[2:]).strip()
            try:
                payload = self.services.memory_runtime.forget_preference(domain=domain, keyword=keyword, source="shell")
            except Exception as exc:
                print(f"forget failed: {exc}")
                return
            print(
                self.renderer.render_named_board(
                    title="Memory Forget",
                    summary=f"{payload.get('domain', '')} | removed={payload.get('removed_count', 0)}",
                    items=[
                        f"path: {payload.get('path', '')}",
                        f"keyword: {payload.get('keyword', '')}",
                    ],
                    empty_text="no matches removed",
                )
            )
            return
        if action == "audit":
            limit = 12
            if len(args) > 1 and args[1].isdigit():
                limit = max(1, int(args[1]))
            entries = self._recent_memory_entries(
                limit=limit,
                kinds={"memory_candidate", "memory_decision", "memory_file_update"},
            )
            print(
                self.renderer.render_named_board(
                    title="Memory Audit",
                    summary=f"entries={len(entries)}",
                    items=[self._format_memory_entry(item, max_chars=96) for item in entries],
                    empty_text="No memory audit entries yet",
                )
            )
            return
        if action in {"show", "status", "panel"}:
            self._print_memory_workbench_panel()
            return
        if action in {"open", "edit"}:
            if len(args) > 1 and args[1].lower() == "ledger":
                self._open_memory_ledger_editor()
                return
            if len(args) > 1:
                self._open_memory_file(args[1].strip())
                return
            self._open_memory_file("agent")
            return
        if action == "full":
            limit = 0
            if len(args) > 1 and args[1].isdigit():
                limit = max(0, int(args[1]))
            entries = self._memory_timeline_items()
            if limit > 0:
                entries = entries[-limit:]
            print(
                self.renderer.render_named_board(
                    title="Memory Full Ledger",
                    summary=f"entries={len(entries)}",
                    items=[self._format_memory_entry(item, max_chars=96) for item in entries],
                    empty_text="No memory entries yet",
                )
            )
            return
        if action in {"timeline", "recent"}:
            limit = 8
            if len(args) > 1 and args[1].isdigit():
                limit = max(1, int(args[1]))
            entries = self._recent_memory_entries(limit=limit)
            print(
                self.renderer.render_named_board(
                    title="Memory Timeline",
                    summary=f"recent={len(entries)}",
                    items=[self._format_memory_entry(item) for item in entries],
                    empty_text="No memory entries",
                )
            )
            return
        if action == "skills":
            skill_counts = self._top_index_items(self._memory_skill_index(), limit=8)
            print(
                self.renderer.render_named_board(
                    title="Memory Skills Index",
                    summary=f"skills={len(self._memory_skill_index())}",
                    items=[f"{name} x {count}" for name, count in skill_counts],
                    empty_text="No skill memory yet",
                )
            )
            return
        if action in {"keywords", "keyword"}:
            keyword_index = self._memory_keyword_index()
            if len(args) == 1:
                recent_keywords = self._recent_memory_keywords(limit=8)
                print(
                    self.renderer.render_named_board(
                        title="Memory Keywords",
                        summary=f"keywords={len(keyword_index)}",
                        items=[f"{name} x {count}" for name, count in recent_keywords],
                        empty_text="No keyword index yet",
                    )
                )
                return
            keyword = args[1].strip().lower()
            entry_ids = keyword_index.get(keyword, [])
            matched_entries = [item for item in self._recent_memory_entries(limit=24) if item.get("entry_id") in set(entry_ids)]
            items = [f"entry_ids: {len(entry_ids)}"]
            items.extend(self._format_memory_entry(item) for item in matched_entries[:6])
            print(
                self.renderer.render_named_board(
                    title="Memory Keyword Hits",
                    summary=f"{keyword} | hits={len(entry_ids)}",
                    items=items,
                    empty_text="No hits",
                )
            )
            return
        if action in {"trace", "show"}:
            if len(args) < 2:
                print("Usage: /memory trace <entry_id>")
                return
            entry = self._find_memory_entry(args[1].strip())
            if entry is None:
                print(f"Memory entry not found: {args[1].strip()}")
                return
            items = [
                f"kind: {entry.get('kind', 'unknown')}",
                f"created_at: {entry.get('created_at', '')}",
                f"text: {self._compact_preview(str(entry.get('text', '')), max_chars=96)}",
            ]
            metadata = entry.get("metadata")
            evidence = entry.get("evidence")
            if metadata:
                items.append(f"metadata: {self._compact_preview(_json_text(metadata), max_chars=96)}")
            if evidence:
                items.append(f"evidence_count: {len(evidence)}")
            print(
                self.renderer.render_named_board(
                    title="Memory Entry",
                    summary=str(entry.get("entry_id", "")),
                    items=items,
                    empty_text="No details",
                )
            )
            return
        if action in {"json", "raw"}:
            _print_json(self.services.memory_runtime.timeline())
            return
        print(
            "Unknown memory command. Use: files|open [file]|remember|forget|prefs|audit|full [N]|timeline [N]|"
            "skills|keywords [word]|trace <entry_id>|json. "
            "For natural language memory, just state your preference/style directly."
        )

    def _interactive_memory_files(self) -> None:
        payload = self.services.memory_runtime.bootstrap_memory_files()
        files = self.services.memory_runtime.memory_files(include_preview=True)
        items: list[str] = []
        for index, item in enumerate(files, start=1):
            title = str(item.get("title", ""))
            key = str(item.get("key", ""))
            preview = self._compact_preview(str(item.get("preview", "")), max_chars=56)
            path = str(item.get("path", ""))
            items.append(f"{index}. {title} ({key}) | {preview} | {path}")
        print(
            self.renderer.render_named_board(
                title="Memory Files",
                summary=(
                    f"files={len(files)} | created={len(payload.get('created_files', []))} | "
                    f"migrated={len(payload.get('migrated_files', []))}"
                ),
                items=[
                    *items,
                    "edit_hint: use /memory open <agent|user|soul|tool|memory> to edit one file",
                ],
                empty_text="No memory files configured",
            )
        )

    def _open_memory_file(self, memory_ref: str) -> None:
        try:
            path = self.services.memory_runtime.open_memory_file(memory_ref)
        except Exception as exc:
            print(f"Open memory file failed: {exc}")
            return
        self._open_path_in_editor(path, label="memory file")

    def _print_memory_preferences(self) -> None:
        prefs = self.services.memory_runtime.effective_preferences()
        items = [
            f"{item.get('domain', '')}: {self._compact_preview(str(item.get('text', '')), max_chars=88)} | {item.get('source_file', '')}"
            for item in prefs[:20]
        ]
        print(
            self.renderer.render_named_board(
                title="Effective Preferences",
                summary=f"count={len(prefs)}",
                items=items,
                empty_text="No managed preferences yet",
            )
        )

    def _open_memory_ledger_editor(self) -> None:
        ledger_path = self.services.layout.memory_ledger_path
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if not ledger_path.exists():
            ledger_path.write_text("", encoding="utf-8")
        self._open_path_in_editor(ledger_path, label="memory ledger")

    def _open_path_in_editor(self, path: Path, *, label: str) -> None:
        editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
        if editor:
            try:
                argv = [*shlex.split(editor), str(path)]
                subprocess.run(argv, check=False)
                print(f"Opened {label} in editor: {path}")
                return
            except Exception as exc:
                print(f"Failed to launch editor `{editor}` for {label}: {exc}")

        if os.name == "nt" and hasattr(os, "startfile") and sys.stdout.isatty():
            try:
                os.startfile(str(path))
                print(f"Opened {label} with default app: {path}")
                return
            except Exception as exc:
                print(f"Failed to open {label} via default app: {exc}")

        print(f"{label.capitalize()} path: {path}")
        print("Tip: set $EDITOR or $VISUAL, or inspect with /memory commands in terminal.")

    def _handle_skills_command(self, args: list[str]) -> None:
        if not args:
            self._interactive_skill_selector()
            return
        if args[0].lower() in {"show", "status", "list"}:
            query = " ".join(args[1:]).strip() if args and args[0].lower() == "list" else " ".join(args).strip()
            self._print_skills_panel(query=query)
            return
        action = args[0].lower()
        if action in {"pick", "select"}:
            self._interactive_skill_selector()
            return
        if action == "load":
            if len(args) < 2:
                print("Usage: /skills load <name>")
                return
            name = args[1].strip()
            try:
                skill = self.services.skill_runtime.load_skill(name)
            except KeyError as exc:
                print(f"Failed to load skill: {exc}")
                return
            items = [
                f"source: {skill.source}",
                f"summary: {self._compact_preview(skill.summary, max_chars=96)}",
                f"hint: {self._compact_preview(skill.prompt_hint, max_chars=96)}",
            ]
            if skill.path:
                items.append(f"path: {skill.path}")
            print(
                self.renderer.render_named_board(
                    title="Skill Loaded",
                    summary=skill.name,
                    items=items,
                    empty_text="No skill metadata",
                )
            )
            return
        self._print_skills_panel(query=" ".join(args).strip())

    def _interactive_skill_selector(self) -> None:
        skills = self.services.skill_runtime.list_skills()
        if not skills:
            print("No skills available.")
            return
        print("Select a skill to load:")
        for index, item in enumerate(skills, start=1):
            name = str(item.get("name", ""))
            summary = self._compact_preview(str(item.get("summary", "")), max_chars=72)
            print(f"  {index:>2}. {name} - {summary}")
        choice = input("Skill # (Enter to cancel): ").strip()
        if not choice:
            print("Skill selection cancelled.")
            return
        if not choice.isdigit():
            print("Invalid selection. Enter a number.")
            return
        picked_index = int(choice) - 1
        if picked_index < 0 or picked_index >= len(skills):
            print("Selection out of range.")
            return
        name = str(skills[picked_index].get("name", "")).strip()
        if not name:
            print("Invalid skill name.")
            return
        self._handle_skills_command(["load", name])

    def _handle_mcp_local_command(self, args: list[str]) -> None:
        if not args:
            self._interactive_mcp_selector()
            return
        action = args[0].lower()
        if action in {"show", "status", "panel"}:
            self._print_mcp_background_validate_panel()
            return
        if action in {"pick", "select"}:
            self._interactive_mcp_selector()
            return
        if action in {"enable", "disable"}:
            if len(args) < 2:
                print("Usage: /mcp enable|disable <server_name>")
                return
            self._set_mcp_server_enabled(args[1].strip(), enabled=action == "enable")
            return
        if action == "detail":
            if len(args) < 2:
                print("Usage: /mcp detail <server_name>")
                return
            self._print_mcp_server_detail(args[1].strip())
            return
        self._print_mcp_background_validate_panel()

    def _interactive_mcp_selector(self) -> None:
        servers = self.services.mcp_runtime.list_servers()
        if not servers:
            print("No MCP servers configured.")
            return

        print("Select an MCP server:")
        for index, item in enumerate(servers, start=1):
            name = str(item.get("name", ""))
            enabled = "enabled" if bool(item.get("enabled", True)) else "disabled"
            desc = self._compact_preview(str(item.get("description", "")), max_chars=56)
            print(f"  {index:>2}. {name} [{enabled}] {desc}")

        choice = input("MCP # (Enter to cancel): ").strip()
        if not choice:
            print("MCP selection cancelled.")
            return
        if not choice.isdigit():
            print("Invalid selection. Enter a number.")
            return
        picked_index = int(choice) - 1
        if picked_index < 0 or picked_index >= len(servers):
            print("Selection out of range.")
            return

        picked = servers[picked_index]
        name = str(picked.get("name", "")).strip()
        if not name:
            print("Invalid MCP server.")
            return
        print("Action: 1) detail  2) toggle enable/disable")
        action = input("Action # (Enter=detail): ").strip() or "1"
        if action == "2":
            enabled = bool(picked.get("enabled", True))
            self._set_mcp_server_enabled(name, enabled=not enabled)
            return
        self._print_mcp_server_detail(name)

    def _print_mcp_server_detail(self, name: str) -> None:
        normalized = name.strip()
        if not normalized:
            print("Server name is required.")
            return
        servers = [item for item in self.services.mcp_runtime.list_servers() if str(item.get("name", "")).strip() == normalized]
        if not servers:
            print(f"MCP server not found: {normalized}")
            return
        item = servers[0]
        items = [
            f"enabled: {bool(item.get('enabled', True))}",
            f"command: {item.get('command', '')}",
            f"args: {self._compact_preview(_json_text(item.get('args', [])), max_chars=96)}",
            f"cwd: {item.get('cwd', '')}",
            f"description: {self._compact_preview(str(item.get('description', '')), max_chars=96)}",
            f"updated_at: {item.get('updated_at', '')}",
        ]
        print(
            self.renderer.render_named_board(
                title="MCP Server",
                summary=normalized,
                items=items,
                empty_text="No details",
            )
        )

    def _set_mcp_server_enabled(self, name: str, *, enabled: bool) -> None:
        normalized = name.strip()
        if not normalized:
            print("Server name is required.")
            return
        servers = [item for item in self.services.mcp_runtime.list_servers() if str(item.get("name", "")).strip() == normalized]
        if not servers:
            print(f"MCP server not found: {normalized}")
            return
        current = servers[0]
        spec = self.services.mcp_runtime.add_server(
            name=normalized,
            command=str(current.get("command", "")),
            args=[str(item) for item in list(current.get("args") or [])],
            env={str(key): str(value) for key, value in dict(current.get("env") or {}).items()},
            cwd=str(current.get("cwd", "")),
            description=str(current.get("description", "")),
            enabled=enabled,
        )
        status_text = "enabled" if bool(spec.get("enabled", True)) else "disabled"
        print(f"MCP server `{normalized}` is now {status_text}.")

    def _handle_compact_command(self, args: list[str]) -> None:
        keep_turns = 2
        if args:
            token = args[0].strip().lower()
            if token in {"help", "-h", "--help"}:
                print("Usage: /compact [keep_turns]")
                print("Default keep_turns is 2, keeping the most recent 2 user turns in active context.")
                return
            if not token.isdigit():
                print("Usage: /compact [keep_turns]")
                return
            keep_turns = max(1, int(token))

        result = self._compact_session_window(keep_turns=keep_turns)
        if not result.get("ok", False):
            print(str(result.get("message", "Nothing to compact.")))
            return
        print(
            self.renderer.render_named_board(
                title="Context Compacted",
                summary=f"keep_turns={keep_turns}",
                items=[
                    f"dropped_messages: {result.get('dropped_message_count', 0)}",
                    f"kept_messages: {result.get('kept_message_count', 0)}",
                    f"boundary_event_id: {result.get('boundary_event_id', '')}",
                ],
                empty_text="No compact result",
            )
        )

    def _compact_session_window(self, *, keep_turns: int) -> dict[str, Any]:
        entries = self.services.session_store.load_messages_with_event_ids(self.session_id)
        concrete_entries = [(event_id, message) for event_id, message in entries if event_id is not None]
        if not concrete_entries:
            return {"ok": False, "message": "No messages to compact."}

        user_positions = [index for index, (_, message) in enumerate(concrete_entries) if message.get("role") == "user"]
        if len(user_positions) <= keep_turns:
            return {
                "ok": False,
                "message": f"Only {len(user_positions)} user turns found. Nothing to compact with keep_turns={keep_turns}.",
            }

        keep_start = user_positions[-keep_turns]
        older_messages = [message for _, message in concrete_entries[:keep_start]]
        kept_entries = concrete_entries[keep_start:]
        boundary_event_id = str(kept_entries[0][0])

        previous_summary = self._current_compaction_summary(entries)
        new_summary = self._summarize_compacted_messages(older_messages)
        summary = self._merge_compaction_summaries(previous_summary, new_summary)

        payload = {
            "keep_turns": keep_turns,
            "summary": summary,
            "boundary_event_id": boundary_event_id,
            "dropped_message_count": len(older_messages),
            "kept_message_count": len(kept_entries),
            "compacted_at": datetime.now(timezone.utc).isoformat(),
        }
        self.services.session_store.append_event(self.session_id, "session_compacted", payload)
        return {"ok": True, **payload}

    def _current_compaction_summary(self, entries: list[tuple[str | None, dict[str, Any]]]) -> str:
        for event_id, message in entries:
            if event_id is not None:
                continue
            if str(message.get("role", "")) != "system":
                continue
            content = str(message.get("content", "")).strip()
            prefix = "Compacted conversation summary:\n"
            if content.startswith(prefix):
                return content[len(prefix) :].strip()
            return content
        return ""

    def _summarize_compacted_messages(self, messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "(no older context)"
        line_chars = max(32, int(self.services.config.runtime.context_summary_line_chars))
        lines: list[str] = []
        for index, message in enumerate(messages, start=1):
            role = str(message.get("role", "unknown"))
            text = self._context_message_text(message).replace("\n", " ").strip()
            if len(text) > line_chars:
                text = text[: line_chars - 3] + "..."
            lines.append(f"{index}. {role}: {text}")
        return "\n".join(lines)

    def _merge_compaction_summaries(self, previous_summary: str, new_summary: str) -> str:
        merged = new_summary.strip()
        if previous_summary.strip():
            merged = previous_summary.strip() + "\n" + merged
        max_chars = max(512, self.services.config.runtime.context_summary_line_chars * 32)
        if len(merged) > max_chars:
            merged = merged[-max_chars:]
        return merged

    def _handle_retrieval_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"show", "status", "recent"}:
            self._print_retrieval_panel()
            return
        action = args[0].lower()
        if action == "decide":
            if len(args) < 2:
                print("Usage: /retrieval decide <prompt>")
                return
            prompt = " ".join(args[1:]).strip()
            decision = self.services.retrieval_router.decide(prompt).to_dict()
            print(
                self.renderer.render_named_board(
                    title="Retrieval Decision",
                    summary=f"route={decision.get('route')} | retrieve={decision.get('retrieve')}",
                    items=[
                        f"reason: {decision.get('reason', '')}",
                        "query_terms: " + ", ".join(decision.get("query_terms", [])),
                    ],
                    empty_text="no retrieval decision",
                )
            )
            return
        if action == "run":
            if len(args) < 2:
                print("Usage: /retrieval run <prompt>")
                return
            prompt = " ".join(args[1:]).strip()
            payload = self.services.retrieval_router.run(prompt)
            self._print_retrieval_payload("Retrieval Run", payload)
            return
        self._print_retrieval_payload("Retrieval Run", self.services.retrieval_router.run(" ".join(args).strip()))

    @staticmethod
    def _cron_toggle_requested(text: str) -> tuple[bool, bool]:
        lowered = text.lower()
        action = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
        disable_action = action in {"disable", "off", "stop", "pause", "close"}
        enable_action = action in {"enable", "on", "start", "resume", "open"}
        disable_phrase = bool(re.search(r"\b(disable|turn off|stop|pause|close|shutdown|suspend|off)\b", lowered))
        enable_phrase = bool(re.search(r"\b(enable|turn on|start|resume|open|reactivate)\b", lowered))
        return disable_action or disable_phrase, enable_action or enable_phrase

    def _normalize_cron_schedule(self, raw_schedule: str, *, source_text: str) -> str | None:
        candidate = raw_schedule.strip()
        if candidate:
            if _looks_like_cron_expression(candidate):
                return candidate
            parsed = _parse_nl_schedule(candidate)
            if parsed:
                return parsed
        return _parse_nl_schedule(source_text)

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on", "enable", "enabled", "start", "resume"}:
                return True
            if lowered in {"false", "0", "no", "off", "disable", "disabled", "stop", "pause"}:
                return False
        return None

    def _normalize_cron_intent_payload(
        self,
        payload: dict[str, Any],
        *,
        source_text: str,
        available_jobs: set[str],
        source: str,
    ) -> CronIntentDecision:
        valid_intents = {"list", "toggle", "set_schedule", "create_terminal_message", "unknown"}
        valid_scopes = {"scheduler", "job", "none"}

        intent = str(payload.get("intent", "unknown")).strip().lower() or "unknown"
        if intent not in valid_intents:
            intent = "unknown"

        scope = str(payload.get("scope", "none")).strip().lower() or "none"
        if scope not in valid_scopes:
            scope = "none"

        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        enabled = self._coerce_optional_bool(payload.get("enabled"))
        if enabled is None:
            enabled = self._coerce_optional_bool(payload.get("action"))

        schedule = self._normalize_cron_schedule(str(payload.get("schedule", "") or ""), source_text=source_text)

        message_template = str(payload.get("message_template") or payload.get("message") or "").strip()
        if not message_template:
            message_template = self._parse_nl_cron_message(source_text)

        job_name: str | None = None
        raw_job = str(payload.get("job_name") or payload.get("job") or payload.get("target") or "").strip()

        normalized_candidates: list[str] = []
        for item in payload.get("candidates", []) if isinstance(payload.get("candidates"), list) else []:
            token = str(item).strip()
            if not token:
                continue
            normalized_candidates.extend(_match_cron_job_candidates(token, available_jobs))

        if raw_job:
            matches = _match_cron_job_candidates(raw_job, available_jobs)
            if len(matches) == 1:
                job_name = matches[0]
            elif len(matches) > 1:
                normalized_candidates.extend(matches)

        if job_name is None:
            heuristic = _resolve_cron_job_name(source_text, available_jobs)
            if heuristic and (scope == "job" or intent in {"toggle", "set_schedule"}):
                job_name = heuristic

        deduped_candidates: list[str] = []
        for item in normalized_candidates:
            if item == job_name or item in deduped_candidates:
                continue
            deduped_candidates.append(item)

        if job_name is None and len(deduped_candidates) == 1:
            job_name = deduped_candidates[0]
            deduped_candidates = []

        missing = set(str(item).strip() for item in payload.get("missing_fields", []) if str(item).strip())

        disable_requested, enable_requested = self._cron_toggle_requested(source_text)
        if intent == "toggle":
            if enabled is None:
                if disable_requested and not enable_requested:
                    enabled = False
                elif enable_requested and not disable_requested:
                    enabled = True
            if scope == "none":
                if job_name or deduped_candidates:
                    scope = "job"
                elif _looks_like_global_cron_scope(source_text) or (
                    "cron" in source_text.lower() and not job_name and not deduped_candidates
                ):
                    scope = "scheduler"
            if scope == "job" and not job_name:
                missing.add("job_name")
            if enabled is None:
                missing.add("enabled")

        if intent == "set_schedule":
            scope = "job"
            if not job_name:
                missing.add("job_name")
            if not schedule:
                missing.add("schedule")

        if intent == "create_terminal_message":
            if not schedule:
                missing.add("schedule")
            if not message_template:
                missing.add("message_template")

        if intent == "unknown":
            if enabled is not None and (scope in {"scheduler", "job"} or job_name or deduped_candidates):
                intent = "toggle"
            elif schedule and (job_name or scope == "job"):
                intent = "set_schedule"
                scope = "job"
            elif schedule or message_template:
                intent = "create_terminal_message"

        reason = str(payload.get("reason", "")).strip()

        return CronIntentDecision(
            intent=intent,
            scope=scope,
            job_name=job_name,
            schedule=schedule,
            enabled=enabled,
            message_template=message_template,
            missing_fields=tuple(sorted(missing)),
            candidates=tuple(deduped_candidates),
            confidence=confidence,
            source=source,
            reason=reason,
        )

    def _parse_cron_intent_with_llm(self, *, text: str, available_jobs: set[str]) -> CronIntentDecision | None:
        system_prompt = (
            "You are a strict parser for CodeLite /cron commands. "
            "Return JSON only. No markdown, no prose. "
            "Schema: {intent, scope, job_name, schedule, enabled, message_template, confidence, missing_fields, candidates, reason}. "
            "intent in [list, toggle, set_schedule, create_terminal_message, unknown]. "
            "scope in [scheduler, job, none]. "
            "When ambiguous, put candidate job names into candidates and include missing_fields."
        )
        jobs = ", ".join(sorted(available_jobs)) if available_jobs else "(none)"
        user_prompt = (
            f"input={text}\n"
            f"known_jobs={jobs}\n"
            f"scheduler_enabled={self.services.cron_scheduler.enabled}"
        )
        try:
            result = self.services.agent_loop.model_client.complete(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                [],
            )
        except Exception:
            return None

        payload = _extract_json_object_from_text(result.text)
        if payload is None:
            return None
        return self._normalize_cron_intent_payload(
            payload,
            source_text=text,
            available_jobs=available_jobs,
            source="llm",
        )

    def _parse_cron_intent_with_rules(self, *, text: str, available_jobs: set[str]) -> CronIntentDecision:
        clean = text.strip()
        action = clean.split(maxsplit=1)[0].lower() if clean else ""
        lowered = clean.lower()

        if not clean:
            return CronIntentDecision(reason="empty input")

        if action in {"list", "ls", "show", "status"}:
            return CronIntentDecision(intent="list", scope="none", confidence=1.0, source="rules")

        disable_requested, enable_requested = self._cron_toggle_requested(clean)
        if disable_requested and enable_requested:
            return CronIntentDecision(reason="conflicting toggle action", source="rules")

        schedule = self._normalize_cron_schedule("", source_text=clean)
        message_template = self._parse_nl_cron_message(clean)

        job_name = _resolve_cron_job_name(clean, available_jobs)
        candidates: list[str] = []

        explicit_target = re.search(r"\b(?:run|job|task|for)\s+([A-Za-z0-9_.-]+)\b", clean, re.IGNORECASE)
        if explicit_target and job_name is None:
            candidates.extend(_match_cron_job_candidates(explicit_target.group(1), available_jobs))

        if job_name is None and not candidates and (disable_requested or enable_requested or schedule is not None):
            for token in re.findall(r"[A-Za-z0-9_.-]+", clean):
                lowered_token = token.lower()
                if lowered_token in {
                    "cron",
                    "scheduler",
                    "job",
                    "jobs",
                    "run",
                    "for",
                    "task",
                    "enable",
                    "disable",
                    "start",
                    "stop",
                    "pause",
                    "resume",
                    "open",
                    "close",
                    "on",
                    "off",
                    "every",
                    "minute",
                    "minutes",
                    "hour",
                    "hours",
                    "daily",
                    "weekly",
                    "at",
                }:
                    continue
                if not re.search(r"[A-Za-z_]", token):
                    continue
                matches = _match_cron_job_candidates(token, available_jobs)
                if matches:
                    candidates.extend(matches)

        deduped_candidates: list[str] = []
        for item in candidates:
            if item in deduped_candidates:
                continue
            deduped_candidates.append(item)

        if job_name is None and len(deduped_candidates) == 1:
            job_name = deduped_candidates[0]
            deduped_candidates = []

        if disable_requested or enable_requested:
            enabled = bool(enable_requested and not disable_requested)
            if _looks_like_global_cron_scope(clean) or ("cron" in lowered and job_name is None and not deduped_candidates):
                return CronIntentDecision(
                    intent="toggle",
                    scope="scheduler",
                    enabled=enabled,
                    confidence=0.9,
                    source="rules",
                )
            if job_name is not None:
                return CronIntentDecision(
                    intent="toggle",
                    scope="job",
                    job_name=job_name,
                    enabled=enabled,
                    confidence=0.9,
                    source="rules",
                )
            return CronIntentDecision(
                intent="toggle",
                scope="job",
                enabled=enabled,
                candidates=tuple(deduped_candidates),
                missing_fields=("job_name",),
                confidence=0.7,
                source="rules",
            )

        if job_name is not None:
            if schedule is None:
                return CronIntentDecision(
                    intent="set_schedule",
                    scope="job",
                    job_name=job_name,
                    missing_fields=("schedule",),
                    confidence=0.75,
                    source="rules",
                )
            return CronIntentDecision(
                intent="set_schedule",
                scope="job",
                job_name=job_name,
                schedule=schedule,
                confidence=0.9,
                source="rules",
            )

        if schedule is not None:
            if message_template:
                return CronIntentDecision(
                    intent="create_terminal_message",
                    schedule=schedule,
                    message_template=message_template,
                    confidence=0.9,
                    source="rules",
                )
            return CronIntentDecision(
                intent="create_terminal_message",
                schedule=schedule,
                missing_fields=("message_template",),
                confidence=0.75,
                source="rules",
            )

        if message_template:
            return CronIntentDecision(
                intent="create_terminal_message",
                message_template=message_template,
                missing_fields=("schedule",),
                confidence=0.7,
                source="rules",
            )

        return CronIntentDecision(reason="no matching intent", source="rules")

    def _decide_cron_intent(self, *, text: str, available_jobs: set[str]) -> CronIntentDecision:
        llm_decision = self._parse_cron_intent_with_llm(text=text, available_jobs=available_jobs)
        rules_decision = self._parse_cron_intent_with_rules(text=text, available_jobs=available_jobs)

        if llm_decision is not None and llm_decision.intent != "unknown" and llm_decision.confidence >= 0.55:
            return llm_decision
        if rules_decision.intent != "unknown":
            return rules_decision
        if llm_decision is not None and llm_decision.intent != "unknown":
            return llm_decision
        return rules_decision

    def _print_cron_ambiguity(self, decision: CronIntentDecision, *, available_jobs: set[str]) -> None:
        print("Cron target is ambiguous; no changes were applied.")
        if decision.candidates:
            print("Candidates: " + ", ".join(decision.candidates))
            print("Use an exact job name, e.g. /cron disable <job_name>")
            return
        if available_jobs:
            print("Available jobs: " + ", ".join(sorted(available_jobs)))
        else:
            print("No registered cron jobs found.")

    def _handle_cron_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"list", "ls", "show", "status"}:
            jobs = self.services.cron_scheduler.list_jobs()
            print(f"Cron scheduler: enabled={self.services.cron_scheduler.enabled}")
            print("Cron jobs:")
            for item in jobs:
                print(f"  - {item['name']} | {item['schedule']} | enabled={item['enabled']} | last={item['last_status']}")
            return

        available_jobs = {item["name"] for item in self.services.cron_scheduler.list_jobs()}
        text = " ".join(args).strip()
        decision = self._decide_cron_intent(text=text, available_jobs=available_jobs)

        if decision.intent == "toggle":
            if decision.scope == "scheduler":
                if decision.enabled is None:
                    print("Missing enable/disable action for cron scheduler.")
                    print("Example: /cron disable cron")
                    return
                enabled = self.services.cron_scheduler.set_enabled(bool(decision.enabled))
                status_text = "enabled" if enabled else "disabled"
                print(f"Cron scheduler updated: {status_text}")
                return

            if decision.scope == "job":
                if decision.job_name is None:
                    self._print_cron_ambiguity(decision, available_jobs=available_jobs)
                    return
                if decision.enabled is None:
                    print("Missing enable/disable action.")
                    print("Example: /cron disable task_reconcile")
                    return
                job = self.services.cron_scheduler.configure_job(decision.job_name, enabled=bool(decision.enabled))
                _set_custom_cron_enabled(self.services.layout, name=job.name, enabled=job.enabled)
                status_text = "enabled" if job.enabled else "disabled"
                print(f"Cron job updated: {job.name} -> {status_text}")
                return

        if decision.intent == "set_schedule":
            if decision.job_name is None:
                self._print_cron_ambiguity(decision, available_jobs=available_jobs)
                return
            if _cron_seconds_requested(text):
                print("Cron supports minute-level scheduling and above; second-level schedules are not supported.")
                print("Example: /cron every minute run task_reconcile")
                return
            schedule = (decision.schedule or "").strip()
            if not schedule:
                print("Missing schedule frequency.")
                print("Example: /cron every minute run task_reconcile")
                return
            if len(schedule.split()) != 5 or not _looks_like_cron_expression(schedule):
                print(f"Invalid cron schedule: {schedule}")
                print("Cron currently supports exactly 5 fields: minute hour day month weekday")
                return

            job = self.services.cron_scheduler.configure_job(decision.job_name, schedule=schedule)
            print(f"Cron configured: {job.name} -> {job.schedule}")
            try:
                payload = self.services.cron_scheduler.run_job(job.name)
            except Exception as exc:
                print(f"Manual dry-run failed: {exc}")
                return
            print(
                f"Manual dry-run passed: {job.name} | last_status={payload['last_status']} | run_count={payload['run_count']}"
            )
            return

        if decision.intent == "create_terminal_message":
            if _cron_seconds_requested(text):
                print("Cron supports minute-level scheduling and above; second-level schedules are not supported.")
                print("Example: /cron every minute print \"hello\"")
                return
            schedule = (decision.schedule or "").strip()
            message_template = decision.message_template.strip()
            if not schedule:
                print("Missing schedule frequency.")
                print("Example: /cron every minute print \"hello\"")
                return
            if len(schedule.split()) != 5 or not _looks_like_cron_expression(schedule):
                print(f"Invalid cron schedule: {schedule}")
                print("Cron currently supports exactly 5 fields: minute hour day month weekday")
                return
            if not message_template:
                print("Missing message content.")
                print("Example: /cron every minute print \"hello\"")
                return

            spec = self._create_terminal_cron_job(
                schedule=schedule,
                message_template=message_template,
                source_text=text,
            )
            print(f"Cron configured: {spec['name']} -> {spec['schedule']}")
            payload = self.services.cron_scheduler.run_job(spec["name"])
            result = payload.get("result") or {}
            print(f"Manual dry-run passed: {spec['name']} | output={result.get('message', '')}")
            return

        if decision.candidates:
            self._print_cron_ambiguity(decision, available_jobs=available_jobs)
            return

        print("Unable to parse cron command.")
        print("Examples:")
        print("  /cron every minute print \"hello\"")
        print("  /cron disable task_reconcile")
        print("  /cron disable cron")
    def _parse_nl_cron_message(self, text: str) -> str:
        lowered = text.lower()
        if any(token in lowered for token in ("current time", "what time", "time now", "现在时间", "当前时间", "几点")):
            return "current_time"

        quoted = re.search(r'["“”](.+?)["“”]', text)
        if quoted:
            return quoted.group(1).strip()

        reminder = re.search(
            r"(?:output|print|send to terminal|remind|提醒|输出|打印)\s+(.+)",
            text,
            re.IGNORECASE,
        )
        if reminder:
            value = reminder.group(1).strip(" ,.;!?")
            return value
        return ""

    def _create_terminal_cron_job(self, *, schedule: str, message_template: str, source_text: str) -> dict[str, Any]:
        specs = _load_custom_cron_jobs(self.services.layout)
        name = f"shell_terminal_{len(specs) + 1:02d}"
        spec = {
            "name": name,
            "kind": "terminal_message",
            "schedule": schedule,
            "enabled": True,
            "description": source_text[:120],
            "message_template": message_template,
        }
        specs.append(spec)
        _save_custom_cron_jobs(self.services.layout, specs)
        _register_custom_cron_jobs(self.services.cron_scheduler, self.services.layout)
        return spec

    def _handle_heart_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"list", "ls", "show", "status"}:
            payload = self.services.heart_service.status()
            print("Heartbeat status:")
            for item in payload["components"]:
                print(f"  - {item['component_id']} | {item['status']} | age={item['last_seen_age_sec']}")
            return

        text = " ".join(args).strip()
        components = [item["component_id"] for item in self.services.heart_service.status()["components"]]
        component = None
        lowered = text.lower()
        for item in components:
            if item.lower() in lowered:
                component = item
                break
        if component is None:
            tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.-]*", text)
            if tokens:
                component = tokens[0]
        if component is None:
            print(f"Missing component name. Available components: {', '.join(components)}")
            print("Example: /heart set tool_router red queue 3 active 1")
            return

        status = _parse_nl_heart_status(text)
        queue_depth = _parse_nl_heart_number(text, r"queue\s*(\d+)", r"闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁炬儳缍婇弻鐔兼⒒鐎靛壊妲紒鐐劤缂嶅﹪寮婚悢鍏尖拻閻庨潧澹婂Σ顔剧磼閻愵剙鍔ょ紓宥咃躬瀵鎮㈤崗灏栨嫽闁诲酣娼ф竟濠偽ｉ鍓х＜闁绘劦鍓欓崝銈囩磽瀹ュ拑韬€殿喖顭烽幃銏ゅ礂鐏忔牗瀚介梺璇查叄濞佳勭珶婵犲伣锝夘敊閸撗咃紲闂佺粯鍔﹂崜娆撳礉閵堝洨纾界€广儱鎷戦煬顒傗偓娈垮枛椤兘骞冮姀銈呯閻忓繑鐗楃€氫粙姊虹拠鏌ュ弰婵炰匠鍕彾濠电姴浼ｉ敐澶樻晩闁告挆鍜冪床闂備胶绮崝锕傚礈濞嗘挸绀夐柕鍫濇川绾剧晫鈧箍鍎遍幏鎴︾叕椤掑倵鍋撳▓鍨灈妞ゎ厾鍏橀獮鍐閵堝懐顦ч柣蹇撶箲閻楁鈧矮绮欏铏规嫚閺屻儱寮板┑鐐板尃閸曨厾褰炬繝鐢靛Т娴硷綁鏁愭径妯绘櫓闂佸憡鎸嗛崪鍐簥闂傚倷鑳剁划顖炲礉閿曞倸绀堟繛鍡樻尭缁€澶愭煏閸繃宸濈痪鍓ф櫕閳ь剙绠嶉崕閬嶅箯閹达妇鍙曟い鎺戝€甸崑鎾斥枔閸喗鐏堝銈庡幘閸忔﹢鐛崘顔碱潊闁靛牆鎳愰ˇ褔鏌ｈ箛鎾剁闁绘顨堥埀顒佺煯缁瑥顫忛搹瑙勫珰闁哄被鍎卞鏉库攽閻愭澘灏冮柛鏇ㄥ幘瑜扮偓绻濋悽闈浶㈠ù纭风秮閺佹劖寰勫Ο缁樻珨闂備線鈧偛鑻晶顔姐亜椤愩垻绠荤€规洦鍋婂畷鐔煎箠瀹勭増鎼愰柣鎰功閹插憡寰勯幇顒傦紱闂佽宕橀褍娲垮┑鐘灱濞夋盯鏁冮敃鍌涘仒闁靛鏅滈埛鎴︽煕濠靛棗顏柍璇差樀閺屾稑螣閸忓吋鐝紓渚囧枛椤兘鐛幒鎳虫梹鎷呴崣澶婎伜婵犵數鍋犻幓顏嗗緤閼测晜濯伴柨鏇炲€归崐鍧楁煕閹炬鎳愰敍婊呯磽閸屾瑧鍔嶉柨姘攽椤旇偐肖闁逞屽墲椤煤韫囨稑纾块柟鎯版閻掑灚銇勯幒鎴姛缂佸鏁婚弻娑㈠箻鐎垫悶鈧帞绱掗鑲╁缂佺粯绻堝畷鍫曞Ω瑜嶉獮宥夋⒑鐠囧弶鎹ｉ柡浣规倐閳ワ箓宕煎┑鍥ㄧ槑缂傚倸鍊烽懗鍫曞磻閹捐纾块柟鎯版鍥撮梺褰掓？缁€渚€鎷戦悢鍏肩厽闁哄倸鐏濋幃鎴︽煟閹哄秶鐭欓柡灞诲姂瀵潙螖閳ь剚绂嶆ィ鍐╁€垫繛鍫濈仢閺嬨倝鏌涚€ｎ偅灏甸柟骞垮灩閳藉濮€閻樿鏁归梻浣虹帛濡礁鈻嶉敐澶嬪亗濞达絽澹婂〒濠氭煏閸繂鏆欏┑鈩冩倐閺屾稒鎯旈敐鍡樻瘓濡ょ姷鍋涢崯顐﹀煝鎼淬劌绠ｉ柡鍌氥仒婢规洟姊哄Ч鍥х仾妞ゆ梹鐗犻幃鐐淬偅閸愨晝鍘撳銈呯箰鐎氼喚绮斿ú顏呯厸閻忕偠顕ч埀顒佺箞閻涱噣骞囬鐔峰妳闂佺偨鍎查崜姘焽閺冨牊鈷掑〒姘ｅ亾婵炰匠鍏犳椽濡堕崶锝呬壕婵﹩鍋勫畵鍡涙煟濞戝崬娅嶆鐐村笒铻栭柍褜鍓熼悰顕€濮€閳ヨ尙绠氶梺缁樺姈濞兼瑩宕濋妶鍡愪簻闁靛濡囬。鑼磼缂佹绠為柟顔荤矙濡啫霉闊彃鍔滈柕鍥у閺佹劙宕熼鐘靛幆闂備胶纭堕弬渚€宕戦幘鎰佹富闁靛牆妫楃粭鎺楁煕婵犲倹鍟炵紒鍌涘笒鐓ゆい蹇撴噽閸橀亶鏌ｆ惔顖滅シ闁告柨鐭傞幃姗€寮婚妷锔惧幐閻庡厜鍋撻悗锝庡墰閿涚喐绻涚€电顎撶紒鐘虫尭閻ｅ嘲顭ㄩ崱鈺傂梻浣告啞鐢绮欓幒鏃€宕叉繝闈涚墕閺嬪牊淇婇姘儓闁冲嘲顦靛铏规嫚閳ヨ櫕鐏堥梺绋匡龚椤绮氭潏銊х瘈闁搞儯鍔岄埀顒€顭烽弻锕€螣閻氬绀嗛梺闈浥堥弲婊堟偂韫囨稓鍙撻柛銉ｅ妽缁€鈧悘蹇撶箲缁绘繈濮€閿濆孩缍堝┑鐐跺皺閸犲酣锝炶箛娑欐優闁革富鍘鹃悡瀣⒑缁洖澧叉い銊ユ嚇椤㈡梻鈧稒顭囩粻楣冩煕椤愩倕鏋戞い銉ョ墦閺屸€崇暆鐎ｎ剛鏆ら悗瑙勬礃閿曘垽銆侀弮鍫濈妞ゆ帒鍊烽柇顖炴⒒閸屾瑧顦﹂柟纰卞亞閹噣顢曢敃鈧粈澶屸偓鍏夊亾闁告洦鍋嗛崢鎾⒑绾懏褰х紒鐘冲灴閻涱噣濮€閳ヨ尙绠氬銈嗙墬閻熴劑顢楅悢鍏肩厓鐟滄粓宕滃杈╃煓闁硅揪绠戦悡姗€鏌熸潏楣冩闁稿﹦鍏橀弻銈囧枈閸楃偛顫梺璋庡倻绐旀慨濠冩そ瀹曨偊宕熼鈧▍銈囩磽娓氬洤鏋熼柟鍝ョ帛缁岃鲸绻濋崶銊ヤ缓缂備礁顑堝▔鏇⑺囬弶娆炬富闁靛牆妫涙晶顒佹叏濡濮傛い銏＄懄缁绘繈宕堕妸銏″闂備浇宕甸崰鎰珶閸℃稑绠洪柣妯兼暩绾惧吋绻涘顔荤敖闁伙綀娅ｉ埀顒冾潐濞叉﹢宕归崸妤冨祦婵☆垰鍚嬬€氭岸鏌ょ喊鍗炲⒕缂侀硸鍣ｅ缁樻媴閾忕懓绗″┑鐐插级閸ㄥ潡骞冮悙鐑樻櫇闁稿本绋掑▍?(\d+)", default=0)
        active_task_count = _parse_nl_heart_number(text, r"active\s*(\d+)", r"婵犵數濮烽弫鍛婃叏閻戣棄鏋侀柛娑橈攻閸欏繘鏌ｉ幋锝嗩棄闁哄绶氶弻娑樷槈濮楀牊鏁鹃梺鍛婄懃缁绘﹢寮婚敐澶婄闁挎繂妫Λ鍕⒑閸濆嫷鍎庣紒鑸靛哺瀵鈽夊Ο閿嬵潔濠殿喗顨呴悧濠囧极妤ｅ啯鈷戦柛娑橈功閹冲啰绱掔紒姗堣€跨€殿喖顭烽弫鎰緞婵犲嫷鍚呴梻浣瑰缁诲倸螞椤撶倣娑㈠礋椤栨稈鎷洪梺鍛婄箓鐎氱兘宕曟惔锝囩＜闁兼悂娼ч崫铏光偓娈垮枛椤兘骞冮姀銈呯閻忓繑鐗楃€氫粙姊虹拠鏌ュ弰婵炰匠鍕彾濠电姴浼ｉ敐澶樻晩闁告挆鍜冪床闂備浇顕栭崹搴ㄥ礃閿濆棗鐦辩紓鍌氬€风欢锟犲闯椤曗偓瀹曞綊骞庨挊澶岊唹闂侀潧绻掓慨顓炍ｉ崼銉︾厪闊洦娲栧暩濡炪倖鎸搁幖顐﹀煘閹达附鍊烽柛娆忣樈濡偟绱撴担铏瑰笡閻㈩垪鈧磭鏆︽繝闈涱儏缁犵粯銇勯弮鍥嗘帡骞忓ú顏呯厸濠㈣泛鑻禒锕€顭块悷鐗堫棦閽樻繈鏌ㄩ弬娆炬綗濞存粍绮撻弻鐔衡偓娑欘焽缁犳牗銇勯妷锝呯仼闁宠鍨块弫宥夊礋椤掍焦鐦撻柣搴ゎ潐濞叉粓宕伴弽顓溾偓浣糕槈濮楀棙鍍垫俊鎻掓湰閻楁洟寮查鍫熲拻濞达綀妫勯崥鐟扳攽椤旇棄鍔ら柍璇茬Ч閺佹劖寰勬繝鍕剁吹闂備線娼ч悧鍡浰囨导鏉戠９闂傚牊鍏氶弮鍫熸櫜闁告侗鍘藉▓鏌ユ⒑閹惰姤鏁遍柛銊ユ健瀵鈽夊Ο閿嬵潔濠殿喗顨呴悧鍡樻叏濞戙垺鈷戦柣鐔告緲閺嗛亶鏌涢悢绋款棆婵″弶鍔欓獮鎺楀籍閸屾粣绱叉繝纰樻閸ㄤ即骞栭锔肩稏闁硅揪闄勯埛鎴︽偣閸ヮ亜鐨虹紒鐘卞嵆閺屾盯鎮㈤崨濠勭▏濡炪値鍋勭换鎰弲濡炪倕绻愮€氼噣顢欓崶顒佺厵闁煎湱澧楄ぐ褏绱掗幓鎺撳仴闁诡噣绠栭幃浠嬪川婵炵偓瀚奸梻浣藉吹閸犳劕顭垮鈧鎶芥焼瀹ュ棌鎷洪柣銏╁灱閸犳岸宕氶悧鍫涗簻闁哄浂浜炵粔顔筋殽閻愬澧柟宄版嚇閹虫牕顭块鐐叉灓闁绘柨妫濋幃瑙勬姜閹峰矈鍔呴梺绋垮閸ㄥ潡寮诲☉妯滅喖鎮滃Ο鐑樻畼缂傚倷鑳剁划顖滅矙閹烘埈鐒介煫鍥ㄧ☉缁€鍫㈡喐瀹ュ鏄ラ悘鐐插⒔缁♀偓闂侀潧绻堥崹娲吹閳ь剟姊洪幐搴㈠濞存粍绮撻幃楣冩倻閽樺宓嗛梺闈涚箳婵兘顢樺ú顏呪拺闁圭瀛╅埛鎺楁煛閸滀礁浜扮€规洏鍨介幃浠嬪川婵炵偓瀚奸梻渚€娼荤€靛矂宕㈤崜褎鏆滄繛鎴欏灪閸嬨劍銇勯弽銊ょ繁婵炲牊绮庨埀顒冾潐濞诧箓宕归崼鏇炵畺婵炲棙鎸婚崐缁樹繆椤栨粌甯舵鐐茬У娣囧﹪鎮欓鍕ㄥ亾閺嶎厽鍋嬫俊銈呭暟閻瑩鏌熼悜姗嗘濠㈣埖鍔曢柋鍥ㄧ節闂堟稑鏆為柡鍌楀亾闂傚倷鐒﹂弸濂稿疾濞戙垹鐤い鏍仜绾惧綊鏌熼柇锕€鍘撮柡鈧禒瀣叆婵炴垶锚椤忣亪鏌ｉ幒鎾愁嚋闁靛洤瀚伴弫鍌炴嚍閵夈儱鏀梻浣告惈婢跺洭宕滃┑鍡╁殫闁告洦鍓欑欢鐐碘偓鍏夊亾闁逞屽墴閹繝骞橀弬銉︽杸濡炪倖姊婚妴瀣礉閻旇櫣纾兼い鏇炴噹閻忥絿绱掗鍛籍闁诡喓鍨介幃鈩冩償閿濆懎袝濠碉紕鍋戦崐鏍ь啅婵犳艾纾婚柟鐐暘娴滄粍銇勯幘璺轰沪缂佸本瀵ч妵鍕晝娴ｅ湱銆愬銈庡弨閸庡藝瀹曞洨纾奸悹鍥皺婢ф洘銇勯銏㈢閻撱倖銇勮箛鎾愁仹缂佸崬鐖煎娲川婵犲啫顦╅梺鍛婃尰閻熴儵鍩㈠澶娢ч柛銉㈡櫇閿涙繃绻涢幘纾嬪婵炲眰鍊濆鎼佹偄閸忚偐鍘甸梺鍛婄☉閿曘儵宕愰幇鐗堢厵妞ゆ洍鍋撶紒鐘崇墵楠炲啫顭ㄩ崼鐔风檮婵犮垼娉涢惉濂稿级閹间焦鈷掑ù锝呮啞閹牊銇勯敂璇茬仩妞ゎ厼娲弫鎾绘偐閸欏鈧剙鈹戦悩璇у伐闁哥喓濞€瀵劍绂掔€ｎ亞顔婇梺瑙勫劶濡嫮澹曡ぐ鎺撶厵闁告挆鍛闂佹悶鍊曠€氫即寮诲☉銏╂晝闁挎繂妫涢ˇ銉╂⒑?(\d+)", default=0)
        failure_streak = _parse_nl_heart_number(text, r"failure\s*(\d+)", r"婵犵數濮烽弫鍛婃叏閻戣棄鏋侀柛娑橈攻閸欏繘鏌ｉ幋锝嗩棄闁哄绶氶弻娑樷槈濮楀牊鏁鹃梺鍛婄懃缁绘﹢寮婚敐澶婄闁挎繂妫Λ鍕⒑閸濆嫷鍎庣紒鑸靛哺瀵鈽夊Ο閿嬵潔濠殿喗顨呴悧濠囧极妤ｅ啯鈷戦柛娑橈功閹冲啰绱掔紒姗堣€跨€殿喖顭烽弫鎰緞婵犲嫷鍚呴梻浣瑰缁诲倸螞椤撶倣娑㈠礋椤栨稈鎷洪梺鍛婄箓鐎氱兘宕曟惔锝囩＜闁兼悂娼ч崫铏光偓娈垮枦椤曆囧煡婢跺á鐔兼煥鐎ｅ灚缍屽┑鐘愁問閸犳銆冮崨瀛樺亱濠电姴娲ら弸浣肝旈敐鍛殲闁抽攱鍨块弻娑樷槈濮楀牆濮涢梺鐟板暱閸熸壆妲愰幒鏃傜＜婵☆垰鎼～鎺戔攽閻橆偄浜炬繛鎾村焹閸嬫挻鎱ㄦ繝鍛仩缂侇喗鐟ч幑鍕Ω瑜滈崬鍫曟⒒娴ｅ憡鍟為柤褰掔畺椤㈡牠宕堕埡鍌ゆ綗闂佺粯鍔曢幖顐ょ不閿濆鐓ラ柡鍐ㄥ€瑰▍鏇㈡煕濮椻偓娴滆泛顫忓ú顏咁棃婵炴垼浜崝鎼佹⒑缁嬪潡鍙勫ù婊冪埣瀹曟椽鍩€椤掍降浜滈柟鍝勬娴滄儳鈹戦悩顐壕闂備緡鍓欑粔瀵哥不閺屻儲鐓忛煫鍥ㄦ礀琚ュ┑鈩冨絻閻楀﹪骞堥妸銉庢棃鍩€椤掆偓铻炴俊銈呮噹閻ら箖鏌ｅΟ鐑樷枙婵炴挸顭烽弻鏇㈠醇濠靛浂妫″銈冨劚濡盯鍩€椤掑喚娼愭繛鍙夌矒瀵偅绻濆顒傜暰闂佸憡娲﹂崜姘跺磿閻斿吋鐓ユ繝闈涙瀹告繈鏌ㄥ☉娆戠煉婵﹨娅ｇ槐鎺懳熼崫鍕戞洟姊洪崨濠冨鞍闁荤啿鏅涢悾宄扳攽鐎ｎ€囨煕閳╁啰鎳呮い鏃€娲滅槐鎺旂磼閵忕姴绫嶅銈冨劜閹告儳顕ｈ閸┾偓妞ゆ帒瀚崐鍨箾閸繄浠㈤柡瀣⊕閵囧嫰顢橀姀鈩冩殸婵烇絽娲ら敃顏勭暦婵傜鍗抽柣鎰礋閺囥垺鈷戦梻鍫熺〒婢ф洟鏌熼崘鑼闁瑰嘲缍婇弫鎾绘偐瀹曞洤甯楅柣鐔哥矋缁挸鐣峰鍫澪╃憸蹇曠矆婵犲洦鐓曢柍鈺佸暟閳藉鐥幆褜鐓奸柡灞剧☉閳藉螣閸忓吋鍠栭梻浣侯焾椤戝洭宕伴弽顓炶摕闁跨喓濮寸粈鍐煏婵炑冨枤閺夋悂姊绘担鐟扳枙闁衡偓闁秴鍨傞柛褎顨呴拑鐔兼煟閺傚灝鎮戦柛銈呭暣閺屽秵娼悧鍫▊缂備緡鍠涢崺鏍崲濠靛牆鏋堝璺虹灱閿涚喖姊洪崫鍕闁稿妫楀嵄闁圭増婢樼粻鎶芥煛閸愶絽浜鹃柣搴㈢瀹€鎼佸蓟閵堝洤鏋堥柛妤冨仜椤晠姊洪幇浣风敖闁轰礁顭烽獮鍐ㄎ旈埀顒勫煝閹捐鍨傛い鏃傛櫕娴犲本淇婇悙顏勨偓鏍р枖閿曞倸鐐婄憸蹇涘矗閳ь剙鈹戦悩顔肩伇闁糕晜鐗犲畷婵嬪即椤喚绋忛梺鍛婄☉閻°劑鎮￠悢闀愮箚妞ゆ牗绋掗妵鐔兼煕閻旈绠婚柡灞剧洴閹垺绺芥径濠傚缂傚倷鑳剁划顖滄崲閸岀儐鏁嬮柕澶嗘櫆閻撱儵鎮楅敐搴′簽闁告埊绻濆娲焻閻愯尪瀚板褜鍨堕弻娑㈠籍閳ь剟鎮烽妷鈺傚仼闁绘垼妫勭粻锝夋煥閺囨浜剧紓浣哄Ь瀹曠數妲愰幘瀛樺濠殿喗鍩堟禍顏堝春閳ь剚銇勯幒鍡椾壕闂佽绻戝畝鎼佺嵁閸儱惟闁靛娴烽崰鏍箖閳╁啯鍎熼柍銉ㄥ皺鐢稒绻濋悽闈浶涢柟宄板暣瀹曟﹢骞嗚椤斿秹姊绘笟鈧埀顒傚仜閼活垱鏅堕娑栦簻闁哄啠鍋撻柣妤冨Т閻ｇ兘寮剁拠鐐瀹曘劑顢橀悩鍨瘒闂傚倷鑳堕…鍫ュ嫉椤掆偓椤繈濡搁埡浣硅緢闂佹寧娲栭崐褰掓偂濞嗘挻鐓熼柟瀵镐紳椤忓牊鍊块柣鎰靛墰缁犻箖鎮樿箛鏃傚婵炲懎锕弻锛勪沪閸撗岀伇缂備胶濮甸惄顖氼嚕閹绢喗鍊烽棅顐幘鐢盯姊婚崒娆愵樂缂侀硸鍣ｅ浠嬪礋椤撶偛鐏婇梺鍦亾閸撴艾顭囬弽顐ょ＝濞达綀顕栭悞浠嬫煟閻旈绉洪柡灞界Х椤т線鏌涢幘瀵告噰闁炽儻绠撳畷鍫曨敂瀹ュ棌鏋岄梻鍌欐祰椤曟牠宕锕€鐐婄憸宥夋嚀閸喒鏀介柣鎰綑閻忥附銇勯鐐村枠闁轰礁绉撮埢搴ㄥ箻缁瀚奸梻浣哄帶椤洟宕愰弴銏犲嚑闁瑰瓨绻嶉悢鍡涙煟閻旂厧浜伴柛搴㈡⒒閳ь剚顔栭崰姘垛€﹂崼銉晣闁稿繒鍘х欢鐐烘倵閿濆骸澧鐐茬墦濮婄粯绗熼埀顒勫焵椤掍胶鈽夌€规挸妫欑换娑欐媴閸愬弶鎼愰柛鎴犲█閺岋綁寮崹顔藉€梺?(\d+)", default=0)
        latency_ms = _parse_nl_heart_float(text, r"latency\s*(\d+(?:\.\d+)?)", r"闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁炬儳缍婇弻鐔兼⒒鐎靛壊妲紒鐐劤缂嶅﹪寮婚悢鍏尖拻閻庨潧澹婂Σ顔剧磼閻愵剙鍔ゆ繝鈧柆宥呯劦妞ゆ帒鍊归崵鈧柣搴㈠嚬閸欏啫鐣峰畷鍥ь棜閻庯絻鍔嬪Ч妤呮⒑閸︻厼鍔嬮柛銊ョ秺瀹曟劙鎮欏顔藉瘜闂侀潧鐗嗗Λ妤冪箔閹烘挶浜滈柨鏂跨仢瀹撳棛鈧鍠楅悡锟犮€侀弮鍫濋唶闁绘棁娓归悽缁樼節閻㈤潧孝闁挎洏鍊濆畷顖炴偋閸喐鐝℃繝鐢靛Х閺佸憡鎱ㄩ銏犵；闁瑰墽鍋ㄩ埀顒佸笒椤繈鏁愰崨顒€顥氬┑鐘愁問閸犳牠鏁冮妸銉㈡瀺闁挎繂娲﹂～鏇㈡煙閻戞ê娈鹃柣鏂垮悑閹偤姊洪锝囥€掔紒鈧崘銊㈡斀闁绘ɑ顔栭弳顖涗繆閹绘帗鍤囩€规洘鍨垮畷鐔碱敇濞戞ü澹曞┑顔筋焽閸樠囧几閻旇櫣纾奸柡鍐ㄥ€搁弸娑氣偓娈垮枟閹告娊骞冮姀銈嗘優闁革富鍘介～灞解攽閻樻剚鍟忛柛鐘愁殜閺佸啴鍩￠崨顓炲亶婵°倧绲介崯顐︽儗濡ゅ懏鐓曢柍鈺佸暔娴犳粓鏌￠埀顒佺鐎ｎ偆鍘藉┑鈽嗗灥閸嬫劗鏁☉娆戠闁瑰啿鍢茬€氼亞鎹㈤崱妯镐簻闁逛即娼ф禍婊勵殽閻愬澧电€规洩绻濋弻鍡楊吋閸℃瑥骞堟繝鐢靛█濞佳呪偓姘煎墴瀹曟繈濡堕崱鏇犵畾濡炪倖鍔﹂崑鍕倶閿曞倹鐓涚€光偓閳ь剟宕伴弽顓溾偓浣糕槈閵忕姴鑰垮┑掳鍊撶粈浣糕枍瑜庢穱濠囧Χ閸℃﹩妫冨┑顔硷功缁垶骞忛崨鏉戝窛濠电姴鍟崜闈涒攽閻橆喖鐏辨繛澶嬬☉鐓ゆい鎾卞灩閺勩儵鏌嶈閸撴岸濡甸崟顖氱鐎广儱娴傚Σ顔界節閳封偓鐏炵晫浠搁梺鍝勬湰缁嬫垿锝炲┑瀣垫晢濠㈣泛锕ゆ竟搴繆閵堝洤啸闁稿鍋ら幃褍顭ㄩ崼婵堫槴闂佸湱鍎ら〃鍛矆閸屾凹鐔嗛悹铏瑰皑濮婃顭跨憴鍕闁诡喗顨堥幉鎾礋椤掑偆妲繝鐢靛仦濞兼瑩宕愰崹顔炬殾妞ゆ牜鍋涢悙濠囨煃閸濆嫬鏆熼柨娑欑箞濮婅櫣绮欓幐搴㈡嫳闂佽崵鍠嗛崝鎴濈暦濡も偓閻ｆ繈宕熼鍌氬箞闂佽鍑界紞鍡涘磻閸涱厾鏆︾€光偓閳ь剟鍩€椤掑喚娼愭繛鍙壝叅闁绘棃顥撻弳锔戒繆椤栨瑨顒熼柛鐔锋噺閵囧嫰寮埀顒勫磿閾忣偅鍙忔繛宸簼閳锋帒霉閿濆牆袚缁绢厼鐖奸弻娑㈡偐閾忣偄闉嶉梺閫涚┒閸旀垿鐛鈧、娆撴寠婢跺苯绲介梻鍌欒兌缁垶寮婚妸鈺佽Е閻庯綆鍠楅崑鍌炵叓閸ャ劎鈯曢柣鎾存礃閵囧嫰骞囬崜浣瑰仹缂備胶濮甸悧鐘诲蓟濞戞埃鍋撻敐鍛倎闂侇収鍨堕弻锛勪沪閸撗勫垱闂佽桨绀侀崐濠氬箯閻樿绠甸柟鐑樻閸炲爼姊婚崒娆掑厡妞ゎ厼鐗撻、鏍幢濞戞顔夐梺鎼炲労閸撴瑩寮告笟鈧弻鐔兼焽閿曗偓楠炴鏌涙惔锝呮灈闁哄苯绉规俊鐑藉Ω閵壯勵嚄闂備礁鎲￠崹鐢电礊婵犲偆娼栫紓浣股戞刊鎾煣韫囨洘鍤€缂佹绱曠槐鎾存媴缁涘娈梺鍝ュ櫏閸嬪棝宕ｉ崨瀛樷拺缂備焦蓱閳锋帡鏌涘Ο鐘叉噽娑撳秹鏌″鍐ㄥ缂佽妫濋弻鏇㈠醇濠靛洤娅ｉ梺鍝勬閸嬨倝寮诲☉銏″亹闁告瑥顦藉Λ锕傛⒑閸濆嫭婀伴柣鈺婂灠椤曪綁骞橀鍢夆晠鏌曟径鍫濆姶婵炶偐鍋撴穱濠囨倷椤忓嫧鍋撹閹峰綊鎮㈤悡搴ｏ紵闂佹眹鍨婚…鍫㈢不閺嶃劋绻嗛柕鍫濆€告禍楣冩⒑瀹曞洨甯涢柟鐟版搐閻ｇ柉銇愰幒婵囨櫓闂佷紮绲芥總鏃堟焽椤栨稓绡€闁汇垽娼цⅴ闂佺懓鍢查崯鏉戠暦濮椻偓椤㈡瑩鎮℃惔鈥虫尋婵犲痉鏉库偓妤佹叏閻戣棄纾婚柣鎰惈閸ㄥ倿鏌熺粙鍨劉闁告瑥绻橀弻宥堫檨闁告挾鍠栧濠氭晲閸滀焦寤洪梺绯曞墲閵囩偟鑺遍悡搴富闁靛牆楠告禍婊呯磼婢跺本鍤€妞ゎ偄绻愮叅妞ゅ繐鎷嬪Λ鍐ㄢ攽閻愭潙鐏︽慨妯稿姂閹偓銈ｉ崘鈹炬嫼闂佸憡绋戦敃銉т焊閹殿喚纾奸悹鍥ㄥ絻閳ь剙娼″畷娲閻欌偓閸氬鏌涘鈧懗鍫曞矗閸℃稒鈷戦柛鎾村絻娴滅偤鏌涢悢閿嬪仴妤犵偛绻愮叅妞ゅ繐鎳夐幏铏圭磼缂併垹骞栭柟鍐插缁傚秷銇愰幒鎾跺幈闁诲函缍嗛崑鍡椕虹€电硶鍋撶憴鍕缂佽鍊介悘鍐⒑閸涘﹣绶遍柛鐘冲哺瀹曘垽骞栨担鍏夋嫼闂佸憡绋戦敃锝囨闁秵鐓曢柣妯哄暱閸濇椽鏌熼姘辩劯妤犵偞甯掕灃濞达絽鎼獮?(\d+(?:\.\d+)?)", default=0.0)
        error_match = re.search(r"(?:error|闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁炬儳缍婇弻鐔兼⒒鐎靛壊妲紒鐐劤缂嶅﹪寮婚悢鍏尖拻閻庨潧澹婂Σ顔剧磼閻愵剙鍔ょ紓宥咃躬瀵鎮㈤崗灏栨嫽闁诲酣娼ф竟濠偽ｉ鍓х＜闁绘劦鍓欓崝銈囩磽瀹ュ拑韬€殿喖顭烽幃銏ゅ礂鐏忔牗瀚介梺璇查叄濞佳勭珶婵犲伣锝夘敊閸撗咃紲闂佺粯鍔﹂崜娆撳礉閵堝洨纾界€广儱鎷戦煬顒傗偓娈垮枛椤兘寮幇顓炵窞濠电姴瀚烽崥鍛存⒒娴ｇ懓顕滅紒璇插€块獮澶娾槈閵忕姷顔掔紓鍌欑劍椤洭宕㈡潏銊х瘈闁汇垽娼у瓭闂佺锕ょ紞濠傜暦閹达箑唯闁冲搫鍊婚崢鎼佹煟韫囨洖浠╂い鏇嗗嫭鍙忛柛灞惧閸嬫挸鈻撻崹顔界彯闂侀潻缍囩徊浠嬫偩闁垮闄勯柛娑橈工娴滄粓姊洪崨濠勨槈妞ゎ収鍓熷畷顒勫醇閺囩啿鎷婚梺绋挎湰閻熴劑宕楃仦淇变簻妞ゆ挾鍋熸晶锔姐亜閵忥紕澧电€规洖宕埥澶娢熺喊鍗炴暪闂傚倷绀侀幉锟犲箰閸℃稑绀冮柕濞у倸鏋堟繝纰夌磿閸嬫垿宕愰弽顓炵濡わ絽鍟壕濠氭煙闁箑鍔﹂柨婵嗩槸闁卞洭鏌ｉ弬鍨暢缂佺姵鑹鹃—鍐Χ閸℃瑥顫х紓浣筋嚙缁夌懓鐣烽棃娑掓瀻闁规壋鏅欑花濠氭⒑閹稿孩顥嗘い顐㈩槸閳诲秵绻濋崘鈺佸伎婵犵數濮撮幊蹇涱敂閻樼數纾兼い鏃傛櫕閹冲啴鏌嶇拠鏌ュ弰妤犵偛妫滈ˇ鎻掆攽閳瑥鎳愮壕浠嬫煕鐏炲墽鎳嗛柛蹇撶灱缁辨帡顢氶崨顓犱化闂佺懓绠嶉崹钘夌暦閻撳簶鏀介柟閭﹀墯椤撳灝鈹戦悩鍨毄濠殿喚鏁婚幊婵囥偅閸愩劎顢呴梺瑙勫劶婵倝鎮￠悢鍏肩厸闁告劑鍔嶆径鍕亜韫囨洖校闁靛洤瀚板顕€鍩€椤掆偓鐓ら柣鏂款殠濞兼牠鏌ц箛鎾磋础缁炬儳鍚嬫穱濠囶敍濮橆厽鍎撻悶姘箞濮婄粯鎷呴崨濠傛殘闂佽鐡曢褔鎮惧┑瀣濞达絽鎽滈悾娲⒑濮瑰洤鐏╅柟璇х節瀵彃鈹戠€ｎ偆鍘遍柣蹇曞仜婢т粙鍩㈤幘缁樼厵?\s*[:闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁炬儳缍婇弻鐔兼⒒鐎靛壊妲紒鐐劤缂嶅﹪寮婚悢鍏尖拻閻庨潧澹婂Σ顔剧磼閻愵剙鍔ょ紓宥咃躬瀵鎮㈤崗灏栨嫽闁诲酣娼ф竟濠偽ｉ鍓х＜闁绘劦鍓欓崝銈囩磽瀹ュ拑韬€殿喖顭烽弫鎰緞婵犲嫷鍚呴梻浣瑰缁诲倿骞夊☉銏犵缂備焦顭囬崢杈ㄧ節閻㈤潧孝闁稿﹤缍婂畷鎴﹀Ψ閳哄倻鍘搁柣蹇曞仩椤曆勬叏閸屾壕鍋撳▓鍨珮闁革綇绲介悾閿嬬附閸涘﹤浜滈梺鍛婄☉椤剟宕崼鏇熲拻闁稿本鐟ㄩ崗灞俱亜椤撶偟澧︽い銏＄墵瀹曞崬鈽夊Ο纰卞敹闂備礁鎲￠幐鍡涘礃閵娧傚枈濠碉紕鍋戦崐鏍箰妤ｅ啫纾婚柟閭﹀劦閿濆閱囬柣鏂垮缁犳艾顪冮妶鍡欏缂佽绉瑰畷闈涒枎閹邦喚顔曢梺鍛婄☉濞层倕煤閿曞倸鐓曢柟瀵稿仧缁犻箖鏌ゆ總鍓叉澓闁搞倖鐟﹂〃銉╂倷閹碱厽鐤侀梺鍝勭焿缂嶄線骞冮姀銈呯煑濠㈣泛顑囪ぐ瀣煟鎼淬埄鍟忛柛鐘崇墵閳ワ箓鎮滈挊澶岀暫闂侀潧绻堥崐鏇犵矆閸岀偞鐓熼柟鎯х－瀹€鎼佹煕鐎ｎ偅灏电紒杈ㄥ笒铻ｉ悹鍥ㄥ絻娴煎孩绻濆閿嬫緲閳ь剚鍔欏畷鎴﹀箻缂堢姷绠氬銈嗗姧缁插潡骞婇崶顒佺厵妞ゆ洍鍋撶紒鐘崇墵楠炲啫顭ㄩ崼鐔锋疅闂侀潧顦崹铏光偓?\s*(.+)$", text, re.IGNORECASE)
        last_error = error_match.group(1).strip() if error_match else ""

        record = self.services.heart_service.beat(
            component,
            status=status,
            queue_depth=queue_depth,
            active_task_count=active_task_count,
            latency_ms_p95=latency_ms,
            failure_streak=failure_streak,
            last_error=last_error,
        )
        payload = self.services.heart_service.status()
        matched = next((item for item in payload["components"] if item["component_id"] == component), None)
        if matched is None:
            print(f"Heartbeat sent, but status check failed: component not found: {component}")
            return
        print(
            "Heartbeat configured and validated: "
            f"{record.component_id} | status={record.status} | queue={record.queue_depth} | active={record.active_task_count}"
        )
        print(f"Status panel: {matched['component_id']} | {matched['status']} | age={matched['last_seen_age_sec']}")


    def _handle_task_command(self, args: list[str]) -> None:
        usage = "/task show|claim|release|block|retry|jump <task_id>"
        if not args or args[0].lower() in {"list", "ls", "status"}:
            print(self.renderer.render_task_board(self._task_board_data()))
            print(f"Usage: {usage}")

        action = args[0].lower()
        if action in {"help", "h"}:
            print(f"Usage: {usage}")
            print("Example: /task claim demo-task")
            return

        if len(args) < 2:
            print(f"Missing task_id. Usage: {usage}")
            return

        task_id = args[1].strip()
        if not task_id:
            print(f"task_id is required. Usage: {usage}")
            return

        if action in {"show", "detail"}:
            self._print_task_detail(task_id)
            return

        if action in {"claim", "acquire"}:
            task = self.services.task_store.get_task(task_id)
            title = task.title if task is not None and task.title else task_id
            metadata = dict(task.metadata) if task is not None else {"source": "shell-manual"}
            try:
                lease = self.services.task_store.acquire_lease(
                    task_id,
                    owner=f"shell:{self.session_id}",
                    title=title,
                    metadata=metadata,
                )
            except LeaseConflictError as exc:
                print(f"claim failed: {exc}")
                self._print_task_detail(task_id)
                return

            try:
                self.services.task_store.start_task(task_id, lease_id=lease.lease_id)
            except TaskStateError:
                # Fallback to leased state when running transition is not allowed.
                pass

            print(f"claimed: {task_id} | lease={lease.lease_id[:8]}")
            self._print_task_detail(task_id)
            return

        if action in {"release", "unlock"}:
            lease = self.services.task_store.get_lease(task_id)
            if lease is None:
                print(f"no active lease: {task_id}")
                self._print_task_detail(task_id)
                return

            self.services.task_store.release_lease(task_id, lease_id=lease.lease_id, next_status=TaskStatus.PENDING)
            print(f"released to pending: {task_id}")
            self._print_task_detail(task_id)
            return

        if action in {"block", "blocked"}:
            reason = " ".join(args[2:]).strip() or "manual blocked from shell"
            try:
                lease = self.services.task_store.get_lease(task_id)
                if lease is None:
                    task = self.services.task_store.get_task(task_id)
                    title = task.title if task is not None and task.title else task_id
                    metadata = dict(task.metadata) if task is not None else {"source": "shell-manual"}
                    lease = self.services.task_store.acquire_lease(
                        task_id,
                        owner=f"shell:{self.session_id}",
                        title=title,
                        metadata=metadata,
                    )
                self.services.task_store.block_task(task_id, lease_id=lease.lease_id, reason=reason)
            except LeaseConflictError as exc:
                print(f"block failed: {exc}")
                self._print_task_detail(task_id)
                return

            print(f"blocked: {task_id} | reason={reason}")
            self._print_task_detail(task_id)
            return

        if action in {"retry", "requeue"}:
            task = self.services.task_store.get_task(task_id)
            if task is None:
                print(f"task not found: {task_id}")
                return

            try:
                lease = self.services.task_store.get_lease(task_id)
                if lease is None:
                    lease = self.services.task_store.acquire_lease(
                        task_id,
                        owner=f"shell:{self.session_id}",
                        title=task.title or task_id,
                        metadata=dict(task.metadata),
                    )
                self.services.task_store.release_lease(
                    task_id,
                    lease_id=lease.lease_id,
                    next_status=TaskStatus.PENDING,
                )
            except LeaseConflictError as exc:
                print(f"retry failed: {exc}")
                self._print_task_detail(task_id)
                return

            retry_count = int(task.metadata.get("retry_count", 0) or 0) + 1
            self.services.task_store.update_metadata(
                task_id,
                {"retry_count": retry_count, "last_retry_at": datetime.now(timezone.utc).isoformat()},
            )
            print(f"retried: {task_id} | retry_count={retry_count}")
            self._print_task_detail(task_id)
            return

        if action in {"jump", "open"}:
            self._print_task_jump(task_id)
            return

        print(f"Unknown task action: {action}. Usage: {usage}")
    def _print_task_detail(self, task_id: str) -> None:
        task = self.services.task_store.get_task(task_id)
        if task is None:
            print(f"Task not found: {task_id}")
            return

        lease = self.services.task_store.get_lease(task_id)
        summary = f"{task.task_id} | status={task.status.value}"
        items = [
            f"title: {task.title or '(untitled)'}",
            f"updated_at: {task.updated_at}",
            f"created_at: {task.created_at}",
        ]
        if task.blocked_reason:
            items.append(f"blocked_reason: {task.blocked_reason}")
        if lease is not None:
            remaining = self._seconds_until(lease.expires_at)
            items.append(f"lease: {lease.lease_id[:8]} | owner={lease.owner}")
            items.append(f"lease_expires_at: {lease.expires_at} | remaining={remaining}s")
        if task.metadata:
            items.append(f"metadata: {self._compact_preview(json.dumps(task.metadata, ensure_ascii=False), max_chars=96)}")

        if self.services.worktree_manager is not None:
            try:
                record = self.services.worktree_manager.get_record(task_id)
            except WorktreeError as exc:
                items.append(f"worktree: error: {exc}")
            else:
                if record is not None:
                    items.append(f"worktree: {record.path} | attached={record.attached}")

        print(
            self.renderer.render_named_board(
                title="Task Detail",
                summary=summary,
                items=items,
                empty_text="No task details.",
            )
        )

    def _print_task_jump(self, task_id: str) -> None:
        task = self.services.task_store.get_task(task_id)
        if task is None:
            print(f"Task not found: {task_id}")
            return

        items: list[str] = []
        if self.services.worktree_manager is not None:
            try:
                record = self.services.worktree_manager.get_record(task_id)
            except WorktreeError as exc:
                items.append(f"worktree: error: {exc}")
            else:
                if record is None:
                    items.append("worktree: not available")
                else:
                    items.append(f"worktree: {record.path}")
                    items.append(f"branch: {record.branch} | attached={record.attached} | exists={record.path_exists}")

        session_id = str(task.metadata.get("session_id", "") or "")
        if session_id:
            items.append(f"session: {session_id}")
            items.append(f"replay: /replay {session_id}")
        else:
            items.append("session: not available in task metadata")

        print(
            self.renderer.render_named_board(
                title="Task Jump",
                summary=f"{task_id} quick navigation hints",
                items=items,
                empty_text="No jump hints.",
            )
        )
    def _handle_queue_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"list", "ls", "show", "status"}:
            print(self.renderer.render_queue_board(self._queue_board_data()))
            return
        action = args[0].lower()
        if action == "process":
            max_items = 20
            if len(args) > 1 and args[1].isdigit():
                max_items = max(1, int(args[1]))
            processed = self.services.delivery_queue.process_all(_delivery_handlers(self.services), max_items=max_items)
            items = [
                f"{item.get('delivery_id', '')[:8]} | status={item.get('status', '')} | error={self._compact_preview(str(item.get('last_error', '') or ''), max_chars=48)}"
                for item in processed[:8]
            ]
            print(
                self.renderer.render_named_board(
                    title="Queue Process Result",
                    summary=f"processed {len(processed)} items",
                    items=items,
                    empty_text="no pending deliveries",
                )
            )
            return
        if action == "recover":
            recovered = self.services.delivery_queue.recover_pending()
            print(
                self.renderer.render_named_board(
                    title="Queue Recovery",
                    summary=f"recovered {len(recovered)} items",
                    items=[f"recovered: {item[:8]}" for item in recovered],
                    empty_text="nothing to recover",
                )
            )
            return
        if action == "replay":
            target = args[1].strip() if len(args) > 1 else "all"
            replayed = self._replay_failed_deliveries(target)
            print(
                self.renderer.render_named_board(
                    title="Failed Queue Replay",
                    summary=f"replayed {len(replayed)} items",
                    items=[f"{item['old'][:8]} -> {item['new'][:8]} | kind={item['kind']}" for item in replayed],
                    empty_text="no failed deliveries to replay",
                )
            )
            return
        print("Unknown queue command. Use status|process [N]|recover|replay [delivery_id|all].")

    def _replay_failed_deliveries(self, target: str) -> list[dict[str, str]]:
        payload = self.services.delivery_queue.status()
        failed = list(payload.get("failed") or [])
        selected: list[dict[str, Any]]
        if target.lower() == "all":
            selected = failed
        else:
            selected = [item for item in failed if str(item.get("delivery_id", "")).startswith(target)]
        replayed: list[dict[str, str]] = []
        for item in selected:
            new_item = self.services.delivery_queue.enqueue(
                str(item.get("kind", "")),
                dict(item.get("payload") or {}),
                max_attempts=int(item.get("max_attempts", 3) or 3),
            )
            replayed.append(
                {
                    "old": str(item.get("delivery_id", "")),
                    "new": new_item.delivery_id,
                    "kind": str(item.get("kind", "")),
                }
            )
        return replayed

    def _handle_locks_command(self, args: list[str]) -> None:
        if args and args[0].lower() == "reconcile":
            reconciled = self.services.task_store.reconcile_expired_leases()
            print(
                self.renderer.render_named_board(
                    title="Lock Reconcile",
                    summary=f"reconciled {len(reconciled)} expired leases",
                    items=[f"{item.task_id} -> {item.status.value}" for item in reconciled],
                    empty_text="no expired leases",
                )
            )
            return
        print(self.renderer.render_lock_board(self._lock_board_data()))

    def _handle_turns_command(self, args: list[str]) -> None:
        if not self._turn_history:
            print("No turn history yet.")
            return
        expanded_turn: int | None = None
        if args and args[0].isdigit():
            expanded_turn = int(args[0])
        elif len(args) >= 2 and args[0].lower() == "expand" and args[1].isdigit():
            expanded_turn = int(args[1])
        self._print_turn_fold_board(expanded_turn=expanded_turn)

    def _handle_view_command(self, args: list[str]) -> None:
        compact_aliases = {"compact", "focus", "minimal", "default"}
        full_aliases = {"full", "verbose", "workbench", "expanded"}
        if not args:
            print(f"Current post-turn view: {self._post_turn_view} (options: compact/full)")
            return
        token = args[0].strip().lower()
        if token in compact_aliases:
            self._post_turn_view = "compact"
            print("Switched to compact: future turns show concise summaries.")
            return
        if token in full_aliases:
            self._post_turn_view = "full"
            print("Switched to full: future turns auto-show full workbench panels.")
            return
        print("Usage: /view compact|full")

    def _handle_ops_command(self, args: list[str]) -> None:
        section = args[0].lower() if args else "all"
        if section in {"all", "overview"}:
            self._print_runtime_workbench_panel()
            self._print_watchdog_panel()
            self._print_lanes_delivery_panel()
            self._print_model_resilience_critic_panel()
            self._print_mcp_background_validate_panel()
            self._print_memory_workbench_panel()
            return
        if section in {"runtime", "metrics", "health"}:
            self._print_runtime_workbench_panel()
            return
        if section in {"watchdog", "heart"}:
            self._print_watchdog_panel()
            return
        if section in {"lanes", "delivery", "queue"}:
            self._print_lanes_delivery_panel()
            return
        if section in {"model", "resilience", "critic"}:
            self._print_model_resilience_critic_panel()
            return
        if section in {"mcp", "background", "validate"}:
            self._print_mcp_background_validate_panel()
            return
        if section == "memory":
            self._print_memory_workbench_panel()
            return
        if section == "skills":
            self._print_skills_panel()
            return
        if section == "retrieval":
            self._print_retrieval_panel()
            return
        print("Unknown ops section. Use all/runtime/watchdog/lanes/model/mcp/memory/skills/retrieval.")

    def _handle_watchdog_local_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"show", "status", "list"}:
            self._print_watchdog_panel()
            return
        action = args[0].lower()
        if action == "scan":
            decisions = [item.to_dict() for item in self.services.watchdog.scan()]
            if decisions:
                self._latest_watchdog_decision = decisions[0]
            print(
                self.renderer.render_named_board(
                    title="Watchdog Scan",
                    summary=f"planned recoveries for {len(decisions)} components",
                    items=[f"{item['component_id']} | {item['status_before']} -> {item['status_after']}" for item in decisions],
                    empty_text="no red components",
                )
            )
            return
        if action == "simulate":
            if len(args) < 2:
                print("Usage: /watchdog simulate <component_id>")
                return
            decision = self.services.watchdog.simulate(args[1]).to_dict()
            self._latest_watchdog_decision = decision
            print(
                self.renderer.render_named_board(
                    title="Watchdog Simulation",
                    summary=f"{decision['component_id']} {decision['status_before']} -> {decision['status_after']}",
                    items=[
                        f"reason: {decision['reason']}",
                        f"snapshot: {decision['snapshot_path']}",
                        "actions: " + ", ".join(decision.get("actions", [])),
                    ],
                    empty_text="no result",
                )
            )
            return
        print("Unknown watchdog command. Use status|scan|simulate <component>.")

    def _handle_background_local_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"show", "status", "list"}:
            self._print_mcp_background_validate_panel()
            return
        action = args[0].lower()
        if action == "process":
            max_items = 20
            if len(args) > 1 and args[1].isdigit():
                max_items = max(1, int(args[1]))
            payload = self.services.skill_runtime.process_background_tasks(max_items=max_items)
            print(
                self.renderer.render_named_board(
                    title="Background Task Process",
                    summary=f"processed {len(payload)} items",
                    items=[self._compact_preview(json.dumps(item, ensure_ascii=False), max_chars=88) for item in payload],
                    empty_text="no processable background tasks",
                )
            )
            return
        print("Unknown background command. Use status|process [N].")

    def _handle_validate_local_command(self, args: list[str]) -> None:
        if not args or args[0].lower() in {"show", "status"}:
            self._print_mcp_background_validate_panel()
            return
        action = args[0].lower()
        if action == "run":
            pytest_target = args[1] if len(args) > 1 else "tests/core"
            report = self.services.validate_pipeline.run(pytest_target=pytest_target)
            self._save_validate_report(report)
            items = [f"{item['stage']}: {'ok' if item['ok'] else 'failed'}" for item in report.get("stages", [])]
            print(
                self.renderer.render_named_board(
                    title="Unified Validation",
                    summary=f"target={pytest_target} | ok={report.get('ok', False)}",
                    items=items,
                    empty_text="no validation output",
                )
            )
            return
        print("Unknown validate command. Use status|run [pytest_target].")

    def _print_watchdog_panel(self) -> None:
        heart = self.services.heart_service.status()
        red_components = [item for item in heart.get("components", []) if item.get("status") == "red"]
        snapshot_items: list[str] = []
        for path in sorted(self.services.layout.watchdog_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:3]:
            snapshot_items.append(path.name)
        items = [
            f"RED component: {item['component_id']} | error={self._compact_preview(str(item.get('last_error', '')), max_chars=56)}"
            for item in red_components
        ]
        if self._latest_watchdog_decision is not None:
            decision = self._latest_watchdog_decision
            items.append(
                f"latest recovery: {decision.get('component_id', '')} | "
                f"{decision.get('status_before', '')}->{decision.get('status_after', '')}"
            )
        if snapshot_items:
            items.append("latest snapshots: " + ", ".join(snapshot_items))
        print(
            self.renderer.render_named_board(
                title="Watchdog Panel",
                summary=f"red={len(red_components)} | snapshots={len(snapshot_items)}",
                items=items,
                empty_text="no abnormal components",
            )
        )

    def _print_lanes_delivery_panel(self) -> None:
        lane_payload = self.services.lane_scheduler.status()
        delivery = self.services.delivery_queue.status()
        items: list[str] = []
        for lane in list(lane_payload.get("lanes", []))[:6]:
            items.append(
                f"lane={lane.get('name')} | gen={lane.get('generation')} | queue={lane.get('queue_depth')} | active={lane.get('active_count')} | last={lane.get('last_status')}"
            )
        items.append(
            f"delivery pending={delivery.get('pending_count')} | failed={delivery.get('failed_count')} | done={delivery.get('done_count')}"
        )
        for item in list(delivery.get("failed", []))[:3]:
            items.append(f"failed {str(item.get('delivery_id', ''))[:8]} | kind={item.get('kind')} | attempts={item.get('attempts')}")
        print(
            self.renderer.render_named_board(
                title="Lanes / Delivery Panel",
                summary=f"lanes={len(lane_payload.get('lanes', []))}",
                items=items,
                empty_text="no lanes or delivery data",
            )
        )

    def _print_model_resilience_critic_panel(self) -> None:
        events = self.services.session_store.replay(self.session_id)
        resilience_event = next(
            (
                dict(event.get("payload") or {})
                for event in reversed(events)
                if event.get("event_type") == "resilience_result"
            ),
            {},
        )
        profile = str(resilience_event.get("profile") or resilience_event.get("selected_profile") or "unknown")
        attempts = list(resilience_event.get("attempts") or [])
        fallback_attempts = [item for item in attempts if str(item.get("status", "")) == "failed"]
        critic_rules = self._load_critic_rules()
        critic_failures = self._count_jsonl_lines(self.services.layout.critic_failures_path)
        items: list[str] = []
        items.append(f"profile: {profile}")
        if attempts:
            items.append("attempts: " + ", ".join(f"{item.get('layer')}:{item.get('status')}" for item in attempts[:6]))
        items.append(f"fallback_failed_attempts: {len(fallback_attempts)}")
        items.append(f"critic_failures: {critic_failures}")
        if critic_rules:
            items.append(f"critic_rules: {len(critic_rules)} rules")
            items.extend(
                f"rule {item.get('failure_kind')}: {self._compact_preview(str(item.get('rule', '')), max_chars=60)}"
                for item in critic_rules[:3]
            )
        print(
            self.renderer.render_named_board(
                title="Model / Resilience / Critic Panel",
                summary=f"profile={profile} | rules={len(critic_rules)}",
                items=items,
                empty_text="no model routing or critic data",
            )
        )

    def _print_mcp_background_validate_panel(self) -> None:
        mcp_servers = self.services.mcp_runtime.list_servers()
        background = self.services.skill_runtime.background_status()
        validate_report = self._load_validate_report()
        items: list[str] = []
        items.append(f"mcp_servers: {len(mcp_servers)}")
        for server in mcp_servers[:3]:
            items.append(f"mcp {server.get('name')} | enabled={server.get('enabled')} | cmd={server.get('command')}")
        invocation_files = sorted(
            self.services.layout.mcp_invocations_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:3]
        if invocation_files:
            items.append("recent_mcp_calls: " + ", ".join(path.name for path in invocation_files))
        items.append(
            f"background pending={background.get('pending_count')} | failed={background.get('failed_count')} | done={background.get('done_count')}"
        )
        result_files = sorted(
            self.services.layout.background_results_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:2]
        if result_files:
            items.append("recent_background_results: " + ", ".join(path.name for path in result_files))
        if validate_report:
            items.append(f"validate ok={validate_report.get('ok')} | stages={len(validate_report.get('stages', []))}")
            items.extend(
                f"validate {item.get('stage')}: {'ok' if item.get('ok') else 'failed'}"
                for item in validate_report.get("stages", [])[:4]
            )
        else:
            items.append("validate: no record yet; run /validate run")
        print(
            self.renderer.render_named_board(
                title="MCP / Background / Validate Panel",
                summary=f"mcp={len(mcp_servers)} | bg_pending={background.get('pending_count')}",
                items=items,
                empty_text="no ops data",
            )
        )

    def _print_runtime_workbench_panel(self) -> None:
        rollup = self._load_metrics_rollup()
        heart = dict(rollup.get("heart") or {})
        components = heart.get("components") if isinstance(heart.get("components"), list) else []
        status_counts: dict[str, int] = {}
        for item in components:
            status = str(item.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1
        red_components = [item for item in components if str(item.get("status", "")) == "red"]
        task_counts = dict(rollup.get("task_counts") or {})
        delivery = self.services.delivery_queue.status()
        background = self.services.skill_runtime.background_status()
        validate_report = self._load_validate_report()
        subagents = self.services.agent_team_runtime.list_subagents(limit=100)
        active_subagents = [item for item in subagents if item.status in {"queued", "running"}]

        items: list[str] = []
        generated_at = str(rollup.get("generated_at") or "").strip()
        if generated_at:
            items.append(f"rollup: {generated_at}")
        items.append(
            f"heart green={status_counts.get('green', 0)} | yellow={status_counts.get('yellow', 0)} | red={status_counts.get('red', 0)}"
        )
        for item in red_components[:4]:
            items.append(
                "red "
                + f"{item.get('component_id', '')} | age={item.get('last_seen_age_sec', 0)}s | "
                + f"queue={item.get('queue_depth', 0)} | "
                + f"error={self._compact_preview(str(item.get('last_error', '') or ''), max_chars=48)}"
            )
        if task_counts:
            items.append("tasks: " + ", ".join(f"{name}={count}" for name, count in sorted(task_counts.items())))
        items.append(
            "snapshots: "
            + f"todo={rollup.get('todo_snapshot_count', 0)} | "
            + f"context={rollup.get('context_snapshot_count', 0)} | "
            + f"worktree={rollup.get('managed_worktree_count', 0)}"
        )
        items.append(
            f"delivery: pending={delivery.get('pending_count', 0)} | failed={delivery.get('failed_count', 0)} | done={delivery.get('done_count', 0)}"
        )
        items.append(
            f"background: pending={background.get('pending_count', 0)} | failed={background.get('failed_count', 0)} | done={background.get('done_count', 0)}"
        )
        if active_subagents:
            items.append(f"subagents: active={len(active_subagents)} | total={len(subagents)}")
        if validate_report:
            items.append(
                f"validate: ok={validate_report.get('ok')} | stages={len(validate_report.get('stages', []))}"
            )

        summary = (
            f"events={rollup.get('event_count', 0)} | sessions={rollup.get('session_count', 0)} | "
            + f"red={status_counts.get('red', 0)}"
        )
        print(
            self.renderer.render_named_board(
                title="Runtime / Metrics Panel",
                summary=summary,
                items=items,
                empty_text="no runtime metrics",
            )
        )

    def _print_memory_workbench_panel(self) -> None:
        bootstrap = self.services.memory_runtime.bootstrap_memory_files()
        memory_files = self.services.memory_runtime.memory_files(include_preview=True)
        effective_prefs = self.services.memory_runtime.effective_preferences()
        timeline_items = self._memory_timeline_items()
        skill_index = self._memory_skill_index()
        keyword_index = self._memory_keyword_index()
        recent_entries = self._recent_memory_entries(limit=4)
        retrieval_entries = self._recent_memory_entries(limit=2, kinds={"retrieval"})
        skill_counts = self._top_index_items(skill_index, limit=4)
        keyword_counts = self._recent_memory_keywords(limit=6)

        items: list[str] = []
        for entry in recent_entries:
            items.append("recent " + self._format_memory_entry(entry))
        if skill_counts:
            items.append("skills: " + ", ".join(f"{name}x{count}" for name, count in skill_counts))
        if keyword_counts:
            items.append("keywords: " + ", ".join(f"{name}x{count}" for name, count in keyword_counts))
        items.append(f"files: {len(memory_files)} | prefs: {len(effective_prefs)}")
        if bootstrap.get("created_files"):
            items.append(f"bootstrap_created: {len(bootstrap.get('created_files', []))}")
        for item in memory_files[:3]:
            items.append(
                f"file {item.get('key', '')}: "
                + self._compact_preview(str(item.get("preview", "")), max_chars=56)
            )
        for entry in retrieval_entries:
            metadata = dict(entry.get("metadata") or {})
            items.append(
                f"retrieval: {metadata.get('route', 'unknown')} | results={metadata.get('result_count', 0)} | "
                + self._compact_preview(str(entry.get("text", "")), max_chars=56)
            )

        summary = f"entries={len(timeline_items)} | skills={len(skill_index)} | keywords={len(keyword_index)}"
        print(
            self.renderer.render_named_board(
                title="Memory / Skills / Retrieval Panel",
                summary=summary,
                items=items,
                empty_text="no memory or retrieval traces",
            )
        )

    def _print_skills_panel(self, *, query: str = "") -> None:
        payload = self.services.skill_runtime.list_skills()
        needle = query.strip().lower()
        if needle:
            payload = [
                item
                for item in payload
                if needle in str(item.get("name", "")).lower()
                or needle in str(item.get("summary", "")).lower()
            ]
        recent_usage = dict(self._top_index_items(self._memory_skill_index(), limit=8))
        items: list[str] = []
        if recent_usage:
            items.append("recent_usage: " + ", ".join(f"{name}x{count}" for name, count in recent_usage.items()))
        for item in payload[:8]:
            name = str(item.get("name", "unknown"))
            recent = recent_usage.get(name)
            recent_text = f" | used={recent}" if recent else ""
            items.append(
                f"{name} | {item.get('source', 'builtin')}{recent_text} | "
                + self._compact_preview(str(item.get("summary", "")), max_chars=68)
            )
        print(
            self.renderer.render_named_board(
                title="Skills Panel",
                summary=f"available={len(payload)}" + (f" | query={needle}" if needle else ""),
                items=items,
                empty_text="no skills matched",
            )
        )

    def _print_retrieval_panel(self, *, limit: int = 4) -> None:
        entries = self._recent_memory_entries(limit=limit, kinds={"retrieval"})
        items: list[str] = []
        for entry in entries:
            metadata = dict(entry.get("metadata") or {})
            items.append(
                f"{metadata.get('route', 'unknown')} | results={metadata.get('result_count', 0)} | "
                + self._compact_preview(str(entry.get("text", "")), max_chars=72)
            )
        print(
            self.renderer.render_named_board(
                title="Retrieval Panel",
                summary=f"recent={len(entries)}",
                items=items,
                empty_text="no retrieval records",
            )
        )

    def _print_retrieval_payload(self, title: str, payload: dict[str, Any]) -> None:
        decision = dict(payload.get("decision") or {})
        results = list(payload.get("results") or [])
        items = [
            f"route={decision.get('route', 'unknown')} | retrieve={decision.get('retrieve', False)} | enough={decision.get('enough', False)}",
            f"reason: {decision.get('reason', '')}",
        ]
        query_terms = list(decision.get("query_terms") or [])
        if query_terms:
            items.append("query_terms: " + ", ".join(query_terms))
        for item in results[:6]:
            items.append(self._format_retrieval_result(item))
        print(
            self.renderer.render_named_board(
                title=title,
                summary=f"results={len(results)}",
                items=items,
                empty_text="no retrieval results",
            )
        )

    def _save_validate_report(self, report: dict[str, Any]) -> None:
        path = self.services.layout.metrics_dir / "validate-latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _load_validate_report(self) -> dict[str, Any]:
        path = self.services.layout.metrics_dir / "validate-latest.json"
        return self._load_json_dict(path)

    def _load_metrics_rollup(self) -> dict[str, Any]:
        path = self.services.layout.metrics_dir / "rollup-latest.json"
        payload = self._load_json_dict(path)
        if payload:
            return payload
        snapshot = build_health_snapshot(self.services)
        return {
            "generated_at": None,
            "event_count": snapshot.get("event_count", 0),
            "session_count": snapshot.get("session_count", 0),
            "task_counts": snapshot.get("task_counts", {}),
            "todo_snapshot_count": snapshot.get("todo_snapshot_count", 0),
            "context_snapshot_count": snapshot.get("context_snapshot_count", 0),
            "managed_worktree_count": snapshot.get("managed_worktree_count", 0),
            "heart": self.services.heart_service.status(),
        }

    def _load_critic_rules(self) -> list[dict[str, Any]]:
        path = self.services.layout.critic_rules_path
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return []
        rules = payload.get("rules") if isinstance(payload, dict) else []
        return rules if isinstance(rules, list) else []

    @staticmethod
    def _count_jsonl_lines(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    @staticmethod
    def _load_json_dict(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _memory_timeline_items(self) -> list[dict[str, Any]]:
        payload = self.services.memory_runtime.timeline()
        items = payload.get("items") if isinstance(payload, dict) else []
        return items if isinstance(items, list) else []

    def _memory_skill_index(self) -> dict[str, list[str]]:
        payload = self.services.memory_runtime.skills()
        index = payload.get("index") if isinstance(payload, dict) else {}
        return index if isinstance(index, dict) else {}

    def _memory_keyword_index(self) -> dict[str, list[str]]:
        payload = self.services.memory_runtime.keywords()
        index = payload.get("index") if isinstance(payload, dict) else {}
        return index if isinstance(index, dict) else {}

    def _recent_memory_entries(
        self,
        *,
        limit: int = 6,
        kinds: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in reversed(self._memory_timeline_items()):
            kind = str(item.get("kind", ""))
            if kinds is not None and kind not in kinds:
                continue
            results.append(item)
            if len(results) >= limit:
                break
        return results

    def _find_memory_entry(self, entry_id: str) -> dict[str, Any] | None:
        needle = entry_id.strip()
        if not needle:
            return None
        for item in self._memory_timeline_items():
            if str(item.get("entry_id", "")) == needle:
                return item
        return None

    def _recent_memory_keywords(self, *, limit: int = 6, sample_size: int = 24) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for item in self._recent_memory_entries(limit=sample_size):
            for token in re.findall(r"[A-Za-z0-9_]{3,}", str(item.get("text", ""))):
                keyword = token.lower()
                if keyword in {"the", "and", "with", "from", "into", "this", "that", "please"}:
                    continue
                counts[keyword] = counts.get(keyword, 0) + 1
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]

    @staticmethod
    def _top_index_items(index: dict[str, Any], *, limit: int = 6) -> list[tuple[str, int]]:
        items: list[tuple[str, int]] = []
        for name, entry_ids in index.items():
            if not isinstance(name, str) or not isinstance(entry_ids, list):
                continue
            items.append((name, len(entry_ids)))
        return sorted(items, key=lambda pair: (-pair[1], pair[0]))[:limit]

    def _format_memory_entry(self, item: dict[str, Any], *, max_chars: int = 72) -> str:
        kind = str(item.get("kind", "unknown"))
        text = self._compact_preview(str(item.get("text", "")), max_chars=max_chars)
        created_at = str(item.get("created_at", ""))
        created_text = created_at[5:16] if len(created_at) >= 16 else created_at
        suffix = f" | {created_text}" if created_text else ""
        return f"[{kind}] {text}{suffix}"

    def _format_retrieval_result(self, item: dict[str, Any]) -> str:
        result_type = str(item.get("type", "local"))
        if result_type == "answer":
            return "answer: " + self._compact_preview(str(item.get("text", "")), max_chars=84)
        if result_type == "web":
            title = self._compact_preview(str(item.get("title", "")), max_chars=40)
            url = self._compact_preview(str(item.get("url", "")), max_chars=40)
            return f"web: {title} | {url}"
        path = str(item.get("path", ""))
        line = item.get("line", "")
        text = self._compact_preview(str(item.get("text", "")), max_chars=68)
        return f"local: {path}:{line} | {text}"

    def _context_usage_snapshot(self) -> dict[str, Any]:
        budget_tokens = max(1, int(self.services.config.runtime.context_auto_compact_char_count / 4))
        usage = dict(self._latest_model_usage or {})
        usage_tokens = (
            self._safe_int(usage.get("input_tokens"))
            + self._safe_int(usage.get("cache_creation_input_tokens"))
            + self._safe_int(usage.get("cache_read_input_tokens"))
        )
        if usage_tokens <= 0:
            usage_tokens = self._safe_int(usage.get("total_tokens"))
        if usage_tokens > 0:
            percent = min(100, int(round((usage_tokens * 100) / budget_tokens)))
            return {
                "percent": percent,
                "source": "usage",
                "level": self._context_usage_level(percent),
                "tokens": usage_tokens,
                "budget_tokens": budget_tokens,
            }

        messages = [item for item in self.services.session_store.load_messages(self.session_id) if item.get("role") != "system"]
        max_chars = max(1, int(self.services.config.runtime.context_auto_compact_char_count))
        char_count = sum(len(self._context_message_text(item)) for item in messages)
        percent = min(100, int(round((char_count * 100) / max_chars)))
        return {
            "percent": percent,
            "source": "estimate",
            "level": self._context_usage_level(percent),
            "message_count": len(messages),
            "char_count": char_count,
            "budget_chars": max_chars,
        }

    def _context_usage_summary(self) -> str:
        usage = self._context_usage_snapshot()
        percent = int(usage.get("percent", 0))
        source = str(usage.get("source", "estimate"))
        level = str(usage.get("level", "normal"))
        suffix = "~" if source != "usage" else ""
        text = f"ctx {percent}%{suffix}"
        if level == "warn":
            return text + " warn"
        if level == "high":
            return text + " high /compact"
        return text

    def _context_left_percent(self) -> int:
        usage = self._context_usage_snapshot()
        percent = max(0, min(100, int(usage.get("percent", 0))))
        return max(0, 100 - percent)

    @staticmethod
    def _context_usage_level(percent: int) -> str:
        if percent >= 90:
            return "high"
        if percent >= 70:
            return "warn"
        return "normal"

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _context_message_text(message: dict[str, Any]) -> str:
        parts: list[str] = []
        if "name" in message and message["name"] is not None:
            parts.append(str(message["name"]))
        if "content" in message and message["content"] is not None:
            parts.append(str(message["content"]))
        if "tool_calls" in message:
            try:
                parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
            except Exception:
                parts.append(str(message["tool_calls"]))
        if "tool_call_id" in message and message["tool_call_id"] is not None:
            parts.append(str(message["tool_call_id"]))
        return " ".join(item for item in parts if item)

    def _runtime_status_summary(self) -> str:
        heart = self.services.heart_service.status()
        components = list(heart.get("components", []))
        red = sum(1 for item in components if str(item.get("status", "")) == "red")
        yellow = sum(1 for item in components if str(item.get("status", "")) == "yellow")
        delivery = self.services.delivery_queue.status()
        background = self.services.skill_runtime.background_status()
        validate_report = self._load_validate_report()
        active_subagents = sum(
            1
            for item in self.services.agent_team_runtime.list_subagents(limit=100)
            if item.status in {"queued", "running"}
        )

        parts: list[str] = []
        if red:
            parts.append(f"red={red}")
        elif yellow:
            parts.append(f"yellow={yellow}")
        if int(delivery.get("failed_count", 0) or 0) > 0:
            parts.append(f"queue_failed={delivery.get('failed_count', 0)}")
        elif int(delivery.get("pending_count", 0) or 0) > 0:
            parts.append(f"queue_pending={delivery.get('pending_count', 0)}")
        if int(background.get("pending_count", 0) or 0) > 0:
            parts.append(f"bg={background.get('pending_count', 0)}")
        if active_subagents:
            parts.append(f"subagent={active_subagents}")
        if validate_report and not bool(validate_report.get("ok", False)):
            parts.append("validate=fail")
        runtime = "runtime ok" if not parts else "runtime " + " | ".join(parts[:4])
        return f"{self._context_usage_summary()} | {runtime}"

    @staticmethod
    def _seconds_until(timestamp: str) -> int:
        try:
            target = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
        except Exception:
            return 0
        now = datetime.now(timezone.utc)
        return int((target - now).total_seconds())


    @classmethod
    def _command_specs(cls) -> list[ShellCommandSpec]:
        return [ShellCommandSpec(name=item.name, description=item.palette_text) for item in _SHELL_LOCAL_COMMANDS]

    def _skill_specs(self) -> list[ShellCommandSpec]:
        payload = self.services.skill_runtime.list_skills()
        specs: list[ShellCommandSpec] = []
        seen: set[str] = set()
        for item in payload:
            name = str(item.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            summary = self._compact_preview(str(item.get("summary", "") or "").strip(), max_chars=96)
            description = f"[Skill] {summary}" if summary else "[Skill]"
            specs.append(ShellCommandSpec(name=name, description=description))
        return specs

    def _welcome_data(self) -> ShellWelcomeData:
        snapshot = build_health_snapshot(self.services)
        return ShellWelcomeData(
            version=__version__,
            session_id=self.session_id,
            model_name=str(snapshot["llm"]["model"]),
            provider=str(snapshot["llm"]["provider"]),
            workspace_root=str(snapshot["workspace_root"]),
            current_dir=str(snapshot["cwd"]),
            health_summary=self._health_summary(snapshot.get("heart_status_summary", {})),
            recent_activity=self._recent_activity_lines(limit=3),
            label=self.label,
            workspace_name=Path(str(snapshot["workspace_root"])).name,
            capability_summary=self._capability_summary(snapshot),
            last_session_id=str(snapshot["last_session_id"] or ""),
            tips=[
                "/help               show local commands",
                "/plan               plan first, then execute",
                "/act                execute directly in act mode",
                "/status             show runtime health",
                "/view compact/full  switch post-turn detail level",
                "/session            show current session",
            ],
            todo_summary=self._todo_summary_line(),
            task_summary=self._task_summary_line(),
            reasoning_effort=self._reasoning_effort,
            quick_suggestion=self._quick_suggestion_text(),
        )

    def _quick_suggestion_text(self) -> str:
        if self._workspace_has_git_commits():
            return "Summarize recent commits"
        snapshot = self.services.todo_manager.get(self.session_id)
        if snapshot is not None and snapshot.items:
            return "Review current TODO items"
        return "Help me plan next steps"

    def _workspace_has_git_commits(self) -> bool:
        root = self.services.layout.workspace_root
        git_marker = root / ".git"
        if not git_marker.exists():
            return False
        try:
            inside = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if inside.returncode != 0:
                return False
            latest = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--pretty=%h"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            return False
        return latest.returncode == 0 and bool(latest.stdout.strip())

    def _recent_activity_lines(self, *, limit: int) -> list[str]:
        lines: list[str] = []
        session_ids = [self.session_id, *self.services.session_store.latest_session_ids(limit=max(limit * 2, 5))]
        seen: set[str] = set()
        for session_id in session_ids:
            if session_id in seen:
                continue
            seen.add(session_id)
            preview = self._session_preview(self.services.session_store.replay(session_id))
            if preview is None:
                continue
            prefix = "current" if session_id == self.session_id else session_id[-8:]
            lines.append(f"{prefix}: {preview[:48]}")
            if len(lines) >= limit:
                break
        return lines or ["No recent activity"]

    def _todo_summary_line(self) -> str:
        payload = self.services.todo_manager.summarize(self.session_id)
        counts = payload.get("counts", {})
        if not counts:
            return "no todo items in current session"
        ordered = ["pending", "in_progress", "done", "blocked"]
        parts = [f"{name}={counts.get(name, 0)}" for name in ordered if counts.get(name, 0)]
        return f"{len(payload.get('items', []))} items | " + " | ".join(parts)

    def _task_summary_line(self) -> str:
        tasks = self.services.task_store.list_tasks()
        if not tasks:
            return "no tasks"
        counts: dict[str, int] = {}
        for item in tasks:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        ordered = ["pending", "leased", "running", "blocked", "done"]
        parts = [f"{name}={counts.get(name, 0)}" for name in ordered if counts.get(name, 0)]
        return f"{len(tasks)} tasks | " + " | ".join(parts)

    @staticmethod
    def _session_preview(events: list[dict[str, Any]]) -> str | None:
        for event in reversed(events):
            if event.get("event_type") == "turn_finished":
                payload = event.get("payload") or {}
                preview = str(payload.get("answer_preview", "")).strip()
                if preview:
                    return preview[:58]
            if event.get("event_type") == "message":
                payload = event.get("payload") or {}
                if payload.get("role") == "assistant":
                    content = str(payload.get("content", "")).strip()
                    if content:
                        return content[:58]
        return None

    @staticmethod
    def _health_summary(summary: dict[str, Any]) -> str:
        order = ("green", "yellow", "red", "unknown")
        parts = [f"{name}={summary.get(name, 0)}" for name in order if summary.get(name, 0)]
        return " | ".join(parts) if parts else "No health data yet"

    def _capability_summary(self, snapshot: dict[str, Any]) -> list[str]:
        llm_state = "ready" if snapshot["llm"]["configured"] else "demo"
        return [
            f"LLM={llm_state}",
            f"Tools={len(self.services.tool_router.tool_schemas())}",
            f"MCP={snapshot['mcp_server_count']}",
            f"Memory={snapshot['memory_entry_count']}",
        ]

    def _session_summary(self) -> dict[str, Any]:
        current_events = self.services.session_store.replay(self.session_id)
        return {
            "current_session_id": self.session_id,
            "current_session_title": self.services.session_store.session_title(self.session_id) or "",
            "event_count": len(current_events),
            "latest_session_ids": self.services.session_store.latest_session_ids(limit=5),
            "last_turn_preview": self._session_preview(current_events),
            "todo_snapshot_exists": self.services.todo_manager.get(self.session_id) is not None,
            "context_snapshot_exists": self.services.context_manager.get(self.session_id) is not None,
            "task_summary": self._task_summary_line(),
        }

    def _auto_claim_turn_task(self, raw: str) -> tuple[str, str]:
        task_id = f"shell-{self.session_id[-8:]}-turn-{self.turn_index:02d}"
        title = raw[:80] or f"shell turn {self.turn_index:02d}"
        lease = self.services.task_store.acquire_lease(
            task_id,
            owner=f"shell:{self.session_id}",
            title=title,
            metadata={
                "source": "shell",
                "session_id": self.session_id,
                "turn_index": self.turn_index,
                "mode": self.mode.value,
                "prompt": raw,
            },
        )
        self.services.task_store.start_task(task_id, lease_id=lease.lease_id)
        self._remember_group_event("Mechanism", f"lease acquired: {lease.lease_id[:8]} | {task_id}")
        self._append_status_event_line(kind="task", line=f"auto-claimed task {task_id}")
        return task_id, lease.lease_id

    def _complete_turn_task(self, task_id: str, lease_id: str, answer: str) -> None:
        self.services.task_store.complete_task(task_id, lease_id=lease_id)
        self.services.task_store.update_metadata(
            task_id,
            {
                "answer_preview": answer[:200],
                "completed_in_shell": True,
            },
        )
        self._remember_group_event("Mechanism", f"lease released and task completed: {task_id}")
        self._append_status_event_line(kind="task", line=f"task completed and archived {task_id}")

    def _block_turn_task(self, task_id: str, lease_id: str, error: str) -> None:
        self.services.task_store.block_task(task_id, lease_id=lease_id, reason=error)
        self.services.task_store.update_metadata(
            task_id,
            {
                "last_error": error,
            },
        )
        self._remember_group_event("Mechanism", f"lease released, task blocked: {task_id}")
        self._append_status_event_line(kind="task", line=f"task blocked {task_id} | {error}")

    def _todo_board_data(self) -> TodoBoardData:
        payload = self.services.todo_manager.summarize(self.session_id)
        items = payload.get("items", [])
        counts = payload.get("counts", {})
        summary = self._todo_summary_line()
        lines = [
            f"[{item.get('status', 'pending')}] {item.get('content', item.get('title', ''))}"
            for item in items[:6]
        ]
        if not lines and counts:
            lines.append("only summary counts are available; no item details")
        return TodoBoardData(summary=summary, items=lines)

    def _task_board_data(self) -> TaskBoardData:
        tasks = sorted(
            self.services.task_store.list_tasks(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        summary = self._task_summary_line()
        lines = []
        for item in tasks[:6]:
            owner = f" | owner={item.lease_owner}" if item.lease_owner else ""
            lines.append(f"[{item.status.value}] {item.task_id} | {item.title}{owner}")
        return TaskBoardData(summary=summary, items=lines)

    def _print_boards(self, *, current_task_id: str) -> None:
        print(self.renderer.render_todo_board(self._todo_board_data()))
        task_data = self._task_board_data()
        highlighted_items = []
        for line in task_data.items:
            if current_task_id in line:
                highlighted_items.append(f"* {line}")
            else:
                highlighted_items.append(line)
        print(self.renderer.render_task_board(TaskBoardData(summary=task_data.summary, items=highlighted_items)))
        print(self.renderer.render_queue_board(self._queue_board_data()))
        print(self.renderer.render_lock_board(self._lock_board_data()))

    def _print_grouped_timeline(self) -> None:
        order = ["Receive", "Retrieve", "Think", "File", "Shell", "Web", "Subagent", "TODO", "Mechanism", "Done", "Error"]
        groups = [
            TimelineGroupData(title=name, items=list(self._grouped_events.get(name, [])))
            for name in order
            if self._grouped_events.get(name)
        ]
        print(self.renderer.render_grouped_timeline(groups))

    def _print_tool_cards(self) -> None:
        print(self.renderer.render_tool_cards(self._tool_cards))

    def _print_team_board(self) -> None:
        print(self.renderer.render_team_board(self._team_board_data()))

    def _team_board_data(self) -> TeamBoardData:
        teams = self.services.agent_team_runtime.list_teams()
        subagents = self.services.agent_team_runtime.list_subagents(limit=8)
        status_counts: dict[str, int] = {}
        for item in subagents:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1
        status_order = ["queued", "running", "done", "failed"]
        status_parts = [f"{name}={status_counts.get(name, 0)}" for name in status_order if status_counts.get(name, 0)]
        summary = (
            f"team={len(teams)} | subagent={len(subagents)}"
            + (f" | {' | '.join(status_parts)}" if status_parts else "")
        )
        team_lines = [
            f"{item.name} ({item.team_id[:8]}) | strategy={item.strategy} | max={item.max_subagents}"
            for item in teams[:4]
        ]
        cards: list[SubagentCardData] = []
        for item in subagents[:6]:
            cards.append(
                SubagentCardData(
                    subagent_id=item.subagent_id,
                    team_id=item.team_id,
                    status=item.status,
                    prompt=item.prompt,
                    session_id=item.subagent_session_id or "",
                    result_preview=item.result_preview,
                    error=item.error,
                    used_web_search=self._subagent_used_web_search(item.subagent_session_id),
                )
            )
        return TeamBoardData(summary=summary, team_lines=team_lines, subagent_cards=cards)

    def _subagent_used_web_search(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        for event in self.services.session_store.replay(session_id):
            if event.get("event_type") != "message":
                continue
            payload = dict(event.get("payload") or {})
            if payload.get("role") == "tool" and payload.get("name") == "web_search":
                return True
        return False

    def _queue_board_data(self) -> QueueBoardData:
        payload = self.services.delivery_queue.status()
        now = datetime.now(timezone.utc)
        due_count = 0
        for item in payload.get("pending", []):
            next_attempt = str(item.get("next_attempt_at", "") or "")
            try:
                target = datetime.fromisoformat(next_attempt).astimezone(timezone.utc)
            except Exception:
                continue
            if target <= now:
                due_count += 1
        summary = (
            f"pending={payload['pending_count']} | failed={payload['failed_count']} | "
            f"done={payload['done_count']} | due={due_count} | wal={payload['wal_count']}"
        )
        items: list[str] = []
        for item in payload.get("pending", [])[:4]:
            remaining = self._seconds_until(str(item.get("next_attempt_at", "") or ""))
            if remaining <= 0:
                items.append(
                    f"[pending|due] {item.get('kind', '')} | id={str(item.get('delivery_id', ''))[:8]} | run now"
                )
            else:
                items.append(
                    f"[pending] {item.get('kind', '')} | id={str(item.get('delivery_id', ''))[:8]} | runs in {remaining}s"
                )
        for item in payload.get("failed", [])[:2]:
            items.append(
                f"[FAILED] {item.get('kind', '')} | id={str(item.get('delivery_id', ''))[:8]} | attempts={item.get('attempts')}/{item.get('max_attempts')}"
            )
        return QueueBoardData(summary=summary, items=items)

    def _lock_board_data(self) -> LockBoardData:
        leases = self.services.task_store.list_active_leases()
        stale_count = 0
        items: list[str] = []
        for lease in leases[:5]:
            remaining = self._seconds_until(lease.expires_at)
            if remaining <= 60:
                stale_count += 1
            status = "STALE" if remaining <= 60 else "active"
            items.append(
                f"[{status}] {lease.task_id} | owner={lease.owner} | remaining={remaining}s | expires={lease.expires_at}"
            )
        summary = f"active={len(leases)} | stale={stale_count}"
        return LockBoardData(summary=summary, items=items)

    def _print_session_replay(self, args: list[str]) -> None:
        session_ids: list[str]
        if not args:
            session_ids = [self.session_id]
        elif args[0] == "--last":
            if len(args) < 2 or not args[1].isdigit():
                print("Usage: /replay [N] or /replay --last N or /replay <session-id>")
                return
            session_ids = self.services.session_store.latest_session_ids(limit=max(int(args[1]), 1))
        elif args[0].isdigit():
            session_ids = self.services.session_store.latest_session_ids(limit=max(int(args[0]), 1))
        else:
            session_ids = [args[0]]

        if not session_ids:
            print("No sessions available to replay.")
            return

        for session_id in session_ids:
            print(f"session: {session_id}")
            for event in self.services.session_store.replay(session_id):
                print(_format_event(event))

    @staticmethod
    def _clear_screen() -> None:
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
            return
        print("\n" * 40, end="")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codelite", description="CodeLite CLI agent")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="show version")

    for name in ("status", "health"):
        status = sub.add_parser(name, help=f"show {name} info")
        status.add_argument("--json", action="store_true", help="print JSON")

    run = sub.add_parser("run", help="run a single agent turn")
    run.add_argument("prompt", nargs="+", help="task prompt")
    run.add_argument("--session-id", help="resume an existing session")
    run.add_argument("--json", action="store_true", help="print JSON")

    shell = sub.add_parser("shell", help="start interactive shell")
    shell.add_argument("--session-id", help="resume an existing session")
    shell.add_argument("--label", help="override the interactive shell label")

    session = sub.add_parser("session", help="session operations")
    session_sub = session.add_subparsers(dest="session_command")
    replay = session_sub.add_parser("replay", help="replay session events")
    replay_group = replay.add_mutually_exclusive_group()
    replay_group.add_argument("--session-id", help="replay a specific session")
    replay_group.add_argument("--last", type=int, default=1, help="replay latest N sessions")
    replay.add_argument("--json", action="store_true", help="print JSON")

    worktree = sub.add_parser("worktree", help="managed worktree operations")
    worktree_sub = worktree.add_subparsers(dest="worktree_command")

    worktree_prepare = worktree_sub.add_parser("prepare", help="create or reattach a managed worktree")
    worktree_prepare.add_argument("--task-id", required=True, help="logical task id")
    worktree_prepare.add_argument("--title", help="optional human-readable title")
    worktree_prepare.add_argument("--base-ref", default="HEAD", help="base git ref when creating the branch")
    worktree_prepare.add_argument("--json", action="store_true", help="print JSON")

    worktree_list = worktree_sub.add_parser("list", help="list managed worktrees")
    worktree_list.add_argument("--json", action="store_true", help="print JSON")

    worktree_remove = worktree_sub.add_parser("remove", help="remove a managed worktree")
    worktree_remove.add_argument("--task-id", required=True, help="logical task id")
    worktree_remove.add_argument("--force", action="store_true", help="force removal even if the worktree is dirty")
    worktree_remove.add_argument("--json", action="store_true", help="print JSON")

    task = sub.add_parser("task", help="task operations")
    task_sub = task.add_subparsers(dest="task_command")

    task_run = task_sub.add_parser("run", help="run one task in its managed worktree")
    task_run.add_argument("--task-id", required=True, help="logical task id")
    task_run.add_argument("--title", help="optional task title")
    task_run.add_argument("--session-id", help="resume an existing session id")
    task_run.add_argument("prompt", nargs="*", help="task prompt; falls back to title or previous prompt")
    task_run.add_argument("--json", action="store_true", help="print JSON")

    task_list = task_sub.add_parser("list", help="list known tasks")
    task_list.add_argument("--json", action="store_true", help="print JSON")

    task_show = task_sub.add_parser("show", help="show one task")
    task_show.add_argument("--task-id", required=True, help="logical task id")
    task_show.add_argument("--json", action="store_true", help="print JSON")

    lanes = sub.add_parser("lanes", help="lane scheduler operations")
    lanes_sub = lanes.add_subparsers(dest="lanes_command")
    lanes_status = lanes_sub.add_parser("status", help="show lane scheduler status")
    lanes_status.add_argument("--json", action="store_true", help="print JSON")
    lanes_bump = lanes_sub.add_parser("bump", help="bump one lane generation")
    lanes_bump.add_argument("--lane", required=True, help="lane name")
    lanes_bump.add_argument("--json", action="store_true", help="print JSON")

    delivery = sub.add_parser("delivery", help="delivery queue operations")
    delivery_sub = delivery.add_subparsers(dest="delivery_command")
    delivery_status = delivery_sub.add_parser("status", help="show delivery queue status")
    delivery_status.add_argument("--json", action="store_true", help="print JSON")
    delivery_enqueue = delivery_sub.add_parser("enqueue", help="enqueue one delivery item")
    delivery_enqueue.add_argument("--kind", required=True, help="delivery kind")
    delivery_enqueue.add_argument("--payload-json", required=True, help="JSON payload")
    delivery_enqueue.add_argument("--max-attempts", type=int)
    delivery_enqueue.add_argument("--json", action="store_true", help="print JSON")
    delivery_process = delivery_sub.add_parser("process", help="process due delivery items")
    delivery_process.add_argument("--max-items", type=int, default=20)
    delivery_process.add_argument("--workers", type=int, help="worker count for parallel processing")
    delivery_process.add_argument("--kind", action="append", help="limit to specific delivery kind (repeatable)")
    delivery_process.add_argument("--json", action="store_true", help="print JSON")
    delivery_recover = delivery_sub.add_parser("recover", help="recover missing pending items from WAL")
    delivery_recover.add_argument("--json", action="store_true", help="print JSON")

    resilience = sub.add_parser("resilience", help="resilience runner drills")
    resilience_sub = resilience.add_subparsers(dest="resilience_command")
    resilience_drill = resilience_sub.add_parser("drill", help="run a scripted resilience drill")
    resilience_drill.add_argument("--scenario", required=True, choices=["overflow_then_fallback", "auth_then_retry", "ok"])
    resilience_drill.add_argument("--json", action="store_true", help="print JSON")

    cron = sub.add_parser("cron", help="cron scheduler operations")
    cron_sub = cron.add_subparsers(dest="cron_command")
    cron_list = cron_sub.add_parser("list", help="list registered cron jobs")
    cron_list.add_argument("--json", action="store_true", help="print JSON")
    cron_run = cron_sub.add_parser("run", help="run one cron job immediately")
    cron_run.add_argument("--job", required=True, help="job name")
    cron_run.add_argument("--json", action="store_true", help="print JSON")
    cron_tick = cron_sub.add_parser("tick", help="run all due cron jobs once")
    cron_tick.add_argument("--json", action="store_true", help="print JSON")

    heart = sub.add_parser("heart", help="heartbeat operations")
    heart_sub = heart.add_subparsers(dest="heart_command")
    heart_status = heart_sub.add_parser("status", help="show heartbeat status")
    heart_status.add_argument("--json", action="store_true", help="print JSON")
    heart_beat = heart_sub.add_parser("beat", help="emit one component heartbeat")
    heart_beat.add_argument("--component", required=True, help="component id")
    heart_beat.add_argument("--status", default="green", help="status hint")
    heart_beat.add_argument("--queue-depth", type=int, default=0)
    heart_beat.add_argument("--active-task-count", type=int, default=0)
    heart_beat.add_argument("--latency-ms-p95", type=float, default=0.0)
    heart_beat.add_argument("--last-error", default="")
    heart_beat.add_argument("--failure-streak", type=int, default=0)
    heart_beat.add_argument("--json", action="store_true", help="print JSON")

    watchdog = sub.add_parser("watchdog", help="watchdog operations")
    watchdog_sub = watchdog.add_subparsers(dest="watchdog_command")
    watchdog_scan = watchdog_sub.add_parser("scan", help="scan all components for red health")
    watchdog_scan.add_argument("--json", action="store_true", help="print JSON")
    watchdog_simulate = watchdog_sub.add_parser("simulate", help="simulate one component failure")
    watchdog_simulate.add_argument("--component", required=True, help="component id")
    watchdog_simulate.add_argument("--json", action="store_true", help="print JSON")

    hooks = sub.add_parser("hooks", help="hook runtime operations")
    hooks_sub = hooks.add_subparsers(dest="hooks_command")
    hooks_doctor = hooks_sub.add_parser("doctor", help="check AGENTS and hook modules")
    hooks_doctor.add_argument("--json", action="store_true", help="print JSON")

    permissions = sub.add_parser("permissions", help="session permission approvals")
    permissions_sub = permissions.add_subparsers(dest="permissions_command")
    permissions_status = permissions_sub.add_parser("status", help="show saved permission decisions")
    permissions_status.add_argument("--session-id", help="optional session id filter")
    permissions_status.add_argument("--limit", type=int, default=100, help="max decisions to return")
    permissions_status.add_argument("--json", action="store_true", help="print JSON")
    permissions_allow = permissions_sub.add_parser("allow", help="allow one tool invocation fingerprint")
    permissions_allow.add_argument("--session-id", required=True, help="session id")
    permissions_allow.add_argument("--tool", required=True, help="tool name")
    permissions_allow.add_argument("--arguments-json", required=True, help="tool arguments JSON")
    permissions_allow.add_argument("--ttl-sec", type=int, help="decision TTL in seconds")
    permissions_allow.add_argument("--reason", help="optional reason")
    permissions_allow.add_argument("--json", action="store_true", help="print JSON")
    permissions_deny = permissions_sub.add_parser("deny", help="deny one tool invocation fingerprint")
    permissions_deny.add_argument("--session-id", required=True, help="session id")
    permissions_deny.add_argument("--tool", required=True, help="tool name")
    permissions_deny.add_argument("--arguments-json", required=True, help="tool arguments JSON")
    permissions_deny.add_argument("--ttl-sec", type=int, help="decision TTL in seconds")
    permissions_deny.add_argument("--reason", help="optional reason")
    permissions_deny.add_argument("--json", action="store_true", help="print JSON")

    skills = sub.add_parser("skills", help="skill runtime operations")
    skills_sub = skills.add_subparsers(dest="skills_command")
    skills_list = skills_sub.add_parser("list", help="list available skills")
    skills_list.add_argument("--query", default="", help="optional name/summary filter")
    skills_list.add_argument("--limit", type=int, default=50, help="maximum skills to return")
    skills_list.add_argument("--json", action="store_true", help="print JSON")
    skills_load = skills_sub.add_parser("load", help="load one skill by name or directory path")
    skills_load.add_argument("--name", required=True, help="skill name or skill directory path")
    skills_load.add_argument("--json", action="store_true", help="print JSON")

    team = sub.add_parser("team", help="agent team operations")
    team_sub = team.add_subparsers(dest="team_command")
    team_create = team_sub.add_parser("create", help="create one agent team")
    team_create.add_argument("--name", required=True, help="team name")
    team_create.add_argument("--strategy", default="parallel", help="team strategy")
    team_create.add_argument("--max-subagents", type=int, default=3, help="max subagents for this team")
    team_create.add_argument("--metadata-json", help="optional metadata JSON")
    team_create.add_argument("--json", action="store_true", help="print JSON")
    team_list = team_sub.add_parser("list", help="list agent teams")
    team_list.add_argument("--json", action="store_true", help="print JSON")

    subagent = sub.add_parser("subagent", help="subagent operations")
    subagent_sub = subagent.add_subparsers(dest="subagent_command")
    subagent_spawn = subagent_sub.add_parser("spawn", help="spawn one subagent")
    subagent_spawn.add_argument("--team-id", required=True, help="team id")
    subagent_spawn.add_argument("--prompt", required=True, help="subagent prompt")
    subagent_spawn.add_argument(
        "--agent-type",
        choices=list(ALL_AGENT_TYPES),
        default=GENERAL_PURPOSE_AGENT_TYPE,
        help="predefined subagent profile",
    )
    subagent_spawn.add_argument("--session-id", help="optional parent session id")
    subagent_spawn.add_argument("--mode", choices=["queue", "sync"], default="queue", help="run mode")
    subagent_spawn.add_argument("--max-attempts", type=int, help="queue max attempts when mode=queue")
    subagent_spawn.add_argument("--metadata-json", help="optional metadata JSON")
    subagent_spawn.add_argument("--json", action="store_true", help="print JSON")
    subagent_process = subagent_sub.add_parser("process", help="process queued subagents")
    subagent_process.add_argument("--max-items", type=int, default=20)
    subagent_process.add_argument("--workers", type=int, help="worker count for parallel processing")
    subagent_process.add_argument("--json", action="store_true", help="print JSON")
    subagent_list = subagent_sub.add_parser("list", help="list subagents")
    subagent_list.add_argument("--team-id", help="filter by team id")
    subagent_list.add_argument("--status", help="filter by status")
    subagent_list.add_argument("--limit", type=int, default=50)
    subagent_list.add_argument("--json", action="store_true", help="print JSON")
    subagent_show = subagent_sub.add_parser("show", help="show one subagent")
    subagent_show.add_argument("--subagent-id", required=True, help="subagent id")
    subagent_show.add_argument("--json", action="store_true", help="print JSON")

    mcp = sub.add_parser("mcp", help="MCP registry and invocation operations")
    mcp_sub = mcp.add_subparsers(dest="mcp_command")
    mcp_add = mcp_sub.add_parser("add", help="register or update one MCP server")
    mcp_add.add_argument("--name", required=True, help="server name")
    mcp_add.add_argument("--command", dest="server_command", required=True, help="server command")
    mcp_add.add_argument("--args-json", help='optional JSON array of command args, e.g. ["-m","server"]')
    mcp_add.add_argument("--env-json", help='optional JSON object of env vars, e.g. {"TOKEN":"x"}')
    mcp_add.add_argument("--cwd", help="optional cwd (must stay inside workspace)")
    mcp_add.add_argument("--description", help="optional description")
    mcp_add.add_argument("--disabled", action="store_true", help="register as disabled")
    mcp_add.add_argument("--json", action="store_true", help="print JSON")
    mcp_list = mcp_sub.add_parser("list", help="list MCP servers")
    mcp_list.add_argument("--json", action="store_true", help="print JSON")
    mcp_remove = mcp_sub.add_parser("remove", help="remove one MCP server")
    mcp_remove.add_argument("--name", required=True, help="server name")
    mcp_remove.add_argument("--json", action="store_true", help="print JSON")
    mcp_call = mcp_sub.add_parser("call", help="call one MCP server with JSON request")
    mcp_call.add_argument("--name", required=True, help="server name")
    mcp_call.add_argument("--request-json", required=True, help="request JSON payload")
    mcp_call.add_argument("--timeout-sec", type=int, default=60, help="timeout seconds")
    mcp_call.add_argument("--json", action="store_true", help="print JSON")

    background = sub.add_parser("background", help="background task operations")
    background_sub = background.add_subparsers(dest="background_command")
    background_run = background_sub.add_parser("run", help="enqueue one background task")
    background_run.add_argument("--name", required=True, help="task name")
    background_run.add_argument("--payload-json", required=True, help="JSON payload")
    background_run.add_argument("--session-id", help="optional session id for provenance")
    background_run.add_argument("--json", action="store_true", help="print JSON")
    background_process = background_sub.add_parser("process", help="process queued background tasks")
    background_process.add_argument("--max-items", type=int, default=20)
    background_process.add_argument("--workers", type=int, help="worker count for parallel processing")
    background_process.add_argument("--json", action="store_true", help="print JSON")
    background_status = background_sub.add_parser("status", help="show background queue status")
    background_status.add_argument("--json", action="store_true", help="print JSON")

    todo = sub.add_parser("todo", help="todo snapshot operations")
    todo_sub = todo.add_subparsers(dest="todo_command")
    todo_show = todo_sub.add_parser("show", help="show one todo snapshot")
    todo_group = todo_show.add_mutually_exclusive_group()
    todo_group.add_argument("--session-id", help="show a specific session todo list")
    todo_group.add_argument("--last", type=int, default=1, help="show latest N lookup, default latest")
    todo_show.add_argument("--json", action="store_true", help="print JSON")

    context = sub.add_parser("context", help="context snapshot operations")
    context_sub = context.add_subparsers(dest="context_command")
    context_show = context_sub.add_parser("show", help="show one compacted context snapshot")
    context_group = context_show.add_mutually_exclusive_group()
    context_group.add_argument("--session-id", help="show a specific session context snapshot")
    context_group.add_argument("--last", type=int, default=1, help="show latest N lookup, default latest")
    context_show.add_argument("--json", action="store_true", help="print JSON")

    retrieval = sub.add_parser("retrieval", help="retrieval router operations")
    retrieval_sub = retrieval.add_subparsers(dest="retrieval_command")
    retrieval_decide = retrieval_sub.add_parser("decide", help="decide whether retrieval is needed")
    retrieval_decide.add_argument("--prompt", required=True, help="prompt text")
    retrieval_decide.add_argument("--json", action="store_true", help="print JSON")
    retrieval_run = retrieval_sub.add_parser("run", help="run local retrieval and enoughness check")
    retrieval_run.add_argument("--prompt", required=True, help="prompt text")
    retrieval_run.add_argument("--json", action="store_true", help="print JSON")

    memory = sub.add_parser("memory", help="memory ledger and views")
    memory_sub = memory.add_subparsers(dest="memory_command")
    memory_timeline = memory_sub.add_parser("timeline", help="show memory timeline view")
    memory_timeline.add_argument("--json", action="store_true", help="print JSON")
    memory_keyword = memory_sub.add_parser("keyword", help="lookup memory entries by keyword")
    memory_keyword.add_argument("--keyword", required=True, help="keyword")
    memory_keyword.add_argument("--json", action="store_true", help="print JSON")
    memory_trace = memory_sub.add_parser("trace", help="show one ledger entry")
    memory_trace.add_argument("--entry-id", required=True, help="memory entry id")
    memory_trace.add_argument("--json", action="store_true", help="print JSON")
    memory_files = memory_sub.add_parser("files", help="show and bootstrap memory files")
    memory_files.add_argument("--json", action="store_true", help="print JSON")
    memory_prefs = memory_sub.add_parser("prefs", help="show effective file-based preferences")
    memory_prefs.add_argument("--json", action="store_true", help="print JSON")
    memory_remember = memory_sub.add_parser("remember", help="remember one preference into file source-of-truth")
    memory_remember.add_argument("--domain", required=True, help="agent|user|soul|tool|memory")
    memory_remember.add_argument("--text", required=True, help="preference text")
    memory_remember.add_argument("--json", action="store_true", help="print JSON")
    memory_forget = memory_sub.add_parser("forget", help="forget preferences by keyword in one domain")
    memory_forget.add_argument("--domain", required=True, help="agent|user|soul|tool|memory")
    memory_forget.add_argument("--keyword", required=True, help="keyword text")
    memory_forget.add_argument("--json", action="store_true", help="print JSON")
    memory_audit = memory_sub.add_parser("audit", help="show memory candidate/decision/file update audit")
    memory_audit.add_argument("--limit", type=int, default=20, help="latest N entries")
    memory_audit.add_argument("--json", action="store_true", help="print JSON")

    model = sub.add_parser("model", help="model routing operations")
    model_sub = model.add_subparsers(dest="model_command")
    model_route = model_sub.add_parser("route", help="select the routing profile for a prompt")
    model_route.add_argument("--prompt", required=True, help="prompt text")
    model_route.add_argument("--json", action="store_true", help="print JSON")

    critic = sub.add_parser("critic", help="critic/refiner operations")
    critic_sub = critic.add_subparsers(dest="critic_command")
    critic_review = critic_sub.add_parser("review", help="run a heuristic review on one answer")
    critic_review.add_argument("--prompt", required=True, help="prompt text")
    critic_review.add_argument("--answer", required=True, help="answer text")
    critic_review.add_argument("--json", action="store_true", help="print JSON")
    critic_log = critic_sub.add_parser("log", help="log one failure sample")
    critic_log.add_argument("--kind", required=True, help="failure kind")
    critic_log.add_argument("--message", required=True, help="failure message")
    critic_log.add_argument("--metadata-json", help="optional metadata JSON")
    critic_log.add_argument("--json", action="store_true", help="print JSON")
    critic_refine = critic_sub.add_parser("refine", help="derive rules from logged failures")
    critic_refine.add_argument("--json", action="store_true", help="print JSON")

    return parser


def main(
    argv: list[str] | None = None,
    *,
    model_client: ModelClient | None = None,
) -> int:
    _configure_stdio()
    argv = _normalize_argv(argv)
    parser = _build_parser()
    args = parser.parse_args(argv)
    setattr(args, "_model_client", model_client)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command in {"status", "health"}:
        return cmd_health(args)

    if args.command == "run":
        return cmd_run(args)

    if args.command == "session" and args.session_command == "replay":
        return cmd_session_replay(args)

    if args.command == "worktree" and args.worktree_command == "prepare":
        return cmd_worktree_prepare(args)

    if args.command == "worktree" and args.worktree_command == "list":
        return cmd_worktree_list(args)

    if args.command == "worktree" and args.worktree_command == "remove":
        return cmd_worktree_remove(args)

    if args.command == "task" and args.task_command == "run":
        return cmd_task_run(args)

    if args.command == "task" and args.task_command == "list":
        return cmd_task_list(args)

    if args.command == "task" and args.task_command == "show":
        return cmd_task_show(args)

    if args.command == "lanes" and args.lanes_command == "status":
        return cmd_lanes_status(args)

    if args.command == "lanes" and args.lanes_command == "bump":
        return cmd_lanes_bump(args)

    if args.command == "delivery" and args.delivery_command == "status":
        return cmd_delivery_status(args)

    if args.command == "delivery" and args.delivery_command == "enqueue":
        return cmd_delivery_enqueue(args)

    if args.command == "delivery" and args.delivery_command == "process":
        return cmd_delivery_process(args)

    if args.command == "delivery" and args.delivery_command == "recover":
        return cmd_delivery_recover(args)

    if args.command == "resilience" and args.resilience_command == "drill":
        return cmd_resilience_drill(args)

    if args.command == "cron" and args.cron_command == "list":
        return cmd_cron_list(args)

    if args.command == "cron" and args.cron_command == "run":
        return cmd_cron_run(args)

    if args.command == "cron" and args.cron_command == "tick":
        return cmd_cron_tick(args)

    if args.command == "heart" and args.heart_command == "status":
        return cmd_heart_status(args)

    if args.command == "heart" and args.heart_command == "beat":
        return cmd_heart_beat(args)

    if args.command == "watchdog" and args.watchdog_command == "scan":
        return cmd_watchdog_scan(args)

    if args.command == "watchdog" and args.watchdog_command == "simulate":
        return cmd_watchdog_simulate(args)

    if args.command == "hooks" and args.hooks_command == "doctor":
        return cmd_hooks_doctor(args)

    if args.command == "permissions" and args.permissions_command == "status":
        return cmd_permissions_status(args)

    if args.command == "permissions" and args.permissions_command == "allow":
        return cmd_permissions_allow(args)

    if args.command == "permissions" and args.permissions_command == "deny":
        return cmd_permissions_deny(args)

    if args.command == "skills" and args.skills_command == "list":
        return cmd_skills_list(args)

    if args.command == "skills" and args.skills_command == "load":
        return cmd_skills_load(args)

    if args.command == "team" and args.team_command == "create":
        return cmd_team_create(args)

    if args.command == "team" and args.team_command == "list":
        return cmd_team_list(args)

    if args.command == "subagent" and args.subagent_command == "spawn":
        return cmd_subagent_spawn(args)

    if args.command == "subagent" and args.subagent_command == "process":
        return cmd_subagent_process(args)

    if args.command == "subagent" and args.subagent_command == "list":
        return cmd_subagent_list(args)

    if args.command == "subagent" and args.subagent_command == "show":
        return cmd_subagent_show(args)

    if args.command == "mcp" and args.mcp_command == "add":
        return cmd_mcp_add(args)

    if args.command == "mcp" and args.mcp_command == "list":
        return cmd_mcp_list(args)

    if args.command == "mcp" and args.mcp_command == "remove":
        return cmd_mcp_remove(args)

    if args.command == "mcp" and args.mcp_command == "call":
        return cmd_mcp_call(args)

    if args.command == "background" and args.background_command == "run":
        return cmd_background_run(args)

    if args.command == "background" and args.background_command == "process":
        return cmd_background_process(args)

    if args.command == "background" and args.background_command == "status":
        return cmd_background_status(args)

    if args.command == "todo" and args.todo_command == "show":
        return cmd_todo_show(args)

    if args.command == "context" and args.context_command == "show":
        return cmd_context_show(args)

    if args.command == "retrieval" and args.retrieval_command == "decide":
        return cmd_retrieval_decide(args)

    if args.command == "retrieval" and args.retrieval_command == "run":
        return cmd_retrieval_run(args)

    if args.command == "memory" and args.memory_command == "timeline":
        return cmd_memory_timeline(args)

    if args.command == "memory" and args.memory_command == "keyword":
        return cmd_memory_keyword(args)

    if args.command == "memory" and args.memory_command == "trace":
        return cmd_memory_trace(args)

    if args.command == "memory" and args.memory_command == "files":
        return cmd_memory_files(args)

    if args.command == "memory" and args.memory_command == "prefs":
        return cmd_memory_prefs(args)

    if args.command == "memory" and args.memory_command == "remember":
        return cmd_memory_remember(args)

    if args.command == "memory" and args.memory_command == "forget":
        return cmd_memory_forget(args)

    if args.command == "memory" and args.memory_command == "audit":
        return cmd_memory_audit(args)

    if args.command == "model" and args.model_command == "route":
        return cmd_model_route(args)

    if args.command == "critic" and args.critic_command == "review":
        return cmd_critic_review(args)

    if args.command == "critic" and args.critic_command == "log":
        return cmd_critic_log(args)

    if args.command == "critic" and args.critic_command == "refine":
        return cmd_critic_refine(args)

    services = build_runtime(model_client=model_client)
    shell_session_id = getattr(args, "session_id", None) if args.command == "shell" else None
    shell_label = getattr(args, "label", None) if args.command == "shell" else None
    return CodeLiteShell(services, session_id=shell_session_id, label=shell_label).run()


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens:
        return tokens
    first = tokens[0]
    if first in {"-h", "--help"} or first.startswith("-"):
        return tokens
    if first in _TOP_LEVEL_COMMANDS:
        return tokens
    return ["run", *tokens]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())









