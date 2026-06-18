# DBAAS 智能助手第十三阶段：网段查询 Admin Tool

## 0. 当前状态

- 状态：设计落定，待实现
- 本文档作用：定义网段查询第一版的数据结构、数据视图刷新策略、tool 行为和权限规则
- 核心边界：Phase13 首版只做 admin-only 网段查询，不做网段写操作，不做普通用户网段数据视图
- 参考关系：
  - 查询形态参考 services / hosts / clusters：使用 schema 描述结构，使用 jq 查询本地数据视图，原始大数据不直接进入模型上下文
  - 刷新形态参考 backups / clusters：首次查询、数据过期或 `refresh=true` 时在 tool 内 lazy refresh，不做后台周期同步

## 1. Phase13 v1 目标

Phase13 第一版实现 DBAAS 网段查询能力。

本阶段范围包括：

- 管理员按需查询 DBAAS 网段列表
- 从 DBAAS 网络接口拉取与 ai-agent schema 一致的 network segment record
- 将网段列表落盘为本地数据视图
- 使用统一 networkSegments schema 描述查询字段
- 使用 jq 查询本地网段数据视图
- 支持 `refresh=true` 强制刷新
- 明确支持的网段、网关、VLAN、启用状态和归属信息，避免模型猜字段含义
- 只将 networkSegments tool 注册给 admin agent

本阶段不包括：

- 普通用户网段查询
- 普通用户 networkSegments 数据视图
- 网段写操作
- 网络地址池调度或自动分配

## 2. 核心结论

Network segment v1 采用 admin-only lazy snapshot。

也就是说：

- 只有管理员身份可以使用网段查询 tool
- 普通 user agent 不注册 networkSegments tool
- 网络数据只维护 admin scope 数据视图
- 不启动后台周期刷新任务
- tool 调用时检查 `admin/networkSegments.meta.json`
- 如果数据视图 fresh，直接执行 jq
- 如果数据视图缺失或 stale，tool 内触发一次 DBAAS 网络接口刷新
- `refresh=true` 时无论当前数据视图是否 fresh，都强制刷新
- 刷新失败时不使用 stale 数据冒充最新事实

Network segment 和 clusters / hosts 的相似点：

- 都是管理员平台级资产数据
- 都只提供 admin scope 查询能力
- 都使用本地数据视图承载大列表查询
- 都通过 schema 约束模型可用字段

Network segment 和 backups 的相似点：

- 都是按需查询型数据视图
- 都支持 `refresh=true`
- 都可以在 tool 内按需拉取 DBAAS 最新数据
- refresh 失败时都不能使用 stale 数据冒充最新事实

### 2.1 与 Phase12 的对齐点

Phase13 在实现方式上与 Phase12 保持一致，方便模型在集群和网段之间复用同一套查询习惯：

- 都是 admin-only 的平台资产查询
- 都采用 schema + 本地 snapshot + jq 的组合
- 都在首次查询、数据过期或显式刷新时做 lazy refresh
- 都要求 ai-agent schema 与 mock-server 接口结构一致
- 都不在 ai-agent 内额外做字段映射
- 都把大列表数据留在本地视图里，不直接把整表灌进模型上下文

### 2.2 与 Phase12 的差异

Phase13 相比 Phase12 的新增关注点主要是网络地址表达：

- 需要同时描述 IPv4 与 IPv6
- 需要同时表达起止地址、网关、掩码、VLAN 和使用率
- 需要能通过 `clusterId`、`clusterName` 和 `supportedNetworkNames` 和集群数据关联
- 需要让模型能直接理解字段含义，所以尽量使用 `startIpv4`、`gatewayIpv4`、`ipv4UsagePercent` 这类直观字段

## 3. Network Segment Record Schema

DBAAS 网络接口应直接返回与 ai-agent schema 一致的 network segment record 数组。Phase13 不在 ai-agent 内设计额外字段映射层。

当前接口假设：

```http
GET /network-segments
```

响应形态假设为顶层数组。

示例：

```json
[
  {
    "id": "71001",
    "siteId": "12",
    "siteName": "南京一区",
    "name": "LEAF-10.24.16",
    "startIpv4": "10.24.16.11",
    "endIpv4": "10.24.16.240",
    "gatewayIpv4": "10.24.16.254",
    "startIpv6": "2405:db8:2000:1010::b",
    "endIpv6": "2405:db8:2000:1010::f0",
    "gatewayIpv6": "2405:db8:2000:1010::1",
    "ipv4MaskLength": 24,
    "ipv6MaskLength": 64,
    "ipv4TotalCount": 230,
    "ipv4UsedCount": 86,
    "ipv4UsagePercent": 37.4,
    "ipv6TotalCount": 230,
    "ipv6UsedCount": 24,
    "ipv6UsagePercent": 10.4,
    "vlanId": 2416,
    "enabled": true,
    "clusterId": "3101",
    "clusterName": "NJ-MYSQL-CLUSTER-01",
    "description": "核心数据库网段",
    "createdAt": "2026-05-18 10:23:00",
    "createdBy": "ops_admin",
    "createdByName": "运维管理员"
  }
]
```

示例数据要求：

- 示例只模拟真实格式，不使用生产真实数据
- 网络名称、IP、VLAN、人员和时间示例只模拟真实格式，不使用生产真实数据
- 时间字段沿用 hosts 风格，保留普通时间字符串，不在 Phase13 强制转换为 ISO 8601
- 记录值尽量贴近真实业务格式，但不要使用生产系统中的原始值
- 字段命名尽量面向模型直接理解，避免再引入二级映射语义

字段约定：

- `id`
  - 网段唯一 ID
  - 字符串
- `siteId`
  - 网段所属站点 ID
  - 字符串
- `siteName`
  - 网段所属站点名称
- `name`
  - 网段名称
- `startIpv4`
  - IPv4 起始地址
- `endIpv4`
  - IPv4 结束地址
- `gatewayIpv4`
  - IPv4 网关地址
- `startIpv6`
  - IPv6 起始地址
- `endIpv6`
  - IPv6 结束地址
- `gatewayIpv6`
  - IPv6 网关地址
- `ipv6MaskLength`
  - IPv6 掩码长度
- `ipv4MaskLength`
  - IPv4 掩码长度
- `ipv4TotalCount`
  - IPv4 总地址数量
- `ipv4UsedCount`
  - IPv4 已使用地址数量
- `ipv4UsagePercent`
  - IPv4 地址使用率，范围 0-100
- `ipv6TotalCount`
  - IPv6 总地址数量
- `ipv6UsedCount`
  - IPv6 已使用地址数量
- `ipv6UsagePercent`
  - IPv6 地址使用率，范围 0-100
- `vlanId`
  - VLAN ID
- `enabled`
  - 网段是否启用
- `clusterId`
  - 所属集群 ID
  - 字符串
- `clusterName`
  - 所属集群名称
- `description`
  - 网段描述，没有描述时为空字符串
- `createdAt`
  - 网段记录创建时间
- `createdBy`
  - 网段记录创建人账号
- `createdByName`
  - 网段记录创建人姓名

## 4. Schema 约束

Schema 要求：

- 顶层为数组
- 数组元素为 network segment record 对象
- `id`、`siteId`、`clusterId` 使用字符串
- `ipv4TotalCount`、`ipv4UsedCount`、`ipv6TotalCount`、`ipv6UsedCount`、`ipv4MaskLength`、`ipv6MaskLength`、`vlanId` 使用数字类型
- `ipv4UsagePercent`、`ipv6UsagePercent` 使用数字类型，取值范围为 0-100
- `enabled` 使用 boolean
- `description` 没有描述时为空字符串
- 接口返回结构应与 `config/schemas/network-segments.v1.schema.json` 保持一致

## 5. 枚举与值约定

### 5.1 enabled

`enabled` 使用 boolean：

```text
true
false
```

Phase13 不额外引入状态显示名称字段。

### 5.2 地址与数量字段

- `startIpv4` / `endIpv4` / `gatewayIpv4`
- `startIpv6` / `endIpv6` / `gatewayIpv6`
- `ipv4TotalCount` / `ipv4UsedCount`
- `ipv4UsagePercent`
- `ipv6TotalCount` / `ipv6UsedCount`
- `ipv6UsagePercent`

这些字段都按普通字符串或数字处理，不在首版强制更复杂的格式约束。

## 6. Snapshot 文件与元数据

新增 kind：

```text
networkSegments
```

新增 schema version：

```text
networkSegments.v1
```

新增文件：

```text
admin/networkSegments.json
admin/networkSegments.meta.json
config/schemas/network-segments.v1.schema.json
```

`networkSegments.meta.json` 结构沿用 backups / clusters 的 snapshot meta 风格：

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
network_segment_snapshot_ttl_seconds = 120
network_segment_refresh_lock_timeout_seconds = 10
```

Phase13 不需要 `network_segment_sync_interval_seconds`，因为不做后台周期同步。

### 6.1 Snapshot 目录约定

本阶段 snapshot 与 meta 的落盘目录保持和 Phase12 一致的风格：

- `admin/networkSegments.json`
- `admin/networkSegments.meta.json`

这样在人工排查和后续自动化测试里，可以直接按 kind 找到对应的 snapshot 文件。

## 7. Query Tool 设计

新增 admin-only tool：

```text
query_dbaas_network_segment_data_tool
```

参数：

- `jq_filter: str`
  - jq 查询表达式
- `max_preview_items: int | None = None`
  - 返回给模型的最大预览条数
- `refresh: bool = False`
  - 是否强制刷新网段 snapshot

工具行为：

- 普通用户不可注册该 tool
- 如果非管理员路径误调用，返回 `permission_denied`
- 调用时先检查 networkSegments snapshot
- snapshot fresh 时直接执行 jq
- snapshot 缺失或 stale 时 lazy refresh
- `refresh=true` 时强制请求 DBAAS 网段接口
- DBAAS 返回非数组时返回 snapshot error
- DBAAS 返回数组但元素不是对象时返回 snapshot error
- DBAAS 返回字段类型不符合 schema 时返回 snapshot error
- jq 输出过大时沿用现有 preview / truncation 行为

成功消息建议：

```text
查询完成，结果来自当前管理员可见的 DBAAS 网段数据视图。
```

失败消息建议：

```text
当前没有可用的 DBAAS 网段数据视图，暂时无法获得准确数据：{reason}
```

### 7.1 常见查询意图

Phase13 重点覆盖的自然语言查询意图包括：

- 查询全部启用的网段
- 查询某个站点下的网段
- 查询某个集群支持的网段
- 查询 IPv4 使用率较高的网段
- 查询某个 VLAN 对应的网段
- 查询某个网段的 IPv4 / IPv6 起止地址和网关

这些意图都应优先映射到 `query_dbaas_network_segment_data_tool`，而不是去猜别的资产类型。

## 8. Schema Tool 行为

`describe_dbaas_schema_tool(kind="networkSegments")` 仅管理员可用。

管理员：

- 返回 `networkSegments.v1` schema
- scope 为 `admin`

普通用户：

- 不允许 describe `networkSegments`
- 返回或抛出 schema scope 错误，行为与 hosts / clusters 保持一致

通用 schema 支持列表应新增：

```text
networkSegments
```

但 role prompt 边界必须保持：

- 通用 system prompt 可以描述“当前支持服务、备份、监控，以及管理员可见的平台资产查询”
- admin extend prompt 写明 networkSegments tool 名称、schema kind 和查询规则
- user extend prompt 只说明普通用户无权查询平台级网段数据
- 不在通用 system prompt 暴露 admin-only tool 名称、schema kind 调用形式或示例参数

### 8.1 Schema 兼容原则

为了和 Phase12 保持同一种“先看 schema，再组查询”的习惯，Phase13 里所有网络字段都遵守下面的原则：

- 字段名尽量语义直观
- 长整型计数值明确拆开，不混放在一个统计字段里
- 使用率直接用百分比字段表达
- 归属信息保持可联动，不在查询层再做二次翻译

## 9. Prompt 规则

### 9.1 admin extend prompt

新增管理员网段查询规则：

- 管理员可以查询网段数据
- 查询网段列表、网段启用状态、IPv4/IPv6 地址范围、网关、VLAN、地址使用率、所属站点或所属集群时，调用 `query_dbaas_network_segment_data_tool`
- 构造 networkSegments 的 jq_filter 前，必须按 networkSegments schema 使用字段名
- 首次查询网段数据或字段不确定时，先调用 `describe_dbaas_schema_tool(kind="networkSegments")`
- networkSegments 查询默认 `refresh=false`
- 用户明确要求最新、刷新、当前或实时网段列表时，调用 `query_dbaas_network_segment_data_tool` 传 `refresh=true`

### 9.2 user extend prompt

普通用户规则保持平台级权限边界：

- 普通用户不能查询平台级网段数据
- 当普通用户请求网段列表、VLAN、地址池使用率、跨站点网络资源或跨用户平台资源时，说明需要管理员权限
- 普通用户没有 networkSegments schema 和 network segment query tool

### 9.3 命名边界

Phase13 中保持以下命名边界：

- 文档里使用 `networkSegments` 作为 kind
- 列表记录使用 `network segment record`
- mock-server 和 ai-agent 使用同一结构
- 不在 ai-agent 内引入额外的字段翻译层

### 9.4 查询优先级

当用户同时提到集群和网段时，优先顺序如下：

1. 先判断是在问集群还是在问网段
2. 如果问题明确包含网段、VLAN、网关、起止地址或使用率，优先走 networkSegments
3. 如果问题主要在问某集群支持什么网络，再用 clusters 的 `supportedNetworkNames`
4. 如果问题需要把两者关联起来，再分别查询两个 snapshot 后做关联分析

### 9.5 system prompt

通用 prompt 只更新能力边界，不写 admin-only 工具名。

需要避免：

- 在通用 prompt 中写 `query_dbaas_network_segment_data_tool`
- 在通用 prompt 中写 `describe_dbaas_schema_tool(kind="networkSegments")`
- 在通用 prompt 中写 networkSegments jq 示例

## 10. Mock Server 文档与示例数据

Phase13 实现时，`docs/api/dbaas-mock-server-api.md` 应补充网段接口：

```http
GET /network-segments
```

接口用途：

- 管理员查询网段资产数据
- ai-agent lazy refresh networkSegments snapshot
- Agent 查询网段 IPv4/IPv6 范围、网关、VLAN、地址使用率、所属站点和所属集群

权限：

- 管理员可见全部网段
- 普通用户不可访问

示例应使用模拟但接近真实格式的数据，不使用生产真实数据。

示例：

```json
[
  {
    "id": "71001",
    "siteId": "12",
    "siteName": "南京一区",
    "name": "LEAF-10.24.16",
    "startIpv4": "10.24.16.11",
    "endIpv4": "10.24.16.240",
    "gatewayIpv4": "10.24.16.254",
    "startIpv6": "2405:db8:2000:1010::b",
    "endIpv6": "2405:db8:2000:1010::f0",
    "gatewayIpv6": "2405:db8:2000:1010::1",
    "ipv4MaskLength": 24,
    "ipv6MaskLength": 64,
    "ipv4TotalCount": 230,
    "ipv4UsedCount": 86,
    "ipv4UsagePercent": 37.4,
    "ipv6TotalCount": 230,
    "ipv6UsedCount": 24,
    "ipv6UsagePercent": 10.4,
    "vlanId": 2416,
    "enabled": true,
    "clusterId": "3101",
    "clusterName": "NJ-MYSQL-CLUSTER-01",
    "description": "核心数据库网段",
    "createdAt": "2026-05-18 10:23:00",
    "createdBy": "ops_admin",
    "createdByName": "运维管理员"
  }
]
```

说明：

- mock-server 接口返回结构应与 ai-agent networkSegments schema 保持一致
- ai-agent snapshot 直接保存该结构，不做字段映射
- `docs/api/dbaas-mock-server-api.md` 中的示例也应使用同一结构
- mock-server seed 建议从 cluster 的 `supportedNetworkNames` 派生，使网络和集群天然可关联
- 每个 cluster 可生成 2 个 network segment；108 个 cluster 时至少有 216 个 network segment

### 10.1 生成规则建议

为了让 mock 数据更像真实业务，建议 seed 生成时满足：

- 同一个 cluster 的网段名称可以重复出现在多个站点下，但 `id` 必须唯一
- `ipv4UsagePercent` 与计数字段保持一致
- `ipv6UsagePercent` 与计数字段保持一致
- `enabled`、VLAN、地址段和所属集群之间保持合理分布
- 时间字段不要全都一样，避免看起来像静态死数据
- 示例值不要直接复用生产系统里的真实字符串

## 11. 与集群、主机的联动查询边界

Phase13 支持模型把 networkSegments 与 clusters / hosts 联合分析，但数据仍分别来自各自 snapshot：

- `networkSegments.clusterId` / `clusterName` 可与 clusters 的 `id` / `name` 关联
- `networkSegments.siteId` / `siteName` 可与 hosts / clusters 的站点字段关联
- `networkSegments.name` 可与 clusters 的 `supportedNetworkNames` 关联
- hosts 当前只有 `networkPartition`，它不是 network segment 名称，不应直接当成 `networkSegments.name`

联动查询示例场景：

- 查询某站点哪些网段属于支持 MySQL 的集群
- 查询某集群支持哪些网络以及每个网段的 IPv4 使用率
- 查询 IPv4 使用率超过 80% 的网段及其所属集群
- 查询某 VLAN 对应的站点、集群和网关

不支持的推断：

- 不能仅凭 `networkPartition` 判断主机属于哪个网段
- 不能从 networkSegments 自动推断主机 IP 占用明细
- 不能在 Phase13 中做 IP 分配、回收或扩容操作

### 11.1 联动查询示例

下面这些问题都应被视为 Phase13 的目标问题：

- “查一下南京一区里哪些网段是给 MySQL 集群用的”
- “把某个集群支持的网段都列出来，再看哪个 IPv4 使用率最高”
- “找出 IPv4 使用率超过 80% 的网段，顺带带上所属集群和站点”
- “某个 VLAN 对应的网段有哪些，网关分别是什么”

## 12. 测试清单

### 12.1 Schema 测试

- `describe_schema("networkSegments")` 在 admin 身份下返回 `networkSegments.v1`
- `describe_schema("networkSegments")` 在普通用户身份下拒绝
- networkSegments schema 顶层为数组
- schema 包含 `startIpv4`
- schema 包含 `gatewayIpv4`
- schema 包含 `ipv4UsagePercent`
- schema 包含 `ipv6UsagePercent`
- schema 包含 `enabled`
- schema 只包含 Phase13 支持的网段查询字段

### 12.2 同步测试

- DBAAS/mock-server 返回结构与 networkSegments schema 一致时可写入 snapshot
- `id` / `siteId` / `clusterId` 为字符串
- `enabled` 为 boolean
- `ipv4MaskLength` / `ipv6MaskLength` 为 integer
- `ipv4TotalCount` / `ipv4UsedCount` / `ipv6TotalCount` / `ipv6UsedCount` 为 integer
- `ipv4UsagePercent` / `ipv6UsagePercent` 为 number
- 响应不是数组时返回 error
- 数组元素不是对象时返回 error
- 返回字段不符合 networkSegments schema 时返回 error

### 12.3 Query 测试

- 管理员首次查询时 lazy refresh `/network-segments`
- snapshot fresh 时不重复请求 DBAAS
- `refresh=true` 时强制刷新 `/network-segments`
- jq 可以按 `enabled` 筛选启用网段
- jq 可以按 `siteName` 筛选某站点网段
- jq 可以按 `clusterName` 筛选某集群网段
- jq 可以按 `ipv4UsagePercent` 筛选高使用率网段
- jq 可以按 `vlanId` 查询指定 VLAN
- jq 输出过大时 preview 被截断

### 12.4 权限与工具集测试

- admin tool set 包含 `query_dbaas_network_segment_data_tool`
- user tool set 不包含 `query_dbaas_network_segment_data_tool`
- 普通用户路径误调用 network segment query 返回 `permission_denied`
- 普通用户最终 system prompt 不包含 `query_dbaas_network_segment_data_tool`
- 普通用户最终 system prompt 不包含 `kind="networkSegments"` 调用形式或 networkSegments jq 示例

### 12.5 refresh 失败语义测试

- 已有 fresh snapshot 时，`refresh=true` 请求失败返回 error
- `refresh=true` 失败不把旧 snapshot 当最新结果
- 失败后旧 fresh snapshot 可继续用于普通 `refresh=false` 查询
- meta 中记录 `last_refresh_status=error` 和 `last_error`

### 12.6 mock-server 测试

- `/network-segments` 管理员访问返回 200
- `/network-segments` 普通用户访问返回 403
- 返回数量不少于 200 条
- 返回字段与 ai-agent networkSegments schema 一致
- 返回记录不包含生产原始字段名
- 返回记录中的 `name` 能对应到某个 cluster 的 `supportedNetworkNames`
- `ipv4UsagePercent` 与 `ipv4UsedCount / ipv4TotalCount` 基本一致
- `ipv6UsagePercent` 与 `ipv6UsedCount / ipv6TotalCount` 基本一致

### 12.7 自然语言验收

至少补一轮真实模型验证，覆盖下面几类问法：

- 按站点查网段
- 按集群查网段
- 按 VLAN 查网段
- 按 IPv4 或 IPv6 使用率查网段
- 把网段和集群一起问，确认不会混淆成主机资产
- 明确要求“最新”时，确认会走 `refresh=true`

## 13. 实现步骤建议

推荐按以下顺序实现：

1. 新增 constants / schema 支持
2. 新增 `network-segments.v1.schema.json`
3. 新增 network segment sync
4. 新增 network segment query
5. 新增 network segment tool 并接入 admin-only tool set
6. 更新 prompt
7. 更新 mock server API 文档
8. 更新 mock-server schema / endpoint / seed
9. 补充 ai-agent 单元测试
10. 补充 mock-server 平台测试
11. 运行相关测试
12. 用真实 LLM 进行管理员自然语言查询验证

每一步保持小范围变更，避免同时引入网络写操作或 IP 分配能力。

### 13.1 相关文件

Phase13 落地时，主要影响这些位置：

- `config/schemas/network-segments.v1.schema.json`
- `backend/src/dbass_ai_agent/dbaas/constants.py`
- `backend/src/dbass_ai_agent/dbaas/schema.py`
- `backend/src/dbass_ai_agent/dbaas/config.py`
- `backend/src/dbass_ai_agent/dbaas/service_tools.py`
- `backend/src/dbass_ai_agent/dbaas/cluster_tools.py` 之外的新 network segment query/sync 模块
- `backend/prompts/admin_extend_system_prompt.md`
- `backend/prompts/user_extend_system_prompt.md`
- `backend/prompts/system.md`
- `docs/api/dbaas-mock-server-api.md`
- `mock-server` 的 schema、API、seed 和测试

## 14. 暂缓到后续阶段的内容

以下能力不在 Phase13 实现：

- 网络创建、编辑、删除
- 启用或停用网段
- IP 地址分配和回收
- 网段容量扩容或缩容
- 普通用户网络数据视图

## 15. 验收标准

Phase13 完成时应满足：

- 管理员可以通过自然语言查询网段列表、IP 范围、网关、VLAN、地址使用率和所属集群
- 普通用户不会看到 networkSegments tool 或 schema kind 调用形式
- ai-agent schema 与 mock-server `/network-segments` 响应结构一致
- 首次查询或 snapshot 过期时会 lazy refresh
- `refresh=true` 失败不会把旧数据当作最新数据
- mock-server 中 network segment 与 cluster 的网络名称能对应
- 相关单元测试通过

### 15.1 人工验收标准

补充人工检查时，重点看这几件事：

- 文档里的字段命名是否足够直观，模型不需要猜
- 示例数据是否像真实业务而不是模板值
- ai-agent 和 mock-server 是否真的共用同一套结构
- 管理员和普通用户的边界是否清晰
- 集群和网段一起问时，模型是否能稳住关联关系
