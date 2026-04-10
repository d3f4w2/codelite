from __future__ import annotations

import json
import shutil
import uuid
from datetime import date
from pathlib import Path

import pytest

from codelite.core.memory_runtime import MemoryRuntime
from codelite.memory import MemoryLedger, MemoryPolicy, MemoryViews
from codelite.storage.events import RuntimeLayout


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"memory-runtime-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _build_runtime(workspace_dir: Path) -> MemoryRuntime:
    layout = RuntimeLayout(workspace_dir)
    return MemoryRuntime(MemoryLedger(layout), MemoryViews(layout), MemoryPolicy())


def test_memory_runtime_assemble_context_respects_priority_and_budget(workspace_dir: Path) -> None:
    (workspace_dir / "agent.md").write_text("agent-map " + "A" * 600, encoding="utf-8")
    (workspace_dir / "soul.md").write_text("soul-note " + "B" * 320, encoding="utf-8")
    (workspace_dir / "user.md").write_text("user-preference " + "C" * 320, encoding="utf-8")
    (workspace_dir / "Memory.md").write_text("long-memory " + "D" * 320, encoding="utf-8")

    runtime = _build_runtime(workspace_dir)
    runtime.remember(kind="experience", text="Always verify architecture rules before coding.")
    runtime.remember(kind="preference", text="Prefer concise shell output and deterministic scripts.")

    bundle = runtime.assemble_context(
        budget_chars=2200,
        today=date(2026, 4, 9),
    )
    report = bundle["report"]
    message = bundle["system_message_text"]

    assert isinstance(message, str)
    assert message
    assert report["total_chars"] <= report["budget_chars"]
    assert report["loaded_sources"]
    assert report["loaded_sources"][0]["source"] == "agent_spec"
    assert any(item["source"] == "ledger_experience" for item in report["loaded_sources"])
    assert "## Agent 合约" in message
    assert "## Experience Snippets" in message


def test_memory_runtime_recent_notes_default_to_two_days(workspace_dir: Path) -> None:
    (workspace_dir / "AGENTS.md").write_text("runtime policy map", encoding="utf-8")
    (workspace_dir / "memory-20260409.md").write_text("today note", encoding="utf-8")
    (workspace_dir / "memory-20260408.md").write_text("yesterday note", encoding="utf-8")
    (workspace_dir / "memory-20260405.md").write_text("older note", encoding="utf-8")

    runtime = _build_runtime(workspace_dir)
    bundle = runtime.assemble_context(
        budget_chars=3000,
        today=date(2026, 4, 9),
    )
    report = bundle["report"]

    loaded_sources = [item["source"] for item in report["loaded_sources"]]
    assert "memory_note:memory-20260409.md" in loaded_sources
    assert "memory_note:memory-20260408.md" in loaded_sources
    assert "memory_note:memory-20260405.md" not in loaded_sources
    assert "older note" not in bundle["system_message_text"]


def test_memory_runtime_bootstrap_creates_manifest_and_core_files(workspace_dir: Path) -> None:
    runtime = _build_runtime(workspace_dir)
    payload = runtime.bootstrap_memory_files()

    assert Path(payload["manifest_path"]).exists()
    files = runtime.memory_files(include_preview=False)
    keys = {item["key"] for item in files}
    assert {"agent", "user", "soul", "tool"} <= keys
    for item in files:
        assert Path(item["path"]).exists()


def test_memory_runtime_remember_forget_and_effective_preferences(workspace_dir: Path) -> None:
    runtime = _build_runtime(workspace_dir)
    runtime.bootstrap_memory_files()

    remembered = runtime.remember_preference(domain="user", text="Tone: concise", source="test")
    assert remembered["added"] is True

    prefs = runtime.effective_preferences()
    assert any(item["domain"] == "user" and item["text"] == "Tone: concise" for item in prefs)

    forgotten = runtime.forget_preference(domain="user", keyword="Tone", source="test")
    assert forgotten["removed_count"] >= 1
    prefs_after = runtime.effective_preferences()
    assert not any(item["domain"] == "user" and "Tone:" in item["text"] for item in prefs_after)


def test_memory_runtime_suggest_candidate_scope_and_domain(workspace_dir: Path) -> None:
    runtime = _build_runtime(workspace_dir)

    style = runtime.suggest_candidate("以后回答风格请简洁一点")
    assert style is not None
    assert style["domain"] == "soul"

    preference = runtime.suggest_candidate("记住我偏好先给结论再给细节")
    assert preference is not None
    assert preference["domain"] == "user"

    normal_task = runtime.suggest_candidate("请帮我修一下 lint 错误")
    assert normal_task is None


def test_memory_runtime_bootstrap_migrates_legacy_templates_and_manifest_titles(workspace_dir: Path) -> None:
    runtime = _build_runtime(workspace_dir)
    manifest_path = workspace_dir / "runtime" / "memory" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "files": [
                    {"key": "agent", "path": "agent.md", "title": "Agent Spec", "max_chars": 1100, "editable": True},
                    {"key": "user", "path": "user.md", "title": "User Profile", "max_chars": 900, "editable": True},
                    {"key": "soul", "path": "soul.md", "title": "Agent Soul", "max_chars": 760, "editable": True},
                    {"key": "tool", "path": "tool.md", "title": "Tool Policy", "max_chars": 760, "editable": True},
                    {
                        "key": "long_memory",
                        "path": "Memory.md",
                        "title": "Long-Term Memory",
                        "max_chars": 900,
                        "editable": True,
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (workspace_dir / "agent.md").write_text(
        (
            "# Agent Contract\n\n"
            "## Role\n"
            "- Define primary system constraints and task navigation here.\n\n"
            "## Navigation\n"
            "- user.md: user preferences\n"
            "- soul.md: response style and principles\n"
            "- tool.md: tool usage policies\n\n"
            "## Managed Preferences\n"
            "<!-- CODELITE:MANAGED_PREFS START -->\n"
            "- Keep outputs concise and actionable.\n"
            "<!-- CODELITE:MANAGED_PREFS END -->\n"
        ),
        encoding="utf-8",
    )
    (workspace_dir / "user.md").write_text(
        (
            "# User Profile\n\n"
            "## Stable Preferences\n"
            "<!-- CODELITE:MANAGED_PREFS START -->\n"
            "- Language: 涓枃\n"
            "<!-- CODELITE:MANAGED_PREFS END -->\n"
        ),
        encoding="utf-8",
    )
    (workspace_dir / "soul.md").write_text(
        (
            "# Agent Soul\n\n"
            "## Working Style\n"
            "<!-- CODELITE:MANAGED_PREFS START -->\n"
            "- Be direct, pragmatic, and factual.\n"
            "<!-- CODELITE:MANAGED_PREFS END -->\n"
        ),
        encoding="utf-8",
    )
    (workspace_dir / "tool.md").write_text(
        (
            "# Tool Policy\n\n"
            "## Tool Preferences\n"
            "<!-- CODELITE:MANAGED_PREFS START -->\n"
            "- Prefer deterministic commands and explicit file paths.\n"
            "<!-- CODELITE:MANAGED_PREFS END -->\n"
        ),
        encoding="utf-8",
    )
    (workspace_dir / "Memory.md").write_text(
        (
            "# Long-Term Memory\n\n"
            "## Managed Preferences\n"
            "<!-- CODELITE:MANAGED_PREFS START -->\n"
            "<!-- CODELITE:MANAGED_PREFS END -->\n"
        ),
        encoding="utf-8",
    )

    payload = runtime.bootstrap_memory_files()

    migrated_files = set(payload.get("migrated_files", []))
    assert any(path.endswith("agent.md") for path in migrated_files)
    assert any(path.endswith("user.md") for path in migrated_files)
    assert any(path.endswith("soul.md") for path in migrated_files)
    assert any(path.endswith("tool.md") for path in migrated_files)
    assert any(path.endswith("Memory.md") for path in migrated_files)

    assert (workspace_dir / "agent.md").read_text(encoding="utf-8").startswith("# Agent 合约")
    assert (workspace_dir / "user.md").read_text(encoding="utf-8").startswith("# 用户画像")
    assert "语言：中文" in (workspace_dir / "user.md").read_text(encoding="utf-8")
    assert (workspace_dir / "soul.md").read_text(encoding="utf-8").startswith("# Agent 灵魂")
    assert (workspace_dir / "tool.md").read_text(encoding="utf-8").startswith("# 工具策略")
    assert (workspace_dir / "Memory.md").read_text(encoding="utf-8").startswith("# 长期记忆")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    title_map = {item["key"]: item["title"] for item in manifest["files"]}
    assert title_map["agent"] == "Agent 合约"
    assert title_map["user"] == "用户画像"
    assert title_map["soul"] == "Agent 灵魂"
    assert title_map["tool"] == "工具策略"
    assert title_map["long_memory"] == "长期记忆"


def test_memory_runtime_bootstrap_does_not_overwrite_customized_template(workspace_dir: Path) -> None:
    runtime = _build_runtime(workspace_dir)
    manifest_path = workspace_dir / "runtime" / "memory" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "files": [
                    {"key": "agent", "path": "agent.md", "title": "Agent Spec", "max_chars": 1100, "editable": True},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace_dir / "agent.md").write_text(
        (
            "# Agent Contract\n\n"
            "## Role\n"
            "- Define primary system constraints and task navigation here.\n\n"
            "## Navigation\n"
            "- user.md: user preferences\n"
            "- soul.md: response style and principles\n"
            "- tool.md: tool usage policies\n\n"
            "## Custom Notes\n"
            "- keep this section\n\n"
            "## Managed Preferences\n"
            "<!-- CODELITE:MANAGED_PREFS START -->\n"
            "- Keep outputs concise and actionable.\n"
            "<!-- CODELITE:MANAGED_PREFS END -->\n"
        ),
        encoding="utf-8",
    )

    payload = runtime.bootstrap_memory_files()
    migrated_files = set(payload.get("migrated_files", []))
    assert not any(path.endswith("agent.md") for path in migrated_files)

    content = (workspace_dir / "agent.md").read_text(encoding="utf-8")
    assert content.startswith("# Agent Contract")
    assert "## Custom Notes" in content
