"""AstrBot loader entry; lifecycle handlers delegate to the integration layer."""

import asyncio
from collections.abc import Mapping
from dataclasses import asdict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from .src.astrbot_plugin_music_context.cache import CacheManager
from .src.astrbot_plugin_music_context.config import PluginConfig
from .src.astrbot_plugin_music_context.config_service import ConfigService
from .src.astrbot_plugin_music_context.context.processor import ContextProcessor
from .src.astrbot_plugin_music_context.context.processor_transport import (
    AstrBotModelAdapter,
)
from .src.astrbot_plugin_music_context.main import create_integration
from .src.astrbot_plugin_music_context.integration.astrbot import DIAGNOSTICS_KEY
from .src.astrbot_plugin_music_context.models import MusicContext
from .src.astrbot_plugin_music_context.webui import DashboardService, ManagementState
from .src.astrbot_plugin_music_context.webui.controller import DashboardController
from .src.astrbot_plugin_music_context.webui.state import ContextTicket

_TICKET_KEY = "astrbot_plugin_music_context.management_ticket"
_MODEL_KEY = "astrbot_plugin_music_context.reply_model"


class MusicContextPlugin(Star):
    """Add factual music data to the existing AstrBot LLM request."""

    # 根入口负责宿主生命周期：加载时装配模块并恢复历史，卸载时保存历史、
    # 释放内存和管理路由；音乐解析与数据处理交给 integration，避免绑定宿主细节。
    def __init__(self, context: Context, config: Mapping | None = None) -> None:
        super().__init__(context)
        try:
            self.settings = PluginConfig.from_mapping(config)
        except ValueError:
            logger.warning(
                "Invalid Music Context configuration; music processing disabled"
            )
            self.settings = PluginConfig(
                enable_cache=False, enable_context_injection=False
            )
        self._transport = AstrBotModelAdapter(context)
        self._processor = ContextProcessor(
            self.settings, transport=self._transport
        )
        try:
            cache_directory = (
                StarTools.get_data_dir("astrbot_plugin_music_context") / "cache"
            )
        except Exception as exc:  # noqa: BLE001 - cache storage must not prevent loading
            logger.warning(
                "Music cache directory unavailable (%s)", type(exc).__name__
            )
            cache_directory = None
        self._management = ManagementState(
            self.settings,
            history_directory=cache_directory.parent if cache_directory is not None else None,
        )
        self._integration = create_integration(
            cache_directory=cache_directory,
            settings=self.settings,
            observer=self._observe,
            processor=self._processor,
        )
        # Disabled processing cache may still be inspected/cleared by the administrator.
        inspection_cache = self._integration.cache
        if inspection_cache is None and cache_directory is not None:
            inspection_cache = CacheManager(
                cache_directory,
                ttl=self.settings.cache_ttl,
                max_entries=self.settings.max_cache_items,
            )
        self._cache_directory = cache_directory
        self._inspection_cache = inspection_cache
        try:
            self._config_service = ConfigService(
                config, on_change=self._apply_settings, model_options=self._transport.models
            )
        except ValueError:
            self._config_service = ConfigService(
                asdict(self.settings), on_change=self._apply_settings,
                model_options=self._transport.models
            )
        service = DashboardService(
            self._management, self._integration.registry, inspection_cache
        )
        service.config_service = self._config_service
        service.processor = self._processor
        self._dashboard_service = service
        self._dashboard = DashboardController(service)
        try:
            self._webui_available = self._dashboard.register(context)
        except Exception as exc:  # noqa: BLE001 - optional management must not prevent chat
            self._dashboard.unregister(context)
            self._webui_available = False
            logger.warning(
                "Music dashboard unavailable (%s)", type(exc).__name__
            )
        logger.info(
            "Music Context initialized (cache=%s, dashboard=%s)",
            self._integration.cache is not None,
            self._webui_available,
        )

    async def _apply_settings(self, settings: PluginConfig) -> None:
        processor = ContextProcessor(
            settings, transport=self._transport
        )
        if self._inspection_cache is not None:
            self._inspection_cache.configure(
                ttl=settings.cache_ttl, max_entries=settings.max_cache_items
            )
        integration = create_integration(
            cache_directory=self._cache_directory,
            registry=self._integration.registry,
            settings=settings,
            observer=self._observe,
            processor=processor,
            cache_manager=self._inspection_cache,
        )
        self._management.configure(settings)
        self.settings = settings
        self._processor = processor
        self._integration = integration
        self._dashboard_service.processor = processor
        await self._persist_history()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        # 此阶段只识别输入并保留事件内状态；是否唤醒、是否发起 LLM 请求由 AstrBot 决定。
        event.set_extra(_TICKET_KEY, None)
        await self._integration.on_message(event)

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        await self._integration.on_llm_request(event, req)
        if self._management.replies_enabled:
            event.set_extra(_MODEL_KEY, getattr(req, "model", None))
        await self._persist_history()

    async def _persist_history(self) -> None:
        # 历史是可选观测：落盘失败不能阻断聊天；超时仅停止等待，文件线程仍可能完成。
        try:
            async with asyncio.timeout(1.0):
                await asyncio.to_thread(self._management.persist)
        except Exception as exc:  # noqa: BLE001 - optional history cannot interrupt chat
            logger.warning(
                "Music history persistence failed (%s)", type(exc).__name__
            )

    def _observe(self, event: AstrMessageEvent, music_context: MusicContext) -> None:
        # 只有成功追加音乐数据后才取得 ticket，使回复关联到本次 Context，避免收集普通聊天。
        ticket = self._management.record_context(
            music_context, diagnostics=event.get_extra(DIAGNOSTICS_KEY)
        )
        if ticket is not None and self._management.replies_enabled:
            event.set_extra(_TICKET_KEY, ticket)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        # 记录宿主 LLM 回调用于 WebUI 追踪；此回调不能证明后续消息已经送达客户端。
        if not self._management.replies_enabled:
            return
        try:
            ticket = event.get_extra(_TICKET_KEY)
            if not isinstance(ticket, ContextTicket):
                return
            message = event.get_message_str()
            length = len(message) if isinstance(message, str) else 0
            # Prefer the actual callback model when the host exposes it. Never guess.
            raw_completion = getattr(resp, "raw_completion", None)
            response_model = (
                raw_completion.get("model") if isinstance(raw_completion, dict)
                else getattr(raw_completion, "model", None)
            )
            model_name = response_model or getattr(resp, "model", None) or event.get_extra(_MODEL_KEY)
            self._management.record_reply(
                ticket, resp.completion_text, length,
                user_message=message, model_name=model_name,
            )
            await self._persist_history()
        except Exception as exc:  # noqa: BLE001 - observation must not break chat
            logger.warning(
                "Music response observation failed (%s)", type(exc).__name__
            )

    async def terminate(self) -> None:
        # 先持久化再关闭内存状态，保留磁盘历史供下次加载恢复。
        await self._persist_history()
        self._management.close()
        self._dashboard.unregister(self.context)
