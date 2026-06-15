# DBAAS 智能助手 API 开发文档

本文档是项目 API 文档入口。详细接口契约按调用方拆分维护：

- [前端调用 AI-Agent API](docs/api/frontend-ai-agent-api.md)
- [AI-Agent 调用 DBAAS / Mock Server API](docs/api/dbaas-mock-server-api.md)

## 文档边界

### 前端调用 AI-Agent

只记录当前 `frontend/app.js` 实际调用的 ai-agent 接口、SSE 事件、请求响应结构和页面调用流程。

当前页面未使用的后端接口不作为前端开发依赖展开，避免前端团队误接。

### AI-Agent 调用 DBAAS / Mock Server

只记录 ai-agent 当前实际调用的 DBAAS/mock-server 接口。

DBAAS 平台其他接口、mock-server 调试接口、前端不会直接调用且 ai-agent 当前也不会调用的接口，不纳入当前文档范围。

## 当前文档维护方式

接口后续还会继续优化。发生以下调整时，应同步更新对应文档：

- 前端调用 ai-agent 的路径、请求体、响应体、SSE 事件或页面调用流程变化
- ai-agent 调用 DBAAS/mock-server 的路径、参数、响应结构、字段含义或错误语义变化
- Session、Message、Approval、Operation、Task 等前端展示结构变化
- DBAAS 服务、主机、备份、监控、预检、写操作、任务接口字段变化
