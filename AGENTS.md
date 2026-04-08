# AGENTS

CodeLite 仓库规则索引，供本地运行时与 hooks 共同执行。

## 核心规则

1. 始终限制在当前工作区内执行。
2. 高风险 shell 命令必须被 `PolicyGate + pre_tool_use hook` 双重阻断。
3. 任务完成前优先跑统一验证管道，而不是只看单个测试是否通过。
4. todo 计划一旦偏离，需要及时更新，不要长期漂移。
5. 背景任务、投递队列、记忆条目都要留下可追溯落盘证据。

## 运行时入口

- 主循环：`codelite/cli.py`
- 工具调度：`codelite/core/tools.py`
- hook 运行时：`codelite/hooks/runtime.py`
- 验证管道：`codelite/core/validate_pipeline.py`

## 运维与验收

- 统一验证：`python scripts/validate.py`
- 架构检查：`python scripts/lint_arch.py --json`
- 验收文档：`docs/acceptance/`
