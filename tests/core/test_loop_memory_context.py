from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from codelite.cli import build_runtime
from codelite.core.llm import ModelResult


class CaptureModelClient:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del tools
        self.calls.append(messages)
        return ModelResult(text="ok", tool_calls=[])


class StreamingCaptureModelClient:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del tools
        self.calls.append(messages)
        return ModelResult(text="hello", tool_calls=[])

    def stream_complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        *,
        on_event: object | None = None,
    ) -> ModelResult:
        del tools
        self.calls.append(messages)
        if callable(on_event):
            on_event({"type": "text", "text": "hel"})
            on_event({"type": "text", "text": "lo"})
        return ModelResult(text="hello", tool_calls=[])


class TimeoutCaptureModelClient:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []
        self.request_timeout_sec: float | None = None

    def stream_complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        *,
        on_event: object | None = None,
        request_timeout_sec: float | None = None,
    ) -> ModelResult:
        del tools
        self.calls.append(messages)
        self.request_timeout_sec = request_timeout_sec
        if callable(on_event):
            on_event({"type": "text", "text": "ok"})
        return ModelResult(text="ok", tool_calls=[])


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"loop-memory-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_loop_assembles_memory_context_and_emits_event(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    (workspace_dir / "agent.md").write_text("Use concise output and strong architecture checks.", encoding="utf-8")
    (workspace_dir / "user.md").write_text("User prefers fast iteration with visible progress.", encoding="utf-8")

    model = CaptureModelClient()
    services = build_runtime(workspace_dir, model_client=model)

    answer = services.agent_loop.run_turn("memory-test-session", "hello")

    assert answer == "ok"
    assert model.calls, "model client should be called once"
    system_messages = [item for item in model.calls[0] if item.get("role") == "system"]
    assert any("Long-term memory context" in str(item.get("content", "")) for item in system_messages)

    events = services.session_store.replay("memory-test-session")
    assembled = [event for event in events if event.get("event_type") == "memory_context_assembled"]
    assert assembled, "memory_context_assembled event should be emitted"
    assert assembled[-1]["payload"]["loaded_sources"]


def test_loop_emits_model_stream_events_when_client_supports_streaming(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    model = StreamingCaptureModelClient()
    services = build_runtime(workspace_dir, model_client=model)

    answer = services.agent_loop.run_turn("stream-test-session", "say hello")

    assert answer == "hello"
    events = services.session_store.replay("stream-test-session")
    stream_events = [event for event in events if event.get("event_type") == "model_stream"]
    text_events = [event for event in stream_events if (event.get("payload") or {}).get("type") == "text"]

    assert text_events
    assert "".join(str((event.get("payload") or {}).get("text", "")) for event in text_events) == "hello"
    assistant_messages = [
        dict(event.get("payload") or {})
        for event in events
        if event.get("event_type") == "message" and dict(event.get("payload") or {}).get("role") == "assistant"
    ]
    assert assistant_messages
    assert assistant_messages[-1]["content"] == "hello"


def test_loop_passes_remaining_turn_timeout_to_streaming_client(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    model = TimeoutCaptureModelClient()
    services = build_runtime(workspace_dir, model_client=model)

    answer = services.agent_loop.run_turn(
        "timeout-budget-session",
        "say hello",
        turn_timeout_sec=0.25,
        timeout_error_message="shell turn timed out after 0.25s while waiting for model response",
    )

    assert answer == "ok"
    assert model.request_timeout_sec is not None
    assert 0 < float(model.request_timeout_sec) <= 0.25
