from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from codelite.core.delivery import DeliveryQueue
from codelite.core.memory_runtime import MemoryRuntime
from codelite.core.parallel_dispatcher import ParallelDispatcher
from codelite.core.todo import TodoManager
from codelite.storage.events import RuntimeLayout, utc_now
from codelite.storage.sessions import SessionStore


@dataclass(frozen=True)
class SkillSpec:
    name: str
    summary: str
    prompt_hint: str
    body: str
    source: str = "builtin"
    path: str = ""
    resources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "prompt_hint": self.prompt_hint,
            "body": self.body,
            "source": self.source,
            "path": self.path,
            "resources": self.resources,
        }


class SkillRuntime:
    VERSIONED_SKILL_PATTERN = re.compile(r"^(?P<base>.+)-(?P<v1>\d+)\.(?P<v2>\d+)\.(?P<v3>\d+)$")
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
        external_skill_dirs: list[str] | None = None,
    ) -> None:
        self.layout = layout
        self.session_store = session_store
        self.todo_manager = todo_manager
        self.delivery_queue = delivery_queue
        self.memory_runtime = memory_runtime
        self.nag_after_steps = nag_after_steps
        self.external_skill_dirs = self._build_external_skill_dirs(external_skill_dirs)

    def load_skill(self, name: str) -> SkillSpec:
        if name in self.BUILTIN_SKILLS:
            skill = self.BUILTIN_SKILLS[name]
            self._remember_skill(skill)
            return skill

        external = self._load_external_skill(name)
        if external is None:
            raise KeyError(f"unknown skill `{name}`")
        self._remember_skill(external)
        return external

    def list_skills(self) -> list[dict[str, Any]]:
        discovered: dict[str, SkillSpec] = {
            name: spec for name, spec in self.BUILTIN_SKILLS.items()
        }
        for skill_dir in self._discover_external_skill_dirs():
            spec = self._parse_external_skill_dir(skill_dir)
            if spec.name in discovered:
                continue
            discovered[spec.name] = spec
        return [spec.to_dict() for _, spec in sorted(discovered.items(), key=lambda item: item[0])]

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

    def process_background_tasks(
        self,
        *,
        max_items: int | None = None,
        workers: int | None = None,
    ) -> list[dict[str, Any]]:
        dispatcher = ParallelDispatcher(
            delivery_queue=self.delivery_queue,
            handlers={"background_task": self._handle_background_task},
        )
        return dispatcher.process(
            max_items=max_items,
            workers=workers,
            allowed_kinds={"background_task"},
            kind_reservations={"background_task": workers or self.delivery_queue.dispatcher_background_reserved_workers},
            worker_prefix="background",
        )

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

    def _remember_skill(self, skill: SkillSpec) -> None:
        if self.memory_runtime is None:
            return
        self.memory_runtime.remember(
            kind="skill",
            text=skill.summary,
            metadata={"skill_name": skill.name, "source": skill.source, "path": skill.path},
        )

    def _load_external_skill(self, name: str) -> SkillSpec | None:
        skill_dir = self._resolve_external_skill_dir(name)
        if skill_dir is None:
            return None
        return self._parse_external_skill_dir(skill_dir)

    def _resolve_external_skill_dir(self, name: str) -> Path | None:
        stripped = name.strip()
        if not stripped:
            return None

        candidate = Path(stripped)
        if candidate.exists():
            if candidate.is_file() and candidate.name == "SKILL.md":
                return candidate.parent.resolve()
            if candidate.is_dir() and (candidate / "SKILL.md").exists():
                return candidate.resolve()

        candidates: list[Path] = []
        versioned: list[tuple[tuple[int, int, int], str, Path]] = []
        for root in self.external_skill_dirs:
            if not root.exists():
                continue
            exact = root / stripped
            if exact.is_dir() and (exact / "SKILL.md").exists():
                candidates.append(exact.resolve())
                continue
            for child in root.iterdir():
                if not child.is_dir() or not (child / "SKILL.md").exists():
                    continue
                base, version = self._parse_skill_dir_name(child.name)
                if child.name == stripped:
                    candidates.append(child.resolve())
                    continue
                if base == stripped and version is not None:
                    versioned.append((version, child.name, child.resolve()))

        if candidates:
            candidates.sort(key=lambda item: item.name, reverse=True)
            return candidates[0]
        if versioned:
            versioned.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return versioned[0][2]
        return None

    def _discover_external_skill_dirs(self) -> list[Path]:
        latest_by_base: dict[str, tuple[tuple[int, int, int], str, Path]] = {}
        for root in self.external_skill_dirs:
            if not root.exists():
                continue
            for child in root.iterdir():
                if not child.is_dir() or not (child / "SKILL.md").exists():
                    continue
                base, version = self._parse_skill_dir_name(child.name)
                key = base or child.name
                rank = version if version is not None else (-1, -1, -1)
                prev = latest_by_base.get(key)
                current = (rank, child.name, child.resolve())
                if prev is None or (current[0], current[1]) > (prev[0], prev[1]):
                    latest_by_base[key] = current
        return [item[2] for item in sorted(latest_by_base.values(), key=lambda value: value[1])]

    def _parse_external_skill_dir(self, skill_dir: Path) -> SkillSpec:
        skill_file = skill_dir / "SKILL.md"
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        metadata, body = self._parse_skill_markdown(text)
        name = str(metadata.get("name") or self._parse_skill_dir_name(skill_dir.name)[0] or skill_dir.name)
        summary = str(metadata.get("description") or self._first_non_empty_line(body) or f"External skill {name}")
        prompt_hint = self._infer_prompt_hint(body=body, summary=summary)
        resources = {
            "scripts_dir": str(skill_dir / "scripts") if (skill_dir / "scripts").exists() else "",
            "references_dir": str(skill_dir / "references") if (skill_dir / "references").exists() else "",
            "assets_dir": str(skill_dir / "assets") if (skill_dir / "assets").exists() else "",
        }
        return SkillSpec(
            name=name,
            summary=summary,
            prompt_hint=prompt_hint,
            body=body,
            source="external",
            path=str(skill_dir),
            resources=resources,
        )

    @staticmethod
    def _parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---"):
            return {}, text.strip()
        lines = text.splitlines()
        if len(lines) < 3:
            return {}, text.strip()
        end_index: int | None = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end_index = index
                break
        if end_index is None:
            return {}, text.strip()
        frontmatter_text = "\n".join(lines[1:end_index])
        body = "\n".join(lines[end_index + 1 :]).strip()
        try:
            metadata = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, body

    @staticmethod
    def _first_non_empty_line(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                stripped = stripped.lstrip("#").strip()
            if stripped:
                return stripped
        return ""

    def _parse_skill_dir_name(self, name: str) -> tuple[str, tuple[int, int, int] | None]:
        match = self.VERSIONED_SKILL_PATTERN.fullmatch(name)
        if match is None:
            return name, None
        return (
            match.group("base"),
            (int(match.group("v1")), int(match.group("v2")), int(match.group("v3"))),
        )

    @staticmethod
    def _infer_prompt_hint(*, body: str, summary: str) -> str:
        first = SkillRuntime._first_non_empty_line(body)
        if first:
            return first[:160]
        return summary[:160]

    def _build_external_skill_dirs(self, configured_dirs: list[str] | None) -> list[Path]:
        paths: list[Path] = []
        if configured_dirs:
            paths.extend(Path(raw).expanduser().resolve() for raw in configured_dirs if raw.strip())
        env_dirs = os.environ.get("CODELITE_SKILLS_DIRS", "")
        if env_dirs:
            paths.extend(Path(raw).expanduser().resolve() for raw in env_dirs.split(os.pathsep) if raw.strip())
        paths.extend(
            [
                (self.layout.workspace_root / ".skills").resolve(),
                (Path.home() / ".agents" / "skills").resolve(),
                (Path.home() / ".codex" / "skills").resolve(),
            ]
        )
        deduped: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped
