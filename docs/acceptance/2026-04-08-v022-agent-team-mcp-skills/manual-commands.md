# 人工验收命令

这份清单用于验证 `v0.2.2` 新增能力：

1. `agent team + subagent`
2. 外部 `skills` 兼容加载（拿来即用）
3. `MCP` 入口注册与调用
4. ToolRouter 对新能力的暴露

建议顺序：

1. 先验 `team/subagent`
2. 再验 `skills` 兼容
3. 再验 `mcp` 接口
4. 最后跑统一验证

## 1. Agent Team 基础能力

### 1.1 创建并列出 Team

```powershell
python -m codelite.cli team create --name manual-v022-team --strategy parallel --max-subagents 4 --json
python -m codelite.cli team list --json
```

预期结果：

- `team create` 返回 JSON，包含：
  - `team_id`
  - `name = "manual-v022-team"`
  - `strategy = "parallel"`
- `team list` 返回里至少包含：
  - 自动创建的 `default` team
  - 刚创建的 `manual-v022-team`

为什么这能证明机制成立：

- 说明 `agent team` 的持久化目录与读写链路可用。

## 2. Subagent 队列执行（可复现脚本）

> 为避免依赖真实模型行为，使用脚本化 model client 做可复现验收。

```powershell
@'
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

from codelite.cli import main
from codelite.core.llm import ModelResult

root = Path(".").resolve()
os.environ["CODELITE_WORKSPACE_ROOT"] = str(root)
os.environ["CODELITE_LLM_API_KEY"] = ""
os.environ["CODELITE_EMBEDDING_API_KEY"] = ""
os.environ["CODELITE_RERANK_API_KEY"] = ""
os.environ["TAVILY_API_KEY"] = ""

class ScriptedSubagentModelClient:
    def complete(self, messages, tools):
        del messages, tools
        return ModelResult(text="subagent complete", tool_calls=[])

def run_json(args, model_client=None):
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(args, model_client=model_client)
    assert code == 0
    return json.loads(stdout.getvalue())

team = run_json(["team", "create", "--name", "manual-v022-subagent-team", "--json"])
spawned = run_json([
    "subagent", "spawn",
    "--team-id", team["team_id"],
    "--prompt", "Summarize this task.",
    "--session-id", "manual-v022-parent",
    "--mode", "queue",
    "--json",
])
subagent_id = spawned["subagent"]["subagent_id"]

processed = run_json(["subagent", "process", "--json"], model_client=ScriptedSubagentModelClient())
detail = run_json(["subagent", "show", "--subagent-id", subagent_id, "--json"])

print(subagent_id)
print(any(item.get("subagent_id") == subagent_id and item.get("status") == "done" for item in processed))
print(detail["status"])
print(Path(detail["result_path"]).exists())
'@ | python -
```

预期结果：

- 输出四行关键结果：
  - 第一行是 `subagent_id`
  - 第二行是 `True`
  - 第三行是 `done`
  - 第四行是 `True`

为什么这能证明机制成立：

- 说明 `spawn -> delivery queue -> process -> result file` 全链路闭环成立。

## 3. Skills 兼容（外部 SKILL.md 拿来即用）

### 3.1 准备一个外部技能目录

```powershell
$skillDir = ".skills/manual-market-demo-1.2.0"
New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
@'
---
name: manual-market-demo
description: External market style skill for manual acceptance.
---

# Manual Market Demo Skill

Use this skill during acceptance testing.
'@ | Set-Content -Path "$skillDir/SKILL.md" -Encoding utf8
```

### 3.2 列出并加载外部技能

```powershell
python -m codelite.cli skills list --query manual-market-demo --json
python -m codelite.cli skills load --name manual-market-demo --json
python -m codelite.cli skills load --name .skills/manual-market-demo-1.2.0 --json
```

预期结果：

- `skills list` 返回里包含 `manual-market-demo`
- `skills load --name manual-market-demo` 返回：
  - `name = "manual-market-demo"`
  - `source = "external"`
  - `path` 指向 `.skills/manual-market-demo-1.2.0`
- 路径方式加载也返回同样的技能信息

为什么这能证明机制成立：

- 说明技能系统不再局限内置 skill，外部市场技能目录可直接接入使用。

## 4. MCP 接入口（注册/调用/移除）

### 4.1 准备一个最小 MCP echo 服务脚本

```powershell
@'
import json
import sys

line = sys.stdin.readline().strip()
payload = json.loads(line) if line else {}
print(json.dumps({
    "ok": True,
    "id": payload.get("id"),
    "method": payload.get("method"),
}))
'@ | Set-Content -Path "runtime/manual-echo-mcp.py" -Encoding utf8
```

### 4.2 注册与调用 MCP

```powershell
python -m codelite.cli mcp add --name manual-echo --command python --args-json '["runtime/manual-echo-mcp.py"]' --json
python -m codelite.cli mcp list --json
python -m codelite.cli mcp call --name manual-echo --request-json '{"id":"manual-1","method":"ping"}' --json
python -m codelite.cli mcp remove --name manual-echo --json
```

预期结果：

- `mcp add` 返回 `name = "manual-echo"`
- `mcp list` 包含 `manual-echo`
- `mcp call` 返回：
  - `response.ok = true`
  - `response.method = "ping"`
  - `invocation_path` 文件存在于 `runtime/mcp/invocations/`
- `mcp remove` 返回 `removed = true`

为什么这能证明机制成立：

- 说明 MCP 注册表、调用入口、调用落盘审计、生命周期管理都可用。

## 5. ToolRouter 新工具面可见性

```powershell
@'
from pathlib import Path
from codelite.cli import build_runtime

services = build_runtime(Path(".").resolve())
router = services.tool_router.for_session("manual-v022-tool-schema")

names = sorted(item["name"] for item in router.tool_schemas())
for target in [
    "skills_list",
    "subagent_spawn",
    "subagent_process",
    "subagent_status",
    "mcp_list",
    "mcp_call",
]:
    print(target in names)
'@ | python -
```

预期结果：

- 输出六行 `True`

为什么这能证明机制成立：

- 说明主 Agent 工具面已经暴露 `skills/subagent/mcp`，不仅是 CLI 子命令。

## 6. 自动化回归

```powershell
python -m pytest tests/core/test_v022_agent_team_mcp_skills.py -q
python -m pytest tests/core -q
python scripts/validate.py --json
```

预期结果：

- `test_v022_agent_team_mcp_skills.py`：`3 passed`
- `tests/core`：`35 passed`
- `validate.py --json`：
  - `ok = true`
  - `stages` 包含 `build/lint-arch/test/verify`

为什么这能证明机制成立：

- 说明新增机制有专门自动化覆盖，并且没有破坏既有核心链路。
