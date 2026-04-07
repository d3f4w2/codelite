from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from codelite.core.worktree import WorktreeManager


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


def run_cli(workspace_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    project_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")
    env["CODELITE_WORKSPACE_ROOT"] = str(workspace_root)
    env["CODELITE_LLM_API_KEY"] = ""
    env["CODELITE_EMBEDDING_API_KEY"] = ""
    env["CODELITE_RERANK_API_KEY"] = ""
    env["TAVILY_API_KEY"] = ""
    return subprocess.run(
        [sys.executable, "-m", "codelite.cli", *args],
        cwd=workspace_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def git_repo() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    base_dir = project_root / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    repo = base_dir / f"worktree-{uuid.uuid4().hex[:8]}"
    repo.mkdir(parents=True, exist_ok=False)
    try:
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.email", "worktree@example.com")
        git(repo, "config", "user.name", "Worktree Tester")
        git(repo, "config", "core.autocrlf", "false")
        (repo / "app.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", "app.txt")
        git(repo, "commit", "-m", "init")
        yield repo
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_worktree_manager_isolates_task_changes(git_repo: Path) -> None:
    manager = WorktreeManager(git_repo)

    first = manager.prepare("demo-task-a", title="Task A")
    second = manager.prepare("demo-task-b", title="Task B")

    first_path = Path(first.path)
    second_path = Path(second.path)
    assert first_path.exists()
    assert second_path.exists()
    assert first.branch != second.branch

    managed = manager.list_managed()
    assert {item.task_id for item in managed} == {"demo-task-a", "demo-task-b"}
    assert all(item.attached for item in managed)

    (first_path / "app.txt").write_text("task-a\n", encoding="utf-8")
    assert (git_repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert (second_path / "app.txt").read_text(encoding="utf-8") == "base\n"

    (second_path / "app.txt").write_text("task-b\n", encoding="utf-8")
    assert (first_path / "app.txt").read_text(encoding="utf-8") == "task-a\n"
    assert (second_path / "app.txt").read_text(encoding="utf-8") == "task-b\n"

    same = manager.prepare("demo-task-a")
    assert same.path == first.path
    assert same.branch == first.branch

    # Restore tracked files so removal does not require --force.
    (first_path / "app.txt").write_text("base\n", encoding="utf-8")
    (second_path / "app.txt").write_text("base\n", encoding="utf-8")
    assert git(first_path, "status", "--short") == ""
    assert git(second_path, "status", "--short") == ""

    removed_first = manager.remove("demo-task-a")
    removed_second = manager.remove("demo-task-b")

    assert removed_first.path_exists is False
    assert removed_second.path_exists is False
    assert not first_path.exists()
    assert not second_path.exists()
    assert manager.list_managed() == []


def test_worktree_cli_prepare_list_and_remove(git_repo: Path) -> None:
    prepared = run_cli(git_repo, "worktree", "prepare", "--task-id", "cli-demo", "--json")
    prepared_payload = json.loads(prepared.stdout)

    assert prepared_payload["task_id"] == "cli-demo"
    assert prepared_payload["attached"] is True
    assert Path(prepared_payload["path"]).exists()

    listed = run_cli(git_repo, "worktree", "list", "--json")
    listed_payload = json.loads(listed.stdout)
    assert len(listed_payload) == 1
    assert listed_payload[0]["task_id"] == "cli-demo"

    removed = run_cli(git_repo, "worktree", "remove", "--task-id", "cli-demo", "--json")
    removed_payload = json.loads(removed.stdout)
    assert removed_payload["task_id"] == "cli-demo"
    assert removed_payload["attached"] is False
    assert removed_payload["path_exists"] is False
    assert not Path(prepared_payload["path"]).exists()
