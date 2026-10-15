"""Structured music data for subsequent context construction."""

from dataclasses import asdict, dataclass, field

from .lyrics import MusicLyrics
from .track import MusicTrack


@dataclass(frozen=True, slots=True)
class ProcessorMetadata:
    enabled: bool = False
    model: str | None = None
    processed_at: str | None = None
    version: str | None = None
    generated_by: str | None = None
    confidence: float | None = None
    validation: str | None = None
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class LyricsPresentation:
    """Derived request facts; unknown defaults preserve historical observations."""

    requested_mode: str = "unknown"
    effective_mode: str = "unknown"
    fallback_reason: str | None = None
    original_truncated: bool = False
    translation_truncated: bool = False
    translation_source: str | None = None


@dataclass(frozen=True, slots=True)
class MusicContext:
    """Music evidence plus optional text-derived hypotheses, never user judgments."""

    track: MusicTrack
    lyrics: MusicLyrics = field(default_factory=MusicLyrics)
    original_lyrics: str | None = field(default=None, repr=False)
    summary: str | None = None
    style_tags: tuple[str, ...] = ()
    emotion_tags: tuple[str, ...] = ()
    theme_tags: tuple[str, ...] = ()
    scene_tags: tuple[str, ...] = ()
    lyrics_summary: str | None = None
    lyrics_topics: tuple[str, ...] = ()
    lyrics_structure: tuple[str, ...] = ()
    processor_metadata: ProcessorMetadata = field(default_factory=ProcessorMetadata)

    artist_info: str | None = None
    lyrics_presentation: LyricsPresentation = field(default_factory=LyricsPresentation)

    def payload(self) -> dict:
        """LLM projection: omit duplicate raw lyrics and absent enhancements."""
        value = asdict(self)
        value.pop("original_lyrics")
        if not self.processor_metadata.enabled:
            return {key: value[key] for key in ("track", "lyrics", "lyrics_presentation")}
        return {
            "track": value["track"],
            "lyrics": value["lyrics"],
            "lyrics_presentation": value["lyrics_presentation"],
            "song_summary": self.summary,
            "artist_info": self.artist_info,
            "style": value["style_tags"],
            "emotion": value["emotion_tags"],
            "theme": value["theme_tags"],
            "keywords": value["lyrics_topics"],
            "processor_metadata": value["processor_metadata"],
        }
