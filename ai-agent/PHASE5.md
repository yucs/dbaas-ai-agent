# DBAAS 智能助手第五阶段设计讨论

## 1. 当前阶段目标

第五阶段开始对接 `dbaas-mock-server`。

本阶段优先解决的问题不是让大模型直接读取完整 DBAAS 数据，
而是建立一套可复用、可隔离、可验证的数据快照机制。

当前设计目标：

- 服务列表、主机、集群、实时状态等数据可以落盘到会话或运行沙箱
- 原始接口数据不直接进入大模型上下文
- 计算、统计、过滤和查询统一交给 `jq` 等确定性工具
- 大模型负责编排工具、解释查询结果和生成用户可读结论
- 后续继续沿用 DeepAgent 的 tool calling、thread 延续、streaming、checkpoint 和上下文压缩能力

## 2. 当前核心结论

第五阶段当前倾向采用：

- 管理员 services 快照保持后台常驻同步
- 普通用户 services 快照按用户身份独立保存
- 普通用户快照由会话活跃状态驱动同步，不做全局常驻同步
- 同步生成对应元数据，例如 `services.meta.json`
- Agent 可见 tool 不接受任意文件路径、不接受模型填写的身份参数
- 数据过期、缺失、刷新失败等状态通过 meta 或 tool 返回结构表达
- 过期快照不再作为 Agent 查询依据，避免旧数据误导用户

也就是说，后台任务负责保持管理员快照新鲜；
普通用户打开会话后，系统按当前用户身份维护该用户自己的服务快照。
大模型 tool 在用户查询时只读取当前身份对应的已发布快照。

## 3. 服务列表快照文件

服务列表每个身份 scope 内采用两个固定文件：

```text
services.json
services.meta.json
```

管理员快照路径：

```text
data/runtime/dbaas_workspace/
  admin/
    services.json
    services.meta.json
```

普通用户快照路径：

```text
data/runtime/dbaas_workspace/
  users/
    {safe_user}/
      services.json
      services.meta.json
```

例如：

```text
data/runtime/dbaas_workspace/users/payment-team-prod/services.json
data/runtime/dbaas_workspace/users/payment-team-prod/services.meta.json
```

`{safe_user}` 使用安全文件名转换，只允许 `[a-zA-Z0-9._-]`；
其他字符替换为 `_`，转换后为空时使用 `unknown`。

管理员 `services.json` 保存完整服务列表原始快照。
普通用户 `services.json` 只保存当前用户可见服务。
普通用户快照必须符合 `services.user.v1` schema，
该 schema 是管理员 schema 的安全字段投影，
不包含所在主机、主机 IP、节点、资源池或平台内部字段等普通用户不可见字段。

管理员快照结构必须符合 `services.admin.v1` schema。
普通用户快照结构必须符合 `services.user.v1` schema。

`services.meta.json` 作为 tool 返回给大模型的主要结构体，
用于说明当前快照是否可用、何时刷新、是否过期以及数据文件在哪里。

services meta 应固定包含以下字段，查询工具必须校验 `scope`、`user`、`schema_version`、
`schema_path`、`data_path` 与当前身份和当前 workspace 一致。
这和早期 meta 示例差别不大，主要是从“建议记录”收敛成 admin/user 快照都必须遵守的校验契约。

```json
{
  "kind": "services",
  "version": 1,
  "scope": "user",
  "user": "payment-team-prod",
  "data_path": ".../users/payment-team-prod/services.json",
  "meta_path": ".../services.meta.json",
  "status": "fresh",
  "synced_at": "2026-04-28T10:00:00+08:00",
  "expires_at": "2026-04-28T10:00:30+08:00",
  "ttl_seconds": 30,
  "record_count": 0,
  "bytes": 0,
  "source": "dbaas-mock-server",
  "source_endpoint": "/services",
  "schema_version": "services.user.v1",
  "schema_path": "config/schemas/services.user.v1.schema.json",
  "last_refresh_status": "success",
  "last_error": null
}
```

## 4. 后台同步策略

管理员 services 后台任务可以按固定间隔执行，例如每 5 秒一次。

管理员后台每次执行时都应该尝试拉取最新数据，并在成功后更新：

```text
admin/services.json
admin/services.meta.json
```

推荐更新流程：

1. 使用管理员身份调用 `dbaas-mock-server` 获取服务列表
2. 将响应写入临时文件，例如 `services.json.tmp`
3. 校验临时文件是合法 JSON
4. 统计记录数、文件大小和刷新时间
5. 写入临时 meta 文件，例如 `services.meta.json.tmp`
6. 原子替换 `services.json`
7. 原子替换 `services.meta.json`

管理员后台同步是 admin 快照的唯一写者，因此 admin 快照不需要资源锁。
`jq` 查询不获取锁，直接读取当前正式 `services.json`。
如果 `jq` 在替换前打开文件，会读旧文件；
如果在替换后打开文件，会读新文件。
`os.replace` 保证不会读到半截 JSON。
`services.json` 和 `services.meta.json` 两次替换之间的短暂不一致窗口可以接受。

普通用户 services 快照不做全局常驻后台同步。
用户打开会话后，后端为当前用户注册 active lease，并触发一次该用户 services 快照刷新。
同一个普通用户打开多个 session 时，共用同一份 `users/{safe_user}/services.json`。
会话活跃期间，系统可以按 `sync_interval_seconds` 周期刷新该用户快照。
会话关闭后移除 lease；如果该用户没有任何活跃 session，就停止该用户的周期刷新，
并删除该用户对应的 `services.json` 和 `services.meta.json`。

第一版不新增专门 heartbeat API。
现有会话接口可作为 active lease 续约信号：

- `GET /api/v1/sessions/{session_id}`
  - 用户打开或查看会话详情时续约当前身份 lease
- `POST /api/v1/sessions/{session_id}/messages`
  - 用户发送消息时续约当前身份 lease

浏览器关闭事件不可靠，因此 active lease 必须配合 heartbeat / idle timeout 回收。
如果超过配置的空闲时间没有活跃心跳，例如 1 到 5 分钟，
系统应认为该用户不再活跃，停止普通用户快照同步，并删除：

```text
data/runtime/dbaas_workspace/users/{safe_user}/services.json
data/runtime/dbaas_workspace/users/{safe_user}/services.meta.json
```

普通用户刷新应使用当前用户身份调用 DBAAS：

```text
Authorization: Bearer user:{identity.user}
```

管理员刷新使用：

```text
Authorization: Bearer admin
```

admin 和普通用户应复用同一套 services snapshot refresh 状态机。
两者只在路径、DBAAS auth、schema、触发方式和锁粒度上不同；
刷新成功、刷新失败、过期删除和 meta 状态语义应保持一致。

刷新失败时采用统一规则：

- 如果当前仍有 fresh 快照，保留现有 `services.json` 和 `services.meta.json`，
  查询仍可继续使用该 fresh 快照；
  同时更新 meta 中的 `last_refresh_status: "error"` 和 `last_error`，
  但 `status` 仍保持 `fresh`，`data_path` 仍指向当前可用快照。
- 如果当前没有 fresh 快照，或旧快照已经 stale，
  删除旧 `services.json`，写入 `status: "error"` 的 `services.meta.json`，
  其中 `data_path` 必须为 `null`，`last_refresh_status` 为 `error`，
  `last_error` 记录本次 DBAAS 请求或 schema 校验失败原因。
- 会话关闭、lease 过期或用户不活跃属于正常回收，
  直接删除普通用户的 `services.json` 和 `services.meta.json`，
  不写 `status: "error"` 的 meta。
- Agent 可见 tool 不能返回 stale 快照路径，也不能基于旧数据猜测。

普通用户打开会话或 `GET /api/v1/sessions/{session_id}` 查看会话详情时，
应立即触发一次异步 prewarm，但不阻塞会话详情接口返回。
如果用户随即发起 services 查询，而该用户快照仍缺失，
query tool 可以等待当前用户 prewarm / refresh 最多 3 秒。
3 秒后如果仍没有 fresh 快照，
tool 返回 `snapshot_unavailable`，
模型应说明当前用户服务快照仍在刷新或 DBAAS 拉取失败，不基于旧数据猜测。
如果普通用户快照已经存在但过期，
query tool 不主动刷新 DBAAS，
直接返回 `snapshot_unavailable`；
过期快照由 active lease 周期刷新流程处理。

普通用户刷新和删除必须使用 per-user lock。
锁粒度为 `users/{safe_user}`：

- 同一普通用户的多个 session 只允许一个刷新任务实际请求 DBAAS
- 其他并发查询可以等待同一个刷新任务，最长等待 `user_snapshot_refresh_wait_seconds`
- 删除用户快照时必须先获取同一个 per-user lock，避免和刷新并发
- `jq` 查询本身不加锁；如果查询打开文件前快照刚好被删除或替换，直接返回 `snapshot_unavailable` 或重试一次后再返回错误
- 管理员后台同步仍保持当前 admin 单写者模型，不需要 per-user lock

旧的过期快照应直接删除，
tool 对外返回时不能再提供旧的过期 `data_path`。
如果过期文件已删除且本次拉取失败，
应写入 `status: "error"`、`data_path: null` 的 meta，
并由 tool 返回 `snapshot_unavailable`。

开发阶段不要求兼容旧 services 快照文件和旧 schema 名。
现有 `config/schemas/services.v1.schema.json` 应改名为：

```text
config/schemas/services.admin.v1.schema.json
```

并新增：

```text
config/schemas/services.user.v1.schema.json
```

旧运行时文件、旧 schema version 的 meta、历史 `.tmp` 文件可以在启动清理或本地手动清理时删除，
不提供数据迁移脚本。

后台同步间隔、TTL、快照根目录和 `dbaas-mock-server` 地址必须写入 `config.toml`，
不能在代码中写死。

例如：

```toml
[dbaas_server]
base_url = "http://127.0.0.1:8001"
request_timeout_seconds = 5

[dbaas_workspace]
dir = "./data/runtime/dbaas_workspace"
sync_interval_seconds = 5
ttl_seconds = 30
user_active_idle_timeout_seconds = 300
user_snapshot_refresh_wait_seconds = 3
jq_timeout_seconds = 3
jq_max_preview_items = 50
jq_max_output_bytes = 1048576
```

其中：

- `dbaas_server.base_url`
  - `dbaas-mock-server` 的基础地址
- `dbaas_server.request_timeout_seconds`
  - 调用 `dbaas-mock-server` HTTP 接口的超时时间
- `dbaas_workspace.dir`
  - DBAAS 工作目录根路径，用于保存快照、临时文件和后续查询输出等运行时数据
- `dbaas_workspace.sync_interval_seconds`
  - 后台任务触发拉取的间隔
- `dbaas_workspace.ttl_seconds`
  - 快照对 Agent 查询来说仍被认为新鲜的时间窗口，用于 tool 判断 `fresh` / `stale`
- `dbaas_workspace.user_active_idle_timeout_seconds`
  - 普通用户会话心跳超过该时间未更新时，停止该用户 services 快照周期同步；第一版默认 300 秒
- `dbaas_workspace.user_snapshot_refresh_wait_seconds`
  - 普通用户查询 services 时，等待当前用户快照刷新完成的最长时间
- `dbaas_workspace.jq_timeout_seconds`
  - 单次 `jq` 查询最多运行多久
- `dbaas_workspace.jq_max_preview_items`
  - 返回给大模型的最大预览条数
- `dbaas_workspace.jq_max_output_bytes`
  - 单次 `jq` 查询允许返回给 tool 处理的最大字节数，用于避免超大输出占用内存和上下文

mock-server 的 endpoint path 当前不会变化，
因此不需要放进配置文件。
它们可以作为代码里的集中常量维护：

```text
/services
/hosts
/clusters
/realtime-status
```

快照具体文件名也作为代码集中约定维护：

```text
services.json
services.meta.json
hosts.json
hosts.meta.json
clusters.json
clusters.meta.json
realtime_status.json
realtime_status.meta.json
```

默认值可以先采用 5 秒同步间隔和 30 秒 TTL，
但后续应以项目配置文件为准，并允许按现有配置体系决定是否支持环境变量覆盖。

`sync_interval_seconds` 和 `ttl_seconds` 的职责不同：

- `sync_interval_seconds`
  - 控制后台任务多久触发一次同步
- `ttl_seconds`
  - 控制 tool 如何判断已有快照是否仍然新鲜
  - 如果后台异常导致快照长时间未更新，tool 可以据此返回 `stale`

后台同步任务必须使用 `request_timeout_seconds`，
避免 HTTP 调用长期阻塞后台刷新循环。

## 5. Tool 语义

第一版 Agent 可见工具保留：

```text
query_dbaas_data_tool
describe_dbaas_schema_tool
```

`query_dbaas_data_tool` 的职责是读取已发布快照并执行受控 `jq`。
它不直接执行任意 DBAAS 请求，
不删除文件，
不写入快照，
也不直接发布快照。

对于管理员，tool 只读取后台已发布的 `admin/services.json`。
对于普通用户，tool 只读取当前用户对应的 `users/{safe_user}/services.json`。
如果普通用户快照缺失，tool 可以等待当前用户已有 prewarm / refresh，
最多等待 `user_snapshot_refresh_wait_seconds`；
如果仍没有 fresh 快照，返回 `snapshot_unavailable`。
如果普通用户快照过期或处于 error 状态，
tool 不主动刷新 DBAAS，直接返回 `snapshot_unavailable`。
任何刷新都必须使用后端 session / request identity，
不能由大模型传入身份，也不能回退读取 admin 快照。

查询前主要执行：

- 根据后端 identity 解析当前身份对应的 data/meta 路径
- 检查当前身份对应的 `services.json` 是否存在
- 检查当前身份对应的 `services.meta.json` 是否存在
- 读取 meta
- 判断当前时间是否超过 `expires_at`
- 检查 meta 中的 `data_path` 是否指向当前身份对应的固定文件
- 检查 meta 中的 `scope` / `user` / `schema_version` 与当前身份匹配
- 如果快照存在且未过期，执行 `jq`
- 如果快照缺失或过期，返回 error，不返回旧的过期 `data_path`

它不做：

- 不用大模型计算服务数量或统计值
- 不把完整 `services.json` 返回给大模型上下文
- 不接受任意 path
- 不接受 `user_id`、`role` 或 `user` 作为模型可填写参数
- 不让普通用户读取或回退到 admin 快照

如果快照不可用，返回 `error`，
`data_path` 为 `null`，
message 说明当前身份没有可用快照，后台同步或当前用户刷新可能尚未完成，
也可能拉取 DBAAS 数据失败。

## 6. 快照状态

当前建议至少支持以下状态：

- `fresh`
  - `services.json` 和 `services.meta.json` 都存在，且未过期
- `stale`
  - 快照文件存在，但已经超过 `expires_at`；这是内部判断状态，不应作为可查询路径返回给 Agent
- `missing`
  - 正式快照或 meta 文件不存在
- `error`
  - 同步失败，当前没有可用于准确查询的 fresh 快照；meta 可保留失败诊断信息，但 `data_path` 必须为 `null`

对于 `stale` 状态，tool 不应返回旧快照路径供 Agent 查询。
它应返回 `error` 并说明当前没有可用于准确查询的数据。
只有 `status == "fresh"`、`data_path` 非空、快照文件存在且未超过 `expires_at` 时，
tool 才能执行 `jq`。

## 7. 是否删除过期文件

当前策略调整为：

- 过期快照不可用于 Agent 查询
- 同步成功后用原子替换覆盖旧文件
- 如果仍有 fresh 快照，同步失败不应让当前快照变为不可查询
- 如果没有 fresh 快照，同步失败时删除旧 `services.json`，写入 `status: "error"`、`data_path: null` 的 `services.meta.json`
- lease 结束或用户不活跃时直接删除普通用户 `services.json` 和 `services.meta.json`，不写 error meta

删除过期文件由后台同步或当前用户受控刷新流程负责。
Agent 可见 tool 不删除文件、不发布文件。

## 8. 启动与临时文件清理

services 快照采用临时文件加 `os.replace` 的方式发布。
如果后端进程在写入临时文件过程中被强制停止、热重载或异常退出，
可能在 DBAAS workspace 中留下孤儿临时文件，例如：

```text
data/runtime/dbaas_workspace/admin/.services.json.3zrmm9ds.tmp
```

这些临时文件不是正式快照，
不应被 Agent 查询工具读取，
也不应影响正式 `services.json` / `services.meta.json`。

后端启动时，应在启动 admin services 后台同步前，
先扫描 DBAAS workspace 并清理上次进程遗留的 services 临时文件。

清理范围：

- `data/runtime/dbaas_workspace/**/.services.json.*.tmp`
- `data/runtime/dbaas_workspace/**/.services.meta.json.*.tmp`
- 后续 `hosts`、`clusters`、`realtime_status` 同类临时文件

清理规则：

- 只删除 `.tmp` 临时文件
- 不删除正式 `services.json` / `services.meta.json`
- 可设置最小年龄阈值，例如只删除 mtime 超过 60 秒的临时文件
- 清理失败只记录日志，不阻塞应用启动

同时，`write_json_temp()` 自身也应尽量做到：

- 如果 `json.dump()` 或文件写入过程中抛出异常，删除刚创建的临时文件
- 如果进程被直接 kill，依赖下次启动时的 workspace 临时文件清理兜底

## 9. 查询与统计策略

完整服务列表、主机列表、集群列表和实时状态都可能达到数 MB。

因此后续查询不应让大模型直接读取原始 JSON，
而应通过受控工具执行 `jq`：

```text
query_dbaas_data_tool(kind, jq_filter, max_preview_items)
```

`jq_filter` 可以由大模型根据用户问题和 schema 摘要生成。

每个 session 首次查询某一类 DBAAS 数据前，
必须先调用 `describe_dbaas_schema_tool(kind=...)` 获取该 kind 的结构定义，
再生成 `jq_filter`。

同一 session 中，如果已经针对相同 kind 调用过 schema 工具，
且 schema version 未变化，可以复用已知结构，不必重复查询。

这样做的原因是：

- DBAAS 字段名不一定符合大模型的常见猜测
- 先查 schema 可以避免模型生成错误 jq，导致统计结果看似成功但实际字段错用

例如：

```json
{
  "kind": "services",
  "jq_filter": ".[] | select(.healthStatus != \"HEALTHY\") | {name, type, user, healthStatus}",
  "max_preview_items": 50
}
```

但 tool 不接受任意文件路径，
也不接受 `user_id` / `role` 参数。

tool 内部必须：

1. 从 session / request identity 获取当前用户身份
2. 根据身份选择当前用户允许的 workspace
3. 校验 meta 为 `fresh` 且未超过 `expires_at`
4. 校验 `services.json` 存在且 meta 中的 `data_path` 指向当前固定文件
5. 校验 meta 中的 `scope`、`user` 和 `schema_version` 与当前身份匹配
6. 使用超时、输出大小和 preview 条数限制

admin 用户对 `admin/services.json` 执行模型给出的 `jq_filter`。
普通用户对 `users/{safe_user}/services.json` 执行模型给出的 `jq_filter`。
普通用户可见范围由 DBAAS 接口和当前用户快照保证，
tool 不再从 admin 快照做 jq wrapper 过滤。

`jq` 查询不获取锁。
如果后台同步或当前用户刷新刚好删除了过期快照，
或本次拉取失败，
查询工具可以直接返回 `error`，
说明当前没有可用数据。

查询工具返回：

- 计数
- 聚合结果
- 少量预览
- 是否截断
- 如果结果过大，只返回 preview 和 `truncated=true`

大模型只基于 `jq` 输出做解释，
不进行口算、手工统计或凭上下文猜测。

相关配置：

```toml
[dbaas_workspace]
jq_timeout_seconds = 3
jq_max_preview_items = 50
jq_max_output_bytes = 1048576
```

其中：

- `jq_timeout_seconds`
  - 单次 `jq` 查询最多运行多久
- `jq_max_preview_items`
  - 返回给大模型的最大预览条数
- `jq_max_output_bytes`
  - 单次 `jq` 查询允许返回给 tool 处理的最大字节数，用于避免超大输出占用内存和上下文

执行安全要求：

- 不开放任意 shell 命令
- 不接受任意 path
- `kind` 必须是枚举，例如 `services`、`hosts`、`clusters`、`realtime_status`
- 执行 `jq` 时使用参数数组，不通过 shell 拼接命令
- admin 用户只能查询 admin 全量快照
- 普通用户只能查询 `users/{safe_user}` 下自己的快照
- 普通用户快照不可用时禁止 fallback 到 admin 快照
- 如果输出过大，只返回 preview 和 `truncated=true`，提示用户缩小查询条件

第一版不写 `query_outputs/`。
如果后续需要导出完整查询结果或基于大结果继续二次查询，
再把 `query_outputs/` 作为可选增强。

第一版建议只实现 `services` 查询，
后续再扩展到 hosts、clusters 和 realtime status。

不建议第一版开放通用 `cat`、`ls`、`grep` 工具。

这些通用文件工具容易绕过 DBAAS workspace 的权限边界。
如果后续确实需要查看文件或搜索数据，
应提供受控 DBAAS 工具：

```text
list_dbaas_workspace_artifacts_tool
read_dbaas_workspace_artifact_tool
search_dbaas_data_tool
```

这些工具也必须：

- 只访问当前用户允许的 workspace
- 不接受任意文件系统 path
- 限制文件类型和输出大小
- 对 schema 说明使用 `describe_dbaas_schema_tool(kind)`，不直接读取任意项目文件

全文搜索需求优先通过 `query_dbaas_data_tool` 的 `jq_filter` 实现；
如果后续成为高频需求，再封装 `search_dbaas_data_tool(kind, query, fields)`。

对于“查看某个具体服务的所有内容”这类低频详情查询，
第一版不新增专用工具，
统一通过 `query_dbaas_data_tool` 生成 jq 查询完成。

示例：

```jq
.[] | select(.name == "mysql-xf2")
```

如果后续发现单服务详情查询成为高频需求，
再考虑封装 `get_dbaas_service_tool(name)`。

第一版工具组合建议：

```text
query_dbaas_data_tool
describe_dbaas_schema_tool
```

## 10. 多用户可见性

服务数据需要区分管理员和普通用户可见范围。

管理员后台同步维护 admin 全量快照：

```text
data/runtime/dbaas_workspace/
  admin/
    services.json
    services.meta.json
```

其中 `admin/services.json` 保存全量服务快照。

普通用户按用户身份维护独立快照：

```text
data/runtime/dbaas_workspace/
  users/
    payment-team-prod/
      services.json
      services.meta.json
```

其中 `users/{safe_user}/services.json` 只保存该普通用户可见的服务。
普通用户快照必须符合 `services.user.v1` schema。
该 schema 只包含普通用户可见字段，
不包含主机、主机 IP、节点、资源池或平台内部 ID 等普通用户不可见字段。

执行流程：

1. 从 session / request identity 获取当前 `user_id` 和 `role`
2. 如果当前用户是管理员，查询工具读取 `admin/services.meta.json` 并确认快照 fresh
3. 如果当前用户是普通用户，查询工具读取 `users/{safe_user}/services.meta.json` 并确认快照 fresh
4. 如果普通用户快照缺失，工具最多等待当前用户 prewarm / refresh 3 秒；刷新失败或超时则返回 error
5. 如果普通用户快照过期或处于 error 状态，工具直接返回 error，不主动刷新 DBAAS
6. 对当前身份对应的 `services.json` 执行 `jq_filter`
7. 返回 `jq` 结果预览和截断信息

如果当前身份对应的快照不可用或已经过期，
tool 应返回 `error`。
模型应直接说明当前无法获得准确数据，
不要基于旧数据猜测。

`user_id` 和 `role` 不应暴露为大模型可填写的 tool 参数。
`describe_dbaas_schema_tool` 也不接受 `role` 参数。

tool 必须以后端 session / request identity 为准，
不能信任模型传入身份。

如果当前 DeepAgent tool 无法直接读取 session context，
可以在 runtime 或 factory 构建 tool 时将当前用户身份通过闭包绑定到 tool 内部。

普通用户不能直接访问 admin 全量结果，
也不能在自己的快照缺失或过期时 fallback 到 admin 快照。
普通用户可见范围由 DBAAS 按身份返回的数据和 `users/{safe_user}` 快照隔离保证，
而不是让模型自行拼接权限条件。

ai-agent 调用 DBAAS services 接口时，应将产品侧 identity 转换为 DBAAS Bearer 身份：

```text
identity.role == "admin" -> Authorization: Bearer admin
identity.role == "user"  -> Authorization: Bearer user:{identity.user}
```

普通用户缺少 `identity.user` 时，tool 应直接返回 `permission_denied`，
不请求 DBAAS。

mock-server 当前已经支持 `Bearer user:{user}` 并按用户过滤 `/services`。
mock-server / 真实 DBAAS 在普通用户身份下返回的 `/services`
必须直接符合 `services.user.v1`，
不能返回 user schema 之外的敏感字段。

## 11. 内存缓存优化项

第一版建议仍以文件快照作为唯一事实源。

也就是说：

- `admin/services.json` 是全量服务事实源
- `users/{safe_user}/services.json` 是普通用户当前可见服务事实源
- `jq` 查询基于当前身份对应的快照文件执行
- 进程重启后可以完全依赖文件恢复

内存缓存可以作为后续性能优化，
但不作为第一版必要项。

可选优化方向：

- 后台刷新 `admin/services.json` 成功后，将解析后的 services 数据放入内存缓存
- 普通用户快照刷新成功后，将解析后的用户 services 数据放入内存缓存
- 如果内存缓存不存在或版本不匹配，则回退使用 `jq` 查询当前身份对应的 `services.json`
- 内存缓存 key 应包含 `kind`、`scope` 和 `source_synced_at`
- admin 或普通用户快照 `synced_at` 变化后，旧内存缓存必须失效

限制：

- 内存缓存不作为事实源
- 内存缓存不能绕过用户权限过滤
- `query_dbaas_data_tool` 等查询工具仍应读取当前身份对应的快照文件

## 12. 快照 Schema 与字段描述

`services.json` 的结构体定义不建议只写在 tool 描述里。

更推荐将结构定义放在独立 JSON Schema 文件中，
tool 描述只保留简短摘要和 schema 引用。

快照 schema 由本项目维护，
作为随代码提交、测试和版本管理的静态契约。

不建议每次 tool 调用时从 `dbaas-mock-server` 动态获取 schema。
`dbaas-mock-server` 只提供业务数据；
本项目负责维护 DBAAS 数据 schema。
`dbaas-mock-server` 返回的数据必须直接符合当前身份对应的 schema。
后台同步逻辑只做 schema 校验，不做随意字段映射或结构规整；
如果校验失败，则本次同步失败，不覆盖旧快照。

建议分层：

```text
admin/services.json
admin/services.meta.json
users/{safe_user}/services.json
users/{safe_user}/services.meta.json
config/schemas/services.admin.v1.schema.json
config/schemas/services.user.v1.schema.json
tool 描述
```

其中：

- `admin/services.json`
  - 管理员全量 services 快照
- `users/{safe_user}/services.json`
  - 普通用户可见 services 快照
- `services.meta.json`
  - 记录当前快照使用的 `schema_version` 和 `schema_path`
- `services.admin.v1.schema.json`
  - 管理员 services 结构定义和字段说明
- `services.user.v1.schema.json`
  - 普通用户 services 结构定义和字段说明；它是 admin schema 的安全字段投影，只包含普通用户可见字段
- tool 描述
  - 只写常用字段、查询约定和 schema 引用

管理员 `services.meta.json` 可以增加：

```json
{
  "kind": "services",
  "scope": "admin",
  "user": null,
  "schema_version": "services.admin.v1",
  "schema_path": "config/schemas/services.admin.v1.schema.json",
  "data_path": ".../admin/services.json"
}
```

普通用户 `services.meta.json` 可以增加：

```json
{
  "kind": "services",
  "scope": "user",
  "user": "payment-team-prod",
  "schema_version": "services.user.v1",
  "schema_path": "config/schemas/services.user.v1.schema.json",
  "data_path": ".../users/payment-team-prod/services.json"
}
```

字段描述建议使用 JSON Schema 的 `description` 字段。

稳定快照结构里的字段建议都保留一句描述，
但明显字段可以写得很短。

建议规则：

- 顶层字段必须写描述
  - 例如 array schema、服务组对象、嵌套对象的 description
- 业务含义不完全显然的字段必须写描述
  - 例如 `status`、`role`、`resource_status`、`health_score`
- 涉及单位的字段必须写清楚单位
  - 例如 `cpu`、`memory`、`storage.data.size`、`storage.log.size`
- ID 和名称类字段可以使用短描述
  - 例如 `name` 写成“服务组名称。”
- 普通用户 schema 不应包含普通用户不可见字段
  - 例如 `hostName`、`hostIp`、`hostId`、节点、资源池或平台内部 ID
  - 模型无法通过 schema 工具看到这些字段，也不应生成针对这些字段的 jq 查询

示例：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "services.user.v1",
  "title": "ServicesUserV1",
  "description": "DBAAS 普通用户服务列表快照。该 schema 是管理员 services schema 的安全字段投影，只包含普通用户可见字段。",
  "type": "array",
  "items": {
    "$ref": "#/$defs/ServiceDetailResponse"
  },
  "$defs": {
    "ServiceDetailResponse": {
      "type": "object",
      "additionalProperties": false,
      "description": "GET /services/{name} 的响应模型；GET /services 返回该对象数组。"
    }
  }
}
```

后续可以增加一个轻量工具：

```text
describe_dbaas_schema_tool(kind="services")
```

它只返回 schema 字段说明摘要，
用于回答“这个字段是什么意思”之类的问题，
不读取完整业务快照数据。
该工具不接受 `role` 参数；
它应根据后端 session / request identity 自动选择 `services.admin.v1` 或 `services.user.v1`。
模型不能通过 tool 参数切换 schema 视角。

## 13. 后续扩展

服务列表验证通过后，同样模式可以扩展到：

- `hosts.json`
- `hosts.meta.json`
- `clusters.json`
- `clusters.meta.json`
- `realtime_status.json`
- `realtime_status.meta.json`

整体模式保持一致：

```text
身份解析 -> 当前身份快照 -> meta 状态 -> jq 查询 -> 大模型解释
```

Phase5 第一版只实现 `services`。
后续 `hosts`、`clusters`、`realtime_status` 如需落盘查询，
应复用 services 的 workspace 路径计算、schema 选择、refresh 状态机、error meta 和启动临时文件清理能力，
不另行设计一套快照生命周期。

## 14. 代码组织建议

第五阶段建议新增独立 DBAAS 模块目录，
避免把同步、快照、tool 和后台任务逻辑塞进 `main.py` 或 `factory.py`。

建议目录：

```text
backend/src/dbass_ai_agent/dbaas/
  __init__.py
  config.py
  constants.py
  workspace.py
  schema.py
  sync.py
  query.py
  tools.py
  background.py

config/schemas/
  services.admin.v1.schema.json
  services.user.v1.schema.json
```

职责建议：

- `dbaas/config.py`
  - DBAAS 配置模型，例如 `base_url`、`workspace_dir`、`sync_interval_seconds`、`ttl_seconds`、普通用户 active lease 超时
- `dbaas/constants.py`
  - endpoint path 和固定文件名，例如 `/services`、`services.json`、`services.meta.json`
- `dbaas/workspace.py`
  - 工作目录路径计算、admin 目录、`users/{safe_user}` 目录、临时文件路径、data/meta 文件路径、启动时孤儿 `.tmp` 清理
- `dbaas/schema.py`
  - 根据身份加载 admin/user JSON Schema、校验 dbaas-server 响应、生成 schema 字段说明摘要
- `dbaas/sync.py`
  - 调用 `dbaas-server` HTTP 接口、刷新 admin 全量快照、刷新普通用户可见快照、临时文件写入、过期文件删除、原子替换发布
- `dbaas/query.py`
  - 读取当前身份对应快照、受控执行 `jq`、处理 timeout、输出限制、preview 和错误返回
- `dbaas/background.py`
  - 启动时触发 workspace 临时文件清理、admin 后台定时同步循环、普通用户 active lease 刷新循环、普通用户不活跃后的快照删除，供 FastAPI 生命周期挂载
- `dbaas/tools.py`
  - DeepAgent 可见工具包装，第一版包含 `query_dbaas_data_tool`、`describe_dbaas_schema_tool`

FastAPI 侧负责在应用生命周期中启动和停止 admin 后台同步任务，
并在会话打开、会话心跳或会话关闭时维护普通用户 active lease；
当普通用户 lease 失效时，停止该用户同步并删除对应 services 快照。

DeepAgent 侧只负责在现有 tool 注册链路里挂接查询和 schema 工具。

建议实现顺序：

1. 将 `config/schemas/services.v1.schema.json` 改名为 `services.admin.v1.schema.json`，新增 `services.user.v1.schema.json`
2. 扩展 workspace 路径能力，支持 `admin/` 和 `users/{safe_user}/`
3. 抽象 admin/user 共用的 services refresh 状态机和 error meta 写入逻辑
4. 增加普通用户 active lease 管理和 per-user refresh lock
5. 调整 query tool，根据后端 identity 选择当前身份快照和 schema，禁止普通用户 fallback 到 admin 快照
6. 调整 mock-server 或联调数据，保证普通用户 `/services` 返回 `services.user.v1` 结构
7. 增加启动时 services orphan `.tmp` 清理和 `write_json_temp()` 异常清理

## 15. 待继续讨论

后续还需要继续明确：

- 后续 hosts、clusters、realtime status 是否抽象通用 workspace/sync 基础能力
