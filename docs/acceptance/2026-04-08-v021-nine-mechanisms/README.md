# v0.2.1 九项新增机制验收包

日期：2026-04-08

这份验收包只覆盖 `v0.2.1` 在 `v0.2` 基础上新增并完成的 9 项机制，不重复之前已经验收过的内容。

已在旧验收包中覆盖、这里不再重复的内容：

- CLI 基础入口：`version / health / session replay`
- 基础工具与安全护栏：`bash / read_file / write_file / edit_file`
- 任务状态机与租约模型
- 受管 `worktree`
- `task run/show/list`
- `v0.2` 的 `todo / context / cron / heart / watchdog / reconcile`

本次新增并纳入人工验收范围的 9 项机制：

1. `DeliveryQueue`
2. `ResilienceRunner`
3. `LaneScheduler`
4. `ValidatePipeline`
5. `AGENTS + Hooks`
6. `RetrievalRouter`
7. `MemoryMVP`
8. `Skill/Plan Runtime`
9. `ModelRouter + CriticRefiner`

这份包的重点不是“功能菜单有没有出现”，而是验证这 9 项机制是否已经形成了最小闭环：

- 有 CLI 或脚本入口
- 有运行时落盘证据
- 有自动化测试覆盖
- 能解释为什么这说明机制真正落地

建议阅读顺序：

1. `manual-commands.md`
2. `runtime/` 下新增的 `delivery-queue / lanes / hooks / memory / critic / background`
3. `python -m pytest tests/core -q`

本包不覆盖但仍属于后续扩展空间的部分：

- 更复杂的多模型真实路由策略
- 真正外部搜索提供商接入
- 更复杂的 skill 市场与远程 skill 拉取
- 更强的交叉 review 与失败规则自动回灌
