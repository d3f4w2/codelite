from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_v00_cli_version() -> None:
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "codelite.cli", "version"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "0.0.0"


def test_v00_cli_status_json() -> None:
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "codelite.cli", "status", "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"version": "0.0.0"' in result.stdout
