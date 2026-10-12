"""Immutable provider capability declarations, independent of routing."""

from dataclasses import dataclass
from typing import Literal

MusicInputType = Literal["song_id", "url", "share_link", "card_message"]
MusicResource = Literal["track", "lyrics"]
INPUT_TYPES = frozenset({"song_id", "url", "share_link", "card_message"})
DATA_TYPES = frozenset({"track", "lyrics"})


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    """Platform identities, raw data resources, input forms and selection rank."""

    platforms: frozenset[str]
    data_types: frozenset[MusicResource]
    input_types: frozenset[MusicInputType]
    priority: int = 0

    def __post_init__(self) -> None:
        for field in ("platforms", "data_types", "input_types"):
            value = getattr(self, field)
            if not isinstance(value, frozenset) or not value:
                raise ValueError("Capability sets must be nonempty frozensets")
        if any(
            not isinstance(platform, str) or not platform for platform in self.platforms
        ):
            raise ValueError("Capability platform identities must be nonempty strings")
        if not self.data_types <= DATA_TYPES or not self.input_types <= INPUT_TYPES:
            raise ValueError("Unknown capability data or input type")
        if type(self.priority) is not int:
            raise TypeError("Capability priority must be an integer")
