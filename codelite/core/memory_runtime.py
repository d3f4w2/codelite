from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from codelite.config import RuntimeConfig
from codelite.memory import MemoryLedger, MemoryPolicy, MemoryViews


@dataclass(frozen=True)
class MemoryContextSource:
    source: str
    title: str
    content: str
    path: str | None = None
    max_chars: int = 800


@dataclass(frozen=True)
class MemoryFileSpec:
    key: str
    path: str
    title: str
    max_chars: int
    editable: bool = True


class MemoryRuntime:
    PREF_START = "<!-- CODELITE:MANAGED_PREFS START -->"
    PREF_END = "<!-- CODELITE:MANAGED_PREFS END -->"
    CORE_FILE_KEYS = {"agent", "user", "soul", "tool", "long_memory"}
    CORE_TITLES_ZH = {
        "agent": "Agent \u5408\u7ea6",
        "user": "\u7528\u6237\u753b\u50cf",
        "soul": "Agent \u7075\u9b42",
        "tool": "\u5de5\u5177\u7b56\u7565",
        "long_memory": "\u957f\u671f\u8bb0\u5fc6",
    }
    CORE_TITLES_LEGACY = {
        "agent": {"Agent Spec"},
        "user": {"User Profile"},
        "soul": {"Agent Soul"},
        "tool": {"Tool Policy"},
        "long_memory": {"Long-Term Memory"},
    }

    def __init__(
        self,
        ledger: MemoryLedger,
        views: MemoryViews,
        policy: MemoryPolicy,
        runtime_config: RuntimeConfig | None = None,
    ) -> None:
        self.ledger = ledger
        self.views = views
        self.policy = policy
        self.runtime_config = runtime_config
        self.workspace_root = self.ledger.layout.workspace_root.resolve()
        self.manifest_path = self._resolve_manifest_path(
            (runtime_config.memory_manifest_path if runtime_config is not None else "runtime/memory/manifest.json")
        )
        whitelist = list(runtime_config.memory_files_whitelist) if runtime_config is not None else []
        self.memory_files_whitelist = self._normalize_whitelist(whitelist)
        self.candidate_enabled = bool(runtime_config.memory_candidate_enabled) if runtime_config is not None else True
        self.candidate_max_per_turn = (
            max(1, int(runtime_config.memory_candidate_max_per_turn))
            if runtime_config is not None
            else 1
        )
        self._lock = threading.RLock()

    def remember(
        self,
        *,
        kind: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if not self.policy.should_write(kind=kind, text=text):
                return None
            entry = self.ledger.append(kind=kind, text=text, metadata=metadata, evidence=evidence)
            view_paths = self.views.refresh(self.ledger.list_entries())
            return {
                "entry": entry.to_dict(),
                "views": view_paths,
            }

    def timeline(self) -> dict[str, Any]:
        with self._lock:
            return self.views.read_timeline()

    def keywords(self) -> dict[str, Any]:
        with self._lock:
            return self.views.read_keywords()

    def skills(self) -> dict[str, Any]:
        with self._lock:
            return self.views.read_skills()

    def bootstrap_memory_files(self) -> dict[str, Any]:
        with self._lock:
            manifest_exists = self.manifest_path.exists()
            payload = self._load_manifest_payload()
            normalized = self._normalize_manifest_payload(payload)
            migrated_manifest_titles = self._migrate_manifest_titles(normalized)
            created_files: list[str] = []
            changed = (not manifest_exists) or normalized != payload

            specs = self._manifest_specs(normalized)
            for spec in specs:
                file_path = self._resolve_workspace_path(spec.path)
                if file_path is None:
                    continue
                if file_path.exists():
                    continue
                template = self._default_template(spec)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(template, encoding="utf-8")
                created_files.append(str(file_path))
                changed = True

            migrated_files = self._migrate_legacy_templates(specs)
            if migrated_files:
                changed = True

            if changed:
                self._save_manifest_payload(normalized)

            return {
                "manifest_path": str(self.manifest_path),
                "created_files": created_files,
                "migrated_files": migrated_files,
                "migrated_manifest_titles": migrated_manifest_titles,
                "file_count": len(specs),
            }

    def memory_file_specs(self) -> list[MemoryFileSpec]:
        with self._lock:
            payload = self._normalize_manifest_payload(self._load_manifest_payload())
            return self._manifest_specs(payload)

    def memory_files(self, *, include_preview: bool = True) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for spec in self.memory_file_specs():
            path = self._resolve_workspace_path(spec.path)
            if path is None:
                continue
            preview = ""
            if include_preview and path.exists():
                preview = self._file_preview(path)
            items.append(
                {
                    "key": spec.key,
                    "path": str(path),
                    "title": spec.title,
                    "editable": spec.editable,
                    "max_chars": spec.max_chars,
                    "exists": path.exists(),
                    "preview": preview,
                }
            )
        return items

    def open_memory_file(self, ref: str) -> Path:
        normalized_ref = ref.strip().lower()
        if not normalized_ref:
            normalized_ref = "agent"
        alias = {
            "profile": "user",
            "preferences": "user",
            "style": "soul",
            "tools": "tool",
            "memory": "long_memory",
        }
        normalized_ref = alias.get(normalized_ref, normalized_ref)
        self.bootstrap_memory_files()
        for spec in self.memory_file_specs():
            if normalized_ref not in {spec.key.lower(), spec.path.lower(), Path(spec.path).name.lower()}:
                continue
            target = self._resolve_workspace_path(spec.path)
            if target is None:
                break
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self._default_template(spec), encoding="utf-8")
            return target
        raise RuntimeError(f"unknown memory file `{ref}`")

    def remember_preference(self, *, domain: str, text: str, source: str = "manual") -> dict[str, Any]:
        normalized_text = text.strip()
        if not normalized_text:
            raise RuntimeError("preference text must not be empty")
        spec = self._domain_spec(domain)
        target = self.open_memory_file(spec.key)
        items = self._managed_pref_items(target)
        lowered = {item.lower() for item in items}
        added = normalized_text.lower() not in lowered
        if added:
            items.append(normalized_text)
            self._write_managed_pref_items(target, items)
        self.remember(
            kind="memory_file_update",
            text=f"{spec.key}: {normalized_text}",
            metadata={
                "action": "remember",
                "domain": spec.key,
                "path": str(target),
                "source": source,
                "added": added,
            },
        )
        return {
            "domain": spec.key,
            "path": str(target),
            "text": normalized_text,
            "added": added,
        }

    def forget_preference(self, *, domain: str, keyword: str, source: str = "manual") -> dict[str, Any]:
        needle = keyword.strip().lower()
        if not needle:
            raise RuntimeError("forget keyword must not be empty")
        spec = self._domain_spec(domain)
        target = self.open_memory_file(spec.key)
        current = self._managed_pref_items(target)
        kept: list[str] = []
        removed: list[str] = []
        for item in current:
            if needle in item.lower():
                removed.append(item)
            else:
                kept.append(item)
        if removed:
            self._write_managed_pref_items(target, kept)
        self.remember(
            kind="memory_file_update",
            text=f"{spec.key}: {needle}",
            metadata={
                "action": "forget",
                "domain": spec.key,
                "path": str(target),
                "source": source,
                "removed_count": len(removed),
            },
            evidence=[{"removed": item} for item in removed[:5]],
        )
        return {
            "domain": spec.key,
            "path": str(target),
            "keyword": keyword.strip(),
            "removed_count": len(removed),
            "removed": removed,
        }

    def effective_preferences(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for spec in self.memory_file_specs():
            path = self._resolve_workspace_path(spec.path)
            if path is None or not path.exists():
                continue
            for index, item in enumerate(self._managed_pref_items(path), start=1):
                key = ""
                value = item
                if ":" in item:
                    left, right = item.split(":", 1)
                    key = left.strip()
                    value = right.strip()
                results.append(
                    {
                        "domain": spec.key,
                        "source_file": str(path),
                        "index": index,
                        "text": item,
                        "key": key,
                        "value": value,
                    }
                )
        return results
    def suggest_candidate(self, prompt: str) -> dict[str, Any] | None:
        if not self.candidate_enabled:
            return None
        text = prompt.strip()
        if not text or text.startswith("/"):
            return None
        lowered = text.lower()
        style_signals = (
            "回答风格",
            "回复风格",
            "写作风格",
            "语气",
            "口吻",
            "表达方式",
            "简洁一点",
            "简短一点",
            "详细一点",
            "专业一点",
            "直接一点",
            "正式一点",
            "口语一点",
            "response style",
            "reply style",
            "writing style",
            "be concise",
            "more concise",
            "more detailed",
            "be direct",
            "tone",
        )
        preference_signals = (
            "记住",
            "以后",
            "默认",
            "偏好",
            "我喜欢",
            "我不喜欢",
            "我希望",
            "请始终",
            "请总是",
            "优先",
            "尽量",
            "remember",
            "from now on",
            "default",
            "preference",
            "i like",
            "i don\'t like",
            "i prefer",
            "please always",
            "prefer",
        )
        if any(token in text for token in style_signals) or any(token in lowered for token in style_signals):
            return {
                "domain": "soul",
                "text": text,
                "confidence": 0.78,
                "reason": "heuristic_style_signal",
            }
        if any(token in text for token in preference_signals) or any(token in lowered for token in preference_signals):
            return {
                "domain": "user",
                "text": text,
                "confidence": 0.72,
                "reason": "heuristic_preference_signal",
            }
        return None

    def record_candidate(self, *, candidate: dict[str, Any], session_id: str) -> dict[str, Any] | None:
        text = str(candidate.get("text", "")).strip()
        if not text:
            return None
        return self.remember(
            kind="memory_candidate",
            text=text,
            metadata={
                "session_id": session_id,
                "domain": str(candidate.get("domain", "user")),
                "confidence": float(candidate.get("confidence", 0.0)),
                "reason": str(candidate.get("reason", "")),
                "status": "pending",
            },
        )

    def record_candidate_decision(
        self,
        *,
        candidate: dict[str, Any],
        session_id: str,
        accepted: bool,
    ) -> dict[str, Any] | None:
        text = str(candidate.get("text", "")).strip()
        if not text:
            return None
        return self.remember(
            kind="memory_decision",
            text=text,
            metadata={
                "session_id": session_id,
                "domain": str(candidate.get("domain", "user")),
                "accepted": accepted,
                "status": "accepted" if accepted else "rejected",
            },
        )

    def assemble_context(
        self,
        *,
        budget_chars: int = 3200,
        recent_days: int = 2,
        recent_note_limit: int = 2,
        ledger_snippet_limit: int = 6,
        today: date | None = None,
    ) -> dict[str, Any]:
        budget = max(400, int(budget_chars))
        report: dict[str, Any] = {
            "budget_chars": budget,
            "loaded_sources": [],
            "skipped_sources": [],
            "total_chars": 0,
        }

        preamble = (
            "Long-term memory context (budgeted, highest priority first).\n"
            "File-based memory (agent/user/soul/tool) is source of truth; ledger snippets are audit hints only."
        )
        sections: list[str] = [preamble]
        remaining = budget - len(preamble)

        for source in self._ordered_memory_sources(
            workspace_root=self.workspace_root,
            recent_days=recent_days,
            recent_note_limit=recent_note_limit,
            ledger_snippet_limit=ledger_snippet_limit,
            today=today,
        ):
            if remaining <= 0:
                report["skipped_sources"].append({"source": source.source, "reason": "budget_exhausted"})
                continue

            section_text, meta = self._render_section(source=source, remaining=remaining)
            if section_text is None:
                report["skipped_sources"].append({"source": source.source, "reason": str(meta.get("reason", "skipped"))})
                continue

            sections.append(section_text)
            used = len(section_text)
            remaining -= used
            report["loaded_sources"].append(
                {
                    "source": source.source,
                    "title": source.title,
                    "path": source.path,
                    "chars": used,
                    "truncated": bool(meta.get("truncated", False)),
                }
            )

        system_message_text = "\n\n".join(sections).strip()
        report["total_chars"] = len(system_message_text)
        return {
            "system_message_text": system_message_text,
            "report": report,
        }

    def _ordered_memory_sources(
        self,
        *,
        workspace_root: Path,
        recent_days: int,
        recent_note_limit: int,
        ledger_snippet_limit: int,
        today: date | None,
    ) -> list[MemoryContextSource]:
        sources: list[MemoryContextSource] = []

        for spec in self.memory_file_specs():
            path = self._resolve_workspace_path(spec.path)
            if path is None or not path.exists():
                continue
            content = self._read_text(path)
            if not content:
                continue
            source_name = self._source_name_for_spec(spec.key)
            sources.append(
                MemoryContextSource(
                    source=source_name,
                    title=spec.title,
                    content=content,
                    path=str(path),
                    max_chars=spec.max_chars,
                )
            )

        for note in self._recent_memory_notes(
            workspace_root=workspace_root,
            recent_days=recent_days,
            note_limit=recent_note_limit,
            today=today,
        ):
            content = self._read_text(note)
            if not content:
                continue
            sources.append(
                MemoryContextSource(
                    source=f"memory_note:{note.name}",
                    title=f"Recent Memory Note ({note.name})",
                    content=content,
                    path=str(note),
                    max_chars=560,
                )
            )

        snippets = self._experience_snippets(limit=ledger_snippet_limit)
        if snippets:
            sources.append(
                MemoryContextSource(
                    source="ledger_experience",
                    title="Experience Snippets (Audit, Lower Priority)",
                    content="\n".join(f"- {line}" for line in snippets),
                    path=str(self.ledger.layout.memory_ledger_path),
                    max_chars=900,
                )
            )
        return sources

    def _experience_snippets(self, *, limit: int) -> list[str]:
        allowed = {"experience", "preference", "reflection_rule", "memory_file_update"}
        lines: list[str] = []
        for entry in reversed(self.ledger.list_entries()):
            if entry.kind not in allowed:
                continue
            text = " ".join(entry.text.split())
            if not text:
                continue
            lines.append(f"[{entry.kind}] {text[:220]}")
            if len(lines) >= max(1, limit):
                break
        return lines

    def _recent_memory_notes(
        self,
        *,
        workspace_root: Path,
        recent_days: int,
        note_limit: int,
        today: date | None,
    ) -> list[Path]:
        pattern = re.compile(r"^memory-(\d{8}|\d{4}-\d{2}-\d{2})(?:\.md)?$", flags=re.IGNORECASE)
        current = today or datetime.now(timezone.utc).date()
        candidates: list[tuple[date, Path]] = []
        for path in sorted(workspace_root.glob("memory-*")):
            match = pattern.match(path.name)
            if not match:
                continue
            parsed = self._parse_note_date(match.group(1))
            if parsed is None:
                continue
            delta_days = (current - parsed).days
            if delta_days < 0 or delta_days > max(0, int(recent_days)):
                continue
            candidates.append((parsed, path))
        candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        return [item[1] for item in candidates[: max(1, int(note_limit))]]

    @staticmethod
    def _parse_note_date(raw: str) -> date | None:
        text = raw.strip()
        try:
            if "-" in text:
                return datetime.strptime(text, "%Y-%m-%d").date()
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    @staticmethod
    def _render_section(*, source: MemoryContextSource, remaining: int) -> tuple[str | None, dict[str, Any]]:
        if not source.content.strip():
            return None, {"reason": "empty"}
        section_limit = min(max(80, source.max_chars), max(0, remaining))
        header = f"## {source.title}\n"
        body_budget = section_limit - len(header)
        if body_budget < 32:
            return None, {"reason": "budget_exhausted"}
        body = source.content.strip()
        truncated = False
        if len(body) > body_budget:
            truncated = True
            clip_budget = max(3, body_budget - 3)
            body = body[:clip_budget].rstrip() + "..."
        return header + body, {"truncated": truncated}

    @staticmethod
    def _normalize_whitelist(paths: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        defaults = ["agent.md", "user.md", "soul.md", "tool.md", "Memory.md"]
        for item in [*defaults, *paths]:
            stripped = item.strip()
            if not stripped:
                continue
            key = stripped.lower().replace("\\", "/")
            if key in seen:
                continue
            seen.add(key)
            normalized.append(stripped)
        return normalized

    def _resolve_manifest_path(self, raw: str) -> Path:
        candidate = Path(raw.strip() or "runtime/memory/manifest.json")
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.workspace_root / candidate).resolve()
        return resolved

    def _load_manifest_payload(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return self._default_manifest_payload()
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return self._default_manifest_payload()
        if not isinstance(payload, dict):
            return self._default_manifest_payload()
        return payload

    def _save_manifest_payload(self, payload: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(self.manifest_path)

    def _default_manifest_payload(self) -> dict[str, Any]:
        files = [
            {"key": "agent", "path": "agent.md", "title": "Agent 合约", "max_chars": 1100, "editable": True},
            {"key": "user", "path": "user.md", "title": "用户画像", "max_chars": 900, "editable": True},
            {"key": "soul", "path": "soul.md", "title": "Agent 灵魂", "max_chars": 760, "editable": True},
            {"key": "tool", "path": "tool.md", "title": "工具策略", "max_chars": 760, "editable": True},
            {"key": "long_memory", "path": "Memory.md", "title": "长期记忆", "max_chars": 900, "editable": True},
        ]
        for extra in self.memory_files_whitelist:
            if any(item["path"].lower() == extra.lower() for item in files):
                continue
            stem = Path(extra).stem
            files.append(
                {
                    "key": self._normalize_key(stem),
                    "path": extra,
                    "title": f"记忆文件 ({Path(extra).name})",
                    "max_chars": 640,
                    "editable": True,
                }
            )
        return {"version": 1, "files": files}

    def _normalize_manifest_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_files = payload.get("files")
        rows = raw_files if isinstance(raw_files, list) else []
        files: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        seen_paths: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = str(row.get("path", "")).strip()
            if not path:
                continue
            key = self._normalize_key(str(row.get("key", "")).strip() or Path(path).stem)
            title = str(row.get("title", "")).strip() or f"记忆文件 ({Path(path).name})"
            max_chars = int(row.get("max_chars", 640) or 640)
            editable = bool(row.get("editable", True))
            low_path = path.lower()
            if key in seen_keys or low_path in seen_paths:
                continue
            seen_keys.add(key)
            seen_paths.add(low_path)
            files.append(
                {
                    "key": key,
                    "path": path,
                    "title": title,
                    "max_chars": max(120, max_chars),
                    "editable": editable,
                }
            )
        for extra in self.memory_files_whitelist:
            low_extra = extra.lower()
            if low_extra in seen_paths:
                continue
            files.append(
                {
                    "key": self._normalize_key(Path(extra).stem),
                    "path": extra,
                    "title": f"记忆文件 ({Path(extra).name})",
                    "max_chars": 640,
                    "editable": True,
                }
            )
            seen_paths.add(low_extra)
        return {"version": 1, "files": files}

    def _migrate_manifest_titles(self, payload: dict[str, Any]) -> list[str]:
        migrated: list[str] = []
        files = payload.get("files")
        if not isinstance(files, list):
            return migrated
        for row in files:
            if not isinstance(row, dict):
                continue
            key = self._normalize_key(str(row.get("key", "")).strip())
            target = self.CORE_TITLES_ZH.get(key)
            if target is None:
                continue
            title = str(row.get("title", "")).strip()
            legacy_titles = self.CORE_TITLES_LEGACY.get(key, set())
            if title not in legacy_titles:
                continue
            if title == target:
                continue
            row["title"] = target
            migrated.append(str(row.get("path", key)))
        return migrated

    def _manifest_specs(self, payload: dict[str, Any]) -> list[MemoryFileSpec]:
        specs: list[MemoryFileSpec] = []
        for row in payload.get("files", []):
            if not isinstance(row, dict):
                continue
            specs.append(
                MemoryFileSpec(
                    key=str(row.get("key", "")).strip(),
                    path=str(row.get("path", "")).strip(),
                    title=str(row.get("title", "")).strip(),
                    max_chars=int(row.get("max_chars", 640) or 640),
                    editable=bool(row.get("editable", True)),
                )
            )
        return specs

    def _domain_spec(self, domain: str) -> MemoryFileSpec:
        normalized = domain.strip().lower()
        alias = {
            "agent": "agent",
            "system": "agent",
            "user": "user",
            "profile": "user",
            "soul": "soul",
            "style": "soul",
            "tool": "tool",
            "tools": "tool",
            "memory": "long_memory",
            "long_memory": "long_memory",
        }.get(normalized, "user")
        for spec in self.memory_file_specs():
            if spec.key == alias:
                return spec
        raise RuntimeError(f"memory domain not configured: {domain}")

    @staticmethod
    def _normalize_key(raw: str) -> str:
        stripped = raw.strip().lower()
        normalized = re.sub(r"[^a-z0-9_]+", "_", stripped).strip("_")
        return normalized or "memory"

    def _resolve_workspace_path(self, raw_path: str) -> Path | None:
        raw = raw_path.strip()
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except Exception:
            return None
        return resolved

    @staticmethod
    def _file_preview(path: Path, *, max_chars: int = 96) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if len(stripped) > max_chars:
                return stripped[: max_chars - 3] + "..."
            return stripped
        return ""

    def _default_template(self, spec: MemoryFileSpec) -> str:
        if spec.key == "agent":
            return (
                "# Agent 合约\n\n"
                "## 角色\n"
                "- 在此定义系统级约束与任务导航。\n\n"
                "## 导航\n"
                "- user.md：用户偏好\n"
                "- soul.md：回复风格与原则\n"
                "- tool.md：工具使用策略\n\n"
                "## 托管偏好\n"
                f"{self.PREF_START}\n"
                "- 输出尽量简洁、可执行。\n"
                f"{self.PREF_END}\n"
            )
        if spec.key == "user":
            return (
                "# 用户画像\n\n"
                "## 稳定偏好\n"
                f"{self.PREF_START}\n"
                "- 语言：中文\n"
                f"{self.PREF_END}\n"
            )
        if spec.key == "soul":
            return (
                "# Agent 灵魂\n\n"
                "## 工作风格\n"
                f"{self.PREF_START}\n"
                "- 直接、务实、基于事实。\n"
                f"{self.PREF_END}\n"
            )
        if spec.key == "tool":
            return (
                "# 工具策略\n\n"
                "## 工具偏好\n"
                f"{self.PREF_START}\n"
                "- 优先使用确定性命令与显式路径。\n"
                f"{self.PREF_END}\n"
            )
        return (
            f"# {spec.title}\n\n"
            "## 托管偏好\n"
            f"{self.PREF_START}\n"
            f"{self.PREF_END}\n"
        )

    def _migrate_legacy_templates(self, specs: list[MemoryFileSpec]) -> list[str]:
        migrated: list[str] = []
        for spec in specs:
            if spec.key not in self.CORE_FILE_KEYS:
                continue
            path = self._resolve_workspace_path(spec.path)
            if path is None or not path.exists():
                continue
            raw = self._read_text(path)
            if not raw:
                continue
            if not self._matches_legacy_template(spec.key, raw):
                continue
            items = [self._translate_legacy_pref_item(item) for item in self._managed_pref_items(path)]
            path.write_text(self._default_template(spec), encoding="utf-8")
            if items:
                self._write_managed_pref_items(path, items)
            migrated.append(str(path))
        return migrated

    def _matches_legacy_template(self, key: str, text: str) -> bool:
        current = self._template_skeleton(text)
        if not current:
            return False
        for legacy in self._legacy_template_variants(key):
            if self._template_skeleton(legacy) == current:
                return True
        return False

    def _legacy_template_variants(self, key: str) -> list[str]:
        if key == "agent":
            return [
                (
                    "# Agent Contract\n\n"
                    "## Role\n"
                    "- Define primary system constraints and task navigation here.\n\n"
                    "## Navigation\n"
                    "- user.md: user preferences\n"
                    "- soul.md: response style and principles\n"
                    "- tool.md: tool usage policies\n\n"
                    "## Managed Preferences\n"
                    f"{self.PREF_START}\n"
                    "- Keep outputs concise and actionable.\n"
                    f"{self.PREF_END}\n"
                )
            ]
        if key == "user":
            return [
                (
                    "# User Profile\n\n"
                    "## Stable Preferences\n"
                    f"{self.PREF_START}\n"
                    "- Language: 涓枃\n"
                    f"{self.PREF_END}\n"
                ),
                (
                    "# User Profile\n\n"
                    "## Stable Preferences\n"
                    f"{self.PREF_START}\n"
                    "- Language: 中文\n"
                    f"{self.PREF_END}\n"
                ),
            ]
        if key == "soul":
            return [
                (
                    "# Agent Soul\n\n"
                    "## Working Style\n"
                    f"{self.PREF_START}\n"
                    "- Be direct, pragmatic, and factual.\n"
                    f"{self.PREF_END}\n"
                )
            ]
        if key == "tool":
            return [
                (
                    "# Tool Policy\n\n"
                    "## Tool Preferences\n"
                    f"{self.PREF_START}\n"
                    "- Prefer deterministic commands and explicit file paths.\n"
                    f"{self.PREF_END}\n"
                )
            ]
        if key == "long_memory":
            return [
                (
                    "# Long-Term Memory\n\n"
                    "## Managed Preferences\n"
                    f"{self.PREF_START}\n"
                    f"{self.PREF_END}\n"
                )
            ]
        return []

    def _template_skeleton(self, text: str) -> str:
        normalized = text.replace("\r\n", "\n").strip()
        if not normalized:
            return ""
        pattern = re.compile(
            re.escape(self.PREF_START) + r"(.*?)" + re.escape(self.PREF_END),
            flags=re.DOTALL,
        )
        return pattern.sub(f"{self.PREF_START}\n{self.PREF_END}", normalized)

    @staticmethod
    def _translate_legacy_pref_item(item: str) -> str:
        mapping = {
            "Keep outputs concise and actionable.": "输出尽量简洁、可执行。",
            "Language: 涓枃": "语言：中文",
            "Language: 中文": "语言：中文",
            "Be direct, pragmatic, and factual.": "直接、务实、基于事实。",
            "Prefer deterministic commands and explicit file paths.": "优先使用确定性命令与显式路径。",
        }
        return mapping.get(item.strip(), item)

    def _managed_pref_items(self, path: Path) -> list[str]:
        text = self._read_text(path)
        if not text:
            return []
        pattern = re.compile(
            re.escape(self.PREF_START) + r"(.*?)" + re.escape(self.PREF_END),
            flags=re.DOTALL,
        )
        match = pattern.search(text)
        if match is None:
            return []
        block = match.group(1)
        items: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("-"):
                value = stripped[1:].strip()
                if value:
                    items.append(value)
            else:
                items.append(stripped)
        return items

    def _write_managed_pref_items(self, path: Path, items: list[str]) -> None:
        original = self._read_text(path)
        if not original:
            try:
                relative_path = str(path.relative_to(self.workspace_root))
            except Exception:
                relative_path = path.name
            spec = next(
                (
                    item
                    for item in self.memory_file_specs()
                    if self._resolve_workspace_path(item.path) == path.resolve()
                ),
                MemoryFileSpec(
                    key=self._normalize_key(path.stem),
                    path=relative_path,
                    title=f"记忆文件 ({path.name})",
                    max_chars=640,
                    editable=True,
                ),
            )
            original = self._default_template(spec)
        block_lines = [self.PREF_START]
        block_lines.extend(f"- {item}" for item in items)
        block_lines.append(self.PREF_END)
        replacement = "\n".join(block_lines)
        pattern = re.compile(
            re.escape(self.PREF_START) + r"(.*?)" + re.escape(self.PREF_END),
            flags=re.DOTALL,
        )
        if pattern.search(original):
            updated = pattern.sub(replacement, original, count=1)
        else:
            updated = original.rstrip() + "\n\n## 托管偏好\n" + replacement + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated.rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _source_name_for_spec(key: str) -> str:
        mapping = {
            "agent": "agent_spec",
            "user": "user_profile",
            "soul": "soul",
            "tool": "tool_policy",
            "long_memory": "long_memory",
        }
        return mapping.get(key, f"memory_file:{key}")

