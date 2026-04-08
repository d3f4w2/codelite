# 人工验收命令

这份清单只验证 `v0.2.1` 新增的 9 项机制，不重复之前已经验收过的基础 CLI、工具护栏、任务租约、worktree、v0.2 心跳/定时作业等链路。

建议顺序：

1. 先看 `lanes / delivery / resilience`
2. 再看 `validate / hooks / skills / background`
3. 最后看 `retrieval / memory / model / critic`

## 1. LaneScheduler

### 1.1 查看 lane 状态

```powershell
python -m codelite.cli lanes status --json
```

预期结果：

- 返回 JSON
- `lanes` 至少包含：
  - `main`
  - `cron`
  - `heartbeat`
- 每条 lane 至少包含：
  - `generation`
  - `queue_depth`
  - `active_count`
  - `max_concurrency`

为什么这能说明机制成立：

- 说明系统已经把主任务、定时作业、心跳通道分成了命名 lane，而不是仍然只有单一执行通道。

### 1.2 手动 bump 一次 generation

```powershell
python -m codelite.cli lanes bump --lane main --json
```

预期结果：

- 返回 JSON
- `name = "main"`
- `generation` 比之前更大
- `queue_depth = 0`

为什么这能说明机制成立：

- 说明 lane 代际号已是显式状态，可用于丢弃旧代任务回灌。

## 2. DeliveryQueue

### 2.1 查看队列状态

```powershell
python -m codelite.cli delivery status --json
```

预期结果：

- 返回 JSON
- 至少包含：
  - `wal_count`
  - `pending_count`
  - `failed_count`
  - `done_count`

为什么这能说明机制成立：

- 说明 DeliveryQueue 已经有独立运行时目录和状态汇总，而不是临时内存结构。

### 2.2 入队一个会失败的任务，观察进入 failed

```powershell
python -m codelite.cli delivery enqueue --kind always_fail --payload-json '{"message":"boom"}' --max-attempts 1 --json
python -m codelite.cli delivery process --json
python -m codelite.cli delivery status --json
```

预期结果：

- `enqueue` 返回一个 `delivery_id`
- `process` 返回的结果里该任务 `status = "failed"`
- 随后 `status` 里 `failed_count >= 1`
- `runtime/delivery-queue/failed/` 下能看到对应 `.json`

为什么这能说明机制成立：

- 说明队列失败不会悄悄丢失，而是进入死信区。

### 2.3 验证后台任务通过 delivery 队列完成

```powershell
python -m codelite.cli background run --name digest --payload-json '{"text":"hello"}' --session-id bg-demo --json
python -m codelite.cli background process --json
python -m codelite.cli delivery status --json
```

预期结果：

- `background run` 返回的 `kind = "background_task"`
- `background process` 返回 `result.result_path`
- 对应结果文件存在于 `runtime/background/results/`
- `delivery status` 中 `done_count >= 1`

为什么这能说明机制成立：

- 说明 DeliveryQueue 已不只是“能存”，而是能驱动真正的后台任务执行。

### 2.4 受控验证 WAL 恢复

```powershell
@'
import json
from pathlib import Path

from codelite.config import load_app_config
from codelite.core.delivery import DeliveryQueue
from codelite.storage.events import RuntimeLayout

root = Path(".").resolve()
layout = RuntimeLayout(root)
queue = DeliveryQueue(layout, load_app_config(root).runtime)
item = queue.enqueue("demo_echo", {"note": "recover me"})

pending_path = layout.delivery_pending_dir / f"{item.delivery_id}.json"
pending_path.unlink()

recovered_queue = DeliveryQueue(layout, load_app_config(root).runtime)
print((layout.delivery_pending_dir / f"{item.delivery_id}.json").exists())
print((layout.delivery_wal_dir / f"{item.delivery_id}.json").exists())
'@ | python -
```

预期结果：

- 第一行输出 `True`
- 第二行输出 `True`

为什么这能说明机制成立：

- 第一行说明重建队列实例后，pending 条目能从 WAL 自动恢复。
- 第二行说明原始 WAL 证据仍然保留，可用于崩溃恢复与审计。

## 3. ResilienceRunner

### 3.1 认证重试演练

```powershell
python -m codelite.cli resilience drill --scenario auth_then_retry --json
```

预期结果：

- 返回 JSON
- `attempts` 中至少包含一条：
  - `layer = "auth_rotation"`
- 最终 `result.text` 非空

### 3.2 上下文溢出 + fallback 演练

```powershell
python -m codelite.cli resilience drill --scenario overflow_then_fallback --json
```

预期结果：

- `attempts` 中至少包含：
  - `overflow_compaction`
  - `fallback`
  - 最后一条 `complete`
- 最终 `profile = "deep"`

为什么这能说明机制成立：

- 说明 v0.2.1 的三层重试洋葱已落地，而不是只有一个普通 try/retry。

## 4. ValidatePipeline

```powershell
python scripts/validate.py --json --pytest-target tests/core/test_v021_mechanisms.py
```

预期结果：

- 返回 JSON
- `ok = true`
- `stages` 按顺序至少包含：
  - `build`
  - `lint-arch`
  - `test`
  - `verify`
- 每个 stage 都有：
  - `command`
  - `exit_code`
  - `ok`

为什么这能说明机制成立：

- 说明项目已经有统一验证出口，不再依赖“手工想起跑哪些命令”。

## 5. AGENTS + Hooks

### 5.1 检查规则仓库与 hooks

```powershell
python -m codelite.cli hooks doctor --json
```

预期结果：

- `agents_md_exists = true`
- `modules.pre_tool_use.exists = true`
- `modules.post_tool_use.exists = true`
- `modules.on_validation_fail.exists = true`

### 5.2 验证 pre hook 会阻断受保护运行时路径写入

```powershell
@'
from pathlib import Path
from codelite.cli import build_runtime
from codelite.core.tools import ToolError

services = build_runtime(Path(".").resolve())
router = services.tool_router.for_session("manual-hook-check")

try:
    router.dispatch("write_file", {"path": "runtime/leases/forbidden.txt", "content": "x"})
except ToolError as exc:
    print(type(exc).__name__)
    print(str(exc))
'@ | python -
```

预期结果：

- 第一行是 `ToolError`
- 第二行语义上说明被 `pre_tool_use` 阻断

为什么这能说明机制成立：

- 说明规则不只写在文档里，而是真的能介入执行链路。

## 6. RetrievalRouter

### 6.1 只做决策

```powershell
python -m codelite.cli retrieval decide --prompt "Read README and summarize runtime services" --json
```

预期结果：

- `route = "local_docs"`
- `retrieve = true`
- `reason` 非空

### 6.2 实际执行一次本地检索

```powershell
python -m codelite.cli retrieval run --prompt "Read README and summarize runtime services" --json
```

预期结果：

- `decision.route = "local_docs"`
- `decision.enough = true`
- `decision.result_count > 0`
- `results` 里有本地文件路径和行号
- `runtime/audit.jsonl` 中新增检索审计记录

为什么这能说明机制成立：

- 说明系统不仅能回答“查不查”，还能记录“查哪里、查完够不够”。

## 7. MemoryMVP

先执行完上一节的 `retrieval run`，这样内存账本里一定会有一条检索记忆。

### 7.1 查看时间线视图

```powershell
python -m codelite.cli memory timeline --json
```

预期结果：

- 返回 JSON
- 至少包含 `items`
- 每条 item 至少包含：
  - `entry_id`
  - `kind`
  - `text`
  - `evidence`

### 7.2 按关键词回查

```powershell
python -m codelite.cli memory keyword --keyword runtime --json
```

预期结果：

- `entry_ids` 非空
- `entries` 非空
- 每条 entry 都能看到对应 `entry_id`

为什么这能说明机制成立：

- 说明 MemoryMVP 已形成 `Raw Ledger -> Derived Views` 的最小闭环，并且视图能回指原始条目。

## 8. Skill/Plan Runtime

### 8.1 加载 skill

```powershell
python -m codelite.cli skills load --name code-review --json
```

预期结果：

- `name = "code-review"`
- `summary` 非空
- `prompt_hint` 非空

### 8.2 受控验证 todo nag

```powershell
@'
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

from codelite.cli import main
from codelite.core.llm import ModelResult, ToolCallRequest
from codelite.storage.events import EventStore, RuntimeLayout
from codelite.storage.sessions import SessionStore

root = Path(".").resolve()
os.environ["CODELITE_WORKSPACE_ROOT"] = str(root)
os.environ["CODELITE_LLM_API_KEY"] = ""
os.environ["CODELITE_EMBEDDING_API_KEY"] = ""
os.environ["CODELITE_RERANK_API_KEY"] = ""
os.environ["TAVILY_API_KEY"] = ""

session_id = "manual-v021-nag"

class ScriptedNagModelClient:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools):
        del messages, tools
        self.calls += 1
        if self.calls <= 3:
            return ModelResult(
                text="",
                tool_calls=[
                    ToolCallRequest(
                        id=f"call-{self.calls}",
                        name="read_file",
                        arguments={"path": "AGENTS.md", "start_line": 1, "end_line": 1},
                    )
                ],
            )
        return ModelResult(text="completed with nag", tool_calls=[])

stdout = io.StringIO()
with redirect_stdout(stdout):
    main(["run", "--session-id", session_id, "--json", "Keep reading files without updating todos."], model_client=ScriptedNagModelClient())
print(stdout.getvalue())

events = SessionStore(EventStore(RuntimeLayout(root))).replay(session_id)
print(any(event["event_type"] == "todo_nag" for event in events))
'@ | python -
```

预期结果：

- 第一段输出的 `answer = "completed with nag"`
- 最后一行输出 `True`

为什么这能说明机制成立：

- 说明 skill/plan runtime 已经具备“计划长期不更新就提醒”的能力，而不是只靠人为自觉。

## 9. ModelRouter + CriticRefiner

### 9.1 查看模型路由结果

```powershell
python -m codelite.cli model route --prompt "Please review this patch for bugs" --json
```

预期结果：

- `name = "review"`
- `reason` 非空

### 9.2 记录失败样本并提炼规则

```powershell
python -m codelite.cli critic log --kind validation --message "pipeline failed" --json
python -m codelite.cli critic refine --json
```

预期结果：

- 第一条返回 `kind = "validation"`
- 第二条返回：
  - `rule_count >= 1`
  - `rules` 里至少有一条 `failure_kind = "validation"`
- `runtime/critic/rules.json` 存在

### 9.3 运行一次 heuristic review

```powershell
python -m codelite.cli critic review --prompt "summarize the work" --answer "TODO" --json
```

预期结果：

- `passed = false`
- `findings` 非空

为什么这能说明机制成立：

- 说明模型路由和 critic/refiner 已经不再只是概念：路由可见，失败样本可落盘，规则可提炼。

## 10. 自动化回归

先跑 v0.2.1 新增专项用例：

```powershell
python -m pytest tests/core/test_v021_mechanisms.py -q
```

预期结果：

- `3 passed`

再跑完整 core 回归：

```powershell
python -m pytest tests/core -q
```

预期结果：

- `29 passed`

为什么这能说明机制成立：

- 第一条说明 v0.2.1 九项机制已有专门自动化覆盖。
- 第二条说明这些新增机制没有把 `v0.0 ~ v0.2` 现有能力打回去。
