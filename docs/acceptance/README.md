# Acceptance Bundles

This directory stores human-readable acceptance bundles for each completed stage.

Each bundle should answer three questions:

1. What is completed now?
2. How do we manually test it?
3. What output should we expect, and why does that prove the mechanism works?

## Bundle Structure

Each stage should use this layout:

- `docs/acceptance/YYYY-MM-DD-<slug>/README.md`
- `docs/acceptance/YYYY-MM-DD-<slug>/manual-commands.md`
- `docs/acceptance/YYYY-MM-DD-<slug>/artifacts/command-output/`
- `docs/acceptance/YYYY-MM-DD-<slug>/artifacts/runtime/`

## Current Bundles

### Historical Snapshot

- [2026-04-07-phase-0-v00-and-task-lease/README.md](c:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-07-phase-0-v00-and-task-lease/README.md)
  Covers the earlier acceptance snapshot for `v0.0` plus the first task/lease implementation.

### Current Completed State

- [2026-04-07-current-completed-state/README.md](c:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-07-current-completed-state/README.md)
  Covers everything completed so far: `v0.0`, core tools and safety rails, task leases, managed worktrees, and task-to-worktree execution binding.

### v0.2 Runtime Services

- [2026-04-08-phase-3-v02-runtime-services/README.md](c:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-08-phase-3-v02-runtime-services/README.md)
  Covers the newly completed `v0.2` runtime services only: todo manager, context compaction, cron jobs, heart service, watchdog, reconcile, and metrics rollup.

### v0.2.1 Nine New Mechanisms

- [2026-04-08-v021-nine-mechanisms/README.md](c:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-08-v021-nine-mechanisms/README.md)
  Covers only the nine new `v0.2.1` mechanisms: lanes, delivery, resilience, validate pipeline, AGENTS/hooks, retrieval, memory, skill/plan runtime, and model routing plus critic/refiner.

### Final Project Complete State

- [2026-04-08-final-project-complete-state/README.md](c:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-08-final-project-complete-state/README.md)
  The final closure bundle for the whole project, covering all completed capabilities from `v0.0` through `v0.2.1`, with fresh command outputs and runtime snapshots.

## Workflow For Future Stages

1. Create a new bundle directory.
2. Write a clear completed-mechanism list in `README.md`.
3. Write detailed manual commands in `manual-commands.md`.
4. For each command, include:
   - the command itself
   - the expected output
   - why that output proves the mechanism works
5. Save real command outputs into `artifacts/command-output/`.
6. Save representative runtime samples into `artifacts/runtime/`.

## Scaffold Command

```powershell
python scripts/scaffold_acceptance_bundle.py phase-slug --title "Bundle Title"
```

This creates the folder skeleton. The mechanism-specific content still needs to be filled in after implementation and manual validation.
