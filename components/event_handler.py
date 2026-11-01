"""从聊天模型可用组件中移除 Agent 的事件处理器。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import EventType
from src.kernel.event import EventDecision


class HideAgentsFromModelHandler(BaseEventHandler):
    """拦截 Agent 筛选事件，避免 Agent 进入聊天模型工具池。"""

    name: str = "hide_agents_from_model"
    description: str = "清空 Agent 筛选结果，阻止 Agent 进入聊天模型工具池"
    weight: int = 2**31 - 1
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.BEFORE_AGENT_FILTER]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """将筛选结果置空，并停止后续处理器修改该结果。"""
        params["component_classes"] = []
        # STOP 会保留空列表，并阻止后续处理器重新加入 Agent。
        return EventDecision.STOP, params

