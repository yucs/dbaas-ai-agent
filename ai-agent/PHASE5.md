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

- 后台定时任务负责周期性拉取 `dbaas-mock-server` 数据
- 固定路径保存最新快照，例如 `services.json`
- 同步生成对应元数据，例如 `services.meta.json`
- Agent 可见 tool 可以在快照缺失或过期时主动触发同步
- 后台任务和 Agent 可见 tool 共用同一套同步逻辑和同一把资源锁
- 数据过期、缺失、刷新中、刷新失败等状态通过 meta 结构表达
- 过期快照不再作为 Agent 查询依据，避免旧数据误导用户

也就是说，后台任务负责尽量保持快照新鲜；
大模型 tool 在用户查询时负责兜底确保快照可用。

## 3. 服务列表快照文件

服务列表可以先采用两个固定文件：

```text
services.json
services.meta.json
```

`services.json` 保存完整服务列表原始快照。
当前第一版不做结构映射，直接保存 `GET /services` 返回的数组，
结构必须符合 `services.v1` schema。

`services.meta.json` 作为 tool 返回给大模型的主要结构体，
用于说明当前快照是否可用、何时刷新、是否过期以及数据文件在哪里。

建议 meta 字段包括：

```json
{
  "kind": "services",
  "version": 1,
  "path": ".../services.json",
  "meta_path": ".../services.meta.json",
  "status": "fresh",
  "synced_at": "2026-04-28T10:00:00+08:00",
  "expires_at": "2026-04-28T10:00:30+08:00",
  "ttl_seconds": 30,
  "record_count": 0,
  "bytes": 0,
  "source": "dbaas-mock-server",
  "source_endpoint": "/services",
  "schema_version": "services.v1",
  "last_refresh_status": "success",
  "last_error": null
}
```

## 4. 后台同步策略

后台任务可以按固定间隔执行，例如每 5 秒一次。

后台每次执行时都应该尝试拉取最新数据，并在成功后更新：

```text
services.json
services.meta.json
```

推荐更新流程：

1. 调用 `dbaas-mock-server` 获取服务列表
2. 将响应写入临时文件，例如 `services.json.tmp`
3. 校验临时文件是合法 JSON
4. 统计记录数、文件大小和刷新时间
5. 写入临时 meta 文件，例如 `services.meta.json.tmp`
6. 原子替换 `services.json`
7. 原子替换 `services.meta.json`

如果刷新失败，不应返回旧的过期快照给 Agent 查询。

刷新失败应体现在 `services.meta.json` 的状态和错误字段中。
旧的过期快照应直接删除，
tool 对外返回时不能再提供旧的过期 `data_path`。

后台同步间隔、TTL、快照根目录和 `dbaas-mock-server` 地址必须写入 `config.toml`，
不能在代码中写死。

例如：

```toml
[dbaas_server]
base_url = "http://127.0.0.1:8001"
request_timeout_seconds = 5

[dbaas_workspace]
dir = ".runtime/dbaas_workspace"
sync_interval_seconds = 5
ttl_seconds = 30
resource_lock_timeout_seconds = 3
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
  - DBAAS 工作目录根路径，用于保存快照、锁、临时文件和后续查询输出等运行时数据
- `dbaas_workspace.sync_interval_seconds`
  - 后台任务触发拉取的间隔
- `dbaas_workspace.ttl_seconds`
  - 快照对 Agent 查询来说仍被认为新鲜的时间窗口，用于 tool 判断 `fresh` / `stale`
- `dbaas_workspace.resource_lock_timeout_seconds`
  - 等待快照资源锁的最长时间，用于避免 tool 或后台任务长时间卡在锁等待上
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

后台同步任务和 `sync_services_tool` 主动触发同步时，
都必须使用同一个 `request_timeout_seconds`，
避免 HTTP 调用长期阻塞锁和用户请求。

`request_timeout_seconds` 和 `resource_lock_timeout_seconds` 的职责不同：

- `request_timeout_seconds`
  - 控制调用 `dbaas-server` HTTP 接口最多等待多久
- `resource_lock_timeout_seconds`
  - 控制等待 `services.lock`、`hosts.lock` 等快照资源锁最多等待多久
  - 如果等待锁超时，tool 应返回 `refreshing`，提示稍后重试
  - 锁文件中记录持有锁进程的 PID；如果 PID 已不存在，视为 stale lock，允许自动删除后重新加锁
  - stale lock 主要用于处理开发阶段服务重启、进程被终止或 reload 中断时残留的锁文件，避免后台同步永久被误判为 `refreshing`

## 5. Tool 语义

`sync_services_tool` 可以保留这个方法名，
但语义需要明确为：

```text
确保服务列表快照可用；如果缺失或过期，则主动触发一次同步。
```

它主要执行：

- 检查 `services.json` 是否存在
- 检查 `services.meta.json` 是否存在
- 读取 meta
- 判断当前时间是否超过 `expires_at`
- 如果快照存在且未过期，直接返回快照状态、路径、刷新时间和可读提示
- 如果快照缺失或过期，则获取 `services.lock`
- 获取锁后再次检查快照是否已经被其他调用刷新
- 如果仍然缺失或过期，则调用统一同步逻辑刷新快照
- 同步成功后返回新的 fresh 快照
- 同步失败时返回 error，不返回旧的过期 `data_path`

它不做：

- 不用大模型计算服务数量或统计值
- 不把完整 `services.json` 返回给大模型上下文

`sync_services_tool` 可以访问 `dbaas-mock-server`，
但必须通过统一同步逻辑访问，
不能另写一套独立拉取和写文件流程。

锁逻辑建议：

1. 先检查快照是否 fresh
2. 如果 fresh，直接返回
3. 如果 missing 或 stale，尝试获取 `services.lock`
4. 获取锁后再次检查快照是否 fresh
5. 仍然 missing 或 stale 时执行同步
6. 使用临时文件写入并原子替换正式文件
7. 释放锁并返回结果

如果等待锁超时，返回：

```json
{
  "kind": "services",
  "status": "refreshing",
  "data_path": null,
  "refreshing": true,
  "message": "服务列表正在刷新，请稍后重试。"
}
```

## 6. 快照状态

当前建议至少支持以下状态：

- `fresh`
  - `services.json` 和 `services.meta.json` 都存在，且未过期
- `stale`
  - 快照文件存在，但已经超过 `expires_at`
- `missing`
  - 正式快照或 meta 文件不存在
- `refreshing`
  - 后台任务正在刷新，可以通过 lock 或 meta 状态表达
- `refreshing_retry_exhausted`
  - 同一轮 Agent 调用中，`sync_services_tool` 已经连续返回超过 3 次 `refreshing`
  - 此状态用于阻止模型无限循环重试；模型必须停止工具调用，直接告知用户数据仍在刷新中
- `error`
  - 同步失败，当前没有可用于准确查询的 fresh 快照

对于 `stale` 状态，tool 不应返回旧快照路径供 Agent 查询。
它应尝试主动同步；
如果同步失败，则返回 `error` 并说明当前没有可用于准确查询的数据。

## 7. 是否删除过期文件

当前策略调整为：

- 过期快照不可用于 Agent 查询
- `sync_services_tool` 在发现缺失或过期时可以主动触发同步
- 同步成功后用原子替换覆盖旧文件
- 同步失败时返回 `error`，不返回旧的过期 `data_path`
- 旧的过期文件直接删除

删除过期文件必须在拿到对应资源锁之后执行，
避免后台任务或其他 tool 调用正在读写同一个文件。

后台任务和 tool 必须共用同一把资源锁：

```text
services.lock
hosts.lock
clusters.lock
realtime_status.lock
```

后台任务和 tool 也必须共用同一个刷新方法，
确保拉取、schema 校验、临时文件写入、原子替换和 meta 更新只有一套实现。

## 8. 查询与统计策略

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
2. 调用 `sync_services_tool` 或通用 `ensure_visible_snapshot(kind, current_user)`
3. 根据当前用户身份得到允许访问的 scoped `data_path`
4. 只对该 `data_path` 执行 `jq`
5. 使用超时、输出大小和 preview 条数限制

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
- 普通用户只能查询自己的 scoped 快照
- admin 用户可以查询 admin 全量快照
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
sync_services_tool
query_dbaas_data_tool
describe_dbaas_schema_tool
```

## 9. 多用户可见性

服务数据需要区分管理员和普通用户可见范围。

当前建议采用工作目录隔离：

```text
.runtime/dbaas_workspace/
  admin/
    services.json
    services.meta.json
  users/
    {user_id}/
      services.json
      services.meta.json
```

其中：

- `admin/services.json`
  - 管理员视图，保存全量服务快照
- `users/{user_id}/services.json`
  - 普通用户视图，只保存该用户可见服务

`sync_services_tool` 的语义应调整为：

```text
确保当前用户可见的 services 快照可用。
```

执行流程：

1. 从 session / request identity 获取当前 `user_id` 和 `role`
2. 确保 `admin/services.json` 全量快照 fresh
3. 如果当前用户是管理员，返回 `admin/services.json`
4. 如果当前用户是普通用户，从全量快照过滤出用户可见服务
5. 写入 `users/{user_id}/services.json` 和 `users/{user_id}/services.meta.json`
6. 返回用户自己的 scoped 快照路径

如果同步路径返回 `refreshing`，同一轮 Agent 调用内最多允许 `sync_services_tool` 重试 3 次。
第 4 次应返回 `refreshing_retry_exhausted`，
让模型结束本轮回复并提示用户稍后重试。
原因是 DBAAS 数据刷新可能受后台锁、接口超时或服务重启影响；
无限重试会导致流式响应长时间不结束，前端发送按钮持续不可用。

`user_id` 和 `role` 不应暴露为大模型可填写的 tool 参数。

tool 必须以后端 session / request identity 为准，
不能信任模型传入身份。

如果当前 DeepAgent tool 无法直接读取 session context，
可以在 runtime 或 factory 构建 tool 时将当前用户身份通过闭包绑定到 tool 内部。

普通用户后续的 `jq` 查询只能基于自己的 scoped 快照路径执行，
不能直接访问 `admin/services.json`。

用户目录名必须使用安全的内部 user id，
不能直接拼接未经校验的任意用户名。

用户 scoped meta 建议记录：

```json
{
  "kind": "services",
  "scope": "user",
  "user_id": "alice",
  "role": "user",
  "source_scope": "admin",
  "source_synced_at": "2026-04-28T10:00:00+08:00",
  "filtered_at": "2026-04-28T10:00:01+08:00",
  "record_count": 12,
  "data_path": ".../users/alice/services.json"
}
```

## 10. 内存缓存优化项

第一版建议仍以文件快照作为唯一事实源。

也就是说：

- `admin/services.json` 是全量服务事实源
- `users/{user_id}/services.json` 是用户可见服务事实源
- `jq` 查询仍然基于文件执行
- 进程重启后可以完全依赖文件恢复

内存缓存可以作为后续性能优化，
但不作为第一版必要项。

可选优化方向：

- 后台刷新 `admin/services.json` 成功后，将解析后的 services 数据放入内存缓存
- 普通用户生成 scoped 视图时，优先使用内存中的 admin 数据过滤
- 如果内存缓存不存在或版本不匹配，则回退读取 `admin/services.json`
- 内存缓存 key 应包含 `kind`、`scope` 和 `source_synced_at`
- admin 快照 `synced_at` 变化后，旧内存缓存必须失效

限制：

- 内存缓存不作为事实源
- 内存缓存不能绕过用户权限过滤
- 内存缓存不能替代用户 scoped 文件
- `query_dbaas_data_tool` 等查询工具仍应读取当前用户可见的文件路径

## 11. 快照 Schema 与字段描述

`services.json` 的结构体定义不建议只写在 tool 描述里。

更推荐将结构定义放在独立 JSON Schema 文件中，
tool 描述只保留简短摘要和 schema 引用。

快照 schema 由本项目维护，
作为随代码提交、测试和版本管理的静态契约。

不建议每次 tool 调用时从 `dbaas-mock-server` 动态获取 schema。
`dbaas-mock-server` 只提供业务数据；
本项目负责维护 DBAAS 数据 schema。
`dbaas-mock-server` 返回的数据必须直接符合该 schema。
后台同步逻辑只做 schema 校验，不做字段映射或结构规整；
如果校验失败，则本次同步失败，不覆盖旧快照。

建议分层：

```text
services.json
services.meta.json
backend/schemas/services.v1.schema.json
tool 描述
```

其中：

- `services.json`
  - 实际快照数据
- `services.meta.json`
  - 记录当前快照使用的 `schema_version` 和 `schema_path`
- `services.v1.schema.json`
  - 稳定结构定义和字段说明
- tool 描述
  - 只写常用字段、查询约定和 schema 引用

`services.meta.json` 可以增加：

```json
{
  "kind": "services",
  "schema_version": "services.v1",
  "schema_path": "backend/schemas/services.v1.schema.json",
  "data_path": ".../services.json"
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

示例：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "services.v1",
  "title": "ServicesV1",
  "description": "DBAAS 服务列表快照。结构必须与 dbaas-server GET /services 响应一致，顶层为服务组数组。",
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

## 12. 后续扩展

服务列表验证通过后，同样模式可以扩展到：

- `hosts.json`
- `hosts.meta.json`
- `clusters.json`
- `clusters.meta.json`
- `realtime_status.json`
- `realtime_status.meta.json`

整体模式保持一致：

```text
后台同步 -> 固定快照 -> meta 状态 -> jq 查询 -> 大模型解释
```

## 13. 代码组织建议

第五阶段建议新增独立 DBAAS 模块目录，
避免把同步、快照、tool 和后台任务逻辑塞进 `main.py` 或 `factory.py`。

建议目录：

```text
backend/src/dbass_ai_agent/dbaas/
  __init__.py
  config.py
  constants.py
  workspace.py
  locks.py
  schema.py
  sync.py
  visibility.py
  query.py
  tools.py
  background.py

backend/schemas/
  services.v1.schema.json
```

职责建议：

- `dbaas/config.py`
  - DBAAS 配置模型，例如 `base_url`、`workspace_dir`、`sync_interval_seconds`、`ttl_seconds`、`resource_lock_timeout_seconds`
- `dbaas/constants.py`
  - endpoint path 和固定文件名，例如 `/services`、`services.json`、`services.meta.json`
- `dbaas/workspace.py`
  - 工作目录路径计算、admin/user 目录、临时文件路径、data/meta 文件路径
- `dbaas/locks.py`
  - `services.lock`、`hosts.lock` 等资源锁获取、释放和超时控制
- `dbaas/schema.py`
  - 加载 JSON Schema、校验 dbaas-server 响应、生成 schema 字段说明摘要
- `dbaas/sync.py`
  - 调用 `dbaas-server` HTTP 接口、刷新 admin 全量快照、临时文件写入、原子替换、失败 meta 更新
- `dbaas/visibility.py`
  - admin/user 可见性判断、普通用户 scoped services 生成、用户身份边界处理
- `dbaas/query.py`
  - 受控执行 `jq`，处理 timeout、输出限制、preview 和错误返回
- `dbaas/background.py`
  - 后台定时同步循环，供 FastAPI 生命周期挂载
- `dbaas/tools.py`
  - DeepAgent 可见工具包装，第一版包含 `sync_services_tool`、`query_dbaas_data_tool`、`describe_dbaas_schema_tool`

FastAPI 侧只负责在应用生命周期中启动和停止后台同步任务。

DeepAgent 侧只负责在现有 tool 注册链路里挂接 `sync_services_tool`。

## 14. 待继续讨论

后续还需要继续明确：

- 是否需要 lock 文件同时表达 `refreshing` 状态
- 过期文件删除后的错误 meta 如何记录
- meta 是否需要记录 schema 字段摘要或只记录 `schema_path`
- 后续 hosts、clusters、realtime status 是否抽象通用 workspace/sync 基础能力
