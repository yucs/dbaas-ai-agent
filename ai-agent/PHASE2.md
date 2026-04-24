# DBAAS 智能助手第二阶段说明

## 1. 文档目的

本文档根据当前仓库的实际代码实现，更新第二阶段内容，重点说明：

- 第二阶段已经完成了哪些 runtime 升级
- 当前代码处于第二阶段的哪个子阶段
- 哪些优化已经在实现中落地
- 哪些能力仍然留在后续阶段

为了避免继续把文档写成“建议方案”，本文档以“当前代码真实状态”为准。

相关文档：

- [DESIGN.md](./DESIGN.md)
- [SESSIONS.md](./SESSIONS.md)
- [API.md](./API.md)
- [MEMORY.md](./MEMORY.md)
- [PHASE1.md](./PHASE1.md)
- [CONTEXT_ARCHITECTURE.md](./CONTEXT_ARCHITECTURE.md)

## 2. 第二阶段当前结论

截至当前代码版本，第二阶段的 `P2A` 已经完成，项目已经从第一阶段的产品壳，升级为：

- 真实 DeepAgent runtime
- 真实大模型接入
- 基于 SQLite checkpointer 的 `thread_id` 持久化
- 保留第一阶段 Session 文件投影
- 保持原有前端与 Session API 不被推翻

当前代码更准确的阶段状态是：

- `P2A`：已完成
- `P2B`：未完成
- `P2C`：未完成
- `P2D`：未完成

也就是说，第二阶段的主成果已经不再是“设计准备”，而是“真实 runtime 已经接通并运行”。

## 3. 第二阶段已实现能力

### 3.1 真实 DeepAgent runtime 已接入

当前后端已经不再使用 demo 拼接式 runtime，而是通过真实组件构建运行时：

- 使用 `deepagents.create_deep_agent(...)`
- 使用 `langchain_openai.ChatOpenAI`
- 使用 `langgraph.checkpoint.sqlite.SqliteSaver`
- 通过 `thread_id` 驱动同一会话持续执行

当前调用方式是：

- 非流式 `invoke(...)`
- 每次请求只向 agent 追加当前用户问题
- 真正的跨轮上下文由 DeepAgent checkpointer 负责恢复

这正是第二阶段最核心的升级点。

### 3.2 OpenAI-compatible 模型接入已经落地

当前配置层已经实现统一的 OpenAI-compatible 接入方式，重点配置集中在 `config.toml` 中，例如：

- `[model].model`
- `[model].base_url`
- `[model].api_key`
- `[model].provider_kind`
- `[paths].checkpoint_db`
- `[paths].system_prompt_path`
- `[paths].data_root`
- `[paths].runtime_root`
- `[paths].frontend_root`
- `[model].context_window`
- `[model].max_output_tokens`

其中当前实现的行为边界需要写清楚：

- `model + base_url + api_key` 是真实运行时的必填核心参数
- `provider_kind` 当前仅支持 `openai_compatible`
- `context_window` 与 `max_output_tokens` 已进入配置模型
- 其中 `max_output_tokens` 已用于模型创建
- `context_window` 当前主要作为预算配置保留，后续再用于更完整的上下文工程控制

这比原先文档中的“建议支持”更进一步，已经成为实际运行约束。

### 3.3 SQLite checkpointer 已启用

当前第二阶段已经正式启用 SQLite checkpointer：

- 默认文件位于 `data/runtime/checkpoints.sqlite`
- 运行时启动时自动创建父目录
- 同一个 `thread_id` 的状态可跨请求恢复
- 服务重启后仍可继续使用同一个 `thread_id`

这意味着第二阶段最核心的“线程持续性”目标已经达成。

### 3.4 Session 文件投影仍然保留

当前项目没有在第二阶段直接推翻第一阶段的 Session 文件结构，而是保留了产品层投影：

- `index.json`
- `meta.json`
- `messages.jsonl`
- `approvals.jsonl`

当前的稳定分层是：

- DeepAgent / LangGraph checkpointer
  - 负责 runtime 状态
- Session 文件投影
  - 负责产品层展示和管理

这一点非常重要，因为它证明：

- 第二阶段接入真实 runtime，不等于直接放弃 Session 产品模型
- Session 仍然是左侧会话列表、标题、预览、归档、删除的主入口

### 3.5 聊天主链路已经切换到真实 runtime

当前 `POST /api/v1/sessions/{session_id}/messages` 的执行链路已经稳定下来：

1. 记录用户消息到 `messages.jsonl`
2. 读取当前 Session 元数据与 `thread_id`
3. 识别是否属于 DBAAS 实时查询 / 操作类请求
4. 如果是实时 DBAAS 请求，直接返回边界提示
5. 否则调用真实 DeepAgent
6. 记录助手回答
7. 更新 Session 投影并返回 `run_id`、`mode`、`warning`

其中 `mode` 当前已经固定为：

- `deepagent`

这说明第二阶段的核心请求链路已经整体切换完成。

### 3.6 DBAAS guard 已从简单拦截升级为分级判断

当前 `agent/dbaas_guard.py` 不再只是粗暴判断“是不是 DBAAS 问题”，而是细分为三类：

- `normal`
- `dbaas_concept`
- `dbaas_realtime`

当前策略是：

- 普通问题：交给真实模型回答
- DBAAS 概念问题：也交给真实模型回答
- DBAAS 实时查询 / 资源操作问题：返回“当前阶段尚未启用后台查询或操作能力”

这比第二阶段原计划里的关键词拦截更贴近真实场景，也减少了不必要的误伤。

### 3.7 system prompt 已经外置化

当前运行时不再把提示词硬编码在业务逻辑中，而是支持：

- 默认读取 `backend/prompts/system.md`
- 文件不存在时回退到内置默认 prompt

当前仓库中已经存在：

- `backend/prompts/system.md`
- `backend/prompts/compression.md`

需要明确的是：

- `system.md` 已经接入真实 runtime
- `compression.md` 后续用于 `SummarizationMiddleware` 的压缩提示约束

### 3.8 生命周期管理和删除语义已补强

第二阶段当前实现还补上了几个很关键但容易遗漏的工程化细节：

- `get_agent_runtime()` 使用缓存，避免每个请求重复初始化 runtime
- 应用关闭时会主动关闭同步/异步 HTTP client 和 SQLite 连接
- 删除 Session 时，会先删除对应 `thread_id` 的 checkpoint，再删除产品层 Session 目录
- 如果 runtime 依赖缺失或配置不完整，接口会返回 `503`

这几项让当前实现比“能跑通”更接近“可维护”状态。

### 3.9 启动脚本与开发联调体验已优化

当前 `start.sh` 已经补齐了一些运行细节：

- 默认读取 `config.toml`
- 相对路径按配置文件所在目录解析
- 启动前先校验并读取同一份配置
- 优先使用已有虚拟环境中的 Python
- 默认启动 `uvicorn --reload`

同时，主应用在返回静态文件时增加了禁止缓存头，减少联调时前端缓存问题。

## 4. 第二阶段当前仍保留的边界

虽然 P2A 已完成，但当前代码仍然明确保留了第二阶段边界：

- 还没有接通 `mock-server` 实时查询
- 还没有接通 `mock-server` 变更操作
- 还没有接通审批中断恢复闭环
- 还没有接通 SSE 流式返回
- 还没有引入子 agent
- 还没有引入长期记忆

当前系统的真实能力边界应该描述为：

- 普通问答：可用
- DBAAS 概念解释：可用
- DBAAS 实时数据查询：未启用
- DBAAS 写操作：未启用

## 5. 第二阶段未完成项

### 5.1 `P2B`：Session 存储抽象与 SQLite Session Store

这一部分当前还没有落地。

目前仍然是：

- Session 主存储使用文件投影
- runtime 持久化使用 SQLite checkpointer

也就是说：

- 当前是“文件 Session + SQLite runtime”
- 还没有实现 `SessionStore` 抽象
- 也还没有实现 `SQLiteSessionStore`

后续如果进入 `P2B`，再做下面这些事会更合适：

- 抽象统一 Session 存储接口
- 支持 `file` / `sqlite` 二选一
- 保持单主存储，不做双写

### 5.2 `P2C`：SSE 流式返回

当前 SSE 路由已经预留，但实现状态仍然是：

- `GET /api/v1/sessions/{session_id}/runs/{run_id}/events`
  - 返回 `501 Not Implemented`

因此第二阶段目前仍是：

- 真实 DeepAgent
- 非流式接口返回

这和当前代码完全一致，也符合现阶段排错边界更清晰的目标。

### 5.3 `P2D`：DBAAS tools 与审批闭环

当前还没有进入真正的 DBAAS agent 阶段。

尚未落地的内容包括：

- 对接 `mock-server` 只读查询 tools
- 对接 `mock-server` 写操作 tools
- 写操作 `interrupt_on`
- 审批记录驱动的恢复执行
- 异步任务跟踪

因此第二阶段当前只能算“runtime 升级完成”，还不能算“DBAAS agent 能力完成”。

### 5.4 上下文压缩能力已在后续阶段接入

第二阶段文档需要补充一个现实说明：

- 当前主干代码已经继续演进
- 长会话压缩已经接入真实主链路
- 压缩优先留在 `SummarizationMiddleware` 与原 `thread_id` 内部
- 不再把 `summary.json` 作为 Session 恢复输入

更具体的当前实现，统一以 [PHASE3.md](./PHASE3.md) 和 [CONTEXT_ARCHITECTURE.md](./CONTEXT_ARCHITECTURE.md) 为准。

## 6. 第二阶段完成标准与当前验收结果

如果用当前代码回看第二阶段最初目标，可以得到下面这个结果：

已完成：

- 使用真实大模型回答普通问题
- 同一个 Session 多轮问答复用同一个 `thread_id`
- 服务重启后仍可继续同一个 Thread
- 保留原有多用户、多 Session 页面模型
- 页面和 Session 管理逻辑未因 runtime 升级而推翻
- DBAAS 概念问题可继续由模型回答
- DBAAS 实时查询 / 操作类问题会明确提示边界

未完成：

- Session 主存储切换到 SQLite
- SSE 流式事件
- DBAAS tools
- 审批闭环

因此当前最准确的验收结论是：

- 第二阶段核心 runtime 升级目标已完成
- 后续剩余的是 tools、审批、异步任务和流式链路等后半段能力

## 7. 当前建议结论

第二阶段文档现在应该明确表达成下面这句话：

- 当前仓库已经完成第二阶段 `P2A`
- 当前最稳妥的后续推进顺序是先做 `P2B` 或 `P2C`
- 不建议在还未收敛 Session 存储和流式链路前，就同时把 DBAAS tools、审批、异步任务全部并进

换句话说，第二阶段不再是“准备接 DeepAgent”，而是“已经接上真实 DeepAgent，接下来要继续把运行链路做厚”。
