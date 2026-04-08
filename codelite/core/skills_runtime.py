from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codelite.core.delivery import DeliveryQueue
from codelite.core.memory_runtime import MemoryRuntime
from codelite.core.todo import TodoManager
from codelite.storage.events import RuntimeLayout, utc_now
from codelite.storage.sessions import SessionStore


@dataclass(frozen=True)
class SkillSpec:
    name: str
    summary: str
    prompt_hint: str
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "prompt_hint": self.prompt_hint,
            "body": self.body,
        }


class SkillRuntime:
    BUILTIN_SKILLS = {
        "code-review": SkillSpec(
            name="code-review",
            summary="Find bugs, regressions, and missing tests before summarizing code changes.",
            prompt_hint="Prioritize findings first, then short summary.",
            body="Review code with a bug-finding mindset and call out risks with file references.",
        ),
        "debug": SkillSpec(
            name="debug",
            summary="Isolate the failing path, capture repro data, then patch with a regression test.",
            prompt_hint="Show repro, root cause, fix, and validation.",
            body="When debugging, preserve a minimal reproduction and keep iteration tight.",
        ),
        "documentation": SkillSpec(
            name="documentation",
            summary="Update docs and acceptance notes whenever behavior changes.",
            prompt_hint="State user-visible behavior, commands, and expected outputs.",
            body="Favor practical commands, expected results, and follow-up notes.",
        ),
    }

    def __init__(
        self,
        *,
        layout: RuntimeLayout,
        session_store: SessionStore,
        todo_manager: TodoManager,
        delivery_queue: DeliveryQueue,
        memory_runtime: MemoryRuntime | None = None,
        nag_after_steps: int = 3,
    ) -> None:
        self.layout = layout
        self.session_store = session_store
        self.todo_manager = todo_manager
        self.delivery_queue = delivery_queue
        self.memory_runtime = memory_runtime
        self.nag_after_steps = nag_after_steps

    def load_skill(self, name: str) -> SkillSpec:
        if name not in self.BUILTIN_SKILLS:
            raise KeyError(f"unknown skill `{name}`")
        skill = self.BUILTIN_SKILLS[name]
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="skill",
                text=skill.summary,
                metadata={"skill_name": skill.name},
            )
        return skill

    def maybe_todo_nag(self, session_id: str, step: int) -> str | None:
        if step < self.nag_after_steps:
            return None
        snapshot = self.todo_manager.get(session_id)
        if snapshot is None:
            return "Reminder: keep the todo plan updated before taking more actions."
        events = self.session_store.replay(session_id)
        todo_updates = [
            event
            for event in events
            if event.get("event_type") == "todo_updated"
            and (event.get("payload") or {}).get("source") != "auto"
        ]
        if todo_updates:
            return None
        return "Reminder: update the todo list if the plan has changed or work has completed."

    def enqueue_background_task(
        self,
        *,
        name: str,
        payload: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        item = self.delivery_queue.enqueue(
            "background_task",
            {
                "name": name,
                "payload": payload,
                "session_id": session_id,
            },
        )
        return item.to_dict()

    def process_background_tasks(self, *, max_items: int | None = None) -> list[dict[str, Any]]:
        return self.delivery_queue.process_all({"background_task": self._handle_background_task}, max_items=max_items)

    def background_status(self) -> dict[str, Any]:
        return self.delivery_queue.status()

    def _handle_background_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "background-task"))
        session_id = payload.get("session_id")
        body = dict(payload.get("payload") or {})
        result = {
            "name": name,
            "session_id": session_id,
            "payload": body,
            "completed_at": utc_now(),
        }
        result_path = self.layout.background_results_dir / f"{name}-{result['completed_at'].replace(':', '').replace('.', '-')}.json"
        tmp_path = result_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(result_path)
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="background",
                text=f"{name} completed",
                metadata={"background_name": name, "session_id": session_id or ""},
                evidence=[{"result_path": str(result_path)}],
            )
        return {"result_path": str(result_path), "name": name}
