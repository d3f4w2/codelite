from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelite.config import AppConfig
from codelite.core.context import ContextCompact
from codelite.core.heartbeat import HeartService
from codelite.core.llm import ModelClient
from codelite.core.loop import AgentLoop
from codelite.core.todo import TodoManager
from codelite.core.tools import ToolRouter
from codelite.core.worktree import WorktreeManager, WorktreeRecord
from codelite.storage.sessions import SessionStore
from codelite.storage.tasks import TaskRecord, TaskStore


@dataclass(frozen=True)
class TaskRunResult:
    task: TaskRecord
    worktree: WorktreeRecord
    session_id: str
    prompt: str
    answer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "worktree": self.worktree.to_dict(),
            "session_id": self.session_id,
            "prompt": self.prompt,
            "answer": self.answer,
        }


class TaskRunner:
    def __init__(
        self,
        *,
        workspace_root: Path,
        config: AppConfig,
        session_store: SessionStore,
        task_store: TaskStore,
        worktree_manager: WorktreeManager,
        model_client: ModelClient,
        todo_manager: TodoManager | None = None,
        context_manager: ContextCompact | None = None,
        heart_service: HeartService | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.config = config
        self.session_store = session_store
        self.task_store = task_store
        self.worktree_manager = worktree_manager
        self.model_client = model_client
        self.todo_manager = todo_manager
        self.context_manager = context_manager
        self.heart_service = heart_service

    def run(
        self,
        *,
        task_id: str,
        prompt: str,
        title: str = "",
        session_id: str | None = None,
        owner: str = "task_runner",
    ) -> TaskRunResult:
        lease = self.task_store.acquire_lease(task_id, owner=owner, title=title or task_id)

        try:
            worktree = self.worktree_manager.prepare(task_id, title=title or task_id)
            self.task_store.update_metadata(
                task_id,
                {
                    "worktree": {
                        "branch": worktree.branch,
                        "path": worktree.path,
                        "base_ref": worktree.base_ref,
                    }
                },
            )

            self.task_store.start_task(task_id, lease_id=lease.lease_id)
            current_session_id = session_id or self.session_store.new_session_id()
            self.task_store.update_metadata(
                task_id,
                {
                    "session_id": current_session_id,
                    "prompt": prompt,
                    "workspace_root": str(self.workspace_root),
                },
            )

            isolated_tool_router = ToolRouter(
                Path(worktree.path),
                self.config.runtime,
                todo_manager=self.todo_manager,
                heart_service=self.heart_service,
            )
            loop = AgentLoop(
                config=self.config,
                session_store=self.session_store,
                tool_router=isolated_tool_router,
                model_client=self.model_client,
                todo_manager=self.todo_manager,
                context_manager=self.context_manager,
                heart_service=self.heart_service,
            )
            answer = loop.run_turn(session_id=current_session_id, user_input=prompt)
            completed_task = self.task_store.complete_task(task_id, lease_id=lease.lease_id)
            completed_task = self.task_store.update_metadata(
                task_id,
                {
                    "session_id": current_session_id,
                    "prompt": prompt,
                    "last_answer_preview": answer[:200],
                    "result": "done",
                    "worktree": {
                        "branch": worktree.branch,
                        "path": worktree.path,
                        "base_ref": worktree.base_ref,
                    },
                },
            )
            return TaskRunResult(
                task=completed_task,
                worktree=self.worktree_manager.get_record(task_id) or worktree,
                session_id=current_session_id,
                prompt=prompt,
                answer=answer,
            )
        except Exception as exc:
            self.task_store.block_task(task_id, lease_id=lease.lease_id, reason=str(exc))
            self.task_store.update_metadata(
                task_id,
                {
                    "prompt": prompt,
                    "result": "blocked",
                    "last_error": str(exc),
                },
            )
            raise
