from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from codelite.storage.events import RuntimeLayout, utc_now

from .ledger import MemoryEntry


class MemoryViews:
    def __init__(self, layout: RuntimeLayout) -> None:
        self.layout = layout
        self.layout.ensure()

    def refresh(self, entries: list[MemoryEntry]) -> dict[str, str]:
        timeline = [
            {
                "entry_id": entry.entry_id,
                "kind": entry.kind,
                "text": entry.text,
                "created_at": entry.created_at,
                "metadata": entry.metadata,
                "evidence": entry.evidence,
            }
            for entry in entries
        ]
        keywords: dict[str, list[str]] = {}
        skills: dict[str, list[str]] = {}
        for entry in entries:
            for keyword in self._keywords(entry.text):
                keywords.setdefault(keyword, []).append(entry.entry_id)
            skill_name = entry.metadata.get("skill_name")
            if isinstance(skill_name, str) and skill_name:
                skills.setdefault(skill_name, []).append(entry.entry_id)

        timeline_path = self._write_view("timeline.json", {"generated_at": utc_now(), "items": timeline})
        keyword_path = self._write_view("keywords.json", {"generated_at": utc_now(), "index": keywords})
        skills_path = self._write_view("skills.json", {"generated_at": utc_now(), "index": skills})
        return {
            "timeline": str(timeline_path),
            "keywords": str(keyword_path),
            "skills": str(skills_path),
        }

    def read_timeline(self) -> dict[str, Any]:
        return self._read_view("timeline.json")

    def read_keywords(self) -> dict[str, Any]:
        return self._read_view("keywords.json")

    def read_skills(self) -> dict[str, Any]:
        return self._read_view("skills.json")

    def _write_view(self, filename: str, payload: dict[str, Any]) -> Path:
        path = self.layout.memory_views_dir / filename
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        return path

    def _read_view(self, filename: str) -> dict[str, Any]:
        path = self.layout.memory_views_dir / filename
        if not path.exists():
            return {"generated_at": None}
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _keywords(text: str) -> list[str]:
        return sorted(
            {
                token.lower()
                for token in re.findall(r"[A-Za-z0-9_]{3,}", text)
                if token.lower() not in {"the", "and", "with", "from", "into", "this"}
            }
        )
