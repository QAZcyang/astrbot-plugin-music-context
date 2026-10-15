"""Assemble defaults for the repository-root AstrBot plugin entry point."""

from collections.abc import Callable
from pathlib import Path

from astrbot.api.event import AstrMessageEvent

from .cache import CacheManager
from .config import PluginConfig
from .context import MusicContextBuilder
from .context.processor import ContextProcessor
from .integration import (
    MUSIC_CONTEXT_PREFIX,
    AstrBotMessageExtractor,
    MusicContextIntegration,
)
from .models import MusicContext
from .parser import MusicParser
from .providers import ProviderRegistry


def create_integration(
    cache_directory: Path | None = None,
    *,
    registry: ProviderRegistry | None = None,
    settings: PluginConfig | None = None,
    observer: Callable[[AstrMessageEvent, MusicContext], None] | None = None,
    processor: ContextProcessor | None = None,
    cache_manager: CacheManager | None = None,
) -> MusicContextIntegration:
    """Wire replaceable modules without modifying their implementations."""
    # Parser 通过 Registry 获取平台 URL 适配器，核心装配无需按平台导入具体 Provider。
    # Builder 的预算预先扣除注入前缀，保证最终发送的数据部分也遵守同一字符上限。
    if registry is None:
        registry = ProviderRegistry()
        registry.discover()
    settings = settings or PluginConfig()
    return MusicContextIntegration(
        parser=MusicParser(
            extractor=AstrBotMessageExtractor(
                enable_card_parser=settings.enable_card_parser
            ), url_parsers=(registry.parse_url,)
        ),
        registry=registry,
        builder=MusicContextBuilder(
            max_context_chars=settings.max_context_length - len(MUSIC_CONTEXT_PREFIX),
            lyrics_mode=settings.lyrics_mode,
            original_lyrics_chars=settings.original_lyrics_chars,
            translation_extra_chars=settings.translation_extra_chars,
        ),
        cache=(
            cache_manager
            or CacheManager(
                cache_directory,
                ttl=settings.cache_ttl,
                max_entries=settings.max_cache_items,
            )
        )
        if cache_directory is not None and settings.enable_cache
        else None,
        max_context_chars=settings.max_context_length,
        debug_mode=settings.debug_mode,
        observer=observer,
        enable_context_injection=settings.enable_context_injection,
        enable_card_parser=settings.enable_card_parser,
        processor=processor,
    )
