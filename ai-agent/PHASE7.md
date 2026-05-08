# DBAAS 智能助手第七阶段设计讨论

## 1. 当前阶段目标

第七阶段开始支持 DBAAS 服务变更操作。

本阶段范围包括：

- 服务资源规格更新，例如 CPU、内存扩容或缩容
- 服务存储规格更新，例如 data/log 卷扩容
- 服务镜像升级
- 服务生命周期操作预留，例如启动、停止、重启
- 异步任务创建和后续状态查询
- 所有写操作执行前的人工确认
- 操作记录、审批记录和异步任务记录的可审计持久化

本阶段继续基于 DeepAgent 框架已有能力实现：

- Agent 运行时
- tool calling
- thread 上下文延续
- streaming
- human-in-the-loop interrupt/resume
- checkpoint
- 上下文压缩

不在项目侧重复自造 Agent runtime、会话恢复机制或工具调用链路。

## 2. 核心结论

第七阶段建议采用“统一操作模型”。

Agent 层只负责：

- 理解用户目标
- 查询必要的 DBAAS 当前状态
- 提出变更操作
- 解释操作对象、影响范围和风险
- 调用受控 DBAAS 写工具
- 根据工具结果向用户说明执行结果

项目层负责：

- 写工具统一注册
- 写工具执行前人工确认
- 审批记录持久化
- 本地重复执行保护
- 操作记录和任务记录持久化
- 同步操作和异步操作的统一结果表达
- 对外 SSE 事件封装

DBAAS 工具层负责：

- 校验参数
- 调用 DBAAS 控制面
- 将同步结果或异步任务引用转换成统一 `OperationResult`
- 保存必要的 changes、task_id 和错误信息

也就是说，同步和异步不应拆成两套 Agent 逻辑。

对于 Agent 来说，升级、扩容、启停都是“受控操作工具”；
同步或异步只是工具返回结果中的一个属性。

## 3. 当前 mock-server 能力

当前相邻 `mock-server` 已具备的服务写接口包括：

```text
PUT  /services/{name}/resource
PUT  /services/{name}/storage
POST /services/{name}/image-upgrade
GET  /tasks/{task_id}
```

其中：

- `resource` 更新是同步操作，返回更新后的服务详情
- `storage` 更新是同步操作，返回更新后的服务详情
- `image-upgrade` 是异步操作，返回 `taskId`
- `tasks/{task_id}` 用于查询异步任务状态

第七阶段第一版可以优先接入这几类能力。

服务启动、停止、重启如果 mock-server 暂未提供接口，可以先完成工具和模型设计，
待 DBAAS 控制面接口稳定后再接入真实执行。

## 4. 统一操作模型

### 4.1 OperationProposal

写工具真正执行前，系统应能表达一个待审批的操作提案。
第七阶段建议把它定义为通用的“审批展示载荷”，而不是服务专属模型。

`OperationProposal` 不由 AI 自由生成。
AI 只生成 tool call；后端根据被 DeepAgent interrupt 拦截到的 tool call、
当前身份、action 配置和风险配置生成 proposal。

Proposal 是给用户和前端看的展示结构，不包含原始 `tool_name` 和 `tool_args`。
原始工具调用记录保存在 `ApprovalRecord.interrupted_tool_call`。

建议字段：

```json
{
  "action": "service.resource.update",
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
  "summary": "将 mysql-xf2/mysql 内存调整为 15GB",
  "risk_level": "medium",
  "required_role": "user",
  "execution_mode": "sync",
  "parameters": [
    {
      "key": "memory",
      "label": "内存",
      "current_value": 8,
      "current_unit": "GB",
      "value": 15,
      "unit": "GB"
    }
  ]
}
```

后续主机或集群操作也复用同一结构：

```json
{
  "action": "host.lifecycle.reboot",
  "targets": [
    {
      "kind": "host",
      "id": "host-001",
      "name": "db-host-001"
    }
  ],
  "summary": "重启主机 db-host-001",
  "risk_level": "critical",
  "required_role": "admin",
  "execution_mode": "async",
  "parameters": []
}
```

设计约束：

- `targets` 支持 service、cluster、host 等资源，也支持一个操作影响多个对象
- service 特有字段放入 `targets[].qualifiers`，例如 `child_service_type`
- `parameters[]` 是从原始工具参数派生出的可展示参数，不是执行参数
- `parameters[].current_value/current_unit` 是可选展示字段，用于审批卡展示 `8GB -> 15GB`
- 如果当前值无法可靠获取，可以不填 current 字段，不允许 AI 编造当前值
- `required_role` 用于在创建 approval 前做权限校验
- `interrupted_tool_call.tool_args` 才是被审批的原始工具参数
- P7A 不实现独立 `OperationProposal` 存储或 API
- P7A 中 proposal 只作为 `ApprovalRecord` 的展示字段保存

### 4.2 OperationResult

所有写工具返回统一结构。
它由工具代码根据 DBAAS API 返回、执行状态、任务引用和本地记录生成，
不由 AI 自由生成。

同步操作示例：

```json
{
  "operation_id": "op_xxx",
  "approval_id": "appr_xxx",
  "action": "service.resource.update",
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
  "execution_mode": "sync",
  "status": "succeeded",
  "summary": "已将 mysql-xf2/mysql 内存调整为 15GB。",
  "task": null,
  "changes": [
    {
      "target": {
        "kind": "service",
        "id": "mysql-xf2",
        "name": "mysql-xf2",
        "qualifiers": {
          "child_service_type": "mysql"
        }
      },
      "field": "memory",
      "label": "内存",
      "before": 8,
      "after": 15,
      "unit": "GB",
      "change_type": "increase"
    }
  ],
  "error": null,
  "details": {
    "before_snapshot": {},
    "after_snapshot": {}
  }
}
```

异步操作示例：

```json
{
  "operation_id": "op_xxx",
  "approval_id": "appr_xxx",
  "action": "service.image.upgrade",
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
  "execution_mode": "async",
  "status": "task_created",
  "summary": "已创建 mysql-xf2/mysql 镜像升级任务 task-0001。",
  "task": {
    "task_id": "task-0001",
    "type": "service.image.upgrade",
    "status": "running"
  },
  "changes": [],
  "error": null,
  "details": {}
}
```

失败示例：

```json
{
  "operation_id": "op_xxx",
  "action": "service.resource.update",
  "execution_mode": "sync",
  "status": "failed",
  "summary": "服务资源规格更新失败。",
  "task": null,
  "changes": [],
  "error": {
    "error_type": "dbaas_request_failed",
    "message": "DBAAS 控制面返回错误：service 'mysql-xf2' not found"
  },
  "details": {}
}
```

超时示例：

```json
{
  "operation_id": "op_xxx",
  "action": "service.resource.update",
  "execution_mode": "sync",
  "status": "timeout",
  "summary": "服务资源规格更新请求超时，当前无法确认是否已生效。",
  "task": null,
  "changes": [],
  "error": {
    "error_type": "dbaas_timeout",
    "message": "DBAAS 控制面在 30 秒内未返回结果。"
  },
  "details": {
    "timeout_seconds": 30,
    "reconcile_required": true
  }
}
```

`changes[]` 是面向用户和前端的变更列表。
每一项表达一个目标对象上的一个字段从什么值变成什么值。

示例：

```text
mysql-xf2/mysql
内存：8GB -> 15GB
变化：扩容
```

如果需要完整审计，工具可以把原始快照放入 `details.before_snapshot` 和
`details.after_snapshot`。
普通前端不应强依赖 `details` 中的原始 DBAAS response。

### 4.3 数据生成责任

第七阶段的结构化数据生成边界如下：

```text
AI：生成 tool call，决定调用哪个工具和参数
DeepAgent：通过 interrupt_on 在写工具执行前暂停
后端：记录 interrupted_tool_call，并派生 OperationProposal/ApprovalRecord
用户：批准或拒绝
后端：通过 Command(resume=...) 恢复同一个 thread
工具：真实执行 DBAAS 操作并生成 OperationResult
AI：根据 OperationResult 解释执行结果
```

这样可以保证：

- 审批展示由 `proposal` 提供，真实被审批的原始工具调用记录在 `interrupted_tool_call`
- 权限、风险等级和执行模式由代码控制
- 工具结果以 DBAAS 返回为准
- 后端不通过 `interrupted_tool_call` 直接调用 DBAAS
- AI 不负责判断操作是否真的成功

### 4.4 action 命名

建议第一版使用稳定的 action 名称：

```text
service.resource.update
service.storage.update
service.image.upgrade
service.lifecycle.change
task.status.get
```

工具名面向模型和 DeepAgent：

```text
update_service_resource_tool
update_service_storage_tool
create_service_image_upgrade_task_tool
get_dbaas_task_tool
change_service_lifecycle_tool
```

action 名面向审计、事件和长期兼容。

## 5. 人工确认设计

### 5.1 总体链路

所有写操作必须先人工确认，再执行 DBAAS 写接口。

推荐链路：

```text
用户提出变更请求
-> Agent 查询必要上下文
-> Agent 生成写工具调用
-> DeepAgent interrupt_on 拦截写工具
-> 后端捕获 interrupt
-> 创建 approval 记录
-> SSE 发送 approval.required
-> 前端展示确认面板
-> 用户批准或拒绝
-> 后端更新 approval 记录
-> 使用 Command(resume=...) 恢复同一个 thread
-> 批准时工具真正执行，拒绝时模型收到拒绝结果
-> SSE/消息返回最终结果
```

确认发生在工具执行前。
前端审批接口不直接调用 DBAAS。
DBAAS 写入只能发生在受控工具内部。

### 5.2 DeepAgent interrupt_on

第七阶段应在创建 DeepAgent 时配置写工具拦截。

示例：

```python
interrupt_on={
    "update_service_resource_tool": {
        "allowed_decisions": ["approve", "reject"],
        "description": format_operation_approval,
    },
    "update_service_storage_tool": {
        "allowed_decisions": ["approve", "reject"],
        "description": format_operation_approval,
    },
    "create_service_image_upgrade_task_tool": {
        "allowed_decisions": ["approve", "reject"],
        "description": format_operation_approval,
    },
    "change_service_lifecycle_tool": {
        "allowed_decisions": ["approve", "reject"],
        "description": format_operation_approval,
    },
}
```

第一版建议只支持：

```text
approve
reject
```

暂不支持 `edit`。

原因是编辑参数会引入：

- 前端参数编辑 UI
- 参数 schema 校验
- 风险说明重新计算
- 当前资源状态重新确认
- 审计记录中原始参数和编辑后参数的双轨保存

这些能力可以放到后续阶段。

### 5.3 ApprovalRecord

现有 `ApprovalRecord` 只有 `approval_id`、`status`、`action` 和 `created_at`，
第七阶段需要扩展成可恢复、可审计的结构。

建议字段：

```json
{
  "approval_id": "appr_xxx",
  "status": "pending",
  "session_id": "sess_xxx",
  "thread_id": "thread_xxx",
  "run_id": "run_xxx",
  "request_message_id": "msg_user_xxx",
  "proposal": {
    "action": "service.resource.update",
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
    "summary": "将 mysql-xf2/mysql 内存调整为 15GB",
    "risk_level": "medium",
    "required_role": "user",
    "execution_mode": "sync",
    "parameters": [
      {
        "key": "memory",
        "label": "内存",
        "value": 15,
        "unit": "GB"
      }
    ],
    "risk_notes": [
      "会变更该子服务下所有 unit 的资源规格"
    ]
  },
  "interrupted_tool_call": {
    "tool_call_id": "call_xxx",
    "tool_name": "update_service_resource_tool",
    "tool_args": {
      "service_name": "mysql-xf2",
      "child_service_type": "mysql",
      "memory": 15
    }
  },
  "allowed_decisions": ["approve", "reject"],
  "decided_by": null,
  "created_at": "2026-05-06T10:00:00Z",
  "expires_at": "2026-05-06T10:30:00Z",
  "decided_at": null,
  "expired_at": null,
  "resume_failed": false,
  "resume_error": null,
  "resume_last_attempt_at": null
}
```

审批完成后：

```json
{
  "status": "approved",
  "decided_by": "admin",
  "decided_at": "2026-05-06T10:01:00Z"
}
```

### 5.4 审批决策接口

建议第七阶段第一版使用 session-scoped 审批决策接口：

```http
POST /api/v1/sessions/{session_id}/approvals/{approval_id}/decision
```

相比全局 `approval_id` 决策接口，带上 `session_id` 更利于多 Session 下做权限校验，
也能避免错误 resume 到其他 Session 的 `thread_id`。

请求体：

```json
{
  "decision": "approved"
}
```

或：

```json
{
  "decision": "rejected"
}
```

P7A 不设计审批备注字段，审批决策接口不要求 `comment`。
后续如果需要审批备注，再扩展请求体和 `ApprovalRecord`。

审批操作者规则：

- 批准或拒绝者必须能访问当前 Session
- 批准或拒绝者必须满足 `proposal.required_role`
- `required_role=user` 表示 Session 可访问用户和 admin 都可以决策
- `required_role=admin` 表示只有 admin 可以决策
- P7A 不做独立审批人模型或多级审批流

审批状态命名需要区分外部记录和 DeepAgent resume payload：

```text
ApprovalRecord.status / API decision: approved / rejected
Command(resume) decision type: approve / reject
```

代码里不要混用这两组值。

后端处理步骤：

1. 根据 `session_id` 加载当前用户可访问的 Session
2. 在该 Session 的审批记录中查找 `approval_id`
3. 校验当前用户满足审批操作者规则
4. 检查审批是否已过期，过期则标记为 `expired` 并拒绝本次决策
5. 校验审批状态仍为 `pending`
6. 更新审批记录为 `approved` 或 `rejected`
7. 使用 approval 记录中的 `thread_id` 恢复同一个 DeepAgent thread
8. 将恢复后的运行事件继续写入当前 run/session 事件流

同一 Session 内重复点击确认/拒绝的语义：

- 只有 `pending` approval 可以进入第一次决策
- 第一次从 `pending` 更新为 `approved` 或 `rejected` 后，才允许执行 `Command(resume=...)`
- 已经是 `approved/rejected/expired` 的 approval，后续提交不再执行 `Command(resume=...)`
- 如果重复提交的 decision 与当前终态一致，直接返回当前 approval 状态
- 如果重复提交的 decision 与当前终态冲突，返回 `409 Conflict`
- 冲突错误建议使用 `error_type=decision_conflict`

示例：

```text
Tab A: pending -> approved，执行 Command(resume=approve)
Tab B: 再提交 approved，返回当前 approved，不再 resume
Tab C: 再提交 rejected，返回 409 decision_conflict，不再 resume
```

如果 approval decision 已落盘，但 `Command(resume=...)` 失败：

- approval 不改回 `pending`
- 记录 `resume_failed=true`
- 记录 `resume_error`
- 记录 `resume_last_attempt_at`
- 相同 decision 的重复提交，如果 `resume_failed=true`，允许重试 `Command(resume=...)`
- 冲突 decision 仍然返回 `409 decision_conflict`
- resume 成功后清理 `resume_failed/resume_error/resume_last_attempt_at`
- 如果已经存在该 approval 触发的 operation 记录，不盲目重复 resume
- 已有 operation 时，应返回当前 operation 状态，或提示需要 reconcile

这样可以避免“用户已经批准或拒绝，但 DeepAgent 暂停点没有恢复成功”时，
把审批状态回滚或重复执行写操作。

批准时恢复 payload：

```python
from langgraph.types import Command

Command(
    resume={
        "decisions": [
            {"type": "approve"}
        ]
    }
)
```

拒绝时恢复 payload：

```python
Command(
    resume={
        "decisions": [
            {
                "type": "reject",
                "message": "用户拒绝执行该操作。"
            }
        ]
    }
)
```

### 5.5 审批超时

第七阶段第一版需要支持审批超时自动取消。

审批状态建议包括：

```text
pending
approved
rejected
expired
```

超时不是自动批准。
超时后系统应保证不执行 DBAAS 写操作。

推荐行为：

1. 创建 approval 时写入 `expires_at`
2. 到期后 approval 不再允许批准
3. 后端将 approval 标记为 `expired`
4. 不调用 DBAAS 写接口
5. 使用 DeepAgent `Command(resume=...)` 注入 reject，清理 interrupt 暂停点

超时恢复 payload：

```python
Command(
    resume={
        "decisions": [
            {
                "type": "reject",
                "message": "审批超时，操作已自动取消，未执行 DBAAS 变更。"
            }
        ]
    }
)
```

TTL 第一版建议：

```text
普通写操作：30 分钟或 1 小时
高风险操作：15 分钟
```

具体值后续配置化。

第一版可以采用 lazy expiration，不必先实现后台定时任务。

触发检查的入口：

- 打开 Session 时
- 发新消息前
- 查询 approvals 时
- 提交 approval decision 前
- 归档或删除 Session 前

如果检查发现 approval 过期，就标记 `expired` 并 resume reject。
这样可以避免用户忘记处理确认，导致 Session 长期卡在 `pending approval`。

过期处理中的 DeepAgent resume 是清理暂停点，不是执行 DBAAS 操作。
建议采用保守顺序：

```text
1. 先把 approval 标记为 expired
2. 再 best-effort Command(resume=reject)
3. 如果 resume 失败，记录 resume_failed=true 和 resume_error
4. 下次查询 approvals 或发新消息前继续尝试 resume reject
```

`resume_failed` 不应把 approval 改回 `pending`，
也不应调用 DBAAS 写接口。
如果发新消息前重试 resume 仍失败，返回 `409 Conflict` 或 `503`，
提示当前 Session 的过期审批暂停点尚未清理完成。

### 5.6 前端确认面板

前端收到 `approval.required` 后展示固定确认面板。

面板至少展示：

- 操作摘要
- 操作对象
- 关键参数
- 风险等级
- 风险说明
- 过期时间或倒计时
- 批准按钮
- 拒绝按钮

按钮只调用审批决策接口。
前端不拼 DBAAS 控制面请求，也不绕过后端工具执行。

### 5.7 pending approval 时的会话行为

当前 Session 存在 `pending approval` 时，表示 DeepAgent thread 已暂停在
human-in-the-loop interrupt 点。
P7A 采用简单规则：该 Session 暂停继续发新消息。

处理方式：

```text
POST /api/v1/sessions/{session_id}/messages
POST /api/v1/sessions/{session_id}/messages/stream
-> 有 pending approval: 409 Conflict
```

`pending approval` 只阻止继续发新消息，不阻止以下接口：

```text
GET approvals
POST approval decision
GET tasks
GET tasks/events
archive/delete 的未结束事项检查
```

前端应禁用输入框，或提示用户先批准、拒绝，或等待审批超时自动取消。
approval 变为 `approved`、`rejected` 或 `expired` 后，
该 Session 才允许继续发新消息。

发新消息前仍要先执行 approval lazy expiration。
如果 pending approval 已过期，应先标记为 `expired` 并通过 reject resume 清理暂停点，
再判断是否仍有 `pending approval`。

### 5.8 session_run_lock

`session_run_lock` 表示同一个 Session 的 DeepAgent 运行互斥锁。
同一个 Session 同一时间只允许一个 DeepAgent run 或 resume。

需要持有 `session_run_lock` 的场景：

- 一次 `/messages` 或 `/messages/stream` 正在调用 DeepAgent
- 一次 approval decision 正在 `Command(resume=...)` 恢复同一个 thread

这样做是为了保护同一个 `thread_id` 的 DeepAgent checkpoint，
避免两个并发请求同时推进同一个 Session 的运行时状态。

处理方式：

```text
POST /api/v1/sessions/{session_id}/messages
POST /api/v1/sessions/{session_id}/messages/stream
-> session_run_lock 已被占用: 409 Conflict
```

approval decision 恢复执行时也需要占用同一个 Session 的 `session_run_lock`。
如果同一个 approval 被重复提交，应优先按 approval 最新状态做幂等返回，
不能重复执行已批准的写操作。

异步 DBAAS task 不需要持有 `session_run_lock`。
task 创建成功后，即使 DBAAS 任务仍在运行，也不阻止用户在同一 Session 继续发起其他无冲突请求。

部署假设：

- P7A 默认按单进程后端实现
- `session_run_lock` 可以先使用进程内 Session 级锁
- 单进程内的文件 append 仍使用 Session 级文件锁
- 如果后续使用多 worker 或多实例部署，`session_run_lock` 需要升级为文件锁、数据库锁或分布式锁
- 多 worker / 多实例不是 P7A 范围

锁顺序：

```text
1. 获取 session_run_lock
2. 需要写状态时，短暂获取 Session 文件锁并 append
3. 释放 Session 文件锁
4. 执行 DeepAgent run/resume 或 DBAAS HTTP
5. 需要写结果时，再短暂获取 Session 文件锁并 append
6. 释放 Session 文件锁
7. 释放 session_run_lock
```

约束：

- `session_run_lock` 保护同一个 Session 的 DeepAgent run/resume
- Session 文件锁只保护 append 写入
- 不在 Session 文件锁内执行 DeepAgent 调用
- 不在 Session 文件锁内执行 DBAAS HTTP
- 不在 Session 文件锁内等待 SSE 推送
- 不要把 `session_run_lock` 和 Session 文件锁混成同一把长锁

## 6. 同步操作处理

同步操作包括：

- `service.resource.update`
- `service.storage.update`

同步操作定义为 DBAAS 接口在一次 HTTP 调用内返回最终业务结果。
同步不代表无限等待，所有同步写操作都必须有配置化 timeout。

timeout 是系统执行策略，不作为 AI tool call 参数暴露。
第一版采用简单规则：

```text
tool 配置了 timeout_seconds -> 使用 tool timeout_seconds
tool 没有配置 timeout_seconds -> 使用现有 dbaas_request_timeout_seconds
```

示例：

```text
update_service_resource_tool timeout_seconds = 30
update_service_storage_tool timeout_seconds = 45
get_dbaas_task_tool 未配置，使用 dbaas_request_timeout_seconds
```

`dbaas_request_timeout_seconds` 已在配置文件中存在，作为 DBAAS HTTP 请求默认超时。
如果某类操作经常需要较长等待，应优先建模为异步任务，而不是依赖很长的同步 HTTP 等待。

推荐执行流程：

1. 根据用户请求和上下文确认服务名、子服务类型和目标参数
2. 必要时调用只读查询工具获取当前服务详情
3. 生成写工具调用
4. DeepAgent interrupt_on 触发审批
5. 用户批准后执行写工具
6. 写工具执行前读取必要的当前状态
7. 调用 DBAAS 写接口
8. 写工具执行后生成 `changes[]`
9. 返回统一 `OperationResult`
10. Agent 向用户说明变更结果和关键 changes

同步操作不应只返回“成功”。

资源更新应至少说明：

- 服务名
- 子服务类型
- 影响的 unit 数量或 unit 名称
- CPU/内存变更前后

存储更新应至少说明：

- 服务名
- 子服务类型
- 影响的 unit 数量或 unit 名称
- data/log 卷大小变更前后

### 6.1 同步写超时处理

同步写接口超时后，工具应返回 `OperationResult.status=timeout`。

超时语义：

- 超时不等于成功
- 超时也不等于失败
- 请求可能已经到达 DBAAS 控制面
- 不应自动重试写接口，避免重复变更

超时后处理：

1. 写入 `operations.json`
2. 设置 `result.error.error_type=dbaas_timeout`
3. 设置 `result.details.reconcile_required=true`
4. 提示用户当前状态未知
5. 后续如需确认结果，通过只读查询做 reconcile

第一版不自动重试写操作。
如果需要再次执行，应由用户重新发起明确操作，并重新经过审批。

P7A 不做自动 reconcile。
`timeout` 或 `unknown/reconcile_required` 只表示当前操作结果需要人工或只读查询确认。
系统可以提示用户查询当前资源状态，但不自动补偿、不自动重试、
不自动判断写操作最终成功。

### 6.2 ai-agent 重启恢复语义

同步写工具真正调用 DBAAS 前，应先写入一条 `operations.json` 记录：

```json
{
  "operation_id": "op_xxx",
  "status": "started",
  "result": null,
  "created_at": "2026-05-06T10:00:00Z",
  "started_at": "2026-05-06T10:01:00Z",
  "completed_at": null
}
```

如果 ai-agent 在同步写操作完成前重启，
重启后可能无法判断 DBAAS 是否已经收到或执行该请求。

P7A 采用简单保守规则：

```text
started -> unknown
result.details.reconcile_required = true
result.error.error_type = operation_interrupted
```

恢复后的 `OperationResult` 示例：

```json
{
  "operation_id": "op_xxx",
  "execution_mode": "sync",
  "status": "unknown",
  "summary": "操作在 AI Agent 服务重启期间中断，当前无法确认是否已生效。",
  "task": null,
  "changes": [],
  "error": {
    "error_type": "operation_interrupted",
    "message": "AI Agent 在同步写操作完成前重启。"
  },
  "details": {
    "reconcile_required": true
  }
}
```

重启恢复时不自动重放写接口。
前端应展示“状态未知，建议查询当前资源状态”。
如果用户需要再次执行，应重新发起操作并重新经过审批。

## 7. 异步操作处理

异步操作包括：

- `service.image.upgrade`
- 后续可能的启停、重启、迁移、备份恢复等长任务

推荐执行流程：

1. 根据用户请求确认服务名、子服务类型、目标镜像、版本和 unit 范围
2. 必要时查询当前服务详情和 unit 列表
3. 生成写工具调用
4. DeepAgent interrupt_on 触发审批
5. 用户批准后执行写工具
6. 写工具调用 DBAAS 创建任务
7. DBAAS 返回 `taskId`
8. 写工具保存当前 Session 的 `tasks.json` 记录
9. 返回 `execution_mode=async` 的 `OperationResult`
10. Agent 告知用户任务已创建、task_id、当前状态和后续查询方式

异步任务创建后，当前消息流不应一直阻塞等待任务完成。

P7A 的异步边界只到“创建 task 成功并保存 task 引用”：

```text
P7A 做：创建 task、写 tasks.json、返回 task_id
P7B 做：GET /api/v1/sessions/{session_id}/tasks lazy refresh、task SSE、任务下拉框/任务面板
```

如果 P7A 和 P7B 不连续上线，不建议单独开放会长期运行的异步写操作。
原因是 P7A 如果只创建 task 而没有任何 task lazy refresh，
`tasks.json` 中的任务可能长期停留在 `running`，从而持续阻止 Session 归档或删除。

因此实现节奏建议：

- 要么 P7A 与 P7B 紧挨着交付
- 要么 P7A 暂不开放异步写工具
- 要么 P7A 带一个最小 `GET /api/v1/sessions/{session_id}/tasks` lazy refresh

第一版推荐 P7A 与 P7B 紧挨着交付，避免把任务追踪拆出太久。

同一个 Session 允许存在多个异步任务。
这和人工确认不同：同一个 Session 同一时间只允许一个 `pending approval`，
但已经创建成功的异步任务不阻塞用户继续发起其他无冲突操作。

创建新的异步任务前，应检查当前 Session 内是否已有同一 `operation_conflict_key`
的非终态任务。
如果存在，应返回冲突并把已有任务返回给前端展示，
避免用户对同一个服务、集群或主机重复提交同类长任务。

冲突判断使用稳定的 `operation_conflict_key`，避免因为字段顺序或名称变化漏判。
第一版规则：

```text
operation_conflict_key =
  action + "|" + sorted(target.kind + ":" + target.id + ":" + sorted(qualifiers))
```

`targets[].name` 只用于展示，不参与冲突 key。
同一 `operation_conflict_key` 的非终态任务存在时，新建同类任务返回 `409 Conflict`。

ai-agent 重启恢复语义：

- 如果 `task_id` 已经返回并写入 `tasks.json`，重启后从 `tasks.json` 恢复任务引用，继续通过 `GET /tasks/{task_id}` 查询状态
- 如果 ai-agent 在提交异步任务请求过程中重启，且本地尚未写入 `task_id`，则将 operation 标记为 `unknown/reconcile_required`
- 对于本地没有 `task_id` 的 `unknown` operation，不自动重试创建任务请求，避免重复创建任务

也就是说，`operation.status=task_created` 表示异步任务提交成功；
任务本身是否完成，以 DBAAS `GET /tasks/{task_id}` 返回为准。
本地 `tasks.json` 保存任务引用、上下文和 last known status，
用于当前 Session 展示和 DBAAS 不可用时的兜底展示。

异步任务创建成功后，`operations.json` 中对应 operation 保持 `task_created`。
任务后续的 `running/succeeded/failed/canceled/refresh_failed` 状态只写入 `tasks.json`。
前端展示时通过 `operation.result.task.task_id` 或 `operation_id` 关联 operation 和 task，
不要把 task 最终状态再反写到 operation，避免两份状态不一致。

后续任务状态查询通过以下方式实现：

- 用户自然语言请求，例如“查一下刚才升级任务进度”
- `get_dbaas_task_tool(task_id)`
- `GET /api/v1/sessions/{session_id}/tasks` lazy refresh
- 当前 Session 页面打开时的任务 SSE

P7B 不做全局 task watcher，不扫描所有 Session 文件，
也不维护跨 Session 任务中心。
任务追踪只服务当前 Session。

本阶段不新增全局任务列表接口，例如：

```http
GET /api/v1/tasks
GET /api/v1/users/{user_id}/tasks
```

DBAAS 的 `task_id` 可以是全局唯一标识，
但 ai-agent 只通过当前 Session 下的 `tasks.json` 记录该任务和会话的关系。
因此任务查询、任务刷新和任务 SSE 都必须带 `session_id`。

刷新触发点：

```text
1. 异步任务创建成功
   -> 写当前 Session 的 tasks.json

2. 打开或刷新 Session 任务面板
   -> GET /api/v1/sessions/{session_id}/tasks
   -> lazy refresh 当前 Session 的非终态任务

3. 当前 Session 建立 task SSE
   -> 只对这个 Session 的非终态任务做周期 refresh
   -> 状态变化时推 task_status_changed
   -> 页面关闭或 SSE 断开后停止该 Session 的 refresh loop
```

建议配置：

```text
dbaas_task_refresh_interval_seconds = 10 或 30
```

SSE 订阅期间的 refresh loop 可以使用单进程内 asyncio task。
每个 Session 同一时间最多启动一个 refresh loop，
避免同一个页面多次订阅或多标签页导致重复刷新。

DBAAS 查询失败时：

- 不把任务标记为 failed
- 标记 `refresh_failed`
- 记录 `last_error`
- 下次 lazy refresh 或 SSE refresh loop 继续刷新

任务刷新只更新任务状态，不自动唤醒 AI，也不自动写 assistant 消息。
前端任务面板或用户自然语言查询读取本地最新状态，
必要时重新调用 `GET /api/v1/sessions/{session_id}/tasks` 触发 lazy refresh。

如果当前 Session 页面正在打开，P7B 可以通过当前 Session 的任务 SSE 推送状态变化。
这是独立 HTTP SSE endpoint，不复用 `/messages/stream`。
`/messages/stream` 只负责当前 AI run 和审批事件，
`tasks/events` 只负责异步任务状态变化。

SSE endpoint：

```http
GET /api/v1/sessions/{session_id}/tasks/events
Accept: text/event-stream
```

SSE 事件名使用当前项目已有风格：

```text
task_status_changed
```

事件示例：

```text
event: task_status_changed
data: {
  "session_id": "sess_xxx",
  "task": {
    "task_id": "task-0001",
    "previous_status": "running",
    "status": "succeeded",
    "message": "image upgrade completed",
    "reason": null,
    "result": {},
    "updated_at": "2026-05-06T10:05:00Z"
  }
}
```

任务 SSE 边界：

- 只推当前 Session 的任务状态变化
- 不跨 Session
- 不触发 AI 自动回复
- 不写 assistant 消息
- 不写入 `/messages/stream`
- 不替代 `tasks.json`
- SSE 断线或页面刷新后，通过 `GET /sessions/{session_id}/tasks` 补齐状态

前端打开或刷新 Session 页面时：

```text
1. GET /api/v1/sessions/{session_id}
   恢复 messages / approvals / operations 等会话内容

2. GET /api/v1/sessions/{session_id}/tasks
   lazy refresh 当前 Session 的非终态任务，并恢复任务卡状态

3. GET /api/v1/sessions/{session_id}/tasks/events
   建立 task SSE，等待后续 task_status_changed
```

前端收到 `task_status_changed` 后：

- 优先用事件 payload 局部更新 task card
- 如果本地找不到 task 或 payload 不完整，则调用 `GET /api/v1/sessions/{session_id}/tasks`
- 不默认刷新整个 Session
- 只有发现 Session 状态明显不一致时，才调用 `GET /api/v1/sessions/{session_id}`
- 任务进入终态时，只更新 task card 或展示 toast，不自动追加 assistant 消息

前端切换 Session 或关闭页面时，应关闭旧的 task SSE 连接。
浏览器刷新、SSE 断线或重连不会影响任务状态恢复，
因为 SSE 只是在线增量通知，不是事实源。

异步任务状态建议统一映射：

```text
RUNNING -> running
SUCCESS -> succeeded
FAILED  -> failed
CANCELED -> canceled
```

如果 DBAAS 返回更多状态，再扩展映射表。

`task_created` 只作为 `OperationResult.status`，
表示异步任务创建请求已经成功返回 `task_id`。
它不作为 `tasks.json.status` 使用。

第一版 task 状态按是否终态分为两类。

非终态任务状态：

```text
running
unknown
refresh_failed
```

终态任务状态：

```text
succeeded
failed
canceled
```

`refresh_failed` 是查询失败，不是任务失败。
它属于非终态任务，仍然算 Session 未结束事项，并阻止归档或删除。
P7B 不做 force archive/delete。
如果 DBAAS 长时间不可用导致任务持续 `refresh_failed`，
用户需要等待任务状态恢复刷新，或后续阶段再设计管理员强制处理能力。

只要 Session 关联了非终态异步任务，该 Session 就仍有未结束事项。

## 8. 本地重复执行保护

第七阶段不实现独立 `idempotency_key` 机制。
如果 DBAAS 后续支持 `Idempotency-Key` 或 `clientOperationId`，
可以再把它作为跨进程、跨重启的幂等能力接入。

P7A 先依赖已有记录做本地重复执行保护。

可能导致重复执行的场景包括：

- SSE 断线后用户重试
- 审批接口重复提交
- 后端恢复执行时超时
- DeepAgent checkpoint 恢复后重复进入工具节点
- 浏览器刷新后再次点击确认

本地关联字段：

- `approval_id`
- `thread_id`
- `run_id`
- `interrupted_tool_call.tool_call_id`
- `operations.json`
- `tasks.json`

工具执行前先查本地操作记录：

- `succeeded`：直接返回已保存的 `OperationResult`
- `failed`：直接返回失败结果
- `timeout`：返回超时结果，不自动重试写接口
- `task_created`：表示 operation 已创建异步任务，直接返回已有 `task_id`
- `started` 或 `unknown`：返回状态未知，提示 reconcile，不自动重试写接口
- 未记录：才真正调用 DBAAS 控制面

这不是严格的分布式幂等。
它的目标是在本地已有明确记录时避免重复执行。

## 9. 持久化与审计

### 9.1 最小文件

第七阶段建议在 Session 目录下增加：

```text
approvals.json
operations.json
tasks.json
```

P7A/P7B 统一使用 `.json` 文件名，不使用 `.jsonl` 文件名。
文件内容采用逐行 JSON，也就是 append-only log。
每一行都是该对象的一条完整最新记录，不是局部 patch。
状态变化时 append 一条同 id 的完整对象，读取时以后写入的完整对象为准。

append 约束：

- 只有对象可见状态或可见内容变化时才 append
- 状态未变化且展示内容未变化时，不追加新行
- `approvals.json` 的状态、决策人、决策时间、过期时间、resume 错误等变化需要 append
- `operations.json` 的状态、result、错误、开始/完成时间等变化需要 append
- `tasks.json` 只有 `status`、`source_status`、`message`、`reason`、`result`、`last_error` 等可见字段变化时才 append
- `last_checked_at` 单独变化不触发 append，避免轮询刷新导致文件快速膨胀
- 如果 DBAAS 查询失败但 `last_error` 内容没有变化，也不重复 append

示例：

```json
{"operation_id":"op_xxx","status":"started","result":null,"created_at":"2026-05-06T10:00:00Z","started_at":"2026-05-06T10:00:00Z","completed_at":null}
{"operation_id":"op_xxx","status":"succeeded","result":{"operation_id":"op_xxx","status":"succeeded","summary":"操作已完成。"},"created_at":"2026-05-06T10:00:00Z","started_at":"2026-05-06T10:00:00Z","completed_at":"2026-05-06T10:00:03Z"}
```

更新方式：

```text
1. 获取当前 Session 的文件锁
2. append 一行新的 JSON 记录
3. 释放文件锁
```

读取方式：

```text
approvals.json  按 approval_id 折叠，返回最新状态
operations.json 按 operation_id 折叠，返回最新状态
tasks.json      按 task_id 折叠，返回最新状态
```

如果同一个 id 有多行记录，后面的记录覆盖前面的记录。
文件本身保留状态变化历史，接口返回折叠后的 latest view。
不要实现 merge patch 或局部字段合并，避免后续迁移数据库前出现语义差异。

文件 compact：

- P7A/P7B 不实现 compact
- 后续如果单个文件过大，可以基于 folded latest view 重写 compact 后的新文件
- compact 不改变 API 语义，接口始终返回 folded latest view
- approval 和 operation 的完整历史如果需要长期审计，后续迁移数据库时可以拆成 latest 表和 history/event 表

锁粒度：

- 同一个 Session 下 `approvals.json`、`operations.json`、`tasks.json` 共用一把 Session 级文件锁
- 锁只包住 append 写入
- 不在锁内执行 DBAAS HTTP 请求
- 不在锁内执行 DeepAgent 调用
- 不在锁内等待 SSE 推送

这种格式和当前 `messages.json`、`approvals.json` 的实现方式一致，
也便于后续迁移到数据库。

### 9.2 operations.json

`operations.json` 保存写操作生命周期。
为避免同一份数据在多个字段重复，P7A 只在顶层保存操作元信息和
便于过滤的 `status`，完整执行结果放入 `result`。

`changes`、`details`、`error` 等字段不在顶层重复保存，
需要展示或审计时从 `result` 中读取。

建议字段：

```json
{
  "operation_id": "op_xxx",
  "approval_id": "appr_xxx",
  "session_id": "sess_xxx",
  "thread_id": "thread_xxx",
  "run_id": "run_xxx",
  "tool_call_id": "call_xxx",
  "action": "service.resource.update",
  "execution_mode": "sync",
  "status": "succeeded",
  "result": {
    "operation_id": "op_xxx",
    "approval_id": "appr_xxx",
    "action": "service.resource.update",
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
    "execution_mode": "sync",
    "status": "succeeded",
    "summary": "已将 mysql-xf2/mysql 内存调整为 15GB。",
    "task": null,
    "changes": [],
    "error": null,
    "details": {}
  },
  "created_at": "2026-05-06T10:00:00Z",
  "started_at": "2026-05-06T10:01:00Z",
  "completed_at": "2026-05-06T10:01:02Z"
}
```

顶层 `status` 应与 `result.status` 保持一致，便于快速过滤。
当 operation 仍处于 `started` 状态时，`result` 可以为 `null`。
进入终态、`timeout` 或 `unknown` 后，应写入完整 `OperationResult`。

同步超时时，`status` 和 `result.status` 可以为 `timeout`，
并在 `result.details.reconcile_required` 中标记需要后续只读确认。
ai-agent 重启后发现 `started` 且无终态的 operation 时，
应标记为 `unknown`，并设置 `result.error.error_type=operation_interrupted`。

### 9.3 tasks.json

`tasks.json` 保存异步任务引用和最新观测状态。
DBAAS 是任务状态事实源，本地记录保存 last known status。

建议字段：

```json
{
  "task_id": "task-0001",
  "operation_id": "op_xxx",
  "session_id": "sess_xxx",
  "action": "service.image.upgrade",
  "operation_conflict_key": "service.image.upgrade|service:mysql-xf2:child_service_type=mysql",
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
  "message": "image upgrade running",
  "reason": null,
  "result": null,
  "last_error": null,
  "created_at": "2026-05-06T10:01:00Z",
  "updated_at": "2026-05-06T10:01:00Z",
  "last_checked_at": "2026-05-06T10:01:00Z"
}
```

### 9.4 Session 生命周期约束

第七阶段需要把审批和异步任务纳入 Session 生命周期保护。

Session 的未结束事项包括：

- `pending approval`
- `session_run_lock` 已被占用
- 非终态异步任务

只要 Session 存在未结束事项，就不允许归档或删除。
归档或删除前应先执行 approval lazy expiration。
如果 `pending approval` 已过期，先标记为 `expired`，
并通过 reject resume 清理暂停点，再判断是否仍存在未结束事项。

```text
POST /api/v1/sessions/{session_id}/archive
-> 先执行 approval lazy expiration
-> session_run_lock 已被占用: 409 Conflict
-> 有 pending approval: 409 Conflict
-> 有 non-terminal task: 409 Conflict
-> 否则允许归档

DELETE /api/v1/sessions/{session_id}
-> 先执行 approval lazy expiration
-> session_run_lock 已被占用: 409 Conflict
-> 有 pending approval: 409 Conflict
-> 有 non-terminal task: 409 Conflict
-> 否则允许删除
```

`restore` 不需要该限制。
恢复 Session 只会让会话重新可见，不会丢失审批、任务或 DeepAgent thread 上下文。

删除判断应以当前进程 `session_run_lock` 状态、Session 下的 `approvals.json` 和 `tasks.json` 为准。

## 10. SSE 与 API 设计

### 10.1 消息流事件扩展

当前 `/messages/stream` 已支持：

```text
user_message
started
token
compression_started
compression_completed
done
error
```

第七阶段第一版只扩展当前 AI run 和审批相关事件：

```text
approval.required
approval.resolved
run.paused
run.resumed
```

`/messages/stream` 不承载异步任务后续状态变化。
异步任务可能在本次 AI run 结束后很久才完成，
任务状态变化统一通过当前 Session 的任务 SSE 推送。

`approval.required` 示例：

```text
event: approval.required
data: {
  "run_id": "run_xxx",
  "approval": {
    "approval_id": "appr_xxx",
    "status": "pending",
    "expires_at": "2026-05-06T10:30:00Z",
    "proposal": {
      "summary": "将 mysql-xf2/mysql 内存调整为 15GB",
      "risk_level": "medium",
      "required_role": "user",
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
          "key": "memory",
          "label": "内存",
          "value": 15,
          "unit": "GB"
        }
      ]
    }
  }
}
```

P7A 中，`/messages/stream` 触发人工确认后不长时间挂起等待用户操作。
推荐事件顺序：

```text
approval.required
run.paused
done
```

前端收到 `run.paused` 后展示确认卡并结束本次消息流。
用户批准或拒绝后，由审批决策接口负责恢复执行并返回结果。

### 10.2 独立运行事件流

当前 `GET /api/v1/sessions/{session_id}/runs/{run_id}/events` 仍未启用。

P7A 不启用独立 run 事件流，避免同时维护两套运行事件通道。
审批恢复完成后，前端通过审批决策接口响应和当前 Session 刷新补齐结果。

后续如果审批恢复后需要支持断线重连，再启用独立 run 事件流。

推荐语义：

```text
POST /api/v1/sessions/{session_id}/messages
-> 返回 run_id

GET /api/v1/sessions/{session_id}/runs/{run_id}/events
-> 订阅该 run 的后续事件
```

但第一版仍可以先沿用 `/messages/stream` 主链路，
审批恢复完成后通过当前页面状态刷新或新一轮事件推送补齐结果。

### 10.3 审批接口

建议新增或补齐：

```http
GET  /api/v1/sessions/{session_id}/approvals
POST /api/v1/sessions/{session_id}/approvals/{approval_id}/decision
```

`GET approvals` 支持按 status 过滤：

```text
pending
approved
rejected
expired
```

审批决策接口在 P7A 中同步完成 DeepAgent resume，并返回恢复后的最新结果。
建议响应至少包含：

```json
{
  "approval": {},
  "assistant_message": {},
  "operation": {},
  "task": null
}
```

含义：

- `approval`：审批最新状态
- `assistant_message`：resume 后生成并写入 `messages.json` 的助手消息；如果拒绝后没有助手消息，可为 `null`
- `operation`：本次审批触发的最新 operation；拒绝或超时时可为 `null`
- `task`：异步任务创建成功时返回当前 task latest view；同步操作或拒绝时为 `null`

前端提交审批决策后，应刷新当前 Session 详情。
如果响应中包含 `task`，或当前 Session 已有任务面板，
再调用 `GET /api/v1/sessions/{session_id}/tasks` 补齐最新任务列表。

审批决策接口的 timeout 语义：

- approval decision 接口同步执行 `Command(resume=...)`
- 如果批准后进入同步 DBAAS 写工具，写工具沿用自身 `timeout_seconds`
- 如果工具没有配置 `timeout_seconds`，使用 `dbaas_request_timeout_seconds`
- HTTP 请求等待时间使用同一个 timeout，或比 tool timeout 略大一点用于返回结构化结果
- 不新增独立的 approval decision timeout 配置
- 同步写工具超时时，仍返回 `OperationResult.status=timeout` 和 `error_type=dbaas_timeout`
- 不因为 approval decision HTTP 超时而自动重试写操作

### 10.3.1 Session 列表和详情响应

`GET /api/v1/sessions` 是会话列表接口，不返回 approvals、operations、tasks 明细。
列表默认只返回轻量 summary。
`pending_approval_count` 和 `running_task_count` 可以作为可选 badge 字段，
但 P7A 不强制实现。

如果实现这些 badge，后端可以读取每个 Session 的折叠后 latest view，
或后续维护索引字段。
第一版为了避免列表接口扫描过多 Session 文件，可以先不返回 count，
只在打开当前 Session 后展示确认卡和任务面板。

可选响应示例：

```json
{
  "session_id": "sess_xxx",
  "title": "mysql 扩容",
  "updated_at": "2026-05-06T10:00:00Z",
  "pending_approval_count": 1,
  "running_task_count": 2
}
```

`GET /api/v1/sessions/{session_id}` 是当前会话详情接口。
Phase7 在不破坏原有字段的前提下扩展响应：

```json
{
  "session": {
    "meta": {},
    "messages": [],
    "approvals": [],
    "operations": []
  }
}
```

任务明细不放在 Session detail 中。
也就是说，Session detail 只扩展 `approvals` 和 `operations`，
不扩展 `tasks`。
前端打开当前会话时，单独调用：

```http
GET /api/v1/sessions/{session_id}/tasks
```

这样 Session 列表保持轻量，当前会话页仍能渲染确认卡、操作结果和任务面板。

### 10.4 Session 时间线展示

人工确认卡属于 Session 产品层状态，不应写入 `messages.json`。

Session 详情加载时，后端应返回：

```text
messages
approvals
operations
```

前端基于这些结构构造时间线：

```text
messages.json    -> 用户/助手自然语言消息
approvals.json   -> 待确认/已确认/已拒绝/已过期的确认卡
operations.json  -> 已执行或尝试执行的操作结果
```

第七阶段不持久化独立 timeline。
`messages`、`approvals`、`operations` 是会话详情时间线的事实源，
页面时间线由前端或后端按需动态 merge。
`tasks` 不直接放在 Session detail 中，统一通过任务接口获取。

确认卡锚点规则：

- 主锚点：`request_message_id`
- 辅助分组：`run_id`
- 兜底排序：`created_at`

前端优先把 approval card 插到 `message_id == request_message_id` 的用户消息后面。
如果找不到该消息，再按 `created_at` 插入时间线对应位置或放到末尾。

刷新恢复规则：

- `pending` 且未过期：恢复可点击确认卡
- `approved`：恢复已确认卡片，按钮禁用
- `rejected`：恢复已拒绝卡片，按钮禁用
- `expired`：恢复已超时取消卡片，按钮禁用

确认或拒绝仍然通过 session-scoped 接口提交：

```http
POST /api/v1/sessions/{session_id}/approvals/{approval_id}/decision
```

这样页面刷新、切换 Session 或浏览器重开后，
仍能看到用户之前触发的确认卡和当前审批状态。

任务提醒展示规则：

- Session 中存在非终态任务时，页面应展示运行中任务提醒或 task card
- 任务提醒从 `tasks.json` 派生，不写入 `messages.json`
- 任务提醒不是 `system` role message，也不是 assistant message
- 页面刷新后，任务提醒由 `GET /api/v1/sessions/{session_id}/tasks` 恢复
- 当前页面收到 `task_status_changed` 后，局部更新对应 task card
- 任务进入终态时，可以展示 toast 或状态变化提示，但不自动追加 assistant 消息

因此，会话中看到的“任务处理中”属于产品层状态，
不是对话历史的一部分。

会话页可以提供当前 Session 的任务下拉框或任务面板。
推荐放在会话顶部或右侧区域，而不是混入聊天消息。

展示方式示例：

```text
任务 2 个运行中 ▼
```

展开后按状态分组展示当前 Session 的任务：

```text
运行中
- 镜像升级 mysql-xf2/mysql
  状态：running
  task_id：task-0001
  最近更新：10:05:12
  信息：image upgrade running

已完成
- 镜像升级 mysql-xf2/mysql
  状态：succeeded
  完成时间：10:08:30

失败
- 重启 mysql-xf2/mysql
  状态：failed
  原因：DBAAS 返回 xxx
```

如果 DBAAS 没有返回 `progress` 或 `stage`，
前端不应伪造百分比进度条。
第一版展示 task 状态流即可：

```text
运行中 -> 成功/失败/取消
```

如果后续 DBAAS 返回 `progress` 或 `stage`，
再在任务卡中展示百分比和阶段信息。

### 10.5 任务接口

P7B 任务接口只服务当前 Session。
接口路径必须带 `session_id`，不提供跨 Session 的任务中心或用户级任务列表。

建议新增：

```http
GET /api/v1/sessions/{session_id}/tasks
GET /api/v1/sessions/{session_id}/tasks/events
```

这些接口的共同语义：

- 只能读取或刷新当前 `session_id` 下记录过的任务
- 即使 DBAAS `task_id` 是全局唯一，也不能通过本 Session 接口访问其他 Session 的任务
- 前端切换到另一个 Session 后，应重新调用新 Session 的任务接口
- 页面刷新后，通过当前 Session 的任务接口恢复任务下拉框或任务面板
- 不新增 `GET /api/v1/tasks` 或 `GET /api/v1/users/{user_id}/tasks`

`GET /api/v1/sessions/{session_id}/tasks` 是当前 Session 任务明细的唯一读取入口。
该接口返回当前 Session 的全部 latest tasks，包括 running、succeeded、failed、canceled 等状态。
第一版不做分页，也不要求 `limit` 或 `status` 过滤参数。
接口只对非终态任务做 lazy refresh。
已经进入终态的任务不再反复查询 DBAAS，直接返回本地 latest view。
这样前端既能展示已完成/失败历史，也不会对终态任务产生不必要的 DBAAS 查询。
如果后续单个 Session 下任务数量过多，再增加 `limit`、`status` 或单任务详情接口。

`tasks/events` 是当前 Session 的任务 SSE 订阅接口，
用于接收 `task_status_changed` 事件。
任务状态变化不通过 `/messages/stream` 推送。

P7B 先不新增前端可见的单任务详情接口或手动 refresh 接口：

```http
GET /api/v1/sessions/{session_id}/tasks/{task_id}
POST /api/v1/sessions/{session_id}/tasks/{task_id}/refresh
```

如果用户需要刷新任务状态，前端重新调用 `GET /api/v1/sessions/{session_id}/tasks` 即可。
如果后续任务数量较大或需要单任务详情页，再补充单任务接口。

当发现当前 Session 已有同一 `operation_conflict_key` 的非终态任务时，
创建异步任务的写工具应返回 `409 Conflict`，
并返回 existing task 供前端展示。

Agent 内部也应有：

```text
get_dbaas_task_tool
```

用于自然语言查询当前 Session 内的任务进度。

### 10.6 归档和删除约束

归档和删除接口需要检查 Session 是否存在未结束事项。
检查前先执行 approval lazy expiration。
如果发现已过期 approval，先标记为 `expired`，
并通过 reject resume 清理暂停点，再继续判断。

```http
POST /api/v1/sessions/{session_id}/archive
DELETE /api/v1/sessions/{session_id}
```

如果存在 `pending approval`，返回：

```json
{
  "error_type": "session_has_pending_approval",
  "detail": "当前 Session 存在待确认操作，请先批准或拒绝后再归档或删除。"
}
```

如果 `session_run_lock` 已被占用，返回：

```json
{
  "error_type": "session_run_locked",
  "detail": "当前 Session 正在执行 AI 请求，请等待本轮完成后再归档或删除。"
}
```

如果存在非终态异步任务，返回：

```json
{
  "error_type": "session_has_running_tasks",
  "detail": "当前 Session 存在运行中的 DBAAS 任务，请等待任务结束后再归档或删除。"
}
```

以上情况都建议使用 `409 Conflict`。
`restore` 不需要检查未结束事项。

### 10.7 前端展示边界

前端普通页面只展示稳定、安全、可解释的字段。

审批前展示 `OperationProposal` 的安全子集：

```text
summary
targets
risk_level
required_role
execution_mode
parameters
```

执行后展示 `OperationResult` 的用户子集：

```text
summary
status
targets
changes
task.task_id
task.status
error.message
```

任务下拉框或任务面板展示 `tasks` 的安全子集：

```text
summary 或 action
targets
task_id
status
message
reason
updated_at
result 简要信息
```

任务面板只展示当前 Session 的任务。
它通过 `GET /api/v1/sessions/{session_id}/tasks` 初始化和刷新，
通过 `task_status_changed` 局部更新任务卡。
任务成功或失败后保留在任务面板中供用户查看结果，
但不自动写入 assistant 消息。

完整结构只用于审计、排障或管理员调试页面。
普通前端不应强依赖：

```text
thread_id
interrupted_tool_call
details
原始 DBAAS response
```

这样可以避免前端和 DBAAS 原始响应、工具内部字段过度耦合。

## 11. DBAAS 工具设计

### 11.1 update_service_resource_tool

用途：

- 更新指定服务组下某类子服务的 CPU、内存或平台自动分配标记

参数：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "platform_auto": null,
  "cpu": 16,
  "memory": 64
}
```

DBAAS 调用：

```text
PUT /services/{name}/resource
```

返回：

- `execution_mode=sync`
- `status=succeeded` 或 `failed`
- `changes[]` 变更列表

### 11.2 update_service_storage_tool

用途：

- 更新指定服务组下某类子服务的 data/log 卷大小或平台自动分配标记

参数：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "platform_auto": null,
  "data_volume_size": 500,
  "log_volume_size": 200
}
```

DBAAS 调用：

```text
PUT /services/{name}/storage
```

返回：

- `execution_mode=sync`
- `status=succeeded` 或 `failed`
- `changes[]` 变更列表

### 11.3 create_service_image_upgrade_task_tool

用途：

- 创建服务镜像升级异步任务

参数：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "image": "mysql:8.0.37",
  "version": "8.0.37",
  "unit_ids": null
}
```

DBAAS 调用：

```text
POST /services/{name}/image-upgrade
```

返回：

- `execution_mode=async`
- `OperationResult.status=task_created`
- `task.task_id`

### 11.4 get_dbaas_task_tool

用途：

- 查询异步任务状态

参数：

```json
{
  "task_id": "task-0001"
}
```

DBAAS 调用：

```text
GET /tasks/{task_id}
```

返回：

- task 当前状态
- source status
- message
- reason
- result
- 是否已更新本地 `tasks.json`

### 11.5 change_service_lifecycle_tool

用途：

- 服务启动、停止、重启等生命周期操作

参数建议：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "action": "restart",
  "unit_ids": null
}
```

`action` 候选：

```text
start
stop
restart
```

该工具先作为预留设计。
实际执行方式由 DBAAS 控制面接口决定：

- 如果控制面同步返回最终状态，则返回 `execution_mode=sync`
- 如果控制面返回 task_id，则返回 `execution_mode=async`

停止生产服务属于更高风险操作，
后续可以增加更严格策略，例如只允许 admin 审批或要求二次确认。

## 12. 权限与风险策略

第七阶段最小权限策略：

- 普通用户只能操作自己可见的服务
- 管理员可以操作全部服务
- 主机、集群等平台级操作第一版默认只允许管理员
- `OperationProposal.required_role` 支持 `user` 和 `admin`
- `required_role=user` 表示普通用户和管理员均可在可见权限范围内操作
- `required_role=admin` 表示只有管理员可以创建审批和执行工具
- 创建 approval 前必须按当前 request identity 校验是否满足 `required_role`
- 写工具真正执行前必须再次按当前 request identity 校验是否满足 `required_role`
- 权限判断以 DBAAS 控制面返回为准，项目侧也应做基础校验
- `user_id`、`role` 不能作为 AI tool 参数暴露给模型填写
- 写工具必须从 request/session identity 获取当前身份
- `SessionMeta.role/user` 是创建 Session 时的身份快照，只用于展示和审计，不作为写操作实时权限判断依据
- DBAAS 写接口调用时必须携带当前身份，由 DBAAS 再次校验目标资源是否允许操作
- 所有写操作必须审批

风险等级建议：

```text
low
medium
high
critical
```

初始映射：

- 资源扩容：`medium`
- 存储扩容：`medium`
- 镜像升级：`high`
- 重启：`high`
- 停止：`critical`
- 启动：`medium`

风险等级第一版主要用于前端展示和审计。
后续可以用于策略控制，例如：

- `critical` 只能 admin 审批
- 生产环境停止操作要求二次确认
- 大规格缩容要求先查询监控和健康状态

## 13. Prompt 约束

系统提示词需要补充第七阶段操作规则：

- 操作类请求必须先确认目标服务、子服务类型和变更参数
- 不得编造操作结果
- 不得绕过 DBAAS 写工具执行变更
- 写操作必须等待系统人工确认流程
- 同步操作完成后说明关键 `changes[]`
- 异步操作创建后说明 task_id 和当前状态，不承诺最终成功
- 查询任务状态必须调用任务查询工具
- 高风险操作必须明确影响范围和风险点

需要避免在 prompt 中硬塞完整接口 schema。
工具描述和结构化返回应承担主要约束。

## 14. 上下文压缩与操作事实

第七阶段后，压缩策略必须保护操作事实。

压缩摘要中至少保留：

- 已观察到的服务对象
- 用户提出过的操作目标
- 已创建的审批
- 已批准或拒绝的审批
- 已执行的操作
- 已创建的 task_id
- 当前仍在运行的任务
- 用户给出的约束条件，例如“延迟超过 1 秒就不要扩容”

操作事实不应只依赖自然语言消息。
持久化记录才是审计和恢复依据。

## 15. 分阶段落地

### 15.0 第一批实现范围

第一批建议实现 `P7A + P7B-min`，避免异步任务只创建不刷新。

第一批包括：

- P7A 写工具与审批闭环
- P7A 同步操作记录和结果展示
- P7A 异步 task 创建和本地 task 记录
- P7B-min 当前 Session task lazy refresh

`P7B-min` 至少实现：

```http
GET /api/v1/sessions/{session_id}/tasks
```

该接口需要 lazy refresh 当前 Session 的非终态任务，
让异步 task 创建后能刷新到 `succeeded/failed/canceled/refresh_failed` 等状态。

第一批不要求实现：

- task SSE
- 任务下拉框或完整任务面板
- 单任务详情接口
- 手动 task refresh 接口

这些放到第二批 `P7B-full`。

### 15.1 P7A：写工具与审批闭环

目标：

- 实现资源规格更新工具
- 实现存储规格更新工具
- 实现镜像升级任务创建工具
- 接入 DeepAgent `interrupt_on`
- 实现 approval 记录创建、查询和决策接口
- 实现 `Command(resume=...)` 恢复执行
- 实现基础 `operations.json`
- 实现基于 `operations.json` / `tasks.json` 的本地重复执行保护
- 同一个 Session 同一时间只允许一个 `session_run_lock` 持有者
- 同一个 Session 同一时间只允许一个 `pending approval`
- Session 有 `pending approval` 时不允许继续发新消息
- 审批决策接口必须绑定 `session_id`，只能 resume 当前 approval 所属的 `thread_id`
- 实现 approval 超时自动取消，超时后通过 reject resume 清理暂停点
- P7A/P7B 持久化统一使用 `.json` 文件，不使用 `.jsonl`
- P7A 异步操作只创建 task、写入 task 引用并返回 task_id，不实现 task SSE 和任务面板
- P7A 基线不实现 task lazy refresh；如果 P7A 单独开放异步写工具，则必须补最小 task lazy refresh，或暂不开放异步写工具

验收标准：

- 审批决策者必须能访问当前 Session，并满足 `proposal.required_role`
- API/ApprovalRecord 使用 `approved/rejected`，DeepAgent resume 使用 `approve/reject`
- 用户请求扩容时，前端收到审批请求
- 用户拒绝时，不调用 DBAAS 写接口
- 用户批准时，工具执行且返回结果
- 同步操作能展示 `changes[]`
- 同步写操作超时时返回 `timeout`，不自动重试写接口
- 同步写操作超时时记录 `reconcile_required`
- `timeout` 或 `unknown/reconcile_required` 不自动 reconcile、不自动补偿、不自动判断最终成功
- ai-agent 重启后，`started` operation 不会自动重放写接口
- ai-agent 重启后，`started` operation 会标记为 `unknown/reconcile_required`
- 异步升级能返回 task_id
- 异步升级创建 task 后写入当前 Session 的 `tasks.json`
- 第一批上线后，异步 task 可以通过 `GET /api/v1/sessions/{session_id}/tasks` lazy refresh 到最终状态
- `tasks.json` 中保存稳定的 `operation_conflict_key`
- 同一 Session 下 `approvals.json`、`operations.json`、`tasks.json` 共用一把 Session 级文件锁
- Session 级文件锁只包住 append 写入，不包住 DBAAS HTTP、DeepAgent 调用或 SSE 推送
- `approvals.json`、`operations.json`、`tasks.json` 每次 append 都写完整最新对象，不写局部 patch
- 重复提交审批不会重复执行同一操作
- 同一 Session 的 `session_run_lock` 已被占用时，新的发消息请求返回 `409 Conflict`
- Session 已有待确认操作时，发新消息返回 `409 Conflict`，前端提示先处理当前审批
- Session 有 `pending approval` 时，不阻止查询 approvals、提交 approval decision、查询 tasks 或订阅 tasks/events
- 跨 Session 提交 approval decision 必须失败
- Session 有 `pending approval` 时不允许归档或删除
- Session 的 `session_run_lock` 被占用时不允许归档或删除
- 归档或删除前会先执行 approval lazy expiration
- 已过期 approval 不应继续阻塞归档或删除
- approval lazy expiration 如果 resume reject 失败，应记录 `resume_failed` 并在下次查询或发消息前重试
- approval 超时后状态变为 `expired`
- approval 超时后不允许批准
- approval 超时后不调用 DBAAS 写接口
- approval 超时清理暂停点后，Session 可以继续使用
- 刷新页面后能根据 `request_message_id` 恢复 pending approval 确认卡
- 确认卡的确认/拒绝按钮按 `session_id + approval_id` 提交
- 审批卡能在已知当前值时展示类似 `8GB -> 15GB` 的变化，不编造未知当前值
- 第一批不要求实现 task SSE、任务下拉框或完整任务面板

### 15.2 P7B：异步任务追踪

目标：

- 实现当前 Session 的 task lazy refresh
- 实现当前 Session task SSE 订阅期间的 refresh loop
- 实现 `get_dbaas_task_tool`
- 实现 `tasks.json`
- 实现 Session 下任务列表接口
- Agent 能回答“刚才那个任务怎么样了”
- 前端能展示当前 Session 的运行中任务
- 前端能基于任务记录展示当前 Session 的任务提醒或 task card
- 前端能通过当前 Session 任务下拉框或任务面板查看进度、成功和失败信息

验收标准：

- 镜像升级创建 task 后，本地能查到任务记录
- 如果 P7A 单独上线异步写工具，必须至少提供当前 Session task lazy refresh；否则异步写工具应等 P7B 一起开放
- 同一 Session 可以存在多个无冲突异步任务
- 同一 Session 已有同一 `operation_conflict_key` 的非终态任务时，新建同类任务返回冲突并展示已有任务
- `GET /sessions/{session_id}/tasks` 返回当前 Session 全部 latest tasks
- `GET /sessions/{session_id}/tasks` 第一版不分页，不要求 `limit/status` 参数
- `GET /sessions/{session_id}/tasks` 只 lazy refresh 当前 Session 的非终态任务，不反复查询终态任务
- 当前 Session task SSE 订阅期间，refresh loop 会更新非终态任务的 last known status
- 异步 operation 创建 task 后保持 `task_created`，task 最终状态只更新 `tasks.json`
- `refresh_failed` 属于非终态，不表示任务失败
- `refresh_failed` 仍阻止归档或删除，P7B 不做 force archive/delete
- 当前 Session 打开时，可通过任务 SSE 收到 `task_status_changed`
- 收到 `task_status_changed` 后，前端更新 task card，不写 assistant 消息
- `task_status_changed` 不通过 `/messages/stream` 推送
- 页面刷新后，`GET /sessions/{session_id}/tasks` 能恢复非终态任务提醒或 task card
- 页面刷新后，任务下拉框或任务面板能恢复当前 Session 的任务列表和最新状态
- 页面刷新后，前端重新订阅当前 Session 的 task SSE
- 切换 Session 后，前端关闭旧 Session 的 task SSE，并只展示新 Session 的任务
- 不提供跨 Session 任务中心、用户级任务列表或全局 `/tasks` 接口
- 任务成功或失败后，只更新任务面板或展示 toast，不自动追加 assistant 消息
- 自然语言查询任务进度会调用任务工具
- 自然语言查询“有哪些任务在跑”只回答当前 Session 内任务
- 任务成功或失败后，任务接口和任务面板能反映最新状态
- Session 有非终态异步任务时不允许归档或删除

### 15.3 P7C：生命周期操作与高风险策略

目标：

- 接入或预留服务启动、停止、重启工具
- 根据 DBAAS 控制面结果支持 sync/async 两种返回
- 增加高风险操作策略
- 必要时增加 admin-only 或二次确认

验收标准：

- 生命周期操作必须审批
- 停止生产服务等高风险操作有清晰风险提示
- 控制面返回 task_id 时进入异步任务追踪
- 控制面同步返回时记录 `changes[]`

## 16. 不做事项

第七阶段第一版不建议做：

- 自研 Agent 运行时
- 自研 thread 恢复机制
- 前端直接调用 DBAAS 写接口
- 审批参数在线编辑
- 审批备注输入
- 复杂多级审批流
- 跨 Session 全局任务中心
- 将完整 DBAAS 原始数据直接塞进模型上下文

这些能力可以在写工具、审批和任务追踪稳定后再逐步扩展。

## 17. 建议结论

第七阶段的主线可以概括为：

```text
Agent 提出操作
-> DeepAgent interrupt_on 暂停
-> 项目侧审批和审计
-> Command(resume=...) 恢复
-> DBAAS 写工具执行
-> OperationResult 屏蔽同步/异步差异
-> Session 记录操作和任务事实
```

这样可以在不重复自造运行时的前提下，
把服务升级、扩容、存储扩容、启停和异步任务追踪统一到同一条受控链路中。
