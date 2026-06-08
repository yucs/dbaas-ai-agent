# DBAAS 智能助手设计说明

## 1. 文档目的

本文档是 DBAAS 智能助手的当前设计入口。

它只保留跨阶段仍然有效的总原则：

- 项目目标
- 能力边界
- 文档分工
- 高层架构
- 长期设计约束

具体阶段设计、接口细节、前端交互和压缩实现不再在本文档中重复展开，
统一放到对应的 Phase 文档和专项文档中维护。

## 2. 项目背景

本项目目标是基于 DeepAgent 构建一个 DBAAS 智能助手。
该助手需要能够通过自然语言帮助用户查询和操作 DBAAS 服务与平台资源，
同时保证执行过程安全、可审计，并且能够方便地对接现有管理面接口。

本项目以 DeepAgent 作为 Agent 运行时内核。
因此，后续涉及的 Session、`thread_id`、tool calling、审批中断与恢复、
streaming、checkpoint、上下文压缩等能力，都优先围绕 DeepAgent 的执行模型展开。

模型接入侧的长期目标是面向私有部署模型：

- 默认面向私有部署的大模型
- 不以公有云 API 作为唯一前提
- 当前优先考虑接入私有部署的 DeepSeek、Qwen 等模型
- 具体模型名以实际部署平台上的模型标识为准

开发阶段可以先支持公网 API 以降低联调成本，但这只作为开发和验证手段，
不改变项目整体面向私有部署模型的长期目标。

模型接入层应统一抽象为：

- `model`
- `base_url`
- `api_key` 或等价鉴权方式
- `context_window`
- `max_output_tokens`

由部署环境决定其指向公网服务还是私有部署服务。

## 3. 文档分工

当前文档按“当前契约 + 阶段记录”的方式维护：

- [API.md](./API.md)
  - 当前页面与后端之间的接口契约
- [FRONTEND.md](./FRONTEND.md)
  - 前端页面需求、交互规则和当前展示边界
- [FUTURE.md](./FUTURE.md)
  - 已讨论但短期不实现的长期设计备忘，不占用 Phase 编号
- [PHASE1.md](./PHASE1.md)
  - 本地登录、多用户、多 Session、Session 文件投影和前端基座
- [PHASE2.md](./PHASE2.md)
  - 真实 DeepAgent runtime、模型接入、SQLite checkpoint
- [PHASE3.md](./PHASE3.md)
  - 上下文压缩接入、压缩提示词、压缩与记忆边界
- [PHASE4.md](./PHASE4.md)
  - FastAPI + SSE 流式对话、压缩提醒和流式错误策略
- [PHASE5.md](./PHASE5.md)
  - DBAAS 服务列表快照、后台同步和只读查询策略
- [PHASE6.md](./PHASE6.md)
  - 监控指标 catalog、latest/history 查询和快照刷新策略
- [PHASE7.md](./PHASE7.md)
  - 写工具、审批闭环、operation/task 模型和异步任务追踪
- [PHASE8.md](./PHASE8.md)
  - 轻量 Precheck Tool、资源/存储调整前的只读事实查询和写前风险判断

已合并的历史专项文档：

- 上下文压缩实现已合并到 [PHASE3.md](./PHASE3.md)、[PHASE4.md](./PHASE4.md) 和 [PHASE7.md](./PHASE7.md)
- 记忆边界已合并到 [PHASE3.md](./PHASE3.md) 和 [PHASE7.md](./PHASE7.md)
- Session 管理设计已合并到 [PHASE1.md](./PHASE1.md)

## 4. 能力边界

DeepAgent 原生支持：

- Agent 运行时与 tool calling loop
- 基于 LangGraph 的 durable execution
- `thread_id` 驱动的执行上下文延续
- streaming 能力
- human-in-the-loop 中断与恢复机制
- checkpoint
- context engineering 基础机制

DeepAgents 默认还会向模型暴露 `write_todos`、文件读写、shell 执行和
`task` 子代理等通用内置工具。DBAAS 助手的运行时边界必须更窄：

- 模型可见工具只允许项目通过 `build_dbaas_tools(...)` 显式注册的 DBAAS 工具
- DeepAgents 内置的 `task` 子代理、`execute`、文件工具和 todo 工具在模型请求前被硬过滤
- 禁用内置工具不改变 DeepAgent 的 checkpoint、streaming、tool calling loop、human-in-the-loop 和上下文压缩能力
- 需要读取运行态数据时，应通过受控 DBAAS 工具返回结构化结果，而不是让模型直接读取任意本地文件

本项目负责实现：

- DBAAS 领域 prompt、工具和策略
- 对接 `mock-server` 与后续真实控制面的客户端封装
- 写操作前的轻量 precheck 工具和事实查询策略
- 多用户、多 Session 的产品层模型
- Session 与 `thread_id` 的绑定规则
- 历史 Session 列表、归档、删除等页面能力
- 审批记录、operation 和 task 的产品层持久化
- 对外 HTTP API 与 SSE 协议
- 本地文件存储布局和后续数据迁移方案

当前需要保持一个明确边界：

- DeepAgent runtime 负责执行状态和上下文延续
- 产品层 Session 负责页面展示、审计、归档、删除和用户可理解的历史
- 运行时压缩结果不是产品层真相
- DBAAS 实时状态必须始终从后端接口读取
- Precheck 只负责写前事实收集和风险说明，不是执行或审批链路

## 5. 高层架构

当前系统可以按下面几层理解：

1. API 层
   - Session 接口
   - 消息接口
   - SSE 流式接口
   - Approval / task / operation 相关接口

2. Agent Runtime 层
   - DeepAgent graph 组装
   - Prompt 与运行时配置
   - Tool calling
   - 审批中断与恢复
   - 上下文压缩

3. DBAAS Tool 层
   - 服务查询工具
   - 监控查询工具
   - 写操作前 precheck 工具
   - 写操作工具
   - 异步任务查询与追踪工具

4. Integration 层
   - `mock-server` 客户端
   - 后续真实 DBAAS 控制面客户端

5. Persistence 层
   - Session 元数据
   - 原始消息
   - 审批记录
   - operation / task 记录
   - DeepAgent checkpoint

## 6. 核心原则

### 6.1 区分产品 Session 与运行时 Thread

`session_id` 是产品层主键，负责用户可见的会话历史和生命周期管理。

`thread_id` 是 DeepAgent 运行时主键，负责持续对话、中断恢复和 checkpoint。

当前建议保持：

- 一个 `session_id` 对应一个 `thread_id`
- 压缩发生在原 `thread_id` 内
- 删除 Session 时同步清理对应运行时数据
- 压缩不导致 Session 或 Thread 切换

### 6.2 API 契约保持集中

`API.md` 是当前接口真相源。

Phase 文档可以记录某阶段新增了哪些接口、为什么这样设计，
但前端联调、测试和后续开发查接口时，应优先看 [API.md](./API.md)。

### 6.3 SSE 协议由项目侧封装

前端应消费本项目定义的稳定 SSE 事件协议，
而不是直接依赖底层 DeepAgent / LangGraph 的原始事件格式。

底层 runtime 事件可以演进，但项目对外事件需要保持稳定、可测试、可兼容。

### 6.4 写操作必须受控

模型不应该直接调用任意 HTTP 接口。

DBAAS 写操作必须通过受控工具执行，并满足：

- 工具按风险分级
- 写工具强制人工确认
- 审批状态可持久化
- 执行结果可审计
- 异步任务可追踪

Phase8 引入轻量 precheck tool 后，额外保持：

- 对已提供 precheck 的资源或存储调整，执行前应先调用对应只读 precheck tool 获取当前规格、容量、运行状态和必要监控摘要
- Precheck tool 不调用写接口、不创建 approval、不替代 Phase7 写工具，也不替代 DBAAS 控制面的硬校验
- `blocking_errors` 非空时，模型应说明阻断原因，并且不建议继续执行
- 用户在了解 precheck 结果后仍明确要求执行时，才回到 Phase7 受控写工具和确认卡链路

### 6.5 压缩不是长期记忆

当前系统已经实现运行时上下文压缩，但没有实现独立长期记忆系统。

当前最准确的说法是：

- 有运行时上下文压缩
- 没有独立长期记忆
- 没有跨 Session 语义记忆
- 没有通用 facts store

运行时压缩解决的是“长会话下模型后续应该看到什么”，
不是“系统长期应该记住哪些稳定事实”。

如果后续引入事实层或记忆层，必须满足：

- 不能替代产品层原始消息
- 不能替代实时 DBAAS 查询
- 不能把运行时摘要直接当最终事实
- 必须可审计
- 必须按用户和 Session 清晰隔离

### 6.6 DBAAS HTTP 请求统一身份注入

所有真正访问 DBAAS 控制面的 HTTP 请求，都必须由后端统一注入当前 request/session identity。

前端和 AI tool 参数不得传入 `user_id`、`role` 或 `user` 来影响 DBAAS 调用身份。
模型可以描述业务目标和过滤条件，但不能决定 DBAAS 请求使用哪个身份。

产品侧 identity 转换为 DBAAS 身份时遵循：

- `identity.role == "admin"` 使用 `Authorization: Bearer admin`
- `identity.role == "user"` 使用 `Authorization: Bearer user`
- 有当前 request/session identity 的请求统一追加 `X-DBAAS-Actor-User: {identity.user_id}`
- 有当前 request/session identity 的请求统一追加 `X-DBAAS-Actor-Role: {identity.role}`
- 后台系统任务使用 `X-DBAAS-Actor-User: dbaas-ai-agent` 和 `X-DBAAS-Actor-Role: system`

资源归属、服务可见性、监控可见性、task 可见性和写操作权限，
最终都以 DBAAS 控制面的鉴权结果为准；项目侧只做必要的入口校验、工具边界控制和审计记录。

## 7. 当前建议

后续维护文档时建议保持这个分工：

- `DESIGN.md` 只写跨阶段总原则
- `API.md` 维护当前接口契约
- `FRONTEND.md` 维护前端需求
- `PHASE*.md` 维护阶段设计、阶段结论和实现边界

这样可以避免同一套设计在多个文档里重复展开，
也能降低后续实现变化时的文档冲突风险。
