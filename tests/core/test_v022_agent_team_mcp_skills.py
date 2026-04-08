from __future__ import annotations

import io
import json
import shutil
import uuid
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from codelite.cli import main
from codelite.core.llm import ModelResult


def run_main_json(args: list[str], *, model_client: object | None = None) -> dict[str, object] | list[object]:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(args, model_client=model_client)  # type: ignore[arg-type]
    assert exit_code == 0
    return json.loads(stdout.getvalue())


class ScriptedSubagentModelClient:
    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del messages, tools
        return ModelResult(text="subagent complete", tool_calls=[])


@pytest.fixture(autouse=True)
def clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CODELITE_CONFIG_PATH",
        "CODELITE_EMBEDDING_API_KEY",
        "CODELITE_LLM_API_KEY",
        "CODELITE_RERANK_API_KEY",
        "CODELITE_WORKSPACE_ROOT",
        "TAVILY_API_KEY",
        "CODELITE_SKILLS_DIRS",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"v022-{uuid.uuid4().hex[:8]}"
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


def test_v022_external_skill_compatibility_loads_skill_md(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    skill_dir = workspace_dir / ".skills" / "market-demo-1.2.0"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: market-demo",
                "description: Market skill loaded from external directory.",
                "---",
                "",
                "# Market Demo",
                "",
                "Use this skill for compatibility tests.",
            ]
        ),
        encoding="utf-8",
    )

    listed = run_main_json(["skills", "list", "--json"])
    assert any(item["name"] == "market-demo" for item in listed)

    loaded = run_main_json(["skills", "load", "--name", "market-demo", "--json"])
    assert loaded["name"] == "market-demo"
    assert loaded["source"] == "external"
    assert str(loaded["path"]).endswith("market-demo-1.2.0")


def test_v022_agent_team_and_subagent_queue_process(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)

    team = run_main_json(
        [
            "team",
            "create",
            "--name",
            "delivery-team",
            "--strategy",
            "parallel",
            "--json",
        ]
    )
    team_id = team["team_id"]

    spawned = run_main_json(
        [
            "subagent",
            "spawn",
            "--team-id",
            team_id,
            "--prompt",
            "Summarize the task in one sentence.",
            "--session-id",
            "parent-session",
            "--mode",
            "queue",
            "--json",
        ]
    )
    subagent_id = spawned["subagent"]["subagent_id"]

    processed = run_main_json(
        [
            "subagent",
            "process",
            "--json",
        ],
        model_client=ScriptedSubagentModelClient(),
    )
    assert processed
    assert any(item["subagent_id"] == subagent_id for item in processed)

    detail = run_main_json(["subagent", "show", "--subagent-id", subagent_id, "--json"])
    assert detail["status"] == "done"
    assert detail["subagent_session_id"]
    assert Path(detail["result_path"]).exists()


def test_v022_subagent_process_ignores_non_subagent_deliveries(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)

    team = run_main_json(
        [
            "team",
            "create",
            "--name",
            "mixed-queue-team",
            "--strategy",
            "parallel",
            "--json",
        ]
    )
    team_id = team["team_id"]

    spawned = run_main_json(
        [
            "subagent",
            "spawn",
            "--team-id",
            team_id,
            "--prompt",
            "Summarize the task in one sentence.",
            "--session-id",
            "parent-session",
            "--mode",
            "queue",
            "--json",
        ]
    )
    subagent_id = spawned["subagent"]["subagent_id"]

    run_main_json(
        [
            "delivery",
            "enqueue",
            "--kind",
            "demo_echo",
            "--payload-json",
            json.dumps({"note": "leave me pending"}),
            "--json",
        ]
    )

    processed = run_main_json(
        [
            "subagent",
            "process",
            "--json",
        ],
        model_client=ScriptedSubagentModelClient(),
    )
    assert any(item["subagent_id"] == subagent_id for item in processed)

    detail = run_main_json(["subagent", "show", "--subagent-id", subagent_id, "--json"])
    assert detail["status"] == "done"

    delivery_status = run_main_json(["delivery", "status", "--json"])
    assert any(item["kind"] == "demo_echo" for item in delivery_status["pending"])


def test_v022_mcp_entrypoint_add_list_and_call(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    (workspace_dir / "echo_mcp.py").write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "",
                "line = sys.stdin.readline().strip()",
                "payload = json.loads(line) if line else {}",
                "print(json.dumps({'ok': True, 'method': payload.get('method'), 'id': payload.get('id')}))",
            ]
        ),
        encoding="utf-8",
    )

    added = run_main_json(
        [
            "mcp",
            "add",
            "--name",
            "echo",
            "--command",
            "python",
            "--args-json",
            json.dumps(["echo_mcp.py"]),
            "--json",
        ]
    )
    assert added["name"] == "echo"

    listed = run_main_json(["mcp", "list", "--json"])
    assert any(item["name"] == "echo" for item in listed)

    called = run_main_json(
        [
            "mcp",
            "call",
            "--name",
            "echo",
            "--request-json",
            json.dumps({"id": "1", "method": "ping"}),
            "--json",
        ]
    )
    assert called["response"]["ok"] is True
    assert called["response"]["method"] == "ping"
    assert Path(called["invocation_path"]).exists()


def test_v022_mcp_entrypoint_accepts_relaxed_json_inputs(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_env(monkeypatch, workspace_dir)
    (workspace_dir / "echo_mcp.py").write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "",
                "line = sys.stdin.readline().strip()",
                "payload = json.loads(line) if line else {}",
                "print(json.dumps({'ok': True, 'method': payload.get('method'), 'id': payload.get('id')}))",
            ]
        ),
        encoding="utf-8",
    )

    added = run_main_json(
        [
            "mcp",
            "add",
            "--name",
            "echo-relaxed",
            "--command",
            "python",
            "--args-json",
            "[echo_mcp.py]",
            "--env-json",
            "{MCP_TAG:manual}",
            "--json",
        ]
    )
    assert added["name"] == "echo-relaxed"
    assert added["args"] == ["echo_mcp.py"]
    assert added["env"]["MCP_TAG"] == "manual"

    called = run_main_json(
        [
            "mcp",
            "call",
            "--name",
            "echo-relaxed",
            "--request-json",
            "{id:manual-1,method:ping}",
            "--json",
        ]
    )
    assert called["response"]["ok"] is True
    assert called["response"]["id"] == "manual-1"
    assert called["response"]["method"] == "ping"
