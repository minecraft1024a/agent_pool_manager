"""查看后台运行 Agent 的命令。"""

from __future__ import annotations

from typing import Any, Protocol, cast

from src.app.plugin_system.api import service_api
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

_SERVICE_SIGNATURE = "agent_pool_manager:service:agent_manager"


class _AgentManagerServiceProtocol(Protocol):
    """声明命令需要调用的 Service 接口。"""

    def list_running_agents(self) -> list[dict[str, Any]]:
        """列出仍在运行的 Agent。"""
        ...


class RunningAgentsCommand(BaseCommand):
    """列出由本插件启动且当前仍在运行的 Agent。"""

    name: str = "agents"
    description: str = "查看当前运行的后台 Agent"
    permission_level: PermissionLevel = PermissionLevel.OPERATOR
    dependencies: list[str] = [_SERVICE_SIGNATURE]

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """委托 Neo-MoFox 命令基类执行路由。"""
        return await super().execute(message_text)

    @cmd_route()
    async def handle_list_running(self) -> tuple[bool, str]:
        """输出 Agent 签名、运行标识和已运行时长。"""
        service = service_api.get_service(_SERVICE_SIGNATURE)
        if service is None:
            return await self._reply("Agent 管理服务当前不可用。")

        running = cast(_AgentManagerServiceProtocol, service).list_running_agents()
        if not running:
            return await self._reply("当前没有运行中的后台 Agent。")

        lines = [f"当前运行中的后台 Agent（{len(running)} 个）："]
        for item in running:
            lines.append(
                f"- {item['signature']} | {item['status']} | "
                f"run_id={item['run_id']} | 已运行 {item['elapsed_seconds']} 秒"
            )
        return await self._reply("\n".join(lines))

    async def _reply(self, text: str) -> tuple[bool, str]:
        """向命令所在聊天流发送文本，并返回相同的命令结果。"""
        try:
            await send_text(text, stream_id=self.stream_id)
        except Exception as exc:
            return False, f"发送 Agent 运行状态失败：{exc}"
        return True, text

