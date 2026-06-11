# DBAAS 智能助手第十一阶段：主机资产查询 Admin Tool

## 0. 当前状态

- 状态：设计落定，待实现
- 本文档作用：定义主机资产查询第一版的接口边界、数据视图刷新策略、tool 行为和权限规则
- 核心边界：Phase11 首版只做 admin-only 主机查询，不做主机写操作，不接 `/hosts/{host_id}`，不做普通用户主机数据视图
- 参考关系：
  - 查询形态参考 Phase5 services：使用 schema 描述结构，使用 jq 查询本地数据视图，原始大数据不直接进入模型上下文
  - 刷新形态参考 Phase5 services 的 admin snapshot 后台刷新，并保留 Phase9 backups 风格的 tool 内 lazy refresh 兜底

## 1. Phase11 v1 目标

Phase11 第一版实现 DBAAS 主机资产查询能力。

本阶段范围包括：

- 管理员按需查询 DBAAS 主机列表
- 将 `GET /hosts` 返回的主机列表落盘为本地数据视图
- 使用统一 hosts schema 描述查询字段
- 使用 jq 查询本地主机数据视图
- 后台周期刷新 admin hosts 数据视图
- 支持 `refresh=true` 强制刷新
- 明确主机字段、容量单位和状态枚举，避免模型猜测字段含义
- 只将 host tool 注册给 admin agent

本阶段不包括：

- 普通用户主机查询
- 普通用户 host 数据视图
- `/hosts/{host_id}` 主机详情接口
- 主机入库、出库、启用、停用或维护切换
- 主机迁移、调度、资源重分布
- 主机 precheck
- 容器明细查询

## 2. 核心结论

Host v1 采用 admin-only 后台周期刷新数据视图，并在 tool 内保留 lazy refresh 兜底。

也就是说：

- 只有管理员身份可以使用主机查询 tool
- 普通 user agent 不注册 host tool
- Host 数据只维护 admin scope 数据视图
- 后台周期刷新 `admin/hosts.json`
- tool 调用时检查 `admin/hosts.meta.json`
- 如果数据视图 fresh，直接执行 jq
- 如果数据视图缺失或 stale，tool 内触发一次 `GET /hosts` 刷新作为兜底
- `refresh=true` 时无论当前数据视图是否 fresh，都强制刷新
- 刷新失败时不使用 stale 数据冒充最新事实

Host 和 services 的相似点：

- 都使用本地数据视图承载大列表查询
- 都通过后台周期刷新降低查询时延
- 都支持 `refresh=true` 在用户明确要求最新时强制刷新

Host 和 services 的差异：

- services 是核心业务事实源，且涉及普通用户权限、active lease、prewarm 和后台同步
- hosts 是管理员平台资产事实源，首版只服务管理员查询
- Host v1 只照搬 services 的 admin snapshot 周期刷新，不照搬 user 数据视图、active lease 和 prewarm 机制

Host 和 backups 的相似点：

- 都是按需查询型数据视图
- 都支持 `refresh=true`
- 都可以在 tool 内按需拉取 DBAAS 最新数据
- refresh 失败时都不能使用 stale 数据冒充最新事实

## 3. Host Record Schema

DBAAS `/hosts` 应直接返回 agent-facing host record 数组。

示例：

```json
[
  {
    "id": "4212111182",
    "name": "syn47011000",
    "ip": "192.18.11.11",
    "sshPort": 22,
    "siteId": "585430486",
    "siteName": "上海PIT站",
    "clusterId": "1026800163",
    "clusterName": "上海PIT站 Cluster 01",
    "clusterEnabled": true,
    "areaId": "1664968891",
    "areaName": "核心区",
    "room": "CN-EAST-1-ROOM-01",
    "seat": "CN-EAST-1-01-01",
    "networkPartition": "ha-a",
    "status": "enabled",
    "healthStatus": "HEALTHY",
    "cpuArchitecture": "amd64",
    "cpuArchitectureName": "X86",
    "cpuCapacityCores": 48,
    "cpuAllocatedCores": 28,
    "cpuAvailableCores": 20,
    "cpuAllocationPercent": 58.3,
    "memoryCapacityGB": 240,
    "memoryAllocatedGB": 104,
    "memoryAvailableGB": 136,
    "memoryAllocationPercent": 43.3,
    "hdd": {
      "device": "/dev/sdb",
      "capacityGB": 8192,
      "usedGB": 4170.1,
      "availableGB": 4021.9,
      "usagePercent": 50.9
    },
    "ssd": null,
    "sanName": null,
    "maxUnitCount": 80,
    "maxUsagePercent": 80,
    "unitCount": 6,
    "createdAt": "2026-05-24 10:23:00",
    "creator": "03001007",
    "creatorName": "陈思远"
  }
]
```

字段约定：

- `id`
  - 主机唯一 ID
- `siteId`
  - 站点 ID
- `areaId`
  - 区域 ID
- `clusterId`
  - 集群 ID
- `name`
  - 主机名称
- `ip`
  - 主机 IP
- `sshPort`
  - SSH 端口
- `siteName`
  - 站点名称
- `clusterName`
  - 集群名称
- `clusterEnabled`
  - 集群是否启用
- `areaName`
  - 区域名称
- `room`
  - 机房或机房位置
- `seat`
  - 机位
- `networkPartition`
  - HA 网络分区，用于判断副本或主备是否处在同一网络分区
- `status`
  - 主机管控状态
- `healthStatus`
  - 主机健康状态
- `cpuArchitecture`
  - CPU 架构机器值，例如 `amd64`、`arm64`
- `cpuArchitectureName`
  - CPU 架构显示名称，例如 `X86`、`ARM`
- `cpuCapacityCores`
  - CPU 总核数
- `cpuAllocatedCores`
  - 已分配 CPU 核数
- `cpuAvailableCores`
  - 可分配 CPU 核数
- `cpuAllocationPercent`
  - CPU 分配率
- `memoryCapacityGB`
  - 内存总容量
- `memoryAllocatedGB`
  - 已分配内存
- `memoryAvailableGB`
  - 可分配内存
- `memoryAllocationPercent`
  - 内存分配率
- `hdd`
  - HDD 存储设备摘要
  - 没有 HDD 时为 `null`
- `ssd`
  - SSD 存储设备摘要
  - 没有 SSD 时为 `null`
- `sanName`
  - SAN 存储名称，没有时为 `null`
- `maxUnitCount`
  - 主机最大承载单元数量
- `maxUsagePercent`
  - 主机最大资源使用率
- `unitCount`
  - 当前已承载单元数量
- `createdAt`
  - 主机记录创建时间
- `creator`
  - 主机记录创建人账号
- `creatorName`
  - 主机记录创建人姓名

### 3.1 hdd / ssd 字段

`hdd` 和 `ssd` 使用相同结构：

```json
{
  "device": "/dev/sdb",
  "capacityGB": 8192,
  "usedGB": 4170.1,
  "availableGB": 4021.9,
  "usagePercent": 50.9
}
```

字段约定：

- `device`
  - 主机侧设备路径
- `capacityGB`
  - 存储总容量
- `usedGB`
  - 已使用容量
- `availableGB`
  - 可用容量
- `usagePercent`
  - 容量使用率

首版 mock 数据中 `hdd` 和 `ssd` 二选一。

真实 DBAAS 如果存在同时配置 HDD 和 SSD 的主机，可以同时返回两个对象。
查询 tool 和 schema 应允许：

```text
hdd: HostStorageDevice | null
ssd: HostStorageDevice | null
```

但 DBAAS 不应返回两者同时为 `null` 的主机。

## 4. 枚举字段

枚举值由 DBAAS 保证稳定输出。
ai-agent 不负责把数字状态、中文状态或展示名映射成枚举。

### 4.1 status

当前建议值：

```text
enabled
disabled
onboarding
offboarding
maintenance
```

含义：

- `enabled`
  - 启用，可作为调度或容量判断候选
- `disabled`
  - 停用，不应作为调度候选
- `onboarding`
  - 入库中
- `offboarding`
  - 出库中
- `maintenance`
  - 维护中

### 4.2 healthStatus

首版沿用当前 DBAAS/mock-server 健康状态：

```text
HEALTHY
WARN
UNHEALTHY
UNKNOWN
```

模型行为：

- `status` 决定主机是否处于可用管控状态
- `healthStatus` 决定主机运行健康风险
- 即使 `status=enabled`，如果 `healthStatus != HEALTHY`，模型也应提示风险

## 5. 数据视图文件

Host v1 只维护管理员数据视图：

```text
data/runtime/dbaas_workspace/
  admin/
    hosts.json
    hosts.meta.json
```

不维护：

```text
data/runtime/dbaas_workspace/users/{safe_user}/hosts.json
data/runtime/dbaas_workspace/users/{safe_user}/hosts.meta.json
```

`hosts.meta.json` 示例：

```json
{
  "kind": "hosts",
  "version": 1,
  "scope": "admin",
  "user": null,
  "data_path": ".../admin/hosts.json",
  "meta_path": ".../admin/hosts.meta.json",
  "status": "fresh",
  "synced_at": "2026-06-10T12:00:00+08:00",
  "expires_at": "2026-06-10T12:02:00+08:00",
  "ttl_seconds": 120,
  "record_count": 2880,
  "bytes": 3241888,
  "source": "dbaas",
  "source_endpoint": "/hosts",
  "schema_version": "hosts.v1",
  "schema_path": "config/schemas/hosts.v1.schema.json",
  "last_refresh_status": "success",
  "last_error": null
}
```

meta 规则：

- `kind` 固定为 `hosts`
- `scope` 固定为 `admin`
- `schema_version` 固定为 `hosts.v1`
- `source_endpoint` 固定为 `/hosts`
- `status=fresh` 时 `data_path` 必须指向当前可用 `hosts.json`
- `status=error` 时 `data_path` 必须为 `null`
- tool 不能返回 stale 数据视图的 `data_path`

## 6. 后台刷新与 Lazy Refresh 策略

Host v1 做 admin hosts 后台周期刷新，同时在查询 tool 内保留 lazy refresh 兜底。

后台刷新规则：

- 启动后由 DBAAS background sync 创建独立 admin hosts 刷新任务
- 后台任务只刷新 `admin/hosts.json`，不创建普通用户 hosts 视图
- hosts 后台刷新使用独立配置，不直接复用 services 的短周期：
  - `host_sync_interval_seconds`
  - `host_snapshot_ttl_seconds`
  - `host_refresh_lock_timeout_seconds`
- 后台刷新失败时不删除仍 fresh 的旧 `hosts.json`
- 后台刷新失败应记录 `last_refresh_status=error` 和 `last_error`
- 如果当前 snapshot 已 stale/missing，刷新失败时发布 `status=error`、`data_path=null` 的 meta

查询 tool 调用时执行 lazy refresh 判断：

```text
query_dbaas_host_data_tool(refresh=false):
  1. 读取 admin/hosts.meta.json
  2. 如果 hosts 数据视图 fresh，直接执行 jq
  3. 如果数据视图缺失或 stale，调用 GET /hosts 刷新
  4. 刷新成功后执行 jq
  5. 刷新失败返回 snapshot_unavailable，不使用 stale 数据

query_dbaas_host_data_tool(refresh=true):
  1. 获取 host 数据视图刷新锁
  2. 不预先删除旧 hosts.json
  3. 无论当前数据视图是否 fresh，都调用 GET /hosts
  4. 刷新成功后原子替换 hosts.json 和 hosts.meta.json，然后执行 jq
  5. 刷新失败返回 refresh_failed，data_path=null，不使用旧数据冒充最新
  6. 如果旧 snapshot 仍 fresh，可物理保留供后续非强刷查询使用
```

刷新成功时：

- 将 DBAAS 响应写入临时 data 文件
- 做轻量 JSON 结构检查，不做严格 schema 校验
- 统计记录数和文件大小
- 写入临时 meta 文件
- 使用 `os.replace` 原子发布 `hosts.json`
- 使用 `os.replace` 原子发布 `hosts.meta.json`

刷新失败时：

- 如果是 `refresh=true`，返回 `refresh_failed`
- 如果是默认 lazy refresh，返回 `snapshot_unavailable`
- 不基于 stale 旧数据视图回答
- `refresh=true` 失败时不预先删除旧 snapshot
- 如果旧 snapshot 仍 fresh，可以保留旧 data 文件，并在 meta 中记录 `last_refresh_status=error`、`last_error`
- 如果旧 snapshot 已 stale/missing，写入 `status=error`、`data_path=null` 的 meta
- tool 本次响应不暴露 stale data path

并发规则：

- Host 只有 admin scope
- 同一进程内应使用 host 数据视图刷新锁
- 多个并发 host 查询命中 stale/missing 数据视图时，只允许一个请求实际调用 DBAAS
- 后台刷新和 tool 刷新共用同一个 host refresh lock
- 其他查询等待同一个刷新结果，最多等待 `host_refresh_lock_timeout_seconds`
- jq 查询本身不加锁，读取当前正式发布的 `hosts.json`

## 7. Tool 设计

### 7.1 query_dbaas_host_data_tool

Tool 名称：

```text
query_dbaas_host_data_tool
```

职责：

- 按管理员身份读取或刷新 hosts 数据视图
- 对 `hosts.json` 执行受控 jq 查询
- 返回查询结果 preview、匹配数量、截断信息和 hosts meta 摘要

参数建议：

```json
{
  "jq_filter": ".[] | select(.status == \"enabled\") | {name, ip, cpuAvailableCores}",
  "max_preview_items": 20,
  "refresh": false
}
```

参数说明：

- `jq_filter`
  - 必填
  - 对 hosts 数组执行的 jq filter
  - 不接受文件路径
  - 不允许模型传入身份参数
- `max_preview_items`
  - 可选
  - 限制 preview item 数量
- `refresh`
  - 可选，默认 `false`
  - 用户明确要求“最新、当前、刷新、现在”时传 `true`

返回结构应沿用 services/backup query tool 的风格：

```json
{
  "status": "ok",
  "kind": "hosts",
  "scope": "admin",
  "record_count": 2880,
  "matched_count": 12,
  "preview": [],
  "truncated": false,
  "meta": {
    "synced_at": "2026-06-10T12:00:00+08:00",
    "expires_at": "2026-06-10T12:00:30+08:00",
    "schema_version": "hosts.v1",
    "source_endpoint": "/hosts"
  }
}
```

错误结构示例：

```json
{
  "status": "snapshot_unavailable",
  "kind": "hosts",
  "scope": "admin",
  "message": "主机数据视图不可用，DBAAS 主机列表刷新失败。",
  "last_error": "GET /hosts timed out"
}
```

### 7.2 describe_dbaas_schema_tool

首版不新增 `describe_host_schema_tool`。

应扩展现有 schema 描述工具，使其支持：

```text
describe_dbaas_schema_tool(kind="hosts")
```

该工具应返回当前可见 hosts 完整 schema 内容。
模型在生成复杂 jq 前，应先查看 hosts schema，避免猜字段名。

## 8. DBAAS 接口

首版只对接：

```text
GET /hosts
```

请求身份：

```text
Authorization: Bearer admin
X-DBAAS-Actor-User: {identity.user_id 或 dbaas-ai-agent}
X-DBAAS-Actor-Role: admin
```

响应：

```text
Host Record 数组
```

首版不对接：

```text
GET /hosts/{host_id}
```

后续如果需要查询主机上的完整单元列表，再单独扩展 Host Detail 能力。

## 9. 权限规则

Host tool 是管理员工具。

规则：

- admin agent 注册 `query_dbaas_host_data_tool`
- user agent 不注册 `query_dbaas_host_data_tool`
- 普通用户不能通过 host tool 查询主机资产
- 不做普通用户 host 数据视图
- 不从普通用户服务数据视图反查主机资产

模型行为：

- 如果普通用户询问主机列表、主机 IP、主机容量或主机健康，模型应说明当前身份无权查询平台主机资产
- 不应尝试使用 services user 数据视图推断隐藏主机信息

Prompt 边界：

- 通用 `backend/prompts/system.md` 不写 host tool 名、hosts schema kind 调用形式或主机查询参数示例
- 主机查询说明只写入 `backend/prompts/admin_extend_system_prompt.md`
- `backend/prompts/user_extend_system_prompt.md` 只描述普通用户无权查询主机资产，不暴露 `query_dbaas_host_data_tool` 或 `describe_dbaas_schema_tool(kind="hosts")`
- 这样可以避免普通用户 agent 虽未注册 host tool，却因通用 prompt 暴露工具名而尝试无效工具调用

## 10. 模型行为规则

模型应该：

- 需要查询主机事实时调用 `query_dbaas_host_data_tool`
- 复杂字段或不确定字段名前先调用 schema 描述工具获取完整 schema
- 用户要求“最新、当前、现在、刷新”时传 `refresh=true`
- 基于 `cpuAvailableCores`、`memoryAvailableGB`、`unitCount`、`maxUnitCount` 判断资源余量
- 区分 allocated 和实时 usage：
  - `cpuAllocatedCores` 是已分配核数
  - `memoryAllocatedGB` 是已分配内存
  - 不代表实时 CPU/内存监控
- 使用 `status` 判断管控状态
- 使用 `healthStatus` 判断健康风险
- 使用 `networkPartition` 判断 HA 分区风险
- 使用 `hdd` / `ssd` 是否为 `null` 判断主机存储介质

模型不应该：

- 猜测不存在的 host 字段
- 使用 stale 数据视图回答
- 把 `memoryCapacityGB` 当 byte、MB 或实时使用率
- 把 `cpuAllocatedCores` 当实时 CPU 使用率
- 对普通用户暴露主机资产信息
- 基于 Host 查询结果直接执行写操作

## 11. 测试计划

首版测试建议：

- schema 测试
  - `hosts.v1.schema.json` 可完整加载并通过 schema tool 返回
  - `hdd` / `ssd` 允许对象或 `null`
  - `id`、`siteId`、`areaId`、`clusterId` 使用字符串类型，不限制具体格式
- sync 测试
  - 后台 admin hosts sync task 会启动并刷新
  - missing 数据视图时 lazy refresh 成功
  - stale 数据视图时 lazy refresh 成功
  - `refresh=true` 强制刷新
  - `refresh=true` 失败时不返回 stale data path
  - `refresh=true` 失败但旧 fresh snapshot 仍可供后续非强刷查询使用
  - 并发查询只触发一次 DBAAS 请求
- query 测试
  - jq 查询 enabled 主机
  - jq 查询 CPU 可用最多的主机
  - jq 查询 `unitCount >= maxUnitCount * 0.8` 的主机
  - jq 查询 SSD 主机
  - jq 查询异常健康主机
- 权限测试
  - admin agent 注册 host tool
  - user agent 不注册 host tool
  - 普通用户不能通过 tool 查询 hosts
- factory 测试
  - admin tool set 包含 `query_dbaas_host_data_tool`
  - user tool set 不包含该 tool
  - `describe_dbaas_schema_tool(kind="hosts")` 支持 hosts schema
  - user system prompt 不包含 admin-only host tool 名或 hosts schema 调用形式
  - admin system prompt 包含 host tool 使用规则

## 12. 后续扩展

后续可以单独推进：

- `/hosts/{host_id}` 主机详情查询
- 主机上的 units 明细查询
- 主机 precheck
- 主机维护状态切换
- 主机入库、出库、启用、停用
- 主机资源迁移建议
- Host 和 Services 的公共 jq runner / 数据视图基础抽象

首版不提前实现这些扩展，避免把 admin-only 主机查询做重。
