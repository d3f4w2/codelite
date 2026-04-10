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
from codelite.config import load_app_config
from codelite.core.delivery import DeliveryQueue
from codelite.core.lanes import LaneScheduler
from codelite.core.llm import ModelResult, ToolCallRequest
from codelite.core.retrieval import RetrievalRouter
from codelite.core.tavily import TavilySearchClient
from codelite.core.validate_pipeline import ValidatePipeline, ValidateStageResult
from codelite.hooks import HookRuntime
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore


def run_main_json(args: list[str], *, model_client: object | None = None) -> dict[str, object] | list[object]:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(args, model_client=model_client)  # type: ignore[arg-type]
    assert exit_code == 0
    return json.loads(stdout.getvalue())


class ScriptedNagModelClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del messages, tools
        self.calls += 1
        if self.calls <= 3:
            return ModelResult(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id=f"call-read-{self.calls}",
                        name="read_file",
                        arguments={"path": "README.md", "start_line": 1, "end_line": 1},
                    )
                ],
            )
        return ModelResult(text="completed with nag", tool_calls=[])


class FakeRetrievalWebClient(TavilySearchClient):
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
            "answer": "web answer",
            "results": [
                {
                    "title": "Web Result",
                    "url": "https://example.com/news",
                    "content": "web content",
                }
            ],
        }


@pytest.fixture(autouse=True)
def clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CODELITE_CONFIG_PATH",
        "CODELITE_EMBEDDING_API_KEY",
        "CODELITE_LLM_API_KEY",
        "CODELITE_RERANK_API_KEY",
        "CODELITE_WORKSPACE_ROOT",
        "TAVILY_API_KEY",
        "CODELITE_MODEL_FAST",
        "CODELITE_MODEL_DEEP",
        "CODELITE_MODEL_REVIEW",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"v021-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def prepare_env(monkeypatch: pytest.MonkeyPatch, workspace_dir: Path) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    monkeypatch.setenv("CODELITE_LLM_API_KEY", "")
    monkeypatch.setenv("CODELITE_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("CODELITE_RERANK_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")


def test_v021_lane_generation_and_delivery_queue_recovery(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    lane_scheduler = LaneScheduler(RuntimeLayout(workspace_dir))
    stale_lane = lane_scheduler.bump_generation("main")
    stale = lane_scheduler.enqueue("main", job_id="stale-job", generation=stale_lane.generation - 1)
    assert stale["accepted"] is False
    assert stale["reason"] == "stale_generation"

    failing = run_main_json(
        [
            "delivery",
            "enqueue",
            "--kind",
            "always_fail",
            "--payload-json",
            json.dumps({"message": "boom"}),
            "--max-attempts",
            "1",
            "--json",
        ]
    )
    delivery_id = failing["delivery_id"]
    processed = run_main_json(["delivery", "process", "--json"])
    assert processed[0]["status"] == "failed"

    layout = RuntimeLayout(workspace_dir)
    pending_item = run_main_json(
        [
            "delivery",
            "enqueue",
            "--kind",
            "demo_echo",
            "--payload-json",
            json.dumps({"note": "recover me"}),
            "--json",
        ]
    )
    recover_id = pending_item["delivery_id"]
    wal_path = layout.delivery_wal_dir / f"{recover_id}.json"
    (layout.delivery_pending_dir / f"{recover_id}.json").unlink()
    DeliveryQueue(layout, load_app_config(workspace_dir).runtime)
    assert (layout.delivery_pending_dir / f"{recover_id}.json").exists()
    assert wal_path.exists()


def test_v021_validate_hooks_skills_background_and_critic(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    project_root = Path(__file__).resolve().parents[2]

    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(project_root))
    doctor = run_main_json(["hooks", "doctor", "--json"])
    assert doctor["agents_md_exists"] is True
    assert all(item["exists"] for item in doctor["modules"].values())

    skill = run_main_json(["skills", "load", "--name", "code-review", "--json"])
    assert skill["name"] == "code-review"

    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    background = run_main_json(
        [
            "background",
            "run",
            "--name",
            "digest",
            "--payload-json",
            json.dumps({"text": "hello"}),
            "--session-id",
            "bg-session",
            "--json",
        ]
    )
    assert background["kind"] == "background_task"

    processed = run_main_json(["background", "process", "--json"])
    result_path = Path(processed[0]["result"]["result_path"])
    assert result_path.exists()

    review = run_main_json(
        [
            "critic",
            "review",
            "--prompt",
            "summarize the work",
            "--answer",
            "TODO",
            "--json",
        ]
    )
    assert review["passed"] is False
    logged = run_main_json(
        [
            "critic",
            "log",
            "--kind",
            "validation",
            "--message",
            "pipeline failed",
            "--json",
        ]
    )
    assert logged["kind"] == "validation"
    refined = run_main_json(["critic", "refine", "--json"])
    assert any(rule["failure_kind"] == "validation" for rule in refined["rules"])

    hook_runtime = HookRuntime(project_root, RuntimeLayout(project_root))

    def fake_executor(command: list[str], cwd: Path) -> ValidateStageResult:
        stage = "unknown"
        if "compileall" in command:
            stage = "build"
        elif "lint_arch.py" in command:
            stage = "lint-arch"
        return ValidateStageResult(stage=stage, command=command, exit_code=1, ok=False, output="boom")

    pipeline = ValidatePipeline(project_root, hook_runtime=hook_runtime, executor=fake_executor)
    report = pipeline.run(pytest_target="tests/core/test_v00_smoke.py")
    assert report["ok"] is False
    assert hook_runtime.layout.hook_failures_path.exists()


def test_v021_retrieval_memory_model_routing_and_todo_nag(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    (workspace_dir / "README.md").write_text("runtime services are documented here\n", encoding="utf-8")

    retrieval = run_main_json(
        [
            "retrieval",
            "run",
            "--prompt",
            "Read README and summarize runtime services",
            "--json",
        ]
    )
    assert retrieval["decision"]["route"] == "local_docs"
    assert retrieval["decision"]["enough"] is True

    route = run_main_json(
        [
            "model",
            "route",
            "--prompt",
            "Please review this patch for bugs",
            "--json",
        ]
    )
    assert route["name"] == "review"

    session_id = "nag-session"
    payload = run_main_json(
        ["run", "--session-id", session_id, "--json", "Keep reading README without updating todos."],
        model_client=ScriptedNagModelClient(),
    )
    assert payload["answer"] == "completed with nag"

    timeline = run_main_json(["memory", "timeline", "--json"])
    kinds = {item["kind"] for item in timeline["items"]}
    assert {"retrieval", "prompt", "answer"} <= kinds

    keyword = run_main_json(["memory", "keyword", "--keyword", "runtime", "--json"])
    assert keyword["entry_ids"]

    session_store = SessionStore(EventStore(RuntimeLayout(workspace_dir)))
    events = session_store.replay(session_id)
    assert any(event["event_type"] == "todo_nag" for event in events)

    resilience = run_main_json(["resilience", "drill", "--scenario", "overflow_then_fallback", "--json"])
    layers = [attempt["layer"] for attempt in resilience["attempts"]]
    assert "overflow_compaction" in layers
    assert resilience["profile"] == "deep"


def test_v021_retrieval_router_can_use_web_search_when_tavily_is_configured(workspace_dir: Path) -> None:
    config = load_app_config(workspace_dir)
    retrieval = RetrievalRouter(
        workspace_root=workspace_dir,
        layout=RuntimeLayout(workspace_dir),
        runtime_config=config.runtime,
        web_search_client=FakeRetrievalWebClient(),
    )

    payload = retrieval.run("search the latest AI news on the internet")

    assert payload["decision"]["route"] == "web"
    assert payload["decision"]["enough"] is True
    assert payload["results"][0]["type"] == "answer"
    assert payload["results"][1]["type"] == "web"


def test_v021_permissions_cli_allows_storing_and_listing_decisions(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    payload = run_main_json(
        [
            "permissions",
            "allow",
            "--session-id",
            "session-1",
            "--tool",
            "mcp_call",
            "--arguments-json",
            json.dumps({"server": "demo", "request": {"method": "ping"}}),
            "--ttl-sec",
            "60",
            "--json",
        ]
    )
    assert payload["decision"] == "allow"
    listed = run_main_json(
        [
            "permissions",
            "status",
            "--session-id",
            "session-1",
            "--json",
        ]
    )
    assert listed
    assert listed[0]["tool_name"] == "mcp_call"


def test_v021_delivery_process_supports_kind_filter_and_parallel_workers(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    run_main_json(
        [
            "delivery",
            "enqueue",
            "--kind",
            "demo_echo",
            "--payload-json",
            json.dumps({"message": "ok"}),
            "--json",
        ]
    )
    run_main_json(
        [
            "delivery",
            "enqueue",
            "--kind",
            "always_fail",
            "--payload-json",
            json.dumps({"message": "should-stay-pending"}),
            "--json",
        ]
    )

    processed = run_main_json(
        [
            "delivery",
            "process",
            "--kind",
            "demo_echo",
            "--workers",
            "2",
            "--json",
        ]
    )
    assert processed
    assert all(item["kind"] == "demo_echo" for item in processed)

    status = run_main_json(["delivery", "status", "--json"])
    assert any(item["kind"] == "always_fail" for item in status["pending"])


def test_v021_delivery_recover_can_requeue_expired_claim(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    layout = RuntimeLayout(workspace_dir)
    queue = DeliveryQueue(layout, load_app_config(workspace_dir).runtime)
    created = queue.enqueue("demo_echo", {"message": "recover-claim"})
    claimed = queue.claim_one(allowed_kinds={"demo_echo"}, worker_id="worker-a")
    assert claimed is not None
    assert claimed.delivery_id == created.delivery_id
    assert claimed.status == "running"

    pending_path = layout.delivery_pending_dir / f"{created.delivery_id}.json"
    pending_payload = json.loads(pending_path.read_text(encoding="utf-8"))
    pending_payload["claim_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    pending_path.write_text(json.dumps(pending_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered_ids = queue.recover_pending()
    assert created.delivery_id in recovered_ids

    status = queue.status()
    restored = next(item for item in status["pending"] if item["delivery_id"] == created.delivery_id)
    assert restored["status"] == "pending"
    assert restored["claimed_by"] == ""
