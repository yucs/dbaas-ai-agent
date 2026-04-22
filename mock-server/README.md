# DBaaS Mock Server

一个本地运行的 HTTP + JSON mock 服务，用来模拟 DBaaS 控制面的部分能力，供后续 AI Agent 联调、测试和验证使用。

当前阶段主要覆盖：

- 服务资源
- 主机资源
- 集群资源
- 站点资源
- 备份策略资源

## 技术栈

- Python 3.11+
- FastAPI
- Pydantic v2
- Uvicorn
- 本地 JSON seed data

## 当前已实现

- `GET /healthz`
- `GET /sites`
- `GET /sites/{siteId}`
- `GET /clusters`
- `GET /clusters/{clusterId}`
- `GET /hosts`
- `GET /hosts/{hostId}`
- `GET /users`
- `GET /users/{user}`
- `GET /services`
- `GET /services/{name}`
- `PUT /services/{name}/resource`
- `PUT /services/{name}/storage`
- `POST /services/{name}/image-upgrade`
- `GET /tasks/{taskId}`

## 认证与权限

- `GET /healthz` 保持免鉴权
- 其他业务接口都需要 `Authorization: Bearer <token>`
- `Bearer admin` 表示管理员，可访问全部接口和全部资源
- `Bearer user:<user>` 表示普通用户，只能访问自己 user 下的服务和对应任务
- `GET /users` 和 `GET /users/{user}` 中的 `user` 直接等于服务组 `user`
- 管理员可查看全部用户；普通用户只能查看自己
- 普通用户无权访问 `sites`、`clusters`、`hosts` 相关平台资源接口

认证示例：

管理员查询任意服务：

```bash
curl http://127.0.0.1:8000/services/mysql-xf2 \
  -H 'Authorization: Bearer admin'
```

普通用户查询自己的服务：

```bash
curl http://127.0.0.1:8000/services \
  -H 'Authorization: Bearer user:payment-team-prod'
```

普通用户访问平台资源会被拒绝：

```bash
curl http://127.0.0.1:8000/hosts \
  -H 'Authorization: Bearer user:payment-team-prod'
```

示例返回：

```json
{"detail":"platform resources are only available to admin users"}
```

未携带 Bearer token 会返回 401：

```bash
curl http://127.0.0.1:8000/services
```

示例返回：

```json
{"detail":"missing bearer token"}
```

其中：

- `GET /users` 查询当前已加载服务中出现过的全部用户，用户名即服务组 `user`
- `GET /users/{user}` 按用户名查询用户详情和其名下服务组摘要
- `GET /services` 查询当前内存中已加载的服务组，支持按 `user` 过滤
- `GET /services/{name}` 按服务组名称查询
- 返回服务组聚合视图，包含站点归属、网络、子服务、单元和备份策略摘要
- `GET /sites`、`GET /clusters`、`GET /hosts` 查询平台资源拓扑
- `PUT /services/{name}/resource` 按指定子服务类型更新 CPU、内存和 `platformAuto`
- `PUT /services/{name}/storage` 按指定子服务类型更新存储和 `platformAuto`
- `POST /services/{name}/image-upgrade` 创建异步镜像升级任务
- `GET /tasks/{taskId}` 查询通用异步任务状态

## 快速启动

进入项目目录：

```bash
cd /Users/yucs/work/dbscale-ai-agent/mock-server
```

执行启动脚本：

```bash
./start.sh
```

同一个脚本同时支持：

- macOS
- Linux

启动脚本会自动完成：

- 创建 `.venv`
- 安装 `requirements.txt`
- 启动 `uvicorn`

默认监听地址：

```text
http://127.0.0.1:8000
```

## 验证接口

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
```

示例返回：

```json
{"status":"ok"}
```

查询服务组：

```bash
curl http://127.0.0.1:8000/services/mysql-xf2 \
  -H 'Authorization: Bearer admin'
```

查询站点：

```bash
curl http://127.0.0.1:8000/sites \
  -H 'Authorization: Bearer admin'
```

查询主机：

```bash
curl http://127.0.0.1:8000/hosts/host-01-01 \
  -H 'Authorization: Bearer admin'
```

查询全部服务组：

```bash
curl http://127.0.0.1:8000/services \
  -H 'Authorization: Bearer admin'
```

查询全部用户：

```bash
curl http://127.0.0.1:8000/users \
  -H 'Authorization: Bearer admin'
```

查询单个用户：

```bash
curl http://127.0.0.1:8000/users/payment-team-prod \
  -H 'Authorization: Bearer admin'
```

按 user 查询服务组：

```bash
curl 'http://127.0.0.1:8000/services?user=payment-team-prod' \
  -H 'Authorization: Bearer admin'
```

普通用户查询自己的服务组：

```bash
curl http://127.0.0.1:8000/services \
  -H 'Authorization: Bearer user:payment-team-prod'
```

更新资源规格：

```bash
curl -X PUT http://127.0.0.1:8000/services/mysql-xf2/resource \
  -H 'Authorization: Bearer admin' \
  -H 'Content-Type: application/json' \
  -d '{
    "childServiceType": "mysql",
    "platformAuto": false,
    "cpu": 16,
    "memory": 64
  }'
```

更新存储规格：

```bash
curl -X PUT http://127.0.0.1:8000/services/mysql-xf2/storage \
  -H 'Authorization: Bearer admin' \
  -H 'Content-Type: application/json' \
  -d '{
    "childServiceType": "mysql",
    "platformAuto": false,
    "storage": {
      "dataVolumeSize": 1024,
      "logVolumeSize": 200
    }
  }'
```

创建镜像升级任务：

```bash
curl -X POST http://127.0.0.1:8000/services/mysql-xf2/image-upgrade \
  -H 'Authorization: Bearer admin' \
  -H 'Content-Type: application/json' \
  -d '{
    "childServiceType": "mysql",
    "image": "mysql:8.0.37",
    "version": "8.0.37",
    "unitIds": ["mysql-primary-01"]
  }'
```

查询通用任务：

```bash
curl http://127.0.0.1:8000/tasks/task-0001 \
  -H 'Authorization: Bearer admin'
```

## 可选启动参数

修改监听地址或端口：

```bash
HOST=0.0.0.0 PORT=9000 ./start.sh
```

启用热重载：

```bash
RELOAD=true ./start.sh
```

## 目录结构

```text
mock-server/
  AGENT.md
  README.md
  app/
    api/
    schemas/
    store/
    main.py
  data/
    sites.json
    clusters.json
    hosts.json
    services.json
  tests/
  requirements.txt
  start.sh
```

目录职责：

- `app/api/`：HTTP 路由定义
- `app/schemas/`：接口级 schema
- `app/store/`：从本地 JSON 加载数据到内存，并提供查询聚合能力
- `app/main.py`：应用入口
- `data/sites.json`：站点原始 seed 数据
- `data/clusters.json`：集群原始 seed 数据
- `data/hosts.json`：主机原始 seed 数据
- `data/services.json`：服务组原始 seed 数据

## 数据加载约定

- 服务启动时会同时读取 `mock-server/data/sites.json`、`clusters.json`、`hosts.json`、`services.json`
- 这四个文件顶层都使用数组格式
- `sites.json`、`clusters.json`、`hosts.json` 只保存各自资源的原始字段，不内嵌聚合结果
- `site` 下的 `clusters`、`serviceGroups`，`cluster` 下的 `hosts`、`serviceGroupCount` 等都在内存加载后动态聚合
- `services.json` 保留服务组自身字段和 unit 到 host/disk 的引用关系
- `backupStrategy` 直接内嵌在服务组对象中
- 服务接口响应会补齐 `environment`、`siteName`、`region`、`zone`
- 服务组、子服务、单元都会返回 `healthStatus`
- 单元会额外返回 `containerStatus`
- 单元会返回 `hostId`、`hostName`、`hostIp`、`containerIp`
- 单元存储固定包含 `data`、`log` 两个 volume，并映射到主机磁盘
- 当前 seed 规模为 12 个站点、48 个集群、1920 台主机、2208 个服务组
- 数据加载到内存后供接口查询使用
- 运行期间的 update 动作只修改内存
- 不回写本地 `data` 文件
- 服务重启后恢复为 seed 数据初始状态
- 如果 seed 数据里缺失这些状态字段，加载时会自动补默认值：`healthStatus=HEALTHY`、`containerStatus=RUNNING`

## 当前示例数据

当前内置示例分布在四个文件中：

- [data/sites.json](data/sites.json)
- [data/clusters.json](data/clusters.json)
- [data/hosts.json](data/hosts.json)
- [data/services.json](data/services.json)

当前样例包括：

- 服务组：`mysql-xf2`
- 服务组：`tidb-oltp`
- 服务组：`kafka-stream`
- 服务组：`influxdb-monitor`
- 服务组：`redis-cache`
- 服务组：`mongodb-docs`
- 服务组：`elasticsearch-search`
- 服务组：`clickhouse-warehouse`

## Update 接口约定

- `childServiceType` 为必填项，只能操作指定类型的子服务
- 只有 body 中明确传入的字段才会被更新，未传字段保持不变
- `resource` 接口更新该子服务下所有 units 的 `cpu`、`memory`
- `storage` 接口更新该子服务下所有 units 的 `storage.data.size`、`storage.log.size`
- `platformAuto` 更新在子服务层
- 服务组不存在时返回 `404`
- 服务组存在但 `childServiceType` 不存在时返回 `502`
- 请求体缺少有效更新字段时返回 `422`

## 通用 Task 接口约定

- 所有异步接口都应返回 `taskId`
- 统一通过 `GET /tasks/{taskId}` 查询任务状态
- 当前通用任务字段包括 `taskId`、`type`、`status`、`message`、`reason`、`resourceType`、`resourceName`、`result`、`createdAt`、`updatedAt`
- 当前任务状态使用 `RUNNING`、`SUCCESS`、`FAILED`
- `POST /services/{name}/image-upgrade` 会创建 `service.image.upgrade` 类型任务
- 镜像升级任务创建接口本身只返回 `taskId`
- 镜像升级任务会在后台执行，不依赖 `GET /tasks/{taskId}` 才推进
- 当前镜像升级任务按 unit 顺序执行，默认每隔 3 秒升级 1 个 unit
- 每完成 1 个 unit，都会更新任务的 `message`
- 当任务完成时，会把目标单元的 `image` 和 `version` 写回服务内存数据
