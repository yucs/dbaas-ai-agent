# Agent 说明

本项目是基于 DeepAgent 框架开发的 DBAAS 智能助手。

开发时应尽可能使用 DeepAgent 框架已有能力，包括 Agent 运行时、tool calling、thread 上下文延续、streaming、human-in-the-loop、中断恢复、checkpoint 和上下文压缩等能力。除非现有框架能力无法满足项目需求，否则不要重复自造运行时、会话延续或工具调用链路。

每次修改代码、配置、文档或其他仓库内容前，必须先给出明确 plan，并等待用户确认后再执行修改。

当用户要求提交代码时，完成 commit 后应顺便 push 到当前分支对应的远端分支；如果 push 失败，需要向用户说明失败原因。

## Prompt 与角色工具边界

新增角色专属能力或 admin-only 工具时，不要把对应工具名、schema kind、调用示例或参数写进通用 `backend/prompts/system.md`。通用 prompt 只写所有角色都可见的能力和规则。

角色专属能力应写入对应的 role extend prompt，例如管理员专属能力写入 `backend/prompts/admin_extend_system_prompt.md`；普通用户 prompt 只描述权限边界和可见范围，不暴露 admin-only 工具名或 schema 调用形式。

新增或调整角色专属工具时，应补测试确认：

- 对应 role 的 tool set 包含该工具
- 其他 role 的 tool set 不包含该工具
- 普通用户最终 system prompt 不包含 admin-only 工具名、schema kind 调用形式或示例参数

## DBAAS Schema 与 Mock Server 契约

ai-agent 面向模型暴露的 DBAAS schema 应与 mock-server 对应接口返回结构保持一致。新增或调整 DBAAS 数据对象时，应优先让 mock-server 直接返回 ai-agent schema 定义的结构体，不要在 ai-agent 内额外设计一套 production/raw body 到 agent-facing schema 的字段映射层。

如果真实生产接口字段与 ai-agent schema 暂时不一致，应优先在 DBAAS/mock-server 适配层统一结构；只有在外部接口无法调整且确有必要时，才在 ai-agent 内增加转换，并在 phase 文档中明确原因和边界。

## 进度反馈与中断恢复

当任务进入多文件联动修改、代码处于中间态或开发时间较长时，必须持续输出阶段性进度，避免界面长时间只显示“思考中”。

如果任务被用户中途打断，恢复后必须先同步当前状态，再继续执行，至少包括：

- 已完成到哪一步
- 已修改了哪些文件
- 下一步准备做什么
- 当前是否仍处于半成品状态，以及哪些地方还没接完

在实现过程中，至少按模块级别持续汇报进度；每完成一个关键模块后，应及时向用户同步当前结果与下一步动作。

## 开发阶段兼容性

当前项目仍处于开发阶段。如果发生设计调整、接口变更或业务逻辑变化，可以不优先考虑旧逻辑或旧接口的向后兼容性；但涉及已有数据结构、持久化数据或用户状态变化时，必须提供相应的数据迁移方案或迁移脚本，确保现有数据可以被正确升级。

## DBAAS Mock Server

对接或验证 DBAAS 数据接口时，可以按需调用相邻项目的启动脚本启动 mock server：

```bash
PORT=9000 ../mock-server/start.sh
```

该脚本位于 `../mock-server/start.sh`。当前推荐使用 `9000` 端口，避免与本项目后端服务端口冲突。

本地联调结束后，可以使用以下脚本停止默认端口上的后端服务和 mock server：

```bash
./stop.sh
```
