# CodeLite 初版强化方案（v0.2.1 封版）

日期：2026-04-07（v0.2.1 封版更新）  
目标：在 v0.2 基础上并入 9 项关键机制，形成“可并行、可恢复、可巡检、可评测、可进化”的工程封版方案。

## 0. v0.0 地基版（一切基础）

定位：`v0.0` 不是“功能版本”，而是“生存版本”。  
原则：如果 `v0.0` 不稳定，后续 `v0.1/v0.2/v0.2.1` 一律不推进。

`v0.0` 最小闭环（必须全部达标）：

1. `while-loop + tool_use dispatch` 可持续运行
2. 基础工具可用：`bash/read_file/write_file/edit_file`
3. 基础安全护栏：危险命令拦截、路径越界拦截
4. 会话可恢复：最小 JSONL 持久化与 replay
5. 最小可观测：事件日志可落盘、失败可定位

`v0.0` 出口门槛（Gate）：

- 主循环稳定运行（无人工干预）`>= 30 min`
- 基础工具调用成功率 `>= 95%`
- 危险命令误执行率 `0`
- 路径越界拦截率 `100%`

`v0.0` 交付物：

- 可运行 CLI 主循环
- `runtime/events.jsonl` 与 `runtime/sessions/*.jsonl`
- `tests/core/test_v00_smoke.py`

## 1. 强化目标（相对 v0.1）

本版不是“多加功能”，而是把执行强度拉满：

1. 从“单任务循环”升级到“多任务隔离并行”（`worktree + lanes`）
2. 从“人工触发维护”升级到“定时自治运维”（`cron + reconcile`）
3. 从“出问题后才发现”升级到“持续健康监控+自愈”（`heart + watchdog + resilience`）
4. 从“能跑 demo”升级到“有 SLO、有验收门槛、有封版清单”

## 2. 核心机制升级：Harness 10 件套（v0.2）

v0.2 的基础机制保留不变：

1. `AgentLoop`：plan -> act -> observe -> next
2. `ToolRouter`：bash/file/todo 统一调度
3. `TodoManager`：任务拆分与状态推进
4. `PolicyGate`：高风险操作拦截与确认
5. `ContextCompact`：micro/auto/manual 三层压缩
6. `EventBus`：全链路事件化与可视化
7. `WorktreeManager`：任务级代码隔离与并行执行
8. `CronScheduler`：定时任务调度与维护作业
9. `HeartService`：组件心跳、健康状态、负载快照
10. `Watchdog/Reconciler`：超时检测、僵尸任务回收、自动修复

## 2.1 v0.2.1 封版并入的 9 项机制（本次新增）

1. `DeliveryQueue`：预写日志、原子写、退避重试、死信队列
2. `ResilienceRunner`：三层重试洋葱（认证轮换/溢出压缩/fallback）
3. `LaneScheduler`：命名 lane 并发（main/cron/heartbeat）+ generation 保护
4. `ValidatePipeline`：统一 `build -> lint-arch -> test -> verify`
5. `AGENTS + Hooks`：规则仓库化、执行前后钩子化、可持续收敛
6. `RetrievalRouter`：LLM 决策“是否检索、检索哪里、何时停止”
7. `MemoryMVP`：`Raw Ledger + Derived Views + Policy` 最小闭环
8. `Skill/Plan Runtime`：`load_skill + todo nag + background tasks`
9. `ModelRouter + CriticRefiner`：模型分工路由、交叉 review、失败学习闭环

## 3. v0.2.1 架构草图（单体 CLI + 背景子系统）

```text
CLI (entry)
  -> SessionManager (JSONL replay/persist)
  -> AgentLoop (task state machine)
       -> Planner (todo + nag)
       -> ToolRouter (bash/file/skill/search)
       -> PolicyGate (allow/deny/ask)
       -> ContextManager (compact + offload)
       -> RetrievalRouter (need-search / stop-search)
       -> WorktreeManager (create/sync/cleanup)
       -> ModelRouter (fast/deep/review)
  -> LaneScheduler (main/cron/heartbeat lanes)
  -> EventBus (append-only)
       -> TUI Renderer (timeline/steps/cost/health)
       -> Logger (jsonl + metrics)
  -> CronScheduler (in-process timer)
       -> Maintenance Jobs (reconcile/gc/metrics/eval)
  -> HeartService (component heartbeat)
  -> Watchdog (stale-heart detection + auto-recovery)
  -> DeliveryQueue (WAL + backoff + failed)
  -> ResilienceRunner (retry onion + fallback chain)
  -> ValidatePipeline (build -> lint-arch -> test -> verify)
  -> MemorySystem2 (ledger/views/policy + provenance)
  -> EvalRunner (baseline compare + regression gate)
```

说明：v0.2.1 仍保持单进程优先；先把稳定性、治理性、可追溯做到位，再考虑 daemon 化。

## 4. 三大机制设计

### 4.1 Worktree 机制（任务隔离 + 并行）

目标：同一仓库并行执行多个任务时，避免改动互相污染。

规则：

1. 每个 `task_id` 对应一个独立 worktree：`./runtime/worktrees/wt-<task_id>-<slug>`
2. 每个 worktree 绑定一个分支：`task/<task_id>`
3. 同一 task 只能在一个 worktree 里 `in_progress`（租约锁防重入）
4. 任务完成后必须走 `verify -> merge -> cleanup`，不可直接遗留脏目录

生命周期：

1. `prepare`：创建 worktree + 同步基线分支
2. `lease`：领取任务租约（带 TTL）
3. `run`：Agent 在隔离目录执行工具链
4. `verify`：运行最小测试/静态检查
5. `merge`：通过门禁后合并回主分支
6. `cleanup`：删除 worktree、释放锁、归档日志

失败策略：

- `verify` 失败：保留 worktree 便于人工介入，任务状态转 `blocked`
- `merge` 冲突：自动 rebase 一次，失败则转人工处理
- 异常退出：由 `reconcile` 任务扫描并回收过期租约

保留策略：

- 成功任务：`cleanup` 后立即删除 worktree，仅保留事件与审计日志
- 失败任务：默认保留 `72h` 便于排障，超时后由 `worktree_gc` 清理
- 保留上限：失败保留 worktree 最多 `20` 个，超过上限按最早失败时间淘汰

### 4.2 Cron 机制（定时自治）

目标：把“保活、清理、统计、评测”从人工操作变成系统默认行为。

首批内置调度任务：

| Job | Cron 表达式 | 职责 | 失败处理 |
|---|---|---|---|
| `heartbeat_scan` | `*/1 * * * *` | 扫描心跳超时组件 | 失败重试 2 次，仍失败告警 |
| `task_reconcile` | `*/2 * * * *` | 回收过期租约、修复僵尸任务 | 标记 `needs_attention` |
| `worktree_gc` | `*/10 * * * *` | 清理已完成任务的残留 worktree | 保留最近 N=5 调试样本 |
| `compact_maintenance` | `*/15 * * * *` | 会话压缩和上下文瘦身 | 超限则分段压缩 |
| `metrics_rollup` | `0 * * * *` | 汇总每小时成功率/成本/耗时 | 失败写入补偿队列 |
| `eval_smoke` | `30 2 * * *` | 每日冒烟评测（小样本） | 连续失败 2 天触发红色告警 |
| `eval_full` | `0 3 * * 1` | 每周全量 B0/C1/C2/C2.1 对比评测 | 生成周报并锁定版本 |

时区与总开关：

- 默认时区：`Asia/Shanghai`（可配置覆盖）
- 全局开关：`scheduler.enabled=true/false`
- 单 job 开关：`jobs.<job_name>.enabled=true/false`
- 暂停语义：暂停期间不触发新任务；恢复后按下一个调度点继续

### 4.3 Heart 机制（心跳 + 健康态）

目标：实时知道系统“活着且健康”，不是只看进程是否存在。

心跳上报对象：

1. `agent_loop`
2. `tool_router`
3. `cron_scheduler`
4. `worktree_manager`
5. `event_bus`
6. `delivery_queue`
7. `lane_scheduler`

心跳字段（写入 `runtime/hearts.jsonl`）：

- `component_id`
- `timestamp`
- `status` (`green/yellow/red`)
- `queue_depth`
- `active_task_count`
- `last_error`
- `latency_ms_p95`

状态判定：

- `green`：最近 30 秒内有心跳且错误率正常
- `yellow`：30-90 秒无心跳或重试率升高
- `red`：超过 90 秒无心跳，或连续 3 次任务失败

阈值配置化：

- 阈值配置：`codelite/config/runtime.yaml`
- 默认值：
  - `heart.green_window_sec: 30`
  - `heart.yellow_window_sec: 90`
  - `heart.red_fail_streak: 3`
- 支持组件覆盖：`heart.components.<component_id>.*`

## 5. 可靠性与治理机制（v0.2.1 强化）

### 5.1 Watchdog 自愈

当 `heart` 进入 `red`：

1. 先尝试组件级重启（仅重启故障模块）
2. 回放最近事件日志进行状态修复
3. 对运行中任务执行“安全暂停 + 快照”
4. 超过 3 次重启失败则升级为人工确认模式

### 5.2 DeliveryQueue（新增）

- 写前落盘（WAL）后再投递
- 原子写入（tmp + replace）
- 退避重试（指数 + 抖动）
- 超过最大重试进入 `failed/` 死信队列
- 重启后扫描 pending 队列自动恢复

### 5.3 ResilienceRunner（新增）

三层重试洋葱：

1. `Layer-1`：认证配置轮换（冷却感知）
2. `Layer-2`：上下文溢出压缩后重试
3. `Layer-3`：标准 tool-use 循环重试

补充：主配置耗尽后执行 fallback model 链。

### 5.4 LaneScheduler（新增）

- 命名 lane：`main/cron/heartbeat`
- 每 lane FIFO 队列 + `max_concurrency`
- `generation` 递增后忽略旧代任务回灌
- 对外暴露 `queue_depth/active/max/generation` 统计

### 5.5 租约锁 + 幂等保护

- 任务锁：`runtime/leases/<task_id>.lock`（默认 TTL=15 分钟）
- 幂等键：`task_id + step_id + tool_name + args_hash`
- 防重复提交：同一窗口只允许一次结果提交

### 5.6 AGENTS + Hooks（新增）

- 在仓库根目录维护 `AGENTS.md`（短索引，不堆细节）
- 关键规则写入 hooks：`PreToolUse/PostToolUse/OnValidationFail`
- 高风险动作必须经 hook + policy gate 双重约束

### 5.7 ValidatePipeline（新增）

统一验证入口，避免“只跑测试”假通过：

1. `build`
2. `lint-arch`
3. `test`
4. `verify`（端到端功能验证）

## 6. 检索/记忆/技能/模型治理（新增）

### 6.1 RetrievalRouter（新增）

目标：系统能回答“为什么检索、检索哪里、何时停止”。

- 由 LLM 决策 `retrieve / no-retrieve`
- 支持 source 路由（本地文档/代码索引/外部搜索）
- 返回 `enough/insufficient` 判定，避免无效重复检索
- 检索行为写入审计日志用于离线评估

### 6.2 MemoryMVP（Raw Ledger + Views + Policy）

最小闭环：

1. `Raw Ledger`：append-only 记录记忆动作与证据
2. `Derived Views`：timeline/keyword/skill 等派生视图
3. `Policy`：读写更新遗忘策略（可回放）

关键要求：`views` 必须可回指 `ledger`，保证 provenance。

### 6.3 Skill/Plan Runtime（新增）

- `load_skill` 按需加载（metadata in prompt，body in tool_result）
- `todo nag`：若多轮不更新计划，系统注入提醒
- `background_run`：慢任务后台执行并通过通知队列回注

### 6.4 ModelRouter + CriticRefiner（新增）

- 模型路由：`fast`（小改）/`deep`（复杂实现）/`review`（交叉评审）
- 交叉 review：编码模型与评审模型分离
- Critic/Refiner：从失败样本提炼规则并反哺 hooks/文档

## 7. 评测升级（B0 / C1 / C2 / C2.1）

### 7.1 对照组定义

- `B0`：即 `v0.0`（裸 loop + 基础工具）
- `C1`：v0.1（todo + compact + policy + event）
- `C2`：v0.2（C1 + worktree + cron + heart + watchdog）
- `C2.1`：v0.2.1（C2 + delivery + resilience + lanes + validate + retrieval + memory + model routing）

### 7.2 任务集扩展（首批 50 条）

1. 小改动（12）
2. 中等改造（12）
3. 风险与恢复（10）
4. 并行冲突场景（8）
5. 检索与记忆场景（8）

### 7.3 指标集（v0.2.1）

1. 任务完成率
2. 工具调用成功率
3. 心跳异常发现时延（`P95 < 90s`）
4. 并行任务交叉污染率（`0`）
5. worktree 清理成功率（`>= 99%`）
6. 自动修复成功率（`>= 70%`）
7. Delivery 最终成功率（`>= 99%`）
8. 死信队列占比（`<= 1%`）
9. Resilience 自恢复成功率（`>= 80%`）
10. Validate 一次通过率
11. 检索命中率与无效检索率
12. 人工介入率与恢复时长（`P95 < 10min`）

### 7.4 验收门槛（v0.2.1）

- `C2.1` 相对 `C2`：任务完成率提升 `>= 8%`
- `C2.1` 相对 `B0`：任务完成率提升 `>= 35%`
- 危险命令误执行率：`0`
- 路径越界拦截率：`100%`
- 并行任务交叉污染：`0`
- 心跳红灯漏检率：`0`
- Delivery 数据丢失：`0`

## 8. 目录结构升级（v0.2.1）

```text
codelite/
  AGENTS.md
  README.md
  docs/
    plans/
    runbooks/
  scripts/
    validate.py
    lint_arch.py
    verify/
  codelite/
    cli.py
    config/
      runtime.yaml
    core/
      loop.py
      planner.py
      context.py
      policy.py
      worktree.py
      scheduler.py
      heartbeat.py
      watchdog.py
      reconcile.py
      lanes.py
      delivery.py
      resilience.py
      retrieval.py
      model_router.py
    memory/
      ledger.py
      views.py
      policy.py
    tools/
      bash.py
      file.py
      todo.py
      skills.py
      background.py
      search.py
    storage/
      sessions.py
      tasks.py
      events.py
      metrics.py
      audit.py
    hooks/
      pre_tool_use.py
      post_tool_use.py
      on_validation_fail.py
    tui/
      timeline.py
      renderer.py
      health_panel.py
    eval/
      benchmark.py
      datasets/
  runtime/
    hearts.jsonl
    audit.jsonl
    metrics/
    leases/
    worktrees/
    delivery-queue/
      failed/
```

## 9. 落地节奏（6 阶段，封版版）

### Phase 0（2 天）：骨架与约束

- 先达成 `v0.0 Gate`，作为后续阶段前置条件
- 固化任务状态机、事件模型、目录规范
- 接入基础 loop + tool router + session store

### Phase 1（3-4 天）：Worktree 并行隔离

- 实现 worktree create/sync/cleanup
- 接入任务租约锁与幂等键
- 完成并行冲突测试

### Phase 2（3-4 天）：Lanes + Delivery + Resilience

- 上线命名 lane 调度
- 上线 DeliveryQueue（WAL/backoff/dead-letter）
- 上线重试洋葱与 fallback model 链

### Phase 3（3 天）：Cron + Heart + Watchdog

- 完成 scheduler/reconcile/gc/metrics
- 完成心跳上报、健康分级、自愈链路
- 打通 health panel 可视化

### Phase 4（3 天）：Validate + AGENTS + Hooks + Skill Runtime

- 落地统一验证管道与 hook 执法
- 落地 `load_skill + todo nag + background_run`
- 形成可执行 runbook

### Phase 5（3-4 天）：Retrieval/Memory/Model Routing + 评测

- 落地检索路由与 MemoryMVP 闭环
- 落地模型路由与交叉 review
- 跑通 B0/C1/C2/C2.1 对比评测并冻结基线

## 10. 第一批可开工任务（v0.2.1 顺序）

1. 定义任务状态机与租约模型（`pending/leased/running/blocked/done`）
2. 实现 `WorktreeManager`（create/sync/remove）
3. 在 `AgentLoop` 中接入 task->worktree 绑定
4. 实现 `runtime/leases` 锁文件机制
5. 实现 `LaneScheduler` 与 lane stats
6. 实现 `DeliveryQueue`（WAL + backoff + failed）
7. 实现 `ResilienceRunner`（auth/profile/overflow/fallback）
8. 实现 `CronScheduler` 与 job 注册器
9. 完成 `task_reconcile + worktree_gc + metrics_rollup`
10. 实现 `HeartService`（组件定时上报）
11. 实现 `Watchdog`（心跳超时 -> 自愈动作）
12. 实现 `ValidatePipeline` 与 `scripts/validate.py`
13. 建立 `AGENTS.md` 与 hooks 框架
14. 实现 `RetrievalRouter`（need-search + enough 判定）
15. 实现 `MemoryMVP`（ledger/views/policy）
16. 接入 `load_skill + todo nag + background_run`
17. 实现 `ModelRouter`（fast/deep/review）
18. 实现交叉 review 与 Critic/Refiner 样本回流
19. 扩展 `eval/benchmark.py` 支持 C2.1
20. 补齐 runbook 与周报导出

## 11. 阶段验收命令清单（v0.2.1）

### Phase 0 验收

1. `python -m codelite.cli health --json`
2. `python -m codelite.cli session replay --last 1`
3. `python -m pytest tests/core/test_loop_smoke.py -q`

### Phase 1 验收

1. `python -m codelite.cli task run --task-id demo_parallel_01`
2. `python -m codelite.cli worktree list`
3. `python -m pytest tests/core/test_worktree_isolation.py -q`

### Phase 2 验收

1. `python -m codelite.cli lanes status --json`
2. `python -m codelite.cli delivery status --json`
3. `python -m codelite.cli resilience drill --scenario overflow_then_fallback`
4. `python -m pytest tests/core/test_lanes_delivery_resilience.py -q`

### Phase 3 验收

1. `python -m codelite.cli cron list`
2. `python -m codelite.cli heart status --json`
3. `python -m codelite.cli watchdog simulate --component tool_router`
4. `python -m pytest tests/core/test_scheduler_heartbeat_watchdog.py -q`

### Phase 4 验收

1. `python scripts/validate.py`
2. `python -m codelite.cli hooks doctor`
3. `python -m codelite.cli skills load --name code-review`
4. `python -m pytest tests/core/test_validate_hooks_skills.py -q`

### Phase 5 验收

1. `python -m codelite.eval.benchmark --profiles B0 C1 C2 C2.1 --report-json runtime/metrics/benchmark.json`
2. `python -m codelite.cli eval retrieval --dataset runtime/metrics/retrieval_eval.json`
3. `python -m codelite.cli report weekly --output runtime/metrics/weekly.md`
4. `python -m pytest tests/eval -q`

## 12. v0.2.1 封版清单（9 项）

- [ ] 1. DeliveryQueue 落地（WAL/原子写/退避重试/死信）
  完成定义：`delivery status` 可见 pending/failed；崩溃重启后可恢复。

- [ ] 2. Resilience 重试洋葱落地（auth/overflow/fallback）
  完成定义：故障演练中可自动切换并继续任务。

- [ ] 3. LaneScheduler 落地（命名 lane + generation）
  完成定义：`main/cron/heartbeat` 并行无互相饿死，旧代任务不回灌。

- [ ] 4. ValidatePipeline 落地（build->lint-arch->test->verify）
  完成定义：统一 `scripts/validate.py` 返回明确通过/失败。

- [ ] 5. AGENTS + Hooks 落地（规则仓库化）
  完成定义：关键操作触发 hook，违规可被前置阻断。

- [ ] 6. RetrievalRouter 落地（查不查/查哪里/够不够）
  完成定义：可导出检索决策日志并计算命中率/无效检索率。

- [ ] 7. MemoryMVP 落地（ledger/views/policy）
  完成定义：任一记忆结论可回指原始 ledger 证据。

- [ ] 8. Skill/Plan Runtime 落地（load_skill + todo nag + background）
  完成定义：长任务中计划不漂移，慢任务不中断主流程。

- [ ] 9. ModelRouter + Critic/Refiner 落地
  完成定义：实现/评审模型分离，失败样本可反哺规则更新。

## 13. 阿里 Harness 思想亮点验收评分表（新增）

目标：把“理念正确”转为“可执行、可验证、可复用”的项目亮点，并可用于周报与简历量化表述。

评分方式：

- 每项 `0-5` 分，合计 `20` 分。
- 封版目标：`>= 16/20`，且任一项不低于 `3/5`。

| 亮点项 | 0 分标准 | 3 分标准 | 5 分标准 | 证据产物 |
|---|---|---|---|---|
| `AGENTS.md + 规则仓库化` | 无 `AGENTS.md` 或仅口号 | 有导航与关键规则，但未和执行链路联动 | `AGENTS.md` 精简可导航，规则可追踪到 hooks 与 runbook | `AGENTS.md`、`docs/runbooks/*`、hooks 配置 |
| `统一验证管道` | 仅零散运行 test/lint | 有 `validate.py`，但未覆盖 `verify` 或无统一出口 | 固化 `build -> lint-arch -> test -> verify`，CI/本地同一入口 | `scripts/validate.py`、CI 日志、失败样例 |
| `协调者-执行者分工 + worktree + 交叉 review` | 协调者直接改代码，无隔离 | 复杂任务使用 worktree，但评审链路不稳定 | 复杂任务默认 `subagent + worktree`，并有跨模型交叉 review | 任务记录、worktree 记录、review 报告 |
| `Critic/Refiner 失败学习闭环` | 失败只重跑，不沉淀 | 有失败记录，但无规则反哺机制 | 失败样本结构化沉淀，定期产出规则更新并回归验证 | `trace/failures`、规则变更记录、回归结果 |

执行节奏（建议）：

1. 每周固定一次评分（建议周五）
2. 评分后必须输出“低分项修复动作”（最多 3 条）
3. 连续两周低于 `16/20`，暂停扩功能，优先修复工程闭环

---

v0.2.1 的目标不是“更复杂”，而是“更可靠、更可控、更能长期迭代”：并行不乱、故障可复原、决策可解释、结果可量化。
