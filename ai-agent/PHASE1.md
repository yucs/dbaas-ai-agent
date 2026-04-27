# DBAAS 智能助手第一阶段说明

## 1. 文档目的

本文档不再只是“第一阶段准备做什么”，而是根据当前仓库中的实际实现，收敛第一阶段已经落地的产品基座能力、实现细节和边界。

需要特别说明：

- 当前主干代码已经继续演进到第二阶段，AI runtime 已升级为真实 DeepAgent
- 但多用户、多 Session、文件投影、前端登录与会话页这些基础能力，仍然属于第一阶段交付成果
- 因此本文档描述的是“当前代码中仍然有效的第一阶段能力基座”

相关文档：

- [DESIGN.md](./DESIGN.md)
- [SESSIONS.md](./SESSIONS.md)
- [API.md](./API.md)
- [MEMORY.md](./MEMORY.md)
- [PHASE2.md](./PHASE2.md)

## 2. 第一阶段当前结论

截至当前代码版本，第一阶段目标已经完成，且形成了后续第二阶段继续演进的稳定基座。

第一阶段已经验证并保留下来的核心能力包括：

- 本地登录
- 多用户隔离
- 多 Session 管理
- Session 历史加载
- 在当前 Session 中持续问答
- `session_id -> thread_id` 绑定
- 本地文件投影
- 简单但可用的聊天前端

第一阶段最重要的价值已经从“做一个 demo”变成：

- 让产品层 Session 模型稳定下来
- 让前端交互和后端接口先收敛
- 为第二阶段替换真实 runtime 时尽量不重写页面和 Session 管理逻辑

## 3. 第一阶段目标与当前实现对应

### 3.1 用户与登录

当前前端已经落地一个本地登录流程：

- 通过登录弹窗输入 `user_id`
- 选择用户类型：
  - `user`
  - `admin`
- 登录后把身份信息保存到浏览器本地 `localStorage`
- 后续请求通过请求头传给后端：
  - `X-User-Id`
  - `X-User-Role`
  - `X-User`

当前实现中的身份规则是：

- 普通用户
  - 默认 `user = user_id`
- 管理员
  - 允许只传 `user_id`
  - `X-User` 可为空

后端还补充了安全校验：

- `user_id`、`user` 仅允许字母、数字、点、下划线和中划线
- 长度限制为 64
- 非法请求头直接返回 `400`

这比最初只强调“简单登录”更完整，已经具备了可持续复用的最小身份入口。

### 3.2 对话页和会话列表

当前前端已经实现一个静态单页聊天壳：

- 左侧展示当前用户自己的 Session 列表
- 顶部展示当前身份
- 支持新建 Session
- 支持打开历史 Session
- 支持删除 Session
- 右侧展示当前会话消息
- 底部输入框继续提问

实际实现比最初计划多了几项体验优化：

- 登录后自动拉取当前用户会话
- 如果当前用户还没有会话，自动创建第一条 Session
- Session 列表按最后消息时间倒序显示
- 发送消息时前端先插入乐观消息和“助手正在思考”占位
- 返回 DBAAS 边界提示时，会在页面顶部显示 flash 提示
- 支持切换用户并清空本地登录态

### 3.3 多用户与多 Session

第一阶段最关键的“多用户、多 Session”能力已经在当前代码中稳定落地：

- Session 目录按 `user_id` 隔离
- 用户只能访问自己的 Session
- 后端对 `session_id` 做格式校验
- Session 不存在、越权访问、非法 ID 都统一返回“Session 不存在”

当前已经支持的 Session 生命周期包括：

- 创建
- 列表读取
- 详情读取
- 归档
- 恢复
- 删除

需要说明的是：

- 后端已提供 `archive` / `restore` API
- 当前前端主页面优先暴露的是“打开”和“删除”
- 也就是说，归档能力已经在服务层和接口层具备，但前端还没有把归档按钮作为当前主交互

## 4. 第一阶段后端已落地能力

### 4.1 Session 服务层

当前 `sessions/service.py` 已经把第一阶段的核心会话逻辑收敛下来，包括：

- 创建 Session 时同时生成 `session_id` 和 `thread_id`
- 写入 `meta.json`
- 更新 `index.json`
- 读取 Session 详情
- 读取消息历史
- 归档 / 恢复 / 删除
- 追加用户消息
- 追加助手消息

在实现过程中又补上了几项很实用的优化：

- Session 默认标题为 `新对话`
- 当用户第一次发送消息后，自动用首条问题生成标题
- `index.json` 中的 `preview` 自动截断到 80 个字符
- `updated_at` 与 `last_message_at` 会随消息自动更新
- 如果一个已归档 Session 收到新消息，会先自动恢复成 `active`

这些优化让左侧会话列表能真正承担“产品层投影”的职责，而不只是存一份原始索引。

### 4.2 文件投影结构

当前仍然沿用第一阶段确定的本地文件结构：

```text
data/users/<user_id>/sessions/index.json
data/users/<user_id>/sessions/<session_id>/meta.json
data/users/<user_id>/sessions/<session_id>/messages.json
data/users/<user_id>/sessions/<session_id>/approvals.jsonl
```

其中已经实际接入并参与主流程的是：

- `index.json`
- `meta.json`
- `messages.json`
- `approvals.jsonl`

这里的“接入”含义是：

- 这些文件已经进入统一仓储读取链路
- 页面和服务层都直接围绕它们组织当前 Session 视图
- 长会话压缩属于运行时内部能力，不再额外投影成 Session 文件

### 4.3 `session_id` 与 `thread_id` 绑定

第一阶段确立的“一条 Session 绑定一条 Thread”的规则仍然成立，而且当前实现比早期约定更清晰：

- `session_id` 形如：
  - `sess_<scope>`
- `thread_id` 形如：
  - `thread_<scope>`
- 两者共享同一段时间戳与随机后缀

这样做的实际好处是：

- 日志和数据目录更容易排查
- 可以直接从 Session 快速定位对应 Thread
- 第二阶段接入真实 DeepAgent 后无需重构产品层主键模型

## 5. 第一阶段接口落地情况

当前已经落地的接口包括：

- `GET /api/v1/sessions`
- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `GET /api/v1/sessions/{session_id}/approvals`
- `POST /api/v1/sessions/{session_id}/messages`
- `POST /api/v1/sessions/{session_id}/archive`
- `POST /api/v1/sessions/{session_id}/restore`
- `DELETE /api/v1/sessions/{session_id}`
- `GET /api/v1/sessions/{session_id}/runs/{run_id}/events`

其中需要特别说明：

- SSE 事件接口目前仍返回 `501 Not Implemented`
- 也就是说接口路径已经预留，但第一阶段主链路仍然是普通请求响应

## 6. 第一阶段前端实现优化

和最初“能用就行”的目标相比，当前前端已经做了几项对真实使用更有帮助的增强：

- 登录态持久化
- 首次登录自动进入首个可用会话
- 新建会话后自动切换到新会话
- 删除当前会话后自动切换到剩余第一条会话
- 消息发送失败时自动回收乐观状态并提示错误
- 页面中明确提示“当前页面只显示当前登录用户自己的会话”

另外，主应用入口也做了两项工程化处理：

- 静态资源通过后端统一托管
- `index.html`、`app.js`、`styles.css` 返回时都带 `no-store` 等禁止缓存头

这解决了开发联调时前端缓存导致页面与代码不一致的问题。

## 7. 第一阶段非目标与当前边界

当前主干中，以下内容仍然不属于第一阶段交付范围：

- 真正接通 `mock-server` 的实时查询
- 真正接通 `mock-server` 的变更操作
- 完整审批闭环
- SSE 流式事件
- 长期记忆
- 跨 Session 检索
- 复杂前端工作流

第一阶段对 DBAAS 问题的定位仍然是：

- 产品壳、会话模型和本地投影先跑稳
- 真实 DBAAS tools 放到后续阶段逐步接入

## 8. 与当前主干的衔接说明

虽然当前代码已经进入第二阶段，第一阶段文档仍然需要保留两点事实：

1. 第一阶段交付的重点从来不是“某个特定模型实现”，而是产品层 Session 基座。
2. 第二阶段之所以能把 demo runtime 替换成真实 DeepAgent，而前端和 Session API 基本不重写，正是因为第一阶段这层基座已经先收敛稳定。

同时，当前主干还在第一阶段删除流程上补了一项跨阶段优化：

- 删除 Session 时，不仅删除产品层目录
- 还会同步清理该 Session 对应的 DeepAgent `thread_id` checkpoint 数据

这项能力严格来说属于第二阶段 runtime 接入后的增强，但它让第一阶段定义的“删除 Session”在当前代码中变成了真正更完整的删除语义。

## 9. 第一阶段完成标准

如果从当前实现倒推第一阶段是否完成，答案是“已完成”，完成标准包括：

- 用户可本地登录进入系统
- 每个用户拥有自己独立的 Session 列表
- 可以新建、打开、删除自己的 Session
- 历史消息可重新加载到当前窗口
- 同一个 Session 能持续对话
- Session 与 Thread 的绑定关系稳定保存
- 文件投影结构已经形成并可持续演进
- 第二阶段升级 runtime 时无需推翻这套产品层结构

## 10. 当前建议结论

第一阶段文档现在应当被理解为：

- 它描述的是已经验证成功的产品基座
- 不是一份待办清单
- 后续所有第二阶段、第三阶段能力，都应尽量复用这套多用户、多 Session、文件投影和页面交互基础

如果后续继续演进，第一阶段这份文档原则上不再扩充新的 runtime 细节，而是只维护“产品层基座”相关内容。
