# CodeLite: 轻量可控的命令行 Code Agent（对标 Claude Code）

## 0. 快速开始（v0.0 CLI）

先在仓库根目录安装本地命令行入口：

```powershell
python -m pip install -e .
```

### 0.1 配置模型与 API

`v0.0` 默认读取包内的 [`codelite/config/runtime.yaml`](codelite/config/runtime.yaml)，其中主模型已锁定为：

- `provider=openai`
- `model=gpt-5.4-mini`
- `base_url=https://code.rayinai.com/v1`

为避免把真实密钥写进仓库，运行时从环境变量读取：

```powershell
$env:CODELITE_LLM_API_KEY="your-llm-key"
$env:CODELITE_EMBEDDING_API_KEY="your-embedding-key"
$env:CODELITE_RERANK_API_KEY="your-rerank-key"
$env:TAVILY_API_KEY="your-tavily-key"
```

### 0.2 常用命令

```powershell
codelite
codelite version
codelite health --json
codelite run "读取 README 并总结当前 v0.0 能力"
codelite session replay --last 1
```

说明：

- 直接输入 `codelite` 会进入交互式命令行。
- 运行时会自动落盘到 `runtime/events.jsonl` 与 `runtime/sessions/*.jsonl`。
- `session replay` 可直接回放最近一次会话事件。

### 0.3 当前 v0.0 已落地的能力

- 主循环：最小 `plan -> act(tool) -> observe -> next`
- 工具：`bash`、`read_file`、`write_file`、`edit_file`
- 护栏：危险命令拦截、工作区路径越界拦截
- 持久化：全局事件流 + 单会话 JSONL
- 可观测：`health`、`session replay`

## 1. 项目定位

本项目目标是开发一个类似 Claude Code 的命令行 Code Agent，强调以下差异化能力：

- 轻量：依赖少、启动快、结构清晰、可快速二开
- 细节可视化：让 Agent 的每一步“可见、可追踪、可复盘”
- 可控：权限、预算、工具调用、执行节奏均可显式控制
- 个性化/定制化：可配置角色、风格、工作流模板、工具白名单

项目实现以现有基础项目为核心参考：

- `learn-claude-code`
- `claw0`

后续将按你提供的实际资料（ClaudeCode 核心工程亮点、项目规范）补充和对齐，不做无关扩展。

---

## 2. 核心设计原则（必须落实到 CLI 体验）

### 2.1 轻量
- 单进程优先，避免早期过度服务化
- 模块解耦但边界简单，先保证可跑可测
- 默认本地优先（日志、配置、缓存均本地化）

### 2.2 可视化
- 统一事件流（Event Stream）驱动 CLI 展示
- 每一步输出“意图 -> 动作 -> 结果 -> 耗时 -> 资源消耗”
- 支持任务时间线（Timeline）和会话复盘（Replay）

### 2.3 可控
- 执行模式：`auto` / `confirm-each-step` / `plan-only`
- 安全护栏：命令白名单、目录沙箱、敏感操作二次确认
- 成本与速度控制：最大步数、最大 token、超时、重试策略

### 2.4 个性化
- Persona 配置（语气、偏好、输出风格）
- 工作流模板（调试模式、重构模式、测试模式、评审模式）
- 可扩展工具注册（Tool Registry）

---

## 3. 架构路线对比（v0 选型）

### 方案 A：单体 CLI + 模块化内核（推荐）
- 特点：一个可执行入口，内部模块化（Agent、Tools、Renderer、Policy）
- 优点：实现快、调试快、适合实习项目展示端到端能力
- 风险：后期多会话并发和远程化扩展成本较高

### 方案 B：CLI + 本地守护进程（Daemon）
- 特点：CLI 负责交互，Daemon 负责任务执行和状态持久化
- 优点：并发能力更强，架构更“工程化”
- 风险：开发复杂度和维护成本明显上升

### 当前推荐
先做 **方案 A**，把“可跑、可演示、可复盘”做到极致；当功能稳定后，再演进为方案 B。

---

## 4. 推荐技术骨架（MVP）

```text
CLI Entrypoint
  -> Session Manager
  -> Agent Orchestrator
       -> Planner
       -> Tool Router
       -> Memory/Context Manager
       -> Policy Guard
  -> Event Bus
       -> TUI Renderer (Timeline / Panel / Diff / Cost)
       -> Logger (JSONL + Summary)
```

关键模块说明：

- `Agent Orchestrator`：控制“思考-行动-观察-调整”循环
- `Tool Router`：统一管理 shell、文件、搜索、代码编辑等工具调用
- `Policy Guard`：执行权限校验和风险拦截
- `Event Bus`：所有状态变化事件化，作为可视化和审计基础
- `TUI Renderer`：把底层事件转成可读、可追踪的命令行界面

---

## 5. MVP 功能边界（第一版只做最关键）

### 必做
- 基础 Agent 循环（plan -> act -> observe -> next）
- shell/file 两类核心工具
- 统一事件日志 + 会话回放
- 可控执行（步进确认、预算限制、超时重试）
- 基础 TUI（任务状态、步骤列表、差异摘要）

### 暂缓
- 多 Agent 协作
- 远程任务队列
- 复杂插件市场
- 云端控制台

---

## 6. 里程碑（适合写简历的交付节奏）

### Phase 0: 框架搭建（1 周）
- 初始化 CLI、配置系统、日志系统、最小可运行主循环

### Phase 1: 可用闭环（1-2 周）
- 打通工具调用链路（shell/file）
- 支持 plan/act/observe 基本循环

### Phase 2: 可视化与可控（1-2 周）
- 上线 Timeline、Step 面板、成本/耗时显示
- 加入权限/预算/确认机制

### Phase 3: 打磨与展示（1 周）
- 典型场景 Demo（修 bug、生成测试、重构小模块）
- 形成技术文档与项目亮点说明（用于简历和面试）

---

## 7. 简历可强调的工程亮点（预留）

- 设计并实现可观测的 Agent 执行引擎（事件驱动 + 会话回放）
- 构建安全可控的工具执行框架（权限护栏 + 风险确认）
- 通过模块化架构实现高可定制 CLI Agent（Persona + Workflow 模板）
- 在真实编码任务上完成端到端闭环（从任务理解到代码变更与验证）

---

## 8. 待你补充的资料（后续对齐）

- `learn-claude-code` 的核心机制清单（你希望直接复用的部分）
- `claw0` 的模块映射（哪些模块要保留、哪些重做）
- ClaudeCode 泄露源码中的“可借鉴工程亮点”
- 你的项目规范（目录规范、日志规范、测试规范、提交规范）

拿到以上资料后，将输出：

- 最终架构版 README（v1）
- 详细实施计划（按模块拆任务）
- 首批可直接编码的 issue 列表（含验收标准）
