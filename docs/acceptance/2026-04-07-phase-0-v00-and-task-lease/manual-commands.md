# 手工验收命令

以下命令按“先理解系统，再验证护栏，最后验证状态机”的顺序排列。

## 1. CLI 基础入口

```powershell
python -m codelite.cli version
```

预期结果：

- 输出 `0.0.0`

解释：

- 说明 CLI 入口、包版本和模块加载正常。

```powershell
python -m codelite.cli health --json
```

预期结果：

- 输出一段 JSON
- 至少包含 `version`、`workspace_root`、`runtime_dir`、`events_path`、`sessions_dir`
- 至少包含 `llm.model` 与 `llm.configured`

解释：

- 说明配置加载、运行时目录和基础存储可见。

```powershell
python -m codelite.cli session replay --last 1
```

预期结果：

- 第一行形如 `session: ...`
- 后面能看到 `session_started`、`turn_started`、`model_request`、`tool_started`、`tool_finished`、`assistant`

解释：

- 说明事件日志与会话回放链路已打通。

```powershell
python -m pytest tests/core -q
```

预期结果：

- 当前结果应为 `19 passed`

解释：

- 说明目前已经实现并被测试覆盖的核心机制全部通过。

## 2. 工具与安全护栏

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

预期结果：

- 输出 `ok`

解释：

- 说明 `bash` 工具可用，子进程输出可被正确读取。

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

预期结果：

- 第一行是 `ToolError`
- 第二行语义上表示“危险命令已拦截”

解释：

- 说明危险 shell 命令不会真的被执行。

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

预期结果：

- 第一行是 `ToolError`
- 第二行语义上表示“路径越界已拦截”

解释：

- 说明工具层不会越过工作区边界。

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

预期结果：

- 先看到写入成功
- 然后看到按行读取的 `hello / world`
- 再看到编辑成功
- 最后读取变成 `hello / codelite`

解释：

- 说明 `write_file -> read_file -> edit_file -> read_file` 的最小闭环成立。

## 3. 任务状态机与租约模型

先准备一个唯一任务 ID：

```powershell
$env:CODELITE_TASK_ID = "manual-demo-$(Get-Date -Format 'HHmmss')"
```

### 3.1 领取租约

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

预期结果：

- 打印一个 `lease_id`
- 任务 `status` 为 `leased`
- `runtime/tasks/` 下生成 `.json`
- `runtime/leases/` 下生成 `.lock`

解释：

- 说明任务持久化和租约锁已经落地。

### 3.2 开始执行

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

预期结果：

- 任务 `status` 变成 `running`
- `lease_id` 仍保留

解释：

- 说明 `leased -> running` 的状态流转正常。

### 3.3 完成任务

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

预期结果：

- 任务 `status` 变成 `done`
- `lease_id`、`lease_owner`、`lease_expires_at` 都变成 `None`
- 对应 `.lock` 文件被删除

解释：

- 说明 `running -> done` 与租约释放是联动生效的。

### 3.4 租约冲突

```powershell
$env:CODELITE_CONFLICT_ID = "conflict-demo-$(Get-Date -Format 'HHmmss')"
```

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

预期结果：

- 第二条命令抛出 `LeaseConflictError`
- 错误语义应为“当前任务已被 alpha 领取”

解释：

- 说明同一任务不会被重复领取。

### 3.5 过期租约回收

```powershell
$env:CODELITE_EXPIRED_ID = "expired-demo-$(Get-Date -Format 'HHmmss')"
```

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

预期结果：

- 回收结果列表里包含该任务
- 最终任务 `status` 为 `blocked`
- `blocked_reason` 为 `lease expired`
- `.lock` 文件被删除

解释：

- 说明过期租约可以被系统回收，任务会进入待人工处理状态。
