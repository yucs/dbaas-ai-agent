# 长期设计备忘

本文档记录已经讨论清楚、但短期不进入实现阶段的能力设计。

这里的内容不是当前版本承诺，也不占用 `PHASE11`、`PHASE12` 等阶段编号。
当某个长期能力真正进入排期时，再从本文档迁出到新的 Phase 文档或专项设计中。

## 1. Agent Run 中断

### 1.1 背景

当前对话框使用流式接口向后端发送用户消息：

```text
POST /api/v1/sessions/{session_id}/messages/stream
```

后端在当前 Session 上获取 `session_run_lock`，追加用户消息，然后调用
`DeepAgentRuntime.stream_reply(...)`。运行时内部通过 DeepAgent/LangGraph 的
`agent.stream(...)` 推进当前 graph run。

当前能力边界：

- 同一 Session 同一时间只允许一个 DeepAgent run
- `run_id` 会随 SSE `started` 事件返回给前端
- 当前没有 active run registry
- 当前没有按 `run_id` 取消 run 的 API
- 当前没有前端“中断”按钮
- 如果用户在 run 未结束时继续发送消息，会被 `session_run_lock` 拒绝

### 1.2 目标

支持用户在当前 AI 回复生成过程中，按按钮中断当前 `run_id` 对应的
DeepAgent graph run。

中断语义：

```text
用户点击中断
-> 后端标记当前 run 已取消
-> 当前 agent.stream event 返回后，runtime 看到取消标记
-> 停止继续消费 DeepAgent stream
-> 不再推进后续 LLM / tool loop
-> 不保存未完成 assistant 正常回复
-> 写入一条系统或 ai-agent 中断说明
-> 释放 session_run_lock
-> 当前 Session 可以继续下一轮提问
```

### 1.3 非目标

第一版不追求强杀正在执行中的底层调用：

- 不保证立即打断正在 thinking 且尚未吐 token 的模型 HTTP 请求
- 不保证立即终止正在执行中的同步 tool
- 不撤销已经执行完成的 tool
- 不取消或回滚已经创建的 DBAAS 异步任务
- 不替代 Phase7 的 approval / operation / task 审计链路

第一版关注的是：当前 event 或当前 tool 返回后，不再继续后续 agent loop。

### 1.4 后端设计草案

新增 active run 控制模块，例如：

```text
backend/src/dbass_ai_agent/agent/run_control.py
```

核心模型：

```text
session_id
run_id
cancel_requested
created_at
closed_at
optional stream_closer
```

核心能力：

- 注册当前 active run
- 按 `session_id + run_id` 标记取消
- 查询当前 run 是否已取消
- 注销已完成、已取消或异常结束的 run
- 可选：保存当前 stream iterator 的 `close()` 回调，取消时尝试关闭

### 1.5 API 草案

新增取消接口：

```http
POST /api/v1/sessions/{session_id}/runs/{run_id}/cancel
```

行为约束：

- 该接口不得获取 `session_run_lock`
- 该接口只标记 active run 取消
- 如果 run 不存在或已结束，可以返回幂等成功或明确的已结束状态
- 普通用户只能取消自己当前 Session 中的 active run
- 管理员也应遵守 Session 归属和当前身份上下文

示例响应：

```json
{
  "session_id": "sess_xxx",
  "run_id": "run_xxx",
  "status": "cancel_requested"
}
```

### 1.6 Runtime 设计草案

当前同步流式路径：

```text
DeepAgentRuntime.stream_reply(...)
-> _stream_agent_text(...)
-> agent.stream(...)
```

第一版可以继续基于 `agent.stream(...)`：

```python
events = agent.stream(input_payload, config=config, stream_mode=["messages", "updates"])
for event in events:
    if run_control.is_canceled(session_id, run_id):
        raise AgentRunCanceled(run_id)
    ...
```

取消发生后：

- runtime 停止继续消费 `events`
- 如果 `events` 支持 `close()`，尝试调用
- 向 route 返回 `run_canceled` 语义事件或抛出专门异常
- route 负责写入中断说明并释放锁

重要边界：

- 如果代码正阻塞在下一次 `next(events)`，取消标记需要等下一次 event 返回后才能被看到
- 如果当前 LLM 一直不吐 token，第一版不承诺立即结束 provider 侧请求
- 如果当前 tool 已经开始执行，第一版等待该 tool 返回或超时后再停止后续 loop

### 1.7 前端设计草案

前端在发送中保存：

```text
currentRunId
messageStreamController
```

交互：

- 发送中按钮从“发送”切换为“中断”
- 收到 SSE `started` 后记录 `run_id`
- 点击“中断”：
  - 调用取消 API
  - abort 当前 fetch stream
  - 将 pending assistant 卡片更新为“本轮已中断”
  - 允许用户继续输入下一条消息

### 1.8 消息与审计

建议落库策略：

- 用户消息保留
- 不保存半截 assistant 正常回复
- 写入一条 `system` 或 `ai_agent` 消息：

```text
本轮 run_id=run_xxx 已由用户中断，未生成完整回复。
```

这样后续同一 thread 恢复时，大模型能看到上一轮被用户中断的事实，
不会把半截输出当成完整回答继续推断。

### 1.9 测试清单

后续实现时至少覆盖：

- active run 注册、取消、注销
- cancel API 不被 `session_run_lock` 阻塞
- cancel API 不能取消其他用户 Session 的 run
- runtime 在 event 间看到 cancel 后停止继续消费 stream
- cancel 后不保存完整 assistant 正常回复
- cancel 后释放 `session_run_lock`
- cancel 后当前 Session 可以继续发送下一条消息
- 当前 run 已结束时重复 cancel 的幂等行为

### 1.10 后续升级方向

如果未来需要更强的中断语义，可以评估：

- 将主流式链路从 `agent.stream(...)` 升级为 `agent.astream(...)`
- 每个 run 使用独立 `asyncio.Task`
- cancel API 调用 `task.cancel()`
- 尝试关闭底层 async HTTP stream
- 为可取消的 DBAAS 只读工具提供 async 实现或更短 timeout

这些升级可以提升 thinking 阶段和同步阻塞调用中的中断及时性，
但不是第一版 run 中断设计的前提。

## 2. DBAAS jq 查询完整结果导出与复用分析

### 2.1 背景

当前 DBAAS services / backups 查询工具使用 jq 对本地数据视图执行查询，
并只将截断后的 `preview` 返回给模型上下文。

该设计可以避免大体积原始数据直接进入 LLM 上下文，但在以下场景中不够：

- 用户明确需要完整明细清单
- 查询结果被截断后，用户需要下载全部匹配记录
- 用户希望基于某次完整查询结果继续做分组、统计或二次筛选

### 2.2 目标

未来支持 jq 查询结果的受控导出：

- 默认查询仍只返回截断 `preview`
- 只有用户明确要求“导出”、“保存文件”、“下载完整明细”或“生成清单文件”时才写文件
- 导出时完整 jq 结果写入运行时导出目录
- tool 返回截断 `preview`、`export_id`、文件元信息和下载入口
- 后续可以基于 `export_id` 对同一份导出结果继续执行 jq 分析

### 2.3 非目标

第一版不做以下能力：

- 不把完整导出内容直接塞进模型上下文
- 不允许模型或用户传入任意输出路径
- 不把本地绝对文件路径直接暴露为前端下载地址
- 不自动为所有截断查询写导出文件
- 不承诺导出文件永久保存

### 2.4 Tool 行为草案

现有查询工具未来可以增加导出参数：

```text
export_full_result: bool = false
export_name_hint: str | None = None
```

行为规则：

- `export_full_result=false` 时，保持当前行为，只返回截断 `preview`
- `export_full_result=true` 时，将完整 jq 输出写入服务端生成的导出文件
- `export_name_hint` 只作为文件名前缀建议，必须经过服务端清洗
- 最终目录、文件名、扩展名和访问权限由服务端决定

示例返回：

```json
{
  "status": "success",
  "preview": [],
  "preview_count": 50,
  "truncated": true,
  "export": {
    "export_id": "exp_20260610_173012_a8f3",
    "display_name": "backup-strategy-conflicts-20260610-173012.jsonl",
    "format": "jsonl",
    "record_count": 1280,
    "size_bytes": 384920,
    "sha256": "...",
    "download_url": "/api/v1/exports/exp_20260610_173012_a8f3/download",
    "truncated": false
  },
  "message": "查询结果较大，已返回截断预览，并将完整结果导出为文件。"
}
```

这里的 `truncated=true` 表示返回给模型的 preview 被截断；
`export.truncated=false` 表示导出文件中的结果未被截断。

### 2.5 对话框下载体验

前端收到 tool 结果或 assistant 消息中的 export 元信息后，可以在对话框内渲染下载附件：

```text
完整明细已导出：backup-strategy-conflicts-20260610-173012.jsonl
[下载]
```

下载按钮不应直接使用本地 `file_path`，而应请求后端受控下载接口：

```http
GET /api/v1/exports/{export_id}/download
```

下载接口职责：

- 校验当前用户是否有权访问该 `export_id`
- 校验导出文件是否存在、未过期、未超过权限边界
- 设置安全的 `Content-Disposition` 文件名
- 返回文件流

如果导出文件已过期或不存在，前端应提示用户重新导出。

### 2.6 基于导出结果继续分析

后续可以增加导出结果查询工具：

```text
query_dbaas_export_result_tool
```

参数草案：

```json
{
  "export_id": "exp_20260610_173012_a8f3",
  "jq_filter": "group_by(.serviceType) | map({serviceType: .[0].serviceType, count: length})",
  "max_preview_items": 50
}
```

该工具只允许通过 `export_id` 定位文件，不接受任意文件路径。
模型仍然只接收 jq 分析后的聚合结果或截断 preview，
不直接读取完整大文件。

这种设计可以保证后续分析基于用户当时导出的同一份结果，
避免后续 DBAAS snapshot 刷新导致“这些数据”的语义变化。

### 2.7 安全与治理

后续实现时需要考虑：

- 导出文件按用户、身份、Session 或 thread 做访问隔离
- 导出目录位于 runtime workspace 内
- 使用临时文件写入，完成后 `os.replace` 原子发布
- 配置最大导出字节数，超限时返回 `export_too_large`
- 配置导出文件 TTL 和清理机制
- 下载接口不暴露服务端真实路径
- 日志记录导出行为，但避免记录完整数据内容
