from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class ShellWelcomeData:
    version: str
    session_id: str
    model_name: str
    provider: str
    workspace_root: str
    current_dir: str
    health_summary: str
    recent_activity: list[str]
    tips: list[str]


class ShellRenderer:
    def __init__(self, width: int | None = None) -> None:
        terminal_width = width or shutil.get_terminal_size((110, 30)).columns
        self.width = max(80, min(terminal_width, 120))

    def render_welcome(self, data: ShellWelcomeData) -> str:
        title = f"CodeLite Shell v{data.version}"
        left_lines = [
            "Welcome back!",
            "",
            "  .-.",
            " (o o)",
            " | O \\",
            "  \\   \\",
            "   `~~~'",
            "",
            f"Model: {data.model_name} ({data.provider})",
            f"Session: {data.session_id}",
            f"Workspace: {self._shorten_middle(data.workspace_root, 42)}",
            f"Current Dir: {self._shorten_middle(data.current_dir, 40)}",
            f"Health: {data.health_summary}",
        ]
        right_lines = [
            "Tips for Getting Started",
            *(f"- {tip}" for tip in data.tips),
            "",
            "Recent Activity",
            *(f"- {item}" for item in data.recent_activity),
        ]

        if self.width >= 104:
            gap = "  "
            panel_width = (self.width - len(gap)) // 2
            left_panel = self._panel("Welcome", left_lines, panel_width)
            right_panel = self._panel("Workspace", right_lines, panel_width)
            body = self._join_columns(left_panel, right_panel, gap)
        else:
            body = self._panel("Welcome", left_lines, self.width)
            body += ["", *self._panel("Workspace", right_lines, self.width)]

        footer = [
            "",
            "Type a task below and press Enter.",
            "Shortcuts: help | health | session replay | exit",
        ]
        return "\n".join([title, *body, *footer])

    def render_help(self, commands: list[str]) -> str:
        lines = ["Local Commands", *[f"- {command}" for command in commands]]
        return "\n".join(self._panel("Help", lines, self.width))

    @staticmethod
    def prompt() -> str:
        return "> "

    def _panel(self, title: str, lines: list[str], width: int) -> list[str]:
        inner_width = max(width - 4, 20)
        title_text = f" {title} "
        top = "+" + title_text + "-" * max(inner_width - len(title_text) + 2, 0) + "+"
        body = [f"| {self._fit(line, inner_width)} |" for line in lines]
        border = "+" + "-" * (inner_width + 2) + "+"
        return [top, *body, border]

    @staticmethod
    def _join_columns(left: list[str], right: list[str], gap: str) -> list[str]:
        max_lines = max(len(left), len(right))
        left_width = max(len(line) for line in left)
        right_width = max(len(line) for line in right)
        joined: list[str] = []
        for index in range(max_lines):
            left_line = left[index] if index < len(left) else " " * left_width
            right_line = right[index] if index < len(right) else " " * right_width
            joined.append(left_line.ljust(left_width) + gap + right_line.ljust(right_width))
        return joined

    @staticmethod
    def _fit(text: str, width: int) -> str:
        truncated = text if len(text) <= width else text[: max(width - 3, 0)] + "..."
        return truncated.ljust(width)

    @staticmethod
    def _shorten_middle(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        if width <= 7:
            return text[:width]
        head = (width - 3) // 2
        tail = width - 3 - head
        return text[:head] + "..." + text[-tail:]
