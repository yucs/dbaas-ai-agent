# 前端调用 AI-Agent API 开发文档

## 1. 文档范围

本文档只描述当前 `frontend/app.js` 实际调用的 ai-agent 后端接口。

当前前端页面是一个本地开发用的单页对话界面，核心流程是：

1. 读取前端配置。
2. 通过本地登录态构造身份请求头。
3. 加载当前用户 Session 列表。
4. 打开最近 Session；如果没有历史 Session，则创建一个新 Session。
5. 在当前 Session 上通过 SSE 发送消息。
6. 如命中审批，在消息时间线里展示审批卡片并提交决策。
7. 如产生 DBAAS 异步任务，展示任务面板并订阅任务事件。

当前页面没有接入的后端接口不放入本文档。

## 2. Base Path 与身份头

### 2.1 Base Path

```text
/api/v1
```

### 2.2 本地登录态

当前页面将登录信息保存在浏览器 `localStorage`：

```text
dbass-auth
```

保存结构：

```json
{
  "user_id": "ops_zhang",
  "role": "admin"
}
```

普通用户示例：

```json
{
  "user_id": "payment_team",
  "role": "user"
}
```

### 2.3 请求头

当前前端所有 `api()` 请求和 SSE `fetch()` 请求都会带以下 header：

```http
X-User-Id: payment_team
X-User-Role: user
Content-Type: application/json
```

管理员登录时：

```http
X-User-Id: ops_zhang
X-User-Role: admin
Content-Type: application/json
```

字段说明：

| Header | 来源 | 说明 |
| --- | --- | --- |
| `X-User-Id` | `state.auth.user_id` | 产品层用户标识、Session 归属用户，也是 DBAAS 调用发起者用户名 |
| `X-User-Role` | `state.auth.role` | `admin` 或 `user` |
| `Content-Type` | 固定 | 当前前端统一发送 `application/json` |

正式接入统一登录后，建议由鉴权层注入身份，前端不再手工维护这些 header。

## 3. 前端实际调用接口清单

| 方法 | 路径 | 当前页面使用位置 | 作用 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/config` | `bootstrap()` | 获取消息最大长度 |
| `GET` | `/api/v1/sessions` | `fetchSessions()` | 获取当前身份的会话列表 |
| `POST` | `/api/v1/sessions` | `createSession()` | 创建新会话 |
| `GET` | `/api/v1/sessions/{session_id}` | `loadSession()` / `refreshCurrentSessionDetail()` | 加载会话详情 |
| `DELETE` | `/api/v1/sessions/{session_id}` | `handleDelete()` | 删除会话 |
| `POST` | `/api/v1/sessions/{session_id}/messages/stream` | `streamMessageResponse()` | SSE 流式发送消息 |
| `POST` | `/api/v1/sessions/{session_id}/approvals/{approval_id}/decision` | `handleApprovalDecision()` | 提交审批决策 |
| `GET` | `/api/v1/sessions/{session_id}/tasks` | `fetchSessionTasks()` | 查询当前会话任务 |
| `GET` | `/api/v1/sessions/{session_id}/tasks/events` | `subscribeTaskEvents()` | 订阅当前会话任务状态变化 |

## 4. 前端页面调用流程

### 4.1 页面启动

当前 `bootstrap()` 流程：

1. 调用 `GET /api/v1/config`。
2. 从 `localStorage.dbass-auth` 读取登录态。
3. 如果没有登录态，打开本地登录弹窗。
4. 如果有登录态，调用 `initializeAfterLogin()`。

`initializeAfterLogin()` 流程：

1. 调用 `GET /api/v1/sessions`。
2. 如果列表不为空，打开排序后的第一个 Session。
3. 如果列表为空，调用 `POST /api/v1/sessions` 创建新 Session，然后打开它。

Session 列表排序逻辑在前端完成：优先按 `last_message_at`，其次按 `updated_at` 倒序。

### 4.2 打开会话

`loadSession(sessionId)` 流程：

1. 调用 `GET /api/v1/sessions/{session_id}`。
2. 将 `payload.session` 写入 `state.currentSession`。
3. 渲染消息、审批、操作结果时间线。
4. 调用 `GET /api/v1/sessions/{session_id}/tasks`。
5. 如果存在运行中任务或尚未发出终态提醒的任务，订阅 `GET /api/v1/sessions/{session_id}/tasks/events`。

### 4.3 发送消息

当前页面只使用 SSE 流式发送：

```http
POST /api/v1/sessions/{session_id}/messages/stream
Accept: text/event-stream
Content-Type: application/json
```

前端发送前会：

- `trim()` 消息内容
- 校验不能为空
- 校验长度不超过 `GET /api/v1/config` 返回的 `message_max_chars`
- 先插入本地 optimistic user message 和 optimistic assistant message
- SSE 完成后用后端返回的真实 message 替换 optimistic message

### 4.4 审批决策

当前前端不单独调用审批列表接口。审批记录来自两个来源：

- `GET /api/v1/sessions/{session_id}` 返回的 `session.approvals`
- 消息流中的 `approval.required` / `done.approval`

用户点击审批卡片按钮后，前端调用：

```http
POST /api/v1/sessions/{session_id}/approvals/{approval_id}/decision
```

提交成功后，当前前端会：

1. 如果响应中有 `system_message`，先插入消息区。
2. 调用 `reconcileCurrentSession()`，重新获取 Session 列表和当前 Session 详情。
3. 如响应中有 `next_approval`，提示后续操作继续等待确认。
4. 否则提示已批准或已拒绝。

### 4.5 任务订阅

当前任务面板只展示当前 Session 的任务。

打开 Session 或审批后重新同步 Session 时，前端调用：

```http
GET /api/v1/sessions/{session_id}/tasks
```

如果任务中存在以下任一情况，会订阅任务事件：

- `status` 不是 `succeeded` / `failed` / `canceled`
- `terminal_notice_emitted=false`

订阅接口：

```http
GET /api/v1/sessions/{session_id}/tasks/events
Accept: text/event-stream
```

任务事件流结束后，如果前端判断仍有任务事件工作未完成，会 3 秒后自动重新订阅。

## 5. 公共错误处理

当前前端的 `api()` 会读取响应 JSON，然后使用 `payload.detail` 生成错误消息。

支持的 `detail` 形态：

字符串：

```json
{
  "detail": "消息内容不能为空。"
}
```

对象：

```json
{
  "detail": {
    "error_type": "session_has_pending_approval",
    "detail": "当前 Session 存在待确认操作，请先批准或拒绝后再发送消息。"
  }
}
```

数组，例如 FastAPI 参数校验错误：

```json
{
  "detail": [
    {
      "loc": ["body", "content"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

前端当前不会直接读取 `detail.error_type` 做分支，只会展示 `detail.detail` 或格式化后的校验消息。

## 6. 核心结构体

### 6.1 SessionListResponse

`GET /api/v1/sessions` 返回：

```json
{
  "items": [
    {
      "session_id": "sess_001",
      "title": "排查 mysql-xf2",
      "status": "active",
      "updated_at": "2026-04-22T12:10:00Z",
      "last_message_at": "2026-04-22T12:10:00Z",
      "preview": "已查询 mysql-xf2，健康状态为 warning"
    }
  ],
  "stale_identity_items": []
}
```

当前前端只使用 `items`，忽略 `stale_identity_items`。

`items[]` 字段：

| 字段 | 类型 | 当前前端用途 |
| --- | --- | --- |
| `session_id` | string | 打开、删除、标记当前会话 |
| `title` | string | 会话列表标题 |
| `status` | string | 会话状态展示 |
| `updated_at` | string | 排序和时间展示 |
| `last_message_at` | string / null | 优先排序和时间展示 |
| `preview` | string | 会话列表摘要 |

### 6.2 SessionDetail

`GET /api/v1/sessions/{session_id}` 返回：

```json
{
  "session": {
    "meta": {
      "session_id": "sess_001",
      "user_id": "payment_team",
      "role": "user",
      "thread_id": "thread_001",
      "title": "排查 mysql-xf2 状态",
      "status": "active",
      "created_at": "2026-04-22T12:00:00Z",
      "updated_at": "2026-04-22T12:10:00Z",
      "last_message_at": "2026-04-22T12:10:00Z",
      "archived_at": null,
      "deleted_at": null
    },
    "messages": [
      {
        "message_id": "msg_001",
        "role": "user",
        "content": "查看 mysql-xf2 状态",
        "created_at": "2026-04-22T12:00:01Z"
      }
    ],
    "approvals": [],
    "operations": []
  }
}
```

当前前端将 `messages`、`approvals`、`operations` 合并成一条时间线展示。

时间线排序规则：

1. message 使用 `created_at`
2. approval 使用 `created_at`
3. operation 使用 `completed_at || started_at || created_at`
4. 同一时间下按 message、approval、operation 优先级排序

### 6.3 ChatMessage

```json
{
  "message_id": "msg_001",
  "role": "assistant",
  "content": "mysql-xf2 当前健康状态为 warning。",
  "created_at": "2026-04-22T12:10:02Z"
}
```

当前前端识别的 `role`：

| role | 展示名称 |
| --- | --- |
| `user` | 用户 |
| `assistant` | 助手 |
| `system` | 系统 |
| `ai-agent` | AI Agent |

### 6.4 ApprovalRecord

当前前端主要使用以下字段渲染审批卡片：

```json
{
  "approval_id": "appr_001",
  "status": "pending",
  "action": "service.resource.update",
  "proposal": {
    "summary": "将 mysql-xf2 的 mysql 子服务扩容到 8C16G。",
    "risk_level": "medium",
    "required_role": "admin",
    "execution_mode": "sync",
    "items": [
      {
        "action": "service.resource.update",
        "summary": "更新 mysql 子服务 CPU / 内存规格。",
        "risk_level": "medium",
        "execution_mode": "sync",
        "targets": [
          {
            "kind": "service",
            "id": "mysql-xf2",
            "name": "mysql-xf2",
            "qualifiers": {
              "child_service_type": "mysql"
            }
          }
        ],
        "parameters": [
          {
            "key": "cpu",
            "label": "CPU",
            "value": 8,
            "unit": "C",
            "current_value": 4,
            "current_unit": "C"
          }
        ],
        "risk_notes": []
      }
    ]
  },
  "created_at": "2026-04-22T12:10:03Z",
  "expires_at": "2026-04-22T12:40:03Z",
  "decided_at": null,
  "expired_at": null,
  "resume_failed": false,
  "resume_error": null
}
```

当前前端行为：

- `status=pending` 且本地判断未过期时显示批准/拒绝按钮。
- 如果 `expires_at <= Date.now()`，本地显示为已过期，并提示刷新同步。
- `proposal.summary` 作为审批标题。
- `proposal.risk_level` 显示为风险等级。
- `proposal.execution_mode` 显示为同步、异步或混合。
- `proposal.items[].targets` 显示目标资源。
- `proposal.items[].parameters` 显示变更参数。
- `proposal.items[].risk_notes` 显示风险提示。
- `resume_failed=true` 时显示 `resume_error`。

### 6.5 OperationRecord

当前前端主要使用以下字段展示操作结果卡片：

```json
{
  "operation_id": "op_001",
  "approval_id": "appr_001",
  "action": "service.resource.update",
  "execution_mode": "sync",
  "status": "succeeded",
  "result": {
    "status": "succeeded",
    "summary": "资源规格更新成功。",
    "changes": [
      {
        "field": "cpu",
        "label": "CPU",
        "before": 4,
        "after": 8,
        "unit": "C"
      }
    ],
    "error": null
  },
  "created_at": "2026-04-22T12:10:04Z",
  "started_at": "2026-04-22T12:10:05Z",
  "completed_at": "2026-04-22T12:10:06Z"
}
```

当前前端行为：

- 标题优先使用 `operation.result.summary`，否则使用 `operation.action`。
- 状态优先使用 `operation.result.status`，否则使用 `operation.status`。
- 变更明细使用 `operation.result.changes`。
- 错误信息使用 `operation.result.error.message || operation.result.error.error_type`。

### 6.6 TaskRecord

当前任务面板主要使用以下字段：

```json
{
  "task_id": "task-service-image-upgrade-mysql-xf2-mysql-a3f9c2",
  "operation_id": "op_002",
  "session_id": "sess_001",
  "action": "service.image.upgrade",
  "operation_conflict_key": "service.image.upgrade:mysql-xf2:mysql",
  "targets": [
    {
      "kind": "service",
      "id": "mysql-xf2",
      "name": "mysql-xf2",
      "qualifiers": {
        "child_service_type": "mysql"
      }
    }
  ],
  "dbaas_type": "service.image.upgrade",
  "status": "running",
  "source_status": "RUNNING",
  "message": "DBAAS 任务正在执行。",
  "reason": null,
  "result": null,
  "last_error": null,
  "terminal_notice_emitted": false,
  "created_at": "2026-04-22T12:10:10Z",
  "updated_at": "2026-04-22T12:10:20Z",
  "last_checked_at": "2026-04-22T12:10:20Z"
}
```

当前前端行为：

- 终态任务：`succeeded`、`failed`、`canceled`
- 非终态任务排在前面。
- 任务排序使用 `updated_at || last_checked_at || created_at` 倒序。
- 行标题使用 `action || dbaas_type || "异步任务"`。
- 说明文案优先级：`last_error || reason || message`。
- `result` 如果是对象，会按 key/value 列表展示。

## 7. 接口详情

### 7.1 获取前端配置

```http
GET /api/v1/config
```

返回：

```json
{
  "message_max_chars": 20000
}
```

当前前端只使用 `message_max_chars`。

### 7.2 获取 Session 列表

```http
GET /api/v1/sessions
```

返回见 `SessionListResponse`。

当前前端处理：

- 使用 `payload.items || []`
- 忽略 `stale_identity_items`
- 按 `last_message_at || updated_at` 倒序排序

### 7.3 创建 Session

```http
POST /api/v1/sessions
Content-Type: application/json
```

当前前端请求体固定为：

```json
{
  "title": null
}
```

返回：

```json
{
  "session": {
    "meta": {
      "session_id": "sess_003",
      "user_id": "payment_team",
      "role": "user",
      "thread_id": "thread_003",
      "title": "新建会话",
      "status": "active",
      "created_at": "2026-04-22T12:20:00Z",
      "updated_at": "2026-04-22T12:20:00Z",
      "last_message_at": null,
      "archived_at": null,
      "deleted_at": null
    },
    "messages": [],
    "approvals": [],
    "operations": []
  }
}
```

当前前端创建后会立即：

1. 重新调用 `GET /api/v1/sessions`
2. 调用 `GET /api/v1/sessions/{new_session_id}`
3. 调用 `GET /api/v1/sessions/{new_session_id}/tasks`

### 7.4 获取 Session 详情

```http
GET /api/v1/sessions/{session_id}
```

返回见 `SessionDetail`。

当前前端使用场景：

- 打开会话
- 任务终态后刷新会话
- 审批决策后 reconcile
- 发送消息异常后 reconcile

### 7.5 删除 Session

```http
DELETE /api/v1/sessions/{session_id}
```

返回：

```json
{
  "session_id": "sess_001",
  "deleted": true
}
```

当前前端行为：

- 删除前使用 `window.confirm()` 二次确认。
- 删除当前 Session 后，如果列表仍有其他 Session，会自动打开第一个。
- 如果没有其他 Session，会清空当前会话区。
- 如果删除失败，展示后端错误消息。

### 7.6 流式发送消息

```http
POST /api/v1/sessions/{session_id}/messages/stream
Accept: text/event-stream
Content-Type: application/json
```

请求：

```json
{
  "content": "查看 mysql-xf2 当前状态"
}
```

当前前端处理的 SSE 事件：

| 事件名 | 当前前端处理 |
| --- | --- |
| `user_message` | 用后端真实 user message 替换 optimistic user message |
| `token` | 拼接到 optimistic assistant message |
| `compression_started` | 仅当 payload 有 `system_message` 时插入；通常没有 |
| `compression_completed` | 如 payload 有 `system_message`，插入系统消息 |
| `approval.required` | 将审批 upsert 到当前 Session，并渲染审批卡 |
| `run.paused` | 当前前端忽略 |
| `error` | 若有 `ai_agent_message`，用它替换 optimistic assistant message；否则抛错 |
| `done` | 普通完成时替换消息；审批暂停时移除 optimistic assistant 并 upsert approval |

#### user_message

```text
event: user_message
data: {"user_message":{"message_id":"msg_010","role":"user","content":"查看 mysql-xf2 当前状态","created_at":"2026-04-22T12:10:01Z"}}
```

#### token

```text
event: token
data: {"run_id":"run_001","mode":"deepagent","delta":"mysql-xf2 当前","warning":null}
```

#### compression_completed

```text
event: compression_completed
data: {"run_id":"run_001","mode":"deepagent","message":"上下文已自动压缩，本会话会继续使用同一个 Session。","system_message":{"message_id":"msg_020","role":"system","content":"上下文已自动压缩，本会话会继续使用同一个 Session。","created_at":"2026-04-22T12:10:03Z"},"details":{"phase":"completed","summarized_messages":8,"summary_chars":1200}}
```

当前前端只使用 `system_message`，不展示 `details`。

#### approval.required

```text
event: approval.required
data: {"run_id":"run_002","approval":{"approval_id":"appr_001","status":"pending","action":"service.resource.update","proposal":{"summary":"将 mysql-xf2 的 mysql 子服务扩容到 8C16G。","risk_level":"medium","required_role":"admin","execution_mode":"sync","items":[]}}}
```

#### error

```text
event: error
data: {"detail":"DBAAS 控制面请求失败。","error_type":"dbaas_request_failed","stage":"tool_call","ai_agent_message":{"message_id":"msg_012","role":"ai-agent","content":"本轮 AI Agent 调用失败：DBAAS 控制面请求失败。","created_at":"2026-04-22T12:10:03Z"},"request_id":"req_001","run_id":"run_001"}
```

当前前端：

- 优先使用 `payload.ai_agent_message` 替换 optimistic assistant message。
- 使用 `detail` 和 `stage` 显示 flash 错误。
- 如果没有 `ai_agent_message`，抛出异常，外层会把 optimistic assistant message 标记为错误。

#### done：普通完成

```text
event: done
data: {"session":{"session_id":"sess_001","user_id":"payment_team","role":"user","thread_id":"thread_001","title":"排查 mysql-xf2","status":"active","created_at":"2026-04-22T12:00:00Z","updated_at":"2026-04-22T12:10:02Z","last_message_at":"2026-04-22T12:10:02Z","archived_at":null,"deleted_at":null},"user_message":{"message_id":"msg_010","role":"user","content":"查看 mysql-xf2 当前状态","created_at":"2026-04-22T12:10:01Z"},"assistant_message":{"message_id":"msg_011","role":"assistant","content":"mysql-xf2 当前健康状态为 warning。","created_at":"2026-04-22T12:10:02Z"},"run_id":"run_001","mode":"deepagent","warning":null}
```

#### done：审批暂停

```text
event: done
data: {"session":{"session_id":"sess_001","status":"active"},"user_message":{"message_id":"msg_010","role":"user","content":"把 mysql-xf2 的 mysql 扩到 8C16G","created_at":"2026-04-22T12:10:01Z"},"assistant_message":null,"approval":{"approval_id":"appr_001","status":"pending","action":"service.resource.update"},"run_id":"run_002","mode":"deepagent","warning":null,"paused":true}
```

当前前端看到 `payload.paused || payload.approval` 会走审批暂停分支：

- 替换 optimistic user message
- 移除 optimistic assistant message
- upsert `payload.approval`
- 更新 Session 列表 preview 为审批摘要

### 7.7 提交审批决策

```http
POST /api/v1/sessions/{session_id}/approvals/{approval_id}/decision
Content-Type: application/json
```

请求：

```json
{
  "decision": "approved"
}
```

当前前端按钮只会提交：

- `approved`
- `rejected`

返回：

```json
{
  "approval": {
    "approval_id": "appr_001",
    "status": "approved",
    "action": "service.resource.update",
    "decided_by": "payment_team",
    "decided_at": "2026-04-22T12:11:00Z"
  },
  "assistant_message": {
    "message_id": "msg_011",
    "role": "assistant",
    "content": "已完成资源规格更新。",
    "created_at": "2026-04-22T12:11:03Z"
  },
  "system_message": null,
  "operations": [],
  "tasks": [],
  "next_approval": null,
  "paused": false,
  "run_id": "run_002",
  "mode": "deepagent"
}
```

当前前端实际使用：

| 字段 | 用途 |
| --- | --- |
| `system_message` | 如果存在，先插入消息区 |
| `next_approval` | 决定 flash 文案是否提示“后续操作等待确认” |

虽然响应中包含 `approval`、`assistant_message`、`operations`、`tasks`，当前前端提交决策后会调用 `reconcileCurrentSession()` 重新拉取会话详情和任务列表，因此最终页面状态以重新拉取结果为准。

### 7.8 查询 Session 任务

```http
GET /api/v1/sessions/{session_id}/tasks
```

返回：

```json
{
  "items": [
    {
      "task_id": "task-service-image-upgrade-mysql-xf2-mysql-a3f9c2",
      "operation_id": "op_002",
      "session_id": "sess_001",
      "action": "service.image.upgrade",
      "operation_conflict_key": "service.image.upgrade:mysql-xf2:mysql",
      "targets": [],
      "dbaas_type": "service.image.upgrade",
      "status": "running",
      "source_status": "RUNNING",
      "message": "DBAAS 任务正在执行。",
      "reason": null,
      "result": null,
      "last_error": null,
      "terminal_notice_emitted": false,
      "created_at": "2026-04-22T12:10:10Z",
      "updated_at": "2026-04-22T12:10:20Z",
      "last_checked_at": "2026-04-22T12:10:20Z"
    }
  ]
}
```

当前前端会将 `items` 排序后展示在任务面板。

### 7.9 订阅 Session 任务事件

```http
GET /api/v1/sessions/{session_id}/tasks/events
Accept: text/event-stream
```

当前前端处理两个事件。

#### task_status_changed

```text
event: task_status_changed
data: {"session_id":"sess_001","task":{"task_id":"task_001","status":"succeeded","previous_status":"running","source_status":"SUCCESS","message":"任务执行成功。"}}
```

当前前端行为：

- 要求 `payload.session_id === 当前 sessionId`
- upsert `payload.task`
- 重新渲染任务面板
- 如果任务进入终态，显示 flash，并调用 `GET /api/v1/sessions/{session_id}` 刷新当前 Session 详情

#### task_terminal_notice_emitted

```text
event: task_terminal_notice_emitted
data: {"session_id":"sess_001","group_key":"approval:appr_001","tasks":[{"task_id":"task_001","status":"succeeded","terminal_notice_emitted":true}],"system_message":{"message_id":"msg_021","role":"system","content":"本次审批确认关联的异步任务 task_001 已成功。","created_at":"2026-04-22T12:12:00Z"}}
```

当前前端行为：

- upsert `payload.tasks`
- 如果有 `payload.system_message`，插入消息区
- 如果没有 `payload.system_message`，调用 `reconcileCurrentSession()` 重新同步

## 8. 当前页面状态与接口关系

| 前端状态 | 数据来源 |
| --- | --- |
| `state.config.messageMaxChars` | `GET /api/v1/config.message_max_chars` |
| `state.sessions` | `GET /api/v1/sessions.items` |
| `state.currentSession` | `GET /api/v1/sessions/{session_id}.session` |
| `state.currentTasks` | `GET /api/v1/sessions/{session_id}/tasks.items` 和 task SSE |
| `state.sending` | 本地发送状态 |
| `state.decidingApprovalIds` | 本地审批按钮防重复状态 |
| `state.taskEventsController` | 本地任务 SSE AbortController |
