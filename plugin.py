"""Agent 后台管理插件入口。"""

from __future__ import annotations

import asyncio
from typing import cast

from src.app.plugin_system.api import service_api
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .components import (
    AgentManagerService,
    AgentRuntimeStatusTool,
    HideAgentsFromModelHandler,
    ListAgentsTool,
    RunningAgentsCommand,
    RunAgentBackgroundTool,
)
from .config import AgentPoolManagerConfig

logger = get_logger("agent_pool_manager")
_SERVICE_SIGNATURE = "agent_pool_manager:service:agent_manager"


@register_plugin
class AgentPoolManagerPlugin(BasePlugin):
    """隐藏模型池中的 Agent，并注册受校验保护的后台管理组件。"""

    plugin_name: str = "agent_pool_manager"
    configs = [AgentPoolManagerConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """按插件开关返回事件处理器、Tool、Command 和 Service。"""
        config = cast(AgentPoolManagerConfig | None, self.config)
        if config is not None and not config.plugin.enabled:
            return []
        return [
            AgentManagerService,
            HideAgentsFromModelHandler,
            ListAgentsTool,
            AgentRuntimeStatusTool,
            RunAgentBackgroundTool,
            RunningAgentsCommand,
        ]

    async def on_plugin_loaded(self) -> None:
        """初始化跨 Service 实例共享的任务记录和异步锁。"""
        if not isinstance(self.config, AgentPoolManagerConfig):
            logger.warning("配置未加载，后台 Agent 启动将不可用")
            return
        if not self.config.plugin.enabled:
            logger.info("插件已加载，但配置已关闭")
            return

        # Service API 每次调用都会新建 Service 实例，插件实例负责保管共享状态。
        self._agent_pool_runs = {}
        self._agent_pool_lock = asyncio.Lock()
        logger.info("Agent 后台管理组件已加载")

    async def on_plugin_unloaded(self) -> None:
        """卸载前取消本插件启动的所有后台 Agent。"""
        service = service_api.get_service(_SERVICE_SIGNATURE)
        if service is not None:
            await service.shutdown()
        logger.info("Agent 后台管理插件已卸载")

