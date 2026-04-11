# CodeLite Interview Site

本目录是一套本地多页静态面试资料站，用来帮助你围绕当前仓库完成：

- 项目总览与主叙事
- 架构与核心机制理解
- 高频面试问答训练
- 简历条目与 3/5/10 分钟讲稿准备
- 知识图谱式复习

## 打开方式

直接打开 [index.html](./index.html) 即可，页面不依赖本地服务器。

## 页面说明

- `index.html`
  项目定位、亮点、时间线、面试前十分钟复习清单
- `architecture.html`
  分层视图、主调用链、关键设计取舍
- `mechanisms.html`
  核心机制卡片，按问题/设计/证据整理
- `interview.html`
  高频问题、30 秒答法、2 分钟展开、追问链路
- `resume.html`
  简历条目、STAR、3/5/10 分钟脚本、关键词与禁区
- `graph.html`
  项目知识图谱，按节点关系做记忆导航

## 资料来源

- `codelite/cli.py`
- `codelite/core/*`
- `codelite/hooks/*`
- `scripts/validate.py`
- `tests/core/*`
- `docs/acceptance/*`

## 复习顺序

1. 先看首页，记住项目定位和六个亮点。
2. 再看架构页，记住 build_runtime、AgentLoop、TaskRunner、ValidatePipeline。
3. 然后刷机制页和问答页。
4. 最后用图谱页做闭环记忆。
