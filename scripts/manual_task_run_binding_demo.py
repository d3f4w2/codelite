from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from codelite.cli import main
from codelite.core.llm import ModelResult, ToolCallRequest


class ScriptedTaskModelClient:
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
                        id="call-edit",
                        name="edit_file",
                        arguments={
                            "path": "app.txt",
                            "old_text": "base\n",
                            "new_text": "worktree-output\n",
                        },
                    )
                ],
            )
        return ModelResult(text="task complete", tool_calls=[])


def git(repo: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        raise RuntimeError(f"git {' '.join(args)} failed\n{output}")


def capture_cli(argv: list[str], *, model_client: ScriptedTaskModelClient | None = None) -> str:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(argv, model_client=model_client)
    if exit_code != 0:
        raise RuntimeError(f"codelite exited with status {exit_code} for argv={argv}")
    return stdout.getvalue().strip()


def main_script() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="codelite-task-run-demo-"))
    old_env = os.environ.copy()

    try:
        git(temp_root, "init", "-b", "main")
        git(temp_root, "config", "user.email", "demo@example.com")
        git(temp_root, "config", "user.name", "CodeLite Demo")
        (temp_root / "app.txt").write_text("base\n", encoding="utf-8")
        git(temp_root, "add", "app.txt")
        git(temp_root, "commit", "-m", "init")

        os.environ["CODELITE_WORKSPACE_ROOT"] = str(temp_root)
        os.environ["CODELITE_LLM_API_KEY"] = ""
        os.environ["CODELITE_EMBEDDING_API_KEY"] = ""
        os.environ["CODELITE_RERANK_API_KEY"] = ""
        os.environ["TAVILY_API_KEY"] = ""

        run_output = capture_cli(
            [
                "task",
                "run",
                "--task-id",
                "demo-task",
                "--title",
                "Demo Task",
                "--json",
                "Update app.txt inside the managed worktree.",
            ],
            model_client=ScriptedTaskModelClient(),
        )
        run_payload = json.loads(run_output)
        task_output = capture_cli(["task", "show", "--task-id", "demo-task", "--json"])
        list_output = capture_cli(["task", "list", "--json"])
        worktree_output = capture_cli(["worktree", "list", "--json"])

        worktree_path = Path(run_payload["worktree"]["path"])
        summary = {
            "workspace_root": str(temp_root),
            "root_app_txt": (temp_root / "app.txt").read_text(encoding="utf-8"),
            "worktree_app_txt": (worktree_path / "app.txt").read_text(encoding="utf-8"),
            "task_run": run_payload,
            "task_show": json.loads(task_output),
            "task_list": json.loads(list_output),
            "worktree_list": json.loads(worktree_output),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main_script())
