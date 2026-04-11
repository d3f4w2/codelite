const knowledge = {};

knowledge.projectMeta = {
  name: "CodeLite",
  packageVersion: "0.2.1",
  capabilityTimeline: "v0.0 -> v0.2.2",
  tagline: "本地 Python CLI coding agent runtime，重点不是聊天壳，而是把 agent 的执行、安全、治理和验收做成可证明的工程系统。",
  elevatorPitch: "如果用一句话介绍，我会说：我实现了一个运行在本地工作区内的轻量级 coding agent CLI，把单轮对话、任务隔离、工具安全、运行时治理和统一验证串成了一个完整 runtime。",
  positioning: [
    "它是 agent runtime，不是单纯命令包装器。",
    "它强调 workspace 边界、安全护栏、状态落盘和验收闭环。",
    "它既能跑单轮 agent，也能绑定 task/worktree 做隔离执行。",
    "它保留了向 subagent、MCP、skills 扩展的接口。"
  ],
  stats: [
    { label: "Python 源文件", value: "52", note: "基于 codelite 包目录统计" },
    { label: "核心测试文件", value: "13", note: "tests/core" },
    { label: "验收包", value: "6", note: "docs/acceptance 下的阶段性 bundle" },
    { label: "验证流水线", value: "4", note: "build -> lint-arch -> test -> verify" }
  ],
  heroBullets: [
    "主入口是 codelite/cli.py，所有运行时能力由 build_runtime 集中装配。",
    "核心执行链是 AgentLoop + ToolRouter + SessionStore。",
    "核心安全链是 PolicyGate + HookRuntime + PermissionStore。",
    "核心治理链是 Todo/Context/Heart/Cron/Watchdog/Reconciler。",
    "核心扩展链是 Delivery/Lanes/AgentTeam/MCP/Skills。"
  ]
};

knowledge.highlights = [
  {
    title: "受控执行，不让 agent 乱跑",
    summary: "所有工具和 shell 调用都被限制在工作区内，shell 只接受 allowlist 头部命令，并禁止管道、重定向和高风险 git 动作。"
  },
  {
    title: "Task 与 worktree 绑定",
    summary: "TaskRunner 会先拿 lease，再准备 managed worktree，再把 ToolRouter 的根目录切到隔离工作树，避免多任务互相污染。"
  },
  {
    title: "不是只会答题，还是有 runtime 的 agent",
    summary: "Todo、Context、Heart、Cron、Watchdog、Reconciler、Delivery、Lanes 等模块让系统具备长期运行和自我治理能力。"
  },
  {
    title: "统一验证而不是口头完成",
    summary: "scripts/validate.py 会统一跑 compileall、架构检查、pytest 和 health 校验，失败时还会写 failure trace 并触发 on_validation_fail hook。"
  },
  {
    title: "agent 智能增强是分层做的",
    summary: "RetrievalRouter 决定查不查、查哪里；MemoryRuntime 组装长期上下文；ModelRouter 选模型档位；ResilienceRunner 负责重试和降级。"
  },
  {
    title: "扩展面可证明",
    summary: "v0.2.2 已经补了 Agent Team、Subagent、MCP 和外部 SKILL.md 兼容，不是只在设计稿里。"
  }
];

knowledge.timeline = [
  {
    stage: "v0.0",
    title: "CLI、基础工具、安全护栏、事件持久化",
    summary: "先把最小 coding agent 跑起来，提供 version、health、session replay、基础 file/shell 工具和 workspace 边界。",
    evidence: [{ label: "阶段验收包", path: "docs/acceptance/2026-04-07-phase-0-v00-and-task-lease/README.md" }]
  },
  {
    stage: "Phase 1",
    title: "任务状态机、租约模型、managed worktree、task-run binding",
    summary: "把一次普通 agent 执行升级为受控任务执行，解决并发任务、分支隔离和恢复问题。",
    evidence: [{ label: "当前完成态验收包", path: "docs/acceptance/2026-04-07-current-completed-state/README.md" }]
  },
  {
    stage: "v0.2",
    title: "runtime services",
    summary: "新增 todo、context compact、cron、heart、watchdog、reconcile，让系统开始像一个可治理运行时，而不是一次性脚本。",
    evidence: [{ label: "runtime services 验收包", path: "docs/acceptance/2026-04-08-phase-3-v02-runtime-services/README.md" }]
  },
  {
    stage: "v0.2.1",
    title: "九项机制补齐",
    summary: "Delivery、Lanes、Resilience、ValidatePipeline、AGENTS/Hooks、Retrieval、Memory、Skill/Plan Runtime、Model Router/Critic 全部落地。",
    evidence: [{ label: "九项机制验收包", path: "docs/acceptance/2026-04-08-v021-nine-mechanisms/README.md" }]
  },
  {
    stage: "v0.2.2",
    title: "Agent Team、Subagent、MCP、外部 Skills 兼容",
    summary: "把 runtime 的扩展面补出来，形成后续多 agent 与工具生态的承载层。",
    evidence: [{ label: "v0.2.2 验收包", path: "docs/acceptance/2026-04-08-v022-agent-team-mcp-skills/README.md" }]
  }
];

knowledge.mustKnow = [
  "一定先讲清项目定位：这是本地 coding agent runtime，不是普通 CLI 工具集合。",
  "一定强调 build_runtime 是组合根，说明系统不是散落脚本。",
  "一定解释 TaskRunner 为什么要绑定 lease 和 worktree，这是项目最有辨识度的机制之一。",
  "一定提 ValidatePipeline，因为这说明项目完成标准是统一验证而不是主观判断。",
  "一定把 PolicyGate + pre_tool_use hook 讲成双重护栏，而不是只说一句有安全考虑。",
  "一定承认 Retrieval、ModelRouter、Critic 是启发式实现，但它们已经形成闭环。"
];

knowledge.redFlags = [
  "不要把项目说成分布式调度平台，它仍然是本地 workspace 内的 runtime。",
  "不要把 Retrieval 说成大型语义 RAG 平台，当前实现仍以本地代码和文档命中为主。",
  "不要把 CriticRefiner 说成复杂 RL 系统，它是基于 failure log 的规则提炼器。",
  "不要把 MCP 描述成完整 marketplace，它目前是受控 registry + invocation entrypoint。",
  "不要把多 agent 说成 fully autonomous swarm，更准确的说法是 team/subagent runtime 与 delivery queue 集成。"
];

knowledge.evidenceBundles = [
  {
    title: "最终总验收",
    path: "docs/acceptance/2026-04-08-final-project-complete-state/README.md",
    why: "证明项目从 v0.0 到 v0.2.1 的核心能力是闭环可验收的。"
  },
  {
    title: "v0.2.2 扩展能力",
    path: "docs/acceptance/2026-04-08-v022-agent-team-mcp-skills/README.md",
    why: "证明 agent team、MCP 和外部 skills 不是口头设计。"
  },
  {
    title: "统一验证入口",
    path: "scripts/validate.py",
    why: "证明项目完成标准被固化为统一流水线。"
  }
];

knowledge.architecture = {
  layers: [
    {
      name: "入口层",
      headline: "CLI 和 shell 负责把用户意图转成受控运行时动作。",
      modules: ["codelite/cli.py", "codelite/tui/shell.py"],
      interviewLine: "我把所有命令入口收敛在 cli.py，既支持一次性 run，也支持进入交互 shell，而且 shell 本地命令和 runtime board 是统一编排的。"
    },
    {
      name: "组合根",
      headline: "build_runtime 是整个系统的 composition root。",
      modules: ["build_runtime in codelite/cli.py", "RuntimeServices dataclass"],
      interviewLine: "这一步把 EventStore、TaskStore、Todo、Context、Heart、Delivery、Memory、Retrieval、Model、Watchdog 等服务一次装配好，避免模块各自偷建依赖。"
    },
    {
      name: "执行核心",
      headline: "AgentLoop 控主循环，ToolRouter 控工具面。",
      modules: ["codelite/core/loop.py", "codelite/core/tools.py", "codelite/storage/sessions.py"],
      interviewLine: "run_turn 会做 todo seed、heartbeat、memory remember、retrieval 决策、model route、context compact、tool call 执行和 turn 级事件记录。"
    },
    {
      name: "安全边界",
      headline: "PolicyGate + HookRuntime + PermissionStore 负责执行约束。",
      modules: [
        "codelite/core/policy.py",
        "codelite/hooks/runtime.py",
        "codelite/hooks/pre_tool_use.py",
        "codelite/core/permissions.py"
      ],
      interviewLine: "工具调用先过策略门，再过 hook，必要时还要显式 approval，所以不是模型想调什么就调什么。"
    },
    {
      name: "任务隔离",
      headline: "TaskRunner 把 task、lease、worktree 和 agent 执行绑定起来。",
      modules: ["codelite/core/task_runner.py", "codelite/core/worktree.py", "codelite/storage/tasks.py"],
      interviewLine: "这里最关键的是 isolated ToolRouter 绑定到 worktree.path，真正把执行根切走。"
    },
    {
      name: "运行时治理",
      headline: "Todo、Context、Heart、Cron、Watchdog、Reconciler 负责长期运行的可观测和恢复。",
      modules: [
        "codelite/core/todo.py",
        "codelite/core/context.py",
        "codelite/core/heartbeat.py",
        "codelite/core/watchdog.py",
        "codelite/core/reconcile.py",
        "codelite/core/scheduler.py"
      ],
      interviewLine: "这部分是我把项目从 demo 拉到 runtime 的关键，能看、能管、能恢复。"
    },
    {
      name: "智能增强",
      headline: "Retrieval、Memory、ModelRouter、Resilience、Critic 让 agent 更稳。",
      modules: [
        "codelite/core/retrieval.py",
        "codelite/core/memory_runtime.py",
        "codelite/core/model_router.py",
        "codelite/core/resilience.py"
      ],
      interviewLine: "我没有把智能全压给一个大模型，而是拆成查不查、带什么记忆、走哪个 profile、失败后怎么补救。"
    },
    {
      name: "扩展与编排",
      headline: "Delivery/Lanes/AgentTeam/MCP/Skills 提供后续扩展面。",
      modules: [
        "codelite/core/delivery.py",
        "codelite/core/lanes.py",
        "codelite/core/agent_team.py",
        "codelite/core/mcp_runtime.py",
        "codelite/core/skills_runtime.py"
      ],
      interviewLine: "这套设计让项目不止能跑一个 agent，还能继续承载 subagent、background task 和外部工具生态。"
    }
  ],
  flows: [
    {
      title: "启动与运行时装配",
      steps: [
        "用户执行 codelite、codelite shell 或裸 prompt。",
        "cli.py 解析命令并决定进入 run、shell 或子命令分支。",
        "build_runtime 创建 RuntimeLayout、各类 Store、Runtime Service 和 AgentLoop。",
        "shell 模式下，用户继续通过 slash 命令与 runtime board 交互。"
      ],
      whyItMatters: "面试时要体现系统是组合型架构，而不是 if/else 堆出来的脚本。",
      evidence: [{ label: "入口实现", path: "codelite/cli.py" }]
    },
    {
      title: "单轮 agent 执行链",
      steps: [
        "AgentLoop.run_turn 先 ensure session，再做 todo seed、heartbeat 和记忆写入。",
        "如果启用 RetrievalRouter，则先做 route/decision 并把结果写入 session 事件。",
        "ModelRouter 选 fast、deep 或 review profile。",
        "MemoryRuntime 组装长期上下文，ContextCompact 决定是否压缩历史消息。",
        "模型返回 tool calls 时，ToolRouter 执行并再次写回 session；无 tool call 时返回最终答案。"
      ],
      whyItMatters: "这是项目最核心的 runtime 主链，几乎所有深挖都能从这里展开。",
      evidence: [
        { label: "主循环", path: "codelite/core/loop.py" },
        { label: "模型与工具协同", path: "codelite/core/tools.py" }
      ]
    },
    {
      title: "task -> worktree 隔离执行",
      steps: [
        "TaskRunner 先对 task 获取 lease，避免同一任务被抢占执行。",
        "WorktreeManager.prepare 为 task 创建或恢复 managed worktree。",
        "TaskStore 记录 session、prompt、worktree 元数据。",
        "TaskRunner 为 worktree.path 创建隔离 ToolRouter，再把 AgentLoop 跑在隔离目录中。",
        "成功则 complete task，失败则 block task 并记录错误。"
      ],
      whyItMatters: "这是最值得在面试里强调的工程细节，因为它体现了 agent 执行隔离能力。",
      evidence: [
        { label: "TaskRunner", path: "codelite/core/task_runner.py" },
        { label: "WorktreeManager", path: "codelite/core/worktree.py" },
        { label: "回归测试", path: "tests/core/test_task_run_worktree_binding.py" }
      ]
    },
    {
      title: "统一验证流水线",
      steps: [
        "scripts/validate.py 创建 ValidatePipeline。",
        "ValidatePipeline 依次执行 compileall、lint_arch、pytest、health。",
        "任一阶段失败就写入 failure trace，并触发 on_validation_fail hook。",
        "只有全部成功才算真正完成。"
      ],
      whyItMatters: "这说明项目有统一完成标准，不是开发者主观认为好了就结束。",
      evidence: [
        { label: "验证入口", path: "scripts/validate.py" },
        { label: "流水线实现", path: "codelite/core/validate_pipeline.py" },
        { label: "失败轨迹测试", path: "tests/core/test_validate_pipeline_failure_trace.py" }
      ]
    },
    {
      title: "subagent 与 MCP 扩展链",
      steps: [
        "AgentTeamRuntime 为 team 生成 subagent 记录，并把 subagent_task 投递到 DeliveryQueue。",
        "ParallelDispatcher 按保留 worker 数和 team limit 处理 subagent 任务。",
        "subagent executor 复用 AgentLoop，但能按 profile 限制工具面。",
        "McpRuntime 维护 registry，调用时记录 invocation 并写入记忆。"
      ],
      whyItMatters: "面试时可以说明系统留有明确扩展面，而不是单体写死。",
      evidence: [
        { label: "AgentTeamRuntime", path: "codelite/core/agent_team.py" },
        { label: "McpRuntime", path: "codelite/core/mcp_runtime.py" },
        { label: "v0.2.2 测试", path: "tests/core/test_v022_agent_team_mcp_skills.py" }
      ]
    }
  ],
  designChoices: [
    {
      choice: "集中装配 RuntimeServices",
      why: "避免每个模块自己初始化依赖，降低耦合和隐藏状态。",
      tradeoff: "cli.py 会比较长，但组合关系更可见。"
    },
    {
      choice: "workspace-first 安全边界",
      why: "本地 coding agent 最大风险是越界写文件和乱跑命令，所以先保边界，再谈能力。",
      tradeoff: "shell 能力被故意收窄，灵活性不如完全开放。"
    },
    {
      choice: "task 绑定 worktree",
      why: "同仓多任务是 coding agent 的典型痛点，worktree 是最实际的本地隔离方案。",
      tradeoff: "依赖 git 仓库，非 git 工作区会回退。"
    },
    {
      choice: "启发式 router 而不是复杂调度器",
      why: "当前阶段先把 route 与 evidence 链打通，保证可解释性。",
      tradeoff: "智能度有限，但可维护、可辩护。"
    },
    {
      choice: "统一 validate pipeline",
      why: "逼迫项目把 build、架构、测试和 health 合在一个完成标准下。",
      tradeoff: "验证时间会更长，但交付更可靠。"
    }
  ]
};

knowledge.mechanisms = [
  {
    id: "loop-toolchain",
    group: "Agent 主链",
    title: "AgentLoop + ToolRouter 主循环",
    problem: "如果只有裸模型调用，很难形成稳定、可追踪的 coding agent。",
    design: "AgentLoop 负责 turn 级状态推进，ToolRouter 负责工具暴露、权限、hook、并行执行和错误归一化。",
    implementation: [
      "run_turn 会追加 session 事件，确保每一步都可回放。",
      "模型返回 tool calls 时，会先把 assistant/tool 消息写回 session，再执行工具。",
      "ToolRouter 会区分只读、破坏性、并行安全等元信息。"
    ],
    interviewAngles: [
      "为什么把主循环和工具路由拆开：职责边界更清楚，测试也更独立。",
      "为什么要记录 turn_started、tool_started、tool_finished：为了调试、追责和复盘。"
    ],
    evidence: [
      { label: "执行主链", path: "codelite/core/loop.py" },
      { label: "工具路由", path: "codelite/core/tools.py" }
    ],
    tests: ["tests/core/test_v00_smoke.py", "tests/core/test_v021_mechanisms.py"]
  },
  {
    id: "task-worktree",
    group: "隔离执行",
    title: "Lease + TaskRunner + Managed Worktree",
    problem: "多个任务同时改同一个仓库时，会产生文件互踩和上下文污染。",
    design: "TaskStore 先发 lease，WorktreeManager 为 task 创建独立 worktree，TaskRunner 再把 ToolRouter 根目录切到 worktree.path。",
    implementation: [
      "WorktreeManager 用 task_id 生成稳定 branch/path/key，并维护 worktree index。",
      "TaskRunner 在成功时 complete task，失败时 block task 并记录 last_error。",
      "Task metadata 中会持久化 session_id、prompt、worktree 信息。"
    ],
    interviewAngles: [
      "这是项目最有辨识度的工程设计，能直接体现你对 agent 执行隔离的理解。",
      "重点别只说创建 worktree，要说 ToolRouter 的 workspace_root 也被切过去了。"
    ],
    evidence: [
      { label: "task runner", path: "codelite/core/task_runner.py" },
      { label: "worktree manager", path: "codelite/core/worktree.py" },
      { label: "绑定测试", path: "tests/core/test_task_run_worktree_binding.py" }
    ],
    tests: [
      "tests/core/test_worktree_isolation.py",
      "tests/core/test_tasks_leases.py",
      "tests/core/test_task_run_worktree_binding.py"
    ]
  },
  {
    id: "policy-hooks",
    group: "安全护栏",
    title: "PolicyGate + pre_tool_use 双重防护",
    problem: "本地 agent 最大风险不是答错，而是执行越界和破坏性命令。",
    design: "先由 PolicyGate 做路径解析和 shell allowlist，再由 pre_tool_use hook 补充高风险 git 和受保护 runtime 目录写入拦截。",
    implementation: [
      "shell 禁止管道、重定向和危险 token。",
      "git 只允许 branch、diff、log、show、status 等安全子命令。",
      "hook 会禁止写 runtime/leases、delivery queue WAL、runtime/hooks 等路径。"
    ],
    interviewAngles: [
      "为什么不是只靠 prompt 约束：因为 prompt 不能替代真正的执行门禁。",
      "为什么要双层：PolicyGate 更像通用策略，hook 更像工作区本地规约。"
    ],
    evidence: [
      { label: "policy", path: "codelite/core/policy.py" },
      { label: "pre hook", path: "codelite/hooks/pre_tool_use.py" },
      { label: "doctor 命令", path: "docs/acceptance/2026-04-08-final-project-complete-state/artifacts/command-output/hooks-doctor.json" }
    ],
    tests: ["tests/core/test_action_verify.py", "tests/core/test_v021_mechanisms.py"]
  },
  {
    id: "validate-pipeline",
    group: "工程质量",
    title: "ValidatePipeline 统一验收",
    problem: "coding agent 项目很容易陷入只跑一个测试就宣称完成。",
    design: "把 compileall、架构检查、pytest、health 合成一个标准 pipeline，失败写 trace 并触发 hook。",
    implementation: [
      "scripts/validate.py 是统一入口。",
      "ValidatePipeline 内部封装阶段结果，失败时会 append_validation_failure。",
      "on_validation_fail hook 会把失败信息追加到 hook_failures_path。"
    ],
    interviewAngles: [
      "这说明项目不仅有 feature，还有交付标准。",
      "如果被问为什么要 health 校验：因为仅通过测试不代表 runtime 入口可正常启动。"
    ],
    evidence: [
      { label: "validate entry", path: "scripts/validate.py" },
      { label: "validate pipeline", path: "codelite/core/validate_pipeline.py" },
      { label: "failure trace test", path: "tests/core/test_validate_pipeline_failure_trace.py" }
    ],
    tests: ["tests/core/test_validate_pipeline_failure_trace.py"]
  },
  {
    id: "todo-context-governance",
    group: "运行时治理",
    title: "TodoManager + ContextCompact",
    problem: "agent 任务容易漂移，长对话容易失控和占满上下文。",
    design: "TodoManager 把计划快照落盘，ContextCompact 在超过阈值时自动压缩历史、折叠系统消息并清理旧 tool result。",
    implementation: [
      "TodoManager 会自动 seed 一个初始 task，也能被 todo_write 替换。",
      "ContextCompact 支持 snip、auto_compact、context_collapse、function_result_clearing。",
      "todo_nag_after_steps 会在 agent 长时间不更新 todo 时注入提醒。"
    ],
    interviewAngles: [
      "这部分体现我不仅关心能不能跑，还关心 agent 是否可控。",
      "如果被问为什么不用单纯 summary：因为 tool result clearing 和 dynamic note collapse 解决的是不同问题。"
    ],
    evidence: [
      { label: "todo manager", path: "codelite/core/todo.py" },
      { label: "context compact", path: "codelite/core/context.py" },
      { label: "todo nag test", path: "tests/core/test_v021_mechanisms.py" }
    ],
    tests: ["tests/core/test_loop_memory_context.py", "tests/core/test_v021_mechanisms.py"]
  },
  {
    id: "delivery-lanes",
    group: "调度与投递",
    title: "DeliveryQueue + LaneScheduler",
    problem: "subagent、background task、cron 这类工作项需要可靠投递、恢复和并发治理。",
    design: "DeliveryQueue 负责 WAL、pending/running/done/failed、claim TTL、退避重试；LaneScheduler 用 generation token 和 queue 状态治理串行 lane。",
    implementation: [
      "delivery item 会写 WAL 和 pending 文件，丢失 pending 也能 recover。",
      "claim 过期后会自动 requeue，避免 worker 崩溃导致任务悬挂。",
      "lane generation bump 后，旧 generation 的任务会被拒绝。"
    ],
    interviewAngles: [
      "可以把它讲成轻量本地 dispatcher，而不是消息中间件。",
      "重点强调持久化和恢复，不要只说队列。"
    ],
    evidence: [
      { label: "delivery queue", path: "codelite/core/delivery.py" },
      { label: "lane scheduler", path: "codelite/core/lanes.py" },
      { label: "状态样例", path: "docs/acceptance/2026-04-08-final-project-complete-state/artifacts/command-output/delivery-status.json" }
    ],
    tests: ["tests/core/test_v021_mechanisms.py"]
  },
  {
    id: "retrieval-memory",
    group: "上下文增强",
    title: "RetrievalRouter + MemoryRuntime",
    problem: "agent 既需要外部/本地资料，也需要长期偏好与经验上下文，但两者不应该混成一团。",
    design: "RetrievalRouter 先决定 route 和 enoughness；MemoryRuntime 负责 ledger、file-based memory、candidate/decision 和 context assembly。",
    implementation: [
      "Retrieval 支持 none、local_docs、local_code、web 四种 route。",
      "Memory 明确把文件型 memory 当 source of truth，ledger 只是 audit hint。",
      "remember_preference 和 forget_preference 会更新 managed preference block。"
    ],
    interviewAngles: [
      "这部分要讲成双层增强：一个查资料，一个带长期记忆。",
      "如果被问是不是复杂 RAG，要诚实回答当前更多是启发式检索和文件记忆。"
    ],
    evidence: [
      { label: "retrieval router", path: "codelite/core/retrieval.py" },
      { label: "memory runtime", path: "codelite/core/memory_runtime.py" },
      { label: "memory timeline artifact", path: "docs/acceptance/2026-04-08-final-project-complete-state/artifacts/command-output/memory-timeline.json" }
    ],
    tests: ["tests/core/test_memory_runtime.py", "tests/core/test_v021_mechanisms.py"]
  },
  {
    id: "model-resilience-critic",
    group: "模型策略",
    title: "ModelRouter + ResilienceRunner + CriticRefiner",
    problem: "同一个模型档位无法同时覆盖快答、深推理和 review，而且真实调用还会遇到超时、上下文溢出、鉴权问题。",
    design: "ModelRouter 先选 fast、deep、review；ResilienceRunner 负责 auth retry、overflow compaction、generic retry 和 fallback；CriticRefiner 负责审查答案和沉淀失败规则。",
    implementation: [
      "review prompt 会走 review profile，复杂设计题更可能走 deep。",
      "ResilienceRunner 会复用 ContextCompact 进行 overflow 补救。",
      "CriticRefiner 可 review answer，也能 log_failure 后 refine_rules。"
    ],
    interviewAngles: [
      "要强调这是 runtime policy，不是纯模型能力比较。",
      "如果被问 critic 是否自动修改答案，当前不是，它更像 review 和规则沉淀器。"
    ],
    evidence: [
      { label: "model router", path: "codelite/core/model_router.py" },
      { label: "resilience runner", path: "codelite/core/resilience.py" },
      { label: "critic outputs", path: "docs/acceptance/2026-04-08-final-project-complete-state/artifacts/command-output/critic-review.json" }
    ],
    tests: ["tests/core/test_v021_mechanisms.py"]
  },
  {
    id: "watchdog-reconcile",
    group: "可靠性",
    title: "HeartService + Watchdog + Reconciler",
    problem: "runtime 只要持续运行，就必须面对心跳过期、租约过期和残留状态恢复。",
    design: "HeartService 记录组件状态，Watchdog 针对 red component 做诊断与安全恢复，Reconciler 回收过期 lease、清理 orphan worktree、做 compact maintenance。",
    implementation: [
      "watchdog 会先写 snapshot，再打 yellow 状态，而不是直接假装恢复完成。",
      "某些组件出问题时会触发 reconcile_expired_leases。",
      "cron 内置 heartbeat_scan、task_reconcile、worktree_gc、compact_maintenance 等 job。"
    ],
    interviewAngles: [
      "这里要体现你理解长期运行系统的恢复语义，而不是只会 happy path。",
      "重点说 safe pause、snapshot、reconcile 这些保守动作。"
    ],
    evidence: [
      { label: "watchdog", path: "codelite/core/watchdog.py" },
      { label: "phase 3 验收包", path: "docs/acceptance/2026-04-08-phase-3-v02-runtime-services/README.md" }
    ],
    tests: ["tests/core/test_v02_runtime_services.py"]
  },
  {
    id: "agent-team-mcp-skills",
    group: "扩展能力",
    title: "AgentTeam + Subagent + MCP + Skills",
    problem: "一个 runtime 如果只能跑单 agent，后续很难扩成更复杂的工具生态。",
    design: "AgentTeamRuntime 管 team/subagent 生命周期，subagent 任务走 delivery queue，McpRuntime 管 registry 与 invocation，SkillRuntime 兼容外部 SKILL.md。",
    implementation: [
      "subagent 可以 queue 或 sync 模式运行。",
      "explore profile 会限制 write_file 这类变更工具。",
      "MCP server 的 command、cwd、env 都经过规范化和危险命令拦截。"
    ],
    interviewAngles: [
      "这部分最好讲成可扩展接口层，而不是已经做成完整生态平台。",
      "可以强调 drop-in external skill compatibility，这点很适合面试加分。"
    ],
    evidence: [
      { label: "agent team runtime", path: "codelite/core/agent_team.py" },
      { label: "mcp runtime", path: "codelite/core/mcp_runtime.py" },
      { label: "v0.2.2 tests", path: "tests/core/test_v022_agent_team_mcp_skills.py" }
    ],
    tests: ["tests/core/test_v022_agent_team_mcp_skills.py"]
  }
];

knowledge.interview = {
  strategy: [
    "先讲项目定位，再讲组合根，再讲主执行链，最后挑 2 到 3 个最硬的机制深挖。",
    "如果面试官偏后端，就多讲 worktree、validate pipeline、delivery queue、安全护栏。",
    "如果面试官偏 agent，就多讲 retrieval、memory、model routing、subagent profile 限权。",
    "遇到没有完全做深的能力，承认边界，但立刻补上为什么当前实现已经足够支撑下一阶段。"
  ],
  questions: [
    {
      category: "项目概述",
      question: "这个项目一句话怎么介绍？",
      answer30: "这是一个本地 Python CLI coding agent runtime，我重点把 agent 的执行、安全、治理和统一验证做成了可落地系统，而不是只做了一个聊天壳。",
      answer120: "项目主入口在 cli.py，运行时通过 build_runtime 一次装配，核心链路是 AgentLoop + ToolRouter。往外有 task/worktree 隔离执行，往内有 retrieval、memory、model routing、resilience 做智能增强，周围还有 todo、context、watchdog、validate pipeline 这类治理机制。所以它更像一个本地 agent runtime，而不是几个脚本拼接出来的命令行工具。",
      followups: ["为什么要叫 runtime 而不是 CLI 工具？", "它和 Claude Code 这类产品最大的差别是什么？"],
      related: ["loop-toolchain", "task-worktree", "validate-pipeline"]
    },
    {
      category: "项目概述",
      question: "你在这个项目里最核心的工程贡献是什么？",
      answer30: "我最核心的贡献不是单点功能，而是把 agent 从一次性调用拉成了有边界、有状态、有验证标准的 runtime。",
      answer120: "如果只看 feature，会觉得是 CLI、tools、memory、retrieval 这些模块；但真正的价值在于组合方式。我把 build_runtime 做成组合根，把 task/worktree 做成隔离执行，把 PolicyGate + hooks 做成双重护栏，把 ValidatePipeline 固化为完成标准，再用 delivery、watchdog、reconcile 等机制把长期运行问题补上。这样项目在面试里能讲清楚系统性，而不是只讲一堆点状功能。",
      followups: ["如果只能挑一个最难点，你会选哪个？", "为什么不是 model 集成最难？"],
      related: ["task-worktree", "policy-hooks", "validate-pipeline"]
    },
    {
      category: "架构设计",
      question: "为什么要搞一个 build_runtime，而不是命令里临时 new 对象？",
      answer30: "因为系统服务多而且彼此有关联，集中装配可以把依赖关系显式化，避免隐藏状态和重复初始化。",
      answer120: "这个项目有 EventStore、TaskStore、TodoManager、ContextCompact、HeartService、DeliveryQueue、MemoryRuntime、RetrievalRouter、ModelRouter、Watchdog 等服务。如果每个命令各自初始化，很快就会出现依赖顺序混乱、状态不一致、测试困难的问题。build_runtime 用 RuntimeServices dataclass 把这些对象集中起来，一方面让 cli.py 成为组合根，另一方面也方便 shell、task runner、validate pipeline 复用同一套服务。",
      followups: ["这样会不会让 cli.py 太胖？", "后续如果要拆 IoC 容器会怎么做？"],
      related: ["loop-toolchain"]
    },
    {
      category: "架构设计",
      question: "AgentLoop 里最关键的几个阶段是什么？",
      answer30: "session 建立、todo/heartbeat/memory 预处理、retrieval 和 model route、context compact、tool call 执行、最终答案落盘。",
      answer120: "run_turn 不是简单调用模型。它先 ensure session，再做 todo seed、heartbeat 和 memory remember。之后如果启用 RetrievalRouter，就先做 route 决策并把结果追加到系统消息。再由 ModelRouter 选 fast、deep、review profile，MemoryRuntime 组长期上下文，ContextCompact 处理长对话。模型返回 tool calls 时，ToolRouter 执行并把每次工具结果都回写到 session。只有没有 tool call 时，这一轮才真正结束。",
      followups: ["为什么不先压缩上下文再做 retrieval？", "tool 调用为什么也要写回 session？"],
      related: ["loop-toolchain", "retrieval-memory", "model-resilience-critic"]
    },
    {
      category: "架构设计",
      question: "为什么 task 执行一定要绑 worktree？",
      answer30: "因为多任务改同一仓库时，最大的工程风险就是互相污染，worktree 是本地最实用的隔离方案。",
      answer120: "如果只是记录一个 task_id 而不切运行根，agent 还是会在同一份工作区改文件，根本谈不上隔离。我的做法是先给 task 发 lease，再由 WorktreeManager.prepare 创建或恢复独立工作树，最后在 TaskRunner 里新建一个以 worktree.path 为 workspace_root 的 ToolRouter。这样 shell、read_file、write_file 全都天然落在隔离目录中，才是真隔离。",
      followups: ["为什么不用直接 copy 仓库？", "如果 worktree 丢了怎么恢复？"],
      related: ["task-worktree"]
    },
    {
      category: "安全",
      question: "你这个 agent 怎么防止危险命令？",
      answer30: "靠执行层门禁，不靠 prompt 幻觉。PolicyGate 先拦 shell 和路径，pre_tool_use hook 再补高风险 git 和受保护 runtime 目录拦截。",
      answer120: "首先 PolicyGate 会对路径做 workspace 内解析，对 shell 做 allowlist 校验，禁止管道、重定向、危险 token 和危险 git 子命令。其次 HookRuntime 的 pre_tool_use 还会拦写 runtime/leases、delivery WAL、runtime/hooks 等受保护目录，并阻断 git commit、push、reset 这类高风险动作。对于像 mcp_call 这样的动作，还能再叠加 PermissionStore 的显式审批。",
      followups: ["为什么还要 hook，PolicyGate 不够吗？", "如果模型构造出奇怪路径怎么办？"],
      related: ["policy-hooks"]
    },
    {
      category: "工程化",
      question: "为什么要统一 validate pipeline？",
      answer30: "因为 coding agent 项目最怕主观完成，我希望 build、架构、测试和 health 都纳入同一个验收标准。",
      answer120: "如果只跑单个 pytest，很可能代码能过测试但 runtime 入口已经坏了，或者架构约束被打破了。ValidatePipeline 的四个阶段是 compileall、lint_arch、pytest 和 health verify，任何一步失败都会生成 failure trace 并触发 on_validation_fail hook。这样我可以把完成标准外显化，既利于自测，也利于后续 agent 自动执行时做 closing gate。",
      followups: ["为什么要 compileall？", "health 和 test 会不会重复？"],
      related: ["validate-pipeline"]
    },
    {
      category: "运行时治理",
      question: "TodoManager 和 ContextCompact 在 agent 项目里价值是什么？",
      answer30: "前者解决任务漂移，后者解决长对话失控，它们都是为了让 agent 更可控。",
      answer120: "TodoManager 会把 session 的计划快照落盘，甚至在没有显式计划时也能 seed 一个初始 task。这样任务执行就有可追踪的计划面。ContextCompact 则在消息数或字符数超阈值时自动做 snip、summary、system note collapse 和旧 tool result 清理，避免上下文越滚越大。两者一个控制目标，一个控制成本，都是 runtime 级能力。",
      followups: ["为什么不用让模型自己记住计划？", "tool result clearing 会不会丢信息？"],
      related: ["todo-context-governance"]
    },
    {
      category: "运行时治理",
      question: "DeliveryQueue 和普通 list 队列有什么区别？",
      answer30: "它不是内存 list，而是带 WAL、pending/running/done/failed、claim TTL 和退避重试的本地持久化队列。",
      answer120: "普通 list 只能表示顺序，没法表达崩溃恢复。DeliveryQueue 每个 item 都会写 WAL 和 pending 文件，处理中会转成 running，完成后落到 done，失败后可能回 pending 或进 failed。claim 有 TTL，worker 崩掉也能 requeue；backoff 带抖动，避免失败任务疯狂重试。这样它才足以承载 subagent、background task 这类真实工作项。",
      followups: ["为什么不用 Redis 或消息队列？", "WAL 和 pending 的职责怎么区分？"],
      related: ["delivery-lanes"]
    },
    {
      category: "运行时治理",
      question: "LaneScheduler 的 generation token 解决什么问题？",
      answer30: "解决旧任务或旧视图提交的 stale job 被误执行的问题。",
      answer120: "某些 lane 是串行语义，比如 main、cron、heartbeat。如果用户刷新了 lane 视图、或者上一轮状态已经被 bump generation 了，再提交旧 generation 的 job 就不应该进入当前队列。LaneScheduler 在 enqueue 时会比对 generation，不匹配就直接拒绝。这个设计很轻量，但能明确处理 stale command 问题。",
      followups: ["为什么不直接靠 queue 清空？", "这种设计对并发有什么帮助？"],
      related: ["delivery-lanes"]
    },
    {
      category: "Agent 机制",
      question: "RetrievalRouter 怎么判断查哪里？",
      answer30: "先看 prompt 是否要最新信息、代码符号或文档，再在 web、local_code、local_docs、none 之间选 route。",
      answer120: "当前 RetrievalRouter 是启发式路由，不是复杂的 embedding 检索编排。它会先抽 query terms，再看 prompt 是否明显要求 latest/internet、是否提到 class/function/module 或 readme/docs。满足 web 条件且配置了 Tavily 时就走 web，否则退到 local_docs；涉及源码符号时走 local_code。run 之后还会评估 enoughness，把结果写进 session 事件和 memory。",
      followups: ["为什么还要 enough 字段？", "为什么不是一开始就做向量检索？"],
      related: ["retrieval-memory"]
    },
    {
      category: "Agent 机制",
      question: "MemoryRuntime 里你最想强调哪点？",
      answer30: "文件型 memory 是 source of truth，ledger 只是审计线索，这个边界很重要。",
      answer120: "很多 agent memory 容易混成一团。我这里明确把 agent.md、user.md、soul.md、tool.md、Memory.md 这类文件记忆作为稳定偏好和长期事实来源；ledger 则记录 retrieval、prompt、answer、review、failure 等轨迹，更多用于 audit 和 context assembly。这样一来，长期偏好可编辑、可追踪，也更适合面试时解释数据边界。",
      followups: ["为什么不直接全存数据库？", "candidate/decision 机制解决什么问题？"],
      related: ["retrieval-memory"]
    },
    {
      category: "Agent 机制",
      question: "ModelRouter 为什么只分 fast、deep、review 三档？",
      answer30: "因为当前项目先解决可解释策略，而不是追求复杂模型编排。",
      answer120: "ModelRouter 现在是很轻量的 profile selector。review 或 critique 类 prompt 走 review，长文本、design、architecture、refactor 类问题更偏 deep，其余走 fast。这样做的价值不是模型多，而是把 runtime 的策略显式化。后面要接更多模型，只需要扩 profile 和 fallback policy，不需要重写主循环。",
      followups: ["这样会不会太粗糙？", "profile 和真实 model 是怎么映射的？"],
      related: ["model-resilience-critic"]
    },
    {
      category: "可靠性",
      question: "ResilienceRunner 实际上处理哪些失败？",
      answer30: "鉴权错误、上下文溢出、通用重试和 profile fallback。",
      answer120: "ResilienceRunner 不只是 try/except。它会先尝试 preferred profile，遇到 auth_error 会先做 auth_rotation retry，遇到 context_overflow 会调用 ContextCompact 再试一次，然后还保留 generic retry。如果这一档还是不行，再切 fallback profile。这个机制的意义是把失败语义分层，而不是所有失败都一把梭重试。",
      followups: ["为什么上下文溢出要和普通错误分开？", "fallback 会不会导致行为不一致？"],
      related: ["model-resilience-critic", "todo-context-governance"]
    },
    {
      category: "可靠性",
      question: "Watchdog 恢复时为什么先打 yellow 而不是 green？",
      answer30: "因为它做的是诊断和恢复计划，不是假装问题已经完全解决。",
      answer120: "Watchdog 的 recover 逻辑很保守。它会先写 diagnostic snapshot，再做 safe pause marker，如果组件属于 agent_loop、tool_router、cron_scheduler、worktree_manager 等，还会触发 reconcile_expired_leases。之后它给 watchdog 和目标组件都打 yellow，表示已经进入恢复流程，但不能宣称恢复成功。这比直接改回 green 更真实，也更适合长期运行系统。",
      followups: ["这会不会让状态机更复杂？", "如果恢复失败怎么办？"],
      related: ["watchdog-reconcile"]
    },
    {
      category: "扩展性",
      question: "Subagent 是怎么和主 runtime 接上的？",
      answer30: "AgentTeamRuntime 负责 team 和 subagent 记录，subagent_task 通过 DeliveryQueue 投递，执行时复用 AgentLoop。",
      answer120: "subagent 不是另起炉灶。AgentTeamRuntime 会先确保 team 存在，再生成 subagent 记录，然后把 subagent_task 投进 DeliveryQueue。处理队列时由 ParallelDispatcher 根据 team limit 和保留 worker 数去跑。真正执行时还是走 AgentLoop，只不过可以按 profile 限制工具集合，比如 explore agent 不允许 write_file。这样主 runtime 和 subagent runtime 共享一套核心执行内核。",
      followups: ["为什么不用直接线程池跑 subagent？", "profile 限权比 prompt 限权强在哪？"],
      related: ["agent-team-mcp-skills", "delivery-lanes"]
    },
    {
      category: "扩展性",
      question: "MCP Runtime 你会怎么描述，才不显得夸大？",
      answer30: "我会说它是受控的本地 MCP registry 和 invocation entrypoint，而不是成熟的插件市场。",
      answer120: "McpRuntime 目前做的事情很明确：维护 server registry、校验名字、限制 cwd 必须在 workspace 内、拦截危险 command head、调用时写 invocation 记录。它已经足以把外部工具接进 runtime，并且保留审计证据。但我不会把它说成完整的生态平台，因为现在还没有完整的远程分发、安装和权限体系。",
      followups: ["那为什么仍然值得讲？", "如果要往 marketplace 方向演进，下一步是什么？"],
      related: ["agent-team-mcp-skills", "policy-hooks"]
    },
    {
      category: "测试与验收",
      question: "这个项目怎么证明不是 PPT 工程？",
      answer30: "我有三层证据：代码、自动化测试、acceptance bundle 和真实命令输出。",
      answer120: "第一层是核心模块代码，比如 loop、worktree、validate pipeline、delivery queue。第二层是 tests/core 下的回归测试，覆盖 v0.0、v0.2、v0.2.1、v0.2.2 关键机制。第三层是 docs/acceptance 里的阶段验收包，它不仅写了怎么手测，还保存了真实 command output 和 runtime snapshot。所以我在面试里不是讲概念，而是能指出哪份证据证明哪项能力已经落地。",
      followups: ["acceptance bundle 和 automated test 分别解决什么问题？", "为什么要保存命令输出样例？"],
      related: ["validate-pipeline"]
    },
    {
      category: "测试与验收",
      question: "如果让你继续做下一阶段，你会优先补什么？",
      answer30: "我会优先补更真实的模型路由和 retrieval provider，再把 extension surface 做成更稳的生态层。",
      answer120: "当前最值得演进的有三块。第一，RetrievalRouter 仍偏启发式，可以引入更稳的 provider 接入和 enoughness 评估。第二，ModelRouter 可以从规则路由进化到更细粒度的 profile policy。第三，MCP 和 external skills 可以继续做权限、安装、发现和更细的审计。如果继续往 agent platform 方向走，这三块会最直接提高实战价值。",
      followups: ["为什么不是先做 UI？", "你会先补能力还是补可靠性？"],
      related: ["retrieval-memory", "model-resilience-critic", "agent-team-mcp-skills"]
    },
    {
      category: "简历追问",
      question: "简历上如果写了统一验证和安全护栏，面试官追问时最少要答到什么深度？",
      answer30: "至少要答出 validate 的四个阶段，以及 PolicyGate 和 hook 分别拦什么。",
      answer120: "统一验证至少要说出 scripts/validate.py 会调用 ValidatePipeline，内部顺序是 compileall、lint_arch、pytest、health，失败会写 failure trace 并触发 hook。安全护栏至少要说出 PolicyGate 会限制路径和 shell allowlist，pre_tool_use 会额外拦危险 git 和受保护 runtime 目录写入。只要你能答到这一层，简历表述就站得住。",
      followups: ["如果继续深挖，还能讲什么？", "有没有对应测试能证明？"],
      related: ["policy-hooks", "validate-pipeline"]
    }
  ]
};

knowledge.resume = {
  bullets: [
    {
      style: "精简版",
      text: "主导实现 CodeLite 本地 Python CLI coding agent runtime，完成 AgentLoop/ToolRouter 主链、task-worktree 隔离执行、安全护栏、统一验证流水线及 retrieval、memory、model routing、subagent/MCP 扩展能力。"
    },
    {
      style: "增强版",
      text: "从 0 到 1 设计并落地 CodeLite 本地 coding agent runtime：以 cli.py 为组合根装配 50+ 源文件级模块，构建 AgentLoop + ToolRouter 执行主链，设计 lease + managed worktree 隔离任务执行，引入 PolicyGate + hooks 双重安全护栏，补齐 Todo/Context/Watchdog/Delivery/ValidatePipeline 等运行时治理能力，并通过 tests/core 与 acceptance bundle 建立可证明交付闭环。"
    }
  ],
  star: {
    situation: "我需要做一个能写进简历、且经得起深挖的本地 coding agent 项目，不能只是一个会调模型的壳。",
    task: "目标是把 agent 的执行、安全、治理、验证和扩展能力做成一个能运行、能证明、能复盘的 runtime。",
    action: "我用 cli.py 的 build_runtime 做组合根，围绕 AgentLoop + ToolRouter 搭主链；引入 TaskRunner + WorktreeManager 做任务隔离；用 PolicyGate + HookRuntime 控制命令与写路径；加入 Todo、Context、Heart、Watchdog、Reconciler、Delivery、Lanes 管理运行时；最后用 ValidatePipeline、tests/core 和 acceptance bundles 固化交付标准。",
    result: "项目形成了从 v0.0 到 v0.2.2 的渐进式能力闭环，既能讲 agent 主流程，也能讲工程治理、可靠性和可扩展接口，适合在面试中进行项目深挖。"
  },
  scripts: [
    {
      length: "3 分钟版",
      paragraphs: [
        "这个项目叫 CodeLite，本质上是一个本地 Python CLI coding agent runtime。我一开始并不想只做一个聊天式命令行壳，所以设计目标是让它具备受控执行、安全护栏、运行时治理和统一验证。",
        "主入口在 cli.py，核心执行链是 AgentLoop + ToolRouter。用户输入进来以后，系统会做 session 记录、todo seed、retrieval 决策、model route、memory 组装、context compact，再执行工具调用。为了避免多任务互相污染，我把 task 和 git worktree 绑定了起来，TaskRunner 会把 ToolRouter 的执行根切到独立 worktree。",
        "工程上我还补了 PolicyGate + hooks 这套双重安全护栏，以及 ValidatePipeline 统一验证入口。再往后项目继续演进出 delivery queue、lane scheduler、watchdog、subagent、MCP 这些运行时能力。所以我一般会把它讲成一个可落地的本地 agent runtime，而不是单纯的 LLM 命令行工具。"
      ]
    },
    {
      length: "5 分钟版",
      paragraphs: [
        "我做这个项目的出发点，是想把本地 coding agent 做成真正的工程系统，而不是只能 demo 的命令行壳。项目采用 Python 实现，入口在 cli.py，通过 build_runtime 把 EventStore、TaskStore、Todo、Context、Memory、Retrieval、ModelRouter、DeliveryQueue、Watchdog 等服务统一装配起来。",
        "执行层面，核心是 AgentLoop + ToolRouter。AgentLoop 负责 turn 级状态推进，包括 session 事件、todo、heartbeat、memory、retrieval、model route、context compact；ToolRouter 负责暴露工具、做权限判断、调用 hooks，并在允许时执行工具。两者配合形成了稳定的 coding agent 主循环。",
        "这个项目最能体现工程深度的地方，是任务隔离。我实现了 TaskRunner + WorktreeManager 的组合，task 先获取 lease，再准备 managed worktree，再把 ToolRouter 的 workspace_root 指向 worktree.path。这样同仓多任务就不会互相改脏。",
        "在治理和可靠性上，我补了 TodoManager、ContextCompact、HeartService、Watchdog、Reconciler、DeliveryQueue、LaneScheduler。安全方面用 PolicyGate + pre_tool_use hook 双重限制越界路径和危险命令。最终再通过 ValidatePipeline 把 compileall、架构检查、pytest、health 整合成统一完成标准。",
        "所以这个项目在面试里我不会只讲模型接入，而是重点讲 agent runtime 的执行、安全、治理和验证闭环。"
      ]
    },
    {
      length: "10 分钟深挖版",
      paragraphs: [
        "如果从整体架构讲，这个项目可以分成几层。第一层是入口层，cli.py 负责 run、shell 和各类子命令；第二层是组合根 build_runtime；第三层是 AgentLoop + ToolRouter 的执行核心；第四层是 PolicyGate、HookRuntime、PermissionStore 的安全边界；第五层是 TaskRunner + WorktreeManager 的任务隔离；第六层是 Todo、Context、Heart、Cron、Watchdog、Reconciler 的治理层；第七层是 Retrieval、Memory、ModelRouter、Resilience、Critic 的智能增强层；最后是 Delivery、Lanes、AgentTeam、MCP、Skills 的扩展层。",
        "如果从一轮执行讲，用户输入进入 AgentLoop 后，系统先建立 session 和记忆，再按需要走 RetrievalRouter，之后由 ModelRouter 决定用 fast、deep 或 review profile。接着 MemoryRuntime 会组装长期上下文，ContextCompact 会在上下文过长时做压缩和 tool result 清理。如果模型要调用工具，则 ToolRouter 负责检查权限、执行 hook 和真正调用工具，所有过程都会回写 session 事件。",
        "如果从工程亮点讲，我会重点讲 task-worktree 隔离执行。TaskRunner 会先拿 task lease，再创建 managed worktree，并把 ToolRouter 的工作根切到隔离目录。这意味着 agent 的读写操作天然被限定在某个任务对应的工作树里，解决了多任务并发修改同一仓库的问题。",
        "第二个亮点是安全护栏。我没有只靠 prompt 约束模型，而是做了执行层门禁。PolicyGate 负责 shell allowlist 和 workspace 边界，pre_tool_use hook 负责危险 git 和受保护 runtime 路径拦截，必要时还可以通过 PermissionStore 做显式审批。这让项目在本地执行场景下更可信。",
        "第三个亮点是统一验证和可证明交付。我专门做了 ValidatePipeline，通过 scripts/validate.py 统一跑 compileall、lint_arch、pytest 和 health。每个阶段如果失败，都会写 failure trace 并触发 on_validation_fail hook。再加上 tests/core 和 docs/acceptance 下的阶段验收包，项目能形成代码、测试、命令输出三层证据。",
        "如果面试官继续问 agent 能力，我会再展开 RetrievalRouter、MemoryRuntime、ModelRouter、ResilienceRunner、CriticRefiner 之间的关系，说明我并没有把所有问题都推给一个模型，而是做成了分层、可解释、可替换的 runtime policy。"
      ]
    }
  ],
  keywords: ["本地 coding agent runtime", "组合根 / composition root", "task-worktree 隔离执行", "workspace boundary", "双重安全护栏", "统一验证流水线", "运行时治理", "可证明交付"],
  doNotSay: [
    "不要说自己做了复杂分布式调度。",
    "不要说自己实现了完整的 RAG 平台。",
    "不要说自己已经做成成熟插件市场。",
    "不要把启发式 router 吹成自适应智能编排系统。"
  ]
};

knowledge.graph = {
  nodes: [
    { id: "cli", label: "CLI", type: "entry", description: "命令入口与 shell 模式入口", refs: ["codelite/cli.py"] },
    { id: "runtime", label: "build_runtime", type: "entry", description: "组合根，装配 RuntimeServices", refs: ["codelite/cli.py"] },
    { id: "loop", label: "AgentLoop", type: "core", description: "主执行循环", refs: ["codelite/core/loop.py"] },
    { id: "tools", label: "ToolRouter", type: "core", description: "工具定义、权限、hook、执行", refs: ["codelite/core/tools.py"] },
    { id: "policy", label: "PolicyGate", type: "safety", description: "路径边界和 shell allowlist", refs: ["codelite/core/policy.py"] },
    { id: "hooks", label: "HookRuntime", type: "safety", description: "pre_tool_use / post_tool_use / on_validation_fail", refs: ["codelite/hooks/runtime.py"] },
    { id: "tasks", label: "TaskRunner", type: "isolation", description: "task、lease、session、worktree 执行绑定", refs: ["codelite/core/task_runner.py"] },
    { id: "worktree", label: "WorktreeManager", type: "isolation", description: "managed git worktree 生命周期", refs: ["codelite/core/worktree.py"] },
    { id: "todo", label: "TodoManager", type: "governance", description: "计划快照与 todo_write", refs: ["codelite/core/todo.py"] },
    { id: "context", label: "ContextCompact", type: "governance", description: "长上下文压缩与 tool result 清理", refs: ["codelite/core/context.py"] },
    { id: "heart", label: "Heart/Watchdog", type: "governance", description: "组件心跳、诊断和恢复", refs: ["codelite/core/heartbeat.py", "codelite/core/watchdog.py"] },
    { id: "delivery", label: "DeliveryQueue", type: "dispatch", description: "本地持久化投递队列", refs: ["codelite/core/delivery.py"] },
    { id: "lanes", label: "LaneScheduler", type: "dispatch", description: "generation token 与 lane 状态", refs: ["codelite/core/lanes.py"] },
    { id: "retrieval", label: "RetrievalRouter", type: "intelligence", description: "route 和 enoughness 决策", refs: ["codelite/core/retrieval.py"] },
    { id: "memory", label: "MemoryRuntime", type: "intelligence", description: "ledger、memory files、context assembly", refs: ["codelite/core/memory_runtime.py"] },
    { id: "model", label: "ModelRouter", type: "intelligence", description: "fast/deep/review 档位选择", refs: ["codelite/core/model_router.py"] },
    { id: "resilience", label: "ResilienceRunner", type: "intelligence", description: "auth retry、overflow compaction、fallback", refs: ["codelite/core/resilience.py"] },
    { id: "critic", label: "CriticRefiner", type: "intelligence", description: "答案审查与失败规则沉淀", refs: ["codelite/core/model_router.py"] },
    { id: "team", label: "AgentTeamRuntime", type: "extension", description: "team 和 subagent 运行时", refs: ["codelite/core/agent_team.py"] },
    { id: "mcp", label: "McpRuntime", type: "extension", description: "MCP registry 和 invocation", refs: ["codelite/core/mcp_runtime.py"] },
    { id: "validate", label: "ValidatePipeline", type: "quality", description: "统一验证闭环", refs: ["codelite/core/validate_pipeline.py", "scripts/validate.py"] },
    { id: "tests", label: "tests/core", type: "evidence", description: "自动化回归测试", refs: ["tests/core"] },
    { id: "acceptance", label: "acceptance bundles", type: "evidence", description: "人工验收文档与命令输出", refs: ["docs/acceptance"] },
    { id: "resume", label: "面试表达层", type: "presentation", description: "简历条目、讲稿、问答映射", refs: ["docs/interview-site"] }
  ],
  edges: [
    { from: "cli", to: "runtime", label: "builds" },
    { from: "runtime", to: "loop", label: "assembles" },
    { from: "runtime", to: "tools", label: "assembles" },
    { from: "loop", to: "tools", label: "executes via" },
    { from: "tools", to: "policy", label: "guards with" },
    { from: "tools", to: "hooks", label: "wraps with" },
    { from: "runtime", to: "tasks", label: "creates" },
    { from: "tasks", to: "worktree", label: "isolates with" },
    { from: "loop", to: "todo", label: "seeds" },
    { from: "loop", to: "context", label: "compacts with" },
    { from: "runtime", to: "heart", label: "observes" },
    { from: "runtime", to: "delivery", label: "dispatches" },
    { from: "delivery", to: "lanes", label: "cooperates with" },
    { from: "loop", to: "retrieval", label: "queries" },
    { from: "loop", to: "memory", label: "augments with" },
    { from: "loop", to: "model", label: "routes by" },
    { from: "loop", to: "resilience", label: "stabilizes with" },
    { from: "model", to: "critic", label: "reviews through" },
    { from: "delivery", to: "team", label: "feeds" },
    { from: "tools", to: "mcp", label: "invokes" },
    { from: "runtime", to: "validate", label: "closes with" },
    { from: "validate", to: "tests", label: "runs" },
    { from: "validate", to: "acceptance", label: "is explained by" },
    { from: "tests", to: "resume", label: "backs claims" },
    { from: "acceptance", to: "resume", label: "backs claims" }
  ]
};

window.CODELITE_KNOWLEDGE = knowledge;
