from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from codelite.config import LLMConfig
from codelite.core.llm import ModelClient, OpenAICompatibleClient
from codelite.core.memory_runtime import MemoryRuntime
from codelite.storage.events import RuntimeLayout, utc_now


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "reason": self.reason,
        }


class ModelRouter:
    def __init__(
        self,
        layout: RuntimeLayout,
        llm_config: LLMConfig,
        *,
        primary_client: ModelClient | None = None,
        memory_runtime: MemoryRuntime | None = None,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.llm_config = llm_config
        self.primary_client = primary_client
        self.memory_runtime = memory_runtime

    def select_profile(self, prompt: str) -> ModelProfile:
        lowered = prompt.lower()
        if any(token in lowered for token in ("review", "critique", "评审", "reviewer")):
            profile = ModelProfile("review", self._profile_model("review"), "prompt asks for review-style reasoning")
        elif len(prompt) > 180 or any(token in lowered for token in ("design", "architecture", "refactor", "复杂", "重构")):
            profile = ModelProfile("deep", self._profile_model("deep"), "prompt appears complex or architectural")
        else:
            profile = ModelProfile("fast", self._profile_model("fast"), "prompt fits the default fast path")
        self._audit({"event": "model_route", **profile.to_dict(), "prompt_preview": prompt[:120]})
        return profile

    def get_client(self, profile_name: str) -> ModelClient:
        if self.primary_client is not None:
            return self.primary_client
        config = replace(self.llm_config, model=self._profile_model(profile_name))
        return OpenAICompatibleClient(config)

    def fallback_profiles(self, preferred: str) -> list[str]:
        order = ["fast", "deep", "review"]
        return [profile for profile in order if profile != preferred]

    def _profile_model(self, profile_name: str) -> str:
        env_names = {
            "fast": "CODELITE_MODEL_FAST",
            "deep": "CODELITE_MODEL_DEEP",
            "review": "CODELITE_MODEL_REVIEW",
        }
        import os

        return os.environ.get(env_names[profile_name], self.llm_config.model)

    def _audit(self, payload: dict[str, Any]) -> None:
        with self.layout.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp_utc": utc_now(), **payload}, ensure_ascii=False))
            handle.write("\n")


class CriticRefiner:
    def __init__(
        self,
        layout: RuntimeLayout,
        *,
        memory_runtime: MemoryRuntime | None = None,
    ) -> None:
        self.layout = layout
        self.layout.ensure()
        self.memory_runtime = memory_runtime

    def review(self, *, prompt: str, answer: str) -> dict[str, Any]:
        findings: list[str] = []
        if len(answer.strip()) < 10:
            findings.append("answer is too short to be a reliable completion")
        if "TODO" in answer:
            findings.append("answer still contains TODO markers")
        if not any(token in answer.lower() for token in ("done", "implemented", "updated", "fixed", "completed")):
            findings.append("answer does not clearly state an outcome")
        result = {
            "reviewed_at": utc_now(),
            "prompt_preview": prompt[:120],
            "answer_preview": answer[:200],
            "findings": findings,
            "passed": not findings,
        }
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="review",
                text=answer[:200],
                metadata={"passed": result["passed"], "finding_count": len(findings)},
                evidence=[{"prompt_preview": prompt[:120]}],
            )
        return result

    def log_failure(self, *, kind: str, message: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "failure_id": utc_now().replace(":", "").replace(".", "-"),
            "kind": kind,
            "message": message,
            "metadata": dict(metadata or {}),
            "created_at": utc_now(),
        }
        with self.layout.critic_failures_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")
        if self.memory_runtime is not None:
            self.memory_runtime.remember(
                kind="failure",
                text=message,
                metadata={"failure_kind": kind, **dict(metadata or {})},
            )
        return payload

    def refine_rules(self) -> dict[str, Any]:
        failures = self._list_failures()
        grouped: dict[str, int] = {}
        for item in failures:
            grouped[item["kind"]] = grouped.get(item["kind"], 0) + 1
        rules = [
            {
                "failure_kind": kind,
                "count": count,
                "rule": self._rule_for_kind(kind),
            }
            for kind, count in sorted(grouped.items())
        ]
        payload = {
            "generated_at": utc_now(),
            "rule_count": len(rules),
            "rules": rules,
        }
        tmp_path = self.layout.critic_rules_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(self.layout.critic_rules_path)
        return payload

    def _list_failures(self) -> list[dict[str, Any]]:
        if not self.layout.critic_failures_path.exists():
            return []
        with self.layout.critic_failures_path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    @staticmethod
    def _rule_for_kind(kind: str) -> str:
        mapping = {
            "validation": "rerun validate pipeline before reporting completion",
            "tool": "inspect tool arguments and protected path rules before retrying",
            "retrieval": "prefer local docs/code retrieval before escalating externally",
        }
        return mapping.get(kind, "capture a minimal repro and add a regression test before retrying")
