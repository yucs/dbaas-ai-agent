# DBAAS 智能助手第一阶段计划

## 1. 文档目的

本文档用于收敛第一阶段的 MVP 目标、范围、边界和实现顺序。

这份文档不替代总设计文档，而是作为“当前先做什么”的执行基线。

相关文档：

- [DESIGN.md](./DESIGN.md)
- [SESSIONS.md](./SESSIONS.md)
- [API.md](./API.md)
- [MEMORY.md](./MEMORY.md)

## 2. 第一阶段目标

第一阶段的核心目标不是完整实现 DBAAS 操作助手，而是先做出一个可运行的最小版本，重点验证：

- 多用户
- 多 Session
- 登录后进入对话页
- 可以查看自己的历史 Session
- 可以创建和切换 Session
- 选中某个 Session 后，可以基于该 Session 继续问答
- 初版 AI 后台可以回答一般问题

## 3. 第一阶段用户流程

第一阶段页面流程建议如下：

1. 用户进入登录页
2. 输入用户名
3. 选择用户类型
   - `admin`
   - `user`
4. 登录成功后进入对话页
5. 页面左侧展示当前用户的历史 Session 列表
6. 用户可以：
   - 新建 Session
   - 打开历史 Session
   - 归档 Session
   - 删除 Session
     删除后会直接移除 Session 目录
7. 用户在当前窗口继续输入问题
8. 后端基于当前 Session 对应的 `thread_id` 继续执行

## 4. 第一阶段页面范围

### 4.1 登录页

第一阶段只需要一个非常简单的登录页：

- 用户名输入框
- 用户类型选择框
- 登录按钮

第一阶段不需要真正接入统一登录系统。

页面提交的信息即可形成当前产品层身份快照，例如：

- `user_id`
- `role`
- `user`

建议规则：

- 如果用户类型为 `user`
  - 第一阶段可简化为 `user = user_id`
- 如果用户类型为 `admin`
  - 后端对接 `mock-server` 时使用 `admin`

### 4.2 对话页

对话页第一阶段建议包含以下最小区域：

- 左侧 Session 列表
- 新建 Session 按钮
- 当前 Session 标题
- 消息列表区域
- 输入框
- 发送按钮
- 可选的归档/删除按钮

第一阶段不需要复杂样式，重点是交互流程跑通。

## 5. 第一阶段后端范围

### 5.1 需要实现的能力

- 登录后的本地用户上下文初始化
- 多用户 Session 目录管理
- Session 列表读取
- Session 详情读取
- 新建 Session
- 归档 Session
- 删除 Session
- 当前 Session 下发送消息
- 简单的流式或准流式返回
- 基于 DeepAgent 的初版 AI 后台

### 5.2 初版 AI 后台要求

第一阶段 AI 后台只要求：

- 能回答普通问题
- 能在同一个 Session 下持续对话
- 能结合当前 Session 继续窗口问答

第一阶段暂不要求：

- 真正接 `mock-server` 做 DBAAS 查询
- 真正接 `mock-server` 做 DBAAS 变更
- 强制人工确认的完整流程闭环
- 完整异步任务跟踪

## 6. 与 DeepAgent 的关系

第一阶段仍然默认项目基于 DeepAgent 实现。

但这里要明确：

- 第一阶段重点验证的是 Session 模型和基本问答流程
- 不是第一时间把 DBAAS tools 和审批流全部接完

因此第一阶段建议：

- 保留 `session_id -> thread_id` 绑定模型
- AI 对话仍通过 DeepAgent runtime 驱动
- 先不启用真正的 DBAAS tool 调用

## 7. 第一阶段对 mock-server 的处理策略

第一阶段如果用户问到了 DBAAS 查询或 DBAAS 操作，不要求真正调用 `mock-server`。

建议行为：

- 后端先识别这是 DBAAS 场景问题
- 给出明确提示：
  - 当前第一阶段后台尚未启用 `mock-server` 调用能力
  - 当前版本仅支持基础问答和 Session 管理

这样做的好处：

- 不阻塞第一阶段页面与 Session 能力落地
- 避免半完成状态下出现错误或误导
- 方便后续第二阶段逐步接入 DBAAS tools

## 8. 第一阶段 Session 规则

第一阶段继续沿用现有 Session 设计：

- 一个 `session_id`
- 对应一个 `thread_id`

页面选择某个 Session 后：

- 先读取该 Session 的历史消息
- 加载到当前窗口
- 后续继续发问时复用原来的 `thread_id`

这正是第一阶段最重要的验证点之一。

## 9. 第一阶段推荐接口范围

建议第一阶段至少实现以下接口：

- `GET /api/v1/sessions`
- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/messages`
- `POST /api/v1/sessions/{session_id}/archive`
- `POST /api/v1/sessions/{session_id}/restore`
- `DELETE /api/v1/sessions/{session_id}`

如果流式能力一起做，则补充：

- `GET /api/v1/sessions/{session_id}/runs/{run_id}/events`

## 10. 第一阶段本地存储范围

建议第一阶段继续使用本地目录：

```text
data/users/<user_id>/sessions/index.json
data/users/<user_id>/sessions/<session_id>/meta.json
data/users/<user_id>/sessions/<session_id>/messages.jsonl
data/users/<user_id>/sessions/<session_id>/summary.json
data/users/<user_id>/sessions/<session_id>/approvals.jsonl
```

其中第一阶段重点使用：

- `index.json`
- `meta.json`
- `messages.jsonl`

`summary.json` 和 `approvals.jsonl` 可以先保留结构，但实现上不必一次做重。

## 11. 第一阶段建议代码组织结构

第一阶段建议采用“前后端分目录、后端按职责分层、本地数据独立存放”的组织方式。

推荐目录如下：

```text
ai-agent/
  DESIGN.md
  SESSIONS.md
  API.md
  MEMORY.md
  PHASE1.md

  data/
    users/
      <user_id>/
        sessions/
          index.json
          <session_id>/
            meta.json
            messages.jsonl
            summary.json
            approvals.jsonl

  backend/
    pyproject.toml
    src/dbass_ai_agent/
      main.py
      config.py

      api/
        deps.py
        schemas.py
        routes_sessions.py
        routes_chat.py
        routes_runs.py

      sessions/
        models.py
        service.py
        repository.py
        index_store.py
        message_store.py
        summary_store.py
        approval_store.py
        thread_binding.py

      agent/
        runtime.py
        prompt.py
        dbaas_guard.py

      identity/
        models.py
        resolver.py

      infra/
        paths.py
        ids.py
        clock.py

  frontend/
    index.html
    styles.css
    app.js
```

### 11.1 组织原则

- `data/`
  - 保存真实 Session 数据
  - 不与代码目录混放
- `backend/`
  - 负责 HTTP API、Session 管理、DeepAgent 对接和本地文件读写
- `frontend/`
  - 负责登录页、对话页、Session 列表和消息展示
  - 第一阶段为了降低依赖，先采用静态单页实现

### 11.2 后端职责建议

- `main.py`
  - 应用入口
  - 组装路由与配置
- `api/`
  - 只负责对外接口
  - 不直接写文件
- `sessions/`
  - 负责 Session 模型、Session 生命周期和本地会话数据读写
  - 是第一阶段最核心的业务模块
- `agent/`
  - 负责 DeepAgent runtime 封装
  - 第一阶段先支持普通问答
  - 对 DBAAS 问题返回“尚未启用 mock-server 调用”的提示
- `identity/`
  - 负责当前用户身份解析
  - 例如从请求头中提取 `user_id`、`role`、`user`
- `infra/`
  - 放路径、ID 生成、时间工具等基础能力

### 11.3 前端职责建议

- `frontend/index.html`
  - 提供简单登录页和对话页骨架
- `frontend/styles.css`
  - 提供第一阶段页面样式
- `frontend/app.js`
  - 管理登录状态、Session 列表加载、当前会话切换和发送消息
  - 通过请求头把 `user_id`、`role`、`user` 传给后端

### 11.4 核心文件职责说明

建议把第一阶段真正关键的文件职责写清楚：

- `api/routes_sessions.py`
  - 提供 Session 列表、详情、新建、归档、恢复、删除接口
- `api/routes_chat.py`
  - 提供当前 Session 下发送消息接口
- `api/routes_runs.py`
  - 如果第一阶段引入 SSE，则放运行流接口
- `api/deps.py`
  - 统一解析当前用户身份

- `sessions/service.py`
  - Session 管理的业务入口
  - 负责把多个 Session 操作串成完整流程
  - 例如：
    - 创建新 Session
    - 加载某个 Session
    - 校验 Session 是否属于当前用户
    - 归档、恢复、删除
    - archived Session 收到新消息时先自动 restore
    - 更新 `index.json` 中的 `preview`、`updated_at`、`last_message_at`

- `sessions/repository.py`
  - Session 数据读写的统一入口
  - 对上层屏蔽底层文件组织细节

- `sessions/index_store.py`
  - 负责读写 `index.json`
  - 支撑左侧历史 Session 列表

- `sessions/message_store.py`
  - 负责读写 `messages.jsonl`
  - 支撑历史消息加载与新消息追加

- `sessions/summary_store.py`
  - 负责读写 `summary.json`
  - 第一阶段可以先保持轻量

- `sessions/approval_store.py`
  - 负责读写 `approvals.jsonl`
  - 第一阶段可先保留结构，不要求完整闭环

- `sessions/thread_binding.py`
  - 负责维护 `session_id -> thread_id` 关系
  - 保证页面选中历史 Session 后可以继续在原会话上下文中对话

- `agent/runtime.py`
  - 对 DeepAgent 的最小封装
  - 第一阶段先支持普通问答

- `agent/dbaas_guard.py`
  - 识别 DBAAS 相关问题
  - 当前阶段统一返回“mock-server 调用尚未启用”的提示

- `identity/resolver.py`
  - 统一把外部传入的身份信息整理成内部身份模型
  - 例如：
    - `user_id`
    - `role`
    - `user`

### 11.5 第一阶段最小实现建议

如果希望第一版尽快跑通，可以把代码组织收敛到下面这个最小核心：

- 后端
  - `api/routes_sessions.py`
  - `api/routes_chat.py`
  - `sessions/service.py`
  - `sessions/repository.py`
  - `sessions/index_store.py`
  - `sessions/message_store.py`
  - `agent/runtime.py`
  - `identity/resolver.py`
- 前端
  - `frontend/index.html`
  - `frontend/styles.css`
  - `frontend/app.js`

这样既能保持结构清晰，也不会在第一阶段把工程拆得太重。

## 12. 第一阶段非目标

以下内容明确不属于第一阶段核心目标：

- 真正打通 `mock-server` 查询能力
- 真正打通 `mock-server` 变更能力
- 完整审批流
- 完整异步任务流
- 复杂前端设计
- 完整权限体系
- 跨 Session 检索

## 13. 推荐实现顺序

建议按下面顺序推进：

1. 搭建最小前后端骨架
   - 登录页
   - 对话页
   - Session 目录读写

2. 跑通多用户多 Session
   - 登录后看到自己的 Session 列表
   - 可以新建和切换 Session
   - 可以归档和删除 Session

3. 跑通当前窗口继续问答
   - 加载历史消息
   - 复用 `thread_id`
   - 新消息写入本地文件

4. 接入初版 DeepAgent 后台
   - 先支持普通问答
   - 对 DBAAS 问题返回“尚未启用 mock-server 调用”的提示

5. 视情况补充 SSE
   - 如果第一阶段希望页面体验更接近真实聊天产品，则加入 SSE
   - 否则可先用简单请求响应跑通主流程

## 14. 当前建议结论

第一阶段应该先聚焦这件事：

- 做一个简单可运行的多用户、多 Session 聊天产品壳
- 用它验证 Session 列表、历史加载、继续问答和 `thread_id` 绑定
- 让初版 AI 后台先具备基础问答能力
- DBAAS 与 `mock-server` 集成延后到下一阶段

如果第一阶段把这几件事跑通，第二阶段再接 DBAAS tools、审批流和异步任务，会稳很多。
