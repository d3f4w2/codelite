from __future__ import annotations

import io
import json
import shutil
import subprocess
import uuid
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from codelite.cli import CodeLiteShell, build_runtime
from codelite.config import load_app_config
from codelite.core.auto_orchestrator import AutoOrchestrationPolicy
from codelite.core.events import EventBus
from codelite.core.llm import ModelResult, ToolCallRequest
from codelite.core.loop import AgentLoop
from codelite.core.todo import TodoManager
from codelite.core.tools import ToolRouter
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import TaskStatus, TaskStore


def _git(repo: Path, *args: str) -> str:
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


class PlanGateModelClient:
    def __init__(self) -> None:
        self.calls = 0
        self.gate_counts: list[int] = []

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del tools
        self.calls += 1
        gate_count = sum(
            1
            for message in messages
            if str(message.get("role")) == "system"
            and "Planning gate:" in str(message.get("content", ""))
        )
        self.gate_counts.append(gate_count)
        if self.calls == 1:
            return ModelResult(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id="todo-1",
                        name="todo_write",
                        arguments={
                            "items": [
                                {"id": "step-1", "content": "Inspect current files", "status": "in_progress"},
                                {"id": "step-2", "content": "Apply edits", "status": "pending"},
                                {"id": "step-3", "content": "Run validation", "status": "pending"},
                            ]
                        },
                    )
                ],
            )
        return ModelResult(text="planned", tool_calls=[])


class WorktreeRouteModelClient:
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
                        id="edit-1",
                        name="edit_file",
                        arguments={
                            "path": "app.txt",
                            "old_text": "base\n",
                            "new_text": "worktree-routed\n",
                        },
                    )
                ],
            )
        return ModelResult(text="routed-done", tool_calls=[])


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
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"auto-orch-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture()
def git_repo() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"auto-orch-git-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        _git(workspace, "init", "-b", "main")
        _git(workspace, "config", "user.email", "auto-orch@example.com")
        _git(workspace, "config", "user.name", "Auto Orch Tester")
        _git(workspace, "config", "core.autocrlf", "false")
        (workspace / "app.txt").write_text("base\n", encoding="utf-8")
        _git(workspace, "add", "app.txt")
        _git(workspace, "commit", "-m", "init")
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _prepare_env(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("CODELITE_LLM_API_KEY", "")
    monkeypatch.setenv("CODELITE_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("CODELITE_RERANK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")


def test_auto_orchestrator_policy_marks_complex_prompt_for_plan_and_worktree(
    workspace_dir: Path,
) -> None:
    config = load_app_config(workspace_dir)
    policy = AutoOrchestrationPolicy(config.runtime)
    decision = policy.decide(
        prompt="Refactor core loop across multiple files and tests, then run full validation in a worktree.",
        mode="act",
        worktree_available=True,
    )

    assert decision.require_plan is True
    assert decision.require_worktree is True
    assert "worktree_candidate" in decision.reason


def test_auto_orchestrator_policy_keeps_plan_but_drops_worktree_when_unavailable(
    workspace_dir: Path,
) -> None:
    config = load_app_config(workspace_dir)
    policy = AutoOrchestrationPolicy(config.runtime)
    decision = policy.decide(
        prompt="Refactor core loop across multiple files and tests, then run full validation in a worktree.",
        mode="act",
        worktree_available=False,
    )

    assert decision.require_plan is True
    assert decision.require_worktree is False
    assert "worktree_unavailable" in decision.reason


def test_agent_loop_injects_plan_gate_until_agent_todo_write(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch, workspace_dir)
    config = load_app_config(workspace_dir)
    layout = RuntimeLayout(workspace_dir)
    event_store = EventStore(layout)
    session_store = SessionStore(event_store)
    event_bus = EventBus(event_store)
    todo_manager = TodoManager(layout, event_bus)
    model = PlanGateModelClient()
    loop = AgentLoop(
        config=config,
        session_store=session_store,
        tool_router=ToolRouter(workspace_dir, config.runtime, todo_manager=todo_manager),
        model_client=model,
        todo_manager=todo_manager,
    )

    answer = loop.run_turn(
        session_id="plan-gate-session",
        user_input="Implement a multi-step refactor safely.",
        require_plan=True,
    )

    assert answer == "planned"
    assert model.gate_counts[0] >= 1
    assert model.gate_counts[-1] == 1
    events = session_store.replay("plan-gate-session")
    assert any(event["event_type"] == "auto_plan_gate_injected" for event in events)
    assert any(
        event["event_type"] == "todo_updated"
        and (event.get("payload") or {}).get("source") == "agent"
        for event in events
    )


def test_shell_auto_routes_complex_turn_to_worktree(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch, git_repo)

    services = build_runtime(git_repo, model_client=WorktreeRouteModelClient())
    shell = CodeLiteShell(services)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        shell._run_agent_turn("Refactor app flow across files and keep isolation with worktree.")

    expected_task_id = f"shell-{shell.session_id[-8:]}-turn-01"
    task = TaskStore(RuntimeLayout(git_repo)).get_task(expected_task_id)
    assert task is not None
    assert task.status is TaskStatus.DONE
    worktree_path = Path(task.metadata["worktree"]["path"])
    assert worktree_path.is_relative_to(git_repo / ".wt")
    assert (git_repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert (worktree_path / "app.txt").read_text(encoding="utf-8") == "worktree-routed\n"

    events = SessionStore(EventStore(RuntimeLayout(git_repo))).replay(shell.session_id)
    assert any(event["event_type"] == "auto_orchestrator_decision" for event in events)
    assert any(event["event_type"] == "auto_worktree_routed" for event in events)

    output = stdout.getvalue()
    assert "[ASSISTANT]" in output
    assert "routed-done" in output


def test_shell_worktree_command_routes_prompt_to_managed_worktree(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch, git_repo)

    services = build_runtime(git_repo, model_client=WorktreeRouteModelClient())
    shell = CodeLiteShell(services)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/worktree Update app.txt inside the managed worktree.") is True

    expected_task_id = f"shell-{shell.session_id[-8:]}-turn-01"
    task = TaskStore(RuntimeLayout(git_repo)).get_task(expected_task_id)
    assert task is not None
    assert task.status is TaskStatus.DONE
    assert task.metadata["worktree"]["path"]
    worktree_path = Path(task.metadata["worktree"]["path"])
    assert worktree_path.is_relative_to(git_repo / ".wt")
    assert (git_repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert (worktree_path / "app.txt").read_text(encoding="utf-8") == "worktree-routed\n"

    events = SessionStore(EventStore(RuntimeLayout(git_repo))).replay(shell.session_id)
    assert any(
        event["event_type"] == "auto_orchestrator_decision"
        and (event.get("payload") or {}).get("reason") == "shell_local_worktree"
        for event in events
    )
    assert any(event["event_type"] == "auto_worktree_routed" for event in events)

    output = stdout.getvalue()
    assert "[ASSISTANT]" in output
    assert "routed-done" in output


def test_shell_worktree_command_works_from_nested_git_workspace_root(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested_dir = git_repo / "src" / "feature"
    nested_dir.mkdir(parents=True, exist_ok=False)
    monkeypatch.chdir(nested_dir)
    monkeypatch.setenv("CODELITE_LLM_API_KEY", "")
    monkeypatch.setenv("CODELITE_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("CODELITE_RERANK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")

    services = build_runtime(model_client=WorktreeRouteModelClient())
    assert services.layout.workspace_root == git_repo.resolve()
    assert services.worktree_manager is not None

    shell = CodeLiteShell(services)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/worktree Update app.txt inside the managed worktree.") is True

    expected_task_id = f"shell-{shell.session_id[-8:]}-turn-01"
    task = TaskStore(RuntimeLayout(git_repo)).get_task(expected_task_id)
    assert task is not None
    assert task.status is TaskStatus.DONE
    worktree_path = Path(task.metadata["worktree"]["path"])
    assert worktree_path.is_relative_to(git_repo / ".wt")
    assert (git_repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert (worktree_path / "app.txt").read_text(encoding="utf-8") == "worktree-routed\n"

    output = stdout.getvalue()
    assert "[ASSISTANT]" in output
    assert "routed-done" in output
