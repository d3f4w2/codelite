# Phase 0 / v0.0 + Task Lease 验收包

日期：2026-04-07

本验收包覆盖两部分内容：

- `v0.0` 最小闭环
- Phase 1 的第一块基础件：任务状态机与租约模型

本次已经人工验收过的机制：

- CLI 基础入口：`version`、`health`、`session replay`
- 运行时持久化：`runtime/events.jsonl`、`runtime/sessions/*.jsonl`
- 基础工具：`bash`、`read_file`、`write_file`、`edit_file`
- 基础安全护栏：危险命令拦截、路径越界拦截
- 任务状态机：`pending -> leased -> running -> done`
- 租约冲突拦截
- 过期租约回收：`running -> blocked`

本验收包不覆盖的机制：

- `WorktreeManager`
- `LaneScheduler`
- `CronScheduler`
- `HeartService`
- `Watchdog`
- `DeliveryQueue`
- `ValidatePipeline`

人工验收时重点关注：

- 命令是否能稳定返回，而不是只看代码存在
- 护栏是否真的阻止了危险路径和危险命令
- 任务状态是否严格按状态机流转
- 租约文件和任务文件是否与状态一致

样本产物说明：

- `artifacts/command-output/`：保存本次人工验收命令的实际输出
- `artifacts/runtime/events.jsonl`：事件流快照
- `artifacts/runtime/sessions/`：回放样本会话
- `artifacts/runtime/tasks/`：任务状态样本
- `artifacts/runtime/leases/`：有效租约样本
- `artifacts/runtime/manual-file.txt`：文件工具链样本

补充说明：

- `runtime/tasks/*.json` 和 `runtime/leases/*.lock` 的文件名采用 `<task_id>-<hash>` 形式。
- 文件名里的 hash 只是磁盘落盘键的一部分，不属于真实 `task_id`。

本次样本中三类代表性任务：

- `manual-demo-*`：正常从 `leased -> running -> done`
- `conflict-demo-*`：演示租约冲突，保留有效 `.lock`
- `expired-demo-*`：演示过期租约回收，任务进入 `blocked`

使用方式：

1. 先看 `manual-commands.md`
2. 再对照 `artifacts/command-output/`
3. 最后再看 `artifacts/runtime/` 中对应样本

备注：

- 某些历史输出里的中文在 PowerShell 控制台可能显示乱码；当前阶段判断标准以“机制是否生效”为主。
- 这份验收包的目标是让后续任何人不读源码，也能知道当前阶段有哪些机制、如何手测、什么结果算通过。
