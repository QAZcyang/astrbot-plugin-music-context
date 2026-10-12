"""Structured music link identity without fetched track metadata."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MusicLinkCandidate:
    """Unverified share data; source denotes input format, not music platform.

    Card titles/artists are optional hints, never authoritative Provider data.
    URL syntax and platform identity are validated separately by MusicParser.
    Optional platform/track_id support explicit structured song IDs; plain
    numeric chat is not extracted as music. input_type may refine URL input;
    known card sources always retain their card identity during routing.
    """

    url: str = ""
    source: str = "text"
    title: str | None = None
    artist: str | None = None
    platform: str | None = None
    track_id: str | None = None
    input_type: str | None = None


@dataclass(frozen=True, slots=True)
class MusicLinkResult:
    """A platform identifier and the original extracted URL.

    Track IDs are strings so future platforms can use opaque identifiers.
    A successful parse confirms URL syntax, not that the track exists.
    """

    platform: str
    track_id: str
    url: str
