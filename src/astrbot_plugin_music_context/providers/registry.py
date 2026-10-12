"""Explicit provider registration and discovery inside the installed package."""

import importlib
import inspect
import logging
import pkgutil
import re
from collections.abc import Mapping
from dataclasses import replace

from ..models import MusicLyrics, MusicTrack
from ..parser import MusicLinkResult
from .base import MusicCandidate, MusicProvider, MusicResource
from .capability import ProviderCapability
from .errors import (
    InsufficientCapabilityError,
    NoMatchingProviderError,
    ProviderExecutionError,
    ProviderRoutingError,
)


class _LegacyProvider(MusicProvider):
    """Adapt the existing integration providers mapping to the new lifecycle."""

    def __init__(self, name: str, provider: MusicProvider) -> None:
        self.name = name
        self.provider = provider

    @property
    def capabilities(self) -> ProviderCapability:
        declared = getattr(self.provider, "capabilities", None)
        return (
            declared
            if isinstance(declared, ProviderCapability)
            else super().capabilities
        )

    async def fetch_metadata(self, track_id: str) -> MusicTrack:
        return await self.provider.fetch_metadata(track_id)

    async def fetch_lyrics(self, track_id: str) -> MusicLyrics:
        return await self.provider.fetch_lyrics(track_id)


class ProviderRegistry:
    """Own instances; highest priority wins, then registration order.

    Names are unique. Failed retrieval does not trigger another provider.
    Discovery only imports trusted, direct modules in this package.
    Construct/register providers before serving concurrent requests.
    """

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._providers: dict[str, MusicProvider] = {}
        self._priorities: dict[str, int] = {}
        self._capabilities: dict[str, ProviderCapability] = {}
        self.logger = logger if logger is not None else logging.getLogger("astrbot")

    def register(
        self,
        provider: MusicProvider | type[MusicProvider],
        *,
        priority: int | None = None,
    ) -> MusicProvider:
        """Register a class with a zero-argument constructor or an instance."""
        if isinstance(provider, type) and issubclass(provider, MusicProvider):
            provider = provider()
        if not isinstance(provider, MusicProvider):
            raise TypeError("Expected a MusicProvider instance or subclass")
        name = provider.name
        if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_]*", name) is None:
            raise ValueError("Provider name must be a lowercase identifier")
        if name in self._providers:
            raise ValueError("Provider name is already registered")
        capability = provider.capabilities
        if not isinstance(capability, ProviderCapability):
            raise TypeError("Expected a ProviderCapability declaration")
        rank = capability.priority if priority is None else priority
        if type(rank) is not int:
            raise TypeError("Provider priority must be an integer")
        self._providers[name] = provider
        self._priorities[name] = rank
        # 注册时固定能力声明，路由据此筛选；能力声明不是远端连通性或真实平台验收结果。
        self._capabilities[name] = replace(capability, priority=rank)
        return provider

    def get_capabilities(self, name: str) -> ProviderCapability | None:
        """Immutable registered declaration, including the rank override."""
        return self._capabilities.get(name)

    def providers_by_priority(self) -> tuple[MusicProvider, ...]:
        """Ordered instances for capability routing."""
        return tuple(self._ordered())

    def get(self, name: str) -> MusicProvider | None:
        """Return a registered instance, or None for an unknown name."""
        return self._providers.get(name)

    def _ordered(self) -> list[MusicProvider]:
        # 稳定排序让同优先级按注册顺序选择，避免新增 Provider 后选择结果随意变化。
        names = sorted(self._providers, key=lambda name: -self._priorities[name])
        return [self._providers[name] for name in names]

    def select(self, candidate: MusicCandidate) -> MusicProvider | None:
        """Select a matching provider; one broken matcher is skipped."""
        for provider in self._ordered():
            try:
                if provider.supports(candidate) is True:
                    return provider
            except Exception as exc:
                self._log_failure("matching", exc)
        return None

    def parse_url(self, url: str) -> MusicLinkResult | None:
        """Dispatch local URL adapters for MusicParser without platform cases."""
        for provider in self._ordered():
            try:
                result = provider.parse_url(url)
                if (
                    isinstance(result, MusicLinkResult)
                    and provider.supports(result) is True
                ):
                    return result
            except Exception as exc:
                self._log_failure("URL recognition", exc)
        return None

    async def fetch(
        self,
        candidate: MusicCandidate,
        *,
        resource: MusicResource = "track",
        provider: MusicProvider | None = None,
    ) -> MusicTrack | MusicLyrics:
        """Retrieve one resource using a selected instance or select it now."""
        from .router import ProviderCapabilityRouter

        if resource not in ("track", "lyrics"):
            raise InsufficientCapabilityError()
        router = ProviderCapabilityRouter(self)
        link = router.normalize(candidate)
        selected = (
            provider
            if provider is not None
            else router.route(candidate, required_resources=(resource,), resolved=link)
        )
        if selected is None:
            raise NoMatchingProviderError()
        if self.get(selected.name) is not selected:
            raise ValueError("Selected provider is not registered here")
        capability = self.get_capabilities(selected.name)
        if (
            capability is None
            or resource not in capability.data_types
            or link.platform not in capability.platforms
        ):
            raise InsufficientCapabilityError()
        try:
            result = await selected.fetch(link, resource=resource)
            if not isinstance(
                result, MusicTrack if resource == "track" else MusicLyrics
            ):
                raise TypeError("Provider returned an invalid resource model")
            return result
        except ProviderRoutingError:
            raise
        except Exception as exc:
            raise ProviderExecutionError(
                stage="fetch", cause_type=type(exc).__name__
            ) from exc

    def discover(self) -> tuple[str, ...]:
        """Load locally declared concrete providers once, in module-name order.

        Existing names take precedence over discovery. Import/construction
        failures are isolated and logged without exception text.
        No entry-point packages, remote downloads or filesystem scans are used.
        """
        # 只发现已安装受信包内的直接模块；发现/构造不得联网，加载插件无需访问音乐平台。
        package = importlib.import_module(__package__)
        added: list[str] = []
        modules = sorted(
            pkgutil.iter_modules(package.__path__), key=lambda item: item.name
        )
        for module_info in modules:
            if (
                module_info.ispkg
                or module_info.name.startswith("_")
                or module_info.name in {"base", "registry"}
            ):
                continue
            try:
                module = importlib.import_module(
                    f"{package.__name__}.{module_info.name}"
                )
                for _, cls in inspect.getmembers(module, inspect.isclass):
                    if (
                        cls.__module__ != module.__name__
                        or not issubclass(cls, MusicProvider)
                        or inspect.isabstract(cls)
                    ):
                        continue
                    if self.get(cls.name) is not None:
                        continue
                    try:
                        provider = self.register(cls.create_default())
                        added.append(provider.name)
                    except Exception as exc:
                        self._log_failure("construction", exc)
            except Exception as exc:
                self._log_failure("discovery", exc)
        return tuple(added)

    def as_mapping(self) -> dict[str, MusicProvider]:
        """Compatibility snapshot; use Registry methods for new integrations."""
        return {
            name: provider.provider
            if isinstance(provider, _LegacyProvider)
            else provider
            for name, provider in self._providers.items()
        }

    @classmethod
    def from_mapping(cls, providers: Mapping[str, MusicProvider]) -> "ProviderRegistry":
        """Keep existing ID-based integration injection behavior unchanged."""
        registry = cls()
        for name, provider in providers.items():
            registry.register(_LegacyProvider(name, provider))
        return registry

    def _log_failure(self, stage: str, exc: Exception) -> None:
        self.logger.warning(
            "Music Context provider %s failed (%s)", stage, type(exc).__name__
        )
