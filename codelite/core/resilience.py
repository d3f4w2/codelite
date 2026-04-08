from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codelite.core.context import ContextCompact
from codelite.core.llm import ModelClient, ModelResult
from codelite.core.model_router import ModelRouter


@dataclass(frozen=True)
class ResilienceAttempt:
    layer: str
    profile: str
    status: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "profile": self.profile,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class ResilienceResult:
    profile: str
    attempts: list[ResilienceAttempt]
    result: ModelResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "result": {
                "text": self.result.text,
                "tool_call_count": len(self.result.tool_calls),
                "usage": self.result.usage or {},
            },
        }


class ResilienceRunner:
    def __init__(
        self,
        *,
        context_manager: ContextCompact | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self.context_manager = context_manager
        self.model_router = model_router

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        preferred_profile: str,
        primary_client: ModelClient,
        session_id: str | None = None,
    ) -> ResilienceResult:
        attempts: list[ResilienceAttempt] = []
        profiles = [preferred_profile]
        if self.model_router is not None:
            profiles.extend(self.model_router.fallback_profiles(preferred_profile))

        compacted_messages = list(messages)
        used_compaction = False
        last_error: Exception | None = None

        for profile in profiles:
            client = primary_client if profile == preferred_profile else (
                self.model_router.get_client(profile) if self.model_router is not None else primary_client
            )
            generic_retry_used = False
            auth_retry_used = False

            while True:
                try:
                    result = client.complete(compacted_messages, tools)
                    attempts.append(ResilienceAttempt(layer="complete", profile=profile, status="ok"))
                    return ResilienceResult(profile=profile, attempts=attempts, result=result)
                except Exception as exc:
                    message = str(exc)
                    last_error = exc
                    if self._is_auth_error(message) and not auth_retry_used:
                        auth_retry_used = True
                        attempts.append(ResilienceAttempt(layer="auth_rotation", profile=profile, status="retry", error=message))
                        continue
                    if self._is_context_overflow(message) and not used_compaction and self.context_manager is not None and session_id is not None:
                        compacted_messages = self.context_manager.prepare(session_id, compacted_messages)
                        used_compaction = True
                        attempts.append(ResilienceAttempt(layer="overflow_compaction", profile=profile, status="retry", error=message))
                        continue
                    if not generic_retry_used:
                        generic_retry_used = True
                        attempts.append(ResilienceAttempt(layer="generic_retry", profile=profile, status="retry", error=message))
                        continue
                    attempts.append(ResilienceAttempt(layer="fallback", profile=profile, status="failed", error=message))
                    break

        if last_error is None:
            raise RuntimeError("resilience runner failed without an underlying error")
        raise last_error

    @staticmethod
    def _is_auth_error(message: str) -> bool:
        lowered = message.lower()
        return "auth_error" in lowered or "401" in lowered or "403" in lowered or "api key" in lowered

    @staticmethod
    def _is_context_overflow(message: str) -> bool:
        lowered = message.lower()
        return "context_overflow" in lowered or "maximum context" in lowered or "token limit" in lowered
