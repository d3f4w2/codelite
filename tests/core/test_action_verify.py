from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from codelite.core.action_verify import verify_action_text, verify_create_file, verify_import


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"action-verify-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_verify_create_file_allows_workspace_path(workspace_dir: Path) -> None:
    result = verify_create_file(workspace_dir, "codelite/core/new_feature.py")

    assert result.ok is True
    assert result.action_type == "create_file"
    assert result.details["layer"] == 2


def test_verify_create_file_rejects_escape_path(workspace_dir: Path) -> None:
    result = verify_create_file(workspace_dir, "../outside.py")

    assert result.ok is False
    assert "escapes workspace" in result.message


def test_verify_import_layer_direction() -> None:
    workspace = Path(".").resolve()

    allowed = verify_import(workspace, "codelite/tui/shell.py", "codelite/core/loop.py")
    denied = verify_import(workspace, "codelite/core/loop.py", "codelite/tui/shell.py")

    assert allowed.ok is True
    assert denied.ok is False
    assert "layer violation" in denied.message


def test_verify_action_text_unknown_format(workspace_dir: Path) -> None:
    result = verify_action_text(workspace_dir, "rename module foo to bar")

    assert result.ok is False
    assert result.action_type == "unknown"
