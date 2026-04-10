from __future__ import annotations

import io
import json
import shutil
import uuid
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from codelite.cli import CodeLiteShell, build_runtime, main
from codelite.core.llm import ModelResult
from codelite.tui import (
    ShellCommandSpec,
    ShellInputFocus,
    ShellInputModel,
    ShellMode,
    ShellRenderer,
    ShellWelcomeData,
    SubagentCardData,
    TeamBoardData,
    ToolCardData,
)


class SimpleShellModelClient:
    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> ModelResult:
        del messages, tools
        return ModelResult(text="done", tool_calls=[])


class TimeoutFailShellModelClient:
    def __init__(self) -> None:
        self.request_timeout_sec: float | None = None

    def stream_complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        *,
        on_event: object | None = None,
        request_timeout_sec: float | None = None,
    ) -> ModelResult:
        del messages, tools, on_event
        self.request_timeout_sec = request_timeout_sec
        rendered_timeout = "unknown"
        if request_timeout_sec is not None:
            rounded = round(float(request_timeout_sec), 3)
            nearest_int = round(rounded)
            rendered_timeout = (
                str(int(nearest_int))
                if abs(rounded - nearest_int) < 0.05
                else f"{rounded:.3f}".rstrip("0").rstrip(".")
            )
        raise RuntimeError(
            f"shell turn timed out after {rendered_timeout}s while waiting for model response"
        )


@pytest.fixture(autouse=True)
def clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CODELITE_CONFIG_PATH",
        "CODELITE_EMBEDDING_API_KEY",
        "CODELITE_LLM_API_KEY",
        "CODELITE_RERANK_API_KEY",
        "CODELITE_SHELL_STYLE",
        "CODELITE_REASONING_EFFORT",
        "CODELITE_WORKSPACE_ROOT",
        "OPENAI_REASONING_EFFORT",
        "TAVILY_API_KEY",
        "EDITOR",
        "VISUAL",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def workspace_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    base_dir = repo / "tests" / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    workspace = base_dir / f"shell-ui-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_shell_renderer_renders_welcome_panels() -> None:
    renderer = ShellRenderer(width=110)
    data = ShellWelcomeData(
        version="0.2.1",
        session_id="demo-session",
        model_name="gpt-test",
        provider="openai",
        workspace_root="C:\\Users\\demo\\workspace",
        current_dir="C:\\Users\\demo\\workspace",
        health_summary="green=5 | unknown=2",
        recent_activity=["No recent activity"],
        tips=["/help", "/plan", "/act", "/status"],
        workspace_name="workspace",
        capability_summary=["LLM=demo", "Tools=11", "MCP=0", "Memory=3"],
        reasoning_effort="xhigh",
        quick_suggestion="Summarize recent commits",
    )

    rendered = renderer.render_welcome(data)

    assert "CodeLite (v0.2.1)" in rendered
    assert "model:    gpt-test xhigh" in rendered
    assert "directory: C:\\Users\\demo\\workspace" in rendered
    assert "Tip: New Build faster with Codex." not in rendered
    assert "Summarize recent commits" not in rendered
    assert "100% left" not in rendered
    assert renderer.prompt(workspace_root="C:\\Users\\demo\\workspace", session_id="demo-session")


def test_shell_renderer_codex_live_input_is_compact() -> None:
    renderer = ShellRenderer(width=110)
    model = ShellInputModel(
        commands=[ShellCommandSpec(name="plan", description="switch to planning mode")],
        mode=ShellMode.ACT,
    )
    model.set_buffer("help me fix lint")

    lines = renderer.render_live_input(model=model, workspace_name="test", session_id="demo-abcd")
    rendered = "\n".join(lines)

    assert len(lines) == 2
    assert lines[0].startswith("> ")
    assert "█" in lines[0]
    assert "s:abcd" in lines[1]
    assert "L1/1:C" not in rendered


def test_shell_renderer_codex_slash_palette_expands_on_prefix_even_if_focus_resets() -> None:
    renderer = ShellRenderer(width=110)
    model = ShellInputModel(
        commands=[
            ShellCommandSpec(name="plan", description="switch to planning mode"),
            ShellCommandSpec(name="status", description="show runtime health"),
        ],
        mode=ShellMode.ACT,
    )
    model.set_buffer("/")
    model.focus = ShellInputFocus.EDITOR

    lines = renderer.render_live_input(model=model, workspace_name="test", session_id="demo-abcd")
    rendered = "\n".join(lines)

    assert "/plan" in rendered
    assert "/status" in rendered
    assert "Tab/Up/Down browse" in rendered


def test_shell_renderer_renders_live_input_state() -> None:
    renderer = ShellRenderer(width=110, style="claude")
    model = ShellInputModel(
        commands=[
            ShellCommandSpec(name="plan", description="switch to planning mode"),
            ShellCommandSpec(name="status", description="show runtime health"),
        ],
        mode=ShellMode.PLAN,
    )
    model.set_buffer("/p")

    lines = renderer.render_live_input(model=model, workspace_name="test", session_id="demo-abcd")
    rendered = "\n".join(lines)

    assert "/plan" in rendered
    assert "Tab/Up/Down to browse" in rendered
    assert "Shift+Tab/Ctrl+M mode" in rendered
    assert "Ctrl+P focus" in rendered
    assert "-> /plan  switch to planning mode [1/1]" in rendered
    assert "Enter will insert: /plan" in rendered
    assert "/plan /act" in rendered


def test_shell_renderer_live_input_highlights_cursor_line_after_scroll() -> None:
    renderer = ShellRenderer(width=110, style="claude")
    model = ShellInputModel(commands=[ShellCommandSpec(name="plan", description="switch to planning mode")])
    model.set_buffer("line1\nline2\nline3\nline4\nline5\nline6")
    model.cursor = model.buffer.find("line5")

    lines = renderer.render_live_input(model=model, workspace_name="test", session_id="demo-abcd")

    assert any("line5" in line and line.startswith("> ") for line in lines)
    assert all(not (line.startswith("> ") and "line3" in line) for line in lines)


def test_shell_renderer_welcome_uses_session_fallback_and_status_items() -> None:
    renderer = ShellRenderer(width=110, style="claude")
    data = ShellWelcomeData(
        version="0.2.1",
        session_id="",
        model_name="gpt-test",
        provider="openai",
        workspace_root="C:\\Users\\demo\\workspace",
        current_dir="C:\\Users\\demo\\workspace",
        health_summary="green=5",
        recent_activity=[],
        tips=["/help"],
        workspace_name="workspace",
        todo_summary="todo=3",
        task_summary="running=1",
    )

    rendered = renderer.render_welcome(data)

    assert "session:----" in rendered
    assert "health:green=5" in rendered
    assert "todo:todo=3" in rendered
    assert "task:running=1" in rendered


def test_shell_renderer_shorten_middle_respects_cjk_display_width() -> None:
    path = "C:\\椤圭洰鐩綍\\瀛愮洰褰昞\闈炲父闀跨殑鏂囦欢璺緞\\main.py"
    shortened = ShellRenderer._shorten_middle(path, 18)

    assert "..." in shortened
    assert ShellRenderer._display_width(shortened) <= 18


def test_shell_renderer_renders_multiline_prompt_with_notifications() -> None:
    renderer = ShellRenderer(width=110, style="claude")
    model = ShellInputModel(
        commands=[
            ShellCommandSpec(name="plan", description="switch to planning mode"),
            ShellCommandSpec(name="status", description="show runtime health"),
        ],
        mode=ShellMode.ACT,
    )
    model.insert("first line")
    model.insert_newline()

    lines = renderer.render_live_input(
        model=model,
        workspace_name="test",
        session_id="demo-abcd",
        notifications=["[Cron] hello"],
    )
    rendered = "\n".join(lines)

    assert "first line" in rendered
    assert "Notice [Cron] hello" in rendered


def test_shell_renderer_renders_tool_cards_with_kind_and_overflow() -> None:
    renderer = ShellRenderer(width=110)
    cards = [
        ToolCardData(
            tool_name="web_search",
            card_kind="search",
            status="ok",
            title=f"Search Card #{idx}",
            lines=["query: test", "results: 3", "source: example.com", "answer: preview"],
        )
        for idx in range(7)
    ]

    rendered = renderer.render_tool_cards(cards)

    assert "... 1 earlier tool cards hidden" in rendered
    assert "[WEB]" in rendered
    assert "... +1 more lines" in rendered


def test_shell_renderer_renders_team_board_compact_subagent_rows() -> None:
    renderer = ShellRenderer(width=110)
    team = TeamBoardData(
        summary="team=1 | subagent=1 | running=1",
        team_lines=["research (team1234) | strategy=parallel | max=3"],
        subagent_cards=[
            SubagentCardData(
                subagent_id="subagent-12345678",
                team_id="team-12345678",
                status="running",
                prompt="Collect and compare sources",
                session_id="session-12345678",
                result_preview="",
                error="",
                used_web_search=True,
            )
        ],
    )

    rendered = renderer.render_team_board(team)

    assert "Subagents (1):" in rendered
    assert "web=yes" in rendered
    assert "session=12345678" in rendered


def test_shell_input_model_toggles_mode_and_autocompletes() -> None:
    model = ShellInputModel(
        commands=[
            ShellCommandSpec(name="plan", description="switch to planning mode"),
            ShellCommandSpec(name="status", description="show runtime health"),
        ]
    )

    model.insert("/")
    assert [item.name for item in model.suggestions()] == ["plan", "status"]
    assert model.focus is ShellInputFocus.COMMAND

    model.insert("p")
    assert [item.name for item in model.suggestions()] == ["plan"]
    assert model.pending_command_preview() == "/plan"
    assert model.autocomplete() is True
    assert model.buffer == "/plan "

    assert model.mode is ShellMode.ACT
    assert model.toggle_mode() is ShellMode.PLAN
    assert model.mode is ShellMode.PLAN
    assert model.toggle_mode() is ShellMode.ACT
    assert model.mode is ShellMode.ACT


def test_shell_input_model_enter_confirm_logic_for_partial_and_exact_command() -> None:
    model = ShellInputModel(
        commands=[
            ShellCommandSpec(name="plan", description="switch to planning mode"),
            ShellCommandSpec(name="status", description="show runtime health"),
        ]
    )

    model.set_buffer("/pl")
    assert model.should_confirm_suggestion_on_enter() is True

    model.set_buffer("/plan")
    assert model.should_confirm_suggestion_on_enter() is False


def test_shell_input_model_supports_skill_palette_insert() -> None:
    model = ShellInputModel(
        commands=[ShellCommandSpec(name="plan", description="switch to planning mode")],
        skills=[
            ShellCommandSpec(name="1password", description="[Skill] Set up and use 1Password CLI"),
            ShellCommandSpec(name="figma", description="[Skill] Use Figma MCP"),
        ],
    )

    model.set_buffer("$")
    assert model.active_palette_prefix() == "$"
    assert [item.name for item in model.suggestions()] == ["1password", "figma"]
    assert model.should_confirm_suggestion_on_enter() is True
    assert model.confirm_suggestion() is True
    assert model.buffer == "$1password "


def test_shell_renderer_renders_skill_palette_state() -> None:
    renderer = ShellRenderer(width=110)
    model = ShellInputModel(
        commands=[ShellCommandSpec(name="plan", description="switch to planning mode")],
        skills=[
            ShellCommandSpec(name="1password", description="[Skill] Set up and use 1Password CLI"),
            ShellCommandSpec(name="figma", description="[Skill] Use Figma MCP"),
        ],
        mode=ShellMode.ACT,
    )
    model.set_buffer("$")

    lines = renderer.render_live_input(model=model, workspace_name="test", session_id="demo-abcd")
    rendered = "\n".join(lines)

    assert "$1password" in rendered
    assert "Enter insert" in rendered
    assert "Esc close" in rendered


def test_shell_command_specs_use_chinese_descriptions() -> None:
    specs = {item.name: item.description for item in CodeLiteShell._command_specs()}
    assert specs["model"] == "模型/韧性/评审面板"
    assert specs["help"] == "显示本地命令帮助"


def test_shell_command_specs_include_resume_rename_and_subagents() -> None:
    specs = {item.name for item in CodeLiteShell._command_specs()}
    assert {"resume", "rename", "subagents", "new"} <= specs


def test_shell_command_help_and_specs_stay_in_sync() -> None:
    help_names = {
        line.strip().split()[0][1:]
        for line in CodeLiteShell._command_help_lines()
        if line.strip().startswith("/")
    }
    spec_names = {item.name for item in CodeLiteShell._command_specs()}

    assert help_names == spec_names
    assert {"plan", "act", "mode", "resume", "rename", "subagents", "team"} <= help_names
    assert {"version", "critic", "watchdog", "lanes", "delivery", "background"} <= help_names


def test_shell_live_input_mode_toggle_helper_covers_supported_keys() -> None:
    assert CodeLiteShell._is_live_input_mode_toggle("\r", ctrl_pressed=True) is True
    assert CodeLiteShell._is_live_input_mode_toggle("\t", shift_pressed=True) is True
    assert CodeLiteShell._is_live_input_mode_toggle("\x1b", escape_sequence="[Z") is True
    assert CodeLiteShell._is_live_input_mode_toggle("\x00", extended_key="\x0f") is True
    assert CodeLiteShell._is_live_input_mode_toggle("\xe0", extended_key="\x94") is True
    assert CodeLiteShell._is_live_input_mode_toggle("\r") is False
    assert CodeLiteShell._is_live_input_mode_toggle("\x1b", escape_sequence="[A") is False


def test_shell_hidden_aliases_remain_callable_but_stay_out_of_help(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    help_names = {
        line.strip().split()[0][1:]
        for line in CodeLiteShell._command_help_lines()
        if line.strip().startswith("/")
    }
    assert {"health", "banner", "planner", "accept", "reset", "q"}.isdisjoint(help_names)

    shell = CodeLiteShell(build_runtime(workspace_dir))
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/health") is True
        assert shell._handle_local_command("/banner") is True
        assert shell._handle_local_command("/planner") is True
        assert shell._handle_local_command("/accept") is True
        assert shell._handle_local_command("/reset") is True

    output = stdout.getvalue()

    assert '"version":' in output
    assert "CodeLite" in output
    assert ShellMode.PLAN.status_text in output
    assert ShellMode.ACT.status_text in output
    assert "Started new session:" in output


def test_shell_public_workbench_commands_are_discoverable_and_callable(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    shell = CodeLiteShell(build_runtime(workspace_dir))
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/version") is True
        assert shell._handle_local_command("/critic") is True
        assert shell._handle_local_command("/watchdog") is True
        assert shell._handle_local_command("/lanes") is True
        assert shell._handle_local_command("/delivery") is True
        assert shell._handle_local_command("/background") is True

    output = stdout.getvalue()

    assert "Watchdog Panel" in output
    assert "Lanes / Delivery Panel" in output
    assert "Model / Resilience / Critic Panel" in output
    assert "Queue Board" in output
    assert "MCP / Background / Validate Panel" in output


def test_shell_prints_welcome_screen_before_prompt(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "exit"

    monkeypatch.setattr("builtins.input", fake_input)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = CodeLiteShell(build_runtime(workspace_dir)).run()

    output = stdout.getvalue()

    assert exit_code == 0
    assert "CodeLite" in output
    assert "/model to change" in output
    assert "Tip: New Build faster with Codex." not in output
    assert "Summarize recent commits" not in output
    assert len(prompts) == 1


def test_shell_slash_commands_are_available(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    responses = iter(["/help", "/session", "/new", "/exit"])

    def fake_input(prompt: str) -> str:
        del prompt
        return next(responses)

    monkeypatch.setattr("builtins.input", fake_input)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = CodeLiteShell(build_runtime(workspace_dir)).run()

    output = stdout.getvalue()

    assert exit_code == 0
    assert "/plan" in output
    assert '"current_session_id":' in output
    assert "Started new session:" in output


def test_shell_rename_and_resume_by_session_id(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))
    original_session = shell.session_id

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/rename alpha thread") is True
        assert shell._handle_local_command("/new") is True
        assert shell._handle_local_command(f"/resume {original_session}") is True

    output = stdout.getvalue()

    assert "Thread renamed: alpha thread" in output
    assert "Resumed session: alpha thread" in output
    assert shell.session_id == original_session
    assert shell.services.session_store.session_title(original_session) == "alpha thread"


def test_shell_resume_selector_supports_number_pick(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))
    first_session = shell.session_id
    shell._handle_local_command("/rename first thread")
    shell._handle_local_command("/new")

    sessions = shell.services.session_store.list_session_summaries(limit=20, query="")
    pick_index = next(
        index
        for index, item in enumerate(sessions, start=1)
        if str(item.get("session_id", "")) == first_session
    )
    monkeypatch.setattr("builtins.input", lambda prompt: str(pick_index))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/resume") is True

    output = stdout.getvalue()

    assert "Resume a previous session" in output
    assert shell.session_id == first_session


def test_shell_subagents_command_reuses_team_runtime(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/subagents 使用subagents，给我发一段话") is True

    output = stdout.getvalue()

    assert "Waiting for" in output
    assert "Finished waiting" in output
    assert "团队汇总结论（合并版）" in output
    assert any(item.status == "done" for item in shell.services.agent_team_runtime.list_subagents(limit=10))


def test_shell_team_default_runs_demo_and_prints_board(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/team") is True

    output = stdout.getvalue()

    assert "Waiting for 3 agents" in output
    assert "团队汇总结论（合并版）" in output
    assert "Team Board" in output
    records = shell.services.agent_team_runtime.list_subagents(limit=20)
    done_count = sum(1 for item in records if item.status == "done")
    assert done_count == 3
    assert all(item.agent_type == "explore" for item in records if item.status == "done")


def test_shell_team_board_only_does_not_spawn_subagents(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/team board") is True

    output = stdout.getvalue()

    assert "Team Board" in output
    assert "no subagent records" in output
    assert "Waiting for" not in output
    assert shell.services.agent_team_runtime.list_subagents(limit=10) == []


def test_shell_team_run_caps_dynamic_tasks_by_team_limit(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/team run 1) 任务一；2) 任务二；3) 任务三；4) 任务四") is True

    output = stdout.getvalue()

    assert "Waiting for 3 agents" in output
    assert "并行分工" in output
    done_count = sum(1 for item in shell.services.agent_team_runtime.list_subagents(limit=20) if item.status == "done")
    assert done_count == 3


def test_shell_run_turn_defaults_to_compact_post_turn_summary(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    services = build_runtime(workspace_dir, model_client=SimpleShellModelClient())
    shell = CodeLiteShell(services)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        shell._run_agent_turn("summarize current task")

    output = stdout.getvalue()
    tasks = services.task_store.list_tasks()

    assert "[USER]" not in output
    assert "[STATUS]" in output
    assert output.count("[STATUS]") == 1
    assert "[ASSISTANT]" in output
    assert "[DONE] response ready" in output
    assert "[TASK]" not in output
    assert "[RECV]" in output
    assert "[RETR]" not in output
    assert "[THINK]" in output
    assert "done" in output
    assert "/view full" not in output
    assert "Tool Cards" not in output
    assert "Team Board" not in output
    assert "Queue Board" not in output
    assert "Task Board" not in output
    assert "Lock Board" not in output
    assert tasks


def test_shell_run_turn_timeout_prints_error_and_skips_done(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    services = build_runtime(workspace_dir, model_client=TimeoutFailShellModelClient())
    services.config = replace(
        services.config,
        runtime=replace(services.config.runtime, shell_timeout_sec=1),
    )
    shell = CodeLiteShell(services)

    stdout = io.StringIO()
    with pytest.raises(RuntimeError, match="shell turn timed out after 1s while waiting for model response"):
        with redirect_stdout(stdout):
            shell._run_agent_turn("say hello slowly")

    output = stdout.getvalue()

    assert "[STATUS]" in output
    assert "[THINK]" in output
    assert "[ERR] shell turn timed out after 1s while waiting for model response" in output
    assert "[DONE] response ready" not in output
    assert "[ASSISTANT]" not in output
    assert shell._submitted_live_prompt == ""
    assert shell._assistant_live_text == ""
    assert shell._turn_history[-1]["status"] == "error"


def test_shell_live_turn_lines_keep_submitted_prompt_visible(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    shell._submitted_live_prompt = "hi"
    shell._status_lines_current_turn = [shell.renderer.render_runtime_event("think", "thinking")]
    shell._status_events_current_turn = [("think", "thinking")]

    rendered = "\n".join(shell._render_live_turn_lines())

    assert "> hi" in rendered
    assert "[STATUS]" in rendered
    assert "[THINK] thinking" in rendered


def test_shell_view_command_switches_post_turn_density(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/view") is True
        assert shell._handle_local_command("/view full") is True
        assert shell._handle_local_command("/view compact") is True

    output = stdout.getvalue()

    assert "compact/full" in output
    assert "full" in output
    assert "compact" in output


def test_slash_plan_executes_inline_prompt(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    shell = CodeLiteShell(build_runtime(workspace_dir))
    captured: dict[str, str] = {}

    def fake_run_turn(session_id: str, user_input: str) -> str:
        captured["session_id"] = session_id
        captured["user_input"] = user_input
        return "planned"

    monkeypatch.setattr(shell.services.agent_loop, "run_turn", fake_run_turn)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        handled = shell._handle_local_command("/plan hi")

    output = stdout.getvalue()

    assert handled is True
    assert "[STATUS]" in output
    assert "[ASSISTANT]" in output
    assert "planned" in output
    assert captured["session_id"] == shell.session_id
    assert captured["user_input"].startswith("[shell-mode=plan]")
    assert captured["user_input"].endswith("hi")


def test_slash_act_executes_inline_prompt(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    shell = CodeLiteShell(build_runtime(workspace_dir))
    captured: dict[str, str] = {}

    def fake_run_turn(session_id: str, user_input: str) -> str:
        captured["session_id"] = session_id
        captured["user_input"] = user_input
        return "acted"

    monkeypatch.setattr(shell.services.agent_loop, "run_turn", fake_run_turn)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        handled = shell._handle_local_command("/act hi")

    output = stdout.getvalue()

    assert handled is True
    assert "[STATUS]" in output
    assert "[ASSISTANT]" in output
    assert "acted" in output
    assert captured["session_id"] == shell.session_id
    assert captured["user_input"] == "hi"


def test_shell_mode_command_queries_switches_and_runs_inline_prompt(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    shell = CodeLiteShell(build_runtime(workspace_dir))
    captured: dict[str, str] = {}

    def fake_run_turn(session_id: str, user_input: str) -> str:
        captured["session_id"] = session_id
        captured["user_input"] = user_input
        return "mode inline"

    monkeypatch.setattr(shell.services.agent_loop, "run_turn", fake_run_turn)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/mode") is True
        assert shell._handle_local_command("/mode plan") is True
        assert shell._handle_local_command("/mode act hi") is True

    output = stdout.getvalue()

    assert ShellMode.ACT.status_text in output
    assert ShellMode.PLAN.status_text in output
    assert "mode inline" in output
    assert shell.mode is ShellMode.ACT
    assert captured["session_id"] == shell.session_id
    assert captured["user_input"] == "hi"


def test_main_routes_plain_prompt_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_cmd_run(args: object) -> int:
        captured["prompt"] = getattr(args, "prompt")
        captured["command"] = getattr(args, "command")
        return 7

    monkeypatch.setattr("codelite.cli.cmd_run", fake_cmd_run)

    exit_code = main(["create", "a", "logger"])

    assert exit_code == 7
    assert captured["command"] == "run"
    assert captured["prompt"] == ["create", "a", "logger"]


def test_shell_plan_mode_wraps_agent_prompt(workspace_dir: Path) -> None:
    shell = CodeLiteShell(build_runtime(workspace_dir))

    assert shell._agent_prompt("fix lint") == "fix lint"

    shell.mode = ShellMode.PLAN
    wrapped = shell._agent_prompt("fix lint")

    assert wrapped.startswith("[shell-mode=plan]")
    assert wrapped.endswith("fix lint")


def test_shell_plan_turn_sets_pending_confirmation_on_proposed_plan(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))
    shell.mode = ShellMode.PLAN

    def fake_run_turn(session_id: str, user_input: str) -> str:
        assert session_id == shell.session_id
        assert user_input.startswith("[shell-mode=plan]")
        return "<proposed_plan>\n- step 1\n- step 2\n</proposed_plan>"

    monkeypatch.setattr(shell.services.agent_loop, "run_turn", fake_run_turn)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        shell._run_agent_turn("draft implementation plan")

    output = stdout.getvalue()
    pending = shell._pending_plan_confirmation

    assert pending is not None
    assert pending.get("state") == "awaiting_plan_confirmation"
    assert "<proposed_plan>" in str(pending.get("plan_text", ""))
    assert "Press Enter to execute plan, or type edits to refine plan." in output


def test_shell_plan_confirmation_blank_input_executes_plan(workspace_dir: Path) -> None:
    shell = CodeLiteShell(build_runtime(workspace_dir))
    shell._pending_plan_confirmation = {"plan_text": "<proposed_plan>\n- A\n</proposed_plan>"}
    captured: dict[str, str] = {}

    def fake_execute(plan_text: str) -> None:
        captured["plan_text"] = plan_text

    shell._execute_confirmed_plan = fake_execute  # type: ignore[method-assign]

    assert shell._handle_plan_confirmation_input("") is True
    assert shell._pending_plan_confirmation is None
    assert "<proposed_plan>" in captured["plan_text"]


def test_shell_plan_confirmation_slash_act_executes_plan(workspace_dir: Path) -> None:
    shell = CodeLiteShell(build_runtime(workspace_dir))
    shell._pending_plan_confirmation = {"plan_text": "<proposed_plan>\n- A\n</proposed_plan>"}
    captured: dict[str, str] = {}

    def fake_execute(plan_text: str) -> None:
        captured["plan_text"] = plan_text

    shell._execute_confirmed_plan = fake_execute  # type: ignore[method-assign]

    assert shell._handle_plan_confirmation_input("/act") is True
    assert shell._pending_plan_confirmation is None
    assert "<proposed_plan>" in captured["plan_text"]


def test_shell_plan_confirmation_text_input_revises_plan(workspace_dir: Path) -> None:
    shell = CodeLiteShell(build_runtime(workspace_dir))
    shell._pending_plan_confirmation = {"plan_text": "<proposed_plan>\n- A\n</proposed_plan>"}
    captured: dict[str, str] = {}

    def fake_revision(*, feedback: str, plan_text: str) -> None:
        captured["feedback"] = feedback
        captured["plan_text"] = plan_text

    shell._run_plan_revision = fake_revision  # type: ignore[method-assign]

    assert shell._handle_plan_confirmation_input("Please add rollback steps") is True
    assert shell._pending_plan_confirmation is None
    assert captured["feedback"] == "Please add rollback steps"
    assert "<proposed_plan>" in captured["plan_text"]


def test_shell_plan_clarification_only_when_context_insufficient(workspace_dir: Path) -> None:
    shell = CodeLiteShell(build_runtime(workspace_dir))
    shell.mode = ShellMode.PLAN

    assert shell._needs_plan_clarification("optimize this") is True
    assert shell._needs_plan_clarification("帮我优化一下这个流程") is True
    assert shell._needs_plan_clarification("为 codelite/cli.py 新增计划确认门并补 tests/core/test_shell_welcome_ui.py") is False
    assert (
        shell._needs_plan_clarification(
            "Implement caching for retrieval responses; scope only codelite/core/retrieval.py; "
            "must keep API compatibility and add acceptance tests."
        )
        is False
    )


def test_shell_plan_clarification_accepts_tab_appended_note(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = CodeLiteShell(build_runtime(workspace_dir))
    shell.mode = ShellMode.PLAN
    shell._pending_plan_clarification = {
        "base_prompt": "优化一下",
        "questions": [
            {
                "question": "这次计划你更看重哪种输出深度？",
                "options": ["完整端到端方案（推荐）", "最小可执行路径", "仅高层方向"],
            }
        ],
        "answers": [],
        "cursor": 0,
    }
    captured: dict[str, str] = {}

    def fake_run_agent_turn(prompt: str) -> None:
        captured["prompt"] = prompt

    monkeypatch.setattr(shell, "_run_agent_turn", fake_run_agent_turn)

    handled = shell._handle_plan_clarification_input("1\t需要给出风险与回滚")

    assert handled is True
    assert shell._pending_plan_clarification is None
    assert "补充: 需要给出风险与回滚" in captured["prompt"]
    assert "<proposed_plan>" in captured["prompt"]


def test_shell_act_turn_emits_milestone_summary_lines(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        shell._run_agent_turn("run a simple task")

    milestones = [line for line in shell._status_lines_current_turn if "milestone " in line]
    assert len(milestones) >= 2


def test_shell_runtime_memory_skills_and_retrieval_commands_render(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    (workspace_dir / "README.md").write_text("runtime services are documented here\n", encoding="utf-8")
    shell = CodeLiteShell(build_runtime(workspace_dir))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/runtime refresh") is True
        assert shell._handle_local_command("/retrieval run Read README and summarize runtime services") is True
        assert shell._handle_local_command("/memory") is True
        assert shell._handle_local_command("/memory full 5") is True
        assert shell._handle_local_command("/memory keywords runtime") is True
        assert shell._handle_local_command("/skills load code-review") is True
        assert shell._handle_local_command("/skills review") is True

    output = stdout.getvalue()
    metrics_report_path = shell.services.layout.metrics_dir / "rollup-latest.json"

    assert "Runtime" in output
    assert "Retrieval" in output
    assert "Memory Full Ledger" in output
    assert "Skill Loaded" in output
    assert metrics_report_path.exists()


def test_shell_memory_remember_forget_and_prefs_commands(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/memory remember user Tone: concise") is True
        assert shell._handle_local_command("/memory prefs") is True
        assert shell._handle_local_command("/memory forget user Tone") is True
        assert shell._handle_local_command("/memory audit 5") is True

    output = stdout.getvalue()

    assert "Memory Remembered" in output
    assert "Effective Preferences" in output
    assert "Memory Forget" in output
    assert "Memory Audit" in output


def test_shell_pending_memory_candidate_yes_no_flow(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        shell._run_agent_turn("我喜欢回答简洁一点")
        assert shell._pending_memory_candidate is not None
        assert shell._handle_pending_memory_candidate_input("yes") is True

    output = stdout.getvalue()
    assert "检测到记忆候选" in output
    assert "记忆已保存:" in output

    prefs = shell.services.memory_runtime.effective_preferences()
    assert any("我喜欢回答简洁一点" in str(item.get("text", "")) for item in prefs)
    assert any(item.get("domain") == "soul" for item in prefs if "我喜欢回答简洁一点" in str(item.get("text", "")))


def test_shell_memory_candidate_domain_classification(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        shell._stage_memory_candidate("以后回答风格请简洁一点")
        assert shell._pending_memory_candidate is not None
        assert shell._pending_memory_candidate["domain"] == "soul"
        assert shell._handle_pending_memory_candidate_input("no") is True
        assert shell._pending_memory_candidate is None

        shell._stage_memory_candidate("记住我偏好先给结论再给细节")
        assert shell._pending_memory_candidate is not None
        assert shell._pending_memory_candidate["domain"] == "user"


def test_shell_memory_candidate_skips_normal_task_prompt(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        shell._stage_memory_candidate("请帮我修 lint 错误")

    assert shell._pending_memory_candidate is None


def test_shell_memory_unknown_command_shows_nl_hint(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/memory unknown") is True

    output = stdout.getvalue()
    assert "For natural language memory" in output


def test_shell_compact_keeps_recent_two_turns_and_injects_summary(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))
    shell._run_agent_turn("turn one")
    shell._run_agent_turn("turn two")
    shell._run_agent_turn("turn three")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/compact 2") is True

    output = stdout.getvalue()
    active_messages = shell.services.session_store.load_messages(shell.session_id)
    user_messages = [item for item in active_messages if item.get("role") == "user"]

    assert "Context Compacted" in output
    assert active_messages[0]["role"] == "system"
    assert "Compacted conversation summary:" in str(active_messages[0].get("content", ""))
    assert len(user_messages) == 2
    assert user_messages[0].get("content") == "turn two"
    assert user_messages[1].get("content") == "turn three"


def test_shell_mcp_selector_handles_empty_registry(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert shell._handle_local_command("/mcp") is True

    output = stdout.getvalue()
    assert "No MCP servers configured." in output


def test_shell_runtime_status_summary_contains_ctx(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    shell._latest_model_usage = {"input_tokens": 400, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    summary = shell._runtime_status_summary()

    assert "ctx" in summary
    assert "runtime" in summary


def test_shell_context_usage_estimate_uses_char_budget_only(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    shell.services.session_store.append_message(shell.session_id, role="user", content="hi")
    shell.services.session_store.append_message(shell.session_id, role="assistant", content="ok")
    shell._latest_model_usage = None

    usage = shell._context_usage_snapshot()

    assert usage["source"] == "estimate"
    assert usage["message_count"] == 2
    assert usage["char_count"] > 0
    assert usage["percent"] <= 1


def test_shell_context_usage_prefers_model_usage_when_available(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))

    shell._latest_model_usage = {"input_tokens": 22000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    usage = shell._context_usage_snapshot()
    expected = int(round((22000 * 100) / max(1, int(shell.services.config.runtime.context_auto_compact_char_count / 4))))

    assert usage["source"] == "usage"
    assert usage["tokens"] == 22000
    assert usage["percent"] == expected


def test_shell_cron_disable_and_enable_job(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))

    assert shell._handle_local_command('/cron every minute print "hello"') is True
    assert shell._handle_local_command("/cron disable shell_terminal_01") is True

    jobs = shell.services.cron_scheduler.list_jobs()
    target = next(item for item in jobs if item["name"] == "shell_terminal_01")
    assert target["enabled"] is False

    assert shell._handle_local_command("/cron enable shell_terminal_01") is True
    jobs = shell.services.cron_scheduler.list_jobs()
    target = next(item for item in jobs if item["name"] == "shell_terminal_01")
    assert target["enabled"] is True


def test_shell_prompt_status_line_renders_codex_left_and_effort(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    monkeypatch.setenv("CODELITE_REASONING_EFFORT", "xhigh")
    shell = CodeLiteShell(build_runtime(workspace_dir, model_client=SimpleShellModelClient()))
    shell._latest_model_usage = {"input_tokens": 400, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    line = shell._prompt_status_line()

    assert "xhigh" in line
    assert "% left" in line


def test_shell_codex_style_uses_live_input_when_tty_available(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyTTY:
        encoding = "utf-8"

        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    monkeypatch.setenv("CODELITE_SHELL_STYLE", "codex")
    monkeypatch.delenv("CODELITE_LIVE_INPUT", raising=False)
    monkeypatch.delenv("CODELITE_PLAIN_INPUT", raising=False)
    monkeypatch.setattr("codelite.cli.sys.stdin", DummyTTY())
    monkeypatch.setattr("codelite.cli.sys.stdout", DummyTTY())

    shell = CodeLiteShell(build_runtime(workspace_dir))

    assert shell.renderer.is_codex_style() is True
    assert shell._use_live_input() is True


def test_shell_plain_input_env_disables_live_input_when_tty_available(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyTTY:
        encoding = "utf-8"

        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    monkeypatch.setenv("CODELITE_SHELL_STYLE", "codex")
    monkeypatch.setenv("CODELITE_PLAIN_INPUT", "1")
    monkeypatch.delenv("CODELITE_LIVE_INPUT", raising=False)
    monkeypatch.setattr("codelite.cli.sys.stdin", DummyTTY())
    monkeypatch.setattr("codelite.cli.sys.stdout", DummyTTY())

    shell = CodeLiteShell(build_runtime(workspace_dir))

    assert shell._use_live_input() is False


def test_shell_quick_suggestion_prefers_git_then_todo_then_fallback(
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))

    monkeypatch.setattr(shell, "_workspace_has_git_commits", lambda: True)
    assert shell._quick_suggestion_text() == "Summarize recent commits"

    monkeypatch.setattr(shell, "_workspace_has_git_commits", lambda: False)
    assert shell._quick_suggestion_text() == "Help me plan next steps"

    shell.services.todo_manager.replace(
        shell.session_id,
        [{"id": "todo-1", "content": "check runtime", "status": "pending"}],
        source="manual",
    )
    assert shell._quick_suggestion_text() == "Review current TODO items"



def test_shell_slash_shows_command_help(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))

    responses = iter(["/", "/exit"])

    def fake_input(prompt: str) -> str:
        del prompt
        return next(responses)

    monkeypatch.setattr("builtins.input", fake_input)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = CodeLiteShell(build_runtime(workspace_dir)).run()

    output = stdout.getvalue()

    assert exit_code == 0
    assert "/plan" in output


def test_shell_nl_shortcut_can_toggle_cron_job(workspace_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODELITE_WORKSPACE_ROOT", str(workspace_dir))
    shell = CodeLiteShell(build_runtime(workspace_dir))
    assert shell._handle_local_command('/cron every minute print "hello"') is True

    assert shell._handle_nl_local_shortcut("disable shell_terminal_01") is True
    jobs = shell.services.cron_scheduler.list_jobs()
    target = next(item for item in jobs if item["name"] == "shell_terminal_01")
    assert target["enabled"] is False
