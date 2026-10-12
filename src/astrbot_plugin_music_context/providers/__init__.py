"""Platform-independent music data provider boundary."""

from .base import MusicProvider
from .capability import ProviderCapability
from .errors import (
    InsufficientCapabilityError,
    NoMatchingProviderError,
    ProviderExecutionError,
)
from .registry import ProviderRegistry
from .router import ProviderCapabilityRouter

__all__ = [
    "MusicProvider",
    "ProviderRegistry",
    "ProviderCapability",
    "ProviderCapabilityRouter",
    "NoMatchingProviderError",
    "InsufficientCapabilityError",
    "ProviderExecutionError",
]
