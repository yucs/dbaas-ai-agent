# DBAAS 智能助手第二阶段计划

## 1. 文档目的

本文档用于收敛第二阶段的实现目标、范围、边界和推荐顺序。

第二阶段的核心主题是：

- 保留第一阶段已经跑通的多用户、多 Session 和页面结构
- 将当前 demo 版 AI runtime 升级为真实的 DeepAgent runtime
- 对接真实大模型
- 把 `thread_id` 升级为真实可恢复的 DeepAgent 线程

这里的“真实大模型”默认指私有部署的大模型服务，而不是只指公有云托管模型。

第二阶段默认应优先支持：

- 私有部署的 DeepSeek 类模型
- 私有部署的 Qwen 类模型

但在当前开发阶段，可以先支持公网 API 进行联调与验证。

也就是说，第二阶段应同时接受下面两类接入方式：

- 开发阶段：公网 API
- 目标形态：私有部署模型服务

具体模型名以实际部署环境中的模型标识为准，例如：

- `deepseek-3.4`
- `qwen-max`

相关文档：

- [DESIGN.md](./DESIGN.md)
- [SESSIONS.md](./SESSIONS.md)
- [API.md](./API.md)
- [MEMORY.md](./MEMORY.md)
- [PHASE1.md](./PHASE1.md)

## 2. 第二阶段目标

第二阶段的核心目标不是马上完成 DBAAS tool 和审批闭环，而是先把 AI runtime 从 demo 升级到真实可用状态，重点验证：

- 使用真实大模型进行普通问答
- 在同一个 Session 下基于真实 `thread_id` 持续对话
- 服务重启后仍可基于同一个 `thread_id` 继续执行
- 保留现有多用户、多 Session 页面模型
- 为后续 SSE、审批和 DBAAS tools 留出稳定边界

## 3. 第二阶段用户流程

第二阶段用户侧流程和第一阶段保持基本一致：

1. 用户进入登录页
2. 输入用户名并选择用户类型
3. 登录后进入对话页
4. 左侧查看当前用户的历史 Session 列表
5. 打开某个历史 Session
6. 在当前窗口继续提问
7. 后端基于该 Session 绑定的真实 `thread_id` 调用 DeepAgent
8. 模型返回真实回答，而不再使用 demo 拼接逻辑

当前阶段仍建议保留：

- `archive`
- `delete`
- 当前窗口继续问答

## 4. 第二阶段范围

### 4.1 需要完成的能力

- 接入真实 DeepAgent SDK
- 接入真实私有部署大模型
- 开发阶段可切换接入公网 API
- 为每个 Session 绑定真实 `thread_id`
- 接入持久化 checkpointer
- 让普通问题由真实模型直接回答
- 让会话在服务重启后仍能继续
- 保留当前 Session 文件投影结构

### 4.2 暂不要求一次完成的能力

- 真正调用 `mock-server` 的 DBAAS 查询 tools
- 真正调用 `mock-server` 的 DBAAS 写操作 tools
- 强制人工确认完整闭环
- 完整 SSE 事件流
- 子 agent 拆分
- 长期记忆

## 5. 第二阶段与 DeepAgent 的关系

第二阶段开始，项目中的 agent 层不再只是“保留 DeepAgent 接入位置”，而是要真正使用 DeepAgent 作为运行时内核。

根据官方文档，DeepAgent 原生提供：

- `create_deep_agent(...)` 创建 agent runtime
- 模型 provider 配置
- LangGraph durable execution
- streaming
- human-in-the-loop
- subagent
- memory / skills / backend

参考：

- https://docs.langchain.com/oss/python/deepagents/overview
- https://docs.langchain.com/oss/python/deepagents/quickstart
- https://docs.langchain.com/oss/python/deepagents/customization
- https://docs.langchain.com/oss/python/deepagents/models

因此，第二阶段建议把当前 [agent/runtime.py](./backend/src/dbass_ai_agent/agent/runtime.py) 从 `DemoAgentRuntime` 升级为真实 `DeepAgentRuntime`。

## 6. 为什么有了 checkpointer 仍然保留 Session 层

第二阶段需要明确一个很重要的边界：

- DeepAgent checkpointer
- Session 产品模型

不是替代关系，而是分层关系。

### 6.1 Checkpointer 负责什么

DeepAgent / LangGraph checkpointer 负责运行时状态：

- 线程级持久化
- 中断恢复
- 审批恢复
- 长任务恢复
- 基于同一个 `thread_id` 的持续对话

参考：

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/integrations/checkpointers

### 6.2 Session 负责什么

Session 继续负责产品层能力：

- 左侧历史会话列表
- 当前用户有哪些会话
- 标题和预览
- 归档和删除
- 页面切换当前会话
- 本地消息投影

### 6.3 为什么仍然保留

保留 Session 层，不只是为了支持页面会话管理，也是为了把产品层和 AI runtime 解耦。

可以把这层关系理解成：

- `session_id`
  - 产品层主键
- `thread_id`
  - AI runtime 主键

因此第二阶段仍建议保持：

- 一个 `session_id`
- 对应一个 `thread_id`

这样即使后续从 DeepAgent 演进到其他 AI runtime，产品层 Session 模型也可以继续保留。

### 6.4 Session 存储后端选择

第二阶段建议把 Session 存储抽象成可配置后端，而不是写死为某一种实现。

建议支持两种后端：

- `file`
  - 兼容第一阶段的本地目录结构
- `sqlite`
  - 适合和 SQLite checkpointer 一起使用

后续支持 `session sqlite`，主要不是为了让 Session 结构和 checkpointer 结构完全一致，
而是为了在进入真实 DeepAgent 阶段后，把产品层主存储也收敛到数据库里，降低双存储带来的复杂性。

这里需要明确两点：

- Session schema 和 checkpointer schema 不要求完全一致
- 但它们适合放在同一个 SQLite 数据库中，通过 `thread_id`、`session_id` 等键关联

支持 `session sqlite` 的主要原因包括：

- 当运行时已经引入 SQLite checkpointer 后，继续把 Session 主存储放在文件中，会形成“运行时一份、产品层一份”的双存储结构
- 双存储不是不能做，但更容易出现投影延迟、删除不一致、恢复点不一致等问题
- 把 Session 主存储也迁到 SQLite 后，可以让产品层数据和运行时数据处于同一种持久化介质中
- 后续接 SSE、审批、运行记录、会话摘要时，也更适合按表扩展，而不是继续扩展本地文件结构

因此，第二阶段支持 `session sqlite` 的真正目的，是：

- 保持 Session 作为产品层模型不变
- 同时让它的持久化方式更适合真实 DeepAgent 运行时
- 而不是去追求和 checkpointer 完全共用同一套结构

但这里需要明确一个原则：

- 运行时只选择一个 Session 主存储后端
- 不做 `file + sqlite` 双写

原因是：

- 双写会引入一致性问题
- 不利于排查到底哪份数据是主真相
- 第二阶段重点是先把真实 DeepAgent 和真实 `thread_id` 跑稳

因此更推荐的模式是：

- Phase 1：默认 `file`
- Phase 2A：先保留 `file`
- Phase 2B：在真实模型链路稳定后，再切换到 `sqlite`
- 如需兼容历史数据，通过迁移或只读导入完成

不建议把同一个 Session 在两个后端里同时维护为主真相。

## 7. 第二阶段技术选型建议

### 7.1 模型建议

建议优先使用支持 tool calling 的模型。

第二阶段默认推荐优先接入私有部署模型，而不是把公有云 provider 写死在系统中。

建议默认支持下面两类模型：

- 私有部署的 DeepSeek 类模型
- 私有部署的 Qwen 类模型

具体模型名以部署侧实际提供的标识为准，例如：

- `deepseek-3.4`
- `qwen-max`

如果私有部署服务暴露的是 OpenAI-compatible 接口，则第二阶段实现应优先按这一协议做统一适配。

如果开发阶段临时使用公网 API，也建议尽量采用同样的 OpenAI-compatible 配置方式，
这样后续切回私有部署时不需要重写 runtime 结构。

运行时至少应支持以下配置：

- `model`
- `base_url`
- `api_key` 或等价 token
- `context_window`
- `max_output_tokens`

参考：

- https://docs.langchain.com/oss/python/deepagents/models

### 7.2 Checkpointer 建议

本地开发阶段建议使用 SQLite checkpointer，而不是内存 checkpointer。

原因：

- 服务重启后仍能恢复 `thread_id`
- 更接近后续真实部署场景
- 比纯内存更适合验证 Session 恢复逻辑

参考：

- https://docs.langchain.com/oss/python/integrations/checkpointers
- https://docs.langchain.com/oss/python/langgraph/persistence

### 7.3 默认运行模式

第二阶段建议默认采用：

- DeepAgent
- 非流式 invoke
- SQLite checkpointer
- 私有部署真实大模型
- `P2A` 阶段继续使用 `file` 作为 Session 主存储
- `P2B` 阶段再引入 `sqlite` 作为 Session 主存储

不建议一开始就同时上：

- streaming
- HITL
- DBAAS tools

因为这样会把排错边界混在一起。

## 8. 第二阶段代码改动重点

第二阶段优先改动以下模块：

- `backend/src/dbass_ai_agent/agent/runtime.py`
- `backend/src/dbass_ai_agent/config.py`
- `backend/src/dbass_ai_agent/api/deps.py`
- `backend/src/dbass_ai_agent/api/routes_chat.py`

必要时新增：

- `backend/src/dbass_ai_agent/agent/factory.py`
- `backend/src/dbass_ai_agent/agent/checkpointer.py`

### 8.1 `agent/runtime.py`

从：

- `DemoAgentRuntime`

升级为：

- `DeepAgentRuntime`

职责建议：

- 创建真实 deep agent
- 调用 `invoke(...)`
- 使用 `thread_id`
- 将模型输出整理成当前接口所需结构

### 8.2 `config.py`

新增真实运行所需配置，例如：

- `DBASS_AGENT_MODEL`
- `DBASS_AGENT_MODE`
- `DBASS_AGENT_CHECKPOINT_DB`
- `OPENAI_API_KEY`

### 8.3 `api/deps.py`

建议在这里统一构造：

- `SessionService`
- `DeepAgentRuntime`
- checkpointer

### 8.4 `routes_chat.py`

需要把当前“先落消息，再走 demo 拼接”的逻辑升级为：

1. 记录用户消息
2. 读取 Session 对应的真实 `thread_id`
3. 调用 DeepAgent
4. 记录模型返回
5. 更新当前 Session 投影

### 8.5 `sessions/` 存储层

这一部分建议放到真实模型适配稳定之后再做，不作为 `P2A` 的阻塞项。

建议形态为：

- `SessionStore`
  - 统一接口
- `FileSessionStore`
  - 第一阶段兼容实现
- `SQLiteSessionStore`
  - 第二阶段后续实现

上层 `SessionService` 不直接依赖文件结构或 SQLite 表结构，而是依赖统一的 Session 存储接口。

这样可以做到：

- 配置切换 `file` / `sqlite`
- 不影响页面与 API 层
- 不需要双写

## 9. 第二阶段对 mock-server 的处理策略

第二阶段的重点仍然不是 DBAAS tool 接入本身。

建议策略如下：

- 普通问题：直接走真实模型
- DBAAS 概念类问题：也可以由真实模型回答
- DBAAS 实时查询 / 操作类问题：继续明确提示当前阶段尚未启用

这样可以让第二阶段先验证：

- 真实模型能力
- 真实 thread/checkpointer 能力

而不把 `mock-server` 集成复杂度提前混进来。

## 10. 第二阶段推荐实现顺序

建议拆成四个连续小阶段：

### 10.1 P2A：真实模型 + 非流式

目标：

- 替换 demo runtime
- 接入真实 DeepAgent
- 用真实模型回答普通问题
- 用真实 checkpointer 绑定 `thread_id`
- 暂时保留第一阶段的 `file` Session 存储

这是第二阶段最关键的一步。

### 10.2 P2B：Session 存储抽象与 SQLite 切换

目标：

- 在真实模型链路稳定后，引入 `SessionStore` 抽象
- 支持 `file` / `sqlite` 两种后端
- 运行时只选择一个主存储，不做双写
- 默认切换到 `sqlite` 作为 Session 主存储

### 10.3 P2C：SSE 流式返回

目标：

- 基于 DeepAgent streaming 对接现有 SSE 接口
- 继续保持项目侧事件封装

这一阶段只做流式，不引入 DBAAS tools。

### 10.4 P2D：DBAAS tools 与审批

目标：

- 接入只读查询 tools
- 再接写操作 tools
- 写操作启用 `interrupt_on`

这一阶段才进入真正的 DBAAS agent 能力。

## 11. 第二阶段非目标

以下内容不建议在第二阶段一开始同时推进：

- 完整长期记忆系统
- 多子 agent 编排
- 复杂前端工作流可视化
- 完整审批中心
- 真实部署级鉴权系统

这些内容可以等真实 DeepAgent runtime 跑稳以后再逐步接入。

## 12. 第二阶段完成标准

如果第二阶段完成，至少应满足：

- 启动后使用真实大模型回答普通问题
- 同一个 Session 在多轮中能持续对话
- 服务重启后，打开历史 Session 仍能继续问答
- 页面和 Session 管理逻辑不需要因为接入 DeepAgent 而重写
- DBAAS 查询 / 操作问题仍能被清楚地区分并提示当前边界
