# DBAAS 智能助手第四阶段说明

## 1. 当前阶段结论

第四阶段聚焦前端可感知的流式对话体验。

当前已经完成：

- FastAPI SSE 流式消息接口
- 前端基于 `fetch` 消费 SSE
- DeepAgent `stream_mode="messages"` token 输出
- 流式结束后继续写回产品层 `messages.jsonl`
- 压缩发生时通过 SSE 发送轻量提醒
- 压缩完成时可写入去重后的 `role=system` 提醒消息

第四阶段不改变第三阶段已经确定的压缩边界：

- 不新增 `summary.json`
- 不新增项目侧 summary store
- 不把压缩摘要正文展示给用户
- 不因为压缩切换新的 `thread_id`

## 2. 新增接口

当前前端主路径使用：

```http
POST /api/v1/sessions/{session_id}/messages/stream
Accept: text/event-stream
```

请求体仍然是：

```json
{
  "content": "继续提问"
}
```

旧接口仍然保留：

```http
POST /api/v1/sessions/{session_id}/messages
```

它继续返回完整 JSON，用于兼容已有调用和非流式测试。

## 3. 当前 SSE 事件

第四阶段对外稳定的事件包括：

- `user_message`
  - 用户消息已经写入产品层 Session
- `started`
  - 本轮运行开始，返回 `run_id` 和 `mode`
- `token`
  - assistant 文本增量
- `compression_started`
  - 当前 thread 即将开始上下文压缩
- `compression_completed`
  - 当前 thread 的上下文压缩已经完成
- `done`
  - assistant 消息已经完整写回产品层 Session
- `error`
  - 本轮流式调用失败

## 4. 压缩提醒策略

压缩提醒分为两层：

- `compression_started`
  - 只作为当前请求内的运行时提示
  - 不写入 `messages.jsonl`
- `compression_completed`
  - 作为当前请求内的运行时提示返回
  - 可以写入一条去重后的 `role=system` 消息，方便页面刷新后仍能看到上下文已整理的事实

也就是说：

- 前端收到 `compression_started` 后提示正在整理上下文
- 前端收到 `compression_completed` 后提示压缩已完成
- `messages.jsonl` 不写入压缩摘要正文
- Session 历史可以包含压缩完成的 `system` 提醒消息
- 提醒中不包含摘要正文

当前压缩事件只包含：

- `run_id`
- `mode`
- `phase`
- 一句用户可读提示
- `summarized_messages`
- `keep`
- `trigger`
- `summary_chars`
- `system_message`
  - `compression_started` 时为 `null`
  - `compression_completed` 时，如果后端本次写入了系统提醒，则返回该消息；重复压缩提示被去重时为 `null`

事件形态示例：

```text
event: compression_started
data: {"run_id":"...","mode":"deepagent","message":"上下文较长，正在整理早期内容。","system_message":null,"details":{"phase":"started","summarized_messages":2,"keep":"('messages', 6)","trigger":"('tokens', 98304)","summary_chars":null}}

event: compression_completed
data: {"run_id":"...","mode":"deepagent","message":"上下文已自动压缩，本会话会继续使用同一个 Session。","system_message":{"message_id":"msg_001","role":"system","content":"上下文已自动压缩，本会话会继续使用同一个 Session。","created_at":"2026-04-22T12:10:03Z"},"details":{"phase":"completed","summarized_messages":2,"keep":"('messages', 6)","trigger":"('tokens', 98304)","summary_chars":512}}
```

这些事件只用于页面提示：

- `compression_started` 不写入 `messages.jsonl`
- `compression_completed` 可以写入去重后的系统提醒
- 不展示摘要正文
- 不作为 `session_events` 持久化
- 不改变当前 `session_id` 或 `thread_id`

## 5. 为什么不写入压缩摘要正文

压缩是运行时上下文工程动作，不是用户和助手之间的一轮对话。
页面只需要知道上下文已经整理，不需要看到摘要正文。

如果把压缩摘要正文或每个 started 事件都写入正式消息历史，会带来几个问题：

- 污染用户可见的对话语义
- 下一轮产品层历史回放会出现非对话消息
- 后续如果做审计或导出，会混入运行时内部事件
- 压缩可能在一次长请求中出现多次，消息列表会变得嘈杂

因此第四阶段采用：

- SSE 里即时提醒
- `compression_started` 前端临时展示
- `compression_completed` 可写入一条去重后的 `system` 提醒
- 后端日志保留
- 不落盘压缩摘要正文

## 6. 压缩日志

压缩真正发生时，后端会输出 `INFO` 日志。

日志内容包含：

- `thread_id`
- 本次被压缩的消息数量
- 当前 `keep`
- 当前 `trigger`
- 摘要字符数

日志大致形态：

```text
会话上下文已压缩 thread_id=... summarized_messages=... keep=('messages', 6) trigger=('tokens', 98304) summary_chars=...
```

日志与 SSE 提醒共用第三阶段的自定义 `SummarizationMiddleware` 包装层，
但日志是后端观测信息，SSE 是当前请求内的临时运行事件。

## 7. 流式报错策略

如果后续模型调用、函数调用或工具调用报错，后端会继续通过 SSE 返回：

```text
event: error
data: {"detail":"函数调用失败：mock_tool 参数 invalid","error_type":"function_error","stage":"tool_call"}
```

当前约定：

- 已经写入的 user 消息会保留
- assistant 消息不会落盘，因为本轮没有正常完成
- 后端会写入一条 `role=ai-agent` 的错误说明，并在 `error` 事件中返回 `ai_agent_message`
- 前端显示 `detail`
- 后端日志记录完整异常和 traceback
- SSE 对外只返回脱敏后的可读错误，不直接暴露完整堆栈

当前错误字段含义：

- `detail`
  - 给前端展示的安全错误说明
- `error_type`
  - 错误分类，例如 `function_error`、`timeout_error`、`provider_error`
- `stage`
  - 错误阶段，例如 `stream`、`invoke`、`tool_call`

## 8. 相关代码

- [backend/src/dbass_ai_agent/agent/runtime.py](./backend/src/dbass_ai_agent/agent/runtime.py)
- [backend/src/dbass_ai_agent/agent/factory.py](./backend/src/dbass_ai_agent/agent/factory.py)
- [backend/src/dbass_ai_agent/agent/compression_events.py](./backend/src/dbass_ai_agent/agent/compression_events.py)
- [backend/src/dbass_ai_agent/api/routes_chat.py](./backend/src/dbass_ai_agent/api/routes_chat.py)
- [frontend/app.js](./frontend/app.js)

## 9. 当前验证

当前已经补充测试覆盖：

- 非流式消息接口仍然可用
- SSE 流式接口事件顺序
- `compression_started` / `compression_completed` 事件透出
- 流式结束后 assistant 消息落盘
- 压缩 middleware 发布压缩通知

测试位置：

- [backend/tests/test_chat_api.py](./backend/tests/test_chat_api.py)
- [backend/tests/test_factory.py](./backend/tests/test_factory.py)
