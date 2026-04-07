from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from codelite.cli import main
from codelite.core.llm import ModelResult, ToolCallRequest
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import TaskStatus, TaskStore


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
        raise AssertionError(f"git {' '.join(args)} failed\n{output}")
    return output


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


@pytest.fixture(autouse=True)
def clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CODELITE_CONFIG_PATH",
        "CODELITE_EMBEDDING_API_KEY",
        "CODELITE_LLM_API_KEY",
        "CODELITE_RERANK_API_KEY",
        "CODELITE_WORKSPACE_ROOT",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def git_repo() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    base_dir = project_root / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    repo = base_dir / f"task-run-{uuid.uuid4().hex[:8]}"
    repo.mkdir(parents=True, exist_ok=False)
    try:
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.email", "taskrun@example.com")
        git(repo, "config", "user.name", "Task Runner Tester")
        git(repo, "config", "core.autocrlf", "false")
        (repo / "app.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", "app.txt")
        git(repo, "commit", "-m", "init")
        yield repo
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_task_run_executes_inside_managed_worktree_and_updates_task(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(git_repo))
    monkeypatch.setenv("CODELITE_LLM_API_KEY", "")
    monkeypatch.setenv("CODELITE_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("CODELITE_RERANK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
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
            model_client=ScriptedTaskModelClient(),
        )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())

    assert payload["task"]["task_id"] == "demo-task"
    assert payload["task"]["status"] == "done"
    assert payload["answer"] == "task complete"
    assert payload["prompt"] == "Update app.txt inside the managed worktree."

    worktree_path = Path(payload["worktree"]["path"])
    assert worktree_path.exists()
    assert (git_repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert (worktree_path / "app.txt").read_text(encoding="utf-8") == "worktree-output\n"

    task_store = TaskStore(RuntimeLayout(git_repo))
    task = task_store.get_task("demo-task")
    assert task is not None
    assert task.status is TaskStatus.DONE
    assert task.metadata["session_id"] == payload["session_id"]
    assert task.metadata["worktree"]["path"] == payload["worktree"]["path"]
    assert task.metadata["last_answer_preview"] == "task complete"

    session_store = SessionStore(EventStore(RuntimeLayout(git_repo)))
    events = session_store.replay(payload["session_id"])
    assert any(event["event_type"] == "tool_finished" for event in events)
    assert any(
        event["event_type"] == "message"
        and event["payload"].get("role") == "assistant"
        and event["payload"].get("content") == "task complete"
        for event in events
    )


def test_task_cli_list_and_show_return_known_tasks(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(git_repo))
    task_store = TaskStore(RuntimeLayout(git_repo))
    task_store.create_task("listed-task", title="Listed Task", metadata={"lane": "main"})

    show_stdout = io.StringIO()
    with redirect_stdout(show_stdout):
        show_exit = main(["task", "show", "--task-id", "listed-task", "--json"])
    assert show_exit == 0
    show_payload = json.loads(show_stdout.getvalue())
    assert show_payload["task_id"] == "listed-task"
    assert show_payload["title"] == "Listed Task"

    list_stdout = io.StringIO()
    with redirect_stdout(list_stdout):
        list_exit = main(["task", "list", "--json"])
    assert list_exit == 0
    list_payload = json.loads(list_stdout.getvalue())
    assert any(item["task_id"] == "listed-task" for item in list_payload)
