# CodeLite

> A local **Python TUI coding agent runtime** with real task isolation, safety rails, runtime governance, and a unified validation pipeline.

`TUI` `Runtime` `Task/Worktree` `Safety Hooks` `Memory/Retrieval` `Watchdog` `Validate Pipeline` `Subagents` `MCP`

CodeLite is not a chat wrapper pretending to be an agent.

It is a local coding-agent runtime that runs inside a workspace, exposes a real TUI shell, tracks sessions and tasks, isolates work with managed worktrees, applies execution-time safety gates, and closes work with a single validation entrypoint.

If you want the shortest description:

> **CodeLite turns a local CLI agent into a system with boundaries, state, observability, and proof.**

## Why CodeLite

### 1. A TUI runtime, not a glorified prompt box

CodeLite starts as an interactive shell, but the shell is only the surface.
Behind it is a composed runtime with session storage, task state, todo snapshots, context compaction, heartbeats, watchdog recovery, delivery queues, validation, and extension points.

### 2. Task execution is actually isolated

Tasks are not “just prompts with IDs”.

CodeLite binds:

- `task`
- `lease`
- `managed worktree`
- `AgentLoop execution`
- `metadata + evidence writeback`

That `task -> lease -> worktree -> agent execution` chain is one of the most important design points in this repo.

### 3. Safety is enforced at runtime

The agent is constrained by real execution gates, not only by prompt instructions.

- `PolicyGate` enforces workspace boundaries and shell allowlists
- `HookRuntime` adds pre/post tool hooks and validation-failure hooks
- high-risk git actions are blocked
- protected runtime state paths are guarded

### 4. Long-running behavior is governed, not hand-waved

CodeLite includes real runtime mechanisms for:

- todo tracking
- context compaction
- heartbeats
- watchdog recovery
- reconciler cleanup
- delivery queue recovery
- lane scheduling

This is the difference between a “demo agent” and a runtime that can explain what it is doing and recover when things go wrong.

### 5. Done means validated

CodeLite ships with a unified validation pipeline:

```text
build -> lint-arch -> test -> verify
```

That flow is exposed through a single entrypoint:

```powershell
python scripts/validate.py
```

## What It Looks Like

### Start the shell

```powershell
python -m pip install -e .
python -m codelite.cli shell --label DemoAgent
```

### Use the TUI as a runtime workbench

```text
/help
/session
/todo
/context
/memory
/runtime refresh
/ops all
/watchdog
/lanes
/delivery
/validate run
```

### Run a quick health and validation pass

```powershell
python -m codelite.cli health --json
python scripts/validate.py
```

## Core Capabilities

| Capability | What it means in this repo | Real entrypoints |
| --- | --- | --- |
| `AgentLoop + ToolRouter` | The core turn loop is stateful, tool-aware, session-aware, and runtime-instrumented. | `codelite/core/loop.py`, `codelite/core/tools.py` |
| `Task + Worktree isolation` | Tasks can be executed in managed git worktrees instead of contaminating one shared workspace. | `codelite/core/task_runner.py`, `codelite/core/worktree.py` |
| `Todo / Context / Memory / Retrieval` | Planning, context control, long-term memory, and retrieval are separate runtime concerns instead of one giant prompt. | `codelite/core/todo.py`, `codelite/core/context.py`, `codelite/core/memory_runtime.py`, `codelite/core/retrieval.py` |
| `Heart / Watchdog / Reconcile` | Runtime health is tracked, scanned, and recovered with explicit artifacts. | `codelite/core/heartbeat.py`, `codelite/core/watchdog.py`, `codelite/core/reconcile.py` |
| `Delivery / Lanes` | Work items can be queued, recovered, retried, and scheduled with lane generation control. | `codelite/core/delivery.py`, `codelite/core/lanes.py` |
| `Validate Pipeline` | Completion is gated by one validation flow, not by subjective “looks done”. | `codelite/core/validate_pipeline.py`, `scripts/validate.py` |
| `Agent Team / Subagent / MCP / Skills` | The runtime already exposes extension surfaces for multi-agent work and external tool interoperability. | `codelite/core/agent_team.py`, `codelite/core/mcp_runtime.py`, `codelite/core/skills_runtime.py` |

## Architecture In One Screen

```text
CLI / TUI shell
  -> build_runtime (composition root)
    -> AgentLoop
      -> ToolRouter
        -> PolicyGate + HookRuntime + Permission checks
    -> TaskRunner + WorktreeManager
    -> Todo / Context / Memory / Retrieval / Model routing
    -> Heart / Watchdog / Reconcile / Cron
    -> Delivery / Lanes / Background / AgentTeam / MCP
    -> ValidatePipeline
```

In short:

- `cli.py` composes the runtime
- `AgentLoop` drives turns
- `ToolRouter` controls execution
- `TaskRunner` binds tasks to isolated worktrees
- runtime services keep the system observable and recoverable
- `ValidatePipeline` defines what “done” means

## Why This Is Not A Demo Toy

CodeLite is backed by three proof layers:

### 1. Source-level mechanisms

Key files:

- [`codelite/cli.py`](./codelite/cli.py)
- [`codelite/core/task_runner.py`](./codelite/core/task_runner.py)
- [`codelite/core/validate_pipeline.py`](./codelite/core/validate_pipeline.py)

### 2. Automated regression coverage

Core tests live in:

- [`tests/core/`](./tests/core/)

Representative areas covered in tests include:

- shell/TUI behavior
- task/worktree binding
- runtime services
- validation failure trace handling
- retrieval, memory, model routing
- agent team / subagent / MCP / skills compatibility

### 3. Human-readable acceptance evidence

Acceptance bundles live in:

- [`docs/acceptance/`](./docs/acceptance/)

They capture:

- what was completed
- how to manually test it
- what outputs prove the mechanism works
- saved command outputs and runtime snapshots

If you want a single top-level closure bundle, start here:

- [`docs/acceptance/2026-04-08-final-project-complete-state/README.md`](./docs/acceptance/2026-04-08-final-project-complete-state/README.md)

## Quick Start

### Requirements

- Python `>= 3.11`
- a local git workspace for worktree-backed task execution

### Install

```powershell
python -m pip install -e .
```

### Open the TUI shell

```powershell
python -m codelite.cli shell --label DemoAgent
```

Or, after installation:

```powershell
codelite shell --label DemoAgent
```

### Run health and validation

```powershell
python -m codelite.cli health --json
python scripts/validate.py
```

## Repo Map

| Path | Role |
| --- | --- |
| [`codelite/cli.py`](./codelite/cli.py) | CLI entrypoint, shell runtime, composition root |
| [`codelite/core/`](./codelite/core/) | Agent loop, tools, task/worktree, runtime mechanisms |
| [`codelite/hooks/`](./codelite/hooks/) | Tool hooks and validation-failure hook handling |
| [`codelite/tui/`](./codelite/tui/) | TUI rendering and shell UI structures |
| [`scripts/validate.py`](./scripts/validate.py) | Unified validation entrypoint |
| [`tests/core/`](./tests/core/) | Regression coverage for shell, runtime services, orchestration, validation |
| [`docs/acceptance/`](./docs/acceptance/) | Stage-by-stage acceptance bundles and artifacts |

## Current Status

- Package metadata is currently `0.2.1`
- Acceptance coverage in the repo also includes post-`0.2.1` additions such as:
  - `Agent Team`
  - `Subagent processing`
  - `MCP entrypoint`
  - external `SKILL.md` compatibility

This means the repo already contains a capability timeline that goes beyond a bare “single-loop CLI agent”.

## Design Direction

CodeLite is opinionated about local agent engineering:

- stay inside the workspace
- make execution boundaries explicit
- prefer runtime evidence over claims
- treat validation as a first-class closing gate
- expose internal mechanisms in the TUI instead of hiding them behind logs

## See Also

- [`docs/acceptance/README.md`](./docs/acceptance/README.md)
- [`docs/plans/2026-04-09-tui-runtime-workbench-design.md`](./docs/plans/2026-04-09-tui-runtime-workbench-design.md)
- [`AGENTS.md`](./AGENTS.md)

---

**CodeLite is built to show that a local coding agent can be more than a prompt loop: it can be a bounded, inspectable, recoverable runtime.**
