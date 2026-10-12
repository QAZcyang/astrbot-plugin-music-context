"""Independent local-file caching for shared music models."""

from .manager import DEFAULT_TTL, CacheManager

__all__ = ["DEFAULT_TTL", "CacheManager"]
