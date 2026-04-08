# 人工验收命令

这份清单只验证本次新增的 `v0.2` 机制，不重复此前已经验收过的基础 CLI、工具护栏、任务租约和 worktree 流程。

建议按下面顺序执行：

1. 先确认 `cron` 已注册新作业
2. 再用受控脚本验证 `todo + context compact`
3. 再验证 `task_reconcile` 对过期租约的自动回收
4. 最后验证 `heart + watchdog + metrics`

## 1. 查看 v0.2 内置 Cron 作业

```powershell
python -m codelite.cli cron list --json
```

预期结果：

- 返回一个 JSON 数组
- 至少包含以下四个作业：
  - `heartbeat_scan`
  - `task_reconcile`
  - `compact_maintenance`
  - `metrics_rollup`
- 每条记录都至少包含 `name`、`schedule`、`enabled`、`due`

为什么这能说明机制成立：

- 说明 `CronScheduler` 已经不是空壳，而是把 v0.2 的关键维护作业注册到了统一调度表里。

## 2. 受控验证 TodoManager + ContextCompact

这一步使用一个受控脚本模型，避免依赖真实线上模型输出，从而稳定验证：

- `todo_write` 是否能写入会话 todo
- 长历史会话是否会自动生成 context 快照
- `todo show / context show` 是否能读到结果

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
template = (root / "codelite" / "config" / "runtime.yaml").read_text(encoding="utf-8")
config_text = (
    template.replace("context_auto_compact_message_count: 18", "context_auto_compact_message_count: 4")
    .replace("context_keep_last_messages: 8", "context_keep_last_messages: 2")
    .replace("context_auto_compact_char_count: 12000", "context_auto_compact_char_count: 200")
)
config_path = root / "runtime" / "manual-v02-runtime.yaml"
config_path.write_text(config_text, encoding="utf-8")

os.environ["CODELITE_CONFIG_PATH"] = str(config_path)
os.environ["CODELITE_LLM_API_KEY"] = ""
os.environ["CODELITE_EMBEDDING_API_KEY"] = ""
os.environ["CODELITE_RERANK_API_KEY"] = ""
os.environ["TAVILY_API_KEY"] = ""

layout = RuntimeLayout(root)
session_store = SessionStore(EventStore(layout))
session_id = "manual-v02-context"
session_store.ensure_session(session_id)
for i in range(5):
    session_store.append_message(session_id, role="user", content=f"older user message {i}")
    session_store.append_message(session_id, role="assistant", content=f"older assistant message {i}")

class ScriptedTodoModelClient:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools):
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            return ModelResult(
                text="planning",
                tool_calls=[
                    ToolCallRequest(
                        id="call-todo",
                        name="todo_write",
                        arguments={
                            "items": [
                                {"id": "inspect", "content": "Inspect repository", "status": "in_progress"},
                                {"id": "summarize", "content": "Write summary", "status": "pending"},
                            ]
                        },
                    )
                ],
            )
        return ModelResult(text="done", tool_calls=[])

def run_json(args, model_client=None):
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(args, model_client=model_client)
    return exit_code, json.loads(stdout.getvalue())

exit_code, run_payload = run_json(
    ["run", "--session-id", session_id, "--json", "Plan and then finish the task."],
    model_client=ScriptedTodoModelClient(),
)
_, todo_payload = run_json(["todo", "show", "--session-id", session_id, "--json"])
_, context_payload = run_json(["context", "show", "--session-id", session_id, "--json"])

print(json.dumps({
    "exit_code": exit_code,
    "run": run_payload,
    "todo": todo_payload,
    "context": context_payload,
}, ensure_ascii=False, indent=2))
'@ | python -
```

预期结果：

- `exit_code` 为 `0`
- `run.answer` 为 `done`
- `todo.counts` 里至少有：
  - `in_progress = 1`
  - `pending = 1`
- `context.original_message_count` 大于 `context.compacted_message_count`
- `context.kept_message_count = 2`
- `runtime/todos/manual-v02-context.json` 存在
- `runtime/context/manual-v02-context.json` 存在

为什么这能说明机制成立：

- 说明 `ToolRouter` 已支持 `todo_write`
- 说明 `TodoManager` 能跟随会话落盘并通过 CLI 读回
- 说明 `ContextCompact` 已真正接入 `AgentLoop`，不是孤立工具类

## 3. 验证 task_reconcile 自动回收过期租约

这里不重复测“如何领取租约”，只测 v0.2 新增的“由 Cron/Reconciler 自动回收”这条链路。

### 3.1 先造一个已过期但仍处于运行中的任务

```powershell
@'
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

store = TaskStore(RuntimeLayout(Path(".").resolve()))
lease = store.acquire_lease("manual-v02-expired", owner="tester", ttl_seconds=30)
store.start_task("manual-v02-expired", lease_id=lease.lease_id)

expired_at = datetime.now(timezone.utc) - timedelta(seconds=5)
store._write_json(  # type: ignore[attr-defined]
    store.lease_path("manual-v02-expired"),
    {
        "task_id": "manual-v02-expired",
        "lease_id": lease.lease_id,
        "owner": lease.owner,
        "acquired_at": lease.acquired_at,
        "expires_at": expired_at.isoformat(),
        "ttl_seconds": lease.ttl_seconds,
    },
)

print(store.get_task("manual-v02-expired").to_dict())
'@ | python -
```

预期结果：

- 输出任务 JSON
- 当前 `status` 仍是 `running`
- 对应 `runtime/leases/*.lock` 仍然存在

### 3.2 运行 cron job：`task_reconcile`

```powershell
python -m codelite.cli cron run --job task_reconcile --json
```

预期结果：

- 返回 JSON
- `last_status = "ok"`
- `result.expired_task_ids` 包含 `manual-v02-expired`

### 3.3 查看任务最终状态

```powershell
python -m codelite.cli task show --task-id manual-v02-expired --json
```

预期结果：

- `status = "blocked"`
- `blocked_reason = "lease expired"`
- 原来的 `.lock` 文件已被回收

为什么这能说明机制成立：

- 说明 v0.2 新增的 `CronScheduler -> Reconciler -> TaskStore.reconcile_expired_leases` 链路已经真正跑通。

## 4. 验证 HeartService

### 4.1 手动打一条红色心跳

```powershell
python -m codelite.cli heart beat --component tool_router --status red --failure-streak 3 --json
```

预期结果：

- 返回 JSON
- `component_id = "tool_router"`
- `status = "red"`
- `failure_streak = 3`

### 4.2 查看健康状态

```powershell
python -m codelite.cli heart status --json
```

预期结果：

- 返回 JSON
- `components` 里能找到 `tool_router`
- 其 `status = "red"`
- 能看到 `hearts_path`

为什么这能说明机制成立：

- 说明组件心跳已经独立落盘并能被状态判定逻辑读回，不再只是运行时内存变量。

## 5. 验证 Watchdog

```powershell
python -m codelite.cli watchdog simulate --component tool_router --json
```

预期结果：

- 返回 JSON
- `component_id = "tool_router"`
- `status_before = "red"`
- `status_after = "yellow"`
- `actions` 至少包含：
  - `captured diagnostic snapshot`
  - `queued safe pause marker`
- `snapshot_path` 指向 `runtime/watchdog/*.json`

为什么这能说明机制成立：

- 说明 `Watchdog` 能根据异常组件给出恢复动作，并把诊断现场落盘，形成最小自愈闭环。

## 6. 验证 Metrics Rollup

```powershell
python -m codelite.cli cron run --job metrics_rollup --json
```

预期结果：

- 返回 JSON
- `last_status = "ok"`
- `result.metrics_path` 指向 `runtime/metrics/rollup-latest.json`

然后查看生成文件：

```powershell
Get-Content runtime/metrics/rollup-latest.json
```

预期结果：

- 至少包含：
  - `generated_at`
  - `task_counts`
  - `todo_snapshot_count`
  - `context_snapshot_count`
  - `heart`

为什么这能说明机制成立：

- 说明 v0.2 的运行时观测已经不止有 `events.jsonl`，还具备面向运维汇总的指标快照出口。

## 7. 自动化回归

只跑和本次改动直接相关的新增用例：

```powershell
python -m pytest tests/core/test_v02_runtime_services.py -q
```

预期结果：

- `2 passed`

再跑完整 core 回归，确保旧能力没有被打回去：

```powershell
python -m pytest tests/core -q
```

预期结果：

- `25 passed`

为什么这能说明机制成立：

- 第一条说明 v0.2 新能力有专门自动化覆盖。
- 第二条说明本次新增机制和 `v0.0/Phase 1` 现有能力能够共存。
