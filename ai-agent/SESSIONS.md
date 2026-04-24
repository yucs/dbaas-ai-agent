# DBAAS 智能助手 Session 管理设计

## 1. 文档目的

本文档用于单独说明多用户、多 Session 的本地存储与加载方案。

本项目基于 DeepAgent 实现，因此这里的 Session 不只是页面上的聊天会话，
也是 DeepAgent 运行时的承载单元。

更具体地说：

- 页面层用 Session 做历史列表、当前窗口切换、归档和删除
- 运行时用 Session 绑定 `thread_id`，以支持继续对话、会话恢复和审批后的继续执行

第一阶段与 `mock-server` 集成时，需要注意：

- 本地 Session 目录按产品层 `user_id` 组织
- 普通用户场景下，可简化为 `user = user_id`
- 管理员场景下，后端身份直接使用 `admin`，不应再把 `user_id` 当作 `user`

## 1.1 能力边界

在 Session 管理这一层，需要区分：

DeepAgent 原生支持：

- `thread_id` 对应的执行线程概念
- 基于同一个 `thread_id` 的继续执行
- 结合 checkpointer 的中断后恢复

本项目需要自己开发：

- `session_id` 这个产品层会话概念
- `user_id -> sessions` 的目录组织和索引
- 历史 Session 列表加载
- 当前窗口切换到指定 Session
- Session 的归档、删除、恢复
- `session_id <-> thread_id` 的映射关系
- Session 相关本地文件结构

当前目标不是设计最终的数据库模型，而是先用本地目录把以下能力稳定落地：

- 用户登录后看到自己的历史 Session 列表
- 点击某个 Session 后加载到当前窗口
- 支持创建新 Session
- 支持归档 Session
- 支持删除 Session
- 为后续接入 Deep Agent thread 保留清晰边界

这套设计本质上属于通用的聊天/助手类 Session 管理模型，
并不是 DBAAS 场景独有。

它的通用部分包括：

- 用户维度的历史会话列表
- Session 详情加载
- 当前窗口切换
- Session 的归档与删除

在此基础上，DBAAS Agent 会增加一些 AI 运行时相关扩展，例如：

- `thread_id`
- `approvals.jsonl`

## 1.2 为什么仍然保留 Session 层

即使后续接入 DeepAgent checkpointer，Session 层仍然有必要保留。

原因不是重复保存会话，而是为了在产品层和 AI 运行时之间建立一个清晰边界。

可以把它理解成两层：

- Session
  - 面向产品层
  - 负责历史列表、标题、预览、归档、删除、当前窗口切换
  - 负责原始记录、审计留痕和产品层管理
- Thread / Checkpointer
  - 面向 DeepAgent 运行时
  - 负责持续对话、中断恢复、审批恢复、执行状态持久化

还需要补充一个运行时约束：

- 长会话压缩优先发生在同一个 `thread_id` 内
- 由 `SummarizationMiddleware` 与 checkpoint 负责维持运行时上下文
- 不再单独持久化 `summary.json`

因此，Session 不只是为了管理页面会话，也是为了把产品层和具体 AI 框架解耦。

这样做有两个直接好处：

- 页面不需要直接依赖 DeepAgent / LangGraph 的内部线程结构
- 后续如果从 DeepAgent 演进到其他 AI runtime，产品层 Session 模型仍然可以保留

换句话说：

- `session_id` 是产品层主键
- `thread_id` 是 AI 运行时主键
- 压缩状态属于运行时内部细节，不单独作为产品层主键或文件对象

第一阶段和后续阶段都建议保持：

- 一个 `session_id`
- 对应一个 `thread_id`

这样页面切换、历史管理和运行时恢复可以各自独立演进，而不会互相绑死。

当前优先策略进一步明确为：

- 长会话压缩优先在原 `thread_id` 内完成
- 优先使用 `SummarizationMiddleware`
- 不因压缩而切换新的 `thread_id`
- 仅在用户显式删除 Session 时，才清理对应 `thread_id`

## 2. 第一阶段设计目标

第一阶段优先满足“简单可用、结构清晰、后续可迁移”。

这意味着：

- 不引入数据库
- 直接使用本地目录存储
- 不做过重的分层设计
- 先服务于页面侧边栏历史会话体验
- 后续能够平滑迁移到 SQLite 或 PostgreSQL

## 3. 推荐目录结构

建议使用以下目录布局：

```text
ai-agent/
  data/
    users/
      <user_id>/
        sessions/
          index.json
          <session_id>/
            meta.json
            messages.jsonl
            approvals.jsonl
```

## 4. 目录结构说明

### 4.1 `users/<user_id>/sessions/`

每个用户一个独立目录。

建议这里直接使用稳定的 `user_id`，而不是展示名或可变更用户名。

不建议直接使用：

- 展示名
- 中文名
- 带空格的名字
- 可能修改的登录昵称

如果当前用户是普通用户，第一阶段可以让 `user_id` 与 `mock-server` 中的 `user`
保持一致；如果当前用户是管理员，则 `user_id` 只作为本地产品用户标识使用。

### 4.2 `index.json`

该文件用于保存当前用户的 Session 列表投影。

页面左侧历史会话列表应优先读取它，而不是每次扫描所有子目录。

这样做的好处：

- 加载速度更稳定
- 前端列表接口更简单
- 更容易支持排序、归档、删除过滤
- 后续更容易迁移到数据库索引表

### 4.3 `<session_id>/`

每个 Session 一个独立子目录。

这样可以自然隔离不同会话的数据，也方便后续做：

- 单个会话加载
- 单个会话归档
- 单个会话导出
- 单个会话清理

## 5. 单个 Session 文件职责

### 5.1 `meta.json`

保存 Session 元信息。

建议字段：

- `session_id`
- `user_id`
- `role`
- `user`
- `thread_id`
- `title`
- `status`
- `created_at`
- `updated_at`
- `archived_at`

其中：

- `role` 和 `user` 用于保存当前 Session 创建时的身份快照
- `thread_id` 用于映射 Deep Agent 运行线程
- `status` 建议支持 `active`、`archived`

示例：

```json
{
  "session_id": "sess_001",
  "user_id": "user_001",
  "role": "user",
  "user": "user_001",
  "thread_id": "thread_001",
  "title": "排查 mysql-xf2 状态",
  "status": "active",
  "created_at": "2026-04-22T12:00:00Z",
  "updated_at": "2026-04-22T12:10:00Z",
  "archived_at": null
}
```

### 5.2 `messages.jsonl`

保存当前 Session 的原始消息记录。

建议使用 `jsonl`，便于顺序追加写入。

建议每条消息至少包含：

- `id`
- `role`
- `content`
- `created_at`

示例：

```json
{"message_id":"msg_001","role":"user","content":"查看 mysql-xf2 状态","created_at":"2026-04-22T12:00:01Z"}
{"message_id":"msg_002","role":"assistant","content":"我先帮你查询 mysql-xf2 的状态。","created_at":"2026-04-22T12:00:02Z"}
```

### 5.3 `approvals.jsonl`

保存当前 Session 的审批记录。

第一阶段将审批和消息分开存储，更利于后续：

- 查询待审批状态
- 展示历史审批记录
- 审计写操作流程

建议每条记录至少包含：

- `approval_id`
- `tool_name`
- `status`
- `created_at`
- `decided_at`
- `payload`

## 6. `index.json` 设计建议

`index.json` 是当前用户历史 Session 列表的直接来源。

建议每个条目至少包含：

- `session_id`
- `title`
- `status`
- `updated_at`
- `last_message_at`
- `preview`

示例：

```json
[
  {
    "session_id": "sess_001",
    "title": "排查 mysql-xf2",
    "status": "active",
    "updated_at": "2026-04-22T12:10:00Z",
    "last_message_at": "2026-04-22T12:10:00Z",
    "preview": "已查询 mysql-xf2，健康状态为 DEGRADED"
  },
  {
    "session_id": "sess_002",
    "title": "MySQL 是什么",
    "status": "archived",
    "updated_at": "2026-04-21T09:00:00Z",
    "last_message_at": "2026-04-21T09:00:00Z",
    "preview": "解释了 MySQL 的基本概念"
  }
]
```

## 7. 页面加载逻辑

### 7.1 登录后加载历史 Session

建议流程：

1. 用户登录
2. 后端识别 `user_id`
3. 读取 `data/users/<user_id>/sessions/index.json`
4. 返回该用户的 Session 列表
5. 前端渲染侧边栏历史会话

### 7.2 打开某个 Session

建议流程：

1. 用户点击某个 `session_id`
2. 后端读取 `meta.json`
3. 后端读取 `messages.jsonl`
4. 后端读取 `approvals.jsonl`
5. 将该 Session 内容返回给前端
6. 前端将其加载到当前窗口

这里“加载到当前窗口”的本质是：

- 前端维护一个 `current_session_id`
- 当用户切换 Session 时，重新读取该 Session 的数据并替换当前视图内容

### 7.3 当前 Session 继续对话

建议流程：

1. 前端向当前 `session_id` 发送用户消息
2. 后端将消息追加到 `messages.jsonl`
3. Agent 优先在当前活动 `thread_id` 上继续执行
4. 返回本轮 assistant 消息结果
5. 将 assistant 消息继续追加到 `messages.jsonl`
6. 更新 `meta.json.updated_at`
7. 更新 `index.json` 中该条目的 `updated_at`、`last_message_at` 和 `preview`

如果后续为了上下文压缩切换到新的 `thread_id`：
当前不作为优先方案。

当前更推荐的做法是：

- 优先在原 `thread_id` 内通过 `SummarizationMiddleware` 完成压缩
- Session 继续绑定原活动线程
- 压缩状态不再额外落盘到 Session 目录
- 压缩不会触发线程切换

## 8. 创建、归档、删除策略

### 8.1 创建 Session

创建新 Session 时建议执行：

- 生成新的 `session_id`
- 创建对应目录
- 写入 `meta.json`
- 创建空的 `messages.jsonl`
- 创建空的 `approvals.jsonl`
- 在 `index.json` 中追加一条记录

### 8.2 归档 Session

归档时不移动目录。

建议做法：

- 更新 `meta.json.status = archived`
- 写入 `archived_at`
- 同步更新 `index.json`

这样可以保持路径稳定，避免额外移动文件带来的复杂性。

对于已归档 Session，建议页面默认只读展示。

如果用户在已归档 Session 中继续发送消息，建议后端先自动执行：

- `restore`
- 再继续本次消息处理

这样前端不需要额外处理“先恢复再发消息”的两步流程。

### 8.3 删除 Session

第一阶段建议采用真正删除，而不是逻辑删除。

建议做法：

- 从 `index.json` 中移除该 Session
- 删除 `data/users/<user_id>/sessions/<session_id>/` 目录
- 同步删除该 Session 绑定的 `thread_id` 运行时数据
- 页面刷新后不再出现在历史列表中
- 需要可恢复能力时，应使用 `archive`

这样和 `archive` 的职责更清楚：

- `archive`：隐藏但可恢复
- `delete`：真正删除

## 9. 与 Deep Agent 的关系

Session 是产品概念，Thread 是 Agent 运行概念。

第一阶段建议保持：

- 一个 `session_id`
- 对应一个 `thread_id`

因此：

- 页面上的“打开某个历史 Session”
- 本质上是加载该 `session_id` 的会话内容
- 如果继续提问，则使用其当前活动 `thread_id`

但这里需要区分：

- `delete session`
  - 可以清理对应运行时线程数据
- `context compression`
  - 优先在原 `thread_id` 内完成
  - 不应额外生成产品层摘要文件
  - 不应因此清理当前 `thread_id`

这可以很好地支持：

- 多用户
- 多 Session
- 会话恢复
- 后续审批中断恢复

## 10. 为什么不建议再简化成一个大 JSON

不建议使用下面这类结构：

- 一个用户一个总 `sessions.json`
- 所有消息都塞到单个大文件里
- Session 列表与消息正文混在同一个 JSON 中

原因是：

- 历史列表读取和会话正文读取的关注点不同
- 大文件后续容易膨胀
- 每次更新都要重写整份文件
- 删除、归档、会话切换会越来越难维护

当前这版结构已经足够轻量，同时也保留了合理演进空间。

## 11. 第一阶段建议接口

本章节给出接口范围概览，具体请求与返回结构以 [API.md](./API.md) 为准。

基于该目录结构，第一阶段建议至少支持以下接口：

- `GET /sessions`
- `POST /sessions`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/messages`
- `POST /sessions/{session_id}/archive`
- `DELETE /sessions/{session_id}`

其中：

- `GET /sessions` 返回当前用户的 `index.json` 视图
- `GET /sessions/{session_id}` 返回单个 Session 的详情
- `DELETE /sessions/{session_id}` 直接删除当前 Session 目录

## 12. 后续可扩展方向

后续如果需要增强，可以继续扩展：

- 增加 `runs.jsonl` 保存执行记录
- 增加 `tasks.jsonl` 保存异步任务追踪
- 增加文件锁避免并发写冲突
- 增加回收站机制，而不是直接删除
- 将当前目录模型平滑迁移到数据库

## 13. 当前建议结论

对于第一阶段，本地目录建议就按下面这条主线落地：

```text
data/users/<user_id>/sessions/index.json
data/users/<user_id>/sessions/<session_id>/meta.json
data/users/<user_id>/sessions/<session_id>/messages.jsonl
data/users/<user_id>/sessions/<session_id>/approvals.jsonl
```

这套结构已经可以很好支撑：

- 多用户
- 多 Session
- 历史列表展示
- 当前窗口切换
- 归档
- 删除
- 后续接入 Deep Agent thread
