# 人工验收命令

这份清单用于项目最终封版验收，覆盖所有已完成内容。

建议顺序：

1. 先验 `v0.0` 基础链路
2. 再验任务、租约、worktree
3. 再验 `v0.2` 运行时服务
4. 最后验 `v0.2.1` 的九项新增机制
5. 收尾跑完整核心回归

## 1. CLI 基础与持久化

```powershell
python -m codelite.cli version
python -m codelite.cli health --json
python -m codelite.cli session replay --last 1
```

预期结果：

- `version` 输出 `0.2.1`
- `health --json` 至少包含：
  - `workspace_root`
  - `runtime_dir`
  - `events_path`
  - `sessions_dir`
  - `lane_count`
  - `delivery_pending_count`
  - `memory_entry_count`
- `session replay --last 1` 能回放最近会话事件

为什么这能说明机制成立：

- 说明 CLI、运行时目录、事件流、会话回放已经形成稳定入口。

## 2. 基础工具与安全护栏

```powershell
@'
from pathlib import Path
from codelite.config import load_app_config
from codelite.core.tools import ToolRouter, ToolError

root = Path(".").resolve()
config = load_app_config(root)
router = ToolRouter(root, config.runtime)

print(router.dispatch("bash", {"command": "echo ok"}).output)

try:
    router.dispatch("bash", {"command": "rm -rf ."})
except ToolError as exc:
    print(type(exc).__name__)
    print(str(exc))

try:
    router.dispatch("read_file", {"path": "../outside.txt"})
except ToolError as exc:
    print(type(exc).__name__)
    print(str(exc))
'@ | python -
```

预期结果：

- 第一段输出 `ok`
- 后两段都抛出 `ToolError`
- 错误语义分别是：
  - 危险命令被阻断
  - 工作区越界被阻断

为什么这能说明机制成立：

- 说明基础工具仍可用，且高风险操作仍被显式拦截。

## 3. 任务状态机、租约、过期回收

```powershell
@'
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

root = Path(".").resolve()
store = TaskStore(RuntimeLayout(root))

lease = store.acquire_lease("final-acceptance-demo", owner="tester", title="Final Acceptance Demo")
print(store.get_task("final-acceptance-demo").to_dict())
print(store.start_task("final-acceptance-demo", lease_id=lease.lease_id).to_dict())
print(store.complete_task("final-acceptance-demo", lease_id=lease.lease_id).to_dict())

expired = store.acquire_lease("final-expired-demo", owner="tester", ttl_seconds=30)
store.start_task("final-expired-demo", lease_id=expired.lease_id)
store._write_json(  # type: ignore[attr-defined]
    store.lease_path("final-expired-demo"),
    {
        "task_id": "final-expired-demo",
        "lease_id": expired.lease_id,
        "owner": expired.owner,
        "acquired_at": expired.acquired_at,
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
        "ttl_seconds": expired.ttl_seconds,
    },
)
print([task.to_dict() for task in store.reconcile_expired_leases()])
'@ | python -
```

预期结果：

- 依次能看到：
  - `leased`
  - `running`
  - `done`
- 过期回收结果中包含 `final-expired-demo`
- 其最终状态会进入 `blocked`

为什么这能说明机制成立：

- 说明任务状态机、租约释放、过期回收都还在按设计工作。

## 4. 受管 Worktree 与 Task 绑定

为了不污染当前主仓库，推荐直接使用项目内置的确定性 demo：

```powershell
python scripts/manual_task_run_binding_demo.py
```

预期结果：

- 输出 JSON
- `root_app_txt = "base\n"`
- `worktree_app_txt = "worktree-output\n"`
- `task_run.task.status = "done"`
- `task_show.metadata.worktree.path` 存在
- `task_show.metadata.session_id` 非空

为什么这能说明机制成立：

- 说明 `task -> lease -> worktree -> Agent 执行 -> metadata 回写` 这条链路已经打通。

## 5. v0.2 运行时服务

```powershell
python -m codelite.cli cron list --json
python -m codelite.cli heart status --json
python -m codelite.cli watchdog simulate --component tool_router --json
python -m codelite.cli todo show --last 1 --json
python -m codelite.cli context show --last 1 --json
```

预期结果：

- `cron list` 至少包含：
  - `heartbeat_scan`
  - `task_reconcile`
  - `worktree_gc`
  - `compact_maintenance`
  - `metrics_rollup`
- `heart status` 能看到组件健康状态
- `watchdog simulate` 返回恢复动作和 `snapshot_path`
- `todo/context show` 至少在有历史会话时可查询；若当前没有样本，允许提示不存在

为什么这能说明机制成立：

- 说明 `v0.2` 的运行时观测、定时维护、心跳、自愈、todo、上下文压缩都仍然可达。

## 6. LaneScheduler

```powershell
python -m codelite.cli lanes status --json
python -m codelite.cli lanes bump --lane main --json
```

预期结果：

- `lanes status` 里至少有 `main / cron / heartbeat`
- `lanes bump` 后 `generation` 增加

为什么这能说明机制成立：

- 说明系统已有命名 lane 和 generation 机制，不再只是单通道执行。

## 7. DeliveryQueue 与 Background Runtime

```powershell
python -m codelite.cli delivery status --json
python -m codelite.cli delivery enqueue --kind always_fail --payload-json '{"message":"boom"}' --max-attempts 1 --json
python -m codelite.cli delivery process --json
python -m codelite.cli background run --name digest --payload-json '{"text":"hello"}' --session-id final-bundle --json
python -m codelite.cli background process --json
python -m codelite.cli delivery status --json
```

预期结果：

- 第一条能看到初始队列状态
- `always_fail` 项经过 `process` 后进入 `failed`
- `background run/process` 后能返回 `result_path`
- 最后一条 `delivery status` 中：
  - `failed_count >= 1`
  - `done_count >= 1`

为什么这能说明机制成立：

- 说明 DeliveryQueue 已支持：
  - WAL + pending/done/failed 分区
  - 失败死信
  - 背景任务通过队列完成

## 8. ResilienceRunner

```powershell
python -m codelite.cli resilience drill --scenario auth_then_retry --json
python -m codelite.cli resilience drill --scenario overflow_then_fallback --json
```

预期结果：

- 第一条 `attempts` 中包含 `auth_rotation`
- 第二条 `attempts` 中包含：
  - `overflow_compaction`
  - `fallback`
- 最终都有可用 `result.text`

为什么这能说明机制成立：

- 说明重试洋葱已经具备认证重试、上下文压缩和 fallback 链路。

## 9. ValidatePipeline + AGENTS + Hooks

```powershell
python -m codelite.cli hooks doctor --json
python scripts/validate.py --json --pytest-target tests/core/test_v021_mechanisms.py
```

预期结果：

- `hooks doctor` 返回：
  - `agents_md_exists = true`
  - 三个 hook 模块都存在
- `validate.py` 返回：
  - `ok = true`
  - `stages` 里包含 `build / lint-arch / test / verify`

为什么这能说明机制成立：

- 说明规则仓库、hook 执法、统一验证管道都已形成最终闭环。

## 10. RetrievalRouter + MemoryMVP

```powershell
python -m codelite.cli retrieval run --prompt "Read README and summarize runtime services" --json
python -m codelite.cli memory timeline --json
python -m codelite.cli memory keyword --keyword runtime --json
```

预期结果：

- `retrieval run` 返回：
  - `route = "local_docs"`
  - `enough = true`
  - `result_count > 0`
- `memory timeline` 至少包含若干 `items`
- `memory keyword runtime` 能返回关联条目

为什么这能说明机制成立：

- 说明检索路由、审计和记忆账本与派生视图已经串起来了。

## 11. Skill/Plan Runtime + ModelRouter + CriticRefiner

```powershell
python -m codelite.cli skills load --name code-review --json
python -m codelite.cli model route --prompt "Please review this patch for bugs" --json
python -m codelite.cli critic review --prompt "summarize the work" --answer "TODO" --json
python -m codelite.cli critic log --kind validation --message "pipeline failed" --json
python -m codelite.cli critic refine --json
```

预期结果：

- `skills load` 返回 `code-review` 的说明
- `model route` 返回 `name = "review"`
- `critic review` 返回 `passed = false`
- `critic refine` 返回：
  - `rule_count >= 1`
  - 至少有 `failure_kind = "validation"`

为什么这能说明机制成立：

- 说明技能加载、todo nag 所在运行时、模型路由、critic/refiner 的失败学习闭环都可被直接触发。

## 12. 项目最终回归

```powershell
python -m pytest tests/core -q
```

预期结果：

- `29 passed`

为什么这能说明机制成立：

- 说明项目从 `v0.0` 到 `v0.2.1` 的核心能力在一个统一回归集下同时通过，可以作为最终封版的底线证明。
