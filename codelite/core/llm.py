from __future__ import annotations

import inspect
import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from codelite.config import LLMConfig


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelResult:
    text: str
    tool_calls: list[ToolCallRequest]
    usage: dict[str, Any] | None = None


class ModelClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        request_timeout_sec: float | None = None,
    ) -> ModelResult:
        ...


class OpenAICompatibleClient:
    DEFAULT_HEADERS = {
        "Accept": "application/json",
        # Some OpenAI-compatible gateways are fronted by Cloudflare and block urllib's default UA.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0 Safari/537.36"
        ),
    }

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        request_timeout_sec: float | None = None,
    ) -> ModelResult:
        if not self.config.api_key:
            raise RuntimeError("LLM API key 未配置，请设置 CODELITE_LLM_API_KEY。")

        started_at = time.monotonic()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
            payload["tool_choice"] = "auto"

        data = self._call_with_optional_timeout(
            self._request,
            payload,
            request_timeout_sec=request_timeout_sec,
        )
        result = self._model_result_from_response(data)

        if not result.text.strip() and not result.tool_calls:
            fallback = self._call_with_optional_timeout(
                self._request_streaming_fallback,
                payload,
                request_timeout_sec=self._remaining_timeout(request_timeout_sec, started_at),
            )
            if fallback.text.strip() or fallback.tool_calls:
                return fallback
            raise RuntimeError(
                "LLM 返回了 200，但没有文本内容也没有 tool_calls。"
                "当前网关的 OpenAI 兼容输出可能不完整。"
            )

        return result

    def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        request_timeout_sec: float | None = None,
    ) -> ModelResult:
        if not self.config.api_key:
            raise RuntimeError("LLM API key 未配置，请设置 CODELITE_LLM_API_KEY。")

        started_at = time.monotonic()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
            payload["tool_choice"] = "auto"

        try:
            result = self._request_streaming(
                payload,
                on_event=on_event,
                request_timeout_sec=request_timeout_sec,
            )
        except RuntimeError:
            return self.complete(
                messages,
                tools,
                request_timeout_sec=self._remaining_timeout(request_timeout_sec, started_at),
            )

        if result.text.strip() or result.tool_calls:
            return result
        return self.complete(
            messages,
            tools,
            request_timeout_sec=self._remaining_timeout(request_timeout_sec, started_at),
        )

    def _request(
        self,
        payload: dict[str, Any],
        *,
        request_timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                **self.DEFAULT_HEADERS,
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._resolve_timeout(request_timeout_sec)) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 400 and self._requires_instructions(detail):
                normalized_payload = self._with_instructions(payload)
                if normalized_payload != payload:
                    return self._request(normalized_payload, request_timeout_sec=request_timeout_sec)
            raise RuntimeError(f"LLM 请求失败: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"LLM 请求失败: {exc.reason}") from exc

        return json.loads(body)

    def _request_streaming_fallback(
        self,
        payload: dict[str, Any],
        *,
        request_timeout_sec: float | None = None,
    ) -> ModelResult:
        return self._request_streaming(payload, request_timeout_sec=request_timeout_sec)

    def _request_streaming(
        self,
        payload: dict[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        request_timeout_sec: float | None = None,
    ) -> ModelResult:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url=url,
            data=json.dumps(stream_payload).encode("utf-8"),
            headers={
                **self.DEFAULT_HEADERS,
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        text_parts: list[str] = []
        tool_call_chunks: dict[int, dict[str, Any]] = {}
        usage_payload: dict[str, Any] | None = None

        try:
            with urllib.request.urlopen(request, timeout=self._resolve_timeout(request_timeout_sec)) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if isinstance(usage, dict) and usage:
                        usage_payload = dict(usage)
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            text_parts.append(content)
                            if on_event is not None:
                                try:
                                    on_event({"type": "text", "text": content})
                                except Exception:
                                    pass
                        for tool_call in delta.get("tool_calls") or []:
                            index = int(tool_call.get("index", 0))
                            entry = tool_call_chunks.setdefault(
                                index,
                                {
                                    "id": tool_call.get("id") or uuid.uuid4().hex,
                                    "name": "",
                                    "arguments": "",
                                },
                            )
                            if tool_call.get("id"):
                                entry["id"] = tool_call["id"]
                            function = tool_call.get("function") or {}
                            if function.get("name"):
                                entry["name"] = function["name"]
                            if function.get("arguments"):
                                entry["arguments"] += function["arguments"]
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 400 and self._requires_instructions(detail):
                normalized_payload = self._with_instructions(payload)
                if normalized_payload != payload:
                    return self._request_streaming(
                        normalized_payload,
                        on_event=on_event,
                        request_timeout_sec=request_timeout_sec,
                    )
            raise RuntimeError(f"LLM 流式请求失败: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"LLM 流式请求失败: {exc.reason}") from exc

        tool_calls: list[ToolCallRequest] = []
        for index in sorted(tool_call_chunks):
            item = tool_call_chunks[index]
            raw_arguments = item["arguments"] or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {"raw": raw_arguments}
            tool_calls.append(
                ToolCallRequest(
                    id=item["id"],
                    name=item["name"],
                    arguments=arguments,
                )
            )

        return ModelResult(text="".join(text_parts), tool_calls=tool_calls, usage=usage_payload)

    def _model_result_from_response(self, data: dict[str, Any]) -> ModelResult:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls: list[ToolCallRequest] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {"raw": raw_arguments}
            tool_calls.append(
                ToolCallRequest(
                    id=item.get("id") or uuid.uuid4().hex,
                    name=function.get("name", ""),
                    arguments=arguments,
                )
            )
        return ModelResult(
            text=self._extract_text(message.get("content")),
            tool_calls=tool_calls,
            usage=data.get("usage"),
        )

    @staticmethod
    def _extract_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
                if item.get("type") == "output_text" and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            return "".join(chunks)
        return str(content)

    @staticmethod
    def _requires_instructions(detail: str) -> bool:
        return "Instructions are required" in detail

    @staticmethod
    def _with_instructions(payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return payload

        system_contents: list[str] = []
        remaining_messages: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "system" and isinstance(message.get("content"), str):
                system_contents.append(message["content"])
                continue
            remaining_messages.append(message)

        if not system_contents:
            return payload

        normalized = dict(payload)
        normalized["messages"] = remaining_messages
        normalized["instructions"] = "\n\n".join(system_contents)
        return normalized

    def _resolve_timeout(self, request_timeout_sec: float | None) -> float:
        if request_timeout_sec is None:
            return float(self.config.timeout_sec)
        return max(0.01, float(request_timeout_sec))

    @staticmethod
    def _call_with_optional_timeout(
        func: Callable[..., Any],
        payload: dict[str, Any],
        *,
        request_timeout_sec: float | None,
    ) -> Any:
        kwargs: dict[str, Any] = {}
        if request_timeout_sec is not None:
            try:
                parameters = inspect.signature(func).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "request_timeout_sec" in parameters:
                kwargs["request_timeout_sec"] = request_timeout_sec
        return func(payload, **kwargs)

    @staticmethod
    def _remaining_timeout(request_timeout_sec: float | None, started_at: float) -> float | None:
        if request_timeout_sec is None:
            return None
        remaining = float(request_timeout_sec) - (time.monotonic() - started_at)
        return max(0.01, remaining)
