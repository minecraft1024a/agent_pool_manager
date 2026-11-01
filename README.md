# Agent 后台管理

Neo-MoFox 插件：在聊天模型的 Agent 筛选事件中移除全部 Agent，并通过白名单保护的后台任务接口提供查询、启动和取消能力。

## 功能组件

- `hide_agents_from_model`：订阅 `BEFORE_AGENT_FILTER`，清空 Agent 筛选结果并停止后续处理器修改该结果。
- `list_agents` Tool：列出系统已注册 Agent 的签名、说明和后台运行白名单状态。
- `agent_runtime_status` Tool：查看任务管理器统计及本插件启动的活动 Agent。
- `run_agent_background` Tool：在当前聊天流启动一个已加入白名单的 Agent。
- `/agents` Command：查看当前运行的后台 Agent；命令要求 `OPERATOR` 权限。
- `agent_pool_manager:service:agent_manager` Service：向其他插件提供 Agent 查询、后台运行、取消、运行列表和状态查询 API。

## 后台启动限制

后台运行默认对全部已注册 Agent 开放（`agents.allowed_signatures = ["*"]`）。如需限制可运行范围，请先通过 `list_agents` 查询签名，再将配置改成明确的签名列表。空白名单会拒绝所有后台启动请求。

每次启动都会验证 Agent 已注册、所属插件已加载、参数名和必填项符合 `execute()` 签名、参数值符合类型注解、聊天流类型和平台符合 Agent 声明，并且 `go_activate()` 返回允许。带 `chatter_allow` 限制的 Agent 还需要 Service 调用方提供匹配的 Chatter 名称或签名。任务同时受并发上限和最长运行时间限制；插件卸载时会取消本插件启动的任务。

Tool 只允许使用当前聊天流；Service 调用方需要显式提供已有聊天流 ID。状态记录不返回聊天流 ID 或启动参数；成功记录会返回 Agent 结果，异常记录会返回报错信息。

## 配置

配置文件路径：`config/plugins/agent_pool_manager/config.toml`

```toml
[plugin]
enabled = true

[agents]
allowed_signatures = ["*"]
max_concurrent_runs = 3
task_timeout_seconds = 1800
```

`max_concurrent_runs` 会被限制在 1 到 16，`task_timeout_seconds` 会被限制在 1 到 3600 秒。状态 API 返回当前任务和最近 20 个已结束任务；状态为 `运行中`、`成功`、`异常` 或 `已取消`。成功记录包含 Agent 返回值，异常记录包含错误信息。

## Service API

通过 Neo-MoFox 的 `service_api.get_service("agent_pool_manager:service:agent_manager")` 获取 Service。该 Service 提供：

- `list_agents()`：列出已注册 Agent。
- `get_runtime_status()`：查询任务管理器和 Agent 运行状态，附成功结果或异常消息。
- `start_agent(signature, stream_id, arguments, chatter_name="", chatter_signature="")`：校验后启动后台任务，并返回 `run_id`。
- `stop_agent(run_id)`：取消由 `start_agent` 返回 ID 对应的任务。
- `list_running_agents()`：列出本 Service 当前运行的任务。
- `shutdown()`：取消本插件的全部活动任务。

Python API 签名及参数由插件内 `AgentManagerService` 定义；其他插件不应直接访问其内部任务表。

## 开发说明

组件基类和 API 优先通过 `src.app.plugin_system` 的公开入口导入。Agent 执行委托给 `agent_api.execute_agent`；插件、聊天流与 Service 查询使用对应的 `plugin_api`、`stream_api` 和 `service_api`。事件处理器依照 Neo-MoFox 的 `EventDecision` 协议修改已有事件参数，并返回 `STOP` 终止后续处理器。

这是 AI 编程作品，不建议参考。
