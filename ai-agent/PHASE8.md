# DBAAS 智能助手第八阶段：轻量 Precheck Tool

## 0. 当前状态

- 状态：已实现 v1 工具主路径
- 当前代码状态：资源规格和存储规格两个 precheck tools 已注册到 DBAAS tool 集合，并通过 DBAAS precheck HTTP 接口获取只读事实
- 本文档作用：说明写操作前轻量只读 precheck 的边界、工具参数、DBAAS 接口和模型行为规则
- 仍有效内容：precheck 不调用写工具、不创建 approval、不替代 Phase7 确认卡；用户明确继续执行后才回到 Phase7 写工具链路
- 后续关注：统一 precheck 平台、前端 precheck 卡片、precheck 持久化和更多操作类型的 precheck 当前不做

## 1. Phase8 v1 结论

第一版支持两个轻量 precheck tools：

```text
precheck_service_resource_update_tool
precheck_service_storage_update_tool
```

核心结论：

- 不做统一 precheck 平台。
- 一个高价值操作对应一个专用 precheck tool。
- 没有目标规格或目标容量时，不做主机、资源池或存储池容量校验。
- 有目标规格或目标容量时，DBAAS 需要校验资源是否足够；不足放入 `blocking_errors`。
- CPU / 内存 `metrics` 只返回 unit 级摘要，不返回 service summary，不返回原始时间序列。
- 存储 `metrics` 只返回每个 unit 当前最新 data/log 使用率。
- 真正执行仍走 Phase7 写工具和确认卡链路。

Phase8 v1 的目标是：扩容或扩磁盘前，先从 DBAAS 获取当前规格、当前容量、运行状态和必要监控摘要，让大模型基于事实给出建议和风险说明。

## 2. 边界

所有 precheck tool 都是只读事实查询工具。

通用边界：

- 不调用 DBAAS 写接口
- 不调用 Phase7 写工具
- 不创建 approval
- 不替代写工具或 DBAAS 控制面的硬校验
- 不直接决定最终是否执行

资源规格 precheck 可以查询：

- 当前 CPU / 内存规格
- DBAAS 支持选择的资源套餐
- unit 运行状态
- unit 级 CPU / 内存使用率摘要
- 用户指定目标规格时的资源是否足够

存储规格 precheck 可以查询：

- 当前 data / log 卷容量
- unit 运行状态
- unit 级 data / log 当前使用率
- 用户指定目标容量时的存储资源是否足够

后续其他操作如果需要 precheck，也按同样模式新增专用 precheck tool，不提前设计通用大接口。

## 3. Tool 说明

### 3.1 资源规格调整

Tool 名称：

```text
precheck_service_resource_update_tool
```

当前 tool 描述要点：

```text
只读工具，用于获取服务 CPU/内存资源调整前的预检事实。

当用户询问服务是否需要扩容/缩容，或用户已指定 CPU、内存目标规格并希望执行前查看风险时，可以调用本工具。

本工具返回当前规格、DBAAS 支持选择的资源套餐、unit 运行状态、unit 级 CPU/内存使用率摘要和 blocking_errors。

本工具不执行写操作，不创建 approval。
```

参数说明：

- `service_name`：服务名。
- `child_service_type`：子服务类型。
- `target_cpu_cores`：可选，用户指定的目标 CPU。
- `target_memory_gb`：可选，用户指定的目标内存。

### 3.2 存储规格调整

Tool 名称：

```text
precheck_service_storage_update_tool
```

当前 tool 描述要点：

```text
只读工具，用于获取服务 data/log 卷容量调整前的预检事实。

当用户询问服务 data/log 卷是否需要扩容，或用户已指定目标容量并希望执行前查看风险时，可以调用本工具。

本工具返回当前 data/log 卷容量、unit 运行状态、unit 级 data/log 当前使用率和 blocking_errors。

本工具不执行写操作，不创建 approval。
```

参数说明：

- `service_name`：服务名。
- `child_service_type`：子服务类型。
- `target_data_volume_gb`：可选，用户指定的目标 data 卷容量。
- `target_log_volume_gb`：可选，用户指定的目标 log 卷容量。

## 4. DBAAS 接口

### 4.1 资源规格 Precheck

接口：

```text
POST /api/v1/prechecks/service-resource-update
```

请求示例：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "target_cpu_cores": 8,
  "target_memory_gb": 16
}
```

返回示例：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "current_spec": {
    "cpu_cores": 2,
    "memory_gb": 4
  },
  "available_specs": [
    {
      "cpu_cores": 4,
      "memory_gb": 8,
      "label": "4C8G"
    },
    {
      "cpu_cores": 8,
      "memory_gb": 16,
      "label": "8C16G"
    }
  ],
  "runtime": {
    "unit_count": 3,
    "running_count": 3,
    "abnormal_units": [
      {
        "unit_name": "mysql-2",
        "status": "stopped"
      }
    ]
  },
  "metrics": {
    "time_window": "1d",
    "units": [
      {
        "unit_name": "mysql-0",
        "cpu": {
          "latest": "82.5%",
          "max": "96.8%",
          "min": "21.3%",
          "avg": "67.4%"
        },
        "memory": {
          "latest": "71.2%",
          "max": "84.6%",
          "min": "48.9%",
          "avg": "63.1%"
        }
      }
    ],
    "missing_metric_units": []
  },
  "blocking_errors": []
}
```

字段约定：

- `available_specs`：DBAAS 支持选择的资源规格套餐；不表示已做主机或资源池容量校验。
- `metrics.time_window`：`max` / `min` / `avg` 的统计窗口，第一版固定为 `"1d"`。
- `metrics.units`：每个 unit 的 CPU / 内存使用率摘要，值使用百分比字符串。

### 4.2 存储规格 Precheck

接口：

```text
POST /api/v1/prechecks/service-storage-update
```

请求示例：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "target_data_volume_gb": 1024,
  "target_log_volume_gb": 200
}
```

返回示例：

```json
{
  "service_name": "mysql-xf2",
  "child_service_type": "mysql",
  "current_storage": {
    "data_volume_gb": 500,
    "log_volume_gb": 100
  },
  "runtime": {
    "unit_count": 3,
    "running_count": 3,
    "abnormal_units": []
  },
  "metrics": {
    "units": [
      {
        "unit_name": "mysql-0",
        "data_usage": "78.5%",
        "log_usage": "42.1%"
      }
    ],
    "missing_metric_units": []
  },
  "blocking_errors": []
}
```

字段约定：

- `current_storage`：当前 data / log 卷容量。
- `metrics.units`：每个 unit 当前最新 data / log 使用率。
- 存储 precheck 第一版不返回 `time_window`，不返回趋势，不返回 max / min / avg。

### 4.3 通用字段约定

- `runtime`：unit 数量、running 数量和异常 unit 摘要；`abnormal_units` 使用 `{unit_name, status}`。
- `metrics.missing_metric_units`：缺失整组监控数据的 unit；第一版不展开单个指标字段缺失。
- `blocking_errors`：DBAAS 成功完成 precheck 后返回的业务阻断错误，固定使用 `{code, message}`；例如目标资源不足。
- DBAAS HTTP 错误、权限错误、连接失败或响应格式异常不放入 `blocking_errors`；ai-agent tool 会返回 `status=error`，并带上 `error_type`、`message` 和可选 `status_code`。

`blocking_errors` 非空示例：

```json
{
  "blocking_errors": [
    {
      "code": "insufficient_capacity",
      "message": "当前主机、资源池或存储池资源不足，无法调整到目标值。"
    }
  ]
}
```

mock-server 联调时，为了方便稳定触发 `blocking_errors`，当前固定使用以下阈值：

- CPU 目标值大于 `100C` 时返回资源不足；测试可填 `101C`。
- 内存目标值大于 `300G` 时返回资源不足；测试可填 `301G`。
- data/log 存储目标值大于 `2000G` 时返回资源不足；测试可填 `2001G`。

等于阈值本身不触发，例如 `100C`、`300G`、`2000G` 仍按普通目标值处理。

## 5. 模型行为规则

系统提示词只需要补充通用 precheck policy 和当前工具清单：

```text
对于已提供 precheck tool 的 DBAAS 操作，执行前应先调用对应 precheck tool 获取只读事实，再基于结果给出建议或风险说明。

如果用户明确要求执行资源或存储调整，先带目标参数调用对应 precheck tool；precheck 成功时，用户看完建议或风险后仍明确要求继续，才调用现有受控写工具；precheck 返回 `status=error` 时，说明当前缺少执行依据和失败原因，不要编造建议，只有用户知情后仍明确要求执行，才可以回到现有受控写工具链路。

如果 precheck 返回可选项，推荐规格或目标值应优先从可选项中选择。blocking_errors 非空时，不建议继续执行，应说明原因。

当前可用 precheck tool：
- 资源规格调整：precheck_service_resource_update_tool
- 存储规格调整：precheck_service_storage_update_tool
```

具体资源判断交给大模型基于字段语义自行完成，不在系统提示词里写复杂运维规则。

## 6. 第一版不做

Phase8 v1 明确不做：

- 不做统一 precheck 平台
- 不做通用 action DSL
- 不做规则引擎
- 不做前端 precheck 卡片
- 不持久化 precheck 记录
- 不新增建议审计表
- 不修改 Phase7 审批和执行链路
- 不覆盖所有 DBAAS 写操作
- 不返回 service 级 metrics summary
- 不返回原始监控时间序列
- 不要求 DBAAS 返回告警历史

## 7. 验收要点

- 用户问“要不要扩容/扩磁盘”时，Agent 先调用对应 precheck tool，不直接触发 approval。
- 用户明确给出目标规格或目标容量时，Agent 先说明当前事实和风险，不直接触发 approval。
- 用户采纳模型推荐值并要求执行时，Agent 再带目标参数调用一次对应 precheck tool。
- 用户明确说“继续执行”后，才调用 Phase7 写工具生成确认卡。
- 资源 precheck 返回的 `metrics` 只包含 unit 级 CPU / 内存摘要。
- 存储 precheck 返回的 `metrics` 只包含 unit 级 data / log 当前使用率。
- precheck 返回可选项时，推荐规格或目标值优先来自可选项。
- 用户指定目标值但主机、资源池或存储池资源不足时，DBAAS 通过 `blocking_errors` 返回。
- `blocking_errors` 非空时，模型说明错误原因，不建议继续执行。
- precheck 查询失败时，模型说明缺少依据；如果用户仍明确要求执行，仍可回到 Phase7 写工具链路。

一句话总结：

```text
Precheck Tool 是写操作前的轻量只读事实查询工具；Phase8 v1 先服务资源规格调整和存储规格调整，后续其他工具按同样方式专用、简单、按需扩展。
```
