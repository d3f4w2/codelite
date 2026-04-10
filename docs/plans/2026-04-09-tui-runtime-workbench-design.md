# CodeLite TUI 运行台设计单

日期：2026-04-09  
目标：把当前 CLI shell 从“增强日志终端”升级成“机制驱动的可视化运行台”，让本项目已经具备的 runtime 机制在 TUI 中真正可见、可控、可操作。

## 1. 当前判断

当前 TUI 已经具备以下基础：

1. 中文欢迎头与基础输入区
2. `/` 命令面板
3. `plan/act` 模式切换
4. 过程时间线
5. `TODO / 任务 / 队列 / 锁` 白板
6. `cron / heart` 的 shell 命令入口

但当前形态仍然更接近“日志增强 shell”，还不是“可操作运行台”。  
主要问题不是功能缺失，而是**机制没有完成 UI 映射**。

## 2. 核心设计原则

### 2.1 非黑盒

用户需要知道：

1. 这一轮在想什么
2. 调了哪些工具
3. 读了哪些文件
4. 派发了哪些 subagent
5. 队列、锁、任务现在是什么状态
6. 为什么失败，下一步怎么办

### 2.2 中文友好

1. 默认文案优先中文
2. 输入提示、错误说明、运行状态优先中文
3. East Asian 宽度必须正确处理
4. 不把 PowerShell / Windows 错误原样暴露给用户，优先转成解释性文案

### 2.3 结构化显示而不是线性刷屏

优先用“固定区域 + 卡片 + 状态条 + 白板”来表达。  
尽量减少长日志堆叠。

### 2.4 机制先于样式

先把 runtime 机制接进 TUI，再做视觉 polish。  
没有机制支撑的花哨界面没有意义。

## 3. 当前已实现机制 vs TUI 落地度

### 3.1 已有机制，但 TUI 只做了浅层展示

1. `todo_manager`
状态：已有 `TODO 白板`
问题：只有快照，没有可编辑/可推进/可确认项。

2. `task_store + lease`
状态：已有 `任务白板` 和自动领取 shell turn 任务
问题：没有任务详情页、没有 lease 倒计时、没有手动操作。

3. `delivery_queue`
状态：已有 `消息队列` 摘要
问题：只有数量和少量条目，没有 retry/recover/process 操作。

4. `heart_service`
状态：已有 `/heart`
问题：没有持续可视化健康面板，没有变化高亮。

5. `cron_scheduler`
状态：已有 `/cron`
问题：缺少真正的 cron 面板，没有“最近执行记录 / 下一次触发 / 失败历史”。

6. `retrieval_router`
状态：已有时间线条目
问题：没有“检索来源卡片 / 检索结果卡片 / enoughness 决策面板”。

7. `agent_team + subagent`
状态：只有线性时间线与少量摘要
问题：缺少 team 看板，这是当前最重要的缺口。

### 3.2 已有机制，但几乎没进 TUI

1. `lanes`
CLI 已有，TUI 没有 lane 视图。

2. `background`
CLI 已有，TUI 没有后台任务面板。

3. `watchdog`
CLI 已有，TUI 没有故障扫描与恢复面板。

4. `model_router / resilience / critic`
TUI 没有“为什么选这个 profile / fallback 发生了什么 / critic 学到了什么”。

5. `mcp_runtime`
TUI 没有 MCP server 列表、最近调用、失败原因。

6. `validate_pipeline`
TUI 没有 build/lint/test/verify 的统一面板。

## 4. 目标架构：TUI 运行台

目标形态不是单一打印流，而是固定布局：

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 顶栏：工作区 / 模型 / 会话 / 健康 / 当前模式                                 │
├──────────────────────────────┬───────────────────────────────┬──────────────┤
│ 左栏：主时间线 / 分组过程     │ 中栏：工具卡片 / Team看板     │ 右栏：白板   │
│ - 接收 / 检索 / 推理          │ - read_file / bash / web      │ - TODO      │
│ - 关键事件折叠                │ - subagent 卡片               │ - 任务      │
│                               │                               │ - 队列      │
│                               │                               │ - 锁        │
├──────────────────────────────┴───────────────────────────────┴──────────────┤
│ 底栏：输入框 / 命令面板 / 模式切换 / 提示 / 即时通知                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 5. P0 / P1 / P2 规划

## 5.1 P0：必须先做

### P0-1 输入层重构

目标：把当前自写字符循环升级为真正稳定的 TUI 输入控制器。

当前问题：

1. 依赖自写 `msvcrt` 处理，复杂度越来越高
2. 中文输入法兼容性弱
3. 多行输入没有
4. 焦点管理没有
5. 通知插入和输入区重绘已经开始互相干扰

P0 输出：

1. 稳定的中文输入
2. 多行输入
3. 命令面板稳定焦点
4. `Shift+Tab` 稳定切换
5. 即时通知不打断输入

涉及模块：

1. `codelite/cli.py`
2. `codelite/tui/shell.py`

验收：

1. 中文输入法可连续输入 20 轮
2. `/todo`、`/cron ...`、普通任务均可稳定发出
3. 多轮通知插入后输入框不乱屏

### P0-2 Team / Subagent 看板

目标：把多代理执行从“文字时间线”升级成“可见团队面板”。

P0 输出：

1. 当前 team 概览卡片
2. 每个 subagent 单独卡片
3. 每张卡片展示：
当前状态 / prompt 摘要 / session_id / 最近结果 / 失败原因
4. 支持：
queued / running / done / failed 的直观状态

涉及模块：

1. `codelite/core/agent_team.py`
2. `codelite/cli.py`
3. `codelite/tui/shell.py`

验收：

1. “四人搜索 team”时能同时看到 4 张 subagent 卡片
2. 每个 subagent 是否真的用了 `web_search` 能在卡片中看见
3. 失败子代理能明显高亮

### P0-3 工具调用卡片化

目标：把工具调用从“摘要日志”升级成不同类型的结构化卡片。

P0 输出：

1. 文件卡片
适用：`read_file / list_files / write_file / edit_file`

2. Shell 卡片
适用：`bash`
显示：命令、平台、退出码、是否被策略拦截

3. 搜索卡片
适用：`web_search`
显示：query、answer、来源、结果数

4. Team 卡片
适用：`team_create / subagent_spawn / subagent_process`

5. Todo 卡片
适用：`todo_write`

涉及模块：

1. `codelite/core/tools.py`
2. `codelite/cli.py`
3. `codelite/tui/shell.py`

验收：

1. 不再出现巨长工具日志挤占屏幕
2. 用户能看懂本轮具体用了什么工具
3. `web_search` 来源以结构化方式展示

## 5.2 P1：交互化与实时化

### P1-1 任务白板可操作化

支持：

1. 查看任务详情
2. 手动领取/释放
3. 标记 blocked
4. 重试
5. 跳转到 worktree/session

### P1-2 队列与锁实时化

支持：

1. pending/failed 高亮
2. lease 过期倒计时
3. failed delivery 一键重放
4. stale lease 高亮预警

### P1-3 回合折叠/展开

支持：

1. 历史回合折叠
2. 当前回合展开
3. 错误回合自动置顶

## 5.3 P2：运维工作台

### P2-1 Watchdog 面板

展示：

1. 红色组件
2. 最近恢复动作
3. simulate 结果

### P2-2 Lanes / Delivery 面板

展示：

1. lane generation
2. job 排队
3. delivery 处理/失败/恢复

### P2-3 Model / Resilience / Critic 面板

展示：

1. 选用的 profile
2. fallback 发生了什么
3. critic 发现了什么规则

### P2-4 MCP / Background / Validate 面板

展示：

1. MCP server 列表与调用结果
2. 后台任务状态
3. build/lint/test/verify 统一看板

## 6. 推荐实现顺序

### 第一阶段

1. P0-1 输入层重构
2. P0-2 Team/Subagent 看板
3. P0-3 工具卡片化

### 第二阶段

1. P1-1 任务白板可操作化
2. P1-2 队列与锁实时化
3. P1-3 回合折叠

### 第三阶段

1. Watchdog
2. Lanes/Delivery
3. MCP/Background/Validate
4. Model/Resilience/Critic

## 7. 风险

### 7.1 输入层继续在现有自写循环上打补丁

风险最高。  
越做越难维护，尤其是中文输入、焦点切换、通知插入、多栏布局会持续互相冲突。

### 7.2 机制没统一映射

如果继续“每个机制都加一个小打印块”，最后会退回日志终端，而不是运行台。

### 7.3 屏幕空间管理

如果没有固定区域和折叠策略，功能越多越乱。

## 8. 最终判断

当前 TUI 已经不是空壳，但它还没有进入本项目真正应该有的阶段。  
真正的跃迁点不是“再多几块白板”，而是：

1. 把输入层重构成稳定控制器
2. 把 subagent 和工具调用做成结构化面板
3. 把任务/队列/锁从快照升级成可操作运行台

一句话总结：

**现在的 TUI 是“可工作的日志增强终端”；目标应该是“机制驱动的中文可视化 agent 工作台”。**
