from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codelite.storage.events import RuntimeLayout, utc_now


class WorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorktreeRecord:
    task_id: str
    branch: str
    path: str
    base_ref: str
    created_at: str
    head: str | None = None
    attached: bool = True
    path_exists: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorktreeRecord:
        return cls(
            task_id=str(payload["task_id"]),
            branch=str(payload["branch"]),
            path=str(payload["path"]),
            base_ref=str(payload["base_ref"]),
            created_at=str(payload["created_at"]),
            head=payload.get("head"),
            attached=bool(payload.get("attached", True)),
            path_exists=bool(payload.get("path_exists", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorktreeManager:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.layout = RuntimeLayout(self.workspace_root)
        self.layout.ensure()
        self._ensure_git_workspace()

    def prepare(self, task_id: str, *, title: str = "", base_ref: str = "HEAD") -> WorktreeRecord:
        key = _worktree_key(task_id)
        metadata_path = self.record_path(task_id)
        existing = self.get_record(task_id)
        git_worktrees = self._git_worktrees_by_path()

        if existing is not None:
            worktree_path = Path(existing.path)
            if existing.path in git_worktrees and worktree_path.exists():
                return self._hydrate_record(existing, git_worktrees)
            if worktree_path.exists() and existing.path not in git_worktrees:
                raise WorktreeError(
                    f"worktree path exists but is not registered with git: {worktree_path}"
                )
            if self._branch_exists(existing.branch):
                self._git("worktree", "add", str(worktree_path), existing.branch)
                return self._hydrate_record(existing, self._git_worktrees_by_path())

        branch = self.branch_name(task_id)
        worktree_path = self.worktree_path(task_id, title=title)
        if worktree_path.exists() and worktree_path.resolve() != self.workspace_root:
            raise WorktreeError(f"worktree path already exists: {worktree_path}")

        if self._branch_exists(branch):
            self._git("worktree", "add", str(worktree_path), branch)
        else:
            self._git("worktree", "add", "-b", branch, str(worktree_path), base_ref)

        record = WorktreeRecord(
            task_id=task_id,
            branch=branch,
            path=str(worktree_path),
            base_ref=base_ref,
            created_at=existing.created_at if existing is not None else utc_now(),
        )
        self._write_record(metadata_path, record)
        return self._hydrate_record(record, self._git_worktrees_by_path())

    def list_managed(self) -> list[WorktreeRecord]:
        git_worktrees = self._git_worktrees_by_path()
        records: list[WorktreeRecord] = []
        for path in sorted(self.layout.worktrees_index_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                record = WorktreeRecord.from_dict(json.load(handle))
            records.append(self._hydrate_record(record, git_worktrees))
        return records

    def get_record(self, task_id: str) -> WorktreeRecord | None:
        path = self.record_path(task_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return WorktreeRecord.from_dict(json.load(handle))

    def remove(self, task_id: str, *, force: bool = False) -> WorktreeRecord:
        record = self.get_record(task_id)
        if record is None:
            raise WorktreeError(f"unknown managed worktree for task `{task_id}`")

        worktree_path = Path(record.path)
        if worktree_path.exists():
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            args.append(str(worktree_path))
            self._git(*args)

        metadata_path = self.record_path(task_id)
        if metadata_path.exists():
            metadata_path.unlink()

        return WorktreeRecord(
            task_id=record.task_id,
            branch=record.branch,
            path=record.path,
            base_ref=record.base_ref,
            created_at=record.created_at,
            head=record.head,
            attached=False,
            path_exists=False,
        )

    def branch_name(self, task_id: str) -> str:
        return f"task/{_worktree_key(task_id)}"

    def worktree_path(self, task_id: str, *, title: str = "") -> Path:
        key = _worktree_key(task_id)
        suffix = _slug(title) if title else ""
        directory_name = f"wt-{key}"
        if suffix:
            directory_name += f"-{suffix}"
        return self.layout.worktrees_dir / directory_name

    def record_path(self, task_id: str) -> Path:
        return self.layout.worktrees_index_dir / f"{_worktree_key(task_id)}.json"

    def _ensure_git_workspace(self) -> None:
        root = self._git("rev-parse", "--show-toplevel").strip()
        git_root = Path(root).resolve()
        if git_root != self.workspace_root:
            raise WorktreeError(
                f"workspace root {self.workspace_root} is not the git toplevel {git_root}"
            )

    def _hydrate_record(
        self,
        record: WorktreeRecord,
        git_worktrees: dict[str, dict[str, str]],
    ) -> WorktreeRecord:
        details = git_worktrees.get(record.path, {})
        worktree_path = Path(record.path)
        return WorktreeRecord(
            task_id=record.task_id,
            branch=record.branch,
            path=record.path,
            base_ref=record.base_ref,
            created_at=record.created_at,
            head=details.get("head"),
            attached=record.path in git_worktrees,
            path_exists=worktree_path.exists(),
        )

    def _branch_exists(self, branch: str) -> bool:
        completed = subprocess.run(
            ["git", "-C", str(self.workspace_root), "show-ref", "--verify", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return completed.returncode == 0

    def _git_worktrees_by_path(self) -> dict[str, dict[str, str]]:
        output = self._git("worktree", "list", "--porcelain")
        current: dict[str, str] | None = None
        parsed: dict[str, dict[str, str]] = {}

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                if current and "path" in current:
                    parsed[current["path"]] = current
                current = None
                continue

            if current is None:
                current = {}

            if line.startswith("worktree "):
                current["path"] = str(Path(line[9:]).resolve())
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch_ref"] = line[7:]

        if current and "path" in current:
            parsed[current["path"]] = current

        return {
            path: details
            for path, details in parsed.items()
            if Path(path).is_relative_to(self.layout.worktrees_dir)
        }

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.workspace_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        if completed.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed (exit={completed.returncode})\n{output}"
            )
        return output

    @staticmethod
    def _write_record(path: Path, record: WorktreeRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)


def _worktree_key(task_id: str) -> str:
    stripped = task_id.strip()
    if not stripped:
        raise WorktreeError("task_id must not be empty")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stripped).strip("._-") or "task"
    safe = safe[:48].rstrip("._-") or "task"
    digest = hashlib.sha1(stripped.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("._-")
    cleaned = cleaned.lower()
    return cleaned[:32]
