from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from codelite.cli import main as cli_main
from codelite.config import load_app_config
from codelite.core.delivery import DeliveryQueue
from codelite.core.llm import ModelResult, ToolCallRequest
from codelite.core.loop import AgentLoop
from codelite.core.tools import ToolError, ToolRouter
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import LeaseConflictError, TaskStore


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DATE = date.today().isoformat()
BUNDLE_DIR = ROOT / "docs" / "acceptance" / f"{BUNDLE_DATE}-final-project-complete-state"
COMMAND_OUTPUT_DIR = BUNDLE_DIR / "artifacts" / "command-output"
RUNTIME_ARTIFACTS_DIR = BUNDLE_DIR / "artifacts" / "runtime"


class ScriptedLoopModelClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            return ModelResult(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call-write",
                        name="write_file",
                        arguments={"path": "notes.txt", "content": "hello"},
                    )
                ],
            )
        if self.calls == 2:
            return ModelResult(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call-read",
                        name="read_file",
                        arguments={"path": "notes.txt"},
                    )
                ],
            )
        return ModelResult(text="done", tool_calls=[])


class ScriptedTaskModelClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            return ModelResult(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="call-edit",
                        name="edit_file",
                        arguments={
                            "path": "app.txt",
                            "old_text": "base\n",
                            "new_text": "worktree-output\n",
                        },
                    )
                ],
            )
        return ModelResult(text="task complete", tool_calls=[])


@contextmanager
def patched_env(workspace_root: Path) -> Iterator[None]:
    previous = os.environ.copy()
    try:
        os.environ["CODELITE_WORKSPACE_ROOT"] = str(workspace_root)
        os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + previous.get("PYTHONPATH", "")
        os.environ["CODELITE_LLM_API_KEY"] = ""
        os.environ["CODELITE_EMBEDDING_API_KEY"] = ""
        os.environ["CODELITE_RERANK_API_KEY"] = ""
        os.environ["TAVILY_API_KEY"] = ""
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def run_subprocess(
    argv: list[str],
    *,
    workspace_root: Path,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["CODELITE_WORKSPACE_ROOT"] = str(workspace_root)
    env["CODELITE_LLM_API_KEY"] = ""
    env["CODELITE_EMBEDDING_API_KEY"] = ""
    env["CODELITE_RERANK_API_KEY"] = ""
    env["TAVILY_API_KEY"] = ""
    return subprocess.run(
        argv,
        cwd=cwd or ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def run_cli(
    args: list[str],
    *,
    workspace_root: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_subprocess([sys.executable, "-m", "codelite.cli", *args], workspace_root=workspace_root, check=check)


def run_cli_with_model(args: list[str], *, workspace_root: Path, model_client: object) -> str:
    with patched_env(workspace_root):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main(args, model_client=model_client)  # type: ignore[arg-type]
    if exit_code != 0:
        raise RuntimeError(f"cli_main exited with code {exit_code} for args={args}")
    return stdout.getvalue()


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed\n{output}")
    return output


def ensure_clean_bundle_dirs() -> None:
    if COMMAND_OUTPUT_DIR.exists():
        shutil.rmtree(COMMAND_OUTPUT_DIR)
    if RUNTIME_ARTIFACTS_DIR.exists():
        shutil.rmtree(RUNTIME_ARTIFACTS_DIR)
    COMMAND_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def combined_output(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()


def copy_if_exists(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def generate_phase0_bundle() -> None:
    workspace = RUNTIME_ARTIFACTS_DIR / "phase0-demo"
    workspace.mkdir(parents=True, exist_ok=True)
    config = load_app_config(workspace)
    event_store = EventStore(RuntimeLayout(workspace))
    session_store = SessionStore(event_store)
    tool_router = ToolRouter(workspace, config.runtime)
    loop = AgentLoop(
        config=config,
        session_store=session_store,
        tool_router=tool_router,
        model_client=ScriptedLoopModelClient(),
    )
    session_id = session_store.new_session_id()
    answer = loop.run_turn(session_id=session_id, user_input="create a note")
    write_json(
        COMMAND_OUTPUT_DIR / "phase0-loop-run.json",
        {
            "session_id": session_id,
            "answer": answer,
            "notes_txt": (workspace / "notes.txt").read_text(encoding="utf-8"),
        },
    )

    replay = run_cli(["session", "replay", "--last", "1"], workspace_root=workspace)
    write_text(COMMAND_OUTPUT_DIR / "phase0-session-replay.txt", combined_output(replay))

    safe_output = tool_router.dispatch("bash", {"command": "echo ok"}).output
    write_text(COMMAND_OUTPUT_DIR / "tool-safe-bash.txt", safe_output)

    try:
        tool_router.dispatch("bash", {"command": "rm -rf ."})
    except ToolError as exc:
        write_text(COMMAND_OUTPUT_DIR / "tool-dangerous-blocked.txt", f"{type(exc).__name__}\n{exc}")

    try:
        tool_router.dispatch("read_file", {"path": "../outside.txt"})
    except ToolError as exc:
        write_text(COMMAND_OUTPUT_DIR / "tool-path-escape-blocked.txt", f"{type(exc).__name__}\n{exc}")

    file_chain = {
        "write": tool_router.dispatch("write_file", {"path": "runtime/manual-file.txt", "content": "hello\nworld"}).output,
        "first_read": tool_router.dispatch("read_file", {"path": "runtime/manual-file.txt"}).output,
        "edit": tool_router.dispatch(
            "edit_file",
            {"path": "runtime/manual-file.txt", "old_text": "world", "new_text": "codelite"},
        ).output,
        "second_read": tool_router.dispatch("read_file", {"path": "runtime/manual-file.txt"}).output,
    }
    write_json(COMMAND_OUTPUT_DIR / "tool-file-chain.json", file_chain)

    store = TaskStore(RuntimeLayout(workspace))
    lease = store.acquire_lease("manual-demo", owner="tester", title="Manual Demo")
    running = store.start_task("manual-demo", lease_id=lease.lease_id)
    done = store.complete_task("manual-demo", lease_id=lease.lease_id)
    write_json(
        COMMAND_OUTPUT_DIR / "task-lease-lifecycle.json",
        {
            "lease": lease.__dict__,
            "running": running.to_dict(),
            "done": done.to_dict(),
        },
    )

    conflict_store = TaskStore(RuntimeLayout(workspace))
    conflict_store.acquire_lease("conflict-demo", owner="alpha")
    try:
        conflict_store.acquire_lease("conflict-demo", owner="beta")
    except LeaseConflictError as exc:
        write_text(COMMAND_OUTPUT_DIR / "task-lease-conflict.txt", f"{type(exc).__name__}\n{exc}")

    expired_lease = store.acquire_lease("expired-demo", owner="alpha", ttl_seconds=30)
    store.start_task("expired-demo", lease_id=expired_lease.lease_id)
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    store._write_json(  # type: ignore[attr-defined]
        store.lease_path("expired-demo"),
        {
            "task_id": "expired-demo",
            "lease_id": expired_lease.lease_id,
            "owner": expired_lease.owner,
            "acquired_at": expired_lease.acquired_at,
            "expires_at": expired_at.isoformat(),
            "ttl_seconds": expired_lease.ttl_seconds,
        },
    )
    reconciled = store.reconcile_expired_leases()
    write_json(
        COMMAND_OUTPUT_DIR / "task-lease-expired.json",
        {
            "reconciled": [task.to_dict() for task in reconciled],
            "final": store.get_task("expired-demo").to_dict(),
        },
    )


def generate_worktree_bundle() -> None:
    repo = RUNTIME_ARTIFACTS_DIR / "worktree-demo"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "demo@example.com")
    git(repo, "config", "user.name", "CodeLite Demo")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "app.txt")
    git(repo, "commit", "-m", "init")

    prepared_a = run_cli(["worktree", "prepare", "--task-id", "demo_a", "--title", "Task A", "--json"], workspace_root=repo)
    prepared_b = run_cli(["worktree", "prepare", "--task-id", "demo_b", "--title", "Task B", "--json"], workspace_root=repo)
    listed = run_cli(["worktree", "list", "--json"], workspace_root=repo)
    write_text(COMMAND_OUTPUT_DIR / "worktree-prepare-a.json", prepared_a.stdout)
    write_text(COMMAND_OUTPUT_DIR / "worktree-prepare-b.json", prepared_b.stdout)
    write_text(COMMAND_OUTPUT_DIR / "worktree-list.json", listed.stdout)

    task_run = run_cli_with_model(
        [
            "task",
            "run",
            "--task-id",
            "demo-task",
            "--title",
            "Demo Task",
            "--json",
            "Update app.txt inside the managed worktree.",
        ],
        workspace_root=repo,
        model_client=ScriptedTaskModelClient(),
    )
    task_show = run_cli(["task", "show", "--task-id", "demo-task", "--json"], workspace_root=repo)
    task_list = run_cli(["task", "list", "--json"], workspace_root=repo)
    write_text(COMMAND_OUTPUT_DIR / "task-run-worktree-binding.json", task_run)
    write_text(COMMAND_OUTPUT_DIR / "task-show-worktree-binding.json", task_show.stdout)
    write_text(COMMAND_OUTPUT_DIR / "task-list-worktree-binding.json", task_list.stdout)


def generate_current_workspace_outputs() -> None:
    version = run_cli(["version"], workspace_root=ROOT)
    write_text(COMMAND_OUTPUT_DIR / "version.txt", combined_output(version))

    health = run_cli(["health", "--json"], workspace_root=ROOT)
    write_text(COMMAND_OUTPUT_DIR / "health.json", health.stdout)

    cron_list = run_cli(["cron", "list", "--json"], workspace_root=ROOT)
    heart_status = run_cli(["heart", "status", "--json"], workspace_root=ROOT)
    lanes_status = run_cli(["lanes", "status", "--json"], workspace_root=ROOT)
    hooks_doctor = run_cli(["hooks", "doctor", "--json"], workspace_root=ROOT)
    delivery_status = run_cli(["delivery", "status", "--json"], workspace_root=ROOT)
    resilience_auth = run_cli(["resilience", "drill", "--scenario", "auth_then_retry", "--json"], workspace_root=ROOT)
    resilience_overflow = run_cli(["resilience", "drill", "--scenario", "overflow_then_fallback", "--json"], workspace_root=ROOT)
    skills_load = run_cli(["skills", "load", "--name", "code-review", "--json"], workspace_root=ROOT)
    background_run = run_cli(
        ["background", "run", "--name", "digest", "--payload-json", '{"text":"hello"}', "--session-id", "final-bundle", "--json"],
        workspace_root=ROOT,
    )
    background_process = run_cli(["background", "process", "--json"], workspace_root=ROOT)
    retrieval_run = run_cli(["retrieval", "run", "--prompt", "Read README and summarize runtime services", "--json"], workspace_root=ROOT)
    memory_timeline = run_cli(["memory", "timeline", "--json"], workspace_root=ROOT)
    memory_keyword = run_cli(["memory", "keyword", "--keyword", "runtime", "--json"], workspace_root=ROOT)
    model_route = run_cli(["model", "route", "--prompt", "Please review this patch for bugs", "--json"], workspace_root=ROOT)
    critic_review = run_cli(["critic", "review", "--prompt", "summarize the work", "--answer", "TODO", "--json"], workspace_root=ROOT)
    critic_log = run_cli(["critic", "log", "--kind", "validation", "--message", "pipeline failed", "--json"], workspace_root=ROOT)
    critic_refine = run_cli(["critic", "refine", "--json"], workspace_root=ROOT)
    validate = run_subprocess(
        [sys.executable, "scripts/validate.py", "--json", "--pytest-target", "tests/core/test_v021_mechanisms.py"],
        workspace_root=ROOT,
    )
    pytest_core = run_subprocess([sys.executable, "-m", "pytest", "tests/core", "-q"], workspace_root=ROOT)

    outputs = {
        "cron-list.json": cron_list.stdout,
        "heart-status.json": heart_status.stdout,
        "lanes-status.json": lanes_status.stdout,
        "hooks-doctor.json": hooks_doctor.stdout,
        "delivery-status.json": delivery_status.stdout,
        "resilience-auth.json": resilience_auth.stdout,
        "resilience-overflow.json": resilience_overflow.stdout,
        "skills-load.json": skills_load.stdout,
        "background-run.json": background_run.stdout,
        "background-process.json": background_process.stdout,
        "retrieval-run.json": retrieval_run.stdout,
        "memory-timeline.json": memory_timeline.stdout,
        "memory-keyword-runtime.json": memory_keyword.stdout,
        "model-route-review.json": model_route.stdout,
        "critic-review.json": critic_review.stdout,
        "critic-log.json": critic_log.stdout,
        "critic-refine.json": critic_refine.stdout,
        "validate.json": validate.stdout,
        "pytest-core.txt": combined_output(pytest_core),
    }
    for filename, content in outputs.items():
        write_text(COMMAND_OUTPUT_DIR / filename, content)

    current_runtime = RUNTIME_ARTIFACTS_DIR / "current-workspace"
    current_runtime.mkdir(parents=True, exist_ok=True)
    runtime_root = ROOT / "runtime"
    for name in (
        "events.jsonl",
        "hearts.jsonl",
        "audit.jsonl",
        "background",
        "critic",
        "delivery-queue",
        "hooks",
        "lanes",
        "memory",
        "metrics",
        "watchdog",
    ):
        copy_if_exists(runtime_root / name, current_runtime / name)


def write_snapshot_note() -> None:
    note = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundle_dir": str(BUNDLE_DIR),
        "command_output_dir": str(COMMAND_OUTPUT_DIR),
        "runtime_artifacts_dir": str(RUNTIME_ARTIFACTS_DIR),
        "notes": [
            "phase0-demo is an isolated workspace for v0.0 tools and lease checks",
            "worktree-demo is an isolated git repository for managed worktree and task binding checks",
            "current-workspace contains selected runtime snapshots copied from the project root after final acceptance commands ran",
        ],
    }
    write_json(RUNTIME_ARTIFACTS_DIR / "bundle-meta.json", note)


def main() -> int:
    ensure_clean_bundle_dirs()
    generate_phase0_bundle()
    generate_worktree_bundle()
    generate_current_workspace_outputs()
    write_snapshot_note()
    print(BUNDLE_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
