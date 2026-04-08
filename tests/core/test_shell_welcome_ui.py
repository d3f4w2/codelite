from __future__ import annotations

import io
import shutil
import uuid
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from codelite.cli import CodeLiteShell, build_runtime
from codelite.tui import ShellRenderer, ShellWelcomeData


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
    workspace = base_dir / f"shell-ui-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_shell_renderer_renders_welcome_panels() -> None:
    renderer = ShellRenderer(width=110)
    data = ShellWelcomeData(
        version="0.2.1",
        session_id="demo-session",
        model_name="gpt-test",
        provider="openai",
        workspace_root="C:\\Users\\demo\\workspace",
        current_dir="C:\\Users\\demo\\workspace",
        health_summary="green=5 | unknown=2",
        recent_activity=["No recent activity"],
        tips=[
            "help               list local commands",
            "health             inspect runtime health",
            "session replay     inspect the latest session",
        ],
    )

    rendered = renderer.render_welcome(data)

    assert "CodeLite Shell v0.2.1" in rendered
    assert "Welcome back!" in rendered
    assert "Tips for Getting Started" in rendered
    assert "Recent Activity" in rendered
    assert "No recent activity" in rendered
    assert "Shortcuts: help | health | session replay | exit" in rendered


def test_shell_prints_welcome_screen_before_prompt(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    monkeypatch.setenv("CODELITE_LLM_API_KEY", "")
    monkeypatch.setenv("CODELITE_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("CODELITE_RERANK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")

    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "exit"

    monkeypatch.setattr("builtins.input", fake_input)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = CodeLiteShell(build_runtime(workspace_dir)).run()

    output = stdout.getvalue()

    assert exit_code == 0
    assert "CodeLite Shell v0.2.1" in output
    assert "Welcome back!" in output
    assert "Session:" in output
    assert "Health:" in output
    assert "Type a task below and press Enter." in output
    assert prompts == ["> "]
