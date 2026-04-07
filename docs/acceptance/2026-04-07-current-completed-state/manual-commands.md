# 人工验收命令

这份文档是“当前已完成全部机制”的详细人工验收清单。

建议按下面顺序执行：

1. CLI 与运行时可见性
2. 核心工具与安全护栏
3. 任务状态机与租约模型
4. 任务执行与 worktree 绑定
5. 受管 worktree 流程
6. 测试套件验证

## 1. CLI 与运行时可见性

### 1.1 查看版本

```powershell
python -m codelite.cli version
```

预期输出：

- 输出 `0.0.0`

为什么这能说明机制成立：

- 说明包入口、CLI 入口和基础模块加载都正常。

### 1.2 健康快照

```powershell
python -m codelite.cli health --json
```

预期输出：

- 输出一段 JSON
- 至少包含 `workspace_root`
- 至少包含 `runtime_dir`
- 至少包含 `events_path`
- 至少包含 `sessions_dir`
- 至少包含 `managed_worktree_count`
- 至少包含 `llm.model`

为什么这能说明机制成立：

- 说明配置加载、运行时目录识别、健康信息聚合都已经打通。

### 1.3 会话回放

```powershell
python -m codelite.cli session replay --last 1
```

预期输出：

- 第一行以 `session:` 开头
- 后续能看到 `session_started`、`turn_started`、`model_request`、`tool_started`、`tool_finished`、`assistant` 等事件

为什么这能说明机制成立：

- 说明事件持久化和会话回放不是纸面设计，而是真能用。

## 2. 核心工具与安全护栏

### 2.1 安全 bash

```powershell
@'
from pathlib import Path
from codelite.config import load_app_config
from codelite.core.tools import ToolRouter

root = Path('.').resolve()
config = load_app_config(root)
router = ToolRouter(root, config.runtime)
print(router.dispatch('bash', {'command': 'echo ok'}).output)
'@ | python -
```

预期输出：

- 输出 `ok`

为什么这能说明机制成立：

- 说明 `bash` 工具能够执行 allowlist 内的命令，并且子进程输出能被正确读取。

### 2.2 危险命令拦截

```powershell
@'
from pathlib import Path
from codelite.config import load_app_config
from codelite.core.tools import ToolRouter, ToolError

root = Path('.').resolve()
config = load_app_config(root)
router = ToolRouter(root, config.runtime)

try:
    router.dispatch('bash', {'command': 'rm -rf .'})
except ToolError as exc:
    print(type(exc).__name__)
    print(str(exc))
'@ | python -
```

预期输出：

- 第一行是 `ToolError`
- 第二行语义上表示“危险命令已拦截”

为什么这能说明机制成立：

- 说明安全护栏会主动阻止破坏性 shell 操作，而不是仅仅做日志记录。

### 2.3 工作区越界拦截

```powershell
@'
from pathlib import Path
from codelite.config import load_app_config
from codelite.core.tools import ToolRouter, ToolError

root = Path('.').resolve()
config = load_app_config(root)
router = ToolRouter(root, config.runtime)

try:
    router.dispatch('read_file', {'path': '../outside.txt'})
except ToolError as exc:
    print(type(exc).__name__)
    print(str(exc))
'@ | python -
```

预期输出：

- 第一行是 `ToolError`
- 第二行语义上表示“路径越界已拦截”

为什么这能说明机制成立：

- 说明工具访问严格限制在工作区内，不会读到工作区外部文件。

### 2.4 文件工具链

```powershell
@'
from pathlib import Path
from codelite.config import load_app_config
from codelite.core.tools import ToolRouter

root = Path('.').resolve()
config = load_app_config(root)
router = ToolRouter(root, config.runtime)

print(router.dispatch('write_file', {'path': 'runtime/manual-file.txt', 'content': 'hello\nworld'}).output)
print(router.dispatch('read_file', {'path': 'runtime/manual-file.txt'}).output)
print(router.dispatch('edit_file', {'path': 'runtime/manual-file.txt', 'old_text': 'world', 'new_text': 'codelite'}).output)
print(router.dispatch('read_file', {'path': 'runtime/manual-file.txt'}).output)
'@ | python -
```

预期输出：

- 第一段输出提示已写入若干字节
- 第二段输出显示编号行 `hello` 和 `world`
- 第三段输出提示编辑成功
- 第四段输出显示 `hello` 和 `codelite`

为什么这能说明机制成立：

- 说明最小可用的 `write_file -> read_file -> edit_file -> read_file` 闭环是正常的。

## 3. 任务状态机与租约模型

为了避免和历史样本冲突，建议每次都使用新的任务 ID。

### 3.1 准备任务 ID

```powershell
$env:CODELITE_TASK_ID = "manual-demo-$(Get-Date -Format 'HHmmss')"
$env:CODELITE_CONFLICT_ID = "conflict-demo-$(Get-Date -Format 'HHmmss')"
$env:CODELITE_EXPIRED_ID = "expired-demo-$(Get-Date -Format 'HHmmss')"
```

### 3.2 获取租约

```powershell
@'
import os
from pathlib import Path
from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

task_id = os.environ["CODELITE_TASK_ID"]
store = TaskStore(RuntimeLayout(Path('.').resolve()))
lease = store.acquire_lease(task_id, owner='tester', title='Manual Demo')
print(lease.lease_id)
print(store.get_task(task_id).to_dict())
'@ | python -
```

预期输出：

- 打印一个 `lease_id`
- 任务状态变成 `leased`
- `runtime/tasks` 下出现对应任务 JSON
- `runtime/leases` 下出现对应 `.lock`

为什么这能说明机制成立：

- 说明任务持久化和租约锁机制都已经在工作。

### 3.3 启动任务

```powershell
@'
import os
from pathlib import Path
from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

task_id = os.environ["CODELITE_TASK_ID"]
store = TaskStore(RuntimeLayout(Path('.').resolve()))
task = store.get_task(task_id)
running = store.start_task(task_id, lease_id=task.lease_id)
print(running.to_dict())
'@ | python -
```

预期输出：

- 任务状态变成 `running`
- 租约字段仍然保留

为什么这能说明机制成立：

- 说明状态流转 `leased -> running` 正常。

### 3.4 完成任务

```powershell
@'
import os
from pathlib import Path
from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

task_id = os.environ["CODELITE_TASK_ID"]
store = TaskStore(RuntimeLayout(Path('.').resolve()))
task = store.get_task(task_id)
done = store.complete_task(task_id, lease_id=task.lease_id)
print(done.to_dict())
'@ | python -
```

预期输出：

- 任务状态变成 `done`
- `lease_id`、`lease_owner`、`lease_expires_at` 都变成 `None`
- 对应 `.lock` 文件被移除

为什么这能说明机制成立：

- 说明正常完成和租约释放已经联动起来。

### 3.5 租约冲突

先获取第一把租约：

```powershell
@'
import os
from pathlib import Path
from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

task_id = os.environ["CODELITE_CONFLICT_ID"]
store = TaskStore(RuntimeLayout(Path('.').resolve()))
lease = store.acquire_lease(task_id, owner='alpha')
print(lease.lease_id)
'@ | python -
```

再尝试第二次获取：

```powershell
@'
import os
from pathlib import Path
from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

task_id = os.environ["CODELITE_CONFLICT_ID"]
store = TaskStore(RuntimeLayout(Path('.').resolve()))
store.acquire_lease(task_id, owner='beta')
'@ | python -
```

预期输出：

- 第二条命令失败
- 报错类型是 `LeaseConflictError`
- 错误信息说明该任务已经被 `alpha` 获取

为什么这能说明机制成立：

- 说明同一个任务不会被重复领取，租约锁有效。

### 3.6 过期租约回收

```powershell
@'
import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from codelite.storage.events import RuntimeLayout
from codelite.storage.tasks import TaskStore

task_id = os.environ["CODELITE_EXPIRED_ID"]
store = TaskStore(RuntimeLayout(Path('.').resolve()))

lease = store.acquire_lease(task_id, owner='alpha', ttl_seconds=30)
store.start_task(task_id, lease_id=lease.lease_id)

lease_path = store.lease_path(task_id)
data = json.loads(lease_path.read_text(encoding='utf-8'))
data["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
lease_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

print([task.to_dict() for task in store.reconcile_expired_leases()])
print(store.get_task(task_id).to_dict())
'@ | python -
```

预期输出：

- 回收结果里包含当前任务
- 最终任务状态是 `blocked`
- `blocked_reason` 是 `lease expired`
- `.lock` 文件被移除

为什么这能说明机制成立：

- 说明过期租约可以被恢复处理，任务会进入待人工关注状态。

## 4. 任务执行与 worktree 绑定

因为真实 `task run` 依赖模型行为，最稳定的人工验收方式是先跑 deterministic demo 脚本。

### 4.1 确定性 demo 脚本

```powershell
python scripts/manual_task_run_binding_demo.py
```

预期输出：

- 输出一段 JSON
- `root_app_txt` 保持为 `base\n`
- `worktree_app_txt` 变成 `worktree-output\n`
- `task_run.task.status` 是 `done`
- `task_run.worktree.path` 非空
- `task_show.metadata.worktree.path` 与上面的 worktree 路径一致
- `task_show.metadata.session_id` 非空
- `worktree_list` 里有一个受管 worktree

为什么这能说明机制成立：

- 说明完整链路已经成立：
  `task -> lease -> worktree -> Agent 执行 -> task metadata 回写 -> root runtime 持久化`

### 4.2 在当前仓库直接跑 `task run`

```powershell
python -m codelite.cli task run --task-id demo_parallel_01 --title "Demo Parallel 01" --json "Read README.md and summarize the current repository state."
```

预期输出：

- 输出 JSON
- `task.status` 是 `done`
- 有 `session_id`
- `worktree.path` 指向 `runtime/worktrees/...`
- `answer` 非空

为什么这能说明机制成立：

- 说明真实 CLI 已经可以驱动“任务 + worktree”的执行链路。

### 4.3 查看任务详情

```powershell
python -m codelite.cli task show --task-id demo_parallel_01 --json
```

预期输出：

- 返回该任务的 JSON
- `metadata.worktree.path` 存在
- `metadata.session_id` 存在
- `metadata.prompt` 与执行时的 prompt 一致
- `metadata.last_answer_preview` 非空

为什么这能说明机制成立：

- 说明任务记录里不只是有状态，还有后续人工排查和回放所需的执行指针。

### 4.4 列出已知任务

```powershell
python -m codelite.cli task list --json
```

预期输出：

- 返回 JSON 数组
- 里面包含刚刚执行过的任务

为什么这能说明机制成立：

- 说明任务发现和运维可见性已经具备。

## 5. 受管 worktree 流程

为了不污染当前项目仓库，建议用一个临时 demo 仓库来做 worktree 隔离测试。

### 5.1 创建临时 demo 仓库

```powershell
$env:CODELITE_WT_DEMO = Join-Path $env:TEMP ("codelite-wt-demo-" + (Get-Date -Format 'yyyyMMddHHmmss'))
New-Item -ItemType Directory -Force -Path $env:CODELITE_WT_DEMO | Out-Null
Set-Location $env:CODELITE_WT_DEMO
git init -b main
git config user.email "demo@example.com"
git config user.name "CodeLite Demo"
"base" | Set-Content -Path "app.txt" -Encoding utf8
git add app.txt
git commit -m "init"
$env:CODELITE_WORKSPACE_ROOT = $env:CODELITE_WT_DEMO
```

预期输出：

- 生成一个干净的临时 Git 仓库
- `app.txt` 已经提交到 `main`

为什么这能说明机制成立：

- 这是一个安全的 worktree 验证环境，不会污染当前主仓库。

### 5.2 创建两个 worktree

```powershell
python -m codelite.cli worktree prepare --task-id demo_a --title "Task A" --json
python -m codelite.cli worktree prepare --task-id demo_b --title "Task B" --json
```

预期输出：

- 两条命令都返回 JSON
- 每个结果都包含 `task_id`、`branch`、`path`、`base_ref`
- 两个 `branch` 值不同
- 两个 `path` 目录都存在于 `runtime/worktrees/` 下

为什么这能说明机制成立：

- 说明受管 worktree 创建可用，并且每个任务都有自己的分支与目录。

### 5.3 列出受管 worktree

```powershell
python -m codelite.cli worktree list --json
```

预期输出：

- 返回一个 JSON 数组
- 有两个记录
- 每条记录都包含 `task_id`、`branch`、`path`、`attached`、`path_exists`、`head`
- `attached` 为 `true`
- `path_exists` 为 `true`

为什么这能说明机制成立：

- 说明 runtime 元数据和 Git 真实状态能够对齐。

### 5.4 验证隔离性

```powershell
$worktrees = python -m codelite.cli worktree list --json | ConvertFrom-Json
$pathA = ($worktrees | Where-Object { $_.task_id -eq "demo_a" }).path
$pathB = ($worktrees | Where-Object { $_.task_id -eq "demo_b" }).path

"task-a" | Set-Content -Path (Join-Path $pathA "app.txt") -Encoding utf8
Get-Content (Join-Path $env:CODELITE_WT_DEMO "app.txt")
Get-Content (Join-Path $pathB "app.txt")

"task-b" | Set-Content -Path (Join-Path $pathB "app.txt") -Encoding utf8
Get-Content (Join-Path $pathA "app.txt")
Get-Content (Join-Path $pathB "app.txt")
```

预期输出：

- 修改 `demo_a/app.txt` 后，根仓库 `app.txt` 仍然是 `base`
- 修改 `demo_a/app.txt` 后，`demo_b/app.txt` 仍然是 `base`
- 修改 `demo_b/app.txt` 后，`demo_a/app.txt` 仍然是 `task-a`
- 修改 `demo_b/app.txt` 后，`demo_b/app.txt` 是 `task-b`

为什么这能说明机制成立：

- 说明不同任务的 worktree 改动互不污染。

### 5.5 恢复干净状态后再删除

```powershell
"base" | Set-Content -Path (Join-Path $pathA "app.txt") -Encoding utf8
"base" | Set-Content -Path (Join-Path $pathB "app.txt") -Encoding utf8
git -C $pathA status --short
git -C $pathB status --short
```

预期输出：

- 两个 `git status --short` 都不输出内容

为什么这能说明机制成立：

- 保证删除 worktree 时不需要 `--force`，避免把“脏目录删除”误当成正常流程。

### 5.6 删除受管 worktree

```powershell
python -m codelite.cli worktree remove --task-id demo_a --json
python -m codelite.cli worktree remove --task-id demo_b --json
python -m codelite.cli worktree list --json
```

预期输出：

- 每次 remove 都返回 JSON
- 返回值里 `attached: false`
- 返回值里 `path_exists: false`
- 最终 `list` 返回 `[]`

为什么这能说明机制成立：

- 说明受管 worktree 的清理链路是完整的。

### 5.7 退出 demo 仓库

```powershell
Remove-Item Env:CODELITE_WORKSPACE_ROOT
Set-Location C:\Users\24719\Desktop\codelite
```

预期输出：

- 后续命令重新作用于当前真实项目仓库

## 6. 测试套件验证

### 6.1 v0.0 回归

```powershell
python -m pytest tests/core/test_v00_smoke.py -q
```

预期输出：

- `13 passed`

为什么这能说明机制成立：

- 说明最初的 `v0.0` 基线没有被后续功能打回去。

### 6.2 任务与租约回归

```powershell
python -m pytest tests/core/test_tasks_leases.py -q
```

预期输出：

- 全部通过

为什么这能说明机制成立：

- 说明任务状态机和租约语义仍然稳定。

### 6.3 Worktree 回归

```powershell
python -m pytest tests/core/test_worktree_isolation.py -q
```

预期输出：

- `2 passed`

为什么这能说明机制成立：

- 说明受管 worktree 的创建、列出、删除和隔离都正常。

### 6.4 任务执行绑定回归

```powershell
python -m pytest tests/core/test_task_run_worktree_binding.py -q
```

预期输出：

- `2 passed`

为什么这能说明机制成立：

- 说明 `task run/show/list` 以及任务到 worktree 的执行绑定在可控条件下是正确的。

### 6.5 全量 core 回归

```powershell
python -m pytest tests/core -q
```

预期输出：

- `23 passed`

为什么这能说明机制成立：

- 说明当前所有已完成核心机制可以一起工作，不只是单点通过。
