"""Basic track metadata shared by music providers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MusicTrack:
    """A track identified by its platform source and opaque track ID.

    Artist is a display name and may contain multiple credited artists.
    URL preserves the original shared link; models do not parse links.
    """

    title: str
    artist: str
    source: str
    track_id: str
    album: str | None = None
    url: str | None = None
