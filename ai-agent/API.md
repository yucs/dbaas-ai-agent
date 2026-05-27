# DBAAS 智能助手 API 设计

## 1. 文档目的

本文档用于定义当前 DBAAS 智能助手的 API 契约，重点覆盖以下能力：

本项目基于 DeepAgent 实现，因此这里的 API 设计不是一个普通聊天后端接口，
而是围绕“Session 绑定 DeepAgent thread，并在该 thread 上持续执行”的模型展开。

这意味着：

- 页面发送消息时，需要复用当前 Session 对应的 `thread_id`
- 当前前端主链路使用 SSE 流式返回
- 命中人工确认时，需要暂停运行并在确认后恢复同一个 thread

当前接口重点覆盖：

- 多用户、多 Session 管理
- 登录后加载用户历史 Session 列表
- 打开指定 Session 到当前窗口
- Session 下继续发送消息
- 提供 SSE 流式消息接口
- 支持审批查询与审批决策
- 支持 operation 结果、当前 Session 任务查询与任务 SSE

## 1.1 能力边界

在 API 这一层，需要区分：

DeepAgent 原生支持：

- Agent invoke/stream 的运行方式
- 运行过程中的流式事件
- human-in-the-loop 的 interrupt/resume 机制
- 通过 `thread_id` 延续同一个执行上下文

本项目需要自己开发：

- 对外 HTTP API 路由
- `user_id` 注入与访问控制
- Session 列表与详情接口
- 消息发送接口
- 面向前端的 SSE 事件封装
- 审批查询与审批决策接口
- 本地文件存储与 API 之间的读写映射

本阶段优先对齐页面体验与本地文件存储模型，
不追求一次性覆盖所有复杂工作流能力。

## 2. 设计范围

当前 API 主要解决：

- Session 列表展示
- Session 详情加载
- 新建 Session
- 归档、恢复与删除
- 会话内消息发送
- 运行流式输出
- 强制人工确认相关接口
- 当前 Session 下的异步任务追踪

暂不覆盖：

- 跨 Session 的任务中心
- 全局任务编排 API
- 跨 Session 搜索
- 高级权限中心

## 3. 基本约定

### 3.1 Base Path

建议统一使用：

```text
/api/v1
```

### 3.2 用户身份

当前本地开发阶段直接使用 `user_id` 作为产品层用户标识。

但调用 `mock-server` 时，不应简单地把所有 `user_id` 都直接当作 `user`。

身份模型拆成：

- `user_id`
  - 当前产品用户标识
- `backend_role`
  - `admin` 或 `user`
- `user`
  - 仅在普通用户场景下使用

对应到 DBAAS 或当前 `mock-server`：

- 管理员
  - 使用 `Authorization: Bearer admin`
  - 可访问全部资源
- 普通用户
  - 使用 `Authorization: Bearer user`
  - DBAAS 根据 actor user 判断普通用户可见范围

所有有当前 request/session identity 的 DBAAS HTTP 请求都追加：

```text
X-DBAAS-Actor-User: {identity.user_id}
X-DBAAS-Actor-Role: {identity.role}
```

后台系统任务使用：

```text
X-DBAAS-Actor-User: dbaas-ai-agent
X-DBAAS-Actor-Role: system
```

所有 DBAAS HTTP 请求都由后端根据当前 request/session identity 统一注入上述身份。
前端请求体、AI tool 参数和模型输出不得传入 `user_id`、`role` 或 `user`
来影响 DBAAS 调用身份。

因此在当前实现中：

- Session 按产品层 `user_id` 组织
- 只有普通用户场景才将 `user_id` 作为 `user`
- 管理员场景直接使用 `admin` 作为后端 principal

在本地开发阶段，可以先通过以下方式之一注入：

- 请求头 `X-User-Id`
- 登录态 middleware 注入

本地开发阶段通过以下请求头区分管理员与普通用户：

- `X-User-Role`
- `X-User`

正式接入统一登录后，再由鉴权层解析并注入这些信息。

Session 创建后身份不可变：

- 后端在创建 Session 时固化 `user_id`、`role` 和 `user`
- 后续访问该 Session 时，当前请求身份必须与 `SessionMeta` 一致
- 如果 `role` 或普通用户 `user` 发生变化，应新建 Session，不复用原 `thread_id`
- 身份不一致时，Session 详情、消息发送、审批决策和任务查询等接口应拒绝继续使用该 Session
- 删除接口可以允许当前 `user_id` 清理同一 `user_id` 下旧身份 Session，但不得触发 DeepAgent resume；若旧 Session 存在 pending approval 或非终态 task，应返回冲突错误

### 3.3 时间格式

统一使用 UTC ISO8601 格式，例如：

```text
2026-04-22T12:10:00Z
```

### 3.4 Session 状态

建议第一阶段统一支持：

- `active`
- `archived`

### 3.5 通用原则

- `GET /sessions` 只返回当前用户自己的 Session
- 不允许跨 `user_id` 访问他人的 Session
- `archive` 负责可恢复，`delete` 负责真正删除
- 写操作类 Agent 行为仍需审批机制控制

## 4. Session 相关接口

### 4.1 获取当前用户 Session 列表

```http
GET /api/v1/sessions
```

#### 作用

用于页面左侧历史会话列表加载。

`items` 返回当前身份可继续使用的 Session。
`stale_identity_items` 返回同一 `user_id` 下旧身份 Session，正常情况下为空数组。
旧身份 Session 只允许删除清理，不允许打开、继续对话、审批或任务查询。

#### 数据来源

读取：

```text
data/users/<user_id>/sessions/index.json
```

#### 返回示例

```json
{
  "items": [
    {
      "session_id": "sess_001",
      "title": "排查 mysql-xf2",
      "status": "active",
      "updated_at": "2026-04-22T12:10:00Z",
      "last_message_at": "2026-04-22T12:10:00Z",
      "preview": "已查询 mysql-xf2，健康状态为 DEGRADED"
    }
  ],
  "stale_identity_items": []
}
```

### 4.2 创建新 Session

```http
POST /api/v1/sessions
```

#### 请求体示例

```json
{
  "title": "新建会话"
}
```

#### 行为

- 生成新的 `session_id`
- 创建 Session 目录
- 初始化 `meta.json`
- `messages.jsonl`、`approvals.jsonl`、`operations.jsonl`、`tasks.jsonl` 可按首次写入懒创建
- 更新当前用户的 `index.json`

#### 返回示例

```json
{
  "session": {
    "meta": {
      "session_id": "sess_003",
      "title": "新建会话",
      "status": "active",
      "thread_id": "thread_003",
      "created_at": "2026-04-22T12:20:00Z"
    },
    "messages": [],
    "approvals": [],
    "operations": []
  }
}
```

### 4.3 获取单个 Session 详情

```http
GET /api/v1/sessions/{session_id}
```

#### 作用

用于点击历史会话后加载到当前窗口。

#### 行为

- 校验该 `session_id` 属于当前 `user_id`
- 读取 `meta.json`
- 读取 `messages.jsonl`
- 读取 `approvals.jsonl`
- 读取 `operations.jsonl`
- `tasks.jsonl` 不放入 Session 详情，统一通过任务接口读取

#### 返回示例

```json
{
  "session": {
    "meta": {
      "session_id": "sess_001",
      "user_id": "user_001",
      "role": "user",
      "user": "user_001",
      "thread_id": "thread_001",
      "title": "排查 mysql-xf2 状态",
      "status": "active",
      "created_at": "2026-04-22T12:00:00Z",
      "updated_at": "2026-04-22T12:10:00Z"
    },
    "approvals": [],
    "operations": [],
    "messages": [
      {
        "message_id": "msg_001",
        "role": "user",
        "content": "查看 mysql-xf2 状态",
        "created_at": "2026-04-22T12:00:01Z"
      }
    ]
  }
}
```

### 4.4 归档 Session

```http
POST /api/v1/sessions/{session_id}/archive
```

#### 行为

- 非阻塞获取当前 Session 的 `session_run_lock`
- 执行 pending approval lazy expiration
- 如果仍存在待确认审批、非终态任务或未清理完成的过期审批暂停点，返回 `409 Conflict`
- 更新 `meta.json.status = archived`
- 写入 `archived_at`
- 同步更新 `index.json`

#### 返回示例

```json
{
  "session": {
    "session_id": "sess_001",
    "status": "archived",
    "archived_at": "2026-04-22T12:12:00Z"
  }
}
```

### 4.5 恢复已归档 Session

```http
POST /api/v1/sessions/{session_id}/restore
```

#### 行为

- 更新 `meta.json.status = active`
- 清空 `archived_at`
- 同步更新 `index.json`

### 4.6 删除 Session

```http
DELETE /api/v1/sessions/{session_id}
```

#### 行为

- 非阻塞获取当前 Session 的 `session_run_lock`
- 执行 pending approval lazy expiration
- 如果仍存在待确认审批或非终态任务，返回 `409 Conflict`
- 从 `data/users/<user_id>/sessions/index.json` 中移除
- 删除 `data/users/<user_id>/sessions/<session_id>/` 目录
- 同步删除该 Session 绑定的 `thread_id` 对应 DeepAgent checkpoint 数据
- 默认不再出现在正常历史列表中

#### 说明

第一阶段中，`archive` 负责“可恢复”，`delete` 负责“真正删除”。
第七阶段后，归档和删除会额外受 pending approval、running task 和 session run lock 约束。

## 5. 消息与运行接口

### 5.1 在 Session 中发送消息

```http
POST /api/v1/sessions/{session_id}/messages
```

#### 作用

用于在当前窗口继续对话。

#### 请求体示例

```json
{
  "content": "查看 mysql-xf2 当前状态"
}
```

#### 行为

- 非阻塞获取当前 Session 的 `session_run_lock`
- 执行 pending approval lazy expiration
- 如果当前 Session 仍有待确认审批，返回 `409 Conflict`
- 如果当前 Session 状态为 `archived`，先自动恢复为 `active`
- 将用户消息追加到 `messages.jsonl`
- 复用当前 Session 对应的 `thread_id`
- 调用 DeepAgent 执行
- 普通完成时将 assistant 消息写回 `messages.jsonl`
- 命中写工具审批时返回 `approval` 且 `paused=true`，本轮不写入 assistant 消息
- 返回本轮消息结果与 `run_id`

#### 返回示例

```json
{
  "session": {
    "session_id": "sess_001",
    "thread_id": "thread_001",
    "title": "排查 mysql-xf2 状态",
    "status": "active"
  },
  "user_message": {
    "message_id": "msg_010",
    "role": "user",
    "content": "查看 mysql-xf2 当前状态",
    "created_at": "2026-04-22T12:10:01Z"
  },
  "assistant_message": {
    "message_id": "msg_011",
    "role": "assistant",
    "content": "当前阶段还未接通 DBAAS 实时查询能力。",
    "created_at": "2026-04-22T12:10:02Z"
  },
  "run_id": "run_010",
  "mode": "deepagent",
  "warning": "mock-server-disabled"
}
```

### 5.2 流式发送消息

```http
POST /api/v1/sessions/{session_id}/messages/stream
Accept: text/event-stream
```

#### 作用

用于在当前窗口继续对话，并通过 SSE 返回运行过程。

#### 请求体示例

```json
{
  "content": "继续分析这个问题"
}
```

#### 当前事件类型

- `user_message`
  - 用户消息已经写入 `messages.jsonl`
- `started`
  - 本轮运行开始
- `token`
  - assistant 文本增量
- `compression_started`
  - 当前运行即将开始上下文压缩
- `compression_completed`
  - 当前运行的上下文压缩已经完成
- `approval.required`
  - 当前运行命中人工确认，返回审批记录
- `run.paused`
  - 当前运行已经暂停，等待审批决策
- `done`
  - assistant 消息已经写入 `messages.jsonl`
- `error`
  - 当前运行失败

#### `error` 事件示例

```text
event: error
data: {"detail":"函数调用失败：mock_tool 参数 invalid","error_type":"function_error","stage":"tool_call"}
```

错误事件约定：

- 本轮不会写入 assistant 消息
- 已写入的 user 消息保留
- 后端会写入一条 `role=ai-agent` 的错误说明，并在事件中返回 `ai_agent_message`
- `detail` 是脱敏后的前端可展示错误
- 完整异常和 traceback 只写入后端日志
- `error_type` 用于区分 `function_error`、`timeout_error`、`provider_error` 等类别
- `stage` 用于标记错误发生阶段

#### 压缩事件示例

```text
event: compression_started
data: {"run_id":"run_001","mode":"deepagent","message":"上下文较长，正在整理早期内容。","system_message":null,"details":{"phase":"started","summarized_messages":8,"keep":"('messages', 6)","trigger":"('tokens', 98304)","summary_chars":null}}

event: compression_completed
data: {"run_id":"run_001","mode":"deepagent","message":"上下文已自动压缩，本会话会继续使用同一个 Session。","system_message":{"message_id":"msg_001","role":"system","content":"上下文已自动压缩，本会话会继续使用同一个 Session。","created_at":"2026-04-22T12:10:03Z"},"details":{"phase":"completed","summarized_messages":8,"keep":"('messages', 6)","trigger":"('tokens', 98304)","summary_chars":1200}}
```

压缩事件用于提醒页面：

- `compression_started` 不写入 `messages.jsonl`
- `compression_completed` 可以写入一条去重后的 `role=system` 提醒消息，并通过 `system_message` 返回
- 不展示摘要正文
- 不改变 `session_id`
- 不改变 `thread_id`

### 5.3 为什么项目侧仍然要封装 SSE

DeepAgent 原生支持 streaming，但它提供的是 Agent 运行时层面的流式事件。

本项目面对的是页面侧集成，因此仍然需要在项目侧封装一层 SSE。

原因如下：

- DeepAgent 负责“怎么流式执行”
- 本项目需要定义“前端应该看到什么事件”

DeepAgent 原生更偏运行时语义，例如：

- token 输出
- tool 调用过程
- subagent 进度
- interrupt/resume

而页面真正关心的是更稳定的产品语义，例如：

- 当前属于哪个 `session_id`
- 当前属于哪个 `run_id`
- 当前消息是否完成
- 当前是否需要人工审批
- 当前运行是否结束或失败

如果前端直接依赖底层原始流，会带来几个问题：

- 前端与 DeepAgent 运行时事件格式耦合过深
- 底层事件过细，页面渲染噪音较大
- Session、审批、归档等产品层状态不容易直接表达
- 后续如果内部运行逻辑调整，前端需要跟着修改

因此建议采用“两层事件模型”：

- DeepAgent 内部流
  - 用于驱动运行时执行
- 项目对外 SSE 流
  - 用于服务前端页面

当前第四阶段实现仍然是一个轻量转换层：

- 从 DeepAgent `stream()` 中读取原始事件
- 转换成项目定义的事件名
- 补充 `session_id`、`run_id` 等页面需要的字段
- 再通过 SSE 返回给前端

## 6. 审批接口

### 6.1 查询 Session 下的审批记录

```http
GET /api/v1/sessions/{session_id}/approvals
```

#### 查询语义

第一版返回当前 Session 下全部 approvals，不做服务端 `status` 过滤。
前端如需只展示 `pending`、`approved`、`rejected`、`expired` 中某类状态，
先在本地过滤。

#### 数据来源

读取：

```text
data/users/<user_id>/sessions/<session_id>/approvals.jsonl
```

### 6.2 提交审批决策

```http
POST /api/v1/sessions/{session_id}/approvals/{approval_id}/decision
```

#### 请求体示例

```json
{
  "decision": "approved"
}
```

#### 行为

- 校验该 approval 属于当前 Session
- 校验当前用户有权限访问该 Session，并满足 approval 要求的角色
- 更新当前 Session 下的审批记录状态
- 触发当前 Session 绑定的 Thread 恢复执行
- 同步返回审批最新状态、恢复后的 assistant 消息、system 消息、operation 和 task 结果
- P7A 不要求审批备注字段

#### 决策值建议

- `approved`
- `rejected`

#### 返回字段

- `approval`
  - 审批最新状态
- `assistant_message`
  - resume 后写入 `messages.jsonl` 的助手消息；用户拒绝时使用固定说明
- `system_message`
  - 异步 task 创建成功时由后端写入的系统提醒；没有新提醒时为 `null`
- `operations`
  - 本次 approval resume 触发的 operation 列表
- `tasks`
  - 本次 approval resume 创建或关联的 task 列表
- `next_approval`
  - resume 后再次命中写工具 interrupt 时返回新的待确认审批
- `paused`
  - 是否因 `next_approval` 再次暂停
- `run_id` / `mode`
  - 本次 resume 对应的运行信息

## 7. 页面加载与接口关系

### 7.1 登录后进入页面

页面初始化建议调用：

```text
GET /api/v1/sessions
```

用于渲染左侧历史 Session 列表。

### 7.2 用户点击某个历史 Session

页面建议调用：

```text
GET /api/v1/sessions/{session_id}
```

用于将该 Session 的消息加载到当前窗口。

### 7.3 用户继续发送消息

页面建议按以下顺序执行：

1. `POST /api/v1/sessions/{session_id}/messages/stream`
2. 按 SSE 事件更新当前窗口
3. 如果调用方不支持 SSE，可退回 `POST /api/v1/sessions/{session_id}/messages`

### 7.4 用户归档或删除 Session

页面调用：

- `POST /api/v1/sessions/{session_id}/archive`
- `DELETE /api/v1/sessions/{session_id}`

完成后刷新左侧 Session 列表。

## 8. API 与本地存储映射

### 8.1 列表

```text
GET /sessions
-> data/users/<user_id>/sessions/index.json
```

### 8.2 详情

```text
GET /sessions/{session_id}
-> data/users/<user_id>/sessions/<session_id>/meta.json
-> data/users/<user_id>/sessions/<session_id>/messages.jsonl
-> data/users/<user_id>/sessions/<session_id>/approvals.jsonl
-> data/users/<user_id>/sessions/<session_id>/operations.jsonl
```

`tasks.jsonl` 不放入 Session 详情响应，统一通过任务接口读取。

### 8.3 审批

```text
GET /sessions/{session_id}/approvals
-> data/users/<user_id>/sessions/<session_id>/approvals.jsonl
```

### 8.4 任务

```text
GET /sessions/{session_id}/tasks
-> data/users/<user_id>/sessions/<session_id>/tasks.jsonl
```

## 9. 当前代码目录映射

当前 API 相关代码主要分布在：

- `backend/src/dbass_ai_agent/api/`
  - 对外 HTTP、SSE 路由、依赖注入和响应 schema
- `backend/src/dbass_ai_agent/sessions/`
  - Session 元数据、列表索引、消息、审批、operation/task append-only 文件读写
- `backend/src/dbass_ai_agent/agent/`
  - DeepAgent runtime、流式事件转换、上下文压缩事件
- `backend/src/dbass_ai_agent/operations/`
  - 审批决策、operation/task 记录和写操作编排
- `backend/src/dbass_ai_agent/infra/`
  - 路径拼装、ID 生成、时间和日志等基础设施

## 10. 当前建议结论

当前 API 已收敛为：

- `GET /api/v1/sessions`
- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/messages`
- `POST /api/v1/sessions/{session_id}/messages/stream`
- `GET /api/v1/sessions/{session_id}/approvals`
- `POST /api/v1/sessions/{session_id}/approvals/{approval_id}/decision`
- `GET /api/v1/sessions/{session_id}/tasks`
- `GET /api/v1/sessions/{session_id}/tasks/events`
- `POST /api/v1/sessions/{session_id}/archive`
- `POST /api/v1/sessions/{session_id}/restore`
- `DELETE /api/v1/sessions/{session_id}`

这套接口已经足以支撑第一版页面体验，并且能和当前的
本地 Session 存储模型、SSE 模型和审批模型自然衔接。
