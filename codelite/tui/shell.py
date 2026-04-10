from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum


class ShellMode(StrEnum):
    PLAN = "plan"
    ACT = "act"

    def cycle(self) -> ShellMode:
        return ShellMode.ACT if self is ShellMode.PLAN else ShellMode.PLAN

    @property
    def status_text(self) -> str:
        if self is ShellMode.PLAN:
            return "已切换到规划模式（Shift+Tab / Ctrl+M 可切换）"
        return "已切换到行动模式（Shift+Tab / Ctrl+M 可切换）"

    @property
    def guidance_prefix(self) -> str:
        if self is ShellMode.PLAN:
            return (
                "[shell-mode=plan]\n"
                "The interactive shell is currently in plan mode. Focus on analysis, tradeoffs, and a concrete plan. "
                "Do not make edits or run modifying actions unless the user explicitly asks to execute now.\n\n"
                "User request:\n"
            )
        return ""


class ShellInputFocus(StrEnum):
    EDITOR = "editor"
    COMMAND = "command"


@dataclass(frozen=True)
class ShellCommandSpec:
    name: str
    description: str


@dataclass(frozen=True)
class ShellInputWindow:
    lines: list[str]
    start_line: int
    total_lines: int


@dataclass
class ShellInputModel:
    commands: list[ShellCommandSpec]
    skills: list[ShellCommandSpec] = field(default_factory=list)
    mode: ShellMode = ShellMode.ACT
    focus: ShellInputFocus = ShellInputFocus.EDITOR
    buffer: str = ""
    cursor: int = 0
    history: list[str] = field(default_factory=list)
    history_index: int | None = None
    draft_buffer: str = ""
    suggestion_index: int = 0

    def insert(self, text: str) -> None:
        if not text:
            return
        self.buffer = self.buffer[: self.cursor] + text + self.buffer[self.cursor :]
        self.cursor += len(text)
        self._reset_suggestion_selection()
        self._sync_focus_after_buffer_change()

    def backspace(self) -> None:
        if self.cursor <= 0:
            return
        self.buffer = self.buffer[: self.cursor - 1] + self.buffer[self.cursor :]
        self.cursor -= 1
        self._reset_suggestion_selection()
        self._sync_focus_after_buffer_change()

    def delete(self) -> None:
        if self.cursor >= len(self.buffer):
            return
        self.buffer = self.buffer[: self.cursor] + self.buffer[self.cursor + 1 :]
        self._reset_suggestion_selection()
        self._sync_focus_after_buffer_change()

    def move_left(self) -> None:
        self.focus = ShellInputFocus.EDITOR
        self.cursor = max(0, self.cursor - 1)

    def move_right(self) -> None:
        self.focus = ShellInputFocus.EDITOR
        self.cursor = min(len(self.buffer), self.cursor + 1)

    def move_home(self) -> None:
        self.focus = ShellInputFocus.EDITOR
        self.cursor = self._line_start(self.cursor)

    def move_end(self) -> None:
        self.focus = ShellInputFocus.EDITOR
        self.cursor = self._line_end(self.cursor)

    def move_up(self) -> None:
        self.focus = ShellInputFocus.EDITOR
        line_start = self._line_start(self.cursor)
        if line_start <= 0:
            return
        col = self.cursor - line_start
        prev_line_end = line_start - 1
        prev_line_start = self._line_start(prev_line_end)
        self.cursor = prev_line_start + min(col, prev_line_end - prev_line_start)

    def move_down(self) -> None:
        self.focus = ShellInputFocus.EDITOR
        line_end = self._line_end(self.cursor)
        if line_end >= len(self.buffer):
            return
        col = self.cursor - self._line_start(self.cursor)
        next_line_start = line_end + 1
        next_line_end = self._line_end(next_line_start)
        self.cursor = next_line_start + min(col, next_line_end - next_line_start)

    def insert_newline(self) -> None:
        self.insert("\n")

    def set_buffer(self, value: str) -> None:
        self.buffer = value
        self.cursor = len(value)
        self._reset_suggestion_selection()
        self._sync_focus_after_buffer_change()

    def toggle_mode(self) -> ShellMode:
        self.mode = self.mode.cycle()
        return self.mode

    def history_previous(self) -> None:
        if not self.history:
            return
        if self.history_index is None:
            self.draft_buffer = self.buffer
            self.history_index = len(self.history) - 1
        elif self.history_index > 0:
            self.history_index -= 1
        self.set_buffer(self.history[self.history_index])

    def history_next(self) -> None:
        if self.history_index is None:
            return
        if self.history_index >= len(self.history) - 1:
            self.history_index = None
            self.set_buffer(self.draft_buffer)
            return
        self.history_index += 1
        self.set_buffer(self.history[self.history_index])

    def suggestions(self, *, limit: int = 8) -> list[ShellCommandSpec]:
        text = self.buffer.lstrip()
        prefix = self.active_palette_prefix()
        if not prefix:
            return []
        source = self.commands if prefix == "/" else self.skills
        token = text[1:].split(" ", 1)[0].strip().lower()
        if not token:
            return source[:limit]
        prefix_matches = [item for item in source if item.name.lower().startswith(token)]
        contains_matches = [item for item in source if token in item.name.lower() and not item.name.lower().startswith(token)]
        return [*prefix_matches, *contains_matches][:limit]

    def active_palette_prefix(self) -> str:
        text = self.buffer.lstrip()
        if not text or "\n" in text:
            return ""
        prefix = text[0]
        if prefix not in {"/", "$"}:
            return ""
        if " " in text[1:]:
            return ""
        return prefix

    def move_suggestion(self, delta: int) -> None:
        suggestions = self.suggestions()
        if not suggestions:
            self.focus = ShellInputFocus.EDITOR
            return
        self.focus = ShellInputFocus.COMMAND
        self.suggestion_index = (self.suggestion_index + delta) % len(suggestions)

    def selected_suggestion(self) -> ShellCommandSpec | None:
        suggestions = self.suggestions()
        if not suggestions:
            return None
        if self.suggestion_index >= len(suggestions):
            self.suggestion_index = 0
        return suggestions[self.suggestion_index]

    def confirm_suggestion(self) -> bool:
        selected = self.selected_suggestion()
        if selected is None:
            return False
        prefix = self.active_palette_prefix()
        if not prefix:
            return False
        replacement = f"{prefix}{selected.name} "
        self.set_buffer(replacement)
        self.focus = ShellInputFocus.EDITOR
        return True

    def autocomplete(self) -> bool:
        prefix = self.active_palette_prefix()
        suggestions = self.suggestions(limit=8)
        if not suggestions:
            return False
        if len(suggestions) == 1:
            marker = prefix if prefix else "/"
            replacement = f"{marker}{suggestions[0].name} "
            self.set_buffer(replacement)
            self.focus = ShellInputFocus.EDITOR
            return True
        if self.focus is ShellInputFocus.EDITOR:
            self.focus = ShellInputFocus.COMMAND
            return True
        self.move_suggestion(1)
        return True

    def consume(self) -> str:
        value = self.buffer
        self.history_index = None
        self.draft_buffer = ""
        self.set_buffer("")
        return value

    def toggle_focus(self) -> ShellInputFocus:
        if not self.suggestions():
            self.focus = ShellInputFocus.EDITOR
            return self.focus
        self.focus = ShellInputFocus.COMMAND if self.focus is ShellInputFocus.EDITOR else ShellInputFocus.EDITOR
        return self.focus

    def set_focus(self, focus: ShellInputFocus) -> None:
        self.focus = focus

    def cursor_line_col(self) -> tuple[int, int]:
        line = self.buffer.count("\n", 0, self.cursor)
        line_start = self._line_start(self.cursor)
        return line, self.cursor - line_start

    def inline_ghost_text(self, *, hint: str = "") -> str:
        if self.cursor != len(self.buffer):
            return ""
        if not self.buffer:
            return hint
        selected = self.selected_suggestion()
        if selected is None:
            return ""
        stripped = self.buffer.lstrip()
        if not stripped.startswith("/") or "\n" in stripped:
            return ""
        command = stripped[1:]
        if " " in command:
            return ""
        token = command.lower()
        if not selected.name.startswith(token) or selected.name == token:
            return ""
        return selected.name[len(token) :] + " "

    def input_window(self, *, limit: int = 4) -> ShellInputWindow:
        raw_lines = self.buffer.split("\n") if self.buffer else [""]
        if limit <= 0 or len(raw_lines) <= limit:
            return ShellInputWindow(lines=raw_lines, start_line=0, total_lines=len(raw_lines))
        cursor_line, _ = self.cursor_line_col()
        context = max(limit // 2, 1)
        start = max(0, min(cursor_line - context, len(raw_lines) - limit))
        end = start + limit
        return ShellInputWindow(lines=raw_lines[start:end], start_line=start, total_lines=len(raw_lines))

    def should_confirm_suggestion_on_enter(self) -> bool:
        text = self.buffer.strip()
        if not text or text[0] not in {"/", "$"}:
            return False
        if " " in text[1:]:
            return False
        selected = self.selected_suggestion()
        if selected is None:
            return False
        # If the typed command already exactly matches the selected command,
        # Enter should send it instead of endlessly re-confirming the same text.
        return text != f"{text[0]}{selected.name}"

    def pending_command_preview(self) -> str:
        stripped = self.buffer.strip()
        if not stripped or stripped[0] not in {"/", "$"} or "\n" in stripped:
            return ""
        if " " in stripped[1:]:
            command = stripped[1:].split(" ", 1)[0].strip()
            return f"{stripped[0]}{command}" if command else ""
        selected = self.selected_suggestion()
        if selected is not None:
            return f"{stripped[0]}{selected.name}"
        return stripped

    def _reset_suggestion_selection(self) -> None:
        self.suggestion_index = 0
        if not self.suggestions():
            self.focus = ShellInputFocus.EDITOR

    def _sync_focus_after_buffer_change(self) -> None:
        if not self.active_palette_prefix():
            self.focus = ShellInputFocus.EDITOR
            return
        self.focus = ShellInputFocus.COMMAND

    def _line_start(self, index: int) -> int:
        target = max(0, min(index, len(self.buffer)))
        return self.buffer.rfind("\n", 0, target) + 1

    def _line_end(self, index: int) -> int:
        target = max(0, min(index, len(self.buffer)))
        pos = self.buffer.find("\n", target)
        if pos < 0:
            return len(self.buffer)
        return pos


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
    label: str = "CodeLite"
    workspace_name: str = ""
    capability_summary: list[str] = field(default_factory=list)
    last_session_id: str | None = None
    todo_summary: str = ""
    task_summary: str = ""
    reasoning_effort: str = ""
    quick_suggestion: str = ""


@dataclass(frozen=True)
class TodoBoardData:
    summary: str
    items: list[str]


@dataclass(frozen=True)
class TaskBoardData:
    summary: str
    items: list[str]


@dataclass(frozen=True)
class QueueBoardData:
    summary: str
    items: list[str]


@dataclass(frozen=True)
class LockBoardData:
    summary: str
    items: list[str]


@dataclass(frozen=True)
class TimelineGroupData:
    title: str
    items: list[str]


@dataclass(frozen=True)
class ToolCardData:
    tool_name: str
    card_kind: str
    status: str
    title: str
    lines: list[str]


@dataclass(frozen=True)
class SubagentCardData:
    subagent_id: str
    team_id: str
    status: str
    prompt: str
    session_id: str
    result_preview: str
    error: str
    used_web_search: bool


@dataclass(frozen=True)
class TeamBoardData:
    summary: str
    team_lines: list[str]
    subagent_cards: list[SubagentCardData]


class ShellRenderer:
    def __init__(
        self,
        width: int | None = None,
        *,
        label: str = "CodeLite",
        color_enabled: bool = False,
        style: str = "codex",
    ) -> None:
        terminal_width = width or shutil.get_terminal_size((110, 30)).columns
        self.width = max(80, min(terminal_width, 116))
        self.frame_width = max(64, min(self.width, 92))
        normalized = (label or "CodeLite").strip()
        self.label = normalized or "CodeLite"
        self.prompt_label = self.label.lower().replace(" ", "-")
        self.color_enabled = color_enabled
        self.style = self._normalize_style(style)
        self._unicode_glyphs = self._resolve_unicode_glyph_mode()
        self._palette = {
            "reset": "\033[0m",
            "dim": "\033[2m",
            "bold": "\033[1m",
            "reverse": "\033[7m",
            "cyan": "\033[36m",
            "green": "\033[32m",
            "yellow": "\033[33m",
            "red": "\033[31m",
            "blue": "\033[34m",
            "gray": "\033[90m",
        }

    def _resolve_unicode_glyph_mode(self) -> bool:
        pref = os.environ.get("CODELITE_UI_GLYPHS", "auto").strip().lower()
        if pref in {"unicode", "utf8", "utf-8"}:
            return True
        if pref in {"ascii", "plain"}:
            return False
        encoding = (sys.stdout.encoding or "").strip().lower()
        return encoding.startswith("utf")

    def _glyph(self, unicode_text: str, ascii_text: str) -> str:
        return unicode_text if self._unicode_glyphs else ascii_text

    def _sep(self) -> str:
        return self._glyph(" · ", " | ")

    def _bullet_sep(self) -> str:
        return self._glyph(" • ", " | ")

    def _line_char(self) -> str:
        return self._glyph("─", "-")

    def _branch_start(self) -> str:
        return self._glyph("╭─", "+-")

    def _branch_mid(self) -> str:
        return self._glyph("├─", "|-")

    def _branch_end(self) -> str:
        return self._glyph("╰─", "`-")

    @staticmethod
    def _normalize_style(style: str | None) -> str:
        value = (style or "").strip().lower()
        if value in {"classic", "legacy", "full"}:
            return "classic"
        if value in {"claude", "anthropic"}:
            return "claude"
        if value in {"codex", "openai"}:
            return "codex"
        return "codex"

    def is_claude_style(self) -> bool:
        return self.style == "claude"

    def is_codex_style(self) -> bool:
        return self.style == "codex"

    def _codex_model_with_effort(self, model_name: str, reasoning_effort: str) -> str:
        effort = (reasoning_effort or "").strip()
        if effort:
            return f"{model_name} {effort}".strip()
        return model_name.strip()

    def _codex_box(self, rows: list[str], *, min_inner_width: int = 56) -> list[str]:
        clean_rows = [str(item) for item in rows]
        content_width = max((self._display_width(item) for item in clean_rows), default=0)
        inner_width = min(max(max(content_width, min_inner_width), 48), max(self.width - 6, 48))
        top_left = self._glyph("┌", "+")
        top_right = self._glyph("┐", "+")
        bottom_left = self._glyph("└", "+")
        bottom_right = self._glyph("┘", "+")
        side = self._glyph("│", "|")
        rule = self._line_char()
        lines = [f"{top_left}{rule * (inner_width + 2)}{top_right}"]
        for row in clean_rows:
            trimmed = self._truncate_display_width(row, inner_width)
            pad = max(0, inner_width - self._display_width(trimmed))
            lines.append(f"{side} {trimmed}{' ' * pad} {side}")
        lines.append(f"{bottom_left}{rule * (inner_width + 2)}{bottom_right}")
        return lines

    def render_labeled_block(self, title: str, lines: list[str], *, min_inner_width: int = 56) -> str:
        if not self.is_codex_style():
            payload = [f"{title}:"] + [f"  {line}" for line in lines]
            return "\n".join(self._fit(line, self.width) for line in payload)
        content_lines = lines or ["(empty)"]
        rows = [f"[{title}]"] + content_lines
        return "\n".join(self._fit(line, self.width) for line in self._codex_box(rows, min_inner_width=min_inner_width))

    def render_status_block(self, lines: list[str]) -> str:
        status_lines = lines or ["[INFO] no runtime status events"]
        return self.render_labeled_block("STATUS", status_lines, min_inner_width=64)

    def render_welcome(self, data: ShellWelcomeData) -> str:
        if self.is_codex_style():
            model_line = self._codex_model_with_effort(data.model_name, data.reasoning_effort)
            rows = [
                f">_ {data.label} (v{data.version})",
                "",
                f"model:    {model_line}    /model to change",
                f"directory: {data.current_dir}",
            ]
            return "\n".join(self._fit(line, self.width) for line in self._codex_box(rows))
        if self.is_claude_style():
            workspace_name = data.workspace_name or self._workspace_name(data.workspace_root)
            quick = "/help  /view full  /view compact"
            session_tail = data.session_id[-4:] if data.session_id else "----"
            lines = [
                self._dim(self._branch_start()) + self._accent(f" {data.label}"),
                self._dim(
                    f"{self._branch_mid()} {workspace_name}{self._sep()}{data.model_name}/{data.provider}{self._sep()}session:{session_tail}"
                ),
                self._dim(f"{self._branch_mid()} commands: {quick}"),
            ]
            status_items: list[str] = []
            if data.health_summary:
                status_items.append(f"health:{data.health_summary}")
            if data.todo_summary:
                status_items.append(f"todo:{data.todo_summary}")
            if data.task_summary:
                status_items.append(f"task:{data.task_summary}")
            if status_items:
                lines.append(self._dim(f"{self._branch_mid()} " + self._sep().join(status_items)))
            if data.recent_activity:
                lines.append(self._dim(f"{self._branch_mid()} recent: {data.recent_activity[0]}"))
            lines.append(self._dim(f"{self._branch_end()} ready"))
            return "\n".join(self._fit(line, self.width) for line in lines)
        title = self._accent(f"{data.label} Interactive Shell")
        workspace_name = data.workspace_name or self._workspace_name(data.workspace_root)
        headline = [
            f"{self._label('Workspace')} {workspace_name}  |  {self._label('Model')} {data.model_name} ({data.provider})  |  {self._label('Session')} {data.session_id[-4:]}",
            f"{self._label('Directory')} {self._shorten_middle(data.current_dir, self.width - 8)}",
            f"{self._label('Health')} {data.health_summary}",
        ]
        if data.capability_summary:
            headline.append(f"{self._label('Capabilities')} {' | '.join(data.capability_summary)}")
        if data.todo_summary:
            headline.append(f"{self._label('TODO')} {data.todo_summary}")
        if data.task_summary:
            headline.append(f"{self._label('Tasks')} {data.task_summary}")
        if data.recent_activity:
            headline.append(f"{self._label('Recent')} {data.recent_activity[0]}")
        footer = [
            self._dim(
                "Tip: type task text to run, or type / to open command palette; arrows select commands/history; Shift+Tab or Ctrl+M switches mode."
            ),
            self._dim("Note: runtime progress is streamed step-by-step."),
        ]
        lines = [title, self._rule(self._line_char()), *headline, *footer]
        return "\n".join(self._fit(line, self.width) for line in lines)

    def render_help(self, commands: list[str]) -> str:
        lines = [self._section_title("本地命令"), *[f"  {command}" for command in commands]]
        return "\n".join(self._compact_section("命令帮助", lines))

    def render_turn_header(self, *, turn_index: int, mode: ShellMode, raw: str) -> str:
        if self.is_codex_style():
            del turn_index, mode, raw
            return ""
        if self.is_claude_style():
            mode_chip = "PLAN" if mode is ShellMode.PLAN else "ACT"
            lines = [
                self._dim(self._branch_start())
                + self._accent(" user")
                + self._dim(f" {self._sep().strip()} turn {turn_index:02d} {self._sep().strip()} {mode_chip}"),
                self._fit(f"{self._branch_mid()} {raw}", self.width),
                self._dim(self._branch_end()),
            ]
            return "\n".join(lines)
        mode_label = "规划" if mode is ShellMode.PLAN else "行动"
        lines = [
            self._rule(self._line_char()),
            self._accent(f"回合 {turn_index:02d}{self._sep()}{mode_label}"),
            f"{self._mode_text(mode)}",
            f"你说: {raw}",
            self._dim("过程时间线"),
        ]
        return "\n".join(lines)

    def render_runtime_event(self, kind: str, event: str) -> str:
        badge = {
            "receive": self._label("[RECV]"),
            "retrieve": self._label("[RETR]"),
            "think": self._accent("[THINK]"),
            "tool": self._warn("[TOOL]"),
            "task": self._label("[TASK]"),
            "done": self._ok("[DONE]"),
            "error": self._warn("[ERR]"),
        }.get(kind, self._dim("[EVENT]"))
        if self.is_codex_style():
            return f"{badge} {event}"
        return f"  {badge} {event}"

    def render_todo_board(self, data: TodoBoardData) -> str:
        lines = [f"summary: {data.summary or 'no todo'}"]
        if data.items:
            lines.extend(f"  {item}" for item in data.items)
        else:
            lines.append("  no todo items in current session")
        return "\n".join(self._compact_section("TODO Board", lines))

    def render_task_board(self, data: TaskBoardData) -> str:
        lines = [f"summary: {data.summary or 'no tasks'}"]
        if data.items:
            lines.extend(f"  {item}" for item in data.items)
        else:
            lines.append("  no task records yet")
        return "\n".join(self._compact_section("Task Board", lines))

    def render_queue_board(self, data: QueueBoardData) -> str:
        lines = [f"summary: {data.summary or 'no queue items'}"]
        if data.items:
            lines.extend(f"  {item}" for item in data.items)
        else:
            lines.append("  no pending or failed queue items")
        return "\n".join(self._compact_section("Queue Board", lines))

    def render_lock_board(self, data: LockBoardData) -> str:
        lines = [f"summary: {data.summary or 'no locks'}"]
        if data.items:
            lines.extend(f"  {item}" for item in data.items)
        else:
            lines.append("  no active locks")
        return "\n".join(self._compact_section("Lock Board", lines))

    def render_named_board(
        self,
        *,
        title: str,
        summary: str,
        items: list[str],
        empty_text: str = "no data",
    ) -> str:
        lines = [f"summary: {summary}"]
        if items:
            lines.extend(f"  {item}" for item in items)
        else:
            lines.append(f"  {empty_text}")
        return "\n".join(self._compact_section(title, lines))

    def render_tool_cards(self, cards: list[ToolCardData]) -> str:
        lines: list[str] = []
        if not cards:
            lines.append("  no tool calls in this turn")
            return "\n".join(self._compact_section("Tool Cards", lines))
        visible_cards = cards[-6:]
        hidden_count = max(0, len(cards) - len(visible_cards))
        if hidden_count:
            lines.append(f"  ... {hidden_count} earlier tool cards hidden")
        for card in visible_cards:
            header = f"  {self._tool_card_badge(card.status)} {self._tool_card_kind_chip(card.card_kind)} {card.title}"
            lines.append(header)
            lines.extend(f"    {item}" for item in card.lines[:3])
            if len(card.lines) > 3:
                lines.append(f"    ... +{len(card.lines) - 3} more lines")
        return "\n".join(self._compact_section("Tool Cards", lines))

    def render_team_board(self, data: TeamBoardData) -> str:
        lines = [f"summary: {data.summary or 'no team'}"]
        if data.team_lines:
            lines.append("  Teams:")
            lines.extend(f"    {item}" for item in data.team_lines)
        else:
            lines.append("  no team records")
        if data.subagent_cards:
            lines.append(f"  Subagents ({len(data.subagent_cards)}):")
            for card in data.subagent_cards:
                badge = self._subagent_badge(card.status)
                session_suffix = f" session={card.session_id[-8:]}" if card.session_id else ""
                lines.append(f"    {badge} {card.subagent_id[:8]} team={card.team_id[:8]} web={'yes' if card.used_web_search else 'no'}{session_suffix}")
                lines.append(f"      prompt: {self._truncate_display_width(card.prompt, 44)}")
                if card.status == "failed" and card.error:
                    lines.append(f"      error: {self._truncate_display_width(card.error, 56)}")
                elif card.result_preview:
                    lines.append(f"      result: {self._truncate_display_width(card.result_preview, 56)}")
        else:
            lines.append("  no subagent records")
        return "\n".join(self._compact_section("Team Board", lines))

    def render_grouped_timeline(self, groups: list[TimelineGroupData]) -> str:
        lines: list[str] = []
        if not groups:
            lines.append("  no grouped events")
            return "\n".join(self._compact_section("Grouped Timeline", lines))
        for group in groups:
            lines.append(f"  {self._label(group.title)}")
            lines.extend(f"    {item}" for item in group.items)
        return "\n".join(self._compact_section("Grouped Timeline", lines))

    def prompt(self, *, workspace_root: str = "", session_id: str = "") -> str:
        if self.is_codex_style() or self.is_claude_style():
            return "> "
        workspace_name = self._workspace_name(workspace_root)
        if workspace_name and session_id:
            return f"{self.prompt_label}:{workspace_name}[{session_id[-4:]}]> "
        if workspace_name:
            return f"{self.prompt_label}:{workspace_name}> "
        return f"{self.prompt_label}> "

    def render_compact_turn_footer(
        self,
        *,
        turn_index: int,
        mode: ShellMode,
        tool_count: int,
        task_id: str,
        elapsed_s: float | None = None,
        event_count: int | None = None,
    ) -> str:
        if self.is_codex_style():
            return ""
        if self.is_claude_style():
            mode_chip = "PLAN" if mode is ShellMode.PLAN else "ACT"
            parts = [
                self._glyph("✓ done", "done"),
                f"turn:{turn_index:02d}",
                mode_chip,
                f"tools:{tool_count}",
            ]
            if elapsed_s is not None:
                parts.append(f"{elapsed_s:0.1f}s")
            if event_count is not None and event_count > 0:
                parts.append(f"events:{event_count}")
            parts.extend([f"task:{task_id}", "/view full"])
            return self._dim(self._sep().join(parts))
        return self.render_named_board(
            title="Turn Summary",
            summary=f"turn={turn_index:02d} | mode={mode.value} | tools={tool_count}",
            items=[
                f"task: {task_id}",
                "Detailed boards are collapsed: use /view full, or /turns /tasks /team /queue /locks /ops all.",
            ],
            empty_text="no turn summary",
        )

    def render_prompt_status(
        self,
        *,
        workspace_name: str,
        session_id: str,
        mode: ShellMode,
        model_name: str,
        provider: str,
        reasoning_effort: str = "",
        remaining_percent: int | None = None,
        current_dir: str = "",
        runtime_summary: str = "",
    ) -> str:
        if self.is_codex_style():
            model_label = self._codex_model_with_effort(model_name, reasoning_effort)
            remaining = 100 if remaining_percent is None else max(0, min(100, int(remaining_percent)))
            directory = current_dir or workspace_name
            return self._fit(f"{model_label}{self._sep()}{remaining}% left{self._sep()}{directory}", self.width)
        if not self.is_claude_style():
            return ""
        mode_chip = "PLAN" if mode is ShellMode.PLAN else "ACT"
        session_tail = session_id[-4:] if session_id else "----"
        parts = [mode_chip, workspace_name, f"{model_name}/{provider}", f"session:{session_tail}"]
        if runtime_summary:
            parts.append(runtime_summary)
        return self._dim(f"{self._branch_mid()} " + self._sep().join(parts))

    def render_thinking_status(
        self,
        *,
        frame: str,
        elapsed_s: float,
        mode: ShellMode,
        verb: str | None = None,
        hint: str | None = None,
    ) -> str:
        if self.is_codex_style():
            elapsed_whole = max(0, int(elapsed_s))
            action_hint = (hint or "esc to interrupt").strip()
            marker = self._glyph("•", "*")
            return self._fit(f"{marker} Working ({elapsed_whole}s{self._bullet_sep()}{action_hint})", self.width)
        mode_chip = "PLAN" if mode is ShellMode.PLAN else "ACT"
        parts = [f"{frame} {verb or 'thinking'}", f"{elapsed_s:0.1f}s", mode_chip]
        if hint:
            parts.append(hint)
        return self._dim(self._sep().join(parts))

    def render_quick_suggestion(self, suggestion: str) -> str:
        text = suggestion.strip()
        if not text:
            return ""
        if self.is_codex_style():
            marker = self._glyph("›", ">")
            return self._fit(f"{marker} {text}", self.width)
        if self.is_claude_style():
            return self._dim(f"{self._branch_mid()} hint: {text}")
        return self._fit(f"Hint: {text}", self.width)

    def render_assistant_output(self, answer: str) -> str:
        lines = (answer or "").splitlines() or [""]
        if self.is_codex_style():
            return self.render_labeled_block("ASSISTANT", lines, min_inner_width=64)
        if self.is_claude_style():
            rendered_lines: list[str] = []
            in_code = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("```"):
                    in_code = not in_code
                    lang = stripped[3:].strip()
                    if in_code:
                        rendered_lines.append(self._dim(f"[code:{lang or 'text'}]"))
                    else:
                        rendered_lines.append(self._dim("[/code]"))
                    continue
                if in_code:
                    rendered_lines.append(self._accent(line))
                else:
                    rendered_lines.append(line)
            out = [
                self._dim(self._branch_start()) + self._ok(" assistant"),
                *[self._fit(f"{self._branch_mid()} {line}", self.width) for line in rendered_lines],
                self._dim(self._branch_end()),
            ]
            return "\n".join(out)
        out = ["助手>", f"  {lines[0]}"]
        out.extend(f"  {line}" for line in lines[1:])
        return "\n".join(out)

    def render_live_input(
        self,
        *,
        model: ShellInputModel,
        workspace_name: str,
        session_id: str,
        notifications: list[str] | None = None,
        runtime_summary: str = "",
        hint: str = 'Try "help me fix lint errors"',
    ) -> list[str]:
        palette_prefix = model.active_palette_prefix()
        palette_visible = bool(palette_prefix)
        suggestions = model.suggestions(limit=5) if palette_visible else []
        all_suggestions = model.suggestions(limit=32) if palette_visible else []
        notifications = notifications or []
        line_idx, col_idx = model.cursor_line_col()
        selected = model.selected_suggestion() if palette_visible else None
        selected_index = None
        if selected is not None:
            selected_index = next((idx for idx, item in enumerate(all_suggestions) if item.name == selected.name), None)
        window = model.input_window(limit=4)
        ghost_text = model.inline_ghost_text(hint=hint)
        cursor = self._accent("█") if self._supports_color() else "█"

        if self.is_codex_style():
            input_lines = model.buffer.split("\n") or [""]
            current_line = input_lines[line_idx] if line_idx < len(input_lines) else ""
            before = current_line[:col_idx]
            after = current_line[col_idx:]
            ghost = self._dim(ghost_text) if model.cursor == len(model.buffer) and ghost_text else ""
            mode_chip = self._mode_chip(model.mode)
            session_tail = session_id[-4:] if session_id else "----"
            status_line = f"{mode_chip}{self._sep()}s:{session_tail}"
            if runtime_summary:
                status_line += f"{self._sep()}{runtime_summary}"
            lines = [
                self._fit(f"> {before}{cursor}{ghost}{after}", self.width),
                self._fit(status_line, self.width),
            ]
            if suggestions:
                row_prefix = "$" if palette_prefix == "$" else "/"
                for item in suggestions:
                    active = selected is not None and item.name == selected.name
                    lines.append(self._fit(self._command_palette_line(item.name, item.description, prefix=row_prefix, active=active), self.width))
                if palette_prefix == "$":
                    lines.append(self._fit("Enter insert • Esc close • Shift+Tab/Ctrl+M mode", self.width))
                else:
                    lines.append(self._fit("Tab/Up/Down browse • Enter insert • Shift+Tab/Ctrl+M mode", self.width))
            return lines

        lines: list[str] = [self._rule(self._line_char())]
        lines.append(
            self._fit(
                self._live_status_line(
                    mode=model.mode,
                    focus=model.focus,
                    workspace_name=workspace_name,
                    session_id=session_id,
                    line_idx=line_idx,
                    col_idx=col_idx,
                    total_lines=window.total_lines,
                    selected=selected,
                    selected_prefix=palette_prefix,
                    runtime_summary=runtime_summary,
                ),
                self.width,
            )
        )
        for offset, raw_line in enumerate(window.lines):
            actual_line = window.start_line + offset
            content = raw_line
            if actual_line == line_idx:
                before = raw_line[:col_idx]
                after = raw_line[col_idx:]
                ghost = self._dim(ghost_text) if model.cursor == len(model.buffer) and ghost_text else ""
                content = before + cursor + ghost + after
            if offset == 0 and window.start_line > 0:
                content = self._glyph("… ", "... ") + content
            if offset == len(window.lines) - 1 and window.start_line + len(window.lines) < window.total_lines:
                content = content + " ..."
            prefix = self._accent(">") if actual_line == line_idx else self._dim(self._glyph("·", "|"))
            lines.append(self._fit(f"{prefix} {content}", self.width))
        lines.extend(
            self._live_footer_lines(
                model=model,
                suggestions=suggestions,
                selected=selected,
                total_suggestions=len(all_suggestions),
                selected_index=selected_index,
                notifications=notifications,
                palette_prefix=palette_prefix if palette_visible else "",
            )
        )
        lines.append(self._rule(self._line_char()))
        return lines

    def _compact_section(self, title: str, lines: list[str]) -> list[str]:
        heading = f"{self._accent('*')}{self._label(title)}"
        return [heading, *[self._fit(line, self.width) for line in lines]]

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
        plain = ShellRenderer._strip_ansi(text)
        plain_width = ShellRenderer._display_width(plain)
        if plain_width <= width:
            return text
        truncated_plain = ShellRenderer._truncate_display_width(plain, max(width - 3, 0)) + "..."
        if plain == text:
            return truncated_plain
        return truncated_plain

    @staticmethod
    def _shorten_middle(text: str, width: int) -> str:
        if ShellRenderer._display_width(text) <= width:
            return text
        if width <= 3:
            return ShellRenderer._truncate_display_width(text, width)
        body_width = width - 3
        head_width = body_width // 2
        tail_width = body_width - head_width
        head = ShellRenderer._truncate_display_width(text, head_width)
        tail_chars: list[str] = []
        current = 0
        for ch in reversed(text):
            ch_width = 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
            if current + ch_width > tail_width:
                break
            tail_chars.append(ch)
            current += ch_width
        tail = "".join(reversed(tail_chars))
        return head + "..." + tail

    @staticmethod
    def _workspace_name(path: str) -> str:
        normalized = path.rstrip("\\/")
        if not normalized:
            return ""
        if "\\" in normalized:
            return normalized.rsplit("\\", 1)[-1]
        if "/" in normalized:
            return normalized.rsplit("/", 1)[-1]
        return normalized

    def _rule(self, char: str = "-") -> str:
        return self._dim(char * self.frame_width)

    def _kv(self, label: str, value: str) -> str:
        styled = self._label(label)
        plain = self._strip_ansi(styled)
        return styled + (" " * max(14 - self._display_width(plain), 0)) + value

    def _recent_line(self, value: str) -> str:
        return f"  {value}"

    def _command_line(self, value: str) -> str:
        command, _, description = value.partition("  ")
        command = command.strip()
        description = description.strip()
        if not description:
            return f"  {command}"
        styled = self._accent(command)
        plain = self._strip_ansi(styled)
        return f"  {styled}" + (" " * max(22 - self._display_width(plain), 1)) + description

    def _command_palette_line(self, command: str, description: str, *, prefix: str = "/", active: bool = False) -> str:
        command_prefix = "$" if prefix == "$" else "/"
        styled = self._accent(command_prefix + command)
        plain = self._strip_ansi(styled)
        prefix = self._ok(">") if active else " "
        line = f"{prefix} {styled}" + (" " * max(18 - self._display_width(plain), 1)) + description
        return self._reverse(line) if active else line

    def _live_status_line(
        self,
        *,
        mode: ShellMode,
        focus: ShellInputFocus,
        workspace_name: str,
        session_id: str,
        line_idx: int,
        col_idx: int,
        total_lines: int,
        selected: ShellCommandSpec | None,
        selected_prefix: str = "",
        runtime_summary: str = "",
    ) -> str:
        parts = [
            self._mode_chip(mode),
            self._focus_chip(focus),
            f"L{line_idx + 1}/{max(total_lines, 1)}:C{col_idx + 1}",
            f"ws:{workspace_name}",
            "mode:Shift+Tab/Ctrl+M",
        ]
        if session_id:
            parts.append(f"s:{session_id[-4:]}")
        if selected is not None:
            prefix = selected_prefix if selected_prefix in {"/", "$"} else "/"
            parts.append(f"{prefix}{selected.name}")
        if runtime_summary:
            parts.append(runtime_summary)
        return self._sep().join(parts)

    def _live_footer_lines(
        self,
        *,
        model: ShellInputModel,
        suggestions: list[ShellCommandSpec],
        selected: ShellCommandSpec | None,
        total_suggestions: int,
        selected_index: int | None,
        notifications: list[str],
        palette_prefix: str = "",
    ) -> list[str]:
        lines: list[str] = []
        separator = self._sep().strip()
        enter_action = "Enter insert" if model.should_confirm_suggestion_on_enter() else "Enter send"
        palette_label = "skills" if palette_prefix == "$" else "commands"
        if suggestions:
            lines.append(
                self._fit(
                    self._dim(
                        f"Tab/Up/Down to browse {palette_label} {separator} {enter_action} {separator} Shift+Tab/Ctrl+M mode {separator} Ctrl+P focus {separator} Ctrl+J newline"
                    ),
                    self.width,
                )
            )
            if selected is not None:
                position = ""
                if selected_index is not None and total_suggestions > 0:
                    position = f" [{selected_index + 1}/{total_suggestions}]"
                prefix = "$" if palette_prefix == "$" else "/"
                lines.append(self._fit(f"{self._dim('->')} {prefix}{selected.name}  {selected.description}{position}", self.width))
            else:
                lines.append(self._fit(self._dim("Pick a command to insert at cursor"), self.width))
            lines.extend(
                self._live_suggestion_rows(
                    suggestions=suggestions,
                    selected=selected,
                    max_rows=3,
                    prefix="$" if palette_prefix == "$" else "/",
                )
            )
            hidden = max(0, total_suggestions - len(suggestions))
            if hidden:
                kind = "skill" if palette_prefix == "$" else "command"
                lines.append(self._fit(self._dim(f"... +{hidden} more {kind} matches"), self.width))
            if palette_prefix == "$":
                lines.append(self._fit(self._dim("Press enter to insert or esc to close"), self.width))
        else:
            lines.append(
                self._fit(
                    self._dim(
                        f"{enter_action} {separator} Ctrl+J newline {separator} Shift+Tab/Ctrl+M mode {separator} Ctrl+P focus"
                    ),
                    self.width,
                )
            )
            if not model.buffer:
                tip = (
                    "Tip: describe goals/constraints first in PLAN mode, then execute."
                    if model.mode is ShellMode.PLAN
                    else "Tip: describe your task directly, or type / for commands"
                )
                lines.append(self._fit(self._dim(tip), self.width))
            else:
                lines.append(self._fit(self._dim("Type / for commands, or $ for skills"), self.width))
        preview = model.pending_command_preview()
        if preview:
            action = "insert" if model.should_confirm_suggestion_on_enter() else "run"
            lines.append(self._fit(self._dim(f"Enter will {action}: {preview}"), self.width))
        for item in notifications[-2:]:
            lines.append(self._fit(f"{self._dim('Notice')} {item}", self.width))
        mode_hint = (
            "Plan mode (Shift+Tab/Ctrl+M; fallback: /plan /act)"
            if model.mode is ShellMode.PLAN
            else "Act mode (Shift+Tab/Ctrl+M; fallback: /plan /act)"
        )
        hint_text = self._warn(mode_hint) if model.mode is ShellMode.PLAN else self._dim(mode_hint)
        lines.append(self._fit(hint_text, self.width))
        minimum_rows = 4 if suggestions else 3
        while len(lines) < minimum_rows:
            lines.append("")
        return lines

    def _live_suggestion_rows(
        self,
        *,
        suggestions: list[ShellCommandSpec],
        selected: ShellCommandSpec | None,
        max_rows: int,
        prefix: str = "/",
    ) -> list[str]:
        rows: list[str] = []
        if max_rows <= 0:
            return rows
        for item in suggestions:
            if selected is not None and item == selected:
                continue
            desc = self._truncate_display_width(item.description, max(self.width - 18, 12))
            line = f"   {prefix}{item.name}"
            if desc:
                line += f"  {desc}"
            rows.append(self._fit(self._dim(line), self.width))
            if len(rows) >= max_rows:
                break
        return rows

    def _mode_chip(self, mode: ShellMode) -> str:
        if mode is ShellMode.PLAN:
            return self._warn("[规划]")
        return self._ok("[行动]")

    def _focus_chip(self, focus: ShellInputFocus) -> str:
        if focus is ShellInputFocus.COMMAND:
            return self._label("[命令]")
        return self._dim("[输入]")

    def _mode_badge(self, mode: ShellMode) -> str:
        if mode is ShellMode.PLAN:
            return f"{self._warn('[PLAN]')}  Analyze first | Shift+Tab/Ctrl+M toggle | Enter send"
        return f"{self._ok('[ACT]')}  Execute directly | Shift+Tab/Ctrl+M toggle | Enter send"

    def _input_focus_badge(self, focus: ShellInputFocus, *, line_idx: int, col_idx: int) -> str:
        focus_text = "command palette" if focus is ShellInputFocus.COMMAND else "editing input"
        return self._dim(f"focus: {focus_text} | cursor: line {line_idx + 1}, col {col_idx + 1}")

    def _tool_card_badge(self, status: str) -> str:
        normalized = status.lower().strip()
        if normalized in {"failed", "error"}:
            return self._warn("[FAILED]")
        if normalized in {"running", "queued"}:
            return self._label("[RUNNING]")
        return self._ok("[DONE]")

    @staticmethod
    def _tool_card_kind_chip(card_kind: str) -> str:
        mapping = {
            "file": "[FILE]",
            "shell": "[SHELL]",
            "search": "[WEB]",
            "team": "[TEAM]",
            "todo": "[TODO]",
        }
        return mapping.get(card_kind.strip().lower(), "[TOOL]")

    def _subagent_badge(self, status: str) -> str:
        normalized = status.lower().strip()
        if normalized == "failed":
            return self._warn("[FAILED]")
        if normalized == "running":
            return self._label("[RUNNING]")
        if normalized == "queued":
            return self._dim("[QUEUED]")
        return self._ok("[DONE]")

    def _section_title(self, text: str) -> str:
        return self._label(text)

    def _mode_text(self, mode: ShellMode) -> str:
        return "模式: 规划" if mode is ShellMode.PLAN else "模式: 行动"

    def _supports_color(self) -> bool:
        if not self.color_enabled:
            return False
        if os.environ.get("NO_COLOR"):
            return False
        return sys.stdout.isatty()

    def _color(self, name: str, text: str) -> str:
        if not self._supports_color():
            return text
        return f"{self._palette[name]}{text}{self._palette['reset']}"

    def _accent(self, text: str) -> str:
        return self._color("cyan", text)

    def _label(self, text: str) -> str:
        return self._color("blue", text)

    def _ok(self, text: str) -> str:
        return self._color("green", text)

    def _warn(self, text: str) -> str:
        return self._color("yellow", text)

    def _dim(self, text: str) -> str:
        return self._color("gray", text)

    def _reverse(self, text: str) -> str:
        return self._color("reverse", text)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        result: list[str] = []
        i = 0
        while i < len(text):
            if text[i] == "\033" and i + 1 < len(text) and text[i + 1] == "[":
                i += 2
                while i < len(text) and text[i] not in "ABCDEFGHJKSTfmnsu":
                    i += 1
                i += 1
                continue
            result.append(text[i])
            i += 1
        return "".join(result)

    @staticmethod
    def _display_width(text: str) -> int:
        width = 0
        for ch in text:
            width += 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
        return width

    @staticmethod
    def _truncate_display_width(text: str, max_width: int) -> str:
        if max_width <= 0:
            return ""
        result: list[str] = []
        current = 0
        for ch in text:
            ch_width = 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
            if current + ch_width > max_width:
                break
            result.append(ch)
            current += ch_width
        return "".join(result)


__all__ = [
    "ShellCommandSpec",
    "ShellInputFocus",
    "ShellInputModel",
    "ShellMode",
    "ShellRenderer",
    "LockBoardData",
    "QueueBoardData",
    "SubagentCardData",
    "TaskBoardData",
    "TeamBoardData",
    "TimelineGroupData",
    "ToolCardData",
    "TodoBoardData",
    "ShellWelcomeData",
]

