# DBAAS 智能助手第三阶段当前状态与压缩实现

## 0. 当前状态

- 状态：已完成
- 当前代码状态：项目自定义 `SummarizationMiddleware` 已接入真实 DeepAgent 主链路，压缩提示词和阈值由配置控制
- 本文档作用：说明运行时上下文压缩的当前实现和记忆边界
- 仍有效内容：不维护 `summary.json`，不新增项目侧 summary store，不把压缩摘要当产品层真相，不切换 `thread_id`
- 后续关注：如后续引入事实层或长期记忆，需要单独设计可审计、按用户和 Session 隔离的存储

## 1. 当前阶段结论

第三阶段当前已经收敛成一件事：

- 在不引入项目侧 summary store 和记忆系统的前提下，
  把 `SummarizationMiddleware` 按项目自己的配置接入真实 DeepAgent 主链路

也就是说，第三阶段现在不是“做一套新的摘要持久化方案”，
而是把运行时压缩真正跑起来。

第四阶段已经在此基础上继续补充：

- FastAPI + SSE 流式对话
- 压缩发生时的前端提醒事件

第四阶段内容见 [PHASE4.md](./PHASE4.md)。

## 2. 本阶段已经实现的内容

- 真实 `DeepAgent` runtime
- 基于 SQLite checkpoint 的 `thread_id` 持续对话
- 项目自定义 `SummarizationMiddleware` 包装层
- 项目自定义压缩提示词
- 项目自定义压缩阈值与保留窗口
- 压缩专用 summary model 输出长度控制
- 压缩发生时的后端日志
- 产品层原始消息继续保留

当前结论是：

- 页面历史与 Session 文件仍保存原始消息
- DeepAgent 继续在原 `thread_id` 上运行
- 长会话压缩由项目自定义包装的 `SummarizationMiddleware` 完成
- 压缩提示词和阈值由项目自己的 `config.toml` 控制
- 不再维护 `summary.json`
- 不向前端发送摘要正文

对应代码主要在：

- [backend/src/dbass_ai_agent/agent/factory.py](./backend/src/dbass_ai_agent/agent/factory.py)
- [backend/src/dbass_ai_agent/agent/runtime.py](./backend/src/dbass_ai_agent/agent/runtime.py)
- [backend/src/dbass_ai_agent/config.py](./backend/src/dbass_ai_agent/config.py)

## 3. 当前实现链路

当前消息链路是：

```text
User
  -> routes_chat.py
  -> SessionService.append_user_message(...)
  -> DeepAgentRuntime.stream_reply(...)
  -> agent.stream(..., stream_mode="messages", config={"configurable": {"thread_id": thread_id}})
  -> SummarizationMiddleware（必要时压缩）
  -> SSE compression_started/compression_completed/token/done events
  -> SessionService.append_assistant_message(...)
  -> messages.jsonl
```

当前没有放弃 `create_deep_agent()`，也没有在业务层重新拼一套 agent graph。
后端仍然通过 `deepagents.create_deep_agent(...)` 保留 DeepAgents 默认主链路能力。

但 DeepAgents 默认会额外注入 `write_todos`、文件工具、`execute` 和
`task` 子代理等通用工具。DBAAS 助手不开放这些通用工具给模型：

- `factory.py` 在创建 agent 时追加 DBAAS 工具 allowlist middleware
- 每次模型请求前只保留 `build_dbaas_tools(...)` 显式注册的工具
- DeepAgents 内置的 todo、文件读写、shell 执行、同步/异步子代理工具都会被过滤
- 这样仍保留 DeepAgent 的运行时、checkpoint、streaming、tool calling loop 和压缩能力，
  但避免模型绕过 DBAAS 受控工具直接读写本地文件或启动子代理

DeepAgents 自己会在 `create_deep_agent()` 内部创建 `SummarizationMiddleware`。
如果项目侧再额外挂一个同类 middleware，会触发 duplicate middleware 报错。
因此当前实现采用的是：

1. 创建主对话模型
2. 创建专门给压缩用的 summary model
3. 基于项目配置生成自定义 `SummarizationMiddleware` 包装层
4. 临时 patch DeepAgents 内部的 `create_summarization_middleware`
5. 在 patch 生效窗口内调用 `create_deep_agent()`

对应代码在 [backend/src/dbass_ai_agent/agent/factory.py](./backend/src/dbass_ai_agent/agent/factory.py)：

- `build_runtime_artifacts()`
- `build_summarization_middleware_factory()`
- `patch_deepagents_summarization_factory()`

这样做的收益是：

- 不会重复挂载 summarization middleware
- 继续使用 DeepAgents 主体能力
- 把压缩提示词和阈值切到项目自己的配置
- 在同一个包装层追加项目侧 side effects

## 4. 本阶段明确不做

- `summary.json`
- `SessionSummary`
- 项目侧 `summary_store`
- `on_summary` 回调落盘
- 前端压缩事件提示
- `session_events`
- 独立记忆系统
- 跨 Session 语义层
- `MemoryMiddleware`
- facts store
- 基于压缩触发的新 `thread_id`

## 5. 当前压缩配置

本阶段真正生效的压缩配置是：

- `compression_enabled`
- `soft_trigger_tokens`
- `keep_recent_messages`
- `summary_max_tokens`
- `compression_prompt_path`

这些配置都已经进入运行时，
不再只是文档上的预留项。

配置映射关系是：

- `compression_enabled`
  - 是否接管并启用项目侧压缩配置
- `soft_trigger_tokens`
  - 转换为 `trigger=("tokens", ...)`
- `keep_recent_messages`
  - 转换为 `keep=("messages", ...)`
- `summary_max_tokens`
  - 作为 summary model 的 `max_completion_tokens`
- `compression_prompt_path`
  - 作为 `summary_prompt`

配置读取见 [backend/src/dbass_ai_agent/config.py](./backend/src/dbass_ai_agent/config.py)，
实际组装见 [backend/src/dbass_ai_agent/agent/factory.py](./backend/src/dbass_ai_agent/agent/factory.py)。

## 6. 压缩提示词

当前压缩提示词来自：

- [backend/prompts/compression.md](./backend/prompts/compression.md)

它要求压缩模型输出结构化 JSON，重点保留：

- 当前目标
- 已确认事实
- 观察到的资源
- 已完成动作
- 已批准或已拒绝动作
- 待处理事项
- 约束条件

需要注意：

- 这份 JSON 是运行时摘要内容
- 它不会再额外持久化为 Session 文件
- 它不是产品层公开的数据结构契约

## 7. 当前用户侧感知

对用户来说，本阶段的效果是：

- 长会话还能继续问答
- 页面历史消息不会因为压缩而消失
- Session 不会因为压缩切换新的 `thread_id`
- 后端会在压缩发生时输出日志

压缩发生后：

- `messages.jsonl` 不会因为压缩而删改已有消息
- SSE 流式接口在 `compression_completed` 时可以追加一条去重后的 `role=system` 提醒
- 页面历史消息不会消失
- `meta.json` 不会新增摘要字段
- `session_id` 不会变化
- `thread_id` 不会切换

所以压缩影响的是模型上下文，不是产品层记录。

## 8. 钩子扩展方向

当前这一版实现已经证明：

- 可以在自定义 `SummarizationMiddleware` 包装层里追加项目侧动作
- 不需要重新引入 `summary.json`
- 也不需要放弃 `create_deep_agent()`

当前已经落地的钩子动作是：

- 压缩日志
- 请求级压缩通知发布

后续如果产品需要，也可以沿同一位置扩展：

- SSE 压缩提醒
- 前端提示事件
- metrics / tracing
- 其他非持久化观测动作

当前建议继续遵守两个约束：

- 不要回到全局 `SESSION_META` 这类共享状态
- SSE / 前端提醒必须基于当前请求和当前 `thread_id` 的上下文做隔离

## 9. 压缩与记忆边界

当前项目还没有实现独立的长期记忆系统。

当前真正存在的状态只有两类：

- 产品层记录
  - `meta.json`
  - `messages.jsonl`
  - `approvals.jsonl`
- 运行时状态
  - `thread_id`
  - SQLite checkpoint
  - `SummarizationMiddleware` 在 thread 内部维护的压缩上下文

因此当前最准确的说法是：

- 有运行时上下文压缩
- 没有独立长期记忆
- 没有 facts store
- 没有 `session_events`
- 没有跨 Session 语义记忆

下面这些虽然和上下文延续有关，但当前不应被当成独立记忆系统：

- `thread_id`
- checkpoint
- `SummarizationMiddleware` 生成的运行时摘要
- 页面上的历史消息回放

它们解决的是“这次调用还能不能接着跑”，不是“系统长期应该记住哪些稳定事实”。

当前系统的产品层真相仍然是：

- 原始消息
- 审批记录
- Session 元信息

运行时压缩结果不是产品层真相。

## 10. 当前验证状态

当前仓库已经补了压缩相关测试，覆盖：

- 配置映射到 `SummarizationMiddleware`
- middleware 压缩触发与有效上下文替换
- 压缩日志输出

测试位置见：

- [backend/tests/test_factory.py](./backend/tests/test_factory.py)
- [backend/tests/test_summarization_middleware.py](./backend/tests/test_summarization_middleware.py)

## 11. 后续顺序

第三阶段之后如果继续推进，更合理的顺序是：

1. Tools 主链路
2. 审批闭环
3. 异步任务链路
4. 再决定是否需要事实层记忆或事件层

在那之前，不建议重新引入业务侧摘要存储。

如果后续真的要实现记忆层，至少需要满足：

- 不能替代产品层原始消息
- 不能替代实时 DBAAS 查询
- 不能把运行时摘要直接当最终事实
- 必须可审计
- 必须按用户和 Session 做清晰隔离
