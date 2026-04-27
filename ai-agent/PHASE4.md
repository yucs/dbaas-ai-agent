# DBAAS 智能助手第四阶段说明

## 1. 当前阶段结论

第四阶段聚焦前端可感知的流式对话体验。

当前已经完成：

- FastAPI SSE 流式消息接口
- 前端基于 `fetch` 消费 SSE
- DeepAgent `stream_mode="messages"` token 输出
- 流式结束后继续写回产品层 `messages.json`
- 压缩发生时通过 SSE 发送轻量提醒

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

压缩提醒只作为运行时提示，不作为聊天消息落盘。

也就是说：

- 前端收到 `compression_started` 后提示正在整理上下文
- 前端收到 `compression_completed` 后提示压缩已完成
- `messages.json` 不写入这条提示
- Session 历史仍只包含用户消息和助手消息
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

## 5. 为什么压缩提醒不写进消息历史

压缩是运行时上下文工程动作，不是用户和助手之间的一轮对话。

如果把它写入正式消息历史，会带来几个问题：

- 污染用户可见的对话语义
- 下一轮产品层历史回放会出现非对话消息
- 后续如果做审计或导出，会混入运行时内部事件
- 压缩可能在一次长请求中出现多次，消息列表会变得嘈杂

因此第四阶段采用：

- SSE 里即时提醒
- 前端临时展示
- 后端日志保留
- 不进入 Session 消息投影

## 5. 流式报错策略

如果后续模型调用、函数调用或工具调用报错，后端会继续通过 SSE 返回：

```text
event: error
data: {"detail":"函数调用失败：mock_tool 参数 invalid","error_type":"function_error","stage":"tool_call"}
```

当前约定：

- 已经写入的 user 消息会保留
- assistant 消息不会落盘，因为本轮没有正常完成
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

## 6. 相关代码

- [backend/src/dbass_ai_agent/agent/runtime.py](./backend/src/dbass_ai_agent/agent/runtime.py)
- [backend/src/dbass_ai_agent/agent/factory.py](./backend/src/dbass_ai_agent/agent/factory.py)
- [backend/src/dbass_ai_agent/agent/compression_events.py](./backend/src/dbass_ai_agent/agent/compression_events.py)
- [backend/src/dbass_ai_agent/api/routes_chat.py](./backend/src/dbass_ai_agent/api/routes_chat.py)
- [frontend/app.js](./frontend/app.js)

## 7. 当前验证

当前已经补充测试覆盖：

- 非流式消息接口仍然可用
- SSE 流式接口事件顺序
- `compression_started` / `compression_completed` 事件透出
- 流式结束后 assistant 消息落盘
- 压缩 middleware 发布压缩通知

测试位置：

- [backend/tests/test_chat_api.py](./backend/tests/test_chat_api.py)
- [backend/tests/test_factory.py](./backend/tests/test_factory.py)
