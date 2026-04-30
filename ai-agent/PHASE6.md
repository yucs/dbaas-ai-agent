# DBAAS 智能助手第六阶段设计讨论

## 1. 当前阶段目标

第六阶段实现 DBAAS 单元监控查询能力。

本阶段范围包括：

- 用 catalog 维护 DBAAS 监控项元数据
- 让大模型先根据用户问题定位具体监控项
- 让大模型根据监控项类型、单位、枚举值等信息生成正确 jq
- 避免大模型猜测监控项名称、值类型或异常状态含义
- 支持 latest 监控查询
- 支持指定真实单元的 history 监控查询
- 支持管理员和普通用户，权限过滤由 DBAAS 监控接口按身份完成
- 使用本地短 TTL 快照、meta 文件、per-snapshot 刷新锁和后台 cleanup

## 2. 核心结论

单元监控数据建议以监控项为维度查询。

例如用户问：

```text
哪些单元内存使用率超过 60%？
```

模型不应直接猜测字段名或值类型，
而应先通过 catalog 找到对应监控项：

```text
container.mem.usagePercent
```

并确认该监控项是数字型、单位是 `%`、支持大小比较，
然后再基于对应监控项数据生成 jq。

监控项 catalog 是生成 jq 的依据，
不应把完整 catalog 写入 system prompt 或 tool 描述。
后续应由专门工具按关键词、服务类型或监控项 key 搜索 catalog，
只把相关监控项元数据返回给大模型。

## 3. 代码目录结构

Phase6 第一版不重构现有 `query.py` 的 services 查询逻辑。
监控查询使用独立 metric 查询链路，
降低对 Phase5 已有服务查询链路的影响。

建议新增：

```text
backend/src/dbass_ai_agent/dbaas/
  metric_catalog.py     # catalog 加载、搜索、打分、精简返回
  metric_workspace.py   # metric 快照路径、身份 scope、meta/data 路径
  metric_sync.py        # 按 snapshot key 刷新 latest 快照、TTL 判断、per-snapshot 内存锁
  metric_history.py     # 指定单元历史缓存下载、参数校验、meta/data 写入
  metric_query.py       # metric latest/history 的 jq 查询入口和共享 jq runner
  metric_cleanup.py     # 后台定期清理 latest/history 监控快照
  tools.py              # 注册 metric catalog/query tools
```

其中 `metric_query.py` 第一版可以独立实现 jq 处理，
并被 latest metric 查询和 history metric 查询复用。
后续如果 services 和 metric 的 jq 处理稳定一致，
再考虑抽取公共 `dbaas/jq.py`。

更细的职责划分：

```text
metric_catalog.py
  - 加载 backend/config/dbaas_metric_catalog.json
  - 校验 metric_key 字符集
  - catalog 搜索、大小写不敏感匹配、打分排序
  - 返回 compact catalog entries

metric_workspace.py
  - 管理管理员 metrics_latest/{metric_key}.json 和 meta 路径
  - 管理普通用户 metrics_latest/user__{safe_user}__{metric_key}.json 和 meta 路径
  - 管理 metrics_history/{scope}__{safe_user}__{safe_unit_name}__{metric_key}__{start_ts}__{end_ts}.json 和 meta 路径
  - 校验 metric_key
  - 生成 safe_user、safe_unit_name 和 history scope key
  - 复用 workspace.py 中的 tmp 写入和 os.replace 原子替换能力

metric_sync.py
  - latest metric 快照 fresh 判断
  - per-snapshot 进程内内存锁
  - 调用 GET /metrics/latest?metric_key=...
  - 写临时 data/meta 并 os.replace 发布
  - 过期刷新失败时删除对应 data/meta

metric_history.py
  - 校验 unit_name、metric_key、start_ts、end_ts
  - 调用 GET /units/{unit_name}/metrics/history?...
  - 写 metrics_history cache 和 meta
  - 命中未过期历史缓存时复用

metric_query.py
  - query_unit_metric_data 业务函数
  - query_unit_metric_history 业务函数
  - latest/history 共享 metric jq runner
  - 处理 preview、truncated、byte limit 和 jq 错误结构

metric_cleanup.py
  - 单协程 background cleanup loop
  - 周期性清理 metrics_latest/
  - 周期性清理 metrics_history/
  - 清理失败只记录日志，不影响查询

tools.py
  - 注册 describe_unit_metric_catalog_tool
  - 注册 query_unit_metric_data_tool
  - 注册 query_unit_metric_history_tool
  - 注册 get_current_time_tool，或后续拆到通用 tools 模块
```

后端启动和关闭接入：

- 在 FastAPI lifespan/startup 中加载 metric catalog
- 在 FastAPI lifespan/startup 中启动 metric cleanup background task
- 在 shutdown 中取消 cleanup task，并等待其退出
- 不在 route handler 或 tool 调用时临时启动 cleanup

Phase6 需要在 `config.toml` 的 `[dbaas_workspace]` 下新增 metric 配置：

```toml
[dbaas_workspace]
# metric latest/history 快照多久算新鲜，单位秒。
metric_snapshot_ttl_seconds = 30

# metric cleanup 后台任务扫描间隔，单位秒。
metric_snapshot_cleanup_interval_seconds = 600

# 同一个 snapshot key 刷新锁最多等待多久，单位秒。
metric_refresh_lock_timeout_seconds = 10
```

metric query 第一版复用 services 已有 jq 配置：

- `jq_timeout_seconds`
- `jq_max_preview_items`
- `jq_max_output_bytes`

暂不新增 metric 专属 jq 配置。

`metric_refresh_lock_timeout_seconds` 第一版作为独立配置项新增。
它表示等待同一 snapshot key 刷新锁的最长时间，
不同于 DBAAS HTTP 请求超时和 jq 执行超时。

Catalog 加载策略：

- 服务启动时加载一次 `backend/config/dbaas_metric_catalog.json`
- 启动时校验 JSON 格式、必填字段、`metric_key` 字符集、`value_type` 和 enum 字段
- 加载后放入进程内只读缓存
- `describe_unit_metric_catalog_tool` 查询内存缓存
- 修改 catalog 后重启服务生效

## 4. Catalog 存放位置

监控项 catalog 建议作为静态业务配置文件维护：

```text
backend/config/dbaas_metric_catalog.json
```

该文件保存真实监控项元数据。
Metric Catalog 会随着 DBAAS 服务类型和监控项持续扩展，
Phase6 不固定第一版最小指标集合。

第一版暂不单独维护 catalog JSON Schema。
catalog 条目的字段约定先在工具描述、代码类型定义和测试中体现。

后续如果 catalog 规模变大、多人维护，或需要 CI 校验，
再补充独立 schema 文件也不迟。

## 5. Metric Key 命名

监控项需要使用全局唯一的命名空间化 key，
避免不同服务类型之间的同名指标混淆。

示例：

```text
container.cpu.use
container.mem.usagePercent
container.mem.usedBytes
container.mem.limitBytes
instance.mysql.replicationStatus
instance.redis.replicationStatus
instance.mysql.version
```

其中：

- `container.*`
  - 通用容器级监控项，可适用于多种服务类型
- `instance.mysql.*`
  - MySQL 实例级监控项
- `instance.redis.*`
  - Redis 实例级监控项

后续查询 tool 应使用唯一 `metric_key`，
不要只使用 `replicationStatus` 这种可能在不同服务类型中重复的短名称。

`metric_key` 只能包含以下字符：

```text
[a-zA-Z0-9._-]
```

因此监控快照文件名可以直接使用 `metric_key`。
如果 catalog 中出现不符合该规则的 `metric_key`，
应视为配置错误。

## 6. Catalog 字段

第一版 catalog 条目应尽量精简，
只保留模型定位监控项和生成 jq 所需的信息。

必填字段建议为：

```json
{
  "metric_key": "container.mem.usagePercent",
  "display_name": "容器内存使用率",
  "service_types": ["container"],
  "value_type": "number",
  "unit": "%",
  "aliases": ["内存使用率", "内存", "memory", "mem"]
}
```

字段说明：

- `metric_key`
  - 全局唯一监控项 key
- `display_name`
  - 面向用户展示的中文名称
- `service_types`
  - 适用服务类型或监控域，例如 `container`、`host`、`mysql`、`redis`
- `value_type`
  - 监控值类型
- `unit`
  - 单位，例如 `%`、`bytes`、`seconds`，无单位时为 `null`
- `aliases`
  - 用户常用说法，用于 catalog 搜索

可选字段：

- `description`
  - 监控项补充说明
- `enum_values`
  - `value_type=enum` 时的合法枚举值
- `normal_values`
  - `value_type=enum` 时表示正常状态的枚举值
- `abnormal_values`
  - `value_type=enum` 时表示异常状态的枚举值

第一版暂不在每个 catalog 条目中维护：

- `scope`
  - 可从 `metric_key` 前缀或 `service_types` 推断
- `operators`
  - 可由 `value_type` 推导
- `sortable`
  - 可由 `value_type=number` 推导
- `supports_latest`
  - Phase6 第一版默认 catalog 条目可作为 latest 查询的元数据依据
- `supports_history`
  - Phase6 第一版默认 catalog 条目可作为 history 查询的元数据依据

也就是说，第一版不在 catalog 条目中单独维护 `supports_latest` 或 `supports_history`，
避免模型误以为某个指标只能用于其中一种查询。

## 7. Value Type

监控值 `value` 不应假设全是数字。

第一版建议支持：

```text
number
string
enum
boolean
```

其中本阶段重点关注：

- `number`
  - 数字型，支持大小比较、排序、topN
- `string`
  - 普通字符串，适合版本号、文本值等
- `enum`
  - 枚举型，本质上以字符串值返回，但 catalog 中有固定合法值和正常/异常语义

## 8. 监控数据记录字段

按某个监控项查询到的单条监控数据，
第一版可以保持扁平结构：

```json
{
  "service_name": "mysql-xf2",
  "unit_name": "mysql-prod-0",
  "service_type": "mysql",
  "value": 72.5
}
```

其中：

- `service_name`
  - 单元所属服务组名称，用于指定服务过滤和权限可见范围表达
- `unit_name`
  - 单元名称
- `service_type`
  - 单元所属服务类型，例如 `mysql`、`redis`
- `value`
  - 当前监控项的值，类型由 catalog 的 `value_type` 决定

查询指定服务时，
模型应使用记录中的 `service_name` 精确匹配。

例如查询服务 `mysql-xf2` 的单元：

```jq
[.[] | select(.service_name == "mysql-xf2")]
```

不要根据 `unit_name` 前缀猜测服务归属，
也不要做服务名模糊匹配。

同时保留 `service_type`，
用于服务类型过滤、指标消歧，以及和 catalog 中的 `service_types` 对齐。

例如用户问：

```text
mysql 单元 CPU 超过 60% 的有哪些？
```

可生成 jq：

```jq
[.[] | select(.service_type == "mysql" and (.value | type) == "number" and .value > 60)]
```

## 9. 数字型示例

### CPU 使用率

Catalog：

```json
{
  "metric_key": "container.cpu.use",
  "display_name": "容器 CPU 使用率",
  "value_type": "number",
  "unit": "%",
  "aliases": ["cpu", "CPU 使用率", "容器 CPU"]
}
```

监控数据可以是：

```json
[
  {"service_name": "mysql-xf2", "unit_name": "mysql-0", "service_type": "mysql", "value": 72.5},
  {"service_name": "mysql-xf2", "unit_name": "mysql-1", "service_type": "mysql", "value": 48}
]
```

用户问：

```text
哪些单元 CPU 使用率超过 60%？
```

可生成 jq：

```jq
[.[] | select((.value | type) == "number" and .value > 60)]
```

### 内存使用量

非百分比内存指标仍然是数字型，
但单位应明确为 `bytes`。

Catalog：

```json
{
  "metric_key": "container.mem.usedBytes",
  "display_name": "容器内存使用量",
  "value_type": "number",
  "unit": "bytes",
  "aliases": ["内存使用量", "已用内存", "memory used"]
}
```

监控数据可以是：

```json
[
  {"service_name": "mysql-xf2", "unit_name": "mysql-0", "service_type": "mysql", "value": 8589934592},
  {"service_name": "mysql-xf2", "unit_name": "mysql-1", "service_type": "mysql", "value": 4294967296}
]
```

用户问：

```text
内存使用量超过 8GB 的单元有哪些？
```

模型应根据 `unit=bytes` 将 `8GB` 换算为 bytes 后生成 jq：

```jq
[.[] | select((.value | type) == "number" and .value > 8589934592)]
```

## 10. 普通字符串示例

普通字符串适合没有固定枚举集合的文本值，
例如版本号。

Catalog：

```json
{
  "metric_key": "instance.mysql.version",
  "display_name": "MySQL 版本",
  "value_type": "string",
  "unit": null,
  "aliases": ["MySQL 版本", "数据库版本", "version"]
}
```

监控数据可以是：

```json
[
  {"service_name": "mysql-xf2", "unit_name": "mysql-0", "service_type": "mysql", "value": "8.0.36"},
  {"service_name": "mysql-xf2", "unit_name": "mysql-1", "service_type": "mysql", "value": "5.7.44"}
]
```

用户问：

```text
哪些单元是 MySQL 8.0？
```

可生成 jq：

```jq
[.[] | select((.value | type) == "string" and (.value | startswith("8.0")))]
```

## 11. 枚举型示例

枚举型适合固定状态集合，
例如复制状态。

Catalog：

```json
{
  "metric_key": "instance.mysql.replicationStatus",
  "display_name": "MySQL 复制状态",
  "value_type": "enum",
  "unit": null,
  "enum_values": ["passing", "warning", "critical", "unknown"],
  "normal_values": ["passing"],
  "abnormal_values": ["warning", "critical", "unknown"],
  "service_types": ["mysql"],
  "aliases": ["MySQL 复制状态", "MySQL 同步状态", "复制状态"]
}
```

监控数据可以是：

```json
[
  {"service_name": "mysql-xf2", "unit_name": "mysql-0", "service_type": "mysql", "value": "passing"},
  {"service_name": "mysql-xf2", "unit_name": "mysql-1", "service_type": "mysql", "value": "critical"}
]
```

用户问：

```text
哪些 MySQL 单元复制状态异常？
```

模型应根据 `abnormal_values` 生成 jq：

```jq
[.[] | select(["warning", "critical", "unknown"] | index(.value))]
```

用户问：

```text
哪些 MySQL 单元复制状态是 passing？
```

可生成 jq：

```jq
[.[] | select(.value == "passing")]
```

## 12. 大模型使用流程

建议后续新增 catalog 查询工具，
例如：

```text
describe_unit_metric_catalog_tool(query, service_type, limit)
```

第一版参数建议：

```python
describe_unit_metric_catalog_tool(
    query: str,
    service_type: str | None = None,
    limit: int | None = 10,
)
```

其中：

- `query`
  - 必填，表示用户要查询的监控项关键词
  - 例如 `内存使用率`、`CPU`、`复制状态`、`版本`
  - 也可以是完整 `metric_key`
- `service_type`
  - 可选，例如 `container`、`host`、`mysql`、`redis`
  - 如果用户问题明确包含服务类型或监控域，应传入该字段
  - 如果用户问题不明确，可以不传，让 catalog 返回候选
- `limit`
  - 可选，限制返回候选数量，避免返回过多 catalog 条目

`query` 应搜索：

- `metric_key`
- `display_name`
- `aliases`
- `description`
- `enum_values`
- `normal_values`
- `abnormal_values`

英文匹配应大小写不敏感。
实现时可以对 `query`、`metric_key`、`display_name`、`aliases`、`service_type` 等使用 `casefold()` 归一化后匹配，
但返回结果保留 catalog 中的原始写法。

`aliases` 是重要匹配来源，
适合维护中文简称、英文缩写、DBAAS 内部叫法和用户常用说法。

例如：

```json
{
  "metric_key": "container.mem.usagePercent",
  "display_name": "容器内存使用率",
  "aliases": ["内存", "内存使用率", "mem", "memory", "memory usage"]
}
```

用户问：

```text
哪些单元内存超过 60%？
```

模型可以调用：

```json
{
  "query": "内存"
}
```

Catalog 搜索第一版可以使用简单打分规则，
不需要 embedding。

打分规则是 tool 内部用于搜索排序的实现细节，
用于在多个 catalog 条目都匹配 `query` 时，
把更可能符合用户意图的监控项排在前面。
分数不需要返回给大模型。

建议匹配优先级：

1. `metric_key` 精确匹配
2. `display_name` 精确匹配
3. `aliases` 精确匹配
4. `metric_key` 包含 `query`
5. `display_name` 或 `aliases` 包含 `query`
6. `description` 包含 `query`
7. `enum_values`、`normal_values`、`abnormal_values` 命中

如果传入 `service_type`，
应优先返回 `service_types` 包含该值的条目。
不匹配的服务类型可以过滤掉。

对于可能在多个服务类型中重复出现的监控项语义，
例如运行时状态、复制状态、健康状态等，
模型应结合用户问题、当前上下文和 catalog 返回结果判断服务类型。
若可确定服务类型，
应在 catalog 查询或 jq 中使用对应 `service_type` 消歧。
若存在多个候选且无法确定，
模型应先向用户澄清，
不要在多个服务类型候选中随意选择。

使用流程：

1. 用户提出监控查询问题
2. 模型调用 catalog 工具搜索相关监控项
3. catalog 返回匹配的 `metric_key`、`value_type`、`unit`、枚举定义等信息
4. 模型选择唯一监控项
5. 如果存在多个候选且上下文无法确定，模型应向用户澄清服务类型或监控项
6. 模型根据 catalog 元数据生成 jq
7. 模型调用后续监控数据查询工具执行 jq

示例：

用户问：

```text
哪些单元内存使用率超过 60%？
```

catalog 命中：

```json
{
  "metric_key": "container.mem.usagePercent",
  "value_type": "number",
  "unit": "%"
}
```

生成 jq：

```jq
[.[] | select((.value | type) == "number" and .value > 60)]
```

用户问：

```text
哪些单元复制状态异常？
```

如果同时存在：

```text
instance.mysql.replicationStatus
instance.redis.replicationStatus
```

且上下文没有服务类型，
模型不应随意选择其中一个，
而应要求用户明确是 MySQL、Redis 还是其他服务类型。

## 13. 监控数据快照与刷新锁

监控数据查询 tool 可以按当前身份和 `metric_key` 将最新监控值下载到本地短 TTL 快照。

示例文件：

```text
runtime/dbaas_workspace/metrics_latest/{metric_key}.json
runtime/dbaas_workspace/metrics_latest/{metric_key}.meta.json
runtime/dbaas_workspace/metrics_latest/user__{safe_user}__{metric_key}.json
runtime/dbaas_workspace/metrics_latest/user__{safe_user}__{metric_key}.meta.json
```

其中：

- 管理员 latest 快照沿用原始 `{metric_key}.json` 路径
- 普通用户 latest 快照必须加用户 scope 前缀，避免复用管理员全量快照
- `metric_key` 只能包含 `[a-zA-Z0-9._-]`，可以直接用于文件名
- `{safe_user}` 需要做安全文件名转换，只保留 `[a-zA-Z0-9._-]`，其他字符替换为 `_`

safe filename 规则在 latest 和 history 中保持一致：

- 只保留 `[a-zA-Z0-9._-]`
- 其他字符替换为 `_`
- 转换后为空字符串时使用 `unknown`
- 管理员 user 固定使用 `all`

示例：

```text
runtime/dbaas_workspace/metrics_latest/container.cpu.use.json
runtime/dbaas_workspace/metrics_latest/container.cpu.use.meta.json
runtime/dbaas_workspace/metrics_latest/user__payment-platform-team__container.cpu.use.json
runtime/dbaas_workspace/metrics_latest/user__payment-platform-team__container.cpu.use.meta.json
```

第一版监控数据查询工具签名建议为：

```python
query_unit_metric_data_tool(
    metric_key: str,
    jq_filter: str,
    max_preview_items: int | None = None,
)
```

其中：

- `metric_key`
  - 来自 catalog 的唯一监控项 key
- `jq_filter`
  - 根据 catalog 中的 `value_type`、`unit`、枚举定义等生成的 jq
- `max_preview_items`
  - 可选，用于覆盖默认预览条数

第一版不额外传 `service_type`。
如果需要按服务类型过滤，
应在 `jq_filter` 中使用监控数据记录里的 `service_type`。
第一版也不额外传 `service_name`。
如果用户指定服务或单元，
仍通过 `jq_filter` 在当前身份可见的 latest 快照中筛选。

DBAAS 最新监控接口第一版可以约定为：

```text
GET /metrics/latest?metric_key=container.cpu.use
```

tool 调用该接口时必须携带当前用户身份。

- 管理员调用时，DBAAS 返回全量监控数据
- 普通用户调用时，DBAAS 返回该用户所属全部服务的监控数据
- ai-agent 不向 latest 接口传 `service_name`
- ai-agent 不通过 services 快照 join 出用户可见 unit 集合

返回结构：

```json
[
  {"service_name": "mysql-xf2", "unit_name": "mysql-prod-0", "service_type": "mysql", "value": 72.5},
  {"service_name": "redis-cache", "unit_name": "redis-prod-0", "service_type": "redis", "value": 41}
]
```

`value` 的具体类型由 catalog 中的 `value_type` 决定。

Metric meta 建议字段。

管理员 meta 示例：

```json
{
  "metric_key": "container.cpu.use",
  "scope": "admin",
  "user": null,
  "status": "fresh",
  "data_path": ".../metrics_latest/container.cpu.use.json",
  "meta_path": ".../metrics_latest/container.cpu.use.meta.json",
  "synced_at": "2026-04-29T12:00:00Z",
  "expires_at": "2026-04-29T12:00:30Z",
  "ttl_seconds": 30,
  "record_count": 100000,
  "bytes": 8388608,
  "source": "dbaas-server",
  "source_endpoint": "/metrics/latest",
  "last_refresh_status": "success",
  "last_error": null
}
```

普通用户 meta 示例：

```json
{
  "metric_key": "container.cpu.use",
  "scope": "user",
  "user": "payment-platform-team",
  "status": "fresh",
  "data_path": ".../metrics_latest/user__payment-platform-team__container.cpu.use.json",
  "meta_path": ".../metrics_latest/user__payment-platform-team__container.cpu.use.meta.json",
  "synced_at": "2026-04-29T12:00:00Z",
  "expires_at": "2026-04-29T12:00:30Z",
  "ttl_seconds": 30,
  "record_count": 1234,
  "bytes": 1048576,
  "source": "dbaas-server",
  "source_endpoint": "/metrics/latest",
  "last_refresh_status": "success",
  "last_error": null
}
```

Metric query tool 返回结构第一版对齐 services 查询。
tool 不单独保存每次 jq 查询结果，
只保存当前身份可见的原始 latest 快照和 meta。

成功返回示例：

```json
{
  "metric_key": "container.cpu.use",
  "scope": "user",
  "user": "payment-platform-team",
  "status": "success",
  "jq_filter": "[.[] | select((.value | type) == \"number\" and .value > 60)]",
  "preview": [
    {"service_name": "mysql-xf2", "unit_name": "mysql-prod-0", "service_type": "mysql", "value": 72.5}
  ],
  "preview_count": 1,
  "truncated": false,
  "data_path": ".../metrics_latest/user__payment-platform-team__container.cpu.use.json",
  "message": "查询完成，结果来自最新 DBAAS 监控快照。"
}
```

其中 `preview` 不是固定结构，
而是 jq 的结果。

例如用户问：

```text
有多少个容器 CPU 超过 60？
```

模型应生成返回数字的 jq：

```jq
[.[] | select((.value | type) == "number" and .value > 60)] | length
```

tool 返回中的 `preview` 可以是数字：

```json
{
  "status": "success",
  "metric_key": "container.cpu.use",
  "preview": 123,
  "preview_count": 1,
  "truncated": false
}
```

如果用户问：

```text
哪些容器 CPU 超过 60？
```

模型可以生成返回数组的 jq：

```jq
[.[] | select((.value | type) == "number" and .value > 60)]
```

此时 `preview` 是数组预览：

```json
{
  "preview": [
    {"service_name": "mysql-xf2", "unit_name": "mysql-prod-0", "service_type": "mysql", "value": 72.5},
    {"service_name": "redis-cache", "unit_name": "redis-prod-0", "service_type": "redis", "value": 68.1}
  ],
  "preview_count": 2,
  "truncated": true
}
```

因此 `preview` 可以是数字、字符串、对象或数组，
取决于 jq 输出。

错误返回示例：

```json
{
  "status": "error",
  "metric_key": "container.cpu.use",
  "error_type": "snapshot_unavailable",
  "message": "当前没有可用的监控快照，可能拉取 DBAAS 监控数据失败。"
}
```

建议第一版统一使用以下 `error_type`：

- `metric_not_found`
  - catalog 中不存在指定 `metric_key`
- `snapshot_unavailable`
  - 当前没有可用快照，且刷新失败或无法完成
- `permission_denied`
  - 当前身份无权访问目标监控数据或历史单元
- `jq_timeout`
  - jq 查询超过超时时间
- `jq_error`
  - jq 表达式执行失败
- `history_time_range_invalid`
  - history 查询时间范围不合法
- `dbaas_request_failed`
  - 请求 DBAAS 监控接口失败或返回非 2xx

错误映射建议：

- DBAAS 返回 401/403
  - `permission_denied`
- DBAAS 返回 404 且表示 `metric_key` 不存在
  - `metric_not_found`
- DBAAS 返回 404 且表示 `unit_name` 不存在或不可见
  - `permission_denied` 或 `dbaas_request_failed`，第一版可以优先用 `permission_denied` 避免向普通用户暴露资源存在性
- DBAAS 请求超时、连接失败或返回其他非 2xx
  - `dbaas_request_failed`
- 本地快照缺失、过期且刷新失败
  - `snapshot_unavailable`
- history 时间范围不满足 `start_ts < end_ts` 或 `end_ts <= now_ts`
  - `history_time_range_invalid`
- jq 超时
  - `jq_timeout`
- jq 返回非 0
  - `jq_error`

查询流程：

1. 如果本地快照存在且未过期，直接对快照执行 jq，不加锁。
2. 如果快照缺失或过期，由 tool 同步触发刷新。
3. 刷新使用进程内 per-snapshot 内存锁，锁粒度为当前身份对应的 snapshot key。
4. 拿到锁后再次检查快照是否已被其他请求刷新。
5. 如果快照仍不可用，请求 DBAAS 监控接口，写临时文件并原子替换正式快照。
6. 等待同一 snapshot key 刷新的并发请求，在锁释放后重新读取快照并执行 jq。
7. jq 查询本身不加锁，避免慢 jq 阻塞其他读取。

锁是针对 snapshot key 的刷新锁，
不是整个 tool 的全局锁。

例如：

```text
管理员 container.cpu.use 正在刷新时，只会阻塞同一个管理员 snapshot key 的并发刷新。
普通用户 payment-platform-team 的 container.cpu.use 使用独立 snapshot key。
container.mem.usedBytes 和 instance.mysql.replicationStatus 可以同时刷新。
```

因此不同监控项、不同用户 scope 不会互相锁住。
锁的作用只是防止同一个 snapshot key 在同一进程内被并发重复下载。

第一版只保证单进程内同一 snapshot key 的刷新 single-flight。
多进程或多实例部署下可能重复刷新，
但依赖临时文件和 `os.replace` 保证不会读到半截 JSON。
如果后续需要跨进程 single-flight，
再引入文件锁或外部分布式锁。

## 14. 过期刷新失败与锁等待策略

监控快照过期后的刷新失败策略沿用 Phase5 services 快照逻辑。

建议规则：

- metric 快照 fresh 时，直接执行 jq
- metric 快照缺失或过期时，tool 触发刷新
- 刷新成功后，写临时 data/meta 文件并用 `os.replace` 发布正式快照
- 快照过期且刷新失败时，删除当前 snapshot key 对应的 data/meta
- 返回 `error`，说明当前没有可用监控快照，可能拉取 DBAAS 监控数据失败
- 不使用过期旧数据回答当前监控状态

锁等待策略：

- 只有缺失或过期需要刷新时才获取 per-snapshot 内存锁
- 锁粒度为当前身份对应的 snapshot key
- 拿到锁后必须再次检查快照是否已被其他请求刷新
- 其他并发请求等待同一个 snapshot key 的刷新锁释放
- 等锁超时时返回 `error`
- 锁等待超时不应降级使用过期数据

锁等待超时时，
可以返回类似：

```text
监控项正在刷新，等待超时，当前无法获得准确监控数据。
```

相关配置项见第 3 节。

## 15. 大规模 jq 约束

单个监控项可能返回 10w 个单元。
第一版 metric query 的 jq 输出预算复用 services 现有配置。
这不是模型最终回答长度限制，
而是限制单次 jq 原始输出中可能进入 tool 返回、进而进入大模型上下文的数据量。

metric query 第一版复用现有 services jq 配置：

- `jq_timeout_seconds`
  - 单次 jq 查询最多运行多久
- `jq_max_preview_items`
  - 返回给大模型的最大预览条数
- `jq_max_output_bytes`
  - 单次 jq 查询允许 tool 处理的最大字节数

tool 返回必须包含截断标记，
让大模型知道当前结果是否只是预览。

建议至少包含：

- `truncated`
  - 结果条数超过 `max_preview_items`，只返回部分预览
- `byte_truncated`
  - jq 原始输出超过 `jq_max_output_bytes`，输出被字节限制截断

如果 `truncated=true`，
模型应说明结果较多、当前只展示部分预览。

如果 `byte_truncated=true`，
模型应更谨慎地说明结果可能不完整，
并建议用户缩小查询条件、改用 count、topN 或更精确的过滤条件。

如果后续 metric 查询明显比 services 更重，
再考虑增加 metric 专属 jq 配置。

第一版为了保持实现简单，
指定单元、指定服务和条件过滤都仍然通过 jq 扫描当前 `metric_key` 的 latest 快照完成。
即使单个监控项有 10w 条记录，
也先不为指定单元或指定服务增加额外接口、本地索引或分片文件。

如果后续实际运行发现 jq 扫描存在明显性能问题，
再考虑优化：

- DBAAS 提供指定单元 latest endpoint
- DBAAS 提供指定服务 latest endpoint
- 本地按 service_name 或 unit_name 建索引
- 本地快照分片

## 16. 权限策略

Phase6 第一版监控查询支持管理员和普通用户。

所有登录用户都可以调用：

```text
describe_unit_metric_catalog_tool
query_unit_metric_data_tool
query_unit_metric_history_tool
```

权限边界：

- catalog 查询不访问真实监控数据，所有登录用户可用
- latest 查询不暴露 `service_name` 参数
- 管理员 latest 查询调用 `/metrics/latest?metric_key=...`，DBAAS 返回全量监控数据
- 普通用户 latest 查询也调用 `/metrics/latest?metric_key=...`，DBAAS 根据当前身份返回该用户所属全部服务的监控数据
- 指定服务、指定单元、服务类型和阈值等条件过滤，都在当前身份可见快照上通过 jq 完成
- ai-agent 不通过 services 快照 join 出用户可见 unit 集合
- history 查询必须指定真实 `unit_name`，管理员可查任意真实 unit，普通用户只能查自己可见的真实 unit

普通用户监控查询依赖 DBAAS 监控接口根据当前用户身份完成权限过滤。
tool 调用 DBAAS 监控接口时必须携带用户身份，
返回结果只包含该用户可见的单元。

这样 metric 快照记录仍然可以保持简单结构：

```json
{
  "service_name": "mysql-xf2",
  "unit_name": "mysql-prod-0",
  "service_type": "mysql",
  "value": 72.5
}
```

不建议通过 services 快照 join 出用户可见 unit 集合。
原因：

- 第一版监控数据记录只包含 `service_name`、`unit_name`、`service_type`、`value`
- 记录中没有 `user`
- 基于 services 做 join 会增加查询复杂度
- 监控查询要求最新状态，和 services 快照 TTL 的组合会让权限判断更复杂
- 权限过滤属于 DBAAS 监控接口职责，由真实数据源按当前身份裁剪结果更可靠

缓存隔离要求：

- 管理员 latest 快照使用 `{metric_key}.json`
- 普通用户 latest 快照使用 `user__{safe_user}__{metric_key}.json`
- 禁止普通用户复用管理员全量 latest 快照
- history 快照也必须包含身份 scope，避免普通用户命中管理员历史缓存

工具调用身份示例：

```text
admin:
GET /metrics/latest?metric_key=container.cpu.use
Authorization: Bearer admin

user:
GET /metrics/latest?metric_key=container.cpu.use
Authorization: Bearer user:payment-platform-team
```

身份 header 生成规则：

- `identity.role == "admin"` 时使用 `Authorization: Bearer admin`
- `identity.role == "user"` 且 `identity.user` 非空时使用 `Authorization: Bearer user:{identity.user}`
- 普通用户缺少 `identity.user` 时，tool 直接返回 `permission_denied`，不请求 DBAAS
- catalog 查询不请求 DBAAS 监控数据，但仍要求存在当前登录身份上下文

## 17. 后台快照清理任务

监控快照是运行时缓存。
长时间未使用的旧文件由独立后台清理任务处理，
不放在监控 query tool 主链路中。

query tool 只负责：

- 判断快照是否 fresh
- 必要时按当前身份对应的 snapshot key 刷新快照
- 对快照执行 jq
- 返回查询结果

后台清理任务负责：

- 定期扫描 `runtime/dbaas_workspace/metrics_latest/` 和 `runtime/dbaas_workspace/metrics_history/`
- 删除已过期的旧快照，并同时删除 data 和 meta
- 删除 meta 缺失、meta 解析失败或字段不合法的坏快照
- 删除 data 缺失时残留的 meta 文件
- 删除没有对应 meta 的孤儿 data 文件
- 记录清理结果和异常日志

data/meta 必须作为一对文件维护和清理。

建议规则：

- 扫描 `*.meta.json`，如果 meta 中的 `expires_at` 早于当前时间，删除对应 `.json` 和 `.meta.json`
- 如果 `.meta.json` 解析失败、字段缺失或 `data_path` 不合法，删除该 `.meta.json`，并尝试删除同名 data 文件
- 如果 meta 指向的 data 文件不存在，删除该 `.meta.json`
- 扫描普通 `.json` 文件时排除 `*.meta.json`，如果找不到对应 `.meta.json`，删除该孤儿 data 文件

文件配对示例：

```text
data: metrics_latest/user__payment-platform-team__container.cpu.use.json
meta: metrics_latest/user__payment-platform-team__container.cpu.use.meta.json
```

执行模型：

- 后端启动时注册一个独立 metric cleanup background task
- 一个进程内只启动一个 cleanup loop
- 第一版可以用单线程或单协程周期性执行
- 第一版采用最简接入方式：后端启动时创建一个 metric cleanup 单协程任务，周期性 sleep + cleanup；服务关闭时取消该任务
- 按 `metric_snapshot_cleanup_interval_seconds` 间隔扫描 metrics 目录
- cleanup 不参与 per-snapshot refresh lock
- query tool 每次仍然自己检查 data/meta 是否存在和 fresh

清理任务失败不影响查询。
如果清理任务运行时 query tool 正在读取某个快照，
依赖文件替换和删除的操作系统语义保证已打开文件句柄不受影响；
下一次查询会重新检查文件和 meta。
如果 query 打开文件前快照刚好被 cleanup 删除，
query 应容忍 `FileNotFoundError`，
并重新走快照缺失后的刷新流程。

相关配置项见第 3 节。

## 18. 历史监控查询

Phase6 第一版同时实现 latest 和 history 查询。
history 查询边界：

- 只能查询指定单元
- 必须指定 `unit_name`
- 必须指定 `metric_key`
- 必须指定时间范围
- 时间参数使用 Unix timestamp 秒数
- 不支持全量单元历史查询
- 不支持后台定期同步全量历史
- 管理员可查询任意真实单元
- 普通用户只能查询自己可见的真实单元
- ai-agent 不做 unit 归属 join，由 DBAAS 根据当前身份校验权限

第一版工具签名建议：

```python
query_unit_metric_history_tool(
    unit_name: str,
    metric_key: str,
    start_ts: int,
    end_ts: int,
    jq_filter: str,
    max_preview_items: int | None = None,
)
```

参数约束：

- `unit_name`
  - 单元名称，必填
- `metric_key`
  - 来自 catalog 的唯一监控项 key，必填
- `start_ts`
  - 开始时间，Unix timestamp 秒数
- `end_ts`
  - 结束时间，Unix timestamp 秒数
- `jq_filter`
  - 对历史点位文件执行的 jq
- `max_preview_items`
  - 可选，用于覆盖默认预览条数

时间范围校验：

- `start_ts < end_ts`
- `end_ts <= now_ts`

用户使用自然语言时间时，
模型应先换算成 Unix timestamp 秒数再调用工具。

涉及相对时间时，
例如 `最近一小时`、`最近一天`、`最近 7 天`，
模型不应依赖自身上下文猜测当前时间，
而应先调用当前时间工具获取准确 `now_ts`。

第一版只需要新增一个通用当前时间工具：

```python
get_current_time_tool() -> dict
```

返回示例：

```json
{
  "status": "success",
  "now_ts": 1777441200,
  "iso_utc": "2026-04-29T04:00:00Z",
  "iso_local": "2026-04-29T12:00:00+08:00",
  "timezone": "Asia/Shanghai"
}
```

`get_current_time_tool` 只负责返回当前 Unix timestamp 秒数和辅助展示时间。
模型根据用户表达计算 `start_ts` 和 `end_ts`，
例如：

```text
最近一小时：start_ts = now_ts - 3600
最近一天：start_ts = now_ts - 86400
最近 7 天：start_ts = now_ts - 604800
```

历史监控 tool 自身仍必须校验时间范围是否合法。

模型流程示例：

```text
用户问“查看 mysql-prod-0 最近一小时 CPU”
1. 调用 get_current_time_tool 获取 now_ts
2. 计算 start_ts = now_ts - 3600，end_ts = now_ts
3. 调用 query_unit_metric_history_tool
```

历史监控数据可能较大，
因此查询结果也需要落盘成运行时缓存文件，
再通过 jq 处理。

历史数据结构建议为：

```json
[
  {"ts": 1777437600, "value": 31.2},
  {"ts": 1777437660, "value": 35.7}
]
```

`value` 的具体类型同样由 catalog 中的 `value_type` 决定，
可能是数字、普通字符串、枚举字符串或布尔值。

DBAAS 历史监控接口第一版可以约定为：

```text
GET /units/{unit_name}/metrics/history?metric_key=container.cpu.use&start_ts=1777437600&end_ts=1777441200
```

其中 `unit_name` 放在 URL path 中，
客户端需要按 URL 规则编码。
tool 调用该接口时必须携带当前用户身份。
DBAAS 应根据身份判断当前用户是否可访问该真实单元。

返回结构：

```json
[
  {"ts": 1777437600, "value": 31.2},
  {"ts": 1777437660, "value": 35.7}
]
```

历史缓存文件名建议直接使用可读 scope key，
便于调试时从路径看出身份、单元、监控项和时间范围。

路径格式：

```text
runtime/dbaas_workspace/metrics_history/{scope}__{safe_user}__{safe_unit_name}__{metric_key}__{start_ts}__{end_ts}.json
runtime/dbaas_workspace/metrics_history/{scope}__{safe_user}__{safe_unit_name}__{metric_key}__{start_ts}__{end_ts}.meta.json
```

其中：

- `scope` 为 `admin` 或 `user`
- `safe_user` 在管理员场景固定为 `all`
- `safe_user` 和 `safe_unit_name` 需要做安全文件名转换，只保留 `[a-zA-Z0-9._-]`，其他字符替换为 `_`
- `metric_key` 只能包含 `[a-zA-Z0-9._-]`，可以直接用于文件名
- `start_ts` 和 `end_ts` 为 Unix timestamp 秒数

示例：

```text
runtime/dbaas_workspace/metrics_history/admin__all__mysql-primary-01__container.cpu.use__1777437600__1777441200.json
runtime/dbaas_workspace/metrics_history/admin__all__mysql-primary-01__container.cpu.use__1777437600__1777441200.meta.json
runtime/dbaas_workspace/metrics_history/user__payment-platform-team__mysql-primary-01__container.cpu.use__1777437600__1777441200.json
runtime/dbaas_workspace/metrics_history/user__payment-platform-team__mysql-primary-01__container.cpu.use__1777437600__1777441200.meta.json
```

同一个 `scope + user + unit_name + metric_key + start_ts + end_ts` 命中本地历史缓存时，
可以直接对缓存文件执行 jq。
未命中时再请求 DBAAS 历史监控接口下载。
禁止普通用户复用管理员历史缓存，
也禁止不同普通用户之间复用历史缓存。

历史查询 jq 示例：

峰值：

```jq
max_by(.value)
```

平均值：

```jq
(map(.value) | add) / length
```

超过 80 的时间点：

```jq
[.[] | select((.value | type) == "number" and .value > 80)]
```

历史监控也应使用 jq timeout、preview 和输出字节限制，
避免大结果进入工具返回和模型上下文。

历史缓存清理可以复用 metric cleanup background task，
扫描 `runtime/dbaas_workspace/metrics_history/` 并删除过期历史缓存。
历史缓存过期策略复用 `metric_snapshot_ttl_seconds`。

## 19. 本阶段暂不决定的内容

Phase6 第一版设计已收敛。
后续进入实现时，可以根据 mock-server 和真实 DBAAS 接口细节再微调。
