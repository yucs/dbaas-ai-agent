# DBaaS Mock Server

## 介绍

`mock-server` 是一个本地运行的 HTTP + JSON 模拟服务，用来模拟真实 DBaaS 控制面的部分能力，供后续 AI Agent 联调、测试和验证使用。

这个 mock server 的目标是：

- 为 AI Agent 提供稳定、可重复的测试数据
- 通过简单的 HTTP 接口返回 JSON 数据，便于本地联调
- 尽量贴近现有 DBaaS 领域模型，方便后续平滑过渡到真实接口
- 在当前阶段保持实现简单，便于随着需求逐步裁剪字段和调整接口

## 协作约定

- 每次准备修改代码、配置或文档之前，必须先给出本次变更 plan
- plan 需要先发给用户确认
- 只有在用户明确允许之后，才能开始实际修改文件
- 如果用户取消、叫停或未确认，只能继续分析和说明，不能落盘修改
- API 接口相关结构体的每个字段都必须写注释，说明字段作用

## 技术栈

- 语言：Python 3.11+
- HTTP 框架：FastAPI
- 数据模型与校验：Pydantic v2
- 运行服务：Uvicorn
- 数据来源：本地 JSON seed data，启动时加载到内存
- 测试：pytest + httpx

选择这套技术栈的原因是：足够简单、成熟稳定、适合快速搭建本地 HTTP mock 服务，也方便后续给 AI Agent 做接口联调和自动化测试。

## 目录结构

推荐目录结构如下：

```text
mock-server/
  AGENT.md
  app/
    api/
    schemas/
    store/
    main.py
  data/
    services/
    backup-strategies/
  tests/
```

各目录职责如下：

- `app/`：`FastAPI` 应用代码目录，后续放接口、schema、数据加载逻辑等代码
- `app/api/`：HTTP 路由定义目录，后续按资源拆分接口
- `app/schemas/`：接口级模型，主要用于定义 HTTP API 的请求和响应结构
- `app/store/`：内存数据读取与聚合目录，负责从 `data/` 加载 JSON 并提供查询能力
- `app/main.py`：应用入口，负责组装 `FastAPI`、路由和内存数据存储
- `data/services/`：服务相关的 mock 数据，建议按服务组维度存放 JSON 文件
- `data/backup-strategies/`：备份策略相关的 mock 数据，建议按服务组维度存放 JSON 文件
- `tests/`：接口测试和后续联调测试代码

当前阶段的数据加载方式约定为：

- 服务启动时直接从 `data/` 目录读取 JSON 文件
- 数据加载到内存中供接口查询使用
- 后续的 `update` 动作只修改内存中的数据状态
- 运行期间不回写 `data/` 目录，不做数据落地
- 不依赖数据库
- 后续如果增加更多资源类型，可以继续在 `data/` 下按资源维度扩展子目录

这样的目录结构有几个好处：

- 数据文件直观，方便人工维护和调试
- 不需要额外准备数据库环境
- 适合本地开发、联调和回归测试
- 每次重启都能回到初始 seed 数据状态
- 后续新增主机、集群、网络等资源时也容易扩展
