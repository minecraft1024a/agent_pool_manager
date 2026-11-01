"""查看后台运行 Agent 的命令。"""

from __future__ import annotations

from typing import Any, Protocol, cast

from src.app.plugin_system.api import service_api, stream_api
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

_SERVICE_SIGNATURE = "agent_pool_manager:service:agent_manager"


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
        """确保命令聊天流可路由后发送文本，并检查适配器发送结果。"""
        try:
            stream_id = self.stream_id
            platform: str | None = None
            reply_to = self.message_id or None
            message = cast(_IncomingMessageProtocol | None, self._message)

            if message is not None:
                platform = message.platform or None
                extra = message.extra if isinstance(message.extra, dict) else {}
                group_id = str(extra.get("group_id") or "")
                chat_type = message.chat_type or ("group" if group_id else "private")
                user_id = message.sender_id if chat_type != "group" else ""

                # 命令在聊天流创建前就会被分发，因此要从原始消息补建流信息。
                if platform and (group_id if chat_type == "group" else user_id):
                    stream = await stream_api.get_or_create_stream(
                        stream_id=stream_id,
                        platform=platform,
                        user_id=user_id,
                        group_id=group_id if chat_type == "group" else "",
                        chat_type=chat_type,
                    )
                    stream_id = stream.stream_id

                    # send_text 查询流信息时会缓存未命中结果；清除旧缓存，
                    # 让刚补建的群/用户路由信息立即生效。
                    from src.core.managers.stream_manager import get_stream_manager

                    cache_clear = getattr(
                        get_stream_manager().get_stream_info, "cache_clear", None
                    )
                    if callable(cache_clear):
                        cache_clear()

            if not stream_id:
                return False, "发送 Agent 运行状态失败：无法确定当前聊天流。"

            sent = await send_text(
                text,
                stream_id=stream_id,
                platform=platform,
                reply_to=reply_to,
            )
        except Exception as exc:
            return False, f"发送 Agent 运行状态失败：{exc}"

        if not sent:
            return False, "发送 Agent 运行状态失败：聊天适配器未确认发送。"
        return True, text

