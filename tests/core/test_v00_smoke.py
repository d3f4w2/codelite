from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from codelite.config import load_app_config
from codelite.core.llm import ModelResult, OpenAICompatibleClient, ToolCallRequest
from codelite.core.loop import AgentLoop
from codelite.core.tools import ToolError, ToolRouter
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore


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


def run_cli(
    repo: Path,
    cwd: Path,
    *args: str,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    env["CODELITE_WORKSPACE_ROOT"] = str(cwd)
    env["CODELITE_LLM_API_KEY"] = ""
    env["CODELITE_EMBEDDING_API_KEY"] = ""
    env["CODELITE_RERANK_API_KEY"] = ""
    env["TAVILY_API_KEY"] = ""
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "codelite.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


class ScriptedModelClient:
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


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"v00-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def build_agent_loop(workspace_root: Path) -> tuple[SessionStore, AgentLoop]:
    config = load_app_config(workspace_root)
    event_store = EventStore(RuntimeLayout(workspace_root))
    session_store = SessionStore(event_store)
    tool_router = ToolRouter(workspace_root, config.runtime)
    loop = AgentLoop(
        config=config,
        session_store=session_store,
        tool_router=tool_router,
        model_client=ScriptedModelClient(),
    )
    return session_store, loop


def build_llm_client(workspace_root: Path) -> OpenAICompatibleClient:
    config = load_app_config(workspace_root)
    return OpenAICompatibleClient(config.llm)


def test_v00_cli_version(workspace_dir: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    result = run_cli(repo, workspace_dir, "version")
    assert result.stdout.strip() == "0.2.1"


def test_v00_cli_health_json(workspace_dir: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    result = run_cli(repo, workspace_dir, "health", "--json")
    payload = json.loads(result.stdout)

    assert payload["version"] == "0.2.1"
    assert payload["llm"]["model"] == "gpt-5.4-mini"
    assert payload["workspace_root"] == str(workspace_dir)
    assert payload["llm"]["configured"] is False


def test_v00_loads_keys_from_dotenv(workspace_dir: Path) -> None:
    (workspace_dir / ".env").write_text(
        "\n".join(
            [
                "CODELITE_LLM_API_KEY=test-llm-key",
                "CODELITE_EMBEDDING_API_KEY=test-embedding-key",
                "CODELITE_RERANK_API_KEY=test-rerank-key",
                "TAVILY_API_KEY=test-tavily-key",
            ]
        ),
        encoding="utf-8",
    )

    config = load_app_config(workspace_dir)

    assert config.llm.api_key == "test-llm-key"
    assert config.embedding.api_key == "test-embedding-key"
    assert config.rerank.api_key == "test-rerank-key"
    assert config.tavily.api_key == "test-tavily-key"


def test_v00_llm_client_uses_browser_like_headers(workspace_dir: Path) -> None:
    client = build_llm_client(workspace_dir)

    assert "User-Agent" in client.DEFAULT_HEADERS
    assert "Mozilla/5.0" in client.DEFAULT_HEADERS["User-Agent"]
    assert client.DEFAULT_HEADERS["Accept"] == "application/json"


def test_v00_llm_client_moves_system_messages_to_instructions(workspace_dir: Path) -> None:
    client = build_llm_client(workspace_dir)
    payload = {
        "model": "gpt-5.4-mini",
        "messages": [
            {"role": "system", "content": "system one"},
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "system two"},
        ],
    }

    normalized = client._with_instructions(payload)

    assert normalized["instructions"] == "system one\n\nsystem two"
    assert normalized["messages"] == [{"role": "user", "content": "hello"}]


def test_v00_llm_client_detects_instructions_required_error(workspace_dir: Path) -> None:
    client = build_llm_client(workspace_dir)

    assert client._requires_instructions('{"error":{"message":"Instructions are required"}}') is True
    assert client._requires_instructions('{"error":{"message":"other error"}}') is False


def test_v00_llm_client_raises_on_empty_assistant_message(workspace_dir: Path) -> None:
    client = build_llm_client(workspace_dir)
    client.config = replace(client.config, api_key="test-key")  # type: ignore[misc]
    client._request = lambda payload: {  # type: ignore[method-assign]
        "choices": [
            {
                "message": {"role": "assistant"},
            }
        ]
    }
    client._request_streaming_fallback = lambda payload: ModelResult(  # type: ignore[method-assign]
        text="",
        tool_calls=[],
    )

    with pytest.raises(RuntimeError, match="没有文本内容也没有 tool_calls"):
        client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])


def test_v00_llm_client_uses_streaming_fallback_when_non_stream_is_empty(workspace_dir: Path) -> None:
    client = build_llm_client(workspace_dir)
    client.config = replace(client.config, api_key="test-key")  # type: ignore[misc]
    client._request = lambda payload: {  # type: ignore[method-assign]
        "choices": [
            {
                "message": {"role": "assistant"},
            }
        ]
    }
    client._request_streaming_fallback = lambda payload: ModelResult(  # type: ignore[method-assign]
        text="ok",
        tool_calls=[],
    )

    result = client.complete(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert result.text == "ok"


def test_v00_path_guard_blocks_out_of_workspace_reads(workspace_dir: Path) -> None:
    config = load_app_config(workspace_dir)
    router = ToolRouter(workspace_dir, config.runtime)

    with pytest.raises(ToolError, match="路径越界"):
        router.dispatch("read_file", {"path": "../outside.txt"})


def test_v00_policy_blocks_dangerous_shell_command(workspace_dir: Path) -> None:
    config = load_app_config(workspace_dir)
    router = ToolRouter(workspace_dir, config.runtime)

    with pytest.raises(ToolError, match="危险命令"):
        router.dispatch("bash", {"command": "rm -rf ."})


def test_v00_agent_loop_persists_session_and_replays(workspace_dir: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    session_store, loop = build_agent_loop(workspace_dir)

    session_id = session_store.new_session_id()
    answer = loop.run_turn(session_id=session_id, user_input="create a note")

    assert answer == "done"
    assert (workspace_dir / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert (workspace_dir / "runtime" / "events.jsonl").exists()

    events = session_store.replay(session_id)
    assert any(event["event_type"] == "tool_finished" for event in events)
    assert any(
        event["event_type"] == "message"
        and event["payload"].get("role") == "assistant"
        and event["payload"].get("content") == "done"
        for event in events
    )

    replay = run_cli(repo, workspace_dir, "session", "replay", "--last", "1")
    assert session_id in replay.stdout
    assert "assistant: done" in replay.stdout


def test_v00_bash_tool_uses_utf8_decode_for_subprocess_output(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_app_config(workspace_dir)
    router = ToolRouter(workspace_dir, config.runtime)
    completed = subprocess.CompletedProcess(args=["powershell"], returncode=0, stdout="ok", stderr="")
    run_mock = Mock(return_value=completed)
    monkeypatch.setattr(subprocess, "run", run_mock)

    result = router.dispatch("bash", {"command": "echo ok"})

    assert result.output == "ok"
    _, kwargs = run_mock.call_args
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"


def test_v00_session_replay_survives_gbk_console_encoding(workspace_dir: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    session_store, _ = build_agent_loop(workspace_dir)
    session_id = session_store.new_session_id()
    session_store.append_message(session_id, role="assistant", content="\ufeffhello")

    replay = run_cli(
        repo,
        workspace_dir,
        "session",
        "replay",
        "--session-id",
        session_id,
        check=False,
        extra_env={"PYTHONIOENCODING": "gbk"},
    )

    assert replay.returncode == 0
    assert session_id in replay.stdout
