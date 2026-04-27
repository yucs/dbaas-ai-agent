# DBAAS 智能助手上下文压缩实现

## 1. 文档目的

本文档只描述当前代码里已经生效的上下文压缩实现。

重点回答四个问题：

- 压缩发生在哪一层
- 哪些配置已经真正生效
- 压缩对 Session、页面历史和 `thread_id` 有什么影响
- 当前实现明确没有做什么

## 2. 当前结论

当前仓库里的压缩能力已经接入主链路，结论如下：

- 页面历史与 Session 文件仍保存原始消息
- DeepAgent 继续在原 `thread_id` 上运行
- 长会话压缩由项目自定义包装的 `SummarizationMiddleware` 完成
- 压缩提示词和阈值由项目自己的 `config.toml` 控制
- 压缩发生时会输出后端 `INFO` 日志
- 如果当前请求走 SSE 流式接口，会发送压缩开始和完成提醒事件
- 不再维护 `summary.json`
- 不向前端发送摘要正文

换句话说：

- `Session` 是产品层真相
- `thread_id + checkpoint` 是运行时真相
- 压缩只改变模型后续看到的上下文，不改变页面聊天记录
- 压缩提醒是临时运行事件，不是聊天消息

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
  -> messages.json
```

相关代码：

- [factory.py](./backend/src/dbass_ai_agent/agent/factory.py)
- [runtime.py](./backend/src/dbass_ai_agent/agent/runtime.py)
- [routes_chat.py](./backend/src/dbass_ai_agent/api/routes_chat.py)

## 4. 压缩是怎么接进去的

### 4.1 仍然使用 `create_deep_agent()`

当前没有放弃 `create_deep_agent()`，也没有在业务层自己重新拼一套 agent graph。

后端仍然调用：

- `deepagents.create_deep_agent(...)`

这样可以继续保留 DeepAgents 默认的主链路能力。

### 4.2 接管 DeepAgents 内部的 summarization factory

DeepAgents 自己会在 `create_deep_agent()` 内部创建 `SummarizationMiddleware`。
如果我们再额外挂一个同类 middleware，就会触发 duplicate middleware 报错。

因此当前实现采用的是：

1. 先创建主对话模型
2. 再创建一个专门给压缩用的 summary model
3. 基于项目配置生成自定义 `SummarizationMiddleware` 包装层
4. 临时 patch DeepAgents 内部的 `create_summarization_middleware`
5. 在 patch 生效窗口内调用 `create_deep_agent()`

对应代码在 [factory.py](./backend/src/dbass_ai_agent/agent/factory.py)：

- `build_runtime_artifacts()`
- `build_summarization_middleware_factory()`
- `patch_deepagents_summarization_factory()`

### 4.3 为什么这样做

这样做的好处是：

- 不会重复挂载 summarization middleware
- 还能继续使用 DeepAgents 主体能力
- 可以把压缩提示词和阈值切到项目自己的配置
- 可以在同一个包装层追加项目自己的 side effects

### 4.4 当前自定义点

当前项目并不是直接使用 DeepAgents 默认生成的 middleware 实例，
而是在应用层生成一个自定义包装类后，再替换 DeepAgents 内部的 factory。

当前这个包装层已经做了三件事：

- 使用项目自己的压缩提示词
- 在压缩真正发生时输出 `INFO` 日志
- 在当前请求注册了监听器时，发布压缩通知

对应代码见 [factory.py](./backend/src/dbass_ai_agent/agent/factory.py)：

- `_build_logged_summarization_middleware_class()`
- `build_summarization_middleware_factory()`

压缩通知的请求级隔离见：

- [compression_events.py](./backend/src/dbass_ai_agent/agent/compression_events.py)
- [runtime.py](./backend/src/dbass_ai_agent/agent/runtime.py)

## 5. 当前已经生效的压缩配置

当前这些配置项已经接入运行时：

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

配置读取见 [config.py](./backend/src/dbass_ai_agent/config.py)，
实际组装见 [factory.py](./backend/src/dbass_ai_agent/agent/factory.py)。

## 6. 压缩提示词

当前压缩提示词来自：

- [backend/prompts/compression.md](./backend/prompts/compression.md)

它要求压缩模型输出结构化 JSON，重点保留：

- 当前目标
- 已确认事实
- 观察到的资源
- 已完成动作
- 已批准 / 已拒绝动作
- 待处理事项
- 约束条件

需要注意：

- 这份 JSON 是运行时摘要内容
- 它不会再额外持久化为 Session 文件
- 它不是产品层公开的数据结构契约

## 7. 压缩对 Session 的影响

压缩发生后：

- `messages.json` 不会被改写
- 页面历史消息不会消失
- `meta.json` 不会新增摘要字段
- `session_id` 不会变化
- `thread_id` 不会切换

Session 目录当前仍然只保留：

- `index.json`
- `meta.json`
- `messages.json`
- `approvals.jsonl`

所以压缩影响的是模型上下文，不是产品层记录。

## 8. 压缩日志与前端提醒

当前实现已经在压缩真正发生时输出后端日志。

日志位置在：

- [factory.py](./backend/src/dbass_ai_agent/agent/factory.py)

日志内容包含：

- `thread_id`
- 本次被压缩的消息数量
- 当前 `keep`
- 当前 `trigger`
- middleware 内部使用的 history path
- 摘要字符数

日志大致形态：

```text
会话上下文已压缩 thread_id=... summarized_messages=... keep=('messages', 6) trigger=('tokens', 98304) history_path=... summary_chars=...
```

如果当前消息请求走的是 SSE 流式接口，还会额外发送：

```text
event: compression_started
data: {"run_id":"...","mode":"deepagent","message":"上下文较长，正在整理早期内容。","details":{"phase":"started","summarized_messages":2,"keep":"('messages', 6)","trigger":"('tokens', 98304)","summary_chars":null}}

event: compression_completed
data: {"run_id":"...","mode":"deepagent","message":"上下文已自动压缩，本会话会继续使用同一个 Session。","details":{"phase":"completed","summarized_messages":2,"keep":"('messages', 6)","trigger":"('tokens', 98304)","summary_chars":512}}
```

这些事件只用于页面提示：

- 不写入 `messages.json`
- 不展示摘要正文
- 不作为 `session_events` 持久化
- 不改变当前 `session_id` 或 `thread_id`

## 9. 当前可扩展钩子

当前代码已经验证了一种稳定的扩展方式：

- 在自定义 `SummarizationMiddleware` 包装层里覆写 `_create_summary()` / `_acreate_summary()`
- 在“压缩已经真正发生”这一时机追加项目侧动作

当前这个钩子已经用于：

- `logger.info(...)`
- SSE `compression_started` / `compression_completed` 提醒事件

后续如果需要，也可以沿同一位置扩展：

- metrics / tracing
- 审计侧的非持久化通知动作

但当前实现仍建议遵守两个约束：

- 不要再回到全局 `SESSION_META` 这类共享状态
- SSE / 前端提醒必须基于当前请求和当前 `thread_id` 的上下文做隔离

## 10. 当前没有做什么

当前实现明确没有这些能力：

- `summary.json`
- 项目侧 `summary_store`
- `on_summary` 回调驱动的业务层摘要落盘
- 独立的长期记忆系统
- `session_events` / facts store
- 基于压缩触发的新 `thread_id`

## 11. 关于历史落盘的边界

`SummarizationMiddleware` 内部仍然有“把被压缩历史 offload 到 backend”的能力，
但这属于 DeepAgents 中间件自己的内部行为。

当前应用层并没有把这件事当成产品契约，原因是：

- 我们没有单独维护项目侧 summary store
- 当前运行时仍然沿用 `create_deep_agent()` 的默认 backend 行为
- 即使 middleware 内部的 offload 是 best-effort，压缩本身仍可继续进行

因此当前对外应只承诺：

- 长会话能在原线程内继续
- 压缩不破坏产品层消息记录

而不应承诺一定存在可供产品层读取的 `conversation_history/*.md` 文件。

## 12. 与记忆的关系

当前压缩能力解决的是：

- 上下文窗口受限时，模型后续该看什么

它没有解决：

- 哪些操作事实需要长期保存
- 是否需要跨 Session 语义层
- 是否需要独立事实库或事件库

这些问题统一以 [MEMORY.md](./MEMORY.md) 为准。
