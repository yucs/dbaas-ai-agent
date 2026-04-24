# DBAAS 智能助手设计说明

## 1. 项目背景

本项目目标是基于 Deep Agents 构建一个 DBAAS 智能助手。
该助手需要能够通过自然语言帮助用户查询和操作 DBAAS 服务与平台资源，
同时保证执行过程安全、可审计，并且能够方便地对接现有管理面接口。

本项目以 DeepAgent 作为 Agent 运行时内核。
因此，后续涉及的 Session、`thread_id`、审批中断与恢复、SSE 流式返回等设计，
都默认围绕 DeepAgent 的执行模型展开。

模型接入侧需要明确一个前提：

- 整个项目默认面向私有部署的大模型
- 不以公有云 API 作为唯一前提
- 当前优先考虑接入私有部署的 DeepSeek、Qwen 等模型
- 具体模型名以实际部署平台上的模型标识为准，例如 `deepseek-v4-flash`、`deepseek-v4-pro`

同时需要补充一个现实约定：

- 在当前开发阶段，为了降低联调门槛，可以先支持公网 API
- 但这只作为开发和验证手段
- 不改变项目整体面向私有部署模型的长期目标
- 因此模型接入层应统一抽象为：
  - `model`
  - `base_url`
  - `api_key` 或等价鉴权方式
  - 由部署环境决定其指向公网服务还是私有部署服务

第一阶段的后端对接目标是本仓库中的本地 `mock-server`。
后续同一套架构应能够平滑切换到真实的 DBAAS 控制面接口。

## 1.1 模型推理模式兼容约束

当前模型接入层已经支持通过 OpenAI-compatible 方式接入 DeepSeek 官方 API，
例如 `deepseek-v4-flash`、`deepseek-v4-pro`。

但需要明确一个运行时边界：

- 当前项目优先保证普通对话与现有 DeepAgent 主链路稳定
- `deepseek-v4-pro` 在 `thinking = false` 时，可视为当前兼容路径
- `deepseek-v4-pro` 在 `thinking = true` 且发生 tool calling 时，暂不作为当前阶段目标

原因是 DeepSeek 的 thinking mode 在 tool calling 场景下，
后续请求需要继续携带本轮生成的 `reasoning_content`。
而当前项目仍沿用：

- `DeepAgent`
- `LangChain`
- `langchain_openai.ChatOpenAI`

这条通用 OpenAI-compatible 链路。

在当前实现里，这条链路还没有把 `reasoning_content` 的提取、
持久化与后续轮次回传作为项目级兼容能力来收敛。

因此当前设计约定为：

- 先支持非 thinking 模式下的稳定运行
- 将 `v4-pro thinking + tool calling` 兼容性改造明确延后
- 等后续真正进入该改造时，再决定是引入 DeepSeek 专用模型适配层，
  还是在现有 agent/runtime 消息链路中补齐 `reasoning_content` 往返支持

## 1.2 文档分工

当前设计文档按下面的层次拆分：

- [DESIGN.md](./DESIGN.md)
  - 说明项目目标、能力边界和总体架构
- [SESSIONS.md](./SESSIONS.md)
  - 说明多用户、多 Session 的存储与加载方式
- [API.md](./API.md)
  - 说明页面与后端之间的接口契约
- [CONTEXT_ARCHITECTURE.md](./CONTEXT_ARCHITECTURE.md)
  - 说明当前已经生效的上下文压缩实现
- [MEMORY.md](./MEMORY.md)
  - 说明当前记忆边界，以及未来如需引入事实层时的约束
- [FRONTEND.md](./FRONTEND.md)
  - 说明登录页、会话页和当前阶段前端交互边界
- [PHASE3.md](./PHASE3.md)
  - 说明当前这一轮压缩接入的阶段状态与验收边界
- [PHASE4.md](./PHASE4.md)
  - 说明 FastAPI + SSE 流式对话和压缩提醒的阶段状态

这些文档分别回答不同问题，避免把总设计、会话结构、接口细节、前端交互、上下文压缩和记忆边界混在一起。

## 1.3 能力边界

为了避免后续讨论混淆，需要先区分“DeepAgent 原生支持的能力”和“本项目需要自己开发的能力”。

DeepAgent 原生支持：

- Agent 运行时与 tool calling loop
- 基于 LangGraph 的 durable execution
- `thread_id` 驱动的执行上下文延续
- streaming 能力
- human-in-the-loop 中断与恢复机制
- subagent 能力
- memory 与 context engineering 的基础机制

本项目需要自己开发：

- DBAAS 领域 prompt、工具和策略
- 对接 `mock-server` 与后续真实控制面的客户端封装
- 多用户、多 Session 的产品层模型
- Session 与 `thread_id` 的绑定规则
- 历史 Session 列表、归档、删除等页面能力
- 审批记录的持久化和页面交互
- 对外 HTTP API 与 SSE 协议
- 本地文件存储布局和后续数据迁移方案

## 1.4 第一阶段身份与后端认证约定

第一阶段需要区分“本地产品用户”与“调用 `mock-server` 时的后端身份”。

建议采用下面的模型：

- `user_id`
  - 产品层用户标识
- `backend_role`
  - 调用 `mock-server` 时使用的后端角色
  - 可取值：`admin`、`user`
- `user`
  - 仅当 `backend_role = user` 时使用
  - 表示当前普通用户在 `mock-server` 中对应的用户标识

在当前 `mock-server` 中：

- 管理员使用 `Authorization: Bearer admin`
- 普通用户使用 `Authorization: Bearer user:<user>`

因此第一阶段建议约定为：

- 本地 Session 目录始终按产品层 `user_id` 组织
- 如果当前用户是普通用户，则可简化为 `user = user_id`
- 如果当前用户是管理员，则后端调用使用 `admin`，而不是把 `user_id` 当作 `user`

也就是说：

- `user_id = user`
  - 只适用于第一阶段的普通用户简化模型
- 管理员场景下
  - `user_id` 与 `user` 不必相同
  - 后端 principal 直接是 `admin`

后续如果接入统一身份体系，可以稳定演进为：

- `user_id`
  - 登录用户标识
- `backend_role`
  - 调用控制面时的后端角色
- `user`
  - 仅对普通用户有效，对应 `mock-server` 的用户标识

## 2. 产品目标

该助手需要支持两类主要模式：

1. DBAAS 领域助手
   - 查询 DBAAS 服务及相关资源
   - 执行 DBAAS 操作，包括同步操作和异步操作
   - 后续支持更复杂的多步骤任务流程

2. 通用问答兜底
   - 如果用户问的是非 DBAAS 领域问题，例如“你是谁”或者“什么是 MySQL”，
     则由大模型直接回答，不调用 DBAAS 工具

## 3. 当前需求范围

### 3.1 领域能力

- 服务查询
- 平台资源查询
- DBAAS 操作执行
- 异步任务跟踪
- 后续复杂工作流执行

### 3.2 当前集成目标

当前 `mock-server` 已提供的首批资源包括：

- `services`
- `hosts`
- `clusters`
- `sites`
- `tasks`

当前已经具备的写操作包括：

- 服务资源规格更新
- 服务存储规格更新
- 服务镜像升级

## 4. 核心需求

### 4.1 Agent 运行时

- 基于 Deep Agents 构建
- 已支持流式响应
- 支持未来扩展子 Agent 或复杂工作流
- 支持通过工具调用执行 DBAAS 操作
- 支持对接私有部署的大模型服务

### 4.2 用户交互

- 已支持 FastAPI + SSE 流式输出
- 支持非 DBAAS 问题由大模型直接回答
- 支持同步结果和异步结果两类返回

### 4.6 模型接入要求

- 优先支持私有部署模型，而不是只支持公有云模型
- 至少支持私有部署的 DeepSeek / Qwen 类模型
- 开发阶段允许先接公网 API 做联调
- 生产和长期目标仍应优先面向私有部署模型
- 模型接入应支持配置：
  - `model`
  - `base_url`
  - `api_key` 或等价鉴权方式
  - `context_window`
  - `max_output_tokens`
- 模型标识不应写死为某一家公有云 provider 的固定命名
- 需要兼容以 OpenAI-compatible 方式暴露的私有部署模型服务

### 4.3 安全与控制

- 对所有变更类操作强制人工确认
- 执行前必须有明确的审批状态
- 对关键操作保留可审计的操作轨迹

### 4.4 多用户与多 Session 管理

- 支持多用户
- 每个用户支持多个 Session
- 每个 Session 都应可恢复
- 支持历史会话加载与查看

### 4.5 上下文压缩与记忆

当前阶段在“上下文压缩”和“记忆”之间，优先实现的是上下文压缩。

也就是说：

- 先解决长会话下模型本次调用应该看到什么
- 优先把压缩留在 `SummarizationMiddleware` 与原 `thread_id` 内部
- 优先通过 `SummarizationMiddleware` 在原 `thread_id` 内压缩上下文
- 不在当前阶段同时落完整 `session_events` 和复杂 memory 系统

当前优先方案进一步明确为：

- 优先使用项目自定义包装的 `SummarizationMiddleware` 在原 `thread_id` 内做上下文压缩
- 不因上下文压缩而切换新的 `thread_id`
- 仅在用户显式删除 Session 时，才删除对应 `thread_id` 的运行时数据

这里所说的“自定义压缩”，当前已经有明确含义：

- 继续使用 `create_deep_agent()`
- 接管 DeepAgents 内部的 summarization factory
- 使用项目自己的压缩提示词
- 使用项目自己的压缩阈值和保留窗口
- 在自定义包装层里追加日志等钩子动作
- 后续可在同一钩子位置扩展 SSE / 前端提醒等非持久化动作

本项目在系统能力上仍然需要：

- 自定义压缩
- 自定义记忆

但阶段上应按下面顺序推进：

1. 当前阶段先实现上下文压缩
2. 后续 Tool、审批、异步任务进入主链路后，再推进完整记忆实现

其中：

- 上下文压缩设计统一以 [CONTEXT_ARCHITECTURE.md](./CONTEXT_ARCHITECTURE.md) 为准
- 记忆边界和未来事实层约束统一以 [MEMORY.md](./MEMORY.md) 为准

无论是压缩还是记忆，都必须遵守同一条原则：

- DBAAS 实时状态必须始终从后端接口读取

## 5. 设计原则

### 5.1 区分产品 Session 与 Agent 执行状态

产品层需要区分两个相关但不同的概念：

- `session_id`：面向用户的会话 ID
- `thread_id`：Deep Agent 的执行线程 ID

同时还需要区分第三类对象：

- 运行时压缩状态：由 `SummarizationMiddleware` 在同一个 `thread_id` 内维护

三者职责应分别收敛为：

- `Session`
  - 负责页面展示、审计、记录与产品层管理
- `thread_id`
  - 负责 DeepAgent 运行时状态、续跑与中断恢复
- 运行时压缩状态
  - 负责长会话下的内部上下文收缩
  - 不单独投影成产品层文件

建议第一阶段采用：

- 一个 `session` 对应一个 `thread`

这样可以让会话恢复、审批中断与历史管理更简单。

当前实现中，`session_id` 与 `thread_id` 的命名规则也保持一致，便于排查和人工定位：

- `session_id` 形如：
  - `sess_<user_id片段>_<14位时间戳>_<6位随机数>`
- `thread_id` 形如：
  - `thread_<user_id片段>_<14位时间戳>_<6位随机数>`

其中：

- 两者共享完全相同的后缀
- 只通过前缀区分产品层 Session 和运行时 Thread
- 这样在日志、文件和调试过程中，可以一眼看出某个 `session_id` 对应的 `thread_id`

示例：

- `sess_admin1_20260423022500_827225`
- `thread_admin1_20260423022500_827225`

同时，删除 Session 时也应同步删除该 Session 绑定的 `thread_id` 运行时数据。

也就是说，`delete session` 不只是删除产品层文件：

- 删除 `index.json` 中的记录
- 删除 Session 目录
- 删除 SQLite checkpointer 中该 `thread_id` 对应的 DeepAgent 持久化数据

这样可以避免出现“页面中的 Session 已删除，但 DeepAgent 运行时线程仍然残留”的孤儿数据。

但这里需要明确区分两种场景：

- `delete session`
  - 用户显式删除整个 Session
  - 可以同步清理该 Session 绑定的运行时线程数据
- `context compression`
  - 优先在原 `thread_id` 内通过 `SummarizationMiddleware` 完成
  - 不应因此切换或删除旧 `thread_id`

### 5.2 记忆以操作为中心，而不是以知识为中心

该助手需要记住“这个会话里发生了什么”，而不是引入一个泛化的长期记忆系统。

应保留：

- 查过什么
- 提议过什么操作
- 哪些操作被批准或拒绝
- 启动过哪些任务
- 哪些任务最终成功或失败

不应保留：

- 已经过期的服务状态作为 memory
- 主机或集群实时状态作为 memory
- 第一阶段的长期用户偏好记忆

### 5.3 Tool 调用必须受控

模型不应该直接调用任意 HTTP 接口。

应采用受控工具体系：

- 定义经过批准的 DBAAS 工具
- 对工具按风险分级
- 对写工具强制审批
- 对工具输入输出进行审计

### 5.4 SSE 协议应保持稳定

前端应消费本项目定义的稳定 SSE 事件协议，
而不是直接依赖底层运行时的原始事件格式。

当前约定：

- 普通 JSON 消息接口继续保留
- 前端主路径使用项目侧封装后的 SSE 接口
- 底层 DeepAgent streaming 事件不直接暴露给页面
- 压缩提醒通过项目侧 `compression_started` / `compression_completed` 事件表达
- 这一调整只影响交互体验，不改变 Session、`thread_id` 和消息落盘模型

## 6. 高层架构

### 6.1 核心分层

1. API 层
   - Session 接口
   - Chat 接口
   - Approval 接口
   - 后续扩展 SSE 流接口

2. Agent Runtime 层
   - Deep Agent graph 组装
   - Prompt 与路由逻辑
   - Tool 调用
   - 审批中断与恢复
   - 会话摘要生成

3. Tool 集成层
   - DBAAS 读工具
   - DBAAS 写工具
   - 异步任务跟踪工具
   - `mock-server` 后端客户端

4. Persistence 层
   - Session 元数据
   - 操作事件
   - 审批记录
   - 会话摘要
   - Agent checkpoint 集成

## 7. 请求处理模型

### 7.1 非 DBAAS 问题

如果请求明确不属于 DBAAS 领域，则由模型直接回答，
不调用任何 DBAAS 工具。

### 7.2 DBAAS 只读请求

对于只读类 DBAAS 问题：

- 识别相关领域资源
- 必要时调用对应读工具
- 当前阶段可以先同步返回结果
- 后续再扩展为流式回传进度与最终答案

只读工具不需要人工审批。

### 7.3 DBAAS 写请求

对于变更类 DBAAS 操作：

- 先生成目标工具调用
- 在真正执行前暂停
- 创建审批请求
- 等待明确的人为确认
- 审批通过后恢复同一个 Session/Thread

### 7.4 异步操作

部分操作不会直接返回最终业务结果，而是先返回任务引用。

此时助手应当：

- 告知用户已创建异步任务
- 将任务 ID 记录到当前 Session 的操作历史中
- 支持后续任务状态查询
- 后续再扩展更复杂的异步工作流编排

## 8. Session、记忆与压缩模型

本章节给出总原则。
当前真实压缩实现统一以 [CONTEXT_ARCHITECTURE.md](./CONTEXT_ARCHITECTURE.md) 为准，
未来如果需要独立事实层，再参考 [MEMORY.md](./MEMORY.md)。

### 8.1 最小持久化对象

当前实现与后续方向可分为两层：

- `sessions`
- `approval_requests`
- 后续可选的 `session_events`

### 8.2 Session 事件

Session 记忆建议采用事件化方式保存。

候选事件类型包括：

- `user_request`
- `agent_response`
- `resource_observation`
- `tool_call_proposed`
- `approval_required`
- `approval_decision`
- `tool_call_executed`
- `task_created`
- `task_status_changed`
- `session_context_compacted`

### 8.3 压缩策略

压缩应针对“对话冗余”，而不是“操作事实”。

建议策略：

- 保留最近一段原始消息
- 将更早的对话压缩到原 `thread_id` 内部的运行时摘要
- 绝不丢失重要操作记录和审批记录

### 8.4 摘要结构

Session 摘要应聚焦执行上下文，建议包含：

- 当前目标
- 已观察到的资源
- 已完成的动作
- 已批准的动作
- 已拒绝的动作
- 待处理动作或运行中任务
- 当前约束条件

## 9. 审批模型

所有写操作都必须经过人工审批。

第一阶段的写操作包括：

- 更新服务资源规格
- 更新服务存储规格
- 创建服务镜像升级任务

预期审批流程：

1. 用户发起 DBAAS 变更请求
2. Agent 准备写操作
3. 运行时在执行前暂停
4. 系统创建审批记录
5. 客户端通过 SSE 收到审批事件
6. 人工进行批准或拒绝
7. 运行时恢复同一个 Session/Thread

## 10. SSE 事件类型

当前第四阶段已经落地的对话流事件包括：

- `user_message`
- `started`
- `token`
- `compression_started`
- `compression_completed`
- `done`
- `error`

后续接入 DBAAS tools、审批和异步任务后，可以在同一 SSE 协议下继续扩展：

- `tool.called`
- `tool.completed`
- `approval.required`
- `approval.resolved`
- `run.paused`
- `run.resumed`
- `run.failed`

## 11. 初始目录方向

第一版实现可以按如下结构演进：

```text
ai-agent/
  DESIGN.md
  src/dbass_ai_agent/
    api/
    agent/
    tools/
    integrations/
    sessions/
    persistence/
```

建议职责划分如下：

- `api/`：HTTP 与 SSE 接口
- `agent/`：Deep Agent runtime 组装与控制逻辑
- `tools/`：DBAAS 工具定义与调用策略
- `integrations/`：后端客户端，例如 `mock-server`
- `sessions/`：会话历史、事件投影与摘要管理
- `persistence/`：数据库与 checkpoint 集成

## 12. 第一阶段非目标

以下内容在第一阶段暂不纳入范围：

- 长期用户偏好记忆
- 任意外部工具执行能力
- 复杂授权策略引擎
- 面向高级 DBAAS 过程的完整工作流引擎
- 生产级前端设计

## 13. 待确认问题

在正式开始实现前，仍需继续讨论以下问题：

1. API 层采用什么后端框架承载？
2. Session 与操作事件使用什么数据库存储？
3. 审批人身份与审批角色如何建模？
4. 异步任务跟踪仅支持查询式轮询，还是也支持后台轮询加主动推送？
5. 大模型直答与 DBAAS 工具调用的边界如何定义？
6. 哪些 Tool 配置适合保留在 YAML 中，哪些逻辑应固定在 Python 中？

## 14. 下一步

下一轮讨论可以把这份设计说明继续收敛为：

- API 列表
- 存储模型
- 具体目录树
- 第一版可运行骨架
- 独立的记忆与压缩策略细化
