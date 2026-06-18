# AI-Agent 调用 DBAAS / Mock Server API 开发文档

## 1. 文档范围

本文档描述 ai-agent 当前实际调用的 DBAAS 控制面接口，以及本地 `../mock-server` 中对应的兼容实现。

只记录 ai-agent 会直接调用的接口：

- 服务视图查询
- 主机资产查询
- 集群资产查询
- 网段资产查询
- 备份列表查询
- 监控最新值和历史值查询
- 资源 / 存储变更预检
- 资源 / 存储变更执行
- 镜像升级候选查询和任务创建
- 手动备份能力查询和任务创建
- DBAAS 异步任务查询

ai-agent 当前未调用的 mock-server 其他接口不放入本文档。

## 2. Base URL 与本地启动

ai-agent 通过配置项连接 DBAAS：

```text
dbaas_server_base_url = "http://127.0.0.1:9000"
```

本地 mock-server 推荐启动方式：

```bash
PORT=9000 ../mock-server/start.sh
```

本地联调结束后：

```bash
./stop.sh
```

## 3. 身份与权限 Header

ai-agent 调用 DBAAS 时统一注入身份 header。前端请求体、AI tool 参数和模型输出不得覆盖 DBAAS 调用身份。

### 3.1 管理员身份

```http
Authorization: Bearer admin
X-DBAAS-Actor-User: ops_zhang
X-DBAAS-Actor-Role: admin
```

管理员可以访问全部资源。

### 3.2 普通用户身份

```http
Authorization: Bearer user
X-DBAAS-Actor-User: payment_team
X-DBAAS-Actor-Role: user
```

普通用户只能访问 DBAAS 侧授权给该 actor user 的资源。

### 3.3 后台系统同步身份

后台系统任务会使用：

```http
Authorization: Bearer admin
X-DBAAS-Actor-User: dbaas-ai-agent
X-DBAAS-Actor-Role: system
```

当前用于管理员全量服务视图和主机资产等后台刷新。

## 4. ai-agent 调用接口清单

| 方法 | 路径 | ai-agent 用途 |
| --- | --- | --- |
| `GET` | `/services` | 刷新当前身份可见服务快照 |
| `GET` | `/services/{service_name}` | 获取单个服务详情，补充审批当前值 |
| `GET` | `/hosts` | 管理员主机资产快照 |
| `GET` | `/clusters` | 管理员集群资产快照 |
| `GET` | `/network-segments` | 管理员网段资产快照 |
| `GET` | `/backups` | 当前身份可见备份列表快照 |
| `GET` | `/metrics/latest?metric_key=...` | 获取某监控项最新值 |
| `GET` | `/units/{unit_name}/metrics/history?metric_key=...&start_ts=...&end_ts=...` | 获取单元历史监控 |
| `POST` | `/api/v1/prechecks/service-resource-update` | CPU / 内存调整前预检 |
| `POST` | `/api/v1/prechecks/service-storage-update` | data / log 容量调整前预检 |
| `PUT` | `/services/{service_name}/resource` | 执行 CPU / 内存调整 |
| `PUT` | `/services/{service_name}/storage` | 执行 data / log 容量调整 |
| `GET` | `/image-upgrade-capabilities?serviceName=...&childServiceType=...` | 查询镜像升级候选 |
| `POST` | `/services/{service_name}/image-upgrade` | 创建镜像升级异步任务 |
| `GET` | `/backup-task-capabilities?...` | 查询手动备份发起能力 |
| `POST` | `/services/{service_name}/backup` | 创建手动备份异步任务 |
| `GET` | `/tasks/{task_id}` | 刷新 DBAAS 异步任务状态 |

## 5. 通用响应与错误约定

### 5.1 JSON 对象或数组

ai-agent 对不同接口的响应形态有明确预期：

- 快照类列表接口返回 JSON 数组。
- 写操作、预检、能力查询和任务查询返回 JSON 对象。

如果写操作响应不是 JSON 对象，ai-agent 会报：

```text
dbaas_invalid_response
```

### 5.2 HTTP 错误

DBAAS 返回 `4xx` / `5xx` 时，ai-agent 会将错误封装为 DBAAS 请求失败。

错误内容优先读取响应 JSON 中的：

- `detail`
- `message`

否则使用响应文本或 HTTP reason phrase。

### 5.3 超时

DBAAS 在 `dbaas_request_timeout_seconds` 内未响应时，ai-agent 会报：

```text
dbaas_timeout
```

### 5.4 权限错误

mock-server 当前使用 `Authorization` 和 `X-DBAAS-Actor-*` 共同判断身份。

常见错误：

- `401`：缺少或无法识别身份
- `403`：身份存在，但无权访问目标资源
- `404`：目标资源不存在，或普通用户不可见

## 6. 服务视图接口

### 6.1 查询服务列表

```http
GET /services
```

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user` | string | 否 | mock-server 支持按服务 owner 精确过滤；ai-agent 当前主要依赖身份 header 控制可见范围 |

用途：

- 管理员后台刷新全量 services 快照。
- 普通用户活跃时刷新该用户可见 services 快照。
- Agent 查询服务状态、规格、单元、站点、备份策略等信息时基于本地快照执行 jq。

管理员返回为 `services.admin.v1` 结构，普通用户返回为 `services.user.v1` 安全字段投影。

最小示例：

```json
[
  {
    "name": "mysql-xf2",
    "type": "mysql",
    "user": "payment_team",
    "siteId": "site-001",
    "siteName": "上海一区",
    "runningStatus": "warning",
    "replicationStatus": "passing",
    "childServices": [
      {
        "name": "mysql",
        "type": "mysql",
        "version": "8.0.37",
        "port": 3306,
        "runningStatus": "warning",
        "units": [
          {
            "name": "mysql-primary-01",
            "type": "mysql",
            "cpuArchitecture": "amd64",
            "cpuArchitectureDisplayName": "X86",
            "version": "8.0.37.1",
            "runningStatus": "passing",
            "hostName": "host-001",
            "hostIp": "10.0.0.1",
            "ip": "172.16.0.10",
            "cpu": 4,
            "memoryGB": 16,
            "storage": {
              "data": {
                "sizeGB": 300,
                "type": "local:SSD"
              },
              "log": {
                "sizeGB": 100,
                "type": "local:SSD"
              }
            }
          }
        ]
      }
    ],
    "backupStrategy": {
      "enabled": true,
      "type": "full",
      "cronExpression": "0 0 2 * * *",
      "retention": 7,
      "compressMode": "gzip",
      "sendAlarm": true
    }
  }
]
```

关键字段：

| 字段 | 说明 |
| --- | --- |
| `name` | 服务组名称，ai-agent 后续写操作的 `service_name` |
| `type` | 服务组类型，例如 mysql、redis、mongodb |
| `user` | 服务所属用户或租户 |
| `siteId` / `siteName` | 站点信息；普通用户 schema 可能不包含 `siteId` |
| `runningStatus` | `passing` / `warning` / `critical` |
| `childServices[].type` | 子服务类型，写操作使用 `childServiceType` |
| `childServices[].units[].name` | 单元名称，监控历史、备份 unit scope 使用 |
| `cpu` | CPU 核数 |
| `memoryGB` | 内存，单位 GB |
| `storage.data.sizeGB` | data 卷容量，单位 GB |
| `storage.log.sizeGB` | log 卷容量，单位 GB |

### 6.2 查询单个服务详情

```http
GET /services/{service_name}
```

用途：

- 写操作审批前补充当前值。
- 操作执行前或执行后获取服务详情。

路径参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `service_name` | string | 服务组名称，例如 `mysql-xf2` |

返回结构与 `/services` 单条元素基本一致。

示例：

```json
{
  "name": "mysql-xf2",
  "type": "mysql",
  "user": "payment_team",
  "siteName": "上海一区",
  "runningStatus": "passing",
  "childServices": []
}
```

## 7. 主机资产接口

### 7.1 查询主机列表

```http
GET /hosts
```

用途：

- 管理员后台刷新 hosts 快照。
- 管理员 Agent 查询主机容量、分配率、健康状态、机房机位等信息。

权限：

- 仅管理员 / system 使用。
- 普通用户查询主机资产时 ai-agent 会直接返回权限不足，不应调用 DBAAS。

返回示例：

```json
[
  {
    "id": "host-001",
    "name": "db-host-001",
    "ip": "10.0.0.1",
    "sshPort": 22,
    "siteId": "site-001",
    "siteName": "上海一区",
    "clusterId": "cluster-001",
    "clusterName": "cluster-a",
    "clusterEnabled": true,
    "areaId": "area-001",
    "areaName": "上海",
    "room": "A101",
    "seat": "R01-U01",
    "networkPartition": "ha-a",
    "status": "enabled",
    "healthStatus": "HEALTHY",
    "cpuArchitecture": "amd64",
    "cpuArchitectureName": "X86",
    "cpuCapacityCores": 64,
    "cpuAllocatedCores": 32,
    "cpuAvailableCores": 32,
    "cpuAllocationPercent": 50,
    "memoryCapacityGB": 256,
    "memoryAllocatedGB": 128,
    "memoryAvailableGB": 128,
    "memoryAllocationPercent": 50,
    "hdd": null,
    "ssd": {
      "device": "/dev/nvme0n1",
      "capacityGB": 4096,
      "usedGB": 2048,
      "availableGB": 2048,
      "usagePercent": 50
    },
    "sanName": null,
    "maxUnitCount": 20,
    "maxUsagePercent": 80,
    "unitCount": 8,
    "createdAt": "2026-04-22 12:00:00",
    "creator": "admin",
    "creatorName": "管理员"
  }
]
```

## 8. 集群资产接口

### 8.1 查询集群列表

```http
GET /clusters
```

用途：

- 管理员 Agent 查询集群启用状态、支持的 CPU 架构、支持的软件类型和支持的网络。
- ai-agent 按查询 lazy refresh clusters 快照。

权限：

- 仅管理员使用。
- 普通用户查询集群资产时 ai-agent 会直接返回权限不足，不应调用 DBAAS。

返回示例：

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

约定：

- 返回结构应与 ai-agent `clusters.v1` schema 保持一致。
- `id`、`siteId`、`areaId` 使用字符串。
- `supportedSoftwareTypes` 使用 DBAAS 服务类型，例如 `mysql`、`redis`、`mongodb`。
- `supportedNetworkNames` 只表示集群支持的网络名称，网络 IP 范围、VLAN 和容量由后续网络接口提供。

## 9. 网段资产接口

### 9.1 查询网段列表

```http
GET /network-segments
```

用途：

- 管理员 Agent 查询网段启用状态、IPv4/IPv6 地址范围、网关、VLAN、地址使用率和所属集群。
- ai-agent 按查询 lazy refresh networkSegments 快照。

权限：

- 仅管理员使用。
- 普通用户查询网段资产时 ai-agent 会直接返回权限不足，不应调用 DBAAS。

返回示例：

```json
[
  {
    "id": "71001",
    "name": "LEAF-10.24.16",
    "description": "核心数据库网段",
    "siteId": "12",
    "siteName": "南京一区",
    "clusterId": "3101",
    "clusterName": "NJ-MYSQL-CLUSTER-01",
    "startIpv4": "10.24.16.11",
    "endIpv4": "10.24.16.240",
    "gatewayIpv4": "10.24.16.254",
    "ipv4MaskLength": 24,
    "ipv4TotalCount": 230,
    "ipv4UsedCount": 86,
    "ipv4UsagePercent": 37.4,
    "startIpv6": "2405:db8:2000:1010::b",
    "endIpv6": "2405:db8:2000:1010::f0",
    "gatewayIpv6": "2405:db8:2000:1010::1",
    "ipv6MaskLength": 64,
    "ipv6TotalCount": 230,
    "ipv6UsedCount": 24,
    "ipv6UsagePercent": 10.4,
    "vlanId": 2416,
    "enabled": true,
    "createdAt": "2026-05-18 10:23:00",
    "createdBy": "ops_admin",
    "createdByName": "运维管理员"
  }
]
```

约定：

- 返回结构应与 ai-agent `networkSegments.v1` schema 保持一致。
- `id`、`siteId`、`clusterId` 使用字符串。
- `ipv4MaskLength`、`ipv6MaskLength`、`ipv4TotalCount`、`ipv4UsedCount`、`ipv6TotalCount`、`ipv6UsedCount`、`vlanId` 使用数字。
- `ipv4UsagePercent`、`ipv6UsagePercent` 使用 0-100 的数字。
- 返回记录不包含生产原始字段名。

## 10. 备份列表接口

### 10.1 查询备份列表

```http
GET /backups
```

用途：

- 刷新当前身份可见 backups 快照。
- Agent 查询最近备份、备份有效性、过期时间、备份任务状态等。

权限：

- 管理员可见全部备份。
- 普通用户只可见自己服务产生的备份。

返回示例：

```json
[
  {
    "backup_id": "backup-001",
    "task_id": "task-backup-001",
    "siteId": "site-001",
    "siteName": "上海一区",
    "service_name": "mysql-xf2",
    "service_type": "mysql",
    "child_service_name": "mysql",
    "child_service_type": "mysql",
    "unit_name": "mysql-primary-01",
    "backup_type": "full",
    "size_bytes": 1073741824,
    "storage_type": "NAS",
    "compress_mode": "gzip",
    "started_at": "2026-04-22 02:00:00",
    "finished_at": "2026-04-22 02:10:00",
    "expires_at": "2026-04-29 02:10:00",
    "duration_seconds": 600,
    "task_status": "succeeded",
    "task_error": null,
    "valid_status": "valid",
    "remark": "daily backup"
  }
]
```

关键字段：

| 字段 | 说明 |
| --- | --- |
| `backup_id` | 备份文件唯一 ID |
| `task_id` | 产生该备份的 DBAAS 任务 ID |
| `service_name` | 服务组名称 |
| `unit_name` | 备份所属单元 |
| `backup_type` | `full`、`incremental`、`ddl`、`snapshot`、`rdb`、`table`、`encrypt` |
| `size_bytes` | 备份大小，单位 byte |
| `task_status` | 备份任务状态 |
| `valid_status` | 备份有效性状态 |

## 11. 监控接口

### 11.1 查询最新监控

```http
GET /metrics/latest?metric_key={metric_key}
```

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `metric_key` | string | 是 | 监控项 key，必须存在于 `config/dbaas_metric_catalog.json` |
| `service_name` | string | 否 | mock-server 支持；ai-agent 当前 latest snapshot 只传 `metric_key` |

用途：

- Agent 查询当前 CPU、内存、容量、QPS 等最新指标。
- ai-agent 会按 `metric_key` 缓存当前身份可见 latest snapshot。

返回示例：

```json
[
  {
    "service_name": "mysql-xf2",
    "unit_name": "mysql-primary-01",
    "service_type": "mysql",
    "value": 63.5
  }
]
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `service_name` | 单元所属服务组 |
| `unit_name` | 单元名称 |
| `service_type` | 服务类型 |
| `value` | 监控值；类型由 metric catalog 的 `value_type` 决定 |

### 11.2 查询单元历史监控

```http
GET /units/{unit_name}/metrics/history?metric_key={metric_key}&start_ts={start_ts}&end_ts={end_ts}
```

路径参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `unit_name` | string | 单元名称，例如 `mysql-primary-01` |

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `metric_key` | string | 是 | 监控项 key |
| `start_ts` | integer | 是 | Unix timestamp 秒数，必须小于 `end_ts` |
| `end_ts` | integer | 是 | Unix timestamp 秒数，不得晚于当前时间 |

用途：

- Agent 分析单个 unit 最近一段时间指标走势。
- precheck 可辅助判断资源调整风险。

返回示例：

```json
[
  {
    "ts": 1776830400,
    "value": 52.1
  },
  {
    "ts": 1776830460,
    "value": 57.8
  }
]
```

## 12. 资源调整预检与执行

### 12.1 CPU / 内存调整预检

```http
POST /api/v1/prechecks/service-resource-update
Content-Type: application/json
```

ai-agent 请求：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "target_cpu_cores": 8,
  "target_memory_gb": 16
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `service_name` | string | 是 | 服务组名称 |
| `child_service_type` | string | 是 | 子服务类型 |
| `target_cpu_cores` | number | 否 | 目标 CPU 核数 |
| `target_memory_gb` | number | 否 | 目标内存，单位 GB |

响应必需字段：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "current_spec": {
    "cpu_cores": 4,
    "memory_gb": 8
  },
  "available_specs": [
    {
      "label": "8C16G",
      "cpu_cores": 8,
      "memory_gb": 16
    }
  ],
  "runtime": {
    "unit_count": 2,
    "running_count": 2,
    "abnormal_units": []
  },
  "metrics": {
    "time_window": "30m",
    "units": [
      {
        "unit_name": "mysql-primary-01",
        "cpu": {
          "latest": "52%",
          "max": "70%",
          "min": "20%",
          "avg": "45%"
        },
        "memory": {
          "latest": "62%",
          "max": "80%",
          "min": "40%",
          "avg": "58%"
        }
      }
    ],
    "missing_metric_units": []
  },
  "blocking_errors": []
}
```

ai-agent 会校验响应必须包含：

- `service_name`
- `child_service_type`
- `current_spec`
- `available_specs`
- `runtime`
- `metrics`
- `blocking_errors`

`blocking_errors[]` 每项必须包含字符串 `code` 和 `message`。

如果 `blocking_errors` 不为空，Agent 应先向用户说明阻断原因，不应直接发起写操作。

### 12.2 执行 CPU / 内存调整

```http
PUT /services/{service_name}/resource
Content-Type: application/json
```

ai-agent 请求：

```json
{
  "childServiceType": "mysql",
  "platformAuto": false,
  "cpu": 8,
  "memoryGB": 16
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `childServiceType` | string | 是 | 目标子服务类型 |
| `platformAuto` | boolean | 否 | 是否由平台自动分配规格 |
| `cpu` | number | 否 | CPU 核数 |
| `memoryGB` | number | 否 | 内存，单位 GB |

mock-server 要求至少传入 `platformAuto`、`cpu`、`memoryGB` 之一。

返回：更新后的服务详情对象。

## 13. 存储调整预检与执行

### 13.1 data / log 容量调整预检

```http
POST /api/v1/prechecks/service-storage-update
Content-Type: application/json
```

ai-agent 请求：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "target_data_volume_gb": 1024,
  "target_log_volume_gb": 200
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `service_name` | string | 是 | 服务组名称 |
| `child_service_type` | string | 是 | 子服务类型 |
| `target_data_volume_gb` | number | 否 | 目标 data 卷容量，单位 GB |
| `target_log_volume_gb` | number | 否 | 目标 log 卷容量，单位 GB |

响应必需字段：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "current_storage": {
    "data_volume_gb": 300,
    "log_volume_gb": 100
  },
  "runtime": {
    "unit_count": 2,
    "running_count": 2,
    "abnormal_units": []
  },
  "metrics": {
    "units": [
      {
        "unit_name": "mysql-primary-01",
        "data_usage": "61%",
        "log_usage": "42%"
      }
    ],
    "missing_metric_units": []
  },
  "blocking_errors": []
}
```

ai-agent 会校验响应必须包含：

- `service_name`
- `child_service_type`
- `current_storage`
- `runtime`
- `metrics`
- `blocking_errors`

### 13.2 执行 data / log 容量调整

```http
PUT /services/{service_name}/storage
Content-Type: application/json
```

ai-agent 请求：

```json
{
  "childServiceType": "mysql",
  "platformAuto": false,
  "storage": {
    "data": {
      "sizeGB": 1024
    },
    "log": {
      "sizeGB": 200
    }
  }
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `childServiceType` | string | 是 | 目标子服务类型 |
| `platformAuto` | boolean | 否 | 是否由平台自动分配 |
| `storage.data.sizeGB` | number | 否 | data 卷目标容量，单位 GB |
| `storage.log.sizeGB` | number | 否 | log 卷目标容量，单位 GB |

mock-server 要求至少传入 `platformAuto` 或 `storage`。

返回：更新后的服务详情对象。

## 14. 镜像升级接口

### 14.1 查询镜像升级候选

```http
GET /image-upgrade-capabilities?serviceName={service_name}&childServiceType={child_service_type}
```

用途：

- 当用户没有明确指定 `image` / `version` 时，Agent 必须先查询候选项，并让用户选择。
- Agent 不应自行猜测或默认选择升级版本。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `serviceName` | string | 是 | 服务组名称 |
| `childServiceType` | string | 是 | 子服务类型 |

返回：

```json
{
  "supported": true,
  "availableTargets": [
    {
      "image": "mysql:8.0.37",
      "version": "8.0.37"
    }
  ]
}
```

### 14.2 创建镜像升级任务

```http
POST /services/{service_name}/image-upgrade
Content-Type: application/json
```

ai-agent 请求：

```json
{
  "childServiceType": "mysql",
  "image": "mysql:8.0.37",
  "version": "8.0.37",
  "unitNames": ["mysql-primary-01", "mysql-replica-01"]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `childServiceType` | string | 是 | 目标子服务类型 |
| `image` | string | 是 | 目标镜像 |
| `version` | string | 否 | 目标版本 |
| `unitNames` | string[] | 否 | 指定升级单元；不传表示升级该子服务下所有单元 |

返回：

```json
{
  "taskId": "task-service-image-upgrade-mysql-xf2-mysql-a3f9c2"
}
```

ai-agent 会将该 `taskId` 记录为当前 Session 下的 `TaskRecord`，前端通过 ai-agent 的任务接口查看和订阅。

## 15. 手动备份接口

### 15.1 查询备份发起能力

```http
GET /backup-task-capabilities?serviceType={service_type}&serviceName={service_name}&unitName={unit_name}
```

ai-agent 会按已知信息传入其中一个或多个 query 参数。mock-server 要求至少提供一个目标参数。

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `serviceType` | string | 否 | 服务类型 |
| `serviceName` | string | 否 | 服务组名称 |
| `unitName` | string | 否 | 单元名称 |

返回：

```json
{
  "supported": true,
  "serviceType": "mysql",
  "scopeValues": ["service", "unit"],
  "fields": [
    {
      "name": "backupType",
      "type": "string",
      "required": true,
      "enumValues": ["full", "incremental"],
      "description": "备份类型",
      "requiresUserInput": true
    },
    {
      "name": "retentionDays",
      "type": "integer",
      "required": true,
      "min": 1,
      "max": 30,
      "description": "备份保留天数",
      "requiresUserInput": true
    }
  ],
  "resolvedTarget": {
    "serviceName": "mysql-xf2",
    "unitName": "mysql-primary-01"
  },
  "runtimeHints": {
    "backupRunning": false,
    "runningBackups": []
  }
}
```

用途：

- Agent 发起手动备份前确认服务是否支持备份。
- Agent 判断必填参数和枚举值。
- `runtimeHints.backupRunning` 只作为提示，不作为 precheck 阻断。

### 15.2 创建手动备份任务

```http
POST /services/{service_name}/backup
Content-Type: application/json
```

ai-agent 请求：

```json
{
  "scope": "unit",
  "backupType": "full",
  "retentionDays": 7,
  "unitName": "mysql-primary-01",
  "options": {
    "compressMode": "gzip"
  },
  "remark": "用户通过 AI Agent 发起"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope` | string | 是 | 备份范围，例如 `service` 或 `unit` |
| `backupType` | string | 是 | 备份类型，例如 `full` |
| `retentionDays` | integer | 是 | 保留天数 |
| `unitName` | string | unit scope 时需要 | 目标单元 |
| `options` | object | 否 | 服务类别相关参数，例如压缩模式 |
| `remark` | string | 否 | 备注 |

返回：

```json
{
  "taskId": "task-backup-001"
}
```

## 16. DBAAS 异步任务接口

### 16.1 查询任务详情

```http
GET /tasks/{task_id}
```

用途：

- ai-agent 对当前 Session 记录的非终态任务做 lazy refresh。
- 前端不会直接调用 DBAAS `/tasks/{task_id}`，而是调用 ai-agent 的 `/api/v1/sessions/{session_id}/tasks` 和 `/tasks/events`。

路径参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `task_id` | string | DBAAS 创建任务时返回的 `taskId` |

返回：

```json
{
  "taskId": "task-service-image-upgrade-mysql-xf2-mysql-a3f9c2",
  "type": "service.image.upgrade",
  "status": "RUNNING",
  "message": "任务正在执行。",
  "reason": null,
  "resourceType": "service",
  "resourceName": "mysql-xf2",
  "result": null,
  "createdAt": "2026-04-22T12:10:10Z",
  "updatedAt": "2026-04-22T12:10:20Z"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `taskId` | DBAAS 任务 ID |
| `type` | 任务类型，例如 `service.image.upgrade`、`service.backup.create` |
| `status` | DBAAS 原始状态，例如 `RUNNING`、`SUCCESS`、`FAILED` |
| `message` | 状态说明 |
| `reason` | 失败原因 |
| `resourceType` / `resourceName` | 操作资源 |
| `result` | 成功后的业务结果 |
| `createdAt` / `updatedAt` | UTC ISO8601 时间 |

ai-agent 会将 DBAAS 原始状态折叠为前端任务状态：

| DBAAS 状态示例 | ai-agent `TaskRecord.status` |
| --- | --- |
| `CREATED` / `RUNNING` | `running` |
| `SUCCESS` / `SUCCEEDED` | `succeeded` |
| `FAILED` / `TIMED_OUT` | `failed` |
| `CANCELED` / `CANCELLED` | `canceled` |
| 其他未知值 | `unknown` |

## 17. ai-agent 数据缓存与刷新语义

ai-agent 不会每次 Agent 问答都直接打 DBAAS 全量接口，而是维护本地快照和 TTL。

当前快照类型：

| 数据 | DBAAS 接口 | 本地 schema |
| --- | --- | --- |
| 服务列表 | `GET /services` | `services.admin.v1` / `services.user.v1` |
| 主机资产 | `GET /hosts` | `hosts.v1` |
| 集群资产 | `GET /clusters` | `clusters.v1` |
| 网段资产 | `GET /network-segments` | `networkSegments.v1` |
| 备份列表 | `GET /backups` | `backups.v1` |
| 最新监控 | `GET /metrics/latest` | metric catalog 决定字段含义 |
| 历史监控 | `GET /units/{unit_name}/metrics/history` | metric catalog 决定字段含义 |

刷新方式：

- 管理员 services 和 hosts 由后台任务周期刷新。
- 普通用户 services 在用户会话活跃时续约，并由后台任务刷新。
- clusters、networkSegments、backups 和 metrics 按查询时 lazy refresh。
- task 状态由 ai-agent 当前 Session 任务接口 lazy refresh。

DBAAS 正式实现需要保证：

- 相同身份下可见资源稳定。
- 普通用户不能越权查询服务、单元、备份、任务。
- 写操作和任务查询可以通过 `taskId` 串联。
- 时间、容量、CPU、内存单位保持明确。
