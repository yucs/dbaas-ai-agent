# Agent 说明

本项目是基于 DeepAgent 框架开发的 DBAAS 智能助手。

开发时应尽可能使用 DeepAgent 框架已有能力，包括 Agent 运行时、tool calling、thread 上下文延续、streaming、human-in-the-loop、中断恢复、checkpoint 和上下文压缩等能力。除非现有框架能力无法满足项目需求，否则不要重复自造运行时、会话延续或工具调用链路。

每次修改代码、配置、文档或其他仓库内容前，必须先给出明确 plan，并等待用户确认后再执行修改。

## DBAAS Mock Server

对接或验证 DBAAS 数据接口时，可以按需调用相邻项目的启动脚本启动 mock server：

```bash
PORT=9000 ../mock-server/start.sh
```

该脚本位于 `../mock-server/start.sh`。当前推荐使用 `9000` 端口，避免与本项目后端服务端口冲突。
