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
    tool_parallel_enabled: bool
    tool_result_keep_recent: int
    context_auto_compact_message_count: int
    context_auto_compact_char_count: int
    context_keep_last_messages: int
    context_summary_line_chars: int
    context_snip_enabled: bool
    context_collapse_enabled: bool
    prompt_dynamic_boundary_enabled: bool
    permission_approval_ttl_sec: int
    heart_green_window_sec: int
    heart_yellow_window_sec: int
    heart_red_fail_streak: int
    scheduler_enabled: bool
    timezone: str
    delivery_max_attempts: int
    delivery_backoff_base_sec: int
    dispatcher_global_workers: int
    dispatcher_subagent_reserved_workers: int
    dispatcher_background_reserved_workers: int
    dispatcher_claim_ttl_sec: int
    dispatcher_team_default_limit: int
    todo_nag_after_steps: int
    retrieval_max_results: int
    auto_plan_enabled: bool
    auto_worktree_enabled: bool
    auto_plan_keywords: list[str]
    auto_worktree_keywords: list[str]
    auto_worktree_min_complexity_score: int
    memory_manifest_path: str
    memory_candidate_enabled: bool
    memory_candidate_max_per_turn: int
    memory_files_whitelist: list[str]


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


def resolve_workspace_root(workspace_root: Path | None = None) -> Path:
    explicit = os.environ.get("CODELITE_WORKSPACE_ROOT")
    if explicit:
        return Path(explicit).resolve()
    if workspace_root is not None:
        return Path(workspace_root).resolve()

    cwd = Path.cwd().resolve()
    if _can_auto_use_workspace(cwd):
        return cwd

    package_workspace = _package_workspace_root()
    if package_workspace is not None:
        return package_workspace

    fallback = (Path.home() / ".codelite" / "workspace").resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def load_app_config(workspace_root: Path | None = None) -> AppConfig:
    root = resolve_workspace_root(workspace_root)
    config_path = Path(os.environ.get("CODELITE_CONFIG_PATH") or Path(__file__).with_name("runtime.yaml")).resolve()
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    env_values = _collect_env_values(root)
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
            tool_parallel_enabled=bool(_get(expanded, "runtime.tool_parallel_enabled", True)),
            tool_result_keep_recent=int(_get(expanded, "runtime.tool_result_keep_recent", 6)),
            context_auto_compact_message_count=int(
                _get(expanded, "runtime.context_auto_compact_message_count", 400)
            ),
            context_auto_compact_char_count=int(
                _get(expanded, "runtime.context_auto_compact_char_count", 800000)
            ),
            context_keep_last_messages=int(_get(expanded, "runtime.context_keep_last_messages", 8)),
            context_summary_line_chars=int(_get(expanded, "runtime.context_summary_line_chars", 120)),
            context_snip_enabled=bool(_get(expanded, "runtime.context_snip_enabled", True)),
            context_collapse_enabled=bool(_get(expanded, "runtime.context_collapse_enabled", True)),
            prompt_dynamic_boundary_enabled=bool(_get(expanded, "runtime.prompt_dynamic_boundary_enabled", True)),
            permission_approval_ttl_sec=int(_get(expanded, "runtime.permission_approval_ttl_sec", 1800)),
            heart_green_window_sec=int(_get(expanded, "runtime.heart_green_window_sec", 30)),
            heart_yellow_window_sec=int(_get(expanded, "runtime.heart_yellow_window_sec", 90)),
            heart_red_fail_streak=int(_get(expanded, "runtime.heart_red_fail_streak", 3)),
            scheduler_enabled=bool(_get(expanded, "runtime.scheduler_enabled", True)),
            timezone=str(_get(expanded, "runtime.timezone", "Asia/Shanghai")),
            delivery_max_attempts=int(_get(expanded, "runtime.delivery_max_attempts", 3)),
            delivery_backoff_base_sec=int(_get(expanded, "runtime.delivery_backoff_base_sec", 5)),
            dispatcher_global_workers=int(_get(expanded, "runtime.dispatcher_global_workers", 8)),
            dispatcher_subagent_reserved_workers=int(
                _get(expanded, "runtime.dispatcher_subagent_reserved_workers", 5)
            ),
            dispatcher_background_reserved_workers=int(
                _get(expanded, "runtime.dispatcher_background_reserved_workers", 2)
            ),
            dispatcher_claim_ttl_sec=int(_get(expanded, "runtime.dispatcher_claim_ttl_sec", 120)),
            dispatcher_team_default_limit=int(_get(expanded, "runtime.dispatcher_team_default_limit", 3)),
            todo_nag_after_steps=int(_get(expanded, "runtime.todo_nag_after_steps", 3)),
            retrieval_max_results=int(_get(expanded, "runtime.retrieval_max_results", 5)),
            auto_plan_enabled=bool(_get(expanded, "runtime.auto_plan_enabled", True)),
            auto_worktree_enabled=bool(_get(expanded, "runtime.auto_worktree_enabled", True)),
            auto_plan_keywords=[str(item) for item in _get(expanded, "runtime.auto_plan_keywords", [])],
            auto_worktree_keywords=[str(item) for item in _get(expanded, "runtime.auto_worktree_keywords", [])],
            auto_worktree_min_complexity_score=int(
                _get(expanded, "runtime.auto_worktree_min_complexity_score", 4)
            ),
            memory_manifest_path=str(_get(expanded, "runtime.memory_manifest_path", "runtime/memory/manifest.json")),
            memory_candidate_enabled=bool(_get(expanded, "runtime.memory_candidate_enabled", True)),
            memory_candidate_max_per_turn=int(_get(expanded, "runtime.memory_candidate_max_per_turn", 1)),
            memory_files_whitelist=[str(item) for item in _get(expanded, "runtime.memory_files_whitelist", [])],
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


def _can_auto_use_workspace(path: Path) -> bool:
    return path.exists() and path.is_dir() and not _is_protected_system_dir(path) and os.access(path, os.W_OK)


def _is_protected_system_dir(path: Path) -> bool:
    if os.name != "nt":
        return False
    normalized = str(path.resolve()).lower()
    candidates = [
        os.environ.get("WINDIR", ""),
        str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"),
        str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "SysWOW64"),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        prefix = str(Path(candidate).resolve()).lower()
        if normalized == prefix or normalized.startswith(prefix + "\\"):
            return True
    return False


def _package_workspace_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").exists() and (candidate / "codelite").is_dir():
        return candidate
    return None


def _collect_env_values(workspace_root: Path) -> dict[str, str]:
    values: dict[str, str] = {}

    package_root = _package_workspace_root()
    layered_paths: list[Path] = []
    if package_root is not None:
        layered_paths.append(package_root / ".env")
    layered_paths.append(Path.home() / ".codelite" / ".env")
    layered_paths.append(workspace_root / ".env")

    seen: set[Path] = set()
    for path in layered_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        values.update(_load_dotenv(resolved))

    values.update(os.environ)
    return values
