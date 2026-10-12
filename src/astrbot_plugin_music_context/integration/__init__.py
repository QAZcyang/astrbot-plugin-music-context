"""AstrBot lifecycle and LLM request integration boundary."""

from .astrbot import (
    MAX_CONTEXT_CHARS,
    MUSIC_CONTEXT_PREFIX,
    MusicContextIntegration,
)
from .extractor import AstrBotMessageExtractor

__all__ = [
    "MAX_CONTEXT_CHARS",
    "MUSIC_CONTEXT_PREFIX",
    "AstrBotMessageExtractor",
    "MusicContextIntegration",
]
