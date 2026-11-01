"""Agent 后台管理插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import (
    BaseConfig,
    Field,
    SectionBase,
    config_section,
)


class AgentPoolManagerConfig(BaseConfig):
    """定义插件开关、后台运行白名单和任务限制。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Agent 后台管理插件配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """控制插件是否启用。"""

        enabled: bool = Field(default=True, description="是否启用插件")

    @config_section("agents", title="Agent 执行策略", tag="agent")
    class AgentSection(SectionBase):
        """配置允许后台运行的 Agent 和任务资源上限。"""

        allowed_signatures: list[str] = Field(
            default_factory=lambda: ["*"],
            description=(
                "允许从后台启动的 Agent 签名白名单。默认 * 表示全部已注册 Agent；"
                "如需限制范围，请改为具体签名，例如 plugin_name:agent:agent_name。"
            ),
        )
        max_concurrent_runs: int = Field(
            default=3,
            description="本插件允许同时运行的后台 Agent 数量，上限 16",
        )
        task_timeout_seconds: int = Field(
            default=1800,
            description="单个后台任务的超时秒数，范围 1 到 3600",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    agents: AgentSection = Field(default_factory=AgentSection)

