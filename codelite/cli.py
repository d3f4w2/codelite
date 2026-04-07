from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite import __version__
from codelite.config import AppConfig, load_app_config
from codelite.core.llm import ModelClient
from codelite.core.loop import AgentLoop
from codelite.core.task_runner import TaskRunner
from codelite.core.tools import ToolRouter
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
    session_store: SessionStore
    task_store: TaskStore
    tool_router: ToolRouter
    worktree_manager: WorktreeManager | None
    agent_loop: AgentLoop


def build_runtime(
    workspace_root: Path | None = None,
    model_client: ModelClient | None = None,
) -> RuntimeServices:
    root = Path(os.environ.get("CODELITE_WORKSPACE_ROOT") or workspace_root or Path.cwd()).resolve()
    config = load_app_config(root)
    layout = RuntimeLayout(root)
    event_store = EventStore(layout)
    session_store = SessionStore(event_store)
    task_store = TaskStore(layout)
    tool_router = ToolRouter(root, config.runtime)
    try:
        worktree_manager = WorktreeManager(root)
    except WorktreeError:
        worktree_manager = None
    agent_loop = AgentLoop(
        config=config,
        session_store=session_store,
        tool_router=tool_router,
        model_client=model_client,
    )
    return RuntimeServices(
        config=config,
        layout=layout,
        event_store=event_store,
        session_store=session_store,
        task_store=task_store,
        tool_router=tool_router,
        worktree_manager=worktree_manager,
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
    return {
        **asdict(_runtime_info()),
        "workspace_root": str(services.layout.workspace_root),
        "runtime_dir": str(services.layout.runtime_dir),
        "events_path": str(services.layout.events_path),
        "sessions_dir": str(services.layout.sessions_dir),
        "session_count": len(list(services.layout.sessions_dir.glob("*.jsonl"))),
        "managed_worktree_count": len(services.worktree_manager.list_managed()) if services.worktree_manager else 0,
        "event_count": _count_lines(services.layout.events_path),
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
    services = build_runtime()
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
        print("没有可回放的会话。")
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


class CodeLiteShell:
    def __init__(self, services: RuntimeServices, session_id: str | None = None) -> None:
        self.services = services
        self.session_id = session_id or services.session_store.new_session_id()
        self.services.session_store.ensure_session(self.session_id)
        self._running = True

    def run(self) -> int:
        print(f"CodeLite v{__version__}")
        print(f"会话: {self.session_id}")
        print("直接输入任务发送给 Agent；输入 help 查看本地命令，输入 exit 退出。")
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
            except Exception as exc:  # pragma: no cover - defensive shell guard
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
        print("本地命令:")
        print("  help            显示帮助")
        print("  version         显示版本")
        print("  health          显示运行时健康信息")
        print("  session id      显示当前会话 ID")
        print("  session replay  回放最近会话")
        print("  exit            退出")
        print("其余输入会作为自然语言任务发送给 Agent。")


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

    services = build_runtime(model_client=model_client)
    shell_session_id = getattr(args, "session_id", None) if args.command == "shell" else None
    return CodeLiteShell(services, session_id=shell_session_id).run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
