# DBAAS 智能助手第十阶段：备份发起

## 0. 当前状态

- 状态：Phase10 进入设计落定阶段，首版先实现备份发起主链路
- 本文档作用：定义 DBAAS 备份发起的接口边界、参数结构、Agent tools、确认流程和后续扩展
- 与 Phase9 的关系：Phase9 负责备份查询；Phase10 负责发起备份任务
- 核心边界：Phase10 首版做 capability 查询和备份任务发起，不做 backup precheck、还原、删除或备份策略修改

### 首版范围

- 查询 DBAAS 备份发起能力，获取当前目标支持的备份类型、参数字段和枚举
- 支持按服务或单元发起备份
- 通过 DBAAS 统一接口创建备份异步任务
- 接入 Phase7 approval、operation 和 task 机制
- DBAAS 发起接口统一只返回一个 `taskId`
- 服务级备份可能产生多条 backup record，后续通过 `task_id` 关联查询

### 首版不实现

- backup precheck
- 前端确认卡内参数选择或编辑
- 独立前端参数选择卡
- 查询单个 `backup_id` 详情
- 指定备份还原
- 备份删除
- 修改备份策略
- 备份任务完成后自动刷新 backups snapshot

## 1. 核心结论

备份发起是 DBAAS 写操作，必须走 Phase7 的人工确认和异步 task 记录链路。

Agent 不直接根据服务类型猜测备份参数。
不同服务类别对应的备份参数差异由 DBAAS capability 接口返回。
首版约定：同一个服务类别的备份参数一致，不按具体服务实例、备份范围或已选择参数动态变化。

首版使用三个概念：

```text
capability：这个服务类别支持哪些备份参数
create：真正发起备份任务
query：任务完成后按 task_id 查询 Phase9 backups snapshot
```

首版不做 backup precheck。
如果 DBAAS 判断当前不允许发起备份，例如已有冲突任务、服务状态不允许或参数非法，应在创建接口中返回明确错误。

## 1.1 命名规范

Phase10 沿用前面阶段的命名风格。

- DBAAS HTTP query、request body 和 response 字段名使用 camelCase
- Agent tool 入参、本地 operation/task 字段和 Phase9 backups 快照字段使用 snake_case
- 枚举值和状态值使用 lower snake_case

示例：

```text
HTTP 字段：backupType、retentionDays、unitName、taskId
Tool 入参：backup_type、retention_days、unit_name、task_id
枚举值：service、unit、full、running、succeeded
```

## 2. DBAAS 接口

### 2.1 查询备份发起能力

接口：

```text
GET /backup-task-capabilities
```

首版 ai-agent 只要求 DBAAS 返回适合模型和前端理解的字段描述。
capability 只描述服务类别级别的参数能力，不描述当前是否适合执行。

capability 可以返回轻量 `runtimeHints`，用于提示当前目标是否已有备份任务正在执行。
`runtimeHints` 不等同于 precheck，不用于强制阻断发起备份。
如果 capability 请求只提供 `serviceType`，没有具体服务或单元目标，DBAAS 可以不返回 `runtimeHints`，或返回 `backupRunning=false`。
如果 capability 请求提供了具体目标，`runtimeHints` 应只描述该目标相关的 running backups，不做同服务类别的全局扫描。

不应在 capability 中返回完整 precheck 信息，例如：

- 当前服务健康状态是否适合备份
- 当前是否命中维护窗口或冲突任务
- 存储空间或备份额度检查结果

这些属于 backup precheck，后续阶段再实现。

请求参数使用 query string。
首版支持以下查询方式：

```text
GET /backup-task-capabilities?serviceType=mysql
GET /backup-task-capabilities?serviceName=mysql-xf2
GET /backup-task-capabilities?unitName=mysql-primary-01
```

字段含义：

- `serviceType`
  - 服务类别，例如 `mysql`、`redis`、`tidb`
  - 首版 capability 推荐使用该字段
- `serviceName`
  - 服务组名称
  - 当调用方没有 `serviceType` 时可传
  - DBAAS 应返回解析后的 `serviceType`
- `unitName`
  - 单元名称
  - 当调用方以单元为目标且没有 `serviceType` 时可传
  - DBAAS 平台内 `unitName` 全局唯一
  - DBAAS 应返回解析后的 `serviceName`、`serviceType` 和所属子服务上下文

上述字段至少提供一个。
如果同时提供多个字段，DBAAS 应校验它们是否指向同一目标或同一服务类别。

响应示例：

```json
{
  "capabilityVersion": "service.backup.create.mysql.v1",
  "serviceType": "mysql",
  "supported": true,
  "resolvedTarget": {
    "serviceName": "mysql-xf2",
    "scope": "service",
    "unitName": null
  },
  "backupTypes": [
    {
      "value": "full",
      "label": "全量备份"
    },
    {
      "value": "incremental",
      "label": "增量备份"
    }
  ],
  "fields": [
    {
      "name": "backupType",
      "label": "备份类型",
      "type": "select",
      "required": true,
      "requiresUserInput": true,
      "options": [
        {
          "value": "full",
          "label": "全量备份"
        },
        {
          "value": "incremental",
          "label": "增量备份"
        }
      ]
    },
    {
      "name": "retentionDays",
      "label": "保留天数",
      "type": "number",
      "required": true,
      "requiresUserInput": true,
      "min": 1,
      "max": 365
    },
    {
      "name": "options.compressMode",
      "label": "压缩模式",
      "type": "select",
      "required": false,
      "requiresUserInput": true,
      "options": [
        {
          "value": "none",
          "label": "不压缩"
        },
        {
          "value": "gzip",
          "label": "gzip"
        },
        {
          "value": "zstd",
          "label": "zstd"
        }
      ]
    }
  ],
  "runtimeHints": {
    "backupRunning": true,
    "runningBackups": [
      {
        "taskId": "task-001",
        "scope": "service",
        "serviceName": "mysql-xf2",
        "childServiceName": null,
        "childServiceType": null,
        "unitName": null,
        "backupType": "full",
        "startedAt": "2026-06-04 10:00:00",
        "taskStatus": "running"
      }
    ],
    "message": "当前服务已有备份任务正在执行。"
  }
}
```

字段规则：

- `fields` 使用通用表单字段描述结构，方便模型和前端理解
- `name` 可以使用点号表示嵌套字段，例如 `options.compressMode`
- `required=true` 的字段必须由用户明确输入或在对话中明确确认后才能发起备份
- `requiresUserInput=true` 表示模型必须追问用户显式输入或确认该字段，不能自行选择枚举值
- capability 不返回 `default`；枚举值只是可选项，不表示默认选择
- `backupTypes` 是备份类型摘要，必须与 `fields` 中 `name=backupType` 的 `options` 保持一致
- `resolvedTarget` 用于说明 DBAAS 根据 `serviceName` 或 `unitName` 解析出的目标上下文
- 如果请求只提供 `serviceType`，没有具体服务或单元目标，`resolvedTarget` 可以为 `null`
- `runtimeHints` 是轻量运行提示，不等同于 precheck，不用于强制阻断发起备份
- capability 不返回 `suggestedArgs`，也不返回默认发起参数
- `supported=false` 时，必须返回原因字段，例如 `message`

### 2.2 发起备份任务

接口：

```text
POST /services/{serviceName}/backup
```

请求体：

```json
{
  "scope": "service",
  "backupType": "full",
  "retentionDays": 7,
  "unitName": null,
  "options": {
    "compressMode": "gzip"
  },
  "remark": "手动备份"
}
```

响应体：

```json
{
  "taskId": "task-xxx"
}
```

约定：

- 备份发起使用服务业务接口，不新增 backup 专属 task 创建接口
- DBAAS 发起接口统一只返回一个 `taskId`
- `taskId` 表示本次备份异步任务 ID
- 单个 `taskId` 可能产生一条或多条 backup record
- 备份任务完成后，Phase9 `/backups` 返回的 backup record 使用 `task_id` 关联该任务
- 备份任务刚创建时，DBAAS 即可在 `/backups` 中返回对应 backup records，初始 `task_status=running`
- 任务结束后，DBAAS 将对应 backup records 更新为 `task_status=succeeded` 或 `task_status=failed`
- `retentionDays` 表示保留天数
- `expires_at` 不作为发起备份入参，由 DBAAS 在备份记录中根据任务完成时间和保留天数计算
- ai-agent 本地重复执行保护复用 Phase7 `operation_conflict_key` 机制，不为备份发起单独设计冲突判断
- `operation_conflict_key` 的目标应体现 `scope` 和实际目标，例如 service 或 unit
- `scope=unit` 时，DBAAS 必须校验 `unitName` 属于 path 中的 `serviceName`

如果 DBAAS 拒绝创建任务，应返回明确错误。
首版 ai-agent 不做备份冲突业务判断。
已有备份任务、冲突任务、服务状态不允许、备份窗口限制或参数非法等，都由 DBAAS `POST /services/{serviceName}/backup` 返回错误控制。

DBAAS 拒绝创建时，ai-agent 应将 operation 记录为 `failed`，不创建 task record，并根据 DBAAS 错误信息向用户说明失败原因。

示例：

```json
{
  "errorType": "backup_task_not_allowed",
  "message": "服务 mysql-xf2 已有备份任务 task-001 正在运行。",
  "details": {
    "taskId": "task-001",
    "reason": "backup_task_running"
  }
}
```

## 3. 备份范围

### 3.1 scope=service

按整个服务组发起备份。

请求示例：

```text
POST /services/mysql-xf2/backup
```

```json
{
  "scope": "service",
  "backupType": "full",
  "retentionDays": 7,
  "options": {},
  "remark": "手动备份"
}
```

语义：

- DBAAS 按服务组发起备份任务
- 如果服务存在多分片、多子服务或多个 unit，DBAAS 仍只返回一个 `taskId`
- 任务完成后可能生成多条 backup record

### 3.2 scope=unit

按指定单元发起备份。

请求示例：

```text
POST /services/mysql-xf2/backup
```

```json
{
  "scope": "unit",
  "unitName": "mysql-primary-01",
  "backupType": "full",
  "retentionDays": 7,
  "options": {},
  "remark": "手动备份"
}
```

语义：

- `unitName` 必填
- DBAAS 平台内 `unit_name` 全局唯一
- 首版不传 `unitId`
- 首版不支持一次指定多个 unit

## 4. Agent Tools

### 4.1 describe_service_backup_capability_tool

Tool 签名：

```text
describe_service_backup_capability_tool(
    service_type: str | None = None,
    service_name: str | None = None,
    unit_name: str | None = None
)
```

职责：

- 调用 DBAAS `/backup-task-capabilities`
- 获取当前服务类别可用备份类型、参数字段和枚举
- 帮助模型判断用户请求是否缺少必要参数
- 帮助模型避免猜测 mysql、redis、tidb 等不同服务类型的差异参数

不负责：

- 判断当前是否适合发起备份
- 查询冲突任务
- 创建 approval
- 发起备份任务

模型调用规则：

- 用户请求发起备份时，调用写工具前应先调用本工具
- 如果当前上下文已经知道服务类别，优先传 `service_type`
- 如果当前上下文只有服务名，可以传 `service_name`，由 DBAAS 解析服务类别
- 如果用户按单元发起备份且当前上下文只有单元名，可以传 `unit_name`，由 DBAAS 解析服务类别和所属服务
- `service_type`、`service_name`、`unit_name` 至少提供一个
- 用户没有明确 `scope`、`backup_type`、`retention_days` 或服务类型差异参数时，应先通过 capability 确认可选项，然后通过对话追问用户补齐
- 首版 capability 只按服务类别区分参数，不按 `scope`、`unit_name` 或已选择参数继续细分
- 首版不提供前端参数选择卡；如果 capability 显示缺少必填字段，模型应通过对话追问用户补齐
- capability 返回的枚举值或范围只能用于追问用户，不能作为默认值直接触发确认卡
- 如果 capability 返回 `runtimeHints.backupRunning=true`，模型应向用户提示当前已有备份正在执行，但不应把该提示当成强阻断
- capability 返回 `supported=false` 时，不应调用发起备份工具

### 4.2 create_service_backup_task_tool

Tool 签名：

```text
create_service_backup_task_tool(
    service_name: str,
    scope: str,
    backup_type: str,
    retention_days: int,
    unit_name: str | None = None,
    options: dict | None = None,
    remark: str | None = None
)
```

职责：

- 发起 DBAAS 备份异步任务
- 接入 Phase7 approval、operation 和 task 机制
- 审批通过后调用 DBAAS `POST /services/{serviceName}/backup`
- 只读取 DBAAS 返回的 `taskId`
- 在当前 Session 中创建 task record

不负责：

- backup precheck
- 等待任务完成
- 查询备份产物列表
- 自动刷新 backups snapshot
- 还原或删除备份

执行模式：

```text
async
```

建议 action：

```text
service.backup.create
```

建议风险级别：

```text
medium
```

建议 required role：

```text
user
```

普通有权限用户可以发起备份。
具体资源权限仍由 DBAAS 控制面按当前身份校验。

如果备份发起会明显影响服务性能，后续可提升为 `high`。

## 5. 确认卡

备份发起是写操作，必须先经过 Phase7 人工确认。

确认卡不是参数选择入口。
首版确认卡只展示最终参数，不提供选择、编辑或补参能力。
首版也不提供独立前端参数选择卡。

参数收集通过对话完成。
如果 capability 返回必填字段且用户没有提供，模型应先追问用户，不应等待确认卡补齐参数。

确认卡应展示：

```text
操作：发起备份
范围：整个服务 / 指定单元
服务：mysql-xf2
单元：mysql-primary-01
备份类型：full
保留天数：7 天
参数：compressMode=gzip
备注：手动备份
```

如果 `scope=service`，确认卡应提示：

```text
该任务可能产生多份备份文件，最终备份文件以任务完成后的备份列表为准。
```

AI 不应手写自然语言确认表来替代受控写工具调用。
只有触发 `create_service_backup_task_tool` 后由系统生成的确认卡才是有效审批入口。

## 6. task 与 backup record 关系

DBAAS `POST /services/{serviceName}/backup` 返回：

```json
{
  "taskId": "task-xxx"
}
```

任务刚创建后，Phase9 `/backups` 可以返回正在备份中的 records：

```json
[
  {
    "backup_id": "backup-001",
    "task_id": "task-xxx",
    "service_name": "mysql-xf2",
    "child_service_name": "mysql-primary",
    "child_service_type": "mysql",
    "unit_name": "mysql-primary-01",
    "backup_type": "full",
    "finished_at": null,
    "expires_at": null,
    "task_status": "running",
    "task_error": null,
    "valid_status": "unchecked"
  }
]
```

任务完成后，Phase9 `/backups` 中同一 `task_id` 的 records 更新为终态：

```json
[
  {
    "backup_id": "backup-001",
    "task_id": "task-xxx",
    "service_name": "mysql-xf2",
    "child_service_name": "mysql-primary",
    "child_service_type": "mysql",
    "unit_name": "mysql-primary-01",
    "backup_type": "full",
    "expires_at": "2026-06-11 10:00:00",
    "task_status": "succeeded",
    "valid_status": "valid"
  },
  {
    "backup_id": "backup-002",
    "task_id": "task-xxx",
    "service_name": "mysql-xf2",
    "child_service_name": "mysql-replica",
    "child_service_type": "mysql",
    "unit_name": "mysql-replica-01",
    "backup_type": "full",
    "expires_at": "2026-06-11 10:00:00",
    "task_status": "succeeded",
    "valid_status": "valid"
  }
]
```

模型回答规则：

- 发起成功时，只能说明已创建备份任务
- 不应在任务刚创建后编造 `backup_id`
- 如果 `/backups` 已返回同 `task_id` 的 running backup records，可以说明备份文件记录已创建但任务仍在执行
- 用户询问备份产物时，应按 `task_id` 查询 backups snapshot
- 如果用户要求最新备份产物，应使用 Phase9 backups query 的 `refresh=true`
- 查询不到时，应说明“当前可见备份快照中尚未看到该任务产生的备份文件”

## 7. 首版参数规则

固定公共参数：

```text
service_name
scope
backup_type
retention_days
unit_name
options
remark
```

### 7.1 retention_days

`retention_days` 表示备份保留天数。

规则：

- 必须是正整数
- 允许范围由 DBAAS capability 返回
- 不传绝对过期时间
- `expires_at` 由 DBAAS 在 backup record 中生成

### 7.2 options

`options` 用于承载不同服务类型、备份类型和范围的差异参数。

规则：

- Agent 不自行猜测 `options`
- `options` 的可用字段来自 DBAAS capability 的 `fields`
- `options` 中的字段名应与 DBAAS capability 返回一致
- DBAAS 是最终参数校验权威

示例：

```json
{
  "options": {
    "compressMode": "gzip",
    "storageType": "NAS"
  }
}
```

## 8. backup precheck 后续扩展

首版不实现 backup precheck。

后续可新增专用接口：

```text
POST /api/v1/prechecks/service-backup-create
```

对应 tool：

```text
precheck_service_backup_create_tool(...)
```

precheck 职责：

- 检查目标服务或 unit 是否存在
- 检查当前用户是否有权限
- 检查备份类型和参数是否合法
- 检查是否已有冲突备份任务
- 检查服务健康状态是否适合备份
- 检查是否命中维护窗口、升级、扩容或其他冲突任务
- 检查存储空间或备份额度是否足够

precheck 不应创建 approval，不应发起写操作。
通过 precheck 后仍需调用 `create_service_backup_task_tool` 进入 Phase7 确认卡。

## 9. Mock Server 要求

mock-server 首版至少支持：

```text
GET  /backup-task-capabilities
POST /services/{serviceName}/backup
GET  /tasks/{task_id}
```

要求：

- `/backup-task-capabilities` 按 `serviceType` 返回不同服务类别的参数字段
- 如果请求使用 `serviceName`，mock-server 应解析并返回对应 `serviceType`
- 如果请求使用 `unitName`，mock-server 应解析并返回对应 `serviceName`、`childServiceName`、`childServiceType` 和 `serviceType`
- `/backup-task-capabilities` 可以返回 `runtimeHints.backupRunning` 和 `runningBackups`，用于提示当前目标已有备份执行中
- `POST /services/{serviceName}/backup` 返回单个 `taskId`
- `POST /services/{serviceName}/backup` 成功后立即生成对应 backup records，初始 `task_status=running`
- 服务级备份可模拟一个 task 产生多条 backup record
- 单元级备份只产生指定 `unitName` 对应 backup record
- Phase10 复用 Phase9 已有 `GET /backups` 查询接口，不新增备份查询接口
- Phase9 `/backups` 中新增记录应使用同一个 `task_id` 关联创建任务
- mock 任务应在 1-2 分钟后进入终态，并将对应 backup records 更新为 `task_status=succeeded` 或 `task_status=failed`
- mock 终态延迟应支持配置，自动化测试可缩短到数秒
- 失败时 backup records 应写入 `task_error`
- `/tasks/{task_id}` 支持任务状态查询

## 10. 后续阶段

可后续推进：

- `precheck_service_backup_create_tool`
- 可选前端参数选择卡
- 可选确认卡参数编辑
- 指定备份还原
- 备份删除
- 备份策略查询或修改
- 备份任务完成后自动刷新 backups snapshot
- 按 `task_id` 自动汇总本次备份产物
