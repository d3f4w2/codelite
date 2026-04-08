from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from codelite import __version__
from codelite.config import AppConfig, load_app_config
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
from codelite.core.reconcile import Reconciler
from codelite.core.resilience import ResilienceRunner
from codelite.core.retrieval import RetrievalRouter
from codelite.core.scheduler import CronScheduler
from codelite.core.skills_runtime import SkillRuntime
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
from codelite.storage.tasks import TaskStore
from codelite.tui import ShellRenderer, ShellWelcomeData


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        stream.reconfigure(errors="replace")


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
    root = Path(os.environ.get("CODELITE_WORKSPACE_ROOT") or workspace_root or Path.cwd()).resolve()
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
    )
    critic_refiner = CriticRefiner(
        layout,
        memory_runtime=memory_runtime,
    )
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
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        subagent_session_id = session_store.new_session_id()
        answer = agent_loop.run_turn(session_id=subagent_session_id, user_input=prompt)
        return {
            "team_id": team_id,
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


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


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
    payload = json.loads(args.payload_json)
    item = services.delivery_queue.enqueue(args.kind, payload, max_attempts=args.max_attempts)
    if args.json:
        _print_json(item.to_dict())
        return 0
    print(json.dumps(item.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_delivery_process(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.delivery_queue.process_all(_delivery_handlers(services), max_items=args.max_items)
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
        metadata=json.loads(args.metadata_json) if args.metadata_json else {},
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
    metadata = json.loads(args.metadata_json) if args.metadata_json else {}
    if args.mode == "sync":
        payload = services.agent_team_runtime.run_subagent_inline(
            team_id=args.team_id,
            prompt=args.prompt,
            parent_session_id=args.session_id,
            metadata=metadata,
        )
    else:
        payload = services.agent_team_runtime.spawn_subagent(
            team_id=args.team_id,
            prompt=args.prompt,
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
    payload = services.agent_team_runtime.process_subagents(max_items=args.max_items)
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
        args=json.loads(args.args_json) if args.args_json else [],
        env=json.loads(args.env_json) if args.env_json else {},
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
        request=json.loads(args.request_json),
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
        payload=json.loads(args.payload_json),
        session_id=args.session_id,
    )
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_background_process(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.skill_runtime.process_background_tasks(max_items=args.max_items)
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
        metadata=json.loads(args.metadata_json) if args.metadata_json else {},
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
    def __init__(self, services: RuntimeServices, session_id: str | None = None) -> None:
        self.services = services
        self.session_id = session_id or services.session_store.new_session_id()
        self.services.session_store.ensure_session(self.session_id)
        self._running = True
        self.renderer = ShellRenderer()

    def run(self) -> int:
        print(self.renderer.render_welcome(self._welcome_data()))
        while self._running:
            try:
                raw = input(self.renderer.prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not raw:
                continue

            try:
                if self._handle_local_command(raw):
                    continue
                answer = self.services.agent_loop.run_turn(self.session_id, raw)
                print(answer)
            except Exception as exc:  # pragma: no cover
                print(f"[error] {exc}")
        return 0

    def _handle_local_command(self, raw: str) -> bool:
        if raw in {"exit", "quit", "q"}:
            self._running = False
            return True

        if raw in {"help", "?"}:
            self._print_help()
            return True

        if raw == "version":
            print(__version__)
            return True

        if raw in {"status", "health"}:
            snapshot = build_health_snapshot(self.services)
            _print_json(snapshot)
            return True

        if raw == "session id":
            print(self.session_id)
            return True

        if raw.startswith("session replay"):
            parser = argparse.ArgumentParser(prog="session replay", add_help=False)
            parser.add_argument("--last", type=int, default=1)
            tokens = shlex.split(raw)
            parsed = parser.parse_args(tokens[2:])
            namespace = argparse.Namespace(last=parsed.last, session_id=None, json=False)
            cmd_session_replay(namespace)
            return True

        return False

    @staticmethod
    def _command_help_lines() -> list[str]:
        return [
            "help            show help",
            "version         show version",
            "health          show runtime health",
            "session id      show current session id",
            "session replay  replay the latest session",
            "exit            quit",
        ]

    def _print_help(self) -> None:
        print(self.renderer.render_help(self._command_help_lines()))

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
            tips=[
                "help               list local commands",
                "health             inspect runtime health",
                "session replay     inspect the latest session",
            ],
        )

    def _recent_activity_lines(self, *, limit: int) -> list[str]:
        events = self.services.session_store.replay(self.session_id)
        preview = self._session_preview(events)
        if preview is None:
            return ["No recent activity"]
        return [preview[:58]]

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codelite", description="CodeLite CLI")
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
    subagent_spawn.add_argument("--session-id", help="optional parent session id")
    subagent_spawn.add_argument("--mode", choices=["queue", "sync"], default="queue", help="run mode")
    subagent_spawn.add_argument("--max-attempts", type=int, help="queue max attempts when mode=queue")
    subagent_spawn.add_argument("--metadata-json", help="optional metadata JSON")
    subagent_spawn.add_argument("--json", action="store_true", help="print JSON")
    subagent_process = subagent_sub.add_parser("process", help="process queued subagents")
    subagent_process.add_argument("--max-items", type=int, default=20)
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
    return CodeLiteShell(services, session_id=shell_session_id).run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
