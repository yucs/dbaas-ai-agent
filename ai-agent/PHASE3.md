# DBAAS 智能助手第三阶段当前状态

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

对应代码主要在：

- [backend/src/dbass_ai_agent/agent/factory.py](./backend/src/dbass_ai_agent/agent/factory.py)
- [backend/src/dbass_ai_agent/agent/runtime.py](./backend/src/dbass_ai_agent/agent/runtime.py)
- [backend/src/dbass_ai_agent/config.py](./backend/src/dbass_ai_agent/config.py)

## 3. 本阶段明确不做

- `summary.json`
- `SessionSummary`
- 项目侧 `summary_store`
- `on_summary` 回调落盘
- 前端压缩事件提示
- `session_events`
- 独立记忆系统
- 跨 Session 语义层

## 4. 当前压缩配置

本阶段真正生效的压缩配置是：

- `compression_enabled`
- `soft_trigger_tokens`
- `keep_recent_messages`
- `summary_max_tokens`
- `compression_prompt_path`

这些配置都已经进入运行时，
不再只是文档上的预留项。

## 5. 当前用户侧感知

对用户来说，本阶段的效果是：

- 长会话还能继续问答
- 页面历史消息不会因为压缩而消失
- Session 不会因为压缩切换新的 `thread_id`
- 后端会在压缩发生时输出日志

## 6. 钩子扩展方向

当前这一版实现已经证明：

- 可以在自定义 `SummarizationMiddleware` 包装层里追加项目侧动作
- 不需要重新引入 `summary.json`
- 也不需要放弃 `create_deep_agent()`

当前已经落地的钩子动作是：

- 压缩日志

后续如果产品需要，也可以沿同一位置扩展：

- SSE 压缩提醒
- 前端提示事件
- metrics / tracing
- 其他非持久化观测动作

## 7. 当前验证状态

当前仓库已经补了压缩相关测试，覆盖：

- 配置映射到 `SummarizationMiddleware`
- middleware 压缩触发与有效上下文替换
- 压缩日志输出

测试位置见：

- [backend/tests/test_factory.py](./backend/tests/test_factory.py)
- [backend/tests/test_summarization_middleware.py](./backend/tests/test_summarization_middleware.py)

## 8. 后续顺序

第三阶段之后如果继续推进，更合理的顺序是：

1. Tools 主链路
2. 审批闭环
3. 异步任务链路
4. 再决定是否需要事实层记忆或事件层

在那之前，不建议重新引入业务侧摘要存储。
