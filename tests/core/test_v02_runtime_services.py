from __future__ import annotations

import io
import json
import shutil
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codelite.cli import main
from codelite.core.llm import ModelResult, ToolCallRequest
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import TaskStatus, TaskStore


class ScriptedTodoModelClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            return ModelResult(
                text="planning",
                tool_calls=[
                    ToolCallRequest(
                        id="call-todo",
                        name="todo_write",
                        arguments={
                            "items": [
                                {"id": "inspect", "content": "Inspect repository", "status": "in_progress"},
                                {"id": "summarize", "content": "Write summary", "status": "pending"},
                            ]
                        },
                    )
                ],
            )
        return ModelResult(text="done", tool_calls=[])


def run_main_json(args: list[str], *, model_client: object | None = None) -> dict[str, object] | list[object]:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(args, model_client=model_client)  # type: ignore[arg-type]
    assert exit_code == 0
    return json.loads(stdout.getvalue())


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
    workspace = base_dir / f"v02-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def write_low_threshold_config(workspace_dir: Path) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    template = (project_root / "codelite" / "config" / "runtime.yaml").read_text(encoding="utf-8")
    customized = (
        template.replace("context_auto_compact_message_count: 18", "context_auto_compact_message_count: 4")
        .replace("context_keep_last_messages: 8", "context_keep_last_messages: 2")
        .replace("context_auto_compact_char_count: 12000", "context_auto_compact_char_count: 200")
    )
    config_path = workspace_dir / "runtime.test.yaml"
    config_path.write_text(customized, encoding="utf-8")
    return config_path


def test_v02_todo_tool_and_context_compaction(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_low_threshold_config(workspace_dir)
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    monkeypatch.setenv("CODELITE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CODELITE_LLM_API_KEY", "")
    monkeypatch.setenv("CODELITE_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("CODELITE_RERANK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")

    layout = RuntimeLayout(workspace_dir)
    session_store = SessionStore(EventStore(layout))
    session_id = session_store.new_session_id()
    session_store.ensure_session(session_id)
    for index in range(5):
        session_store.append_message(session_id, role="user", content=f"older user message {index}")
        session_store.append_message(session_id, role="assistant", content=f"older assistant message {index}")

    payload = run_main_json(
        ["run", "--session-id", session_id, "--json", "Plan and then finish the task."],
        model_client=ScriptedTodoModelClient(),
    )

    assert payload["session_id"] == session_id
    assert payload["answer"] == "done"

    todo_payload = run_main_json(["todo", "show", "--session-id", session_id, "--json"])
    assert todo_payload["counts"]["in_progress"] == 1
    assert todo_payload["counts"]["pending"] == 1

    context_payload = run_main_json(["context", "show", "--session-id", session_id, "--json"])
    assert context_payload["original_message_count"] > context_payload["compacted_message_count"]
    assert context_payload["kept_message_count"] == 2

    events = session_store.replay(session_id)
    assert any(event["event_type"] == "context_compacted" for event in events)
    assert any(
        event["event_type"] == "message"
        and event["payload"].get("role") == "assistant"
        and event["payload"].get("content") == "done"
        for event in events
    )


def test_v02_cron_reconcile_heart_and_watchdog_cli(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    monkeypatch.setenv("CODELITE_LLM_API_KEY", "")
    monkeypatch.setenv("CODELITE_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("CODELITE_RERANK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")

    store = TaskStore(RuntimeLayout(workspace_dir))
    lease = store.acquire_lease("expired-demo", owner="tester", ttl_seconds=30)
    store.start_task("expired-demo", lease_id=lease.lease_id)

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    store._write_json(  # type: ignore[attr-defined]
        store.lease_path("expired-demo"),
        {
            "task_id": "expired-demo",
            "lease_id": lease.lease_id,
            "owner": lease.owner,
            "acquired_at": lease.acquired_at,
            "expires_at": expired_at.isoformat(),
            "ttl_seconds": lease.ttl_seconds,
        },
    )

    jobs_payload = run_main_json(["cron", "list", "--json"])
    assert {item["name"] for item in jobs_payload} >= {
        "heartbeat_scan",
        "task_reconcile",
        "compact_maintenance",
        "metrics_rollup",
    }

    reconcile_payload = run_main_json(["cron", "run", "--job", "task_reconcile", "--json"])
    assert reconcile_payload["last_status"] == "ok"
    assert reconcile_payload["result"]["expired_task_ids"] == ["expired-demo"]
    assert store.get_task("expired-demo").status is TaskStatus.BLOCKED

    beat_payload = run_main_json(
        [
            "heart",
            "beat",
            "--component",
            "tool_router",
            "--status",
            "red",
            "--failure-streak",
            "3",
            "--json",
        ]
    )
    assert beat_payload["component_id"] == "tool_router"
    assert beat_payload["status"] == "red"

    heart_payload = run_main_json(["heart", "status", "--json"])
    tool_router = next(item for item in heart_payload["components"] if item["component_id"] == "tool_router")
    assert tool_router["status"] == "red"

    watchdog_payload = run_main_json(["watchdog", "simulate", "--component", "tool_router", "--json"])
    assert watchdog_payload["component_id"] == "tool_router"
    assert watchdog_payload["status_before"] == "red"
    assert watchdog_payload["status_after"] == "yellow"
    assert Path(watchdog_payload["snapshot_path"]).exists()

    metrics_payload = run_main_json(["cron", "run", "--job", "metrics_rollup", "--json"])
    metrics_path = Path(metrics_payload["result"]["metrics_path"])
    assert metrics_path.exists()
