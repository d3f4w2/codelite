from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from codelite.config import RuntimeConfig
from codelite.core.todo import TodoSnapshot, TodoStatus


_DEFAULT_PLAN_KEYWORDS = (
    "plan",
    "planning",
    "steps",
    "todo",
    "先列计划",
    "先规划",
    "分步骤",
)

_DEFAULT_WORKTREE_KEYWORDS = (
    "worktree",
    "refactor",
    "restructure",
    "migration",
    "multi-file",
    "multiple files",
    "across files",
    "架构",
    "重构",
    "迁移",
    "多文件",
    "并行",
)

_ACTION_HINTS = (
    "implement",
    "update",
    "modify",
    "refactor",
    "add",
    "fix",
    "change",
    "optimize",
    "write",
    "create",
    "集成",
    "实现",
    "修改",
    "优化",
    "新增",
    "修复",
)


@dataclass(frozen=True)
class AutoOrchestrationDecision:
    require_plan: bool
    require_worktree: bool
    reason: str
    complexity_score: int
    matched_plan_keywords: list[str]
    matched_worktree_keywords: list[str]
    task_title_hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_plan": self.require_plan,
            "require_worktree": self.require_worktree,
            "reason": self.reason,
            "complexity_score": self.complexity_score,
            "matched_plan_keywords": list(self.matched_plan_keywords),
            "matched_worktree_keywords": list(self.matched_worktree_keywords),
            "task_title_hint": self.task_title_hint,
        }


class AutoOrchestrationPolicy:
    def __init__(self, runtime_config: RuntimeConfig) -> None:
        self.runtime_config = runtime_config
        self.plan_keywords = self._normalize_keywords(
            runtime_config.auto_plan_keywords,
            fallback=_DEFAULT_PLAN_KEYWORDS,
        )
        self.worktree_keywords = self._normalize_keywords(
            runtime_config.auto_worktree_keywords,
            fallback=_DEFAULT_WORKTREE_KEYWORDS,
        )

    def decide(
        self,
        *,
        prompt: str,
        mode: str,
        worktree_available: bool,
        todo_snapshot: TodoSnapshot | None = None,
    ) -> AutoOrchestrationDecision:
        normalized_prompt = " ".join(prompt.strip().split())
        lowered = normalized_prompt.lower()
        complexity_score = self._complexity_score(normalized_prompt, lowered)

        matched_plan_keywords = self._matched_keywords(lowered, self.plan_keywords)
        matched_worktree_keywords = self._matched_keywords(lowered, self.worktree_keywords)

        has_active_agent_plan = self._has_active_agent_plan(todo_snapshot)

        reasons: list[str] = []
        require_plan = False
        require_worktree = False

        if self.runtime_config.auto_plan_enabled:
            if mode.strip().lower() == "plan":
                require_plan = True
                reasons.append("shell_mode_plan")
            elif matched_plan_keywords:
                require_plan = True
                reasons.append("plan_keyword")
            elif complexity_score >= max(2, self.runtime_config.auto_worktree_min_complexity_score - 1):
                require_plan = True
                reasons.append("high_complexity")

        if has_active_agent_plan and mode.strip().lower() != "plan":
            require_plan = False
            reasons.append("active_agent_todo")

        if self.runtime_config.auto_worktree_enabled:
            should_route_worktree = bool(matched_worktree_keywords) or (
                complexity_score >= self.runtime_config.auto_worktree_min_complexity_score
            )
            if should_route_worktree and worktree_available:
                require_worktree = True
                require_plan = True
                reasons.append("worktree_candidate")
            elif should_route_worktree and not worktree_available:
                require_plan = True
                reasons.append("worktree_unavailable")

        reason = ",".join(dict.fromkeys(reasons)) if reasons else "default"
        return AutoOrchestrationDecision(
            require_plan=require_plan,
            require_worktree=require_worktree,
            reason=reason,
            complexity_score=complexity_score,
            matched_plan_keywords=matched_plan_keywords,
            matched_worktree_keywords=matched_worktree_keywords,
            task_title_hint=self._task_title_hint(normalized_prompt),
        )

    def _complexity_score(self, prompt: str, lowered: str) -> int:
        score = 0
        if len(prompt) >= 120:
            score += 1
        if len(re.findall(r"\b\w+\b", lowered)) >= 20:
            score += 1

        separator_hits = (
            lowered.count(" and ")
            + lowered.count(" then ")
            + lowered.count(" also ")
            + lowered.count("然后")
            + lowered.count("并且")
            + lowered.count("再")
            + lowered.count("\n")
        )
        if separator_hits >= 2:
            score += 1

        action_hits = sum(1 for token in _ACTION_HINTS if token in lowered)
        if action_hits >= 2:
            score += 1

        if re.search(r"\btests?/|\.py\b|\.ts\b|\.tsx\b|\.md\b", prompt):
            score += 1
        return score

    @staticmethod
    def _normalize_keywords(values: list[str], *, fallback: tuple[str, ...]) -> list[str]:
        normalized = [str(item).strip().lower() for item in values if str(item).strip()]
        if normalized:
            return normalized
        return list(fallback)

    @staticmethod
    def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
        return [item for item in keywords if item and item in text]

    @staticmethod
    def _has_active_agent_plan(snapshot: TodoSnapshot | None) -> bool:
        if snapshot is None or snapshot.source != "agent":
            return False
        return any(item.status in {TodoStatus.PENDING, TodoStatus.IN_PROGRESS} for item in snapshot.items)

    @staticmethod
    def _task_title_hint(prompt: str) -> str:
        if not prompt:
            return "shell task"
        first_line = prompt.splitlines()[0].strip()
        first_line = re.sub(r"\s+", " ", first_line)
        return first_line[:80] if first_line else "shell task"
