"""Agent 列表、运行状态和后台启动 Tool。"""

from __future__ import annotations

import json
from typing import Annotated, Any, Protocol

from src.app.plugin_system.api import service_api
from src.app.plugin_system.base import BaseTool

_SERVICE_SIGNATURE = "agent_pool_manager:service:agent_manager"


class _AgentManagerServiceProtocol(Protocol):
    """描述本文件中 Tool 会调用的 Service 方法。"""

    def list_agents(self) -> dict[str, Any]:
        """列出 Agent。"""
        ...

    def get_runtime_status(self) -> dict[str, Any]:
        """查询后台运行状态。"""
        ...

    async def start_agent(
        self, signature: str, stream_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """校验并启动后台 Agent。"""
        ...


def _get_service() -> _AgentManagerServiceProtocol:
    """通过公开 Service API 获取 Agent 管理服务。"""
    service = service_api.get_service(_SERVICE_SIGNATURE)
    if service is None:
        raise RuntimeError("agent_manager 服务未注册")
    return service  # type: ignore[return-value]


class ListAgentsTool(BaseTool):
    """查询系统中已注册的 Agent 及其白名单状态。"""

    name: str = "list_agents"
    description: str = "获取系统中已注册的 Agent 列表及其后台运行白名单状态。"
    dependencies: list[str] = [_SERVICE_SIGNATURE]

    async def execute(
        self,
    ) -> tuple[Annotated[bool, "是否成功"], Annotated[str | dict, "返回结果"]]:
        """返回 Agent 签名、说明和是否允许后台运行。"""
        try:
            return True, _get_service().list_agents()
        except Exception as exc:
            return False, f"获取 Agent 列表失败：{exc}"


class AgentRuntimeStatusTool(BaseTool):
    """查询任务管理器和 Agent 后台运行状态。"""

    name: str = "agent_runtime_status"
    description: str = (
        "获取后台运行状态和任务管理器统计。每条 Agent 记录显示运行中、成功、异常或取消；"
        "成功时附 Agent 返回结果，异常时附错误信息。"
    )
    dependencies: list[str] = [_SERVICE_SIGNATURE]

    async def execute(
        self,
    ) -> tuple[Annotated[bool, "是否成功"], Annotated[str | dict, "返回结果"]]:
        """返回当前活动任务数量和本插件的后台 Agent 列表。"""
        try:
            return True, _get_service().get_runtime_status()
        except Exception as exc:
            return False, f"获取后台状态失败：{exc}"


class RunAgentBackgroundTool(BaseTool):
    """按白名单和 Agent 参数定义校验后启动后台任务。"""

    name: str = "run_agent_background"
    description: str = (
        "在当前聊天流后台启动一个已加入配置白名单的 Agent。先调用 list_agents "
        "确认签名与白名单；arguments_json 必须是 JSON object，参数会按 Agent 的 "
        "execute() 类型和签名校验。后台运行受并发上限、平台/聊天类型和 go_activate 限制。"
    )
    dependencies: list[str] = [_SERVICE_SIGNATURE]

    async def execute(
        self,
        agent_signature: Annotated[str, "Agent 完整签名，例如 plugin:agent:name"],
        arguments_json: Annotated[
            str,
            'Agent execute() 参数的 JSON object，例如 {"task":"..."}',
        ],
    ) -> tuple[Annotated[bool, "是否成功"], Annotated[str | dict, "返回结果"]]:
        """解析参数并在当前聊天流中提交后台 Agent 任务。"""
        if not isinstance(arguments_json, str) or len(arguments_json) > 16_384:
            return False, "arguments_json 必须是长度不超过 16384 的 JSON 字符串"
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError as exc:
            return False, f"arguments_json 不是有效 JSON：{exc.msg}"
        if not isinstance(arguments, dict):
            return False, "arguments_json 顶层必须是 JSON object"

        stream_id = self.get_current_stream_id()
        if not stream_id:
            return False, "当前工具调用没有绑定聊天流，无法启动 Agent"
        try:
            result = await _get_service().start_agent(
                signature=agent_signature,
                stream_id=stream_id,
                arguments=arguments,
            )
            return True, result
        except Exception as exc:
            return False, f"后台启动 Agent 失败：{exc}"

