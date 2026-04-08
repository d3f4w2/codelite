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
from codelite.core.context import ContextCompact
from codelite.core.events import EventBus
from codelite.core.heartbeat import HeartService
from codelite.core.llm import ModelClient
from codelite.core.loop import AgentLoop
from codelite.core.reconcile import Reconciler
from codelite.core.scheduler import CronScheduler
from codelite.core.task_runner import TaskRunner
from codelite.core.todo import TodoManager
from codelite.core.tools import ToolRouter
from codelite.core.watchdog import Watchdog
from codelite.core.worktree import WorktreeError, WorktreeManager
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import TaskStore


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
    tool_router: ToolRouter
    worktree_manager: WorktreeManager | None
    reconciler: Reconciler
    cron_scheduler: CronScheduler
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
    try:
        worktree_manager = WorktreeManager(root)
    except WorktreeError:
        worktree_manager = None

    tool_router = ToolRouter(
        root,
        config.runtime,
        todo_manager=todo_manager,
        heart_service=heart_service,
    )
    agent_loop = AgentLoop(
        config=config,
        session_store=session_store,
        tool_router=tool_router,
        model_client=model_client,
        todo_manager=todo_manager,
        context_manager=context_manager,
        heart_service=heart_service,
    )
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

    heart_service.beat("event_bus")
    heart_service.beat("todo_manager")
    heart_service.beat("context_compact")
    heart_service.beat("cron_scheduler", queue_depth=len(cron_scheduler.jobs))
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
        tool_router=tool_router,
        worktree_manager=worktree_manager,
        reconciler=reconciler,
        cron_scheduler=cron_scheduler,
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
    )
    result = runner.run(
        task_id=args.task_id,
        prompt=prompt,
        title=args.title or args.task_id,
        session_id=args.session_id,
    )

    if args.json:
        _print_json(result.to_dict())
        return 0

    print(f"task_id: {result.task.task_id}")
    print(f"status: {result.task.status}")
    print(f"session_id: {result.session_id}")
    print(f"worktree: {result.worktree.path}")
    print(f"answer: {result.answer}")
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
    payload = services.cron_scheduler.run_job(args.job)
    if args.json:
        _print_json(payload)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_cron_tick(args: argparse.Namespace) -> int:
    services = build_runtime()
    payload = services.cron_scheduler.run_due()
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


class CodeLiteShell:
    def __init__(self, services: RuntimeServices, session_id: str | None = None) -> None:
        self.services = services
        self.session_id = session_id or services.session_store.new_session_id()
        self.services.session_store.ensure_session(self.session_id)
        self._running = True

    def run(self) -> int:
        print(f"CodeLite v{__version__}")
        print(f"session: {self.session_id}")
        print("Type a task to send it to the agent. Use `help` for local commands, or `exit` to leave.")
        while self._running:
            try:
                raw = input("codelite> ").strip()
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
    def _print_help() -> None:
        print("local commands:")
        print("  help            show help")
        print("  version         show version")
        print("  health          show runtime health")
        print("  session id      show current session id")
        print("  session replay  replay the latest session")
        print("  exit            quit")


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

    if args.command == "todo" and args.todo_command == "show":
        return cmd_todo_show(args)

    if args.command == "context" and args.context_command == "show":
        return cmd_context_show(args)

    services = build_runtime(model_client=model_client)
    shell_session_id = getattr(args, "session_id", None) if args.command == "shell" else None
    return CodeLiteShell(services, session_id=shell_session_id).run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
