"""Agent 查询与后台运行 Service。"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, get_args, get_origin, get_type_hints
from uuid import uuid4

from pydantic import TypeAdapter

from src.app.plugin_system.api import agent_api, plugin_api, stream_api
from src.app.plugin_system.base import BaseAgent, BasePlugin, BaseService
from src.app.plugin_system.types import ChatType
from src.kernel.concurrency import (
    TaskInfo,
    TaskNotFoundError,
    get_task_manager,
)

from ..config import AgentPoolManagerConfig

_TASK_GROUP = "agent_pool_manager"
_MAX_ARGUMENTS_JSON_CHARS = 16_384
_MAX_ARGUMENT_DEPTH = 12
_MAX_ARGUMENT_ITEMS = 128
_MAX_RETAINED_RUNS = 500


@dataclass(slots=True)
class AgentRun:
    """记录一次后台 Agent 任务的运行信息。"""

    run_id: str
    signature: str
    stream_id: str
    task_id: str
    status: str
    started_at: float
    finished_at: float | None = None
    message: str | dict[str, Any] | None = None


class AgentManagerService(BaseService):
    """列出 Agent，并管理通过白名单校验的后台运行任务。"""

    name: str = "agent_manager"
    description: str = "Agent 查询、后台运行、状态与取消服务"
    version: str = "1.0.0"

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化 Service，并复用插件级别的任务状态。"""
        super().__init__(plugin)

        # Service API 每次查询都会创建新实例，因此任务表和锁放在插件实例上，
        # 让 Tool、Command 与其他插件拿到的 Service 共享同一份运行状态。
        if getattr(plugin, "_agent_pool_runs", None) is None:
            plugin._agent_pool_runs = {}
        if getattr(plugin, "_agent_pool_lock", None) is None:
            plugin._agent_pool_lock = asyncio.Lock()
        self._runs: dict[str, AgentRun] = plugin._agent_pool_runs
        self._lock: asyncio.Lock = plugin._agent_pool_lock

    def _config(self) -> AgentPoolManagerConfig:
        """读取并检查当前插件配置。"""
        config = getattr(self.plugin, "config", None)
        if not isinstance(config, AgentPoolManagerConfig):
            raise RuntimeError("agent_pool_manager 配置未加载")
        return config

    def _allowed_signatures(self) -> set[str]:
        """返回规范化后的后台运行白名单；星号表示全部 Agent。"""
        return {
            value.strip()
            for value in self._config().agents.allowed_signatures
            if isinstance(value, str) and value.strip()
        }

    def list_agents(self) -> dict[str, Any]:
        """列出已注册 Agent，不返回可执行参数 Schema。"""
        allowed = self._allowed_signatures()
        agents = []
        for signature, agent_cls in sorted(agent_api.get_all_agents().items()):
            agents.append(
                {
                    "signature": signature,
                    "name": str(getattr(agent_cls, "name", "")),
                    "description": str(getattr(agent_cls, "description", "")),
                    "plugin": str(getattr(agent_cls, "_plugin_", "")),
                    "allowed_for_background_run": "*" in allowed or signature in allowed,
                }
            )
        return {"agents": agents, "count": len(agents)}

    @staticmethod
    def _run_snapshot(run: AgentRun) -> dict[str, Any]:
        """生成不包含聊天流和任务参数的运行状态摘要。"""
        elapsed = max(0.0, (run.finished_at or time.time()) - run.started_at)
        return {
            "run_id": run.run_id,
            "signature": run.signature,
            "status": run.status,
            "message": run.message,
            "started_at": datetime.fromtimestamp(
                run.started_at, timezone.utc
            ).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
        }

    def list_running_agents(self) -> list[dict[str, Any]]:
        """返回当前 Service 启动且仍在运行的 Agent。"""
        manager = get_task_manager()
        active: list[dict[str, Any]] = []
        for run in self._runs.values():
            try:
                task = manager.get_task(run.task_id)
            except TaskNotFoundError:
                continue

            if not task.is_done():
                run.status = "运行中"
                active.append(self._run_snapshot(run))
            elif run.status in {"运行中", "正在取消"}:
                self._refresh_finished_run(run, task)
        return sorted(active, key=lambda item: item["started_at"])

    def get_runtime_status(self) -> dict[str, Any]:
        """返回任务管理器统计和本插件当前运行的 Agent。"""
        task_manager = get_task_manager()
        own_active = self.list_running_agents()
        config = self._config().agents
        active_ids = {item["run_id"] for item in own_active}
        recent_finished = sorted(
            (
                run
                for run in self._runs.values()
                if run.finished_at is not None and run.run_id not in active_ids
            ),
            key=lambda run: run.finished_at or run.started_at,
        )[-20:]
        status_items = own_active + [
            self._run_snapshot(run) for run in recent_finished
        ]
        return {
            "task_manager": task_manager.get_stats(),
            "agent_runs": {
                "active": len(own_active),
                "limit": self._max_concurrent_runs(config.max_concurrent_runs),
                "items": sorted(status_items, key=lambda item: item["started_at"]),
            },
        }

    async def start_agent(
        self,
        signature: str,
        stream_id: str,
        arguments: dict[str, Any],
        *,
        chatter_name: str = "",
        chatter_signature: str = "",
    ) -> dict[str, Any]:
        """校验 Agent、聊天上下文和参数后创建后台任务。"""
        signature = self._validate_text(signature, "signature", 256)
        stream_id = self._validate_text(stream_id, "stream_id", 512)
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是 JSON object")
        self._validate_json_value(arguments)
        try:
            encoded_arguments = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("arguments 必须只包含有效 JSON 值") from exc
        if len(encoded_arguments) > _MAX_ARGUMENTS_JSON_CHARS:
            raise ValueError(
                f"arguments 编码后不能超过 {_MAX_ARGUMENTS_JSON_CHARS} 个字符"
            )

        allowed = self._allowed_signatures()
        if "*" not in allowed and signature not in allowed:
            raise ValueError("该 Agent 未加入后台运行白名单")

        agent_cls = agent_api.get_agent_class(signature)
        if (
            agent_cls is None
            or not inspect.isclass(agent_cls)
            or not issubclass(agent_cls, BaseAgent)
        ):
            raise ValueError(f"Agent 不存在或类型无效：{signature}")

        stream_info = await self._get_stream_info(stream_id)
        self._validate_context(
            agent_cls,
            stream_info,
            chatter_name=chatter_name,
            chatter_signature=chatter_signature,
        )

        plugin_name = str(getattr(agent_cls, "_plugin_", "")).strip()
        if not plugin_name:
            plugin_name = signature.split(":", 1)[0]
        agent_plugin = plugin_api.get_plugin(plugin_name)
        if agent_plugin is None:
            raise ValueError(f"Agent 所属插件未加载：{plugin_name}")

        agent_instance = agent_cls(stream_id=stream_id, plugin=agent_plugin)
        self._validate_arguments(agent_instance.execute, arguments)
        activate = getattr(agent_instance, "go_activate", None)
        if callable(activate):
            try:
                if not await activate():
                    raise ValueError("Agent 未通过 go_activate 激活判定")
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"Agent 激活检查失败：{type(exc).__name__}") from exc

        config = self._config().agents
        limit = self._max_concurrent_runs(config.max_concurrent_runs)
        timeout = self._task_timeout(config.task_timeout_seconds)
        run_id = uuid4().hex
        now = time.time()

        async with self._lock:
            if len(self.list_running_agents()) >= limit:
                raise RuntimeError(f"后台 Agent 已达到并发上限（{limit}）")

            coro = self._run_agent(
                run_id=run_id,
                signature=signature,
                agent_plugin=agent_plugin,
                stream_id=stream_id,
                arguments=arguments,
            )
            try:
                task_info = get_task_manager().create_task(
                    coro=coro,
                    name=f"agent:{signature}:{run_id[:8]}",
                    timeout=timeout,
                    group_name=_TASK_GROUP,
                    metadata={"run_id": run_id, "agent_signature": signature},
                )
            except Exception:
                coro.close()
                raise

            run = AgentRun(
                run_id=run_id,
                signature=signature,
                stream_id=stream_id,
                task_id=task_info.task_id,
                status="运行中",
                started_at=now,
            )
            self._runs[run_id] = run
            self._prune_runs()

        return self._run_snapshot(run)

    async def stop_agent(self, run_id: str) -> bool:
        """根据启动时返回的 run_id 取消后台任务。"""
        run_id = self._validate_text(run_id, "run_id", 64)
        run = self._runs.get(run_id)
        if run is None:
            return False
        cancelled = get_task_manager().cancel_task(run.task_id)
        if cancelled:
            run.status = "正在取消"
        return cancelled

    async def shutdown(self) -> None:
        """插件卸载时取消本插件启动的所有活动任务。"""
        manager = get_task_manager()
        for run in list(self._runs.values()):
            try:
                task = manager.get_task(run.task_id)
            except TaskNotFoundError:
                continue
            if not task.is_done():
                manager.cancel_task(run.task_id)
                run.status = "正在取消"

    async def _get_stream_info(self, stream_id: str) -> dict[str, Any]:
        """优先读取内存中的聊天流，缺失时再读取持久化信息。"""
        stream = await stream_api.get_stream(stream_id)
        if stream is not None:
            return {
                "platform": stream.platform,
                "chat_type": stream.chat_type,
            }
        info = await stream_api.get_stream_info(stream_id)
        if info is None:
            raise ValueError("找不到指定的聊天流")
        return info

    async def _run_agent(
        self,
        *,
        run_id: str,
        signature: str,
        agent_plugin: Any,
        stream_id: str,
        arguments: dict[str, Any],
    ) -> None:
        """在后台调用框架 Agent API，并记录最终状态。"""
        run = self._runs.get(run_id)
        try:
            success, result = await agent_api.execute_agent(
                signature=signature,
                plugin=agent_plugin,
                stream_id=stream_id,
                **arguments,
            )
            if run is not None:
                run.status = "成功" if success else "异常"
                run.message = result
        except asyncio.CancelledError:
            if run is not None:
                if run.status == "正在取消":
                    run.status = "已取消"
                    run.message = "后台任务已取消"
                else:
                    run.status = "异常"
                    run.message = "后台任务意外取消或超时"
            raise
        except Exception as exc:
            if run is not None:
                run.status = "异常"
                run.message = f"{type(exc).__name__}: {exc}"
        finally:
            if run is not None:
                run.finished_at = time.time()

    @staticmethod
    def _refresh_finished_run(run: AgentRun, task: TaskInfo) -> None:
        """从 TaskManager 同步已结束任务的状态。"""
        if task.is_cancelled():
            if run.status == "正在取消":
                run.status = "已取消"
                run.message = run.message or "后台任务已取消"
            else:
                run.status = "异常"
                run.message = run.message or "后台任务意外取消或超时"
        elif task.is_failed():
            run.status = "异常"
            error = task.get_exception()
            run.message = run.message or (
                f"{type(error).__name__}: {error}" if error else "后台任务异常"
            )
        else:
            run.status = "成功"
        run.finished_at = run.finished_at or time.time()

    def _prune_runs(self) -> None:
        """限制内存中保留的已结束运行记录数量。"""
        if len(self._runs) <= _MAX_RETAINED_RUNS:
            return
        finished = [
            run_id for run_id, run in self._runs.items() if run.finished_at is not None
        ]
        for run_id in finished[: len(self._runs) - _MAX_RETAINED_RUNS]:
            self._runs.pop(run_id, None)

    @staticmethod
    def _validate_text(value: Any, name: str, max_length: int) -> str:
        """检查必填字符串并限制长度。"""
        if not isinstance(value, str):
            raise ValueError(f"{name} 必须是字符串")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} 不能为空")
        if len(normalized) > max_length:
            raise ValueError(f"{name} 长度不能超过 {max_length} 个字符")
        return normalized

    @classmethod
    def _validate_json_value(
        cls,
        value: Any,
        *,
        depth: int = 0,
        count: list[int] | None = None,
    ) -> None:
        """限制参数 JSON 的嵌套层级、元素数量和数据类型。"""
        if count is None:
            count = [0]
        if depth > _MAX_ARGUMENT_DEPTH:
            raise ValueError(f"arguments 嵌套层级不能超过 {_MAX_ARGUMENT_DEPTH}")
        count[0] += 1
        if count[0] > _MAX_ARGUMENT_ITEMS:
            raise ValueError(f"arguments 最多包含 {_MAX_ARGUMENT_ITEMS} 个值")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("arguments 的 object key 必须是字符串")
                cls._validate_json_value(child, depth=depth + 1, count=count)
        elif isinstance(value, list):
            for child in value:
                cls._validate_json_value(child, depth=depth + 1, count=count)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("arguments 只能包含 JSON 类型")

    @staticmethod
    def _validate_arguments(execute, arguments: dict[str, Any]) -> None:
        """按 Agent execute() 的参数名、必填项和类型注解校验输入。"""
        signature = inspect.signature(execute)
        parameters = signature.parameters
        if any(
            parameter.kind
            in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for parameter in parameters.values()
        ):
            raise ValueError("该 Agent 使用动态参数，后台运行器无法安全校验")

        unknown = sorted(set(arguments) - set(parameters))
        if unknown:
            raise ValueError(f"未知 Agent 参数：{', '.join(unknown)}")
        try:
            signature.bind(**arguments)
        except TypeError as exc:
            raise ValueError(f"Agent 参数不完整或格式错误：{exc}") from exc

        try:
            type_hints = get_type_hints(execute, include_extras=True)
        except Exception:
            type_hints = {}
        for name, value in arguments.items():
            annotation = type_hints.get(name, parameters[name].annotation)
            value_type = (
                get_args(annotation)[0]
                if get_origin(annotation) is Annotated and get_args(annotation)
                else annotation
            )
            if annotation is inspect.Parameter.empty or value_type in (Any, object):
                raise ValueError(f"Agent 参数 {name} 缺少具体类型声明，拒绝执行")
            try:
                TypeAdapter(annotation).validate_python(value, strict=True)
            except Exception as exc:
                raise ValueError(f"Agent 参数 {name} 类型不匹配") from exc

    @staticmethod
    def _validate_context(
        agent_cls: type[BaseAgent],
        stream_info: dict[str, Any],
        *,
        chatter_name: str,
        chatter_signature: str,
    ) -> None:
        """按 Agent 声明检查聊天类型、平台和 Chatter 限制。"""
        try:
            chat_type = ChatType(stream_info.get("chat_type"))
        except (ValueError, TypeError) as exc:
            raise ValueError("无法识别当前聊天流类型") from exc

        required_chat_type = getattr(agent_cls, "chat_type", ChatType.ALL)
        if not isinstance(required_chat_type, ChatType):
            try:
                required_chat_type = ChatType(required_chat_type)
            except (ValueError, TypeError) as exc:
                raise ValueError("Agent 声明了无效的 chat_type") from exc
        if required_chat_type != ChatType.ALL and required_chat_type != chat_type:
            raise ValueError("Agent 不支持当前聊天流类型")

        platform = str(stream_info.get("platform") or "").strip()
        associated_platforms = getattr(agent_cls, "associated_platforms", []) or []
        if associated_platforms and (
            not platform or platform not in associated_platforms
        ):
            raise ValueError("Agent 不支持当前聊天平台")

        chatter_allow = getattr(agent_cls, "chatter_allow", []) or []
        if chatter_allow and (
            chatter_name not in chatter_allow
            and chatter_signature not in chatter_allow
        ):
            raise ValueError("Agent 未允许当前 Chatter")

    @staticmethod
    def _max_concurrent_runs(value: Any) -> int:
        """将并发上限限制在 1 到 16 之间。"""
        try:
            return max(1, min(16, int(value)))
        except (TypeError, ValueError):
            return 3

    @staticmethod
    def _task_timeout(value: Any) -> int:
        """将任务超时限制在 1 到 3600 秒之间。"""
        try:
            return max(1, min(3600, int(value)))
        except (TypeError, ValueError):
            return 1800

