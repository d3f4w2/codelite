本项目目标是开发一个，类似于claudecode的命令行code agent
目前想到的能比claudecode，做得好的地方，就是轻量，细节可视化，可控
然后我们有相关的基础项目，比如说，learn-claude-code以及claw0
这两个项目都是我实际从头到尾做过的，因为我们要作为实习项目写道简历上，所以，我们的核心机制，按照这上面的来实现就可以了。
此外我还有claudecode源码泄露出来的核心工程亮点以及一些做项目的规范（怎么做比较好），这些我都会给你，你不需要扩充太多内容，但是要足够满足我的要求。
此外，对于命令行，要充分体现个性化，定制化，可视化的原则。这是本项目的核心.
现在请参照这些来进行构思，实际资料，我会在后续提供给你
参考实现参考，要求参考文档，必要时参考claudecode源码

## 当前 CLI 形态

- `codelite`
  直接进入交互式 shell，显示欢迎面板、运行态摘要、最近活动和 slash 本地命令。
- `codelite 修复 validate pipeline`
  不必显式输入 `run`，裸 prompt 会直接作为单轮 agent 任务执行。
- `codelite shell --label MyAgent`
  可以覆盖 shell 标题和 prompt 前缀，方便做个性化演示。

## Shell 内置命令

- `/help`
- `/plan`
- `/act`
- `/mode`
- `/status`
- `/session`
- `/replay 3`
- `/todo`
- `/context`
- `/memory`
- `/new`
- `/clear`
- `/exit`

## 交互体验

- 在 Windows PowerShell / Windows Terminal 中，直接输入 `codelite` 就会进入对话式 shell。
- 输入 `/` 会弹出可用命令列表。
- 按 `Shift+Tab` 可以在 `plan` 和 `act` 两种模式之间循环切换。
