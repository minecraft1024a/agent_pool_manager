"""查看后台运行 Agent 的命令。"""

from __future__ import annotations

from typing import Any, Protocol, cast

from src.app.plugin_system.api import service_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

_SERVICE_SIGNATURE = "agent_pool_manager:service:agent_manager"
logger = get_logger("agent_pool_manager.command")


class _AgentManagerServiceProtocol(Protocol):
    """声明命令需要调用的 Service 接口。"""

    def list_running_agents(self) -> list[dict[str, Any]]:
        """列出仍在运行的 Agent。"""
        ...


class _IncomingMessageProtocol(Protocol):
    """声明发送命令回复所需的当前消息字段。"""

    platform: str
    chat_type: str
    sender_id: str
    sender_name: str
    message_id: str
    extra: dict[str, Any]


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
        """发送命令结果，并记录发送路由和适配器返回值用于排查。"""
        message = cast(_IncomingMessageProtocol | None, self._message)
        extra = message.extra if message and isinstance(message.extra, dict) else {}
        platform = message.platform if message else ""
        chat_type = message.chat_type if message else ""
        group_id = str(extra.get("group_id") or "")
        sender_id = message.sender_id if message else ""

        logger.info(
            f"准备发送 /agents 回复：stream_id={self.stream_id}, "
            f"platform={platform}, chat_type={chat_type}, "
            f"message_id={self.message_id}, group_id={group_id}, "
            f"sender_id={sender_id}, text_length={len(text)}"
        )
        try:
            sent = await send_text(text, stream_id=self.stream_id)
        except Exception as exc:
            logger.error(
                f"发送 /agents 回复时抛出异常：stream_id={self.stream_id}, "
                f"message_id={self.message_id}, error={exc}",
                exc_info=True,
            )
            return False, f"发送 Agent 运行状态失败：{exc}"

        logger.info(
            f"发送 /agents 回复完成：stream_id={self.stream_id}, "
            f"message_id={self.message_id}, sent={sent}"
        )
        if not sent:
            logger.error(
                f"send_text 返回 False：stream_id={self.stream_id}, "
                f"platform={platform}, chat_type={chat_type}, "
                f"group_id={group_id}, sender_id={sender_id}"
            )
            return False, "发送 Agent 运行状态失败：聊天适配器未确认发送。"
        return True, text

