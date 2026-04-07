# Current Completed State Acceptance Bundle

Date: 2026-04-07

This bundle is the cumulative acceptance package for everything implemented so far.

It covers:

- `v0.0` minimum runnable CLI loop
- core tools and safety rails
- session persistence and replay
- task state machine and lease model
- managed Git worktree support
- task-to-worktree execution binding

## Completed Mechanisms

### 1. CLI Foundation

- `python -m codelite.cli version`
- `python -m codelite.cli health --json`
- `python -m codelite.cli session replay --last 1`
- interactive shell entry via `python -m codelite.cli`

### 2. Runtime Persistence

- global event stream in `runtime/events.jsonl`
- per-session replay files in `runtime/sessions/*.jsonl`
- health snapshot can read runtime counts

### 3. Core Tooling

- `bash`
- `read_file`
- `write_file`
- `edit_file`

### 4. Safety Rails

- dangerous shell command blocking
- workspace path escape blocking
- limited safe command allowlist for `bash`

### 5. Task State Machine

- task statuses: `pending`, `leased`, `running`, `blocked`, `done`
- lease acquisition
- lease renewal
- normal release and completion
- conflict detection
- expired lease reconciliation

### 6. Managed Worktrees

- managed worktree creation
- managed worktree listing
- managed worktree removal
- branch naming per task
- runtime metadata in `runtime/worktrees/.index/*.json`
- isolation verified in dedicated tests

### 7. Task Runner Binding

- `python -m codelite.cli task run --task-id ...`
- `python -m codelite.cli task list --json`
- `python -m codelite.cli task show --task-id ... --json`
- execution acquires a lease, prepares a worktree, runs inside that worktree, and writes task metadata back to the root runtime
- root repo files remain unchanged while worktree files can diverge

### 8. Deterministic Manual Demo

- `python scripts/manual_task_run_binding_demo.py`
- prints a predictable JSON summary for manual acceptance

### 9. Test Coverage Available Now

- `tests/core/test_v00_smoke.py`
- `tests/core/test_tasks_leases.py`
- `tests/core/test_worktree_isolation.py`
- `tests/core/test_task_run_worktree_binding.py`

Current full core regression result at packaging time:

- `python -m pytest tests/core -q` -> `23 passed`

## Not Completed Yet

These are still outside the acceptance scope of this bundle:

- task-to-worktree binding directly inside `AgentLoop`
- lane scheduler
- cron scheduler
- heart service
- watchdog
- delivery queue
- validate pipeline
- retrieval router
- memory system
- model router

## What To Read First

1. `manual-commands.md`
2. `artifacts/command-output/`
3. `artifacts/runtime/`

## Artifact Layout

- `artifacts/command-output/`
  Stores command outputs captured while preparing this bundle.
- `artifacts/runtime/current-workspace/`
  Stores snapshots copied from the current repository runtime.
- `artifacts/runtime/worktree-demo/`
  Stores snapshots from a temporary isolated Git repo used to demonstrate worktree behavior safely.

## Notes

- For task and lease files, the filename may contain `<task_id>-<hash>`, but the actual `task_id` stored inside the file is the logical task id.
- Some historical command output may contain garbled Chinese text in terminal capture; this does not invalidate the mechanism itself.
- Worktree isolation manual testing is documented using a temporary demo repo on purpose, so we do not pollute the current project with extra demo branches and linked worktrees.
