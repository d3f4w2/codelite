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
