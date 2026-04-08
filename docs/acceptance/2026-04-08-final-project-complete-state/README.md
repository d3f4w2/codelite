# 项目最终封版验收包

日期：2026-04-08

这份验收包是项目结束时的最终总包，覆盖从 `v0.0` 到 `v0.2.1` 的全部已完成能力。

它不是替代历史分阶段验收包，而是把“现在这个项目完整交付时到底具备什么、怎么验证、有哪些真实输出”收拢成一个总入口。

本包覆盖的能力范围：

- `v0.0`
  - CLI 主循环
  - 基础工具
  - 基础安全护栏
  - 事件持久化与会话回放
- Phase 1
  - 任务状态机
  - 租约模型
  - 受管 `worktree`
  - `task -> worktree` 执行绑定
- `v0.2`
  - `TodoManager`
  - `ContextCompact`
  - `CronScheduler`
  - `HeartService`
  - `Watchdog`
  - `Reconciler`
- `v0.2.1`
  - `LaneScheduler`
  - `DeliveryQueue`
  - `ResilienceRunner`
  - `ValidatePipeline`
  - `AGENTS + Hooks`
  - `RetrievalRouter`
  - `MemoryMVP`
  - `Skill/Plan Runtime`
  - `ModelRouter + CriticRefiner`

## 本包里的内容

- [manual-commands.md](C:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-08-final-project-complete-state/manual-commands.md)
  最终项目的全量人工验收命令清单，按机制分组。
- `artifacts/command-output/`
  本次最终验收命令的实际输出样本。
- `artifacts/runtime/phase0-demo/`
  为避免污染当前项目而单独创建的基础能力演示工作区。
- `artifacts/runtime/worktree-demo/`
  用于验证受管 `worktree` 与 `task run` 的隔离 git 仓库样本。
- `artifacts/runtime/current-workspace/`
  最终封版时从当前项目复制的运行时快照，重点保留 `delivery-queue / lanes / hooks / memory / critic / background / metrics / watchdog`。

## 验收策略

为了“全量覆盖”同时又避免把主仓库弄脏，本包采用两类环境：

1. 当前项目仓库
   用来验收：
   - `hooks / skills / retrieval / memory / critic / validate`
   - 最终 `pytest tests/core -q`

2. 隔离 demo 工作区
   用来验收：
   - 基础工具与安全护栏
   - 租约与过期回收
   - `worktree` 创建与 `task run` 绑定

## 和历史包的关系

如果你想看某个阶段更细的背景解释，仍然建议同步查看：

- [2026-04-07-phase-0-v00-and-task-lease/README.md](C:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-07-phase-0-v00-and-task-lease/README.md)
- [2026-04-07-current-completed-state/README.md](C:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-07-current-completed-state/README.md)
- [2026-04-08-phase-3-v02-runtime-services/README.md](C:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-08-phase-3-v02-runtime-services/README.md)
- [2026-04-08-v021-nine-mechanisms/README.md](C:/Users/24719/Desktop/codelite/docs/acceptance/2026-04-08-v021-nine-mechanisms/README.md)

但如果只想判断“项目现在是否已经完整交付”，看这一包就够了。
