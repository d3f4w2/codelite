# v0.2 Runtime Services 验收包

日期：2026-04-08

这份验收包只覆盖本次新增完成的 `v0.2` 机制，不重复前面已经验收过的内容。

已在更早验收包中覆盖、这里不再重复的部分：

- CLI 基础入口：`version / health / session replay`
- 基础工具与安全护栏：`bash / read_file / write_file / edit_file`
- 任务状态机与租约模型
- 受管 `worktree` 创建、隔离、删除
- `task run/show/list` 与 worktree 绑定

本次新增并纳入人工验收的 `v0.2` 机制：

- `TodoManager`
  - 新增 `todo_write` 工具
  - 新增 `todo show` CLI 查询
- `ContextCompact`
  - 长会话自动生成压缩快照
  - 新增 `context show` CLI 查询
- `CronScheduler`
  - 内置 `heartbeat_scan / task_reconcile / compact_maintenance / metrics_rollup`
  - 支持 `cron list / cron run / cron tick`
- `HeartService`
  - 组件心跳写入 `runtime/hearts.jsonl`
  - 支持 `heart beat / heart status`
- `Watchdog`
  - 支持 `watchdog simulate / watchdog scan`
  - 生成诊断快照并给出恢复动作
- `Reconciler`
  - 通过 `task_reconcile` 自动回收过期租约
  - 通过 `metrics_rollup` 输出运行时指标快照

本验收包不覆盖的部分：

- `v0.2.1` 才引入的 `LaneScheduler`
- `DeliveryQueue`
- `ResilienceRunner`
- `ValidatePipeline`
- `RetrievalRouter / MemoryMVP / ModelRouter`

推荐阅读顺序：

1. 先看 `manual-commands.md`
2. 再结合 `runtime/` 中真实生成的 `todos / context / hearts / metrics / watchdog`
3. 最后看自动化回归结果 `python -m pytest tests/core -q`

验收重点：

- 新机制必须能通过 CLI 或受控脚本稳定触发，而不是只存在于代码里
- 新落盘数据必须能在 `runtime/` 下被看见、被解释、被复盘
- 不重复测试历史包已经证明过的基础链路
- `v0.2` 的新增能力要能和旧能力一起工作，不能把 `v0.0/Phase 1` 打回去
