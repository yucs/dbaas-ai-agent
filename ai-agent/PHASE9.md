# DBAAS 智能助手第九阶段：备份查询

## 0. 当前状态

- 状态：设计草案
- 当前代码状态：尚未实现 Phase9
- 本文档作用：定义 DBAAS 备份查询第一版的字段结构、同步方式、查询工具和模型行为规则
- 核心边界：Phase9 第一版只做备份查询，不做发起备份、恢复、删除或备份策略修改
- 后续关注：发起备份任务、指定备份详情接口、备份恢复、备份删除、后台预热同步和更完整的备份策略查询可后续推进

## 1. Phase9 v1 目标

Phase9 第一版实现 DBAAS 备份文件查询能力。

本阶段范围包括：

- 按当前身份查询 DBAAS 当前可见的备份文件列表
- 将备份列表原样落盘为本地快照
- 使用统一 backups JSON Schema 描述查询字段
- 使用 jq 查询本地备份快照
- 支持模型在用户明确要求最新数据时强制刷新备份快照
- 明确备份字段、枚举值和时间格式，避免模型猜测字段含义

本阶段不包括：

- 发起备份
- 查询单个 `backup_id` 详情接口
- 恢复备份
- 删除备份
- 修改备份策略
- 解析或查询 tables 细节
- 备份后台定时同步

## 2. 核心结论

备份查询第一版只面向 DBAAS 当前仍存在、当前身份可见的备份文件。

`backups.json` 是当前 DBAAS 返回的可见备份集合，不是审计历史。

也就是说：

- DBAAS 返回什么，ai-agent 就发布什么
- 每次刷新都全量覆盖本地 `backups.json`
- 不 append
- 不做增量 merge
- 已过期但尚未删除的备份也保留在快照中
- 不保留已删除或当前用户无权访问的备份
- 查询为空只能说明当前可见备份快照中没有，不能证明历史上从未存在

DBAAS 需要直接返回适合大模型使用的备份记录。
ai-agent 不做备份业务字段转换，只做轻量 JSON 检查、快照落盘和 jq 查询。

## 3. Backup Record Schema

DBAAS `/backups` 应直接返回 agent-facing backup record 数组。

示例：

```json
[
  {
    "backup_id": "7d034e16d18c8b89f9b173b00f210054",
    "task_id": "task-001",
    "service_name": "upsql_7197",
    "service_type": "upsql",
    "child_service_name": "mysql-shard-01",
    "child_service_type": "mysql",
    "unit_name": "mysql-primary-01",
    "backup_type": "ddl",
    "size_bytes": 2269646,
    "storage_type": "NAS",
    "compress_mode": "none",
    "started_at": "2026-06-01 07:30:06",
    "finished_at": "2026-06-01 07:30:10",
    "expires_at": "2026-06-04 07:30:10",
    "duration_seconds": 4,
    "task_status": "succeeded",
    "task_error": null,
    "valid_status": "valid",
    "remark": "自动备份"
  }
]
```

字段说明：

- `backup_id`
  - 单个备份文件唯一 ID
- `task_id`
  - 产生该备份文件的 DBAAS 任务 ID
  - 同一个任务可能产生多个备份文件
- `service_name`
  - 服务组名称
- `service_type`
  - 服务组类型
- `child_service_name`
  - 子服务名称
  - 多分片或多个同类型子服务时，用于定位具体子服务
- `child_service_type`
  - 子服务类型，例如 `mysql`、`tidb`、`tikv`
- `unit_name`
  - 备份所属或执行的单元名称
  - 第一版不暴露 `unit_id`
- `backup_type`
  - 备份类型枚举
- `size_bytes`
  - 备份文件大小，单位 byte
- `storage_type`
  - 存储类型枚举
- `compress_mode`
  - 压缩模式枚举
- `started_at`
  - 备份开始时间
- `finished_at`
  - 备份结束时间；未完成时可为 `null`
- `expires_at`
  - 备份过期时间；无过期时间时可为 `null`
- `duration_seconds`
  - 备份耗时，单位秒
  - 由 DBAAS 提供；未完成时可为 `null`
- `task_status`
  - 任务状态枚举
- `task_error`
  - 任务错误信息；没有错误时为 `null`
- `valid_status`
  - 备份有效性校验状态
- `remark`
  - 备注

## 4. 枚举字段

枚举值由 DBAAS 保证稳定输出。
ai-agent 不负责把数字状态、中文状态或展示名映射成枚举。

### 4.1 backup_type

当前已知值：

```text
full
incremental
ddl
snapshot
rdb
table
encrypt
```

其中表级备份类型使用 `table`，不是 `tables`。
`backup_type` 可能和服务类别有关，DBAAS 应按真实备份能力返回稳定枚举值。

如果 DBAAS 后续新增备份类型，应同步更新本文档和工具描述。

### 4.2 storage_type

当前已知值：

```text
NAS
S3
```

模型应使用 `storage_type` 做存储类型过滤，不应从路径字符串推断存储类型。

### 4.3 compress_mode

当前已知值：

```text
none
gzip
zstd
```

`compress_mode` 只保留机器可过滤值。
不返回 `compress_mode_display_name`。

### 4.4 task_status

当前固定值：

```text
created
running
stopped
canceled
succeeded
timed_out
failed
unknown
```

DBAAS 不应把任务状态数字直接返回给 ai-agent。
如果 DBAAS 内部状态为数字，应在 DBAAS 侧转成上述字符串。

DBAAS / mock-server 内部如果仍使用数字状态，应按以下规则转换后返回：

```text
1 -> created
2 -> running
3 -> stopped
4 -> canceled
5 -> succeeded
6 -> timed_out
7 -> failed
8 -> unknown
其他 -> unknown
```

### 4.5 valid_status

`valid_status` 表示备份文件有效性校验状态。

当前固定值：

```text
unchecked
checking
valid
invalid
unknown
null
```

语义：

- `unchecked`
  - 未验证
- `checking`
  - 验证中
- `valid`
  - 验证成功
- `invalid`
  - 验证失败
- `unknown`
  - DBAAS 无法确认验证状态
- `null`
  - 当前备份类型或当前接口不提供验证状态

`task_status` 表示备份任务是否完成。
`valid_status` 表示备份文件是否经过有效性验证。
`task_status=succeeded` 不等于 `valid_status=valid`。

DBAAS 不应返回中文展示文案作为 `valid_status`。
如需展示中文，由前端或回答层解释枚举。

## 5. 时间字段

时间字段统一使用 DBAAS 本地时间字符串：

```text
YYYY-MM-DD HH:mm:ss
```

例如：

```json
{
  "started_at": "2026-06-01 07:30:06",
  "finished_at": "2026-06-01 07:30:10",
  "expires_at": "2026-06-04 07:30:10"
}
```

约定：

- 时间字符串必须固定补零
- 同一份备份快照内必须使用同一时区
- 当前默认按 DBAAS 本地时区理解
- jq 可以直接用字符串比较做时间范围筛选
- 涉及“今天”“昨天”“前 3 天”“最近 7 天”等相对时间时，可复用 Phase6 已有 `get_current_time_tool`
- 优先使用 time tool 返回的 `local_datetime` 或 `local_date` 生成绝对时间边界，不依赖模型自行把 Unix timestamp 转成时间字符串
- `expires_at` 可直接用于判断备份是否已过期；过期不等于已删除
- 如果时间未知或任务未完成，对应字段返回 `null`

时间范围查询示例：

```jq
[
  .[]
  | select(.started_at >= "2026-06-01 00:00:00"
       and .started_at <  "2026-06-02 00:00:00")
]
```

`duration_seconds` 由 DBAAS 提供。
模型不应自己用 `started_at` 和 `finished_at` 做耗时计算。

## 6. Snapshot 存放

备份快照沿用 DBAAS workspace 思路，按身份隔离。

文件路径：

```text
admin/backups.json
admin/backups.meta.json

users/{safe_user}/backups.json
users/{safe_user}/backups.meta.json
```

刷新方式：

```text
GET /backups
```

请求必须携带现有 DBAAS 身份头：

```text
Authorization
X-DBAAS-Actor-User
X-DBAAS-Actor-Role
```

DBAAS 负责按身份过滤返回结果：

- admin 返回当前存在的全量备份
- 普通用户返回当前用户可见且仍存在的备份
- 已过期但尚未删除的备份也应返回；只有删除后或当前不可见时才不返回

`backups.meta.json` 应固定包含以下字段，查询工具必须校验 `scope`、`user`、`schema_version`、`schema_path`、`data_path` 与当前身份和当前 workspace 一致。

```json
{
  "kind": "backups",
  "version": 1,
  "scope": "user",
  "user": "payment-team-prod",
  "data_path": ".../users/payment-team-prod/backups.json",
  "meta_path": ".../backups.meta.json",
  "status": "fresh",
  "synced_at": "2026-06-01T10:00:00+08:00",
  "expires_at": "2026-06-01T10:00:30+08:00",
  "ttl_seconds": 30,
  "record_count": 0,
  "bytes": 0,
  "source": "dbaas-server",
  "source_endpoint": "/backups",
  "schema_version": "backups.v1",
  "schema_path": "config/schemas/backups.v1.schema.json",
  "last_refresh_status": "success",
  "last_error": null
}
```

## 7. Schema

备份快照使用统一 schema，不按 admin/user 区分。

Schema 文件：

```text
config/schemas/backups.v1.schema.json
```

Schema version：

```text
backups.v1
```

`backups.meta.json` 应记录：

```json
{
  "schema_version": "backups.v1",
  "schema_path": "config/schemas/backups.v1.schema.json"
}
```

第一版 schema 目标：

- 面向大模型描述 backups 查询字段、类型、枚举和 nullable 规则
- 本文定义的字段固定存在；未知或不适用时使用 `null`，不要省略字段
- 描述 `backup_type`、`storage_type`、`compress_mode`、`task_status`、`valid_status` 等枚举
- `task_status` 必须是非空字符串枚举，不允许为 `null`
- `started_at`、`finished_at`、`expires_at` 类型为字符串或 `null`
- `size_bytes` 和 `duration_seconds` 为数字或允许的 `null`
- 第一版允许额外字段，不使用 `additionalProperties=false` 卡死 DBAAS 对接
- 第一版不做跨字段强约束，不校验时间正则、不校验 `duration_seconds` 和时间差一致
- 第一版不把严格 schema 校验作为落盘前置条件，避免 DBAAS 临时新增字段或枚举导致备份列表不可查询

固定字段：

```text
backup_id
task_id
service_name
service_type
child_service_name
child_service_type
unit_name
backup_type
size_bytes
storage_type
compress_mode
started_at
finished_at
expires_at
duration_seconds
task_status
task_error
valid_status
remark
```

其中 `task_status` 不可为 `null`。
`finished_at`、`expires_at`、`duration_seconds`、`task_error`、`valid_status`、`remark` 等字段可按业务状态返回 `null`。

ai-agent 仍不做业务转换。
schema 主要用于让模型知道字段含义、枚举值和 nullable 规则，帮助稳定生成 jq。

刷新落盘前只做轻量结构检查：

- DBAAS 响应必须是合法 JSON
- 顶层必须是数组
- 每个数组元素必须是对象

如果满足上述条件，即可原子写入 `backups.json`。
枚举、时间格式、nullable 细节或额外字段不应导致落盘失败。

模型不会直接读取 schema 文件。
ai-agent 应通过现有 schema 描述工具向模型暴露 backups schema 摘要：

```text
describe_dbaas_schema_tool(kind="backups")
```

模型在生成 backups jq 前，应先调用该工具确认字段名、枚举值、nullable 字段和时间格式。
同一 session 中如果已获取相同 schema version，可以复用已有 schema 上下文。

## 8. Refresh 策略

Phase9 v1 不做后台定时同步。
管理员和普通用户都使用查询时懒刷新。

查询工具支持 `refresh` 参数：

```text
query_dbaas_backup_data_tool(jq_filter, max_preview_items=None, refresh=False)
```

行为：

```text
refresh=true:
  无论本地 backups snapshot 是否 fresh，都先调用 DBAAS 拉取 /backups
  拉取成功后全量覆盖 backups.json 和 backups.meta.json
  然后执行 jq

refresh=false:
  如果本地 backups snapshot fresh，直接执行 jq
  如果本地 backups snapshot missing/stale/error，懒刷新当前身份 backups
  刷新成功后执行 jq
  刷新失败则返回错误
```

刷新失败规则：

- `refresh=true` 拉取失败时，必须返回错误，不使用旧快照冒充最新数据
- `refresh=false` 时，如果已有 fresh 快照则不刷新并直接查询
- `refresh=false` 时，如果 snapshot missing/stale/error 后刷新失败，返回错误，不查询旧的 stale 文件
- 刷新失败且没有可用 fresh 快照时，`backups.meta.json` 的 `status` 应为 `error`，`data_path` 应为 `null`

并发刷新规则：

- admin 和普通用户都可能由查询触发懒刷新
- 同一身份同一时间只允许一个 `/backups` refresh 实际请求 DBAAS
- admin 使用 `admin/backups` 刷新锁
- 普通用户使用 `users/{safe_user}/backups` 刷新锁
- 其他并发查询可以等待同一个刷新完成，最长等待现有 `user_snapshot_refresh_wait_seconds`

配置复用：

- 第一版复用现有 DBAAS workspace 配置，不新增 backup 专属 TTL 或 jq 配置
- 快照新鲜度使用 `ttl_seconds`
- DBAAS HTTP 请求使用 `request_timeout_seconds`
- jq 查询使用 `jq_timeout_seconds`、`jq_max_preview_items` 和 `jq_max_output_bytes`

模型调用规则：

- 用户明确要求“最新”“刷新”“现在”“当前”“实时”备份列表时，传 `refresh=true`
- 普通查询默认 `refresh=false`
- 用户询问当前 session 中刚发起的任务状态时，后续版本应优先使用 task 查询工具，而不是用备份列表猜测任务状态

## 9. 启动与临时文件清理

backups 快照采用临时文件加 `os.replace` 的方式发布。
如果后端进程在写入临时文件过程中异常退出，可能在 DBAAS workspace 中留下孤儿临时文件。

后端启动时，应清理 backups 相关临时文件：

```text
data/runtime/dbaas_workspace/**/.backups.json.*.tmp
data/runtime/dbaas_workspace/**/.backups.meta.json.*.tmp
```

清理规则：

- 只删除 `.tmp` 临时文件
- 不删除正式 `backups.json` 和 `backups.meta.json`
- 可设置最小年龄阈值，例如只删除 mtime 超过 60 秒的临时文件
- 清理失败只记录日志，不阻塞应用启动

## 10. Tool 设计

### 10.1 query_dbaas_backup_data_tool

Tool 签名：

```text
query_dbaas_backup_data_tool(
    jq_filter: str,
    max_preview_items: int | None = None,
    refresh: bool = False
)
```

职责：

- 获取当前身份
- 确保当前身份有可用 backups snapshot
- 必要时调用 DBAAS `/backups` 刷新
- 对 `backups.json` 执行受控 jq
- 返回 preview、truncated、错误类型和基础 meta

使用前提：

- 生成 backups jq 前，应先调用 `describe_dbaas_schema_tool(kind="backups")`
- schema 已在当前上下文中可用且 version 未变化时，可以复用

不负责：

- 字段改名
- 状态映射
- 基于 `expires_at` 主动过滤备份
- 单条 `backup_id` 详情查询
- 发起备份

### 10.2 第一版不提供的工具

第一版不提供：

```text
get_dbaas_backup_tool(backup_id)
create_service_backup_task_tool(...)
```

如果后续新增发起备份，应复用 Phase7 approval、operation 和 task 机制。

## 11. 查询示例

查询某服务的所有成功备份：

```jq
[
  .[]
  | select(.service_name == "upsql_7197"
       and .task_status == "succeeded")
]
```

查询某服务最近一次成功备份：

```jq
[
  .[]
  | select(.service_name == "upsql_7197"
       and .task_status == "succeeded"
       and .finished_at != null)
]
| sort_by(.finished_at)
| reverse
| .[0]
```

查询某个子服务的备份：

```jq
[
  .[]
  | select(.service_name == "upsql_7197"
       and .child_service_name == "mysql-shard-01")
]
```

查询某天内开始的备份：

```jq
[
  .[]
  | select(.started_at >= "2026-06-01 00:00:00"
       and .started_at <  "2026-06-02 00:00:00")
]
```

查询耗时最长的备份：

```jq
[
  .[]
  | select(.duration_seconds != null)
]
| sort_by(.duration_seconds)
| reverse
| .[0]
```

## 12. 模型行为规则

模型查询备份时应遵守：

- 生成 jq 前先调用 `describe_dbaas_schema_tool(kind="backups")`，确认字段名、枚举值和 nullable 规则
- 使用结构化字段过滤，不要从路径字符串推断服务、时间、类型或存储类型
- 服务级问题应先按 `service_name` 过滤
- 子服务或分片问题应结合 `child_service_name` 和 `child_service_type`
- 单元问题应使用 `unit_name`
- 时间范围查询使用 `started_at`、`finished_at` 或 `expires_at` 字符串比较
- 判断备份是否已过期时，直接比较 `expires_at`；不要把“已过期”误当成“已删除”
- 涉及相对时间时，先调用 `get_current_time_tool`，优先使用 `local_datetime` 或 `local_date` 生成绝对时间边界，再生成 jq
- 耗时问题使用 `duration_seconds`
- 查询不到记录时，应说明“当前可见备份快照中没有”，不要断言历史上从未存在
- 需要最新备份列表时，调用 tool 时传 `refresh=true`

## 13. DBAAS / Mock Server 要求

mock-server 和真实 DBAAS 应负责返回最终模型友好字段。

第一版至少需要：

```text
GET /backups
```

接口要求：

- 返回顶层 JSON 数组
- 按 DBAAS 身份头做权限过滤
- 只返回当前仍存在的备份
- 已过期但尚未删除的备份也返回
- 不返回已删除的备份
- 直接返回本文定义的字段名
- 直接返回字符串枚举状态
- 直接返回 `duration_seconds`
- mock-server 必须模拟真实 DBAAS 的最终对外结构，不能把内部数字状态或中文校验状态透传给 ai-agent
- `valid_status` 使用英文机器枚举，不返回中文展示文案
- 不返回 `cleared`
- 不要求返回 `tables`
- 不要求返回 `version`
- 不要求返回 `backup_type_display_name`
- 不要求返回 `compress_mode_display_name`
- 不要求返回 `backup_path`

ai-agent 第一版使用统一 `backups.v1` schema 向模型描述字段。
落盘前只做轻量 JSON 结构检查，不做严格 schema 校验。

## 14. 后续阶段

可后续推进：

- `get_dbaas_backup_tool(backup_id)` 直接查询单个备份详情，不落盘
- `create_service_backup_task_tool(...)` 发起备份，并接入 Phase7 approval/task
- 备份恢复
- 备份删除
- 备份策略查询或修改
- tables 级备份查询
- 后台预热或定时同步 backups snapshot
- 更严格的 backups schema 校验
