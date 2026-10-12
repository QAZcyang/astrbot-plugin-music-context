"""Capability routing of candidates; no platform-specific dispatch branches."""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..parser import MusicLinkCandidate, MusicLinkResult, MusicParser
from .base import MusicCandidate, MusicProvider
from .capability import DATA_TYPES, INPUT_TYPES, MusicResource
from .errors import (
    InsufficientCapabilityError,
    NoMatchingProviderError,
    ProviderExecutionError,
)

if TYPE_CHECKING:
    from .registry import ProviderRegistry


class ProviderCapabilityRouter:
    """Normalize via Parser, then filter capabilities and supports locally."""

    def __init__(
        self, registry: "ProviderRegistry", *, parser: MusicParser | None = None
    ) -> None:
        self.registry = registry
        self.parser = (
            parser
            if parser is not None
            else MusicParser(url_parsers=(registry.parse_url,))
        )

    def normalize(self, candidate: MusicCandidate) -> MusicLinkResult:
        if isinstance(candidate, MusicLinkResult):
            return candidate
        if candidate.input_type == "song_id" or (
            candidate.track_id and not candidate.url
        ):
            if not candidate.platform or not candidate.track_id or candidate.url:
                raise NoMatchingProviderError()
            return MusicLinkResult(candidate.platform, candidate.track_id, "")
        link = self.parser.parse(candidate.url)
        if link is None or (
            candidate.platform is not None and candidate.platform != link.platform
        ):
            raise NoMatchingProviderError()
        return link

    def route(
        self,
        candidate: MusicCandidate,
        *,
        required_resources: Iterable[MusicResource] = ("track", "lyrics"),
        resolved: MusicLinkResult | None = None,
    ) -> MusicProvider:
        """Select by capabilities, then priority; raise typed log-safe errors.

        Pass an already parsed identity to avoid repeating short-link I/O.
        A broken matcher is logged and other eligible providers are tried.
        """
        # 归一化歌曲身份与原始输入形式分别保留：卡片即使含 URL，也必须匹配卡片能力；
        # required_resources 只包含当前缺失资源，优先级仅在满足这些条件后参与选择。
        required = frozenset(required_resources)
        if not required or not required <= DATA_TYPES:
            raise InsufficientCapabilityError()
        link = resolved if resolved is not None else self.normalize(candidate)
        if isinstance(candidate, MusicLinkCandidate):
            if candidate.input_type == "song_id" and candidate.url:
                raise NoMatchingProviderError()
            if candidate.url and candidate.url != link.url:
                raise NoMatchingProviderError()
            if (
                candidate.input_type is not None
                and candidate.input_type not in INPUT_TYPES
            ):
                raise InsufficientCapabilityError()
            if candidate.platform is not None and candidate.platform != link.platform:
                raise NoMatchingProviderError()
            if candidate.source in {"qq_json", "xml"}:
                input_type = "card_message"
            elif not candidate.url and candidate.track_id:
                input_type = "song_id"
            else:
                input_type = candidate.input_type or "url"
        else:
            input_type = "song_id" if not candidate.url else "url"
        # A normalized URL with no local identity is a resolved share. A card
        # carrying such a URL additionally requires share_link support.
        share = bool(link.url) and self.parser.parse_url(link.url) is None
        if share and input_type == "url":
            input_type = "share_link"
        insufficient = False
        failed = None
        for provider in self.registry.providers_by_priority():
            capability = self.registry.get_capabilities(provider.name)
            if capability is None or link.platform not in capability.platforms:
                continue
            if (
                input_type not in capability.input_types
                or not required <= capability.data_types
                or (share and "share_link" not in capability.input_types)
            ):
                insufficient = True
                continue
            try:
                if provider.supports(link) is True:
                    return provider
            except Exception as exc:
                failed = ProviderExecutionError(
                    stage="matching", cause_type=type(exc).__name__
                )
                self.registry.logger.warning(
                    "Music Context routing failed (%s, stage=%s, cause=%s)",
                    failed.code,
                    failed.stage,
                    failed.cause_type,
                )
        if insufficient:
            raise InsufficientCapabilityError()
        if failed is not None:
            raise failed
        raise NoMatchingProviderError()
