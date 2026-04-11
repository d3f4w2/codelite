from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from codelite.config import load_app_config
import codelite.config.loader as config_loader
from codelite.core.context import ContextCompact
from codelite.core.llm import ModelResult, OpenAICompatibleClient, ToolCallRequest
from codelite.core.loop import AgentLoop
from codelite.core.permissions import PermissionStore
from codelite.core.system_prompt import DYNAMIC_BOUNDARY_MARKER, build_system_prompt
from codelite.core.tavily import TavilySearchClient
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


class FakeWebSearchClient(TavilySearchClient):
    def __init__(self) -> None:
        super().__init__("test-key")

    def search(
        self,
        *,
        query: str,
        max_results: int = 5,
        topic: str = "general",
        search_depth: str = "basic",
        include_answer: bool = True,
    ) -> dict[str, object]:
        del max_results, topic, search_depth, include_answer
        return {
            "query": query,
            "answer": "stub answer",
            "results": [
                {
                    "title": "Stub Result",
                    "url": "https://example.com",
                    "content": "stub content",
                }
            ],
        }


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


def test_v00_cli_health_from_system32_falls_back_to_safe_workspace() -> None:
    if os.name != "nt":
        pytest.skip("Windows-only shell startup behavior")

    repo = Path(__file__).resolve().parents[2]
    system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("CODELITE_WORKSPACE_ROOT", None)
    env["CODELITE_LLM_API_KEY"] = ""
    env["CODELITE_EMBEDDING_API_KEY"] = ""
    env["CODELITE_RERANK_API_KEY"] = ""
    env["TAVILY_API_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-m", "codelite.cli", "health", "--json"],
        cwd=system32,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["workspace_root"] == str(repo)
    assert payload["cwd"] == str(system32)
    assert "PermissionError" not in result.stderr


def test_v00_resolve_workspace_root_promotes_nested_git_directory(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _git(workspace_dir, "init", "-b", "main")
    _git(workspace_dir, "config", "user.email", "nested@example.com")
    _git(workspace_dir, "config", "user.name", "Nested Repo Tester")
    nested_dir = workspace_dir / "src" / "feature"
    nested_dir.mkdir(parents=True, exist_ok=False)

    monkeypatch.chdir(nested_dir)

    resolved = config_loader.resolve_workspace_root()

    assert resolved == workspace_dir.resolve()


def test_v00_cli_health_from_nested_git_directory_uses_git_root_as_workspace(
    workspace_dir: Path,
) -> None:
    _git(workspace_dir, "init", "-b", "main")
    _git(workspace_dir, "config", "user.email", "nested@example.com")
    _git(workspace_dir, "config", "user.name", "Nested Repo Tester")
    nested_dir = workspace_dir / "src" / "feature"
    nested_dir.mkdir(parents=True, exist_ok=False)

    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("CODELITE_WORKSPACE_ROOT", None)
    env["CODELITE_LLM_API_KEY"] = ""
    env["CODELITE_EMBEDDING_API_KEY"] = ""
    env["CODELITE_RERANK_API_KEY"] = ""
    env["TAVILY_API_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-m", "codelite.cli", "health", "--json"],
        cwd=nested_dir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["workspace_root"] == str(workspace_dir.resolve())
    assert payload["cwd"] == str(nested_dir.resolve())


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


def test_v00_loads_api_keys_from_package_env_fallback(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = workspace_dir.parent / f"pkg-{uuid.uuid4().hex[:8]}"
    package_root.mkdir(parents=True, exist_ok=False)
    (package_root / ".env").write_text(
        "\n".join(
            [
                "CODELITE_LLM_API_KEY=fallback-llm",
                "CODELITE_EMBEDDING_API_KEY=fallback-embedding",
                "CODELITE_RERANK_API_KEY=fallback-rerank",
                "TAVILY_API_KEY=fallback-tavily",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config_loader, "_package_workspace_root", lambda: package_root)

    config = load_app_config(workspace_dir)

    assert config.llm.api_key == "fallback-llm"
    assert config.embedding.api_key == "fallback-embedding"
    assert config.rerank.api_key == "fallback-rerank"
    assert config.tavily.api_key == "fallback-tavily"


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


def test_v00_tool_router_supports_web_search_with_tavily_client(workspace_dir: Path) -> None:
    config = load_app_config(workspace_dir)
    router = ToolRouter(
        workspace_dir,
        config.runtime,
        web_search_client=FakeWebSearchClient(),
    )

    result = router.dispatch("web_search", {"query": "latest ai news"})

    assert "stub answer" in result.output
    assert "Stub Result" in result.output


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


def test_v00_dynamic_prompt_boundary_builder(workspace_dir: Path) -> None:
    prompt = build_system_prompt(
        base_prompt="You are CodeLite.",
        workspace_root=workspace_dir,
        session_id="session-x",
        profile_name="fast",
        enable_dynamic_boundary=True,
    ).full_prompt
    assert DYNAMIC_BOUNDARY_MARKER in prompt
    assert "session_id: session-x" in prompt


def test_v00_context_function_result_clearing_keeps_recent_tool_messages(workspace_dir: Path) -> None:
    config = load_app_config(workspace_dir)
    runtime = replace(
        config.runtime,
        tool_result_keep_recent=2,
        context_auto_compact_message_count=999,
        context_auto_compact_char_count=999999,
    )
    compact = ContextCompact(RuntimeLayout(workspace_dir), runtime)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "u1"},
        {"role": "tool", "name": "a", "tool_call_id": "1", "content": "r1"},
        {"role": "tool", "name": "b", "tool_call_id": "2", "content": "r2"},
        {"role": "tool", "name": "c", "tool_call_id": "3", "content": "r3"},
        {"role": "tool", "name": "d", "tool_call_id": "4", "content": "r4"},
    ]
    prepared = compact.prepare("session-fc", messages)
    tool_messages = [item for item in prepared if item.get("role") == "tool"]
    assert len(tool_messages) == 4
    assert tool_messages[0]["content"].startswith("[tool result cleared")
    assert tool_messages[1]["content"].startswith("[tool result cleared")
    assert tool_messages[2]["content"] == "r3"
    assert tool_messages[3]["content"] == "r4"
    snapshot = compact.get("session-fc")
    assert snapshot is not None
    assert snapshot.cleared_tool_results == 2


def test_v00_context_should_compact_respects_message_and_char_thresholds(workspace_dir: Path) -> None:
    config = load_app_config(workspace_dir)
    compact = ContextCompact(RuntimeLayout(workspace_dir), config.runtime)
    small = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    assert compact.should_compact(small) is False

    message_overflow = [{"role": "user", "content": "x"} for _ in range(config.runtime.context_auto_compact_message_count + 1)]
    assert compact.should_compact(message_overflow) is True

    char_runtime = replace(
        config.runtime,
        context_auto_compact_message_count=999999,
        context_auto_compact_char_count=20,
    )
    char_compact = ContextCompact(RuntimeLayout(workspace_dir), char_runtime)
    assert char_compact.should_compact([{"role": "user", "content": "a" * 40}]) is True


def test_v00_permission_store_controls_ask_tools(workspace_dir: Path) -> None:
    class DummyMcp:
        def list_servers(self) -> list[dict[str, Any]]:
            return [{"name": "demo"}]

        def call(self, name: str, request: dict[str, Any], timeout_sec: int = 60) -> dict[str, Any]:
            return {"name": name, "request": request, "timeout_sec": timeout_sec, "ok": True}

    config = load_app_config(workspace_dir)
    layout = RuntimeLayout(workspace_dir)
    permission_store = PermissionStore(layout)
    session_id = "perm-session"
    router = ToolRouter(
        workspace_dir,
        config.runtime,
        session_id=session_id,
        mcp_runtime=DummyMcp(),
        permission_store=permission_store,
    )
    args = {"server": "demo", "request": {"method": "ping"}, "timeout_sec": 5}
    first = router.execute_tool_calls(
        [ToolCallRequest(id="c1", name="mcp_call", arguments=args)]
    )[0]
    assert first.ok is False
    assert "Permission requires approval" in first.output

    permission_store.remember(
        session_id=session_id,
        tool_name="mcp_call",
        arguments=args,
        decision="allow",
        ttl_seconds=300,
    )
    second = router.dispatch("mcp_call", args)
    assert "\"ok\": true" in second.output.lower()
