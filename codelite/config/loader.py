from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(RuntimeError):
    pass


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


@dataclass(frozen=True)
class AgentConfig:
    system_prompt: str


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    deployment_name: str
    azure_endpoint: str
    api_version: str
    api_key: str
    base_url: str
    temperature: float
    max_tokens: int
    timeout_sec: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimensions: int
    azure_endpoint: str
    deployment_name: str
    api_version: str
    api_key: str
    base_url: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class RerankConfig:
    provider: str
    model: str
    api_key: str
    base_url: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class TavilyConfig:
    api_key: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class RuntimeConfig:
    max_steps: int
    shell_timeout_sec: int
    file_size_limit_bytes: int
    tool_output_limit_chars: int
    context_auto_compact_message_count: int
    context_auto_compact_char_count: int
    context_keep_last_messages: int
    context_summary_line_chars: int
    heart_green_window_sec: int
    heart_yellow_window_sec: int
    heart_red_fail_streak: int
    scheduler_enabled: bool
    timezone: str


@dataclass(frozen=True)
class AppConfig:
    workspace_root: Path
    config_path: Path
    agent: AgentConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    rerank: RerankConfig
    tavily: TavilyConfig
    runtime: RuntimeConfig


def load_app_config(workspace_root: Path | None = None) -> AppConfig:
    root = Path(os.environ.get("CODELITE_WORKSPACE_ROOT") or workspace_root or Path.cwd()).resolve()
    config_path = Path(os.environ.get("CODELITE_CONFIG_PATH") or Path(__file__).with_name("runtime.yaml")).resolve()
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    env_values = {**_load_dotenv(root / ".env"), **os.environ}
    expanded = _expand_env_vars(raw, env_values)

    return AppConfig(
        workspace_root=root,
        config_path=config_path,
        agent=AgentConfig(system_prompt=_require(expanded, "agent.system_prompt")),
        llm=LLMConfig(
            provider=_require(expanded, "llm.provider"),
            model=_require(expanded, "llm.model"),
            deployment_name=_get(expanded, "llm.deployment_name", ""),
            azure_endpoint=_get(expanded, "llm.azure_endpoint", ""),
            api_version=_get(expanded, "llm.api_version", ""),
            api_key=_get(expanded, "llm.api_key", ""),
            base_url=_require(expanded, "llm.base_url"),
            temperature=float(_get(expanded, "llm.temperature", 0.0)),
            max_tokens=int(_get(expanded, "llm.max_tokens", 4096)),
            timeout_sec=int(_get(expanded, "llm.timeout_sec", 120)),
        ),
        embedding=EmbeddingConfig(
            provider=_require(expanded, "embedding.provider"),
            model=_require(expanded, "embedding.model"),
            dimensions=int(_get(expanded, "embedding.dimensions", 1536)),
            azure_endpoint=_get(expanded, "embedding.azure_endpoint", ""),
            deployment_name=_get(expanded, "embedding.deployment_name", ""),
            api_version=_get(expanded, "embedding.api_version", ""),
            api_key=_get(expanded, "embedding.api_key", ""),
            base_url=_require(expanded, "embedding.base_url"),
        ),
        rerank=RerankConfig(
            provider=_get(expanded, "rerank.provider", "openai"),
            model=_require(expanded, "rerank.model"),
            api_key=_get(expanded, "rerank.api_key", ""),
            base_url=_require(expanded, "rerank.base_url"),
        ),
        tavily=TavilyConfig(api_key=_get(expanded, "tavily.api_key", "")),
        runtime=RuntimeConfig(
            max_steps=int(_get(expanded, "runtime.max_steps", 8)),
            shell_timeout_sec=int(_get(expanded, "runtime.shell_timeout_sec", 30)),
            file_size_limit_bytes=int(_get(expanded, "runtime.file_size_limit_bytes", 200000)),
            tool_output_limit_chars=int(_get(expanded, "runtime.tool_output_limit_chars", 12000)),
            context_auto_compact_message_count=int(
                _get(expanded, "runtime.context_auto_compact_message_count", 18)
            ),
            context_auto_compact_char_count=int(
                _get(expanded, "runtime.context_auto_compact_char_count", 12000)
            ),
            context_keep_last_messages=int(_get(expanded, "runtime.context_keep_last_messages", 8)),
            context_summary_line_chars=int(_get(expanded, "runtime.context_summary_line_chars", 120)),
            heart_green_window_sec=int(_get(expanded, "runtime.heart_green_window_sec", 30)),
            heart_yellow_window_sec=int(_get(expanded, "runtime.heart_yellow_window_sec", 90)),
            heart_red_fail_streak=int(_get(expanded, "runtime.heart_red_fail_streak", 3)),
            scheduler_enabled=bool(_get(expanded, "runtime.scheduler_enabled", True)),
            timezone=str(_get(expanded, "runtime.timezone", "Asia/Shanghai")),
        ),
    )


def _expand_env_vars(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env_vars(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item, env) for item in value]
    if isinstance(value, str):
        return ENV_PATTERN.sub(lambda match: env.get(match.group(1), ""), value)
    return value


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
    return values


def _get(data: Mapping[str, Any], dotted_key: str, default: Any) -> Any:
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _require(data: Mapping[str, Any], dotted_key: str) -> Any:
    value = _get(data, dotted_key, None)
    if value is None:
        raise ConfigError(f"缺少必填配置: {dotted_key}")
    return value
