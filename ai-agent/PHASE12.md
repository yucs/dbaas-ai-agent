# DBAAS 智能助手第十二阶段：集群查询 Admin Tool

## 0. 当前状态

- 状态：设计落定，待实现
- 本文档作用：定义集群查询第一版的数据结构、数据视图刷新策略、tool 行为和权限规则
- 核心边界：Phase12 首版只做 admin-only 集群查询，不做集群写操作，不做普通用户集群数据视图，不接集群详情接口
- 参考关系：
  - 查询形态参考 services / hosts：使用 schema 描述结构，使用 jq 查询本地数据视图，原始大数据不直接进入模型上下文
  - 刷新形态参考 backups：首次查询、数据过期或 `refresh=true` 时在 tool 内 lazy refresh，不做后台周期同步

## 1. Phase12 v1 目标

Phase12 第一版实现 DBAAS 集群查询能力。

本阶段范围包括：

- 管理员按需查询 DBAAS 集群列表
- 从 DBAAS 集群接口拉取与 ai-agent schema 一致的 cluster record
- 将集群列表落盘为本地数据视图
- 使用统一 clusters schema 描述查询字段
- 使用 jq 查询本地集群数据视图
- 支持 `refresh=true` 强制刷新
- 明确支持的 CPU 架构、软件类型和网络字段，避免模型猜测字段含义
- 只将 cluster tool 注册给 admin agent

本阶段不包括：

- 普通用户集群查询
- 普通用户 clusters 数据视图
- 集群详情接口
- 集群创建、启用、停用、删除或配置变更
- 集群容量调度、主机调度或资源重分布
- 集群 precheck
- 集群监控、统计或告警查询
- 网络详情查询，网络数据在 Phase13 单独实现

## 2. 核心结论

Cluster v1 采用 admin-only lazy snapshot。

也就是说：

- 只有管理员身份可以使用集群查询 tool
- 普通 user agent 不注册 cluster tool
- Cluster 数据只维护 admin scope 数据视图
- 不启动后台周期刷新任务
- tool 调用时检查 `admin/clusters.meta.json`
- 如果数据视图 fresh，直接执行 jq
- 如果数据视图缺失或 stale，tool 内触发一次 DBAAS 集群接口刷新
- `refresh=true` 时无论当前数据视图是否 fresh，都强制刷新
- 刷新失败时不使用 stale 数据冒充最新事实

Cluster 和 hosts 的相似点：

- 都是管理员平台级资产数据
- 都只提供 admin scope 查询能力
- 都使用本地数据视图承载大列表查询
- 都通过 schema 约束模型可用字段

Cluster 和 hosts 的差异：

- hosts 使用后台周期刷新，适合较常用的容量和健康查询
- clusters 首版查询频率预计低于 hosts，采用 backups 风格 lazy refresh
- clusters 使用与 DBAAS/mock-server 接口一致的 agent-facing 字段结构

Cluster 和 backups 的相似点：

- 都是按需查询型数据视图
- 都支持 `refresh=true`
- 都可以在 tool 内按需拉取 DBAAS 最新数据
- refresh 失败时都不能使用 stale 数据冒充最新事实

## 3. Cluster Record Schema

DBAAS 集群接口应直接返回与 ai-agent schema 一致的 cluster record 数组。Phase12 不在 ai-agent 内设计额外字段映射层。

当前接口假设：

```http
GET /clusters
```

响应形态假设为顶层数组。

示例：

```json
[
  {
    "id": "3101",
    "name": "NJ-MYSQL-CLUSTER-01",
    "siteId": "12",
    "siteName": "南京一区",
    "areaId": "8",
    "areaName": "核心区",
    "supportedCpuArchitectures": ["amd64"],
    "supportedCpuArchitectureNames": ["X86"],
    "supportedSoftwareTypes": ["mysql", "redis", "mongodb"],
    "supportedNetworkNames": ["LEAF-10.24.16", "LEAF-10.24.17"],
    "haNetworkTag": "NJ-MYSQL-CLUSTER-01",
    "enabled": true,
    "description": "核心数据库集群",
    "createdAt": "2026-05-18 10:23:00",
    "createdBy": "ops_admin",
    "createdByName": "运维管理员",
    "updatedAt": "2026-05-20 15:42:11",
    "updatedBy": "ops_admin",
    "updatedByName": "运维管理员"
  }
]
```

示例数据要求：

- 示例只模拟真实格式，不使用生产真实数据
- `supportedSoftwareTypes` 相关示例使用 DBAAS 服务类型，例如 `mysql`、`redis`、`mongodb`
- 网络名称示例使用接近生产命名风格的虚构值，例如 `LEAF-10.24.16`
- 时间字段沿用 hosts 风格，保留普通时间字符串，不在 Phase12 强制转换为 ISO 8601

字段约定：

- `id`
  - 集群唯一 ID
  - 字符串
- `name`
  - 集群名称
- `siteId`
  - 集群所属站点 ID
  - 字符串
- `siteName`
  - 集群所属站点名称
- `areaId`
  - 集群所属区域 ID
  - 字符串
- `areaName`
  - 集群所属区域名称
- `supportedCpuArchitectures`
  - 集群支持的 CPU 架构机器值，例如 `amd64`、`arm64`
- `supportedCpuArchitectureNames`
  - 集群支持的 CPU 架构显示名称，例如 `X86`、`ARM`
- `supportedSoftwareTypes`
  - 集群支持部署的软件或服务类型，例如 `mysql`、`redis`、`mongodb`
- `supportedNetworkNames`
  - 集群支持使用的网络名称
- `haNetworkTag`
  - HA 网络标签
  - 该字段为 DBAAS 业务概念，Phase12 保留原名
- `enabled`
  - 集群是否启用
- `description`
  - 集群描述，没有描述时为空字符串
- `createdAt`
  - 集群记录创建时间
- `createdBy`
  - 集群记录创建人账号
- `createdByName`
  - 集群记录创建人姓名
- `updatedAt`
  - 集群记录最近更新时间，没有更新时为 `null`
- `updatedBy`
  - 集群记录最近更新人账号，没有更新时为 `null`
- `updatedByName`
  - 集群记录最近更新人姓名，没有更新时为 `null`

## 4. Schema 约束

Schema 要求：

- 顶层为数组
- 数组元素为 cluster record 对象
- `id`、`siteId`、`areaId` 使用字符串
- `supportedCpuArchitectures`、`supportedCpuArchitectureNames`、`supportedSoftwareTypes`、`supportedNetworkNames` 使用数组
- `enabled` 使用 boolean
- `description` 没有描述时为空字符串
- `updatedAt`、`updatedBy`、`updatedByName` 没有更新时为 `null`
- 接口返回结构应与 `config/schemas/clusters.v1.schema.json` 保持一致

## 5. 枚举与值约定

### 5.1 enabled

`enabled` 使用 boolean：

```text
true
false
```

Phase12 不额外引入 `status` 字段。

### 5.2 supportedCpuArchitectures

常见值包括：

```text
amd64
arm64
```

具体取值以 DBAAS 返回为准，schema 不在首版强制枚举。

### 5.3 supportedCpuArchitectureNames

常见值包括：

```text
X86
ARM
```

具体取值以 DBAAS 返回为准，schema 不在首版强制枚举。

### 5.4 supportedSoftwareTypes

常见值包括：

```text
mysql
redis
mongodb
postgresql
upkafka
zookeeper
```

具体取值以 DBAAS 服务类型为准，schema 不在首版强制枚举。

### 5.5 supportedNetworkNames

`supportedNetworkNames` 使用 DBAAS 网络名称。

示例：

```text
LEAF-10.24.16
LEAF-10.24.17
BOND-172.18.20
```

Phase12 只展示集群支持哪些网络名称，不查询网络 IP 范围、VLAN 或容量。网络详情在 Phase13 实现。

## 6. Snapshot 文件与元数据

新增 kind：

```text
clusters
```

新增 schema version：

```text
clusters.v1
```

新增文件：

```text
admin/clusters.json
admin/clusters.meta.json
config/schemas/clusters.v1.schema.json
```

`clusters.meta.json` 结构沿用 backups / hosts 的 snapshot meta 风格：

- `kind`
- `scope`
- `user`
- `version`
- `data_path`
- `meta_path`
- `status`
- `synced_at`
- `expires_at`
- `ttl_seconds`
- `record_count`
- `bytes`
- `source`
- `source_endpoint`
- `schema_version`
- `schema_path`
- `last_refresh_status`
- `last_error`

建议配置项：

```toml
[dbaas_workspace]
cluster_snapshot_ttl_seconds = 120
cluster_refresh_lock_timeout_seconds = 10
```

Phase12 不需要 `cluster_sync_interval_seconds`，因为不做后台周期同步。

## 7. Query Tool 设计

新增 admin-only tool：

```text
query_dbaas_cluster_data_tool
```

参数：

- `jq_filter: str`
  - jq 查询表达式
- `max_preview_items: int | None = None`
  - 返回给模型的最大预览条数
- `refresh: bool = False`
  - 是否强制刷新集群 snapshot

工具行为：

- 普通用户不可注册该 tool
- 如果非管理员路径误调用，返回 `permission_denied`
- 调用时先检查 clusters snapshot
- snapshot fresh 时直接执行 jq
- snapshot 缺失或 stale 时 lazy refresh
- `refresh=true` 时强制请求 DBAAS 集群接口
- DBAAS 返回非数组时返回 snapshot error
- DBAAS 返回数组但元素不是对象时返回 snapshot error
- jq 输出过大时沿用现有 preview / truncation 行为

成功消息建议：

```text
查询完成，结果来自当前管理员可见的 DBAAS 集群数据视图。
```

失败消息建议：

```text
当前没有可用的 DBAAS 集群数据视图，暂时无法获得准确数据：{reason}
```

## 8. Schema Tool 行为

`describe_dbaas_schema_tool(kind="clusters")` 仅管理员可用。

管理员：

- 返回 `clusters.v1` schema
- scope 为 `admin`

普通用户：

- 不允许 describe `clusters`
- 返回或抛出 schema scope 错误，行为与 hosts 保持一致

通用 schema 支持列表应新增：

```text
clusters
```

但 role prompt 边界必须保持：

- 通用 system prompt 可以描述“当前支持服务、备份、监控，以及管理员可见的平台资产查询”
- admin extend prompt 写明 cluster tool 名称、clusters schema kind 和查询规则
- user extend prompt 只说明普通用户无权查询平台级集群数据
- 不在通用 system prompt 暴露 admin-only tool 名称、schema kind 调用形式或示例参数

## 9. Prompt 规则

### 9.1 admin extend prompt

新增管理员集群查询规则：

- 管理员可以查询集群数据
- 查询集群列表、集群启用状态、集群支持的 CPU 架构、支持的软件类型或支持的网络时，调用 `query_dbaas_cluster_data_tool`
- 构造 clusters 的 jq_filter 前，必须按 clusters schema 使用字段名
- 首次查询集群数据或字段不确定时，先调用 `describe_dbaas_schema_tool(kind="clusters")`
- clusters 查询默认 `refresh=false`
- 用户明确要求最新、刷新、当前或实时集群列表时，调用 `query_dbaas_cluster_data_tool` 传 `refresh=true`
- 集群监控、统计或告警查询不在 Phase12 支持范围内

### 9.2 user extend prompt

普通用户规则保持平台级权限边界：

- 普通用户不能查询平台级集群数据
- 当普通用户请求集群列表、集群支持的软件类型、集群网络或跨用户平台资源时，说明需要管理员权限
- 普通用户没有 clusters schema 和 cluster query tool

### 9.3 system prompt

通用 prompt 只更新能力边界，不写 admin-only 工具名。

需要避免：

- 在通用 prompt 中写 `query_dbaas_cluster_data_tool`
- 在通用 prompt 中写 `describe_dbaas_schema_tool(kind="clusters")`
- 在通用 prompt 中写 clusters jq 示例

## 10. Mock Server 文档与示例数据

Phase12 实现时，`docs/api/dbaas-mock-server-api.md` 应补充集群接口：

```http
GET /clusters
```

接口用途：

- 管理员查询集群资产数据
- ai-agent lazy refresh clusters snapshot
- Agent 查询集群启用状态、支持的 CPU 架构、支持的软件类型和支持的网络

权限：

- 管理员可见全部集群
- 普通用户不可访问

示例应使用模拟但接近真实格式的数据，不使用生产真实数据。

示例：

```json
[
  {
    "id": "3101",
    "name": "NJ-MYSQL-CLUSTER-01",
    "siteId": "12",
    "siteName": "南京一区",
    "areaId": "8",
    "areaName": "核心区",
    "supportedCpuArchitectures": ["amd64"],
    "supportedCpuArchitectureNames": ["X86"],
    "supportedSoftwareTypes": ["mysql", "redis", "mongodb"],
    "supportedNetworkNames": ["LEAF-10.24.16", "LEAF-10.24.17"],
    "haNetworkTag": "NJ-MYSQL-CLUSTER-01",
    "enabled": true,
    "description": "核心数据库集群",
    "createdAt": "2026-05-18 10:23:00",
    "createdBy": "ops_admin",
    "createdByName": "运维管理员",
    "updatedAt": "2026-05-20 15:42:11",
    "updatedBy": "ops_admin",
    "updatedByName": "运维管理员"
  }
]
```

说明：

- mock-server 接口返回结构应与 ai-agent clusters schema 保持一致
- ai-agent snapshot 直接保存该结构，不做字段映射
- `docs/api/dbaas-mock-server-api.md` 中的示例也应使用同一结构

## 11. 测试清单

### 11.1 Schema 测试

- `describe_schema("clusters")` 在 admin 身份下返回 `clusters.v1`
- `describe_schema("clusters")` 在普通用户身份下拒绝
- clusters schema 顶层为数组
- schema 包含 `supportedCpuArchitectures`
- schema 包含 `supportedSoftwareTypes`
- schema 包含 `supportedNetworkNames`
- schema 只包含 Phase12 支持的集群查询字段

### 11.2 同步测试

- DBAAS/mock-server 返回结构与 clusters schema 一致时可写入 snapshot
- `id` / `siteId` / `areaId` 为字符串
- `supportedCpuArchitectures` / `supportedCpuArchitectureNames` 为数组
- `supportedSoftwareTypes` / `supportedNetworkNames` 为数组
- 响应不是数组时返回 error
- 数组元素不是对象时返回 error
- 返回字段不符合 clusters schema 时返回 error

### 11.3 Query 测试

- 管理员首次查询时 lazy refresh `/clusters`
- snapshot fresh 时不重复请求 DBAAS
- `refresh=true` 时强制刷新 `/clusters`
- jq 可以按 `enabled` 筛选启用集群
- jq 可以按 `supportedSoftwareTypes` 筛选支持 `mysql` 的集群
- jq 可以按 `supportedCpuArchitectures` 筛选支持 `amd64` 的集群
- jq 可以按 `supportedNetworkNames` 筛选支持指定网络的集群
- jq 输出过大时 preview 被截断

### 11.4 权限与工具集测试

- admin tool set 包含 `query_dbaas_cluster_data_tool`
- user tool set 不包含 `query_dbaas_cluster_data_tool`
- 普通用户路径误调用 cluster query 返回 `permission_denied`
- 普通用户最终 system prompt 不包含 `query_dbaas_cluster_data_tool`
- 普通用户最终 system prompt 不包含 `kind="clusters"` 调用形式或 clusters jq 示例

### 11.5 refresh 失败语义测试

- 已有 fresh snapshot 时，`refresh=true` 请求失败返回 error
- `refresh=true` 失败不把旧 snapshot 当最新结果
- 失败后旧 fresh snapshot 可继续用于普通 `refresh=false` 查询
- meta 中记录 `last_refresh_status=error` 和 `last_error`

## 12. 实现步骤建议

推荐按以下顺序实现：

1. 新增 constants / schema 支持
2. 新增 `clusters.v1.schema.json`
3. 新增 cluster sync
4. 新增 cluster query
5. 新增 cluster tool 并接入 admin-only tool set
6. 更新 prompt
7. 更新 mock server API 文档
8. 补充单元测试
9. 运行相关测试

每一步保持小范围提交，避免同时引入 Phase13 网络查询。

## 13. 暂缓到 Phase13 的内容

以下能力不在 Phase12 实现：

- 网络列表查询
- 网络 IP 范围查询
- IPv4 / IPv6 网关、掩码、VLAN 查询
- IP 总量、已用量和剩余量统计
- 根据集群 join 网络详情
- 网络启用状态查询
- 网络容量预检

Phase12 只通过 `supportedNetworkNames` 告诉模型某集群支持哪些网络名称。
需要网络详情时，应说明 Phase13 才支持。
